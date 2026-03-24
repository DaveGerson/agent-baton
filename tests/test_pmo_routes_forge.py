"""Tests for new Forge API endpoints."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from agent_baton.api.server import create_app
from agent_baton.api.deps import get_forge_session, get_pmo_store
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.pmo import InterviewQuestion, PmoProject


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_plan_dict() -> dict:
    plan = MachinePlan(
        task_id="test-001",
        task_summary="Test plan",
        phases=[
            PlanPhase(
                phase_id=0,
                name="Phase 1",
                steps=[
                    PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="Do work")
                ],
            )
        ],
    )
    return plan.to_dict()


def _make_forge_mock(questions: list[InterviewQuestion] | None = None) -> MagicMock:
    """Return a ForgeSession mock with sensible defaults."""
    mock_forge = MagicMock()
    if questions is None:
        questions = [
            InterviewQuestion(
                id="q1",
                question="Testing?",
                context="No tests",
                answer_type="choice",
                choices=["yes", "no"],
            )
        ]
    mock_forge.generate_interview.return_value = questions

    # regenerate_plan returns a simple MachinePlan so the route can call .to_dict()
    mock_forge.regenerate_plan.return_value = MachinePlan(
        task_id="regen-001",
        task_summary="Regenerated plan",
        phases=[
            PlanPhase(
                phase_id=0,
                name="Phase 1",
                steps=[
                    PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="Work")
                ],
            )
        ],
    )
    return mock_forge


def _make_store_mock(project_path: str = "/tmp/proj") -> MagicMock:
    """Return a PmoStore mock whose get_project() returns a real PmoProject."""
    mock_store = MagicMock()
    mock_store.get_project.return_value = PmoProject(
        project_id="proj1",
        name="Test Project",
        path=project_path,
        program="TEST",
    )
    return mock_store


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    """Create the FastAPI app once per test."""
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def client_with_forge_mock(app):
    """TestClient whose forge dependency is replaced with a controllable mock.

    Uses FastAPI's ``dependency_overrides`` so that ``Depends(get_forge_session)``
    in the route handler receives the mock — no monkey-patching required.
    """
    mock_forge = _make_forge_mock()
    mock_store = _make_store_mock()

    app.dependency_overrides[get_forge_session] = lambda: mock_forge
    app.dependency_overrides[get_pmo_store] = lambda: mock_store

    with TestClient(app) as c:
        # Stash mocks on the client so tests can inspect calls.
        c.mock_forge = mock_forge  # type: ignore[attr-defined]
        c.mock_store = mock_store  # type: ignore[attr-defined]
        yield c

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Existing tests (kept exactly as before, now using dependency_overrides)
# ---------------------------------------------------------------------------

def test_forge_interview_returns_questions(app):
    """Happy path: valid plan returns a list of interview questions."""
    mock_forge = _make_forge_mock()
    app.dependency_overrides[get_forge_session] = lambda: mock_forge
    try:
        with TestClient(app) as c:
            resp = c.post("/api/v1/pmo/forge/interview", json={"plan": _make_plan_dict()})
        assert resp.status_code == 200
        data = resp.json()
        assert "questions" in data
        assert len(data["questions"]) >= 1
    finally:
        app.dependency_overrides.clear()


def test_ado_search_returns_mock_items(client):
    resp = client.get("/api/v1/pmo/ado/search?q=crew")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert isinstance(data["items"], list)


# ---------------------------------------------------------------------------
# New route tests
# ---------------------------------------------------------------------------

def test_forge_interview_invalid_plan_returns_error(app):
    """Posting an empty dict as plan must return 400 (handler-level validation)."""
    app.dependency_overrides[get_forge_session] = lambda: _make_forge_mock()
    try:
        with TestClient(app) as c:
            resp = c.post("/api/v1/pmo/forge/interview", json={"plan": {}})
        assert resp.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_forge_interview_with_feedback(app):
    """POSTing with a feedback field must pass it through to generate_interview."""
    mock_forge = _make_forge_mock(
        questions=[
            InterviewQuestion(
                id="q-feedback",
                question='You mentioned: "needs error handling". Can you elaborate?',
                context="Your feedback will be used to guide re-generation.",
                answer_type="text",
            )
        ]
    )
    app.dependency_overrides[get_forge_session] = lambda: mock_forge
    try:
        payload = {"plan": _make_plan_dict(), "feedback": "needs error handling"}
        with TestClient(app) as c:
            resp = c.post("/api/v1/pmo/forge/interview", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert "questions" in data
        # Confirm the forge was called with the feedback kwarg
        mock_forge.generate_interview.assert_called_once()
        call_kwargs = mock_forge.generate_interview.call_args
        assert call_kwargs.kwargs.get("feedback") == "needs error handling"
    finally:
        app.dependency_overrides.clear()


def test_ado_search_no_match_returns_empty(client):
    """Searching for a term that matches no mock items must return an empty list."""
    resp = client.get("/api/v1/pmo/ado/search?q=zzz_no_match_xyz")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["items"] == []


def test_forge_regenerate_happy_path(app):
    """POST /pmo/forge/regenerate with a valid payload must return a plan dict."""
    mock_forge = _make_forge_mock()
    mock_store = _make_store_mock()

    app.dependency_overrides[get_forge_session] = lambda: mock_forge
    app.dependency_overrides[get_pmo_store] = lambda: mock_store
    try:
        payload = {
            "project_id": "proj1",
            "description": "Build a new widget",
            "original_plan": _make_plan_dict(),
            "answers": [{"question_id": "q-testing", "answer": "Add unit tests"}],
        }

        with TestClient(app) as c:
            resp = c.post("/api/v1/pmo/forge/regenerate", json=payload)

        assert resp.status_code == 201
        data = resp.json()
        assert "task_id" in data
        # Confirm forge.regenerate_plan was called with the correct answers
        mock_forge.regenerate_plan.assert_called_once()
        call_args = mock_forge.regenerate_plan.call_args
        answers = call_args.kwargs.get("answers", [])
        assert len(answers) == 1
        assert answers[0].question_id == "q-testing"
        assert answers[0].answer == "Add unit tests"
    finally:
        app.dependency_overrides.clear()


def test_forge_regenerate_project_not_found(app):
    """POST /pmo/forge/regenerate with unknown project_id must return 404."""
    mock_forge = _make_forge_mock()
    mock_store = MagicMock()
    mock_store.get_project.return_value = None  # project does not exist

    app.dependency_overrides[get_forge_session] = lambda: mock_forge
    app.dependency_overrides[get_pmo_store] = lambda: mock_store
    try:
        payload = {
            "project_id": "nonexistent",
            "description": "Build something",
            "original_plan": _make_plan_dict(),
            "answers": [],
        }
        with TestClient(app) as c:
            resp = c.post("/api/v1/pmo/forge/regenerate", json=payload)
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()
