"""Tests for ActionType.INTERACT — multi-turn interactive step protocol.

Coverage:
  - InteractionTurn to_dict / from_dict round-trip
  - PlanStep.from_dict with and without interactive fields (backward compat)
  - StepResult.from_dict with and without interaction_history (backward compat)
  - Executor: INTERACT action returned when a step is in "interacting" status
  - Executor: provide_interact_input() records human turn, sets interact_dispatched
  - Executor: provide_interact_input() raises when step is not interacting
  - Executor: complete_interaction() promotes step from interacting to complete
  - Executor: record_step_result(status="interacting") appends agent turn
  - Executor: INTERACT_COMPLETE signal in agent output auto-promotes to complete
  - Executor: max_turns auto-complete when turn count hits limit
  - Executor: parallel safety — other steps dispatch while one is interacting
  - Regression: existing DISPATCH/GATE/COMPLETE flows unchanged
  - Dispatcher: build_continuation_prompt() includes interaction history
  - Dispatcher: sliding window (last 3 full, earlier summarised)
  - CLI: _print_action() renders INTERACT format correctly
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import (
    ActionType,
    ExecutionAction,
    InteractionTurn,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Minimal plan/phase/step helpers (mirrors test_executor.py conventions)
# ---------------------------------------------------------------------------

def _step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task: str = "Implement feature X",
    interactive: bool = False,
    max_turns: int = 10,
    depends_on: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        interactive=interactive,
        max_turns=max_turns,
        depends_on=depends_on or [],
    )


def _gate(gate_type: str = "test", command: str = "pytest") -> PlanGate:
    return PlanGate(gate_type=gate_type, command=command)


def _phase(
    phase_id: int = 1,
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
    shared_context: str = "Shared context here.",
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        phases=phases or [_phase()],
        shared_context=shared_context,
    )


def _engine(tmp_path: Path) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path)


# ---------------------------------------------------------------------------
# Tests: InteractionTurn model
# ---------------------------------------------------------------------------

class TestInteractionTurnModel:
    """InteractionTurn serialization and round-trip."""

    def test_to_dict_contains_required_keys(self) -> None:
        turn = InteractionTurn(role="agent", content="Here is my analysis.", turn_number=1)
        d = turn.to_dict()
        assert d["role"] == "agent"
        assert d["content"] == "Here is my analysis."
        assert d["turn_number"] == 1
        assert "timestamp" in d

    def test_from_dict_round_trip(self) -> None:
        original = InteractionTurn(role="human", content="Dig deeper.", turn_number=2)
        restored = InteractionTurn.from_dict(original.to_dict())
        assert restored.role == original.role
        assert restored.content == original.content
        assert restored.turn_number == original.turn_number
        assert restored.timestamp == original.timestamp

    def test_timestamp_auto_set_on_creation(self) -> None:
        turn = InteractionTurn(role="agent", content="output")
        assert turn.timestamp != ""

    def test_timestamp_preserved_from_dict_when_present(self) -> None:
        data = {
            "role": "agent",
            "content": "x",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "turn_number": 5,
        }
        turn = InteractionTurn.from_dict(data)
        assert turn.timestamp == "2026-01-01T00:00:00+00:00"

    def test_from_dict_backward_compat_missing_fields(self) -> None:
        # Only role + content — all other fields default gracefully.
        turn = InteractionTurn.from_dict({"role": "human", "content": "ok"})
        assert turn.role == "human"
        assert turn.content == "ok"
        assert turn.turn_number == 0
        assert isinstance(turn.timestamp, str)

    def test_agent_and_human_roles_both_serialise(self) -> None:
        for role in ("agent", "human"):
            t = InteractionTurn(role=role, content="text")
            d = t.to_dict()
            assert d["role"] == role


# ---------------------------------------------------------------------------
# Tests: PlanStep model — interactive fields
# ---------------------------------------------------------------------------

class TestPlanStepInteractiveFields:
    """PlanStep serialises interactive/max_turns and restores them."""

    def test_interactive_step_to_dict_includes_fields(self) -> None:
        step = _step(interactive=True, max_turns=5)
        d = step.to_dict()
        assert d["interactive"] is True
        assert d["max_turns"] == 5

    def test_non_interactive_step_to_dict_omits_fields(self) -> None:
        # Fields are conditionally included only when interactive=True.
        step = _step(interactive=False)
        d = step.to_dict()
        assert "interactive" not in d
        assert "max_turns" not in d

    def test_from_dict_with_interactive_true(self) -> None:
        data = {
            "step_id": "1.1",
            "agent_name": "data-analyst",
            "task_description": "Analyse the data.",
            "interactive": True,
            "max_turns": 7,
        }
        step = PlanStep.from_dict(data)
        assert step.interactive is True
        assert step.max_turns == 7

    def test_from_dict_without_interactive_defaults_to_false(self) -> None:
        # Backward compatibility: old plans without the field load as non-interactive.
        data = {
            "step_id": "1.1",
            "agent_name": "backend-engineer",
            "task_description": "Do the work.",
        }
        step = PlanStep.from_dict(data)
        assert step.interactive is False
        assert step.max_turns == 10  # default

    def test_round_trip_interactive_step(self) -> None:
        original = _step(interactive=True, max_turns=3)
        restored = PlanStep.from_dict(original.to_dict())
        assert restored.interactive is True
        assert restored.max_turns == 3

    def test_round_trip_non_interactive_step(self) -> None:
        original = _step(interactive=False)
        restored = PlanStep.from_dict(original.to_dict())
        assert restored.interactive is False
        assert restored.max_turns == 10


# ---------------------------------------------------------------------------
# Tests: StepResult model — interaction_history
# ---------------------------------------------------------------------------

class TestStepResultInteractionHistory:
    """StepResult serialises interaction_history and restores it."""

    def test_from_dict_with_interaction_history(self) -> None:
        turns = [
            {"role": "agent", "content": "First response.", "timestamp": "2026-01-01T00:00:00+00:00", "turn_number": 1},
            {"role": "human", "content": "Go deeper.", "timestamp": "2026-01-01T00:01:00+00:00", "turn_number": 1},
        ]
        data = {
            "step_id": "1.1",
            "agent_name": "data-analyst",
            "status": "interacting",
            "outcome": "First response.",
            "interaction_history": turns,
        }
        result = StepResult.from_dict(data)
        assert len(result.interaction_history) == 2
        assert result.interaction_history[0].role == "agent"
        assert result.interaction_history[1].role == "human"

    def test_from_dict_without_interaction_history_defaults_to_empty(self) -> None:
        # Backward compatibility: old StepResult records without the field load fine.
        data = {
            "step_id": "1.1",
            "agent_name": "backend-engineer",
            "status": "complete",
            "outcome": "Done.",
        }
        result = StepResult.from_dict(data)
        assert result.interaction_history == []

    def test_to_dict_includes_history_when_present(self) -> None:
        result = StepResult(
            step_id="1.1",
            agent_name="agent",
            interaction_history=[
                InteractionTurn(role="agent", content="output", turn_number=1)
            ],
        )
        d = result.to_dict()
        assert "interaction_history" in d
        assert len(d["interaction_history"]) == 1
        assert d["interaction_history"][0]["role"] == "agent"

    def test_to_dict_omits_history_when_empty(self) -> None:
        result = StepResult(step_id="1.1", agent_name="agent")
        d = result.to_dict()
        assert "interaction_history" not in d

    def test_round_trip_with_history(self) -> None:
        turns = [
            InteractionTurn(role="agent", content="analysis", turn_number=1),
            InteractionTurn(role="human", content="continue", turn_number=1),
        ]
        original = StepResult(
            step_id="2.1",
            agent_name="data-analyst",
            status="interacting",
            interaction_history=turns,
        )
        restored = StepResult.from_dict(original.to_dict())
        assert len(restored.interaction_history) == 2
        assert restored.interaction_history[0].content == "analysis"
        assert restored.interaction_history[1].content == "continue"


# ---------------------------------------------------------------------------
# Tests: Executor — INTERACT action returned
# ---------------------------------------------------------------------------

class TestExecutorInteractAction:
    """The engine returns INTERACT when a step is in 'interacting' status."""

    def test_interact_action_returned_for_interacting_step(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)

        # Simulate agent responding with partial output (still needs more input).
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="interacting",
            outcome="I found two approaches. Which do you prefer?",
        )

        action = engine.next_action()
        assert action.action_type == ActionType.INTERACT

    def test_interact_action_carries_step_id(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="output")
        action = engine.next_action()
        assert action.interact_step_id == "1.1"

    def test_interact_action_carries_agent_name(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", agent_name="data-analyst", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "data-analyst", status="interacting", outcome="output")
        action = engine.next_action()
        assert action.interact_agent_name == "data-analyst"

    def test_interact_action_carry_agent_output_in_interact_prompt(self, tmp_path: Path) -> None:
        outcome = "Here is my analysis of the two approaches."
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome=outcome)
        action = engine.next_action()
        assert action.interact_prompt == outcome

    def test_interact_action_turn_number(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True, max_turns=5)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="first turn")
        action = engine.next_action()
        # After 1 agent turn, turn count = 1.
        assert action.interact_turn == 1
        assert action.interact_max_turns == 5

    def test_execution_status_stays_running_during_interaction(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="output")
        state = engine._load_state()
        # Global status must NOT block — stays "running".
        assert state.status == "running"


# ---------------------------------------------------------------------------
# Tests: provide_interact_input()
# ---------------------------------------------------------------------------

class TestProvideInteractInput:
    """provide_interact_input() records human turn and sets interact_dispatched."""

    def test_records_human_turn_in_history(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="output")

        engine.provide_interact_input("1.1", "Please go with option A.")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        human_turns = [t for t in result.interaction_history if t.role == "human"]
        assert len(human_turns) == 1
        assert human_turns[0].content == "Please go with option A."

    def test_sets_step_status_to_interact_dispatched(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="output")

        engine.provide_interact_input("1.1", "Use option A.")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "interact_dispatched"

    def test_raises_when_step_not_interacting(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        # Step is in "dispatched" status, not "interacting".
        with pytest.raises(ValueError, match="not in 'interacting' status"):
            engine.provide_interact_input("1.1", "some input")

    def test_raises_when_step_id_not_found(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="output")

        with pytest.raises(ValueError):
            engine.provide_interact_input("9.9", "input for missing step")

    def test_next_action_after_input_is_dispatch_continuation(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="output")
        engine.provide_interact_input("1.1", "Use option A.")

        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.1"
        assert action.interactive is True

    def test_continuation_dispatch_uses_continuation_prompt(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="analysis")
        engine.provide_interact_input("1.1", "Please go deeper.")

        action = engine.next_action()
        # The continuation prompt must include the interaction history section.
        assert "Interaction History" in action.delegation_prompt
        assert "INTERACT_COMPLETE" in action.delegation_prompt

    def test_human_turn_number_increments_sequentially(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="t1")
        engine.provide_interact_input("1.1", "first human")

        # Simulate re-dispatch + next agent turn.
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="t2")
        engine.provide_interact_input("1.1", "second human")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        human_turns = sorted(
            [t for t in result.interaction_history if t.role == "human"],
            key=lambda t: t.turn_number,
        )
        assert human_turns[0].turn_number == 1
        assert human_turns[1].turn_number == 2


# ---------------------------------------------------------------------------
# Tests: complete_interaction()
# ---------------------------------------------------------------------------

class TestCompleteInteraction:
    """complete_interaction() promotes an interacting step to complete."""

    def test_promotes_interacting_to_complete(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="final output")

        engine.complete_interaction("1.1")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "complete"

    def test_preserves_last_agent_outcome(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="final output")

        engine.complete_interaction("1.1")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.outcome == "final output"

    def test_sets_completed_at(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="done")

        engine.complete_interaction("1.1")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.completed_at != ""

    def test_raises_when_step_not_interacting(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        # Step is still "dispatched" — complete_interaction should reject.
        with pytest.raises(ValueError, match="not in 'interacting' status"):
            engine.complete_interaction("1.1")

    def test_engine_reaches_complete_after_interaction_done(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="done")
        engine.complete_interaction("1.1")

        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE


# ---------------------------------------------------------------------------
# Tests: record_step_result with status="interacting"
# ---------------------------------------------------------------------------

class TestRecordStepResultInteracting:
    """record_step_result(status="interacting") appends agent turn to history."""

    def test_appends_agent_turn_to_history(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_step_result(
            "1.1", "backend-engineer", status="interacting",
            outcome="Here is the first analysis."
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        agent_turns = [t for t in result.interaction_history if t.role == "agent"]
        assert len(agent_turns) == 1
        assert agent_turns[0].content == "Here is the first analysis."

    def test_step_stays_interacting_after_normal_turn(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="output")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "interacting"

    def test_interact_complete_signal_promotes_to_complete(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)

        # Agent signals it is done on the same response.
        engine.record_step_result(
            "1.1", "backend-engineer", status="interacting",
            outcome="Final answer here.\nINTERACT_COMPLETE"
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "complete"

    def test_interact_complete_signal_stripped_from_outcome(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_step_result(
            "1.1", "backend-engineer", status="interacting",
            outcome="Final answer here.\nINTERACT_COMPLETE"
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert "INTERACT_COMPLETE" not in result.outcome

    def test_interact_complete_as_standalone_line(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_step_result(
            "1.1", "backend-engineer", status="interacting",
            outcome="INTERACT_COMPLETE"
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "complete"

    def test_max_turns_auto_complete(self, tmp_path: Path) -> None:
        max_turns = 2
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True, max_turns=max_turns)])])
        engine = _engine(tmp_path)
        engine.start(plan)

        # Fill the history to hit the limit: max_turns*2 = 4 turns total.
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="a1")
        engine.provide_interact_input("1.1", "h1")
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="a2")
        engine.provide_interact_input("1.1", "h2")
        # The next agent turn exceeds max_turns*2 and should auto-complete.
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="a3 final")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "complete"
        assert "[Auto-completed: max_turns reached]" in result.outcome

    def test_second_agent_turn_uses_existing_result(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="turn 1")
        engine.provide_interact_input("1.1", "continue")
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="turn 2")

        state = engine._load_state()
        # There should still be only ONE StepResult for "1.1".
        results_for_step = [r for r in state.step_results if r.step_id == "1.1"]
        assert len(results_for_step) == 1
        agent_turns = [t for t in results_for_step[0].interaction_history if t.role == "agent"]
        assert len(agent_turns) == 2


# ---------------------------------------------------------------------------
# Tests: parallel safety
# ---------------------------------------------------------------------------

class TestParallelSafety:
    """Other steps dispatch while one step is in interacting status."""

    def test_non_interactive_step_dispatches_while_other_is_interacting(
        self, tmp_path: Path
    ) -> None:
        # Phase with two independent steps: 1.1 (interactive) and 1.2 (normal).
        steps = [
            _step("1.1", interactive=True),
            _step("1.2", agent_name="test-engineer"),
        ]
        plan = _plan(phases=[_phase(steps=steps)])
        engine = _engine(tmp_path)
        engine.start(plan)

        # Dispatch 1.1 first (it was returned by start()).
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="first")

        # Next action must be to dispatch 1.2 (not INTERACT), because 1.2 is still pending.
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.2"

    def test_interact_action_returned_only_after_all_dispatchable_steps_are_in_flight(
        self, tmp_path: Path
    ) -> None:
        steps = [
            _step("1.1", interactive=True),
            _step("1.2", agent_name="test-engineer"),
        ]
        plan = _plan(phases=[_phase(steps=steps)])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="out")
        # Dispatch step 1.2.
        engine.record_step_result("1.2", "test-engineer", status="dispatched", outcome="")

        # Now 1.1 is interacting, 1.2 is dispatched (in-flight). INTERACT surfaces.
        action = engine.next_action()
        assert action.action_type == ActionType.INTERACT
        assert action.interact_step_id == "1.1"

    def test_interacting_step_does_not_block_global_execution_status(
        self, tmp_path: Path
    ) -> None:
        steps = [
            _step("1.1", interactive=True),
            _step("1.2", agent_name="test-engineer"),
        ]
        plan = _plan(phases=[_phase(steps=steps)])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="out")

        state = engine._load_state()
        assert state.status == "running"

    def test_phase_completes_after_interacting_step_finishes(
        self, tmp_path: Path
    ) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True)])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="interacting", outcome="out")
        engine.complete_interaction("1.1")

        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE


# ---------------------------------------------------------------------------
# Tests: regression — non-interactive flows unchanged
# ---------------------------------------------------------------------------

class TestRegressionNonInteractiveFlows:
    """Existing DISPATCH/GATE/COMPLETE flows must be unaffected."""

    def test_normal_dispatch_flow_unchanged(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH
        assert action.interactive is False

    def test_non_interactive_step_dispatch_has_no_interactive_flag(
        self, tmp_path: Path
    ) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=False)])])
        engine = _engine(tmp_path)
        action = engine.start(plan)
        assert action.interactive is False

    def test_complete_step_leads_to_complete_action(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete", outcome="done")
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

    def test_gate_flow_unchanged(self, tmp_path: Path) -> None:
        plan = _plan(phases=[
            _phase(steps=[_step("1.1")], gate=_gate()),
        ])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete", outcome="done")
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

    def test_failed_step_leads_to_failed_action(self, tmp_path: Path) -> None:
        plan = _plan(phases=[_phase(steps=[_step("1.1")])])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="failed", error="crashed")
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_existing_plan_without_interactive_fields_loads_fine(self) -> None:
        # Simulate an old plan.json with no interactive/max_turns on any step.
        old_plan_data = {
            "task_id": "old-task-001",
            "task_summary": "Old task",
            "phases": [
                {
                    "phase_id": 1,
                    "name": "Build",
                    "steps": [
                        {
                            "step_id": "1.1",
                            "agent_name": "backend-engineer",
                            "task_description": "Do the thing.",
                        }
                    ],
                }
            ],
        }
        plan = MachinePlan.from_dict(old_plan_data)
        assert plan.phases[0].steps[0].interactive is False
        assert plan.phases[0].steps[0].max_turns == 10

    def test_multi_phase_plan_reaches_complete(self, tmp_path: Path) -> None:
        plan = _plan(phases=[
            _phase(phase_id=1, steps=[_step("1.1")]),
            _phase(phase_id=2, steps=[_step("2.1")]),
        ])
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer", status="complete", outcome="done")
        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        engine.record_step_result("2.1", "backend-engineer", status="complete", outcome="done")
        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE


# ---------------------------------------------------------------------------
# Tests: PromptDispatcher.build_continuation_prompt()
# ---------------------------------------------------------------------------

class TestBuildContinuationPrompt:
    """build_continuation_prompt() builds a correct continuation prompt."""

    def _history(self, n: int) -> list[InteractionTurn]:
        """Create n alternating agent/human turns."""
        turns = []
        for i in range(1, n + 1):
            role = "agent" if i % 2 == 1 else "human"
            turns.append(
                InteractionTurn(role=role, content=f"Turn {i} content.", turn_number=i)
            )
        return turns

    def test_contains_interaction_history_header(self) -> None:
        dispatcher = PromptDispatcher()
        step = _step("1.1", interactive=True)
        history = self._history(2)
        prompt = dispatcher.build_continuation_prompt(
            step, history, shared_context="ctx", task_summary="Task"
        )
        assert "Interaction History" in prompt

    def test_contains_interact_complete_signal_instruction(self) -> None:
        dispatcher = PromptDispatcher()
        step = _step("1.1", interactive=True)
        history = self._history(2)
        prompt = dispatcher.build_continuation_prompt(step, history)
        assert "INTERACT_COMPLETE" in prompt

    def test_contains_step_id_in_header(self) -> None:
        dispatcher = PromptDispatcher()
        step = _step("2.3", interactive=True)
        history = self._history(1)
        prompt = dispatcher.build_continuation_prompt(step, history, task_summary="T")
        assert "2.3" in prompt

    def test_recent_turns_shown_in_full(self) -> None:
        dispatcher = PromptDispatcher()
        step = _step("1.1", interactive=True)
        # 3 turns: all should appear in full.
        history = self._history(3)
        prompt = dispatcher.build_continuation_prompt(step, history)
        for turn in history:
            assert turn.content in prompt

    def test_older_turns_summarised_with_sliding_window(self) -> None:
        dispatcher = PromptDispatcher()
        step = _step("1.1", interactive=True)
        # 5 turns: turns 1-2 are "older", turns 3-5 are "recent".
        history = self._history(5)
        prompt = dispatcher.build_continuation_prompt(step, history)
        # Older turns section should appear.
        assert "summarised" in prompt.lower() or "Earlier turns" in prompt

    def test_recent_turns_have_full_content_when_window_exceeded(self) -> None:
        dispatcher = PromptDispatcher()
        step = _step("1.1", interactive=True)
        history = self._history(5)
        prompt = dispatcher.build_continuation_prompt(step, history)
        # The last 3 turns (turns 3, 4, 5) should appear in full.
        for turn in history[-3:]:
            assert turn.content in prompt

    def test_older_turns_truncated_not_in_full(self) -> None:
        dispatcher = PromptDispatcher()
        step = _step("1.1", interactive=True)
        # Create history where older turn has long content that exceeds the snippet limit.
        long_content = "A" * 200
        older_turn = InteractionTurn(role="agent", content=long_content, turn_number=1)
        # 4 total turns so turn 1 is "older" (outside the 3-turn window).
        history = [older_turn] + self._history(3)
        prompt = dispatcher.build_continuation_prompt(step, history)
        # Full content must NOT appear verbatim; only a 100-char snippet.
        assert long_content not in prompt

    def test_empty_history_does_not_crash(self) -> None:
        dispatcher = PromptDispatcher()
        step = _step("1.1", interactive=True)
        prompt = dispatcher.build_continuation_prompt(step, [])
        assert "Interaction History" in prompt
        assert isinstance(prompt, str)

    def test_shared_context_included_in_prompt(self) -> None:
        dispatcher = PromptDispatcher()
        step = _step("1.1", interactive=True)
        prompt = dispatcher.build_continuation_prompt(
            step, [], shared_context="Very specific context."
        )
        assert "Very specific context." in prompt

    def test_task_summary_included_in_prompt(self) -> None:
        dispatcher = PromptDispatcher()
        step = _step("1.1", interactive=True)
        prompt = dispatcher.build_continuation_prompt(
            step, [], task_summary="Migrate the auth system."
        )
        assert "Migrate the auth system." in prompt

    def test_continuation_label_reflects_turn_count(self) -> None:
        dispatcher = PromptDispatcher()
        step = _step("1.1", interactive=True)
        history = self._history(4)
        prompt = dispatcher.build_continuation_prompt(step, history)
        # The prompt header should say "Continuation, Turn 5" (len + 1).
        assert "Turn 5" in prompt


# ---------------------------------------------------------------------------
# Tests: CLI _print_action() for INTERACT
# ---------------------------------------------------------------------------

class TestPrintActionInteract:
    """_print_action() renders the INTERACT format correctly."""

    def _capture(self, action: ExecutionAction) -> str:
        """Run _print_action and return captured stdout."""
        from agent_baton.cli.commands.execution.execute import _print_action
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            _print_action(action.to_dict())
        finally:
            sys.stdout = old_stdout
        return buf.getvalue()

    def test_action_type_header(self) -> None:
        action = ExecutionAction(
            action_type=ActionType.INTERACT,
            message="Interactive step awaiting input.",
            interact_step_id="2.1",
            interact_agent_name="data-analyst",
            interact_turn=1,
            interact_max_turns=10,
            interact_prompt="Here is my analysis.",
        )
        out = self._capture(action)
        assert "ACTION: INTERACT" in out

    def test_step_id_rendered(self) -> None:
        action = ExecutionAction(
            action_type=ActionType.INTERACT,
            message="msg",
            interact_step_id="3.2",
            interact_agent_name="architect",
            interact_turn=2,
            interact_max_turns=8,
            interact_prompt="output",
        )
        out = self._capture(action)
        assert "Step:    3.2" in out

    def test_agent_name_rendered(self) -> None:
        action = ExecutionAction(
            action_type=ActionType.INTERACT,
            message="msg",
            interact_step_id="1.1",
            interact_agent_name="data-scientist",
            interact_turn=1,
            interact_max_turns=10,
            interact_prompt="out",
        )
        out = self._capture(action)
        assert "Agent:   data-scientist" in out

    def test_turn_fraction_rendered(self) -> None:
        action = ExecutionAction(
            action_type=ActionType.INTERACT,
            message="msg",
            interact_step_id="1.1",
            interact_agent_name="agent",
            interact_turn=3,
            interact_max_turns=10,
            interact_prompt="out",
        )
        out = self._capture(action)
        assert "Turn:    3/10" in out

    def test_agent_output_block_delimiters(self) -> None:
        action = ExecutionAction(
            action_type=ActionType.INTERACT,
            message="msg",
            interact_step_id="1.1",
            interact_agent_name="agent",
            interact_turn=1,
            interact_max_turns=10,
            interact_prompt="The agent's analysis.",
        )
        out = self._capture(action)
        assert "--- Agent Output ---" in out
        assert "--- End Output ---" in out
        assert "The agent's analysis." in out

    def test_respond_with_instruction_rendered(self) -> None:
        action = ExecutionAction(
            action_type=ActionType.INTERACT,
            message="msg",
            interact_step_id="2.3",
            interact_agent_name="agent",
            interact_turn=1,
            interact_max_turns=10,
            interact_prompt="output",
        )
        out = self._capture(action)
        assert "baton execute interact --step-id 2.3 --input" in out

    def test_signal_done_instruction_rendered(self) -> None:
        action = ExecutionAction(
            action_type=ActionType.INTERACT,
            message="msg",
            interact_step_id="2.3",
            interact_agent_name="agent",
            interact_turn=1,
            interact_max_turns=10,
            interact_prompt="output",
        )
        out = self._capture(action)
        assert "baton execute interact --step-id 2.3 --done" in out

    def test_dispatch_interactive_step_shows_interactive_flag(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.cli.commands.execution.execute import _print_action
        plan = _plan(phases=[_phase(steps=[_step("1.1", interactive=True, max_turns=6)])])
        engine = _engine(tmp_path)
        action = engine.start(plan)

        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            _print_action(action.to_dict())
        finally:
            sys.stdout = old_stdout
        out = buf.getvalue()

        assert "Interactive: yes" in out
        assert "Max-Turns: 6" in out

    def test_print_action_raises_on_non_string_action_type(self) -> None:
        from agent_baton.cli.commands.execution.execute import _print_action
        with pytest.raises(ValueError, match="action_type must be str"):
            _print_action({"action_type": ActionType.INTERACT})  # enum, not .value
