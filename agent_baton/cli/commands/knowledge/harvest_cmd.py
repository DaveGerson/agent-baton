"""``baton knowledge harvest`` — convert artefacts into knowledge entries.

Two harvesters:

* ``baton knowledge harvest adrs`` — walk a docs tree for Architecture
  Decision Records and convert each into a knowledge document under
  ``.claude/knowledge/<pack>/`` (default pack: ``decisions``).
* ``baton knowledge harvest reviews`` — pull PR review comments via the
  ``gh`` CLI and distil salient ones into
  ``.claude/knowledge/lessons-from-pr-<N>.md``.

Both writers are idempotent: re-running on unchanged input is a no-op.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "knowledge",
        help="Manage knowledge packs (harvest, etc.)",
    )
    sub = p.add_subparsers(dest="subcommand")

    p_harvest = sub.add_parser(
        "harvest",
        help="Harvest knowledge entries from existing artefacts (ADRs, PR reviews)",
    )
    h_sub = p_harvest.add_subparsers(dest="harvest_kind")

    # adrs
    p_adrs = h_sub.add_parser(
        "adrs",
        help="Convert Architecture Decision Records into knowledge documents",
    )
    p_adrs.add_argument(
        "--source-dir",
        default=None,
        help="Root to walk for ADR markdown files (default: docs/)",
    )
    p_adrs.add_argument(
        "--target-pack",
        default="decisions",
        help='Knowledge pack name to write into (default: "decisions")',
    )
    p_adrs.add_argument(
        "--knowledge-root",
        default=None,
        help="Override the .claude/knowledge root (mostly for tests)",
    )

    # reviews
    p_reviews = h_sub.add_parser(
        "reviews",
        help="Harvest lessons from a GitHub PR's review comments",
    )
    p_reviews.add_argument(
        "--pr",
        type=int,
        required=True,
        help="Pull-request number to harvest",
    )
    p_reviews.add_argument(
        "--repo",
        default=None,
        help="Repository slug 'owner/name' (default: auto-detect via gh)",
    )
    p_reviews.add_argument(
        "--knowledge-root",
        default=None,
        help="Override the .claude/knowledge root (mostly for tests)",
    )

    return p


def handler(args: argparse.Namespace) -> None:
    if not getattr(args, "subcommand", None):
        print("usage: baton knowledge <subcommand>")
        print("subcommands: harvest")
        sys.exit(1)
    if args.subcommand == "harvest":
        kind = getattr(args, "harvest_kind", None)
        if kind == "adrs":
            _harvest_adrs(args)
        elif kind == "reviews":
            _harvest_reviews(args)
        else:
            print("usage: baton knowledge harvest {adrs|reviews}")
            sys.exit(1)
    else:
        print(f"error: unknown knowledge subcommand: {args.subcommand}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def _harvest_adrs(args: argparse.Namespace) -> None:
    from agent_baton.core.knowledge.adr_harvester import harvest_adrs

    source_dir = Path(args.source_dir) if args.source_dir else Path("docs")
    if not source_dir.is_dir():
        print(f"error: source dir does not exist: {source_dir}")
        sys.exit(1)

    knowledge_root = Path(args.knowledge_root) if args.knowledge_root else None
    result = harvest_adrs(
        source_dir,
        target_pack=args.target_pack,
        knowledge_root=knowledge_root,
    )

    print(f"Scanned {result.scanned} ADR file(s) under {source_dir}")
    print(f"  Wrote:   {len(result.written)}")
    print(f"  Skipped: {len(result.skipped)} (already up to date)")
    print(f"  Pack:    {result.pack_dir}")
    if result.written:
        print()
        print("Wrote:")
        for path in result.written:
            print(f"  - {path}")


def _harvest_reviews(args: argparse.Namespace) -> None:
    from agent_baton.core.knowledge.review_harvester import harvest_reviews

    repo = args.repo
    if not repo:
        repo = _detect_repo_slug()
    if not repo:
        _file_gh_unavailable_bead(
            f"Could not determine GitHub repo for PR #{args.pr} — pass --repo OWNER/NAME"
        )
        print("error: --repo is required when gh CLI cannot detect the repo.")
        sys.exit(1)

    knowledge_root = Path(args.knowledge_root) if args.knowledge_root else None
    try:
        result = harvest_reviews(
            args.pr,
            repo,
            knowledge_root=knowledge_root,
        )
    except RuntimeError as exc:
        # Most likely: gh CLI missing or auth failure. File a bead and exit.
        _file_gh_unavailable_bead(str(exc))
        print(f"error: {exc}")
        sys.exit(1)

    print(f"Harvested PR {repo}#{args.pr}")
    print(f"  Comments fetched: {result.comments_in}")
    print(f"  Salient kept:     {result.comments_kept}")
    if result.by_severity:
        breakdown = ", ".join(
            f"{sev}={n}" for sev, n in sorted(result.by_severity.items())
        )
        print(f"  Severity:         {breakdown}")
    if result.written:
        print(f"  Wrote:            {result.target_path}")
    else:
        print(f"  Skipped:          {result.skipped_reason or 'no changes'}")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _detect_repo_slug() -> str | None:
    """Best-effort ``owner/name`` detection via ``gh repo view``."""
    import shutil
    import subprocess

    if shutil.which("gh") is None:
        return None
    try:
        proc = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    slug = proc.stdout.strip()
    return slug or None


def _file_gh_unavailable_bead(message: str) -> None:
    """Best-effort bead creation for gh CLI unavailability.

    Silently succeeds whether or not the bead store is available — the
    user-facing error is printed by the caller.
    """
    try:
        from datetime import datetime, timezone

        from agent_baton.core.storage.bead_store import BeadStore  # type: ignore
        from agent_baton.models.bead import Bead  # type: ignore

        bead = Bead(
            bead_id=f"bd-knowharvest-{int(datetime.now(timezone.utc).timestamp())}",
            type="warning",
            title="knowledge.harvest.reviews degraded — gh CLI unavailable",
            body=message,
            tags=["knowledge", "harvester", "gh"],
        )
        store = BeadStore()
        store.write(bead)
    except Exception:
        # Bead system unavailable — degradation is acceptable; the user
        # already sees the error message in stderr.
        return
