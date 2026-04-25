"""SQL DDL definitions for all Agent Baton SQLite databases.

This module is the single source of truth for the database schemas used
throughout the storage subsystem.  Three distinct schemas are defined:

``PROJECT_SCHEMA_DDL``
    Per-project ``baton.db`` -- stores execution state, plans, step results,
    events, usage, telemetry, retrospectives, traces, learned patterns,
    budget recommendations, mission log entries, shared context, and the
    codebase profile.  All tables use ``task_id`` as the primary key or
    foreign key; no ``project_id`` column exists here.

``PMO_SCHEMA_DDL``
    Global ``~/.baton/pmo.db`` (legacy) -- projects, programs, signals,
    archived cards, forge sessions, and PMO metrics.  Superseded by
    ``central.db`` (see below) but still supported for backward
    compatibility and standalone PMO use.

``CENTRAL_SCHEMA_DDL``
    Global ``~/.baton/central.db`` -- the cross-project read replica.
    Contains:

    * **Sync infrastructure** -- ``sync_watermarks`` and ``sync_history``
      tables used by ``SyncEngine``.
    * **PMO tables** -- identical schema to ``PMO_SCHEMA_DDL``, migrated
      once from ``pmo.db`` by ``_maybe_migrate_pmo``.
    * **External source tables** -- ``external_sources``, ``external_items``,
      ``external_mappings`` for adapter integrations.
    * **Synced project tables** -- mirrors of every project-level table
      with an added ``project_id`` column.  Written exclusively by
      ``SyncEngine.push``.
    * **Analytics views** -- ``v_agent_reliability``,
      ``v_cost_by_task_type``, ``v_recurring_knowledge_gaps``,
      ``v_project_failure_rate``, and ``v_external_plan_mapping``.

``MIGRATIONS``
    A ``dict[int, str]`` mapping schema version numbers to incremental
    ALTER TABLE / CREATE TABLE scripts.  ``ConnectionManager._run_migrations``
    applies these sequentially when an existing database is behind the
    current ``SCHEMA_VERSION``.
"""

SCHEMA_VERSION = 16

