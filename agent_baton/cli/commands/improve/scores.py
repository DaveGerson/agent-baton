"""``baton scores`` -- show agent performance scorecards.

Scorecards aggregate agent performance metrics (success rate, retry
rate, gate pass rate, token efficiency) into a health indicator.
Used to identify underperforming agents that may benefit from prompt
evolution.

Display modes:
    * ``baton scores`` -- Full report across all agents.
    * ``baton scores --agent NAME`` -- Scorecard for a specific agent.
    * ``baton scores --write`` -- Write the report to disk.
    * ``baton scores --trends`` -- Show performance trend direction
      (improving, stable, degrading) for each agent.

Delegates to:
    :class:`~agent_baton.core.improve.scoring.PerformanceScorer`
"""
from __future__ import annotations

import argparse

from agent_baton.core.improve.scoring import PerformanceScorer


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("scores", help="Show agent performance scorecards")
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--agent", metavar="NAME", help="Show scorecard for a specific agent",
    )
    group.add_argument(
        "--write", action="store_true", help="Write scorecard report to disk",
    )
    group.add_argument(
        "--trends", action="store_true", help="Show performance trends for all agents",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    scorer = PerformanceScorer()

    if args.agent:
        sc = scorer.score_agent(args.agent)
        if sc.times_used == 0:
            print(f"No usage data for agent '{args.agent}'.")
            return
        print(sc.to_markdown())
        return

    if args.write:
        path = scorer.write_report()
        print(f"Scorecard report written to {path}")
        return

    if args.trends:
        scorecards = scorer.score_all()
        if not scorecards:
            print("No usage data available for trend analysis.")
            return
        print("Agent Performance Trends:")
        print()
        for sc in scorecards:
            trend = scorer.detect_trends(sc.agent_name)
            trend_indicator = {"improving": "+", "degrading": "-", "stable": "="}.get(trend, "?")
            print(f"  [{trend_indicator}] {sc.agent_name}: {trend} (health={sc.health})")
        return

    report = scorer.generate_report()
    print(report)
