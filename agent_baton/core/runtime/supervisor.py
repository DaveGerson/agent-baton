"""WorkerSupervisor -- lifecycle management for daemon-mode execution.

The supervisor wraps ``TaskWorker`` with production-grade process management:

- **PID file management** with ``flock()``-based locking to prevent
  duplicate daemons.  The OS releases the lock automatically when the
  process exits, eliminating stale-PID-file race conditions.
- **Structured logging** via ``RotatingFileHandler`` (10 MB, 3 backups).
- **Graceful shutdown** via ``SignalHandler`` (SIGTERM/SIGINT) with a
  30-second drain timeout for in-flight agents.
- **Status querying** from execution state + event log, readable by
  ``baton daemon status`` without requiring a running daemon.
- **Namespaced execution** directories for concurrent plans when
  ``task_id`` is provided.

The supervisor is the entry point for ``baton daemon start``.  It blocks
the calling process until the worker completes or a shutdown signal arrives.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

if sys.platform != "win32":
    import fcntl
else:
    fcntl = None  # type: ignore[assignment]

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.protocols import ExecutionDriver
from agent_baton.core.events.bus import EventBus
from agent_baton.core.runtime.context import ExecutionContext
from agent_baton.core.runtime.launcher import AgentLauncher
from agent_baton.core.runtime.signals import SignalHandler
from agent_baton.core.runtime.worker import TaskWorker
from agent_baton.models.execution import MachinePlan


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class WorkerSupervisor:
    """Manage the lifecycle of a ``TaskWorker`` in daemon mode.

    The supervisor handles everything outside the worker loop itself:
    PID file locking, log rotation, signal handling, and status snapshots.

    Files managed (legacy, when *task_id* is ``None``):

    - ``daemon.pid`` -- PID of the running process (flock-locked).
    - ``daemon.log`` -- structured log output from the worker.
    - ``daemon-status.json`` -- snapshot of last known execution status.

    When *task_id* is provided, files are namespaced under
    ``executions/<task_id>/``:

    - ``executions/<task_id>/worker.pid``
    - ``executions/<task_id>/worker.log``
    - ``executions/<task_id>/worker-status.json``

    Attributes:
        _root: Resolved team-context root directory.
        _task_id: Optional task ID for namespaced execution directories.
    """

    def __init__(
        self,
        team_context_root: Path | None = None,
        task_id: str | None = None,
    ) -> None:
        self._root = (team_context_root or Path(".claude/team-context")).resolve()
        self._task_id = task_id

    @property
    def _exec_dir(self) -> Path:
        """Directory for this supervisor's files (namespaced or legacy)."""
        if self._task_id:
            return self._root / "executions" / self._task_id
        return self._root

    @property
    def pid_path(self) -> Path:
        if self._task_id:
            return self._exec_dir / "worker.pid"
        return self._root / "daemon.pid"

    @property
    def log_path(self) -> Path:
        if self._task_id:
            return self._exec_dir / "worker.log"
        return self._root / "daemon.log"

    @property
    def status_path(self) -> Path:
        if self._task_id:
            return self._exec_dir / "worker-status.json"
        return self._root / "daemon-status.json"

    # ── Start ───────────────────────────────────────────────────────────────

    def start(
        self,
        plan: MachinePlan,
        launcher: AgentLauncher,
        bus: EventBus | None = None,
        max_parallel: int = 3,
        resume: bool = False,
    ) -> str:
        """Start the worker synchronously (blocking).

        Writes PID file, runs the async worker loop, cleans up on exit.
        Returns the completion summary.

        Args:
            plan: The execution plan.  Ignored when *resume* is True.
            launcher: Agent launcher implementation.
            bus: Event bus (created if not supplied).
            max_parallel: Maximum concurrently dispatched agents.
            resume: When True, call ``engine.resume()`` instead of
                ``engine.start(plan)`` to continue from persisted state.
        """
        self._root.mkdir(parents=True, exist_ok=True)
        self._write_pid()
        self._setup_logging()

        logger = logging.getLogger("baton.daemon")

        ctx = ExecutionContext.build(
            launcher=launcher,
            team_context_root=self._root,
            bus=bus,
            task_id=self._task_id,
        )
        engine = ctx.engine

        if resume:
            logger.info("Daemon resuming: task=%s", plan.task_id)
            engine.resume()
            engine.recover_dispatched_steps()
        else:
            logger.info("Daemon starting: task=%s", plan.task_id)
            engine.start(plan)

        # Use resource_limits.max_concurrent_agents from the plan when set,
        # falling back to the caller-supplied max_parallel.
        effective_parallel = max_parallel
        if plan.resource_limits is not None:
            effective_parallel = plan.resource_limits.max_concurrent_agents

        worker = TaskWorker(
            engine=engine,
            launcher=launcher,
            bus=ctx.bus,
            max_parallel=effective_parallel,
        )

        summary = ""
        try:
            summary = asyncio.run(self._run_with_signals(worker, launcher))
        except KeyboardInterrupt:
            summary = "Daemon interrupted by user."
            logger.info("Daemon interrupted.")
        except Exception as exc:
            summary = f"Daemon failed: {exc}"
            logger.exception("Daemon failed with exception.")
        finally:
            self._write_status(engine, summary=summary)
            self._remove_pid()
            logger.info("Daemon stopped.")

        return summary

    async def _run_with_signals(
        self, worker: TaskWorker, launcher: AgentLauncher
    ) -> str:
        """Run the worker with signal handling for graceful shutdown.

        Installs SIGTERM and SIGINT handlers via :class:`SignalHandler`.
        When a signal arrives the worker task is cancelled and we wait up to
        30 seconds for an orderly drain of in-flight agents.  The ``finally``
        block always calls ``launcher.cleanup()`` (if available) so that child
        ``claude`` subprocesses started with ``start_new_session=True`` are not
        orphaned when the daemon exits.
        """
        handler = SignalHandler()
        handler.install()
        try:
            worker_task = asyncio.create_task(worker.run())
            signal_task = asyncio.create_task(handler.wait())
            done, _pending = await asyncio.wait(
                {worker_task, signal_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if signal_task in done:
                # A shutdown signal was received — drain the worker gracefully.
                worker_task.cancel()
                try:
                    await asyncio.wait_for(worker_task, timeout=30.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                return "Daemon stopped by signal."
            else:
                # Worker finished normally — cancel the signal waiter.
                signal_task.cancel()
                return worker_task.result()
        finally:
            handler.uninstall()
            cleanup = getattr(launcher, "cleanup", None)
            if cleanup is not None:
                await cleanup()

    # ── Status ──────────────────────────────────────────────────────────────

    def status(self) -> dict:
        """Read execution status without starting anything.

        Returns a dict with keys: running, pid, task_id, status, etc.
        """
        result: dict = {"running": False, "pid": None}

        # Check PID file.
        if self.pid_path.exists():
            try:
                pid = int(self.pid_path.read_text().strip())
                result["pid"] = pid
                # Check if process is alive.
                try:
                    os.kill(pid, 0)
                    result["running"] = True
                except OSError:
                    result["running"] = False
            except (ValueError, OSError):
                pass

        # Read engine status.
        engine = ExecutionEngine(
            team_context_root=self._root, task_id=self._task_id
        )
        engine_status = engine.status()
        result.update(engine_status)

        # Read last saved daemon status.
        if self.status_path.exists():
            try:
                data = json.loads(self.status_path.read_text(encoding="utf-8"))
                result["last_update"] = data.get("timestamp", "")
            except (json.JSONDecodeError, OSError):
                pass

        return result

    def stop(self, timeout: float = 30.0) -> bool:
        """Send SIGTERM to the running daemon and wait for it to exit.

        Polls for process exit up to *timeout* seconds after sending SIGTERM.

        Returns True if the process exited within the timeout, False if no
        daemon was found or if it failed to exit before the deadline.
        """
        if not self.pid_path.exists():
            return False
        try:
            pid = int(self.pid_path.read_text().strip())
            os.kill(pid, signal.SIGTERM)
        except (ValueError, OSError):
            return False

        import time

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)  # probe — raises OSError if process is gone
                time.sleep(0.5)
            except OSError:
                # Process has exited.  Clean up a stale PID file if the daemon
                # didn't remove it (e.g. killed -9 or crash).
                if self.pid_path.exists():
                    try:
                        self.pid_path.unlink()
                    except OSError:
                        pass
                return True
        # Process still alive after timeout.
        return False

    # ── Discovery ──────────────────────────────────────────────────────────

    @staticmethod
    def list_workers(team_context_root: Path) -> list[dict]:
        """Scan all execution directories for running worker processes.

        Returns a list of dicts with keys: ``task_id``, ``pid``,
        ``alive`` (bool), ``pid_path``.  Checks liveness via
        ``os.kill(pid, 0)``.
        """
        exec_dir = team_context_root / "executions"
        results: list[dict] = []

        if exec_dir.is_dir():
            for child in sorted(exec_dir.iterdir()):
                if not child.is_dir():
                    continue
                pid_file = child / "worker.pid"
                if not pid_file.exists():
                    continue
                try:
                    pid = int(pid_file.read_text().strip())
                except (ValueError, OSError):
                    continue
                alive = False
                try:
                    os.kill(pid, 0)
                    alive = True
                except OSError:
                    pass
                results.append({
                    "task_id": child.name,
                    "pid": pid,
                    "alive": alive,
                    "pid_path": str(pid_file),
                })

        # Also check legacy daemon.pid
        legacy_pid = team_context_root / "daemon.pid"
        if legacy_pid.exists():
            try:
                pid = int(legacy_pid.read_text().strip())
                alive = False
                try:
                    os.kill(pid, 0)
                    alive = True
                except OSError:
                    pass
                results.append({
                    "task_id": "(legacy)",
                    "pid": pid,
                    "alive": alive,
                    "pid_path": str(legacy_pid),
                })
            except (ValueError, OSError):
                pass

        return results

    # ── Internal helpers ────────────────────────────────────────────────────

    def _write_pid(self) -> None:
        self._exec_dir.mkdir(parents=True, exist_ok=True)
        # Open the PID file and acquire an exclusive lock BEFORE writing.
        # The OS releases the lock automatically when the process exits or the
        # FD is closed, which eliminates the stale-PID-file race condition.
        # Note: flock() on network filesystems may not enforce mutual exclusion.
        self._pid_fd = open(self.pid_path, "w+")  # noqa: SIM115  # "w+" allows seek+read
        if fcntl is not None:
            try:
                fcntl.flock(self._pid_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self._pid_fd.close()
                self._pid_fd = None
                raise RuntimeError("Another daemon is already running.")
        self._pid_fd.write(str(os.getpid()))
        self._pid_fd.flush()
        # Hold the FD open — lock is released when process exits or FD closes.

    def _remove_pid(self) -> None:
        if hasattr(self, "_pid_fd") and self._pid_fd is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(self._pid_fd, fcntl.LOCK_UN)
                self._pid_fd.close()
            except OSError:
                pass
            self._pid_fd = None
        if self.pid_path.exists():
            try:
                self.pid_path.unlink()
            except OSError:
                pass

    def _setup_logging(self) -> None:
        """Configure file-based logging for the daemon with rotation."""
        self._exec_dir.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("baton.daemon")
        # Remove any existing handlers so we always log to the current path.
        for h in logger.handlers[:]:
            logger.removeHandler(h)
            h.close()
        handler = RotatingFileHandler(
            str(self.log_path),
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    def _write_status(self, engine: ExecutionDriver, summary: str = "") -> None:
        """Write a status snapshot to disk atomically.

        Writes to a temporary file first, then renames over the destination.
        This prevents a partially-written JSON file if the process crashes
        mid-write.
        """
        status = engine.status()
        status["timestamp"] = _utcnow()
        status["summary"] = summary
        tmp_path = self.status_path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(status, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(str(tmp_path), str(self.status_path))
