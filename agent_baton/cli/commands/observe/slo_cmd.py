"""``baton slo`` -- SLO definition + error-budget tracking (O1.5).

Subcommands:

* ``define``         -- create or update an SLO
* ``list``           -- show all SLOs with current SLI + budget remaining
* ``measure``        -- recompute and persist measurements
* ``burns``          -- list error-budget burn events
* ``seed-defaults``  -- insert the canonical SLOs (idempotent)

The CLI is read-side observation only.  It never blocks execution and
never prompts the operator -- it just surfaces the engine's reliability
signals.

Delegates to:
    agent_baton.core.observe.slo_computer.SLOComputer
    agent_baton.core.storage.slo_store.SLOStore
"""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_baton.core.observe.slo_computer import SLOComputer
from agent_baton.core.storage.slo_store import SLOStore
from agent_baton.models.slo import DEFAULT_SLOS, SLODefinition


def _default_db_path() -> Path:
    return Path.cwd() / ".claude" / "team-context" / "baton.db"


def _resolve_db(args: argparse.Namespace) -> Path:
    explicit = getattr(args, "db", None)
    return Path(explicit) if explicit else _default_db_path()


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "slo",
        help="Define and track Service-Level Objectives + error budgets",
    )
    p.add_argument(
        "--db",
        metavar="PATH",
        help="Explicit path to baton.db (default: .claude/team-context/baton.db)",
    )
    sub = p.add_subparsers(dest="slo_action")

    # define
    sp_def = sub.add_parser("define", help="Create or update an SLO")
    sp_def.add_argument("--name", required=True, help="SLO identifier")
    sp_def.add_argument(
        "--sli",
        required=True,
        help="SLI source: dispatch_success_rate | gate_pass_rate | engine_uptime",
    )
    sp_def.add_argument("--target", type=float, required=True, help="Target ratio (0..1)")
    sp_def.add_argument("--window", type=int, default=28, help="Window in days")
    sp_def.add_argument("--description", default="", help="Human-readable description")

    # list
    sub.add_parser("list", help="Show all SLOs with current SLI + budget remaining")

    # measure
    sp_meas = sub.add_parser("measure", help="Recompute and persist SLO measurements")
    sp_meas.add_argument("--name", help="Limit to a single SLO")

    # burns
    sp_burns = sub.add_parser("burns", help="Show recent error-budget burn events")
    sp_burns.add_argument("--name", help="Filter by SLO name")
    sp_burns.add_argument(
        "--since",
        default="",
        help="Filter to burns started on/after this ISO timestamp or duration (e.g. 7d)",
    )

    # seed-defaults
    sub.add_parser(
        "seed-defaults",
        help="Insert the canonical SLOs (dispatch_success_rate, gate_pass_rate, engine_uptime)",
    )

    return p


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    action = getattr(args, "slo_action", None)
    if action is None:
        print("baton slo: pick a subcommand (define | list | measure | burns | seed-defaults)")
        return

    db_path = _resolve_db(args)

    if action == "define":
        _handle_define(args, db_path)
    elif action == "list":
        _handle_list(db_path)
    elif action == "measure":
        _handle_measure(args, db_path)
    elif action == "burns":
        _handle_burns(args, db_path)
    elif action == "seed-defaults":
        _handle_seed(db_path)
    else:
        print(f"Unknown action: {action}")


def _handle_define(args: argparse.Namespace, db_path: Path) -> None:
    if args.sli not in SLOComputer.SUPPORTED_SLIS:
        supported = ", ".join(SLOComputer.SUPPORTED_SLIS)
        print(f"Error: --sli must be one of: {supported}")
        return
    if not (0.0 <= args.target <= 1.0):
        print("Error: --target must be in [0.0, 1.0]")
        return
    if args.window <= 0:
        print("Error: --window must be a positive integer (days)")
        return

    store = SLOStore(db_path)
    store.upsert_definition(
        SLODefinition(
            name=args.name,
            sli_query=args.sli,
            target=float(args.target),
            window_days=int(args.window),
            description=args.description or "",
        )
    )
    print(f"SLO '{args.name}' defined: target={args.target} window={args.window}d sli={args.sli}")


