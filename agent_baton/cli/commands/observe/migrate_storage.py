"""``baton migrate-storage`` -- DEPRECATED alias.

Use ``baton storage migrate`` instead.

This module keeps the old top-level ``baton migrate-storage`` command working
so that existing scripts and CI pipelines are not broken.  A deprecation
warning is printed to stderr on every invocation.
"""
from __future__ import annotations

import argparse
import sys


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "migrate-storage",
        help="[DEPRECATED] Use 'baton storage migrate' instead",
        description=(
            "DEPRECATED: Use 'baton storage migrate' instead.\n\n"
            "Scans the team-context directory for existing JSON/JSONL files "
            "and imports them into baton.db."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without writing to the database",
    )
    file_handling = p.add_mutually_exclusive_group()
    file_handling.add_argument(
        "--keep-files",
        action="store_true",
        default=True,
        help="Keep original files after migration (default)",
    )
    file_handling.add_argument(
        "--remove-files",
        action="store_true",
        default=False,
        help="Move original files to pre-sqlite-backup/ after successful import",
    )
    p.add_argument(
        "--team-context",
        default=".claude/team-context",
        metavar="PATH",
        help="Path to team-context directory (default: .claude/team-context)",
    )
    p.add_argument(
        "--verify",
        action="store_true",
        help="After migrating, compare source file counts against DB row counts",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    print(
        "warning: `baton migrate-storage` is deprecated; use `baton storage migrate`"
        " instead. This alias will be removed in a future release.",
        file=sys.stderr,
    )
    from agent_baton.cli.commands.observe.storage_cmd import _cmd_migrate
    _cmd_migrate(args)
