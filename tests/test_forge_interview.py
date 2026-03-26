"""Tests for interview question generation and plan regeneration."""
from __future__ import annotations

import pytest

pydantic = pytest.importorskip("pydantic")

from pathlib import Path
from unittest.mock import MagicMock
from agent_baton.core.pmo.forge import ForgeSession
from agent_baton.core.runtime.headless import HeadlessClaude, HeadlessConfig
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.pmo import InterviewQuestion, InterviewAnswer
from agent_baton.api.models.requests import (
    InterviewAnswerPayload,
    InterviewRequest,
    RegenerateRequest,
)
from agent_baton.api.models.responses import (
    InterviewQuestionResponse,
    InterviewResponse,
    AdoWorkItemResponse,
    AdoSearchResponse,
)


def test_interview_question_to_dict():
    q = InterviewQuestion(
        id="q1",
        question="What testing strategy?",
        context="Plan has no test phase",
        answer_type="choice",
        choices=["unit", "integration", "both"],
    )
    d = q.to_dict()
    assert d["id"] == "q1"
    assert d["answer_type"] == "choice"
    assert d["choices"] == ["unit", "integration", "both"]


def test_interview_question_from_dict():
    d = {"id": "q2", "question": "Timeout?", "context": "Long task", "answer_type": "text"}
    q = InterviewQuestion.from_dict(d)
    assert q.id == "q2"
    assert q.choices is None


def test_interview_answer_to_dict():
    a = InterviewAnswer(question_id="q1", answer="both")
    d = a.to_dict()
    assert d == {"question_id": "q1", "answer": "both"}


def test_interview_answer_from_dict():
    d = {"question_id": "q1", "answer": "both"}
    a = InterviewAnswer.from_dict(d)
    assert a.question_id == "q1"
    assert a.answer == "both"


def test_interview_question_roundtrip():
    q = InterviewQuestion(
        id="q1", question="Testing?", context="No tests",
        answer_type="choice", choices=["unit", "e2e"],
    )
    assert InterviewQuestion.from_dict(q.to_dict()).to_dict() == q.to_dict()


def test_interview_question_to_dict_omits_none_choices():
    q = InterviewQuestion(id="q1", question="What?", context="ctx", answer_type="text")
    d = q.to_dict()
    assert "choices" not in d


def test_interview_question_to_dict_includes_empty_choices():
    q = InterviewQuestion(id="q1", question="What?", context="ctx", answer_type="choice", choices=[])
    d = q.to_dict()
    assert d["choices"] == []


def test_interview_answer_roundtrip():
    a = InterviewAnswer(question_id="q1", answer="yes")
    assert InterviewAnswer.from_dict(a.to_dict()).to_dict() == a.to_dict()


def test_interview_request_validates():
    req = InterviewRequest(plan={"task_id": "t1"}, feedback="needs more tests")
    assert req.feedback == "needs more tests"


def test_regenerate_request_validates():
    req = RegenerateRequest(
        project_id="proj1",
        description="build a thing",
        original_plan={"task_id": "t1"},
        answers=[InterviewAnswerPayload(question_id="q1", answer="both")],
    )
    assert len(req.answers) == 1
    assert req.answers[0].question_id == "q1"


def test_interview_response_validates():
    resp = InterviewResponse(questions=[
        InterviewQuestionResponse(
            id="q1", question="Testing?", context="no test phase",
            answer_type="choice", choices=["unit", "e2e"],
        )
    ])
    assert len(resp.questions) == 1


def test_ado_search_response_validates():
    resp = AdoSearchResponse(items=[
        AdoWorkItemResponse(
            id="F-100", title="Feature", type="Feature",
            program="NDS", owner="Dave", priority="P0",
            description="Build it",
        )
    ])
    assert resp.items[0].id == "F-100"


