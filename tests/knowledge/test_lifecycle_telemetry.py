"""Tests for F0.4 KnowledgeTelemetryStore — event emission and view query."""
from __future__ import annotations

import sqlite3
import pytest
from pathlib import Path

from agent_baton.core.engine.knowledge_telemetry import KnowledgeTelemetryStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path: Path) -> Path:
    db_path = tmp_path / "telemetry_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS knowledge_telemetry (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_name            TEXT NOT NULL DEFAULT '',
            pack_name           TEXT NOT NULL DEFAULT '',
            task_id             TEXT NOT NULL DEFAULT '',
            step_id             TEXT NOT NULL DEFAULT '',
            used_at             TEXT NOT NULL DEFAULT '',
            delivery            TEXT NOT NULL DEFAULT 'inline',
            outcome_correlation REAL
        );
        CREATE INDEX IF NOT EXISTS idx_kt_doc ON knowledge_telemetry(doc_name, pack_name);
        CREATE INDEX IF NOT EXISTS idx_kt_task ON knowledge_telemetry(task_id);

        CREATE TABLE IF NOT EXISTS knowledge_doc_meta (
            doc_name         TEXT NOT NULL,
            pack_name        TEXT NOT NULL DEFAULT '',
            last_modified    TEXT NOT NULL DEFAULT '',
            stale_after_days INTEGER NOT NULL DEFAULT 90,
            PRIMARY KEY (doc_name, pack_name)
        );

        CREATE VIEW IF NOT EXISTS v_knowledge_effectiveness AS
        SELECT
            kt.doc_name,
            kt.pack_name,
            COUNT(*) AS total_uses,
            ROUND(AVG(CASE WHEN kt.outcome_correlation IS NOT NULL
                      THEN kt.outcome_correlation ELSE NULL END), 4) AS avg_outcome_score,
            dm.last_modified,
            dm.stale_after_days,
            CAST(julianday('now') - julianday(NULLIF(dm.last_modified, '')) AS INTEGER)
                AS days_since_modified
        FROM knowledge_telemetry kt
        LEFT JOIN knowledge_doc_meta dm ON dm.doc_name = kt.doc_name AND dm.pack_name = kt.pack_name
        GROUP BY kt.doc_name, kt.pack_name;
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def store(db: Path) -> KnowledgeTelemetryStore:
    return KnowledgeTelemetryStore(db_path=db)


# ---------------------------------------------------------------------------
# record_used
# ---------------------------------------------------------------------------

def test_record_used_inserts_row(store: KnowledgeTelemetryStore, db: Path) -> None:
    row_id = store.record_used(
        doc_name="baton-patterns",
        pack_name="core-docs",
        task_id="task-001",
        step_id="step-1",
        delivery="inline",
    )
    assert row_id > 0
    count = store.doc_usage_count("baton-patterns", "core-docs")
    assert count == 1


def test_record_used_multiple_increments_count(store: KnowledgeTelemetryStore) -> None:
    for i in range(3):
        store.record_used(doc_name="my-doc", pack_name="my-pack", task_id=f"task-{i}")
    assert store.doc_usage_count("my-doc", "my-pack") == 3


def test_record_used_default_delivery_is_inline(store: KnowledgeTelemetryStore, db: Path) -> None:
    store.record_used(doc_name="d", pack_name="p", task_id="t")
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT delivery FROM knowledge_telemetry WHERE doc_name='d'"
    ).fetchone()
    conn.close()
    assert row[0] == "inline"


# ---------------------------------------------------------------------------
# record_outcome
# ---------------------------------------------------------------------------

def test_record_outcome_updates_existing_row(store: KnowledgeTelemetryStore, db: Path) -> None:
    store.record_used(doc_name="doc-a", pack_name="pack-a", task_id="task-x")
    updated = store.record_outcome(
        doc_name="doc-a", pack_name="pack-a", task_id="task-x",
        outcome_correlation=0.85,
    )
    assert updated >= 1
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT outcome_correlation FROM knowledge_telemetry WHERE doc_name='doc-a'"
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(0.85)


def test_record_outcome_inserts_when_no_prior_row(store: KnowledgeTelemetryStore, db: Path) -> None:
    # No prior record_used call
    store.record_outcome(
        doc_name="new-doc", pack_name="", task_id="task-y",
        outcome_correlation=0.6,
    )
    count = store.doc_usage_count("new-doc", "")
    assert count >= 1


# ---------------------------------------------------------------------------
# upsert_doc_meta
# ---------------------------------------------------------------------------

def test_upsert_doc_meta_insert(store: KnowledgeTelemetryStore, db: Path) -> None:
    store.upsert_doc_meta("doc-b", "pack-b", last_modified="2026-01-01T00:00:00Z",
                          stale_after_days=30)
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT stale_after_days, last_modified FROM knowledge_doc_meta WHERE doc_name='doc-b'"
    ).fetchone()
    conn.close()
    assert row[0] == 30
    assert row[1] == "2026-01-01T00:00:00Z"


def test_upsert_doc_meta_update(store: KnowledgeTelemetryStore, db: Path) -> None:
    store.upsert_doc_meta("doc-c", "pack-c", last_modified="2026-01-01", stale_after_days=90)
    store.upsert_doc_meta("doc-c", "pack-c", last_modified="2026-04-01", stale_after_days=60)
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT stale_after_days, last_modified FROM knowledge_doc_meta WHERE doc_name='doc-c'"
    ).fetchone()
    conn.close()
    assert row[0] == 60
    assert row[1] == "2026-04-01"


# ---------------------------------------------------------------------------
# effectiveness_summary (view query)
# ---------------------------------------------------------------------------

def test_effectiveness_summary_returns_rows(store: KnowledgeTelemetryStore) -> None:
    store.record_used(doc_name="wiki", pack_name="core", task_id="t1")
    store.record_used(doc_name="wiki", pack_name="core", task_id="t2")
    store.record_outcome(doc_name="wiki", pack_name="core", task_id="t1",
                         outcome_correlation=1.0)
    rows = store.effectiveness_summary()
    assert len(rows) >= 1
    wiki_row = next((r for r in rows if r["doc_name"] == "wiki"), None)
    assert wiki_row is not None
    assert wiki_row["total_uses"] >= 2


def test_effectiveness_summary_empty_db(store: KnowledgeTelemetryStore) -> None:
    rows = store.effectiveness_summary()
    assert rows == []


def test_effectiveness_summary_avg_outcome_score(store: KnowledgeTelemetryStore) -> None:
    store.record_used(doc_name="scored-doc", pack_name="p", task_id="t1")
    store.record_used(doc_name="scored-doc", pack_name="p", task_id="t2")
    store.record_outcome(doc_name="scored-doc", pack_name="p", task_id="t1",
                         outcome_correlation=0.8)
    store.record_outcome(doc_name="scored-doc", pack_name="p", task_id="t2",
                         outcome_correlation=0.6)
    rows = store.effectiveness_summary()
    scored = next(r for r in rows if r["doc_name"] == "scored-doc")
    # avg of 0.8 and 0.6 = 0.7
    assert scored["avg_outcome_score"] == pytest.approx(0.7, abs=0.01)


def test_doc_usage_count_nonexistent_returns_zero(store: KnowledgeTelemetryStore) -> None:
    assert store.doc_usage_count("no-such-doc", "no-such-pack") == 0