def _handle_list(db_path: Path) -> None:
    store = SLOStore(db_path)
    defs = store.list_definitions()
    if not defs:
        print("No SLOs defined. Run 'baton slo seed-defaults' to install canonical defaults.")
        return

    print(f"{'NAME':<28}{'SLI':<26}{'TARGET':>8}{'CURRENT':>10}{'BUDGET%':>10}  STATUS")
    for d in defs:
        latest = store.latest_measurement(d.name)
        if latest is None:
            sli_str = "(none)"
            budget_str = "(none)"
            status = "no data"
        else:
            sli_str = f"{latest.sli_value:.4f}"
            budget_str = f"{latest.error_budget_remaining_pct * 100:.1f}"
            status = "OK" if latest.is_meeting else "BREACH"
        print(
            f"{d.name:<28}{d.sli_query:<26}{d.target:>8.4f}{sli_str:>10}{budget_str:>10}  {status}"
        )


def _handle_measure(args: argparse.Namespace, db_path: Path) -> None:
    computer = SLOComputer(db_path)
    store = computer.store
    if args.name:
        target = store.get_definition(args.name)
        if target is None:
            print(f"No SLO named '{args.name}'.")
            return
        defs = [target]
    else:
        defs = store.list_definitions()
        if not defs:
            print("No SLOs defined.")
            return

    for d in defs:
        m, burn = computer.measure_and_persist(d)
        flag = "OK" if m.is_meeting else "BREACH"
        burn_note = ""
        if burn is not None:
            burn_note = (
                f"  [BURN: rate={burn.burn_rate:.4f}/h "
                f"consumed={burn.budget_consumed_pct * 100:.1f}%]"
            )
        print(
            f"{d.name:<28} sli={m.sli_value:.4f} target={m.target:.4f} "
            f"budget={m.error_budget_remaining_pct * 100:.1f}%  {flag}{burn_note}"
        )


def _handle_burns(args: argparse.Namespace, db_path: Path) -> None:
    store = SLOStore(db_path)
    since = _resolve_since(args.since) if args.since else None
    burns = store.list_burns(slo_name=args.name, since=since)
    if not burns:
        print("No error-budget burns recorded.")
        return

    print(f"{'STARTED':<22}{'SLO':<28}{'RATE/h':>10}{'CONSUMED%':>12}  INCIDENT")
    for b in burns:
        incident = b.incident_id or "-"
        print(
            f"{b.started_at:<22}{b.slo_name:<28}{b.burn_rate:>10.4f}"
            f"{b.budget_consumed_pct * 100:>12.1f}  {incident}"
        )


def _handle_seed(db_path: Path) -> None:
    store = SLOStore(db_path)
    for slo in DEFAULT_SLOS:
        store.upsert_definition(slo)
    print(f"Seeded {len(DEFAULT_SLOS)} canonical SLO(s):")
    for slo in DEFAULT_SLOS:
        print(f"  - {slo.name} (target={slo.target}, window={slo.window_days}d)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_since(value: str) -> str:
    """Accept either an ISO timestamp or a short duration like ``7d``.

    Duration suffixes: ``h`` (hours), ``d`` (days), ``w`` (weeks).
    """
    from datetime import datetime, timedelta, timezone

    v = value.strip()
    if not v:
        return v
    if v.endswith(("h", "d", "w")):
        try:
            n = int(v[:-1])
        except ValueError:
            return v
        unit = v[-1]
        delta = {
            "h": timedelta(hours=n),
            "d": timedelta(days=n),
            "w": timedelta(weeks=n),
        }[unit]
        ts = datetime.now(timezone.utc) - delta
        return ts.strftime("%Y-%m-%dT%H:%M:%SZ")
    return v
