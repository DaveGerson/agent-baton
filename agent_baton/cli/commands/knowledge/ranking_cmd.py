"""``baton knowledge ranking`` — show all known docs ranked by historical effectiveness.

Reads ``v_knowledge_effectiveness`` from the central database and prints every
document with its composite score, sorted descending.

Examples::

    baton knowledge ranking
    baton knowledge ranking --output json
    baton knowledge ranking --db ~/.baton/central.db
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from agent_baton.cli.commands.knowledge import (
    ensure_parent_parser,
    register_handler,
)
from agent_baton.core.intel.knowledge_ranker import KnowledgeRanker, RankedDoc


SUBCOMMAND = "ranking"

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"


def _add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--output",
        choices=("table", "json"),
        default="table",
        help="Output format: table (default) or json",
    )
    p.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help=f"Path to the SQLite database (default: {_CENTRAL_DB_DEFAULT})",
    )


def register(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> argparse.ArgumentParser:
    """Hook into the shared ``baton knowledge`` parent parser."""
    sub = ensure_parent_parser(subparsers)
    ranking_p = sub.add_parser(
        SUBCOMMAND,
        help="Show all known docs ranked by historical effectiveness score",
    )
    _add_arguments(ranking_p)
    register_handler(SUBCOMMAND, _run_ranking)
    parent = subparsers.choices["knowledge"]
    return parent


def handler(args: argparse.Namespace) -> None:
    """Auto-discovery entry point — delegate to the parent dispatcher."""
    dispatch = getattr(args, "_dispatch", None)
    if dispatch is None:
        raise SystemExit("baton knowledge: dispatcher missing")
    dispatch(args)


# ---------------------------------------------------------------------------
# Subcommand body
# ---------------------------------------------------------------------------


def _run_ranking(args: argparse.Namespace) -> None:
    db_path = Path(args.db).resolve() if getattr(args, "db", None) else None
    ranker = KnowledgeRanker(db_path=db_path)
    rows = ranker.rank_all_known()

    if getattr(args, "output", "table") == "json":
        print(json.dumps([r.to_dict() for r in rows], indent=2))
    else:
        print(_render_table(rows))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render_table(rows: Sequence[RankedDoc]) -> str:
    if not rows:
        return "No knowledge telemetry recorded yet. Run some tasks first."
    lines = [
        "| Pack | Document | Final | Effectiveness | Recency | Usage |",
        "|------|----------|-------|---------------|---------|-------|",
    ]
    for r in rows:
        pack = r.pack_name or "(standalone)"
        lines.append(
            f"| {pack} | {r.document_name} "
            f"| {r.final_score:.3f} "
            f"| {r.effectiveness_score:.3f} "
            f"| {r.recency_factor:.3f} "
            f"| {r.usage_factor:.3f} |"
        )
    return "\n".join(lines)
