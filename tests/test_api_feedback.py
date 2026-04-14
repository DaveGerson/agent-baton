"""Tests for POST /api/v1/executions/{task_id}/feedback.

The feedback endpoint is a new sub-route on the existing executions router.
It wraps ``ExecutionEngine.record_feedback_result()`` and returns the next
batch of actions for the orchestrator to dispatch.

Contract:
  POST /api/v1/executions/{task_id}/feedback
  Body: { "phase_id": int, "question_id": str, "chosen_index": int }

  200 — feedback recorded, next_actions returned
  400 — invalid phase_id / question_id / chosen_index (ValueError from engine)
  404 — no active execution matching task_id
  422 — request body fails validation (missing required fields, wrong types)

Strategy: use the real ``create_app`` + TestClient pattern established by the
existing execution tests.  The test starts a real (in-memory) execution so the
404 guard is exercised naturally; for cases that reach deep into the engine, a
``MagicMock`` overrides the ``get_engine`` dependency.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.models.execution import ActionType, MachinePlan, PlanGate, PlanPhase, PlanStep  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def app(tmp_path: Path):
    return create_app(team_context_root=tmp_path)


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


def _make_test_plan(task_id: str = "fb-task") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Feedback test task",
        phases=[
            PlanPhase(
                phase_id=0,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="test-agent",
                        task_description="Do the work",
                    ),
                ],
                gate=PlanGate(gate_type="test", command="pytest"),
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Shared mock-engine fixture for tests that need to reach record_feedback_result
# ---------------------------------------------------------------------------


def _make_engine_mock(_next_actions: list | None = None) -> MagicMock:
    """Return a MagicMock shaped like ExecutionEngine for feedback tests."""
    from agent_baton.models.execution import ExecutionAction

    eng = MagicMock()
    # status() must return the active task_id so the 404 guard passes
    eng.status.return_value = {"task_id": "fb-task", "status": "running"}
    # record_feedback_result is a no-op by default (success)
    eng.record_feedback_result.return_value = None
    # next_actions() returns a minimal COMPLETE action
    complete_action = ExecutionAction(action_type=ActionType.COMPLETE, message="done")
    eng.next_actions.return_value = [complete_action]
    eng.next_action.return_value = complete_action
    return eng


# ===========================================================================
# POST /api/v1/executions/{task_id}/feedback — happy path
# ===========================================================================


class TestFeedbackEndpointHappyPath:
    """Tests for the nominal flow of the feedback endpoint."""

    @pytest.fixture()
    def engine_mock(self) -> MagicMock:
        return _make_engine_mock()

    @pytest.fixture()
    def client_with_mock(self, app, engine_mock: MagicMock) -> TestClient:
        from agent_baton.api.deps import get_engine
        app.dependency_overrides[get_engine] = lambda: engine_mock
        return TestClient(app)

    def test_returns_200(self, client_with_mock: TestClient) -> None:
        r = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 0, "question_id": "q1", "chosen_index": 0},
        )
        assert r.status_code == 200

    def test_response_contains_recorded_true(self, client_with_mock: TestClient) -> None:
        body = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 0, "question_id": "q1", "chosen_index": 0},
        ).json()
        assert body.get("recorded") is True

    def test_response_contains_next_actions(self, client_with_mock: TestClient) -> None:
        body = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 0, "question_id": "q1", "chosen_index": 0},
        ).json()
        assert "next_actions" in body
        assert isinstance(body["next_actions"], list)

    def test_engine_record_feedback_is_called_with_correct_args(
        self, client_with_mock: TestClient, engine_mock: MagicMock
    ) -> None:
        client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 2, "question_id": "clarity", "chosen_index": 1},
        )
        engine_mock.record_feedback_result.assert_called_once()
        call_kwargs = engine_mock.record_feedback_result.call_args
        # Verify key arguments are passed through correctly
        kw = call_kwargs.kwargs
        positional = call_kwargs.args
        phase_id_val = kw.get("phase_id", positional[0] if positional else None)
        question_id_val = kw.get("question_id", positional[1] if len(positional) > 1 else None)
        chosen_index_val = kw.get("chosen_index", positional[2] if len(positional) > 2 else None)
        assert phase_id_val == 2
        assert question_id_val == "clarity"
        assert chosen_index_val == 1

    def test_chosen_index_zero_is_valid(self, client_with_mock: TestClient) -> None:
        r = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 0, "question_id": "q1", "chosen_index": 0},
        )
        assert r.status_code == 200

    def test_large_phase_id_is_accepted(self, client_with_mock: TestClient) -> None:
        r = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 99, "question_id": "q9", "chosen_index": 0},
        )
        assert r.status_code == 200


# ===========================================================================
# 404 — no active execution
# ===========================================================================


class TestFeedbackEndpoint404:
    """The endpoint must return 404 when no active execution matches task_id."""

    def test_returns_404_for_nonexistent_task(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/executions/no-such-task/feedback",
            json={"phase_id": 0, "question_id": "q1", "chosen_index": 0},
        )
        assert r.status_code == 404

    def test_404_detail_mentions_task_id(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/executions/missing-task-xyz/feedback",
            json={"phase_id": 0, "question_id": "q1", "chosen_index": 0},
        )
        assert "missing-task-xyz" in r.json()["detail"]


# ===========================================================================
# 400 — invalid arguments (engine raises ValueError)
# ===========================================================================


class TestFeedbackEndpoint400:
    """Engine raises ValueError for unknown phase_id, question_id, or index."""

    @pytest.fixture()
    def engine_mock(self) -> MagicMock:
        eng = _make_engine_mock()
        eng.record_feedback_result.side_effect = ValueError("Phase 99 not found in plan.")
        return eng

    @pytest.fixture()
    def client_with_mock(self, app, engine_mock: MagicMock) -> TestClient:
        from agent_baton.api.deps import get_engine
        app.dependency_overrides[get_engine] = lambda: engine_mock
        return TestClient(app)

    def test_engine_value_error_returns_400(self, client_with_mock: TestClient) -> None:
        r = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 99, "question_id": "q1", "chosen_index": 0},
        )
        assert r.status_code == 400

    def test_400_detail_contains_error_message(self, client_with_mock: TestClient) -> None:
        r = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 99, "question_id": "q1", "chosen_index": 0},
        )
        assert "Phase 99" in r.json()["detail"]

    @pytest.fixture()
    def engine_mock_bad_question(self, app) -> MagicMock:  # app injected for fixture ordering
        eng = _make_engine_mock()
        eng.record_feedback_result.side_effect = ValueError(
            "Feedback question 'bad-q' not found on phase 0."
        )
        return eng

    def test_unknown_question_id_returns_400(self, app, engine_mock_bad_question: MagicMock) -> None:
        from agent_baton.api.deps import get_engine
        app.dependency_overrides[get_engine] = lambda: engine_mock_bad_question
        r = TestClient(app).post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 0, "question_id": "bad-q", "chosen_index": 0},
        )
        assert r.status_code == 400

    @pytest.fixture()
    def engine_mock_bad_index(self, app) -> MagicMock:  # app injected for fixture ordering
        eng = _make_engine_mock()
        eng.record_feedback_result.side_effect = ValueError(
            "chosen_index 9 out of range for question 'q1' with 2 options."
        )
        return eng

    def test_out_of_range_index_returns_400(self, app, engine_mock_bad_index: MagicMock) -> None:
        from agent_baton.api.deps import get_engine
        app.dependency_overrides[get_engine] = lambda: engine_mock_bad_index
        r = TestClient(app).post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 0, "question_id": "q1", "chosen_index": 9},
        )
        assert r.status_code == 400


# ===========================================================================
# 422 — request body validation failures
# ===========================================================================


class TestFeedbackEndpointValidation:
    """Pydantic validation must reject malformed request bodies."""

    @pytest.fixture()
    def engine_mock(self) -> MagicMock:
        return _make_engine_mock()

    @pytest.fixture()
    def client_with_mock(self, app, engine_mock: MagicMock) -> TestClient:
        from agent_baton.api.deps import get_engine
        app.dependency_overrides[get_engine] = lambda: engine_mock
        return TestClient(app)

    def test_missing_phase_id_returns_422(self, client_with_mock: TestClient) -> None:
        r = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"question_id": "q1", "chosen_index": 0},
        )
        assert r.status_code == 422

    def test_missing_question_id_returns_422(self, client_with_mock: TestClient) -> None:
        r = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 0, "chosen_index": 0},
        )
        assert r.status_code == 422

    def test_missing_chosen_index_returns_422(self, client_with_mock: TestClient) -> None:
        r = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 0, "question_id": "q1"},
        )
        assert r.status_code == 422

    def test_empty_body_returns_422(self, client_with_mock: TestClient) -> None:
        r = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={},
        )
        assert r.status_code == 422

    def test_negative_chosen_index_returns_422(self, client_with_mock: TestClient) -> None:
        # chosen_index must be >= 0
        r = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 0, "question_id": "q1", "chosen_index": -1},
        )
        assert r.status_code == 422

    def test_string_phase_id_returns_422(self, client_with_mock: TestClient) -> None:
        r = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": "zero", "question_id": "q1", "chosen_index": 0},
        )
        assert r.status_code == 422

    def test_string_chosen_index_returns_422(self, client_with_mock: TestClient) -> None:
        r = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 0, "question_id": "q1", "chosen_index": "first"},
        )
        assert r.status_code == 422

    def test_empty_question_id_returns_422(self, client_with_mock: TestClient) -> None:
        r = client_with_mock.post(
            "/api/v1/executions/fb-task/feedback",
            json={"phase_id": 0, "question_id": "", "chosen_index": 0},
        )
        assert r.status_code == 422
