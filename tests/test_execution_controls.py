"""Tests for PMO execution interrupt controls.

Endpoints covered (all prefixed with /api/v1):

  POST /pmo/execute/{card_id}/pause       — send SIGSTOP to worker
  POST /pmo/execute/{card_id}/resume      — send SIGCONT to worker
  POST /pmo/execute/{card_id}/cancel      — send SIGTERM to worker
  POST /pmo/execute/{card_id}/retry-step  — reset failed step to pending
  POST /pmo/execute/{card_id}/skip-step   — mark step as skipped

Also covers ``WorkerSupervisor`` unit tests for the signal-dispatch
methods (pause_worker, resume_worker, cancel_worker).

Strategy:
- PmoScanner is replaced with _StubScanner returning controlled cards.
- PmoStore is backed by a tmp directory.
- os.kill is mocked to avoid sending signals to real processes.
- Storage backend writes real JSON state files so load_execution works.
- EventBus is overridden so SSE publish calls never raise.
"""
from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.deps import (  # noqa: E402
    get_bus,
    get_forge_session,
    get_pmo_scanner,
    get_pmo_store,
)
from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.events.bus import EventBus  # noqa: E402
from agent_baton.core.pmo.store import PmoStore  # noqa: E402
from agent_baton.core.runtime.supervisor import WorkerSupervisor  # noqa: E402
from agent_baton.models.execution import (  # noqa: E402
    ExecutionState,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)
from agent_baton.models.pmo import PmoCard, PmoProject  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tmp_store(tmp_path: Path) -> PmoStore:
    return PmoStore(
        config_path=tmp_path / "pmo-config.json",
        archive_path=tmp_path / "pmo-archive.jsonl",
    )


def _minimal_plan(task_id: str = "ctrl-task-001") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Execution control test plan",
        phases=[
            PlanPhase(
                phase_id=0,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer--python",
                        task_description="Implement feature",
                    ),
                    PlanStep(
                        step_id="1.2",
                        agent_name="frontend-engineer--react",
                        task_description="Add UI",
                    ),
                ],
                gate=PlanGate(gate_type="test", command="pytest"),
            )
        ],
    )


def _make_card(
    task_id: str,
    project_id: str = "proj-ctrl",
    column: str = "executing",
) -> PmoCard:
    return PmoCard(
        card_id=task_id,
        project_id=project_id,
        program="CTRL",
        title=f"Card {task_id}",
        column=column,
        risk_level="LOW",
        priority=0,
        agents=["backend-engineer--python"],
    )


class _StubScanner:
    def __init__(self, cards: list[PmoCard]) -> None:
        self._cards = cards

    def scan_all(self) -> list[PmoCard]:
        return list(self._cards)

    def program_health(self, cards=None):
        return {}

    def find_card(self, card_id: str):
        for c in self._cards:
            if c.card_id == card_id:
                return c, None
        raise KeyError(card_id)


def _make_app(
    tmp_path: Path,
    store: PmoStore,
    cards: list[PmoCard],
    bus: EventBus | None = None,
) -> TestClient:
    app = create_app(team_context_root=tmp_path)
    scanner = _StubScanner(cards)
    forge_stub = MagicMock()
    _bus = bus or EventBus()

    app.dependency_overrides[get_pmo_store] = lambda: store
    app.dependency_overrides[get_pmo_scanner] = lambda: scanner
    app.dependency_overrides[get_forge_session] = lambda: forge_stub
    app.dependency_overrides[get_bus] = lambda: _bus
    return TestClient(app)


def _register_project(
    store: PmoStore,
    tmp_path: Path,
    project_id: str = "proj-ctrl",
) -> Path:
    project_root = tmp_path / project_id
    project_root.mkdir(parents=True, exist_ok=True)
    store.register_project(
        PmoProject(
            project_id=project_id,
            name="Control Project",
            path=str(project_root),
            program="CTRL",
        )
    )
    return project_root


def _write_execution_state(project_root: Path, state: ExecutionState) -> None:
    exec_dir = (
        project_root / ".claude" / "team-context" / "executions" / state.task_id
    )
    exec_dir.mkdir(parents=True, exist_ok=True)
    (exec_dir / "execution-state.json").write_text(
        json.dumps(state.to_dict()), encoding="utf-8"
    )