def _make_plan(
    *,
    phases=1,
    steps_per_phase=2,
    has_gate=False,
    risk_level: str = "LOW",
    agent_names: list[str] | None = None,
):
    """Build a minimal MachinePlan for testing.

    Parameters
    ----------
    phases:
        Number of phases to create.
    steps_per_phase:
        Number of steps per phase.
    has_gate:
        When True, attach a PlanGate to every phase.
    risk_level:
        Value passed to ``MachinePlan.risk_level`` (e.g. "LOW", "HIGH").
    agent_names:
        If supplied, step ``i`` inside phase ``j`` is assigned
        ``agent_names[(j * steps_per_phase + i) % len(agent_names)]``.
        This lets callers control the set of distinct agents in the plan.
        When ``None`` every step uses ``"backend-engineer"``.
    """
    from agent_baton.models.execution import PlanGate

    plan_phases = []
    for pi in range(phases):
        steps = []
        for si in range(steps_per_phase):
            if agent_names:
                agent = agent_names[(pi * steps_per_phase + si) % len(agent_names)]
            else:
                agent = "backend-engineer"
            steps.append(
                PlanStep(
                    step_id=f"{pi+1}.{si+1}",
                    agent_name=agent,
                    task_description=f"Step {pi+1}.{si+1}",
                )
            )
        gate = PlanGate(gate_type="test", command="pytest") if has_gate else None
        plan_phases.append(
            PlanPhase(phase_id=pi, name=f"Phase {pi+1}", steps=steps, gate=gate)
        )
    return MachinePlan(
        task_id="test-001",
        task_summary="Test plan",
        phases=plan_phases,
        risk_level=risk_level,
    )


def test_generate_interview_returns_questions():
    planner = MagicMock()
    store = MagicMock()
    forge = ForgeSession(planner=planner, store=store, headless=HeadlessClaude(HeadlessConfig(claude_path="/nonexistent/claude")))
    plan = _make_plan(phases=2, steps_per_phase=3)
    questions = forge.generate_interview(plan)
    assert isinstance(questions, list)
    assert len(questions) >= 1
    for q in questions:
        assert q.id
        assert q.question
        assert q.answer_type in ("choice", "text")


def test_generate_interview_asks_about_missing_tests():
    planner = MagicMock()
    store = MagicMock()
    forge = ForgeSession(planner=planner, store=store, headless=HeadlessClaude(HeadlessConfig(claude_path="/nonexistent/claude")))
    plan = _make_plan(phases=1, steps_per_phase=2)
    questions = forge.generate_interview(plan)
    question_texts = [q.question.lower() for q in questions]
    assert any("test" in t for t in question_texts)


def test_regenerate_plan_calls_planner_with_enriched_context():
    planner = MagicMock()
    planner.create_plan.return_value = _make_plan()
    store = MagicMock()
    store.get_project.return_value = MagicMock(path="/tmp/proj")
    forge = ForgeSession(planner=planner, store=store, headless=HeadlessClaude(HeadlessConfig(claude_path="/nonexistent/claude")))
    answers = [InterviewAnswer(question_id="q1", answer="unit tests")]
    forge.regenerate_plan(
        description="build a widget",
        project_id="proj1",
        answers=answers,
    )
    planner.create_plan.assert_called_once()
    call_kwargs = planner.create_plan.call_args
    assert "unit tests" in call_kwargs.kwargs.get("task_summary", "")


# ---------------------------------------------------------------------------
# Additional generate_interview branch tests
# ---------------------------------------------------------------------------


def _forge() -> ForgeSession:
    """Return a ForgeSession with stub planner and store."""
    return ForgeSession(planner=MagicMock(), store=MagicMock(), headless=HeadlessClaude(HeadlessConfig(claude_path="/nonexistent/claude")))


def test_generate_interview_asks_about_risk_for_high_risk_plan():
    """Plans classified HIGH must produce a q-risk question."""
    plan = _make_plan(phases=1, steps_per_phase=2, risk_level="HIGH")
    questions = _forge().generate_interview(plan)
    ids = [q.id for q in questions]
    assert "q-risk" in ids


def test_generate_interview_asks_about_risk_for_critical_plan():
    """Plans classified CRITICAL must produce a q-risk question."""
    plan = _make_plan(phases=1, steps_per_phase=2, risk_level="CRITICAL")
    questions = _forge().generate_interview(plan)
    ids = [q.id for q in questions]
    assert "q-risk" in ids


def test_generate_interview_asks_about_coordination_for_many_agents():
    """Plans with 3+ distinct agents must produce a q-coordination question."""
    plan = _make_plan(
        phases=1,
        steps_per_phase=3,
        agent_names=["backend-engineer", "test-engineer", "architect"],
    )
    questions = _forge().generate_interview(plan)
    ids = [q.id for q in questions]
    assert "q-coordination" in ids


