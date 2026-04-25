"""``baton knowledge brief`` -- generate a codebase briefing.

Produces a concise summary (stack, layout, entry points, conventions,
tests, and health snapshot) that dispatched agents can read instead of
re-discovering the basics every run.

Delegates to:
    agent_baton.core.knowledge.codebase_brief.CodebaseBriefer
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.core.knowledge.codebase_brief import (
    CodebaseBriefer,
    render,
)

_DEFAULT_SAVE_PATH = Path(".claude/team-context/codebase-brief.md")


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register ``baton knowledge brief``.

    The ``knowledge`` parent parser owns sub-subcommands; ``brief`` is the
    only one for now (K2.7).  Future ``knowledge X`` commands can extend
    the same parent by adding their own sub-parsers — but care must be
    taken to avoid registering the parent twice from multiple modules.
    """
    p = subparsers.add_parser(
        "knowledge",
        help="Codebase knowledge utilities (briefing, packs, etc.)",
    )
    sub = p.add_subparsers(dest="knowledge_cmd", metavar="SUBCOMMAND")

    brief_p = sub.add_parser(
        "brief",
        help=(
            "Generate a concise codebase briefing for new agents "
            "(stack, layout, entry points, conventions, tests, health)"
        ),
    )
    brief_p.add_argument(
        "--project",
        dest="project",
        metavar="DIR",
        default=None,
        help="Project directory to brief (default: current working directory)",
    )
    brief_p.add_argument(
        "--save",
        action="store_true",
        default=False,
        help=(
            "Write the brief to .claude/team-context/codebase-brief.md "
            "instead of stdout"
        ),
    )
    brief_p.add_argument(
        "--format",
        dest="fmt",
        choices=["markdown", "json"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    """Dispatch ``baton knowledge ...`` subcommands."""
    sub = getattr(args, "knowledge_cmd", None)
    if sub is None:
        print(
            "usage: baton knowledge {brief}\n\n"
            "Run `baton knowledge brief --help` for details.",
            file=sys.stderr,
        )
        sys.exit(2)

    if sub == "brief":
        _handle_brief(args)
        return

    print(f"error: unknown knowledge subcommand '{sub}'", file=sys.stderr)
    sys.exit(2)


def _handle_brief(args: argparse.Namespace) -> None:
    """Generate and emit the codebase brief."""
    project = Path(args.project).resolve() if args.project else Path.cwd().resolve()
    if not project.is_dir():
        print(f"error: project path is not a directory: {project}", file=sys.stderr)
        sys.exit(1)

    brief = CodebaseBriefer.generate(project)
    rendered = render(brief, fmt=args.fmt)

    if args.save:
        # When saving, always write markdown to the canonical path even if
        # --format=json was requested (the file convention is markdown).
        # JSON savers can pipe stdout themselves.
        if args.fmt == "json":
            target = project / ".claude" / "team-context" / "codebase-brief.json"
        else:
            target = project / _DEFAULT_SAVE_PATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
        print(f"Brief written to {target}")
        return

    sys.stdout.write(rendered)
