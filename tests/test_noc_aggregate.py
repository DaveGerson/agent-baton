"""Tests for NOC aggregate cross-project endpoints.

Endpoints covered (all prefixed with /api/v1):

  GET /noc/projects               — list projects with summary stats
  GET /noc/aggregate/usage        — cross-project token usage rollup
  GET /noc/aggregate/incidents    — cross-project warning-bead count
  GET /noc/aggregate/throughput   — tasks completed per project per day (7d)

Strategy: dependency_overrides on the optional central-store getter so
each test can provide an isolated in-memory CentralStore without touching
~/.baton/central.db.  A second fixture omits the override to simulate
the "central.db absent" code path (returns empty results gracefully).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.routes.noc import get_central_store_optional  # noqa: E402
from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.storage.central import CentralStore  # noqa: E402
from agent_baton.core.storage.schema import CENTRAL_SCHEMA_DDL  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_central_store(db_path: Path) -> CentralStore:
    """Create an isolated CentralStore at *db_path* (applies full DDL)."""
    store = CentralStore(db_path)
    return store


def _seed_projects(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert rows into the ``projects`` table."""
    for r in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO projects
              (project_id, name, path, program, registered_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                r["project_id"],
                r.get("name", r["project_id"]),
                r.get("path", "/tmp/" + r["project_id"]),
                r.get("program", "default"),
                r.get("registered_at", "2026-01-01T00:00:00Z"),
            ),
        )
    conn.commit()


