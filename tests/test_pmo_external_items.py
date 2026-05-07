"""Tests for the external items PMO endpoints and planner annotation.

Part A — Backend API:
    GET /api/v1/pmo/external-items
    GET /api/v1/pmo/external-items/{item_id}/mappings

Part C — Planner annotation:
    IntelligentPlanner._fetch_external_annotations
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from agent_baton.api.server import create_app
from agent_baton.api.deps import get_central_store
from agent_baton.core.storage.central import CentralStore
from agent_baton.core.storage.schema import CENTRAL_SCHEMA_DDL, SCHEMA_VERSION
from agent_baton.core.storage.connection import ConnectionManager


# ---------------------------------------------------------------------------
# Helpers — build a minimal in-memory CentralStore with test data
# ---------------------------------------------------------------------------

def _make_central_db(tmp_path: Path) -> Path:
    """Create a central.db with one external source, two items, and one mapping."""
    db_path = tmp_path / "central.db"
    mgr = ConnectionManager(db_path)
    mgr.configure_schema(CENTRAL_SCHEMA_DDL, SCHEMA_VERSION)
    conn = mgr.get_connection()

    conn.execute(
        "INSERT OR REPLACE INTO external_sources "
        "(source_id, source_type, display_name, config, last_synced, enabled) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("src-1", "jira", "My Jira", "{}", "2024-01-01T00:00:00Z", 1),
    )
    conn.execute(
        "INSERT OR REPLACE INTO external_items "
        "(source_id, external_id, item_type, title, description, state, "
        " assigned_to, priority, parent_id, tags, url, fetched_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "src-1", "JIRA-42", "story",
            "Implement login page", "Allow users to authenticate",
            "In Progress", "alice", "1", "", json.dumps(["auth", "frontend"]),
            "https://jira.example.com/browse/JIRA-42",
            "2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z",
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO external_items "
        "(source_id, external_id, item_type, title, description, state, "
        " assigned_to, priority, parent_id, tags, url, fetched_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "src-1", "JIRA-43", "bug",
            "Fix logout crash", "Crash on logout",
            "Open", "bob", "2", "", json.dumps([]),
            "", "2024-01-01T00:00:00Z", "2024-01-03T00:00:00Z",
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO external_mappings "
        "(source_id, external_id, project_id, task_id, mapping_type, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("src-1", "JIRA-42", "proj-alpha", "task-abc", "implements", "2024-01-01T00:00:00Z"),
    )
    conn.commit()
    mgr.close()
    return db_path


def _store_from_db(db_path: Path) -> CentralStore:
    return CentralStore(db_path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    return _make_central_db(tmp_path)


@pytest.fixture
def central_store(tmp_db):
    store = _store_from_db(tmp_db)
    yield store
    store.close()


@pytest.fixture
def app_with_store(central_store):
    """FastAPI test app with the CentralStore dependency overridden."""
    application = create_app()
    application.dependency_overrides[get_central_store] = lambda: central_store
    yield application
    application.dependency_overrides.clear()


@pytest.fixture
def client(app_with_store):
    with TestClient(app_with_store) as c:
        yield c


# ---------------------------------------------------------------------------
# Part A — GET /api/v1/pmo/external-items
# ---------------------------------------------------------------------------

class TestListExternalItems:
    def test_returns_all_items_when_no_filter(self, client):
        resp = client.get("/api/v1/pmo/external-items")
        assert resp.status_code == 200
        items = resp.json()
        assert isinstance(items, list)
        assert len(items) == 2

    def test_items_have_required_fields(self, client):
        resp = client.get("/api/v1/pmo/external-items")
        item = resp.json()[0]
        for field in ("id", "source_id", "external_id", "item_type", "title",
                      "state", "tags", "source_type"):
            assert field in item, f"Missing field: {field}"

    def test_source_type_populated_from_join(self, client):
        resp = client.get("/api/v1/pmo/external-items")
        for item in resp.json():
            assert item["source_type"] == "jira"

    def test_tags_deserialised_as_list(self, client):
        resp = client.get("/api/v1/pmo/external-items")
        items = {i["external_id"]: i for i in resp.json()}
        assert items["JIRA-42"]["tags"] == ["auth", "frontend"]
        assert items["JIRA-43"]["tags"] == []

    def test_filter_by_source_type(self, client):
        resp = client.get("/api/v1/pmo/external-items?source=jira")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_filter_by_unknown_source_returns_400(self, client):
        resp = client.get("/api/v1/pmo/external-items?source=unknown")
        assert resp.status_code == 400

    def test_filter_by_project_id_returns_mapped_only(self, client):
        resp = client.get("/api/v1/pmo/external-items?project_id=proj-alpha")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["external_id"] == "JIRA-42"

    def test_filter_by_project_id_no_match_returns_empty(self, client):
        resp = client.get("/api/v1/pmo/external-items?project_id=no-such-project")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_filter_by_status(self, client):
        resp = client.get("/api/v1/pmo/external-items?status=Open")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["external_id"] == "JIRA-43"

    def test_empty_when_no_central_db(self, tmp_path):
        """Endpoint returns [] gracefully when central.db is absent."""
        # CentralStore pointing at a new (empty) db — external_items table exists
        # but has no rows.
        store = CentralStore(tmp_path / "empty.db")
        application = create_app()
        application.dependency_overrides[get_central_store] = lambda: store
        try:
            with TestClient(application) as c:
                resp = c.get("/api/v1/pmo/external-items")
            assert resp.status_code == 200
            assert resp.json() == []
        finally:
            application.dependency_overrides.clear()
            store.close()

    def test_graceful_on_store_query_error(self, tmp_path):
        """Endpoint returns [] when the store raises an exception."""
        broken_store = MagicMock()
        broken_store.query.side_effect = RuntimeError("db exploded")
        application = create_app()
        application.dependency_overrides[get_central_store] = lambda: broken_store
        try:
            with TestClient(application) as c:
                resp = c.get("/api/v1/pmo/external-items")
            assert resp.status_code == 200
            assert resp.json() == []
        finally:
            application.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Part A — GET /api/v1/pmo/external-items/{item_id}/mappings
# ---------------------------------------------------------------------------

class TestGetExternalItemMappings:
    def _get_item_id(self, client) -> int:
        items = client.get("/api/v1/pmo/external-items").json()
        jira42 = next(i for i in items if i["external_id"] == "JIRA-42")
        return jira42["id"]

    def test_returns_mappings_for_known_item(self, client):
        item_id = self._get_item_id(client)
        resp = client.get(f"/api/v1/pmo/external-items/{item_id}/mappings")
        assert resp.status_code == 200
        mappings = resp.json()
        assert len(mappings) == 1
        assert mappings[0]["task_id"] == "task-abc"
        assert mappings[0]["project_id"] == "proj-alpha"
        assert mappings[0]["mapping_type"] == "implements"

    def test_mapping_includes_item_detail(self, client):
        item_id = self._get_item_id(client)
        resp = client.get(f"/api/v1/pmo/external-items/{item_id}/mappings")
        mapping = resp.json()[0]
        assert mapping["item"] is not None
        assert mapping["item"]["external_id"] == "JIRA-42"
        assert mapping["item"]["title"] == "Implement login page"
        assert mapping["item"]["source_type"] == "jira"

    def test_returns_empty_for_unmapped_item(self, client):
        items = client.get("/api/v1/pmo/external-items").json()
        jira43 = next(i for i in items if i["external_id"] == "JIRA-43")
        resp = client.get(f"/api/v1/pmo/external-items/{jira43['id']}/mappings")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_404_for_unknown_item_id(self, client):
        resp = client.get("/api/v1/pmo/external-items/99999/mappings")
        assert resp.status_code == 404

    def test_graceful_on_store_error(self, tmp_path):
        broken_store = MagicMock()
        broken_store.query.side_effect = RuntimeError("db exploded")
        application = create_app()
        application.dependency_overrides[get_central_store] = lambda: broken_store
        try:
            with TestClient(application) as c:
                resp = c.get("/api/v1/pmo/external-items/1/mappings")
            # query raises on the first call (item lookup) → returns []
            assert resp.status_code == 200
            assert resp.json() == []
        finally:
            application.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Part C — IntelligentPlanner._fetch_external_annotations
# ---------------------------------------------------------------------------

class TestFetchExternalAnnotations:
    """Unit tests for _fetch_external_annotations — isolated from disk."""

    def _make_planner(self, tmp_path: Path):
        from agent_baton.core.engine.planner import IntelligentPlanner
        return IntelligentPlanner(team_context_root=tmp_path)

    def test_returns_empty_when_central_db_absent(self, tmp_path):
        planner = self._make_planner(tmp_path)
        # No central.db at ~/.baton/central.db in the patched home.
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = planner._fetch_external_annotations("implement login page")
        assert result == []

    def test_returns_empty_when_no_mappings(self, tmp_path):
        # central.db exists but external_mappings is empty.
        db_path = tmp_path / ".baton" / "central.db"
        db_path.parent.mkdir(parents=True)
        store = CentralStore(db_path)
        store.close()

        planner = self._make_planner(tmp_path)
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = planner._fetch_external_annotations("implement login page")
        assert result == []

    def test_returns_matching_items(self, tmp_path):
        # Build central.db with an item and a mapping.
        baton_dir = tmp_path / ".baton"
        baton_dir.mkdir()
        db_path = _make_central_db(baton_dir)
        # _make_central_db puts the db directly in the passed dir as central.db.
        # We need it at tmp_path/.baton/central.db.
        target = baton_dir / "central.db"
        assert target.exists()

        planner = self._make_planner(tmp_path)
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = planner._fetch_external_annotations("implement login page authentication")
        # "implement", "login", "page" all appear in JIRA-42 title
        assert len(result) >= 1
        assert any("JIRA-42" in r for r in result)

    def test_no_match_returns_empty(self, tmp_path):
        baton_dir = tmp_path / ".baton"
        baton_dir.mkdir()
        _make_central_db(baton_dir)

        planner = self._make_planner(tmp_path)
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = planner._fetch_external_annotations("unrelated database migration task")
        # "unrelated", "database", "migration" not in JIRA-42 or JIRA-43 titles
        assert result == []

    def test_never_raises_on_db_error(self, tmp_path):
        """_fetch_external_annotations swallows all exceptions and returns []."""
        planner = self._make_planner(tmp_path)
        # Place a corrupt (non-SQLite) file at the central.db path so
        # CentralStore.__init__ raises when it tries to open it.
        baton_dir = tmp_path / ".baton"
        baton_dir.mkdir()
        corrupt_db = baton_dir / "central.db"
        corrupt_db.write_bytes(b"this is not a sqlite database")
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = planner._fetch_external_annotations("implement task description")
        assert result == []

    def test_annotation_format(self, tmp_path):
        baton_dir = tmp_path / ".baton"
        baton_dir.mkdir()
        _make_central_db(baton_dir)

        planner = self._make_planner(tmp_path)
        with patch("pathlib.Path.home", return_value=tmp_path):
            result = planner._fetch_external_annotations("login page implementation")
        # Each annotation should be "EXTERNAL-ID (title)" format.
        for annotation in result:
            assert "(" in annotation and ")" in annotation

    def test_shared_context_includes_annotation(self, tmp_path):
        """create_plan() shared_context includes 'Relates to:' when items match."""
        baton_dir = tmp_path / ".baton"
        baton_dir.mkdir()
        _make_central_db(baton_dir)

        from agent_baton.core.engine.planner import IntelligentPlanner
        planner = IntelligentPlanner(team_context_root=tmp_path)

        with patch("pathlib.Path.home", return_value=tmp_path):
            plan = planner.create_plan("implement login page authentication for users")

        assert "Relates to:" in plan.shared_context
        assert "JIRA-42" in plan.shared_context
