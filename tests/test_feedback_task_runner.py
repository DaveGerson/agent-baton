"""Tests for the high-throughput feedback task runner.

Covers:
- TestFeedbackModels — FeedbackQuestion/FeedbackResult serialisation roundtrip
- TestFeedbackGates — end-to-end feedback flow through the execution engine
  (present questions, record answers, amend plan with dispatch steps)
- TestPrintActionFeedback — _print_action output format for FEEDBACK actions
"""
from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from agent_baton.models.execution import (
    ActionType,
    ExecutionAction,
    ExecutionState,
    FeedbackQuestion,
    FeedbackResult,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)
from agent_baton.core.engine.executor import ExecutionEngine


# ---------------------------------------------------------------------------
# Shared factory helpers
# ---------------------------------------------------------------------------

def _step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task: str = "Implement feature X",
    model: str = "sonnet",
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        model=model,
    )


def _feedback_question(
    question_id: str = "q1",
    question: str = "Which layout style?",
    context: str = "The dashboard needs a layout approach.",
    options: list[str] | None = None,
    option_agents: list[str] | None = None,
    option_prompts: list[str] | None = None,
) -> FeedbackQuestion:
    return FeedbackQuestion(
        question_id=question_id,
        question=question,
        context=context,
        options=options or ["Grid layout", "List layout", "Card layout"],
        option_agents=option_agents or ["frontend-engineer", "frontend-engineer", "frontend-engineer"],
        option_prompts=option_prompts or [
            "Implement a grid-based dashboard layout for: {task}",
            "Implement a list-based dashboard layout for: {task}",
            "Implement a card-based dashboard layout for: {task}",
        ],
    )


def _phase(
    phase_id: int = 0,
    name: str = "Implementation",
    steps: list[PlanStep] | None = None,
    gate: PlanGate | None = None,
    approval_required: bool = False,
    feedback_questions: list[FeedbackQuestion] | None = None,
) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=name,
        steps=steps or [_step()],
        gate=gate,
        approval_required=approval_required,
        feedback_questions=feedback_questions or [],
    )


def _plan(
    task_id: str = "task-001",
    phases: list[PlanPhase] | None = None,
    task_summary: str = "Build a dashboard",
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        phases=phases if phases is not None else [_phase()],
    )


def _engine(tmp_path: Path) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path)


def _reach_feedback(
    tmp_path: Path,
    *,
    questions: list[FeedbackQuestion] | None = None,
    gate: PlanGate | None = None,
) -> ExecutionEngine:
    """Start engine on a plan with a feedback gate and complete the step.

    Returns the engine positioned just before the FEEDBACK action is consumed.
    """
    fqs = questions or [_feedback_question()]
    plan = _plan(
        phases=[
            _phase(
                phase_id=0,
                steps=[_step("1.1")],
                feedback_questions=fqs,
                gate=gate,
            ),
        ]
    )
    engine = _engine(tmp_path)
    engine.start(plan)
    engine.record_step_result("1.1", "backend-engineer")
    return engine


# ===========================================================================
# TestFeedbackModels
# ===========================================================================

class TestFeedbackModels:
    """Serialisation roundtrip for FeedbackQuestion and FeedbackResult."""

    def test_feedback_question_roundtrip(self) -> None:
        q = _feedback_question()
        data = q.to_dict()
        q2 = FeedbackQuestion.from_dict(data)
        assert q2.question_id == q.question_id
        assert q2.options == q.options
        assert q2.option_agents == q.option_agents
        assert q2.option_prompts == q.option_prompts

    def test_feedback_result_roundtrip(self) -> None:
        r = FeedbackResult(
            phase_id=1,
            question_id="q1",
            chosen_option="Grid layout",
            chosen_index=0,
            dispatched_step_id="2.1",
        )
        data = r.to_dict()
        r2 = FeedbackResult.from_dict(data)
        assert r2.phase_id == r.phase_id
        assert r2.chosen_option == r.chosen_option
        assert r2.dispatched_step_id == r.dispatched_step_id

    def test_plan_phase_with_feedback_roundtrip(self) -> None:
        phase = _phase(feedback_questions=[_feedback_question()])
        data = phase.to_dict()
        phase2 = PlanPhase.from_dict(data)
        assert len(phase2.feedback_questions) == 1
        assert phase2.feedback_questions[0].question_id == "q1"

    def test_execution_state_with_feedback_roundtrip(self) -> None:
        plan = _plan(phases=[_phase(feedback_questions=[_feedback_question()])])
        state = ExecutionState(
            task_id="t1",
            plan=plan,
            feedback_results=[
                FeedbackResult(
                    phase_id=0,
                    question_id="q1",
                    chosen_option="Grid layout",
                    chosen_index=0,
                )
            ],
        )
        data = state.to_dict()
        state2 = ExecutionState.from_dict(data)
        assert len(state2.feedback_results) == 1
        assert state2.feedback_results[0].question_id == "q1"

    def test_execution_state_backward_compat(self) -> None:
        """Old state files without feedback_results should load cleanly."""
        plan = _plan()
        state = ExecutionState(task_id="t1", plan=plan)
        data = state.to_dict()
        del data["feedback_results"]  # Simulate old format
        state2 = ExecutionState.from_dict(data)
        assert state2.feedback_results == []