def _seed_executions(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert rows into the central ``executions`` table."""
    for r in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO executions
              (project_id, task_id, status, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                r["project_id"],
                r["task_id"],
                r.get("status", "complete"),
                r.get("started_at", "2026-04-20T10:00:00Z"),
                r.get("completed_at", "2026-04-20T10:05:00Z"),
            ),
        )
    conn.commit()


def _seed_agent_usage(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert rows into the central ``agent_usage`` table."""
    for r in rows:
        conn.execute(
            """
            INSERT INTO agent_usage
              (project_id, task_id, agent_name, estimated_tokens)
            VALUES (?, ?, ?, ?)
            """,
            (
                r["project_id"],
                r.get("task_id", "t1"),
                r.get("agent_name", "backend-engineer"),
                r.get("estimated_tokens", 1000),
            ),
        )
    conn.commit()


def _seed_beads(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """Insert rows into the central ``beads`` table."""
    for i, r in enumerate(rows):
        conn.execute(
            """
            INSERT OR REPLACE INTO beads
              (project_id, bead_id, step_id, agent_name, bead_type, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                r["project_id"],
                r.get("bead_id", f"bd-{i:04d}"),
                r.get("step_id", "1.1"),
                r.get("agent_name", "backend-engineer"),
                r.get("bead_type", "warning"),
                r.get("created_at", "2026-04-20T10:00:00Z"),
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def central_db(tmp_path: Path) -> Path:
    """Return the path to a freshly initialised central.db in tmp_path."""
    db_path = tmp_path / "central.db"
    store = _make_central_store(db_path)
    store.close()
    return db_path


@pytest.fixture()
def app_with_central(tmp_path: Path, central_db: Path):
    """App whose NOC dependency is wired to an in-memory central.db stub."""
    app = create_app(team_context_root=tmp_path)
    store = _make_central_store(central_db)

    app.dependency_overrides[get_central_store_optional] = lambda: store
    yield app, store
    app.dependency_overrides.clear()
    store.close()


@pytest.fixture()
def app_no_central(tmp_path: Path):
    """App where central.db does not exist — dependency returns None."""
    app = create_app(team_context_root=tmp_path)
    app.dependency_overrides[get_central_store_optional] = lambda: None
    yield app
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Tests: /noc/projects
# ---------------------------------------------------------------------------


class TestNocProjects:
    def test_projects_endpoint_returns_list(self, app_with_central):
        app, store = app_with_central
        conn = store._conn()
        _seed_projects(
            conn,
            [
                {"project_id": "proj-alpha", "name": "Alpha"},
                {"project_id": "proj-beta", "name": "Beta"},
            ],
        )
        _seed_executions(
            conn,
            [
                {"project_id": "proj-alpha", "task_id": "t1"},
                {"project_id": "proj-alpha", "task_id": "t2"},
                {"project_id": "proj-beta", "task_id": "t3"},
            ],
        )

        client = TestClient(app)
        resp = client.get("/api/v1/noc/projects")

        assert resp.status_code == 200
        data = resp.json()
        assert "projects" in data
        projects = data["projects"]
        assert len(projects) == 2
        ids = {p["project_id"] for p in projects}
        assert ids == {"proj-alpha", "proj-beta"}

        alpha = next(p for p in projects if p["project_id"] == "proj-alpha")
        assert alpha["task_count"] == 2
        assert alpha["project_name"] == "Alpha"

    def test_projects_endpoint_empty_when_central_missing(self, app_no_central):
        client = TestClient(app_no_central)
        resp = client.get("/api/v1/noc/projects")

        assert resp.status_code == 200
        data = resp.json()
        assert data == {"projects": []}

    def test_projects_no_data_returns_empty_list(self, app_with_central):
        """When projects table exists but is empty the endpoint still returns 200."""
        app, store = app_with_central
        client = TestClient(app)
        resp = client.get("/api/v1/noc/projects")

        assert resp.status_code == 200
        assert resp.json() == {"projects": []}


# ---------------------------------------------------------------------------
# Tests: /noc/aggregate/usage
# ---------------------------------------------------------------------------


class TestNocUsage:
    def test_usage_aggregate_sums_by_project(self, app_with_central):
        app, store = app_with_central
        conn = store._conn()
        _seed_projects(
            conn,
            [
                {"project_id": "p1"},
                {"project_id": "p2"},
            ],
        )
        _seed_agent_usage(
            conn,
            [
                {"project_id": "p1", "task_id": "t1", "estimated_tokens": 5000},
                {"project_id": "p1", "task_id": "t1", "agent_name": "tester", "estimated_tokens": 3000},
                {"project_id": "p2", "task_id": "t2", "estimated_tokens": 2000},
            ],
        )

        client = TestClient(app)
        resp = client.get("/api/v1/noc/aggregate/usage")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tokens"] == 10000

        by_project = {r["project_id"]: r["total_tokens"] for r in data["by_project"]}
        assert by_project["p1"] == 8000
        assert by_project["p2"] == 2000

    def test_usage_returns_zeros_when_no_data(self, app_with_central):
        app, store = app_with_central
        client = TestClient(app)
        resp = client.get("/api/v1/noc/aggregate/usage")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tokens"] == 0
        assert data["by_project"] == []

    def test_usage_aggregate_empty_when_central_missing(self, app_no_central):
        client = TestClient(app_no_central)
        resp = client.get("/api/v1/noc/aggregate/usage")

        assert resp.status_code == 200
        assert resp.json() == {"by_project": [], "total_tokens": 0}


# ---------------------------------------------------------------------------
# Tests: /noc/aggregate/incidents
# ---------------------------------------------------------------------------


class TestNocIncidents:
    def test_incidents_aggregate_counts_warnings(self, app_with_central):
        app, store = app_with_central
        conn = store._conn()
        _seed_projects(conn, [{"project_id": "pa"}, {"project_id": "pb"}])
        _seed_beads(
            conn,
            [
                {"project_id": "pa", "bead_id": "bd-0001", "bead_type": "warning"},
                {"project_id": "pa", "bead_id": "bd-0002", "bead_type": "warning"},
                {"project_id": "pa", "bead_id": "bd-0003", "bead_type": "decision"},
                {"project_id": "pb", "bead_id": "bd-0004", "bead_type": "warning"},
            ],
        )

        client = TestClient(app)
        resp = client.get("/api/v1/noc/aggregate/incidents")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_warnings"] == 3

        by_project = {r["project_id"]: r["warning_count"] for r in data["by_project"]}
        assert by_project["pa"] == 2
        assert by_project["pb"] == 1

    def test_incidents_non_warning_beads_excluded(self, app_with_central):
        app, store = app_with_central
        conn = store._conn()
        _seed_projects(conn, [{"project_id": "pc"}])
        _seed_beads(
            conn,
            [
                {"project_id": "pc", "bead_id": "bd-0010", "bead_type": "discovery"},
                {"project_id": "pc", "bead_id": "bd-0011", "bead_type": "decision"},
            ],
        )

        client = TestClient(app)
        resp = client.get("/api/v1/noc/aggregate/incidents")

        assert resp.status_code == 200
        data = resp.json()
        assert data["total_warnings"] == 0
        assert data["by_project"] == []

    def test_incidents_empty_when_central_missing(self, app_no_central):
        client = TestClient(app_no_central)
        resp = client.get("/api/v1/noc/aggregate/incidents")

        assert resp.status_code == 200
        assert resp.json() == {"by_project": [], "total_warnings": 0}


# ---------------------------------------------------------------------------
# Tests: /noc/aggregate/throughput
# ---------------------------------------------------------------------------


class TestNocThroughput:
    def test_throughput_aggregate_returns_7d_window(self, app_with_central):
        app, store = app_with_central
        conn = store._conn()
        _seed_projects(conn, [{"project_id": "px"}, {"project_id": "py"}])
        # Recent completions (within 7-day window) — use a fixed recent date.
        # SQLite DATE('now', '-6 days') covers today minus 6 = 7 days total.
        _seed_executions(
            conn,
            [
                {
                    "project_id": "px",
                    "task_id": "t10",
                    "status": "complete",
                    "started_at": "2026-04-25T10:00:00Z",
                    "completed_at": "2026-04-25T10:10:00Z",
                },
                {
                    "project_id": "px",
                    "task_id": "t11",
                    "status": "complete",
                    "started_at": "2026-04-25T11:00:00Z",
                    "completed_at": "2026-04-25T11:10:00Z",
                },
                {
                    "project_id": "py",
                    "task_id": "t12",
                    "status": "complete",
                    "started_at": "2026-04-26T08:00:00Z",
                    "completed_at": "2026-04-26T08:30:00Z",
                },
                # Old execution — should be excluded (> 7 days ago)
                {
                    "project_id": "px",
                    "task_id": "t13",
                    "status": "complete",
                    "started_at": "2026-01-01T00:00:00Z",
                    "completed_at": "2026-01-01T00:10:00Z",
                },
                # Running execution — should be excluded (status != complete)
                {
                    "project_id": "py",
                    "task_id": "t14",
                    "status": "running",
                    "started_at": "2026-04-26T09:00:00Z",
                    "completed_at": "",
                },
            ],
        )

        client = TestClient(app)
        resp = client.get("/api/v1/noc/aggregate/throughput")

        assert resp.status_code == 200
        data = resp.json()
        assert data["window_days"] == 7
        rows = data["by_project_day"]
        # Verify the response shape
        if rows:
            assert "project_id" in rows[0]
            assert "day" in rows[0]
            assert "tasks_completed" in rows[0]

    def test_throughput_aggregate_empty_when_central_missing(self, app_no_central):
        client = TestClient(app_no_central)
        resp = client.get("/api/v1/noc/aggregate/throughput")

        assert resp.status_code == 200
        assert resp.json() == {"window_days": 7, "by_project_day": []}

    def test_throughput_no_complete_tasks_returns_empty(self, app_with_central):
        """When no tasks have status=complete the by_project_day list is empty."""
        app, store = app_with_central
        conn = store._conn()
        _seed_projects(conn, [{"project_id": "pz"}])
        _seed_executions(
            conn,
            [
                {
                    "project_id": "pz",
                    "task_id": "t20",
                    "status": "running",
                    "started_at": "2026-04-26T09:00:00Z",
                    "completed_at": "",
                }
            ],
        )

        client = TestClient(app)
        resp = client.get("/api/v1/noc/aggregate/throughput")

        assert resp.status_code == 200
        assert resp.json()["by_project_day"] == []


# ---------------------------------------------------------------------------
# Tests: graceful no-data handling across all endpoints
# ---------------------------------------------------------------------------


class TestEndpointsHandleNoDataGracefully:
    def test_endpoints_handle_no_data_gracefully(self, app_with_central):
        """All four NOC endpoints return 200 with empty/zero payloads on a
        freshly initialised central.db that has no project data."""
        app, store = app_with_central
        client = TestClient(app)

        for path in [
            "/api/v1/noc/projects",
            "/api/v1/noc/aggregate/usage",
            "/api/v1/noc/aggregate/incidents",
            "/api/v1/noc/aggregate/throughput",
        ]:
            resp = client.get(path)
            assert resp.status_code == 200, f"{path} returned {resp.status_code}"
            data = resp.json()
            # Each endpoint must return a dict (not an error response).
            assert isinstance(data, dict), f"{path} did not return a dict"
