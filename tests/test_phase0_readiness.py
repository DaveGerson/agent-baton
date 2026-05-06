"""Regression tests for Fix 0.3 — readiness probe real checks.

Verifies that GET /ready returns:
  - ready=False + reason when baton.db cannot be opened.
  - ready=False + reason when the state directory is not writable.
  - ready=False + reason when engine status == "failed".
  - ready=True when all checks pass.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from agent_baton.api.server import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def app(tmp_path: Path):
    return create_app(team_context_root=tmp_path)


@pytest.fixture()
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Fix 0.3: readiness checks
# ---------------------------------------------------------------------------

class TestReadinessProbeChecks:
    def test_ready_true_when_all_checks_pass(self, client: TestClient) -> None:
        """Baseline: /ready returns ready=True when SQLite, dir, and engine are healthy."""
        body = client.get("/api/v1/ready").json()
        assert body["ready"] is True
        # reason is an optional field; it may be absent, None, or empty string when ready=True.
        reason = body.get("reason")
        assert reason is None or reason == "", (
            f"reason should be empty or absent when ready=True, got {reason!r}"
        )

    def test_ready_false_when_sqlite_unavailable(self, tmp_path: Path) -> None:
        """ready=False with a reason when baton.db cannot be opened via SELECT 1."""
        app = create_app(team_context_root=tmp_path)
        test_client = TestClient(app)

        def _broken_connect(*args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error")

        with patch("agent_baton.api.routes.health.sqlite3.connect", side_effect=_broken_connect):
            body = test_client.get("/api/v1/ready").json()

        assert body["ready"] is False, (
            "/ready must return ready=False when SQLite is unavailable"
        )
        assert "reason" in body and body["reason"], (
            "/ready must include a non-empty reason when SQLite check fails"
        )
        assert "sqlite" in body["reason"].lower() or "sql" in body["reason"].lower(), (
            "reason should mention SQLite"
        )

    def test_ready_false_when_state_dir_not_writable(self, tmp_path: Path) -> None:
        """ready=False when the state directory is not writable."""
        app = create_app(team_context_root=tmp_path)
        test_client = TestClient(app)

        # Patch os.access so the writable check returns False for W_OK.
        original_access = os.access

        def _fake_access(path: str, mode: int) -> bool:
            if mode == os.W_OK:
                return False
            return original_access(path, mode)

        with patch("agent_baton.api.routes.health.os.access", side_effect=_fake_access):
            body = test_client.get("/api/v1/ready").json()

        assert body["ready"] is False, (
            "/ready must return ready=False when state directory is not writable"
        )
        assert "reason" in body and body["reason"], (
            "/ready must include a reason when the directory is not writable"
        )
        assert "writable" in body["reason"].lower() or "write" in body["reason"].lower(), (
            "reason should mention writability"
        )

    def test_ready_false_when_engine_status_failed(self, tmp_path: Path) -> None:
        """ready=False with reason 'failed' when engine.status() returns failed."""
        app = create_app(team_context_root=tmp_path)
        test_client = TestClient(app)

        from agent_baton.api import deps

        original_engine = deps._engine
        mock_engine = MagicMock()
        mock_engine.status.return_value = {"status": "failed"}

        try:
            deps._engine = mock_engine
            body = test_client.get("/api/v1/ready").json()
        finally:
            deps._engine = original_engine

        assert body["ready"] is False, (
            "/ready must return ready=False when engine status is 'failed'"
        )
        assert "reason" in body and body["reason"], (
            "/ready must include a reason when engine is in failed state"
        )
        assert "failed" in body["reason"].lower(), (
            "reason should mention that engine status is failed"
        )

    def test_ready_response_includes_daemon_running_field(self, client: TestClient) -> None:
        """The /ready response must always include daemon_running."""
        body = client.get("/api/v1/ready").json()
        assert "daemon_running" in body

    def test_ready_response_includes_pending_decisions_field(self, client: TestClient) -> None:
        """The /ready response must always include pending_decisions."""
        body = client.get("/api/v1/ready").json()
        assert "pending_decisions" in body

    def test_ready_false_has_reason_string(self, tmp_path: Path) -> None:
        """When ready=False, reason must be a non-empty string, not None."""
        app = create_app(team_context_root=tmp_path)
        test_client = TestClient(app)

        def _broken_connect(*args, **kwargs):
            raise sqlite3.OperationalError("no such file")

        with patch("agent_baton.api.routes.health.sqlite3.connect", side_effect=_broken_connect):
            body = test_client.get("/api/v1/ready").json()

        assert body["ready"] is False
        reason = body.get("reason")
        assert isinstance(reason, str) and len(reason) > 0, (
            "reason must be a non-empty string when ready=False"
        )
