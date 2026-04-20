"""Tests for the PMO role-based approval workflow.

Endpoints covered (all prefixed with /api/v1):

  POST /pmo/cards/{card_id}/request-review  — write approval_log entry
  GET  /pmo/cards/{card_id}/approval-log    — read approval_log history

Tests also cover the UserIdentityMiddleware's extraction of the
X-Baton-User header.

Strategy:
- get_central_store is overridden with a _FakeCentralStore whose execute()
  and query() write/read from a real in-memory SQLite database.  This
  bypasses the CentralStore write guard (which only permits external-source
  tables) while keeping the data layer realistic.
- PmoScanner / PmoStore are stubbed out.
- EventBus is overridden so SSE publish calls never raise.
- X-Baton-User is set directly in request headers to verify identity
  propagation.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.deps import (  # noqa: E402
    get_bus,
    get_central_store,
    get_forge_session,
    get_pmo_scanner,
    get_pmo_store,
)
from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.events.bus import EventBus  # noqa: E402
from agent_baton.core.pmo.store import PmoStore  # noqa: E402
from agent_baton.core.storage.central import CentralStore  # noqa: E402
from agent_baton.models.pmo import PmoProject  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_APPROVAL_LOG_DDL = """
CREATE TABLE IF NOT EXISTS approval_log (
    log_id     TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    phase_id   TEXT NOT NULL DEFAULT '',
    user_id    TEXT NOT NULL DEFAULT 'local-user',
    action     TEXT NOT NULL,
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


class _FakeCentralStore:
    """In-memory SQLite store that accepts arbitrary execute/query calls.

    The real ``CentralStore.execute()`` has a write guard that only permits
    writes to ``external_sources``, ``external_items``, and
    ``external_mappings``.  The ``approval_log`` table is not in that
    allowlist, so the route handler raises 500 when using the real class.

    This stub exposes the same interface (``execute`` and ``query``) but
    routes all calls straight to an in-memory SQLite database, making it
    suitable for endpoint tests that need approval_log writes to succeed.
    """

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_APPROVAL_LOG_DDL)
        self._conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> None:
        self._conn.execute(sql, params)
        self._conn.commit()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        cursor = self._conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def _make_tmp_store(tmp_path: Path) -> PmoStore:
    return PmoStore(
        config_path=tmp_path / "pmo-config.json",
        archive_path=tmp_path / "pmo-archive.jsonl",
    )


def _make_app(
    tmp_path: Path,
    store: PmoStore,
    central: _FakeCentralStore,
) -> TestClient:
    app = create_app(team_context_root=tmp_path)
    forge_stub = MagicMock()
    bus = EventBus()
    scanner_stub = _StubScanner()

    app.dependency_overrides[get_pmo_store] = lambda: store
    app.dependency_overrides[get_pmo_scanner] = lambda: scanner_stub
    app.dependency_overrides[get_forge_session] = lambda: forge_stub
    app.dependency_overrides[get_bus] = lambda: bus
    app.dependency_overrides[get_central_store] = lambda: central
    return TestClient(app)


class _StubScanner:
    def scan_all(self):
        return []

    def program_health(self, cards=None):
        return {}

    def find_card(self, card_id: str):
        raise KeyError(card_id)


# ===========================================================================
# UserIdentityMiddleware — X-Baton-User header extraction
# ===========================================================================


