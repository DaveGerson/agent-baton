"""baton escalations — show, resolve, or clear escalations."""
from __future__ import annotations

import argparse

from agent_baton.core.govern.escalation import EscalationManager


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
    return p


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

    if not escalations:
        label = "escalations" if args.all else "pending escalations"
        print(f"No {label}.")
        return

    label = "All escalations" if args.all else "Pending escalations"
    print(f"{label} ({len(escalations)}):\n")
    for esc in escalations:
        print(esc.to_markdown())
        print()
        print("---")
        print()
