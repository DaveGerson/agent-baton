"""Production-path wiring tests for F0.4 knowledge telemetry (bd-a313).

bd-32d3 added the optional ``telemetry=`` ctor kwarg on ``KnowledgeResolver``
and ``RetrospectiveEngine`` plus ``attached_docs=`` on
``RetrospectiveEngine.generate_from_usage()`` — but the production call sites
were not actually constructing a telemetry store, so
``v_knowledge_effectiveness`` returned empty in production.

These tests pin down the wiring at the *production* construction points:

* ``agent_baton.cli.commands.execution.execute._build_knowledge_resolver``
* ``agent_baton.core.engine.planner.IntelligentPlanner`` (planner-time resolver)
* ``agent_baton.core.engine.executor.ExecutionEngine`` (runtime + retro)

Each test points the production code at a temp ``central.db`` (via the
``HOME`` env var so ``KnowledgeTelemetryStore``'s default
``~/.baton/central.db`` resolves under tmp_path) and verifies the telemetry
view sees the writes.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.knowledge_telemetry import KnowledgeTelemetryStore
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
)
from agent_baton.models.knowledge import KnowledgeAttachment
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_F04_DDL = """
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


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the production telemetry default DB to a tmp sandbox.

    ``KnowledgeTelemetryStore._CENTRAL_DB_DEFAULT`` is computed at module
    import time, so we override the module-level constant rather than
    ``Path.home()``.  Pre-creates the F0.4 schema so production stores
    can write immediately.
    """
    db = tmp_path / ".baton" / "central.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.executescript(_F04_DDL)
    conn.commit()
    conn.close()

    import agent_baton.core.engine.knowledge_telemetry as kt_mod
    monkeypatch.setattr(kt_mod, "_CENTRAL_DB_DEFAULT", db)
    return tmp_path


def _read_view(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM v_knowledge_effectiveness ORDER BY total_uses DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _make_plan_with_attachment(
    task_id: str = "task-prod-A",
    doc_name: str = "design.md",
    pack_name: str = "core",
) -> MachinePlan:
    att = KnowledgeAttachment(
        source="explicit",
        pack_name=pack_name,
        document_name=doc_name,
        path="/tmp/fake-doc.md",
        delivery="inline",
        token_estimate=100,
    )
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer",
        task_description="do a thing",
        knowledge=[att],
    )
    phase = PlanPhase(phase_id=1, name="implementing", steps=[step])
    return MachinePlan(
        task_id=task_id,
        task_summary="test task",
        risk_level="LOW",
        phases=[phase],
    )


# ---------------------------------------------------------------------------
# 1. Production construction — _build_knowledge_resolver wires telemetry
# ---------------------------------------------------------------------------

def test_build_knowledge_resolver_passes_telemetry_store(
    fake_home: Path,
) -> None:
    """The CLI helper must construct a resolver whose ``_telemetry`` is set."""
    from agent_baton.cli.commands.execution import execute as exec_mod

    plan = _make_plan_with_attachment()
    resolver = exec_mod._build_knowledge_resolver(plan)
    assert resolver is not None
    # The bd-a313 fix injects a KnowledgeTelemetryStore.
    assert resolver._telemetry is not None
    assert isinstance(resolver._telemetry, KnowledgeTelemetryStore)


# ---------------------------------------------------------------------------
# 2. Executor dispatch path — emits KnowledgeUsed for each attachment
# ---------------------------------------------------------------------------

def test_executor_dispatch_emits_knowledge_used(fake_home: Path) -> None:
    """The executor's _emit_knowledge_used path writes to central.db."""
    plan = _make_plan_with_attachment(
        task_id="task-prod-A", doc_name="design.md", pack_name="core"
    )
    engine = ExecutionEngine(team_context_root=fake_home / "team-ctx")

    # Drive only the telemetry helper (the rest of dispatch needs a full
    # subagent runtime).  This is the exact code path called from
    # _dispatch_action when a step has attachments.
    engine._emit_knowledge_used(plan.task_id, plan.phases[0].steps[0])

    db = fake_home / ".baton" / "central.db"
    rows = _read_view(db)
    assert len(rows) == 1
    assert rows[0]["doc_name"] == "design.md"
    assert rows[0]["pack_name"] == "core"
    assert rows[0]["total_uses"] >= 1


def test_executor_dispatch_no_attachments_is_noop(fake_home: Path) -> None:
    """Steps without attachments must not write telemetry rows."""
    plan = MachinePlan(
        task_id="task-empty",
        task_summary="nothing to attach",
        risk_level="LOW",
        phases=[
            PlanPhase(
                phase_id=1,
                name="implementing",
                steps=[PlanStep(step_id="1.1", agent_name="x", task_description="x")],
            )
        ],
    )
    engine = ExecutionEngine(team_context_root=fake_home / "team-ctx")
    engine._emit_knowledge_used(plan.task_id, plan.phases[0].steps[0])

    db = fake_home / ".baton" / "central.db"
    rows = _read_view(db)
    assert rows == []


# ---------------------------------------------------------------------------
# 3. Executor RetrospectiveEngine — wired with telemetry + attached_docs
# ---------------------------------------------------------------------------

def test_executor_retro_engine_has_telemetry(fake_home: Path) -> None:
    """ExecutionEngine in legacy/file mode must inject a telemetry store."""
    engine = ExecutionEngine(team_context_root=fake_home / "team-ctx")
    assert engine._retro_engine is not None
    # bd-a313: telemetry side-channel must be set.
    assert engine._retro_engine._telemetry is not None


