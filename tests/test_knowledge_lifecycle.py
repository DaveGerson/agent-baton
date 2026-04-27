"""Tests for K2.3 — knowledge lifecycle: usage tracking, deprecation, retirement.

Covers:
- Schema migration v23 applies cleanly on a fresh DB and on a DB at v15+.
- KnowledgeLifecycle.record_usage bumps usage_count and updates last_used_at.
- KnowledgeLifecycle.mark_deprecated sets lifecycle_state, deprecated_at,
  and retire_after correctly.
- KnowledgeLifecycle.retire flips lifecycle_state to "retired".
- KnowledgeLifecycle.compute_staleness honours boundary cases for both
  the days-since-use threshold and the usage-count threshold.
- KnowledgeLifecycle.find_stale filters correctly.
- KnowledgeLifecycle.auto_retire_expired only retires items past their
  retire_after timestamp.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_baton.core.knowledge.lifecycle import KnowledgeLifecycle
from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import (
    MIGRATIONS,
    PROJECT_SCHEMA_DDL,
    SCHEMA_VERSION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Fresh project DB at the current SCHEMA_VERSION."""
    db = tmp_path / "baton.db"
    cm = ConnectionManager(db)
    cm.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    # Force connection so DDL + migrations apply.
    cm.get_connection()
    cm.close()
    return db


@pytest.fixture()
def lifecycle(db_path: Path) -> KnowledgeLifecycle:
    return KnowledgeLifecycle(db_path)


# ---------------------------------------------------------------------------
# Schema / migration
# ---------------------------------------------------------------------------

def test_schema_version_at_least_23() -> None:
    assert SCHEMA_VERSION >= 23, "K2.3 requires SCHEMA_VERSION >= 23"


def test_migration_v23_registered() -> None:
    assert 23 in MIGRATIONS, "Migration v23 must be registered"
    ddl = MIGRATIONS[23]
    assert "knowledge_items" in ddl
    assert "lifecycle_state" in ddl
    assert "usage_count" in ddl
    assert "last_used_at" in ddl
    assert "deprecated_at" in ddl
    assert "retire_after" in ddl


def test_fresh_db_has_knowledge_items_table(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='knowledge_items'"
        ).fetchall()
        assert rows, "knowledge_items table must exist on a fresh DB"
        # Verify columns
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(knowledge_items)"
        ).fetchall()}
        for col in (
            "knowledge_id", "pack_name", "doc_name",
            "lifecycle_state", "usage_count", "last_used_at",
            "deprecated_at", "retire_after",
        ):
            assert col in cols, f"Expected column {col!r} on knowledge_items"
    finally:
        conn.close()


def test_migration_v23_applies_on_v15_db(tmp_path: Path) -> None:
    """Simulate an older DB at v15; migration v23 must add the new table."""
    db = tmp_path / "old.db"
    # Create a v15 DB by writing the schema then forcing the version back.
    cm = ConnectionManager(db)
    cm.configure_schema(PROJECT_SCHEMA_DDL, 15)
    conn = cm.get_connection()
    # Drop knowledge_items if PROJECT_SCHEMA_DDL ever adds it; simulate the
    # pre-v23 state where the table did not exist.
    conn.execute("DROP TABLE IF EXISTS knowledge_items")
    conn.execute("UPDATE _schema_version SET version = 15")
    conn.commit()
    cm.close()

    # Re-open at the current version — should run migrations 16..23.
    cm2 = ConnectionManager(db)
    cm2.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    cm2.get_connection()
    cm2.close()

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='knowledge_items'"
        ).fetchall()
        assert rows, "Migration v23 must create knowledge_items"
        version = conn.execute(
            "SELECT version FROM _schema_version"
        ).fetchone()[0]
        assert version == SCHEMA_VERSION
    finally:
        conn.close()


def test_migration_v23_idempotent(tmp_path: Path) -> None:
    """Re-running migrations must not fail when knowledge_items already exists."""
    db = tmp_path / "idem.db"
    cm = ConnectionManager(db)
    cm.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    cm.get_connection()
    cm.close()
    # Open again at the same version — no-op path, must not raise.
    cm2 = ConnectionManager(db)
    cm2.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
    cm2.get_connection()
    cm2.close()


# ---------------------------------------------------------------------------
# record_usage
# ---------------------------------------------------------------------------

def test_record_usage_creates_row_if_missing(
    lifecycle: KnowledgeLifecycle,
) -> None:
    lifecycle.record_usage("pack-a/doc-1")
    info = lifecycle.compute_staleness("pack-a/doc-1")
    assert info["usage_count"] == 1


