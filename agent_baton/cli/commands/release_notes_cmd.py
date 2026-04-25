"""``baton release notes`` -- auto-generate release notes (R3.3).

Standalone command for now. Once R3.1's ``release_cmd.py`` lands, the
``notes`` subparser can be lifted into that command's subparser tree;
the handler here is parameter-compatible.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.core.release.notes import ReleaseNotesBuilder


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "release",
        help="Release management commands (notes, ...)",
    )
    rsub = p.add_subparsers(dest="release_command")

    n = rsub.add_parser(
        "notes",
        help="Auto-generate release notes for a commit range or release",
    )
    n.add_argument(
        "--release",
        dest="release_id",
        default=None,
        help="Release entity ID (R3.1). Falls back to commit-only mode if unavailable.",
    )
    n.add_argument(
        "--from",
        dest="from_ref",
        default=None,
        help="Git ref to start from (default: master)",
    )
    n.add_argument(
        "--to",
        dest="to_ref",
        default=None,
        help="Git ref to end at (default: HEAD)",
    )
    n.add_argument(
        "--format",
        choices=("markdown", "html", "json"),
        default="markdown",
        help="Output format (default: markdown)",
    )
    n.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write output to PATH instead of stdout",
    )
    n.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (default: current working directory)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    sub = getattr(args, "release_command", None)
    if sub != "notes":
        # ``baton release`` with no subcommand or unknown subcommand.
        print(
            "usage: baton release notes [--release ID] [--from REF --to REF] "
            "[--format markdown|html|json] [--output PATH]",
            file=sys.stderr,
        )
        sys.exit(2)

    builder = ReleaseNotesBuilder(repo_root=args.repo_root)
    notes = builder.build(
        release_id=args.release_id,
        from_ref=args.from_ref,
        to_ref=args.to_ref,
    )

    if args.format == "markdown":
        rendered = notes.to_markdown()
    elif args.format == "html":
        rendered = notes.to_html()
    else:
        rendered = notes.to_json()

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote release notes to {args.output}")
    else:
        print(rendered)