def test_generate_interview_no_coordination_for_two_agents():
    """Plans with exactly 2 distinct agents must NOT produce q-coordination."""
    plan = _make_plan(
        phases=1,
        steps_per_phase=2,
        agent_names=["backend-engineer", "test-engineer"],
    )
    questions = _forge().generate_interview(plan)
    ids = [q.id for q in questions]
    assert "q-coordination" not in ids


def test_generate_interview_asks_about_gates_when_none_defined():
    """Multi-phase plans with no gates must produce a q-gates question."""
    # has_gate=False (default) and 2 phases — satisfies both conditions
    plan = _make_plan(phases=2, steps_per_phase=1, has_gate=False)
    questions = _forge().generate_interview(plan)
    ids = [q.id for q in questions]
    assert "q-gates" in ids


def test_generate_interview_no_gates_question_when_gate_present():
    """Multi-phase plans that already have a gate must NOT produce q-gates."""
    plan = _make_plan(phases=2, steps_per_phase=1, has_gate=True)
    questions = _forge().generate_interview(plan)
    ids = [q.id for q in questions]
    assert "q-gates" not in ids


def test_generate_interview_includes_feedback_question():
    """Passing feedback must produce a q-feedback question."""
    plan = _make_plan(phases=1, steps_per_phase=1)
    questions = _forge().generate_interview(plan, feedback="please add error handling")
    ids = [q.id for q in questions]
    assert "q-feedback" in ids
    # The user's verbatim text should appear in the question
    q_feedback = next(q for q in questions if q.id == "q-feedback")
    assert "please add error handling" in q_feedback.question


def test_generate_interview_asks_scope_for_large_plan():
    """Plans with 3+ phases and no feedback must produce a q-scope question."""
    plan = _make_plan(phases=3, steps_per_phase=1)
    questions = _forge().generate_interview(plan)
    ids = [q.id for q in questions]
    assert "q-scope" in ids


def test_generate_interview_no_scope_when_feedback_provided():
    """When feedback is given, q-scope must NOT be generated (q-feedback takes the slot)."""
    plan = _make_plan(phases=3, steps_per_phase=1)
    questions = _forge().generate_interview(plan, feedback="reduce phases")
    ids = [q.id for q in questions]
    assert "q-scope" not in ids
    assert "q-feedback" in ids


def test_generate_interview_caps_at_five_questions():
    """Regardless of plan complexity, at most 5 questions are returned."""
    # Craft a plan that triggers every branch:
    # - no test step      → q-testing
    # - HIGH risk         → q-risk
    # - 3 distinct agents → q-coordination
    # - 2 phases, no gate → q-gates
    # - phase_count >= 3, no feedback → q-scope  (use 3 phases)
    plan = _make_plan(
        phases=3,
        steps_per_phase=3,
        has_gate=False,
        risk_level="HIGH",
        agent_names=["backend-engineer", "test-engineer", "architect"],
    )
    questions = _forge().generate_interview(plan)
    assert len(questions) <= 5


def test_generate_interview_skips_test_question_when_test_step_present():
    """A plan containing a step whose description includes 'test' must skip q-testing."""
    steps = [
        PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="Implement feature"),
        PlanStep(step_id="1.2", agent_name="test-engineer", task_description="Write unit tests"),
    ]
    phase = PlanPhase(phase_id=0, name="Phase 1", steps=steps)
    plan = MachinePlan(task_id="test-002", task_summary="Feature with tests", phases=[phase])
    questions = _forge().generate_interview(plan)
    ids = [q.id for q in questions]
    assert "q-testing" not in ids


def test_save_plan_returns_path(tmp_path):
    """save_plan writes files to the project path and returns the plan.json Path."""
    from agent_baton.models.pmo import PmoProject

    # Create a real project directory that ContextManager can write to
    project_dir = tmp_path / "my-project"
    project_dir.mkdir()
    project = PmoProject(
        project_id="my-project",
        name="My Project",
        path=str(project_dir),
        program="TEST",
    )

    planner = MagicMock()
    store = MagicMock()
    forge = ForgeSession(planner=planner, store=store, headless=HeadlessClaude(HeadlessConfig(claude_path="/nonexistent/claude")))
    plan = _make_plan(phases=1, steps_per_phase=1)

    result_path = forge.save_plan(plan, project)

    assert result_path is not None
    assert isinstance(result_path, Path)
    # The plan.json file must actually exist on disk
    assert result_path.exists()