def test_record_usage_bumps_count(lifecycle: KnowledgeLifecycle) -> None:
    for _ in range(3):
        lifecycle.record_usage("pack-a/doc-1")
    info = lifecycle.compute_staleness("pack-a/doc-1")
    assert info["usage_count"] == 3


def test_record_usage_updates_last_used_at(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    lifecycle.record_usage("pack-a/doc-1")
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT last_used_at FROM knowledge_items "
            "WHERE knowledge_id = ?",
            ("pack-a/doc-1",),
        ).fetchone()
        assert row is not None and row[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# mark_deprecated
# ---------------------------------------------------------------------------

def test_mark_deprecated_sets_state_and_retire_after(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    lifecycle.mark_deprecated("pack-a/doc-1", grace_days=14, reason="API gone")
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT lifecycle_state, deprecated_at, retire_after, "
            "       deprecation_reason "
            "FROM knowledge_items WHERE knowledge_id = ?",
            ("pack-a/doc-1",),
        ).fetchone()
        assert row is not None
        state, dep_at, retire_after, reason = row
        assert state == "deprecated"
        assert dep_at  # non-empty timestamp
        assert retire_after  # non-empty timestamp
        assert reason == "API gone"
        # retire_after should be roughly 14 days after deprecated_at.
        dep_dt = datetime.strptime(dep_at, "%Y-%m-%dT%H:%M:%SZ")
        ret_dt = datetime.strptime(retire_after, "%Y-%m-%dT%H:%M:%SZ")
        delta_days = (ret_dt - dep_dt).days
        assert 13 <= delta_days <= 15
    finally:
        conn.close()


def test_mark_deprecated_default_grace_30_days(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    lifecycle.mark_deprecated("pack-a/doc-1")
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT deprecated_at, retire_after FROM knowledge_items "
            "WHERE knowledge_id = ?",
            ("pack-a/doc-1",),
        ).fetchone()
    finally:
        conn.close()
    dep_dt = datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%SZ")
    ret_dt = datetime.strptime(row[1], "%Y-%m-%dT%H:%M:%SZ")
    assert 29 <= (ret_dt - dep_dt).days <= 31


# ---------------------------------------------------------------------------
# retire
# ---------------------------------------------------------------------------

def test_retire_sets_state_retired(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    lifecycle.mark_deprecated("pack-a/doc-1", grace_days=0)
    lifecycle.retire("pack-a/doc-1")
    conn = sqlite3.connect(db_path)
    try:
        state = conn.execute(
            "SELECT lifecycle_state FROM knowledge_items "
            "WHERE knowledge_id = ?",
            ("pack-a/doc-1",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert state == "retired"


def test_retire_creates_row_for_unknown_id(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    """Manual retire on an unknown id should still record the retirement."""
    lifecycle.retire("pack-x/never-seen")
    conn = sqlite3.connect(db_path)
    try:
        state = conn.execute(
            "SELECT lifecycle_state FROM knowledge_items "
            "WHERE knowledge_id = ?",
            ("pack-x/never-seen",),
        ).fetchone()[0]
    finally:
        conn.close()
    assert state == "retired"


# ---------------------------------------------------------------------------
# compute_staleness — boundary cases
# ---------------------------------------------------------------------------

def _seed_item(
    db_path: Path,
    knowledge_id: str,
    *,
    last_used_at: datetime | None,
    usage_count: int,
    state: str = "active",
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO knowledge_items "
            "  (knowledge_id, pack_name, doc_name, lifecycle_state, "
            "   usage_count, last_used_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                knowledge_id,
                knowledge_id.split("/")[0],
                knowledge_id.split("/", 1)[1],
                state,
                usage_count,
                _utc_iso(last_used_at) if last_used_at else "",
                _utc_iso(_now()),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_compute_staleness_active_recent_high_usage(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    _seed_item(
        db_path, "pack/a",
        last_used_at=_now() - timedelta(days=10),
        usage_count=50,
    )
    info = lifecycle.compute_staleness("pack/a")
    assert info["is_stale"] is False
    assert info["usage_count"] == 50


def test_compute_staleness_old_and_unused_is_stale(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    _seed_item(
        db_path, "pack/b",
        last_used_at=_now() - timedelta(days=100),
        usage_count=2,
    )
    info = lifecycle.compute_staleness("pack/b")
    assert info["is_stale"] is True


def test_compute_staleness_old_but_popular_not_stale(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    """Boundary: days > threshold, but usage_count >= threshold => not stale."""
    _seed_item(
        db_path, "pack/c",
        last_used_at=_now() - timedelta(days=200),
        usage_count=99,
    )
    info = lifecycle.compute_staleness("pack/c")
    assert info["is_stale"] is False


def test_compute_staleness_exactly_at_threshold(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    """At exactly 90 days + exactly 5 uses => not stale (strict inequalities)."""
    _seed_item(
        db_path, "pack/d",
        last_used_at=_now() - timedelta(days=90),
        usage_count=5,
    )
    info = lifecycle.compute_staleness("pack/d")
    # Stale condition is `days > 90 AND usage_count < 5`, both strict — at
    # the boundary the item is considered fresh.
    assert info["is_stale"] is False


def test_compute_staleness_never_used(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    """An item with no last_used_at counts as stale once it exists past the
    threshold; for this test, treat 'never used' + zero usage as stale."""
    _seed_item(
        db_path, "pack/e",
        last_used_at=None,
        usage_count=0,
    )
    info = lifecycle.compute_staleness("pack/e")
    assert info["is_stale"] is True
    assert info["usage_count"] == 0


def test_compute_staleness_unknown_id_returns_zero_usage(
    lifecycle: KnowledgeLifecycle,
) -> None:
    info = lifecycle.compute_staleness("unknown/doc")
    assert info["usage_count"] == 0
    assert info["is_stale"] is False


# ---------------------------------------------------------------------------
# find_stale
# ---------------------------------------------------------------------------

def test_find_stale_returns_only_stale_active_items(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    _seed_item(
        db_path, "pack/old-unused",
        last_used_at=_now() - timedelta(days=120), usage_count=1,
    )
    _seed_item(
        db_path, "pack/old-popular",
        last_used_at=_now() - timedelta(days=120), usage_count=80,
    )
    _seed_item(
        db_path, "pack/recent",
        last_used_at=_now() - timedelta(days=5), usage_count=0,
    )
    _seed_item(
        db_path, "pack/already-deprecated",
        last_used_at=_now() - timedelta(days=300), usage_count=0,
        state="deprecated",
    )

    stale = lifecycle.find_stale()
    assert "pack/old-unused" in stale
    assert "pack/old-popular" not in stale
    assert "pack/recent" not in stale
    # Deprecated items are not re-surfaced as stale; user already acted.
    assert "pack/already-deprecated" not in stale


def test_find_stale_respects_custom_thresholds(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    _seed_item(
        db_path, "pack/x",
        last_used_at=_now() - timedelta(days=40), usage_count=2,
    )
    # Default thresholds (90/5) -> not stale; tighter thresholds -> stale.
    assert "pack/x" not in lifecycle.find_stale()
    assert "pack/x" in lifecycle.find_stale(stale_days=30, max_usage=3)


# ---------------------------------------------------------------------------
# auto_retire_expired
# ---------------------------------------------------------------------------

def test_auto_retire_expired_only_retires_past_grace(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    # Two deprecated items: one whose grace expired, one still in grace.
    expired_at = _utc_iso(_now() - timedelta(days=5))
    future_at = _utc_iso(_now() + timedelta(days=5))
    conn = sqlite3.connect(db_path)
    try:
        for kid, retire_after in (
            ("pack/expired", expired_at),
            ("pack/in-grace", future_at),
        ):
            conn.execute(
                "INSERT INTO knowledge_items "
                "  (knowledge_id, pack_name, doc_name, lifecycle_state, "
                "   usage_count, deprecated_at, retire_after, created_at) "
                "VALUES (?, ?, ?, 'deprecated', 0, ?, ?, ?)",
                (
                    kid,
                    kid.split("/")[0],
                    kid.split("/", 1)[1],
                    _utc_iso(_now() - timedelta(days=40)),
                    retire_after,
                    _utc_iso(_now() - timedelta(days=40)),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    retired = lifecycle.auto_retire_expired()
    assert retired == ["pack/expired"]

    conn = sqlite3.connect(db_path)
    try:
        rows = dict(conn.execute(
            "SELECT knowledge_id, lifecycle_state FROM knowledge_items"
        ).fetchall())
    finally:
        conn.close()
    assert rows["pack/expired"] == "retired"
    assert rows["pack/in-grace"] == "deprecated"


def test_auto_retire_expired_ignores_active_items(
    lifecycle: KnowledgeLifecycle, db_path: Path,
) -> None:
    _seed_item(
        db_path, "pack/active-old",
        last_used_at=_now() - timedelta(days=200), usage_count=0,
    )
    retired = lifecycle.auto_retire_expired()
    assert retired == []
