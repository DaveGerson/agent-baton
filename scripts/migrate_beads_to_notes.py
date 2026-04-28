#!/usr/bin/env python3
"""One-shot migration: write git notes for every existing SQLite bead.

Part A of the Gastown bead architecture (bd-2870).

For each row in the ``beads`` table this script:

1. Computes an anchor commit via ``git log -1 --before=<created_at>``
   (best-effort).  Falls back to ``git merge-base origin/main HEAD``,
   then to the repository root commit.
2. Writes the bead as a JSON note in ``refs/notes/baton-beads`` anchored
   to that commit.
3. Updates the ``bead_anchors`` SQLite index.

The script is **idempotent** — it skips beads whose anchor commit already
has a note (``git notes show`` returns exit 0).

Usage::

    python scripts/migrate_beads_to_notes.py [--dry-run] [--limit N]
    python scripts/migrate_beads_to_notes.py --db /path/to/baton.db --repo /path/to/repo

Options:
    --dry-run       Print what would be done without writing any notes.
    --limit N       Process at most N beads (useful for testing).
    --db PATH       Path to ``baton.db``.  Defaults to discovered path.
    --repo PATH     Path to git repo root.  Defaults to cwd.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

_GIT_TIMEOUT = 15


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )


def _anchor_for_bead(repo_root: Path, created_at: str, task_id: str | None) -> str:
    """Return the best anchor commit SHA for a bead created at *created_at*.

    Strategy (in priority order):
    1. ``git log -1 --before=<created_at> --format=%H`` — most accurate.
    2. ``git merge-base origin/main HEAD`` — for project-scoped beads or
       when no commit precedes created_at.
    3. ``git rev-list --max-parents=0 HEAD`` — root commit, last resort.

    Returns empty string on total failure.
    """
    # Strategy 1: commit just before bead creation
    if created_at:
        result = _git(
            repo_root,
            "log",
            "-1",
            f"--before={created_at}",
            "--format=%H",
            "--",
        )
        if result.returncode == 0:
            sha = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
            if sha:
                return sha

    # Strategy 2: merge-base (good for project-scoped beads)
    result = _git(repo_root, "merge-base", "origin/main", "HEAD")
    if result.returncode == 0:
        sha = result.stdout.strip()
        if sha:
            return sha

    # Strategy 3: root commit
    result = _git(repo_root, "rev-list", "--max-parents=0", "HEAD")
    if result.returncode == 0 and result.stdout.strip():
        sha = result.stdout.strip().splitlines()[0]
        if sha:
            print(
                f"  WARNING: anchor-fallback-root used for created_at={created_at!r}",
                file=sys.stderr,
            )
            return sha

    return ""


def _note_exists(repo_root: Path, anchor_commit: str) -> bool:
    """Return True if a note already exists on *anchor_commit*."""
    result = _git(repo_root, "notes", "--ref=refs/notes/baton-beads", "show", anchor_commit)
    return result.returncode == 0


def _write_note(repo_root: Path, anchor_commit: str, blob: dict) -> bool:
    """Write *blob* as a JSON note on *anchor_commit*.  Returns True on success."""
    json_body = json.dumps(blob, separators=(",", ":"), ensure_ascii=False)
    result = _git(
        repo_root,
        "notes",
        "--ref=refs/notes/baton-beads",
        "add",
        "-f",
        "-m",
        json_body,
        anchor_commit,
    )
    return result.returncode == 0


def _update_anchor_index(conn: sqlite3.Connection, bead_id: str, anchor_commit: str) -> None:
    """Upsert into bead_anchors (created by v31 migration or BeadAnchorIndex)."""
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bead_anchors (
                bead_id        TEXT PRIMARY KEY,
                anchor_commit  TEXT NOT NULL,
                last_seen_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )
            """
        )
        conn.execute(
            """
            INSERT INTO bead_anchors (bead_id, anchor_commit, last_seen_at)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            ON CONFLICT(bead_id) DO UPDATE SET
                anchor_commit = excluded.anchor_commit,
                last_seen_at  = excluded.last_seen_at
            """,
            (bead_id, anchor_commit),
        )
        conn.commit()
    except Exception as exc:
        print(f"  WARNING: anchor index update failed for {bead_id}: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def _discover_db(repo_root: Path) -> Path | None:
    """Walk upward from repo_root searching for baton.db."""
    candidates = [
        repo_root / "baton.db",
        repo_root / ".claude" / "team-context" / "baton.db",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Walk up
    candidate = repo_root
    for _ in range(6):
        p = candidate / "baton.db"
        if p.exists():
            return p
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate SQLite beads to git notes (Gastown Part A, bd-2870).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without writing notes.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        metavar="N",
        help="Process at most N beads (0 = unlimited).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to baton.db.  Auto-discovered if omitted.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to git repo root.  Defaults to current directory.",
    )
    args = parser.parse_args()

    repo_root = args.repo or Path.cwd()
    if not (repo_root / ".git").exists():
        # Try to find .git upward
        candidate = repo_root
        for _ in range(8):
            if (candidate / ".git").exists():
                repo_root = candidate
                break
            parent = candidate.parent
            if parent == candidate:
                break
            candidate = parent
        else:
            print(
                f"ERROR: no git repository found at or above {repo_root}", file=sys.stderr
            )
            sys.exit(1)

    db_path = args.db
    if db_path is None:
        db_path = _discover_db(repo_root)
    if db_path is None or not db_path.exists():
        print(
            f"ERROR: baton.db not found.  Pass --db /path/to/baton.db", file=sys.stderr
        )
        sys.exit(1)

    print(f"Repository: {repo_root}")
    print(f"Database:   {db_path}")
    if args.dry_run:
        print("Mode:       DRY RUN (no notes will be written)")
    else:
        print("Mode:       LIVE")
    print()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Check beads table exists
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='beads'"
    ).fetchone()
    if row is None:
        print("ERROR: beads table not found in database — schema v4+ required", file=sys.stderr)
        conn.close()
        sys.exit(1)

    limit_clause = f"LIMIT {args.limit}" if args.limit > 0 else ""
    rows = conn.execute(
        f"SELECT bead_id, task_id, created_at, * FROM beads ORDER BY created_at ASC {limit_clause}"
    ).fetchall()

    total = len(rows)
    print(f"Beads to process: {total}")
    print()

    skipped = 0
    written = 0
    failed = 0

    for row in rows:
        bead_id: str = row["bead_id"]
        task_id: str | None = row["task_id"]
        created_at: str = row["created_at"] or ""

        anchor_commit = _anchor_for_bead(repo_root, created_at, task_id)
        if not anchor_commit:
            print(f"  SKIP  {bead_id}  (no anchor commit found)")
            failed += 1
            continue

        # Idempotency: skip if a note already exists on this anchor
        if not args.dry_run and _note_exists(repo_root, anchor_commit):
            print(f"  SKIP  {bead_id}  anchor={anchor_commit[:8]} (note already exists)")
            skipped += 1
            continue

        # Build the blob
        blob: dict = {}
        for key in row.keys():
            val = row[key]
            if key in ("tags", "affected_files", "links"):
                try:
                    val = json.loads(val or "[]")
                except (json.JSONDecodeError, TypeError):
                    val = []
            blob[key] = val

        # Stamp Gastown fields
        blob["schema_version"] = "gastown-1"
        blob["anchor_commit"] = anchor_commit
        blob["branch_at_create"] = ""  # unknown for historical beads

        if args.dry_run:
            print(
                f"  WOULD WRITE  {bead_id}  anchor={anchor_commit[:8]}"
                f"  created={created_at}"
            )
            written += 1
            continue

        ok = _write_note(repo_root, anchor_commit, blob)
        if ok:
            _update_anchor_index(conn, bead_id, anchor_commit)
            print(f"  OK    {bead_id}  anchor={anchor_commit[:8]}")
            written += 1
        else:
            print(f"  FAIL  {bead_id}  anchor={anchor_commit[:8]}", file=sys.stderr)
            failed += 1

    conn.close()

    print()
    print(f"Done. written={written}  skipped={skipped}  failed={failed}  total={total}")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
