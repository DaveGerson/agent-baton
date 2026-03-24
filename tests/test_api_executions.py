"""Tests for execution lifecycle endpoints:

POST   /api/v1/executions
GET    /api/v1/executions/{task_id}
POST   /api/v1/executions/{task_id}/record
POST   /api/v1/executions/{task_id}/gate
POST   /api/v1/executions/{task_id}/complete
DELETE /api/v1/executions/{task_id}
"""
from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
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


def start_execution(client: TestClient, task_id: str = "test-task") -> dict:
    """Helper: start an execution with an inline plan and return the response body."""
    plan = make_test_plan(task_id=task_id)
    r = client.post("/api/v1/executions", json={"plan": plan.to_dict()})
    assert r.status_code == 201, r.text
    return r.json()


# ===========================================================================
# POST /api/v1/executions — start execution
# ===========================================================================


class TestStartExecution:
    def test_inline_plan_returns_201(self, client: TestClient) -> None:
        plan = make_test_plan()
        r = client.post("/api/v1/executions", json={"plan": plan.to_dict()})
        assert r.status_code == 201

    def test_response_contains_execution_and_next_actions(self, client: TestClient) -> None:
        body = start_execution(client)
        assert "execution" in body
        assert "next_actions" in body

    def test_execution_task_id_matches_plan(self, client: TestClient) -> None:
        plan = make_test_plan(task_id="my-unique-task")
        r = client.post("/api/v1/executions", json={"plan": plan.to_dict()})
        body = r.json()
        assert body["execution"]["task_id"] == "my-unique-task"

    def test_next_actions_is_non_empty_list(self, client: TestClient) -> None:
        body = start_execution(client)
        assert isinstance(body["next_actions"], list)
        assert len(body["next_actions"]) >= 1

    def test_first_action_has_action_type(self, client: TestClient) -> None:
        body = start_execution(client)
        first = body["next_actions"][0]
        assert "action_type" in first
        assert first["action_type"] in ("dispatch", "gate", "complete", "wait", "failed")

    def test_missing_plan_source_returns_422(self, client: TestClient) -> None:
        r = client.post("/api/v1/executions", json={})
        assert r.status_code == 422

    def test_both_plan_id_and_plan_returns_422(self, client: TestClient) -> None:
        plan = make_test_plan()
        r = client.post(
            "/api/v1/executions",
            json={"plan": plan.to_dict(), "plan_id": "some-id"},
        )
        assert r.status_code == 422

    def test_invalid_plan_dict_returns_400(self, client: TestClient) -> None:
        r = client.post("/api/v1/executions", json={"plan": {"broken": "yes"}})
        assert r.status_code == 400

    def test_execution_status_is_running(self, client: TestClient) -> None:
        body = start_execution(client)
        assert body["execution"]["status"] == "running"


# ===========================================================================
# GET /api/v1/executions/{task_id}
# ===========================================================================


class TestGetExecution:
    def test_returns_404_when_no_active_execution(self, client: TestClient) -> None:
        r = client.get("/api/v1/executions/no-such-task")
        assert r.status_code == 404

    def test_404_detail_mentions_task_id(self, client: TestClient) -> None:
        r = client.get("/api/v1/executions/my-missing-task")
        assert "my-missing-task" in r.json()["detail"]

    def test_returns_200_for_active_execution(self, client: TestClient) -> None:
        start_execution(client, task_id="active-task")
        r = client.get("/api/v1/executions/active-task")
        assert r.status_code == 200

    def test_execution_schema_fields_present(self, client: TestClient) -> None:
        start_execution(client, task_id="active-task")
        body = client.get("/api/v1/executions/active-task").json()
        expected = {"task_id", "status", "steps_completed", "steps_remaining", "gates_passed"}
        assert expected.issubset(set(body.keys()))


# ===========================================================================
# POST /api/v1/executions/{task_id}/record
# ===========================================================================


class TestRecordStep:
    def test_record_returns_200(self, client: TestClient) -> None:
        start_execution(client, task_id="t1")
        r = client.post(
            "/api/v1/executions/t1/record",
            json={"step_id": "1.1", "agent": "test-agent", "status": "complete"},
        )
        assert r.status_code == 200

    def test_record_returns_recorded_true(self, client: TestClient) -> None:
        start_execution(client, task_id="t1")
        r = client.post(
            "/api/v1/executions/t1/record",
            json={"step_id": "1.1", "agent": "test-agent", "status": "complete"},
        )
        assert r.json()["recorded"] is True

    def test_record_returns_next_actions(self, client: TestClient) -> None:
        start_execution(client, task_id="t1")
        r = client.post(
            "/api/v1/executions/t1/record",
            json={"step_id": "1.1", "agent": "test-agent", "status": "complete"},
        )
        assert "next_actions" in r.json()

    def test_record_on_nonexistent_task_returns_404(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/executions/no-such-task/record",
            json={"step_id": "1.1", "agent": "test-agent", "status": "complete"},
        )
        assert r.status_code == 404

    def test_record_with_optional_fields(self, client: TestClient) -> None:
        start_execution(client, task_id="t1")
        r = client.post(
            "/api/v1/executions/t1/record",
            json={
                "step_id": "1.1",
                "agent": "test-agent",
                "status": "complete",
                "output_summary": "All done",
                "tokens": 500,
                "duration_ms": 1200,
            },
        )
        assert r.status_code == 200


