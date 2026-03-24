"""Tests for new Forge API endpoints."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from agent_baton.api.server import create_app
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.pmo import InterviewQuestion


def _make_plan_dict():
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


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def test_forge_interview_returns_questions(client):
    with patch("agent_baton.api.routes.pmo.get_forge_session") as mock_dep:
        mock_forge = MagicMock()
        mock_forge.generate_interview.return_value = [
            InterviewQuestion(id="q1", question="Testing?", context="No tests", answer_type="choice", choices=["yes", "no"]),
        ]
        mock_dep.return_value = mock_forge

        resp = client.post("/api/v1/pmo/forge/interview", json={
            "plan": _make_plan_dict(),
        })

    assert resp.status_code == 200
    data = resp.json()
    assert "questions" in data
    assert len(data["questions"]) >= 1


def test_ado_search_returns_mock_items(client):
    resp = client.get("/api/v1/pmo/ado/search?q=crew")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert isinstance(data["items"], list)
