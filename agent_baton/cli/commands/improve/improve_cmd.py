"""``baton improve`` -- DEPRECATED shim.

Use ``baton learn improve`` instead.

This module keeps the old top-level ``baton improve`` command working so that
existing scripts and CI pipelines are not broken.  A deprecation warning is
printed to stderr on every invocation.

The real implementation lives here (``handler`` / ``_improve_handler_impl``)
and is imported directly by ``learn_cmd.py`` for the ``baton learn improve``
subcommand path.

Delegates to:
    agent_baton.core.improve.loop.ImprovementLoop
"""
from __future__ import annotations

import argparse
import sys

from agent_baton.core.improve.loop import ImprovementLoop


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "improve",
        help="[DEPRECATED] Use 'baton learn improve' instead",
        description="DEPRECATED: Use 'baton learn improve' instead.",
    )
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--run",
        action="store_true",
        help="Run a full improvement cycle",
    )
    group.add_argument(
        "--force",
        action="store_true",
        help="Force-run a cycle bypassing the data-threshold check entirely",
    )
    group.add_argument(
        "--report",
        action="store_true",
        help="Show the latest improvement report",
    )
    group.add_argument(
        "--experiments",
        action="store_true",
        help="Show active experiments",
    )
    group.add_argument(
        "--history",
        action="store_true",
        help="Show all improvement reports",
    )
    # Threshold overrides (only meaningful with --run; ignored with --force)
    p.add_argument(
        "--min-tasks",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Minimum total tasks before analysis fires (overrides default and "
            "BATON_MIN_TASKS env var for this run only)"
        ),
    )
    p.add_argument(
        "--interval",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Re-analyze every N new tasks (overrides default and "
            "BATON_ANALYSIS_INTERVAL env var for this run only)"
        ),
    )
    return p


def handler(args: argparse.Namespace) -> None:
    """Deprecated top-level entry point — prints warning then delegates."""
    print(
        "WARN: 'baton improve' is deprecated, use 'baton learn improve'",
        file=sys.stderr,
    )
    _improve_handler_impl(args)


def _improve_handler_impl(args: argparse.Namespace) -> None:
    """Real implementation shared by top-level shim and 'baton learn improve'."""
    from pathlib import Path
    from agent_baton.core.improve.triggers import TriggerEvaluator
    from agent_baton.core.storage import get_project_storage
    from agent_baton.models.improvement import TriggerConfig

    # Resolve storage backend for the current project so the improvement
    # pipeline reads from SQLite instead of stale JSONL.
    context_root = Path(".claude/team-context").resolve()
    try:
        storage = get_project_storage(context_root)
    except Exception:
        storage = None

    # Construct bead_store and ledger from storage.db_path when available
    # (SqliteStorage only; FileStorage has no db_path and fails gracefully).
    bead_store = None
    ledger = None
    if storage is not None:
        try:
            from agent_baton.core.engine.bead_store import BeadStore
            bead_store = BeadStore(storage.db_path)
        except Exception:
            pass
        try:
            from agent_baton.core.learn.ledger import LearningLedger
            ledger = LearningLedger(storage.db_path)
        except Exception:
            pass

    # Build a custom TriggerConfig when CLI overrides are supplied.
    trigger_evaluator = None
    if getattr(args, "min_tasks", None) is not None or getattr(args, "interval", None) is not None:
        base_config = TriggerConfig.from_env()
        if args.min_tasks is not None:
            base_config.min_tasks_before_analysis = args.min_tasks
        if args.interval is not None:
            base_config.analysis_interval_tasks = args.interval
        trigger_evaluator = TriggerEvaluator(
            config=base_config,
            storage=storage,
            bead_store=bead_store,
            ledger=ledger,
        )

    loop = ImprovementLoop(
        trigger_evaluator=trigger_evaluator,
        storage=storage,
        bead_store=bead_store,
        ledger=ledger,
    )

    if args.run or args.force:
        report = loop.run_cycle(force=args.force)
        if report.skipped:
            print(f"Improvement cycle skipped: {report.reason}")
            return
        print(f"Improvement cycle complete: {report.report_id}")
        print(f"  Anomalies:    {len(report.anomalies)}")
        print(f"  Recommendations: {len(report.recommendations)}")
        print(f"  Auto-applied: {len(report.auto_applied)}")
        print(f"  Escalated:    {len(report.escalated)}")
        print(f"  Active experiments: {len(report.active_experiments)}")
        if report.auto_applied:
            print()
            print("Auto-applied:")
            for rec_id in report.auto_applied:
                print(f"  - {rec_id}")
        if report.escalated:
            print()
            print("Escalated (needs human review):")
            for rec_id in report.escalated:
                print(f"  - {rec_id}")
        return

    if args.experiments:
        import warnings
        from agent_baton.core.improve.experiments import ExperimentManager
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            mgr = ExperimentManager()
        active = mgr.active()
        if not active:
            print("No active experiments.")
            return
        print(f"Active Experiments ({len(active)}):")
        print()
        for exp in active:
            print(f"  {exp.experiment_id}")
            print(f"    Recommendation: {exp.recommendation_id}")
            print(f"    Agent:    {exp.agent_name}")
            print(f"    Metric:   {exp.metric}")
            print(f"    Baseline: {exp.baseline_value:.4f}")
            print(f"    Target:   {exp.target_value:.4f}")
            print(f"    Samples:  {len(exp.samples)}/{exp.min_samples}")
            print(f"    Started:  {exp.started_at}")
            print()
        return

    if args.history:
        reports = loop.load_reports()
        if not reports:
            print("No improvement reports found.")
            print("Run 'baton improve --run' to trigger an improvement cycle.")
            return
        print(f"Improvement Reports ({len(reports)}):")
        print()
        for r in reports:
            status = "SKIPPED" if r.skipped else "COMPLETED"
            print(f"  {r.report_id}  [{status}]  {r.timestamp}")
            if r.skipped:
                print(f"    Reason: {r.reason}")
            else:
                print(f"    Recs: {len(r.recommendations)}, Auto: {len(r.auto_applied)}, Escalated: {len(r.escalated)}")
            print()
        return

    # Default: show latest report
    if args.report or True:
        reports = loop.load_reports()
        if not reports:
            print("No improvement reports found.")
            print("Run 'baton improve --run' to trigger an improvement cycle.")
            return
        latest = reports[-1]
        print(f"Latest Improvement Report: {latest.report_id}")
        print(f"  Timestamp: {latest.timestamp}")
        if latest.skipped:
            print(f"  Status: SKIPPED ({latest.reason})")
        else:
            print(f"  Anomalies:       {len(latest.anomalies)}")
            print(f"  Recommendations: {len(latest.recommendations)}")
            print(f"  Auto-applied:    {len(latest.auto_applied)}")
            print(f"  Escalated:       {len(latest.escalated)}")
            print(f"  Active experiments: {len(latest.active_experiments)}")
