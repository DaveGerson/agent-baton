"""Tests for observability endpoints:

GET /api/v1/dashboard
GET /api/v1/traces/{task_id}
GET /api/v1/usage
"""
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
def tmp_root(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def app(tmp_root: Path):
    return create_app(team_context_root=tmp_root)


@pytest.fixture()
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def make_test_plan(task_id: str = "obs-task") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Test observability task",
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
# GET /api/v1/dashboard
# ===========================================================================


class TestDashboardEndpoint:
    def test_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/v1/dashboard")
        assert r.status_code == 200

    def test_response_has_dashboard_markdown(self, client: TestClient) -> None:
        body = client.get("/api/v1/dashboard").json()
        assert "dashboard_markdown" in body
        assert isinstance(body["dashboard_markdown"], str)

    def test_dashboard_markdown_is_non_empty(self, client: TestClient) -> None:
        body = client.get("/api/v1/dashboard").json()
        assert len(body["dashboard_markdown"]) > 0

    def test_response_has_metrics_field(self, client: TestClient) -> None:
        body = client.get("/api/v1/dashboard").json()
        assert "metrics" in body
        assert isinstance(body["metrics"], dict)


# ===========================================================================
# GET /api/v1/traces/{task_id}
# ===========================================================================


class TestTraceEndpoint:
    def test_unknown_task_returns_404(self, client: TestClient) -> None:
        r = client.get("/api/v1/traces/no-such-task")
        assert r.status_code == 404

    def test_404_detail_mentions_task_id(self, client: TestClient) -> None:
        r = client.get("/api/v1/traces/my-missing-task")
        assert "my-missing-task" in r.json()["detail"]

    def test_completed_task_returns_200(self, client: TestClient) -> None:
        plan = make_test_plan()
        r = client.post("/api/v1/executions", json={"plan": plan.to_dict()})
        task_id = r.json()["execution"]["task_id"]

        # Drive the execution to completion so a trace is written.
        client.post(
            f"/api/v1/executions/{task_id}/record",
            json={"step_id": "1.1", "agent": "test-agent", "status": "complete"},
        )
        client.post(
            f"/api/v1/executions/{task_id}/gate",
            json={"phase_id": 0, "result": "pass"},
        )
        client.post(f"/api/v1/executions/{task_id}/complete")

        r = client.get(f"/api/v1/traces/{task_id}")
        assert r.status_code == 200

    def test_completed_task_trace_has_task_id(self, client: TestClient) -> None:
        plan = make_test_plan(task_id="trace-test-task")
        client.post("/api/v1/executions", json={"plan": plan.to_dict()})
        client.post(
            "/api/v1/executions/trace-test-task/record",
            json={"step_id": "1.1", "agent": "test-agent", "status": "complete"},
        )
        client.post(
            "/api/v1/executions/trace-test-task/gate",
            json={"phase_id": 0, "result": "pass"},
        )
        client.post("/api/v1/executions/trace-test-task/complete")

        body = client.get("/api/v1/traces/trace-test-task").json()
        assert body["task_id"] == "trace-test-task"


# ===========================================================================
# GET /api/v1/usage
# ===========================================================================


class TestUsageEndpoint:
    def test_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/v1/usage")
        assert r.status_code == 200

    def test_empty_records_when_no_usage_data(self, client: TestClient) -> None:
        body = client.get("/api/v1/usage").json()
        assert body["records"] == []

    def test_summary_present_in_response(self, client: TestClient) -> None:
        body = client.get("/api/v1/usage").json()
        assert "summary" in body
        assert isinstance(body["summary"], dict)

    def test_summary_total_tasks_zero_when_no_data(self, client: TestClient) -> None:
        body = client.get("/api/v1/usage").json()
        assert body["summary"]["total_tasks"] == 0

    def test_since_query_param_accepted(self, client: TestClient) -> None:
        r = client.get("/api/v1/usage?since=2024-01-01T00:00:00Z")
        assert r.status_code == 200

    def test_agent_query_param_accepted(self, client: TestClient) -> None:
        r = client.get("/api/v1/usage?agent=my-agent")
        assert r.status_code == 200
