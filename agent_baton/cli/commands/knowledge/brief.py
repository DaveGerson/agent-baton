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

from agent_baton.cli.commands.knowledge import (
    dispatch as _knowledge_dispatch,
    get_or_create_parser,
    register_handler,
)
from agent_baton.core.knowledge.codebase_brief import (
    CodebaseBriefer,
    render,
)

_DEFAULT_SAVE_PATH = Path(".claude/team-context/codebase-brief.md")


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register ``baton knowledge brief`` via the cooperative parser helper."""
    p, sub = get_or_create_parser(subparsers)

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
    register_handler("brief", _handle_brief)
    return p


def handler(args: argparse.Namespace) -> None:
    """Auto-discovery entry.

    When invoked through the CLI ``register()`` path, the cooperative
    knowledge dispatcher routes via ``args.knowledge_cmd`` to the bound
    ``_handle_brief``.  Tests that build their own ``argparse.Namespace``
    and call ``handler`` directly (without first calling ``register``) get
    the same behavior because the dispatcher falls through to a direct
    branch on ``knowledge_cmd == 'brief'``.
    """
    if getattr(args, "knowledge_cmd", None) == "brief":
        _handle_brief(args)
        return
    _knowledge_dispatch(args)


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
