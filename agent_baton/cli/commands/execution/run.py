"""``baton run`` -- autonomously drive an orchestrated execution in the foreground.

This module implements the interactive, autonomous runner. It initializes the
ExecutionEngine and TaskWorker, then loops automatically, only pausing to prompt
the user interactively when a human gate (e.g., approval) is reached.

Delegates to:
    agent_baton.core.orchestration.runner.BatonRunner
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from agent_baton.cli.colors import success, error as color_error, info as color_info
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.runtime.launcher import AgentLauncher, DryRunLauncher
from agent_baton.core.runtime.worker import TaskWorker
from agent_baton.core.orchestration.runner import BatonRunner
from agent_baton.models.execution import MachinePlan

_log = logging.getLogger(__name__)


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "run",
        help="Autonomously run a plan in the foreground with interactive prompts",
    )
    p.add_argument(
        "--plan",
        default=".claude/team-context/plan.json",
        help="Path to plan.json (default: .claude/team-context/plan.json)",
    )
    p.add_argument(
        "--task-id",
        default=None,
        help="Target a specific execution by task ID (default: active execution)",
    )
    p.add_argument(
        "--max-parallel",
        metavar="N",
        type=int,
        default=3,
        help="Maximum parallel agents (default: 3)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        default=False,
        help="Dry-run mode: no real agent calls will be made",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Resume an already started execution without passing a new plan",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    # 1. Initialize Launcher
    if args.dry_run:
        print(color_info("Starting runner in DRY RUN mode..."))
        launcher: AgentLauncher = DryRunLauncher()
    else:
        # Avoid importing heavy modules if not needed
        from agent_baton.core.runtime.claude_launcher import ClaudeCodeLauncher
        launcher = ClaudeCodeLauncher()

    # 2. Load Plan if not resuming
    plan = None
    if not args.resume:
        plan_path = Path(args.plan)
        if not plan_path.is_file():
            print(color_error(f"Plan file not found: {plan_path}"))
            print("Generate a plan first using 'baton plan', or pass --resume.")
            return
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            plan = MachinePlan.parse_obj(data)
        except Exception as e:
            print(color_error(f"Failed to parse plan: {e}"))
            return

    # 3. Initialize Engine
    # By default, ExecutionEngine uses the current working directory
    engine = ExecutionEngine()

    if plan is not None:
        try:
            engine.start(plan, task_id=args.task_id)
            print(color_info(f"Engine started with task ID: {plan.task_id}"))
        except Exception as e:
            if "already exists" in str(e).lower() or "active" in str(e).lower():
                print(color_info("Execution already active. Resuming..."))
            else:
                print(color_error(f"Failed to start engine: {e}"))
                return

    # 4. Initialize Worker
    worker = TaskWorker(
        engine=engine,
        launcher=launcher,
        max_parallel=args.max_parallel,
    )

    # 5. Initialize Facade
    runner = BatonRunner(engine=engine, worker=worker)

    # 6. Run the Event Loop
    print(color_info("\nStarting autonomous execution loop... (Press Ctrl+C to abort)\n"))
    try:
        summary = asyncio.run(runner.run_until_complete_or_gate(args.task_id))
        print(f"\n{success('Execution Complete')}")
        print(summary)
    except KeyboardInterrupt:
        print(color_error("\nExecution aborted by user."))
    except Exception as e:
        print(color_error(f"\nExecution failed: {e}"))
        _log.exception("Runner failed")
