"""baton status — show team-context file status."""
from __future__ import annotations

import argparse

from agent_baton.core.context import ContextManager


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("status", help="Show team-context file status")
    return p


def handler(args: argparse.Namespace) -> None:
    ctx = ContextManager()
    files = ctx.recovery_files_exist()

    print("Team context status:")
    for name, exists in files.items():
        marker = "✓" if exists else "✗"
        print(f"  {marker} {name}")
