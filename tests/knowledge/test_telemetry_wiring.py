"""Tests that KnowledgeResolver and RetrospectiveEngine wire into F0.4 telemetry.

Covers bd-32d3 — the F0.4 callsite wiring follow-up.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_baton.core.engine.knowledge_resolver import KnowledgeResolver
from agent_baton.core.engine.knowledge_telemetry import KnowledgeTelemetryStore
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.models.knowledge import KnowledgeDocument, KnowledgePack
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path: Path) -> Path:
    """Provision an empty SQLite DB pre-populated with the F0.4 schema."""
    db_path = tmp_path / "telemetry.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
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
                      THEN kt.outcome_correlation ELSE NULL END), 4)
                AS avg_outcome_score,
            dm.last_modified,
            dm.stale_after_days,
            CAST(julianday('now') - julianday(NULLIF(dm.last_modified, ''))
                 AS INTEGER) AS days_since_modified
        FROM knowledge_telemetry kt
        LEFT JOIN knowledge_doc_meta dm
            ON dm.doc_name = kt.doc_name AND dm.pack_name = kt.pack_name
        GROUP BY kt.doc_name, kt.pack_name;
        """
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def telemetry(db: Path) -> KnowledgeTelemetryStore:
    return KnowledgeTelemetryStore(db_path=db)


def _make_registry_with_doc(
    doc_name: str = "alpha", pack_name: str = "p1"
) -> KnowledgeRegistry:
    """Build a registry with one pack containing one explicit-friendly doc."""
    reg = KnowledgeRegistry()
    doc = KnowledgeDocument(
        name=doc_name,
        description="test doc",
        token_estimate=100,
        priority="normal",
    )
    pack = KnowledgePack(
        name=pack_name,
        description="test pack",
        documents=[doc],
    )
    # KnowledgeRegistry has no public add_pack; populate the index directly.
    reg._packs[pack.name] = pack  # noqa: SLF001 — test-only registry seeding
    return reg


# ---------------------------------------------------------------------------
# Resolver wiring
# ---------------------------------------------------------------------------

def test_resolver_records_used_for_each_attachment(
    telemetry: KnowledgeTelemetryStore, db: Path
) -> None:
    """KnowledgeResolver.resolve() should bump usage_count via telemetry."""
    registry = _make_registry_with_doc("alpha", "p1")
    resolver = KnowledgeResolver(registry, telemetry=telemetry)

    attachments = resolver.resolve(
        agent_name="any-agent",
        task_description="anything",
        explicit_packs=["p1"],
        task_id="task-A",
        step_id="step-1",
    )

    assert len(attachments) == 1
    assert telemetry.doc_usage_count("alpha", "p1") == 1


def test_resolver_without_telemetry_is_noop(db: Path) -> None:
    """When telemetry is None, resolution must succeed and write nothing."""
    registry = _make_registry_with_doc("beta", "p2")
    resolver = KnowledgeResolver(registry, telemetry=None)

    attachments = resolver.resolve(
        agent_name="any-agent",
        task_description="anything",
        explicit_packs=["p2"],
    )

    assert len(attachments) == 1
    # No telemetry store provided — table should be empty.
    conn = sqlite3.connect(str(db))
    rows = conn.execute("SELECT COUNT(*) FROM knowledge_telemetry").fetchone()
    conn.close()
    assert rows[0] == 0


def test_resolver_telemetry_failure_does_not_crash(
    tmp_path: Path,
) -> None:
    """A broken telemetry store must not propagate exceptions to the resolver caller."""

    class _Broken:
        def record_used(self, **_kwargs):  # noqa: ANN003
            raise RuntimeError("simulated DB outage")

    registry = _make_registry_with_doc("gamma", "p3")
    resolver = KnowledgeResolver(registry, telemetry=_Broken())  # type: ignore[arg-type]

    # Must not raise.
    attachments = resolver.resolve(
        agent_name="any-agent",
        task_description="anything",
        explicit_packs=["p3"],
    )
    assert len(attachments) == 1


# ---------------------------------------------------------------------------
# Retrospective wiring
# ---------------------------------------------------------------------------

def _usage_with_gates(task_id: str, passed: int, failed: int) -> TaskUsageRecord:
    return TaskUsageRecord(
        task_id=task_id,
        timestamp="2026-04-25T00:00:00Z",
        risk_level="LOW",
        agents_used=[
            AgentUsageRecord(
                name="planner",
                model="sonnet",
                estimated_tokens=1000,
                retries=0,
            )
        ],
        gates_passed=passed,
        gates_failed=failed,
    )