# ===========================================================================
# POST /api/v1/executions/{task_id}/gate
# ===========================================================================


class TestRecordGate:
    def _setup(self, client: TestClient, task_id: str = "gt") -> None:
        """Start execution and complete the step so the gate is reachable."""
        start_execution(client, task_id=task_id)
        client.post(
            f"/api/v1/executions/{task_id}/record",
            json={"step_id": "1.1", "agent": "test-agent", "status": "complete"},
        )

    def test_gate_pass_returns_200(self, client: TestClient) -> None:
        self._setup(client)
        r = client.post("/api/v1/executions/gt/gate", json={"phase_id": 0, "result": "pass"})
        assert r.status_code == 200

    def test_gate_returns_recorded_true(self, client: TestClient) -> None:
        self._setup(client)
        r = client.post("/api/v1/executions/gt/gate", json={"phase_id": 0, "result": "pass"})
        assert r.json()["recorded"] is True

    def test_gate_with_notes(self, client: TestClient) -> None:
        self._setup(client)
        r = client.post(
            "/api/v1/executions/gt/gate",
            json={"phase_id": 0, "result": "pass_with_notes", "notes": "Minor style issues"},
        )
        assert r.status_code == 200

    def test_gate_on_nonexistent_task_returns_404(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/executions/no-such/gate",
            json={"phase_id": 0, "result": "pass"},
        )
        assert r.status_code == 404

    def test_gate_invalid_result_value_returns_422(self, client: TestClient) -> None:
        start_execution(client, task_id="gtv")
        r = client.post(
            "/api/v1/executions/gtv/gate",
            json={"phase_id": 0, "result": "invalid_value"},
        )
        assert r.status_code == 422


# ===========================================================================
# POST /api/v1/executions/{task_id}/complete
# ===========================================================================


class TestCompleteExecution:
    def _run_to_completion(self, client: TestClient, task_id: str = "ct") -> None:
        start_execution(client, task_id=task_id)
        client.post(
            f"/api/v1/executions/{task_id}/record",
            json={"step_id": "1.1", "agent": "test-agent", "status": "complete"},
        )
        client.post(
            f"/api/v1/executions/{task_id}/gate",
            json={"phase_id": 0, "result": "pass"},
        )

    def test_complete_returns_200(self, client: TestClient) -> None:
        self._run_to_completion(client)
        r = client.post("/api/v1/executions/ct/complete")
        assert r.status_code == 200

    def test_complete_response_contains_task_id(self, client: TestClient) -> None:
        self._run_to_completion(client)
        body = client.post("/api/v1/executions/ct/complete").json()
        assert body["task_id"] == "ct"

    def test_complete_status_field_is_complete(self, client: TestClient) -> None:
        self._run_to_completion(client)
        body = client.post("/api/v1/executions/ct/complete").json()
        assert body["status"] == "complete"

    def test_complete_on_nonexistent_task_returns_404(self, client: TestClient) -> None:
        r = client.post("/api/v1/executions/no-such/complete")
        assert r.status_code == 404


# ===========================================================================
# DELETE /api/v1/executions/{task_id}
# ===========================================================================


class TestCancelExecution:
    def test_cancel_active_execution_returns_200(self, client: TestClient) -> None:
        start_execution(client, task_id="del-task")
        r = client.delete("/api/v1/executions/del-task")
        assert r.status_code == 200

    def test_cancel_returns_cancelled_true(self, client: TestClient) -> None:
        start_execution(client, task_id="del-task")
        body = client.delete("/api/v1/executions/del-task").json()
        assert body["cancelled"] is True

    def test_cancel_response_contains_task_id(self, client: TestClient) -> None:
        start_execution(client, task_id="del-task")
        body = client.delete("/api/v1/executions/del-task").json()
        assert body["task_id"] == "del-task"

    def test_cancel_nonexistent_task_returns_404(self, client: TestClient) -> None:
        r = client.delete("/api/v1/executions/no-such-task")
        assert r.status_code == 404
