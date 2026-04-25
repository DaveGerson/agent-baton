"""CLI command: ``baton merge`` — merge readiness reporting.

Subcommands
-----------
readiness   Generate a Merge-Readiness Pack (MRP) markdown bundle for
            the current branch / PR.

Flags (readiness)
-----------------
--task-id ID    Pull plan + step results for this execution.  Defaults
                to the active task ID stored in baton.db.
--branch NAME   Branch to report on.  Defaults to current HEAD.
--base NAME     Base branch for diff / commit-range comparisons.
                Defaults to ``master`` (falls back to ``main``).
--output PATH   Write the pack to this file instead of stdout.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "merge",
        help="Generate merge-readiness artifacts for a branch / PR",
    )
    p.add_argument(
        "subcommand",
        nargs="?",
        default="readiness",
        choices=["readiness"],
        help="Subcommand (default: readiness)",
    )
    p.add_argument(
        "--task-id",
        default=None,
        help="Execution task ID (default: active task in baton.db)",
    )
    p.add_argument(
        "--branch",
        default=None,
        help="Branch to report on (default: current HEAD)",
    )
    p.add_argument(
        "--base",
        default=None,
        help="Base branch for diff / range queries (default: master|main)",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Write Markdown to this file instead of stdout",
    )
    return p


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    if args.subcommand != "readiness":
        print(f"error: unknown subcommand {args.subcommand!r}", file=sys.stderr)
        sys.exit(2)

    from agent_baton.core.release.mrp import MRPBuilder

    builder = MRPBuilder()
    pack = builder.build(
        task_id=args.task_id,
        branch=args.branch,
        base=args.base,
    )
    text = pack.to_markdown()

    if args.output:
        out_path = Path(args.output).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"Wrote MRP to {out_path}")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