def test_retrospective_emits_outcome_for_attached_docs(
    telemetry: KnowledgeTelemetryStore, tmp_path: Path
) -> None:
    """RetrospectiveEngine should populate outcome_correlation for attached docs."""
    # First, a usage row exists from the resolver:
    telemetry.record_used(
        doc_name="alpha", pack_name="p1", task_id="task-A", step_id="s1"
    )

    engine = RetrospectiveEngine(
        retrospectives_dir=tmp_path / "retros",
        telemetry=telemetry,
    )
    usage = _usage_with_gates("task-A", passed=3, failed=1)

    engine.generate_from_usage(
        usage,
        task_name="task-A",
        attached_docs=[("alpha", "p1")],
    )

    rows = telemetry.effectiveness_summary()
    alpha = next(r for r in rows if r["doc_name"] == "alpha")
    # 3 passed + 1 failed = 0.75
    assert alpha["avg_outcome_score"] == pytest.approx(0.75, abs=0.01)


def test_retrospective_without_telemetry_is_noop(tmp_path: Path) -> None:
    """Engine generates retros normally when telemetry is None."""
    engine = RetrospectiveEngine(
        retrospectives_dir=tmp_path / "retros", telemetry=None
    )
    usage = _usage_with_gates("task-Z", passed=1, failed=0)
    retro = engine.generate_from_usage(
        usage, attached_docs=[("any", "any")]
    )
    assert retro.task_id == "task-Z"


def test_retrospective_telemetry_failure_does_not_crash(tmp_path: Path) -> None:
    """A broken telemetry store must not propagate from generate_from_usage."""

    class _Broken:
        def record_outcome(self, **_kwargs):  # noqa: ANN003
            raise RuntimeError("simulated DB outage")

    engine = RetrospectiveEngine(
        retrospectives_dir=tmp_path / "retros",
        telemetry=_Broken(),  # type: ignore[arg-type]
    )
    usage = _usage_with_gates("task-X", passed=1, failed=0)
    retro = engine.generate_from_usage(
        usage, attached_docs=[("doc", "pack")]
    )
    assert retro.task_id == "task-X"


# ---------------------------------------------------------------------------
# End-to-end: 3 hits + 3 outcomes → view shows 3 rows with correlation
# ---------------------------------------------------------------------------

def test_end_to_end_resolver_then_retro_populates_view(
    telemetry: KnowledgeTelemetryStore, tmp_path: Path
) -> None:
    """3 resolver hits across 3 docs, then 3 retro outcomes → view has 3 rows."""
    registry = KnowledgeRegistry()
    docs = [
        KnowledgeDocument(name=f"doc{i}", description="d", token_estimate=100)
        for i in range(3)
    ]
    pack = KnowledgePack(name="bundle", description="d", documents=docs)
    registry._packs[pack.name] = pack  # noqa: SLF001 — test-only registry seeding

    resolver = KnowledgeResolver(registry, telemetry=telemetry)
    retro_engine = RetrospectiveEngine(
        retrospectives_dir=tmp_path / "retros", telemetry=telemetry
    )

    # 3 resolver hits — one per doc, all under the same task.
    for i in range(3):
        resolver.resolve(
            agent_name="agent",
            task_description="run",
            explicit_docs=[],
            explicit_packs=["bundle"]
            if i == 0
            else [],  # only first call resolves all 3 (one pack)
            task_id="task-E2E",
            step_id=f"step-{i}",
        )
        # subsequent calls without explicit_packs resolve 0 — we want 3 hits
        # by attaching the same pack repeatedly across distinct steps:
        if i > 0:
            resolver.resolve(
                agent_name="agent",
                task_description="run",
                explicit_packs=["bundle"],
                task_id="task-E2E",
                step_id=f"step-{i}",
            )

    # Retrospective: emit outcomes for each doc.
    usage = _usage_with_gates("task-E2E", passed=2, failed=2)
    retro_engine.generate_from_usage(
        usage,
        attached_docs=[(d.name, "bundle") for d in docs],
    )

    rows = telemetry.effectiveness_summary()
    by_doc = {r["doc_name"]: r for r in rows}
    assert {"doc0", "doc1", "doc2"}.issubset(by_doc.keys())
    for name in ("doc0", "doc1", "doc2"):
        assert by_doc[name]["total_uses"] >= 1
        # outcome_correlation = 2 / (2+2) = 0.5
        assert by_doc[name]["avg_outcome_score"] == pytest.approx(0.5, abs=0.01)
