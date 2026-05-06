"""Tests for K2.4 — knowledge A/B testing.

Covers:
1. Migration applies cleanly on a fresh DB (tables exist, schema version 25).
2. Create experiment + roundtrip via get_experiment.
3. Assignment is deterministic per (task_id, step_id).
4. Distribution: 1000 random task_ids gives ~split_ratio (within ±10%).
5. Record outcome -> compute_results reflects it.
6. Winner detection: A=20 success, B=0/20 -> winner='a'.
7. Insufficient samples -> winner=None.
8. Stop sets status + stopped_at.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from agent_baton.core.knowledge.ab_testing import (
    KnowledgeABService,
    _deterministic_variant,
)
from agent_baton.core.storage.schema import MIGRATIONS, PROJECT_SCHEMA_DDL, SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _apply_schema(conn: sqlite3.Connection) -> None:
    """Apply PROJECT_SCHEMA_DDL and all pending migrations to a fresh DB."""
    conn.executescript(PROJECT_SCHEMA_DDL)
    # Simulate the migrations engine: apply every migration up to SCHEMA_VERSION.
    for version in sorted(MIGRATIONS.keys()):
        try:
            conn.executescript(MIGRATIONS[version])
        except sqlite3.OperationalError:
            # Idempotent: column/table already exists from DDL — skip.
            pass
    conn.commit()


@pytest.fixture()
def conn() -> sqlite3.Connection:
    """In-memory SQLite connection with the full project schema applied."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    _apply_schema(c)
    return c


@pytest.fixture()
def svc(conn: sqlite3.Connection) -> KnowledgeABService:
    return KnowledgeABService(conn)


# ---------------------------------------------------------------------------
# Test 1: migration creates tables and version is 25
# ---------------------------------------------------------------------------

def test_migration_creates_tables(conn: sqlite3.Connection) -> None:
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "knowledge_ab_experiments" in tables
    assert "knowledge_ab_assignments" in tables


def test_k24_migration_is_v25() -> None:
    """K2.4 reserved schema version v25; SCHEMA_VERSION moves forward as
    other waves land migrations on top of v25 (e.g. R3.8 ships v26)."""
    assert SCHEMA_VERSION >= 25
    assert 25 in MIGRATIONS


# ---------------------------------------------------------------------------
# Test 2: create experiment + roundtrip
# ---------------------------------------------------------------------------

def test_create_and_get_experiment(svc: KnowledgeABService) -> None:
    exp_id = svc.create_experiment(
        knowledge_id="security/owasp.md",
        variant_a_path="knowledge/owasp-v1.md",
        variant_b_path="knowledge/owasp-v2.md",
        split_ratio=0.5,
    )
    assert exp_id  # non-empty UUID string

    exp = svc.get_experiment(exp_id)
    assert exp is not None
    assert exp.experiment_id == exp_id
    assert exp.knowledge_id == "security/owasp.md"
    assert exp.variant_a_path == "knowledge/owasp-v1.md"
    assert exp.variant_b_path == "knowledge/owasp-v2.md"
    assert exp.split_ratio == 0.5
    assert exp.status == "active"
    assert exp.started_at != ""
    assert exp.stopped_at == ""


def test_get_nonexistent_experiment_returns_none(svc: KnowledgeABService) -> None:
    assert svc.get_experiment("no-such-id") is None


# ---------------------------------------------------------------------------
# Test 3: assignment is deterministic per (task_id, step_id)
# ---------------------------------------------------------------------------

def test_assignment_is_deterministic(svc: KnowledgeABService) -> None:
    exp_id = svc.create_experiment("k/doc.md", "a.md", "b.md")
    task_id = "task-abc"
    step_id = "step-1"

    first = svc.assign_variant(exp_id, task_id, step_id)
    # Second call must return identical value.
    second = svc.assign_variant(exp_id, task_id, step_id)
    assert first == second
    assert first in ("a", "b")


def test_assignment_idempotent_no_duplicate_rows(
    svc: KnowledgeABService, conn: sqlite3.Connection
) -> None:
    exp_id = svc.create_experiment("k/doc.md", "a.md", "b.md")
    for _ in range(5):
        svc.assign_variant(exp_id, "task-xyz", "step-0")

    count = conn.execute(
        "SELECT COUNT(*) FROM knowledge_ab_assignments "
        "WHERE experiment_id = ? AND task_id = ? AND step_id = ?",
        (exp_id, "task-xyz", "step-0"),
    ).fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# Test 4: distribution ~= split_ratio for 1000 task_ids
# ---------------------------------------------------------------------------

def test_distribution_matches_split_ratio() -> None:
    split = 0.3
    n = 1000
    a_count = sum(
        1
        for i in range(n)
        if _deterministic_variant(f"task-{i}", "", split) == "a"
    )
    ratio = a_count / n
    # Allow ±10 percentage points tolerance.
    assert abs(ratio - split) < 0.10, f"ratio={ratio:.3f}, expected ~{split}"


def test_distribution_50_50() -> None:
    n = 1000
    a_count = sum(
        1
        for i in range(n)
        if _deterministic_variant(f"task-dist-{i}", "s", 0.5) == "a"
    )
    ratio = a_count / n
    assert abs(ratio - 0.5) < 0.10, f"ratio={ratio:.3f}"


# ---------------------------------------------------------------------------
# Test 5: record_outcome -> compute_results reflects it
# ---------------------------------------------------------------------------

