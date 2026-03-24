"""SQLite storage backend for Agent-Baton per-project persistence.

Replaces all JSON/JSONL flat-file persistence with a single baton.db database.
All multi-table writes are wrapped in transactions for atomicity.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION

if TYPE_CHECKING:
    pass


class SqliteStorage:
    """SQLite-backed storage for a single project's baton.db.

    Thread-safe: one connection per thread via ConnectionManager.
    All public methods acquire a connection on each call; no connection
    is kept open across calls (connections are cached per-thread by
    ConnectionManager).
    """

    def __init__(self, db_path: Path) -> None:
        self._conn_mgr = ConnectionManager(db_path)
        self._conn_mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)

    @property
    def db_path(self) -> Path:
        return self._conn_mgr.db_path

    def close(self) -> None:
        """Close the SQLite connection for the current thread."""
        self._conn_mgr.close()

    def _conn(self) -> sqlite3.Connection:
        return self._conn_mgr.get_connection()

    # ==========================================================================
    # 1. Execution State
    # ==========================================================================

    def save_execution(self, state: "ExecutionState") -> None:  # noqa: F821
        """Persist a full ExecutionState — upserts all related tables."""
        from agent_baton.models.execution import ExecutionState  # noqa: F401

        conn = self._conn()
        with conn:
            # -- executions row ------------------------------------------------
            conn.execute(
                """
                INSERT OR REPLACE INTO executions
                    (task_id, status, current_phase, current_step_index,
                     started_at, completed_at, updated_at,
                     pending_gaps, resolved_decisions)
                VALUES (?, ?, ?, ?, ?, ?,
                        strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                        ?, ?)
                """,
                (
                    state.task_id,
                    state.status,
                    state.current_phase,
                    state.current_step_index,
                    state.started_at,
                    state.completed_at or None,
                    json.dumps([g.to_dict() for g in state.pending_gaps]),
                    json.dumps([d.to_dict() for d in state.resolved_decisions]),
                ),
            )

            # -- plan ----------------------------------------------------------
            _upsert_plan(conn, state.plan)

            # -- step_results (DELETE + INSERT for clean replacement) ----------
            conn.execute(
                "DELETE FROM step_results WHERE task_id = ?", (state.task_id,)
            )
            for sr in state.step_results:
                conn.execute(
                    """
                    INSERT INTO step_results
                        (task_id, step_id, agent_name, status, outcome,
                         files_changed, commit_hash, estimated_tokens,
                         duration_seconds, retries, error, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.task_id,
                        sr.step_id,
                        sr.agent_name,
                        sr.status,
                        sr.outcome,
                        json.dumps(sr.files_changed),
                        sr.commit_hash,
                        sr.estimated_tokens,
                        sr.duration_seconds,
                        sr.retries,
                        sr.error,
                        sr.completed_at,
                    ),
                )
                # team step results cascade from step_results, delete via FK
                for mr in sr.member_results:
                    conn.execute(
                        """
                        INSERT INTO team_step_results
                            (task_id, step_id, member_id, agent_name,
                             status, outcome, files_changed)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            state.task_id,
                            sr.step_id,
                            mr.member_id,
                            mr.agent_name,
                            mr.status,
                            mr.outcome,
                            json.dumps(mr.files_changed),
                        ),
                    )

            # -- gate_results --------------------------------------------------
            conn.execute(
                "DELETE FROM gate_results WHERE task_id = ?", (state.task_id,)
            )
            for gr in state.gate_results:
                conn.execute(
                    """
                    INSERT INTO gate_results
                        (task_id, phase_id, gate_type, passed, output, checked_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.task_id,
                        gr.phase_id,
                        gr.gate_type,
                        int(gr.passed),
                        gr.output,
                        gr.checked_at,
                    ),
                )

            # -- approval_results ----------------------------------------------
            conn.execute(
                "DELETE FROM approval_results WHERE task_id = ?", (state.task_id,)
            )
            for ar in state.approval_results:
                conn.execute(
                    """
                    INSERT INTO approval_results
                        (task_id, phase_id, result, feedback, decided_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        state.task_id,
                        ar.phase_id,
                        ar.result,
                        ar.feedback,
                        ar.decided_at,
                    ),
                )

            # -- amendments ----------------------------------------------------
            conn.execute(
                "DELETE FROM amendments WHERE task_id = ?", (state.task_id,)
            )
            for am in state.amendments:
                conn.execute(
                    """
                    INSERT INTO amendments
                        (task_id, amendment_id, trigger, trigger_phase_id,
                         description, phases_added, steps_added,
                         feedback, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.task_id,
                        am.amendment_id,
                        am.trigger,
                        am.trigger_phase_id,
                        am.description,
                        json.dumps(am.phases_added),
                        json.dumps(am.steps_added),
                        am.feedback,
                        am.created_at,
                    ),
                )

    def load_execution(self, task_id: str) -> "ExecutionState | None":
        """Reconstruct a full ExecutionState from normalized tables."""
        from agent_baton.models.execution import (
            ApprovalResult,
            ExecutionState,
            GateResult,
            PlanAmendment,
            StepResult,
            TeamStepResult,
        )
        from agent_baton.models.knowledge import KnowledgeGapSignal, ResolvedDecision

        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM executions WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None

        plan = _load_plan_struct(conn, task_id)
        if plan is None:
            return None

        # step_results
        step_rows = conn.execute(
            "SELECT * FROM step_results WHERE task_id = ? ORDER BY rowid",
            (task_id,),
        ).fetchall()
        team_rows = conn.execute(
            "SELECT * FROM team_step_results WHERE task_id = ? ORDER BY rowid",
            (task_id,),
        ).fetchall()
        # group team results by step_id
        team_by_step: dict[str, list] = {}
        for tr in team_rows:
            team_by_step.setdefault(tr["step_id"], []).append(tr)

        step_results = []
        for sr in step_rows:
            member_results = [
                TeamStepResult(
                    member_id=mr["member_id"],
                    agent_name=mr["agent_name"],
                    status=mr["status"],
                    outcome=mr["outcome"],
                    files_changed=json.loads(mr["files_changed"]),
                )
                for mr in team_by_step.get(sr["step_id"], [])
            ]
            step_results.append(
                StepResult(
                    step_id=sr["step_id"],
                    agent_name=sr["agent_name"],
                    status=sr["status"],
                    outcome=sr["outcome"],
                    files_changed=json.loads(sr["files_changed"]),
                    commit_hash=sr["commit_hash"],
                    estimated_tokens=sr["estimated_tokens"],
                    duration_seconds=sr["duration_seconds"],
                    retries=sr["retries"],
                    error=sr["error"],
                    completed_at=sr["completed_at"],
                    member_results=member_results,
                )
            )

        # gate_results
        gate_results = [
            GateResult(
                phase_id=gr["phase_id"],
                gate_type=gr["gate_type"],
                passed=bool(gr["passed"]),
                output=gr["output"],
                checked_at=gr["checked_at"],
            )
            for gr in conn.execute(
                "SELECT * FROM gate_results WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        ]

        # approval_results
        approval_results = [
            ApprovalResult(
                phase_id=ar["phase_id"],
                result=ar["result"],
                feedback=ar["feedback"],
                decided_at=ar["decided_at"],
            )
            for ar in conn.execute(
                "SELECT * FROM approval_results WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        ]

        # amendments
        amendments = [
            PlanAmendment(
                amendment_id=am["amendment_id"],
                trigger=am["trigger"],
                trigger_phase_id=am["trigger_phase_id"],
                description=am["description"],
                phases_added=json.loads(am["phases_added"]),
                steps_added=json.loads(am["steps_added"]),
                feedback=am["feedback"],
                created_at=am["created_at"],
            )
            for am in conn.execute(
                "SELECT * FROM amendments WHERE task_id = ? ORDER BY rowid",
                (task_id,),
            ).fetchall()
        ]

        exec_keys = row.keys() if hasattr(row, "keys") else []
        raw_pg = row["pending_gaps"] if "pending_gaps" in exec_keys else "[]"
        raw_rd = row["resolved_decisions"] if "resolved_decisions" in exec_keys else "[]"

        return ExecutionState(
            task_id=row["task_id"],
            plan=plan,
            current_phase=row["current_phase"],
            current_step_index=row["current_step_index"],
            status=row["status"],
            step_results=step_results,
            gate_results=gate_results,
            approval_results=approval_results,
            amendments=amendments,
            started_at=row["started_at"],
            completed_at=row["completed_at"] or "",
            pending_gaps=[
                KnowledgeGapSignal.from_dict(g)
                for g in json.loads(raw_pg or "[]")
            ],
            resolved_decisions=[
                ResolvedDecision.from_dict(d)
                for d in json.loads(raw_rd or "[]")
            ],
        )

    def list_executions(self) -> list[str]:
        """Return all task_ids in the executions table."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT task_id FROM executions ORDER BY started_at"
        ).fetchall()
        return [r["task_id"] for r in rows]

    def delete_execution(self, task_id: str) -> None:
        """Delete an execution and all its child rows (CASCADE)."""
        conn = self._conn()
        with conn:
            conn.execute("DELETE FROM executions WHERE task_id = ?", (task_id,))

    # ==========================================================================
    # 2. Active Task
    # ==========================================================================

    def set_active_task(self, task_id: str) -> None:
        """Persist the active task_id (singleton row, id=1)."""
        conn = self._conn()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO active_task (id, task_id) VALUES (1, ?)",
                (task_id,),
            )

    def get_active_task(self) -> str | None:
        """Return the active task_id, or None if none is set."""
        conn = self._conn()
        row = conn.execute(
            "SELECT task_id FROM active_task WHERE id = 1"
        ).fetchone()
        return row["task_id"] if row else None

    # ==========================================================================
    # 3. Plans (standalone — queued, not yet executing)
    # ==========================================================================

    def save_plan(self, plan: "MachinePlan") -> None:  # noqa: F821
        """Save a plan without starting an execution.

        Creates an executions row with status='queued' so that the plans
        foreign-key constraint is satisfied.
        """
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO executions
                    (task_id, status, current_phase, current_step_index,
                     started_at, updated_at)
                VALUES (?, 'queued', 0, 0,
                        strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                        strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                """,
                (plan.task_id,),
            )
            _upsert_plan(conn, plan)

    def load_plan(self, task_id: str) -> "MachinePlan | None":
        """Load a MachinePlan from plans + plan_phases + plan_steps."""
        conn = self._conn()
        return _load_plan_struct(conn, task_id)

    # ==========================================================================
    # 4. Incremental result writers
    # ==========================================================================

    def save_step_result(self, task_id: str, result: "StepResult") -> None:  # noqa: F821
        """Upsert a single StepResult (and its TeamStepResults)."""
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO step_results
                    (task_id, step_id, agent_name, status, outcome,
                     files_changed, commit_hash, estimated_tokens,
                     duration_seconds, retries, error, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    result.step_id,
                    result.agent_name,
                    result.status,
                    result.outcome,
                    json.dumps(result.files_changed),
                    result.commit_hash,
                    result.estimated_tokens,
                    result.duration_seconds,
                    result.retries,
                    result.error,
                    result.completed_at,
                ),
            )
            # Replace team member results for this step
            conn.execute(
                "DELETE FROM team_step_results WHERE task_id = ? AND step_id = ?",
                (task_id, result.step_id),
            )
            for mr in result.member_results:
                conn.execute(
                    """
                    INSERT INTO team_step_results
                        (task_id, step_id, member_id, agent_name,
                         status, outcome, files_changed)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        result.step_id,
                        mr.member_id,
                        mr.agent_name,
                        mr.status,
                        mr.outcome,
                        json.dumps(mr.files_changed),
                    ),
                )

    def save_gate_result(self, task_id: str, result: "GateResult") -> None:  # noqa: F821
        """Append a GateResult row."""
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT INTO gate_results
                    (task_id, phase_id, gate_type, passed, output, checked_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    result.phase_id,
                    result.gate_type,
                    int(result.passed),
                    result.output,
                    result.checked_at,
                ),
            )

    def save_approval_result(self, task_id: str, result: "ApprovalResult") -> None:  # noqa: F821
        """Append an ApprovalResult row."""
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT INTO approval_results
                    (task_id, phase_id, result, feedback, decided_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    result.phase_id,
                    result.result,
                    result.feedback,
                    result.decided_at,
                ),
            )

    def save_amendment(self, task_id: str, amendment: "PlanAmendment") -> None:  # noqa: F821
        """Upsert a PlanAmendment row."""
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO amendments
                    (task_id, amendment_id, trigger, trigger_phase_id,
                     description, phases_added, steps_added,
                     feedback, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    amendment.amendment_id,
                    amendment.trigger,
                    amendment.trigger_phase_id,
                    amendment.description,
                    json.dumps(amendment.phases_added),
                    json.dumps(amendment.steps_added),
                    amendment.feedback,
                    amendment.created_at,
                ),
            )

    # ==========================================================================
    # 5. Events
    # ==========================================================================

    def append_event(self, event: "Event") -> None:  # noqa: F821
        """Append a domain Event to the events table."""
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO events
                    (event_id, task_id, timestamp, topic, sequence, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.task_id,
                    event.timestamp,
                    event.topic,
                    event.sequence,
                    json.dumps(event.payload),
                ),
            )

    def read_events(self, task_id: str, from_seq: int = 0) -> list["Event"]:
        """Return all events for task_id with sequence >= from_seq."""
        from agent_baton.models.events import Event

        conn = self._conn()
        rows = conn.execute(
            """
            SELECT * FROM events
            WHERE task_id = ? AND sequence >= ?
            ORDER BY sequence, rowid
            """,
            (task_id, from_seq),
        ).fetchall()
        return [
            Event(
                event_id=r["event_id"],
                timestamp=r["timestamp"],
                topic=r["topic"],
                task_id=r["task_id"],
                sequence=r["sequence"],
                payload=json.loads(r["payload"]),
            )
            for r in rows
        ]

    def delete_events(self, task_id: str) -> None:
        """Delete all events for a task."""
        conn = self._conn()
        with conn:
            conn.execute("DELETE FROM events WHERE task_id = ?", (task_id,))

    # ==========================================================================
    # 6. Usage
    # ==========================================================================

    def log_usage(self, record: "TaskUsageRecord") -> None:  # noqa: F821
        """Insert (or replace) a TaskUsageRecord and its AgentUsageRecords."""
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO usage_records
                    (task_id, timestamp, total_agents, risk_level,
                     sequencing_mode, gates_passed, gates_failed,
                     outcome, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.task_id,
                    record.timestamp,
                    record.total_agents,
                    record.risk_level,
                    record.sequencing_mode,
                    record.gates_passed,
                    record.gates_failed,
                    record.outcome,
                    record.notes,
                ),
            )
            # agent_usage has no natural PK — delete and re-insert
            conn.execute(
                "DELETE FROM agent_usage WHERE task_id = ?", (record.task_id,)
            )
            for au in record.agents_used:
                conn.execute(
                    """
                    INSERT INTO agent_usage
                        (task_id, agent_name, model, steps, retries,
                         gate_results, estimated_tokens, duration_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.task_id,
                        au.name,
                        au.model,
                        au.steps,
                        au.retries,
                        json.dumps(au.gate_results),
                        au.estimated_tokens,
                        au.duration_seconds,
                    ),
                )

    def read_usage(self, limit: int | None = None) -> list["TaskUsageRecord"]:
        """Return TaskUsageRecords ordered by timestamp descending."""
        from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord

        conn = self._conn()
        query = "SELECT * FROM usage_records ORDER BY timestamp DESC"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        rows = conn.execute(query).fetchall()

        records: list[TaskUsageRecord] = []
        for row in rows:
            agent_rows = conn.execute(
                "SELECT * FROM agent_usage WHERE task_id = ?", (row["task_id"],)
            ).fetchall()
            agents_used = [
                AgentUsageRecord(
                    name=ar["agent_name"],
                    model=ar["model"],
                    steps=ar["steps"],
                    retries=ar["retries"],
                    gate_results=json.loads(ar["gate_results"]),
                    estimated_tokens=ar["estimated_tokens"],
                    duration_seconds=ar["duration_seconds"],
                )
                for ar in agent_rows
            ]
            records.append(
                TaskUsageRecord(
                    task_id=row["task_id"],
                    timestamp=row["timestamp"],
                    agents_used=agents_used,
                    total_agents=row["total_agents"],
                    risk_level=row["risk_level"],
                    sequencing_mode=row["sequencing_mode"],
                    gates_passed=row["gates_passed"],
                    gates_failed=row["gates_failed"],
                    outcome=row["outcome"],
                    notes=row["notes"],
                )
            )
        return records

    # ==========================================================================
    # 7. Telemetry
    # ==========================================================================

    def log_telemetry(self, event: dict) -> None:
        """Insert a single telemetry event dict into the telemetry table."""
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT INTO telemetry
                    (timestamp, agent_name, event_type, tool_name,
                     file_path, duration_ms, details, task_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.get("timestamp", ""),
                    event.get("agent_name", ""),
                    event.get("event_type", ""),
                    event.get("tool_name", ""),
                    event.get("file_path", ""),
                    int(event.get("duration_ms", 0)),
                    event.get("details", ""),
                    event.get("task_id", ""),
                ),
            )

    def read_telemetry(self, limit: int | None = None) -> list[dict]:
        """Return telemetry events as dicts, ordered by timestamp descending."""
        conn = self._conn()
        query = "SELECT * FROM telemetry ORDER BY timestamp DESC"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        rows = conn.execute(query).fetchall()
        return [
            {
                "timestamp": r["timestamp"],
                "agent_name": r["agent_name"],
                "event_type": r["event_type"],
                "tool_name": r["tool_name"],
                "file_path": r["file_path"],
                "duration_ms": r["duration_ms"],
                "details": r["details"],
                "task_id": r["task_id"],
            }
            for r in rows
        ]

    def telemetry_summary(self) -> dict:
        """Return aggregated telemetry statistics."""
        conn = self._conn()
        total = conn.execute("SELECT COUNT(*) AS n FROM telemetry").fetchone()["n"]

        by_agent = {
            r["agent_name"]: r["cnt"]
            for r in conn.execute(
                "SELECT agent_name, COUNT(*) AS cnt FROM telemetry GROUP BY agent_name"
            ).fetchall()
        }
        by_type = {
            r["event_type"]: r["cnt"]
            for r in conn.execute(
                "SELECT event_type, COUNT(*) AS cnt FROM telemetry GROUP BY event_type"
            ).fetchall()
        }
        files_read = [
            r["file_path"]
            for r in conn.execute(
                "SELECT file_path FROM telemetry WHERE event_type = 'file_read' AND file_path != ''"
            ).fetchall()
        ]
        files_written = [
            r["file_path"]
            for r in conn.execute(
                "SELECT file_path FROM telemetry WHERE event_type = 'file_write' AND file_path != ''"
            ).fetchall()
        ]
        return {
            "total_events": total,
            "events_by_agent": by_agent,
            "events_by_type": by_type,
            "files_read": files_read,
            "files_written": files_written,
        }

    # ==========================================================================
    # 8. Retrospectives
    # ==========================================================================

    def save_retrospective(self, retro: "Retrospective") -> None:  # noqa: F821
        """Persist a Retrospective and all its child collections."""
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO retrospectives
                    (task_id, task_name, timestamp, agent_count,
                     retry_count, gates_passed, gates_failed, risk_level,
                     duration_estimate, estimated_tokens, markdown)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    retro.task_id,
                    retro.task_name,
                    retro.timestamp,
                    retro.agent_count,
                    retro.retry_count,
                    retro.gates_passed,
                    retro.gates_failed,
                    retro.risk_level,
                    retro.duration_estimate,
                    retro.estimated_tokens,
                    retro.to_markdown(),
                ),
            )

            # outcomes: what_worked (category='worked') + what_didnt (category='didnt')
            conn.execute(
                "DELETE FROM retrospective_outcomes WHERE task_id = ?",
                (retro.task_id,),
            )
            for outcome in retro.what_worked:
                conn.execute(
                    """
                    INSERT INTO retrospective_outcomes
                        (task_id, category, agent_name,
                         worked_well, issues, root_cause)
                    VALUES (?, 'worked', ?, ?, ?, ?)
                    """,
                    (
                        retro.task_id,
                        outcome.name,
                        outcome.worked_well,
                        outcome.issues,
                        outcome.root_cause,
                    ),
                )
            for outcome in retro.what_didnt:
                conn.execute(
                    """
                    INSERT INTO retrospective_outcomes
                        (task_id, category, agent_name,
                         worked_well, issues, root_cause)
                    VALUES (?, 'didnt', ?, ?, ?, ?)
                    """,
                    (
                        retro.task_id,
                        outcome.name,
                        outcome.worked_well,
                        outcome.issues,
                        outcome.root_cause,
                    ),
                )

            conn.execute(
                "DELETE FROM knowledge_gaps WHERE task_id = ?", (retro.task_id,)
            )
            for gap in retro.knowledge_gaps:
                conn.execute(
                    """
                    INSERT INTO knowledge_gaps
                        (task_id, description, affected_agent, suggested_fix)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        retro.task_id,
                        gap.description,
                        gap.affected_agent,
                        gap.suggested_fix,
                    ),
                )

            conn.execute(
                "DELETE FROM roster_recommendations WHERE task_id = ?",
                (retro.task_id,),
            )
            for rec in retro.roster_recommendations:
                conn.execute(
                    """
                    INSERT INTO roster_recommendations
                        (task_id, action, target, reason)
                    VALUES (?, ?, ?, ?)
                    """,
                    (retro.task_id, rec.action, rec.target, rec.reason),
                )

            conn.execute(
                "DELETE FROM sequencing_notes WHERE task_id = ?", (retro.task_id,)
            )
            for note in retro.sequencing_notes:
                conn.execute(
                    """
                    INSERT INTO sequencing_notes
                        (task_id, phase, observation, keep)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        retro.task_id,
                        note.phase,
                        note.observation,
                        int(note.keep),
                    ),
                )

    def load_retrospective(self, task_id: str) -> "Retrospective | None":
        """Reconstruct a Retrospective from the database."""
        from agent_baton.models.retrospective import (
            AgentOutcome,
            KnowledgeGap,
            Retrospective,
            RosterRecommendation,
            SequencingNote,
        )

        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM retrospectives WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None

        outcome_rows = conn.execute(
            "SELECT * FROM retrospective_outcomes WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        what_worked = [
            AgentOutcome(
                name=r["agent_name"],
                worked_well=r["worked_well"],
                issues=r["issues"],
                root_cause=r["root_cause"],
            )
            for r in outcome_rows
            if r["category"] == "worked"
        ]
        what_didnt = [
            AgentOutcome(
                name=r["agent_name"],
                worked_well=r["worked_well"],
                issues=r["issues"],
                root_cause=r["root_cause"],
            )
            for r in outcome_rows
            if r["category"] == "didnt"
        ]

        knowledge_gaps = [
            KnowledgeGap(
                description=r["description"],
                affected_agent=r["affected_agent"],
                suggested_fix=r["suggested_fix"],
            )
            for r in conn.execute(
                "SELECT * FROM knowledge_gaps WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        ]

        roster_recommendations = [
            RosterRecommendation(
                action=r["action"],
                target=r["target"],
                reason=r["reason"],
            )
            for r in conn.execute(
                "SELECT * FROM roster_recommendations WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        ]

        sequencing_notes = [
            SequencingNote(
                phase=r["phase"],
                observation=r["observation"],
                keep=bool(r["keep"]),
            )
            for r in conn.execute(
                "SELECT * FROM sequencing_notes WHERE task_id = ? ORDER BY id",
                (task_id,),
            ).fetchall()
        ]

        return Retrospective(
            task_id=row["task_id"],
            task_name=row["task_name"],
            timestamp=row["timestamp"],
            agent_count=row["agent_count"],
            retry_count=row["retry_count"],
            gates_passed=row["gates_passed"],
            gates_failed=row["gates_failed"],
            risk_level=row["risk_level"],
            duration_estimate=row["duration_estimate"],
            estimated_tokens=row["estimated_tokens"],
            what_worked=what_worked,
            what_didnt=what_didnt,
            knowledge_gaps=knowledge_gaps,
            roster_recommendations=roster_recommendations,
            sequencing_notes=sequencing_notes,
        )

    def list_retrospective_ids(self, limit: int = 100) -> list[str]:
        """Return task_ids of retrospectives, most recent first."""
        conn = self._conn()
        rows = conn.execute(
            "SELECT task_id FROM retrospectives ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r["task_id"] for r in rows]

    # ==========================================================================
    # 9. Traces
    # ==========================================================================

    def save_trace(self, trace: "TaskTrace") -> None:  # noqa: F821
        """Persist a TaskTrace (upsert header + DELETE/INSERT events)."""
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO traces
                    (task_id, plan_snapshot, started_at, completed_at, outcome)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    trace.task_id,
                    json.dumps(trace.plan_snapshot),
                    trace.started_at,
                    trace.completed_at,
                    trace.outcome,
                ),
            )
            conn.execute(
                "DELETE FROM trace_events WHERE task_id = ?", (trace.task_id,)
            )
            for ev in trace.events:
                conn.execute(
                    """
                    INSERT INTO trace_events
                        (task_id, timestamp, event_type, agent_name,
                         phase, step, details, duration_seconds)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trace.task_id,
                        ev.timestamp,
                        ev.event_type,
                        ev.agent_name,
                        ev.phase,
                        ev.step,
                        json.dumps(ev.details),
                        ev.duration_seconds,
                    ),
                )

    def load_trace(self, task_id: str) -> "TaskTrace | None":
        """Reconstruct a TaskTrace from the database."""
        from agent_baton.models.trace import TaskTrace, TraceEvent

        conn = self._conn()
        row = conn.execute(
            "SELECT * FROM traces WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None

        event_rows = conn.execute(
            "SELECT * FROM trace_events WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()
        events = [
            TraceEvent(
                timestamp=er["timestamp"],
                event_type=er["event_type"],
                agent_name=er["agent_name"],
                phase=er["phase"],
                step=er["step"],
                details=json.loads(er["details"]),
                duration_seconds=er["duration_seconds"],
            )
            for er in event_rows
        ]

        return TaskTrace(
            task_id=row["task_id"],
            plan_snapshot=json.loads(row["plan_snapshot"]),
            events=events,
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            outcome=row["outcome"],
        )

    # ==========================================================================
    # 10. Patterns & Budget
    # ==========================================================================

    def save_patterns(self, patterns: list["LearnedPattern"]) -> None:  # noqa: F821
        """Full replacement: delete all existing patterns then insert."""
        conn = self._conn()
        with conn:
            conn.execute("DELETE FROM learned_patterns")
            for p in patterns:
                conn.execute(
                    """
                    INSERT INTO learned_patterns
                        (pattern_id, task_type, stack,
                         recommended_template, recommended_agents,
                         confidence, sample_size, success_rate,
                         avg_token_cost, evidence, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        p.pattern_id,
                        p.task_type,
                        p.stack,
                        p.recommended_template,
                        json.dumps(p.recommended_agents),
                        p.confidence,
                        p.sample_size,
                        p.success_rate,
                        p.avg_token_cost,
                        json.dumps(p.evidence),
                        p.created_at,
                        p.updated_at,
                    ),
                )

    def load_patterns(self) -> list["LearnedPattern"]:
        """Return all learned patterns."""
        from agent_baton.models.pattern import LearnedPattern

        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM learned_patterns ORDER BY confidence DESC"
        ).fetchall()
        return [
            LearnedPattern(
                pattern_id=r["pattern_id"],
                task_type=r["task_type"],
                stack=r["stack"],
                recommended_template=r["recommended_template"],
                recommended_agents=json.loads(r["recommended_agents"]),
                confidence=r["confidence"],
                sample_size=r["sample_size"],
                success_rate=r["success_rate"],
                avg_token_cost=r["avg_token_cost"],
                evidence=json.loads(r["evidence"]),
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def save_budget_recommendations(
        self, recs: list["BudgetRecommendation"]  # noqa: F821
    ) -> None:
        """Full replacement: delete all then insert."""
        conn = self._conn()
        with conn:
            conn.execute("DELETE FROM budget_recommendations")
            for rec in recs:
                conn.execute(
                    """
                    INSERT INTO budget_recommendations
                        (task_type, current_tier, recommended_tier,
                         reason, avg_tokens_used, median_tokens_used,
                         p95_tokens_used, sample_size, confidence,
                         potential_savings)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rec.task_type,
                        rec.current_tier,
                        rec.recommended_tier,
                        rec.reason,
                        rec.avg_tokens_used,
                        rec.median_tokens_used,
                        rec.p95_tokens_used,
                        rec.sample_size,
                        rec.confidence,
                        rec.potential_savings,
                    ),
                )

    def load_budget_recommendations(self) -> list["BudgetRecommendation"]:
        """Return all budget recommendations."""
        from agent_baton.models.budget import BudgetRecommendation

        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM budget_recommendations ORDER BY task_type"
        ).fetchall()
        return [
            BudgetRecommendation(
                task_type=r["task_type"],
                current_tier=r["current_tier"],
                recommended_tier=r["recommended_tier"],
                reason=r["reason"],
                avg_tokens_used=r["avg_tokens_used"],
                median_tokens_used=r["median_tokens_used"],
                p95_tokens_used=r["p95_tokens_used"],
                sample_size=r["sample_size"],
                confidence=r["confidence"],
                potential_savings=r["potential_savings"],
            )
            for r in rows
        ]

    # ==========================================================================
    # 11. Mission Log
    # ==========================================================================

    def append_mission_log(
        self, task_id: str, entry: "MissionLogEntry"  # noqa: F821
    ) -> None:
        """Append a MissionLogEntry row for the given task."""
        conn = self._conn()
        ts = (
            entry.timestamp.isoformat()
            if hasattr(entry.timestamp, "isoformat")
            else str(entry.timestamp)
        )
        failure_class_val: str | None = (
            entry.failure_class.value if entry.failure_class is not None else None
        )
        with conn:
            conn.execute(
                """
                INSERT INTO mission_log_entries
                    (task_id, agent_name, status, assignment, result,
                     files, decisions, issues, handoff, commit_hash,
                     failure_class, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    entry.agent_name,
                    entry.status,
                    entry.assignment,
                    entry.result,
                    json.dumps(entry.files),
                    json.dumps(entry.decisions),
                    json.dumps(entry.issues),
                    entry.handoff,
                    entry.commit_hash,
                    failure_class_val,
                    ts,
                ),
            )

    def read_mission_log(self, task_id: str) -> list["MissionLogEntry"]:
        """Return all MissionLogEntry rows for a task, in insertion order."""
        from datetime import datetime

        from agent_baton.models.enums import FailureClass
        from agent_baton.models.plan import MissionLogEntry

        conn = self._conn()
        rows = conn.execute(
            "SELECT * FROM mission_log_entries WHERE task_id = ? ORDER BY id",
            (task_id,),
        ).fetchall()

        entries: list[MissionLogEntry] = []
        for r in rows:
            fc: FailureClass | None = None
            if r["failure_class"]:
                try:
                    fc = FailureClass(r["failure_class"])
                except ValueError:
                    fc = None
            ts_raw = r["timestamp"]
            try:
                ts = datetime.fromisoformat(ts_raw)
            except (ValueError, TypeError):
                ts = datetime.now()
            entries.append(
                MissionLogEntry(
                    agent_name=r["agent_name"],
                    status=r["status"],
                    assignment=r["assignment"],
                    result=r["result"],
                    files=json.loads(r["files"]),
                    decisions=json.loads(r["decisions"]),
                    issues=json.loads(r["issues"]),
                    handoff=r["handoff"],
                    commit_hash=r["commit_hash"],
                    failure_class=fc,
                    timestamp=ts,
                )
            )
        return entries

    # ==========================================================================
    # 12. Shared Context & Codebase Profile
    # ==========================================================================

    def save_context(
        self, task_id: str, content: str, **sections: str
    ) -> None:
        """Upsert the shared_context row for a task.

        The ``content`` parameter holds the full free-text context string.
        Named keyword arguments populate the structured section columns:
        task_title, stack, architecture, conventions, guardrails,
        agent_assignments, domain_context.
        """
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO shared_context
                    (task_id, content, task_title, stack, architecture,
                     conventions, guardrails, agent_assignments,
                     domain_context, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                        strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                """,
                (
                    task_id,
                    content,
                    sections.get("task_title", ""),
                    sections.get("stack", ""),
                    sections.get("architecture", ""),
                    sections.get("conventions", ""),
                    sections.get("guardrails", ""),
                    sections.get("agent_assignments", ""),
                    sections.get("domain_context", ""),
                ),
            )

    def read_context(self, task_id: str) -> str | None:
        """Return the free-text context content for a task, or None."""
        conn = self._conn()
        row = conn.execute(
            "SELECT content FROM shared_context WHERE task_id = ?", (task_id,)
        ).fetchone()
        return row["content"] if row else None

    def save_profile(self, content: str) -> None:
        """Upsert the singleton codebase profile (id=1)."""
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO codebase_profile
                    (id, content, updated_at)
                VALUES (1, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                """,
                (content,),
            )

    def read_profile(self) -> str | None:
        """Return the codebase profile content, or None if not set."""
        conn = self._conn()
        row = conn.execute(
            "SELECT content FROM codebase_profile WHERE id = 1"
        ).fetchone()
        return row["content"] if row else None


# ==========================================================================
# Private helpers (module-level, not part of the public API)
# ==========================================================================


def _upsert_plan(conn: sqlite3.Connection, plan: "MachinePlan") -> None:  # noqa: F821
    """Insert or replace a MachinePlan and its phases/steps/team_members.

    Caller is responsible for the surrounding transaction.
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO plans
            (task_id, task_summary, risk_level, budget_tier,
             execution_mode, git_strategy, shared_context,
             pattern_source, plan_markdown, created_at,
             explicit_knowledge_packs, explicit_knowledge_docs,
             intervention_level, task_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan.task_id,
            plan.task_summary,
            plan.risk_level,
            plan.budget_tier,
            plan.execution_mode,
            plan.git_strategy,
            plan.shared_context,
            plan.pattern_source,
            plan.to_markdown(),
            plan.created_at,
            json.dumps(plan.explicit_knowledge_packs),
            json.dumps(plan.explicit_knowledge_docs),
            plan.intervention_level,
            plan.task_type,
        ),
    )

    # Phases — DELETE + INSERT so removed phases are cleaned up
    conn.execute("DELETE FROM plan_phases WHERE task_id = ?", (plan.task_id,))
    for phase in plan.phases:
        gate = phase.gate
        conn.execute(
            """
            INSERT INTO plan_phases
                (task_id, phase_id, name, approval_required,
                 approval_description, gate_type, gate_command,
                 gate_description, gate_fail_on)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.task_id,
                phase.phase_id,
                phase.name,
                int(phase.approval_required),
                phase.approval_description,
                gate.gate_type if gate else None,
                gate.command if gate else None,
                gate.description if gate else None,
                json.dumps(gate.fail_on) if gate else None,
            ),
        )

        # Steps (plan_phases DELETE cascades to plan_steps, but we're
        # re-inserting phases so we need to insert steps explicitly)
        for step in phase.steps:
            conn.execute(
                """
                INSERT INTO plan_steps
                    (task_id, step_id, phase_id, agent_name,
                     task_description, model, depends_on,
                     deliverables, allowed_paths, blocked_paths,
                     context_files, knowledge_attachments)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.task_id,
                    step.step_id,
                    phase.phase_id,
                    step.agent_name,
                    step.task_description,
                    step.model,
                    json.dumps(step.depends_on),
                    json.dumps(step.deliverables),
                    json.dumps(step.allowed_paths),
                    json.dumps(step.blocked_paths),
                    json.dumps(step.context_files),
                    json.dumps([a.to_dict() for a in step.knowledge]),
                ),
            )

            for member in step.team:
                conn.execute(
                    """
                    INSERT INTO team_members
                        (task_id, step_id, member_id, agent_name,
                         role, task_description, model,
                         depends_on, deliverables)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        plan.task_id,
                        step.step_id,
                        member.member_id,
                        member.agent_name,
                        member.role,
                        member.task_description,
                        member.model,
                        json.dumps(member.depends_on),
                        json.dumps(member.deliverables),
                    ),
                )


def _load_plan_struct(
    conn: sqlite3.Connection, task_id: str
) -> "MachinePlan | None":  # noqa: F821
    """Reconstruct a MachinePlan from plans + plan_phases + plan_steps + team_members."""
    from agent_baton.models.execution import (
        MachinePlan,
        PlanGate,
        PlanPhase,
        PlanStep,
        TeamMember,
    )
    from agent_baton.models.knowledge import KnowledgeAttachment

    plan_row = conn.execute(
        "SELECT * FROM plans WHERE task_id = ?", (task_id,)
    ).fetchone()
    if plan_row is None:
        return None

    phase_rows = conn.execute(
        "SELECT * FROM plan_phases WHERE task_id = ? ORDER BY phase_id",
        (task_id,),
    ).fetchall()
    step_rows = conn.execute(
        "SELECT * FROM plan_steps WHERE task_id = ? ORDER BY phase_id, step_id",
        (task_id,),
    ).fetchall()
    member_rows = conn.execute(
        "SELECT * FROM team_members WHERE task_id = ? ORDER BY step_id, member_id",
        (task_id,),
    ).fetchall()

    # Group steps and members by phase_id / step_id
    steps_by_phase: dict[int, list] = {}
    for sr in step_rows:
        steps_by_phase.setdefault(sr["phase_id"], []).append(sr)

    members_by_step: dict[str, list] = {}
    for mr in member_rows:
        members_by_step.setdefault(mr["step_id"], []).append(mr)

    phases: list[PlanPhase] = []
    for pr in phase_rows:
        gate: PlanGate | None = None
        if pr["gate_type"]:
            fail_on_raw = pr["gate_fail_on"]
            gate = PlanGate(
                gate_type=pr["gate_type"],
                command=pr["gate_command"] or "",
                description=pr["gate_description"] or "",
                fail_on=json.loads(fail_on_raw) if fail_on_raw else [],
            )

        steps: list[PlanStep] = []
        for sr in steps_by_phase.get(pr["phase_id"], []):
            team = [
                TeamMember(
                    member_id=mr["member_id"],
                    agent_name=mr["agent_name"],
                    role=mr["role"],
                    task_description=mr["task_description"],
                    model=mr["model"],
                    depends_on=json.loads(mr["depends_on"]),
                    deliverables=json.loads(mr["deliverables"]),
                )
                for mr in members_by_step.get(sr["step_id"], [])
            ]
            raw_ka = sr["knowledge_attachments"] if "knowledge_attachments" in sr.keys() else "[]"
            steps.append(
                PlanStep(
                    step_id=sr["step_id"],
                    agent_name=sr["agent_name"],
                    task_description=sr["task_description"],
                    model=sr["model"],
                    depends_on=json.loads(sr["depends_on"]),
                    deliverables=json.loads(sr["deliverables"]),
                    allowed_paths=json.loads(sr["allowed_paths"]),
                    blocked_paths=json.loads(sr["blocked_paths"]),
                    context_files=json.loads(sr["context_files"]),
                    team=team,
                    knowledge=[
                        KnowledgeAttachment.from_dict(a)
                        for a in json.loads(raw_ka or "[]")
                    ],
                )
            )

        phases.append(
            PlanPhase(
                phase_id=pr["phase_id"],
                name=pr["name"],
                steps=steps,
                gate=gate,
                approval_required=bool(pr["approval_required"]),
                approval_description=pr["approval_description"],
            )
        )

    # Read knowledge columns with graceful fallback for pre-v2 row factories
    plan_keys = plan_row.keys() if hasattr(plan_row, "keys") else []
    ekp = plan_row["explicit_knowledge_packs"] if "explicit_knowledge_packs" in plan_keys else "[]"
    ekd = plan_row["explicit_knowledge_docs"] if "explicit_knowledge_docs" in plan_keys else "[]"
    il = plan_row["intervention_level"] if "intervention_level" in plan_keys else "low"
    tt = plan_row["task_type"] if "task_type" in plan_keys else None

    return MachinePlan(
        task_id=plan_row["task_id"],
        task_summary=plan_row["task_summary"],
        risk_level=plan_row["risk_level"],
        budget_tier=plan_row["budget_tier"],
        execution_mode=plan_row["execution_mode"],
        git_strategy=plan_row["git_strategy"],
        phases=phases,
        shared_context=plan_row["shared_context"],
        pattern_source=plan_row["pattern_source"],
        created_at=plan_row["created_at"],
        explicit_knowledge_packs=json.loads(ekp or "[]"),
        explicit_knowledge_docs=json.loads(ekd or "[]"),
        intervention_level=il or "low",
        task_type=tt,
    )