# Sequential migration scripts: {version: DDL_string}
MIGRATIONS: dict[int, str] = {
    2: """
-- v2: add knowledge-delivery columns to plans, plan_steps, and executions.
-- Only applied to existing v1 databases; fresh databases start at v2 and
-- already have these columns in their CREATE TABLE statements.

ALTER TABLE plans ADD COLUMN explicit_knowledge_packs TEXT NOT NULL DEFAULT '[]';
ALTER TABLE plans ADD COLUMN explicit_knowledge_docs   TEXT NOT NULL DEFAULT '[]';
ALTER TABLE plans ADD COLUMN intervention_level        TEXT NOT NULL DEFAULT 'low';
ALTER TABLE plans ADD COLUMN task_type                 TEXT;

ALTER TABLE plan_steps ADD COLUMN knowledge_attachments TEXT NOT NULL DEFAULT '[]';

ALTER TABLE executions ADD COLUMN pending_gaps         TEXT NOT NULL DEFAULT '[]';
ALTER TABLE executions ADD COLUMN resolved_decisions   TEXT NOT NULL DEFAULT '[]';
""",
    3: """
-- v3: add deviations column to step_results.
-- Active data loss fix: StepResult.deviations was not persisted to SQLite.

ALTER TABLE step_results ADD COLUMN deviations TEXT NOT NULL DEFAULT '[]';
""",
    4: """
-- v4: add bead memory tables.
-- Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
-- Beads are discrete units of structured memory (discoveries, decisions,
-- warnings, outcomes, planning notes) produced by agents during execution.
--
-- NOTE: FK constraints are intentionally omitted from this migration
-- because it is applied to BOTH project and central databases via
-- ConnectionManager._run_migrations().  The central executions table has
-- a composite PK (project_id, task_id) which is incompatible with a
-- single-column FK reference.  Fresh project databases get the FK via
-- PROJECT_SCHEMA_DDL; central databases get no FK (by design).
CREATE TABLE IF NOT EXISTS beads (
    bead_id          TEXT PRIMARY KEY,
    task_id          TEXT NOT NULL,
    step_id          TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    bead_type        TEXT NOT NULL,
    content          TEXT NOT NULL DEFAULT '',
    confidence       TEXT NOT NULL DEFAULT 'medium',
    scope            TEXT NOT NULL DEFAULT 'step',
    tags             TEXT NOT NULL DEFAULT '[]',
    affected_files   TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       TEXT NOT NULL,
    closed_at        TEXT NOT NULL DEFAULT '',
    summary          TEXT NOT NULL DEFAULT '',
    links            TEXT NOT NULL DEFAULT '[]',
    source           TEXT NOT NULL DEFAULT 'agent-signal',
    token_estimate   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_beads_task ON beads(task_id);
CREATE INDEX IF NOT EXISTS idx_beads_agent ON beads(agent_name);
CREATE INDEX IF NOT EXISTS idx_beads_type ON beads(bead_type);
CREATE INDEX IF NOT EXISTS idx_beads_status ON beads(status);
CREATE TABLE IF NOT EXISTS bead_tags (
    bead_id  TEXT NOT NULL,
    tag      TEXT NOT NULL,
    PRIMARY KEY (bead_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_bead_tags_tag ON bead_tags(tag);
""",
    5: """
-- v5: add learning_issues table for the learning automation system.
-- Applied to both project and central databases via
-- ConnectionManager._run_migrations().  The central CENTRAL_SCHEMA_DDL
-- includes project_id; this migration uses the same DDL for both (no
-- project_id) because existing central databases already have the full
-- table from CENTRAL_SCHEMA_DDL on fresh install.  For central DBs
-- upgrading via migration, sync uses INSERT OR IGNORE which tolerates
-- the missing column.

CREATE TABLE IF NOT EXISTS learning_issues (
    issue_id          TEXT PRIMARY KEY,
    issue_type        TEXT NOT NULL,
    severity          TEXT NOT NULL DEFAULT 'medium',
    status            TEXT NOT NULL DEFAULT 'open',
    title             TEXT NOT NULL,
    target            TEXT NOT NULL,
    evidence          TEXT NOT NULL DEFAULT '[]',
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    occurrence_count  INTEGER NOT NULL DEFAULT 1,
    proposed_fix      TEXT,
    resolution        TEXT,
    resolution_type   TEXT,
    experiment_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_learning_issues_type
    ON learning_issues(issue_type);
CREATE INDEX IF NOT EXISTS idx_learning_issues_status
    ON learning_issues(status);
CREATE INDEX IF NOT EXISTS idx_learning_issues_target
    ON learning_issues(target);
CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_issues_type_target_open
    ON learning_issues(issue_type, target)
    WHERE status NOT IN ('resolved', 'wontfix');
""",
    6: """
-- v6: add quality_score and retrieval_count to beads (F12 Quality Scoring).
-- Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
--
-- NOTE: No FK constraints — this migration is applied to BOTH project and
-- central databases via ConnectionManager._run_migrations().  See the v4
-- note for the full rationale.
ALTER TABLE beads ADD COLUMN quality_score   REAL    NOT NULL DEFAULT 0.0;
ALTER TABLE beads ADD COLUMN retrieval_count INTEGER NOT NULL DEFAULT 0;
""",
    7: """
-- v7: add project_id to learning_issues for central DB sync compatibility.
-- Central databases need project_id for cross-project aggregation.
-- Project databases get the column too (harmless, stays NULL).
-- The idempotent migration handler silently skips if the column already
-- exists (fresh installs from CENTRAL_SCHEMA_DDL).
ALTER TABLE learning_issues ADD COLUMN project_id TEXT;
""",
    8: """
-- v8: no schema DDL change.
-- This version documents the fix for the beads FK cascade bug:
-- save_execution() previously used INSERT OR REPLACE INTO executions, which
-- is DELETE + INSERT in SQLite and triggered ON DELETE CASCADE, silently
-- destroying all bead rows for the task on every save.  The query was changed
-- to INSERT ... ON CONFLICT(task_id) DO UPDATE SET which is a true in-place
-- upsert that does not fire the CASCADE.  No ALTER TABLE is required.
SELECT 1;
""",
    9: """
-- v9: add step_type taxonomy columns to plan_steps and step_results.
-- step_type classifies what kind of work a step performs (developing,
-- planning, testing, reviewing, consulting, task, automation) so the
-- engine can route each step through the appropriate execution path.
-- command holds the shell command for automation steps (no LLM dispatch).
-- All existing rows default to 'developing' / '' which preserves
-- existing behaviour unchanged.
--
-- NOTE: FK constraints are intentionally omitted from this migration
-- because it is applied to BOTH project and central databases via
-- ConnectionManager._run_migrations().  Fresh project DBs get FKs
-- from PROJECT_SCHEMA_DDL directly.
ALTER TABLE plan_steps ADD COLUMN step_type TEXT NOT NULL DEFAULT 'developing';
ALTER TABLE plan_steps ADD COLUMN command TEXT NOT NULL DEFAULT '';
ALTER TABLE step_results ADD COLUMN step_type TEXT NOT NULL DEFAULT 'developing';
""",
    10: """
-- v10: Phase A compliance and identity columns (A2, A3, A4, A6).
--
-- A2 — Decision source and identity on gate_results and approval_results.
--   decision_source: who/what made this decision (human, daemon_auto, api, policy_auto)
--   actor: best-available identity string ($USER@$HOSTNAME or "daemon")
--   rationale: structured rationale for approval decisions
--
-- A3 — Persist ClassificationResult signals/confidence on plans.
--   classification_signals: JSON blob from ClassificationResult.to_dict()
--   classification_confidence: 0.0–1.0 confidence score
--
-- A4 — Persist interaction_turns (multi-turn INTERACT exchanges).
--   A new interaction_turns table stores each InteractionTurn individually.
--   A new feedback_responses table stores each FeedbackResult individually.
--
-- A6 — Gate command traceability on gate_results.
--   command: the shell command that was run
--   exit_code: subprocess return code (NULL for manual/human gates)
--
-- NOTE: FK constraints are intentionally omitted from this migration
-- because it is applied to BOTH project and central databases via
-- ConnectionManager._run_migrations().  Fresh project DBs get FKs
-- from PROJECT_SCHEMA_DDL directly.

-- A6: gate traceability
ALTER TABLE gate_results ADD COLUMN command TEXT NOT NULL DEFAULT '';
ALTER TABLE gate_results ADD COLUMN exit_code INTEGER;

-- A2: decision identity on gate_results
ALTER TABLE gate_results ADD COLUMN decision_source TEXT NOT NULL DEFAULT '';
ALTER TABLE gate_results ADD COLUMN actor TEXT NOT NULL DEFAULT '';

-- A2: decision identity + rationale on approval_results
ALTER TABLE approval_results ADD COLUMN decision_source TEXT NOT NULL DEFAULT '';
ALTER TABLE approval_results ADD COLUMN actor TEXT NOT NULL DEFAULT '';
ALTER TABLE approval_results ADD COLUMN rationale TEXT NOT NULL DEFAULT '';

-- A3: classification signals on plans
ALTER TABLE plans ADD COLUMN classification_signals TEXT;
ALTER TABLE plans ADD COLUMN classification_confidence REAL;

-- A4: interaction turns table
CREATE TABLE IF NOT EXISTS interaction_turns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    step_id      TEXT NOT NULL,
    turn_number  INTEGER NOT NULL DEFAULT 0,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL DEFAULT '',
    timestamp    TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'human'
);
CREATE INDEX IF NOT EXISTS idx_interaction_turns_task ON interaction_turns(task_id);
CREATE INDEX IF NOT EXISTS idx_interaction_turns_step ON interaction_turns(task_id, step_id);

-- A4: feedback responses table
CREATE TABLE IF NOT EXISTS feedback_responses (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id            TEXT NOT NULL,
    phase_id           INTEGER NOT NULL,
    question_id        TEXT NOT NULL,
    chosen_index       INTEGER NOT NULL DEFAULT 0,
    chosen_option      TEXT NOT NULL DEFAULT '',
    dispatched_step_id TEXT NOT NULL DEFAULT '',
    decided_at         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_feedback_responses_task ON feedback_responses(task_id);
""",
    11: """
-- v11: relax beads.task_id to nullable.
--
-- Motivation: beads created via `baton beads create` (manual / CLI) do not
-- have a corresponding executions row, so the FK constraint on task_id
-- fires and rejects the INSERT.  Making task_id nullable lets project-scoped
-- beads (task_id IS NULL) bypass the FK check under SQLite MATCH SIMPLE
-- semantics (the default), while task-scoped beads (task_id IS NOT NULL)
-- continue to be validated normally.
--
-- SQLite does not support DROP NOT NULL via ALTER TABLE.  The only safe
-- approach is a table rebuild.  The migration uses the standard SQLite
-- sequence: rename → create-new → insert-from-old → drop-old → re-index.
--
-- NOTE: This migration is applied to BOTH project and central databases.
-- The central beads table also carries task_id as a non-nullable column so
-- the same rebuild is required there.  Central beads have no FK constraint
-- (by design — see the v4 note), so only the NOT NULL relaxation matters.

-- Step 1: rename old table
ALTER TABLE beads RENAME TO _beads_old_v10;

-- Step 2: create new table with task_id nullable
CREATE TABLE beads (
    bead_id          TEXT PRIMARY KEY,
    task_id          TEXT,
    step_id          TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    bead_type        TEXT NOT NULL,
    content          TEXT NOT NULL DEFAULT '',
    confidence       TEXT NOT NULL DEFAULT 'medium',
    scope            TEXT NOT NULL DEFAULT 'step',
    tags             TEXT NOT NULL DEFAULT '[]',
    affected_files   TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       TEXT NOT NULL,
    closed_at        TEXT NOT NULL DEFAULT '',
    summary          TEXT NOT NULL DEFAULT '',
    links            TEXT NOT NULL DEFAULT '[]',
    source           TEXT NOT NULL DEFAULT 'agent-signal',
    token_estimate   INTEGER NOT NULL DEFAULT 0,
    quality_score    REAL    NOT NULL DEFAULT 0.0,
    retrieval_count  INTEGER NOT NULL DEFAULT 0
);

-- Step 3: copy data (empty-string task_id → NULL for project-scope beads)
INSERT INTO beads SELECT
    bead_id,
    CASE WHEN task_id = '' THEN NULL ELSE task_id END,
    step_id, agent_name, bead_type, content, confidence, scope,
    tags, affected_files, status, created_at, closed_at, summary,
    links, source, token_estimate,
    COALESCE(quality_score, 0.0),
    COALESCE(retrieval_count, 0)
FROM _beads_old_v10;

-- Step 4: drop old table
DROP TABLE _beads_old_v10;

-- Step 5: recreate indexes
CREATE INDEX IF NOT EXISTS idx_beads_task   ON beads(task_id);
CREATE INDEX IF NOT EXISTS idx_beads_agent  ON beads(agent_name);
CREATE INDEX IF NOT EXISTS idx_beads_type   ON beads(bead_type);
CREATE INDEX IF NOT EXISTS idx_beads_status ON beads(status);
""",
    12: """
-- v12: add updated_at to step_results for bi-directional split-brain reconciliation.
--
-- When a crash leaves SQLite and the file fallback with divergent step states,
-- the reconciler needs a reliable "which write happened later?" signal.
-- updated_at (ISO 8601 UTC) is set on every status mutation so the reconciler
-- can compare timestamps across both backends and always pick the newer write,
-- regardless of direction (SQLite newer OR file newer).
--
-- Fallback: rows with an empty updated_at string (pre-v12 data) continue to be
-- resolved by the existing status-rank logic so existing databases are not
-- affected.
--
-- NOTE: FK constraints are intentionally omitted from this migration because
-- it is applied to BOTH project and central databases via
-- ConnectionManager._run_migrations().  Fresh project DBs get FKs from
-- PROJECT_SCHEMA_DDL directly.
ALTER TABLE step_results ADD COLUMN updated_at TEXT NOT NULL DEFAULT '';
""",
    13: """
-- v13: add real per-step token accounting columns to step_results.
--
-- The engine previously used a char/4 heuristic for estimated_tokens, which
-- was off by 2-3 orders of magnitude (real spend ~2.56B tokens vs ~3,344
-- estimated).  These columns hold the actual values sourced from the Claude
-- Code session JSONL files (~/.claude/projects/<slug>/<session_id>.jsonl).
--
-- Fields:
--   input_tokens          -- sum of input_tokens across assistant turns
--   cache_read_tokens     -- sum of cache_read_input_tokens
--   cache_creation_tokens -- sum of cache_creation_input_tokens
--   output_tokens         -- sum of output_tokens
--   model_id              -- exact model string (e.g. "claude-sonnet-4-6")
--   session_id            -- Claude Code session UUID used to scan
--   step_started_at       -- ISO 8601 dispatch time (lower bound for scan)
--
-- All default to 0/"" so existing databases are not affected.
-- estimated_tokens is preserved for backward compat and is set to
-- input_tokens + cache_read_tokens + output_tokens when real data is present,
-- or the char/4 heuristic when it is not.
--
-- NOTE: FK constraints are intentionally omitted from this migration because
-- it is applied to BOTH project and central databases via
-- ConnectionManager._run_migrations().  Fresh project DBs get FKs from
-- PROJECT_SCHEMA_DDL directly.
ALTER TABLE step_results ADD COLUMN input_tokens          INTEGER NOT NULL DEFAULT 0;
ALTER TABLE step_results ADD COLUMN cache_read_tokens     INTEGER NOT NULL DEFAULT 0;
ALTER TABLE step_results ADD COLUMN cache_creation_tokens INTEGER NOT NULL DEFAULT 0;
ALTER TABLE step_results ADD COLUMN output_tokens         INTEGER NOT NULL DEFAULT 0;
ALTER TABLE step_results ADD COLUMN model_id              TEXT NOT NULL DEFAULT '';
ALTER TABLE step_results ADD COLUMN session_id            TEXT NOT NULL DEFAULT '';
ALTER TABLE step_results ADD COLUMN step_started_at       TEXT NOT NULL DEFAULT '';
""",
    14: """
-- v14: add role-based approval tables to central.db.
--
-- users       -- PMO user identity and role (creator, reviewer, approver, admin).
--                Lives in central.db only; cross-project visibility by design.
-- approval_log -- Immutable audit trail of every approve/reject/request_review
--                action taken via the PMO API.  task_id ties each entry back to
--                the originating execution; user_id ties it to a PMO user.
--
-- Both tables are CENTRAL-only (no project FK constraints) and use
-- CREATE TABLE IF NOT EXISTS so the migration is idempotent when applied
-- to a central.db that was already initialised from CENTRAL_SCHEMA_DDL.
--
-- NOTE: This migration is applied to BOTH project and central databases via
-- ConnectionManager._run_migrations().  Project databases will acquire these
-- tables too (harmless — they stay empty on the project side).  The
-- authoritative data always lives in central.db.
CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    email        TEXT NOT NULL DEFAULT '',
    role         TEXT NOT NULL DEFAULT 'creator',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

CREATE TABLE IF NOT EXISTS approval_log (
    log_id     TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    phase_id   TEXT NOT NULL DEFAULT '',
    user_id    TEXT NOT NULL DEFAULT 'local-user',
    action     TEXT NOT NULL,
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_approval_log_task ON approval_log(task_id);
CREATE INDEX IF NOT EXISTS idx_approval_log_user ON approval_log(user_id);
""",
    15: """
-- v15: multi-team orchestration — teams registry + sub_team/synthesis columns.
--
-- A Team is a stable identity for a coordinated group of agents.  Multiple
-- teams per leader are explicitly allowed: team_id is the identity, not
-- leader_agent (no UNIQUE constraint on leader_agent).
--
-- Nested teams: parent_team_id points to the enclosing team when a lead
-- carves out a sub-team via the team_dispatch tool.
--
-- team_members gains two JSON-blob columns so nested sub_teams and the
-- optional synthesis spec survive a storage round-trip.  Legacy rows
-- default to '[]' / '' — behavior is preserved for plans that never use
-- the new fields.
--
-- NOTE: FK constraints are intentionally omitted from this migration because
-- it is applied to BOTH project and central databases via
-- ConnectionManager._run_migrations().  Fresh project DBs get FKs from
-- PROJECT_SCHEMA_DDL directly.
CREATE TABLE IF NOT EXISTS teams (
    task_id          TEXT NOT NULL,
    team_id          TEXT NOT NULL,
    step_id          TEXT NOT NULL,
    parent_team_id   TEXT NOT NULL DEFAULT '',
    leader_agent     TEXT NOT NULL,
    leader_member_id TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (task_id, team_id)
);
CREATE INDEX IF NOT EXISTS idx_teams_leader ON teams(task_id, leader_agent);
CREATE INDEX IF NOT EXISTS idx_teams_parent ON teams(task_id, parent_team_id);

ALTER TABLE team_members ADD COLUMN sub_team  TEXT NOT NULL DEFAULT '[]';
ALTER TABLE team_members ADD COLUMN synthesis TEXT NOT NULL DEFAULT '';
""",
    16: """
-- v16: tenancy & cost attribution columns on usage_records / agent_usage /
-- compliance_log so the F0.2 v_usage_by_team / v_usage_by_org /
-- v_usage_by_cost_center views aggregate non-NULL groupings.
--
-- All five identity fields (org_id, team_id, user_id, spec_author_id,
-- cost_center) plus agent_type on agent_usage.  Defaults match the
-- TenancyContext fallback so legacy rows roll up under "default" rather
-- than disappearing into a NULL bucket.
ALTER TABLE usage_records ADD COLUMN org_id          TEXT NOT NULL DEFAULT 'default';
ALTER TABLE usage_records ADD COLUMN team_id         TEXT NOT NULL DEFAULT 'default';
ALTER TABLE usage_records ADD COLUMN user_id         TEXT NOT NULL DEFAULT 'local-user';
ALTER TABLE usage_records ADD COLUMN spec_author_id  TEXT NOT NULL DEFAULT '';
ALTER TABLE usage_records ADD COLUMN cost_center     TEXT NOT NULL DEFAULT '';

ALTER TABLE agent_usage ADD COLUMN agent_type      TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_usage ADD COLUMN org_id          TEXT NOT NULL DEFAULT 'default';
ALTER TABLE agent_usage ADD COLUMN team_id         TEXT NOT NULL DEFAULT 'default';
ALTER TABLE agent_usage ADD COLUMN user_id         TEXT NOT NULL DEFAULT 'local-user';
ALTER TABLE agent_usage ADD COLUMN spec_author_id  TEXT NOT NULL DEFAULT '';
ALTER TABLE agent_usage ADD COLUMN cost_center     TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_usage_team ON usage_records(team_id);
CREATE INDEX IF NOT EXISTS idx_usage_org  ON usage_records(org_id);
CREATE INDEX IF NOT EXISTS idx_usage_cc   ON usage_records(cost_center);

CREATE VIEW IF NOT EXISTS v_usage_by_team AS
SELECT ur.team_id,
       COUNT(DISTINCT ur.task_id) AS task_count,
       COALESCE(SUM(au.estimated_tokens), 0) AS total_tokens,
       COALESCE(SUM(au.duration_seconds), 0) AS total_duration_seconds
FROM usage_records ur
LEFT JOIN agent_usage au ON au.task_id = ur.task_id
GROUP BY ur.team_id;

CREATE VIEW IF NOT EXISTS v_usage_by_org AS
SELECT ur.org_id,
       COUNT(DISTINCT ur.task_id) AS task_count,
       COALESCE(SUM(au.estimated_tokens), 0) AS total_tokens,
       COALESCE(SUM(au.duration_seconds), 0) AS total_duration_seconds
FROM usage_records ur
LEFT JOIN agent_usage au ON au.task_id = ur.task_id
GROUP BY ur.org_id;

CREATE VIEW IF NOT EXISTS v_usage_by_cost_center AS
SELECT ur.cost_center,
       COUNT(DISTINCT ur.task_id) AS task_count,
       COALESCE(SUM(au.estimated_tokens), 0) AS total_tokens,
       COALESCE(SUM(au.duration_seconds), 0) AS total_duration_seconds
FROM usage_records ur
LEFT JOIN agent_usage au ON au.task_id = ur.task_id
GROUP BY ur.cost_center;
""",
}