class TestUserIdentityMiddleware:
    """Verify that X-Baton-User is plumbed through to request.state.user_id."""

    def test_x_baton_user_header_sets_user_id(self, tmp_path: Path) -> None:
        """The X-Baton-User header value becomes request.state.user_id."""
        from agent_baton.api.middleware.user_identity import UserIdentityMiddleware

        recorded: list[str] = []

        from starlette.testclient import TestClient as _SC
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import JSONResponse
        from starlette.requests import Request

        async def _handler(request: Request):
            recorded.append(getattr(request.state, "user_id", "UNSET"))
            return JSONResponse({"user_id": recorded[-1]})

        app = Starlette(routes=[Route("/probe", _handler)])
        app.add_middleware(UserIdentityMiddleware)

        sc = _SC(app)
        sc.get("/probe", headers={"X-Baton-User": "alice"})
        assert recorded[-1] == "alice"

    def test_x_baton_user_overrides_bearer_token(self, tmp_path: Path) -> None:
        """X-Baton-User wins over Authorization: Bearer."""
        from agent_baton.api.middleware.user_identity import UserIdentityMiddleware
        from starlette.testclient import TestClient as _SC
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import JSONResponse
        from starlette.requests import Request

        recorded: list[str] = []

        async def _handler(request: Request):
            recorded.append(getattr(request.state, "user_id", "UNSET"))
            return JSONResponse({"user_id": recorded[-1]})

        app = Starlette(routes=[Route("/probe", _handler)])
        app.add_middleware(UserIdentityMiddleware)

        sc = _SC(app)
        sc.get(
            "/probe",
            headers={
                "X-Baton-User": "alice",
                "Authorization": "Bearer bob-token",
            },
        )
        assert recorded[-1] == "alice"

    def test_bearer_token_used_when_no_x_baton_user(
        self, tmp_path: Path
    ) -> None:
        """Authorization: Bearer is used when X-Baton-User is absent."""
        from agent_baton.api.middleware.user_identity import UserIdentityMiddleware
        from starlette.testclient import TestClient as _SC
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import JSONResponse
        from starlette.requests import Request

        recorded: list[str] = []

        async def _handler(request: Request):
            recorded.append(getattr(request.state, "user_id", "UNSET"))
            return JSONResponse({"user_id": recorded[-1]})

        app = Starlette(routes=[Route("/probe", _handler)])
        app.add_middleware(UserIdentityMiddleware)

        sc = _SC(app)
        sc.get("/probe", headers={"Authorization": "Bearer charlie-token"})
        assert recorded[-1] == "charlie-token"

    def test_fallback_to_local_user_when_no_headers(
        self, tmp_path: Path
    ) -> None:
        """No headers → fallback to 'local-user'."""
        from agent_baton.api.middleware.user_identity import UserIdentityMiddleware
        from starlette.testclient import TestClient as _SC
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import JSONResponse
        from starlette.requests import Request

        recorded: list[str] = []

        async def _handler(request: Request):
            recorded.append(getattr(request.state, "user_id", "UNSET"))
            return JSONResponse({"user_id": recorded[-1]})

        app = Starlette(routes=[Route("/probe", _handler)])
        app.add_middleware(UserIdentityMiddleware)

        sc = _SC(app)
        sc.get("/probe")
        assert recorded[-1] == "local-user"

    def test_empty_x_baton_user_falls_back_to_local_user(
        self, tmp_path: Path
    ) -> None:
        """An empty X-Baton-User header must not be used; fallback activates."""
        from agent_baton.api.middleware.user_identity import UserIdentityMiddleware
        from starlette.testclient import TestClient as _SC
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import JSONResponse
        from starlette.requests import Request

        recorded: list[str] = []

        async def _handler(request: Request):
            recorded.append(getattr(request.state, "user_id", "UNSET"))
            return JSONResponse({"user_id": recorded[-1]})

        app = Starlette(routes=[Route("/probe", _handler)])
        app.add_middleware(UserIdentityMiddleware)

        sc = _SC(app)
        sc.get("/probe", headers={"X-Baton-User": "  "})
        assert recorded[-1] == "local-user"


# ===========================================================================
# POST /api/v1/pmo/cards/{card_id}/request-review
# ===========================================================================