def _write_pid_file(project_root: Path, task_id: str, pid: int) -> None:
    exec_dir = (
        project_root / ".claude" / "team-context" / "executions" / task_id
    )
    exec_dir.mkdir(parents=True, exist_ok=True)
    (exec_dir / "worker.pid").write_text(str(pid), encoding="utf-8")


# ===========================================================================
# WorkerSupervisor unit tests
# ===========================================================================


class TestWorkerSupervisorSignals:
    """Unit tests for WorkerSupervisor.pause/resume/cancel_worker."""

    def test_pause_worker_sends_sigstop(self, tmp_path: Path) -> None:
        supervisor = WorkerSupervisor(team_context_root=tmp_path)

        # Write a PID file for the task.
        exec_dir = tmp_path / "executions" / "task-sig-001"
        exec_dir.mkdir(parents=True, exist_ok=True)
        (exec_dir / "worker.pid").write_text("12345", encoding="utf-8")

        with patch("os.kill") as mock_kill:
            pid = supervisor.pause_worker("task-sig-001")

        mock_kill.assert_called_once_with(12345, signal.SIGSTOP)
        assert pid == 12345

    def test_resume_worker_sends_sigcont(self, tmp_path: Path) -> None:
        supervisor = WorkerSupervisor(team_context_root=tmp_path)

        exec_dir = tmp_path / "executions" / "task-sig-002"
        exec_dir.mkdir(parents=True, exist_ok=True)
        (exec_dir / "worker.pid").write_text("22222", encoding="utf-8")

        with patch("os.kill") as mock_kill:
            pid = supervisor.resume_worker("task-sig-002")

        mock_kill.assert_called_once_with(22222, signal.SIGCONT)
        assert pid == 22222

    def test_cancel_worker_sends_sigterm(self, tmp_path: Path) -> None:
        supervisor = WorkerSupervisor(team_context_root=tmp_path)

        exec_dir = tmp_path / "executions" / "task-sig-003"
        exec_dir.mkdir(parents=True, exist_ok=True)
        (exec_dir / "worker.pid").write_text("33333", encoding="utf-8")

        with patch("os.kill") as mock_kill:
            pid = supervisor.cancel_worker("task-sig-003")

        mock_kill.assert_called_once_with(33333, signal.SIGTERM)
        assert pid == 33333

    def test_pause_worker_raises_file_not_found_when_no_pid_file(
        self, tmp_path: Path
    ) -> None:
        supervisor = WorkerSupervisor(team_context_root=tmp_path)
        with pytest.raises(FileNotFoundError):
            supervisor.pause_worker("no-such-task-id")

    def test_resume_worker_raises_file_not_found_when_no_pid_file(
        self, tmp_path: Path
    ) -> None:
        supervisor = WorkerSupervisor(team_context_root=tmp_path)
        with pytest.raises(FileNotFoundError):
            supervisor.resume_worker("no-such-task-id")

    def test_cancel_worker_raises_file_not_found_when_no_pid_file(
        self, tmp_path: Path
    ) -> None:
        supervisor = WorkerSupervisor(team_context_root=tmp_path)
        with pytest.raises(FileNotFoundError):
            supervisor.cancel_worker("no-such-task-id")

    def test_pause_worker_propagates_process_lookup_error(
        self, tmp_path: Path
    ) -> None:
        """If os.kill raises ProcessLookupError (dead process), it propagates."""
        supervisor = WorkerSupervisor(team_context_root=tmp_path)

        exec_dir = tmp_path / "executions" / "task-dead"
        exec_dir.mkdir(parents=True, exist_ok=True)
        (exec_dir / "worker.pid").write_text("99999", encoding="utf-8")

        with patch("os.kill", side_effect=ProcessLookupError("no process")):
            with pytest.raises(ProcessLookupError):
                supervisor.pause_worker("task-dead")


# ===========================================================================
# POST /api/v1/pmo/execute/{card_id}/pause
# ===========================================================================


