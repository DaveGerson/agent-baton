"""Tests for the Learning Automation API endpoints.

Endpoints covered:

  GET    /api/v1/learn/issues              — list issues (with optional filters)
  GET    /api/v1/learn/issues/{issue_id}   — single issue detail with evidence
  POST   /api/v1/learn/analyze             — trigger analysis cycle
  POST   /api/v1/learn/issues/{issue_id}/apply   — apply fix for an issue
  PATCH  /api/v1/learn/issues/{issue_id}   — update issue status

Implementation notes (from reading learn.py)
--------------------------------------------
- list_issues and get_issue go through ``engine._ledger`` directly, so the
  test needs a real ``LearningEngine`` pointed at a temp SQLite file.
- analyze() and apply() are called on the engine object, so a mock works.
- PATCH returns a ``LearningIssueResponse`` dict (no "updated" sentinel).
- ``POST /apply`` response is ``ApplyLearningFixResponse``:
  ``{issue_id, resolution, status}``.  There is no "applied" key.
- ``POST /analyze`` response is ``LearningAnalyzeResponse``:
  ``{candidates, proposed_count}`` (no bare "count" key).

Strategy
--------
- Routes that touch the ledger: override ``get_learning_engine`` with a real
  ``LearningEngine`` seeded with known records via ``LearningLedger``.
- Routes that call engine methods directly (analyze, apply): override
  ``get_learning_engine`` with a ``MagicMock``.
- Filter-validation tests: the route does *not* validate enum values at the
  HTTP layer (no Query Enum type annotation), so invalid values return 200
  with an empty list rather than 422.  Tests document the actual behaviour.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.learn.engine import LearningEngine  # noqa: E402
from agent_baton.core.learn.ledger import LearningLedger  # noqa: E402
from agent_baton.models.learning import (  # noqa: E402
    LearningEvidence,
    LearningIssue,
    VALID_ISSUE_TYPES,
    VALID_STATUSES,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _seed_issue(
    ledger: LearningLedger,
    *,
    issue_type: str = "routing_mismatch",
    severity: str = "medium",
    target: str = "some-agent",
    title: str = "Test issue",
) -> LearningIssue:
    """Insert one issue into the ledger and return it."""
    ev = LearningEvidence(
        timestamp=_utcnow(),
        source_task_id="task-seed",
        detail="Seed evidence",
        data={},
    )
    return ledger.record_issue(
        issue_type=issue_type,
        target=target,
        severity=severity,
        title=title,
        evidence=ev,
    )


def _real_engine(tmp_path: Path) -> LearningEngine:
    """Return a real LearningEngine backed by a temp SQLite file."""
    _db_path = tmp_path / "baton.db"
    return LearningEngine(team_context_root=tmp_path)


def _mock_engine() -> MagicMock:
    """Return a MagicMock shaped like LearningEngine with safe defaults."""
    engine = MagicMock()
    engine.analyze.return_value = []
    return engine


# ---------------------------------------------------------------------------
# Base app fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def base_app(tmp_path: Path):
    return create_app(team_context_root=tmp_path)


# ===========================================================================
# GET /api/v1/learn/issues — list issues
# ===========================================================================


class TestListLearningIssues:
    """Tests for GET /api/v1/learn/issues."""

    @pytest.fixture()
    def engine_root(self, tmp_path: Path) -> Path:
        return tmp_path

    @pytest.fixture()
    def engine(self, engine_root: Path) -> LearningEngine:
        return _real_engine(engine_root)

    @pytest.fixture()
    def _seeded_ledger(self, engine: LearningEngine) -> LearningLedger:
        ledger = engine._ledger  # noqa: SLF001
        _seed_issue(ledger, issue_type="routing_mismatch", severity="medium")
        _seed_issue(ledger, issue_type="agent_degradation", severity="high")
        return ledger

    @pytest.fixture()
    def client(self, base_app, engine: LearningEngine, _seeded_ledger: LearningLedger) -> TestClient:
        from agent_baton.api.deps import get_learning_engine
        base_app.dependency_overrides[get_learning_engine] = lambda: engine
        return TestClient(base_app)

    def test_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/v1/learn/issues")
        assert r.status_code == 200

    def test_response_contains_issues_list(self, client: TestClient) -> None:
        body = client.get("/api/v1/learn/issues").json()
        assert "issues" in body
        assert isinstance(body["issues"], list)

    def test_response_contains_count_field(self, client: TestClient) -> None:
        body = client.get("/api/v1/learn/issues").json()
        assert "count" in body
        assert body["count"] == len(body["issues"])

    def test_count_matches_seeded_issues(self, client: TestClient) -> None:
        body = client.get("/api/v1/learn/issues").json()
        assert body["count"] == 2

    def test_each_issue_has_required_fields(self, client: TestClient) -> None:
        body = client.get("/api/v1/learn/issues").json()
        required = {"issue_id", "issue_type", "severity", "status", "title", "target"}
        for issue in body["issues"]:
            assert required.issubset(set(issue.keys())), (
                f"Issue missing fields: {required - set(issue.keys())}"
            )

    def test_empty_list_when_no_issues(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        eng = _real_engine(empty_dir)
        _app = create_app(team_context_root=empty_dir)
        from agent_baton.api.deps import get_learning_engine
        _app.dependency_overrides[get_learning_engine] = lambda: eng
        r = TestClient(_app).get("/api/v1/learn/issues")
        assert r.status_code == 200
        assert r.json()["count"] == 0


class TestListLearningIssuesFiltering:
    """Tests for query parameter filtering on GET /api/v1/learn/issues."""

    @pytest.fixture()
    def engine(self, tmp_path: Path) -> LearningEngine:
        eng = _real_engine(tmp_path)
        ledger = eng._ledger  # noqa: SLF001
        _seed_issue(ledger, issue_type="routing_mismatch", severity="medium", target="agent-a")
        _seed_issue(ledger, issue_type="agent_degradation", severity="high", target="agent-b")
        _seed_issue(ledger, issue_type="knowledge_gap", severity="low", target="agent-c")
        return eng

    @pytest.fixture()
    def client(self, base_app, engine: LearningEngine) -> TestClient:
        from agent_baton.api.deps import get_learning_engine
        base_app.dependency_overrides[get_learning_engine] = lambda: engine
        return TestClient(base_app)

    def test_issue_type_filter_narrows_results(self, client: TestClient) -> None:
        body = client.get("/api/v1/learn/issues?issue_type=routing_mismatch").json()
        assert body["count"] == 1
        assert body["issues"][0]["issue_type"] == "routing_mismatch"

    def test_severity_filter_narrows_results(self, client: TestClient) -> None:
        body = client.get("/api/v1/learn/issues?severity=high").json()
        assert body["count"] == 1
        assert body["issues"][0]["severity"] == "high"

    def test_status_filter_open_returns_all_seeded(self, client: TestClient) -> None:
        # All seeded issues start as "open"
        body = client.get("/api/v1/learn/issues?status=open").json()
        assert body["count"] == 3

    def test_status_filter_resolved_returns_empty(self, client: TestClient) -> None:
        body = client.get("/api/v1/learn/issues?status=resolved").json()
        assert body["count"] == 0

    def test_unknown_issue_type_returns_empty_not_error(self, client: TestClient) -> None:
        # The route passes filters directly to the ledger — no enum validation
        # at the HTTP layer, so unknown values silently match nothing.
        r = client.get("/api/v1/learn/issues?issue_type=not_a_real_type")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_unknown_severity_returns_empty_not_error(self, client: TestClient) -> None:
        r = client.get("/api/v1/learn/issues?severity=catastrophic")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_all_valid_issue_type_values_accepted(self, client: TestClient) -> None:
        for issue_type in VALID_ISSUE_TYPES:
            r = client.get(f"/api/v1/learn/issues?issue_type={issue_type}")
            assert r.status_code == 200, f"issue_type={issue_type!r} should be accepted"

    def test_combined_filters_are_anded(self, client: TestClient) -> None:
        # routing_mismatch+medium matches 1; routing_mismatch+high matches 0
        body = client.get(
            "/api/v1/learn/issues?issue_type=routing_mismatch&severity=medium"
        ).json()
        assert body["count"] == 1

        body2 = client.get(
            "/api/v1/learn/issues?issue_type=routing_mismatch&severity=high"
        ).json()
        assert body2["count"] == 0


# ===========================================================================
# GET /api/v1/learn/issues/{issue_id} — get single issue
# ===========================================================================


class TestGetLearningIssue:
    """Tests for GET /api/v1/learn/issues/{issue_id}."""

    @pytest.fixture()
    def engine(self, tmp_path: Path) -> LearningEngine:
        return _real_engine(tmp_path)

    @pytest.fixture()
    def issue(self, engine: LearningEngine) -> LearningIssue:
        return _seed_issue(
            engine._ledger,  # noqa: SLF001
            issue_type="routing_mismatch",
            title="Routing mismatch for python stack",
        )

    @pytest.fixture()
    def client(self, base_app, engine: LearningEngine, issue) -> TestClient:
        from agent_baton.api.deps import get_learning_engine
        base_app.dependency_overrides[get_learning_engine] = lambda: engine
        return TestClient(base_app)

    def test_known_issue_returns_200(self, client: TestClient, issue: LearningIssue) -> None:
        r = client.get(f"/api/v1/learn/issues/{issue.issue_id}")
        assert r.status_code == 200

    def test_response_contains_issue_id(self, client: TestClient, issue: LearningIssue) -> None:
        body = client.get(f"/api/v1/learn/issues/{issue.issue_id}").json()
        assert body["issue_id"] == issue.issue_id

    def test_response_contains_title(self, client: TestClient, issue: LearningIssue) -> None:
        body = client.get(f"/api/v1/learn/issues/{issue.issue_id}").json()
        assert body["title"] == "Routing mismatch for python stack"

    def test_response_contains_evidence_list(
        self, client: TestClient, issue: LearningIssue
    ) -> None:
        body = client.get(f"/api/v1/learn/issues/{issue.issue_id}").json()
        assert "evidence" in body
        assert isinstance(body["evidence"], list)
        # Seeded with one evidence entry
        assert len(body["evidence"]) >= 1

    def test_evidence_entry_has_required_fields(
        self, client: TestClient, issue: LearningIssue
    ) -> None:
        body = client.get(f"/api/v1/learn/issues/{issue.issue_id}").json()
        ev = body["evidence"][0]
        assert {"timestamp", "source_task_id", "detail", "data"}.issubset(set(ev.keys()))

    def test_unknown_issue_returns_404(self, client: TestClient) -> None:
        r = client.get("/api/v1/learn/issues/no-such-issue-id")
        assert r.status_code == 404

    def test_404_detail_mentions_issue_id(self, client: TestClient) -> None:
        r = client.get("/api/v1/learn/issues/my-missing-issue")
        assert "my-missing-issue" in r.json()["detail"]


# ===========================================================================
# POST /api/v1/learn/analyze — trigger analysis
# ===========================================================================


class TestTriggerAnalysis:
    """Tests for POST /api/v1/learn/analyze."""

    @pytest.fixture()
    def engine(self) -> MagicMock:
        eng = _mock_engine()
        eng.analyze.return_value = [
            LearningIssue(
                issue_id=str(uuid.uuid4()),
                issue_type="routing_mismatch",
                severity="medium",
                status="proposed",
                title="Routing mismatch",
                target="some-agent",
                occurrence_count=3,
                first_seen=_utcnow(),
                last_seen=_utcnow(),
            ),
        ]
        return eng

    @pytest.fixture()
    def client(self, base_app, engine: MagicMock) -> TestClient:
        from agent_baton.api.deps import get_learning_engine
        base_app.dependency_overrides[get_learning_engine] = lambda: engine
        return TestClient(base_app)

    def test_returns_200(self, client: TestClient) -> None:
        r = client.post("/api/v1/learn/analyze")
        assert r.status_code == 200

    def test_response_contains_candidates_list(self, client: TestClient) -> None:
        body = client.post("/api/v1/learn/analyze").json()
        assert "candidates" in body
        assert isinstance(body["candidates"], list)

    def test_response_contains_proposed_count(self, client: TestClient) -> None:
        body = client.post("/api/v1/learn/analyze").json()
        assert "proposed_count" in body
        assert isinstance(body["proposed_count"], int)

    def test_proposed_count_matches_proposed_status(self, client: TestClient) -> None:
        body = client.post("/api/v1/learn/analyze").json()
        # Engine returns one "proposed" issue
        assert body["proposed_count"] == 1

    def test_candidates_reflect_engine_return(self, client: TestClient) -> None:
        body = client.post("/api/v1/learn/analyze").json()
        assert len(body["candidates"]) == 1
        assert body["candidates"][0]["issue_type"] == "routing_mismatch"

    def test_engine_analyze_is_called_once(
        self, client: TestClient, engine: MagicMock
    ) -> None:
        client.post("/api/v1/learn/analyze")
        engine.analyze.assert_called_once()

    def test_empty_candidates_when_engine_returns_nothing(self, base_app) -> None:
        eng = _mock_engine()
        eng.analyze.return_value = []
        from agent_baton.api.deps import get_learning_engine
        base_app.dependency_overrides[get_learning_engine] = lambda: eng
        body = TestClient(base_app).post("/api/v1/learn/analyze").json()
        assert body["candidates"] == []
        assert body["proposed_count"] == 0

    def test_engine_error_returns_500(self, base_app) -> None:
        eng = _mock_engine()
        eng.analyze.side_effect = RuntimeError("database unavailable")
        from agent_baton.api.deps import get_learning_engine
        base_app.dependency_overrides[get_learning_engine] = lambda: eng
        # raise_server_exceptions=False so the test client returns 500
        # instead of re-raising the RuntimeError.
        r = TestClient(base_app, raise_server_exceptions=False).post("/api/v1/learn/analyze")
        assert r.status_code == 500


# ===========================================================================
# POST /api/v1/learn/issues/{issue_id}/apply — apply fix
# ===========================================================================


class TestApplyLearningFix:
    """Tests for POST /api/v1/learn/issues/{issue_id}/apply.

    The apply endpoint calls ``engine.apply(issue_id, resolution_type=...)``.
    ValueError from the engine is mapped to 404.
    """

    ISSUE_ID = "deadbeef-dead-beef-dead-beefdeadbeef"

    @pytest.fixture()
    def engine(self) -> MagicMock:
        eng = _mock_engine()
        eng.apply.side_effect = lambda iid, resolution_type="auto": (
            f"Applied routing fix for {iid}"
            if iid == self.ISSUE_ID
            else (_ for _ in ()).throw(ValueError(f"Issue not found: {iid}"))
        )
        return eng

    @pytest.fixture()
    def client(self, base_app, engine: MagicMock) -> TestClient:
        from agent_baton.api.deps import get_learning_engine
        base_app.dependency_overrides[get_learning_engine] = lambda: engine
        return TestClient(base_app)

    def test_known_issue_returns_200(self, client: TestClient) -> None:
        # The endpoint requires an explicit body (even empty {}) because
        # FastAPI treats a request model with all-default fields as still
        # requiring a body to be present.
        r = client.post(f"/api/v1/learn/issues/{self.ISSUE_ID}/apply", json={})
        assert r.status_code == 200

    def test_response_contains_issue_id(self, client: TestClient) -> None:
        body = client.post(f"/api/v1/learn/issues/{self.ISSUE_ID}/apply", json={}).json()
        assert body["issue_id"] == self.ISSUE_ID

    def test_response_contains_resolution_string(self, client: TestClient) -> None:
        body = client.post(f"/api/v1/learn/issues/{self.ISSUE_ID}/apply", json={}).json()
        assert "resolution" in body
        assert isinstance(body["resolution"], str)
        assert len(body["resolution"]) > 0

    def test_response_contains_status_field(self, client: TestClient) -> None:
        body = client.post(f"/api/v1/learn/issues/{self.ISSUE_ID}/apply", json={}).json()
        assert "status" in body
        assert body["status"] == "applied"

    def test_resolution_type_default_is_human(
        self, client: TestClient, engine: MagicMock
    ) -> None:
        # Empty body {} triggers Pydantic default resolution_type="human"
        client.post(f"/api/v1/learn/issues/{self.ISSUE_ID}/apply", json={})
        engine.apply.assert_called_once()
        call_kwargs = engine.apply.call_args.kwargs
        positional = engine.apply.call_args.args
        resolution_type = call_kwargs.get("resolution_type") or (
            positional[1] if len(positional) > 1 else None
        )
        assert resolution_type == "human"

    def test_resolution_type_can_be_overridden(
        self, client: TestClient, engine: MagicMock
    ) -> None:
        client.post(
            f"/api/v1/learn/issues/{self.ISSUE_ID}/apply",
            json={"resolution_type": "interview"},
        )
        call_kwargs = engine.apply.call_args.kwargs
        positional = engine.apply.call_args.args
        resolution_type = call_kwargs.get("resolution_type") or (
            positional[1] if len(positional) > 1 else None
        )
        assert resolution_type == "interview"

    def test_unknown_issue_returns_404(self, client: TestClient) -> None:
        r = client.post("/api/v1/learn/issues/no-such-issue/apply", json={})
        assert r.status_code == 404

    def test_404_detail_mentions_issue_id(self, client: TestClient) -> None:
        r = client.post("/api/v1/learn/issues/missing-issue-xyz/apply", json={})
        assert "missing-issue-xyz" in r.json()["detail"]

    def test_invalid_resolution_type_returns_422(self, client: TestClient) -> None:
        r = client.post(
            f"/api/v1/learn/issues/{self.ISSUE_ID}/apply",
            json={"resolution_type": "magic"},
        )
        assert r.status_code == 422


# ===========================================================================
# PATCH /api/v1/learn/issues/{issue_id} — update status
# ===========================================================================


class TestUpdateLearningIssueStatus:
    """Tests for PATCH /api/v1/learn/issues/{issue_id}.

    The PATCH route uses ``engine._ledger`` directly and returns a refreshed
    ``LearningIssueResponse`` dict (no "updated" sentinel key).
    The route validates the status against ``VALID_STATUSES`` itself (400),
    but only after Pydantic accepts the request body (status has no pattern
    constraint on the Pydantic model, so any string passes Pydantic; 400
    comes from the route logic after injection).
    """

    @pytest.fixture()
    def engine(self, tmp_path: Path) -> LearningEngine:
        return _real_engine(tmp_path)

    @pytest.fixture()
    def issue(self, engine: LearningEngine) -> LearningIssue:
        return _seed_issue(engine._ledger, title="Status update test issue")  # noqa: SLF001

    @pytest.fixture()
    def client(self, base_app, engine: LearningEngine, issue) -> TestClient:
        from agent_baton.api.deps import get_learning_engine
        base_app.dependency_overrides[get_learning_engine] = lambda: engine
        return TestClient(base_app)

    def test_valid_status_update_returns_200(
        self, client: TestClient, issue: LearningIssue
    ) -> None:
        r = client.patch(
            f"/api/v1/learn/issues/{issue.issue_id}",
            json={"status": "investigating"},
        )
        assert r.status_code == 200

    def test_response_contains_issue_id(
        self, client: TestClient, issue: LearningIssue
    ) -> None:
        body = client.patch(
            f"/api/v1/learn/issues/{issue.issue_id}",
            json={"status": "wontfix"},
        ).json()
        assert body["issue_id"] == issue.issue_id

    def test_response_reflects_new_status(
        self, client: TestClient, issue: LearningIssue
    ) -> None:
        body = client.patch(
            f"/api/v1/learn/issues/{issue.issue_id}",
            json={"status": "resolved"},
        ).json()
        assert body["status"] == "resolved"

    def test_all_valid_statuses_are_accepted(
        self, engine: LearningEngine, base_app
    ) -> None:
        from agent_baton.api.deps import get_learning_engine
        base_app.dependency_overrides[get_learning_engine] = lambda: engine
        c = TestClient(base_app)
        for status in VALID_STATUSES:
            issue = _seed_issue(engine._ledger, target=f"agent-{status}")  # noqa: SLF001
            r = c.patch(
                f"/api/v1/learn/issues/{issue.issue_id}",
                json={"status": status},
            )
            assert r.status_code == 200, f"status={status!r} should be accepted"

    def test_invalid_status_returns_400(
        self, client: TestClient, issue: LearningIssue
    ) -> None:
        # The route validates status after Pydantic; returns 400 not 422
        r = client.patch(
            f"/api/v1/learn/issues/{issue.issue_id}",
            json={"status": "not_a_real_status"},
        )
        assert r.status_code == 400

    def test_missing_status_returns_422(
        self, client: TestClient, issue: LearningIssue
    ) -> None:
        r = client.patch(f"/api/v1/learn/issues/{issue.issue_id}", json={})
        assert r.status_code == 422

    def test_unknown_issue_returns_404(self, client: TestClient) -> None:
        r = client.patch(
            "/api/v1/learn/issues/no-such-issue",
            json={"status": "wontfix"},
        )
        assert r.status_code == 404

    def test_404_detail_mentions_issue_id(self, client: TestClient) -> None:
        r = client.patch(
            "/api/v1/learn/issues/ghost-issue-id",
            json={"status": "wontfix"},
        )
        assert "ghost-issue-id" in r.json()["detail"]

    def test_optional_resolution_field_is_persisted(
        self, client: TestClient, issue: LearningIssue, engine: LearningEngine
    ) -> None:
        client.patch(
            f"/api/v1/learn/issues/{issue.issue_id}",
            json={"status": "resolved", "resolution": "Fixed upstream"},
        )
        refreshed = engine._ledger.get_issue(issue.issue_id)  # noqa: SLF001
        assert refreshed is not None
        assert refreshed.resolution == "Fixed upstream"

    def test_optional_resolution_type_field_accepted(
        self, client: TestClient, issue: LearningIssue
    ) -> None:
        r = client.patch(
            f"/api/v1/learn/issues/{issue.issue_id}",
            json={"status": "resolved", "resolution_type": "human"},
        )
        assert r.status_code == 200

    def test_invalid_resolution_type_returns_422(
        self, client: TestClient, issue: LearningIssue
    ) -> None:
        r = client.patch(
            f"/api/v1/learn/issues/{issue.issue_id}",
            json={"status": "resolved", "resolution_type": "magic"},
        )
        assert r.status_code == 422