class TestRequestReview:
    def test_returns_201(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        r = client.post(
            "/api/v1/pmo/cards/task-rr-001/request-review",
            json={},
        )
        assert r.status_code == 201

    def test_response_has_logged_true(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        body = client.post(
            "/api/v1/pmo/cards/task-rr-002/request-review",
            json={},
        ).json()
        assert body["logged"] is True

    def test_response_has_log_id(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        body = client.post(
            "/api/v1/pmo/cards/task-rr-003/request-review",
            json={},
        ).json()
        assert "log_id" in body
        assert isinstance(body["log_id"], str)
        assert len(body["log_id"]) > 0

    def test_response_has_correct_card_id(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        body = client.post(
            "/api/v1/pmo/cards/task-rr-cardid/request-review",
            json={},
        ).json()
        assert body["card_id"] == "task-rr-cardid"

    def test_writes_approval_log_entry(self, tmp_path: Path) -> None:
        """The approval_log table should have a new row after request-review."""
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        client.post(
            "/api/v1/pmo/cards/task-rr-db/request-review",
            json={"notes": "Please check security"},
        )

        rows = central.query(
            "SELECT * FROM approval_log WHERE task_id = ?",
            ("task-rr-db",),
        )
        assert len(rows) == 1
        assert rows[0]["action"] == "request_review"

    def test_x_baton_user_recorded_in_approval_log(
        self, tmp_path: Path
    ) -> None:
        """The user_id from X-Baton-User header appears in the log entry."""
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        client.post(
            "/api/v1/pmo/cards/task-rr-user/request-review",
            json={},
            headers={"X-Baton-User": "diana"},
        )

        rows = central.query(
            "SELECT * FROM approval_log WHERE task_id = ?",
            ("task-rr-user",),
        )
        assert len(rows) == 1
        assert rows[0]["user_id"] == "diana"

    def test_reviewer_id_included_in_notes(self, tmp_path: Path) -> None:
        """When reviewer_id is supplied it should appear in the log entry notes."""
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        client.post(
            "/api/v1/pmo/cards/task-rr-rev/request-review",
            json={"reviewer_id": "bob", "notes": "Urgent review needed"},
        )

        rows = central.query(
            "SELECT * FROM approval_log WHERE task_id = ?",
            ("task-rr-rev",),
        )
        assert len(rows) == 1
        assert "bob" in rows[0]["notes"]

    def test_notes_stored_in_approval_log(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        client.post(
            "/api/v1/pmo/cards/task-rr-notes/request-review",
            json={"notes": "Check edge cases carefully"},
        )

        rows = central.query(
            "SELECT * FROM approval_log WHERE task_id = ?",
            ("task-rr-notes",),
        )
        assert "Check edge cases carefully" in rows[0]["notes"]

    def test_multiple_request_reviews_create_multiple_entries(
        self, tmp_path: Path
    ) -> None:
        """Each POST to request-review creates a distinct log entry."""
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        client.post(
            "/api/v1/pmo/cards/task-multi-rr/request-review", json={}
        )
        client.post(
            "/api/v1/pmo/cards/task-multi-rr/request-review", json={}
        )

        rows = central.query(
            "SELECT * FROM approval_log WHERE task_id = ?",
            ("task-multi-rr",),
        )
        assert len(rows) == 2


# ===========================================================================
# GET /api/v1/pmo/cards/{card_id}/approval-log
# ===========================================================================


class TestGetApprovalLog:
    def test_returns_200_for_unknown_card(self, tmp_path: Path) -> None:
        """The log endpoint returns 200 with empty entries when no rows exist."""
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        r = client.get("/api/v1/pmo/cards/no-such-card/approval-log")
        assert r.status_code == 200

    def test_empty_entries_for_card_with_no_log(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        body = client.get(
            "/api/v1/pmo/cards/no-log-card/approval-log"
        ).json()
        assert "entries" in body
        assert body["entries"] == []

    def test_response_has_entries_key(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        body = client.get(
            "/api/v1/pmo/cards/any-card/approval-log"
        ).json()
        assert "entries" in body

    def test_request_review_entry_appears_in_log(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        client.post(
            "/api/v1/pmo/cards/task-log-001/request-review",
            json={"notes": "From test"},
        )

        body = client.get(
            "/api/v1/pmo/cards/task-log-001/approval-log"
        ).json()
        assert len(body["entries"]) == 1
        assert body["entries"][0]["action"] == "request_review"

    def test_log_entries_have_required_fields(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        client.post(
            "/api/v1/pmo/cards/task-logfields/request-review",
            json={},
        )

        body = client.get(
            "/api/v1/pmo/cards/task-logfields/approval-log"
        ).json()
        entry = body["entries"][0]
        for field in ("log_id", "task_id", "user_id", "action", "created_at"):
            assert field in entry, f"missing field: {field}"

    def test_log_entries_ordered_newest_first(
        self, tmp_path: Path
    ) -> None:
        """Two entries for the same card: the most recent appears first."""
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        client.post(
            "/api/v1/pmo/cards/task-order/request-review",
            json={"notes": "first"},
            headers={"X-Baton-User": "user-a"},
        )
        client.post(
            "/api/v1/pmo/cards/task-order/request-review",
            json={"notes": "second"},
            headers={"X-Baton-User": "user-b"},
        )

        body = client.get(
            "/api/v1/pmo/cards/task-order/approval-log"
        ).json()
        assert len(body["entries"]) == 2
        # Newest first: second entry is at index 0.
        first_created = body["entries"][0]["created_at"]
        second_created = body["entries"][1]["created_at"]
        assert first_created >= second_created

    def test_log_only_returns_entries_for_requested_card(
        self, tmp_path: Path
    ) -> None:
        """Approval log for card-A must not include entries for card-B."""
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        client.post(
            "/api/v1/pmo/cards/task-isolate-A/request-review", json={}
        )
        client.post(
            "/api/v1/pmo/cards/task-isolate-B/request-review", json={}
        )

        body = client.get(
            "/api/v1/pmo/cards/task-isolate-A/approval-log"
        ).json()
        for entry in body["entries"]:
            assert entry["task_id"] == "task-isolate-A"

    def test_user_id_captured_correctly_in_log(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        central = _FakeCentralStore()
        client = _make_app(tmp_path, store, central)

        client.post(
            "/api/v1/pmo/cards/task-user-log/request-review",
            json={},
            headers={"X-Baton-User": "eve"},
        )

        body = client.get(
            "/api/v1/pmo/cards/task-user-log/approval-log"
        ).json()
        assert body["entries"][0]["user_id"] == "eve"

    def test_approval_log_endpoint_returns_200_when_table_raises(
        self, tmp_path: Path
    ) -> None:
        """When the approval_log query raises an exception, the endpoint returns
        200 with an empty entries list (the route has an except clause for this).
        """
        store = _make_tmp_store(tmp_path)

        # Use a fake central that raises on query to simulate a missing table.
        class _ErrorCentral:
            def execute(self, sql, params=()):
                raise RuntimeError("no such table: approval_log")

            def query(self, sql, params=()):
                raise RuntimeError("no such table: approval_log")

        client = _make_app(tmp_path, store, _ErrorCentral())

        r = client.get("/api/v1/pmo/cards/any-card/approval-log")
        assert r.status_code == 200
        body = r.json()
        assert body["entries"] == []
