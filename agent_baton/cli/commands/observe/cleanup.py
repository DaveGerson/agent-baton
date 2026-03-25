"""``baton cleanup`` -- archive or remove old execution artifacts.

Removes traces, events, retrospectives, and other execution artifacts
older than a configurable retention period. Supports --dry-run to
preview what would be removed without making changes.

Delegates to:
    agent_baton.core.observe.archiver.DataArchiver
"""
from __future__ import annotations

import argparse
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "cleanup",
        help="Remove old execution artifacts (traces, events, retrospectives)",
    )
    p.add_argument(
        "--retention-days",
        type=int,
        default=90,
        help="Keep files newer than this many days (default: 90)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without deleting",
    )
    p.add_argument(
        "--team-context",
        default=".claude/team-context",
        help="Path to team-context directory",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    from agent_baton.core.observe.archiver import DataArchiver

    root = Path(args.team_context).resolve()
    archiver = DataArchiver(root)

    if args.dry_run:
        print(archiver.summary(retention_days=args.retention_days))
        print("\n  (dry run — no files removed)")
    else:
        print(archiver.summary(retention_days=args.retention_days))
        counts = archiver.cleanup(
            retention_days=args.retention_days,
        )
        total = sum(counts.values())
        if total > 0:
            print(f"\n  Removed {total} item(s).")
            for cat, n in counts.items():
                if n > 0:
                    print(f"    {cat}: {n}")
        else:
            print("\n  Nothing to clean up.")
