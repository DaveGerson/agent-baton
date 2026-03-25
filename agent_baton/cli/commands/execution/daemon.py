"""CLI command: ``baton daemon`` — background execution management."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

from agent_baton.core.runtime.supervisor import WorkerSupervisor
from agent_baton.core.runtime.launcher import DryRunLauncher, AgentLauncher
from agent_baton.models.execution import MachinePlan


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser("daemon", help="Background execution management")
    sub = p.add_subparsers(dest="daemon_action")

    start = sub.add_parser("start", help="Start daemon execution")
    start.add_argument(
        "--plan", metavar="FILE", default=None,
        help="Path to the MachinePlan JSON file (required unless --resume)",
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
    # ── API integration flags ────────────────────────────────────────────────
    start.add_argument(
        "--serve", action="store_true", default=False,
        help=(
            "Also start the HTTP API server in the same process, sharing the "
            "EventBus with the async worker."
        ),
    )
    start.add_argument(
        "--port", metavar="PORT", type=int, default=8741,
        help="Port for the API server (only used with --serve, default: 8741)",
    )
    start.add_argument(
        "--host", metavar="HOST", default="127.0.0.1",
        help=(
            "Bind address for the API server "
            "(only used with --serve, default: 127.0.0.1)"
        ),
    )
    start.add_argument(
        "--token", metavar="TOKEN", default=None,
        help="Bearer token for API authentication (only used with --serve)",
    )
    start.add_argument(
        "--task-id", metavar="ID", default=None, dest="task_id",
        help="Namespace this daemon under a specific task ID",
    )

    status_p = sub.add_parser("status", help="Show daemon status")
    status_p.add_argument(
        "--task-id", metavar="ID", default=None, dest="task_id",
        help="Show status for a specific task ID",
    )

    stop_p = sub.add_parser("stop", help="Stop the running daemon")
    stop_p.add_argument(
        "--task-id", metavar="ID", default=None, dest="task_id",
        help="Stop the daemon for a specific task ID",
    )

    list_p = sub.add_parser("list", help="List all daemon workers")
    list_p.add_argument(
        "--project-dir", metavar="DIR", default=None,
        help="Project directory to scan (default: cwd)",
    )

    return p


# ---------------------------------------------------------------------------
# Combined daemon + API runner
# ---------------------------------------------------------------------------

async def _run_daemon_with_api(
    *,
    plan: MachinePlan | None,
    launcher: AgentLauncher,
    supervisor: WorkerSupervisor,
    max_parallel: int,
    resume: bool,
    host: str,
    port: int,
    token: str | None,
    team_context_root: Path,
    task_id: str | None = None,
) -> str:
    """Run the async worker and the HTTP API server concurrently.

    Both share a single :class:`~agent_baton.core.events.bus.EventBus`
    instance so events emitted by the worker are immediately visible to
    connected API clients (SSE stream, webhook layer, observability routes).

    The runner mirrors :meth:`WorkerSupervisor._run_with_signals` but adds a
    third concurrent task for the uvicorn server.  On any signal the worker
    and server are both cancelled gracefully.

    Args:
        plan: The execution plan.  Ignored when *resume* is True.
        launcher: Agent launcher implementation.
        supervisor: A pre-initialised :class:`WorkerSupervisor` (used for its
            PID-file/logging setup that was already called by the caller).
        max_parallel: Maximum concurrently dispatched agents.
        resume: When True, resume from persisted engine state.
        host: Bind address for uvicorn.
        port: Port for uvicorn.
        token: Optional Bearer token for API auth.
        team_context_root: Root directory used by the engine and API.
    """
    from agent_baton.api.server import create_app
    from agent_baton.core.events.bus import EventBus
    from agent_baton.core.runtime.context import ExecutionContext
    from agent_baton.core.runtime.signals import SignalHandler
    from agent_baton.core.runtime.worker import TaskWorker
    import uvicorn

    logger = logging.getLogger("baton.daemon")

    # ── Shared EventBus ──────────────────────────────────────────────────────
    bus = EventBus()

    # ── Execution engine + worker ────────────────────────────────────────────
    ctx = ExecutionContext.build(
        launcher=launcher,
        team_context_root=team_context_root,
        bus=bus,
        task_id=task_id,
    )
    engine = ctx.engine

    if resume:
        logger.info("Daemon resuming (with API): task=%s host=%s port=%d", "?", host, port)
        engine.resume()
    else:
        task_id = plan.task_id if plan else "?"
        logger.info(
            "Daemon starting (with API): task=%s host=%s port=%d",
            task_id, host, port,
        )
        engine.start(plan)

    worker = TaskWorker(
        engine=engine,
        launcher=launcher,
        bus=bus,
        max_parallel=max_parallel,
    )

    # ── FastAPI app (shares the same bus) ────────────────────────────────────
    app = create_app(
        bus=bus,
        host=host,
        port=port,
        token=token,
        team_context_root=team_context_root,
    )

    # ── uvicorn server ───────────────────────────────────────────────────────
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",  # keep daemon log clean; baton.daemon logger owns INFO
    )
    server = uvicorn.Server(config)

    # ── Signal handling ──────────────────────────────────────────────────────
    signal_handler = SignalHandler()
    signal_handler.install()

    worker_task = asyncio.create_task(worker.run(), name="baton-worker")
    server_task = asyncio.create_task(server.serve(), name="baton-api-server")
    signal_task = asyncio.create_task(signal_handler.wait(), name="baton-signal")

    summary = ""
    try:
        done, pending = await asyncio.wait(
            {worker_task, server_task, signal_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        if signal_task in done:
            # Graceful shutdown: cancel worker + server, then wait for drain.
            logger.info("Shutdown signal received — draining worker and API server.")
            worker_task.cancel()
            server.should_exit = True  # ask uvicorn to stop accepting new requests
            try:
                await asyncio.wait_for(
                    asyncio.gather(worker_task, server_task, return_exceptions=True),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Drain timeout after 30 s — forcing exit.")
            summary = "Daemon stopped by signal."

        elif worker_task in done:
            # Worker finished (or failed) — summary is its return value.
            exc = worker_task.exception()
            if exc is not None:
                summary = f"Daemon failed: {exc}"
                logger.exception("Worker raised an exception.", exc_info=exc)
            else:
                summary = worker_task.result()
                logger.info("Worker finished: %s", summary)
            # Shut the API server down gracefully; no more work to observe.
            server.should_exit = True
            signal_task.cancel()
            try:
                await asyncio.wait_for(server_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        else:
            # Server task completed first (shouldn't happen in normal operation).
            logger.warning("API server exited unexpectedly; stopping worker.")
            worker_task.cancel()
            signal_task.cancel()
            try:
                summary = await asyncio.wait_for(worker_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                summary = "Daemon stopped: API server exited unexpectedly."

    finally:
        signal_handler.uninstall()
        # Cancel any tasks still pending (defensive cleanup).
        for task in (worker_task, server_task, signal_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(worker_task, server_task, signal_task, return_exceptions=True)

    return summary


# ---------------------------------------------------------------------------
# CLI handler
# ---------------------------------------------------------------------------

def handler(args: argparse.Namespace) -> None:
    task_id: str | None = getattr(args, "task_id", None)
    supervisor = WorkerSupervisor(task_id=task_id)

    if args.daemon_action == "start":
        if not args.resume and not args.plan:
            print("Error: --plan is required (unless --resume is set)")
            return

        plan = None
        if args.plan:
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
                from agent_baton.core.orchestration.registry import AgentRegistry
                from agent_baton.core.runtime.claude_launcher import ClaudeCodeLauncher
                registry = AgentRegistry()
                registry.load_default_paths()
                launcher = ClaudeCodeLauncher(registry=registry)
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

        # --serve is only meaningful in foreground mode or after daemonization.
        # Validate it before forking so the user gets clear feedback.
        serve = getattr(args, "serve", False)
        host: str = getattr(args, "host", "127.0.0.1")
        port: int = getattr(args, "port", 8741)
        token: str | None = getattr(args, "token", None)

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

            task_label = plan.task_id if plan else "resumed execution"
            if serve:
                print(
                    f"Starting daemon for task '{task_label}' "
                    f"with API server on {host}:{port}..."
                )
            else:
                print(f"Starting daemon for task '{task_label}'...")
            daemonize()
        else:
            task_label = plan.task_id if plan else "resumed execution"
            if serve:
                print(
                    f"Starting daemon for task '{task_label}' "
                    f"with API server on {host}:{port}..."
                )
            else:
                print(f"Starting daemon for task '{task_label}'...")

        if serve:
            # ── Combined worker + API path ───────────────────────────────────
            # We bypass supervisor.start() because that method calls
            # asyncio.run() internally — we need to run uvicorn alongside the
            # worker in the same event loop via asyncio.gather().
            supervisor._root.mkdir(parents=True, exist_ok=True)
            try:
                supervisor._write_pid()
            except RuntimeError as exc:
                print(f"Error: {exc}")
                return
            supervisor._setup_logging()

            logger = logging.getLogger("baton.daemon")
            summary = ""
            try:
                summary = asyncio.run(
                    _run_daemon_with_api(
                        plan=plan,
                        launcher=launcher,
                        supervisor=supervisor,
                        max_parallel=args.max_parallel,
                        resume=args.resume,
                        host=host,
                        port=port,
                        token=token,
                        team_context_root=supervisor._root,
                        task_id=task_id,
                    )
                )
            except KeyboardInterrupt:
                summary = "Daemon interrupted by user."
                logger.info("Daemon interrupted.")
            except RuntimeError as exc:
                summary = f"Error: {exc}"
                logger.error("Daemon runtime error: %s", exc)
            except Exception as exc:
                summary = f"Daemon failed: {exc}"
                logger.exception("Daemon failed with exception.")
            finally:
                # Write status snapshot using a temporary ExecutionEngine so
                # we can read the final persisted state without re-running.
                from agent_baton.core.engine.executor import ExecutionEngine
                _engine_for_status = ExecutionEngine(
                    team_context_root=supervisor._root,
                    task_id=task_id,
                )
                supervisor._write_status(_engine_for_status, summary=summary)
                supervisor._remove_pid()
                logger.info("Daemon stopped.")
        else:
            # ── Worker-only path (original behaviour) ────────────────────────
            try:
                summary = supervisor.start(
                    plan=plan,
                    launcher=launcher,
                    max_parallel=args.max_parallel,
                    resume=args.resume,
                )
            except RuntimeError as exc:
                print(f"Error: {exc}")
                return

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

    if args.daemon_action == "list":
        project_dir = getattr(args, "project_dir", None)
        if project_dir is not None:
            context_root = Path(project_dir).resolve() / ".claude" / "team-context"
        else:
            context_root = Path(".claude/team-context").resolve()
        workers = WorkerSupervisor.list_workers(context_root)
        if not workers:
            print("No daemon workers found.")
            return
        print(f"{'TASK ID':<40}  {'PID':>7}  {'ALIVE'}")
        print("-" * 55)
        for w in workers:
            alive_str = "yes" if w["alive"] else "no"
            print(f"{w['task_id']:<40}  {w['pid']:>7}  {alive_str}")
        return

    # No subcommand — show help.
    print("Usage: baton daemon {start|status|stop|list}")
    print("Run 'baton daemon start --help' for options.")
