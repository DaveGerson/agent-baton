"""Tests for TokenAuthMiddleware — Bearer token authentication.

The middleware is a no-op when no token is configured (auth disabled),
and enforces Bearer token validation when a token is provided.
Health/readiness probes are always exempt.
"""
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
def app_no_token(tmp_path: Path):
    """App instance with no token (auth disabled)."""
    return create_app(team_context_root=tmp_path)


@pytest.fixture()
def client_no_token(app_no_token):
    return TestClient(app_no_token)


@pytest.fixture()
def app_with_token(tmp_path: Path):
    """App instance with a required Bearer token."""
    return create_app(team_context_root=tmp_path, token="test-secret-token")


@pytest.fixture()
def client_with_token(app_with_token):
    return TestClient(app_with_token)


# ===========================================================================
# Auth disabled (no token configured)
# ===========================================================================


class TestAuthDisabled:
    def test_agents_accessible_without_credentials(self, client_no_token: TestClient) -> None:
        r = client_no_token.get("/api/v1/agents")
        assert r.status_code == 200

    def test_decisions_accessible_without_credentials(
        self, client_no_token: TestClient
    ) -> None:
        r = client_no_token.get("/api/v1/decisions")
        assert r.status_code == 200

    def test_dashboard_accessible_without_credentials(
        self, client_no_token: TestClient
    ) -> None:
        r = client_no_token.get("/api/v1/dashboard")
        assert r.status_code == 200

    def test_health_accessible_without_credentials(
        self, client_no_token: TestClient
    ) -> None:
        r = client_no_token.get("/api/v1/health")
        assert r.status_code == 200


# ===========================================================================
# Auth enabled — requests without a token
# ===========================================================================


class TestAuthEnabledNoToken:
    def test_agents_without_token_returns_401(self, client_with_token: TestClient) -> None:
        r = client_with_token.get("/api/v1/agents")
        assert r.status_code == 401

    def test_decisions_without_token_returns_401(
        self, client_with_token: TestClient
    ) -> None:
        r = client_with_token.get("/api/v1/decisions")
        assert r.status_code == 401

    def test_dashboard_without_token_returns_401(
        self, client_with_token: TestClient
    ) -> None:
        r = client_with_token.get("/api/v1/dashboard")
        assert r.status_code == 401

    def test_webhooks_without_token_returns_401(
        self, client_with_token: TestClient
    ) -> None:
        r = client_with_token.get("/api/v1/webhooks")
        assert r.status_code == 401


# ===========================================================================
# Auth enabled — correct token
# ===========================================================================


class TestAuthEnabledCorrectToken:
    _headers = {"Authorization": "Bearer test-secret-token"}

    def test_agents_with_correct_token_returns_200(
        self, client_with_token: TestClient
    ) -> None:
        r = client_with_token.get("/api/v1/agents", headers=self._headers)
        assert r.status_code == 200

    def test_decisions_with_correct_token_returns_200(
        self, client_with_token: TestClient
    ) -> None:
        r = client_with_token.get("/api/v1/decisions", headers=self._headers)
        assert r.status_code == 200

    def test_dashboard_with_correct_token_returns_200(
        self, client_with_token: TestClient
    ) -> None:
        r = client_with_token.get("/api/v1/dashboard", headers=self._headers)
        assert r.status_code == 200


# ===========================================================================
# Auth enabled — exempt paths bypass auth
# ===========================================================================


class TestAuthExemptPaths:
    def test_health_bypasses_auth(self, client_with_token: TestClient) -> None:
        r = client_with_token.get("/api/v1/health")
        assert r.status_code == 200

    def test_ready_bypasses_auth(self, client_with_token: TestClient) -> None:
        r = client_with_token.get("/api/v1/ready")
        assert r.status_code == 200

    def test_openapi_schema_bypasses_auth(self, client_with_token: TestClient) -> None:
        r = client_with_token.get("/openapi.json")
        assert r.status_code == 200

    def test_health_with_wrong_token_still_200(
        self, client_with_token: TestClient
    ) -> None:
        # Even a wrong token header should not affect exempt paths.
        r = client_with_token.get(
            "/api/v1/health",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert r.status_code == 200


# ===========================================================================
# Auth enabled — invalid token formats
# ===========================================================================


class TestAuthInvalidTokenFormat:
    def test_wrong_token_value_returns_401(self, client_with_token: TestClient) -> None:
        r = client_with_token.get(
            "/api/v1/agents",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert r.status_code == 401

    def test_missing_bearer_scheme_returns_401(
        self, client_with_token: TestClient
    ) -> None:
        # Token sent without "Bearer" prefix.
        r = client_with_token.get(
            "/api/v1/agents",
            headers={"Authorization": "test-secret-token"},
        )
        assert r.status_code == 401

    def test_basic_scheme_returns_401(self, client_with_token: TestClient) -> None:
        r = client_with_token.get(
            "/api/v1/agents",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert r.status_code == 401

    def test_empty_authorization_header_returns_401(
        self, client_with_token: TestClient
    ) -> None:
        r = client_with_token.get(
            "/api/v1/agents",
            headers={"Authorization": ""},
        )
        assert r.status_code == 401

    def test_401_response_is_json(self, client_with_token: TestClient) -> None:
        r = client_with_token.get("/api/v1/agents")
        assert r.headers["content-type"].startswith("application/json")
        body = r.json()
        assert "error" in body
