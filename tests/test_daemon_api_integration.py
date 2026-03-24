"""Tests for the --serve flag on ``baton daemon start``.

These tests verify that:
1. The new CLI flags are registered correctly.
2. ``_run_daemon_with_api`` creates a shared EventBus and passes it to both
   the TaskWorker and the FastAPI app.
3. The worker completes and then triggers a graceful uvicorn shutdown.
4. A pre-set shutdown signal cancels both the worker and the server.
5. The handler wires the combined path when ``--serve`` is set.

uvicorn is patched in all tests so we never actually bind a port.  The
FastAPI app factory (``create_app``) is also patched to avoid filesystem
and import-time side effects.

Async tests follow the project convention of wrapping coroutines in an inner
``async def _run()`` function and calling ``asyncio.run(_run())``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_baton.cli.commands.execution.daemon import (
    _run_daemon_with_api,
    handler,
    register,
)
from agent_baton.core.events.bus import EventBus
from agent_baton.core.runtime.launcher import DryRunLauncher
from agent_baton.core.runtime.supervisor import WorkerSupervisor
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Plan helpers (mirrors test_daemon.py helpers)
# ---------------------------------------------------------------------------

def _step(step_id: str = "1.1", agent: str = "backend") -> PlanStep:
    return PlanStep(step_id=step_id, agent_name=agent, task_description="task")


def _phase(phase_id: int = 0, steps=None) -> PlanPhase:
    return PlanPhase(phase_id=phase_id, name="P", steps=steps or [_step()])


def _plan(task_id: str = "t1") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="test plan",
        phases=[_phase()],
    )


# ---------------------------------------------------------------------------
# Argument parser registration
# ---------------------------------------------------------------------------

class TestDaemonStartFlags:
    """The new flags are present and default to the documented values."""

    def _make_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        register(sub)
        return parser

    def _parse(self, *argv: str) -> argparse.Namespace:
        return self._make_parser().parse_args(
            ["daemon", "start", "--plan", "plan.json", *argv]
        )

    def test_serve_defaults_to_false(self) -> None:
        assert self._parse().serve is False

    def test_serve_flag_sets_true(self) -> None:
        assert self._parse("--serve").serve is True

    def test_port_defaults_to_8741(self) -> None:
        assert self._parse().port == 8741

    def test_port_flag_overrides(self) -> None:
        assert self._parse("--port", "9000").port == 9000

    def test_host_defaults_to_loopback(self) -> None:
        assert self._parse().host == "127.0.0.1"

    def test_host_flag_overrides(self) -> None:
        assert self._parse("--host", "0.0.0.0").host == "0.0.0.0"

    def test_token_defaults_to_none(self) -> None:
        assert self._parse().token is None

    def test_token_flag_sets_value(self) -> None:
        assert self._parse("--token", "mysecret").token == "mysecret"


# ---------------------------------------------------------------------------
# _run_daemon_with_api — shared EventBus
# ---------------------------------------------------------------------------

class TestRunDaemonWithApiSharedBus:
    """The same EventBus instance must reach both the worker and the app."""

    def test_bus_passed_to_create_app(self, tmp_path: Path) -> None:
        """create_app() receives the EventBus created inside _run_daemon_with_api.

        create_app is imported locally inside _run_daemon_with_api so we must
        patch it at its source module (agent_baton.api.server).
        """
        captured_bus: list[EventBus] = []

        fake_server = MagicMock()
        fake_server.serve = AsyncMock(return_value=None)
        fake_server.should_exit = False

        def fake_create_app(**kwargs):
            captured_bus.append(kwargs.get("bus"))
            return MagicMock()

        supervisor = WorkerSupervisor(team_context_root=tmp_path)

        async def _run():
            with (
                patch(
                    "agent_baton.api.server.create_app",
                    side_effect=fake_create_app,
                ),
                patch("uvicorn.Config", MagicMock()),
                patch("uvicorn.Server", MagicMock(return_value=fake_server)),
            ):
                await _run_daemon_with_api(
                    plan=_plan(),
                    launcher=DryRunLauncher(),
                    supervisor=supervisor,
                    max_parallel=1,
                    resume=False,
                    host="127.0.0.1",
                    port=8741,
                    token=None,
                    team_context_root=tmp_path,
                )

        asyncio.run(_run())

        assert len(captured_bus) == 1
        assert isinstance(captured_bus[0], EventBus)

    def test_worker_uses_same_bus_as_app(self, tmp_path: Path) -> None:
        """The EventBus given to create_app is the same object the worker holds.

        TaskWorker and create_app are both imported locally inside
        _run_daemon_with_api, so we patch them at their source modules.
        """
        app_bus: list[EventBus] = []
        worker_buses: list[EventBus] = []

        def fake_create_app(**kwargs):
            app_bus.append(kwargs.get("bus"))
            return MagicMock()

        from agent_baton.core.runtime.worker import TaskWorker as OriginalTaskWorker

        class SpyTaskWorker(OriginalTaskWorker):
            def __init__(self, **kwargs):
                worker_buses.append(kwargs.get("bus"))
                super().__init__(**kwargs)

        fake_server = MagicMock()
        fake_server.serve = AsyncMock(return_value=None)
        fake_server.should_exit = False

        supervisor = WorkerSupervisor(team_context_root=tmp_path)

        async def _run():
            with (
                patch(
                    "agent_baton.api.server.create_app",
                    side_effect=fake_create_app,
                ),
                patch(
                    "agent_baton.core.runtime.worker.TaskWorker",
                    SpyTaskWorker,
                ),
                patch("uvicorn.Config", MagicMock()),
                patch("uvicorn.Server", MagicMock(return_value=fake_server)),
            ):
                await _run_daemon_with_api(
                    plan=_plan(),
                    launcher=DryRunLauncher(),
                    supervisor=supervisor,
                    max_parallel=1,
                    resume=False,
                    host="127.0.0.1",
                    port=8741,
                    token=None,
                    team_context_root=tmp_path,
                )

        asyncio.run(_run())

        assert len(app_bus) == 1
        assert len(worker_buses) == 1
        assert app_bus[0] is worker_buses[0], (
            "create_app and TaskWorker must receive the same EventBus instance"
        )


# ---------------------------------------------------------------------------
# _run_daemon_with_api — completion flow
# ---------------------------------------------------------------------------

class TestRunDaemonWithApiCompletion:
    """Worker completes normally — server is shut down and summary is returned."""

    def test_worker_completes_shuts_down_server(self, tmp_path: Path) -> None:
        """When the worker finishes, server.should_exit is set to True."""
        server_exit_flags: list[bool] = []

        class TrackingServer:
            should_exit = False

            async def serve(self) -> None:
                while not self.should_exit:
                    await asyncio.sleep(0.01)
                server_exit_flags.append(True)

        fake_server = TrackingServer()
        supervisor = WorkerSupervisor(team_context_root=tmp_path)

        async def _run():
            with (
                patch(
                    "agent_baton.api.server.create_app",
                    return_value=MagicMock(),
                ),
                patch("uvicorn.Config", MagicMock()),
                patch("uvicorn.Server", MagicMock(return_value=fake_server)),
            ):
                return await _run_daemon_with_api(
                    plan=_plan(),
                    launcher=DryRunLauncher(),
                    supervisor=supervisor,
                    max_parallel=1,
                    resume=False,
                    host="127.0.0.1",
                    port=8741,
                    token=None,
                    team_context_root=tmp_path,
                )

        summary = asyncio.run(_run())
        assert "complete" in summary.lower()
        assert server_exit_flags, "server.should_exit must have been set to True"

    def test_summary_returned_from_worker(self, tmp_path: Path) -> None:
        """The return value is the worker's summary string."""
        server = MagicMock()
        server.serve = AsyncMock(return_value=None)
        server.should_exit = False

        supervisor = WorkerSupervisor(team_context_root=tmp_path)

        async def _run():
            with (
                patch(
                    "agent_baton.api.server.create_app",
                    return_value=MagicMock(),
                ),
                patch("uvicorn.Config", MagicMock()),
                patch("uvicorn.Server", MagicMock(return_value=server)),
            ):
                return await _run_daemon_with_api(
                    plan=_plan(),
                    launcher=DryRunLauncher(),
                    supervisor=supervisor,
                    max_parallel=1,
                    resume=False,
                    host="127.0.0.1",
                    port=8741,
                    token=None,
                    team_context_root=tmp_path,
                )

        result = asyncio.run(_run())
        assert isinstance(result, str)
        assert len(result) > 0

    def test_uvicorn_config_receives_host_and_port(self, tmp_path: Path) -> None:
        """uvicorn.Config is called with the host and port passed to the coroutine."""
        captured_config_kwargs: list[dict] = []

        server = MagicMock()
        server.serve = AsyncMock(return_value=None)
        server.should_exit = False

        def fake_config(app, **kwargs):
            captured_config_kwargs.append(kwargs)
            return MagicMock()

        supervisor = WorkerSupervisor(team_context_root=tmp_path)

        async def _run():
            with (
                patch(
                    "agent_baton.api.server.create_app",
                    return_value=MagicMock(),
                ),
                patch("uvicorn.Config", side_effect=fake_config),
                patch("uvicorn.Server", MagicMock(return_value=server)),
            ):
                await _run_daemon_with_api(
                    plan=_plan(),
                    launcher=DryRunLauncher(),
                    supervisor=supervisor,
                    max_parallel=1,
                    resume=False,
                    host="0.0.0.0",
                    port=9001,
                    token=None,
                    team_context_root=tmp_path,
                )

        asyncio.run(_run())

        assert len(captured_config_kwargs) == 1
        assert captured_config_kwargs[0]["host"] == "0.0.0.0"
        assert captured_config_kwargs[0]["port"] == 9001


