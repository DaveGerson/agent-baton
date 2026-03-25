"""``baton budget`` -- show or refresh budget tier recommendations.

The budget tuner analyses actual token consumption across tasks and
compares it to budget tier ceilings (lean, standard, full).  When a
task type consistently uses fewer tokens than its tier allows, the
tuner recommends a downgrade to save resources.

Display modes:
    * ``baton budget`` -- Show previously saved recommendations.
    * ``baton budget --recommend`` -- Re-analyse and display fresh
      recommendations.
    * ``baton budget --save`` -- Save recommendations to
      ``budget-recommendations.json``.
    * ``baton budget --auto-apply`` -- Show only auto-applicable downgrades
      above 80% confidence.

Delegates to:
    :class:`~agent_baton.core.learn.budget_tuner.BudgetTuner`
"""
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
    p.add_argument(
        "--auto-apply",
        action="store_true",
        dest="auto_apply",
        help="Show only auto-applicable (downgrade) recommendations above 80%% confidence",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    tuner = BudgetTuner()

    if args.auto_apply:
        eligible = tuner.auto_apply_recommendations()
        if not eligible:
            print("No auto-applicable budget recommendations (downgrades above 80% confidence).")
            return
        print(f"Auto-Applicable Budget Recommendations ({len(eligible)}):")
        print("  (Only downgrades to cheaper tiers are auto-applicable)")
        print()
        _print_recommendations(eligible)
        return

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
    """Determine whether a budget tier change is an upgrade or downgrade.

    Args:
        current: Current budget tier name (lean, standard, full).
        recommended: Recommended budget tier name.

    Returns:
        ``"UPGRADE"`` if the recommended tier is more expensive, otherwise
        ``"DOWNGRADE"``.
    """
    _order = {"lean": 0, "standard": 1, "full": 2}
    if _order.get(recommended, 0) > _order.get(current, 0):
        return "UPGRADE"
    return "DOWNGRADE"


def _print_recommendations(recs: list) -> None:
    """Print a formatted list of budget tier recommendations.

    Each recommendation shows the direction (upgrade/downgrade), reason,
    confidence, sample size, token statistics (avg, median, p95), and
    potential savings.

    Args:
        recs: List of recommendation objects from the budget tuner.
    """
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
