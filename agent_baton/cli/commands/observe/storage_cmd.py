"""``baton storage preflight`` -- pre-migration safety check.

Reports the current schema version, file size, and last-modified time for
both the local ``baton.db`` and the global ``~/.baton/central.db``, then
creates a timestamped backup of each so the operator has a safe rollback
point before triggering any schema migration.

Usage::

    baton storage preflight
    baton storage preflight --context .claude/team-context
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "storage",
        help="Storage diagnostics and pre-migration safety checks",
        description=(
            "Report schema versions, file sizes, and create pre-migration "
            "backups for baton.db and central.db."
        ),
    )
    sub = p.add_subparsers(dest="storage_cmd", metavar="SUBCOMMAND")
    sub.required = True

    pf = sub.add_parser(
        "preflight",
        help="Check schema versions and create backups before a migration",
    )
    pf.add_argument(
        "--context",
        default=".claude/team-context",
        metavar="PATH",
        help="Path to team-context directory (default: .claude/team-context)",
    )
    pf.add_argument(
        "--no-backup",
        action="store_true",
        help="Report only — skip backup creation",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    if args.storage_cmd == "preflight":
        _preflight(args)
    else:
        print(f"Unknown storage subcommand: {args.storage_cmd}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Preflight implementation
# ---------------------------------------------------------------------------

def _preflight(args: argparse.Namespace) -> None:
    from agent_baton.core.storage.migration_backup import backup_db
    from agent_baton.core.storage.schema import SCHEMA_VERSION

    context_root = Path(args.context).resolve()
    baton_db = context_root / "baton.db"
    central_db = Path.home() / ".baton" / "central.db"

    print("baton storage preflight")
    print("=" * 60)
    print(f"Current engine SCHEMA_VERSION : {SCHEMA_VERSION}")
    print()

    errors: list[str] = []

    for label, db_path in [("baton.db", baton_db), ("central.db", central_db)]:
        print(f"[{label}]")
        print(f"  path      : {db_path}")

        if not db_path.exists():
            print("  status    : NOT FOUND (will be created on first use)")
            print()
            continue

        # Size
        size_bytes = db_path.stat().st_size
        size_kb = size_bytes / 1024
        print(f"  size      : {size_kb:.1f} KB ({size_bytes} bytes)")

        # Last modified
        mtime = db_path.stat().st_mtime
        mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        print(f"  modified  : {mtime_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}")

        # Schema version on disk
        db_version = _read_schema_version(db_path)
        print(f"  db version: {db_version}")

        if int(db_version) > SCHEMA_VERSION:
            msg = (
                f"  WARNING: {label} is at v{db_version}, "
                f"engine expects v{SCHEMA_VERSION}. Downgrade risk."
            )
            print(msg)
            errors.append(msg)

        # Backup
        if not args.no_backup:
            try:
                bak = backup_db(db_path)
                print(f"  backup    : {bak}")
            except Exception as exc:
                msg = f"  ERROR: backup failed for {label}: {exc}"
                print(msg)
                errors.append(msg)

        print()

    if errors:
        print("Preflight completed with warnings/errors:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    else:
        print("Preflight OK — safe to proceed with migration.")
        sys.exit(0)


def _read_schema_version(db_path: Path) -> str:
    """Return the integer schema version stored in *db_path*, or '0'."""
    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            row = conn.execute(
                "SELECT version FROM _schema_version LIMIT 1"
            ).fetchone()
            return str(row[0]) if row else "0"
        except sqlite3.OperationalError:
            return "0"
        finally:
            conn.close()
    except Exception:
        return "0"
