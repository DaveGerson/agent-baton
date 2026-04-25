"""``baton cost-anomalies`` -- statistical cost anomaly detection (O1.3).

Surfaces token-cost outliers detected by
:class:`agent_baton.core.improve.cost_anomaly.CostAnomalyDetector`.

This is a *detection only* tool.  It never blocks execution and never
auto-applies any remediation.

Usage
-----
.. code-block:: shell

   baton cost-anomalies                          # current anomalies
   baton cost-anomalies --window-days 7
   baton cost-anomalies --severity high
   baton cost-anomalies --format json
   baton cost-anomalies --clear                  # acknowledge all current

The ``baton improve anomalies`` invocation referenced in the O1.3 spec
maps to this command -- ``improve`` keeps its existing flag-driven UX
and ``anomalies`` already exists for the legacy heuristic detector.
``cost-anomalies`` is the dedicated entry point for the statistical
detector.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_baton.core.improve.cost_anomaly import CostAnomalyDetector


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "cost-anomalies",
        help="Statistical cost anomaly detection (z-score + IQR)",
        description=(
            "Detect statistically unusual token costs per (agent, model) "
            "pair.  Pure detection: no execution path is blocked."
        ),
    )
    p.add_argument(
        "--window-days",
        type=int,
        default=30,
        metavar="N",
        help="Rolling window in days (default: 30).",
    )
    p.add_argument(
        "--severity",
        choices=("low", "medium", "high"),
        default=None,
        help="Filter to only this severity level.",
    )
    p.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format (default: markdown).",
    )
    p.add_argument(
        "--clear",
        action="store_true",
        help=(
            "Acknowledge all currently-detected anomalies so they do not "
            "re-surface on the next run."
        ),
    )
    p.add_argument(
        "--db-path",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Override the SQLite baton.db path.  Default: auto-discover "
            "via the project storage backend."
        ),
    )
    return p


def handler(args: argparse.Namespace) -> None:
    db_path = _resolve_db_path(getattr(args, "db_path", None))
    detector = CostAnomalyDetector(db_path=db_path)

    anomalies = detector.detect(window_days=args.window_days)

    if args.severity:
        anomalies = [a for a in anomalies if a.severity == args.severity]

    if args.clear:
        added = detector.acknowledge(anomalies)
        print(f"Acknowledged {added} new cost anomalies "
              f"({len(anomalies)} currently visible).")
        return

    if args.format == "json":
        payload = {
            "window_days": args.window_days,
            "count": len(anomalies),
            "anomalies": [a.to_dict() for a in anomalies],
        }
        print(json.dumps(payload, indent=2))
        return

    _render_markdown(anomalies, args.window_days)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_db_path(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return Path(explicit)
    try:
        from agent_baton.core.storage import get_project_storage
        context_root = Path(".claude/team-context").resolve()
        storage = get_project_storage(context_root)
        if storage is not None and hasattr(storage, "db_path"):
            return Path(storage.db_path)
    except Exception:
        return None
    return None


def _render_markdown(anomalies: list, window_days: int) -> None:
    if not anomalies:
        print(f"No cost anomalies detected (window: {window_days}d).")
        return

    print(f"## Cost Anomalies (window: {window_days}d)")
    print()
    print(f"Total: **{len(anomalies)}**")
    print()
    print("| Severity | Agent | Model | Step | Tokens | Baseline | z-score | IQR factor |")
    print("|----------|-------|-------|------|-------:|---------:|--------:|-----------:|")
    for a in anomalies:
        print(
            f"| {a.severity} | {a.agent} | {a.model} | "
            f"`{a.step_id}` | {a.observed_tokens:,} | "
            f"{a.baseline_mean:,.0f} | {a.z_score:.2f} | {a.iqr_factor:.2f} |"
        )
