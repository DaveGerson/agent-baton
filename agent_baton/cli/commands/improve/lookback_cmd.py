"""``baton lookback`` — historical failure analysis CLI.

Aggregates data from beads, traces, retrospectives, and execution state
to classify why plans failed and recommend fixes.

Delegates to:
    agent_baton.core.improve.lookback.LookbackAnalyzer
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "lookback",
        help="Historical failure analysis — classify why plans failed and recommend fixes",
        description=(
            "Aggregate data from beads, traces, retrospectives, and execution "
            "state to classify failure modes and surface recurring patterns."
        ),
    )
    p.add_argument(
        "task_id",
        nargs="?",
        default=None,
        metavar="TASK_ID",
        help="Analyze a single task ID.  Omit to analyze a range.",
    )
    p.add_argument(
        "--since",
        default=None,
        metavar="ISO8601",
        help="Lower bound for range analysis (e.g. '2026-01-01').",
    )
    p.add_argument(
        "--until",
        default=None,
        metavar="ISO8601",
        help="Upper bound for range analysis (e.g. '2026-12-31').",
    )
    p.add_argument(
        "--recurring",
        action="store_true",
        help=(
            "Run cross-task pattern detection across all stored executions "
            "and report recurring failure patterns."
        ),
    )
    p.add_argument(
        "--status",
        default="failed",
        metavar="STATUS",
        help=(
            "Execution status filter for range analysis "
            "(default: 'failed'; use 'all' for every status)."
        ),
    )
    p.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Emit machine-readable JSON instead of markdown.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Auto-apply safe recommendations (those with auto_applicable=True). "
            "Currently prints what would be applied — full apply support coming soon."
        ),
    )
    return p


def handler(args: argparse.Namespace) -> None:
    storage = _discover_storage()
    bead_store = _discover_bead_store()
    team_context_root = Path(".claude/team-context").resolve()

    from agent_baton.core.improve.lookback import LookbackAnalyzer

    analyzer = LookbackAnalyzer(
        storage=storage,
        bead_store=bead_store,
        team_context_root=team_context_root,
    )

    if storage is None:
        print(
            "warning: no storage backend found — "
            "run from a project directory with an active baton.db or execution state.",
            file=sys.stderr,
        )

    if args.recurring:
        patterns = analyzer.detect_recurring_patterns()
        if not patterns:
            print("No recurring failure patterns detected.")
            return
        if args.output_json:
            print(json.dumps([p.to_dict() for p in patterns], indent=2))
        else:
            print(f"Recurring Failure Patterns ({len(patterns)} found):")
            print()
            for pat in patterns:
                rate_pct = int(pat.failure_rate * 100)
                print(
                    f"  [{pat.pattern_type}] {pat.description} "
                    f"— {pat.frequency} task(s), {rate_pct}% failure rate"
                )
                if pat.recommended_action:
                    print(f"    => {pat.recommended_action}")
        return

    if args.task_id:
        report = analyzer.analyze_task(args.task_id)
    else:
        report = analyzer.analyze_range(
            since=args.since,
            until=args.until,
            status_filter=args.status,
        )

    if args.output_json:
        print(json.dumps(report.to_dict(), indent=2))
        return

    print(analyzer.to_markdown(report))

    if args.apply:
        safe_recs = [r for r in report.recommendations if r.auto_applicable]
        if not safe_recs:
            print("No auto-applicable recommendations found.")
            return
        print(f"\nAuto-applicable recommendations ({len(safe_recs)}):")
        for rec in safe_recs:
            print(f"  [{rec.action}] {rec.target}: {rec.detail}")
        print(
            "\nNote: automatic application is not yet implemented. "
            "Apply the above changes manually or via 'baton learn apply'."
        )


# ---------------------------------------------------------------------------
# Helpers — storage discovery follows the same pattern as scores.py
# ---------------------------------------------------------------------------


def _discover_storage() -> object | None:
    try:
        from agent_baton.core.storage import detect_backend, get_project_storage

        context_root = Path(".claude/team-context").resolve()
        if detect_backend(context_root) == "sqlite":
            return get_project_storage(context_root)
        # Fall through to file-backend attempt
        return get_project_storage(context_root)
    except Exception:
        return None


def _discover_bead_store() -> object | None:
    try:
        from agent_baton.core.engine.bead_store import BeadStore

        db_path = Path(".claude/team-context/baton.db")
        if db_path.exists():
            return BeadStore(db_path)
        return None
    except Exception:
        return None
