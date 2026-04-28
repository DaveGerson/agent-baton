"""``baton knowledge effectiveness`` — per-doc effectiveness + ROI report (K2.2).

Surface for ``agent_baton.core.knowledge.effectiveness``.  Renders either a
sorted Markdown table or a JSON document.

Examples
--------
::

    baton knowledge effectiveness
    baton knowledge effectiveness --pack data-validation-rules
    baton knowledge effectiveness --since-days 7 --format json
    baton knowledge effectiveness --stale --format json
"""
from __future__ import annotations

import argparse
import json
from typing import Sequence

from agent_baton.cli.commands.knowledge import (
    ensure_parent_parser,
    register_handler,
)
from agent_baton.core.knowledge.effectiveness import (
    DocEffectiveness,
    StaleDoc,
    compute_effectiveness,
    find_stale_docs,
)


SUBCOMMAND = "effectiveness"


def _add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--pack",
        metavar="PACK",
        default=None,
        help="Restrict the report to a single knowledge pack",
    )
    p.add_argument(
        "--since-days",
        type=int,
        default=30,
        metavar="N",
        help="Rolling window in days for effectiveness rollup (0 = all time)",
    )
    p.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown)",
    )
    p.add_argument(
        "--stale",
        action="store_true",
        help="Show only stale candidates (foreshadows K2.3 cleanup)",
    )
    p.add_argument(
        "--threshold-days",
        type=int,
        default=90,
        metavar="N",
        help="Stale-by-age threshold in days (used with --stale)",
    )


def register(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Hook into the shared ``baton knowledge`` parent parser."""
    sub = ensure_parent_parser(subparsers)
    eff_p = sub.add_parser(
        SUBCOMMAND,
        help="Show per-doc effectiveness + ROI scores",
    )
    _add_arguments(eff_p)
    register_handler(SUBCOMMAND, _run_effectiveness)
    # Auto-discovery keys the dispatch table by the parent parser's name
    # ("knowledge").  Returning the parent here is intentional — sibling
    # modules do the same and all funnel through the parent's _dispatch.
    parent = subparsers.choices["knowledge"]
    return parent


def handler(args: argparse.Namespace) -> None:
    """Auto-discovery entry point — delegate to the parent dispatcher."""
    dispatch = getattr(args, "_dispatch", None)
    if dispatch is None:
        # Should not happen: ensure_parent_parser always installs _dispatch.
        raise SystemExit("baton knowledge: dispatcher missing")
    dispatch(args)


# ---------------------------------------------------------------------------
# Subcommand body
# ---------------------------------------------------------------------------


def _run_effectiveness(args: argparse.Namespace) -> None:
    if args.stale:
        rows = find_stale_docs(threshold_days=args.threshold_days)
        if args.format == "json":
            print(json.dumps([r.to_dict() for r in rows], indent=2))
        else:
            print(_render_stale_markdown(rows))
        return

    rows = compute_effectiveness(
        pack=args.pack, since_days=args.since_days
    )
    if args.format == "json":
        print(json.dumps([r.to_dict() for r in rows], indent=2))
    else:
        print(_render_effectiveness_markdown(rows))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_effectiveness_markdown(rows: Sequence[DocEffectiveness]) -> str:
    if not rows:
        return "No knowledge attachments recorded yet."
    lines = [
        "| Pack | Document | Attachments | Success rate | ROI / k-tok | Last used |",
        "|------|----------|-------------|--------------|-------------|-----------|",
    ]
    for r in rows:
        pack = r.pack_name or "(standalone)"
        last_used = (r.last_used or "—")[:19]
        lines.append(
            f"| {pack} | {r.document_name} | {r.attachments} | "
            f"{r.effectiveness_score:.2%} | {r.roi_score:+.2f} | {last_used} |"
        )
    return "\n".join(lines)


def _render_stale_markdown(rows: Sequence[StaleDoc]) -> str:
    if not rows:
        return "No stale knowledge documents detected."
    lines = [
        "| Pack | Document | Attachments | Effectiveness | Last used | Reasons |",
        "|------|----------|-------------|---------------|-----------|---------|",
    ]
    for r in rows:
        pack = r.pack_name or "(standalone)"
        last_used = (r.last_used or "—")[:19]
        reasons = ", ".join(r.reasons)
        lines.append(
            f"| {pack} | {r.document_name} | {r.attachments} | "
            f"{r.effectiveness_score:.2%} | {last_used} | {reasons} |"
        )
    return "\n".join(lines)
