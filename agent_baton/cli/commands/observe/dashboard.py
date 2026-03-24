"""baton dashboard — generate or display the usage dashboard."""
from __future__ import annotations

import argparse

from agent_baton.core.observe.dashboard import DashboardGenerator


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("dashboard", help="Generate usage dashboard")
    p.add_argument(
        "--write", action="store_true", help="Write dashboard to disk",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    gen = DashboardGenerator()

    if args.write:
        path = gen.write()
        print(f"Dashboard written to {path}")
        return

    print(gen.generate())
