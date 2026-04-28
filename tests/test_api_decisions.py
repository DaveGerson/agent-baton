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
# Path traversal guard on context_files (bd-90c4)
# ===========================================================================


class TestContextFilesPathTraversal:
    """Regression coverage for bd-90c4.

    The GET /decisions/{id} route enriches responses with the inline contents
    of every path listed in ``context_files``. Prior to bd-90c4 those reads
    were unconstrained, so any caller that could place an absolute or
    ``..``-prefixed path into the decision payload could read arbitrary
    files from the API host. The fix constrains reads to paths that resolve
    inside ``DecisionManager.safe_read_root`` (the team-context root).
    """

    def test_absolute_path_outside_root_is_silently_omitted(
        self, client: TestClient, decision_manager: DecisionManager, tmp_path: Path
    ) -> None:
        outside = tmp_path.parent / "secret.txt"  # one level above safe_root
        outside.write_text("TOP-SECRET", encoding="utf-8")
        req = DecisionRequest.create(
            task_id="t1",
            decision_type="gate_approval",
            summary="x",
            context_files=[str(outside)],
        )
        decision_manager.request(req)

        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        assert "TOP-SECRET" not in (body.get("context_file_contents") or {}).values()
        assert str(outside) not in (body.get("context_file_contents") or {})

    def test_etc_passwd_style_absolute_read_is_blocked(
        self, client: TestClient, decision_manager: DecisionManager
    ) -> None:
        req = DecisionRequest.create(
            task_id="t1",
            decision_type="gate_approval",
            summary="x",
            context_files=["/etc/passwd"],
        )
        decision_manager.request(req)

        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        contents = body.get("context_file_contents") or {}
        assert "/etc/passwd" not in contents
        assert all("root:" not in v for v in contents.values())

    def test_dotdot_traversal_is_blocked(
        self, client: TestClient, decision_manager: DecisionManager, tmp_path: Path
    ) -> None:
        outside = tmp_path.parent / "leak.txt"
        outside.write_text("LEAKED", encoding="utf-8")
        traversal = str(tmp_path / ".." / outside.name)
        req = DecisionRequest.create(
            task_id="t1",
            decision_type="gate_approval",
            summary="x",
            context_files=[traversal],
        )
        decision_manager.request(req)

        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        contents = body.get("context_file_contents") or {}
        assert "LEAKED" not in contents.values()

    def test_symlink_escaping_root_is_blocked(
        self, client: TestClient, decision_manager: DecisionManager, tmp_path: Path
    ) -> None:
        outside = tmp_path.parent / "secret-target.txt"
        outside.write_text("CLASSIFIED", encoding="utf-8")
        link = tmp_path / "evil-symlink"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unsupported on this platform")

        req = DecisionRequest.create(
            task_id="t1",
            decision_type="gate_approval",
            summary="x",
            context_files=[str(link)],
        )
        decision_manager.request(req)

        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        contents = body.get("context_file_contents") or {}
        assert "CLASSIFIED" not in contents.values()

    def test_path_inside_root_is_returned(
        self, client: TestClient, decision_manager: DecisionManager, tmp_path: Path
    ) -> None:
        # Sanity: legitimate reads inside the root still work.
        legit = tmp_path / "context.md"
        legit.write_text("hello world", encoding="utf-8")

        req = DecisionRequest.create(
            task_id="t1",
            decision_type="gate_approval",
            summary="x",
            context_files=[str(legit)],
        )
        decision_manager.request(req)

        body = client.get(f"/api/v1/decisions/{req.request_id}").json()
        contents = body.get("context_file_contents") or {}
        assert contents.get(str(legit)) == "hello world"

    def test_manager_with_no_safe_root_refuses_all_reads(
        self, tmp_path: Path
    ) -> None:
        # If a caller forgets to wire safe_read_root, the route MUST refuse
        # every read rather than fall back to the old unconstrained behaviour.
        from agent_baton.api.routes.decisions import _safe_read_context_file

        legit = tmp_path / "in.txt"
        legit.write_text("inside", encoding="utf-8")

        assert _safe_read_context_file(str(legit), safe_root=None) is None


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
