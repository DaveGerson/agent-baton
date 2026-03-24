"""Tests for agent_baton.core.engine.executor.ExecutionEngine."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.models.execution import (
    ActionType,
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)
from agent_baton.core.engine.executor import ExecutionEngine


# ---------------------------------------------------------------------------
# Plan / phase / step factories
# ---------------------------------------------------------------------------

def _step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task: str = "Implement feature X",
    model: str = "sonnet",
    deliverables: list[str] | None = None,
    allowed_paths: list[str] | None = None,
    context_files: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        model=model,
        deliverables=deliverables or [],
        allowed_paths=allowed_paths or [],
        context_files=context_files or [],
    )


def _gate(gate_type: str = "test", command: str = "pytest") -> PlanGate:
    return PlanGate(gate_type=gate_type, command=command)


def _phase(
    phase_id: int = 0,
    name: str = "Implementation",
    steps: list[PlanStep] | None = None,
    gate: PlanGate | None = None,
) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=name,
        steps=steps or [_step()],
        gate=gate,
    )


def _plan(
    task_id: str = "task-001",
    task_summary: str = "Build a thing",
    phases: list[PlanPhase] | None = None,
    risk_level: str = "LOW",
    shared_context: str = "",
) -> MachinePlan:
    if phases is None:
        phases = [_phase()]
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        risk_level=risk_level,
        phases=phases,
        shared_context=shared_context,
    )


def _engine(tmp_path: Path) -> ExecutionEngine:
    """Return a fresh engine backed by *tmp_path*."""
    return ExecutionEngine(team_context_root=tmp_path)


# ---------------------------------------------------------------------------
# start() — basic contract
# ---------------------------------------------------------------------------

class TestStart:
    # DECISION: test_returns_execution_action removed — trivial isinstance check.
    # DECISION: test_state_file_created / test_state_file_is_valid_json removed —
    #   covered by TestStartExecution in test_engine_integration.py.
    # DECISION: three dispatch-field tests collapsed into one parametrized test.

    def test_returns_dispatch_for_first_step(self, tmp_path: Path) -> None:
        action = _engine(tmp_path).start(_plan())
        assert action.action_type == ActionType.DISPATCH

    @pytest.mark.parametrize("field,step_kw,expected", [
        ("agent_name", {"agent_name": "architect"}, "architect"),
        ("step_id",    {"step_id": "1.1"},           "1.1"),
        ("agent_model", {"model": "opus"},            "opus"),
    ])
    def test_dispatch_carries_field(
        self, tmp_path: Path, field: str, step_kw: dict, expected: str
    ) -> None:
        plan = _plan(phases=[_phase(steps=[_step(**step_kw)])])
        action = _engine(tmp_path).start(plan)
        assert getattr(action, field) == expected

    def test_state_status_is_running(self, tmp_path: Path) -> None:
        _engine(tmp_path).start(_plan())
        data = json.loads((tmp_path / "execution-state.json").read_text())
        assert data["status"] == "running"

    def test_plan_with_no_phases_returns_complete(self, tmp_path: Path) -> None:
        plan = _plan(phases=[])
        action = _engine(tmp_path).start(plan)
        assert action.action_type == ActionType.COMPLETE

    def test_delegation_prompt_contains_task(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step(task="Do something important")])])
        action = _engine(tmp_path).start(plan)
        assert "Do something important" in action.delegation_prompt

    def test_delegation_prompt_contains_shared_context(self, tmp_path: Path) -> None:
        plan = _plan(
            shared_context="Important context here",
            phases=[_phase(steps=[_step()])],
        )
        action = _engine(tmp_path).start(plan)
        assert "Important context here" in action.delegation_prompt

    def test_delegation_prompt_contains_context_files(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step(context_files=["src/main.py"])])])
        action = _engine(tmp_path).start(plan)
        assert "src/main.py" in action.delegation_prompt

    def test_delegation_prompt_mentions_claude_md(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step()])])
        action = _engine(tmp_path).start(plan)
        assert "CLAUDE.md" in action.delegation_prompt


# ---------------------------------------------------------------------------
# record_step_result() — state mutation
# ---------------------------------------------------------------------------

class TestRecordStepResult:
    # DECISION: five field-storage tests (outcome, files_changed, tokens, duration,
    #   commit_hash) share identical setup — folded into one parametrized test.
    #   The shared @pytest.fixture for engine+start is inlined via a helper method
    #   to stay within the class (pytest class fixtures require specific scoping).

    @staticmethod
    def _started_engine(tmp_path: Path) -> ExecutionEngine:
        engine = _engine(tmp_path)
        engine.start(_plan())
        return engine

    def test_step_appears_in_completed_ids(self, tmp_path: Path) -> None:
        engine = self._started_engine(tmp_path)
        engine.record_step_result("1.1", "backend-engineer", status="complete")
        assert "1.1" in engine._load_state().completed_step_ids

    def test_failed_step_appears_in_failed_ids(self, tmp_path: Path) -> None:
        engine = self._started_engine(tmp_path)
        engine.record_step_result("1.1", "backend-engineer", status="failed", error="oops")
        assert "1.1" in engine._load_state().failed_step_ids

    @pytest.mark.parametrize("record_kw,attr,expected", [
        ({"outcome": "Feature X done"},           "outcome",           "Feature X done"),
        ({"files_changed": ["a.py", "b.py"]},     "files_changed",     ["a.py", "b.py"]),
        ({"estimated_tokens": 5000},               "estimated_tokens",  5000),
        ({"duration_seconds": 42.5},               "duration_seconds",  42.5),
        ({"commit_hash": "abc123"},                "commit_hash",       "abc123"),
    ])
    def test_step_result_stores_field(
        self, tmp_path: Path, record_kw: dict, attr: str, expected
    ) -> None:
        engine = self._started_engine(tmp_path)
        engine.record_step_result("1.1", "backend-engineer", **record_kw)
        result = engine._load_state().get_step_result("1.1")
        assert result is not None
        assert getattr(result, attr) == expected

    def test_state_persisted_to_disk(self, tmp_path: Path) -> None:
        engine = self._started_engine(tmp_path)
        engine.record_step_result("1.1", "backend-engineer")
        data = json.loads((tmp_path / "execution-state.json").read_text())
        assert len(data["step_results"]) == 1

    def test_raises_without_active_state(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with pytest.raises(RuntimeError):
            engine.record_step_result("1.1", "agent")


# ---------------------------------------------------------------------------
# next_action() — state machine progression
# ---------------------------------------------------------------------------

class TestNextAction:
    def test_returns_dispatch_before_first_step_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH

    def test_returns_complete_after_last_step(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

    def test_returns_gate_after_all_steps_complete_in_phase(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate("test", "pytest"))])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

    def test_gate_action_carries_gate_type(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate("lint", "ruff"))])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.gate_type == "lint"

    def test_gate_action_carries_command(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate("build", "make build"))])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.gate_command == "make build"

    def test_gate_action_carries_phase_id(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")], gate=_gate())])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.phase_id == 0

    def test_dispatch_second_step_in_phase(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[_phase(steps=[_step("1.1"), _step("1.2", agent_name="architect")])]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.2"

    def test_returns_failed_when_step_failed(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", status="failed")
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_returns_no_state_error_without_start(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_complete_action_when_already_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # returns COMPLETE, does not mark complete
        # Manually mark complete
        state = engine._load_state()
        state.status = "complete"
        engine._save_state(state)
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE


# ---------------------------------------------------------------------------
# record_gate_result() — gate pass / fail behaviour
# ---------------------------------------------------------------------------

class TestRecordGateResult:
    # DECISION: shared helper _gate_engine builds a two-phase plan with gate on phase 0
    #   to eliminate repeated plan construction boilerplate.

    @staticmethod
    def _gate_engine(tmp_path: Path, two_phase: bool = False) -> ExecutionEngine:
        """Return an engine that has started and completed step 1.1 in a gated phase."""
        if two_phase:
            plan = _plan(phases=[
                _phase(phase_id=0, steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=1, name="Phase 2", steps=[_step("2.1")]),
            ])
        else:
            plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")], gate=_gate())])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        return engine

    def test_passed_gate_advances_phase(self, tmp_path: Path) -> None:
        engine = self._gate_engine(tmp_path, two_phase=True)
        engine.next_action()  # returns GATE
        engine.record_gate_result(phase_id=0, passed=True)
        state = engine._load_state()
        assert state.current_phase == 1
        assert state.status == "running"

    def test_failed_gate_sets_status_failed(self, tmp_path: Path) -> None:
        engine = self._gate_engine(tmp_path)
        engine.next_action()  # returns GATE
        engine.record_gate_result(phase_id=0, passed=False, output="tests failed")
        assert engine._load_state().status == "failed"

    def test_gate_result_stored(self, tmp_path: Path) -> None:
        engine = self._gate_engine(tmp_path)
        engine.record_gate_result(phase_id=0, passed=True, output="all green")
        state = engine._load_state()
        assert len(state.gate_results) == 1
        assert state.gate_results[0].passed is True
        assert state.gate_results[0].output == "all green"

    def test_failed_gate_followed_by_failed_action(self, tmp_path: Path) -> None:
        engine = self._gate_engine(tmp_path)
        engine.record_gate_result(phase_id=0, passed=False)
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_raises_without_active_state(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        with pytest.raises(RuntimeError):
            engine.record_gate_result(phase_id=0, passed=True)


# ---------------------------------------------------------------------------
# Phase transitions — multi-phase plan
# ---------------------------------------------------------------------------

class TestPhaseTransitions:
    def _two_phase_plan(self) -> MachinePlan:
        return _plan(
            phases=[
                _phase(phase_id=0, name="Phase 1", steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=1, name="Phase 2", steps=[_step("2.1", agent_name="architect")]),
            ]
        )

    def test_dispatches_phase1_step_first(self, tmp_path: Path) -> None:
        action = _engine(tmp_path).start(self._two_phase_plan())
        assert action.step_id == "1.1"

    def test_gate_triggered_after_phase1_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(self._two_phase_plan())
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

    def test_dispatches_phase2_step_after_gate_passes(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(self._two_phase_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # GATE
        engine.record_gate_result(phase_id=0, passed=True)
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "2.1"

    def test_complete_after_phase2_step_done(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(self._two_phase_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        engine.record_gate_result(phase_id=0, passed=True)
        engine.next_action()
        engine.record_step_result("2.1", "architect")
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

    def test_phase_pointer_incremented_after_gate(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(self._two_phase_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.record_gate_result(phase_id=0, passed=True)
        state = engine._load_state()
        assert state.current_phase == 1


# ---------------------------------------------------------------------------
# complete() — finalisation artefacts
# ---------------------------------------------------------------------------

class TestComplete:
    def test_returns_string(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # COMPLETE action (state not yet marked)
        summary = engine.complete()
        assert isinstance(summary, str)

    def test_summary_contains_task_id(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(task_id="my-task"))
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        summary = engine.complete()
        assert "my-task" in summary

    def test_state_status_set_to_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        engine.complete()
        state = engine._load_state()
        assert state.status == "complete"

    def test_retrospective_written(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(task_id="retro-test"))
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        engine.complete()
        retro_dir = tmp_path / "retrospectives"
        assert retro_dir.exists()
        retro_files = list(retro_dir.glob("*.md"))
        assert len(retro_files) == 1

    def test_summary_mentions_steps_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()
        summary = engine.complete()
        assert "Steps" in summary or "1" in summary

    def test_no_state_returns_gracefully(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        result = engine.complete()
        assert isinstance(result, str)
        assert "No active" in result


# ---------------------------------------------------------------------------
# status() — snapshot dict
# ---------------------------------------------------------------------------

class TestStatus:
    # DECISION: test_steps_complete_initially_zero removed — trivial default-value check.
    # DECISION: test_current_phase_key_present + test_elapsed_seconds_present_and_non_negative
    #   folded into one parametrized key-presence test; the non-negative constraint for
    #   elapsed_seconds is preserved as a separate substantive check.

    def test_returns_dict(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        s = engine.status()
        assert isinstance(s, dict)

    def test_task_id_present(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(task_id="status-task"))
        s = engine.status()
        assert s["task_id"] == "status-task"

    def test_status_running_on_start(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        s = engine.status()
        assert s["status"] == "running"

    @pytest.mark.parametrize("key", ["current_phase", "elapsed_seconds"])
    def test_numeric_keys_present(self, tmp_path: Path, key: str) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        assert key in engine.status()

    def test_elapsed_seconds_non_negative(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        assert engine.status()["elapsed_seconds"] >= 0.0

    def test_steps_complete_increments(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        assert engine.status()["steps_complete"] == 1

    def test_steps_total_matches_plan(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1"), _step("1.2")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        assert engine.status()["steps_total"] == 2

    def test_gates_passed_increments(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate())])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.record_gate_result(phase_id=0, passed=True)
        assert engine.status()["gates_passed"] == 1

    def test_gates_failed_increments(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate())])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.record_gate_result(phase_id=0, passed=False)
        assert engine.status()["gates_failed"] == 1

    def test_no_state_returns_indicator(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        s = engine.status()
        assert s["status"] == "no_active_execution"


# ---------------------------------------------------------------------------
# resume() — crash recovery
# ---------------------------------------------------------------------------

class TestResume:
    # DECISION: test_resume_returns_action removed — trivial isinstance check;
    #   crash-recovery behaviour is fully covered by TestCrashRecovery in
    #   test_engine_integration.py.

    def test_resume_returns_dispatch_if_step_pending(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        # Crash before recording step result.
        resumed = _engine(tmp_path)
        action = resumed.resume()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.1"

    def test_resume_returns_correct_action_after_partial_progress(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[_phase(steps=[_step("1.1"), _step("1.2", agent_name="architect")])]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        # Crash after step 1.1.
        resumed = _engine(tmp_path)
        action = resumed.resume()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.2"

    def test_resume_returns_gate_if_pending(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate())])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        # Manually set gate_pending to simulate crash mid-gate.
        state = engine._load_state()
        state.status = "gate_pending"
        engine._save_state(state)
        resumed = _engine(tmp_path)
        action = resumed.resume()
        assert action.action_type == ActionType.GATE

    def test_resume_returns_failed_on_no_state(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        action = engine.resume()
        assert action.action_type == ActionType.FAILED

    def test_resume_picks_up_completed_steps(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1"), _step("1.2")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        resumed = _engine(tmp_path)
        action = resumed.resume()
        # 1.1 complete, so next should be 1.2
        assert action.step_id == "1.2"


# ---------------------------------------------------------------------------
# End-to-end: multi-phase plan
# ---------------------------------------------------------------------------

class TestMultiPhaseEndToEnd:
    def test_two_phase_two_step_with_gate_completes(self, tmp_path: Path) -> None:
        """Full happy-path: 2 phases, 1 step each, gate after phase 0."""
        plan = _plan(
            task_id="e2e-task",
            phases=[
                _phase(phase_id=0, steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=1, steps=[_step("2.1")]),
            ],
        )
        engine = _engine(tmp_path)

        # Phase 0 — step 1.1
        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.1"

        engine.record_step_result("1.1", "backend-engineer", estimated_tokens=2000)

        # Gate for phase 0
        action = engine.next_action()
        assert action.action_type == ActionType.GATE
        engine.record_gate_result(phase_id=0, passed=True)

        # Phase 1 — step 2.1
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "2.1"

        engine.record_step_result("2.1", "backend-engineer", estimated_tokens=1500)

        # Complete
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

        summary = engine.complete()
        assert "e2e-task" in summary

    def test_three_phases_no_gates_completes(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[
                _phase(phase_id=0, steps=[_step("1.1")]),
                _phase(phase_id=1, steps=[_step("2.1")]),
                _phase(phase_id=2, steps=[_step("3.1")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        for step_id in ("1.1", "2.1", "3.1"):
            engine.record_step_result(step_id, "backend-engineer")
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

    def test_gate_failure_halts_execution(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[
                _phase(phase_id=0, steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=1, steps=[_step("2.1")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.next_action()  # GATE
        engine.record_gate_result(phase_id=0, passed=False)
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_step_failure_in_phase2_halts_execution(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[
                _phase(phase_id=0, steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=1, steps=[_step("2.1")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.record_gate_result(phase_id=0, passed=True)
        engine.next_action()  # DISPATCH 2.1
        engine.record_step_result("2.1", "backend-engineer", status="failed")
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED


# ---------------------------------------------------------------------------
# Failed step handling
# ---------------------------------------------------------------------------

class TestFailedStepHandling:
    def test_failed_step_triggers_failed_action(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", status="failed", error="import error")
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_failed_action_message_contains_step_id(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", status="failed")
        action = engine.next_action()
        assert "1.1" in action.message or "1.1" in action.summary

    def test_status_reflects_failure(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", status="failed")
        engine.next_action()  # triggers failed state update
        s = engine.status()
        assert s["status"] == "failed"


# ---------------------------------------------------------------------------
# _build_usage_record — unit tests for the conversion helper
# ---------------------------------------------------------------------------

class TestBuildUsageRecord:
    def test_task_id_matches(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(task_id="usage-test"))
        engine.record_step_result("1.1", "backend-engineer", estimated_tokens=500)
        state = engine._load_state()
        state.status = "complete"
        state.completed_at = "2026-03-20T10:00:00+00:00"
        record = engine._build_usage_record(state)
        assert record.task_id == "usage-test"

    def test_agents_used_populated(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", estimated_tokens=1000)
        state = engine._load_state()
        record = engine._build_usage_record(state)
        assert len(record.agents_used) == 1
        assert record.agents_used[0].name == "backend-engineer"

    def test_tokens_aggregated_per_agent(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1"), _step("1.2")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", estimated_tokens=1000)
        engine.record_step_result("1.2", "backend-engineer", estimated_tokens=2000)
        state = engine._load_state()
        record = engine._build_usage_record(state)
        assert record.agents_used[0].estimated_tokens == 3000

    def test_outcome_ship_when_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        state = engine._load_state()
        state.status = "complete"
        record = engine._build_usage_record(state)
        assert record.outcome == "SHIP"

    def test_outcome_block_when_failed(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", status="failed")
        state = engine._load_state()
        state.status = "failed"
        record = engine._build_usage_record(state)
        assert record.outcome == "BLOCK"

    def test_risk_level_propagated(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(risk_level="HIGH"))
        state = engine._load_state()
        record = engine._build_usage_record(state)
        assert record.risk_level == "HIGH"

    def test_gates_counted(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate())])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.record_gate_result(phase_id=0, passed=True)
        state = engine._load_state()
        record = engine._build_usage_record(state)
        assert record.gates_passed == 1
        assert record.gates_failed == 0


# ---------------------------------------------------------------------------
# _save_state / _load_state round-trip
# ---------------------------------------------------------------------------

class TestStatePersistence:
    def test_save_then_load_roundtrip(self, tmp_path: Path) -> None:
        plan = _plan(task_id="persist-test")
        state = ExecutionState(task_id="persist-test", plan=plan)
        engine = _engine(tmp_path)
        engine._save_state(state)
        loaded = engine._load_state()
        assert loaded is not None
        assert loaded.task_id == "persist-test"

    def test_load_returns_none_when_no_file(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        assert engine._load_state() is None

    def test_load_returns_none_on_corrupted_json(self, tmp_path: Path) -> None:
        (tmp_path / "execution-state.json").write_text("NOT JSON", encoding="utf-8")
        engine = _engine(tmp_path)
        assert engine._load_state() is None

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        deep_root = tmp_path / "deep" / "context"
        plan = _plan()
        state = ExecutionState(task_id="deep-test", plan=plan)
        engine = ExecutionEngine(team_context_root=deep_root)
        engine._save_state(state)
        assert (deep_root / "execution-state.json").exists()

    def test_step_results_persist_through_disk(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer", outcome="done", commit_hash="aabbcc")
        loaded = engine._load_state()
        assert len(loaded.step_results) == 1
        assert loaded.step_results[0].commit_hash == "aabbcc"

    def test_gate_results_persist_through_disk(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")], gate=_gate())])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.record_gate_result(phase_id=0, passed=True, output="green")
        loaded = engine._load_state()
        assert len(loaded.gate_results) == 1
        assert loaded.gate_results[0].passed is True


# ---------------------------------------------------------------------------
# Phase-advance regression (1-based phase_ids, matching planner output)
# ---------------------------------------------------------------------------

class TestPhaseAdvanceWithOneBasedIds:
    """Regression tests for the gate phase-advance bug.

    The planner creates phases with 1-based phase_ids (1, 2, 3, ...) while
    current_phase is a 0-based index.  The old buggy code used
    ``state.current_phase = phase_id + 1`` which skipped phases when IDs
    were 1-based.
    """

    def test_three_phases_two_gates_no_skip(self, tmp_path: Path) -> None:
        """A→gate→B→gate→C must dispatch all three phases, not skip B."""
        plan = _plan(
            phases=[
                _phase(phase_id=1, name="Design", steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=2, name="Implement", steps=[_step("2.1")], gate=_gate()),
                _phase(phase_id=3, name="Review", steps=[_step("3.1")]),
            ]
        )
        engine = _engine(tmp_path)
        dispatched: list[str] = []

        action = engine.start(plan)
        for _ in range(30):
            if action.action_type == ActionType.COMPLETE:
                break
            if action.action_type == ActionType.FAILED:
                break
            if action.action_type == ActionType.DISPATCH:
                dispatched.append(action.step_id)
                engine.record_step_result(action.step_id, action.agent_name)
            elif action.action_type == ActionType.GATE:
                engine.record_gate_result(action.phase_id, passed=True)
            action = engine.next_action()

        assert dispatched == ["1.1", "2.1", "3.1"], (
            f"Expected all 3 phases dispatched in order, got {dispatched}"
        )

    def test_gate_on_phase1_reaches_phase2(self, tmp_path: Path) -> None:
        """Gate on 1-based phase_id=1 must advance to phase at index 1."""
        plan = _plan(
            phases=[
                _phase(phase_id=1, steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=2, steps=[_step("2.1")]),
            ]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()  # GATE
        assert action.action_type == ActionType.GATE

        engine.record_gate_result(action.phase_id, passed=True)
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "2.1"

    def test_four_phases_all_gated_dispatches_all(self, tmp_path: Path) -> None:
        """4 phases each with a gate — every phase must be visited."""
        plan = _plan(
            phases=[
                _phase(phase_id=1, name="A", steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=2, name="B", steps=[_step("2.1")], gate=_gate()),
                _phase(phase_id=3, name="C", steps=[_step("3.1")], gate=_gate()),
                _phase(phase_id=4, name="D", steps=[_step("4.1")]),
            ]
        )
        engine = _engine(tmp_path)
        dispatched = []
        gates_run = []

        action = engine.start(plan)
        for _ in range(40):
            if action.action_type in (ActionType.COMPLETE, ActionType.FAILED):
                break
            if action.action_type == ActionType.DISPATCH:
                dispatched.append(action.step_id)
                engine.record_step_result(action.step_id, action.agent_name)
            elif action.action_type == ActionType.GATE:
                gates_run.append(action.phase_id)
                engine.record_gate_result(action.phase_id, passed=True)
            action = engine.next_action()

        assert dispatched == ["1.1", "2.1", "3.1", "4.1"]
        assert len(gates_run) == 3


# ---------------------------------------------------------------------------
# Status validation
# ---------------------------------------------------------------------------

class TestStepStatusValidation:
    """record_step_result must reject invalid status values."""

    def test_invalid_status_raises_value_error(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        with pytest.raises(ValueError, match="Invalid step status 'success'"):
            engine.record_step_result("1.1", "backend-engineer", status="success")

    def test_valid_statuses_accepted(self, tmp_path: Path) -> None:
        for status in ("complete", "failed"):
            plan = _plan(phases=[_phase(phase_id=0, steps=[_step("1.1")])])
            engine = _engine(tmp_path)
            engine.start(plan)
            engine.record_step_result("1.1", "backend-engineer", status=status)


# ---------------------------------------------------------------------------
# Parallel step dispatch
# ---------------------------------------------------------------------------

class TestParallelDispatch:
    """Tests for dependency-aware parallel step dispatch."""

    def test_independent_steps_dispatch_in_order(self, tmp_path: Path) -> None:
        """Steps with no depends_on dispatch in phase order."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            _step("1.2", agent_name="b"),
        ])])
        engine = _engine(tmp_path)
        action = engine.start(plan)
        assert action.step_id == "1.1"

    def test_dispatched_step_not_redispatched(self, tmp_path: Path) -> None:
        """A step marked as dispatched should not be returned again."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            _step("1.2", agent_name="b"),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.mark_dispatched("1.1", "a")
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.2"

    def test_blocked_step_returns_wait(self, tmp_path: Path) -> None:
        """Step blocked by dependency returns WAIT when all runnable are dispatched."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            PlanStep(step_id="1.2", agent_name="b",
                     task_description="depends on 1.1", depends_on=["1.1"]),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.mark_dispatched("1.1", "a")
        action = engine.next_action()
        assert action.action_type == ActionType.WAIT

    def test_dependency_satisfied_enables_dispatch(self, tmp_path: Path) -> None:
        """Once dependency completes, blocked step becomes dispatchable."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            PlanStep(step_id="1.2", agent_name="b",
                     task_description="depends on 1.1", depends_on=["1.1"]),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "a")
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.2"

    def test_next_actions_returns_all_runnable(self, tmp_path: Path) -> None:
        """next_actions() returns all steps with satisfied dependencies."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            _step("1.2", agent_name="b"),
            PlanStep(step_id="1.3", agent_name="c",
                     task_description="depends on both", depends_on=["1.1", "1.2"]),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        actions = engine.next_actions()
        step_ids = {a.step_id for a in actions}
        assert step_ids == {"1.1", "1.2"}
        assert "1.3" not in step_ids

    def test_next_actions_empty_when_all_dispatched(self, tmp_path: Path) -> None:
        """next_actions() returns empty when all runnable steps are dispatched."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1"), _step("1.2"),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")
        engine.mark_dispatched("1.2", "backend-engineer")
        actions = engine.next_actions()
        assert actions == []

    def test_next_actions_unlocks_after_completion(self, tmp_path: Path) -> None:
        """Completing a dependency unlocks blocked steps in next_actions()."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            PlanStep(step_id="1.2", agent_name="b",
                     task_description="dep", depends_on=["1.1"]),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "a")
        actions = engine.next_actions()
        assert len(actions) == 1
        assert actions[0].step_id == "1.2"

    def test_dispatched_then_completed_advances(self, tmp_path: Path) -> None:
        """Full parallel flow: dispatch both -> complete both -> gate/complete."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1", agent_name="a"),
            _step("1.2", agent_name="b"),
        ])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.mark_dispatched("1.1", "a")
        engine.mark_dispatched("1.2", "b")
        # Both in flight -- should WAIT
        action = engine.next_action()
        assert action.action_type == ActionType.WAIT
        # Complete both
        engine.record_step_result("1.1", "a")
        engine.record_step_result("1.2", "b")
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

    def test_mark_dispatched_is_valid_status(self, tmp_path: Path) -> None:
        """mark_dispatched uses 'dispatched' status which is accepted."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")
        state = engine._load_state()
        assert "1.1" in state.dispatched_step_ids

    def test_backward_compat_no_depends_on(self, tmp_path: Path) -> None:
        """Plans without depends_on work exactly as before (sequential dispatch)."""
        plan = _plan(phases=[_phase(phase_id=1, steps=[
            _step("1.1"), _step("1.2"), _step("1.3"),
        ])])
        engine = _engine(tmp_path)
        dispatched = []
        action = engine.start(plan)
        for _ in range(10):
            if action.action_type == ActionType.COMPLETE:
                break
            if action.action_type == ActionType.DISPATCH:
                dispatched.append(action.step_id)
                engine.record_step_result(action.step_id, action.agent_name)
            action = engine.next_action()
        assert dispatched == ["1.1", "1.2", "1.3"]
