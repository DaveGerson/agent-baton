"""Tests for :class:`agent_baton.core.storage.release_store.ReleaseStore` (R3.1).

Coverage:
- Schema bootstrap: ``releases`` table exists; ``plans.release_id`` column
  is added on ALTER (migration v16).
- ``create`` returns the release_id and persists every column.
- ``create`` is idempotent (INSERT OR REPLACE) for the same release_id.
- ``get`` returns a fully-hydrated Release; ``None`` for missing.
- ``list`` returns all rows sorted by target_date asc, empty target_date last.
- ``list(status=...)`` filters.
- ``update_status`` validates against ``RELEASE_STATUSES`` and rowcount.
- ``tag_plan`` / ``untag_plan`` toggle ``plans.release_id`` for an existing plan
  and report rowcount semantics for unknown plan_ids.
- ``list_plans_for_release`` returns only tagged plans.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_baton.core.storage.release_store import ReleaseStore
from agent_baton.models.release import Release


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_execution(db_path: Path, task_id: str) -> None:
    """Insert a minimal executions row so plans-table FKs pass."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        "INSERT OR IGNORE INTO executions "
        "(task_id, status, current_phase, current_step_index, started_at, "
        " created_at, updated_at) "
        "VALUES (?, 'running', 0, 0, '2026-01-01T00:00:00Z', "
        "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (task_id,),
    )
    conn.commit()
    conn.close()


def _seed_plan(db_path: Path, task_id: str, summary: str = "test plan") -> None:
    """Insert a minimal plans row for tagging tests."""
    _seed_execution(db_path, task_id)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute(
        """
        INSERT OR REPLACE INTO plans
            (task_id, task_summary, risk_level, budget_tier,
             execution_mode, git_strategy, shared_context,
             pattern_source, plan_markdown, created_at,
             explicit_knowledge_packs, explicit_knowledge_docs,
             intervention_level, task_type)
        VALUES (?, ?, 'LOW', 'standard', 'phased', 'commit-per-agent',
                '', NULL, '', '2026-01-01T00:00:00Z',
                '[]', '[]', 'low', NULL)
        """,
        (task_id, summary),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def store(tmp_path: Path) -> ReleaseStore:
    db = tmp_path / "baton.db"
    s = ReleaseStore(db)
    # Force schema to disk so subsequent connections can see it.
    s._conn()
    return s


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------


class TestSchema:
    def test_releases_table_exists(self, store: ReleaseStore) -> None:
        conn = store._conn()
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='releases'"
        ).fetchone()
        assert row is not None

    def test_plans_release_id_column_present(self, store: ReleaseStore) -> None:
        conn = store._conn()
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(plans)").fetchall()}
        assert "release_id" in cols


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestCreateGetList:
    def test_create_returns_release_id(self, store: ReleaseStore) -> None:
        rel = Release(release_id="v2.5.0", name="Q2 Stability", target_date="2026-06-30")
        assert store.create(rel) == "v2.5.0"

    def test_create_persists_all_columns(self, store: ReleaseStore) -> None:
        rel = Release(
            release_id="v2.5.0",
            name="Q2 Stability",
            target_date="2026-06-30",
            status="active",
            notes="reliability theme",
        )
        store.create(rel)
        got = store.get("v2.5.0")
        assert got is not None
        assert got.name == "Q2 Stability"
        assert got.target_date == "2026-06-30"
        assert got.status == "active"
        assert got.notes == "reliability theme"
        assert got.created_at  # non-empty

    def test_get_returns_none_for_missing(self, store: ReleaseStore) -> None:
        assert store.get("does-not-exist") is None

    def test_create_is_idempotent(self, store: ReleaseStore) -> None:
        store.create(Release(release_id="v1.0", name="first"))
        store.create(Release(release_id="v1.0", name="renamed"))
        got = store.get("v1.0")
        assert got is not None
        assert got.name == "renamed"

    def test_list_returns_all(self, store: ReleaseStore) -> None:
        store.create(Release(release_id="v1.0"))
        store.create(Release(release_id="v2.0", target_date="2026-06-30"))
        store.create(Release(release_id="v3.0", target_date="2026-03-15"))
        rels = store.list()
        ids = [r.release_id for r in rels]
        # Dated releases sort by date asc; undated last
        assert ids == ["v3.0", "v2.0", "v1.0"]

    def test_list_filters_by_status(self, store: ReleaseStore) -> None:
        store.create(Release(release_id="v1.0", status="planned"))
        store.create(Release(release_id="v2.0", status="active"))
        store.create(Release(release_id="v3.0", status="released"))
        active = store.list(status="active")
        assert [r.release_id for r in active] == ["v2.0"]
        planned = store.list(status="planned")
        assert [r.release_id for r in planned] == ["v1.0"]
        none_match = store.list(status="cancelled")
        assert none_match == []

    def test_list_empty(self, store: ReleaseStore) -> None:
        assert store.list() == []