# =====================================================================
# Per-project database schema (baton.db)
# =====================================================================

PROJECT_SCHEMA_DDL = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER NOT NULL
);

-- EXECUTIONS (replaces execution-state.json)
CREATE TABLE IF NOT EXISTS executions (
    task_id              TEXT PRIMARY KEY,
    status               TEXT NOT NULL DEFAULT 'running',
    current_phase        INTEGER NOT NULL DEFAULT 0,
    current_step_index   INTEGER NOT NULL DEFAULT 0,
    started_at           TEXT NOT NULL,
    completed_at         TEXT,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    pending_gaps         TEXT NOT NULL DEFAULT '[]',
    resolved_decisions   TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_executions_status ON executions(status);
CREATE INDEX IF NOT EXISTS idx_executions_started ON executions(started_at);

-- PLANS (replaces plan.json)
CREATE TABLE IF NOT EXISTS plans (
    task_id                    TEXT PRIMARY KEY,
    task_summary               TEXT NOT NULL,
    risk_level                 TEXT NOT NULL DEFAULT 'LOW',
    budget_tier                TEXT NOT NULL DEFAULT 'standard',
    execution_mode             TEXT NOT NULL DEFAULT 'phased',
    git_strategy               TEXT NOT NULL DEFAULT 'commit-per-agent',
    shared_context             TEXT NOT NULL DEFAULT '',
    pattern_source             TEXT,
    plan_markdown              TEXT NOT NULL DEFAULT '',
    created_at                 TEXT NOT NULL,
    explicit_knowledge_packs   TEXT NOT NULL DEFAULT '[]',
    explicit_knowledge_docs    TEXT NOT NULL DEFAULT '[]',
    intervention_level         TEXT NOT NULL DEFAULT 'low',
    task_type                  TEXT,
    classification_signals     TEXT,
    classification_confidence  REAL,
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);

-- PLAN_PHASES
CREATE TABLE IF NOT EXISTS plan_phases (
    task_id              TEXT NOT NULL,
    phase_id             INTEGER NOT NULL,
    name                 TEXT NOT NULL,
    approval_required    INTEGER NOT NULL DEFAULT 0,
    approval_description TEXT NOT NULL DEFAULT '',
    gate_type            TEXT,
    gate_command         TEXT,
    gate_description     TEXT,
    gate_fail_on         TEXT,
    PRIMARY KEY (task_id, phase_id),
    FOREIGN KEY (task_id) REFERENCES plans(task_id) ON DELETE CASCADE
);

-- PLAN_STEPS
CREATE TABLE IF NOT EXISTS plan_steps (
    task_id               TEXT NOT NULL,
    step_id               TEXT NOT NULL,
    phase_id              INTEGER NOT NULL,
    agent_name            TEXT NOT NULL,
    task_description      TEXT NOT NULL DEFAULT '',
    model                 TEXT NOT NULL DEFAULT 'sonnet',
    depends_on            TEXT NOT NULL DEFAULT '[]',
    deliverables          TEXT NOT NULL DEFAULT '[]',
    allowed_paths         TEXT NOT NULL DEFAULT '[]',
    blocked_paths         TEXT NOT NULL DEFAULT '[]',
    context_files         TEXT NOT NULL DEFAULT '[]',
    knowledge_attachments TEXT NOT NULL DEFAULT '[]',
    step_type             TEXT NOT NULL DEFAULT 'developing',
    command               TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (task_id, step_id),
    FOREIGN KEY (task_id, phase_id) REFERENCES plan_phases(task_id, phase_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_plan_steps_agent ON plan_steps(agent_name);
CREATE INDEX IF NOT EXISTS idx_plan_steps_phase ON plan_steps(task_id, phase_id);

-- TEAM_MEMBERS
CREATE TABLE IF NOT EXISTS team_members (
    task_id        TEXT NOT NULL,
    step_id        TEXT NOT NULL,
    member_id      TEXT NOT NULL,
    agent_name     TEXT NOT NULL,
    role           TEXT NOT NULL DEFAULT 'implementer',
    task_description TEXT NOT NULL DEFAULT '',
    model          TEXT NOT NULL DEFAULT 'sonnet',
    depends_on     TEXT NOT NULL DEFAULT '[]',
    deliverables   TEXT NOT NULL DEFAULT '[]',
    sub_team       TEXT NOT NULL DEFAULT '[]',
    synthesis      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (task_id, step_id, member_id),
    FOREIGN KEY (task_id, step_id) REFERENCES plan_steps(task_id, step_id) ON DELETE CASCADE
);

-- STEP_RESULTS
CREATE TABLE IF NOT EXISTS step_results (
    task_id                TEXT NOT NULL,
    step_id                TEXT NOT NULL,
    agent_name             TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'complete',
    outcome                TEXT NOT NULL DEFAULT '',
    files_changed          TEXT NOT NULL DEFAULT '[]',
    commit_hash            TEXT NOT NULL DEFAULT '',
    estimated_tokens       INTEGER NOT NULL DEFAULT 0,
    duration_seconds       REAL NOT NULL DEFAULT 0.0,
    retries                INTEGER NOT NULL DEFAULT 0,
    error                  TEXT NOT NULL DEFAULT '',
    completed_at           TEXT NOT NULL DEFAULT '',
    deviations             TEXT NOT NULL DEFAULT '[]',
    step_type              TEXT NOT NULL DEFAULT 'developing',
    updated_at             TEXT NOT NULL DEFAULT '',
    input_tokens           INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens          INTEGER NOT NULL DEFAULT 0,
    model_id               TEXT NOT NULL DEFAULT '',
    session_id             TEXT NOT NULL DEFAULT '',
    step_started_at        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (task_id, step_id),
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_step_results_status ON step_results(status);
CREATE INDEX IF NOT EXISTS idx_step_results_agent ON step_results(agent_name);

-- TEAM_STEP_RESULTS
CREATE TABLE IF NOT EXISTS team_step_results (
    task_id        TEXT NOT NULL,
    step_id        TEXT NOT NULL,
    member_id      TEXT NOT NULL,
    agent_name     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'complete',
    outcome        TEXT NOT NULL DEFAULT '',
    files_changed  TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (task_id, step_id, member_id),
    FOREIGN KEY (task_id, step_id) REFERENCES step_results(task_id, step_id) ON DELETE CASCADE
);

-- GATE_RESULTS
CREATE TABLE IF NOT EXISTS gate_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id          TEXT NOT NULL,
    phase_id         INTEGER NOT NULL,
    gate_type        TEXT NOT NULL,
    passed           INTEGER NOT NULL,
    output           TEXT NOT NULL DEFAULT '',
    checked_at       TEXT NOT NULL DEFAULT '',
    command          TEXT NOT NULL DEFAULT '',
    exit_code        INTEGER,
    decision_source  TEXT NOT NULL DEFAULT '',
    actor            TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_gate_results_task ON gate_results(task_id);

-- APPROVAL_RESULTS
CREATE TABLE IF NOT EXISTS approval_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id          TEXT NOT NULL,
    phase_id         INTEGER NOT NULL,
    result           TEXT NOT NULL,
    feedback         TEXT NOT NULL DEFAULT '',
    decided_at       TEXT NOT NULL DEFAULT '',
    decision_source  TEXT NOT NULL DEFAULT '',
    actor            TEXT NOT NULL DEFAULT '',
    rationale        TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);

-- AMENDMENTS
CREATE TABLE IF NOT EXISTS amendments (
    task_id           TEXT NOT NULL,
    amendment_id      TEXT NOT NULL,
    trigger           TEXT NOT NULL,
    trigger_phase_id  INTEGER NOT NULL,
    description       TEXT NOT NULL DEFAULT '',
    phases_added      TEXT NOT NULL DEFAULT '[]',
    steps_added       TEXT NOT NULL DEFAULT '[]',
    feedback          TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (task_id, amendment_id),
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);

-- EVENTS
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT NOT NULL,
    task_id     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    topic       TEXT NOT NULL,
    sequence    INTEGER NOT NULL DEFAULT 0,
    payload     TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (event_id)
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_topic ON events(topic);
CREATE INDEX IF NOT EXISTS idx_events_task_seq ON events(task_id, sequence);

-- USAGE_RECORDS
CREATE TABLE IF NOT EXISTS usage_records (
    task_id           TEXT PRIMARY KEY,
    timestamp         TEXT NOT NULL,
    total_agents      INTEGER NOT NULL DEFAULT 0,
    risk_level        TEXT NOT NULL DEFAULT 'LOW',
    sequencing_mode   TEXT NOT NULL DEFAULT 'phased_delivery',
    gates_passed      INTEGER NOT NULL DEFAULT 0,
    gates_failed      INTEGER NOT NULL DEFAULT 0,
    outcome           TEXT NOT NULL DEFAULT '',
    notes             TEXT NOT NULL DEFAULT '',
    -- Tenancy attribution (F0.2)
    org_id            TEXT NOT NULL DEFAULT 'default',
    team_id           TEXT NOT NULL DEFAULT 'default',
    user_id           TEXT NOT NULL DEFAULT 'local-user',
    spec_author_id    TEXT NOT NULL DEFAULT '',
    cost_center       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage_records(timestamp);
CREATE INDEX IF NOT EXISTS idx_usage_team ON usage_records(team_id);
CREATE INDEX IF NOT EXISTS idx_usage_org  ON usage_records(org_id);
CREATE INDEX IF NOT EXISTS idx_usage_cc   ON usage_records(cost_center);

-- AGENT_USAGE
CREATE TABLE IF NOT EXISTS agent_usage (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id            TEXT NOT NULL,
    agent_name         TEXT NOT NULL,
    model              TEXT NOT NULL DEFAULT 'sonnet',
    steps              INTEGER NOT NULL DEFAULT 1,
    retries            INTEGER NOT NULL DEFAULT 0,
    gate_results       TEXT NOT NULL DEFAULT '[]',
    estimated_tokens   INTEGER NOT NULL DEFAULT 0,
    duration_seconds   REAL NOT NULL DEFAULT 0.0,
    -- Tenancy attribution (F0.2)
    agent_type         TEXT NOT NULL DEFAULT '',
    org_id             TEXT NOT NULL DEFAULT 'default',
    team_id            TEXT NOT NULL DEFAULT 'default',
    user_id            TEXT NOT NULL DEFAULT 'local-user',
    spec_author_id     TEXT NOT NULL DEFAULT '',
    cost_center        TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES usage_records(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_agent_usage_task ON agent_usage(task_id);
CREATE INDEX IF NOT EXISTS idx_agent_usage_agent ON agent_usage(agent_name);

-- Tenancy aggregation views (F0.2)
CREATE VIEW IF NOT EXISTS v_usage_by_team AS
SELECT ur.team_id,
       COUNT(DISTINCT ur.task_id) AS task_count,
       COALESCE(SUM(au.estimated_tokens), 0) AS total_tokens,
       COALESCE(SUM(au.duration_seconds), 0) AS total_duration_seconds
FROM usage_records ur
LEFT JOIN agent_usage au ON au.task_id = ur.task_id
GROUP BY ur.team_id;

CREATE VIEW IF NOT EXISTS v_usage_by_org AS
SELECT ur.org_id,
       COUNT(DISTINCT ur.task_id) AS task_count,
       COALESCE(SUM(au.estimated_tokens), 0) AS total_tokens,
       COALESCE(SUM(au.duration_seconds), 0) AS total_duration_seconds
FROM usage_records ur
LEFT JOIN agent_usage au ON au.task_id = ur.task_id
GROUP BY ur.org_id;

CREATE VIEW IF NOT EXISTS v_usage_by_cost_center AS
SELECT ur.cost_center,
       COUNT(DISTINCT ur.task_id) AS task_count,
       COALESCE(SUM(au.estimated_tokens), 0) AS total_tokens,
       COALESCE(SUM(au.duration_seconds), 0) AS total_duration_seconds
FROM usage_records ur
LEFT JOIN agent_usage au ON au.task_id = ur.task_id
GROUP BY ur.cost_center;

-- TELEMETRY
CREATE TABLE IF NOT EXISTS telemetry (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT NOT NULL,
    agent_name   TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    tool_name    TEXT NOT NULL DEFAULT '',
    file_path    TEXT NOT NULL DEFAULT '',
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    details      TEXT NOT NULL DEFAULT '',
    task_id      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_telemetry_agent ON telemetry(agent_name);
CREATE INDEX IF NOT EXISTS idx_telemetry_type ON telemetry(event_type);
CREATE INDEX IF NOT EXISTS idx_telemetry_timestamp ON telemetry(timestamp);

-- RETROSPECTIVES
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
    markdown           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_retro_timestamp ON retrospectives(timestamp);

-- RETROSPECTIVE_OUTCOMES
CREATE TABLE IF NOT EXISTS retrospective_outcomes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    category     TEXT NOT NULL,
    agent_name   TEXT NOT NULL,
    worked_well  TEXT NOT NULL DEFAULT '',
    issues       TEXT NOT NULL DEFAULT '',
    root_cause   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES retrospectives(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_retro_outcomes_task ON retrospective_outcomes(task_id);

-- KNOWLEDGE_GAPS
CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL,
    description     TEXT NOT NULL,
    affected_agent  TEXT NOT NULL DEFAULT '',
    suggested_fix   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES retrospectives(task_id) ON DELETE CASCADE
);

-- ROSTER_RECOMMENDATIONS
CREATE TABLE IF NOT EXISTS roster_recommendations (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id  TEXT NOT NULL,
    action   TEXT NOT NULL,
    target   TEXT NOT NULL,
    reason   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES retrospectives(task_id) ON DELETE CASCADE
);

-- SEQUENCING_NOTES
CREATE TABLE IF NOT EXISTS sequencing_notes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    phase        TEXT NOT NULL,
    observation  TEXT NOT NULL,
    keep         INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (task_id) REFERENCES retrospectives(task_id) ON DELETE CASCADE
);

-- TRACES
CREATE TABLE IF NOT EXISTS traces (
    task_id        TEXT PRIMARY KEY,
    plan_snapshot  TEXT NOT NULL DEFAULT '{}',
    started_at     TEXT NOT NULL DEFAULT '',
    completed_at   TEXT,
    outcome        TEXT
);

-- TRACE_EVENTS
CREATE TABLE IF NOT EXISTS trace_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id          TEXT NOT NULL,
    timestamp        TEXT NOT NULL,
    event_type       TEXT NOT NULL,
    agent_name       TEXT,
    phase            INTEGER NOT NULL DEFAULT 0,
    step             INTEGER NOT NULL DEFAULT 0,
    details          TEXT NOT NULL DEFAULT '{}',
    duration_seconds REAL,
    FOREIGN KEY (task_id) REFERENCES traces(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_trace_events_task ON trace_events(task_id);

-- LEARNED_PATTERNS
CREATE TABLE IF NOT EXISTS learned_patterns (
    pattern_id           TEXT PRIMARY KEY,
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
    updated_at           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_patterns_type ON learned_patterns(task_type);

-- BUDGET_RECOMMENDATIONS
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

-- MISSION_LOG_ENTRIES
CREATE TABLE IF NOT EXISTS mission_log_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
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
    timestamp     TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_mission_log_task ON mission_log_entries(task_id);

-- SHARED_CONTEXT
CREATE TABLE IF NOT EXISTS shared_context (
    task_id        TEXT PRIMARY KEY,
    content        TEXT NOT NULL DEFAULT '',
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

-- CODEBASE_PROFILE (singleton)
CREATE TABLE IF NOT EXISTS codebase_profile (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    content    TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- ACTIVE_TASK (singleton)
CREATE TABLE IF NOT EXISTS active_task (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    task_id  TEXT NOT NULL
);

-- LEARNING_ISSUES (learning automation system)
CREATE TABLE IF NOT EXISTS learning_issues (
    issue_id          TEXT PRIMARY KEY,
    issue_type        TEXT NOT NULL,
    severity          TEXT NOT NULL DEFAULT 'medium',
    status            TEXT NOT NULL DEFAULT 'open',
    title             TEXT NOT NULL,
    target            TEXT NOT NULL,
    evidence          TEXT NOT NULL DEFAULT '[]',
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    occurrence_count  INTEGER NOT NULL DEFAULT 1,
    proposed_fix      TEXT,
    resolution        TEXT,
    resolution_type   TEXT,
    experiment_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_learning_issues_type
    ON learning_issues(issue_type);
CREATE INDEX IF NOT EXISTS idx_learning_issues_status
    ON learning_issues(status);
CREATE INDEX IF NOT EXISTS idx_learning_issues_target
    ON learning_issues(target);
CREATE UNIQUE INDEX IF NOT EXISTS idx_learning_issues_type_target_open
    ON learning_issues(issue_type, target)
    WHERE status NOT IN ('resolved', 'wontfix');

-- BEADS (Inspired by Steve Yegge's Beads agent memory system, beads-ai/beads-cli)
-- Discrete units of structured memory produced by agents during execution.
-- task_id is nullable: NULL means project-scoped bead (no execution parent).
-- Task-scoped beads (task_id IS NOT NULL) are validated by the FK constraint
-- under SQLite MATCH SIMPLE semantics (NULL bypasses the FK check).
CREATE TABLE IF NOT EXISTS beads (
    bead_id          TEXT PRIMARY KEY,
    task_id          TEXT,
    step_id          TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    bead_type        TEXT NOT NULL,
    content          TEXT NOT NULL DEFAULT '',
    confidence       TEXT NOT NULL DEFAULT 'medium',
    scope            TEXT NOT NULL DEFAULT 'step',
    tags             TEXT NOT NULL DEFAULT '[]',
    affected_files   TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       TEXT NOT NULL,
    closed_at        TEXT NOT NULL DEFAULT '',
    summary          TEXT NOT NULL DEFAULT '',
    links            TEXT NOT NULL DEFAULT '[]',
    source           TEXT NOT NULL DEFAULT 'agent-signal',
    token_estimate   INTEGER NOT NULL DEFAULT 0,
    quality_score    REAL    NOT NULL DEFAULT 0.0,
    retrieval_count  INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_beads_task ON beads(task_id);
CREATE INDEX IF NOT EXISTS idx_beads_agent ON beads(agent_name);
CREATE INDEX IF NOT EXISTS idx_beads_type ON beads(bead_type);
CREATE INDEX IF NOT EXISTS idx_beads_status ON beads(status);

-- BEAD_TAGS (normalised for efficient tag-based retrieval)
CREATE TABLE IF NOT EXISTS bead_tags (
    bead_id  TEXT NOT NULL,
    tag      TEXT NOT NULL,
    PRIMARY KEY (bead_id, tag),
    FOREIGN KEY (bead_id) REFERENCES beads(bead_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bead_tags_tag ON bead_tags(tag);

-- INTERACTION_TURNS (A4: persist multi-turn INTERACT exchanges)
-- Each row is one turn in a multi-turn agent interaction step.
-- Persisted incrementally — rows are inserted as turns arrive and
-- never deleted (append-only audit record).
CREATE TABLE IF NOT EXISTS interaction_turns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    step_id      TEXT NOT NULL,
    turn_number  INTEGER NOT NULL DEFAULT 0,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL DEFAULT '',
    timestamp    TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'human',
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_interaction_turns_task ON interaction_turns(task_id);
CREATE INDEX IF NOT EXISTS idx_interaction_turns_step ON interaction_turns(task_id, step_id);

-- FEEDBACK_RESPONSES (A4: persist FeedbackResult per question answer)
-- Each row records a user's answer to a single FeedbackQuestion.
CREATE TABLE IF NOT EXISTS feedback_responses (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id            TEXT NOT NULL,
    phase_id           INTEGER NOT NULL,
    question_id        TEXT NOT NULL,
    chosen_index       INTEGER NOT NULL DEFAULT 0,
    chosen_option      TEXT NOT NULL DEFAULT '',
    dispatched_step_id TEXT NOT NULL DEFAULT '',
    decided_at         TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_feedback_responses_task ON feedback_responses(task_id);

-- TEAMS (v15: multi-team orchestration registry)
-- A Team is a stable identity for a coordinated group of agents.  Multiple
-- teams may share the same leader_agent — team_id is the identity, not the
-- leader.  Nested sub-teams carved out by a lead via team_dispatch set
-- parent_team_id to the enclosing team.
CREATE TABLE IF NOT EXISTS teams (
    task_id          TEXT NOT NULL,
    team_id          TEXT NOT NULL,
    step_id          TEXT NOT NULL,
    parent_team_id   TEXT NOT NULL DEFAULT '',
    leader_agent     TEXT NOT NULL,
    leader_member_id TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (task_id, team_id),
    FOREIGN KEY (task_id) REFERENCES executions(task_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_teams_leader ON teams(task_id, leader_agent);
CREATE INDEX IF NOT EXISTS idx_teams_parent ON teams(task_id, parent_team_id);
"""

# =====================================================================
# Global PMO database schema (pmo.db)
# =====================================================================

PMO_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER NOT NULL
);

-- PROJECTS
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

-- PROGRAMS
CREATE TABLE IF NOT EXISTS programs (
    name TEXT PRIMARY KEY
);

-- SIGNALS
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

-- ARCHIVED_CARDS
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

-- FORGE_SESSIONS
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

-- PMO_METRICS
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
"""

# =====================================================================
# Central read-replica database schema (~/.baton/central.db)
# =====================================================================

CENTRAL_SCHEMA_DDL = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS _schema_version (
    version INTEGER NOT NULL
);

-- ================================================================
-- Sync infrastructure
-- ================================================================

-- Per-table watermarks tracking the highest rowid already synced
CREATE TABLE IF NOT EXISTS sync_watermarks (
    project_id   TEXT NOT NULL,
    table_name   TEXT NOT NULL,
    last_rowid   INTEGER NOT NULL DEFAULT 0,
    last_synced  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, table_name)
);

-- History of sync runs for observability
CREATE TABLE IF NOT EXISTS sync_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id    TEXT NOT NULL,
    started_at    TEXT NOT NULL DEFAULT '',
    completed_at  TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'running',
    rows_synced   INTEGER NOT NULL DEFAULT 0,
    tables_synced INTEGER NOT NULL DEFAULT 0,
    error         TEXT NOT NULL DEFAULT '',
    trigger       TEXT NOT NULL DEFAULT 'manual'
);
CREATE INDEX IF NOT EXISTS idx_sync_history_project ON sync_history(project_id);
CREATE INDEX IF NOT EXISTS idx_sync_history_started ON sync_history(started_at);

-- ================================================================
-- PMO tables (migrated from pmo.db — authoritative copy here)
-- ================================================================

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
CREATE INDEX IF NOT EXISTS idx_central_projects_program ON projects(program);

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
CREATE INDEX IF NOT EXISTS idx_central_signals_status ON signals(status);
CREATE INDEX IF NOT EXISTS idx_central_signals_severity ON signals(severity);
CREATE INDEX IF NOT EXISTS idx_central_signals_project ON signals(source_project_id);

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
CREATE INDEX IF NOT EXISTS idx_central_archive_project ON archived_cards(project_id);
CREATE INDEX IF NOT EXISTS idx_central_archive_program ON archived_cards(program);

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
CREATE INDEX IF NOT EXISTS idx_central_forge_project ON forge_sessions(project_id);
CREATE INDEX IF NOT EXISTS idx_central_forge_status ON forge_sessions(status);

CREATE TABLE IF NOT EXISTS pmo_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    program         TEXT NOT NULL DEFAULT '',
    metric_name     TEXT NOT NULL,
    metric_value    REAL NOT NULL DEFAULT 0.0,
    details         TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_central_pmo_metrics_ts ON pmo_metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_central_pmo_metrics_name ON pmo_metrics(metric_name);

-- ================================================================
-- External source tables
-- ================================================================

CREATE TABLE IF NOT EXISTS external_sources (
    source_id     TEXT PRIMARY KEY,
    source_type   TEXT NOT NULL,
    display_name  TEXT NOT NULL DEFAULT '',
    config        TEXT NOT NULL DEFAULT '{}',
    last_synced   TEXT NOT NULL DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_ext_sources_type ON external_sources(source_type);

CREATE TABLE IF NOT EXISTS external_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    item_type    TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    state        TEXT NOT NULL DEFAULT '',
    assigned_to  TEXT NOT NULL DEFAULT '',
    priority     TEXT NOT NULL DEFAULT '',
    parent_id    TEXT NOT NULL DEFAULT '',
    tags         TEXT NOT NULL DEFAULT '[]',
    url          TEXT NOT NULL DEFAULT '',
    raw_data     TEXT NOT NULL DEFAULT '{}',
    fetched_at   TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL DEFAULT '',
    UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_ext_items_source ON external_items(source_id);
CREATE INDEX IF NOT EXISTS idx_ext_items_type ON external_items(item_type);
CREATE INDEX IF NOT EXISTS idx_ext_items_state ON external_items(state);

CREATE TABLE IF NOT EXISTS external_mappings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id    TEXT NOT NULL,
    external_id  TEXT NOT NULL,
    project_id   TEXT NOT NULL,
    task_id      TEXT NOT NULL DEFAULT '',
    mapping_type TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT '',
    UNIQUE (source_id, external_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_ext_mappings_project ON external_mappings(project_id);
CREATE INDEX IF NOT EXISTS idx_ext_mappings_source ON external_mappings(source_id, external_id);

-- ================================================================
-- Synced project tables — mirror of baton.db tables with project_id
-- ================================================================

CREATE TABLE IF NOT EXISTS executions (
    project_id           TEXT NOT NULL,
    task_id              TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'running',
    current_phase        INTEGER NOT NULL DEFAULT 0,
    current_step_index   INTEGER NOT NULL DEFAULT 0,
    started_at           TEXT NOT NULL,
    completed_at         TEXT,
    created_at           TEXT NOT NULL DEFAULT '',
    updated_at           TEXT NOT NULL DEFAULT '',
    pending_gaps         TEXT NOT NULL DEFAULT '[]',
    resolved_decisions   TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (project_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_central_exec_status ON executions(status);
CREATE INDEX IF NOT EXISTS idx_central_exec_project ON executions(project_id);
CREATE INDEX IF NOT EXISTS idx_central_exec_started ON executions(started_at);

CREATE TABLE IF NOT EXISTS plans (
    project_id                 TEXT NOT NULL,
    task_id                    TEXT NOT NULL,
    task_summary               TEXT NOT NULL,
    risk_level                 TEXT NOT NULL DEFAULT 'LOW',
    budget_tier                TEXT NOT NULL DEFAULT 'standard',
    execution_mode             TEXT NOT NULL DEFAULT 'phased',
    git_strategy               TEXT NOT NULL DEFAULT 'commit-per-agent',
    shared_context             TEXT NOT NULL DEFAULT '',
    pattern_source             TEXT,
    plan_markdown              TEXT NOT NULL DEFAULT '',
    created_at                 TEXT NOT NULL,
    explicit_knowledge_packs   TEXT NOT NULL DEFAULT '[]',
    explicit_knowledge_docs    TEXT NOT NULL DEFAULT '[]',
    intervention_level         TEXT NOT NULL DEFAULT 'low',
    task_type                  TEXT,
    classification_signals     TEXT,
    classification_confidence  REAL,
    PRIMARY KEY (project_id, task_id)
);
CREATE INDEX IF NOT EXISTS idx_central_plans_risk ON plans(risk_level);
CREATE INDEX IF NOT EXISTS idx_central_plans_project ON plans(project_id);

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
    project_id            TEXT NOT NULL,
    task_id               TEXT NOT NULL,
    step_id               TEXT NOT NULL,
    phase_id              INTEGER NOT NULL,
    agent_name            TEXT NOT NULL,
    task_description      TEXT NOT NULL DEFAULT '',
    model                 TEXT NOT NULL DEFAULT 'sonnet',
    depends_on            TEXT NOT NULL DEFAULT '[]',
    deliverables          TEXT NOT NULL DEFAULT '[]',
    allowed_paths         TEXT NOT NULL DEFAULT '[]',
    blocked_paths         TEXT NOT NULL DEFAULT '[]',
    context_files         TEXT NOT NULL DEFAULT '[]',
    knowledge_attachments TEXT NOT NULL DEFAULT '[]',
    step_type             TEXT NOT NULL DEFAULT 'developing',
    command               TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, task_id, step_id)
);
CREATE INDEX IF NOT EXISTS idx_central_steps_agent ON plan_steps(agent_name);
CREATE INDEX IF NOT EXISTS idx_central_steps_project ON plan_steps(project_id);

CREATE TABLE IF NOT EXISTS team_members (
    project_id       TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    step_id          TEXT NOT NULL,
    member_id        TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    role             TEXT NOT NULL DEFAULT 'implementer',
    task_description TEXT NOT NULL DEFAULT '',
    model            TEXT NOT NULL DEFAULT 'sonnet',
    depends_on       TEXT NOT NULL DEFAULT '[]',
    deliverables     TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (project_id, task_id, step_id, member_id)
);

CREATE TABLE IF NOT EXISTS step_results (
    project_id             TEXT NOT NULL,
    task_id                TEXT NOT NULL,
    step_id                TEXT NOT NULL,
    agent_name             TEXT NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'complete',
    outcome                TEXT NOT NULL DEFAULT '',
    files_changed          TEXT NOT NULL DEFAULT '[]',
    commit_hash            TEXT NOT NULL DEFAULT '',
    estimated_tokens       INTEGER NOT NULL DEFAULT 0,
    duration_seconds       REAL NOT NULL DEFAULT 0.0,
    retries                INTEGER NOT NULL DEFAULT 0,
    error                  TEXT NOT NULL DEFAULT '',
    completed_at           TEXT NOT NULL DEFAULT '',
    deviations             TEXT NOT NULL DEFAULT '[]',
    step_type              TEXT NOT NULL DEFAULT 'developing',
    updated_at             TEXT NOT NULL DEFAULT '',
    input_tokens           INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_creation_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens          INTEGER NOT NULL DEFAULT 0,
    model_id               TEXT NOT NULL DEFAULT '',
    session_id             TEXT NOT NULL DEFAULT '',
    step_started_at        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, task_id, step_id)
);
CREATE INDEX IF NOT EXISTS idx_central_step_results_status ON step_results(status);
CREATE INDEX IF NOT EXISTS idx_central_step_results_agent ON step_results(agent_name);
CREATE INDEX IF NOT EXISTS idx_central_step_results_project ON step_results(project_id);

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
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    phase_id         INTEGER NOT NULL,
    gate_type        TEXT NOT NULL,
    passed           INTEGER NOT NULL,
    output           TEXT NOT NULL DEFAULT '',
    checked_at       TEXT NOT NULL DEFAULT '',
    command          TEXT NOT NULL DEFAULT '',
    exit_code        INTEGER,
    decision_source  TEXT NOT NULL DEFAULT '',
    actor            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_central_gate_task ON gate_results(project_id, task_id);

CREATE TABLE IF NOT EXISTS approval_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       TEXT NOT NULL,
    task_id          TEXT NOT NULL,
    phase_id         INTEGER NOT NULL,
    result           TEXT NOT NULL,
    feedback         TEXT NOT NULL DEFAULT '',
    decided_at       TEXT NOT NULL DEFAULT '',
    decision_source  TEXT NOT NULL DEFAULT '',
    actor            TEXT NOT NULL DEFAULT '',
    rationale        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_central_approval_task ON approval_results(project_id, task_id);

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
CREATE INDEX IF NOT EXISTS idx_central_events_task ON events(project_id, task_id);
CREATE INDEX IF NOT EXISTS idx_central_events_topic ON events(topic);

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
CREATE INDEX IF NOT EXISTS idx_central_usage_ts ON usage_records(timestamp);
CREATE INDEX IF NOT EXISTS idx_central_usage_project ON usage_records(project_id);

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
CREATE INDEX IF NOT EXISTS idx_central_agent_usage_task ON agent_usage(project_id, task_id);
CREATE INDEX IF NOT EXISTS idx_central_agent_usage_agent ON agent_usage(agent_name);

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
CREATE INDEX IF NOT EXISTS idx_central_telemetry_agent ON telemetry(agent_name);
CREATE INDEX IF NOT EXISTS idx_central_telemetry_type ON telemetry(event_type);
CREATE INDEX IF NOT EXISTS idx_central_telemetry_ts ON telemetry(timestamp);
CREATE INDEX IF NOT EXISTS idx_central_telemetry_project ON telemetry(project_id);

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
CREATE INDEX IF NOT EXISTS idx_central_retro_ts ON retrospectives(timestamp);
CREATE INDEX IF NOT EXISTS idx_central_retro_project ON retrospectives(project_id);

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
CREATE INDEX IF NOT EXISTS idx_central_retro_outcomes_task ON retrospective_outcomes(project_id, task_id);

CREATE TABLE IF NOT EXISTS knowledge_gaps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL,
    task_id         TEXT NOT NULL,
    description     TEXT NOT NULL,
    affected_agent  TEXT NOT NULL DEFAULT '',
    suggested_fix   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_central_knowledge_gaps_project ON knowledge_gaps(project_id);

CREATE TABLE IF NOT EXISTS roster_recommendations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    task_id    TEXT NOT NULL,
    action     TEXT NOT NULL,
    target     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT ''
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
CREATE INDEX IF NOT EXISTS idx_central_trace_events_task ON trace_events(project_id, task_id);

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
CREATE INDEX IF NOT EXISTS idx_central_patterns_type ON learned_patterns(task_type);

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
CREATE INDEX IF NOT EXISTS idx_central_mission_log_task ON mission_log_entries(project_id, task_id);
CREATE INDEX IF NOT EXISTS idx_central_mission_log_agent ON mission_log_entries(agent_name);

CREATE TABLE IF NOT EXISTS shared_context (
    project_id        TEXT NOT NULL,
    task_id           TEXT NOT NULL,
    content           TEXT NOT NULL DEFAULT '',
    task_title        TEXT NOT NULL DEFAULT '',
    stack             TEXT NOT NULL DEFAULT '',
    architecture      TEXT NOT NULL DEFAULT '',
    conventions       TEXT NOT NULL DEFAULT '',
    guardrails        TEXT NOT NULL DEFAULT '',
    agent_assignments TEXT NOT NULL DEFAULT '',
    domain_context    TEXT NOT NULL DEFAULT '',
    updated_at        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, task_id)
);

-- BEADS (mirror — Inspired by Steve Yegge's Beads agent memory system, beads-ai/beads-cli)
-- task_id is nullable: NULL = project-scoped bead with no execution parent.
CREATE TABLE IF NOT EXISTS beads (
    project_id       TEXT NOT NULL,
    bead_id          TEXT NOT NULL,
    task_id          TEXT,
    step_id          TEXT NOT NULL,
    agent_name       TEXT NOT NULL,
    bead_type        TEXT NOT NULL,
    content          TEXT NOT NULL DEFAULT '',
    confidence       TEXT NOT NULL DEFAULT 'medium',
    scope            TEXT NOT NULL DEFAULT 'step',
    tags             TEXT NOT NULL DEFAULT '[]',
    affected_files   TEXT NOT NULL DEFAULT '[]',
    status           TEXT NOT NULL DEFAULT 'open',
    created_at       TEXT NOT NULL,
    closed_at        TEXT NOT NULL DEFAULT '',
    summary          TEXT NOT NULL DEFAULT '',
    links            TEXT NOT NULL DEFAULT '[]',
    source           TEXT NOT NULL DEFAULT 'agent-signal',
    token_estimate   INTEGER NOT NULL DEFAULT 0,
    quality_score    REAL    NOT NULL DEFAULT 0.0,
    retrieval_count  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (project_id, bead_id)
);
CREATE INDEX IF NOT EXISTS idx_central_beads_task ON beads(project_id, task_id);
CREATE INDEX IF NOT EXISTS idx_central_beads_agent ON beads(agent_name);
CREATE INDEX IF NOT EXISTS idx_central_beads_type ON beads(bead_type);
CREATE INDEX IF NOT EXISTS idx_central_beads_status ON beads(status);

-- BEAD_TAGS (mirror — normalised for efficient tag-based retrieval)
CREATE TABLE IF NOT EXISTS bead_tags (
    project_id  TEXT NOT NULL,
    bead_id     TEXT NOT NULL,
    tag         TEXT NOT NULL,
    PRIMARY KEY (project_id, bead_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_central_bead_tags_tag ON bead_tags(tag);

-- LEARNING_ISSUES (mirror — learning automation system)
CREATE TABLE IF NOT EXISTS learning_issues (
    project_id        TEXT NOT NULL,
    issue_id          TEXT NOT NULL,
    issue_type        TEXT NOT NULL,
    severity          TEXT NOT NULL DEFAULT 'medium',
    status            TEXT NOT NULL DEFAULT 'open',
    title             TEXT NOT NULL,
    target            TEXT NOT NULL,
    evidence          TEXT NOT NULL DEFAULT '[]',
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    occurrence_count  INTEGER NOT NULL DEFAULT 1,
    proposed_fix      TEXT,
    resolution        TEXT,
    resolution_type   TEXT,
    experiment_id     TEXT,
    PRIMARY KEY (project_id, issue_id)
);
CREATE INDEX IF NOT EXISTS idx_central_learning_issues_type
    ON learning_issues(issue_type);
CREATE INDEX IF NOT EXISTS idx_central_learning_issues_status
    ON learning_issues(status);
CREATE INDEX IF NOT EXISTS idx_central_learning_issues_project
    ON learning_issues(project_id);

-- INTERACTION_TURNS (mirror — A4: multi-turn INTERACT exchanges)
CREATE TABLE IF NOT EXISTS interaction_turns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id   TEXT NOT NULL,
    task_id      TEXT NOT NULL,
    step_id      TEXT NOT NULL,
    turn_number  INTEGER NOT NULL DEFAULT 0,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL DEFAULT '',
    timestamp    TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'human'
);
CREATE INDEX IF NOT EXISTS idx_central_interaction_turns_task ON interaction_turns(project_id, task_id);
CREATE INDEX IF NOT EXISTS idx_central_interaction_turns_step ON interaction_turns(project_id, task_id, step_id);

-- FEEDBACK_RESPONSES (mirror — A4: FeedbackResult per question answer)
CREATE TABLE IF NOT EXISTS feedback_responses (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id         TEXT NOT NULL,
    task_id            TEXT NOT NULL,
    phase_id           INTEGER NOT NULL,
    question_id        TEXT NOT NULL,
    chosen_index       INTEGER NOT NULL DEFAULT 0,
    chosen_option      TEXT NOT NULL DEFAULT '',
    dispatched_step_id TEXT NOT NULL DEFAULT '',
    decided_at         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_central_feedback_responses_task ON feedback_responses(project_id, task_id);

-- ================================================================
-- Role-based approval tables (central.db only — cross-project)
-- ================================================================

-- PMO user identity and role assignment.
-- role: creator, reviewer, approver, admin
CREATE TABLE IF NOT EXISTS users (
    user_id      TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    email        TEXT NOT NULL DEFAULT '',
    role         TEXT NOT NULL DEFAULT 'creator',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_central_users_role ON users(role);

-- Immutable audit log of every approval action (approve, reject,
-- request_review, feedback) taken via the PMO API.
CREATE TABLE IF NOT EXISTS approval_log (
    log_id     TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    phase_id   TEXT NOT NULL DEFAULT '',
    user_id    TEXT NOT NULL DEFAULT 'local-user',
    action     TEXT NOT NULL,
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_central_approval_log_task ON approval_log(task_id);
CREATE INDEX IF NOT EXISTS idx_central_approval_log_user ON approval_log(user_id);

-- ================================================================
-- Cross-project analytics views
-- ================================================================

CREATE VIEW IF NOT EXISTS v_agent_reliability AS
SELECT
    sr.agent_name,
    COUNT(*)                                                      AS total_steps,
    SUM(CASE WHEN sr.status = 'complete' THEN 1 ELSE 0 END)      AS successful_steps,
    ROUND(
        1.0 * SUM(CASE WHEN sr.status = 'complete' THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0),
        4
    )                                                             AS success_rate,
    AVG(sr.retries)                                               AS avg_retries,
    AVG(sr.duration_seconds)                                      AS avg_duration_seconds,
    AVG(sr.estimated_tokens)                                      AS avg_tokens
FROM step_results sr
GROUP BY sr.agent_name;

CREATE VIEW IF NOT EXISTS v_cost_by_task_type AS
SELECT
    p.task_summary                         AS task_type_hint,
    COUNT(DISTINCT p.task_id)              AS task_count,
    SUM(au.estimated_tokens)               AS total_tokens,
    AVG(au.estimated_tokens)               AS avg_tokens_per_agent,
    SUM(au.duration_seconds)               AS total_duration_seconds,
    p.project_id
FROM plans p
JOIN agent_usage au ON au.project_id = p.project_id AND au.task_id = p.task_id
GROUP BY p.project_id, p.task_summary;

CREATE VIEW IF NOT EXISTS v_recurring_knowledge_gaps AS
SELECT
    kg.description,
    kg.affected_agent,
    COUNT(DISTINCT kg.project_id)          AS project_count,
    GROUP_CONCAT(DISTINCT kg.project_id)  AS projects
FROM knowledge_gaps kg
GROUP BY kg.description, kg.affected_agent
HAVING COUNT(DISTINCT kg.project_id) >= 2;

CREATE VIEW IF NOT EXISTS v_project_failure_rate AS
SELECT
    e.project_id,
    COUNT(*)                                                       AS total_executions,
    SUM(CASE WHEN e.status = 'failed' THEN 1 ELSE 0 END)         AS failed_executions,
    ROUND(
        1.0 * SUM(CASE WHEN e.status = 'failed' THEN 1 ELSE 0 END)
        / NULLIF(COUNT(*), 0),
        4
    )                                                              AS failure_rate
FROM executions e
GROUP BY e.project_id;

-- Cross-project discovery analytics (F10 — Bead Central Store Analytics).
-- Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
-- Query via: baton query "SELECT * FROM v_cross_project_discoveries LIMIT 20"
CREATE VIEW IF NOT EXISTS v_cross_project_discoveries AS
SELECT
    b.project_id,
    b.bead_id,
    b.bead_type,
    b.agent_name,
    b.content,
    b.confidence,
    b.tags,
    b.affected_files,
    b.created_at,
    b.quality_score,
    b.retrieval_count,
    p.task_summary      AS task_summary
FROM beads b
LEFT JOIN plans p
    ON p.project_id = b.project_id AND p.task_id = b.task_id
WHERE b.bead_type IN ('discovery', 'warning');

CREATE VIEW IF NOT EXISTS v_external_plan_mapping AS
SELECT
    em.source_id,
    em.external_id,
    ei.title                AS external_title,
    ei.state                AS external_state,
    es.source_type,
    es.display_name         AS source_name,
    em.project_id,
    em.task_id,
    em.mapping_type,
    p.task_summary          AS plan_summary
FROM external_mappings em
JOIN external_sources es ON es.source_id = em.source_id
LEFT JOIN external_items ei
    ON ei.source_id = em.source_id AND ei.external_id = em.external_id
LEFT JOIN plans p
    ON p.project_id = em.project_id AND p.task_id = em.task_id;
"""
