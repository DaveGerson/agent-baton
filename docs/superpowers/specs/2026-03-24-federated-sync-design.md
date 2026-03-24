Good. That's 29 project tables (including `_schema_version`) and 8 PMO tables (including `_schema_version`). Now I have everything I need to produce the comprehensive design. Let me write it.

---

Here is the comprehensive design document.

---

# Federated Sync Architecture for Agent-Baton

## 1. Recommended Approach

### 1.1 Architecture Overview

```
  Project A                   Project B                  Project C
  .claude/team-context/       .claude/team-context/      .claude/team-context/
  baton.db (29 tables)        baton.db (29 tables)       baton.db (29 tables)
       |                           |                          |
       |  baton sync               |  baton sync              |  auto on complete
       +---------------------------+--------------------------+
                                   |
                                   v
                          ~/.baton/central.db
                          +-----------------------+
                          | project-scoped mirror |
                          | of all 27 sync tables |
                          | + PMO tables (merged) |
                          | + sync_watermarks     |
                          | + sync_history        |
                          | + external_items      |
                          | + external_mappings   |
                          | + cross-project views |
                          +-----------------------+
                                   |
                                   v
                          PMO UI / baton query / baton pmo status
```

**Core invariants:**

- Per-project `baton.db` remains the **sole write target** for execution. No execution code ever writes to `central.db`.
- `central.db` is a **read replica** populated exclusively by the sync mechanism.
- Sync is one-way: project -> central. Never the reverse.
- `pmo.db` is **absorbed** into `central.db`. The `projects`, `programs`, `signals`, `archived_cards`, `forge_sessions`, and `pmo_metrics` tables move into central.db alongside the synced project data.
- Each synced row in central.db carries a `project_id` TEXT column that was not present in the per-project schema.

**What does NOT sync to central.db:**

- `_schema_version` -- central has its own versioning.
- `active_task` -- ephemeral per-project singleton, meaningless globally.
- `codebase_profile` -- large text blob, project-local concern.

That leaves **27 syncable tables** from the project schema, plus 6 PMO tables (excluding `_schema_version`), plus 4 new tables for sync and external sources.

### 1.2 Data Flow

```
baton plan "..."  -->  writes to project baton.db (plans, plan_phases, plan_steps, ...)
baton execute start --> writes to project baton.db (executions, ...)
  ...agent dispatches...
baton execute complete
  |
  +--> executor.complete()   writes final state to project baton.db
  +--> auto-sync hook        SyncEngine.push(project_id) copies new rows to central.db
  |
  +--> event: sync.completed published

baton sync                   manual trigger, same SyncEngine.push()
baton sync --all             iterates all registered projects

baton pmo status             reads from central.db (not from individual baton.dbs)
baton query "..."            cross-project SQL against central.db
```

### 1.3 Why This Shape

**Trade-off: central read-replica vs. central as source of truth.**

A central write-through design (where all projects write to central.db and local is a cache) would simplify consistency but create a single point of failure. If `~/.baton/central.db` is corrupted or missing, every project's execution engine breaks. The replica approach means projects always work offline and central.db can be rebuilt from scratch by re-syncing all projects.

**Trade-off: row-level sync vs. file-level sync (copying the entire baton.db).**

File-level copy is simpler but O(n) on total data, not incremental, and requires complex merge logic when two projects share the same central.db. Row-level sync with watermarks is O(delta), idempotent, and naturally deduplicates.

**Trade-off: merging pmo.db into central.db vs. keeping them separate.**

Keeping them separate means two SQLite files at `~/.baton/`. The PMO tables are already global (not per-project), so merging them into the same central.db avoids cross-database joins, reduces file management complexity, and gives the PMO scanner one database to query instead of N+1. The cost is a slightly larger schema, which is acceptable.

---

## 2. Central Database Schema (Full DDL)

```sql
-- =======================================================================
-- ~/.baton/central.db — Central Aggregation Database
-- =======================================================================

-- Schema version tracking (central's own)
CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER NOT NULL
);

-- =======================================================================
-- SYNC INFRASTRUCTURE
-- =======================================================================

-- Per-project sync watermarks: tracks the last-synced state for each table
CREATE TABLE IF NOT EXISTS sync_watermarks (
    project_id    TEXT NOT NULL,
    table_name    TEXT NOT NULL,
    last_rowid    INTEGER NOT NULL DEFAULT 0,
    last_synced   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, table_name)
);

-- Sync history log: one row per sync operation
CREATE TABLE IF NOT EXISTS sync_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    completed_at  TEXT,
    status        TEXT NOT NULL DEFAULT 'running',   -- running|complete|failed
    rows_synced   INTEGER NOT NULL DEFAULT 0,
    tables_synced INTEGER NOT NULL DEFAULT 0,
    error         TEXT NOT NULL DEFAULT '',
    trigger       TEXT NOT NULL DEFAULT 'manual'     -- manual|auto|rebuild
);
CREATE INDEX IF NOT EXISTS idx_sync_history_project ON sync_history(project_id);
CREATE INDEX IF NOT EXISTS idx_sync_history_status ON sync_history(status);

-- =======================================================================
-- PMO TABLES (migrated from pmo.db — global, not per-project)
-- =======================================================================

CREATE TABLE IF NOT EXISTS projects (
    project_id     TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    path           TEXT NOT NULL,
    program        TEXT NOT NULL,
    color          TEXT NOT NULL DEFAULT '',
    description    TEXT NOT NULL DEFAULT '',
    registered_at  TEXT NOT NULL DEFAULT '',
    ado_project    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_projects_program ON projects(program);

CREATE TABLE IF NOT EXISTS programs (
    name TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS signals (
    signal_id         TEXT PRIMARY KEY,
    signal_type       TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS archived_cards (
    card_id          TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    program          TEXT NOT NULL,
    title            TEXT NOT NULL,
    column_name      TEXT NOT NULL,
    risk_level       TEXT NOT NULL DEFAULT 'LOW',
    priority         INTEGER NOT NULL DEFAULT 0,
    agents           TEXT NOT NULL DEFAULT '[]',
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

CREATE TABLE IF NOT EXISTS forge_sessions (
    session_id    TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL DEFAULT '',
    completed_at  TEXT,
    task_id       TEXT,
    notes         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pmo_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    program         TEXT NOT NULL DEFAULT '',
    metric_name     TEXT NOT NULL,
    metric_value    REAL NOT NULL DEFAULT 0.0,
    details         TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_pmo_metrics_ts ON pmo_metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_pmo_metrics_name ON pmo_metrics(metric_name);

-- =======================================================================
-- EXTERNAL SOURCE TABLES (ADO, Jira, GitHub, etc.)
-- =======================================================================

-- Registered external source connections
CREATE TABLE IF NOT EXISTS external_sources (
    source_id     TEXT PRIMARY KEY,              -- e.g. "ado-myorg-myproject"
    source_type   TEXT NOT NULL,                 -- ado|jira|github|linear
    display_name  TEXT NOT NULL DEFAULT '',
    config        TEXT NOT NULL DEFAULT '{}',    -- JSON: org, project, pat_env_var, etc.
    last_synced   TEXT NOT NULL DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 1
);

-- Work items pulled from external sources
CREATE TABLE IF NOT EXISTS external_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL,
    external_id     TEXT NOT NULL,                -- e.g. ADO work item ID "12345"
    item_type       TEXT NOT NULL,                -- feature|bug|epic|story|task
    title           TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL DEFAULT '',     -- e.g. "New", "Active", "Closed"
    assigned_to     TEXT NOT NULL DEFAULT '',
    priority        INTEGER NOT NULL DEFAULT 0,
    parent_id       TEXT NOT NULL DEFAULT '',     -- external_id of parent (epic->feature)
    tags            TEXT NOT NULL DEFAULT '[]',   -- JSON array
    url             TEXT NOT NULL DEFAULT '',     -- web link to the item
    raw_data        TEXT NOT NULL DEFAULT '{}',   -- full API response as JSON
    fetched_at      TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT '',     -- from the external system
    UNIQUE (source_id, external_id),
    FOREIGN KEY (source_id) REFERENCES external_sources(source_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ext_items_source ON external_items(source_id);
CREATE INDEX IF NOT EXISTS idx_ext_items_type ON external_items(item_type);
CREATE INDEX IF NOT EXISTS idx_ext_items_state ON external_items(state);

-- Mapping between external items and baton plans/executions
CREATE TABLE IF NOT EXISTS external_mappings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    project_id      TEXT NOT NULL,
    task_id         TEXT NOT NULL,                -- baton task_id (execution/plan)
    mapping_type    TEXT NOT NULL DEFAULT 'implements',  -- implements|blocks|related
    created_at      TEXT NOT NULL DEFAULT '',
    UNIQUE (source_id, external_id, task_id),
    FOREIGN KEY (source_id) REFERENCES external_sources(source_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ext_map_task ON external_mappings(task_id);
CREATE INDEX IF NOT EXISTS idx_ext_map_external ON external_mappings(source_id, external_id);

-- =======================================================================
-- SYNCED PROJECT TABLES (mirrors of per-project baton.db, with project_id)
-- =======================================================================
-- Every table below has the same columns as the per-project version,
-- plus a project_id TEXT column added to the PRIMARY KEY.

CREATE TABLE IF NOT EXISTS executions (
    project_id      TEXT NOT NULL,
    task_id         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'running',
    current_phase   INTEGER NOT NULL DEFAULT 0,
    current_step_index INTEGER NOT NULL DEFAULT 0,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    created_at      TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_c_exec_status ON executions(status);
CREATE INDEX IF NOT EXISTS idx_c_exec_started ON executions(started_at);
CREATE INDEX IF NOT EXISTS idx_c_exec_project ON executions(project_id);

CREATE TABLE IF NOT EXISTS plans (
    project_id        TEXT NOT NULL,
    task_id           TEXT NOT NULL,
    task_summary      TEXT NOT NULL,
    risk_level        TEXT NOT NULL DEFAULT 'LOW',
    budget_tier       TEXT NOT NULL DEFAULT 'standard',
    execution_mode    TEXT NOT NULL DEFAULT 'phased',
    git_strategy      TEXT NOT NULL DEFAULT 'commit-per-agent',
    shared_context    TEXT NOT NULL DEFAULT '',
    pattern_source    TEXT,
    plan_markdown     TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    PRIMARY KEY (project_id, task_id)
);

CREATE TABLE IF NOT EXISTS plan_phases (
    project_id           TEXT NOT NULL,
    task_id              TEXT NOT NULL,
    phase_id             INTEGER NOT NULL,
    name                 TEXT NOT NULL,
    approval_required    INTEGER NOT NULL DEFAULT 0,
    approval_description TEXT NOT NULL DEFAULT '',
    gate_type            TEXT,
    gate_command         TEXT,
    gate_description     TEXT,
    gate_fail_on         TEXT,
    PRIMARY KEY (project_id, task_id, phase_id)
);

CREATE TABLE IF NOT EXISTS plan_steps (
    project_id        TEXT NOT NULL,
    task_id           TEXT NOT NULL,
    step_id           TEXT NOT NULL,
    phase_id          INTEGER NOT NULL,
    agent_name        TEXT NOT NULL,
    task_description  TEXT NOT NULL DEFAULT '',
    model             TEXT NOT NULL DEFAULT 'sonnet',
    depends_on        TEXT NOT NULL DEFAULT '[]',
    deliverables      TEXT NOT NULL DEFAULT '[]',
    allowed_paths     TEXT NOT NULL DEFAULT '[]',
    blocked_paths     TEXT NOT NULL DEFAULT '[]',
    context_files     TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (project_id, task_id, step_id)
);
CREATE INDEX IF NOT EXISTS idx_c_steps_agent ON plan_steps(agent_name);

CREATE TABLE IF NOT EXISTS team_members (
    project_id     TEXT NOT NULL,
    task_id        TEXT NOT NULL,
    step_id        TEXT NOT NULL,
    member_id      TEXT NOT NULL,
    agent_name     TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'implementer',
    task_description TEXT NOT NULL DEFAULT '',
    model          TEXT NOT NULL DEFAULT 'sonnet',
    depends_on     TEXT NOT NULL DEFAULT '[]',
    deliverables   TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (project_id, task_id, step_id, member_id)
);

CREATE TABLE IF NOT EXISTS step_results (
    project_id        TEXT NOT NULL,
    task_id           TEXT NOT NULL,
    step_id           TEXT NOT NULL,
    agent_name        TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'complete',
    outcome           TEXT NOT NULL DEFAULT '',
    files_changed     TEXT NOT NULL DEFAULT '[]',
    commit_hash       TEXT NOT NULL DEFAULT '',
    estimated_tokens  INTEGER NOT NULL DEFAULT 0,
    duration_seconds  REAL NOT NULL DEFAULT 0.0,
    retries           INTEGER NOT NULL DEFAULT 0,
    error             TEXT NOT NULL DEFAULT '',
    completed_at      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, task_id, step_id)
);
CREATE INDEX IF NOT EXISTS idx_c_sr_status ON step_results(status);
CREATE INDEX IF NOT EXISTS idx_c_sr_agent ON step_results(agent_name);

CREATE TABLE IF NOT EXISTS team_step_results (
    project_id     TEXT NOT NULL,
    task_id        TEXT NOT NULL,
    step_id        TEXT NOT NULL,
    member_id      TEXT NOT NULL,
    agent_name     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'complete',
    outcome        TEXT NOT NULL DEFAULT '',
    files_changed  TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (project_id, task_id, step_id, member_id)
);

CREATE TABLE IF NOT EXISTS gate_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    phase_id    INTEGER NOT NULL,
    gate_type   TEXT NOT NULL,
    passed      INTEGER NOT NULL,
    output      TEXT NOT NULL DEFAULT '',
    checked_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_c_gate_task ON gate_results(project_id, task_id);

CREATE TABLE IF NOT EXISTS approval_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    phase_id    INTEGER NOT NULL,
    result      TEXT NOT NULL,
    feedback    TEXT NOT NULL DEFAULT '',
    decided_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS amendments (
    project_id        TEXT NOT NULL,
    task_id           TEXT NOT NULL,
    amendment_id      TEXT NOT NULL,
    trigger           TEXT NOT NULL,
    trigger_phase_id  INTEGER NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    phases_added      TEXT NOT NULL DEFAULT '[]',
    steps_added       TEXT NOT NULL DEFAULT '[]',
    feedback          TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, task_id, amendment_id)
);

CREATE TABLE IF NOT EXISTS events (
    project_id  TEXT NOT NULL,
    event_id    TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    topic       TEXT NOT NULL,
    sequence    INTEGER NOT NULL DEFAULT 0,
    payload     TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (project_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_c_events_task ON events(project_id, task_id);
CREATE INDEX IF NOT EXISTS idx_c_events_topic ON events(topic);

CREATE TABLE IF NOT EXISTS usage_records (
    project_id        TEXT NOT NULL,
    task_id           TEXT NOT NULL,
    timestamp         TEXT NOT NULL,
    total_agents      INTEGER NOT NULL DEFAULT 0,
    risk_level        TEXT NOT NULL DEFAULT 'LOW',
    sequencing_mode   TEXT NOT NULL DEFAULT 'phased_delivery',
    gates_passed      INTEGER NOT NULL DEFAULT 0,
    gates_failed      INTEGER NOT NULL DEFAULT 0,
    outcome           TEXT NOT NULL DEFAULT '',
    notes             TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_c_usage_ts ON usage_records(timestamp);

CREATE TABLE IF NOT EXISTS agent_usage (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id         TEXT NOT NULL,
    task_id            TEXT NOT NULL,
    agent_name         TEXT NOT NULL,
    model              TEXT NOT NULL DEFAULT 'sonnet',
    steps              INTEGER NOT NULL DEFAULT 1,
    retries            INTEGER NOT NULL DEFAULT 0,
    gate_results       TEXT NOT NULL DEFAULT '[]',
    estimated_tokens   INTEGER NOT NULL DEFAULT 0,
    duration_seconds   REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_c_au_agent ON agent_usage(agent_name);
CREATE INDEX IF NOT EXISTS idx_c_au_project ON agent_usage(project_id);

CREATE TABLE IF NOT EXISTS telemetry (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   TEXT NOT NULL,
    timestamp    TEXT NOT NULL,
    agent_name   TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    tool_name    TEXT NOT NULL DEFAULT '',
    file_path    TEXT NOT NULL DEFAULT '',
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    details      TEXT NOT NULL DEFAULT '',
    task_id      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_c_telem_agent ON telemetry(agent_name);
CREATE INDEX IF NOT EXISTS idx_c_telem_project ON telemetry(project_id);

CREATE TABLE IF NOT EXISTS retrospectives (
    project_id         TEXT NOT NULL,
    task_id            TEXT NOT NULL,
    task_name          TEXT NOT NULL,
    timestamp          TEXT NOT NULL,
    agent_count        INTEGER NOT NULL DEFAULT 0,
    retry_count        INTEGER NOT NULL DEFAULT 0,
    gates_passed       INTEGER NOT NULL DEFAULT 0,
    gates_failed       INTEGER NOT NULL DEFAULT 0,
    risk_level         TEXT NOT NULL DEFAULT 'LOW',
    duration_estimate  TEXT NOT NULL DEFAULT '',
    estimated_tokens   INTEGER NOT NULL DEFAULT 0,
    markdown           TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, task_id)
);

CREATE TABLE IF NOT EXISTS retrospective_outcomes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    category     TEXT NOT NULL,
    agent_name   TEXT NOT NULL,
    worked_well  TEXT NOT NULL DEFAULT '',
    issues       TEXT NOT NULL DEFAULT '',
    root_cause   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_c_ro_task ON retrospective_outcomes(project_id, task_id);

CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL,
    task_id         TEXT NOT NULL,
    description     TEXT NOT NULL,
    affected_agent  TEXT NOT NULL DEFAULT '',
    suggested_fix   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_c_kg_project ON knowledge_gaps(project_id);

CREATE TABLE IF NOT EXISTS roster_recommendations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    task_id  TEXT NOT NULL,
    action   TEXT NOT NULL,
    target   TEXT NOT NULL,
    reason   TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sequencing_notes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    phase        TEXT NOT NULL,
    observation  TEXT NOT NULL,
    keep         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS traces (
    project_id     TEXT NOT NULL,
    task_id        TEXT NOT NULL,
    plan_snapshot  TEXT NOT NULL DEFAULT '{}',
    started_at     TEXT NOT NULL DEFAULT '',
    completed_at   TEXT,
    outcome        TEXT,
    PRIMARY KEY (project_id, task_id)
);

CREATE TABLE IF NOT EXISTS trace_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    timestamp        TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    agent_name       TEXT,
    phase            INTEGER NOT NULL DEFAULT 0,
    step             INTEGER NOT NULL DEFAULT 0,
    details          TEXT NOT NULL DEFAULT '{}',
    duration_seconds REAL
);
CREATE INDEX IF NOT EXISTS idx_c_te_task ON trace_events(project_id, task_id);

CREATE TABLE IF NOT EXISTS learned_patterns (
    project_id           TEXT NOT NULL,
    pattern_id           TEXT NOT NULL,
    task_type            TEXT NOT NULL,
    stack                TEXT,
    recommended_template TEXT NOT NULL DEFAULT '',
    recommended_agents   TEXT NOT NULL DEFAULT '[]',
    confidence           REAL NOT NULL DEFAULT 0.0,
    sample_size          INTEGER NOT NULL DEFAULT 0,
    success_rate         REAL NOT NULL DEFAULT 0.0,
    avg_token_cost       INTEGER NOT NULL DEFAULT 0,
    evidence             TEXT NOT NULL DEFAULT '[]',
    created_at           TEXT NOT NULL DEFAULT '',
    updated_at           TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, pattern_id)
);
CREATE INDEX IF NOT EXISTS idx_c_lp_type ON learned_patterns(task_type);

CREATE TABLE IF NOT EXISTS budget_recommendations (
    project_id         TEXT NOT NULL,
    task_type          TEXT NOT NULL,
    current_tier       TEXT NOT NULL,
    recommended_tier   TEXT NOT NULL,
    reason             TEXT NOT NULL DEFAULT '',
    avg_tokens_used    INTEGER NOT NULL DEFAULT 0,
    median_tokens_used INTEGER NOT NULL DEFAULT 0,
    p95_tokens_used    INTEGER NOT NULL DEFAULT 0,
    sample_size        INTEGER NOT NULL DEFAULT 0,
    confidence         REAL NOT NULL DEFAULT 0.0,
    potential_savings  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, task_type)
);

CREATE TABLE IF NOT EXISTS mission_log_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    TEXT NOT NULL,
    task_id       TEXT NOT NULL,
    agent_name    TEXT NOT NULL,
    status        TEXT NOT NULL,
    assignment    TEXT NOT NULL DEFAULT '',
    result        TEXT NOT NULL DEFAULT '',
    files         TEXT NOT NULL DEFAULT '[]',
    decisions     TEXT NOT NULL DEFAULT '[]',
    issues        TEXT NOT NULL DEFAULT '[]',
    handoff       TEXT NOT NULL DEFAULT '',
    commit_hash   TEXT NOT NULL DEFAULT '',
    failure_class TEXT,
    timestamp     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_c_ml_task ON mission_log_entries(project_id, task_id);

CREATE TABLE IF NOT EXISTS shared_context (
    project_id     TEXT NOT NULL,
    task_id        TEXT NOT NULL,
    content        TEXT NOT NULL DEFAULT '',
    task_title     TEXT NOT NULL DEFAULT '',
    stack          TEXT NOT NULL DEFAULT '',
    architecture   TEXT NOT NULL DEFAULT '',
    conventions    TEXT NOT NULL DEFAULT '',
    guardrails     TEXT NOT NULL DEFAULT '',
    agent_assignments TEXT NOT NULL DEFAULT '',
    domain_context TEXT NOT NULL DEFAULT '',
    updated_at     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, task_id)
);

-- =======================================================================
-- CROSS-PROJECT VIEWS
-- =======================================================================

-- Agent reliability leaderboard across all projects
CREATE VIEW IF NOT EXISTS v_agent_reliability AS
SELECT
    sr.agent_name,
    COUNT(*) AS total_steps,
    SUM(CASE WHEN sr.status = 'complete' THEN 1 ELSE 0 END) AS successes,
    SUM(CASE WHEN sr.status = 'failed' THEN 1 ELSE 0 END) AS failures,
    ROUND(
        100.0 * SUM(CASE WHEN sr.status = 'complete' THEN 1 ELSE 0 END) / COUNT(*),
        1
    ) AS success_rate_pct,
    SUM(sr.retries) AS total_retries,
    ROUND(AVG(sr.duration_seconds), 1) AS avg_duration_s,
    SUM(sr.estimated_tokens) AS total_tokens,
    COUNT(DISTINCT sr.project_id) AS projects_used_in
FROM step_results sr
GROUP BY sr.agent_name
ORDER BY success_rate_pct DESC, total_steps DESC;

-- Cost analysis by task type across all projects
CREATE VIEW IF NOT EXISTS v_cost_by_task_type AS
SELECT
    lp.task_type,
    COUNT(DISTINCT lp.project_id) AS project_count,
    ROUND(AVG(lp.avg_token_cost)) AS avg_tokens,
    ROUND(AVG(lp.success_rate), 2) AS avg_success_rate,
    SUM(lp.sample_size) AS total_samples
FROM learned_patterns lp
GROUP BY lp.task_type
ORDER BY total_samples DESC;

-- Knowledge gaps that appear in multiple projects
CREATE VIEW IF NOT EXISTS v_recurring_knowledge_gaps AS
SELECT
    kg.description,
    kg.affected_agent,
    COUNT(DISTINCT kg.project_id) AS project_count,
    GROUP_CONCAT(DISTINCT kg.project_id) AS projects
FROM knowledge_gaps kg
GROUP BY kg.description, kg.affected_agent
HAVING project_count > 1
ORDER BY project_count DESC;

-- Project failure ranking
CREATE VIEW IF NOT EXISTS v_project_failure_rate AS
SELECT
    e.project_id,
    p.name AS project_name,
    COUNT(*) AS total_executions,
    SUM(CASE WHEN e.status = 'complete' THEN 1 ELSE 0 END) AS completed,
    SUM(CASE WHEN e.status = 'failed' THEN 1 ELSE 0 END) AS failed,
    ROUND(
        100.0 * SUM(CASE WHEN e.status = 'failed' THEN 1 ELSE 0 END) / COUNT(*),
        1
    ) AS failure_rate_pct
FROM executions e
LEFT JOIN projects p ON e.project_id = p.project_id
GROUP BY e.project_id
ORDER BY failure_rate_pct DESC;

-- External item to plan mapping
CREATE VIEW IF NOT EXISTS v_external_plan_mapping AS
SELECT
    ei.source_id,
    es.source_type,
    ei.external_id,
    ei.item_type,
    ei.title AS external_title,
    ei.state AS external_state,
    em.project_id,
    em.task_id,
    pl.task_summary AS plan_title,
    ex.status AS execution_status,
    em.mapping_type
FROM external_mappings em
JOIN external_items ei ON em.source_id = ei.source_id AND em.external_id = ei.external_id
JOIN external_sources es ON em.source_id = es.source_id
LEFT JOIN plans pl ON em.project_id = pl.project_id AND em.task_id = pl.task_id
LEFT JOIN executions ex ON em.project_id = ex.project_id AND em.task_id = ex.task_id
ORDER BY es.source_type, ei.external_id;
```

---

## 3. Python Class Signatures

### 3.1 SyncEngine

**Location:** `agent_baton/core/storage/sync.py`

```python
"""Incremental one-way sync from per-project baton.db to central.db."""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

# Tables that sync from project -> central.
# Ordered by foreign key dependencies (parents first).
SYNCABLE_TABLES: list[SyncTableSpec] = [...]  # defined below


@dataclass
class SyncTableSpec:
    """Describes how to sync one table from project to central."""
    name: str                       # table name in both DBs
    pk_columns: list[str]           # PK in the project DB (without project_id)
    has_autoincrement_pk: bool      # True for tables with INTEGER PK AUTOINCREMENT
    watermark_column: str           # column to use for incremental detection


@dataclass
class SyncResult:
    """Result of a single sync operation."""
    project_id: str
    tables_synced: int
    rows_synced: int
    duration_seconds: float
    errors: list[str]

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


class SyncEngine:
    """Incremental one-way sync: project baton.db -> ~/.baton/central.db.

    For each table, reads rows from the project DB with rowid > last watermark,
    inserts them into central.db with the project_id column prepended,
    and updates the watermark.

    Thread-safe: uses separate connections for source and destination.
    """

    def __init__(self, central_db_path: Path | None = None) -> None:
        """Initialize with path to central.db. Defaults to ~/.baton/central.db."""
        ...

    def push(self, project_id: str, project_db_path: Path) -> SyncResult:
        """Sync one project's baton.db into central.db.

        Args:
            project_id: The project's registered ID (e.g., "nds").
            project_db_path: Path to the project's baton.db.

        Returns:
            SyncResult with counts and any errors.
        """
        ...

    def push_all(self) -> list[SyncResult]:
        """Sync all registered projects. Reads project list from central.db."""
        ...

    def rebuild(self, project_id: str, project_db_path: Path) -> SyncResult:
        """Full re-sync: delete all central rows for this project, then push.

        Use after schema changes or to recover from corruption.
        """
        ...

    def _sync_table(
        self,
        src_conn: sqlite3.Connection,
        dst_conn: sqlite3.Connection,
        project_id: str,
        spec: SyncTableSpec,
        watermark: int,
    ) -> int:
        """Copy new rows for one table. Returns count of rows copied."""
        ...

    def _get_watermark(self, project_id: str, table_name: str) -> int:
        """Read the last-synced rowid for a project+table pair."""
        ...

    def _set_watermark(
        self, project_id: str, table_name: str, rowid: int
    ) -> None:
        """Update the watermark after a successful table sync."""
        ...
```

**Sync algorithm for `_sync_table`:**

```python
def _sync_table(self, src_conn, dst_conn, project_id, spec, watermark):
    # 1. Read new rows from project DB
    rows = src_conn.execute(
        f"SELECT rowid, * FROM {spec.name} WHERE rowid > ? ORDER BY rowid",
        (watermark,),
    ).fetchall()

    if not rows:
        return 0

    # 2. For each row, INSERT OR REPLACE into central with project_id prepended
    count = 0
    max_rowid = watermark
    for row in rows:
        source_rowid = row[0]
        columns = row.keys()[1:]  # skip the synthetic rowid column
        values = [row[col] for col in columns]

        # Build INSERT with project_id as first column
        col_list = "project_id, " + ", ".join(columns)
        placeholders = "?, " + ", ".join("?" for _ in columns)

        if spec.has_autoincrement_pk:
            # For AUTOINCREMENT tables, omit the id column to let central
            # generate its own. Use a dedup check instead.
            # ... (dedup via unique constraint on project_id + natural key)
            pass
        else:
            dst_conn.execute(
                f"INSERT OR REPLACE INTO {spec.name} ({col_list}) VALUES ({placeholders})",
                [project_id] + values,
            )

        max_rowid = max(max_rowid, source_rowid)
        count += 1

    # 3. Update watermark
    self._set_watermark(project_id, spec.name, max_rowid)
    return count
```

### 3.2 CentralStore

**Location:** `agent_baton/core/storage/central.py`

```python
"""CentralStore — read interface for cross-project queries against central.db."""
from __future__ import annotations

from pathlib import Path

from agent_baton.core.storage.connection import ConnectionManager


class CentralStore:
    """Read-only query interface for ~/.baton/central.db.

    Provides typed query methods for the cross-project views and
    raw SQL access for ad-hoc queries.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        """Initialize. Defaults to ~/.baton/central.db."""
        ...

    def agent_reliability(
        self, min_steps: int = 5
    ) -> list[dict]:
        """Return agent reliability stats from v_agent_reliability."""
        ...

    def cost_by_task_type(self) -> list[dict]:
        """Return cost analysis from v_cost_by_task_type."""
        ...

    def recurring_knowledge_gaps(self) -> list[dict]:
        """Return knowledge gaps appearing in 2+ projects."""
        ...

    def project_failure_rates(self) -> list[dict]:
        """Return per-project failure rates."""
        ...

    def external_plan_mapping(
        self, source_type: str | None = None
    ) -> list[dict]:
        """Return external item to plan mappings."""
        ...

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute arbitrary read-only SQL. For power users and the CLI."""
        ...

    def close(self) -> None:
        ...
```

### 3.3 External Source Adapter Protocol

**Location:** `agent_baton/core/storage/adapters/__init__.py`

```python
"""External source adapter protocol and registry."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol


@dataclass
class ExternalItem:
    """Normalized work item from any external source."""
    source_id: str
    external_id: str
    item_type: str          # feature|bug|epic|story|task
    title: str
    description: str = ""
    state: str = ""
    assigned_to: str = ""
    priority: int = 0
    parent_id: str = ""
    tags: list[str] | None = None
    url: str = ""
    raw_data: dict | None = None
    updated_at: str = ""


class ExternalSourceAdapter(Protocol):
    """Protocol for external work-tracking system adapters."""

    source_type: str        # "ado", "jira", "github", "linear"

    def connect(self, config: dict) -> None:
        """Validate connection to the external system."""
        ...

    def fetch_items(
        self,
        item_types: list[str] | None = None,
        since: str | None = None,
    ) -> list[ExternalItem]:
        """Fetch work items, optionally filtered by type and updated-since."""
        ...

    def fetch_item(self, external_id: str) -> ExternalItem | None:
        """Fetch a single item by ID."""
        ...


class AdapterRegistry:
    """Discover and instantiate external source adapters."""

    _adapters: dict[str, type[ExternalSourceAdapter]] = {}

    @classmethod
    def register(cls, adapter_class: type[ExternalSourceAdapter]) -> None:
        cls._adapters[adapter_class.source_type] = adapter_class

    @classmethod
    def get(cls, source_type: str) -> type[ExternalSourceAdapter] | None:
        return cls._adapters.get(source_type)

    @classmethod
    def available(cls) -> list[str]:
        return list(cls._adapters.keys())
```

### 3.4 ADO Adapter

**Location:** `agent_baton/core/storage/adapters/ado.py`

```python
"""Azure DevOps adapter — fetches Features, Bugs, Epics via REST API."""
from __future__ import annotations

import os
from dataclasses import dataclass

from agent_baton.core.storage.adapters import (
    AdapterRegistry,
    ExternalItem,
    ExternalSourceAdapter,
)


class AdoAdapter:
    """Fetches work items from Azure DevOps using the REST API.

    Config keys:
        organization: str   — ADO org name
        project: str        — ADO project name
        pat_env_var: str    — env var name containing the PAT (default: ADO_PAT)
        area_path: str      — optional area path filter
    """
    source_type = "ado"

    def __init__(self) -> None:
        self._org: str = ""
        self._project: str = ""
        self._pat: str = ""
        self._area_path: str = ""

    def connect(self, config: dict) -> None:
        self._org = config["organization"]
        self._project = config["project"]
        pat_var = config.get("pat_env_var", "ADO_PAT")
        self._pat = os.environ.get(pat_var, "")
        self._area_path = config.get("area_path", "")
        if not self._pat:
            raise ValueError(
                f"ADO PAT not found in environment variable: {pat_var}"
            )

    def fetch_items(
        self,
        item_types: list[str] | None = None,
        since: str | None = None,
    ) -> list[ExternalItem]:
        """Execute a WIQL query and return normalized ExternalItems."""
        ...  # Uses requests + ADO REST API v7.0

    def fetch_item(self, external_id: str) -> ExternalItem | None:
        """GET a single work item by ID."""
        ...


# Self-register on import
AdapterRegistry.register(AdoAdapter)
```

### 3.5 Updated Factory in `__init__.py`

**Location:** `agent_baton/core/storage/__init__.py` (additions)

```python
# Existing exports unchanged, add:

_CENTRAL_DB = "central.db"
_BATON_DIR = Path.home() / ".baton"


def get_central_storage(central_db_path: Path | None = None):
    """Factory: return the CentralStore backed by central.db."""
    from agent_baton.core.storage.central import CentralStore
    path = central_db_path or (_BATON_DIR / _CENTRAL_DB)
    return CentralStore(path)


def get_sync_engine(central_db_path: Path | None = None):
    """Factory: return the SyncEngine."""
    from agent_baton.core.storage.sync import SyncEngine
    return SyncEngine(central_db_path)
```

---

## 4. CLI Commands

### 4.1 `baton sync`

**Location:** `agent_baton/cli/commands/sync_cmd.py`

```
baton sync                         Sync current project to central.db
baton sync --all                   Sync all registered projects
baton sync --project <id>          Sync a specific project by ID
baton sync --rebuild               Full rebuild (delete + re-sync)
baton sync --rebuild --project <id>  Rebuild a single project
baton sync status                  Show sync watermarks for all projects
```

### 4.2 `baton query`

**Location:** `agent_baton/cli/commands/query_cmd.py`

```
baton query "SELECT * FROM v_agent_reliability"
baton query agents                 Shortcut for v_agent_reliability
baton query costs                  Shortcut for v_cost_by_task_type
baton query gaps                   Shortcut for v_recurring_knowledge_gaps
baton query failures               Shortcut for v_project_failure_rate
baton query mapping                Shortcut for v_external_plan_mapping
baton query --format json "..."    Output as JSON (default: table)
```

### 4.3 `baton source`

**Location:** `agent_baton/cli/commands/source_cmd.py`

```
baton source add ado \
    --name "My ADO" \
    --org myorg \
    --project myproject \
    --pat-env ADO_PAT

baton source list                  Show all registered external sources
baton source sync <source-id>      Pull latest items from an external source
baton source sync --all            Pull from all enabled sources
baton source remove <source-id>    Unregister an external source

baton source map <source-id> <external-id> <project-id> <task-id>
                                   Create a mapping between external item and plan
```

### 4.4 Updated `baton pmo add`

The existing `baton pmo add` command writes to `PmoStore` (JSON) or `PmoSqliteStore` (pmo.db). Post-migration, it writes to `CentralStore` instead. The `_add` function in `pmo_cmd.py` changes its import target.

### 4.5 Auto-sync hook in `baton execute complete`

In `agent_baton/cli/commands/execution/execute.py`, after the `engine.complete()` call at line 295-296:

```python
elif args.subcommand == "complete":
    summary = engine.complete()
    print(summary)

    # Auto-sync to central.db (best-effort, non-blocking)
    try:
        from agent_baton.core.storage.sync import auto_sync_current_project
        result = auto_sync_current_project()
        if result and result.rows_synced > 0:
            print(f"Synced {result.rows_synced} rows to central.db")
    except Exception:
        pass  # sync failure must never block execution completion
```

The `auto_sync_current_project()` helper resolves the current project's registration from central.db by matching the cwd against registered project paths.

---

## 5. Install and Setup Flow

### 5.1 Lifecycle Walkthrough

**Step A: Install agent-baton**

```bash
pip install agent-baton
# or
cd orchestrator-v2 && pip install -e ".[dev]"
```

Nothing happens to `~/.baton/` yet. No databases are created until first use.

**Step B: First run of any `baton` command**

```
baton --version
```

The CLI entrypoint checks for `~/.baton/` and creates it if missing. `central.db` is created lazily on first access by `ConnectionManager`, which applies the `CENTRAL_SCHEMA_DDL`. This means:

- `~/.baton/central.db` is created with all schema tables (sync infra, PMO tables, synced project tables, external source tables, views).
- The old `~/.baton/pmo.db` and `~/.baton/pmo-config.json` are NOT touched yet.

**Step C: Migration from pmo.db (one-time)**

If `~/.baton/pmo.db` exists and `central.db` has no projects, the first `baton pmo` or `baton sync` command triggers an auto-migration:

```python
def _maybe_migrate_pmo(central_path: Path) -> None:
    """One-time migration: copy pmo.db tables into central.db."""
    pmo_path = central_path.parent / "pmo.db"
    if not pmo_path.exists():
        return
    marker = central_path.parent / ".pmo-migrated"
    if marker.exists():
        return

    # ATTACH pmo.db and INSERT INTO central tables
    conn = sqlite3.connect(str(central_path))
    conn.execute(f"ATTACH DATABASE '{pmo_path}' AS pmo")
    conn.execute("INSERT OR IGNORE INTO projects SELECT * FROM pmo.projects")
    conn.execute("INSERT OR IGNORE INTO programs SELECT * FROM pmo.programs")
    conn.execute("INSERT OR IGNORE INTO signals SELECT * FROM pmo.signals")
    # ... same for archived_cards, forge_sessions, pmo_metrics
    conn.execute("DETACH DATABASE pmo")
    conn.commit()
    conn.close()

    marker.write_text("migrated")
```

After migration, the old `pmo.db` and `pmo-config.json` are left in place (not deleted) but are no longer read. A deprecation notice prints once.

**Step D: Register a project**

```bash
cd /home/user/my-project
baton pmo add --id myproj --name "My Project" --path /home/user/my-project --program TEAM1
```

This writes to `central.db` -> `projects` table. Also creates `.claude/team-context/` in the project directory.

**Step E: Create and execute a plan**

```bash
baton plan "Add user authentication" --save --explain
baton execute start
# ... dispatch loop ...
baton execute complete
```

All writes go to `/home/user/my-project/.claude/team-context/baton.db` as before. On `baton execute complete`, the auto-sync hook fires and copies new rows to `~/.baton/central.db`.

**Step F: PMO reads from central**

```bash
baton pmo status
```

The `PmoScanner` now queries `central.db` instead of reaching into each project's filesystem. It reads the `executions`, `plans`, and `step_results` tables filtered by project_id, plus the PMO-native tables (`archived_cards`, `signals`, etc.).

**Step G: Register a second project**

```bash
baton pmo add --id proj2 --name "Project Two" --path /home/user/proj2 --program TEAM1
cd /home/user/proj2
baton plan "..." --save
baton execute start
baton execute complete    # auto-syncs proj2 into central.db
```

**Step H: Cross-project queries**

```bash
baton query agents
# Shows agent reliability across both projects

baton query "SELECT project_id, COUNT(*) as tasks, 
             SUM(CASE WHEN status='complete' THEN 1 ELSE 0 END) as done
             FROM executions GROUP BY project_id"
```

### 5.2 Updated install.sh

Add to the end of `scripts/install.sh`, before the summary section:

```bash
# ── Step 4: Central Database ─────────────────────────────
echo ""
echo "  STEP 4: Central Database"
echo "  ────────────────────────"

BATON_DIR="$HOME/.baton"
mkdir -p "$BATON_DIR"

if [ -f "$BATON_DIR/central.db" ]; then
    echo "  ~ central.db exists — will be upgraded on next baton command"
else
    echo "  + central.db will be created on first baton command"
fi

# Migrate pmo.db if it exists
if [ -f "$BATON_DIR/pmo.db" ] && [ ! -f "$BATON_DIR/.pmo-migrated" ]; then
    echo "  ~ pmo.db detected — will be migrated to central.db on first use"
fi
```

### 5.3 Directory Layout After Setup

```
~/.baton/
    central.db           <-- THE central database (new)
    pmo.db               <-- legacy, left in place after migration
    pmo-config.json      <-- legacy, left in place after migration
    .pmo-migrated        <-- marker file after one-time migration

/home/user/my-project/
    .claude/
        team-context/
            baton.db     <-- per-project, unchanged
        agents/
        references/
```

---

## 6. Cross-Project Query Examples

### 6.1 Agent Reliability Across All Projects

```sql
-- Which agents are most reliable across all my projects?
SELECT * FROM v_agent_reliability WHERE total_steps >= 5;
```

Output:
```
agent_name              | total_steps | successes | failures | success_rate_pct | total_retries | avg_duration_s | total_tokens | projects_used_in
backend-engineer--python|          47 |        45 |        2 |             95.7 |             3 |           42.1 |       285000 |                4
test-engineer           |          31 |        30 |        1 |             96.8 |             1 |           28.3 |       142000 |                4
code-reviewer           |          28 |        28 |        0 |            100.0 |             0 |           18.7 |        98000 |                3
architect               |          15 |        14 |        1 |             93.3 |             2 |           55.2 |       175000 |                3
```

### 6.2 Cost Per Task Type

```sql
-- What do different task types cost on average?
SELECT * FROM v_cost_by_task_type;
```

### 6.3 Knowledge Gaps Across Projects

```sql
-- Which knowledge gaps keep appearing across projects?
SELECT * FROM v_recurring_knowledge_gaps;
```

Output:
```
description                          | affected_agent              | project_count | projects
"Missing test fixtures for DB layer" | test-engineer               |             3 | nds,atl,baton
"Unclear error handling conventions" | backend-engineer--python    |             2 | nds,atl
```

### 6.4 Project Failure Ranking

```sql
-- Which projects have the most execution failures?
SELECT * FROM v_project_failure_rate;
```

### 6.5 ADO Feature to Plan Mapping

```sql
-- Which ADO features have orchestration plans?
SELECT
    external_title,
    external_state,
    project_id,
    plan_title,
    execution_status
FROM v_external_plan_mapping
WHERE item_type = 'feature'
ORDER BY external_state;
```

### 6.6 Ad-hoc: Token spend by project, last 30 days

```sql
SELECT
    e.project_id,
    p.name,
    COUNT(DISTINCT e.task_id) AS tasks,
    SUM(au.estimated_tokens) AS total_tokens,
    ROUND(SUM(au.estimated_tokens) / COUNT(DISTINCT e.task_id)) AS avg_tokens_per_task
FROM executions e
JOIN agent_usage au ON e.project_id = au.project_id AND e.task_id = au.task_id
JOIN projects p ON e.project_id = p.project_id
WHERE e.started_at >= datetime('now', '-30 days')
GROUP BY e.project_id
ORDER BY total_tokens DESC;
```

---

## 7. Alternatives Considered

### 7.1 Central-as-source-of-truth (rejected)

All projects would write directly to central.db and use local baton.db as a read cache. Rejected because:
- Single point of failure: central.db corruption breaks all projects.
- File-locking contention: multiple concurrent Claude sessions writing to the same SQLite file.
- Violates offline-first: projects should work independently.

### 7.2 SQLite ATTACH for cross-project queries (rejected)

Instead of syncing, ATTACH all project baton.db files and query across them. Rejected because:
- SQLite limits ATTACH to 10 databases by default (recompile needed for more).
- Requires all project baton.db files to be accessible at query time (fails if project is on a different machine, external drive, etc.).
- No place to store external source mappings or PMO data that spans projects.

### 7.3 Postgres/DuckDB for central (rejected for now)

A real RDBMS or analytical DB would be more powerful. Rejected because:
- SQLite is already the only dependency for per-project storage.
- Adding Postgres requires infrastructure the user may not have.
- DuckDB is interesting for analytics but adds a dependency.
- The data volumes (hundreds of executions, not millions) don't justify it.
- Can revisit later. The adapter protocol makes the central store backend swappable.

### 7.4 Event-sourcing sync (rejected)

Sync by replaying the events table rather than row-level copy. Rejected because:
- Not all data is event-sourced (telemetry, retrospectives, patterns are direct writes).
- Would require building projections in central.db, adding significant complexity.
- Row-level sync with watermarks is simpler and covers all tables uniformly.

---

## 8. Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Schema drift between project and central | Sync fails, queries break | Central schema version tracks independently. Migration scripts handle adding `project_id` columns. Both schemas live in the same `schema.py` module. |
| Large project DBs slow down sync | Sync takes too long, user skips it | Watermark-based incremental sync is O(delta). First sync is O(n) but runs once. `baton sync status` shows progress. |
| Concurrent sync from two terminals | SQLite write contention | WAL mode + busy_timeout=5000ms. Sync acquires a single transaction per table batch. If contention occurs, retry once. |
| central.db corruption | Cross-project queries fail | central.db is rebuildable: `baton sync --rebuild --all`. Project baton.db files are untouched. PMO tables are backed by the still-present legacy `pmo.db` as fallback. |
| Auto-sync slows down `baton execute complete` | User experiences lag | Auto-sync runs in a best-effort try/except. If it takes >2s, it logs a warning and returns. The user can run `baton sync` manually later. Consider making auto-sync async (subprocess) in a later phase. |
| ADO PAT rotation / expiry | External source sync fails | `baton source sync` reports clear error with the env var name. No PAT is stored in the database (only the env var name). |
| PMO migration from pmo.db loses data | Config/signals/archive lost | Migration uses INSERT OR IGNORE (idempotent). Original files are preserved (never deleted). Marker file prevents re-migration. |

---

## 9. Build Order

### Phase 1: Central Schema and SyncEngine (foundation)

**Depends on:** nothing new (uses existing ConnectionManager, schema.py)

1. Add `CENTRAL_SCHEMA_DDL` to `agent_baton/core/storage/schema.py`
2. Create `agent_baton/core/storage/central.py` -- `CentralStore` class
3. Create `agent_baton/core/storage/sync.py` -- `SyncEngine` class
4. Add `get_central_storage()` and `get_sync_engine()` to `agent_baton/core/storage/__init__.py`
5. Tests: sync engine unit tests (push, rebuild, watermarks, idempotency)

**Deliverables:** SyncEngine can copy rows from any project baton.db to central.db.

### Phase 2: CLI Commands (`baton sync`, `baton query`)

**Depends on:** Phase 1

6. Create `agent_baton/cli/commands/sync_cmd.py` -- `baton sync` command
7. Create `agent_baton/cli/commands/query_cmd.py` -- `baton query` command
8. Register both in `agent_baton/cli/main.py`
9. Tests: CLI integration tests

**Deliverables:** User can manually sync projects and run cross-project queries.

### Phase 3: PMO Migration

**Depends on:** Phase 1

10. Create `_maybe_migrate_pmo()` in `agent_baton/core/storage/central.py`
11. Update `PmoSqliteStore` to accept central.db path (or create a thin wrapper)
12. Update `PmoScanner` to read from central.db instead of scanning filesystems
13. Update `pmo_cmd.py` (`_add`, `_status`, `_health`) to use central.db
14. Update PMO API routes (`agent_baton/api/routes/pmo.py`) if present
15. Tests: migration from pmo.db, scanner reading from central

**Deliverables:** `baton pmo` commands read/write central.db. Old pmo.db auto-migrated.

### Phase 4: Auto-sync Hook

**Depends on:** Phase 1, Phase 2

16. Add `auto_sync_current_project()` helper to `sync.py`
17. Hook it into `execute.py` handler for the `complete` subcommand
18. Add project-path-to-project-id resolution logic
19. Tests: auto-sync fires on complete, graceful failure handling

**Deliverables:** Every `baton execute complete` automatically syncs to central.db.

### Phase 5: External Source Adapters

**Depends on:** Phase 1

20. Create `agent_baton/core/storage/adapters/__init__.py` -- protocol, registry, ExternalItem
21. Create `agent_baton/core/storage/adapters/ado.py` -- ADO adapter
22. Create `agent_baton/cli/commands/source_cmd.py` -- `baton source` command
23. Register in `agent_baton/cli/main.py`
24. Tests: adapter protocol, ADO adapter with mocked HTTP, source CLI

**Deliverables:** Users can register ADO sources, pull items, and map them to plans.

### Phase 6: Install Flow + Documentation

**Depends on:** Phases 1-4

25. Update `scripts/install.sh` with Step 4 (central database)
26. Update `CLAUDE.md` -- repo structure, new modules, new CLI commands
27. Update `docs/architecture.md` -- central.db, sync, external sources
28. Update `README.md` -- mention cross-project queries, PMO central
29. Add `docs/design-decisions.md` entry for federated sync

**Deliverables:** Clean install path, current documentation.

### Phase 7: PMO UI Integration (future)

**Depends on:** Phases 1-5

30. Update `pmo-ui/` React app to use central.db-backed API endpoints
31. Add cross-project dashboard views (agent leaderboard, cost analysis)
32. Add external source mapping UI

---

## 10. File Inventory (new and modified)

**New files:**

| File | Purpose |
|------|---------|
| `agent_baton/core/storage/sync.py` | SyncEngine, SyncTableSpec, SyncResult, auto_sync_current_project |
| `agent_baton/core/storage/central.py` | CentralStore (read interface for central.db) |
| `agent_baton/core/storage/adapters/__init__.py` | ExternalSourceAdapter protocol, AdapterRegistry, ExternalItem |
| `agent_baton/core/storage/adapters/ado.py` | ADO REST API adapter |
| `agent_baton/cli/commands/sync_cmd.py` | `baton sync` CLI command |
| `agent_baton/cli/commands/query_cmd.py` | `baton query` CLI command |
| `agent_baton/cli/commands/source_cmd.py` | `baton source` CLI command |
| `tests/core/storage/test_sync.py` | SyncEngine tests |
| `tests/core/storage/test_central.py` | CentralStore tests |
| `tests/core/storage/test_adapters.py` | Adapter protocol tests |
| `tests/cli/test_sync_cmd.py` | CLI integration tests |

**Modified files:**

| File | Change |
|------|--------|
| `agent_baton/core/storage/schema.py` | Add CENTRAL_SCHEMA_DDL, bump SCHEMA_VERSION |
| `agent_baton/core/storage/__init__.py` | Add get_central_storage(), get_sync_engine() |
| `agent_baton/cli/main.py` | Register sync, query, source commands |
| `agent_baton/cli/commands/execution/execute.py` | Add auto-sync hook after complete |
| `agent_baton/cli/commands/pmo_cmd.py` | Switch from PmoStore/PmoSqliteStore to CentralStore |
| `agent_baton/core/pmo/scanner.py` | Read from central.db instead of filesystem traversal |
| `agent_baton/core/storage/pmo_sqlite.py` | Deprecation path (still usable but central.db preferred) |
| `scripts/install.sh` | Add Step 4 for central database |

---

## 11. Key Design Decisions Summary

1. **One-way sync, project is authoritative.** Central.db is always rebuildable. This means sync bugs cannot corrupt project data.

2. **Rowid-based watermarks, not timestamp-based.** SQLite rowids are monotonically increasing and not subject to clock skew. Every table in SQLite has an implicit rowid even if the schema uses a TEXT primary key.

3. **INSERT OR REPLACE for sync, not INSERT OR IGNORE.** A row may be updated in the project DB after initial sync (e.g., execution status changes from running to complete). OR REPLACE ensures the central copy reflects the latest state.

4. **AUTOINCREMENT tables get special handling.** Tables like `gate_results`, `telemetry`, and `agent_usage` use `INTEGER PRIMARY KEY AUTOINCREMENT`. In central.db, these get new autoincrement IDs, and deduplication relies on unique constraints on (project_id, task_id, ...) natural keys rather than matching the source DB's id.

5. **External adapters are optional imports.** The ADO adapter uses `requests` which is already a common dependency but is not required. If `requests` is missing, `baton source add ado` prints a clear error message. Future adapters (Jira, GitHub) follow the same pattern.

6. **PMO migration is automatic and one-time.** Users do not need to run a migration command. The first `baton pmo` call after central.db exists will auto-migrate if the marker file is absent.