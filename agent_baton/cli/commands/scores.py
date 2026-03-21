"""baton scores — show agent performance scorecards."""
from __future__ import annotations

import argparse

from agent_baton.core.scoring import PerformanceScorer


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("scores", help="Show agent performance scorecards")
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--agent", metavar="NAME", help="Show scorecard for a specific agent",
    )
    group.add_argument(
        "--write", action="store_true", help="Write scorecard report to disk",
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

    report = scorer.generate_report()
    print(report)