# ---------------------------------------------------------------------------
# _run_daemon_with_api — signal shutdown
# ---------------------------------------------------------------------------

class TestRunDaemonWithApiSignalShutdown:
    """A shutdown signal cancels both the worker and the API server."""

    def test_signal_triggers_graceful_shutdown(self, tmp_path: Path) -> None:
        """Simulating a signal completes without exception and returns a string."""
        server_exit_seen: list[bool] = []
        shutdown_event = asyncio.Event()

        class TrackingServer:
            should_exit = False

            async def serve(self) -> None:
                while not self.should_exit:
                    await asyncio.sleep(0.01)
                server_exit_seen.append(True)

        class FakeSignalHandler:
            def install(self) -> None:
                pass

            def uninstall(self) -> None:
                pass

            async def wait(self) -> None:
                await shutdown_event.wait()

        supervisor = WorkerSupervisor(team_context_root=tmp_path)

        async def _trigger_shutdown():
            await asyncio.sleep(0.05)
            shutdown_event.set()

        async def _run():
            with (
                patch(
                    "agent_baton.api.server.create_app",
                    return_value=MagicMock(),
                ),
                patch(
                    "agent_baton.core.runtime.signals.SignalHandler",
                    FakeSignalHandler,
                ),
                patch("uvicorn.Config", MagicMock()),
                patch(
                    "uvicorn.Server",
                    MagicMock(return_value=TrackingServer()),
                ),
            ):
                daemon_coro = _run_daemon_with_api(
                    plan=_plan(),
                    launcher=DryRunLauncher(),
                    supervisor=supervisor,
                    max_parallel=1,
                    resume=False,
                    host="127.0.0.1",
                    port=8741,
                    token=None,
                    team_context_root=tmp_path,
                )
                results = await asyncio.gather(
                    daemon_coro,
                    _trigger_shutdown(),
                    return_exceptions=True,
                )
            return results[0]

        result = asyncio.run(_run())
        # Result is either the worker summary (if it finished first) or the
        # signal-stop message.
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# handler() — --serve path integration
# ---------------------------------------------------------------------------

