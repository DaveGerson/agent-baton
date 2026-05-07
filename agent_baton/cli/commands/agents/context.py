"""``baton agent-context`` -- inspect harvested agent_context rows.

Wave 2.2 read-side CLI for :class:`ContextHarvester`.  Lists rows from
the project ``agent_context`` table, scoped to a single agent.

Usage::

    baton agent-context show backend-engineer
    baton agent-context show backend-engineer --domain agent_baton

Plain table output: domain | files | last_task | updated_at | summary.

Renamed from ``baton context`` to avoid collision with
``observe/context_cmd.py``'s situational-awareness ``context`` parser.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from agent_baton.core.intel.context_harvester import ContextHarvester

_BATON_DB = "baton.db"
_DEFAULT_CONTEXT_ROOT = Path(".claude/team-context")


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "agent-context",
        help="Inspect harvested agent_context rows (Wave 2.2 ContextHarvester)",
    )
    sub = p.add_subparsers(dest="action", required=True)

    show = sub.add_parser(
        "show",
        help="Show agent_context rows for a single agent",
    )
    show.add_argument(
        "agent_name",
        help="Agent name to inspect (e.g. backend-engineer)",
    )
    show.add_argument(
        "--domain",
        default="",
        help="Optional: filter to a single domain (first path segment)",
    )
    show.add_argument(
        "--db",
        default=str(_DEFAULT_CONTEXT_ROOT / _BATON_DB),
        help="Path to baton.db (default: .claude/team-context/baton.db)",
    )
    return p


def _open_conn(db_path: str) -> sqlite3.Connection:
    p = Path(db_path)
    if not p.exists():
        raise SystemExit(f"baton.db not found at {p}")
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    return conn


def _print_table(rows: list[dict[str, str]]) -> None:
    if not rows:
        print("(no rows)")
        return

    # Compute column widths (capped)
    headers = ["domain", "last_task", "updated_at", "summary"]
    widths = {
        "domain": max(6, min(24, max(len(r.get("domain") or "") for r in rows))),
        "last_task": max(9, min(20, max(len(r.get("last_task_id") or "") for r in rows))),
        "updated_at": 20,
        "summary": 60,
    }

    line = "  ".join(h.ljust(widths[h]) for h in headers)
    print(line)
    print("  ".join("-" * widths[h] for h in headers))

    for r in rows:
        summary = (r.get("expertise_summary") or "").replace("\n", " ")
        if len(summary) > widths["summary"]:
            summary = summary[: widths["summary"] - 3] + "..."
        cols = [
            (r.get("domain") or "").ljust(widths["domain"])[: widths["domain"]],
            (r.get("last_task_id") or "").ljust(widths["last_task"])[: widths["last_task"]],
            (r.get("updated_at") or "").ljust(widths["updated_at"])[: widths["updated_at"]],
            summary.ljust(widths["summary"]),
        ]
        print("  ".join(cols))


def handler(args: argparse.Namespace) -> None:
    if args.action != "show":
        raise SystemExit(f"Unknown context action: {args.action!r}")

    conn = _open_conn(args.db)
    try:
        rows = ContextHarvester.fetch_all_for_agent(conn, args.agent_name)
        if args.domain:
            rows = [r for r in rows if (r.get("domain") or "") == args.domain]
        if not rows:
            scope = f" / domain={args.domain}" if args.domain else ""
            print(f"No agent_context rows for agent={args.agent_name}{scope}.")
            return
        _print_table(rows)
        print(f"\n{len(rows)} row(s).")
    finally:
        conn.close()
