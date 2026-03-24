"""CLI command: ``baton daemon`` — background execution management."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from agent_baton.core.runtime.supervisor import WorkerSupervisor
from agent_baton.core.runtime.launcher import DryRunLauncher
from agent_baton.models.execution import MachinePlan


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser("daemon", help="Background execution management")
    sub = p.add_subparsers(dest="daemon_action")

    start = sub.add_parser("start", help="Start daemon execution")
    start.add_argument(
        "--plan", metavar="FILE", required=True,
        help="Path to the MachinePlan JSON file",
    )
    start.add_argument(
        "--max-parallel", metavar="N", type=int, default=3,
        help="Maximum parallel agents (default: 3)",
    )
    start.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Use DryRunLauncher (no real agent calls)",
    )
    start.add_argument(
        "--foreground", action="store_true",
        help="Run in foreground (don't daemonize)",
    )
    start.add_argument(
        "--resume", action="store_true",
        help="Resume from saved execution state",
    )
    start.add_argument(
        "--project-dir", metavar="DIR", default=None,
        help="Project directory for execution (default: cwd)",
    )

    sub.add_parser("status", help="Show daemon status")
    sub.add_parser("stop", help="Stop the running daemon")

    return p


def handler(args: argparse.Namespace) -> None:
    supervisor = WorkerSupervisor()

    if args.daemon_action == "start":
        plan_path = Path(args.plan)
        if not plan_path.exists():
            print(f"Error: plan file not found: {plan_path}")
            return
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
            plan = MachinePlan.from_dict(data)
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"Error: invalid plan file: {exc}")
            return

        if args.dry_run:
            launcher = DryRunLauncher()
        else:
            try:
                from agent_baton.core.runtime.claude_launcher import ClaudeCodeLauncher
                launcher = ClaudeCodeLauncher()
            except RuntimeError as exc:
                print(f"Error: {exc}")
                print("Install Claude Code CLI or use --dry-run.")
                return

        # Change to --project-dir before forking so all relative paths resolve
        # correctly inside the daemon process.
        if args.project_dir is not None:
            project_dir = Path(args.project_dir).resolve()
            if not project_dir.is_dir():
                print(f"Error: project directory not found: {project_dir}")
                return
            os.chdir(project_dir)

        # Single-instance check + daemonize (skipped with --foreground).
        if not args.foreground:
            if supervisor.pid_path.exists():
                try:
                    pid = int(supervisor.pid_path.read_text().strip())
                    os.kill(pid, 0)  # probe — raises if process is gone
                    print(
                        f"Daemon already running (PID {pid}). "
                        "Use 'baton daemon stop' first."
                    )
                    return
                except (ValueError, OSError):
                    pass  # stale PID file — proceed

            from agent_baton.core.runtime.daemon import daemonize

            print(f"Starting daemon for task '{plan.task_id}'...")
            daemonize()
        else:
            print(f"Starting daemon for task '{plan.task_id}'...")

        summary = supervisor.start(
            plan=plan,
            launcher=launcher,
            max_parallel=args.max_parallel,
            resume=args.resume,
        )
        # In foreground mode the process is still attached to the terminal and
        # can print the summary.  In daemon mode stdout has been redirected to
        # /dev/null so this is a no-op.
        print(summary)
        return

    if args.daemon_action == "status":
        status = supervisor.status()
        if status.get("status") == "no_active_execution" and not status.get("running"):
            print("No active daemon or execution.")
            return
        running = "running" if status.get("running") else "not running"
        print(f"Daemon: {running}")
        if status.get("pid"):
            print(f"PID: {status['pid']}")
        if status.get("task_id"):
            print(f"Task: {status['task_id']}")
            print(f"Status: {status.get('status', 'unknown')}")
            print(f"Phase: {status.get('current_phase', '?')}")
            steps_done = status.get("steps_complete", 0)
            steps_total = status.get("steps_total", 0)
            print(f"Steps: {steps_done}/{steps_total}")
            gates_passed = status.get("gates_passed", 0)
            gates_failed = status.get("gates_failed", 0)
            print(f"Gates: {gates_passed} passed, {gates_failed} failed")
            elapsed = status.get("elapsed_seconds", 0)
            print(f"Elapsed: {int(elapsed)}s")
        if status.get("last_update"):
            print(f"Last update: {status['last_update']}")
        return

    if args.daemon_action == "stop":
        ok = supervisor.stop()
        if ok:
            print("Stop signal sent to daemon.")
        else:
            print("No running daemon found.")
        return

    # No subcommand — show help.
    print("Usage: baton daemon {start|status|stop}")
    print("Run 'baton daemon start --help' for options.")
