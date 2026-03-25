"""baton query — typed and ad-hoc queries against baton.db (per-project).

This command queries the **local** ``baton.db`` for the current project.
It covers execution history, agent step results, tasks, gates, and
learned patterns scoped to this project.

For **cross-project** analytics across all projects use ``baton cquery``,
which targets the central ``~/.baton/central.db``.

Subcommands (predefined)
------------------------
agent-reliability       Agent success rates and token costs
agent-history NAME      Recent step results for a specific agent
tasks                   Recent task list
task-detail TASK_ID     Full breakdown for one task
knowledge-gaps          Recurring knowledge gaps across tasks
roster-recommendations  Consensus roster recommendations
gate-stats              Gate pass rates by type
cost-by-type            Token costs grouped by task type
cost-by-agent           Token costs grouped by agent
current                 What is running right now
patterns                Learned patterns with confidence scores

Ad-hoc SQL
----------
baton query --sql "SELECT ..."

Output formats
--------------
--format table   Human-readable table (default)
--format json    JSON array
--format csv     Comma-separated values
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "query",
        help=(
            "Query this project's execution history and agent performance (baton.db).  "
            "For cross-project analytics use 'baton cquery'."
        ),
    )

    # ── Predefined subcommand (positional, optional) ──────────────────────
    p.add_argument(
        "subcommand",
        nargs="?",
        metavar="SUBCOMMAND",
        choices=[
            "agent-reliability",
            "agent-history",
            "tasks",
            "task-detail",
            "knowledge-gaps",
            "roster-recommendations",
            "gate-stats",
            "cost-by-type",
            "cost-by-agent",
            "current",
            "patterns",
        ],
        help=(
            "Predefined query to run.  "
            "One of: agent-reliability, agent-history, tasks, task-detail, "
            "knowledge-gaps, roster-recommendations, gate-stats, cost-by-type, "
            "cost-by-agent, current, patterns"
        ),
    )

    # ── Subcommand arguments ───────────────────────────────────────────────
    p.add_argument(
        "target",
        nargs="?",
        metavar="ARG",
        help=(
            "Subcommand argument.  "
            "For agent-history: agent name.  "
            "For task-detail: task ID."
        ),
    )

    # ── Ad-hoc SQL ─────────────────────────────────────────────────────────
    p.add_argument(
        "--sql",
        metavar="SQL",
        help="Run arbitrary read-only SQL (SELECT only)",
    )

    # ── Shared options ─────────────────────────────────────────────────────
    p.add_argument(
        "--format",
        choices=["table", "json", "csv"],
        default="table",
        metavar="FORMAT",
        help="Output format: table (default), json, csv",
    )
    p.add_argument(
        "--days",
        type=int,
        default=30,
        metavar="N",
        help="Days window for time-bounded queries (default: 30)",
    )
    p.add_argument(
        "--limit",
        type=int,
        default=20,
        metavar="N",
        help="Maximum rows to return for list queries (default: 20)",
    )
    p.add_argument(
        "--status",
        metavar="STATUS",
        help="Filter tasks by status (for the 'tasks' subcommand)",
    )
    p.add_argument(
        "--min-frequency",
        type=int,
        default=1,
        metavar="N",
        dest="min_frequency",
        help="Minimum occurrence frequency for knowledge-gaps (default: 1)",
    )
    p.add_argument(
        "--db",
        metavar="PATH",
        help="Explicit path to baton.db (overrides default discovery)",
    )
    p.add_argument(
        "--central",
        action="store_true",
        help="Query the central database at ~/.baton/central.db",
    )

    return p


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    from agent_baton.core.storage.queries import open_query_engine

    db_path = Path(args.db) if getattr(args, "db", None) else None
    central = getattr(args, "central", False)

    engine = open_query_engine(db_path=db_path, central=central)
    try:
        _dispatch(args, engine)
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _dispatch(args: argparse.Namespace, engine: Any) -> None:
    """Route to the appropriate predefined query or execute ad-hoc SQL.

    Handles all 11 predefined subcommands (agent-reliability, agent-history,
    tasks, task-detail, knowledge-gaps, roster-recommendations, gate-stats,
    cost-by-type, cost-by-agent, current, patterns) plus ``--sql`` for
    arbitrary read-only queries.

    Args:
        args: Parsed CLI arguments with ``subcommand``, ``target``, ``format``,
            ``days``, ``limit``, and other query-specific options.
        engine: An open query engine instance from
            :func:`~agent_baton.core.storage.queries.open_query_engine`.
    """
    fmt = args.format

    # ── Ad-hoc SQL ─────────────────────────────────────────────────────────
    if getattr(args, "sql", None):
        try:
            rows = engine.raw_query(args.sql)
        except ValueError as exc:
            _err(str(exc))
            return
        _render(rows, fmt, title="Query Results")
        return

    sub = getattr(args, "subcommand", None)
    if sub is None:
        _print_help()
        return

    # ── agent-reliability ──────────────────────────────────────────────────
    if sub == "agent-reliability":
        data = engine.agent_reliability(days=args.days)
        rows = [
            {
                "agent": s.agent_name,
                "steps": s.total_steps,
                "success_rate": f"{s.success_rate:.0%}",
                "successes": s.successes,
                "failures": s.failures,
                "retries": s.total_retries,
                "tokens": s.total_tokens,
                "avg_duration_s": s.avg_duration,
            }
            for s in data
        ]
        _render(rows, fmt, title=f"Agent Reliability (last {args.days} days)")
        return

    # ── agent-history NAME ─────────────────────────────────────────────────
    if sub == "agent-history":
        agent_name = args.target
        if not agent_name:
            _err("agent-history requires an agent name: baton query agent-history <name>")
            return
        rows = engine.agent_history(agent_name, limit=args.limit)
        if not rows:
            print(f"No history found for agent '{agent_name}'.")
            return
        _render(rows, fmt, title=f"Agent History: {agent_name}")
        return

    # ── tasks ──────────────────────────────────────────────────────────────
    if sub == "tasks":
        data = engine.task_list(
            status=getattr(args, "status", None), limit=args.limit
        )
        if not data:
            print("No tasks found.")
            return
        rows = [
            {
                "task_id": t.task_id,
                "summary": t.task_summary[:50] + "..." if len(t.task_summary) > 50 else t.task_summary,
                "status": t.status,
                "risk": t.risk_level,
                "steps": f"{t.steps_completed}/{t.steps_total}",
                "agents": len(t.agents),
                "started_at": t.started_at[:19] if t.started_at else "",
            }
            for t in data
        ]
        _render(rows, fmt, title="Recent Tasks")
        return

    # ── task-detail TASK_ID ────────────────────────────────────────────────
    if sub == "task-detail":
        task_id = args.target
        if not task_id:
            _err("task-detail requires a task ID: baton query task-detail <task-id>")
            return
        detail = engine.task_detail(task_id)
        if detail is None:
            print(f"Task '{task_id}' not found.")
            return

        if fmt == "json":
            print(json.dumps(detail, indent=2, default=str))
            return

        # Human-readable structured output
        plan = detail.get("plan") or {}
        print(f"Task: {detail['task_id']}")
        print(f"  Status:        {detail['status']}")
        print(f"  Summary:       {plan.get('task_summary', '')}")
        print(f"  Risk:          {plan.get('risk_level', '')}")
        print(f"  Phase:         {detail['current_phase']}")
        print(f"  Started:       {detail['started_at']}")
        if detail.get("completed_at"):
            print(f"  Completed:     {detail['completed_at']}")
        print()

        steps = detail.get("steps") or []
        if steps:
            print(f"Plan steps ({len(steps)}):")
            for s in steps:
                print(
                    f"  [{s['phase_id']}] {s['step_id']:<8} "
                    f"  {s['agent_name']:<35}  "
                    f"{s.get('task_description', '')[:40]}"
                )
            print()

        results = detail.get("step_results") or []
        if results:
            print(f"Step results ({len(results)}):")
            for r in results:
                status_icon = "+" if r["status"] == "complete" else "!"
                tokens = r.get("estimated_tokens") or 0
                print(
                    f"  [{status_icon}] {r['step_id']:<8}  "
                    f"{r['agent_name']:<35}  "
                    f"tokens={tokens:<6}  "
                    f"{r.get('outcome', '')[:40]}"
                )
                if r.get("error"):
                    print(f"      error: {r['error'][:80]}")
            print()

        gates = detail.get("gates") or []
        if gates:
            print(f"Gates ({len(gates)}):")
            for g in gates:
                icon = "PASS" if g.get("passed") else "FAIL"
                print(
                    f"  [{icon}] phase={g['phase_id']}  "
                    f"type={g['gate_type']}"
                )
        return

    # ── knowledge-gaps ─────────────────────────────────────────────────────
    if sub == "knowledge-gaps":
        data = engine.knowledge_gaps(min_frequency=args.min_frequency)
        if not data:
            print("No knowledge gaps found.")
            return
        rows = [
            {
                "frequency": g.frequency,
                "affected_agent": g.affected_agent,
                "description": g.description[:60],
                "task_count": len(g.tasks),
            }
            for g in data
        ]
        _render(rows, fmt, title="Knowledge Gaps")
        return

    # ── roster-recommendations ─────────────────────────────────────────────
    if sub == "roster-recommendations":
        data = engine.roster_recommendations()
        if not data:
            print("No roster recommendations found.")
            return
        rows = [
            {
                "action": r["action"],
                "target": r["target"],
                "votes": r["count"],
                "reason": r["reason_sample"][:60],
            }
            for r in data
        ]
        _render(rows, fmt, title="Roster Recommendations")
        return

    # ── gate-stats ─────────────────────────────────────────────────────────
    if sub == "gate-stats":
        data = engine.gate_stats()
        if not data:
            print("No gate results found.")
            return
        rows = [
            {
                "gate_type": g.gate_type,
                "total": g.total,
                "passed": g.passed,
                "failed": g.total - g.passed,
                "pass_rate": f"{g.pass_rate:.0%}",
            }
            for g in data
        ]
        _render(rows, fmt, title="Gate Statistics")
        return

    # ── cost-by-type ───────────────────────────────────────────────────────
    if sub == "cost-by-type":
        data = engine.cost_by_task_type()
        if not data:
            print("No cost data found.")
            return
        rows = [
            {
                "task_type": c.task_type,
                "task_count": c.task_count,
                "total_tokens": c.total_tokens,
                "avg_tokens": c.avg_tokens,
            }
            for c in data
        ]
        _render(rows, fmt, title="Token Cost by Task Type")
        return

    # ── cost-by-agent ──────────────────────────────────────────────────────
    if sub == "cost-by-agent":
        data = engine.cost_by_agent(days=args.days)
        if not data:
            print("No cost data found.")
            return
        _render(data, fmt, title=f"Token Cost by Agent (last {args.days} days)")
        return

    # ── current ────────────────────────────────────────────────────────────
    if sub == "current":
        ctx = engine.current_context()
        if fmt == "json":
            print(json.dumps(ctx, indent=2))
            return
        if not ctx.get("has_active_task"):
            print("No active task.")
            return
        print("Current execution:")
        print(f"  Task ID:       {ctx['task_id']}")
        print(f"  Summary:       {ctx.get('task_summary', '')}")
        print(f"  Status:        {ctx['status']}")
        print(f"  Risk:          {ctx.get('risk_level', '')}")
        print(f"  Phase:         {ctx['current_phase']}")
        print(f"  Step index:    {ctx['current_step_index']}")
        print(f"  Current agent: {ctx.get('current_agent', '')}")
        print(f"  Started:       {ctx.get('started_at', '')}")
        return

    # ── patterns ───────────────────────────────────────────────────────────
    if sub == "patterns":
        data = engine.patterns()
        if not data:
            print("No learned patterns found.")
            return
        rows = [
            {
                "pattern_id": p["pattern_id"][:20],
                "task_type": p["task_type"],
                "stack": p.get("stack") or "",
                "confidence": f"{p.get('confidence', 0):.0%}",
                "sample_size": p.get("sample_size", 0),
                "success_rate": f"{p.get('success_rate', 0):.0%}",
                "avg_tokens": p.get("avg_token_cost", 0),
            }
            for p in data
        ]
        _render(rows, fmt, title="Learned Patterns")
        return

    _print_help()


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def _render(rows: list[dict], fmt: str, title: str = "") -> None:
    """Dispatch to the appropriate output formatter (table, JSON, or CSV).

    Args:
        rows: List of dictionaries representing query result rows.  Column
            order is determined by the key order of the first row.
        fmt: Output format -- ``"table"`` for fixed-width aligned columns,
            ``"json"`` for pretty-printed JSON array, or ``"csv"`` for
            comma-separated values.
        title: Optional title printed above the output.
    """
    if not rows:
        if title:
            print(f"{title}: (no data)")
        else:
            print("(no data)")
        return

    if fmt == "json":
        _render_json(rows)
    elif fmt == "csv":
        _render_csv(rows, title=title)
    else:
        _render_table(rows, title=title)


def _render_table(rows: list[dict], title: str = "") -> None:
    """Pretty-print *rows* as a fixed-width table."""
    if not rows:
        return

    headers = list(rows[0].keys())

    # Compute column widths: max of header width and all cell widths.
    col_widths: dict[str, int] = {}
    for h in headers:
        col_widths[h] = len(h)
    for row in rows:
        for h in headers:
            col_widths[h] = max(col_widths[h], len(str(row.get(h, ""))))

    sep = "  "
    header_line = sep.join(h.upper().ljust(col_widths[h]) for h in headers)
    divider = sep.join("-" * col_widths[h] for h in headers)

    if title:
        print(title)
        print()
    print(header_line)
    print(divider)
    for row in rows:
        line = sep.join(str(row.get(h, "")).ljust(col_widths[h]) for h in headers)
        print(line)


def _render_json(rows: list[dict]) -> None:
    """Print *rows* as a pretty-printed JSON array."""
    print(json.dumps(rows, indent=2, default=str))


def _render_csv(rows: list[dict], title: str = "") -> None:
    """Write *rows* as CSV to stdout."""
    if not rows:
        return
    if title:
        print(f"# {title}")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    print(buf.getvalue(), end="")


def _err(msg: str) -> None:
    """Print an error message to stderr."""
    print(f"error: {msg}", file=sys.stderr)


def _print_help() -> None:
    """Print a short usage summary."""
    print(
        "Usage: baton query <subcommand> [ARG] [--format table|json|csv]\n"
        "\n"
        "Predefined queries:\n"
        "  agent-reliability              Agent success rates and token costs\n"
        "  agent-history <name>           Recent results for a specific agent\n"
        "  tasks                          Recent task list\n"
        "  task-detail <task-id>          Full task breakdown\n"
        "  knowledge-gaps                 Recurring knowledge gaps\n"
        "  roster-recommendations         Consensus roster recommendations\n"
        "  gate-stats                     Gate pass rates by type\n"
        "  cost-by-type                   Token costs by task type\n"
        "  cost-by-agent                  Token costs by agent\n"
        "  current                        What is running right now\n"
        "  patterns                       Learned patterns\n"
        "\n"
        "Ad-hoc SQL (read-only):\n"
        "  baton query --sql \"SELECT agent_name, COUNT(*) FROM step_results "
        "GROUP BY agent_name\"\n"
        "\n"
        "Database selection:\n"
        "  --db PATH       Explicit path to baton.db\n"
        "  --central       Query ~/.baton/central.db\n"
        "\n"
        "For cross-project analytics (all projects in central.db) use:\n"
        "  baton cquery agents\n"
        "  baton cquery costs --format json\n"
        "  baton cquery --tables\n"
    )
