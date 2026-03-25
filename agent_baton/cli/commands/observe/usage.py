"""``baton usage`` -- show usage statistics from the usage log.

Provides aggregate and per-agent views of orchestration usage data
including task counts, agent frequency, token consumption, retry
rates, and gate pass rates.

Display modes:
    * ``baton usage`` -- Overall summary across all tasks.
    * ``baton usage --recent N`` -- Last N usage records with details.
    * ``baton usage --agent NAME`` -- Per-agent statistics and model usage.

Delegates to:
    :class:`~agent_baton.core.observe.usage.UsageLogger`
"""
from __future__ import annotations

import argparse

from agent_baton.core.observe.usage import UsageLogger


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("usage", help="Show usage statistics")
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--recent",
        type=int,
        metavar="N",
        help="Show the N most recent records",
    )
    group.add_argument(
        "--agent",
        metavar="NAME",
        help="Show stats for a specific agent",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    logger = UsageLogger()

    if args.agent:
        # Per-agent stats
        stats = logger.agent_stats(args.agent)
        if stats["times_used"] == 0:
            print(f"No records found for agent '{args.agent}'.")
            return

        gate_rate = stats["gate_pass_rate"]
        gate_str = f"{gate_rate:.0%}" if gate_rate is not None else "n/a"

        print(f"Agent stats: {args.agent}")
        print(f"  Times used:      {stats['times_used']}")
        print(f"  Total retries:   {stats['total_retries']}")
        print(f"  Avg retries:     {stats['avg_retries']}")
        print(f"  Gate pass rate:  {gate_str}")
        if stats["models_used"]:
            print("  Models used:")
            for model, count in sorted(stats["models_used"].items(), key=lambda x: -x[1]):
                print(f"    {model:<20} {count}")
        return

    if args.recent is not None:
        # Recent records
        records = logger.read_recent(args.recent)
        if not records:
            print("No usage records found.")
            return
        print(f"Recent {len(records)} record(s):")
        for rec in records:
            agents_str = ", ".join(a.name for a in rec.agents_used) or "(none)"
            print(f"  {rec.timestamp}  [{rec.outcome or 'no outcome'}]  {rec.task_id}")
            print(f"    agents: {agents_str}")
            print(f"    risk: {rec.risk_level}  gates: {rec.gates_passed}P/{rec.gates_failed}F")
            if rec.notes:
                print(f"    notes: {rec.notes}")
        return

    # Default: summary
    stats = logger.summary()
    total = stats["total_tasks"]

    if total == 0:
        print("No usage records found.")
        return

    print(f"Usage Summary ({total} task{'s' if total != 1 else ''}):")
    print(f"  Total agents used:     {stats['total_agents_used']}")
    print(f"  Estimated tokens:      {stats['total_estimated_tokens']:,}")
    print(f"  Avg agents/task:       {stats['avg_agents_per_task']}")
    print(f"  Avg retries/task:      {stats['avg_retries_per_task']}")

    if stats["outcome_counts"]:
        print()
        print("Outcomes:")
        for outcome, count in sorted(stats["outcome_counts"].items(), key=lambda x: -x[1]):
            print(f"  {outcome:<18} {count}")

    if stats["agent_frequency"]:
        print()
        print("Top Agents:")
        sorted_agents = sorted(
            stats["agent_frequency"].items(), key=lambda x: -x[1]
        )
        for name, count in sorted_agents[:10]:
            uses = "use" if count == 1 else "uses"
            print(f"  {name:<35} {count} {uses}")
