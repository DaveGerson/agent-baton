"""Tests for GET /api/v1/agents and GET /api/v1/agents/{name} endpoints."""
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
# Helper: fetch the first registered agent name
# ---------------------------------------------------------------------------

def _first_agent_name(client: TestClient) -> str:
    body = client.get("/api/v1/agents").json()
    assert body["count"] > 0, "Registry is empty — cannot test individual agent lookup."
    return body["agents"][0]["name"]


# ===========================================================================
# GET /api/v1/agents — list agents
# ===========================================================================


class TestListAgents:
    def test_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/v1/agents")
        assert r.status_code == 200

    def test_response_has_count_field(self, client: TestClient) -> None:
        body = client.get("/api/v1/agents").json()
        assert "count" in body
        assert isinstance(body["count"], int)

    def test_count_matches_agents_list_length(self, client: TestClient) -> None:
        body = client.get("/api/v1/agents").json()
        assert body["count"] == len(body["agents"])

    def test_agents_list_is_non_empty(self, client: TestClient) -> None:
        # The distributable agents/ dir ships with agents, so the registry
        # should always have at least one entry.
        body = client.get("/api/v1/agents").json()
        assert body["count"] > 0

    def test_agent_entry_has_required_fields(self, client: TestClient) -> None:
        body = client.get("/api/v1/agents").json()
        agent = body["agents"][0]
        assert "name" in agent
        assert "description" in agent
        assert "category" in agent


# ===========================================================================
# GET /api/v1/agents?category=...
# ===========================================================================


class TestListAgentsByCategory:
    def test_engineering_category_returns_subset(self, client: TestClient) -> None:
        all_count = client.get("/api/v1/agents").json()["count"]
        eng_count = client.get("/api/v1/agents?category=Engineering").json()["count"]
        assert 0 <= eng_count <= all_count

    def test_engineering_results_all_have_correct_category(self, client: TestClient) -> None:
        body = client.get("/api/v1/agents?category=Engineering").json()
        for agent in body["agents"]:
            assert agent["category"] == "Engineering"

    def test_unknown_category_returns_400(self, client: TestClient) -> None:
        r = client.get("/api/v1/agents?category=Bogus")
        assert r.status_code == 400

    def test_category_filter_is_case_insensitive(self, client: TestClient) -> None:
        # Both "Engineering" and "engineering" should resolve to the same result.
        r1 = client.get("/api/v1/agents?category=Engineering").json()["count"]
        r2 = client.get("/api/v1/agents?category=engineering").json()["count"]
        assert r1 == r2


# ===========================================================================
# GET /api/v1/agents/{name}
# ===========================================================================


class TestGetAgent:
    def test_known_agent_returns_200(self, client: TestClient) -> None:
        name = _first_agent_name(client)
        r = client.get(f"/api/v1/agents/{name}")
        assert r.status_code == 200

    def test_known_agent_returns_correct_name(self, client: TestClient) -> None:
        name = _first_agent_name(client)
        body = client.get(f"/api/v1/agents/{name}").json()
        assert body["name"] == name

    def test_unknown_agent_returns_404(self, client: TestClient) -> None:
        r = client.get("/api/v1/agents/totally-nonexistent-agent-xyz")
        assert r.status_code == 404

    def test_unknown_agent_404_detail_mentions_name(self, client: TestClient) -> None:
        r = client.get("/api/v1/agents/my-missing-agent")
        assert "my-missing-agent" in r.json()["detail"]