class TestPauseExecution:
    def test_unknown_card_returns_404(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        client = _make_app(tmp_path, store, [])
        r = client.post("/api/v1/pmo/execute/no-card/pause")
        assert r.status_code == 404

    def test_card_with_no_pid_file_returns_404(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        _register_project(store, tmp_path)
        card = _make_card("pause-nopid")
        client = _make_app(tmp_path, store, [card])

        r = client.post("/api/v1/pmo/execute/pause-nopid/pause")
        assert r.status_code == 404

    def test_pause_returns_200_with_mock_signal(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        _write_pid_file(project_root, "pause-ok", 54321)

        card = _make_card("pause-ok")
        client = _make_app(tmp_path, store, [card])

        with patch("os.kill"):
            r = client.post("/api/v1/pmo/execute/pause-ok/pause")

        assert r.status_code == 200

    def test_pause_response_status_is_paused(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        _write_pid_file(project_root, "pause-status", 54321)

        card = _make_card("pause-status")
        client = _make_app(tmp_path, store, [card])

        with patch("os.kill"):
            body = client.post(
                "/api/v1/pmo/execute/pause-status/pause"
            ).json()

        assert body["status"] == "paused"

    def test_pause_response_contains_task_id(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        _write_pid_file(project_root, "pause-tid", 54321)

        card = _make_card("pause-tid")
        client = _make_app(tmp_path, store, [card])

        with patch("os.kill"):
            body = client.post(
                "/api/v1/pmo/execute/pause-tid/pause"
            ).json()

        assert body["task_id"] == "pause-tid"

    def test_pause_sends_sigstop_via_os_kill(self, tmp_path: Path) -> None:
        """Verify that os.kill is called with SIGSTOP for a valid card."""
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        _write_pid_file(project_root, "pause-sig", 77777)

        card = _make_card("pause-sig")
        client = _make_app(tmp_path, store, [card])

        with patch("os.kill") as mock_kill:
            client.post("/api/v1/pmo/execute/pause-sig/pause")

        mock_kill.assert_called_once_with(77777, signal.SIGSTOP)

    def test_dead_process_returns_409(self, tmp_path: Path) -> None:
        """A ProcessLookupError from os.kill should produce a 409 response."""
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        _write_pid_file(project_root, "pause-dead", 99998)

        card = _make_card("pause-dead")
        client = _make_app(tmp_path, store, [card])

        with patch("os.kill", side_effect=ProcessLookupError("no proc")):
            r = client.post("/api/v1/pmo/execute/pause-dead/pause")

        assert r.status_code == 409


# ===========================================================================
# POST /api/v1/pmo/execute/{card_id}/resume
# ===========================================================================


class TestResumeExecution:
    def test_unknown_card_returns_404(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        client = _make_app(tmp_path, store, [])
        r = client.post("/api/v1/pmo/execute/no-card/resume")
        assert r.status_code == 404

    def test_card_with_no_pid_file_returns_404(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        _register_project(store, tmp_path)
        card = _make_card("resume-nopid")
        client = _make_app(tmp_path, store, [card])

        r = client.post("/api/v1/pmo/execute/resume-nopid/resume")
        assert r.status_code == 404

    def test_resume_returns_200_with_mock_signal(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        _write_pid_file(project_root, "resume-ok", 11111)

        card = _make_card("resume-ok")
        client = _make_app(tmp_path, store, [card])

        with patch("os.kill"):
            r = client.post("/api/v1/pmo/execute/resume-ok/resume")

        assert r.status_code == 200

    def test_resume_response_status_is_running(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        _write_pid_file(project_root, "resume-status", 11111)

        card = _make_card("resume-status")
        client = _make_app(tmp_path, store, [card])

        with patch("os.kill"):
            body = client.post(
                "/api/v1/pmo/execute/resume-status/resume"
            ).json()

        assert body["status"] == "running"

    def test_resume_sends_sigcont_via_os_kill(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        _write_pid_file(project_root, "resume-sig", 22222)

        card = _make_card("resume-sig")
        client = _make_app(tmp_path, store, [card])

        with patch("os.kill") as mock_kill:
            client.post("/api/v1/pmo/execute/resume-sig/resume")

        mock_kill.assert_called_once_with(22222, signal.SIGCONT)


# ===========================================================================
# POST /api/v1/pmo/execute/{card_id}/cancel
# ===========================================================================


class TestCancelExecution:
    def test_unknown_card_returns_404(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        client = _make_app(tmp_path, store, [])
        r = client.post("/api/v1/pmo/execute/no-card/cancel")
        assert r.status_code == 404

    def test_card_with_no_pid_file_returns_404(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        _register_project(store, tmp_path)
        card = _make_card("cancel-nopid")
        client = _make_app(tmp_path, store, [card])

        r = client.post("/api/v1/pmo/execute/cancel-nopid/cancel")
        assert r.status_code == 404

    def test_cancel_returns_200_with_mock_signal(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        _write_pid_file(project_root, "cancel-ok", 44444)

        card = _make_card("cancel-ok")
        client = _make_app(tmp_path, store, [card])

        with patch("os.kill"):
            r = client.post("/api/v1/pmo/execute/cancel-ok/cancel")

        assert r.status_code == 200

    def test_cancel_response_status_is_cancelled(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        _write_pid_file(project_root, "cancel-status", 44444)

        card = _make_card("cancel-status")
        client = _make_app(tmp_path, store, [card])

        with patch("os.kill"):
            body = client.post(
                "/api/v1/pmo/execute/cancel-status/cancel"
            ).json()

        assert body["status"] == "cancelled"

    def test_cancel_sends_sigterm_via_os_kill(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        _write_pid_file(project_root, "cancel-sig", 55555)

        card = _make_card("cancel-sig")
        client = _make_app(tmp_path, store, [card])

        with patch("os.kill") as mock_kill:
            client.post("/api/v1/pmo/execute/cancel-sig/cancel")

        mock_kill.assert_called_once_with(55555, signal.SIGTERM)

    def test_cancel_response_contains_task_id(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        _write_pid_file(project_root, "cancel-tid", 44444)

        card = _make_card("cancel-tid")
        client = _make_app(tmp_path, store, [card])

        with patch("os.kill"):
            body = client.post(
                "/api/v1/pmo/execute/cancel-tid/cancel"
            ).json()

        assert body["task_id"] == "cancel-tid"


# ===========================================================================
# POST /api/v1/pmo/execute/{card_id}/retry-step
# ===========================================================================


class TestRetryStep:
    def test_unknown_card_returns_404(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        client = _make_app(tmp_path, store, [])
        r = client.post(
            "/api/v1/pmo/execute/no-card/retry-step",
            json={"step_id": "1.1"},
        )
        assert r.status_code == 404

    def test_missing_step_id_returns_422(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("retry-nofield")
        state = ExecutionState(plan=plan, task_id="retry-nofield")
        _write_execution_state(project_root, state)

        card = _make_card("retry-nofield")
        client = _make_app(tmp_path, store, [card])

        r = client.post(
            "/api/v1/pmo/execute/retry-nofield/retry-step",
            json={},
        )
        assert r.status_code == 422

    def test_step_not_in_execution_state_returns_404(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("retry-nostep")
        state = ExecutionState(plan=plan, task_id="retry-nostep")
        _write_execution_state(project_root, state)

        card = _make_card("retry-nostep")
        client = _make_app(tmp_path, store, [card])

        r = client.post(
            "/api/v1/pmo/execute/retry-nostep/retry-step",
            json={"step_id": "9.9"},
        )
        assert r.status_code == 404

    def test_non_failed_step_returns_409(self, tmp_path: Path) -> None:
        """Retrying a completed step should return 409."""
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("retry-complete")
        state = ExecutionState(plan=plan, task_id="retry-complete")
        state.step_results = [
            StepResult(
                step_id="1.1",
                agent_name="backend-engineer--python",
                status="complete",
                outcome="Done",
            )
        ]
        _write_execution_state(project_root, state)

        card = _make_card("retry-complete")
        client = _make_app(tmp_path, store, [card])

        r = client.post(
            "/api/v1/pmo/execute/retry-complete/retry-step",
            json={"step_id": "1.1"},
        )
        assert r.status_code == 409

    def test_failed_step_is_reset_to_pending(self, tmp_path: Path) -> None:
        """Retrying a failed step removes its result so the engine re-dispatches."""
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("retry-ok")
        state = ExecutionState(plan=plan, task_id="retry-ok")
        state.step_results = [
            StepResult(
                step_id="1.1",
                agent_name="backend-engineer--python",
                status="failed",
                outcome="",
                error="Timeout",
            )
        ]
        _write_execution_state(project_root, state)

        card = _make_card("retry-ok")
        client = _make_app(tmp_path, store, [card])

        r = client.post(
            "/api/v1/pmo/execute/retry-ok/retry-step",
            json={"step_id": "1.1"},
        )
        assert r.status_code == 200

    def test_retry_response_status_is_retried(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("retry-status")
        state = ExecutionState(plan=plan, task_id="retry-status")
        state.step_results = [
            StepResult(
                step_id="1.1",
                agent_name="backend-engineer--python",
                status="failed",
                error="Timeout",
            )
        ]
        _write_execution_state(project_root, state)

        card = _make_card("retry-status")
        client = _make_app(tmp_path, store, [card])

        body = client.post(
            "/api/v1/pmo/execute/retry-status/retry-step",
            json={"step_id": "1.1"},
        ).json()
        assert body["status"] == "retried"

    def test_retry_response_contains_step_id(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("retry-stepid")
        state = ExecutionState(plan=plan, task_id="retry-stepid")
        state.step_results = [
            StepResult(
                step_id="1.2",
                agent_name="frontend-engineer--react",
                status="failed",
                error="Build error",
            )
        ]
        _write_execution_state(project_root, state)

        card = _make_card("retry-stepid")
        client = _make_app(tmp_path, store, [card])

        body = client.post(
            "/api/v1/pmo/execute/retry-stepid/retry-step",
            json={"step_id": "1.2"},
        ).json()
        assert body["step_id"] == "1.2"

    def test_retried_step_result_removed_from_state(
        self, tmp_path: Path
    ) -> None:
        """After a successful retry the failed StepResult is gone."""
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("retry-removes")
        state = ExecutionState(plan=plan, task_id="retry-removes")
        state.step_results = [
            StepResult(
                step_id="1.1",
                agent_name="backend-engineer--python",
                status="failed",
                error="Crash",
            )
        ]
        _write_execution_state(project_root, state)

        card = _make_card("retry-removes")
        client = _make_app(tmp_path, store, [card])
        client.post(
            "/api/v1/pmo/execute/retry-removes/retry-step",
            json={"step_id": "1.1"},
        )

        # Read back the state and verify 1.1 result is gone.
        exec_dir = (
            project_root
            / ".claude"
            / "team-context"
            / "executions"
            / "retry-removes"
        )
        saved = json.loads((exec_dir / "execution-state.json").read_text())
        step_ids = [r["step_id"] for r in saved.get("step_results", [])]
        assert "1.1" not in step_ids


# ===========================================================================
# POST /api/v1/pmo/execute/{card_id}/skip-step
# ===========================================================================


class TestSkipStep:
    def test_unknown_card_returns_404(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        client = _make_app(tmp_path, store, [])
        r = client.post(
            "/api/v1/pmo/execute/no-card/skip-step",
            json={"step_id": "1.1"},
        )
        assert r.status_code == 404

    def test_missing_step_id_returns_422(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("skip-nofield")
        state = ExecutionState(plan=plan, task_id="skip-nofield")
        _write_execution_state(project_root, state)

        card = _make_card("skip-nofield")
        client = _make_app(tmp_path, store, [card])

        r = client.post(
            "/api/v1/pmo/execute/skip-nofield/skip-step",
            json={},
        )
        assert r.status_code == 422

    def test_skip_complete_step_returns_409(self, tmp_path: Path) -> None:
        """Skipping a step that is already 'complete' must be rejected."""
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("skip-complete")
        state = ExecutionState(plan=plan, task_id="skip-complete")
        state.step_results = [
            StepResult(
                step_id="1.1",
                agent_name="backend-engineer--python",
                status="complete",
                outcome="Already done",
            )
        ]
        _write_execution_state(project_root, state)

        card = _make_card("skip-complete")
        client = _make_app(tmp_path, store, [card])

        r = client.post(
            "/api/v1/pmo/execute/skip-complete/skip-step",
            json={"step_id": "1.1", "reason": "Want to skip"},
        )
        assert r.status_code == 409

    def test_skip_failed_step_returns_200(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("skip-ok")
        state = ExecutionState(plan=plan, task_id="skip-ok")
        state.step_results = [
            StepResult(
                step_id="1.1",
                agent_name="backend-engineer--python",
                status="failed",
                error="Timeout",
            )
        ]
        _write_execution_state(project_root, state)

        card = _make_card("skip-ok")
        client = _make_app(tmp_path, store, [card])

        r = client.post(
            "/api/v1/pmo/execute/skip-ok/skip-step",
            json={"step_id": "1.1", "reason": "Not critical"},
        )
        assert r.status_code == 200

    def test_skip_pending_step_returns_200(self, tmp_path: Path) -> None:
        """Skipping a step that has no result yet (pending) also succeeds."""
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("skip-pending")
        state = ExecutionState(plan=plan, task_id="skip-pending")
        # No step results — step 1.1 is implicitly pending.
        _write_execution_state(project_root, state)

        card = _make_card("skip-pending")
        client = _make_app(tmp_path, store, [card])

        r = client.post(
            "/api/v1/pmo/execute/skip-pending/skip-step",
            json={"step_id": "1.1", "reason": "Skip pre-emptively"},
        )
        assert r.status_code == 200

    def test_skip_response_status_is_skipped(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("skip-statusfield")
        state = ExecutionState(plan=plan, task_id="skip-statusfield")
        state.step_results = [
            StepResult(
                step_id="1.1",
                agent_name="backend-engineer--python",
                status="failed",
                error="x",
            )
        ]
        _write_execution_state(project_root, state)

        card = _make_card("skip-statusfield")
        client = _make_app(tmp_path, store, [card])

        body = client.post(
            "/api/v1/pmo/execute/skip-statusfield/skip-step",
            json={"step_id": "1.1"},
        ).json()
        assert body["status"] == "skipped"

    def test_skip_response_contains_step_id(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("skip-stepid")
        state = ExecutionState(plan=plan, task_id="skip-stepid")
        state.step_results = [
            StepResult(
                step_id="1.2",
                agent_name="frontend-engineer--react",
                status="failed",
                error="y",
            )
        ]
        _write_execution_state(project_root, state)

        card = _make_card("skip-stepid")
        client = _make_app(tmp_path, store, [card])

        body = client.post(
            "/api/v1/pmo/execute/skip-stepid/skip-step",
            json={"step_id": "1.2"},
        ).json()
        assert body["step_id"] == "1.2"

    def test_skipped_step_written_to_state_with_skipped_status(
        self, tmp_path: Path
    ) -> None:
        """The persisted state should have a StepResult with status='skipped'."""
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("skip-persisted")
        state = ExecutionState(plan=plan, task_id="skip-persisted")
        _write_execution_state(project_root, state)

        card = _make_card("skip-persisted")
        client = _make_app(tmp_path, store, [card])
        client.post(
            "/api/v1/pmo/execute/skip-persisted/skip-step",
            json={"step_id": "1.1", "reason": "Manual skip"},
        )

        exec_dir = (
            project_root
            / ".claude"
            / "team-context"
            / "executions"
            / "skip-persisted"
        )
        saved = json.loads((exec_dir / "execution-state.json").read_text())
        step_results = saved.get("step_results", [])
        skipped = [r for r in step_results if r["step_id"] == "1.1"]
        assert len(skipped) == 1
        assert skipped[0]["status"] == "skipped"

    def test_skip_reason_stored_in_outcome(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("skip-reason")
        state = ExecutionState(plan=plan, task_id="skip-reason")
        _write_execution_state(project_root, state)

        card = _make_card("skip-reason")
        client = _make_app(tmp_path, store, [card])
        client.post(
            "/api/v1/pmo/execute/skip-reason/skip-step",
            json={"step_id": "1.1", "reason": "Not needed for this release"},
        )

        exec_dir = (
            project_root
            / ".claude"
            / "team-context"
            / "executions"
            / "skip-reason"
        )
        saved = json.loads((exec_dir / "execution-state.json").read_text())
        skipped = next(
            r for r in saved["step_results"] if r["step_id"] == "1.1"
        )
        assert "Not needed for this release" in skipped.get("outcome", "")
