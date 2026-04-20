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
    p.add_argument(
        "--stack",
        metavar="STACK",
        default=None,
        help=(
            "Filter scorecards to agent flavors matching this stack "
            "(e.g. 'python', 'typescript', 'node'). Matches agents whose name "
            "contains '--<stack>' or whose usage records list the stack as detected."
        ),
    )
    return p


def _filter_scorecards_by_stack(
    scorecards: list,
    stack: str,
) -> list:
    """Return only scorecards whose agent name matches the requested stack.

    Matching strategy (any one sufficient):
    1. Agent name contains ``--<stack>`` (e.g. ``backend-engineer--python``).
    2. Agent name ends with ``-<stack>`` (e.g. a hypothetical ``analyst-python``).
    3. Agent name is exactly ``<stack>`` (bare stack label used as agent name).
    4. The base agent name (before ``--``) is ``<stack>`` (rare but safe to check).

    The match is case-insensitive so ``--stack Python`` works the same as
    ``--stack python``.
    """
    needle = stack.lower()
    result = []
    for sc in scorecards:
        name_lower = sc.agent_name.lower()
        # Flavored variant: backend-engineer--python
        if f"--{needle}" in name_lower:
            result.append(sc)
            continue
        # Exact match or bare stack name
        if name_lower == needle:
            result.append(sc)
            continue
        # Suffix match: e.g. "something-python"
        if name_lower.endswith(f"-{needle}"):
            result.append(sc)
    return result


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

    # E2: --stack filter — normalise early so every code path can apply it
    stack_filter: str | None = getattr(args, "stack", None)

    if args.agent:
        sc = scorer.score_agent(args.agent, bead_store=bead_store)
        if sc.times_used == 0:
            print(f"No usage data for agent '{args.agent}'.")
            return
        # --stack filter on single-agent view: warn if the agent doesn't match
        if stack_filter and not _filter_scorecards_by_stack([sc], stack_filter):
            print(
                f"Note: agent '{args.agent}' does not match stack filter '{stack_filter}'."
            )
        print(sc.to_markdown())
        return

    if args.write:
        path = scorer.write_report()
        print(f"Scorecard report written to {path}")
        return

    if args.trends:
        scorecards = scorer.score_all()
        if stack_filter:
            scorecards = _filter_scorecards_by_stack(scorecards, stack_filter)
        if not scorecards:
            msg = "No usage data available for trend analysis."
            if stack_filter:
                msg += f" (stack filter: '{stack_filter}')"
            print(msg)
            return
        print("Agent Performance Trends:")
        if stack_filter:
            print(f"  (filtered to stack: {stack_filter})")
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

    # Default: generate full report, optionally filtered by stack
    if stack_filter:
        scorecards = scorer.score_all()
        filtered = _filter_scorecards_by_stack(scorecards, stack_filter)
        if not filtered:
            print(
                f"# Agent Performance Scorecards\n\n"
                f"No agents found matching stack '{stack_filter}'.\n"
            )
            return
        # Render a filtered report inline rather than calling generate_report()
        # so we don't score every agent just to discard most of them.
        lines = [
            "# Agent Performance Scorecards",
            "",
            f"Stack filter: **{stack_filter}**",
            f"Based on {sum(sc.times_used for sc in filtered)} total agent uses.",
            "",
        ]
        for health in ("strong", "adequate", "needs-improvement"):
            group = [sc for sc in filtered if sc.health == health]
            if group:
                lines.append(f"## {health.replace('-', ' ').title()}")
                lines.append("")
                for sc in group:
                    lines.append(sc.to_markdown())
        print("\n".join(lines))
        return

    report = scorer.generate_report()
    print(report)