# ===========================================================================
# TestFeedbackGates
# ===========================================================================

class TestFeedbackGates:
    # ------------------------------------------------------------------ #
    # 1. Phase with feedback_questions returns FEEDBACK after steps       #
    # ------------------------------------------------------------------ #

    def test_feedback_returned_after_steps_complete(self, tmp_path: Path) -> None:
        engine = _reach_feedback(tmp_path)
        action = engine.next_action()
        assert action.action_type == ActionType.FEEDBACK

    # ------------------------------------------------------------------ #
    # 2. Phase without feedback_questions skips to gate/next              #
    # ------------------------------------------------------------------ #

    def test_no_feedback_skips_to_gate(self, tmp_path: Path) -> None:
        plan = _plan(
            phases=[_phase(
                steps=[_step("1.1")],
                gate=PlanGate(gate_type="test", command="pytest"),
            )]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.action_type == ActionType.GATE

    # ------------------------------------------------------------------ #
    # 3. FEEDBACK action carries questions and context                    #
    # ------------------------------------------------------------------ #

    def test_feedback_action_carries_questions(self, tmp_path: Path) -> None:
        engine = _reach_feedback(tmp_path)
        action = engine.next_action()
        assert len(action.feedback_questions) == 1
        assert action.feedback_questions[0].question_id == "q1"
        assert len(action.feedback_questions[0].options) == 3

    def test_feedback_action_carries_phase_id(self, tmp_path: Path) -> None:
        engine = _reach_feedback(tmp_path)
        action = engine.next_action()
        assert action.phase_id == 0

    def test_feedback_action_has_context(self, tmp_path: Path) -> None:
        engine = _reach_feedback(tmp_path)
        action = engine.next_action()
        assert "Feedback" in action.feedback_context

    # ------------------------------------------------------------------ #
    # 4. Recording feedback creates a plan amendment                      #
    # ------------------------------------------------------------------ #

    def test_record_feedback_creates_amendment(self, tmp_path: Path) -> None:
        engine = _reach_feedback(tmp_path)
        engine.next_action()  # consume FEEDBACK action
        engine.record_feedback_result(phase_id=0, question_id="q1", chosen_index=0)
        # The engine should have amended the plan with a new dispatch phase.
        status = engine.status()
        assert status["status"] == "running"

    def test_record_feedback_resumes_execution(self, tmp_path: Path) -> None:
        engine = _reach_feedback(tmp_path)
        engine.next_action()  # consume FEEDBACK
        engine.record_feedback_result(phase_id=0, question_id="q1", chosen_index=0)
        action = engine.next_action()
        # Should now dispatch the feedback-generated step (or proceed to gate/next)
        assert action.action_type in (ActionType.DISPATCH, ActionType.GATE, ActionType.COMPLETE)

    # ------------------------------------------------------------------ #
    # 5. Multiple questions — all must be answered                        #
    # ------------------------------------------------------------------ #

    def test_multiple_questions_all_must_resolve(self, tmp_path: Path) -> None:
        q1 = _feedback_question(question_id="q1", question="Layout?")
        q2 = _feedback_question(
            question_id="q2",
            question="Color scheme?",
            options=["Dark", "Light"],
            option_agents=["frontend-engineer", "frontend-engineer"],
            option_prompts=["Dark theme for: {task}", "Light theme for: {task}"],
        )
        engine = _reach_feedback(tmp_path, questions=[q1, q2])
        action = engine.next_action()
        assert action.action_type == ActionType.FEEDBACK
        assert len(action.feedback_questions) == 2
        first_phase_id = action.phase_id

        # Answer first question.
        engine.record_feedback_result(phase_id=first_phase_id, question_id="q1", chosen_index=1)
        action = engine.next_action()
        # Should still be FEEDBACK — q2 unanswered.
        assert action.action_type == ActionType.FEEDBACK
        assert len(action.feedback_questions) == 1
        assert action.feedback_questions[0].question_id == "q2"

        # Answer second question — use the phase_id from the latest action
        # (phase_id may have been renumbered by the first amendment).
        engine.record_feedback_result(phase_id=action.phase_id, question_id="q2", chosen_index=0)
        action = engine.next_action()
        # Now should proceed past feedback.
        assert action.action_type != ActionType.FEEDBACK

    # ------------------------------------------------------------------ #
    # 6. Feedback before gate — gate still runs after feedback resolves   #
    # ------------------------------------------------------------------ #

    def test_feedback_before_gate(self, tmp_path: Path) -> None:
        gate = PlanGate(gate_type="test", command="pytest")
        engine = _reach_feedback(tmp_path, gate=gate)
        action = engine.next_action()
        assert action.action_type == ActionType.FEEDBACK

        engine.record_feedback_result(phase_id=0, question_id="q1", chosen_index=0)
        # After feedback, it should proceed to gate.
        action = engine.next_action()
        # The new feedback-dispatch phase is inserted and needs dispatching first.
        assert action.action_type in (ActionType.DISPATCH, ActionType.GATE)

    # ------------------------------------------------------------------ #
    # 7. Invalid chosen_index raises ValueError                           #
    # ------------------------------------------------------------------ #

    def test_invalid_chosen_index_raises(self, tmp_path: Path) -> None:
        engine = _reach_feedback(tmp_path)
        engine.next_action()
        with pytest.raises(ValueError, match="out of range"):
            engine.record_feedback_result(phase_id=0, question_id="q1", chosen_index=99)

    def test_unknown_question_id_raises(self, tmp_path: Path) -> None:
        engine = _reach_feedback(tmp_path)
        engine.next_action()
        with pytest.raises(ValueError, match="not found"):
            engine.record_feedback_result(phase_id=0, question_id="nonexistent", chosen_index=0)

    # ------------------------------------------------------------------ #
    # 8. Prompt template {task} expansion                                 #
    # ------------------------------------------------------------------ #

    def test_prompt_template_expands_task(self, tmp_path: Path) -> None:
        engine = _reach_feedback(tmp_path)
        engine.next_action()
        engine.record_feedback_result(phase_id=0, question_id="q1", chosen_index=0)
        # Check the amended plan's new step has the expanded prompt.
        status = engine.status()
        # Load execution state directly to inspect.
        state = engine._load_execution()
        # Find the feedback-dispatch phase.
        feedback_phases = [
            p for p in state.plan.phases if "Feedback-Dispatch" in p.name
        ]
        assert len(feedback_phases) >= 1
        step = feedback_phases[0].steps[0]
        assert "Build a dashboard" in step.task_description

    # ------------------------------------------------------------------ #
    # 9. Feedback with approval — approval checked first                  #
    # ------------------------------------------------------------------ #

    def test_approval_before_feedback(self, tmp_path: Path) -> None:
        """When a phase has both approval and feedback, approval runs first."""
        plan = _plan(
            phases=[_phase(
                phase_id=0,
                steps=[_step("1.1")],
                approval_required=True,
                feedback_questions=[_feedback_question()],
            )]
        )
        engine = _engine(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        action = engine.next_action()
        assert action.action_type == ActionType.APPROVAL

        engine.record_approval_result(phase_id=0, result="approve")
        action = engine.next_action()
        assert action.action_type == ActionType.FEEDBACK


# ===========================================================================
# TestPrintActionFeedback
# ===========================================================================

class TestPrintActionFeedback:
    """Test the _print_action output format for FEEDBACK actions."""

    def test_print_action_feedback(self, capsys) -> None:
        from agent_baton.cli.commands.execution.execute import _print_action

        q = _feedback_question()
        action = ExecutionAction(
            action_type=ActionType.FEEDBACK,
            message="Phase 1 has feedback questions.",
            phase_id=1,
            feedback_questions=[q],
            feedback_context="Prior work summary here.",
        )
        _print_action(action.to_dict())
        captured = capsys.readouterr().out
        assert "ACTION: FEEDBACK" in captured
        assert "Phase:   1" in captured
        assert "--- Feedback Context ---" in captured
        assert "--- End Context ---" in captured
        assert "--- Question: q1 ---" in captured
        assert "[0] Grid layout" in captured
        assert "[1] List layout" in captured
        assert "[2] Card layout" in captured
        assert "--- End Question ---" in captured
        assert "baton execute feedback" in captured

    def test_feedback_action_to_dict(self) -> None:
        q = _feedback_question()
        action = ExecutionAction(
            action_type=ActionType.FEEDBACK,
            message="test",
            phase_id=1,
            feedback_questions=[q],
            feedback_context="ctx",
        )
        d = action.to_dict()
        assert d["action_type"] == "feedback"
        assert len(d["feedback_questions"]) == 1
        assert d["feedback_context"] == "ctx"
        assert d["phase_id"] == 1


# ===========================================================================
# TestPlanMarkdown
# ===========================================================================

class TestPlanMarkdown:
    """Test that feedback questions render in plan markdown."""

    def test_feedback_in_markdown(self) -> None:
        plan = _plan(phases=[
            _phase(feedback_questions=[_feedback_question()])
        ])
        md = plan.to_markdown()
        assert "[FEEDBACK GATE]" in md
        assert "Feedback Gate" in md
        assert "q1" in md
        assert "Grid layout" in md
