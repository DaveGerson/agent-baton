"""Tests for POST /api/v1/plans and GET /api/v1/plans/{plan_id} endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app(tmp_path: Path):
    return create_app(team_context_root=tmp_path)


@pytest.fixture()
def client(app):
    return TestClient(app)


def make_test_plan(task_id: str = "test-task") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Test task",
        phases=[
            PlanPhase(
                phase_id=0,
                name="Phase 1",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="test-agent",
                        task_description="Do stuff",
                    ),
                ],
                gate=PlanGate(gate_type="test", command="pytest"),
            ),
        ],
    )


# ===========================================================================
# POST /api/v1/plans
# ===========================================================================


class TestCreatePlan:
    def test_valid_description_returns_201(self, client: TestClient) -> None:
        r = client.post("/api/v1/plans", json={"description": "Build a new feature"})
        assert r.status_code == 201

    def test_response_contains_plan_id(self, client: TestClient) -> None:
        r = client.post("/api/v1/plans", json={"description": "Build a new feature"})
        body = r.json()
        assert "plan_id" in body
        assert isinstance(body["plan_id"], str)
        assert len(body["plan_id"]) > 0

    def test_response_contains_phases(self, client: TestClient) -> None:
        r = client.post("/api/v1/plans", json={"description": "Build a new feature"})
        body = r.json()
        assert "phases" in body
        assert isinstance(body["phases"], list)
        assert len(body["phases"]) > 0

    def test_response_contains_task_summary(self, client: TestClient) -> None:
        r = client.post("/api/v1/plans", json={"description": "Build a new feature"})
        body = r.json()
        assert "task_summary" in body

    def test_empty_description_returns_422(self, client: TestClient) -> None:
        r = client.post("/api/v1/plans", json={"description": ""})
        assert r.status_code == 422

    def test_missing_description_returns_422(self, client: TestClient) -> None:
        r = client.post("/api/v1/plans", json={})
        assert r.status_code == 422

    def test_plan_schema_fields_present(self, client: TestClient) -> None:
        r = client.post("/api/v1/plans", json={"description": "Implement the thing"})
        body = r.json()
        expected_fields = {"plan_id", "task_summary", "phases", "total_steps", "agents"}
        assert expected_fields.issubset(set(body.keys()))

    def test_total_steps_matches_phases(self, client: TestClient) -> None:
        r = client.post("/api/v1/plans", json={"description": "Implement the thing"})
        body = r.json()
        computed = sum(len(p["steps"]) for p in body["phases"])
        assert body["total_steps"] == computed


# ===========================================================================
# GET /api/v1/plans/{plan_id}
# ===========================================================================


class TestGetPlan:
    def test_no_active_plan_returns_404(self, client: TestClient) -> None:
        r = client.get("/api/v1/plans/nonexistent-plan-id")
        assert r.status_code == 404

    def test_404_detail_mentions_plan_id(self, client: TestClient) -> None:
        r = client.get("/api/v1/plans/my-missing-plan")
        assert "my-missing-plan" in r.json()["detail"]

    def test_active_plan_returns_200(self, client: TestClient) -> None:
        plan = make_test_plan()
        client.post("/api/v1/executions", json={"plan": plan.to_dict()})
        r = client.get(f"/api/v1/plans/{plan.task_id}")
        assert r.status_code == 200

    def test_active_plan_returns_correct_id(self, client: TestClient) -> None:
        plan = make_test_plan(task_id="my-plan-123")
        client.post("/api/v1/executions", json={"plan": plan.to_dict()})
        r = client.get("/api/v1/plans/my-plan-123")
        body = r.json()
        assert body["plan_id"] == "my-plan-123"
