"""Tests for GET /api/v1/health and GET /api/v1/ready endpoints."""
from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.server import create_app  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app(tmp_path: Path):
    return create_app(team_context_root=tmp_path)


@pytest.fixture()
def client(app):
    return TestClient(app)


# ===========================================================================
# GET /api/v1/health
# ===========================================================================


class TestHealthEndpoint:
    def test_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/v1/health")
        assert r.status_code == 200

    def test_status_is_healthy(self, client: TestClient) -> None:
        body = client.get("/api/v1/health").json()
        assert body["status"] == "healthy"

    def test_version_string_present(self, client: TestClient) -> None:
        body = client.get("/api/v1/health").json()
        assert isinstance(body["version"], str)
        assert len(body["version"]) > 0

    def test_uptime_seconds_is_a_number(self, client: TestClient) -> None:
        body = client.get("/api/v1/health").json()
        assert isinstance(body["uptime_seconds"], (int, float))

    def test_uptime_seconds_is_non_negative(self, client: TestClient) -> None:
        body = client.get("/api/v1/health").json()
        assert body["uptime_seconds"] >= 0

    def test_response_schema_fields_present(self, client: TestClient) -> None:
        body = client.get("/api/v1/health").json()
        assert set(body.keys()) >= {"status", "version", "uptime_seconds"}


# ===========================================================================
# GET /api/v1/ready
# ===========================================================================


class TestReadyEndpoint:
    def test_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/v1/ready")
        assert r.status_code == 200

    def test_ready_is_true(self, client: TestClient) -> None:
        body = client.get("/api/v1/ready").json()
        assert body["ready"] is True

    def test_daemon_running_is_false_on_fresh_app(self, client: TestClient) -> None:
        # No active execution has been started, so the daemon should not be running.
        body = client.get("/api/v1/ready").json()
        assert body["daemon_running"] is False

    def test_pending_decisions_is_zero_on_fresh_app(self, client: TestClient) -> None:
        body = client.get("/api/v1/ready").json()
        assert body["pending_decisions"] == 0

    def test_response_schema_fields_present(self, client: TestClient) -> None:
        body = client.get("/api/v1/ready").json()
        assert set(body.keys()) >= {"ready", "daemon_running", "pending_decisions"}
