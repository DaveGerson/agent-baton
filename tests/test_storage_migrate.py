"""Tests for StorageMigrator and the migrate-storage CLI command."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_baton.core.storage.migrate import StorageMigrator
from agent_baton.models.budget import BudgetRecommendation
from agent_baton.models.events import Event
from agent_baton.models.execution import (
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanAmendment,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
    TeamMember,
    TeamStepResult,
)
from agent_baton.models.pattern import LearnedPattern
from agent_baton.models.retrospective import (
    AgentOutcome,
    KnowledgeGap,
    Retrospective,
    RosterRecommendation,
    SequencingNote,
)
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord


# ---------------------------------------------------------------------------
# Helpers — build fixture data
# ---------------------------------------------------------------------------

def _minimal_plan(task_id: str = "task-001") -> MachinePlan:
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer--python",
        task_description="Write code",
    )
    phase = PlanPhase(
        phase_id=1,
        name="Implementation",
        steps=[step],
        gate=PlanGate(gate_type="test", command="pytest", description="Run tests"),
    )
    return MachinePlan(
        task_id=task_id,
        task_summary="Add feature X",
        phases=[phase],
        created_at="2026-03-01T10:00:00Z",
    )


def _execution_state(task_id: str = "task-001") -> ExecutionState:
    plan = _minimal_plan(task_id)
    state = ExecutionState(
        task_id=task_id,
        plan=plan,
        status="complete",
        started_at="2026-03-01T10:00:00Z",
        completed_at="2026-03-01T10:30:00Z",
    )
    state.step_results.append(
        StepResult(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
            outcome="Done",
            completed_at="2026-03-01T10:20:00Z",
        )
    )
    state.gate_results.append(
        GateResult(
            phase_id=1,
            gate_type="test",
            passed=True,
            output="5 passed",
            checked_at="2026-03-01T10:25:00Z",
        )
    )
    return state


def _write_execution(ctx: Path, state: ExecutionState) -> None:
    """Write execution-state.json in namespaced path."""
    from agent_baton.core.engine.persistence import StatePersistence
    sp = StatePersistence(ctx, task_id=state.task_id)
    sp.save(state)


def _write_usage(ctx: Path, records: list[TaskUsageRecord]) -> None:
    from agent_baton.core.observe.usage import UsageLogger
    logger = UsageLogger(log_path=ctx / "usage-log.jsonl")
    for rec in records:
        logger.log(rec)


def _usage_record(task_id: str = "task-001") -> TaskUsageRecord:
    agent = AgentUsageRecord(
        name="backend-engineer--python",
        model="sonnet",
        steps=1,
        retries=0,
        gate_results=["PASS"],
        estimated_tokens=5000,
        duration_seconds=120.0,
    )
    return TaskUsageRecord(
        task_id=task_id,
        timestamp="2026-03-01T10:30:00Z",
        agents_used=[agent],
        total_agents=1,
        risk_level="LOW",
        sequencing_mode="phased_delivery",
        gates_passed=1,
        gates_failed=0,
        outcome="SHIP",
        notes="",
    )


# ---------------------------------------------------------------------------
# scan() — counts files without touching DB
# ---------------------------------------------------------------------------

class TestScan:
    def test_empty_directory_returns_zeros(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        migrator = StorageMigrator(ctx)
        counts = migrator.scan()
        assert all(v == 0 for v in counts.values())

    def test_counts_executions(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        state = _execution_state("task-001")
        _write_execution(ctx, state)

        counts = StorageMigrator(ctx).scan()
        assert counts["executions"] == 1

    def test_counts_usage_lines(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_usage(ctx, [_usage_record("t-1"), _usage_record("t-2")])

        counts = StorageMigrator(ctx).scan()
        assert counts["usage"] == 2

    def test_counts_events(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        events_dir = ctx / "events"
        events_dir.mkdir()
        event = Event.create(topic="step.completed", task_id="task-001")
        (events_dir / "task-001.jsonl").write_text(
            json.dumps(event.to_dict()) + "\n", encoding="utf-8"
        )

        counts = StorageMigrator(ctx).scan()
        assert counts["events"] == 1

    def test_counts_retrospectives_json_files(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        retro_dir = ctx / "retrospectives"
        retro_dir.mkdir()
        retro = Retrospective(
            task_id="task-001",
            task_name="Feature X",
            timestamp="2026-03-01T10:00:00Z",
        )
        (retro_dir / "task-001.json").write_text(
            json.dumps(retro.to_dict()), encoding="utf-8"
        )

        counts = StorageMigrator(ctx).scan()
        assert counts["retrospectives"] == 1

    def test_counts_patterns(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        pattern = LearnedPattern(
            pattern_id="phased_delivery-001",
            task_type="phased_delivery",
            stack=None,
            recommended_template="Standard workflow",
            recommended_agents=["backend-engineer--python"],
            confidence=0.8,
            sample_size=10,
            success_rate=0.9,
            avg_token_cost=5000,
        )
        (ctx / "learned-patterns.json").write_text(
            json.dumps([pattern.to_dict()]), encoding="utf-8"
        )

        counts = StorageMigrator(ctx).scan()
        assert counts["patterns"] == 1


# ---------------------------------------------------------------------------
# migrate() — dry_run returns counts without writing
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_returns_scan_counts(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("t-1"))
        _write_usage(ctx, [_usage_record("t-1")])

        migrator = StorageMigrator(ctx)
        result = migrator.migrate(dry_run=True)

        assert result["executions"] == 1
        assert result["usage"] == 1
        # DB should NOT have been created
        assert not (ctx / "baton.db").exists()


# ---------------------------------------------------------------------------
# migrate() — actual import
# ---------------------------------------------------------------------------

class TestMigrateExecutions:
    def test_inserts_execution_row(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("task-001"))

        migrator = StorageMigrator(ctx)
        imported = migrator.migrate()

        assert imported["executions"] == 1
        conn = sqlite3.connect(str(ctx / "baton.db"))
        row = conn.execute(
            "SELECT status FROM executions WHERE task_id = ?", ("task-001",)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "complete"

    def test_inserts_plan_row(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("task-001"))

        StorageMigrator(ctx).migrate()

        conn = sqlite3.connect(str(ctx / "baton.db"))
        row = conn.execute(
            "SELECT task_summary FROM plans WHERE task_id = ?", ("task-001",)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "Add feature X"

    def test_inserts_phase_and_step(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("task-001"))

        StorageMigrator(ctx).migrate()

        conn = sqlite3.connect(str(ctx / "baton.db"))
        phases = conn.execute(
            "SELECT phase_id, name FROM plan_phases WHERE task_id = ?", ("task-001",)
        ).fetchall()
        steps = conn.execute(
            "SELECT step_id, agent_name FROM plan_steps WHERE task_id = ?", ("task-001",)
        ).fetchall()
        conn.close()

        assert len(phases) == 1
        assert phases[0][1] == "Implementation"
        assert len(steps) == 1
        assert steps[0][1] == "backend-engineer--python"

    def test_inserts_step_result(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("task-001"))

        StorageMigrator(ctx).migrate()

        conn = sqlite3.connect(str(ctx / "baton.db"))
        row = conn.execute(
            "SELECT status, outcome FROM step_results WHERE task_id = ? AND step_id = ?",
            ("task-001", "1.1"),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "complete"
        assert row[1] == "Done"

    def test_inserts_gate_result(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("task-001"))

        StorageMigrator(ctx).migrate()

        conn = sqlite3.connect(str(ctx / "baton.db"))
        row = conn.execute(
            "SELECT gate_type, passed FROM gate_results WHERE task_id = ?",
            ("task-001",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "test"
        assert row[1] == 1

    def test_idempotent_second_run(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("task-001"))

        StorageMigrator(ctx).migrate()
        imported2 = StorageMigrator(ctx).migrate()

        # Second run should import 0 new executions (INSERT OR IGNORE)
        assert imported2["executions"] == 0

    def test_multiple_executions(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        for i in range(3):
            _write_execution(ctx, _execution_state(f"task-{i:03d}"))

        imported = StorageMigrator(ctx).migrate()
        assert imported["executions"] == 3

    def test_execution_with_team_step(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()

        plan = _minimal_plan("task-team")
        member = TeamMember(
            member_id="1.1.a",
            agent_name="backend-engineer--python",
            role="implementer",
        )
        plan.phases[0].steps[0].team.append(member)

        state = ExecutionState(
            task_id="task-team",
            plan=plan,
            status="complete",
            started_at="2026-03-01T10:00:00Z",
        )
        result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
        )
        result.member_results.append(
            TeamStepResult(
                member_id="1.1.a",
                agent_name="backend-engineer--python",
                status="complete",
                outcome="Done",
            )
        )
        state.step_results.append(result)
        _write_execution(ctx, state)

        StorageMigrator(ctx).migrate()

        conn = sqlite3.connect(str(ctx / "baton.db"))
        members_db = conn.execute(
            "SELECT member_id FROM team_members WHERE task_id = ?", ("task-team",)
        ).fetchall()
        team_results_db = conn.execute(
            "SELECT member_id FROM team_step_results WHERE task_id = ?", ("task-team",)
        ).fetchall()
        conn.close()

        assert len(members_db) == 1
        assert len(team_results_db) == 1

    def test_amendments_imported(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        state = _execution_state("task-amend")
        state.amendments.append(
            PlanAmendment(
                amendment_id="amend-001",
                trigger="gate_feedback",
                trigger_phase_id=1,
                description="Add retry phase",
                phases_added=[2],
                steps_added=["2.1"],
            )
        )
        _write_execution(ctx, state)

        StorageMigrator(ctx).migrate()

        conn = sqlite3.connect(str(ctx / "baton.db"))
        row = conn.execute(
            "SELECT amendment_id, trigger FROM amendments WHERE task_id = ?",
            ("task-amend",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "amend-001"


class TestMigrateEvents:
    def test_events_imported(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        # Need a matching execution row for the FK (events.task_id is not FK-constrained
        # in schema — it's a standalone PK table, so this is fine without execution row)
        events_dir = ctx / "events"
        events_dir.mkdir()
        for _i in range(3):
            ev = Event.create(topic="step.completed", task_id="task-ev")
            with (events_dir / "task-ev.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev.to_dict()) + "\n")

        imported = StorageMigrator(ctx).migrate()
        assert imported["events"] == 3


class TestMigrateUsage:
    def test_usage_records_imported(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        records = [_usage_record(f"task-{i:03d}") for i in range(4)]
        _write_usage(ctx, records)

        imported = StorageMigrator(ctx).migrate()
        assert imported["usage"] == 4

    def test_agent_usage_rows_created(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        rec = _usage_record("task-001")
        rec.agents_used.append(
            AgentUsageRecord(name="architect", estimated_tokens=2000)
        )
        _write_usage(ctx, [rec])

        StorageMigrator(ctx).migrate()

        conn = sqlite3.connect(str(ctx / "baton.db"))
        rows = conn.execute(
            "SELECT agent_name FROM agent_usage WHERE task_id = ?", ("task-001",)
        ).fetchall()
        conn.close()
        assert len(rows) == 2  # two agent entries


class TestMigrateTelemetry:
    def test_telemetry_imported(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        from agent_baton.core.observe.telemetry import AgentTelemetry, TelemetryEvent

        telem = AgentTelemetry(log_path=ctx / "telemetry.jsonl")
        for i in range(5):
            telem.log_event(
                TelemetryEvent(
                    timestamp=f"2026-03-01T10:0{i}:00Z",
                    agent_name="backend-engineer--python",
                    event_type="tool_call",
                    tool_name="Read",
                )
            )

        imported = StorageMigrator(ctx).migrate()
        assert imported["telemetry"] == 5


class TestMigrateRetrospectives:
    def test_retrospective_imported(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        retro_dir = ctx / "retrospectives"
        retro_dir.mkdir()

        retro = Retrospective(
            task_id="task-001",
            task_name="Feature X",
            timestamp="2026-03-01T10:00:00Z",
            agent_count=2,
            retry_count=1,
            gates_passed=2,
            gates_failed=0,
            risk_level="MEDIUM",
            estimated_tokens=10000,
            what_worked=[AgentOutcome(name="architect", worked_well="Great design")],
            what_didnt=[AgentOutcome(name="backend", issues="Slow", root_cause="I/O")],
            knowledge_gaps=[KnowledgeGap(description="Missing docs", affected_agent="backend")],
            roster_recommendations=[RosterRecommendation(action="improve", target="backend", reason="retry rate")],
            sequencing_notes=[SequencingNote(phase="1", observation="Gate was useful", keep=True)],
        )
        (retro_dir / "task-001.json").write_text(
            json.dumps(retro.to_dict()), encoding="utf-8"
        )

        imported = StorageMigrator(ctx).migrate()
        assert imported["retrospectives"] == 1

        conn = sqlite3.connect(str(ctx / "baton.db"))
        row = conn.execute(
            "SELECT task_name, risk_level FROM retrospectives WHERE task_id = ?",
            ("task-001",),
        ).fetchone()
        outcomes = conn.execute(
            "SELECT category, agent_name FROM retrospective_outcomes WHERE task_id = ?",
            ("task-001",),
        ).fetchall()
        gaps = conn.execute(
            "SELECT description FROM knowledge_gaps WHERE task_id = ?", ("task-001",)
        ).fetchall()
        recs = conn.execute(
            "SELECT action, target FROM roster_recommendations WHERE task_id = ?",
            ("task-001",),
        ).fetchall()
        notes = conn.execute(
            "SELECT phase, keep FROM sequencing_notes WHERE task_id = ?", ("task-001",)
        ).fetchall()
        conn.close()

        assert row[0] == "Feature X"
        assert row[1] == "MEDIUM"
        assert len(outcomes) == 2
        assert len(gaps) == 1
        assert len(recs) == 1
        assert len(notes) == 1


class TestMigrateTraces:
    def test_trace_imported(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        from agent_baton.core.observe.trace import TraceRecorder

        recorder = TraceRecorder(team_context_root=ctx)
        trace = recorder.start_trace("task-001", plan_snapshot={"phases": []})
        recorder.record_event(
            trace,
            "agent_start",
            agent_name="backend-engineer--python",
            phase=1,
            step=1,
        )
        recorder.complete_trace(trace, outcome="SHIP")

        imported = StorageMigrator(ctx).migrate()
        assert imported["traces"] == 1

        conn = sqlite3.connect(str(ctx / "baton.db"))
        row = conn.execute(
            "SELECT outcome FROM traces WHERE task_id = ?", ("task-001",)
        ).fetchone()
        events = conn.execute(
            "SELECT event_type FROM trace_events WHERE task_id = ?", ("task-001",)
        ).fetchall()
        conn.close()

        assert row[0] == "SHIP"
        assert len(events) == 1


class TestMigratePatterns:
    def test_patterns_imported(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        patterns = [
            LearnedPattern(
                pattern_id=f"phased_delivery-{i:03d}",
                task_type="phased_delivery",
                stack=None,
                recommended_template="Standard workflow",
                recommended_agents=["backend-engineer--python"],
                confidence=0.8,
                sample_size=10,
                success_rate=0.9,
                avg_token_cost=5000,
                created_at="2026-03-01T00:00:00Z",
                updated_at="2026-03-01T00:00:00Z",
            )
            for i in range(2)
        ]
        (ctx / "learned-patterns.json").write_text(
            json.dumps([p.to_dict() for p in patterns]), encoding="utf-8"
        )

        imported = StorageMigrator(ctx).migrate()
        assert imported["patterns"] == 2


class TestMigrateBudget:
    def test_budget_recommendations_imported(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        recs = [
            BudgetRecommendation(
                task_type="phased_delivery",
                current_tier="standard",
                recommended_tier="lean",
                reason="p95 below floor",
                avg_tokens_used=10000,
                median_tokens_used=8000,
                p95_tokens_used=12000,
                sample_size=5,
                confidence=0.5,
                potential_savings=2000,
            )
        ]
        (ctx / "budget-recommendations.json").write_text(
            json.dumps([r.to_dict() for r in recs]), encoding="utf-8"
        )

        imported = StorageMigrator(ctx).migrate()
        assert imported["budget"] == 1


class TestMigrateActiveTask:
    def test_active_task_imported(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        (ctx / "active-task-id.txt").write_text("task-001", encoding="utf-8")

        StorageMigrator(ctx).migrate()

        conn = sqlite3.connect(str(ctx / "baton.db"))
        row = conn.execute("SELECT task_id FROM active_task WHERE id = 1").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "task-001"

    def test_missing_active_task_file_is_skipped(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        imported = StorageMigrator(ctx).migrate()
        assert imported["active_task"] == 0


# ---------------------------------------------------------------------------
# verify()
# ---------------------------------------------------------------------------

class TestVerify:
    def test_verify_matches_after_migration(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("task-001"))
        _write_usage(ctx, [_usage_record("task-001")])

        migrator = StorageMigrator(ctx)
        migrator.migrate()
        verification = migrator.verify()

        assert verification["executions"] == (1, 1)
        assert verification["usage"] == (1, 1)

    def test_verify_mismatch_when_db_missing(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("task-001"))

        # Do NOT migrate — DB is empty
        migrator = StorageMigrator(ctx)
        verification = migrator.verify()

        src, db = verification["executions"]
        assert src == 1
        assert db == 0


# ---------------------------------------------------------------------------
# File archiving (keep_files=False)
# ---------------------------------------------------------------------------

class TestArchiving:
    def test_files_moved_to_backup_when_remove_files(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("task-001"))
        _write_usage(ctx, [_usage_record("task-001")])

        StorageMigrator(ctx).migrate(keep_files=False)

        backup = ctx / "pre-sqlite-backup"
        assert backup.is_dir()
        # The executions directory should have been moved
        assert (backup / "executions").is_dir()
        assert not (ctx / "executions").is_dir()

    def test_files_kept_by_default(self, tmp_path: Path) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("task-001"))

        StorageMigrator(ctx).migrate(keep_files=True)

        assert (ctx / "executions").is_dir()
        assert not (ctx / "pre-sqlite-backup").is_dir()


# ---------------------------------------------------------------------------
# CLI handler
# ---------------------------------------------------------------------------

class TestCLIHandler:
    def test_handler_dry_run_no_db(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("task-001"))

        import argparse
        from agent_baton.cli.commands.observe.migrate_storage import handler

        args = argparse.Namespace(
            team_context=str(ctx),
            dry_run=True,
            remove_files=False,
            verify=False,
        )
        handler(args)

        captured = capsys.readouterr()
        assert "dry run" in captured.out
        assert not (ctx / "baton.db").exists()

    def test_handler_migrates_and_prints_summary(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("task-001"))
        _write_usage(ctx, [_usage_record("task-001")])

        import argparse
        from agent_baton.cli.commands.observe.migrate_storage import handler

        args = argparse.Namespace(
            team_context=str(ctx),
            dry_run=False,
            remove_files=False,
            verify=False,
        )
        handler(args)

        captured = capsys.readouterr()
        assert "Migrating" in captured.out
        assert (ctx / "baton.db").exists()

    def test_handler_verify_shows_ok(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        _write_execution(ctx, _execution_state("task-001"))

        import argparse
        from agent_baton.cli.commands.observe.migrate_storage import handler

        args = argparse.Namespace(
            team_context=str(ctx),
            dry_run=False,
            remove_files=False,
            verify=True,
        )
        handler(args)

        captured = capsys.readouterr()
        assert "OK" in captured.out

    def test_handler_missing_directory(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        import argparse
        from agent_baton.cli.commands.observe.migrate_storage import handler

        args = argparse.Namespace(
            team_context=str(tmp_path / "nonexistent"),
            dry_run=False,
            remove_files=False,
            verify=False,
        )
        handler(args)

        captured = capsys.readouterr()
        assert "not found" in captured.out

    def test_handler_empty_context_exits_cleanly(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        ctx = tmp_path / "team-context"
        ctx.mkdir()

        import argparse
        from agent_baton.cli.commands.observe.migrate_storage import handler

        args = argparse.Namespace(
            team_context=str(ctx),
            dry_run=False,
            remove_files=False,
            verify=False,
        )
        handler(args)

        captured = capsys.readouterr()
        assert "Nothing to migrate" in captured.out

    def test_register_returns_parser(self) -> None:
        import argparse
        from agent_baton.cli.commands.observe.migrate_storage import register

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        sp = register(sub)

        assert sp.prog.endswith("migrate-storage")


# ---------------------------------------------------------------------------
# ConnectionManager migration idempotency tests
# ---------------------------------------------------------------------------

class TestConnectionManagerMigrationIdempotency:
    """Guard against 'duplicate column name' errors when a column that a
    migration adds already exists in the database.

    This scenario occurs when a database received schema changes outside
    the normal version-tracking path — e.g. when a migration was applied
    once but the version counter was rolled back, so the engine attempts
    to apply the same migration again on the next startup.

    The real-world trigger was baton.db sitting at version=5 but already
    possessing the ``quality_score`` and ``retrieval_count`` columns that
    the v6 migration adds to the ``beads`` table.
    """

    def test_migration_normal_path(self, tmp_path: Path) -> None:
        """A v5 database without quality_score upgrades to v6 cleanly."""
        from agent_baton.core.storage.connection import ConnectionManager
        from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL

        db_path = tmp_path / "test.db"

        # Build a minimal v5-equivalent DB: _schema_version table + beads
        # table WITHOUT quality_score/retrieval_count.
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE _schema_version (version INTEGER NOT NULL)"
        )
        conn.execute("INSERT INTO _schema_version (version) VALUES (5)")
        conn.execute(
            """CREATE TABLE beads (
                bead_id        TEXT PRIMARY KEY,
                task_id        TEXT NOT NULL,
                step_id        TEXT NOT NULL,
                agent_name     TEXT NOT NULL,
                bead_type      TEXT NOT NULL,
                content        TEXT NOT NULL DEFAULT '',
                confidence     TEXT NOT NULL DEFAULT 'medium',
                scope          TEXT NOT NULL DEFAULT 'step',
                tags           TEXT NOT NULL DEFAULT '[]',
                affected_files TEXT NOT NULL DEFAULT '[]',
                status         TEXT NOT NULL DEFAULT 'open',
                created_at     TEXT NOT NULL DEFAULT '',
                closed_at      TEXT NOT NULL DEFAULT '',
                summary        TEXT NOT NULL DEFAULT '',
                links          TEXT NOT NULL DEFAULT '[]',
                source         TEXT NOT NULL DEFAULT 'agent-signal',
                token_estimate INTEGER NOT NULL DEFAULT 0
            )"""
        )
        conn.commit()
        conn.close()

        mgr = ConnectionManager(db_path)
        mgr.configure_schema(PROJECT_SCHEMA_DDL, 6)
        result_conn = mgr.get_connection()  # triggers _ensure_schema

        cols = {
            row[1]
            for row in result_conn.execute("PRAGMA table_info(beads)")
        }
        assert "quality_score" in cols
        assert "retrieval_count" in cols

        version = result_conn.execute(
            "SELECT version FROM _schema_version"
        ).fetchone()[0]
        assert version == 6
        mgr.close()

    def test_migration_idempotent_duplicate_columns(self, tmp_path: Path) -> None:
        """A v5 database that already has quality_score/retrieval_count
        upgrades to v6 without raising an error.

        This is the exact bug: baton.db had version=5 in _schema_version
        but quality_score already present in beads.  Previously
        _run_migrations called executescript() which failed hard on the
        duplicate column.  After the fix it skips those statements silently.
        """
        from agent_baton.core.storage.connection import ConnectionManager
        from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL

        db_path = tmp_path / "test.db"

        # Simulate the broken state: version=5 but beads already has the
        # v6 columns.
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE _schema_version (version INTEGER NOT NULL)"
        )
        conn.execute("INSERT INTO _schema_version (version) VALUES (5)")
        conn.execute(
            """CREATE TABLE beads (
                bead_id         TEXT PRIMARY KEY,
                task_id         TEXT NOT NULL,
                step_id         TEXT NOT NULL,
                agent_name      TEXT NOT NULL,
                bead_type       TEXT NOT NULL,
                content         TEXT NOT NULL DEFAULT '',
                confidence      TEXT NOT NULL DEFAULT 'medium',
                scope           TEXT NOT NULL DEFAULT 'step',
                tags            TEXT NOT NULL DEFAULT '[]',
                affected_files  TEXT NOT NULL DEFAULT '[]',
                status          TEXT NOT NULL DEFAULT 'open',
                created_at      TEXT NOT NULL DEFAULT '',
                closed_at       TEXT NOT NULL DEFAULT '',
                summary         TEXT NOT NULL DEFAULT '',
                links           TEXT NOT NULL DEFAULT '[]',
                source          TEXT NOT NULL DEFAULT 'agent-signal',
                token_estimate  INTEGER NOT NULL DEFAULT 0,
                quality_score   REAL    NOT NULL DEFAULT 0.0,
                retrieval_count INTEGER NOT NULL DEFAULT 0
            )"""
        )
        conn.commit()
        conn.close()

        mgr = ConnectionManager(db_path)
        mgr.configure_schema(PROJECT_SCHEMA_DDL, 6)

        # Must not raise; previously raised OperationalError duplicate column
        result_conn = mgr.get_connection()

        cols = {
            row[1]
            for row in result_conn.execute("PRAGMA table_info(beads)")
        }
        assert "quality_score" in cols
        assert "retrieval_count" in cols

        version = result_conn.execute(
            "SELECT version FROM _schema_version"
        ).fetchone()[0]
        assert version == 6
        mgr.close()

    def test_migration_real_errors_still_propagate(self, tmp_path: Path) -> None:
        """A genuine SQL error (not duplicate-column) is not swallowed."""
        from agent_baton.core.storage.connection import ConnectionManager

        db_path = tmp_path / "test.db"

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE _schema_version (version INTEGER NOT NULL)"
        )
        conn.execute("INSERT INTO _schema_version (version) VALUES (1)")
        conn.commit()
        conn.close()

        # Inject a migration that references a non-existent table — a real
        # schema error that must not be silently ignored.
        bad_ddl = "ALTER TABLE nonexistent_table ADD COLUMN foo TEXT;"
        from agent_baton.core.storage import schema as schema_mod
        original_migrations = schema_mod.MIGRATIONS.copy()
        schema_mod.MIGRATIONS[2] = bad_ddl

        try:
            mgr = ConnectionManager(db_path)
            mgr.configure_schema("", 2)  # empty DDL, version=2 triggers migration
            # configure_schema only sets ddl/version; _ensure_schema is called
            # on first get_connection().  Provide a stub DDL that creates
            # _schema_version so the version-check branch is reached.
            mgr._schema_ddl = ""
            mgr._schema_version = 2

            with pytest.raises(sqlite3.OperationalError, match="nonexistent_table"):
                # We need to call _run_migrations directly because
                # _ensure_schema sees _schema_version already exists and
                # goes into the migration branch.
                raw = sqlite3.connect(str(db_path))
                raw.row_factory = sqlite3.Row
                mgr._run_migrations(raw, 1, 2)
                raw.close()
        finally:
            schema_mod.MIGRATIONS.clear()
            schema_mod.MIGRATIONS.update(original_migrations)
