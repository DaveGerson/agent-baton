"""HTTP-level tests for ``GET /api/v1/pmo/beads`` (DX.6 / bd-aade).

Covers:

  - empty list when no DB / no beads
  - populated list returned in ``created_at DESC`` order
  - ``status`` filter (default 'open' hides closed beads; ``status=all``
    returns everything)
  - ``bead_type`` filter
  - ``tags`` filter (comma-separated, AND semantics)
  - ``task_id`` filter
  - ``limit`` query param
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
def empty_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Spin up the API with a tmp cwd so the bead store finds no DB."""
    monkeypatch.chdir(tmp_path)
    app = create_app(team_context_root=tmp_path / ".claude" / "team-context")
    return TestClient(app)


def _seed_execution(db_path: Path, task_id: str) -> None:
    """Insert a minimal executions row so the beads.task_id FK passes."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute(
            "INSERT OR IGNORE INTO executions "
            "(task_id, status, current_phase, current_step_index, started_at, "
            " created_at, updated_at) "
            "VALUES (?, 'running', 0, 0, '2026-01-01T00:00:00Z', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (task_id,),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def populated_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """Spin up the API with a baton.db pre-populated with a few beads."""
    monkeypatch.chdir(tmp_path)
    db_dir = tmp_path / ".claude" / "team-context"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "baton.db"

    from agent_baton.core.engine.bead_store import BeadStore
    from agent_baton.models.bead import Bead, BeadLink

    store = BeadStore(db_path)
    # Force schema application before seeding parent rows.
    store._table_exists()
    _seed_execution(db_path, "task-1")
    _seed_execution(db_path, "task-2")
    # Three beads, varying type/status/tags/task_id, distinct timestamps so
    # ordering is deterministic.
    store.write(
        Bead(
            bead_id="bd-aaa1",
            task_id="task-1",
            step_id="step-a",
            agent_name="planner",
            bead_type="planning",
            content="plan something",
            tags=["roadmap", "dx"],
            affected_files=["foo.py"],
            status="open",
            created_at="2026-04-25T10:00:00Z",
            links=[BeadLink(target_bead_id="bd-bbb2", link_type="blocks")],
            source="agent-signal",
            token_estimate=120,
        )
    )
    store.write(
        Bead(
            bead_id="bd-bbb2",
            task_id="task-1",
            step_id="step-b",
            agent_name="auditor",
            bead_type="warning",
            content="watch out",
            tags=["dx", "audit"],
            status="closed",
            created_at="2026-04-24T09:00:00Z",
            closed_at="2026-04-25T11:00:00Z",
            source="agent-signal",
            token_estimate=80,
        )
    )
    store.write(
        Bead(
            bead_id="bd-ccc3",
            task_id="task-2",
            step_id="step-c",
            agent_name="reviewer",
            bead_type="warning",
            content="another warning",
            tags=["audit"],
            status="open",
            created_at="2026-04-26T08:00:00Z",
            source="agent-signal",
            token_estimate=60,
        )
    )

    app = create_app(team_context_root=db_dir)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_beads_empty_db_returns_empty_envelope(empty_client: TestClient) -> None:
    res = empty_client.get("/api/v1/pmo/beads")
    assert res.status_code == 200
    data = res.json()
    assert data == {"beads": [], "total": 0}


def test_beads_default_status_is_open(populated_client: TestClient) -> None:
    res = populated_client.get("/api/v1/pmo/beads")
    assert res.status_code == 200
    data = res.json()
    # Two open beads (bd-aaa1, bd-ccc3); closed bd-bbb2 is hidden.
    ids = [b["bead_id"] for b in data["beads"]]
    assert set(ids) == {"bd-aaa1", "bd-ccc3"}
    assert data["total"] == 2
    # newest-first
    assert ids[0] == "bd-ccc3"


def test_beads_status_all_returns_everything(populated_client: TestClient) -> None:
    res = populated_client.get("/api/v1/pmo/beads", params={"status": "all"})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 3
    ids = [b["bead_id"] for b in data["beads"]]
    # newest-first: bd-ccc3 (Apr 26) > bd-aaa1 (Apr 25) > bd-bbb2 (Apr 24)
    assert ids == ["bd-ccc3", "bd-aaa1", "bd-bbb2"]


def test_beads_status_closed_filter(populated_client: TestClient) -> None:
    res = populated_client.get("/api/v1/pmo/beads", params={"status": "closed"})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["beads"][0]["bead_id"] == "bd-bbb2"
    assert data["beads"][0]["status"] == "closed"


def test_beads_bead_type_filter(populated_client: TestClient) -> None:
    res = populated_client.get(
        "/api/v1/pmo/beads",
        params={"bead_type": "warning", "status": "all"},
    )
    assert res.status_code == 200
    data = res.json()
    ids = sorted(b["bead_id"] for b in data["beads"])
    assert ids == ["bd-bbb2", "bd-ccc3"]


def test_beads_tags_filter_and_semantics(populated_client: TestClient) -> None:
    # Single-tag filter — both bd-aaa1 and bd-bbb2 carry "dx".
    res = populated_client.get(
        "/api/v1/pmo/beads",
        params={"tags": "dx", "status": "all"},
    )
    assert res.status_code == 200
    ids = sorted(b["bead_id"] for b in res.json()["beads"])
    assert ids == ["bd-aaa1", "bd-bbb2"]

    # Multi-tag AND filter — only bd-bbb2 has both 'dx' AND 'audit'.
    res = populated_client.get(
        "/api/v1/pmo/beads",
        params={"tags": "dx,audit", "status": "all"},
    )
    assert res.status_code == 200
    ids = [b["bead_id"] for b in res.json()["beads"]]
    assert ids == ["bd-bbb2"]


def test_beads_task_id_filter(populated_client: TestClient) -> None:
    res = populated_client.get(
        "/api/v1/pmo/beads",
        params={"task_id": "task-2", "status": "all"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["beads"][0]["bead_id"] == "bd-ccc3"


def test_beads_limit_param(populated_client: TestClient) -> None:
    res = populated_client.get(
        "/api/v1/pmo/beads",
        params={"status": "all", "limit": 1},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    # newest first → bd-ccc3
    assert data["beads"][0]["bead_id"] == "bd-ccc3"


def test_beads_response_includes_links_and_full_shape(
    populated_client: TestClient,
) -> None:
    res = populated_client.get(
        "/api/v1/pmo/beads", params={"status": "all", "task_id": "task-1"}
    )
    assert res.status_code == 200
    by_id = {b["bead_id"]: b for b in res.json()["beads"]}
    aaa = by_id["bd-aaa1"]
    # Full shape: required fields all present.
    for key in (
        "bead_id",
        "task_id",
        "step_id",
        "agent_name",
        "bead_type",
        "content",
        "confidence",
        "scope",
        "tags",
        "affected_files",
        "status",
        "created_at",
        "closed_at",
        "summary",
        "links",
        "source",
        "token_estimate",
    ):
        assert key in aaa, f"missing key {key} in response"
    assert aaa["affected_files"] == ["foo.py"]
    assert sorted(aaa["tags"]) == ["dx", "roadmap"]
    assert aaa["links"] == [
        {
            "target_bead_id": "bd-bbb2",
            "link_type": "blocks",
            "created_at": "",
        }
    ]


def test_beads_limit_validation(populated_client: TestClient) -> None:
    # Out-of-range limit should be rejected by FastAPI validation.
    res = populated_client.get("/api/v1/pmo/beads", params={"limit": 0})
    assert res.status_code == 422
    res = populated_client.get("/api/v1/pmo/beads", params={"limit": 5000})
    assert res.status_code == 422
