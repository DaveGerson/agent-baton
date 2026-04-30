"""``baton test-plan`` -- smoke-test the execution engine lifecycle.

Builds a synthetic plan exercising all engine features (dispatch, gates,
teams, parallel-safe, approvals, automation) and drives it through the
full execution loop in dry-run mode.  Reports pass/fail with diagnostics.

Delegates to:
    agent_baton.testing.harness
"""
from __future__ import annotations

import argparse


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "test-plan",
        help="Smoke-test the execution engine with a synthetic plan",
    )
    p.add_argument(
        "--verbose", "-v", action="store_true", help="Show each action"
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        dest="dry_run",
        help="Use dry-run mode (default)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    from agent_baton.testing.harness import run_harness

    print("Running baton self-test harness...")
    print()

    result = run_harness(
        dry_run=getattr(args, "dry_run", True),
        verbose=getattr(args, "verbose", False),
    )

    print()
    print(result.summary())

    if not result.passed:
        raise SystemExit(1)
