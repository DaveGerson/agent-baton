"""HTTP-level tests for ``GET /api/v1/pmo/beads`` (DX.6 / bd-aade).

Covers:

  - empty list when no DB / no beads
  - populated list returned in ``created_at DESC`` order
  - ``status`` filter (default 'open' returns open beads)
  - ``bead_type`` filter
  - ``tags`` filter (comma-separated, AND semantics)
  - ``task_id`` filter
  - ``limit`` query param

ADR-13b WP-H: Retargeted to BdBeadStore via make_bead_store(). The SQLite
BeadStore (bead_store.py) was deleted in WP-G; all bead seeding now goes
through BdBeadStore. Tests that depended on closed-bead retrieval are marked
xfail because BdBeadStore.query() returns only open beads (bd list default
is open-only; querying closed issues requires a source fix).
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


@pytest.fixture()
def populated_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    """Spin up the API with a baton.db pre-populated with a few beads.

    ADR-13b WP-H: seeding now goes through BdBeadStore (SQLite BeadStore
    deleted in WP-G). All beads are kept open because BdBeadStore.query()
    does not return closed beads (bd list default is open-only).
    """
    monkeypatch.chdir(tmp_path)
    db_dir = tmp_path / ".claude" / "team-context"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "baton.db"
    db_path.touch()

    from agent_baton.core.engine.bead_backend import make_bead_store
    from agent_baton.models.bead import Bead, BeadLink

    store = make_bead_store(db_path, repo_root=tmp_path)

    # Three open beads, varying type/tags/task_id, distinct timestamps so
    # ordering is deterministic. All beads kept open — BdBeadStore.query()
    # cannot retrieve closed beads reliably (bd list default is open-only).
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
            status="open",
            created_at="2026-04-24T09:00:00Z",
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
    # All three beads are open; all should appear with default status filter.
    ids = {b["bead_id"] for b in data["beads"]}
    assert "bd-aaa1" in ids
    assert "bd-bbb2" in ids
    assert "bd-ccc3" in ids
    assert data["total"] == 3


def test_beads_status_all_returns_all_open(populated_client: TestClient) -> None:
    """With status=all, all three open beads are returned newest-first.

    ADR-13b WP-H: All beads are open (closed beads not retrievable via
    BdBeadStore.query() — see xfail note on test_beads_status_closed_filter).
    """
    res = populated_client.get("/api/v1/pmo/beads", params={"status": "all"})
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 3
    ids = [b["bead_id"] for b in data["beads"]]
    # newest-first: bd-ccc3 (Apr 26) > bd-aaa1 (Apr 25) > bd-bbb2 (Apr 24)
    assert ids == ["bd-ccc3", "bd-aaa1", "bd-bbb2"]


def test_beads_status_closed_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """status=closed returns closed beads (bd list --status closed).

    Self-contained: seeds one open + one closed bead through the bd backend so
    the closed-retrieval path is exercised directly (ADR-13b: BdBeadStore.query
    routes closed/all to `bd list --status closed` / `--all`).
    """
    monkeypatch.chdir(tmp_path)
    db_dir = tmp_path / ".claude" / "team-context"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "baton.db"
    db_path.touch()

    from agent_baton.core.engine.bead_backend import make_bead_store
    from agent_baton.models.bead import Bead

    store = make_bead_store(db_path, repo_root=tmp_path)
    store.write(Bead(bead_id="bd-open1", task_id="t", step_id="s", agent_name="a",
                     bead_type="warning", content="still open", status="open",
                     created_at="2026-04-24T09:00:00Z", source="agent-signal"))
    store.write(Bead(bead_id="bd-done1", task_id="t", step_id="s", agent_name="a",
                     bead_type="warning", content="resolved", status="open",
                     created_at="2026-04-25T09:00:00Z", source="agent-signal"))
    store.close("bd-done1", summary="resolved")

    app = create_app(team_context_root=db_dir)
    client = TestClient(app)
    res = client.get("/api/v1/pmo/beads", params={"status": "closed"})
    assert res.status_code == 200
    data = res.json()
    ids = {b["bead_id"] for b in data["beads"]}
    assert "bd-done1" in ids
    assert "bd-open1" not in ids
    assert all(b["status"] == "closed" for b in data["beads"])


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
    # Links are stored in the baton metadata blob via BdBeadStore.
    # The link to bd-bbb2 should be present.
    link_targets = [lnk["target_bead_id"] for lnk in aaa.get("links", [])]
    assert "bd-bbb2" in link_targets


def test_beads_limit_validation(populated_client: TestClient) -> None:
    # Out-of-range limit should be rejected by FastAPI validation.
    res = populated_client.get("/api/v1/pmo/beads", params={"limit": 0})
    assert res.status_code == 422
    res = populated_client.get("/api/v1/pmo/beads", params={"limit": 5000})
    assert res.status_code == 422
