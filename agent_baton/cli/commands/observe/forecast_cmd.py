"""``baton forecast cost`` -- estimate execution cost for a plan.

Reads ``plan.json`` (default: ``.claude/team-context/plan.json``),
queries historical token data from ``baton.db``, and prints a cost
forecast table.

Usage::

    baton forecast cost
    baton forecast cost --plan /path/to/plan.json --window 30
    baton forecast cost --json

Delegates to:
    agent_baton.core.observe.cost_forecaster.CostForecaster
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("forecast", help="Cost and resource forecasting")
    sub = p.add_subparsers(dest="forecast_cmd")

    cost = sub.add_parser("cost", help="Estimate execution cost for a plan")
    cost.add_argument(
        "--plan",
        metavar="PATH",
        default=".claude/team-context/plan.json",
        help="Path to plan.json (default: .claude/team-context/plan.json)",
    )
    cost.add_argument(
        "--window",
        type=int,
        default=14,
        metavar="DAYS",
        help="Historical window in days for token medians (default: 14)",
    )
    cost.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        help="Output raw JSON instead of a markdown table",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    forecast_cmd = getattr(args, "forecast_cmd", None)
    if forecast_cmd == "cost":
        _handle_cost(args)
    else:
        print("Usage: baton forecast cost [--plan PATH] [--window DAYS] [--json]")


def _handle_cost(args: argparse.Namespace) -> None:
    from agent_baton.core.observe.cost_forecaster import CostForecaster
    from agent_baton.models.execution import MachinePlan

    plan_path = Path(args.plan)
    if not plan_path.exists():
        print(f"Error: plan file not found: {plan_path}", file=sys.stderr)
        sys.exit(1)

    try:
        plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
        plan = MachinePlan.from_dict(plan_data)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: could not parse plan: {exc}", file=sys.stderr)
        sys.exit(1)

    conn = _open_db()
    forecaster = CostForecaster(conn, basis_window_days=args.window)
    forecast = forecaster.forecast(plan)
    if conn is not None:
        conn.close()

    if args.output_json:
        print(json.dumps(forecast.to_dict(), indent=2))
        return

    _render_markdown(forecast)


def _open_db() -> sqlite3.Connection | None:
    db_path = Path(".claude/team-context/baton.db")
    if not db_path.exists():
        return None
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:  # noqa: BLE001
        return None


def _render_markdown(forecast) -> None:  # type: ignore[no-untyped-def]
    """Print a markdown cost table plus confidence band summary."""
    print(f"## Cost Forecast — plan `{forecast.plan_id}`")
    print(f"Computed: {forecast.computed_at}  |  "
          f"Window: {forecast.basis_window_days}d  |  "
          f"Samples: {forecast.sample_size}")
    print()

    # Per-agent breakdown table
    print("| Agent | Model | Steps | Est. Tokens | Est. Cost (USD) |")
    print("|-------|-------|------:|------------:|----------------:|")
    for row in forecast.breakdown:
        print(
            f"| {row['agent_name']} "
            f"| {row['model']} "
            f"| {row['est_steps']} "
            f"| {row['est_tokens']:,} "
            f"| ${row['est_usd']:.4f} |"
        )

    # Total row
    total_tokens = forecast.est_input_tokens + forecast.est_output_tokens
    print(
        f"| **TOTAL** | | "
        f"| **{total_tokens:,}** "
        f"| **${forecast.est_usd_mid:.4f}** |"
    )
    print()

    # Confidence band
    print(
        f"Confidence band: "
        f"low ${forecast.est_usd_low:.4f} / "
        f"mid ${forecast.est_usd_mid:.4f} / "
        f"high ${forecast.est_usd_high:.4f}"
    )
    print(
        f"Input tokens: {forecast.est_input_tokens:,}  |  "
        f"Output tokens: {forecast.est_output_tokens:,}"
    )
