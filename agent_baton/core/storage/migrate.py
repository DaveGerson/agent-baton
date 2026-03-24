"""StorageMigrator — import JSON/JSONL flat files into baton.db.

Import order (respects foreign-key dependencies):

1. Executions (execution-state.json files) — parent rows for most tables
2. Events (events/*.jsonl)
3. Usage (usage-log.jsonl)
4. Telemetry (telemetry.jsonl)
5. Retrospectives (retrospectives/*.json)
6. Traces (traces/*.json)
7. Patterns (learned-patterns.json)
8. Budget (budget-recommendations.json)
9. Active task (active-task-id.txt)
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION

_log = logging.getLogger(__name__)

_BACKUP_DIR = "pre-sqlite-backup"


class StorageMigrator:
    """Migrate existing JSON/JSONL files into baton.db.

    The migrator reads source files using the same model classes the rest
    of the codebase uses, then writes normalised rows into baton.db via
    parameterised INSERT OR IGNORE statements.  Duplicate task_ids (e.g.
    both a namespaced and a legacy flat execution-state.json for the same
    task) are silently de-duplicated.

    Usage::

        migrator = StorageMigrator(Path(".claude/team-context"))
        counts = migrator.scan()      # preview what exists
        imported = migrator.migrate() # do the work
        verified = migrator.verify()  # confirm counts match
    """

    def __init__(self, context_root: Path) -> None:
        self._root = context_root.resolve()
        self._db_path = self._root / "baton.db"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> dict[str, int]:
        """Count migratable records by category without touching the DB.

        Returns a dict whose keys are category names and values are the
        number of records (or files) found on disk.
        """
        counts: dict[str, int] = {}

        # Executions: namespaced dirs + legacy flat file
        exec_ids = self._discover_execution_state_paths()
        counts["executions"] = len(exec_ids)

        # Events: one .jsonl file per task under events/
        counts["events"] = self._count_jsonl_lines(self._root / "events")

        # Usage
        counts["usage"] = self._count_jsonl_lines_file(
            self._root / "usage-log.jsonl"
        )

        # Telemetry
        counts["telemetry"] = self._count_jsonl_lines_file(
            self._root / "telemetry.jsonl"
        )

        # Retrospectives: JSON sidecar files under retrospectives/
        retro_dir = self._root / "retrospectives"
        if retro_dir.is_dir():
            counts["retrospectives"] = len(list(retro_dir.glob("*.json")))
        else:
            counts["retrospectives"] = 0

        # Traces
        traces_dir = self._root / "traces"
        if traces_dir.is_dir():
            counts["traces"] = len(list(traces_dir.glob("*.json")))
        else:
            counts["traces"] = 0

        # Patterns file
        patterns_path = self._root / "learned-patterns.json"
        if patterns_path.exists():
            try:
                raw = json.loads(patterns_path.read_text(encoding="utf-8"))
                counts["patterns"] = len(raw) if isinstance(raw, list) else 0
            except (json.JSONDecodeError, OSError):
                counts["patterns"] = 0
        else:
            counts["patterns"] = 0

        # Budget recommendations
        budget_path = self._root / "budget-recommendations.json"
        if budget_path.exists():
            try:
                raw = json.loads(budget_path.read_text(encoding="utf-8"))
                counts["budget"] = len(raw) if isinstance(raw, list) else 0
            except (json.JSONDecodeError, OSError):
                counts["budget"] = 0
        else:
            counts["budget"] = 0

        # Active task file
        counts["active_task"] = (
            1 if (self._root / "active-task-id.txt").exists() else 0
        )

        return counts

    def migrate(
        self,
        *,
        dry_run: bool = False,
        keep_files: bool = True,
    ) -> dict[str, int]:
        """Import all discovered files into baton.db.

        Args:
            dry_run: If True, scan only and return counts without writing.
            keep_files: If False, move original files to
                ``<context_root>/pre-sqlite-backup/`` after a successful
                import.  Defaults to True.

        Returns:
            Dict mapping category name to number of records imported.
        """
        if dry_run:
            return self.scan()

        conn_mgr = ConnectionManager(self._db_path)
        conn_mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
        conn = conn_mgr.get_connection()

        imported: dict[str, int] = {}

        # 1. Executions (must be first — most tables FK to executions)
        imported["executions"] = self._migrate_executions(conn)

        # 2. Events
        imported["events"] = self._migrate_events(conn)

        # 3. Usage
        imported["usage"] = self._migrate_usage(conn)

        # 4. Telemetry
        imported["telemetry"] = self._migrate_telemetry(conn)

        # 5. Retrospectives
        imported["retrospectives"] = self._migrate_retrospectives(conn)

        # 6. Traces
        imported["traces"] = self._migrate_traces(conn)

        # 7. Patterns
        imported["patterns"] = self._migrate_patterns(conn)

        # 8. Budget
        imported["budget"] = self._migrate_budget(conn)

        # 9. Active task
        imported["active_task"] = self._migrate_active_task(conn)

        conn_mgr.close()

        if not keep_files:
            self._archive_source_files()

        return imported

    def verify(self) -> dict[str, tuple[int, int]]:
        """Compare source file counts against DB row counts.

        Returns:
            Dict mapping category to ``(source_count, db_count)`` tuples.
            Both values should be equal after a successful migration.
        """
        source = self.scan()

        conn_mgr = ConnectionManager(self._db_path)
        conn_mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
        conn = conn_mgr.get_connection()

        db_counts = self._db_row_counts(conn)
        conn_mgr.close()

        result: dict[str, tuple[int, int]] = {}
        for key in source:
            result[key] = (source[key], db_counts.get(key, 0))
        return result

    # ------------------------------------------------------------------
    # Migration helpers — one per category
    # ------------------------------------------------------------------

    def _migrate_executions(self, conn: sqlite3.Connection) -> int:
        """Import all ExecutionState files into executions, plans, and
        related child tables (plan_phases, plan_steps, team_members,
        step_results, team_step_results, gate_results, approval_results,
        amendments).
        """
        from agent_baton.core.engine.persistence import StatePersistence
        from agent_baton.models.execution import ExecutionState

        states = StatePersistence.load_all(self._root)
        imported = 0

        for state in states:
            try:
                new_rows = self._insert_execution(conn, state)
                imported += new_rows
            except Exception as exc:
                _log.warning(
                    "Skipping execution %s: %s", state.task_id, exc
                )

        conn.commit()
        return imported

    def _migrate_events(self, conn: sqlite3.Connection) -> int:
        """Import all per-task JSONL event files."""
        from agent_baton.core.events.persistence import EventPersistence
        from agent_baton.models.events import Event

        events_dir = self._root / "events"
        ep = EventPersistence(events_dir=events_dir)
        task_ids = ep.list_task_ids()
        imported = 0

        for task_id in task_ids:
            events = ep.read(task_id)
            for event in events:
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO events
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
                    imported += 1
                except Exception as exc:
                    _log.warning(
                        "Skipping event %s for task %s: %s",
                        event.event_id, task_id, exc,
                    )

        conn.commit()
        return imported

    def _migrate_usage(self, conn: sqlite3.Connection) -> int:
        """Import usage-log.jsonl into usage_records and agent_usage."""
        from agent_baton.core.observe.usage import UsageLogger

        log_path = self._root / "usage-log.jsonl"
        logger = UsageLogger(log_path=log_path)
        records = logger.read_all()
        imported = 0

        for record in records:
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO usage_records
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
                for agent in record.agents_used:
                    conn.execute(
                        """
                        INSERT INTO agent_usage
                            (task_id, agent_name, model, steps, retries,
                             gate_results, estimated_tokens, duration_seconds)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            record.task_id,
                            agent.name,
                            agent.model,
                            agent.steps,
                            agent.retries,
                            json.dumps(agent.gate_results),
                            agent.estimated_tokens,
                            agent.duration_seconds,
                        ),
                    )
                imported += 1
            except Exception as exc:
                _log.warning(
                    "Skipping usage record %s: %s", record.task_id, exc
                )

        conn.commit()
        return imported

    def _migrate_telemetry(self, conn: sqlite3.Connection) -> int:
        """Import telemetry.jsonl into telemetry table."""
        from agent_baton.core.observe.telemetry import AgentTelemetry

        log_path = self._root / "telemetry.jsonl"
        telem = AgentTelemetry(log_path=log_path)
        events = telem.read_events()
        imported = 0

        for ev in events:
            try:
                conn.execute(
                    """
                    INSERT INTO telemetry
                        (timestamp, agent_name, event_type, tool_name,
                         file_path, duration_ms, details, task_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev.timestamp,
                        ev.agent_name,
                        ev.event_type,
                        ev.tool_name,
                        ev.file_path,
                        ev.duration_ms,
                        ev.details,
                        "",  # TelemetryEvent has no task_id field; leave blank
                    ),
                )
                imported += 1
            except Exception as exc:
                _log.warning("Skipping telemetry event: %s", exc)

        conn.commit()
        return imported

    def _migrate_retrospectives(self, conn: sqlite3.Connection) -> int:
        """Import retrospectives/*.json into retrospectives and child tables."""
        from agent_baton.models.retrospective import Retrospective

        retro_dir = self._root / "retrospectives"
        if not retro_dir.is_dir():
            return 0

        json_files = sorted(retro_dir.glob("*.json"))
        imported = 0

        for path in json_files:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                retro = Retrospective.from_dict(raw)
            except (json.JSONDecodeError, KeyError, TypeError, OSError) as exc:
                _log.warning("Skipping retrospective %s: %s", path.name, exc)
                continue

            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO retrospectives
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

                # Outcomes: what_worked + what_didnt share the table
                for outcome in retro.what_worked:
                    conn.execute(
                        """
                        INSERT INTO retrospective_outcomes
                            (task_id, category, agent_name, worked_well,
                             issues, root_cause)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            retro.task_id,
                            "worked",
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
                            (task_id, category, agent_name, worked_well,
                             issues, root_cause)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            retro.task_id,
                            "didnt_work",
                            outcome.name,
                            outcome.worked_well,
                            outcome.issues,
                            outcome.root_cause,
                        ),
                    )

                for gap in retro.knowledge_gaps:
                    conn.execute(
                        """
                        INSERT INTO knowledge_gaps
                            (task_id, description, affected_agent,
                             suggested_fix)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            retro.task_id,
                            gap.description,
                            gap.affected_agent,
                            gap.suggested_fix,
                        ),
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
                            1 if note.keep else 0,
                        ),
                    )

                imported += 1
            except Exception as exc:
                _log.warning(
                    "Skipping retrospective %s during insert: %s",
                    retro.task_id, exc,
                )

        conn.commit()
        return imported

    def _migrate_traces(self, conn: sqlite3.Connection) -> int:
        """Import traces/*.json into traces and trace_events tables."""
        from agent_baton.core.observe.trace import TraceRecorder

        recorder = TraceRecorder(team_context_root=self._root)
        traces_dir = recorder.traces_dir

        if not traces_dir.is_dir():
            return 0

        json_files = list(traces_dir.glob("*.json"))
        imported = 0

        for path in json_files:
            task_id = path.stem
            trace = recorder.load_trace(task_id)
            if trace is None:
                continue

            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO traces
                        (task_id, plan_snapshot, started_at, completed_at,
                         outcome)
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

                imported += 1
            except Exception as exc:
                _log.warning(
                    "Skipping trace %s: %s", task_id, exc
                )

        conn.commit()
        return imported

    def _migrate_patterns(self, conn: sqlite3.Connection) -> int:
        """Import learned-patterns.json into learned_patterns table."""
        from agent_baton.core.learn.pattern_learner import PatternLearner

        learner = PatternLearner(team_context_root=self._root)
        patterns = learner.load_patterns()
        imported = 0

        for p in patterns:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO learned_patterns
                        (pattern_id, task_type, stack, recommended_template,
                         recommended_agents, confidence, sample_size,
                         success_rate, avg_token_cost, evidence,
                         created_at, updated_at)
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
                imported += 1
            except Exception as exc:
                _log.warning(
                    "Skipping pattern %s: %s", p.pattern_id, exc
                )

        conn.commit()
        return imported

    def _migrate_budget(self, conn: sqlite3.Connection) -> int:
        """Import budget-recommendations.json into budget_recommendations table."""
        from agent_baton.core.learn.budget_tuner import BudgetTuner

        tuner = BudgetTuner(team_context_root=self._root)
        recs = tuner.load_recommendations()
        if not recs:
            return 0

        imported = 0
        for rec in recs:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO budget_recommendations
                        (task_type, current_tier, recommended_tier, reason,
                         avg_tokens_used, median_tokens_used, p95_tokens_used,
                         sample_size, confidence, potential_savings)
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
                imported += 1
            except Exception as exc:
                _log.warning(
                    "Skipping budget rec %s: %s", rec.task_type, exc
                )

        conn.commit()
        return imported

    def _migrate_active_task(self, conn: sqlite3.Connection) -> int:
        """Import active-task-id.txt into the active_task singleton table."""
        active_path = self._root / "active-task-id.txt"
        if not active_path.exists():
            return 0

        task_id = active_path.read_text(encoding="utf-8").strip()
        if not task_id:
            return 0

        try:
            conn.execute(
                "INSERT OR REPLACE INTO active_task (id, task_id) VALUES (1, ?)",
                (task_id,),
            )
            conn.commit()
            return 1
        except Exception as exc:
            _log.warning("Skipping active task: %s", exc)
            return 0

    # ------------------------------------------------------------------
    # Execution insertion (decomposes ExecutionState into all child tables)
    # ------------------------------------------------------------------

    def _insert_execution(
        self, conn: sqlite3.Connection, state: "ExecutionState"
    ) -> int:
        """Insert a single ExecutionState and all its nested data.

        Returns 1 if a new execution row was inserted, 0 if it already existed.
        Child rows (plan, phases, steps, results) are always upserted so that
        a re-run after a partial failure fills in any missing child data.
        """
        plan = state.plan

        # executions row — use rowcount to detect whether this is new
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO executions
                (task_id, status, current_phase, current_step_index,
                 started_at, completed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                state.task_id,
                state.status,
                state.current_phase,
                state.current_step_index,
                state.started_at,
                state.completed_at or None,
                state.started_at,
                state.started_at,
            ),
        )
        new_execution = cur.rowcount > 0

        # plans row
        conn.execute(
            """
            INSERT OR IGNORE INTO plans
                (task_id, task_summary, risk_level, budget_tier,
                 execution_mode, git_strategy, shared_context,
                 pattern_source, plan_markdown, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )

        # plan_phases + plan_steps + team_members
        for phase in plan.phases:
            conn.execute(
                """
                INSERT OR IGNORE INTO plan_phases
                    (task_id, phase_id, name, approval_required,
                     approval_description, gate_type, gate_command,
                     gate_description, gate_fail_on)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    plan.task_id,
                    phase.phase_id,
                    phase.name,
                    1 if phase.approval_required else 0,
                    phase.approval_description,
                    phase.gate.gate_type if phase.gate else None,
                    phase.gate.command if phase.gate else None,
                    phase.gate.description if phase.gate else None,
                    json.dumps(phase.gate.fail_on) if phase.gate else None,
                ),
            )

            for step in phase.steps:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO plan_steps
                        (task_id, step_id, phase_id, agent_name,
                         task_description, model, depends_on,
                         deliverables, allowed_paths, blocked_paths,
                         context_files)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )

                for member in step.team:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO team_members
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

        # step_results + team_step_results
        for result in state.step_results:
            conn.execute(
                """
                INSERT OR IGNORE INTO step_results
                    (task_id, step_id, agent_name, status, outcome,
                     files_changed, commit_hash, estimated_tokens,
                     duration_seconds, retries, error, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.task_id,
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

            for member_result in result.member_results:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO team_step_results
                        (task_id, step_id, member_id, agent_name,
                         status, outcome, files_changed)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        state.task_id,
                        result.step_id,
                        member_result.member_id,
                        member_result.agent_name,
                        member_result.status,
                        member_result.outcome,
                        json.dumps(member_result.files_changed),
                    ),
                )

        # gate_results
        for gate in state.gate_results:
            conn.execute(
                """
                INSERT INTO gate_results
                    (task_id, phase_id, gate_type, passed, output, checked_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    state.task_id,
                    gate.phase_id,
                    gate.gate_type,
                    1 if gate.passed else 0,
                    gate.output,
                    gate.checked_at,
                ),
            )

        # approval_results
        for approval in state.approval_results:
            conn.execute(
                """
                INSERT INTO approval_results
                    (task_id, phase_id, result, feedback, decided_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    state.task_id,
                    approval.phase_id,
                    approval.result,
                    approval.feedback,
                    approval.decided_at,
                ),
            )

        # amendments
        for amendment in state.amendments:
            conn.execute(
                """
                INSERT OR IGNORE INTO amendments
                    (task_id, amendment_id, trigger, trigger_phase_id,
                     description, phases_added, steps_added,
                     feedback, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    state.task_id,
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

        return 1 if new_execution else 0

    # ------------------------------------------------------------------
    # DB row count helpers (for verify())
    # ------------------------------------------------------------------

    def _db_row_counts(self, conn: sqlite3.Connection) -> dict[str, int]:
        """Query DB for current row counts per logical category."""

        def _count(table: str, dedupe_col: str | None = None) -> int:
            if dedupe_col:
                row = conn.execute(
                    f"SELECT COUNT(DISTINCT {dedupe_col}) FROM {table}"
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()
            return row[0] if row else 0

        # Check if tables exist (DB might be empty/new)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        counts: dict[str, int] = {}

        counts["executions"] = _count("executions") if "executions" in tables else 0
        counts["events"] = _count("events") if "events" in tables else 0
        counts["usage"] = _count("usage_records") if "usage_records" in tables else 0
        counts["telemetry"] = _count("telemetry") if "telemetry" in tables else 0
        counts["retrospectives"] = (
            _count("retrospectives") if "retrospectives" in tables else 0
        )
        counts["traces"] = _count("traces") if "traces" in tables else 0
        counts["patterns"] = (
            _count("learned_patterns") if "learned_patterns" in tables else 0
        )
        counts["budget"] = (
            _count("budget_recommendations") if "budget_recommendations" in tables else 0
        )
        counts["active_task"] = (
            _count("active_task") if "active_task" in tables else 0
        )

        return counts

    # ------------------------------------------------------------------
    # Source file discovery helpers
    # ------------------------------------------------------------------

    def _discover_execution_state_paths(self) -> list[Path]:
        """Return all execution-state.json paths (namespaced + legacy)."""
        from agent_baton.core.engine.persistence import StatePersistence

        paths: list[Path] = []

        # Namespaced
        exec_dir = self._root / "executions"
        if exec_dir.is_dir():
            for child in sorted(exec_dir.iterdir()):
                if child.is_dir() and (child / "execution-state.json").exists():
                    paths.append(child / "execution-state.json")

        # Legacy flat
        legacy = self._root / "execution-state.json"
        if legacy.exists():
            paths.append(legacy)

        return paths

    @staticmethod
    def _count_jsonl_lines(directory: Path) -> int:
        """Count non-blank JSONL lines across all *.jsonl files in a dir."""
        if not directory.is_dir():
            return 0
        total = 0
        for path in directory.glob("*.jsonl"):
            total += StorageMigrator._count_jsonl_lines_file(path)
        return total

    @staticmethod
    def _count_jsonl_lines_file(path: Path) -> int:
        """Count non-blank lines in a single JSONL file."""
        if not path.exists():
            return 0
        count = 0
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        count += 1
        except OSError:
            pass
        return count

    # ------------------------------------------------------------------
    # Archive helpers
    # ------------------------------------------------------------------

    def _archive_source_files(self) -> None:
        """Move original JSON/JSONL files to pre-sqlite-backup/."""
        import shutil

        backup_root = self._root / _BACKUP_DIR
        backup_root.mkdir(parents=True, exist_ok=True)

        _candidates = [
            self._root / "execution-state.json",
            self._root / "usage-log.jsonl",
            self._root / "telemetry.jsonl",
            self._root / "learned-patterns.json",
            self._root / "budget-recommendations.json",
            self._root / "active-task-id.txt",
        ]
        _dirs = [
            self._root / "executions",
            self._root / "events",
            self._root / "retrospectives",
            self._root / "traces",
        ]

        for path in _candidates:
            if path.exists():
                dest = backup_root / path.name
                shutil.move(str(path), str(dest))
                _log.info("Archived %s -> %s", path, dest)

        for dirpath in _dirs:
            if dirpath.is_dir():
                dest = backup_root / dirpath.name
                shutil.move(str(dirpath), str(dest))
                _log.info("Archived %s -> %s", dirpath, dest)
