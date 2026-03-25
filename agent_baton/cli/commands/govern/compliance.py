"""``baton compliance`` -- show compliance reports.

Compliance reports are generated during execution for tasks that touch
regulated domains.  This command lists and displays them.

Display modes:
    * ``baton compliance`` -- List recent compliance reports.
    * ``baton compliance --task-id ID`` -- Show a specific report.

Delegates to:
    :class:`~agent_baton.core.govern.compliance.ComplianceReportGenerator`
"""
from __future__ import annotations

import argparse

from agent_baton.core.govern.compliance import ComplianceReportGenerator


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("compliance", help="Show compliance reports")
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--task-id", metavar="ID", help="Show a specific compliance report",
    )
    p.add_argument(
        "--count", type=int, default=None, metavar="N",
        help="Number of recent reports to list (default 5)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    generator = ComplianceReportGenerator()

    if args.task_id:
        content = generator.load(args.task_id)
        if content is None:
            print(f"No compliance report found for task '{args.task_id}'.")
            return
        print(content)
        return

    # Default: list recent reports
    count = args.count or 5
    recent = generator.list_recent(count)
    if not recent:
        print("No compliance reports found.")
        return
    print(f"Recent compliance reports ({len(recent)}):")
    for path in recent:
        print(f"  {path.stem}")
