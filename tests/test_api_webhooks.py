"""Tests for webhook CRUD endpoints:

POST   /api/v1/webhooks
GET    /api/v1/webhooks
DELETE /api/v1/webhooks/{webhook_id}
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
def app(tmp_path: Path):
    return create_app(team_context_root=tmp_path)


@pytest.fixture()
def client(app):
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _register(
    client: TestClient,
    url: str = "https://example.com/hook",
    events: list[str] | None = None,
    secret: str | None = None,
) -> dict:
    """Register a webhook and return the response body."""
    payload: dict = {"url": url, "events": events or ["step.*"]}
    if secret is not None:
        payload["secret"] = secret
    r = client.post("/api/v1/webhooks", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


# ===========================================================================
# POST /api/v1/webhooks — register
# ===========================================================================


class TestRegisterWebhook:
    def test_register_returns_201(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/webhooks",
            json={"url": "https://example.com/hook", "events": ["step.*"]},
        )
        assert r.status_code == 201

    def test_response_contains_webhook_id(self, client: TestClient) -> None:
        body = _register(client)
        assert "webhook_id" in body
        assert isinstance(body["webhook_id"], str)
        assert len(body["webhook_id"]) > 0

    def test_response_contains_url(self, client: TestClient) -> None:
        body = _register(client, url="https://my.service.io/cb")
        assert body["url"] == "https://my.service.io/cb"

    def test_response_contains_events(self, client: TestClient) -> None:
        body = _register(client, events=["step.completed", "gate.*"])
        assert body["events"] == ["step.completed", "gate.*"]

    def test_response_contains_created_timestamp(self, client: TestClient) -> None:
        body = _register(client)
        assert "created" in body
        assert isinstance(body["created"], str)
        assert len(body["created"]) > 0

    def test_each_registration_gets_unique_id(self, client: TestClient) -> None:
        id1 = _register(client)["webhook_id"]
        id2 = _register(client)["webhook_id"]
        assert id1 != id2

    def test_missing_url_returns_422(self, client: TestClient) -> None:
        r = client.post("/api/v1/webhooks", json={"events": ["step.*"]})
        assert r.status_code == 422

    def test_missing_events_returns_422(self, client: TestClient) -> None:
        r = client.post("/api/v1/webhooks", json={"url": "https://example.com/hook"})
        assert r.status_code == 422


# ===========================================================================
# GET /api/v1/webhooks — list
# ===========================================================================


class TestListWebhooks:
    def test_empty_list_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/v1/webhooks")
        assert r.status_code == 200

    def test_empty_list_returns_empty_array(self, client: TestClient) -> None:
        body = client.get("/api/v1/webhooks").json()
        assert body == []

    def test_registered_webhook_appears_in_list(self, client: TestClient) -> None:
        _register(client)
        body = client.get("/api/v1/webhooks").json()
        assert len(body) == 1

    def test_multiple_registrations_all_appear(self, client: TestClient) -> None:
        _register(client, url="https://one.example.com/hook")
        _register(client, url="https://two.example.com/hook")
        body = client.get("/api/v1/webhooks").json()
        assert len(body) == 2


# ===========================================================================
# DELETE /api/v1/webhooks/{webhook_id}
# ===========================================================================


class TestDeleteWebhook:
    def test_delete_existing_returns_200(self, client: TestClient) -> None:
        wh_id = _register(client)["webhook_id"]
        r = client.delete(f"/api/v1/webhooks/{wh_id}")
        assert r.status_code == 200

    def test_delete_returns_deleted_true(self, client: TestClient) -> None:
        wh_id = _register(client)["webhook_id"]
        body = client.delete(f"/api/v1/webhooks/{wh_id}").json()
        assert body["deleted"] is True

    def test_deleted_webhook_no_longer_listed(self, client: TestClient) -> None:
        wh_id = _register(client)["webhook_id"]
        client.delete(f"/api/v1/webhooks/{wh_id}")
        body = client.get("/api/v1/webhooks").json()
        assert all(w["webhook_id"] != wh_id for w in body)

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.delete("/api/v1/webhooks/totally-nonexistent-wh-id")
        assert r.status_code == 404

    def test_delete_nonexistent_detail_mentions_id(self, client: TestClient) -> None:
        r = client.delete("/api/v1/webhooks/my-missing-webhook")
        assert "my-missing-webhook" in r.json()["detail"]
