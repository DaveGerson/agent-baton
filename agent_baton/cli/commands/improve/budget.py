"""baton budget — show or refresh budget tier recommendations."""
from __future__ import annotations

import argparse

from agent_baton.core.learn.budget_tuner import BudgetTuner


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "budget",
        help="Show or refresh budget tier recommendations based on usage history",
    )
    p.add_argument(
        "--recommend",
        action="store_true",
        help="Re-analyse the usage log and display fresh recommendations",
    )
    p.add_argument(
        "--save",
        action="store_true",
        help="Save recommendations to budget-recommendations.json",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    tuner = BudgetTuner()

    if args.save:
        recs = tuner.analyze()
        path = tuner.save_recommendations()
        if recs:
            print(f"Saved {len(recs)} recommendation(s) -> {path}")
        else:
            print(f"No recommendations to save (all tiers are well-sized). Written empty list to {path}")
        _print_recommendations(recs)
        return

    if args.recommend:
        recs = tuner.analyze()
        if not recs:
            print("No budget adjustments needed — all task types are within their expected tier boundaries.")
            return
        print(f"Budget Recommendations ({len(recs)}):")
        print()
        _print_recommendations(recs)
        return

    # Default: load previously saved recommendations
    loaded = tuner.load_recommendations()
    if loaded is None:
        print("No saved recommendations found.")
        print("Run 'baton budget --recommend' to analyse the usage log.")
        return
    if not loaded:
        print("No budget adjustments needed (saved recommendations are empty).")
        return

    print(f"Saved Budget Recommendations ({len(loaded)}):")
    print()
    _print_recommendations(loaded)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _direction(current: str, recommended: str) -> str:
    _order = {"lean": 0, "standard": 1, "full": 2}
    if _order.get(recommended, 0) > _order.get(current, 0):
        return "UPGRADE"
    return "DOWNGRADE"


def _print_recommendations(recs: list) -> None:
    if not recs:
        return
    for rec in recs:
        direction = _direction(rec.current_tier, rec.recommended_tier)
        print(f"  {rec.task_type}")
        print(f"    Action:    {direction} {rec.current_tier} -> {rec.recommended_tier}")
        print(f"    Reason:    {rec.reason}")
        print(f"    Confidence: {rec.confidence:.0%}  |  Samples: {rec.sample_size}")
        print(
            f"    Avg: {rec.avg_tokens_used:,}  |  "
            f"Median: {rec.median_tokens_used:,}  |  "
            f"p95: {rec.p95_tokens_used:,}"
        )
        if rec.potential_savings > 0:
            print(f"    Savings:   ~{rec.potential_savings:,} tokens/task")
        print()