def test_record_outcome_reflected_in_results(svc: KnowledgeABService) -> None:
    exp_id = svc.create_experiment("k/doc.md", "a.md", "b.md", split_ratio=0.5)

    # Seed 6 A-successes and 4 A-failures via force-testing _deterministic_variant.
    # We drive outcomes directly rather than relying on split assignment so the
    # test is not fragile to hash distribution changes.
    for i in range(6):
        tid = f"success-a-{i}"
        # Patch: directly insert an "a" assignment.
        svc._get_conn().execute(
            "INSERT INTO knowledge_ab_assignments "
            "(experiment_id, task_id, step_id, variant, assigned_at, outcome) "
            "VALUES (?, ?, '', 'a', '2026-01-01T00:00:00Z', '')",
            (exp_id, tid),
        )
        svc._get_conn().commit()
        svc.record_outcome(exp_id, tid, "success")

    for i in range(4):
        tid = f"fail-a-{i}"
        svc._get_conn().execute(
            "INSERT INTO knowledge_ab_assignments "
            "(experiment_id, task_id, step_id, variant, assigned_at, outcome) "
            "VALUES (?, ?, '', 'a', '2026-01-01T00:00:00Z', '')",
            (exp_id, tid),
        )
        svc._get_conn().commit()
        svc.record_outcome(exp_id, tid, "failure")

    results = svc.compute_results(exp_id)
    assert results["a_count"] == 10
    assert results["b_count"] == 0
    assert abs(results["a_success_rate"] - 0.6) < 0.001


# ---------------------------------------------------------------------------
# Test 6: winner detection — A dominates
# ---------------------------------------------------------------------------

def _seed_outcomes(
    svc: KnowledgeABService,
    exp_id: str,
    variant: str,
    successes: int,
    failures: int,
) -> None:
    conn = svc._get_conn()
    for i in range(successes):
        tid = f"{variant}-win-{uuid.uuid4()}"
        conn.execute(
            "INSERT INTO knowledge_ab_assignments "
            "(experiment_id, task_id, step_id, variant, assigned_at, outcome) "
            "VALUES (?, ?, '', ?, '2026-01-01T00:00:00Z', 'success')",
            (exp_id, tid, variant),
        )
    for i in range(failures):
        tid = f"{variant}-fail-{uuid.uuid4()}"
        conn.execute(
            "INSERT INTO knowledge_ab_assignments "
            "(experiment_id, task_id, step_id, variant, assigned_at, outcome) "
            "VALUES (?, ?, '', ?, '2026-01-01T00:00:00Z', 'failure')",
            (exp_id, tid, variant),
        )
    conn.commit()


def test_winner_a_detected(svc: KnowledgeABService) -> None:
    exp_id = svc.create_experiment("k/doc.md", "a.md", "b.md")
    _seed_outcomes(svc, exp_id, "a", successes=20, failures=0)
    _seed_outcomes(svc, exp_id, "b", successes=0, failures=20)

    results = svc.compute_results(exp_id)
    assert results["winner"] == "a"
    assert results["a_success_rate"] == 1.0
    assert results["b_success_rate"] == 0.0


def test_winner_b_detected(svc: KnowledgeABService) -> None:
    exp_id = svc.create_experiment("k/doc.md", "a.md", "b.md")
    _seed_outcomes(svc, exp_id, "a", successes=0, failures=20)
    _seed_outcomes(svc, exp_id, "b", successes=20, failures=0)

    results = svc.compute_results(exp_id)
    assert results["winner"] == "b"


# ---------------------------------------------------------------------------
# Test 7: insufficient samples -> winner=None
# ---------------------------------------------------------------------------

def test_insufficient_samples_no_winner(svc: KnowledgeABService) -> None:
    exp_id = svc.create_experiment("k/doc.md", "a.md", "b.md")
    # Only 5 samples each — below the 10-sample threshold.
    _seed_outcomes(svc, exp_id, "a", successes=5, failures=0)
    _seed_outcomes(svc, exp_id, "b", successes=0, failures=5)

    results = svc.compute_results(exp_id)
    assert results["winner"] is None
    assert results["a_count"] == 5
    assert results["b_count"] == 5


def test_tied_no_winner(svc: KnowledgeABService) -> None:
    exp_id = svc.create_experiment("k/doc.md", "a.md", "b.md")
    # Equal rates — margin is 0%, below 10% threshold.
    _seed_outcomes(svc, exp_id, "a", successes=10, failures=0)
    _seed_outcomes(svc, exp_id, "b", successes=10, failures=0)

    results = svc.compute_results(exp_id)
    assert results["winner"] is None


# ---------------------------------------------------------------------------
# Test 8: stop sets status + stopped_at
# ---------------------------------------------------------------------------

def test_stop_experiment(svc: KnowledgeABService) -> None:
    exp_id = svc.create_experiment("k/doc.md", "a.md", "b.md")
    assert svc.get_experiment(exp_id).status == "active"

    svc.stop_experiment(exp_id)

    exp = svc.get_experiment(exp_id)
    assert exp.status == "stopped"
    assert exp.stopped_at != ""


def test_list_experiments(svc: KnowledgeABService) -> None:
    id1 = svc.create_experiment("k/a.md", "a1.md", "b1.md")
    id2 = svc.create_experiment("k/b.md", "a2.md", "b2.md")

    exps = svc.list_experiments()
    ids = [e.experiment_id for e in exps]
    assert id1 in ids
    assert id2 in ids
