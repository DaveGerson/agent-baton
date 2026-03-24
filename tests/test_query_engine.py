"""Tests for agent_baton.core.storage.queries.QueryEngine.

All tests use an in-memory or tmp_path SQLite database so they are fully
isolated and require no external services.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_baton.core.storage.queries import (
    AgentStats,
    CostReport,
    GateStats,
    KnowledgeGapReport,
    QueryEngine,
    TaskSummary,
    _is_write_statement,
    open_query_engine,
)
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import (
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
)
from agent_baton.models.retrospective import (
    AgentOutcome,
    KnowledgeGap,
    Retrospective,
    RosterRecommendation,
)
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.models.pattern import LearnedPattern


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "baton.db"


@pytest.fixture
def store(db_path: Path) -> SqliteStorage:
    return SqliteStorage(db_path)


@pytest.fixture
def engine(db_path: Path) -> QueryEngine:
    qe = QueryEngine(db_path)
    yield qe
    qe.close()


def _make_plan(task_id: str, summary: str = "A task", risk: str = "LOW") -> MachinePlan:
    step = PlanStep(
        step_id=f"{task_id}-s1",
        agent_name="backend-engineer--python",
        task_description="Implement feature",
        model="sonnet",
        depends_on=[],
        deliverables=[],
        allowed_paths=["agent_baton/"],
        blocked_paths=[],
        context_files=[],
    )
    phase = PlanPhase(
        phase_id=1,
        name="Implementation",
        steps=[step],
        approval_required=False,
    )
    return MachinePlan(
        task_id=task_id,
        task_summary=summary,
        risk_level=risk,
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[phase],
        shared_context="",
        created_at="2026-01-01T00:00:00Z",
    )


def _now_utc() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_state(
    task_id: str,
    status: str = "complete",
    plan: MachinePlan | None = None,
    step_results: list[StepResult] | None = None,
    gate_results: list[GateResult] | None = None,
) -> ExecutionState:
    if plan is None:
        plan = _make_plan(task_id)
    now = _now_utc()
    return ExecutionState(
        task_id=task_id,
        plan=plan,
        current_phase=1,
        current_step_index=0,
        status=status,
        step_results=step_results or [],
        gate_results=gate_results or [],
        started_at=now,
        completed_at=now if status == "complete" else "",
    )


def _make_step_result(
    task_id: str,
    step_id: str,
    agent: str,
    status: str = "complete",
    tokens: int = 1000,
    retries: int = 0,
    duration: float = 5.0,
) -> StepResult:
    return StepResult(
        step_id=step_id,
        agent_name=agent,
        status=status,
        outcome="Done",
        files_changed=[],
        commit_hash="",
        estimated_tokens=tokens,
        duration_seconds=duration,
        retries=retries,
        error="" if status == "complete" else "Something failed",
        completed_at=_now_utc(),
    )


def _make_retro(task_id: str) -> Retrospective:
    return Retrospective(
        task_id=task_id,
        task_name="A task",
        timestamp=_now_utc(),
        agent_count=1,
        retry_count=0,
        gates_passed=1,
        gates_failed=0,
        risk_level="LOW",
        duration_estimate="1h",
        estimated_tokens=1000,
        what_worked=[
            AgentOutcome(
                name="backend-engineer--python",
                worked_well="Clean code",
                issues="",
                root_cause="",
            )
        ],
        what_didnt=[],
        knowledge_gaps=[
            KnowledgeGap(
                description="Missing SQLite docs",
                affected_agent="backend-engineer--python",
                suggested_fix="Add reference doc",
            )
        ],
        roster_recommendations=[
            RosterRecommendation(
                action="add",
                target="dba-agent",
                reason="DB tasks needed",
            )
        ],
    )


# ---------------------------------------------------------------------------
# _is_write_statement
# ---------------------------------------------------------------------------


class TestIsWriteStatement:
    def test_select_is_read(self) -> None:
        assert not _is_write_statement("SELECT * FROM foo")

    def test_insert_is_write(self) -> None:
        assert _is_write_statement("INSERT INTO foo VALUES (1)")

    def test_update_is_write(self) -> None:
        assert _is_write_statement("UPDATE foo SET x=1")

    def test_delete_is_write(self) -> None:
        assert _is_write_statement("DELETE FROM foo")

    def test_drop_is_write(self) -> None:
        assert _is_write_statement("DROP TABLE foo")

    def test_alter_is_write(self) -> None:
        assert _is_write_statement("ALTER TABLE foo ADD COLUMN bar TEXT")

    def test_create_is_write(self) -> None:
        assert _is_write_statement("CREATE TABLE foo (id INTEGER)")

    def test_case_insensitive(self) -> None:
        assert _is_write_statement("insert into foo values (1)")

    def test_leading_whitespace(self) -> None:
        assert _is_write_statement("  \n  DELETE FROM foo")

    def test_with_clause_followed_by_select(self) -> None:
        # WITH ... SELECT is read-only but starts with WITH (not in our list)
        assert not _is_write_statement("WITH cte AS (SELECT 1) SELECT * FROM cte")


# ---------------------------------------------------------------------------
# open_query_engine factory
# ---------------------------------------------------------------------------


class TestOpenQueryEngine:
    def test_explicit_path(self, db_path: Path) -> None:
        qe = open_query_engine(db_path=db_path)
        assert qe.db_path == db_path
        qe.close()

    def test_central_flag(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        qe = open_query_engine(central=True)
        assert qe.db_path == fake_home / ".baton" / "central.db"
        qe.close()

    def test_default_uses_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        qe = open_query_engine()
        expected = tmp_path / ".claude" / "team-context" / "baton.db"
        assert qe.db_path == expected
        qe.close()


# ---------------------------------------------------------------------------
# agent_reliability
# ---------------------------------------------------------------------------


class TestAgentReliability:
    def test_empty_database(self, engine: QueryEngine) -> None:
        result = engine.agent_reliability()
        assert result == []

    def test_single_agent_all_success(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        sr = _make_step_result("t1", "t1-s1", "backend-engineer--python", tokens=2000)
        state = _make_state("t1", step_results=[sr])
        store.save_execution(state)

        stats = engine.agent_reliability()
        assert len(stats) == 1
        s = stats[0]
        assert s.agent_name == "backend-engineer--python"
        assert s.total_steps == 1
        assert s.successes == 1
        assert s.failures == 0
        assert s.success_rate == 1.0
        assert s.total_tokens == 2000

    def test_mixed_success_failure(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        sr1 = _make_step_result("t1", "t1-s1", "agent-a", status="complete")
        sr2 = _make_step_result("t1", "t1-s2", "agent-a", status="failed")
        plan = _make_plan("t1")
        # Add a second step to the plan so FK constraints are satisfied
        plan.phases[0].steps.append(
            PlanStep(
                step_id="t1-s2",
                agent_name="agent-a",
                task_description="step2",
                model="sonnet",
                depends_on=[],
                deliverables=[],
                allowed_paths=[],
                blocked_paths=[],
                context_files=[],
            )
        )
        state = _make_state("t1", step_results=[sr1, sr2], plan=plan)
        store.save_execution(state)

        stats = engine.agent_reliability()
        assert len(stats) == 1
        s = stats[0]
        assert s.total_steps == 2
        assert s.successes == 1
        assert s.failures == 1
        assert s.success_rate == 0.5

    def test_returns_list_of_agent_stats_type(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        sr = _make_step_result("t1", "t1-s1", "agent-x")
        state = _make_state("t1", step_results=[sr])
        store.save_execution(state)
        result = engine.agent_reliability()
        assert all(isinstance(r, AgentStats) for r in result)

    def test_retries_aggregated(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        sr = _make_step_result("t1", "t1-s1", "agent-a", retries=3)
        state = _make_state("t1", step_results=[sr])
        store.save_execution(state)
        stats = engine.agent_reliability()
        assert stats[0].total_retries == 3


# ---------------------------------------------------------------------------
# agent_history
# ---------------------------------------------------------------------------


class TestAgentHistory:
    def test_empty(self, engine: QueryEngine) -> None:
        assert engine.agent_history("nobody") == []

    def test_returns_correct_agent(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        sr_a = _make_step_result("t1", "t1-s1", "agent-a")
        sr_b = _make_step_result("t1", "t1-s2", "agent-b")
        plan = _make_plan("t1")
        plan.phases[0].steps.append(
            PlanStep(
                step_id="t1-s2",
                agent_name="agent-b",
                task_description="step2",
                model="sonnet",
                depends_on=[],
                deliverables=[],
                allowed_paths=[],
                blocked_paths=[],
                context_files=[],
            )
        )
        state = _make_state("t1", step_results=[sr_a, sr_b], plan=plan)
        store.save_execution(state)

        hist = engine.agent_history("agent-a")
        assert len(hist) == 1
        assert hist[0]["agent_name"] if "agent_name" in hist[0] else hist[0].get("step_id") is not None

    def test_limit_respected(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        plan = _make_plan("t1")
        step_results = []
        for i in range(5):
            sid = f"t1-s{i}"
            if i > 0:
                plan.phases[0].steps.append(
                    PlanStep(
                        step_id=sid,
                        agent_name="agent-a",
                        task_description=f"step {i}",
                        model="sonnet",
                        depends_on=[],
                        deliverables=[],
                        allowed_paths=[],
                        blocked_paths=[],
                        context_files=[],
                    )
                )
            else:
                plan.phases[0].steps[0].step_id = sid
                plan.phases[0].steps[0].agent_name = "agent-a"
            step_results.append(_make_step_result("t1", sid, "agent-a"))

        state = _make_state("t1", step_results=step_results, plan=plan)
        store.save_execution(state)

        hist = engine.agent_history("agent-a", limit=3)
        assert len(hist) <= 3


# ---------------------------------------------------------------------------
# task_list
# ---------------------------------------------------------------------------


class TestTaskList:
    def test_empty(self, engine: QueryEngine) -> None:
        assert engine.task_list() == []

    def test_returns_task_summaries(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        state = _make_state("t1")
        store.save_execution(state)
        tasks = engine.task_list()
        assert len(tasks) == 1
        assert isinstance(tasks[0], TaskSummary)
        assert tasks[0].task_id == "t1"

    def test_status_filter(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        store.save_execution(_make_state("t1", status="complete"))
        store.save_execution(_make_state("t2", status="running",
                                          plan=_make_plan("t2")))
        complete = engine.task_list(status="complete")
        running = engine.task_list(status="running")
        assert all(t.status == "complete" for t in complete)
        assert all(t.status == "running" for t in running)

    def test_limit(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        for i in range(5):
            store.save_execution(_make_state(f"task-{i}", plan=_make_plan(f"task-{i}")))
        tasks = engine.task_list(limit=3)
        assert len(tasks) <= 3

    def test_risk_level_populated(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        state = _make_state("t1", plan=_make_plan("t1", risk="HIGH"))
        store.save_execution(state)
        tasks = engine.task_list()
        assert tasks[0].risk_level == "HIGH"


# ---------------------------------------------------------------------------
# task_detail
# ---------------------------------------------------------------------------


class TestTaskDetail:
    def test_missing_task(self, engine: QueryEngine) -> None:
        assert engine.task_detail("nonexistent") is None

    def test_returns_dict_with_expected_keys(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        state = _make_state("t1")
        store.save_execution(state)
        detail = engine.task_detail("t1")
        assert detail is not None
        assert "task_id" in detail
        assert "status" in detail
        assert "plan" in detail
        assert "steps" in detail
        assert "step_results" in detail
        assert "gates" in detail

    def test_step_results_present(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        sr = _make_step_result("t1", "t1-s1", "agent-a")
        state = _make_state("t1", step_results=[sr])
        store.save_execution(state)
        detail = engine.task_detail("t1")
        assert len(detail["step_results"]) == 1
        assert detail["step_results"][0]["step_id"] == "t1-s1"

    def test_gates_present(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        gr = GateResult(
            phase_id=1,
            gate_type="test",
            passed=True,
            output="All tests pass",
            checked_at="2026-01-10T11:00:00Z",
        )
        state = _make_state("t1", gate_results=[gr])
        store.save_execution(state)
        detail = engine.task_detail("t1")
        assert len(detail["gates"]) == 1
        assert detail["gates"][0]["gate_type"] == "test"


# ---------------------------------------------------------------------------
# knowledge_gaps
# ---------------------------------------------------------------------------


class TestKnowledgeGaps:
    def test_empty(self, engine: QueryEngine) -> None:
        assert engine.knowledge_gaps() == []

    def test_returns_knowledge_gap_reports(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        retro = _make_retro("t1")
        state = _make_state("t1")
        store.save_execution(state)
        store.save_retrospective(retro)

        gaps = engine.knowledge_gaps()
        assert len(gaps) == 1
        assert isinstance(gaps[0], KnowledgeGapReport)
        assert gaps[0].description == "Missing SQLite docs"
        assert gaps[0].affected_agent == "backend-engineer--python"
        assert gaps[0].frequency == 1

    def test_frequency_grouping(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        for i, tid in enumerate(["t1", "t2"]):
            plan = _make_plan(tid)
            state = _make_state(tid, plan=plan)
            store.save_execution(state)
            retro = _make_retro(tid)
            store.save_retrospective(retro)

        # Both retros have the same gap description — should be grouped
        gaps = engine.knowledge_gaps()
        assert len(gaps) == 1
        assert gaps[0].frequency == 2
        assert len(gaps[0].tasks) == 2

    def test_min_frequency_filter(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        state = _make_state("t1")
        store.save_execution(state)
        retro = _make_retro("t1")
        store.save_retrospective(retro)

        gaps_min2 = engine.knowledge_gaps(min_frequency=2)
        assert len(gaps_min2) == 0

        gaps_min1 = engine.knowledge_gaps(min_frequency=1)
        assert len(gaps_min1) == 1


# ---------------------------------------------------------------------------
# roster_recommendations
# ---------------------------------------------------------------------------


class TestRosterRecommendations:
    def test_empty(self, engine: QueryEngine) -> None:
        assert engine.roster_recommendations() == []

    def test_returns_recs(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        state = _make_state("t1")
        store.save_execution(state)
        retro = _make_retro("t1")
        store.save_retrospective(retro)

        recs = engine.roster_recommendations()
        assert len(recs) == 1
        assert recs[0]["action"] == "add"
        assert recs[0]["target"] == "dba-agent"
        assert recs[0]["count"] == 1

    def test_vote_aggregation(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        for tid in ["t1", "t2"]:
            plan = _make_plan(tid)
            state = _make_state(tid, plan=plan)
            store.save_execution(state)
            retro = _make_retro(tid)
            store.save_retrospective(retro)

        recs = engine.roster_recommendations()
        assert recs[0]["count"] == 2


# ---------------------------------------------------------------------------
# gate_stats
# ---------------------------------------------------------------------------


class TestGateStats:
    def test_empty(self, engine: QueryEngine) -> None:
        assert engine.gate_stats() == []

    def test_pass_rate_calculation(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        gr_pass = GateResult(
            phase_id=1, gate_type="test", passed=True,
            output="ok", checked_at="2026-01-10T11:00:00Z",
        )
        gr_fail = GateResult(
            phase_id=2, gate_type="test", passed=False,
            output="fail", checked_at="2026-01-10T11:05:00Z",
        )
        state = _make_state("t1", gate_results=[gr_pass, gr_fail])
        store.save_execution(state)

        stats = engine.gate_stats()
        assert len(stats) == 1
        s = stats[0]
        assert isinstance(s, GateStats)
        assert s.gate_type == "test"
        assert s.total == 2
        assert s.passed == 1
        assert s.pass_rate == 0.5

    def test_multiple_gate_types(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        gates = [
            GateResult(
                phase_id=1, gate_type="test", passed=True,
                output="ok", checked_at="2026-01-10T11:00:00Z",
            ),
            GateResult(
                phase_id=2, gate_type="lint", passed=False,
                output="fail", checked_at="2026-01-10T11:00:00Z",
            ),
        ]
        state = _make_state("t1", gate_results=gates)
        store.save_execution(state)

        stats = engine.gate_stats()
        types = {s.gate_type for s in stats}
        assert "test" in types
        assert "lint" in types


# ---------------------------------------------------------------------------
# patterns
# ---------------------------------------------------------------------------


class TestPatterns:
    def test_empty(self, engine: QueryEngine) -> None:
        assert engine.patterns() == []

    def test_returns_patterns(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        pattern = LearnedPattern(
            pattern_id="pat-001",
            task_type="feature",
            stack="python",
            recommended_template="standard",
            recommended_agents=["backend-engineer--python"],
            confidence=0.9,
            sample_size=10,
            success_rate=0.85,
            avg_token_cost=5000,
            evidence=["task-1"],
        )
        store.save_patterns([pattern])

        patterns = engine.patterns()
        assert len(patterns) == 1
        assert patterns[0]["pattern_id"] == "pat-001"
        assert patterns[0]["task_type"] == "feature"

    def test_sorted_by_confidence(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        store.save_patterns([
            LearnedPattern(
                pattern_id=f"pat-{i}",
                task_type="feature",
                stack="python",
                recommended_template="standard",
                recommended_agents=[],
                confidence=conf,
                sample_size=5,
                success_rate=0.8,
                avg_token_cost=1000,
                evidence=[],
            )
            for i, conf in enumerate([0.6, 0.9, 0.75])
        ])
        patterns = engine.patterns()
        confidences = [p["confidence"] for p in patterns]
        assert confidences == sorted(confidences, reverse=True)


# ---------------------------------------------------------------------------
# current_context
# ---------------------------------------------------------------------------


class TestCurrentContext:
    def test_no_active_task(self, engine: QueryEngine) -> None:
        ctx = engine.current_context()
        assert ctx == {"has_active_task": False}

    def test_active_task_present(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        state = _make_state("t1", status="running",
                             plan=_make_plan("t1", summary="Build it"))
        store.save_execution(state)
        store.set_active_task("t1")

        ctx = engine.current_context()
        assert ctx["has_active_task"] is True
        assert ctx["task_id"] == "t1"
        assert ctx["task_summary"] == "Build it"
        assert ctx["status"] == "running"

    def test_orphaned_active_task(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        """active_task references a task_id not in executions → no active."""
        store.set_active_task("ghost-task")
        ctx = engine.current_context()
        assert ctx["has_active_task"] is False


# ---------------------------------------------------------------------------
# agent_briefing
# ---------------------------------------------------------------------------


class TestAgentBriefing:
    def test_unknown_agent_still_returns_markdown(
        self, engine: QueryEngine
    ) -> None:
        briefing = engine.agent_briefing("unknown-agent")
        assert "## Agent Briefing: unknown-agent" in briefing
        assert "No performance data" in briefing

    def test_known_agent_includes_stats(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        sr = _make_step_result("t1", "t1-s1", "backend-engineer--python", tokens=3000)
        state = _make_state("t1", step_results=[sr])
        store.save_execution(state)

        briefing = engine.agent_briefing("backend-engineer--python")
        assert "Steps completed:" in briefing
        assert "Success rate:" in briefing

    def test_includes_knowledge_gaps(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        state = _make_state("t1")
        store.save_execution(state)
        retro = _make_retro("t1")
        store.save_retrospective(retro)

        briefing = engine.agent_briefing("backend-engineer--python")
        assert "Known Knowledge Gaps" in briefing
        assert "Missing SQLite docs" in briefing


# ---------------------------------------------------------------------------
# raw_query
# ---------------------------------------------------------------------------


class TestRawQuery:
    def test_select_allowed(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        store.save_execution(_make_state("t1"))
        rows = engine.raw_query("SELECT task_id FROM executions")
        assert len(rows) == 1
        assert rows[0]["task_id"] == "t1"

    def test_insert_rejected(self, engine: QueryEngine) -> None:
        with pytest.raises(ValueError, match="read-only"):
            engine.raw_query("INSERT INTO executions (task_id) VALUES ('x')")

    def test_update_rejected(self, engine: QueryEngine) -> None:
        with pytest.raises(ValueError, match="read-only"):
            engine.raw_query("UPDATE executions SET status='x'")

    def test_delete_rejected(self, engine: QueryEngine) -> None:
        with pytest.raises(ValueError, match="read-only"):
            engine.raw_query("DELETE FROM executions")

    def test_drop_rejected(self, engine: QueryEngine) -> None:
        with pytest.raises(ValueError, match="read-only"):
            engine.raw_query("DROP TABLE executions")

    def test_returns_list_of_dicts(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        store.save_execution(_make_state("t1"))
        rows = engine.raw_query("SELECT * FROM executions WHERE task_id = ?", ("t1",))
        assert isinstance(rows, list)
        assert isinstance(rows[0], dict)

    def test_with_params(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        store.save_execution(_make_state("t1"))
        store.save_execution(_make_state("t2", plan=_make_plan("t2")))
        rows = engine.raw_query(
            "SELECT task_id FROM executions WHERE task_id = ?", ("t1",)
        )
        assert len(rows) == 1
        assert rows[0]["task_id"] == "t1"

    def test_empty_result(self, engine: QueryEngine) -> None:
        rows = engine.raw_query(
            "SELECT task_id FROM executions WHERE task_id = ?", ("nope",)
        )
        assert rows == []


# ---------------------------------------------------------------------------
# cost_by_task_type
# ---------------------------------------------------------------------------


class TestCostByTaskType:
    def test_empty(self, engine: QueryEngine) -> None:
        assert engine.cost_by_task_type() == []

    def test_returns_cost_reports(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        usage = TaskUsageRecord(
            task_id="t1",
            timestamp=_now_utc(),
            risk_level="LOW",
            sequencing_mode="phased_delivery",
            gates_passed=1,
            gates_failed=0,
            outcome="success",
            agents_used=[
                AgentUsageRecord(
                    name="backend-engineer--python",
                    model="sonnet",
                    steps=1,
                    retries=0,
                    gate_results=[],
                    estimated_tokens=5000,
                    duration_seconds=10.0,
                )
            ],
        )
        store.log_usage(usage)

        reports = engine.cost_by_task_type()
        assert len(reports) == 1
        assert isinstance(reports[0], CostReport)
        assert reports[0].task_type == "phased_delivery"
        assert reports[0].total_tokens == 5000


# ---------------------------------------------------------------------------
# cost_by_agent
# ---------------------------------------------------------------------------


class TestCostByAgent:
    def test_empty(self, engine: QueryEngine) -> None:
        assert engine.cost_by_agent() == []

    def test_returns_agent_costs(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        sr = _make_step_result("t1", "t1-s1", "backend-engineer--python", tokens=4000)
        state = _make_state("t1", step_results=[sr])
        store.save_execution(state)

        costs = engine.cost_by_agent()
        assert len(costs) == 1
        assert costs[0]["agent_name"] == "backend-engineer--python"
        assert costs[0]["total_tokens"] == 4000

    def test_dict_has_expected_keys(
        self, store: SqliteStorage, engine: QueryEngine
    ) -> None:
        sr = _make_step_result("t1", "t1-s1", "agent-x", tokens=100)
        state = _make_state("t1", step_results=[sr])
        store.save_execution(state)

        costs = engine.cost_by_agent()
        assert "agent_name" in costs[0]
        assert "total_tokens" in costs[0]
        assert "total_steps" in costs[0]
        assert "avg_tokens_per_step" in costs[0]
        assert "total_duration" in costs[0]


# ---------------------------------------------------------------------------
# CLI: baton query (smoke tests via handler)
# ---------------------------------------------------------------------------


class TestQueryCLIHandler:
    """Smoke tests that the CLI handler runs without raising for each subcommand."""

    def _run(self, args_list: list[str], db_path: Path, capsys: pytest.CaptureFixture) -> str:
        import argparse
        from agent_baton.cli.commands.observe.query import register, handler

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        register(sub)
        args = parser.parse_args(["query"] + args_list + ["--db", str(db_path)])
        handler(args)
        return capsys.readouterr().out

    def test_agent_reliability_empty(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        out = self._run(["agent-reliability"], db_path, capsys)
        # Either data or "(no data)"
        assert isinstance(out, str)

    def test_tasks_empty(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        out = self._run(["tasks"], db_path, capsys)
        assert isinstance(out, str)

    def test_current_no_active_task(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        out = self._run(["current"], db_path, capsys)
        assert "No active task" in out

    def test_task_detail_missing(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        out = self._run(["task-detail", "ghost-id"], db_path, capsys)
        assert "not found" in out

    def test_agent_history_missing_arg(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        # Missing target → error message to stderr, nothing crashes
        import argparse
        from agent_baton.cli.commands.observe.query import register, handler
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        register(sub)
        args = parser.parse_args(["query", "agent-history", "--db", str(db_path)])
        handler(args)  # no exception

    def test_sql_adhoc_select(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        out = self._run(["--sql", "SELECT 1 AS val"], db_path, capsys)
        assert "val" in out.upper() or "1" in out

    def test_sql_write_rejected(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        import argparse, sys
        from io import StringIO
        from agent_baton.cli.commands.observe.query import register, handler
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        register(sub)
        args = parser.parse_args(
            ["query", "--sql", "DELETE FROM executions", "--db", str(db_path)]
        )
        handler(args)
        err = capsys.readouterr().err
        assert "read-only" in err

    def test_format_json(
        self, store: SqliteStorage, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store.save_execution(_make_state("t1"))
        out = self._run(["tasks", "--format", "json"], db_path, capsys)
        data = json.loads(out)
        assert isinstance(data, list)

    def test_format_csv(
        self, store: SqliteStorage, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        store.save_execution(_make_state("t1"))
        out = self._run(["tasks", "--format", "csv"], db_path, capsys)
        assert "task_id" in out


# ---------------------------------------------------------------------------
# CLI: baton context (smoke tests)
# ---------------------------------------------------------------------------


class TestContextCLIHandler:
    def _run(
        self,
        args_list: list[str],
        db_path: Path,
        capsys: pytest.CaptureFixture,
    ) -> str:
        import argparse
        from agent_baton.cli.commands.observe.context_cmd import register, handler

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        register(sub)
        # --db is now a per-subcommand flag so it goes after the subcommand keyword
        args = parser.parse_args(["context"] + args_list + ["--db", str(db_path)])
        handler(args)
        return capsys.readouterr().out

    def test_current_no_active(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        out = self._run(["current"], db_path, capsys)
        assert "No active task" in out

    def test_current_with_active_task(
        self, store: SqliteStorage, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        state = _make_state(
            "t1", status="running", plan=_make_plan("t1", summary="Fix the bug")
        )
        store.save_execution(state)
        store.set_active_task("t1")
        out = self._run(["current"], db_path, capsys)
        assert "t1" in out
        assert "Fix the bug" in out

    def test_current_json_output(
        self, store: SqliteStorage, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        state = _make_state("t1", status="running", plan=_make_plan("t1"))
        store.save_execution(state)
        store.set_active_task("t1")
        out = self._run(["current", "--json"], db_path, capsys)
        data = json.loads(out)
        assert data["has_active_task"] is True

    def test_briefing(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        out = self._run(["briefing", "backend-engineer--python"], db_path, capsys)
        assert "Agent Briefing" in out
        assert "backend-engineer--python" in out

    def test_gaps_empty(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        out = self._run(["gaps"], db_path, capsys)
        assert "No knowledge gaps" in out

    def test_gaps_with_data(
        self, store: SqliteStorage, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        state = _make_state("t1")
        store.save_execution(state)
        retro = _make_retro("t1")
        store.save_retrospective(retro)
        out = self._run(["gaps"], db_path, capsys)
        assert "Missing SQLite docs" in out

    def test_gaps_agent_filter(
        self, store: SqliteStorage, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        state = _make_state("t1")
        store.save_execution(state)
        retro = _make_retro("t1")
        store.save_retrospective(retro)
        out_match = self._run(
            ["gaps", "--agent", "backend-engineer--python"], db_path, capsys
        )
        assert "Missing SQLite docs" in out_match
        out_no_match = self._run(
            ["gaps", "--agent", "nobody"], db_path, capsys
        )
        assert "No knowledge gaps" in out_no_match

    def test_no_subcommand_prints_help(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        import argparse
        from agent_baton.cli.commands.observe.context_cmd import register, handler

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        register(sub)
        # No subcommand — parse only ["context"] to trigger the help path
        args = parser.parse_args(["context"])
        handler(args)
        out = capsys.readouterr().out
        assert "Usage" in out or "usage" in out or "baton context" in out
