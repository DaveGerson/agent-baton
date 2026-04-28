"""HTTP-level tests for the H3 PMO endpoints (pmo_h3.py).

Covers:

  GET  /pmo/scorecard/{user_id}                — returns zeros on empty DB
  GET  /pmo/arch-beads                          — empty list when no beads
  POST /pmo/arch-beads/{bead_id}/review         — files a follow-up id
  GET  /pmo/playbooks                           — lists templates/playbooks/*.md
  POST /pmo/crp                                 — synthesizes a plan summary

These tests exercise the Velocity-first contract: every endpoint
gracefully degrades when the underlying tables / files are absent
rather than 500ing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.server import create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Spin up the API with a tmp cwd so the H3 endpoints find no DB.

    The H3 routes resolve ``baton.db`` and ``templates/playbooks``
    relative to the current working directory, so chdir-ing into a
    fresh tmp_path makes the tests deterministic.
    """
    monkeypatch.chdir(tmp_path)
    app = create_app(team_context_root=tmp_path / ".claude" / "team-context")
    return TestClient(app)


# ---------------------------------------------------------------------------
# H3.4 — scorecard
# ---------------------------------------------------------------------------


def test_scorecard_empty_db_returns_zeros(client: TestClient) -> None:
    res = client.get("/api/v1/pmo/scorecard/alice")
    assert res.status_code == 200
    data = res.json()
    assert data["user_id"] == "alice"
    assert data["window_days"] == 30
    assert data["tasks_completed"] == 0
    assert data["incidents_authored"] == 0
    assert data["gate_pass_rate"] == 0.0


# ---------------------------------------------------------------------------
# H3.7 — arch beads
# ---------------------------------------------------------------------------


def test_arch_beads_empty_returns_empty_list(client: TestClient) -> None:
    res = client.get("/api/v1/pmo/arch-beads")
    assert res.status_code == 200
    assert res.json() == []


def test_arch_beads_review_returns_followup_id(client: TestClient) -> None:
    body = {"action": "approve", "reason": "looks good", "reviewer": "bob"}
    res = client.post("/api/v1/pmo/arch-beads/bd-fake-1/review", json=body)
    assert res.status_code == 201
    data = res.json()
    assert data["bead_id"] == "bd-fake-1"
    assert data["action"] == "approve"
    assert data["follow_up_bead_id"].startswith("bd-rv-")


def test_arch_beads_review_rejects_invalid_action(client: TestClient) -> None:
    res = client.post(
        "/api/v1/pmo/arch-beads/bd-fake-1/review",
        json={"action": "maybe", "reason": ""},
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# H3.8 — playbooks
# ---------------------------------------------------------------------------


def test_playbooks_lists_files_from_templates_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    pdir = tmp_path / "templates" / "playbooks"
    pdir.mkdir(parents=True)
    (pdir / "first.md").write_text("# First Playbook\n\nbody one.\n")
    (pdir / "second.md").write_text("no title heading here\n")

    app = create_app(team_context_root=tmp_path / ".claude" / "team-context")
    client = TestClient(app)

    res = client.get("/api/v1/pmo/playbooks")
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 2
    by_slug = {p["slug"]: p for p in items}
    assert by_slug["first"]["title"] == "First Playbook"
    # Falls back to the slug when no heading is found.
    assert by_slug["second"]["title"].lower().startswith("second")


def test_playbooks_returns_empty_when_dir_missing(client: TestClient) -> None:
    res = client.get("/api/v1/pmo/playbooks")
    assert res.status_code == 200
    assert res.json() == []


# ---------------------------------------------------------------------------
# H3.9 — CRP
# ---------------------------------------------------------------------------


def test_crp_returns_plan_summary(client: TestClient) -> None:
    body = {
        "title": "Adopt new caching layer",
        "scope": ["src/cache.py", "src/handlers.py"],
        "rationale": "Throughput is bottlenecked on disk I/O.",
        "risk_level": "high",
        "suggested_agent": "architect",
    }
    res = client.post("/api/v1/pmo/crp", json=body)
    assert res.status_code == 201
    data = res.json()
    assert data["crp_id"].startswith("crp-")
    assert "Adopt new caching layer" in data["plan_summary"]
    # high-risk requests get an extra security-review phase.
    assert "security-review" in data["suggested_phases"]
    assert "audit" in data["suggested_phases"]


def test_crp_requires_title(client: TestClient) -> None:
    res = client.post("/api/v1/pmo/crp", json={"title": "  "})
    assert res.status_code == 422


def test_crp_low_risk_skips_security_review(client: TestClient) -> None:
    body = {"title": "tiny tweak", "risk_level": "low"}
    res = client.post("/api/v1/pmo/crp", json=body)
    assert res.status_code == 201
    data = res.json()
    assert "security-review" not in data["suggested_phases"]