# ---------------------------------------------------------------------------
# update_status
# ---------------------------------------------------------------------------


class TestUpdateStatus:
    def test_transitions(self, store: ReleaseStore) -> None:
        store.create(Release(release_id="v1.0", status="planned"))
        assert store.update_status("v1.0", "active") is True
        assert store.get("v1.0").status == "active"
        assert store.update_status("v1.0", "released") is True
        assert store.get("v1.0").status == "released"

    def test_unknown_release_returns_false(self, store: ReleaseStore) -> None:
        assert store.update_status("missing", "active") is False

    def test_invalid_status_raises(self, store: ReleaseStore) -> None:
        store.create(Release(release_id="v1.0"))
        with pytest.raises(ValueError):
            store.update_status("v1.0", "shipped")  # not in RELEASE_STATUSES


# ---------------------------------------------------------------------------
# Plan tagging
# ---------------------------------------------------------------------------


class TestPlanTagging:
    def test_tag_plan_sets_release_id(self, store: ReleaseStore) -> None:
        _seed_plan(store.db_path, "task-001")
        store.create(Release(release_id="v2.5.0"))
        assert store.tag_plan("task-001", "v2.5.0") is True

        conn = store._conn()
        row = conn.execute(
            "SELECT release_id FROM plans WHERE task_id = ?", ("task-001",)
        ).fetchone()
        assert row["release_id"] == "v2.5.0"

    def test_tag_plan_unknown_plan_returns_false(self, store: ReleaseStore) -> None:
        store.create(Release(release_id="v2.5.0"))
        assert store.tag_plan("ghost-task", "v2.5.0") is False

    def test_untag_plan_clears_release_id(self, store: ReleaseStore) -> None:
        _seed_plan(store.db_path, "task-002")
        store.create(Release(release_id="v2.5.0"))
        store.tag_plan("task-002", "v2.5.0")
        assert store.untag_plan("task-002") is True

        conn = store._conn()
        row = conn.execute(
            "SELECT release_id FROM plans WHERE task_id = ?", ("task-002",)
        ).fetchone()
        assert row["release_id"] is None

    def test_untag_unknown_plan_returns_false(self, store: ReleaseStore) -> None:
        assert store.untag_plan("ghost-task") is False

    def test_list_plans_for_release(self, store: ReleaseStore) -> None:
        _seed_plan(store.db_path, "task-A", summary="alpha")
        _seed_plan(store.db_path, "task-B", summary="beta")
        _seed_plan(store.db_path, "task-C", summary="gamma")
        store.create(Release(release_id="v1.0"))
        store.create(Release(release_id="v2.0"))

        store.tag_plan("task-A", "v1.0")
        store.tag_plan("task-C", "v1.0")
        store.tag_plan("task-B", "v2.0")

        v1_plans = store.list_plans_for_release("v1.0")
        v1_ids = sorted(p["task_id"] for p in v1_plans)
        assert v1_ids == ["task-A", "task-C"]

        v2_plans = store.list_plans_for_release("v2.0")
        assert [p["task_id"] for p in v2_plans] == ["task-B"]

    def test_list_plans_for_release_empty(self, store: ReleaseStore) -> None:
        store.create(Release(release_id="v9.9"))
        assert store.list_plans_for_release("v9.9") == []

    def test_tagging_does_not_affect_other_plan_columns(
        self, store: ReleaseStore
    ) -> None:
        _seed_plan(store.db_path, "task-D", summary="payload check")
        store.create(Release(release_id="v1.0"))
        store.tag_plan("task-D", "v1.0")
        conn = store._conn()
        row = conn.execute(
            "SELECT task_summary, risk_level, release_id FROM plans "
            "WHERE task_id = ?",
            ("task-D",),
        ).fetchone()
        assert row["task_summary"] == "payload check"
        assert row["risk_level"] == "LOW"
        assert row["release_id"] == "v1.0"
