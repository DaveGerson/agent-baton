"""Tests for agent_baton.core.storage.derived_bead_store.DerivedBeadStore.

Coverage:
- Schema creation: tables bead_edges, bead_clusters, handoff_beads are present.
- edges_for: returns edges for given bead IDs; empty list when no match.
- clusters: returns all cluster rows.
- handoffs: returns handoff rows scoped to a task_id; empty for missing task.
- connection() context manager: writes committed on clean exit; rollback on error.
- Path creation: parent directories are created automatically.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_baton.core.storage.derived_bead_store import DerivedBeadStore
from agent_baton.utils.time import utcnow_zulu as _utcnow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> DerivedBeadStore:
    return DerivedBeadStore(tmp_path / "baton-derived.db")


def _seed_edge(conn: sqlite3.Connection, src: str, dst: str, edge_type: str = "file_overlap",
               weight: float = 0.5) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO bead_edges "
        "(src_bead_id, dst_bead_id, edge_type, weight, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (src, dst, edge_type, weight, _utcnow()),
    )


def _seed_cluster(conn: sqlite3.Connection, cluster_id: str, label: str,
                  bead_ids: list[str]) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO bead_clusters (cluster_id, label, bead_ids, created_at) "
        "VALUES (?, ?, ?, ?)",
        (cluster_id, label, json.dumps(bead_ids), _utcnow()),
    )


def _seed_handoff(conn: sqlite3.Connection, handoff_id: str, task_id: str,
                  from_step: str, to_step: str, content: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO handoff_beads "
        "(handoff_id, task_id, from_step_id, to_step_id, content, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (handoff_id, task_id, from_step, to_step, content, _utcnow()),
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema:
    def test_tables_created(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        conn = sqlite3.connect(str(tmp_path / "baton-derived.db"))
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "bead_edges" in names
        assert "bead_clusters" in names
        assert "handoff_beads" in names

    def test_parent_dirs_created(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c" / "baton-derived.db"
        store = DerivedBeadStore(deep)
        assert deep.exists()

    def test_idempotent_init(self, tmp_path: Path) -> None:
        """Constructing twice must not fail or corrupt the schema."""
        _make_store(tmp_path)
        _make_store(tmp_path)  # second time — tables already exist
        conn = sqlite3.connect(str(tmp_path / "baton-derived.db"))
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
            "AND name IN ('bead_edges', 'bead_clusters', 'handoff_beads')"
        ).fetchone()
        conn.close()
        assert row[0] == 3

    def test_bead_edges_columns(self, tmp_path: Path) -> None:
        _make_store(tmp_path)
        conn = sqlite3.connect(str(tmp_path / "baton-derived.db"))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bead_edges)").fetchall()}
        conn.close()
        assert {"src_bead_id", "dst_bead_id", "edge_type", "weight", "created_at"} <= cols

    def test_handoff_beads_columns(self, tmp_path: Path) -> None:
        _make_store(tmp_path)
        conn = sqlite3.connect(str(tmp_path / "baton-derived.db"))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(handoff_beads)").fetchall()}
        conn.close()
        assert {"handoff_id", "task_id", "from_step_id", "to_step_id",
                "content", "created_at"} <= cols


# ---------------------------------------------------------------------------
# edges_for
# ---------------------------------------------------------------------------


class TestEdgesFor:
    def test_returns_edges_for_src(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.connection() as conn:
            _seed_edge(conn, "bd-a", "bd-b")
        edges = store.edges_for(["bd-a"])
        assert len(edges) == 1
        assert edges[0]["src_bead_id"] == "bd-a"
        assert edges[0]["dst_bead_id"] == "bd-b"
        assert edges[0]["edge_type"] == "file_overlap"
        assert edges[0]["weight"] == pytest.approx(0.5)

    def test_returns_edges_for_dst(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.connection() as conn:
            _seed_edge(conn, "bd-x", "bd-y")
        edges = store.edges_for(["bd-y"])
        assert len(edges) == 1
        assert edges[0]["src_bead_id"] == "bd-x"
        assert edges[0]["dst_bead_id"] == "bd-y"

    def test_returns_edges_for_multiple_ids(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.connection() as conn:
            _seed_edge(conn, "bd-1", "bd-2")
            _seed_edge(conn, "bd-3", "bd-4")
        edges = store.edges_for(["bd-1", "bd-3"])
        ids = {(e["src_bead_id"], e["dst_bead_id"]) for e in edges}
        assert ("bd-1", "bd-2") in ids
        assert ("bd-3", "bd-4") in ids

    def test_empty_list_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.edges_for([]) == []

    def test_unknown_ids_return_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.edges_for(["bd-does-not-exist"]) == []


# ---------------------------------------------------------------------------
# clusters
# ---------------------------------------------------------------------------


class TestClusters:
    def test_returns_all_clusters(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.connection() as conn:
            _seed_cluster(conn, "bc-aaa", "auth", ["bd-1", "bd-2"])
            _seed_cluster(conn, "bc-bbb", "security", ["bd-3", "bd-4", "bd-5"])
        clusters = store.clusters()
        assert len(clusters) == 2
        ids = {c["cluster_id"] for c in clusters}
        assert "bc-aaa" in ids
        assert "bc-bbb" in ids

    def test_empty_when_no_clusters(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.clusters() == []

    def test_cluster_bead_ids_is_json_string(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.connection() as conn:
            _seed_cluster(conn, "bc-ccc", "testing", ["bd-a", "bd-b"])
        clusters = store.clusters()
        assert len(clusters) == 1
        parsed = json.loads(clusters[0]["bead_ids"])
        assert parsed == ["bd-a", "bd-b"]


# ---------------------------------------------------------------------------
# handoffs
# ---------------------------------------------------------------------------


class TestHandoffs:
    def test_returns_handoffs_for_task(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.connection() as conn:
            _seed_handoff(conn, "hf-001", "T-1", "step-1", "step-2", "handoff text A")
            _seed_handoff(conn, "hf-002", "T-1", "step-2", "step-3", "handoff text B")
            _seed_handoff(conn, "hf-003", "T-2", "step-1", "step-2", "other task")
        handoffs = store.handoffs("T-1")
        assert len(handoffs) == 2
        contents = {h["content"] for h in handoffs}
        assert "handoff text A" in contents
        assert "handoff text B" in contents

    def test_empty_for_missing_task(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.handoffs("task-none") == []

    def test_empty_task_id_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.handoffs("") == []

    def test_handoff_row_fields(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.connection() as conn:
            _seed_handoff(conn, "hf-xyz", "T-99", "s-a", "s-b", "the body")
        rows = store.handoffs("T-99")
        assert len(rows) == 1
        row = rows[0]
        assert row["handoff_id"] == "hf-xyz"
        assert row["task_id"] == "T-99"
        assert row["from_step_id"] == "s-a"
        assert row["to_step_id"] == "s-b"
        assert row["content"] == "the body"
        assert row["created_at"] != ""


# ---------------------------------------------------------------------------
# connection() context manager
# ---------------------------------------------------------------------------


class TestConnectionContextManager:
    def test_write_is_committed_on_clean_exit(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with store.connection() as conn:
            _seed_edge(conn, "bd-p", "bd-q")
        # Re-open directly to confirm the row persisted.
        conn2 = sqlite3.connect(str(tmp_path / "baton-derived.db"))
        rows = conn2.execute("SELECT COUNT(*) FROM bead_edges").fetchone()
        conn2.close()
        assert rows[0] == 1

    def test_rollback_on_exception(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(ValueError):
            with store.connection() as conn:
                _seed_edge(conn, "bd-r", "bd-s")
                raise ValueError("intentional failure")
        # Row should NOT have been committed.
        conn2 = sqlite3.connect(str(tmp_path / "baton-derived.db"))
        rows = conn2.execute("SELECT COUNT(*) FROM bead_edges").fetchone()
        conn2.close()
        assert rows[0] == 0
