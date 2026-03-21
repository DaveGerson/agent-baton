"""baton changelog — show agent changelog entries or list backup files."""
from __future__ import annotations

import argparse

from agent_baton.core.vcs import AgentVersionControl


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("changelog", help="Show agent changelog or list backups")
    p.add_argument(
        "--agent",
        default=None,
        metavar="NAME",
        help="Show history for a specific agent",
    )
    p.add_argument(
        "--backups",
        nargs="?",
        const="",
        default=None,
        metavar="NAME",
        help=(
            "List backup files. Optionally filter by agent name: "
            "--backups lists all, --backups NAME lists for that agent."
        ),
    )
    return p


def handler(args: argparse.Namespace) -> None:
    vcs = AgentVersionControl()

    if args.backups is not None:
        # --backups [NAME] — list backup files
        agent_filter: str | None = args.backups if args.backups else None
        backups = vcs.list_backups(agent_filter)
        if not backups:
            label = f" for agent '{agent_filter}'" if agent_filter else ""
            print(f"No backups found{label}.")
            return
        label = f" for agent '{agent_filter}'" if agent_filter else ""
        print(f"Backups{label}:")
        for path in backups:
            print(f"  {path}")
        return

    # Default: show changelog entries
    agent_filter_name: str | None = args.agent if args.agent else None
    if agent_filter_name:
        entries = vcs.get_agent_history(agent_filter_name)
    else:
        entries = vcs.read_changelog()

    if not entries:
        if agent_filter_name:
            print(f"No changelog entries for agent '{agent_filter_name}'.")
        else:
            print("Changelog is empty.")
        return

    label = f" for agent '{agent_filter_name}'" if agent_filter_name else ""
    print(f"Agent changelog{label}:\n")
    for entry in entries:
        print(f"  {entry.timestamp}  [{entry.action}]  {entry.agent_name}")
        print(f"    {entry.summary}")
        if entry.backup_path:
            print(f"    Backup: {entry.backup_path}")
        print()
