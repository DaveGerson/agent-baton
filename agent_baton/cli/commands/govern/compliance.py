"""``baton compliance`` — compliance reports + hash-chain audit (F0.3).

Subcommands
-----------
(default)      List or show compliance reports
verify         Walk the compliance-audit.jsonl chain; exit 1 on tamper
rechain        One-time migration: add hash chain to existing log

This module intentionally extends the pre-existing compliance command
rather than replacing it.  The original list/show behaviour is preserved.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.core.govern.compliance import ComplianceReportGenerator


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser("compliance", help="Compliance reports and audit chain (F0.3)")
    sub = p.add_subparsers(dest="compliance_cmd", metavar="SUBCOMMAND")

    # -- legacy default (list/show) ------------------------------------------
    pls = sub.add_parser("list", help="List recent compliance reports")
    pls.add_argument("--count", type=int, default=5, metavar="N",
                     help="Number of recent reports to list (default 5)")

    psh = sub.add_parser("show", help="Show a specific compliance report")
    psh.add_argument("task_id", help="Task ID of the report to show")

    # -- verify (F0.3) --------------------------------------------------------
    pv = sub.add_parser(
        "verify",
        help="Verify compliance-audit.jsonl hash chain integrity",
    )
    pv.add_argument(
        "--log",
        metavar="PATH",
        default=None,
        help="Path to compliance-audit.jsonl (default: .claude/team-context/compliance-audit.jsonl)",
    )

    # -- rechain (F0.3) -------------------------------------------------------
    pr = sub.add_parser(
        "rechain",
        help="One-time migration: add hash chain to an existing compliance-audit.jsonl",
    )
    pr.add_argument(
        "--log",
        metavar="PATH",
        default=None,
        help="Path to compliance-audit.jsonl",
    )
    pr.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help="Output path (default: atomic replace of --log)",
    )

    # Top-level flags (backward compat with original single-command design)
    p.add_argument("--task-id", metavar="ID", default=None,
                   help="Show a specific compliance report (legacy flag)")
    p.add_argument("--count", type=int, default=None, metavar="N",
                   help="Number of recent reports to list (legacy flag)")
    return p


def handler(args: argparse.Namespace) -> None:
    cmd = getattr(args, "compliance_cmd", None)

    # ── verify ────────────────────────────────────────────────────────────────
    if cmd == "verify":
        from agent_baton.core.govern.compliance import verify_chain

        log_path = Path(args.log) if args.log else Path(
            ".claude/team-context/compliance-audit.jsonl"
        )
        ok, message = verify_chain(log_path)
        print(message)
        if not ok:
            sys.exit(1)
        return

    # ── rechain ───────────────────────────────────────────────────────────────
    if cmd == "rechain":
        from agent_baton.core.govern.compliance import rechain

        log_path = Path(args.log) if args.log else Path(
            ".claude/team-context/compliance-audit.jsonl"
        )
        out_path = Path(args.out) if getattr(args, "out", None) else None
        count = rechain(log_path, out_path)
        dest = out_path or log_path
        print(f"Rechained {count} entries -> {dest}")
        return

    # ── show (sub or legacy --task-id) ────────────────────────────────────────
    task_id = getattr(args, "task_id", None) or getattr(args, "_task_id_legacy", None)
    # Legacy: baton compliance --task-id <id>
    if not task_id and hasattr(args, "task_id"):
        task_id = args.task_id

    generator = ComplianceReportGenerator()

    if cmd == "show":
        content = generator.load(args.task_id)
        if content is None:
            print(f"No compliance report found for task '{args.task_id}'.")
            return
        print(content)
        return

    if task_id and cmd is None:
        # Legacy --task-id flag at top-level
        content = generator.load(task_id)
        if content is None:
            print(f"No compliance report found for task '{task_id}'.")
            return
        print(content)
        return

    # ── list (sub or legacy default) ──────────────────────────────────────────
    count = getattr(args, "count", None) or 5
    recent = generator.list_recent(count)
    if not recent:
        print("No compliance reports found.")
        return
    print(f"Recent compliance reports ({len(recent)}):")
    for path in recent:
        print(f"  {path.stem}")