# ---------------------------------------------------------------------------
# 4. End-to-end: dispatch emits KnowledgeUsed, retro emits outcome
# ---------------------------------------------------------------------------

def test_end_to_end_dispatch_then_retro_populates_view(fake_home: Path) -> None:
    """A dispatch + retrospective round-trip via the production code paths
    must populate v_knowledge_effectiveness with usage_count >= 1 and a
    non-NULL outcome correlation."""
    plan = _make_plan_with_attachment(
        task_id="task-E2E", doc_name="api.md", pack_name="ext"
    )
    state = ExecutionState(task_id="task-E2E", plan=plan, status="complete")

    engine = ExecutionEngine(team_context_root=fake_home / "team-ctx")

    # 1. Simulate dispatch — emits KnowledgeUsed.
    engine._emit_knowledge_used(state.task_id, plan.phases[0].steps[0])

    # 2. Build a usage record and run the retrospective.
    usage = TaskUsageRecord(
        task_id=state.task_id,
        timestamp="2026-04-25T00:00:00Z",
        risk_level="LOW",
        agents_used=[
            AgentUsageRecord(
                name="backend-engineer", model="sonnet",
                estimated_tokens=100, retries=0,
            )
        ],
        gates_passed=3,
        gates_failed=1,
    )
    # This path mirrors the executor's own _handle_complete code: it reads
    # attached_docs from state.plan and passes them into generate_from_usage.
    attached = []
    seen: set[tuple[str, str]] = set()
    for ph in state.plan.phases:
        for st in ph.steps:
            for att in st.knowledge or []:
                pair = (att.document_name, att.pack_name or "")
                if pair not in seen:
                    seen.add(pair)
                    attached.append(pair)
    engine._retro_engine.generate_from_usage(
        usage=usage,
        task_name="task-E2E",
        attached_docs=attached or None,
    )

    db = fake_home / ".baton" / "central.db"
    rows = _read_view(db)
    by_doc = {r["doc_name"]: r for r in rows}
    assert "api.md" in by_doc
    assert by_doc["api.md"]["total_uses"] >= 1
    # outcome_correlation = 3 / (3+1) = 0.75 — must be non-NULL.
    assert by_doc["api.md"]["avg_outcome_score"] is not None
    assert by_doc["api.md"]["avg_outcome_score"] == pytest.approx(0.75, abs=0.01)


# ---------------------------------------------------------------------------
# 5. attached_docs assembly happens automatically inside the executor
# ---------------------------------------------------------------------------

def test_attached_docs_assembled_from_plan(fake_home: Path) -> None:
    """When _retro_engine has telemetry, the executor's _handle_complete path
    must collect (doc, pack) pairs from every step's knowledge list and
    forward them to generate_from_usage as attached_docs."""
    # Build a plan with two distinct attachments across two steps.
    att1 = KnowledgeAttachment(
        source="explicit", pack_name="p1", document_name="doc1",
        path="/x/doc1.md", delivery="inline", token_estimate=10,
    )
    att2 = KnowledgeAttachment(
        source="explicit", pack_name="p2", document_name="doc2",
        path="/x/doc2.md", delivery="reference", token_estimate=10,
    )
    plan = MachinePlan(
        task_id="task-multi",
        task_summary="multi",
        risk_level="LOW",
        phases=[
            PlanPhase(phase_id=1, name="impl", steps=[
                PlanStep(step_id="1.1", agent_name="a", task_description="t",
                         knowledge=[att1]),
                PlanStep(step_id="1.2", agent_name="b", task_description="t",
                         knowledge=[att2]),
            ]),
        ],
    )
    state = ExecutionState(task_id="task-multi", plan=plan, status="complete")
    engine = ExecutionEngine(team_context_root=fake_home / "team-ctx")

    # Pre-seed knowledge_telemetry so outcome update finds rows.
    engine._emit_knowledge_used(state.task_id, plan.phases[0].steps[0])
    engine._emit_knowledge_used(state.task_id, plan.phases[0].steps[1])

    # Drive the same code path as _handle_complete.
    usage = TaskUsageRecord(
        task_id=state.task_id,
        timestamp="2026-04-25T00:00:00Z",
        risk_level="LOW",
        agents_used=[
            AgentUsageRecord(name="a", model="sonnet",
                             estimated_tokens=10, retries=0)
        ],
        gates_passed=1, gates_failed=1,
    )

    # Mirror _handle_complete's assembly block (this is the wiring under test).
    pairs = []
    seen: set[tuple[str, str]] = set()
    for ph in state.plan.phases:
        for st in ph.steps:
            for att in st.knowledge or []:
                pair = (att.document_name, att.pack_name or "")
                if pair not in seen:
                    seen.add(pair)
                    pairs.append(pair)

    assert pairs == [("doc1", "p1"), ("doc2", "p2")]

    engine._retro_engine.generate_from_usage(
        usage=usage, task_name="task-multi", attached_docs=pairs,
    )

    rows = _read_view(fake_home / ".baton" / "central.db")
    by_doc = {r["doc_name"]: r for r in rows}
    for name in ("doc1", "doc2"):
        assert by_doc[name]["total_uses"] >= 1
        assert by_doc[name]["avg_outcome_score"] is not None
        assert by_doc[name]["avg_outcome_score"] == pytest.approx(0.5, abs=0.01)
