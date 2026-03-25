"""``baton improve`` -- run or view improvement cycle reports.

The improvement loop is the top-level entry point for the closed-loop
learning pipeline.  A single cycle: detects anomalies, generates
recommendations, auto-applies safe changes (budget downgrades), escalates
risky changes, and starts experiments.

Display modes:
    * ``baton improve`` -- Show the latest improvement report (default).
    * ``baton improve --run`` -- Run a full improvement cycle.
    * ``baton improve --force`` -- Force-run even if triggers haven't fired.
    * ``baton improve --report`` -- Show the latest report (explicit).
    * ``baton improve --experiments`` -- Show active experiments.
    * ``baton improve --history`` -- Show all improvement reports.

Delegates to:
    :class:`~agent_baton.core.improve.loop.ImprovementLoop`
"""
from __future__ import annotations

import argparse

from agent_baton.core.improve.loop import ImprovementLoop


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "improve",
        help="Run the improvement loop or view reports",
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
        help="Force-run a cycle even if triggers haven't fired",
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
    return p


def handler(args: argparse.Namespace) -> None:
    loop = ImprovementLoop()

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
        from agent_baton.core.improve.experiments import ExperimentManager
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
