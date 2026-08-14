"""RW-2.4 — ``baton execute gate`` must reject cleanly, not traceback.

Since the F002 pre-conditions landed, ``ExecutionEngine.record_gate_result``
raises :class:`~agent_baton.core.engine.errors.InvalidGateState` for a gate
recorded against a phase that does not exist, is not current, or whose steps
have not run.  Nothing in ``baton execute gate`` catches it, so the operator
who mistypes ``--phase-id`` gets a raw Python traceback and an exit status
that depends on the interpreter rather than the CLI's error contract.

The CLI already does this correctly for ``UnknownTeamBackendError`` (mapped
to ``user_error`` at the ``handler`` boundary).  These tests pin the same
contract for ``InvalidGateState``:

* exit non-zero via ``SystemExit`` (``user_error``), never a bare traceback,
* the message reaches stderr in the standard ``error: ...`` shape,
* the rejected recording still leaves execution state untouched.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_baton.cli.commands.execution import execute as _mod
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)

_EXECUTE_MOD = "agent_baton.cli.commands.execution.execute"


class _FakeStorage:
    """Minimal storage shim — file persistence does the real work."""

    def __init__(self) -> None:
        self._active: str | None = None

    def get_active_task(self) -> str | None:
        return self._active

    def set_active_task(self, task_id: str) -> None:
        self._active = task_id

    def load_execution(self, task_id: str):  # noqa: ARG002
        return None

    def save_execution(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None


def _plan(task_id: str) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="CLI gate rejection fixture",
        risk_level="LOW",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Build it",
                        model="sonnet",
                    ),
                ],
                gate=PlanGate(gate_type="test", command="pytest -q"),
            ),
            PlanPhase(
                phase_id=2,
                name="Review",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="code-reviewer",
                        task_description="Review it",
                        model="sonnet",
                    ),
                ],
                gate=PlanGate(gate_type="review", command="baton review"),
            ),
        ],
    )


def _seed(context_root: Path, task_id: str) -> ExecutionState:
    """Phase 1 is current and its lone step is still un-run."""
    plan = _plan(task_id)
    state = ExecutionState(
        task_id=task_id, plan=plan, current_phase=0, status="running"
    )
    StatePersistence(context_root, task_id=task_id).save(state)
    return state


def _seed_phase_one_done(context_root: Path, task_id: str) -> ExecutionState:
    plan = _plan(task_id)
    state = ExecutionState(
        task_id=task_id, plan=plan, current_phase=0, status="running"
    )
    state.step_results.append(
        StepResult(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome="done",
        )
    )
    StatePersistence(context_root, task_id=task_id).save(state)
    return state


def _gate_args(task_id: str, phase_id: int, result: str = "pass") -> argparse.Namespace:
    return argparse.Namespace(
        subcommand="gate",
        task_id=task_id,
        phase_id=phase_id,
        result=result,
        gate_output="",
        gate_type_override=None,
        ci_workflow="",
        ci_timeout_s=None,
        output="text",
        force_override=False,
        override_justification="",
    )


def _patches(tmp_path: Path):
    storage = _FakeStorage()
    factory = lambda **kwargs: ExecutionEngine(  # noqa: E731
        team_context_root=tmp_path,
        bus=kwargs.get("bus"),
        task_id=kwargs.get("task_id"),
        storage=kwargs.get("storage"),
    )
    return [
        patch(f"{_EXECUTE_MOD}.get_project_storage", return_value=storage),
        patch(f"{_EXECUTE_MOD}.ExecutionEngine", side_effect=factory),
        patch(f"{_EXECUTE_MOD}.ContextManager"),
        patch(
            "agent_baton.core.storage.sync.auto_sync_current_project",
            return_value=None,
        ),
        patch(f"{_EXECUTE_MOD}.detect_backend", return_value="file"),
        patch(f"{_EXECUTE_MOD}._resolve_context_root", return_value=tmp_path),
    ]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BATON_TASK_ID", raising=False)


class TestGateRejectionIsACleanCliError:
    @pytest.mark.parametrize(
        ("label", "phase_id", "seeder"),
        [
            ("unknown_phase", 99, _seed),
            ("phase_mismatch", 2, _seed),
            ("steps_incomplete", 1, _seed),
        ],
    )
    def test_rejected_gate_exits_cleanly(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture,
        label: str,
        phase_id: int,
        seeder,
    ) -> None:
        """A mistyped ``--phase-id`` is an operator error, not a crash."""
        task_id = f"cli-gate-{label}"
        seeder(tmp_path, task_id)

        p = _patches(tmp_path)
        with p[0], p[1], p[2], p[3], p[4], p[5], pytest.raises(SystemExit) as exc_info:
            _mod.handler(_gate_args(task_id, phase_id))

        assert exc_info.value.code not in (0, None), (
            f"a rejected gate ({label}) must exit non-zero"
        )
        captured = capsys.readouterr()
        assert "error:" in captured.err, (
            "the rejection must be reported through the CLI's standard error "
            f"channel; stderr was {captured.err!r}"
        )
        assert "Traceback" not in (captured.err + captured.out)

    def test_rejected_gate_does_not_mutate_state(
        self, tmp_path: Path
    ) -> None:
        """The state file must be exactly as it was before the bad command."""
        task_id = "cli-gate-nomutate"
        _seed(tmp_path, task_id)
        sp = StatePersistence(tmp_path, task_id=task_id)
        before = sp.load()
        assert before is not None

        p = _patches(tmp_path)
        with p[0], p[1], p[2], p[3], p[4], p[5], pytest.raises(SystemExit):
            _mod.handler(_gate_args(task_id, 2))

        after = sp.load()
        assert after is not None
        assert after.current_phase == before.current_phase
        assert after.status == before.status
        assert after.gate_results == []

    def test_valid_gate_recording_still_succeeds(self, tmp_path: Path) -> None:
        """The handler must not swallow or reject legitimate recordings."""
        task_id = "cli-gate-happy"
        _seed_phase_one_done(tmp_path, task_id)

        p = _patches(tmp_path)
        with p[0], p[1], p[2], p[3], p[4], p[5]:
            _mod.handler(_gate_args(task_id, 1))

        after = StatePersistence(tmp_path, task_id=task_id).load()
        assert after is not None
        assert [(g.phase_id, g.passed) for g in after.gate_results] == [(1, True)]
        assert after.current_phase == 1
