"""baton context — situational awareness for Claude agents.

Subcommands
-----------
current                What task is executing right now (phase, step, agent)
briefing <agent>       Markdown briefing for an agent about to be dispatched
gaps                   Knowledge gaps to watch for during execution

This command is designed to be called by Claude agents before or during
execution to retrieve situational awareness from baton.db.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Shared flags helper
# ---------------------------------------------------------------------------


def _add_shared_flags(p: argparse.ArgumentParser) -> None:
    """Attach --db, --central, and --json to a subcommand parser.

    These are added per-subcommand so that argparse resolves them correctly
    when the flags follow the subcommand keyword (e.g.
    ``baton context current --db PATH``).
    """
    p.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help="Explicit path to baton.db",
    )
    p.add_argument(
        "--central",
        action="store_true",
        help="Query the central database at ~/.baton/central.db",
    )
    p.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Machine-readable JSON output",
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "context",
        help="Situational awareness for agents: current task, briefings, gaps",
    )

    sub = p.add_subparsers(dest="subcommand")

    # baton context current [--db PATH] [--central] [--json]
    p_current = sub.add_parser(
        "current",
        help="Show what task, phase, step, and agent are currently active",
    )
    _add_shared_flags(p_current)

    # baton context briefing <agent-name> [--db PATH] [--central] [--json]
    p_briefing = sub.add_parser(
        "briefing",
        help="Print a performance briefing for an agent about to be dispatched",
    )
    p_briefing.add_argument(
        "agent_name",
        metavar="AGENT",
        help="Name of the agent to brief (e.g. backend-engineer--python)",
    )
    _add_shared_flags(p_briefing)

    # baton context gaps [--min-frequency N] [--agent NAME] [--db] [--json]
    p_gaps = sub.add_parser(
        "gaps",
        help="Show knowledge gaps identified across recent retrospectives",
    )
    p_gaps.add_argument(
        "--min-frequency",
        type=int,
        default=1,
        metavar="N",
        dest="min_frequency",
        help="Minimum occurrence count to include a gap (default: 1)",
    )
    p_gaps.add_argument(
        "--agent",
        metavar="NAME",
        dest="agent_name",
        default=None,
        help="Filter gaps to a specific agent",
    )
    _add_shared_flags(p_gaps)

    return p


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    """Dispatch to the appropriate ``context`` subcommand.

    Opens a query engine (project-local ``baton.db`` by default, or
    ``~/.baton/central.db`` with ``--central``), dispatches to the
    matching subcommand, and ensures the engine is closed on exit.

    Args:
        args: Parsed CLI arguments with ``subcommand``, database options,
            and subcommand-specific fields.
    """
    from agent_baton.core.storage.queries import open_query_engine

    sub = getattr(args, "subcommand", None)
    if sub is None:
        _print_help()
        return

    db_path = Path(args.db) if getattr(args, "db", None) else None
    central = getattr(args, "central", False)

    engine = open_query_engine(db_path=db_path, central=central)
    try:
        if sub == "current":
            _current(args, engine)
        elif sub == "briefing":
            _briefing(args, engine)
        elif sub == "gaps":
            _gaps(args, engine)
        else:
            _print_help()
    finally:
        engine.close()


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def _current(args: argparse.Namespace, engine: object) -> None:
    """Print what is currently executing."""
    # engine is typed as object here to keep the top-level import lazy.
    ctx = engine.current_context()  # type: ignore[attr-defined]

    if getattr(args, "output_json", False):
        print(json.dumps(ctx, indent=2))
        return

    if not ctx.get("has_active_task"):
        print("No active task.")
        return

    print("Active task:")
    print(f"  Task ID:       {ctx['task_id']}")
    summary = ctx.get("task_summary", "")
    if summary:
        print(f"  Summary:       {summary}")
    risk = ctx.get("risk_level", "")
    if risk:
        print(f"  Risk:          {risk}")
    print(f"  Status:        {ctx['status']}")
    print(f"  Phase:         {ctx['current_phase']}")
    print(f"  Step index:    {ctx['current_step_index']}")
    agent = ctx.get("current_agent", "")
    if agent:
        print(f"  Current agent: {agent}")
    started = ctx.get("started_at", "")
    if started:
        print(f"  Started:       {started}")


def _briefing(args: argparse.Namespace, engine: object) -> None:
    """Print a performance briefing for a named agent."""
    agent_name = args.agent_name
    briefing = engine.agent_briefing(agent_name)  # type: ignore[attr-defined]

    if getattr(args, "output_json", False):
        print(json.dumps({"agent_name": agent_name, "briefing": briefing}, indent=2))
        return

    print(briefing)


def _gaps(args: argparse.Namespace, engine: object) -> None:
    """Print knowledge gaps, optionally filtered by agent."""
    min_freq = getattr(args, "min_frequency", 1)
    agent_filter = getattr(args, "agent_name", None)

    gaps = engine.knowledge_gaps(min_frequency=min_freq)  # type: ignore[attr-defined]

    if agent_filter:
        gaps = [g for g in gaps if g.affected_agent == agent_filter]

    if getattr(args, "output_json", False):
        data = [
            {
                "description": g.description,
                "affected_agent": g.affected_agent,
                "frequency": g.frequency,
                "tasks": g.tasks,
            }
            for g in gaps
        ]
        print(json.dumps(data, indent=2))
        return

    if not gaps:
        msg = "No knowledge gaps found"
        if agent_filter:
            msg += f" for agent '{agent_filter}'"
        print(msg + ".")
        return

    header = "Knowledge Gaps"
    if agent_filter:
        header += f" — {agent_filter}"
    print(header)
    print()

    for gap in gaps:
        freq_label = f"(seen {gap.frequency}x)" if gap.frequency > 1 else ""
        agent_label = f"[{gap.affected_agent}]" if gap.affected_agent else ""
        parts = [part for part in [agent_label, freq_label] if part]
        suffix = "  " + "  ".join(parts) if parts else ""
        print(f"  - {gap.description}{suffix}")
        if gap.tasks:
            tasks_preview = ", ".join(gap.tasks[:3])
            if len(gap.tasks) > 3:
                tasks_preview += f" (+{len(gap.tasks) - 3} more)"
            print(f"    tasks: {tasks_preview}")


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------


def _print_help() -> None:
    print(
        "Usage: baton context <subcommand> [options]\n"
        "\n"
        "Subcommands:\n"
        "  current                     What task, phase, and agent are active\n"
        "  briefing <agent-name>       Performance briefing before dispatch\n"
        "  gaps [--agent NAME]         Knowledge gaps from retrospectives\n"
        "\n"
        "Options (per subcommand):\n"
        "  --db PATH       Explicit path to baton.db\n"
        "  --central       Query ~/.baton/central.db\n"
        "  --json          Machine-readable JSON output\n",
        end="",
    )
