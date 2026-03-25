"""``baton patterns`` -- display and refresh learned orchestration patterns.

Patterns capture recurring agent sequencing strategies that correlate
with successful outcomes. The pattern learner analyses the usage log
and identifies high-confidence templates for the planner to reuse.

Delegates to:
    agent_baton.core.learn.pattern_learner.PatternLearner
"""
from __future__ import annotations

import argparse

from agent_baton.core.learn.pattern_learner import PatternLearner


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "patterns",
        help="Show or refresh learned orchestration patterns",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Re-analyse the usage log and update learned-patterns.json",
    )
    p.add_argument(
        "--task-type",
        metavar="TYPE",
        dest="task_type",
        help="Show patterns for a specific task type",
    )
    p.add_argument(
        "--min-confidence",
        type=float,
        metavar="N",
        dest="min_confidence",
        default=0.0,
        help="Filter patterns by minimum confidence (0.0-1.0)",
    )
    p.add_argument(
        "--recommendations",
        action="store_true",
        help="Show sequencing recommendations for each task type",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    learner = PatternLearner()

    if args.recommendations:
        patterns = learner.load_patterns()
        if not patterns:
            print("No patterns available for recommendations.")
            print("Run 'baton patterns --refresh' first.")
            return
        task_types = sorted(set(p.task_type for p in patterns))
        print("Sequencing Recommendations:")
        print()
        for tt in task_types:
            result = learner.recommend_sequencing(tt)
            if result is not None:
                agents, confidence = result
                print(f"  {tt}")
                print(f"    Agents:     {', '.join(agents)}")
                print(f"    Confidence: {confidence:.0%}")
                print()
        return

    if args.refresh:
        patterns = learner.refresh()
        if not patterns:
            print("No patterns found — not enough qualifying usage records.")
            print("Ensure at least 5 tasks share a sequencing_mode and meet the")
            print("confidence threshold (default 0.70).")
            return
        print(f"Refreshed {len(patterns)} pattern(s) -> learned-patterns.json")
        _print_patterns(patterns, args.min_confidence)
        return

    if args.task_type:
        patterns = learner.get_patterns_for_task(args.task_type)
        if not patterns:
            print(f"No patterns found for task type '{args.task_type}'.")
            return
        filtered = [p for p in patterns if p.confidence >= args.min_confidence]
        if not filtered:
            print(
                f"No patterns for '{args.task_type}' above confidence "
                f"{args.min_confidence:.0%}."
            )
            return
        _print_patterns(filtered, args.min_confidence)
        return

    # Default: show all stored patterns
    patterns = learner.load_patterns()
    if not patterns:
        print("No learned patterns found.")
        print("Run 'baton patterns --refresh' to analyse the usage log.")
        return

    filtered = [p for p in patterns if p.confidence >= args.min_confidence]
    if not filtered:
        print(
            f"No patterns above confidence {args.min_confidence:.0%}. "
            "Try a lower --min-confidence value."
        )
        return
    _print_patterns(filtered, args.min_confidence)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _confidence_bar(confidence: float, width: int = 10) -> str:
    filled = round(confidence * width)
    return "[" + "=" * filled + " " * (width - filled) + "]"


def _print_patterns(patterns: list, min_confidence: float) -> None:
    shown = [p for p in patterns if p.confidence >= min_confidence]
    if not shown:
        return

    print(f"Learned Patterns ({len(shown)}):")
    print()

    for p in shown:
        bar = _confidence_bar(p.confidence)
        agents_str = ", ".join(p.recommended_agents) if p.recommended_agents else "(none)"
        print(f"  {p.pattern_id}")
        print(f"    Task type:  {p.task_type}")
        print(f"    Stack:      {p.stack or 'any'}")
        print(f"    Confidence: {p.confidence:.0%} {bar}")
        print(f"    Success:    {p.success_rate:.0%}  |  Samples: {p.sample_size}  |  Avg tokens: {p.avg_token_cost:,}")
        print(f"    Template:   {p.recommended_template}")
        print(f"    Agents:     {agents_str}")
        print()
