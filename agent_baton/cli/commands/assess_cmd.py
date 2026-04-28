"""``baton assess`` -- org-readiness assessment.

H3.6 (bd-0dea): a single CLI that scores how prepared an organisation is
to delegate work to baton-orchestrated agents.

Usage::

    baton assess readiness [--project DIR] [--format markdown|json]

Delegates to:
    :class:`agent_baton.core.improve.readiness.ReadinessAssessor`
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.core.improve.readiness import ReadinessAssessor


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "assess",
        help="Assess project readiness for agent delegation",
    )
    sub = p.add_subparsers(dest="assess_subcommand")

    readiness = sub.add_parser(
        "readiness",
        help="Score how ready the project is to delegate work to baton agents",
    )
    readiness.add_argument(
        "--project",
        metavar="DIR",
        default=".",
        help="Project root to assess (default: current directory)",
    )
    readiness.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    sub = getattr(args, "assess_subcommand", None)
    if sub is None or sub == "readiness":
        # Default to readiness when no subcommand given (covers the common case).
        if sub is None:
            print(
                "usage: baton assess readiness [--project DIR] [--format markdown|json]",
                file=sys.stderr,
            )
            sys.exit(2)
        _handle_readiness(args)
        return

    print(f"unknown assess subcommand: {sub}", file=sys.stderr)
    sys.exit(2)


def _handle_readiness(args: argparse.Namespace) -> None:
    project = Path(getattr(args, "project", ".")).resolve()
    if not project.is_dir():
        print(f"error: not a directory: {project}", file=sys.stderr)
        sys.exit(1)

    assessor = ReadinessAssessor()
    report = assessor.assess(project)

    fmt = getattr(args, "format", "markdown")
    if fmt == "json":
        print(report.to_json())
    else:
        print(report.to_markdown())
