"""Tests for the single gate-retry mechanism (Phase D, 007).

Covers four scenarios required by the spec:

(a) BATON_GATE_RETRY=0 (default) — gate failure is terminal; status becomes
    gate_failed and a gate_failed_terminal compliance entry is written.

(b) BATON_GATE_RETRY=1 — first gate failure re-dispatches the failing step
    with gate output appended to the prompt; status returns to running.

(c) BATON_GATE_RETRY=1 — second gate failure of the same phase is terminal
    (phase already retried once).

(d) phase_retries counter is persisted inside ExecutionState so restart
    after a transient failure still treats the second gate failure as terminal.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)



# ---------------------------------------------------------------------------
# Shared builders
# ---------------------------------------------------------------------------


def _step(step_id: str = "1.1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer",
        task_description="implement the feature",
        model="sonnet",
    )


def _phase_with_gate(
    phase_id: int = 0,
    step_id: str = "1.1",
    gate_command: str = "pytest tests/ -q",
) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=f"phase-{phase_id}",
        steps=[_step(step_id)],
        gate=PlanGate(
            gate_type="test",
            command=gate_command,
            description="Tests pass.",
        ),
    )


def _plan(
    task_id: str = "task-gate-retry-test",
    phases: list[PlanPhase] | None = None,
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Gate-retry test plan",
        risk_level="LOW",
        phases=phases or [_phase_with_gate()],
    )


def _engine(tmp_path: Path) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path)


def _run_step(engine: ExecutionEngine, step_id: str, agent_name: str = "backend-engineer") -> None:
    """Simulate the step completing successfully so the engine can run the gate."""
    engine.record_step_result(
        step_id=step_id,
        agent_name=agent_name,
        status="complete",
        outcome="Done.",
        estimated_tokens=1000,
        duration_seconds=5.0,
    )


def _read_compliance_log(tmp_path: Path) -> list[dict]:
    """Read all compliance-audit.jsonl entries from the engine root."""
    log_path = tmp_path / "compliance-audit.jsonl"
    if not log_path.exists():
        return []
    entries = []
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# (a) Flag off — gate failure is terminal
# ---------------------------------------------------------------------------


class TestGateRetryFlagOff:
    """When BATON_GATE_RETRY is unset or 0, gate failure is terminal."""

    def test_gate_failure_terminal_flag_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First gate failure → status=gate_failed and gate_failed_terminal entry."""
        monkeypatch.delenv("BATON_GATE_RETRY", raising=False)

        engine = _engine(tmp_path)
        plan = _plan()
        action = engine.start(plan)

        # Drive to GATE action.
        assert action.action_type == ActionType.DISPATCH, (
            f"Expected DISPATCH; got {action.action_type}"
        )
        _run_step(engine, action.step_id, action.agent_name)
        action = engine.next_action()
        assert action.action_type == ActionType.GATE, (
            f"Expected GATE; got {action.action_type}"
        )

        # Fail the gate.
        engine.record_gate_result(
            phase_id=action.phase_id,
            passed=False,
            output="FAILED: 3 tests failed",
            command="pytest tests/ -q",
            exit_code=1,
        )

        state = engine._load_state()
        assert state is not None
        assert state.status == "gate_failed", (
            f"Expected gate_failed; got {state.status}"
        )

    def test_gate_failure_writes_terminal_compliance_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """gate_failed_terminal compliance entry is written when flag is off."""
        monkeypatch.delenv("BATON_GATE_RETRY", raising=False)

        engine = _engine(tmp_path)
        action = engine.start(_plan())

        _run_step(engine, action.step_id, action.agent_name)
        action = engine.next_action()

        engine.record_gate_result(
            phase_id=action.phase_id,
            passed=False,
            output="test failures",
            exit_code=1,
        )

        entries = _read_compliance_log(tmp_path)
        terminal_entries = [e for e in entries if e.get("event_type") == "gate_failed_terminal"]
        assert len(terminal_entries) >= 1, (
            f"Expected gate_failed_terminal compliance entry; got event_types: "
            f"{[e.get('event_type') for e in entries]}"
        )

    def test_gate_failure_flag_explicitly_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BATON_GATE_RETRY=0 is the same as unset — gate failure is terminal."""
        monkeypatch.setenv("BATON_GATE_RETRY", "0")

        engine = _engine(tmp_path)
        action = engine.start(_plan())

        _run_step(engine, action.step_id, action.agent_name)
        action = engine.next_action()

        engine.record_gate_result(
            phase_id=action.phase_id,
            passed=False,
            output="failures",
            exit_code=1,
        )

        state = engine._load_state()
        assert state is not None
        assert state.status == "gate_failed"


# ---------------------------------------------------------------------------
# (b) BATON_GATE_RETRY=1 — first failure re-dispatches with gate output
# ---------------------------------------------------------------------------


class TestGateRetryFirstFailureRedispatches:
    """When BATON_GATE_RETRY=1, first gate failure re-dispatches the failing step."""

    def test_first_gate_failure_sets_status_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After first gate failure with retry enabled, status returns to running."""
        monkeypatch.setenv("BATON_GATE_RETRY", "1")

        engine = _engine(tmp_path)
        action = engine.start(_plan())

        _run_step(engine, action.step_id, action.agent_name)
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

        engine.record_gate_result(
            phase_id=action.phase_id,
            passed=False,
            output="test_foo FAILED",
            exit_code=1,
        )

        state = engine._load_state()
        assert state is not None
        assert state.status == "running", (
            f"Expected running after gate-retry trigger; got {state.status}"
        )

    def test_first_gate_failure_produces_dispatch_action(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """next_action() after first gate failure issues a DISPATCH for the failing step."""
        monkeypatch.setenv("BATON_GATE_RETRY", "1")

        engine = _engine(tmp_path)
        plan = _plan()
        action = engine.start(plan)

        first_step_id = action.step_id
        _run_step(engine, action.step_id, action.agent_name)
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

        engine.record_gate_result(
            phase_id=action.phase_id,
            passed=False,
            output="3 tests failed",
            exit_code=1,
        )

        retry_action = engine.next_action()
        assert retry_action.action_type == ActionType.DISPATCH, (
            f"Expected DISPATCH for gate-retry; got {retry_action.action_type}"
        )
        assert retry_action.step_id == first_step_id, (
            f"Expected retry of step {first_step_id}; got {retry_action.step_id}"
        )

    def test_retry_prompt_contains_gate_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The re-dispatched step's prompt contains the gate output section."""
        monkeypatch.setenv("BATON_GATE_RETRY", "1")

        engine = _engine(tmp_path)
        action = engine.start(_plan())

        _run_step(engine, action.step_id, action.agent_name)
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

        gate_output = "FAILED: test_bar.py::test_something — AssertionError"
        engine.record_gate_result(
            phase_id=action.phase_id,
            passed=False,
            output=gate_output,
            command="pytest tests/ -q",
            exit_code=1,
        )

        retry_action = engine.next_action()
        assert retry_action.action_type == ActionType.DISPATCH
        assert "GATE OUTPUT (retry 1/1)" in retry_action.delegation_prompt, (
            f"Expected GATE OUTPUT section in prompt; got: {retry_action.delegation_prompt[:300]}"
        )
        assert gate_output in retry_action.delegation_prompt, (
            "Expected gate output text to appear in retry prompt"
        )

    def test_retry_writes_gate_retry_dispatched_compliance_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """gate_retry_dispatched compliance entry is written on first gate failure."""
        monkeypatch.setenv("BATON_GATE_RETRY", "1")

        engine = _engine(tmp_path)
        action = engine.start(_plan())

        _run_step(engine, action.step_id, action.agent_name)
        action = engine.next_action()

        engine.record_gate_result(
            phase_id=action.phase_id,
            passed=False,
            output="failures",
            exit_code=1,
        )

        entries = _read_compliance_log(tmp_path)
        retry_entries = [e for e in entries if e.get("event_type") == "gate_retry_dispatched"]
        assert len(retry_entries) >= 1, (
            f"Expected gate_retry_dispatched entry; got: {[e.get('event_type') for e in entries]}"
        )

    def test_retry_increments_phase_retries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """phase_retries is incremented to 1 after the first gate failure."""
        monkeypatch.setenv("BATON_GATE_RETRY", "1")

        engine = _engine(tmp_path)
        plan = _plan()
        action = engine.start(plan)

        _run_step(engine, action.step_id, action.agent_name)
        action = engine.next_action()

        engine.record_gate_result(
            phase_id=action.phase_id,
            passed=False,
            output="failures",
            exit_code=1,
        )

        state = engine._load_state()
        assert state is not None
        phase_key = str(action.phase_id)
        assert (state.phase_retries or {}).get(phase_key, 0) == 1, (
            f"Expected phase_retries['{phase_key}']=1; got {state.phase_retries}"
        )


# ---------------------------------------------------------------------------
# (c) Second gate failure of the same phase → terminal
# ---------------------------------------------------------------------------


class TestGateRetrySecondFailureIsTerminal:
    """When BATON_GATE_RETRY=1, second failure of the same phase is terminal."""

    def _run_to_second_gate_failure(
        self, engine: ExecutionEngine, plan: MachinePlan, gate_output: str = "still failing"
    ) -> None:
        """Helper: drive engine through first gate failure + re-dispatch + second gate failure."""
        action = engine.start(plan)

        # First dispatch.
        _run_step(engine, action.step_id, action.agent_name)
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

        # First gate failure — triggers retry.
        engine.record_gate_result(
            phase_id=action.phase_id,
            passed=False,
            output="initial failure",
            exit_code=1,
        )

        # Re-dispatch the step.
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH, (
            f"Expected DISPATCH for retry; got {action.action_type}"
        )
        _run_step(engine, action.step_id, action.agent_name)
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

        # Second gate failure — must be terminal.
        engine.record_gate_result(
            phase_id=action.phase_id,
            passed=False,
            output=gate_output,
            exit_code=1,
        )

    def test_second_gate_failure_status_is_gate_failed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Status is gate_failed after second gate failure with retry enabled."""
        monkeypatch.setenv("BATON_GATE_RETRY", "1")

        engine = _engine(tmp_path)
        plan = _plan()
        self._run_to_second_gate_failure(engine, plan)

        state = engine._load_state()
        assert state is not None
        assert state.status == "gate_failed", (
            f"Expected gate_failed after second failure; got {state.status}"
        )

    def test_second_gate_failure_writes_terminal_compliance_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """gate_failed_terminal is written on second gate failure."""
        monkeypatch.setenv("BATON_GATE_RETRY", "1")

        engine = _engine(tmp_path)
        self._run_to_second_gate_failure(engine, _plan())

        entries = _read_compliance_log(tmp_path)
        terminal_entries = [e for e in entries if e.get("event_type") == "gate_failed_terminal"]
        assert len(terminal_entries) >= 1, (
            f"Expected gate_failed_terminal on second failure; "
            f"got: {[e.get('event_type') for e in entries]}"
        )

    def test_second_gate_failure_no_third_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After second gate failure, next_action does NOT return DISPATCH."""
        monkeypatch.setenv("BATON_GATE_RETRY", "1")

        engine = _engine(tmp_path)
        self._run_to_second_gate_failure(engine, _plan())

        action = engine.next_action()
        # Should be FAILED, not DISPATCH.
        assert action.action_type != ActionType.DISPATCH, (
            f"Expected non-DISPATCH after second gate failure; got {action.action_type}"
        )


# ---------------------------------------------------------------------------
# (d) phase_retries is persisted
# ---------------------------------------------------------------------------


class TestGateRetryPhaseRetriesPersisted:
    """phase_retries is written to ExecutionState and survives serialization."""

    def test_phase_retries_roundtrip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """phase_retries value roundtrips through to_dict / from_dict."""
        monkeypatch.setenv("BATON_GATE_RETRY", "1")

        engine = _engine(tmp_path)
        plan = _plan()
        action = engine.start(plan)

        _run_step(engine, action.step_id, action.agent_name)
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

        engine.record_gate_result(
            phase_id=action.phase_id,
            passed=False,
            output="failure",
            exit_code=1,
        )

        # Load and round-trip via to_dict / from_dict.
        state = engine._load_state()
        assert state is not None

        from agent_baton.models.execution import ExecutionState

        state_dict = state.to_dict()
        assert state_dict.get("phase_retries"), (
            "phase_retries should be present in serialised state"
        )

        restored = ExecutionState.from_dict(state_dict)
        phase_key = str(action.phase_id)
        assert restored.phase_retries.get(phase_key, 0) == 1, (
            f"phase_retries not preserved; got {restored.phase_retries}"
        )

    def test_second_failure_terminal_after_restore(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a restart (new engine, same tmp_path), second gate failure is
        treated as terminal because phase_retries is read from persisted state."""
        monkeypatch.setenv("BATON_GATE_RETRY", "1")

        # --- First engine instance: first gate failure triggers retry ---
        engine1 = _engine(tmp_path)
        plan = _plan(task_id="task-persist-retry")
        action = engine1.start(plan)

        _run_step(engine1, action.step_id, action.agent_name)
        action = engine1.next_action()
        assert action.action_type == ActionType.GATE

        engine1.record_gate_result(
            phase_id=action.phase_id,
            passed=False,
            output="initial failure",
            exit_code=1,
        )

        # Retry dispatch is pending — simulate a restart here by constructing a
        # new engine that loads the saved state (file-only mode; no task_id so
        # the flat execution-state.json path is used, same as engine1).
        engine2 = ExecutionEngine(team_context_root=tmp_path)
        retry_action = engine2.next_action()
        assert retry_action.action_type == ActionType.DISPATCH, (
            f"Expected DISPATCH for retry after restart; got {retry_action.action_type}"
        )

        _run_step(engine2, retry_action.step_id, retry_action.agent_name)
        gate_action = engine2.next_action()
        assert gate_action.action_type == ActionType.GATE

        # Second failure on the new engine — must be terminal.
        engine2.record_gate_result(
            phase_id=gate_action.phase_id,
            passed=False,
            output="still failing",
            exit_code=1,
        )

        state = engine2._load_state()
        assert state is not None
        assert state.status == "gate_failed", (
            f"Expected gate_failed after second failure on resumed engine; got {state.status}"
        )