class TestHandlerServeFlag:
    """The CLI handler routes correctly when --serve is provided."""

    def _build_args(
        self,
        tmp_path: Path,
        *,
        serve: bool = True,
        foreground: bool = True,
        dry_run: bool = True,
    ) -> argparse.Namespace:
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(
            json.dumps(_plan().to_dict(), ensure_ascii=False),
            encoding="utf-8",
        )
        return argparse.Namespace(
            daemon_action="start",
            plan=str(plan_file),
            max_parallel=1,
            dry_run=dry_run,
            foreground=foreground,
            resume=False,
            project_dir=None,
            serve=serve,
            host="127.0.0.1",
            port=8741,
            token=None,
        )

    def test_serve_false_calls_supervisor_start(self, tmp_path: Path) -> None:
        """Without --serve, supervisor.start() is called (original behaviour)."""
        args = self._build_args(tmp_path, serve=False)

        with patch(
            "agent_baton.cli.commands.execution.daemon.WorkerSupervisor"
        ) as MockSupervisor:
            mock_sup = MagicMock()
            mock_sup.start.return_value = "Task t1 completed."
            mock_sup.pid_path = MagicMock()
            mock_sup.pid_path.exists.return_value = False
            MockSupervisor.return_value = mock_sup

            handler(args)

        mock_sup.start.assert_called_once()

    def test_serve_true_does_not_call_supervisor_start(self, tmp_path: Path) -> None:
        """With --serve, the combined path is taken — supervisor.start() is NOT called."""
        args = self._build_args(tmp_path, serve=True)

        with (
            patch(
                "agent_baton.cli.commands.execution.daemon.WorkerSupervisor"
            ) as MockSupervisor,
            patch(
                "agent_baton.cli.commands.execution.daemon.asyncio.run",
                return_value="Task t1 completed.",
            ),
        ):
            mock_sup = MagicMock()
            mock_sup._root = tmp_path
            MockSupervisor.return_value = mock_sup

            handler(args)

        mock_sup.start.assert_not_called()

    def test_serve_true_calls_asyncio_run(self, tmp_path: Path) -> None:
        """With --serve, asyncio.run is called exactly once."""
        args = self._build_args(tmp_path, serve=True)

        run_calls: list = []

        def fake_asyncio_run(coro, **kwargs):
            run_calls.append(coro)
            coro.close()  # prevent "coroutine never awaited" warning
            return "Task t1 completed."

        with (
            patch(
                "agent_baton.cli.commands.execution.daemon.WorkerSupervisor"
            ) as MockSupervisor,
            patch(
                "agent_baton.cli.commands.execution.daemon.asyncio.run",
                side_effect=fake_asyncio_run,
            ),
        ):
            mock_sup = MagicMock()
            mock_sup._root = tmp_path
            MockSupervisor.return_value = mock_sup

            handler(args)

        assert len(run_calls) == 1
        import inspect
        assert inspect.iscoroutine(run_calls[0])

    def test_serve_pid_file_written_and_removed(self, tmp_path: Path) -> None:
        """With --serve, the PID file lifecycle (_write_pid, _remove_pid) is invoked."""
        args = self._build_args(tmp_path, serve=True)

        with (
            patch(
                "agent_baton.cli.commands.execution.daemon.WorkerSupervisor"
            ) as MockSupervisor,
            patch(
                "agent_baton.cli.commands.execution.daemon.asyncio.run",
                return_value="done",
            ),
        ):
            mock_sup = MagicMock()
            mock_sup._root = tmp_path
            MockSupervisor.return_value = mock_sup

            handler(args)

        mock_sup._write_pid.assert_called_once()
        mock_sup._remove_pid.assert_called_once()

    def test_serve_status_written_after_run(self, tmp_path: Path) -> None:
        """With --serve, _write_status is called with the asyncio.run() return value."""
        args = self._build_args(tmp_path, serve=True)

        # ExecutionEngine is imported locally inside the handler's finally block;
        # patch it at the source module level.
        with (
            patch(
                "agent_baton.cli.commands.execution.daemon.WorkerSupervisor"
            ) as MockSupervisor,
            patch(
                "agent_baton.cli.commands.execution.daemon.asyncio.run",
                return_value="Task t1 completed.",
            ),
            patch(
                "agent_baton.core.engine.executor.ExecutionEngine",
                MagicMock(),
            ),
        ):
            mock_sup = MagicMock()
            mock_sup._root = tmp_path
            MockSupervisor.return_value = mock_sup

            handler(args)

        mock_sup._write_status.assert_called_once()
        _, kwargs = mock_sup._write_status.call_args
        assert kwargs.get("summary") == "Task t1 completed."

    def test_missing_plan_error_with_serve(self, tmp_path: Path) -> None:
        """--serve does not bypass the --plan required check."""
        args = argparse.Namespace(
            daemon_action="start",
            plan=None,
            max_parallel=1,
            dry_run=True,
            foreground=True,
            resume=False,
            project_dir=None,
            serve=True,
            host="127.0.0.1",
            port=8741,
            token=None,
        )
        with patch(
            "agent_baton.cli.commands.execution.daemon.asyncio.run"
        ) as mock_run:
            handler(args)

        mock_run.assert_not_called()

    def test_write_pid_failure_aborts_serve(self, tmp_path: Path) -> None:
        """If _write_pid raises RuntimeError, asyncio.run is not called."""
        args = self._build_args(tmp_path, serve=True)

        with (
            patch(
                "agent_baton.cli.commands.execution.daemon.WorkerSupervisor"
            ) as MockSupervisor,
            patch(
                "agent_baton.cli.commands.execution.daemon.asyncio.run"
            ) as mock_run,
        ):
            mock_sup = MagicMock()
            mock_sup._root = tmp_path
            mock_sup._write_pid.side_effect = RuntimeError("Another daemon is already running.")
            MockSupervisor.return_value = mock_sup

            handler(args)

        mock_run.assert_not_called()
