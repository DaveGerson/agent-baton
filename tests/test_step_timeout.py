"""Tests for per-step timeout enforcement (bd-7312).

Covers:
- Default timeout_seconds=0 means no enforcement.
- Explicit timeout=0 is unlimited.
- Timeout marks step failed when elapsed > timeout.
- BATON_DEFAULT_STEP_TIMEOUT_S env var provides a fallback default.
- Explicit step timeout overrides the env var.
- Timeout fires a warning bead via BeadStore.
- PlanStep.to_dict() omits timeout_seconds when 0.
- PlanStep.from_dict() defaults timeout_seconds to 0.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
)
from agent_baton.core.engine.executor import ExecutionEngine


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task: str = "Do something",
    timeout_seconds: int = 0,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        timeout_seconds=timeout_seconds,
    )


def _plan(steps: list[PlanStep], task_id: str = "task-timeout-test") -> MachinePlan:
    phase = PlanPhase(phase_id=1, name="Phase 1", steps=steps)
    return MachinePlan(
        task_id=task_id,
        task_summary="Timeout test plan",
        phases=[phase],
    )


def _engine(tmp_path: Path) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path)


def _iso_ago(seconds: float) -> str:
    """Return an ISO 8601 timestamp *seconds* in the past."""
    dt = datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)
    return dt.isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Model serialisation
# ---------------------------------------------------------------------------

class TestPlanStepSerialization:
    def test_to_dict_omits_zero_timeout(self) -> None:
        step = _step(timeout_seconds=0)
        d = step.to_dict()
        assert "timeout_seconds" not in d

    def test_to_dict_includes_nonzero_timeout(self) -> None:
        step = _step(timeout_seconds=300)
        d = step.to_dict()
        assert d["timeout_seconds"] == 300

    def test_from_dict_defaults_to_zero(self) -> None:
        data = {
            "step_id": "1.1",
            "agent_name": "backend-engineer",
            "task_description": "Do something",
        }
        step = PlanStep.from_dict(data)
        assert step.timeout_seconds == 0

    def test_from_dict_reads_timeout(self) -> None:
        data = {
            "step_id": "1.1",
            "agent_name": "backend-engineer",
            "task_description": "Do something",
            "timeout_seconds": 120,
        }
        step = PlanStep.from_dict(data)
        assert step.timeout_seconds == 120


# ---------------------------------------------------------------------------
# _effective_timeout helper
# ---------------------------------------------------------------------------

class TestEffectiveTimeout:
    def _make_engine(self, tmp_path: Path) -> ExecutionEngine:
        engine = _engine(tmp_path)
        # Start a minimal plan so the engine is initialised (not strictly
        # required for _effective_timeout, but keeps the test self-contained).
        return engine

    def test_default_timeout_is_zero_no_enforcement(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path)
        step = _step(timeout_seconds=0)
        assert engine._effective_timeout(step) == 0

    def test_step_with_timeout_zero_is_unlimited(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path)
        step = _step(timeout_seconds=0)
        # Explicitly confirm: zero -> unlimited (no enforcement).
        result = engine._effective_timeout(step)
        assert result == 0

    def test_env_var_provides_default_timeout(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path)
        step = _step(timeout_seconds=0)
        with patch.dict(os.environ, {"BATON_DEFAULT_STEP_TIMEOUT_S": "600"}):
            assert engine._effective_timeout(step) == 600

    def test_explicit_step_timeout_overrides_env_var(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path)
        step = _step(timeout_seconds=60)
        with patch.dict(os.environ, {"BATON_DEFAULT_STEP_TIMEOUT_S": "600"}):
            assert engine._effective_timeout(step) == 60

    def test_invalid_env_var_falls_back_to_zero(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path)
        step = _step(timeout_seconds=0)
        with patch.dict(os.environ, {"BATON_DEFAULT_STEP_TIMEOUT_S": "notanint"}):
            assert engine._effective_timeout(step) == 0

    def test_negative_env_var_falls_back_to_zero(self, tmp_path: Path) -> None:
        engine = self._make_engine(tmp_path)
        step = _step(timeout_seconds=0)
        with patch.dict(os.environ, {"BATON_DEFAULT_STEP_TIMEOUT_S": "-1"}):
            assert engine._effective_timeout(step) == 0


# ---------------------------------------------------------------------------
# Timeout enforcement via _determine_action
# ---------------------------------------------------------------------------

class TestStepTimeoutEnforcement:
    def test_step_with_explicit_timeout_marks_failed_when_exceeded(
        self, tmp_path: Path
    ) -> None:
        """A dispatched step that exceeds its timeout is marked failed."""
        step = _step(step_id="1.1", timeout_seconds=10)
        engine = _engine(tmp_path)
        engine.start(_plan([step]))

        # Simulate the step being dispatched 30 seconds ago.
        state = engine._load_execution()
        assert state is not None
        result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer",
            status="dispatched",
            step_started_at=_iso_ago(30),   # 30s ago > 10s timeout
        )
        state.step_results = [result]
        engine._save_execution(state)

        action = engine.next_action()
        assert action.action_type == ActionType.FAILED
        # Confirm the step result was updated to "failed".
        state2 = engine._load_execution()
        assert state2 is not None
        step_result = state2.get_step_result("1.1")
        assert step_result is not None
        assert step_result.status == "failed"
        assert "TIMEOUT" in step_result.outcome

    def test_step_within_timeout_returns_wait(self, tmp_path: Path) -> None:
        """A dispatched step well within its timeout still returns WAIT."""
        step = _step(step_id="1.1", timeout_seconds=3600)
        engine = _engine(tmp_path)
        engine.start(_plan([step]))

        state = engine._load_execution()
        assert state is not None
        result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer",
            status="dispatched",
            step_started_at=_iso_ago(5),   # 5s ago, well within 3600s timeout
        )
        state.step_results = [result]
        engine._save_execution(state)

        action = engine.next_action()
        assert action.action_type == ActionType.WAIT

    def test_step_with_zero_timeout_never_times_out(self, tmp_path: Path) -> None:
        """A step with timeout_seconds=0 is never marked timed out."""
        step = _step(step_id="1.1", timeout_seconds=0)
        engine = _engine(tmp_path)
        engine.start(_plan([step]))

        state = engine._load_execution()
        assert state is not None
        result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer",
            status="dispatched",
            step_started_at=_iso_ago(99999),  # extremely old
        )
        state.step_results = [result]
        engine._save_execution(state)

        action = engine.next_action()
        # Should be WAIT (not FAILED), because timeout is disabled.
        assert action.action_type == ActionType.WAIT

    def test_env_var_default_timeout_triggers_failure(
        self, tmp_path: Path
    ) -> None:
        """BATON_DEFAULT_STEP_TIMEOUT_S applies when step.timeout_seconds==0."""
        step = _step(step_id="1.1", timeout_seconds=0)
        engine = _engine(tmp_path)
        engine.start(_plan([step]))

        state = engine._load_execution()
        assert state is not None
        result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer",
            status="dispatched",
            step_started_at=_iso_ago(120),  # 120s ago > 30s env default
        )
        state.step_results = [result]
        engine._save_execution(state)

        with patch.dict(os.environ, {"BATON_DEFAULT_STEP_TIMEOUT_S": "30"}):
            action = engine.next_action()

        assert action.action_type == ActionType.FAILED


# ---------------------------------------------------------------------------
# Bead filing on timeout
# ---------------------------------------------------------------------------

class TestTimeoutBead:
    def test_timeout_files_warning_bead(self, tmp_path: Path) -> None:
        """On timeout, a warning bead is written to the bead store."""
        step = _step(step_id="1.1", timeout_seconds=5)
        engine = _engine(tmp_path)
        engine.start(_plan([step]))

        # Inject a mock bead store so we can assert write() was called.
        mock_bead_store = MagicMock()
        mock_bead_store.query.return_value = []
        engine._bead_store = mock_bead_store

        state = engine._load_execution()
        assert state is not None
        result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer",
            status="dispatched",
            step_started_at=_iso_ago(60),  # 60s ago > 5s timeout
        )
        state.step_results = [result]
        engine._save_execution(state)

        engine.next_action()

        mock_bead_store.write.assert_called_once()
        bead_arg = mock_bead_store.write.call_args[0][0]
        assert bead_arg.bead_type == "warning"
        assert "timeout" in bead_arg.tags
        assert "1.1" in bead_arg.content

    def test_bead_write_failure_does_not_block_timeout(
        self, tmp_path: Path
    ) -> None:
        """A bead-store write failure must not prevent the step being failed."""
        step = _step(step_id="1.1", timeout_seconds=5)
        engine = _engine(tmp_path)
        engine.start(_plan([step]))

        # Make the bead store raise on write.
        mock_bead_store = MagicMock()
        mock_bead_store.query.return_value = []
        mock_bead_store.write.side_effect = RuntimeError("db locked")
        engine._bead_store = mock_bead_store

        state = engine._load_execution()
        assert state is not None
        result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer",
            status="dispatched",
            step_started_at=_iso_ago(60),
        )
        state.step_results = [result]
        engine._save_execution(state)

        # Must not raise; the step should still be marked failed.
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED
