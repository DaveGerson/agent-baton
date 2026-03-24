# SQLite Central Storage — Implementation Plan

Now I have a thorough understanding of every data model, every persistence class, and every file format. Let me design the full system.

---

# SQLite Central Storage Design for Agent-Baton

## 1. Recommended Approach

### 1.1 Architecture Overview

Two databases, both using WAL mode:

- **Project database**: `<project>/.claude/team-context/baton.db` -- replaces all per-project JSON/JSONL files
- **Global PMO database**: `~/.baton/pmo.db` -- replaces `pmo-config.json` and `pmo-archive.jsonl`

A new `agent_baton.core.storage` sub-package provides the storage layer, with a `StorageBackend` protocol that both `SqliteStorage` and `FileStorage` (legacy) implement. All existing persistence classes (`StatePersistence`, `UsageLogger`, `AgentTelemetry`, `EventPersistence`, `TraceRecorder`, `RetrospectiveEngine`, `PatternLearner`, `BudgetTuner`, `ContextManager`) are refactored to delegate to the active backend rather than doing their own file I/O.

### 1.2 Package Layout

```
agent_baton/core/storage/
    __init__.py          -- exports SqliteStorage, FileStorage, detect_backend()
    protocol.py          -- StorageBackend protocol (abstract interface)
    sqlite_backend.py    -- SqliteStorage (the new path)
    file_backend.py      -- FileStorage (wraps existing file I/O, for backward compat)
    schema.py            -- SQL DDL strings, schema version constant
    migrate.py           -- JSON-to-SQLite migration tool
    connection.py        -- ConnectionManager (WAL, pooling, schema versioning)
    pmo_sqlite.py        -- PmoSqliteStore (global PMO database)
```

### 1.3 Connection Management

```python
# agent_baton/core/storage/connection.py

import sqlite3
from pathlib import Path
from threading import local

SCHEMA_VERSION = 1

class ConnectionManager:
    """Thread-safe SQLite connection manager with WAL mode.

    One connection per thread. All connections share the same database
    file. Connections are opened lazily and cached in thread-local storage.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._local = local()

    @property
    def db_path(self) -> Path:
        return self._db_path

    def get_connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._db_path),
                timeout=10.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
            self._ensure_schema(conn)
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Create tables if they don't exist; run migrations if version changed."""
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_version'"
        )
        if cur.fetchone() is None:
            # Fresh database -- create all tables
            conn.executescript(PROJECT_SCHEMA_DDL)
            conn.execute(
                "INSERT INTO _schema_version (version) VALUES (?)",
                (SCHEMA_VERSION,),
            )
            conn.commit()
        else:
            row = conn.execute("SELECT version FROM _schema_version").fetchone()
            current = row["version"] if row else 0
            if current < SCHEMA_VERSION:
                self._run_migrations(conn, current, SCHEMA_VERSION)
                conn.execute(
                    "UPDATE _schema_version SET version = ?",
                    (SCHEMA_VERSION,),
                )
                conn.commit()

    def _run_migrations(
        self, conn: sqlite3.Connection, from_v: int, to_v: int
    ) -> None:
        """Apply sequential migration scripts."""
        for v in range(from_v + 1, to_v + 1):
            ddl = MIGRATIONS.get(v)
            if ddl:
                conn.executescript(ddl)

# MIGRATIONS dict populated in schema.py, keyed by target version number.
MIGRATIONS: dict[int, str] = {}
```

### 1.4 Per-Project Database Schema (`baton.db`)

```sql
-- schema.py: PROJECT_SCHEMA_DDL

-- Schema version tracking
CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER NOT NULL
);

-- =====================================================
-- EXECUTIONS (replaces execution-state.json)
-- Maps to: ExecutionState
-- =====================================================
CREATE TABLE IF NOT EXISTS executions (
    task_id         TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'running',
        -- running, gate_pending, approval_pending, complete, failed
    current_phase   INTEGER NOT NULL DEFAULT 0,
    current_step_index INTEGER NOT NULL DEFAULT 0,
    started_at      TEXT NOT NULL,   -- ISO 8601
    completed_at    TEXT,
    -- The full plan is stored in the plans table; linked by task_id.
    -- We do NOT store a JSON blob of the entire ExecutionState here;
    -- the state is reconstructed from normalized tables.
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);
CREATE INDEX IF NOT EXISTS idx_executions_started ON executions(started_at);

-- =====================================================
-- PLANS (replaces plan.json)
-- Maps to: MachinePlan
-- =====================================================
CREATE TABLE IF NOT EXISTS plans (
    task_id           TEXT PRIMARY KEY,
    task_summary      TEXT NOT NULL,
    risk_level        TEXT NOT NULL DEFAULT 'LOW',
    budget_tier       TEXT NOT NULL DEFAULT 'standard',
    execution_mode    TEXT NOT NULL DEFAULT 'phased',
    git_strategy      TEXT NOT NULL DEFAULT 'commit-per-agent',
    shared_context    TEXT NOT NULL DEFAULT '',
    pattern_source    TEXT,
    plan_markdown     TEXT NOT NULL DEFAULT '',  -- rendered plan.md
    created_at        TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);

-- =====================================================
-- PLAN_PHASES (replaces phases[] within plan.json)
-- Maps to: PlanPhase
-- =====================================================
CREATE TABLE IF NOT EXISTS plan_phases (
    task_id              TEXT NOT NULL,
    phase_id             INTEGER NOT NULL,
    name                 TEXT NOT NULL,
    approval_required    INTEGER NOT NULL DEFAULT 0,  -- boolean
    approval_description TEXT NOT NULL DEFAULT '',
    -- Gate stored inline since it's 1:1 with phase
    gate_type            TEXT,     -- NULL = no gate
    gate_command         TEXT,
    gate_description     TEXT,
    gate_fail_on         TEXT,     -- JSON array string, e.g. '["error","warning"]'
    PRIMARY KEY (task_id, phase_id),
    FOREIGN KEY (task_id) REFERENCES plans(task_id) ON DELETE CASCADE
);

-- =====================================================
-- PLAN_STEPS (replaces steps[] within each phase)
-- Maps to: PlanStep
-- =====================================================
CREATE TABLE IF NOT EXISTS plan_steps (
    task_id           TEXT NOT NULL,
    step_id           TEXT NOT NULL,   -- e.g. "1.1"
    phase_id          INTEGER NOT NULL,
    agent_name        TEXT NOT NULL,
    task_description  TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT 'sonnet',
    depends_on        TEXT NOT NULL DEFAULT '[]',    -- JSON array of step_id strings
    deliverables      TEXT NOT NULL DEFAULT '[]',    -- JSON array
    allowed_paths     TEXT NOT NULL DEFAULT '[]',    -- JSON array
    blocked_paths     TEXT NOT NULL DEFAULT '[]',    -- JSON array
    context_files     TEXT NOT NULL DEFAULT '[]',    -- JSON array
    PRIMARY KEY (task_id, step_id),
    FOREIGN KEY (task_id, phase_id) REFERENCES plan_phases(task_id, phase_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_plan_steps_agent ON plan_steps(agent_name);
CREATE INDEX IF NOT EXISTS idx_plan_steps_phase ON plan_steps(task_id, phase_id);

-- =====================================================
-- TEAM_MEMBERS (replaces team[] within PlanStep)
-- Maps to: TeamMember
-- =====================================================
CREATE TABLE IF NOT EXISTS team_members (
    task_id        TEXT NOT NULL,
    step_id        TEXT NOT NULL,
    member_id      TEXT NOT NULL,   -- e.g. "1.1.a"
    agent_name     TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'implementer',
    task_description TEXT NOT NULL DEFAULT '',
    model          TEXT NOT NULL DEFAULT 'sonnet',
    depends_on     TEXT NOT NULL DEFAULT '[]',   -- JSON array
    deliverables   TEXT NOT NULL DEFAULT '[]',   -- JSON array
    PRIMARY KEY (task_id, step_id, member_id),
    FOREIGN KEY (task_id, step_id) REFERENCES plan_steps(task_id, step_id) ON DELETE CASCADE
);

-- =====================================================
-- STEP_RESULTS (replaces step_results[] in execution-state.json)
-- Maps to: StepResult
-- =====================================================
CREATE TABLE IF NOT EXISTS step_results (
    task_id           TEXT NOT NULL,
    step_id           TEXT NOT NULL,
    agent_name        TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'complete',
        -- complete, failed, dispatched
    outcome           TEXT NOT NULL DEFAULT '',
    files_changed     TEXT NOT NULL DEFAULT '[]',  -- JSON array
    commit_hash       TEXT NOT NULL DEFAULT '',
    estimated_tokens  INTEGER NOT NULL DEFAULT 0,
    duration_seconds  REAL NOT NULL DEFAULT 0.0,
    retries           INTEGER NOT NULL DEFAULT 0,
    error             TEXT NOT NULL DEFAULT '',
    completed_at      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (task_id, step_id),
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_step_results_status ON step_results(status);
CREATE INDEX IF NOT EXISTS idx_step_results_agent ON step_results(agent_name);

-- =====================================================
-- TEAM_STEP_RESULTS (replaces member_results[] within StepResult)
-- Maps to: TeamStepResult
-- =====================================================
CREATE TABLE IF NOT EXISTS team_step_results (
    task_id        TEXT NOT NULL,
    step_id        TEXT NOT NULL,
    member_id      TEXT NOT NULL,
    agent_name     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'complete',
    outcome        TEXT NOT NULL DEFAULT '',
    files_changed  TEXT NOT NULL DEFAULT '[]',  -- JSON array
    PRIMARY KEY (task_id, step_id, member_id),
    FOREIGN KEY (task_id, step_id) REFERENCES step_results(task_id, step_id) ON DELETE CASCADE
);

-- =====================================================
-- GATE_RESULTS (replaces gate_results[] in execution-state.json)
-- Maps to: GateResult
-- =====================================================
CREATE TABLE IF NOT EXISTS gate_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    phase_id    INTEGER NOT NULL,
    gate_type   TEXT NOT NULL,
    passed      INTEGER NOT NULL,  -- boolean
    output      TEXT NOT NULL DEFAULT '',
    checked_at  TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_gate_results_task ON gate_results(task_id);
CREATE INDEX IF NOT EXISTS idx_gate_results_passed ON gate_results(passed);

-- =====================================================
-- APPROVAL_RESULTS (replaces approval_results[] in execution-state.json)
-- Maps to: ApprovalResult
-- =====================================================
CREATE TABLE IF NOT EXISTS approval_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    phase_id    INTEGER NOT NULL,
    result      TEXT NOT NULL,   -- approve, reject, approve-with-feedback
    feedback    TEXT NOT NULL DEFAULT '',
    decided_at  TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_approval_results_task ON approval_results(task_id);

-- =====================================================
-- AMENDMENTS (replaces amendments[] in execution-state.json)
-- Maps to: PlanAmendment
-- =====================================================
CREATE TABLE IF NOT EXISTS amendments (
    task_id           TEXT NOT NULL,
    amendment_id      TEXT NOT NULL,
    trigger           TEXT NOT NULL,   -- gate_feedback, approval_feedback, manual
    trigger_phase_id  INTEGER NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    phases_added      TEXT NOT NULL DEFAULT '[]',  -- JSON array of int
    steps_added       TEXT NOT NULL DEFAULT '[]',  -- JSON array of str
    feedback          TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (task_id, amendment_id),
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);

-- =====================================================
-- EVENTS (replaces events/<task-id>.jsonl)
-- Maps to: Event
-- =====================================================
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    topic       TEXT NOT NULL,
    sequence    INTEGER NOT NULL DEFAULT 0,
    payload     TEXT NOT NULL DEFAULT '{}',  -- JSON object
    PRIMARY KEY (event_id)
);

CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_topic ON events(topic);
CREATE INDEX IF NOT EXISTS idx_events_task_seq ON events(task_id, sequence);

-- =====================================================
-- USAGE_RECORDS (replaces usage-log.jsonl, one row per task)
-- Maps to: TaskUsageRecord
-- =====================================================
CREATE TABLE IF NOT EXISTS usage_records (
    task_id           TEXT PRIMARY KEY,
    timestamp         TEXT NOT NULL,   -- ISO 8601
    total_agents      INTEGER NOT NULL DEFAULT 0,
    risk_level        TEXT NOT NULL DEFAULT 'LOW',
    sequencing_mode   TEXT NOT NULL DEFAULT 'phased_delivery',
    gates_passed      INTEGER NOT NULL DEFAULT 0,
    gates_failed      INTEGER NOT NULL DEFAULT 0,
    outcome           TEXT NOT NULL DEFAULT '',
    notes             TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_usage_outcome ON usage_records(outcome);
CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_records(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_seq_mode ON usage_records(sequencing_mode);

-- =====================================================
-- AGENT_USAGE (replaces agents_used[] within TaskUsageRecord)
-- Maps to: AgentUsageRecord
-- =====================================================
CREATE TABLE IF NOT EXISTS agent_usage (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id            TEXT NOT NULL,
    agent_name         TEXT NOT NULL,
    model              TEXT NOT NULL DEFAULT 'sonnet',
    steps              INTEGER NOT NULL DEFAULT 1,
    retries            INTEGER NOT NULL DEFAULT 0,
    gate_results       TEXT NOT NULL DEFAULT '[]',  -- JSON array of strings
    estimated_tokens   INTEGER NOT NULL DEFAULT 0,
    duration_seconds   REAL NOT NULL DEFAULT 0.0,
    FOREIGN KEY (task_id) REFERENCES usage_records(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_agent_usage_task ON agent_usage(task_id);
CREATE INDEX IF NOT EXISTS idx_agent_usage_agent ON agent_usage(agent_name);

-- =====================================================
-- TELEMETRY (replaces telemetry.jsonl)
-- Maps to: TelemetryEvent
-- =====================================================
CREATE TABLE IF NOT EXISTS telemetry (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT NOT NULL,
    agent_name   TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    tool_name    TEXT NOT NULL DEFAULT '',
    file_path    TEXT NOT NULL DEFAULT '',
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    details      TEXT NOT NULL DEFAULT '',
    task_id      TEXT NOT NULL DEFAULT ''  -- optional: link to execution
);

CREATE INDEX IF NOT EXISTS idx_telemetry_agent ON telemetry(agent_name);
CREATE INDEX IF NOT EXISTS idx_telemetry_type ON telemetry(event_type);
CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry(timestamp);
CREATE INDEX IF NOT EXISTS idx_telemetry_task ON telemetry(task_id);

-- =====================================================
-- RETROSPECTIVES (replaces retrospectives/<task-id>.json)
-- Maps to: Retrospective
-- =====================================================
CREATE TABLE IF NOT EXISTS retrospectives (
    task_id            TEXT PRIMARY KEY,
    task_name          TEXT NOT NULL,
    timestamp          TEXT NOT NULL,
    agent_count        INTEGER NOT NULL DEFAULT 0,
    retry_count        INTEGER NOT NULL DEFAULT 0,
    gates_passed       INTEGER NOT NULL DEFAULT 0,
    gates_failed       INTEGER NOT NULL DEFAULT 0,
    risk_level         TEXT NOT NULL DEFAULT 'LOW',
    duration_estimate  TEXT NOT NULL DEFAULT '',
    estimated_tokens   INTEGER NOT NULL DEFAULT 0,
    markdown           TEXT NOT NULL DEFAULT ''  -- rendered .md for backward compat
);

CREATE INDEX IF NOT EXISTS idx_retro_timestamp ON retrospectives(timestamp);

-- =====================================================
-- RETROSPECTIVE_OUTCOMES (replaces what_worked/what_didnt arrays)
-- Maps to: AgentOutcome
-- =====================================================
CREATE TABLE IF NOT EXISTS retrospective_outcomes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    category     TEXT NOT NULL,   -- 'worked' or 'didnt'
    agent_name   TEXT NOT NULL,
    worked_well  TEXT NOT NULL DEFAULT '',
    issues       TEXT NOT NULL DEFAULT '',
    root_cause   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES retrospectives(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_retro_outcomes_task ON retrospective_outcomes(task_id);
CREATE INDEX IF NOT EXISTS idx_retro_outcomes_agent ON retrospective_outcomes(agent_name);

-- =====================================================
-- KNOWLEDGE_GAPS (replaces knowledge_gaps[] in retrospective JSON)
-- Maps to: KnowledgeGap
-- =====================================================
CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL,
    description     TEXT NOT NULL,
    affected_agent  TEXT NOT NULL DEFAULT '',
    suggested_fix   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES retrospectives(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_knowledge_gaps_agent ON knowledge_gaps(affected_agent);

-- =====================================================
-- ROSTER_RECOMMENDATIONS (replaces roster_recommendations[] in retro)
-- Maps to: RosterRecommendation
-- =====================================================
CREATE TABLE IF NOT EXISTS roster_recommendations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  TEXT NOT NULL,
    action   TEXT NOT NULL,   -- create, improve, remove
    target   TEXT NOT NULL,
    reason   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES retrospectives(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_roster_rec_action ON roster_recommendations(action);

-- =====================================================
-- SEQUENCING_NOTES (replaces sequencing_notes[] in retro)
-- Maps to: SequencingNote
-- =====================================================
CREATE TABLE IF NOT EXISTS sequencing_notes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    phase        TEXT NOT NULL,
    observation  TEXT NOT NULL,
    keep         INTEGER NOT NULL DEFAULT 1,  -- boolean
    FOREIGN KEY (task_id) REFERENCES retrospectives(task_id) ON DELETE CASCADE
);

-- =====================================================
-- TRACES (replaces traces/<task-id>.json)
-- Maps to: TaskTrace
-- =====================================================
CREATE TABLE IF NOT EXISTS traces (
    task_id        TEXT PRIMARY KEY,
    plan_snapshot  TEXT NOT NULL DEFAULT '{}',  -- JSON blob
    started_at     TEXT NOT NULL DEFAULT '',
    completed_at   TEXT,
    outcome        TEXT
);

-- =====================================================
-- TRACE_EVENTS (replaces events[] within TaskTrace)
-- Maps to: TraceEvent
-- =====================================================
CREATE TABLE IF NOT EXISTS trace_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id          TEXT NOT NULL,
    timestamp        TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    agent_name       TEXT,
    phase            INTEGER NOT NULL DEFAULT 0,
    step             INTEGER NOT NULL DEFAULT 0,
    details          TEXT NOT NULL DEFAULT '{}',  -- JSON object
    duration_seconds REAL,
    FOREIGN KEY (task_id) REFERENCES traces(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_trace_events_task ON trace_events(task_id);
CREATE INDEX IF NOT EXISTS idx_trace_events_type ON trace_events(event_type);

-- =====================================================
-- LEARNED_PATTERNS (replaces learned-patterns.json)
-- Maps to: LearnedPattern
-- =====================================================
CREATE TABLE IF NOT EXISTS learned_patterns (
    pattern_id           TEXT PRIMARY KEY,
    task_type            TEXT NOT NULL,
    stack                TEXT,           -- NULL = any stack
    recommended_template TEXT NOT NULL DEFAULT '',
    recommended_agents   TEXT NOT NULL DEFAULT '[]',  -- JSON array
    confidence           REAL NOT NULL DEFAULT 0.0,
    sample_size          INTEGER NOT NULL DEFAULT 0,
    success_rate         REAL NOT NULL DEFAULT 0.0,
    avg_token_cost       INTEGER NOT NULL DEFAULT 0,
    evidence             TEXT NOT NULL DEFAULT '[]',  -- JSON array
    created_at           TEXT NOT NULL DEFAULT '',
    updated_at           TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_patterns_type ON learned_patterns(task_type);
CREATE INDEX IF NOT EXISTS idx_patterns_confidence ON learned_patterns(confidence);

-- =====================================================
-- BUDGET_RECOMMENDATIONS (replaces budget-recommendations.json)
-- Maps to: BudgetRecommendation
-- =====================================================
CREATE TABLE IF NOT EXISTS budget_recommendations (
    task_type          TEXT PRIMARY KEY,
    current_tier       TEXT NOT NULL,
    recommended_tier   TEXT NOT NULL,
    reason             TEXT NOT NULL DEFAULT '',
    avg_tokens_used    INTEGER NOT NULL DEFAULT 0,
    median_tokens_used INTEGER NOT NULL DEFAULT 0,
    p95_tokens_used    INTEGER NOT NULL DEFAULT 0,
    sample_size        INTEGER NOT NULL DEFAULT 0,
    confidence         REAL NOT NULL DEFAULT 0.0,
    potential_savings  INTEGER NOT NULL DEFAULT 0
);

-- =====================================================
-- MISSION_LOG_ENTRIES (replaces mission-log.md)
-- Maps to: MissionLogEntry
-- =====================================================
CREATE TABLE IF NOT EXISTS mission_log_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    agent_name    TEXT NOT NULL,
    status        TEXT NOT NULL,
    assignment    TEXT NOT NULL DEFAULT '',
    result        TEXT NOT NULL DEFAULT '',
    files         TEXT NOT NULL DEFAULT '[]',      -- JSON array
    decisions     TEXT NOT NULL DEFAULT '[]',      -- JSON array
    issues        TEXT NOT NULL DEFAULT '[]',      -- JSON array
    handoff       TEXT NOT NULL DEFAULT '',
    commit_hash   TEXT NOT NULL DEFAULT '',
    failure_class TEXT,
    timestamp     TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_mission_log_task ON mission_log_entries(task_id);

-- =====================================================
-- SHARED_CONTEXT (replaces context.md per task)
-- Text blob with structured metadata
-- =====================================================
CREATE TABLE IF NOT EXISTS shared_context (
    task_id        TEXT PRIMARY KEY,
    content        TEXT NOT NULL DEFAULT '',  -- full markdown content
    task_title     TEXT NOT NULL DEFAULT '',
    stack          TEXT NOT NULL DEFAULT '',
    architecture   TEXT NOT NULL DEFAULT '',
    conventions    TEXT NOT NULL DEFAULT '',
    guardrails     TEXT NOT NULL DEFAULT '',
    agent_assignments TEXT NOT NULL DEFAULT '',
    domain_context TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);

-- =====================================================
-- CODEBASE_PROFILE (replaces codebase-profile.md)
-- Single-row table (project-level, not task-scoped)
-- =====================================================
CREATE TABLE IF NOT EXISTS codebase_profile (
    id         INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
    content    TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- =====================================================
-- IMPROVEMENT_TRIGGER_STATE (replaces improvement-trigger-state.json)
-- =====================================================
CREATE TABLE IF NOT EXISTS improvement_trigger_state (
    id                    INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
    last_analyzed_count   INTEGER NOT NULL DEFAULT 0,
    last_analyzed_at      TEXT
);

-- =====================================================
-- ACTIVE_TASK (replaces active-task-id.txt)
-- =====================================================
CREATE TABLE IF NOT EXISTS active_task (
    id       INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
    task_id  TEXT NOT NULL
);
```

### 1.5 Global PMO Database Schema (`pmo.db`)

```sql
-- pmo_schema.py: PMO_SCHEMA_DDL

CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER NOT NULL
);

-- =====================================================
-- PROJECTS (replaces projects[] in pmo-config.json)
-- Maps to: PmoProject
-- =====================================================
CREATE TABLE IF NOT EXISTS projects (
    project_id     TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    path           TEXT NOT NULL,   -- absolute filesystem path
    program        TEXT NOT NULL,
    color          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    registered_at  TEXT NOT NULL DEFAULT '',
    ado_project    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_projects_program ON projects(program);

-- =====================================================
-- PROGRAMS (replaces programs[] in pmo-config.json)
-- =====================================================
CREATE TABLE IF NOT EXISTS programs (
    name TEXT PRIMARY KEY
);

-- =====================================================
-- SIGNALS (replaces signals[] in pmo-config.json)
-- Maps to: PmoSignal
-- =====================================================
CREATE TABLE IF NOT EXISTS signals (
    signal_id         TEXT PRIMARY KEY,
    signal_type       TEXT NOT NULL,     -- bug, escalation, blocker
    title             TEXT NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    source_project_id TEXT NOT NULL DEFAULT '',
    severity          TEXT NOT NULL DEFAULT 'medium',
    status            TEXT NOT NULL DEFAULT 'open',
    created_at        TEXT NOT NULL DEFAULT '',
    resolved_at       TEXT NOT NULL DEFAULT '',
    forge_task_id     TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_severity ON signals(severity);
CREATE INDEX IF NOT EXISTS idx_signals_type ON signals(signal_type);

-- =====================================================
-- ARCHIVED_CARDS (replaces pmo-archive.jsonl)
-- Maps to: PmoCard
-- =====================================================
CREATE TABLE IF NOT EXISTS archived_cards (
    card_id          TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    program          TEXT NOT NULL,
    title            TEXT NOT NULL,
    column_name      TEXT NOT NULL,   -- 'column' is reserved in SQL
    risk_level       TEXT NOT NULL DEFAULT 'LOW',
    priority         INTEGER NOT NULL DEFAULT 0,
    agents           TEXT NOT NULL DEFAULT '[]',  -- JSON array
    steps_completed  INTEGER NOT NULL DEFAULT 0,
    steps_total      INTEGER NOT NULL DEFAULT 0,
    gates_passed     INTEGER NOT NULL DEFAULT 0,
    current_phase    TEXT NOT NULL DEFAULT '',
    error            TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT '',
    updated_at       TEXT NOT NULL DEFAULT '',
    external_id      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_archive_project ON archived_cards(project_id);
CREATE INDEX IF NOT EXISTS idx_archive_program ON archived_cards(program);
CREATE INDEX IF NOT EXISTS idx_archive_created ON archived_cards(created_at);

-- =====================================================
-- FORGE_SESSIONS (consultative planning)
-- =====================================================
CREATE TABLE IF NOT EXISTS forge_sessions (
    session_id    TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active',  -- active, complete, abandoned
    created_at    TEXT NOT NULL DEFAULT '',
    completed_at  TEXT,
    task_id       TEXT,   -- resulting task_id if a plan was generated
    notes         TEXT NOT NULL DEFAULT ''
);

-- =====================================================
-- PMO_METRICS (aggregate snapshots for trend reporting)
-- =====================================================
CREATE TABLE IF NOT EXISTS pmo_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    program         TEXT NOT NULL DEFAULT '',  -- '' = cross-program
    metric_name     TEXT NOT NULL,
    metric_value    REAL NOT NULL DEFAULT 0.0,
    details         TEXT NOT NULL DEFAULT '{}'  -- JSON
);

CREATE INDEX IF NOT EXISTS idx_pmo_metrics_ts ON pmo_metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_pmo_metrics_name ON pmo_metrics(metric_name);
```

### 1.6 StorageBackend Protocol

```python
# agent_baton/core/storage/protocol.py

from __future__ import annotations
from typing import Protocol, runtime_checkable
from pathlib import Path

from agent_baton.models.execution import (
    ExecutionState, StepResult, GateResult, ApprovalResult, PlanAmendment,
    MachinePlan,
)
from agent_baton.models.events import Event
from agent_baton.models.usage import TaskUsageRecord
from agent_baton.models.trace import TaskTrace, TraceEvent
from agent_baton.models.retrospective import Retrospective
from agent_baton.models.pattern import LearnedPattern
from agent_baton.models.budget import BudgetRecommendation
from agent_baton.models.plan import MissionLogEntry
from agent_baton.core.observe.telemetry import TelemetryEvent


@runtime_checkable
class StorageBackend(Protocol):
    """Abstract storage contract.

    Both SqliteStorage and FileStorage implement this.
    Existing classes (StatePersistence, UsageLogger, etc.) delegate to
    whichever backend is active.
    """

    # -- Execution State --
    def save_execution(self, state: ExecutionState) -> None: ...
    def load_execution(self, task_id: str) -> ExecutionState | None: ...
    def list_executions(self) -> list[str]: ...
    def delete_execution(self, task_id: str) -> None: ...

    # -- Active task --
    def set_active_task(self, task_id: str) -> None: ...
    def get_active_task(self) -> str | None: ...

    # -- Plans (read from execution or standalone) --
    def save_plan(self, plan: MachinePlan) -> None: ...
    def load_plan(self, task_id: str) -> MachinePlan | None: ...

    # -- Step/Gate/Approval results --
    def save_step_result(self, task_id: str, result: StepResult) -> None: ...
    def save_gate_result(self, task_id: str, result: GateResult) -> None: ...
    def save_approval_result(self, task_id: str, result: ApprovalResult) -> None: ...
    def save_amendment(self, task_id: str, amendment: PlanAmendment) -> None: ...

    # -- Events --
    def append_event(self, event: Event) -> None: ...
    def read_events(
        self, task_id: str, from_seq: int = 0, topic_pattern: str | None = None
    ) -> list[Event]: ...

    # -- Usage --
    def log_usage(self, record: TaskUsageRecord) -> None: ...
    def read_usage(self, limit: int | None = None) -> list[TaskUsageRecord]: ...

    # -- Telemetry --
    def log_telemetry(self, event: TelemetryEvent, task_id: str = "") -> None: ...
    def read_telemetry(
        self, agent_name: str | None = None, limit: int | None = None
    ) -> list[TelemetryEvent]: ...

    # -- Retrospectives --
    def save_retrospective(self, retro: Retrospective) -> None: ...
    def load_retrospective(self, task_id: str) -> Retrospective | None: ...
    def list_retrospectives(self, limit: int = 100) -> list[str]: ...

    # -- Traces --
    def save_trace(self, trace: TaskTrace) -> None: ...
    def load_trace(self, task_id: str) -> TaskTrace | None: ...

    # -- Patterns & Budget --
    def save_patterns(self, patterns: list[LearnedPattern]) -> None: ...
    def load_patterns(self) -> list[LearnedPattern]: ...
    def save_budget_recommendations(self, recs: list[BudgetRecommendation]) -> None: ...
    def load_budget_recommendations(self) -> list[BudgetRecommendation]: ...

    # -- Mission Log --
    def append_mission_log(self, task_id: str, entry: MissionLogEntry) -> None: ...
    def read_mission_log(self, task_id: str) -> list[MissionLogEntry]: ...

    # -- Shared Context --
    def save_context(self, task_id: str, content: str, **sections: str) -> None: ...
    def read_context(self, task_id: str) -> str | None: ...

    # -- Codebase Profile --
    def save_profile(self, content: str) -> None: ...
    def read_profile(self) -> str | None: ...
```

### 1.7 Backend Detection and Factory

```python
# agent_baton/core/storage/__init__.py

from pathlib import Path

_BATON_DB = "baton.db"
_TEAM_CONTEXT = ".claude/team-context"


def detect_backend(project_root: Path) -> str:
    """Detect whether a project uses 'sqlite' or 'file' storage.

    Logic:
    1. If baton.db exists -> 'sqlite'
    2. If execution-state.json or executions/ dir exists -> 'file'
    3. Default for new projects -> 'sqlite' (the new default)
    """
    tc = project_root / _TEAM_CONTEXT
    if (tc / _BATON_DB).exists():
        return "sqlite"
    if (tc / "execution-state.json").exists() or (tc / "executions").is_dir():
        return "file"
    return "sqlite"  # new projects get SQLite


def get_storage(
    project_root: Path,
    backend: str | None = None,
    task_id: str | None = None,
) -> StorageBackend:
    """Factory: return the appropriate storage backend.

    Args:
        project_root: Absolute path to the project root.
        backend: Force 'sqlite' or 'file'. If None, auto-detect.
        task_id: Optional task_id for task-scoped operations.
    """
    if backend is None:
        backend = detect_backend(project_root)

    if backend == "sqlite":
        from agent_baton.core.storage.sqlite_backend import SqliteStorage
        db_path = project_root / _TEAM_CONTEXT / _BATON_DB
        return SqliteStorage(db_path)
    else:
        from agent_baton.core.storage.file_backend import FileStorage
        tc = project_root / _TEAM_CONTEXT
        return FileStorage(tc, task_id=task_id)
```

### 1.8 Example Queries That Become Possible

These are the queries that were impossible or expensive with flat files and become trivial with SQLite.

**All failed agents in the last 30 days:**
```sql
SELECT sr.agent_name, sr.error, sr.step_id, e.task_id, e.started_at
FROM step_results sr
JOIN executions e ON sr.task_id = e.task_id
WHERE sr.status = 'failed'
  AND e.started_at >= datetime('now', '-30 days')
ORDER BY e.started_at DESC;
```

**Agent reliability leaderboard:**
```sql
SELECT agent_name,
       COUNT(*) AS total_steps,
       SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) AS successes,
       ROUND(100.0 * SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) / COUNT(*), 1) AS success_pct,
       SUM(retries) AS total_retries,
       SUM(estimated_tokens) AS total_tokens
FROM step_results
GROUP BY agent_name
ORDER BY success_pct DESC;
```

**Gate pass rate by gate type:**
```sql
SELECT gate_type,
       COUNT(*) AS total,
       SUM(passed) AS passed,
       ROUND(100.0 * SUM(passed) / COUNT(*), 1) AS pass_rate
FROM gate_results
GROUP BY gate_type;
```

**Average task duration by risk level:**
```sql
SELECT p.risk_level,
       COUNT(*) AS tasks,
       ROUND(AVG(
           (julianday(e.completed_at) - julianday(e.started_at)) * 86400
       ), 0) AS avg_duration_seconds
FROM executions e
JOIN plans p ON e.task_id = p.task_id
WHERE e.status = 'complete' AND e.completed_at IS NOT NULL
GROUP BY p.risk_level;
```

**Token cost per task type (for budget tuning):**
```sql
SELECT ur.sequencing_mode,
       COUNT(*) AS tasks,
       SUM(au.estimated_tokens) AS total_tokens,
       ROUND(AVG(au.estimated_tokens), 0) AS avg_tokens_per_agent
FROM usage_records ur
JOIN agent_usage au ON ur.task_id = au.task_id
GROUP BY ur.sequencing_mode;
```

**Knowledge gaps that keep appearing:**
```sql
SELECT description, affected_agent, COUNT(*) AS frequency
FROM knowledge_gaps
GROUP BY description, affected_agent
HAVING COUNT(*) > 1
ORDER BY frequency DESC;
```

**Roster recommendations consensus:**
```sql
SELECT action, target, COUNT(*) AS times_recommended,
       GROUP_CONCAT(DISTINCT task_id) AS from_tasks
FROM roster_recommendations
GROUP BY action, target
ORDER BY times_recommended DESC;
```

**Cross-project: which project has the most failures (PMO db):**
```sql
SELECT ac.project_id, p.name,
       COUNT(*) AS total_plans,
       SUM(CASE WHEN ac.error != '' THEN 1 ELSE 0 END) AS with_errors
FROM archived_cards ac
JOIN projects p ON ac.project_id = p.project_id
GROUP BY ac.project_id
ORDER BY with_errors DESC;
```

### 1.9 Migration Strategy (JSON to SQLite)

```python
# agent_baton/core/storage/migrate.py

"""Migrate existing JSON/JSONL files into baton.db.

Usage:
    baton migrate [--dry-run] [--keep-files]

Strategy:
1. Scan .claude/team-context/ for existing files
2. Open (or create) baton.db
3. Import in dependency order:
   - executions + plans (from execution-state.json in executions/*)
   - events (from events/*.jsonl)
   - usage (from usage-log.jsonl)
   - telemetry (from telemetry.jsonl)
   - retrospectives (from retrospectives/*.json)
   - traces (from traces/*.json)
   - patterns (from learned-patterns.json)
   - budget (from budget-recommendations.json)
   - context + mission log (from executions/*/context.md, mission-log.md)
4. Verify row counts match source record counts
5. Optionally archive original files to .claude/team-context/pre-sqlite-backup/
"""

import json
import shutil
from pathlib import Path
from datetime import datetime, timezone

from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.models.execution import ExecutionState


class Migrator:
    def __init__(self, context_root: Path, *, dry_run: bool = False) -> None:
        self._root = context_root
        self._dry_run = dry_run
        self._db_path = context_root / "baton.db"
        self._stats: dict[str, int] = {}

    def run(self, keep_files: bool = False) -> dict[str, int]:
        """Run the full migration. Returns category -> count migrated."""
        if self._dry_run:
            return self._scan_counts()

        mgr = ConnectionManager(self._db_path)
        conn = mgr.get_connection()

        try:
            self._migrate_executions(conn)
            self._migrate_events(conn)
            self._migrate_usage(conn)
            self._migrate_telemetry(conn)
            self._migrate_retrospectives(conn)
            self._migrate_traces(conn)
            self._migrate_patterns(conn)
            self._migrate_budget(conn)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            mgr.close()

        if not keep_files:
            self._archive_originals()

        return self._stats

    def _scan_counts(self) -> dict[str, int]:
        """Dry-run: count what would be migrated."""
        counts: dict[str, int] = {}
        exec_dir = self._root / "executions"
        if exec_dir.is_dir():
            counts["executions"] = sum(
                1 for d in exec_dir.iterdir()
                if d.is_dir() and (d / "execution-state.json").exists()
            )
        events_dir = self._root / "events"
        if events_dir.is_dir():
            counts["event_files"] = sum(1 for _ in events_dir.glob("*.jsonl"))
        usage = self._root / "usage-log.jsonl"
        if usage.exists():
            counts["usage_records"] = sum(
                1 for line in usage.read_text().splitlines() if line.strip()
            )
        # ... similar for other categories
        return counts

    def _archive_originals(self) -> None:
        """Move original files to a backup directory."""
        backup = self._root / "pre-sqlite-backup"
        backup.mkdir(exist_ok=True)
        for name in [
            "executions", "events", "traces", "retrospectives",
            "usage-log.jsonl", "telemetry.jsonl",
            "learned-patterns.json", "budget-recommendations.json",
            "active-task-id.txt", "execution-state.json",
        ]:
            src = self._root / name
            if src.exists():
                dst = backup / name
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                    shutil.rmtree(src)
                else:
                    shutil.copy2(src, dst)
                    src.unlink()

    # Each _migrate_* method reads the source files, parses them,
    # and INSERTs into the appropriate tables using executemany().
    # Implementation follows the patterns already established in the
    # existing from_dict() methods on each dataclass.
    # (Full implementations omitted for brevity but are straightforward.)
```

### 1.10 SqliteStorage Implementation Sketch

The core methods follow a simple pattern. Here is the critical `save_execution`/`load_execution` pair to show the normalized approach:

