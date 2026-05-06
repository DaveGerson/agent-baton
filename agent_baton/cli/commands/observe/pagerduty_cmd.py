"""``baton pagerduty`` -- PagerDuty integration management.

Provides a test sub-command to verify the PagerDuty routing key and
connectivity by sending a low-severity test event.

Usage::

    baton pagerduty test
    baton pagerduty test --routing-key <KEY>

The routing key is read from ``--routing-key`` or the ``BATON_PAGERDUTY_KEY``
environment variable. If neither is set the command exits with an error.
"""
from __future__ import annotations

import argparse

from agent_baton.core.observe.pagerduty import PagerDutyNotifier


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "pagerduty",
        help="PagerDuty integration management",
    )
    sub = p.add_subparsers(dest="pd_command")

    test_p = sub.add_parser("test", help="Send a test event to PagerDuty")
    test_p.add_argument(
        "--routing-key",
        metavar="KEY",
        default=None,
        help="PagerDuty integration routing key (overrides BATON_PAGERDUTY_KEY)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    if not getattr(args, "pd_command", None) or args.pd_command == "test":
        _handle_test(args)
    else:
        print(f"Unknown pagerduty sub-command: {args.pd_command}")


def _handle_test(args: argparse.Namespace) -> None:
    routing_key: str | None = getattr(args, "routing_key", None)
    notifier = PagerDutyNotifier(routing_key=routing_key)

    if not notifier._routing_key:
        print(
            "error: no PagerDuty routing key configured.\n"
            "  Set BATON_PAGERDUTY_KEY or pass --routing-key KEY."
        )
        return

    print("Sending test event to PagerDuty...")
    dedup_key = notifier.notify_incident(
        incident_id="baton-test",
        severity="info",
        summary="Agent Baton test event",
        details={"source": "baton pagerduty test"},
    )

    if dedup_key:
        print(f"Test event sent. dedup_key: {dedup_key}")
    else:
        print("Test event request completed (no dedup_key returned or notifier disabled).")
