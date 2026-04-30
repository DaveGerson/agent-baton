"""Unit tests for CHECKPOINT emission (context-rot defence).

Covers:
- 70% token threshold trips CHECKPOINT
- 10 step completions in a phase trips CHECKPOINT
- CHECKPOINT fires only once per phase
- Phase advance resets the trip so the next phase re-arms
- CHECKPOINT is NOT emitted before either threshold
- _print_action renders CHECKPOINT as structured output
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip(
    "agent_baton.core.engine.resolver",
    reason="005b decomposition not yet present on this branch",
)

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import (
    ActionType,
    ExecutionAction,
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STANDARD_LIMIT = 500_000  # matches executor.py "standard" tier threshold


def _step(step_id: str = "1.1") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer",
        task_description="do work",
    )


def _phase(phase_id: int = 1, steps: list[PlanStep] | None = None) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=f"phase-{phase_id}",
        steps=steps if steps is not None else [_step(f"{phase_id}.1")],
    )


def _plan(
    task_id: str = "cp-test",
    phases: list[PlanPhase] | None = None,
    budget_tier: str = "standard",
) -> MachinePlan:
    plan = MachinePlan(
        task_id=task_id,
        task_summary="checkpoint test task",
        phases=phases if phases is not None else [_phase()],
    )
    plan.budget_tier = budget_tier
    return plan


def _engine(tmp_path: Path) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path, enforce_token_budget=False)


def _fresh(
    tmp_path: Path,
    *,
    budget_tier: str = "standard",
    phases: list[PlanPhase] | None = None,
) -> tuple[ExecutionEngine, ExecutionState]:
    """Return an engine + running ExecutionState, no prior step results."""
    engine = _engine(tmp_path)
    plan = _plan(phases=phases, budget_tier=budget_tier)
    engine.start(plan)
    state = engine._load_state()
    assert state is not None
    state.status = "running"
    return engine, state


def _add_token_result(
    state: ExecutionState,
    estimated_tokens: int,
    step_id: str = "1.1",
    status: str = "complete",
) -> None:
    """Append a fake StepResult with the given token count."""
    state.step_results.append(
        StepResult(
            step_id=step_id,
            agent_name="backend-engineer",
            status=status,
            estimated_tokens=estimated_tokens,
        )
    )


# ---------------------------------------------------------------------------
# Tests: _check_checkpoint_triggers directly
# ---------------------------------------------------------------------------


class TestTokenThresholdTrigger:
    """CHECKPOINT fires at >=70% of the effective budget."""

    def test_fires_at_70_percent(self, tmp_path: Path) -> None:
        engine, state = _fresh(tmp_path)
        # 70% of standard (500_000) = 350_000
        _add_token_result(state, 350_000)

        action = engine._check_checkpoint_triggers(state)

        assert action is not None
        assert action.action_type == ActionType.CHECKPOINT
        assert action.metadata["reason"] == "token_threshold"
        assert action.metadata["tokens_used"] == 350_000
        assert action.metadata["tokens_limit"] == _STANDARD_LIMIT

    def test_fires_above_70_percent(self, tmp_path: Path) -> None:
        engine, state = _fresh(tmp_path)
        _add_token_result(state, 499_999)  # just below 100%

        action = engine._check_checkpoint_triggers(state)

        assert action is not None
        assert action.action_type == ActionType.CHECKPOINT
        assert action.metadata["reason"] == "token_threshold"

    def test_does_not_fire_below_70_percent(self, tmp_path: Path) -> None:
        engine, state = _fresh(tmp_path)
        _add_token_result(state, 349_999)  # just below 70%

        action = engine._check_checkpoint_triggers(state)

        assert action is None

    def test_does_not_fire_with_zero_tokens(self, tmp_path: Path) -> None:
        engine, state = _fresh(tmp_path)

        action = engine._check_checkpoint_triggers(state)

        assert action is None

    def test_respects_explicit_token_budget(self, tmp_path: Path) -> None:
        """An explicit token_budget override is honoured over the tier threshold."""
        engine = ExecutionEngine(
            team_context_root=tmp_path,
            enforce_token_budget=False,
            token_budget=10_000,
        )
        plan = _plan()
        engine.start(plan)
        state = engine._load_state()
        assert state is not None
        state.status = "running"
        _add_token_result(state, 7_000)  # 70% of 10_000

        action = engine._check_checkpoint_triggers(state)

        assert action is not None
        assert action.metadata["reason"] == "token_threshold"
        assert action.metadata["tokens_limit"] == 10_000


class TestStepCountThresholdTrigger:
    """CHECKPOINT fires at 10 consecutive completions within a phase."""

    def _set_step_count(
        self, state: ExecutionState, phase_id: int, count: int
    ) -> None:
        if state.speculations is None:
            state.speculations = {}
        counts = state.speculations.get("_checkpoint_step_count", {})
        counts[f"phase_{phase_id}"] = count
        state.speculations["_checkpoint_step_count"] = counts

    def test_fires_at_exactly_10_completions(self, tmp_path: Path) -> None:
        engine, state = _fresh(tmp_path)
        self._set_step_count(state, phase_id=1, count=10)

        action = engine._check_checkpoint_triggers(state)

        assert action is not None
        assert action.action_type == ActionType.CHECKPOINT
        assert action.metadata["reason"] == "step_count_threshold"
        assert action.metadata["step_count_in_phase"] == 10

    def test_fires_above_10_completions(self, tmp_path: Path) -> None:
        engine, state = _fresh(tmp_path)
        self._set_step_count(state, phase_id=1, count=15)

        action = engine._check_checkpoint_triggers(state)

        assert action is not None
        assert action.metadata["reason"] == "step_count_threshold"

    def test_does_not_fire_below_10_completions(self, tmp_path: Path) -> None:
        engine, state = _fresh(tmp_path)
        self._set_step_count(state, phase_id=1, count=9)

        action = engine._check_checkpoint_triggers(state)

        assert action is None

    def test_does_not_fire_with_zero_completions(self, tmp_path: Path) -> None:
        engine, state = _fresh(tmp_path)

        action = engine._check_checkpoint_triggers(state)

        assert action is None

    def test_token_threshold_takes_precedence_over_step_count(
        self, tmp_path: Path
    ) -> None:
        """When both triggers fire, token_threshold is checked first."""
        engine, state = _fresh(tmp_path)
        _add_token_result(state, 400_000)  # > 70%
        self._set_step_count(state, phase_id=1, count=10)

        action = engine._check_checkpoint_triggers(state)

        assert action is not None
        assert action.metadata["reason"] == "token_threshold"


# ---------------------------------------------------------------------------
# Tests: one-shot per phase
# ---------------------------------------------------------------------------


class TestCheckpointOneShot:
    """CHECKPOINT fires only once per phase; phase advance re-arms it."""

    def test_does_not_re_emit_for_same_phase(self, tmp_path: Path) -> None:
        """After the first CHECKPOINT the trigger is suppressed for that phase."""
        engine, state = _fresh(tmp_path)
        _add_token_result(state, 400_000)

        first = engine._check_checkpoint_triggers(state)
        assert first is not None, "Expected CHECKPOINT on first call"

        # Second call with the same trigger still active — must return None.
        second = engine._check_checkpoint_triggers(state)
        assert second is None, "CHECKPOINT must not re-emit for the same phase"

    def test_phase_advance_re_arms_trigger(self, tmp_path: Path) -> None:
        """After a phase advance the next phase can trip CHECKPOINT again."""
        phases = [
            _phase(phase_id=1, steps=[_step("1.1")]),
            _phase(phase_id=2, steps=[_step("2.1")]),
        ]
        engine, state = _fresh(tmp_path, phases=phases)
        _add_token_result(state, 400_000)

        # Fire CHECKPOINT for phase 1.
        first = engine._check_checkpoint_triggers(state)
        assert first is not None
        assert first.metadata.get("phase_id") == 1 or first.phase_id == 1

        # Simulate phase advance: reset checkpoint state for phase 1 and
        # move current_phase pointer to phase 2.
        engine._reset_checkpoint_state(state, phase_id=1)
        state.current_phase = 1  # advance to phase 2 (index 1)

        # The token count is still above 70%, so phase 2 should trip.
        second = engine._check_checkpoint_triggers(state)
        assert second is not None, (
            "CHECKPOINT must re-arm after phase advance when token threshold "
            "is still exceeded"
        )
        assert second.phase_id == 2

    def test_retry_phase_re_arms_trigger(self, tmp_path: Path) -> None:
        """After a phase retry the same phase can trip CHECKPOINT again."""
        engine, state = _fresh(tmp_path)
        _add_token_result(state, 400_000)

        first = engine._check_checkpoint_triggers(state)
        assert first is not None

        # Simulate RETRY_PHASE reset.
        engine._reset_checkpoint_state(state, phase_id=1)

        second = engine._check_checkpoint_triggers(state)
        assert second is not None, "CHECKPOINT must re-arm after phase retry"


# ---------------------------------------------------------------------------
# Tests: step count incremented by record_step_result
# ---------------------------------------------------------------------------


class TestStepCountIncrement:
    """record_step_result increments _checkpoint_step_count on 'complete'."""

    def test_complete_increments_counter(self, tmp_path: Path) -> None:
        engine = ExecutionEngine(
            team_context_root=tmp_path, enforce_token_budget=False
        )
        phases = [_phase(phase_id=1, steps=[_step("1.1"), _step("1.2")])]
        plan = _plan(phases=phases)
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome="done",
        )

        state = engine._load_state()
        assert state is not None
        specs = state.speculations or {}
        counts = specs.get("_checkpoint_step_count", {})
        assert counts.get("phase_1", 0) == 1

    def test_failed_does_not_increment_counter(self, tmp_path: Path) -> None:
        engine = ExecutionEngine(
            team_context_root=tmp_path, enforce_token_budget=False
        )
        phases = [_phase(phase_id=1, steps=[_step("1.1")])]
        plan = _plan(phases=phases)
        engine.start(plan)
        engine.mark_dispatched("1.1", "backend-engineer")
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="failed",
            error="something went wrong",
        )

        state = engine._load_state()
        assert state is not None
        specs = state.speculations or {}
        counts = specs.get("_checkpoint_step_count", {})
        assert counts.get("phase_1", 0) == 0

    def test_ten_completions_trigger_checkpoint_via_drive_loop(
        self, tmp_path: Path
    ) -> None:
        """With 10 complete steps and no token pressure, _drive_resolver_loop
        returns CHECKPOINT before the 11th DISPATCH."""
        # Build a phase with 12 steps so there is always a next step to dispatch.
        steps = [_step(f"1.{i}") for i in range(1, 13)]
        phases = [_phase(phase_id=1, steps=steps)]
        engine = ExecutionEngine(
            team_context_root=tmp_path, enforce_token_budget=False
        )
        plan = _plan(phases=phases)
        engine.start(plan)

        # Record 10 completed steps to trip the counter.
        for i in range(1, 11):
            step_id = f"1.{i}"
            engine.mark_dispatched(step_id, "backend-engineer")
            engine.record_step_result(
                step_id=step_id,
                agent_name="backend-engineer",
                status="complete",
                outcome="done",
            )

        # The 11th call to next_action should return CHECKPOINT, not DISPATCH.
        action = engine.next_action()
        assert action.action_type == ActionType.CHECKPOINT, (
            f"Expected CHECKPOINT after 10 completions, got {action.action_type}"
        )
        assert action.metadata["reason"] == "step_count_threshold"


# ---------------------------------------------------------------------------
# Tests: _print_action CHECKPOINT branch
# ---------------------------------------------------------------------------


class TestPrintActionCheckpoint:
    """_print_action renders CHECKPOINT action as structured text."""

    def test_checkpoint_output_contains_key_labels(self) -> None:
        from agent_baton.cli.commands.execution.execute import _print_action

        action_dict = {
            "action_type": "checkpoint",
            "message": (
                "Save state with `baton execute pause` and start a fresh "
                "Claude session with `baton execute resume`."
            ),
            "phase_id": 2,
            "reason": "token_threshold",
            "tokens_used": 375_000,
            "tokens_limit": 500_000,
            "step_count_in_phase": 3,
        }

        captured = StringIO()
        with patch("sys.stdout", captured):
            _print_action(action_dict)

        output = captured.getvalue()
        assert "ACTION: CHECKPOINT" in output
        assert "token_threshold" in output
        assert "375,000" in output
        assert "500,000" in output
        assert "baton execute pause" in output
        assert "baton execute resume" in output

    def test_checkpoint_to_dict_roundtrip(self) -> None:
        """ExecutionAction.to_dict() serialises CHECKPOINT fields correctly."""
        action = ExecutionAction(
            action_type=ActionType.CHECKPOINT,
            message="Save state with `baton execute pause`...",
            phase_id=3,
            metadata={
                "reason": "step_count_threshold",
                "tokens_used": 100,
                "tokens_limit": 500_000,
                "step_count_in_phase": 10,
            },
        )
        d = action.to_dict()

        assert d["action_type"] == "checkpoint"
        assert d["reason"] == "step_count_threshold"
        assert d["tokens_used"] == 100
        assert d["tokens_limit"] == 500_000
        assert d["phase_id"] == 3
        assert d["step_count_in_phase"] == 10