```python
# agent_baton/core/storage/sqlite_backend.py (key methods)

class SqliteStorage:
    def __init__(self, db_path: Path) -> None:
        self._mgr = ConnectionManager(db_path)

    def save_execution(self, state: ExecutionState) -> None:
        conn = self._mgr.get_connection()
        with conn:  # transaction
            # Upsert execution row
            conn.execute("""
                INSERT OR REPLACE INTO executions
                    (task_id, status, current_phase, current_step_index,
                     started_at, completed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                state.task_id, state.status, state.current_phase,
                state.current_step_index, state.started_at,
                state.completed_at,
                datetime.now(timezone.utc).isoformat(),
            ))

            # Save plan (upsert)
            plan = state.plan
            conn.execute("""
                INSERT OR REPLACE INTO plans
                    (task_id, task_summary, risk_level, budget_tier,
                     execution_mode, git_strategy, shared_context,
                     pattern_source, plan_markdown, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                plan.task_id, plan.task_summary, plan.risk_level,
                plan.budget_tier, plan.execution_mode, plan.git_strategy,
                plan.shared_context, plan.pattern_source,
                plan.to_markdown(), plan.created_at,
            ))

            # Delete and re-insert phases, steps (simpler than diffing)
            conn.execute("DELETE FROM plan_phases WHERE task_id = ?", (state.task_id,))
            conn.execute("DELETE FROM plan_steps WHERE task_id = ?", (state.task_id,))

            for phase in plan.phases:
                gate = phase.gate
                conn.execute("""
                    INSERT INTO plan_phases
                        (task_id, phase_id, name, approval_required,
                         approval_description, gate_type, gate_command,
                         gate_description, gate_fail_on)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    state.task_id, phase.phase_id, phase.name,
                    int(phase.approval_required), phase.approval_description,
                    gate.gate_type if gate else None,
                    gate.command if gate else None,
                    gate.description if gate else None,
                    json.dumps(gate.fail_on) if gate else None,
                ))
                for step in phase.steps:
                    conn.execute("""
                        INSERT INTO plan_steps
                            (task_id, step_id, phase_id, agent_name,
                             task_description, model, depends_on,
                             deliverables, allowed_paths, blocked_paths,
                             context_files)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        state.task_id, step.step_id, phase.phase_id,
                        step.agent_name, step.task_description, step.model,
                        json.dumps(step.depends_on),
                        json.dumps(step.deliverables),
                        json.dumps(step.allowed_paths),
                        json.dumps(step.blocked_paths),
                        json.dumps(step.context_files),
                    ))

            # Step results: delete and re-insert
            conn.execute("DELETE FROM step_results WHERE task_id = ?", (state.task_id,))
            for sr in state.step_results:
                conn.execute("""
                    INSERT INTO step_results
                        (task_id, step_id, agent_name, status, outcome,
                         files_changed, commit_hash, estimated_tokens,
                         duration_seconds, retries, error, completed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    state.task_id, sr.step_id, sr.agent_name, sr.status,
                    sr.outcome, json.dumps(sr.files_changed), sr.commit_hash,
                    sr.estimated_tokens, sr.duration_seconds, sr.retries,
                    sr.error, sr.completed_at,
                ))

            # Gate results, approval results, amendments similarly...

    def load_execution(self, task_id: str) -> ExecutionState | None:
        conn = self._mgr.get_connection()
        row = conn.execute(
            "SELECT * FROM executions WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None

        plan = self._load_plan_internal(conn, task_id)
        if plan is None:
            return None

        step_results = self._load_step_results(conn, task_id)
        gate_results = self._load_gate_results(conn, task_id)
        approval_results = self._load_approval_results(conn, task_id)
        amendments = self._load_amendments(conn, task_id)

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
        )
```

---

## 2. Alternatives Considered

### 2a. JSON blob column per execution (denormalized)

Store the entire `ExecutionState.to_dict()` as a single JSON column. This is what the current file system does, just in a database file.

**Rejected because:** It defeats the querying requirement. You cannot efficiently ask "which agents failed" without loading and parsing every execution's JSON blob. The whole point of this migration is to make data queryable. We do use JSON columns for leaf-level arrays (e.g., `depends_on`, `files_changed`, `deliverables`) where per-element querying is not needed, but the core entities are fully normalized.

### 2b. Multiple SQLite databases per project (one per concern)

Separate databases for executions, telemetry, events, retrospectives, etc.

**Rejected because:** This adds complexity without benefit. SQLite handles millions of rows per table trivially. A single file is easier to back up, migrate, and reason about. The only exception is the PMO database, which by nature is cross-project and lives at `~/.baton/pmo.db`.

### 2c. DuckDB instead of SQLite

Better analytical queries, columnar storage, richer SQL.

**Rejected because:** DuckDB is not in Python's stdlib. The "zero dependencies" requirement rules it out. SQLite is universally available, well-understood, and more than sufficient for the data volumes here (hundreds to low thousands of executions per project).

### 2d. Keep file-based but add an index file

Maintain the current JSON/JSONL layout but add a lightweight index file for querying.

**Rejected because:** This is half the complexity of SQLite for a fraction of the benefit. You still have no transactions, no concurrent read safety, and the index must be kept in sync manually. SQLite gives all of this for free.

---

## 3. Risks and Mitigations

### 3.1 Database corruption

**Risk:** SQLite files can corrupt if the filesystem doesn't honor `fsync`, or if the process crashes during a write.

**Mitigation:** WAL mode is more resilient than the default rollback journal. The `ConnectionManager` uses `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=5000`. The migration tool creates a backup before importing. The `FileStorage` backend remains available as a fallback.

### 3.2 Concurrent writes from multiple CLI processes

**Risk:** Two `baton execute record` calls in quick succession could conflict.

**Mitigation:** WAL mode allows concurrent readers with a single writer. The `busy_timeout` of 5 seconds handles brief writer contention. For the critical `save_execution` path, the entire write is wrapped in a single transaction (`with conn:`), so partial writes are impossible. This is strictly better than the current file-based approach, which has no locking at all.

### 3.3 Migration data loss

**Risk:** The JSON-to-SQLite migration could lose or corrupt data.

**Mitigation:** The `Migrator` runs a verification pass comparing source record counts to inserted row counts. A `--dry-run` mode shows what would be migrated without touching anything. Original files are moved to `pre-sqlite-backup/`, not deleted. The `--keep-files` flag preserves originals alongside the new database.

### 3.4 Breaking existing integrations

**Risk:** External tools or scripts that read `.claude/team-context/execution-state.json` directly will break.

**Mitigation:** The `detect_backend()` function auto-detects file vs. SQLite mode. Projects that haven't migrated continue to use file-based storage with zero changes. The `plan.md` file is still written to `<task_dir>/plan.md` even in SQLite mode (it's a rendered output, not a data store) for users who want to read it. The migration is opt-in via `baton migrate`.

### 3.5 Database file size

**Risk:** With telemetry events, the database could grow large over time.

**Mitigation:** The existing `DataArchiver.cleanup()` method is adapted to run `DELETE FROM telemetry WHERE timestamp < ?` instead of rotating JSONL lines. An explicit `VACUUM` can be offered as `baton db vacuum`. SQLite WAL checkpointing handles normal growth. Expected sizes: a project with 500 executions, averaging 10 steps each, with full telemetry, would produce a database under 50 MB.

### 3.6 Performance of `save_execution` with delete-and-reinsert

**Risk:** Deleting all phases/steps/results and reinserting them on every save could be slow.

