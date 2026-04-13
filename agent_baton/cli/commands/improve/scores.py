"""``baton scores`` -- show agent performance scorecards.

Scorecards aggregate agent performance metrics (success rate, retry
rate, gate pass rate, token efficiency) into a health indicator.

Delegates to:
    agent_baton.core.improve.scoring.PerformanceScorer
"""
from __future__ import annotations

import argparse
from pathlib import Path

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
    group.add_argument(
        "--teams", action="store_true", help="Show team composition effectiveness",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    # Wire storage backend so PerformanceScorer can read retrospectives
    # from SQLite when the project uses SQLite storage mode.
    storage = None
    try:
        from agent_baton.core.storage import detect_backend, get_project_storage
        context_root = Path(".claude/team-context").resolve()
        if detect_backend(context_root) == "sqlite":
            storage = get_project_storage(context_root)
    except Exception:
        pass  # Fall back to filesystem mode
    scorer = PerformanceScorer(storage=storage)

    # Wire bead store for F12 quality metrics in scorecards
    bead_store = None
    try:
        from agent_baton.core.engine.bead_store import BeadStore
        db_path = Path(".claude/team-context/baton.db")
        if db_path.exists():
            bead_store = BeadStore(db_path)
    except Exception:
        pass

    if args.agent:
        sc = scorer.score_agent(args.agent, bead_store=bead_store)
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

    if args.teams:
        report = scorer.generate_team_report()
        print(report)
        return

    report = scorer.generate_report()
    print(report)
