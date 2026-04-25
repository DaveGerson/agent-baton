"""``baton escalations`` -- show, resolve, or clear agent escalations.

When agents encounter situations beyond their scope, they file escalation
requests. This command manages the escalation queue.

Delegates to:
    agent_baton.core.govern.escalation.EscalationManager
"""
from __future__ import annotations

import argparse
from datetime import timezone, datetime

from agent_baton.core.govern.escalation import EscalationManager
from agent_baton.models.escalation import Escalation


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("escalations", help="Show or resolve agent escalations")
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--all",
        action="store_true",
        help="Show all escalations, including resolved ones",
    )
    group.add_argument(
        "--resolve",
        nargs=2,
        metavar=("AGENT", "ANSWER"),
        help="Resolve the oldest pending escalation for AGENT with ANSWER",
    )
    group.add_argument(
        "--clear",
        action="store_true",
        help="Remove all resolved escalations from the file",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="Render a compact table (id, role, time-remaining, next-role)",
    )
    p.add_argument(
        "--expired",
        action="store_true",
        help=(
            "With --list: show only escalations whose timeout has elapsed. "
            "Observation-only — no automatic paging is performed."
        ),
    )
    return p


def _format_remaining(esc: Escalation, now: datetime) -> str:
    if esc.timeout_minutes <= 0:
        return "-"
    if esc.expired(now):
        return "EXPIRED"
    remaining = esc.time_remaining(now)
    if remaining is None:
        return "-"
    total_seconds = int(remaining.total_seconds())
    if total_seconds <= 0:
        return "EXPIRED"
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


def _print_table(escalations: list[Escalation]) -> None:
    """Render escalations as a compact table.

    Columns: id, required_role, time_remaining, next_role.
    ``id`` is the 1-based file order index, which matches how the
    markdown manager iterates entries.
    """
    if not escalations:
        print("No escalations.")
        return

    now = datetime.now(tz=timezone.utc)
    rows: list[tuple[str, str, str, str]] = []
    for idx, esc in enumerate(escalations, start=1):
        rows.append(
            (
                str(idx),
                esc.required_role or "-",
                _format_remaining(esc, now),
                esc.next_role(now) or "-",
            )
        )

    headers = ("ID", "REQUIRED_ROLE", "TIME_REMAINING", "NEXT_ROLE")
    widths = [
        max(len(headers[i]), max(len(r[i]) for r in rows))
        for i in range(4)
    ]

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))


def handler(args: argparse.Namespace) -> None:
    manager = EscalationManager()

    if args.clear:
        manager.clear_resolved()
        print("Resolved escalations cleared.")
        return

    if args.resolve:
        agent_name, answer = args.resolve
        if manager.resolve(agent_name, answer):
            print(f"Resolved escalation for agent '{agent_name}'.")
        else:
            print(f"No pending escalation found for agent '{agent_name}'.")
        return

    escalations = manager.get_all() if args.all else manager.get_pending()

    # --list path: compact table with optional --expired filter.
    if args.list:
        if args.expired:
            now = datetime.now(tz=timezone.utc)
            escalations = [e for e in escalations if e.expired(now)]
        _print_table(escalations)
        return

    # --expired without --list still applies the filter to the verbose view.
    if args.expired:
        now = datetime.now(tz=timezone.utc)
        escalations = [e for e in escalations if e.expired(now)]

    if not escalations:
        if args.expired:
            print("No expired escalations.")
        else:
            label = "escalations" if args.all else "pending escalations"
            print(f"No {label}.")
        return

    label = "All escalations" if args.all else "Pending escalations"
    if args.expired:
        label = "Expired escalations"
    print(f"{label} ({len(escalations)}):\n")
    for esc in escalations:
        print(esc.to_markdown())
        print()
        print("---")
        print()