**Mitigation:** A typical execution has 3-5 phases with 2-4 steps each. That's 10-20 rows. Delete-and-reinsert for 20 rows inside a transaction takes under 1 millisecond on any modern disk. This is not a concern until plans regularly exceed hundreds of steps, which is structurally impossible in the agent-baton model.

---

## 4. Implementation Guidance

### 4.1 Implementation Order

The work should be done in this order, each step independently testable:

**Phase 1: Foundation (2 files, ~400 lines)**
1. Create `agent_baton/core/storage/__init__.py` with `detect_backend()` and `get_storage()`.
2. Create `agent_baton/core/storage/connection.py` with `ConnectionManager`.
3. Create `agent_baton/core/storage/schema.py` with `PROJECT_SCHEMA_DDL` and `PMO_SCHEMA_DDL` as string constants.
4. Create `agent_baton/core/storage/protocol.py` with `StorageBackend` protocol.

**Phase 2: SQLite Backend (1 large file, ~800 lines)**
5. Create `agent_baton/core/storage/sqlite_backend.py` implementing `SqliteStorage`. Start with the execution state path (`save_execution`, `load_execution`) since that is the hot path during `baton execute`. Add remaining methods in order of usage frequency: events, usage, telemetry, retrospectives, traces, patterns, budget, mission log, context, profile.

**Phase 3: File Backend Wrapper (1 file, ~300 lines)**
6. Create `agent_baton/core/storage/file_backend.py` implementing `FileStorage` that wraps the existing `StatePersistence`, `UsageLogger`, `EventPersistence`, `TraceRecorder`, `RetrospectiveEngine`, `PatternLearner`, `BudgetTuner`, and `ContextManager`. This is a thin adapter, not a rewrite.

**Phase 4: Wire Existing Classes (~100 lines of changes across ~8 files)**
7. Modify `StatePersistence` to accept an optional `StorageBackend`. When provided, delegate to it. When not provided, use the existing file I/O (preserving backward compatibility for tests).
8. Same for `UsageLogger`, `EventPersistence`, `TraceRecorder`, `RetrospectiveEngine`, `PatternLearner`, `BudgetTuner`, `ContextManager`.
9. Modify `ExecutionDriver` (in `agent_baton/core/engine/executor.py`) to call `get_storage()` and pass the backend to `StatePersistence`.

**Phase 5: PMO SQLite (1 file, ~200 lines)**
10. Create `agent_baton/core/storage/pmo_sqlite.py` implementing `PmoSqliteStore` with the same API surface as `PmoStore` but backed by `~/.baton/pmo.db`.
11. Modify `PmoStore` to auto-detect and delegate (same pattern as project storage).

**Phase 6: Migration Tool (1 file + 1 CLI command, ~300 lines)**
12. Create `agent_baton/core/storage/migrate.py` with `Migrator`.
13. Add `baton migrate` CLI command (in `agent_baton/cli/commands/execution/`).

**Phase 7: CLI Query Commands (~200 lines)**
14. Add `baton db query` command for ad-hoc SQL queries against `baton.db`.
15. Add `baton db stats` command for pre-built summaries (agent reliability, gate pass rates, etc.).
16. Add `baton db vacuum` command.

### 4.2 Key Design Decisions

**JSON columns for leaf-level arrays.** Fields like `depends_on`, `deliverables`, `files_changed`, `gate_results` are stored as `TEXT` containing JSON arrays. These are never queried individually (you never ask "find all steps that depend on step 1.2"). They are deserialized in Python after row fetch. This avoids junction tables for data that is always loaded and saved as a unit.

**No ORM.** Raw `sqlite3` from the stdlib. The queries are simple CRUD. An ORM would add a dependency and cognitive overhead for no benefit.

**Singleton tables.** `codebase_profile`, `improvement_trigger_state`, and `active_task` use `CHECK (id = 1)` to enforce single-row semantics. This replaces files like `active-task-id.txt` and `improvement-trigger-state.json`.

**`plan_markdown` stored in `plans`.** Even though the markdown is derived from the structured plan data, we store it for fast retrieval when `baton status` is called. Regenerating it every time from normalized tables would be wasteful. It's updated whenever the plan is saved.

**`telemetry.task_id` is optional.** Telemetry events don't always have a task context (e.g., during `baton plan`). The column defaults to empty string and is indexed for when it is present.

**PmoCard `column` renamed to `column_name`.** `column` is a SQL reserved word. The `PmoCard.to_dict()` key remains `"column"` for backward compatibility in the API; only the database column name differs.

### 4.3 Transaction Boundaries

- `save_execution`: One transaction wrapping the full state save (execution + plan + phases + steps + results). This is the most critical atomicity guarantee -- a partial save would leave the engine in an inconsistent state.
- `append_event`: Individual INSERT, no transaction needed (append-only, idempotent by event_id).
- `log_usage`: Individual INSERT (append-only).
- `log_telemetry`: Individual INSERT (append-only, high volume).
- `save_retrospective`: One transaction for retro + outcomes + gaps + recommendations + sequencing notes.
- `save_patterns` / `save_budget_recommendations`: DELETE all + INSERT all in one transaction (same as the current overwrite-file pattern).

### 4.4 Test Strategy

The existing 2007 tests continue to pass without modification because the `FileStorage` backend wraps the existing classes unchanged. New tests for `SqliteStorage` should mirror the existing test patterns:

- `tests/core/storage/test_connection.py` -- WAL mode, schema creation, version upgrade
- `tests/core/storage/test_sqlite_backend.py` -- Full round-trip for every StorageBackend method
- `tests/core/storage/test_file_backend.py` -- Verify the FileStorage adapter delegates correctly
- `tests/core/storage/test_migrate.py` -- Create sample JSON files, migrate, verify row counts
- `tests/core/storage/test_detect.py` -- Backend detection logic

### 4.5 Files That Change

New files:
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/storage/__init__.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/storage/protocol.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/storage/connection.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/storage/schema.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/storage/sqlite_backend.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/storage/file_backend.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/storage/pmo_sqlite.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/storage/migrate.py`

Modified files (minimal changes -- add optional `backend` parameter):
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/engine/persistence.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/engine/executor.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/observe/usage.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/observe/telemetry.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/observe/trace.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/observe/retrospective.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/observe/archiver.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/events/persistence.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/learn/pattern_learner.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/learn/budget_tuner.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/orchestration/context.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/pmo/store.py`
- `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/improve/triggers.py`

The `TelemetryEvent` dataclass at `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/observe/telemetry.py` should be moved to `/home/djiv/PycharmProjects/orchestrator-v2/agent_baton/models/telemetry.py` to follow the project convention of models in `models/`.