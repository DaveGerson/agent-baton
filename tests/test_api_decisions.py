"""Tests for decision management endpoints:

GET  /api/v1/decisions
GET  /api/v1/decisions/{request_id}
POST /api/v1/decisions/{request_id}/resolve
"""
from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.runtime.decisions import DecisionManager  # noqa: E402
from agent_baton.models.decision import DecisionRequest  # noqa: E402


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


@pytest.fixture()
def decision_manager(tmp_root: Path) -> DecisionManager:
    """Return a DecisionManager scoped to the same tmp directory as the app."""
    return DecisionManager(decisions_dir=tmp_root / "decisions")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_decision(
    mgr: DecisionManager,
    task_id: str = "t1",
    decision_type: str = "gate_approval",
    summary: str = "Review phase 1",
) -> DecisionRequest:
    req = DecisionRequest.create(task_id=task_id, decision_type=decision_type, summary=summary)
    mgr.request(req)
    return req


# ===========================================================================
# GET /api/v1/decisions — list decisions
# ===========================================================================


class TestListDecisions:
    def test_empty_list_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/v1/decisions")
        assert r.status_code == 200

    def test_empty_list_count_is_zero(self, client: TestClient) -> None:
        body = client.get("/api/v1/decisions").json()
        assert body["count"] == 0
        assert body["decisions"] == []

    def test_returns_created_decision(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        _create_decision(decision_manager)
        body = client.get("/api/v1/decisions").json()
        assert body["count"] == 1

    def test_count_matches_decisions_list_length(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        _create_decision(decision_manager, task_id="t1")
        _create_decision(decision_manager, task_id="t2")
        body = client.get("/api/v1/decisions").json()
        assert body["count"] == len(body["decisions"])

    def test_status_pending_filter_returns_only_pending(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        _create_decision(decision_manager, task_id="t2")  # second pending
        # Resolve the first one.
        decision_manager.resolve(req.request_id, "approve")

        body = client.get("/api/v1/decisions?status=pending").json()
        for d in body["decisions"]:
            assert d["status"] == "pending"

    def test_status_resolved_filter_returns_only_resolved(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        _create_decision(decision_manager, task_id="t2")  # remains pending
        decision_manager.resolve(req.request_id, "approve")

        body = client.get("/api/v1/decisions?status=resolved").json()
        assert body["count"] == 1
        assert body["decisions"][0]["status"] == "resolved"

    def test_task_id_filter_returns_only_matching_task(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        _create_decision(decision_manager, task_id="task-a")
        _create_decision(decision_manager, task_id="task-b")

        body = client.get("/api/v1/decisions?task_id=task-a").json()
        assert body["count"] == 1
        assert body["decisions"][0]["task_id"] == "task-a"

    def test_task_id_filter_no_match_returns_empty(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        _create_decision(decision_manager, task_id="real-task")
        body = client.get("/api/v1/decisions?task_id=nonexistent-task").json()
        assert body["count"] == 0


# ===========================================================================
# GET /api/v1/decisions/{request_id}
# ===========================================================================


class TestGetDecision:
    def test_returns_200_for_existing_decision(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        r = client.get(f"/api/v1/decisions/{req.request_id}")
        assert r.status_code == 200

    def test_returns_correct_decision_fields(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager, task_id="t1", summary="Check this")
        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        assert body["request_id"] == req.request_id
        assert body["task_id"] == "t1"
        assert body["summary"] == "Check this"
        assert body["status"] == "pending"

    def test_returns_options_list(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        assert isinstance(body["options"], list)
        assert len(body["options"]) > 0

    def test_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.get("/api/v1/decisions/no-such-id")
        assert r.status_code == 404

    def test_404_detail_mentions_request_id(self, client: TestClient) -> None:
        r = client.get("/api/v1/decisions/missing-req-id")
        assert "missing-req-id" in r.json()["detail"]


# ===========================================================================
# POST /api/v1/decisions/{request_id}/resolve
# ===========================================================================


class TestResolveDecision:
    def test_resolve_returns_200(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        r = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve"},
        )
        assert r.status_code == 200

    def test_resolve_returns_resolved_true(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        body = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve"},
        ).json()
        assert body["resolved"] is True

    def test_resolve_with_rationale_succeeds(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        r = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "reject", "rationale": "Not ready yet"},
        )
        assert r.status_code == 200

    def test_resolve_with_custom_resolved_by(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        r = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve", "resolved_by": "automated-policy"},
        )
        assert r.status_code == 200

    def test_resolve_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/decisions/no-such-id/resolve",
            json={"option": "approve"},
        )
        assert r.status_code == 404

    def test_resolve_already_resolved_returns_400(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        # First resolution.
        client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve"},
        )
        # Second resolution — should be rejected.
        r = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "reject"},
        )
        assert r.status_code == 400

    def test_resolve_already_resolved_detail_mentions_status(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve"},
        )
        r = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "reject"},
        )
        assert "resolved" in r.json()["detail"]

    def test_resolve_updates_decision_status(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={"option": "approve"},
        )
        updated = decision_manager.get(req.request_id)
        assert updated is not None
        assert updated.status == "resolved"

    def test_missing_option_returns_422(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = _create_decision(decision_manager)
        r = client.post(
            f"/api/v1/decisions/{req.request_id}/resolve",
            json={},
        )
        assert r.status_code == 422
