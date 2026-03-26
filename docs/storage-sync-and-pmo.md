# Storage, Sync, and PMO Systems

Comprehensive reference for Agent Baton's data management layer: per-project
SQLite databases, the central read-replica, federated sync, external source
adapters, cross-project queries, the PMO portfolio overlay, and the PMO UI
frontend.

---

## 1. Overview

Agent Baton uses a **federated storage architecture** where every project
maintains its own local SQLite database (`baton.db`) and a central
read-replica (`~/.baton/central.db`) aggregates data from all projects
for cross-project analytics and the PMO dashboard.

**Data flow:**

```
Project A: .claude/team-context/baton.db  ─┐
Project B: .claude/team-context/baton.db  ─┤── SyncEngine.push() ──> ~/.baton/central.db
Project C: .claude/team-context/baton.db  ─┘                              │
                                                                          ├── CentralStore (read-only queries)
External: Azure DevOps ── AdoAdapter ──────────────────────────────> external_* tables
                                                                          │
                                                                          ├── baton cquery (CLI)
                                                                          ├── PMO Scanner (board state)
                                                                          └── PMO UI (React frontend)
```

**Key principles:**

- **baton.db is the write target.** All execution data is written to the
  project's local database. Central.db is never written to by the execution
  engine.
- **central.db is a read-only replica** (with the exception of PMO tables
  and external source tables, which are written directly).
- **Sync is one-way and incremental.** SyncEngine pushes rows from project
  databases into central.db using rowid-based watermarks.
- **SQLite with WAL mode** is used everywhere for concurrent read access
  during execution.

---

## 2. Storage Architecture

### 2.1 Project-Level Storage (baton.db)

Each project stores its execution data in a single SQLite database at:

```
<project-root>/.claude/team-context/baton.db
```

The storage subsystem supports two backends behind a common
`StorageBackend` protocol:

| Backend | Class | Location | Status |
|---------|-------|----------|--------|
| **SQLite** | `SqliteStorage` | `core/storage/sqlite_backend.py` | Default for all new projects |
| **File** | `FileStorage` | `core/storage/file_backend.py` | Legacy, backward-compatible |

**Backend auto-detection** (`core/storage/__init__.py: detect_backend`):

1. If `baton.db` exists in the context root -> `sqlite`
2. If `execution-state.json` or `executions/` directory exists -> `file`
3. Default for new projects -> `sqlite`

The factory function `get_project_storage(context_root)` returns the
appropriate backend instance.

**StorageBackend protocol** (`core/storage/protocol.py`) defines the
complete interface:

- Execution state: `save_execution`, `load_execution`, `list_executions`,
  `delete_execution`
- Active task tracking: `set_active_task`, `get_active_task`
- Plans: `save_plan`, `load_plan`
- Results: `save_step_result`, `save_gate_result`, `save_approval_result`,
  `save_amendment`
- Events: `append_event`, `read_events`
- Usage/Telemetry: `log_usage`, `read_usage`, `log_telemetry`,
  `read_telemetry`
- Retrospectives: `save_retrospective`, `load_retrospective`,
  `list_retrospective_ids`
- Traces: `save_trace`, `load_trace`
- Patterns and Budget: `save_patterns`, `load_patterns`,
  `save_budget_recommendations`, `load_budget_recommendations`
- Mission Log: `append_mission_log`, `read_mission_log`
- Context: `save_context`, `read_context`, `save_profile`, `read_profile`

### 2.2 ConnectionManager

All SQLite access goes through `ConnectionManager`
(`core/storage/connection.py`), which provides:

- **Thread-safe connections:** One connection per thread, cached in
  thread-local storage and created lazily.
- **WAL journal mode:** Enables concurrent reads during execution.
- **Schema management:** Applies DDL on first connection, runs sequential
  migrations when the schema version changes.
- **Busy timeout:** 5-second busy timeout to handle lock contention.

```python
conn_mgr = ConnectionManager(db_path)
conn_mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
conn = conn_mgr.get_connection()  # thread-safe, lazily created
```

### 2.3 CentralStore (~/.baton/central.db)

`CentralStore` (`core/storage/central.py`) is the **read-only query
interface** for the central database at `~/.baton/central.db`.

- Initialized via `ConnectionManager` with `CENTRAL_SCHEMA_DDL`.
- Exposes pre-built analytics views: `agent_reliability()`,
  `cost_by_task_type()`, `recurring_knowledge_gaps()`,
  `project_failure_rates()`, `external_plan_mapping()`.
- Provides a generic `query(sql, params)` method with a **read-only guard**
  that rejects SQL statements starting with write keywords (INSERT, UPDATE,
  DELETE, DROP, CREATE, ALTER, REPLACE, ATTACH, DETACH).
- Has a controlled `execute(sql, params)` method that allows writes **only**
  to the external source tables: `external_sources`, `external_items`,
  `external_mappings`.

**PMO migration:** On first access via `get_pmo_central_store()`, the
function `_maybe_migrate_pmo()` automatically copies all rows from the
legacy `~/.baton/pmo.db` into central.db's PMO tables using INSERT OR
REPLACE. A marker file `~/.baton/.pmo-migrated` prevents re-running the
migration.

### 2.4 Storage Migration (JSON -> SQLite)

`StorageMigrator` (`core/storage/migrate.py`) handles importing legacy
JSON/JSONL flat files into baton.db:

```python
migrator = StorageMigrator(Path(".claude/team-context"))
counts = migrator.scan()       # preview what exists
imported = migrator.migrate()  # do the import
verified = migrator.verify()   # confirm source vs DB counts match
```

Import order respects foreign-key dependencies:

1. Executions (parent rows for most tables)
2. Events
3. Usage records
4. Telemetry
5. Retrospectives
6. Traces
7. Learned patterns
8. Budget recommendations
9. Active task

After migration, source files can optionally be moved to
`pre-sqlite-backup/`.

---

## 3. Sync Engine

### 3.1 SyncEngine Overview

`SyncEngine` (`core/storage/sync.py`) implements **one-way incremental
sync** from per-project `baton.db` to `~/.baton/central.db`.

```python
engine = SyncEngine()
result = engine.push("my-project", Path("/path/to/project/.claude/team-context/baton.db"))
```

**Design invariants:**

- baton.db is the sole write target; central.db is a read-only replica.
- Watermarks are stored in central.db (`sync_watermarks` table).
- AUTOINCREMENT `id` columns in project tables are dropped on insert so
  central generates its own sequence.
- Sync is idempotent: INSERT OR REPLACE for natural-PK tables, INSERT OR
  IGNORE for AUTOINCREMENT tables.

### 3.2 Sync Protocol

**Incremental sync** uses rowid-based watermarks:

1. For each syncable table, read the last-synced rowid from
   `sync_watermarks` for the (project_id, table_name) pair.
2. SELECT all rows from the project table where `rowid > watermark`.
3. INSERT into the central table with `project_id` prepended to the
   primary key.
4. Update the watermark to the highest rowid seen.
5. Record the sync run in `sync_history`.

**Column discovery is dynamic:** The engine reads column names from the
source table via `PRAGMA table_info` at sync time, so it handles new
columns without code changes.

**AUTOINCREMENT handling:** For tables with AUTOINCREMENT primary keys
(gate_results, approval_results, agent_usage, telemetry, etc.), the `id`
column is stripped on insert. Central.db assigns its own sequence. Dedup
relies on UNIQUE constraints on the natural key columns.

### 3.3 Syncable Tables

The following tables are synced, listed in FK dependency order:

| Table | PK Columns | Auto-increment? |
|-------|-----------|-----------------|
| `executions` | task_id | No |
| `usage_records` | task_id | No |
| `retrospectives` | task_id | No |
| `traces` | task_id | No |
| `learned_patterns` | pattern_id | No |
| `budget_recommendations` | task_type | No |
| `plans` | task_id | No |
| `plan_phases` | task_id, phase_id | No |
| `plan_steps` | task_id, step_id | No |
| `team_members` | task_id, step_id, member_id | No |
| `step_results` | task_id, step_id | No |
| `team_step_results` | task_id, step_id, member_id | No |
| `gate_results` | task_id, phase_id, gate_type, checked_at | Yes |
| `approval_results` | task_id, phase_id, result, decided_at | Yes |
| `amendments` | task_id, amendment_id | No |
| `events` | event_id | No |
| `agent_usage` | task_id, agent_name | Yes |
| `telemetry` | task_id, timestamp, agent_name, event_type | Yes |
| `retrospective_outcomes` | task_id, category, agent_name | Yes |
| `knowledge_gaps` | task_id, description | Yes |
| `roster_recommendations` | task_id, action, target | Yes |
| `sequencing_notes` | task_id, phase, observation | Yes |
| `trace_events` | task_id, timestamp, event_type | Yes |
| `mission_log_entries` | task_id, agent_name, timestamp | Yes |
| `shared_context` | task_id | No |

### 3.4 SyncResult

Each sync operation returns a `SyncResult`:

```python
@dataclass
class SyncResult:
    project_id: str
    tables_synced: int = 0
    rows_synced: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0
```

### 3.5 CLI Commands

**`baton sync`** (default): Sync the current project, auto-detected from
the working directory. Matches the cwd against registered project paths
in central.db.

```bash
baton sync                    # sync current project (incremental)
baton sync --all              # sync all registered projects
baton sync --project myproj   # sync a specific project by ID
baton sync --rebuild          # full rebuild: delete all central rows, re-sync
baton sync status             # show per-table watermarks for all projects
```

**`baton sync status`** output shows watermarks grouped by project:

```
Sync Watermarks (42 entries)

  Project: nds
    executions                      rowid=15       2024-03-20T14:30:00Z
    step_results                    rowid=48       2024-03-20T14:30:00Z
    ...
```

### 3.6 Auto-Sync

`auto_sync_current_project()` is a convenience function that resolves the
current project from central.db by matching the working directory against
registered project paths and runs an incremental push.

### 3.7 Rebuild

`engine.rebuild(project_id, db_path)` performs a full rebuild:

1. DELETE all central rows for the project (across all syncable tables).
2. Reset all watermarks for the project.
3. Run a fresh `push()` from scratch.

---

## 4. External Source Adapters

### 4.1 ExternalSourceAdapter Protocol

`ExternalSourceAdapter` (`core/storage/adapters/__init__.py`) is a
structural Protocol for bridging external work-tracking systems:

```python
@runtime_checkable
class ExternalSourceAdapter(Protocol):
    source_type: str  # "ado" | "jira" | "github" | "linear"

    def connect(self, config: dict) -> None: ...
    def fetch_items(self, item_types: list[str] | None = None,
                    since: str | None = None) -> list[ExternalItem]: ...
    def fetch_item(self, external_id: str) -> ExternalItem | None: ...
```

### 4.2 ExternalItem

Normalized work item from any source:

```python
@dataclass
class ExternalItem:
    source_id: str      # matches external_sources.source_id
    external_id: str    # unique ID in the source system
    item_type: str      # feature | bug | epic | story | task
    title: str
    description: str = ""
    state: str = ""
    assigned_to: str = ""
    priority: int = 0
    parent_id: str = ""
    tags: list[str] = field(default_factory=list)
    url: str = ""
    raw_data: dict | None = None
    updated_at: str = ""
```

### 4.3 AdapterRegistry

Class-level registry where adapters self-register on import:

```python
AdapterRegistry.register(AdoAdapter)  # called at module level in ado.py

# Usage:
cls = AdapterRegistry.get("ado")
adapter = cls()
adapter.connect(config)
items = adapter.fetch_items()
```

### 4.4 AdoAdapter (Azure DevOps)

`AdoAdapter` (`core/storage/adapters/ado.py`) fetches work items via the
Azure DevOps REST API (v7.0).

**Authentication:** Reads a Personal Access Token from the environment
variable named by `config["pat_env_var"]` (default `ADO_PAT`). The PAT
must have at least **Work Items (Read)** scope. No credentials are
persisted to disk.

**Configuration:**

| Key | Required | Description |
|-----|----------|-------------|
| `organization` | Yes | ADO organization name |
| `project` | Yes | ADO project name |
| `pat_env_var` | No | Env var name for the PAT (default: `ADO_PAT`) |
| `area_path` | No | Area path filter (e.g. `"MyProject\\Team"`) |

**Type mapping (ADO -> Baton):**

| ADO Work Item Type | Baton item_type |
|--------------------|-----------------|
| Feature | feature |
| Epic | epic |
| Bug | bug |
| User Story | story |
| Task | task |
| Issue | bug |
| Test Case | task |
| Product Backlog Item | story |
| Impediment | bug |
| (anything else) | task |

**Fetch flow:**

1. Execute a WIQL query to get matching work item IDs.
2. Fetch full field data in batches of 200 (ADO API limit).
3. Normalize each item to an `ExternalItem`.

### 4.5 Adding a New Adapter

To add support for a new source (e.g., Jira, GitHub, Linear):

1. Create `agent_baton/core/storage/adapters/<source>.py`
2. Implement the `ExternalSourceAdapter` protocol
3. Call `AdapterRegistry.register(YourAdapter)` at module level
4. Import the module in `source_cmd.py` so it triggers registration

```python
# agent_baton/core/storage/adapters/jira.py

class JiraAdapter:
    source_type = "jira"

    def connect(self, config: dict) -> None: ...
    def fetch_items(self, ...) -> list[ExternalItem]: ...
    def fetch_item(self, external_id: str) -> ExternalItem | None: ...

AdapterRegistry.register(JiraAdapter)
```

### 4.6 Source CLI Commands

```bash
# Register an external source
baton source add ado --name "My ADO" --org myorg --project MyProject --pat-env ADO_PAT

# List registered sources
baton source list

# Sync work items from a source into central.db
baton source sync ado-myorg-myproject
baton source sync --all

# Remove a source
baton source remove ado-myorg-myproject

# Map an external item to a baton plan
baton source map ado-myorg-myproject 12345 my-project task-abc --type implements
```

Mapping types: `implements`, `blocks`, `related`.

---

## 5. Cross-Project Query

### 5.1 QueryEngine (Project-Level)

`QueryEngine` (`core/storage/queries.py`) provides a typed, read-only
query interface for baton.db. Works with both per-project and central
databases.

**Pre-built queries:**

| Method | Description |
|--------|-------------|
| `agent_reliability(days=30)` | Agent success rates, tokens, retries over N days |
| `agent_history(agent_name, limit)` | Recent step results for an agent |
| `task_list(status, limit)` | Recent tasks with summary, risk, agents |
| `task_detail(task_id)` | Full nested task detail (plan, steps, gates) |
| `knowledge_gaps(min_frequency)` | Recurring knowledge gaps across tasks |
| `roster_recommendations()` | Agent roster change recommendations |
| `patterns()` | Learned patterns with confidence scores |
| `gate_stats()` | Gate pass/fail rates by type |
| `cost_by_task_type()` | Token costs grouped by sequencing mode |
| `cost_by_agent(days)` | Token costs grouped by agent |
| `current_context()` | Active task, current step, current agent |
| `agent_briefing(agent_name)` | Markdown briefing for dispatch prompts |
| `raw_query(sql, params)` | Ad-hoc read-only SQL (write-guarded) |

### 5.2 baton cquery (Cross-Project CLI)

`baton cquery` (`cli/commands/query_cmd.py`) runs SQL against central.db:

```bash
# Shortcut queries
baton cquery agents         # v_agent_reliability view
baton cquery costs          # v_cost_by_task_type view
baton cquery gaps           # v_recurring_knowledge_gaps view
baton cquery failures       # v_project_failure_rate view
baton cquery mapping        # v_external_plan_mapping view

# Ad-hoc SQL
baton cquery "SELECT * FROM executions LIMIT 10"
baton cquery "SELECT agent_name, COUNT(*) FROM step_results GROUP BY agent_name"

# Schema introspection
baton cquery --tables                  # list all tables and views
baton cquery --table executions        # describe a table's columns

# Output formats
baton cquery agents --format json
baton cquery costs --format csv
baton cquery agents --format table     # default
```

### 5.3 Analytics Views in central.db

| View | Description |
|------|-------------|
| `v_agent_reliability` | Agent success rate, avg retries, avg duration, avg tokens |
| `v_cost_by_task_type` | Token costs grouped by task summary and project |
| `v_recurring_knowledge_gaps` | Gaps appearing in 2+ projects |
| `v_project_failure_rate` | Per-project execution failure rates |
| `v_external_plan_mapping` | External items linked to baton plans |

### 5.4 Example Queries

**Agent reliability across all projects:**

```sql
SELECT agent_name, total_steps, success_rate, avg_tokens
  FROM v_agent_reliability
 WHERE total_steps >= 5
 ORDER BY success_rate DESC;
```

**Token costs by project:**

```sql
SELECT project_id,
       SUM(estimated_tokens) AS total_tokens,
       COUNT(*) AS total_steps
  FROM step_results
 GROUP BY project_id
 ORDER BY total_tokens DESC;
```

**Knowledge gaps appearing in multiple projects:**

```sql
SELECT description, affected_agent, project_count, projects
  FROM v_recurring_knowledge_gaps
 ORDER BY project_count DESC;
```

**Recent failed executions:**

```sql
SELECT project_id, task_id, status, started_at
  FROM executions
 WHERE status = 'failed'
 ORDER BY started_at DESC
 LIMIT 20;
```

---

## 6. PMO System

### 6.1 Data Models (agent_baton/models/pmo.py)

**PmoProject:** A project registered with the PMO.

| Field | Type | Description |
|-------|------|-------------|
| `project_id` | str | Slug identifier (e.g., "nds") |
| `name` | str | Human-readable project name |
| `path` | str | Absolute filesystem path |
| `program` | str | Program code (e.g., "RW", "NDS") |
| `color` | str | Optional display color |
| `description` | str | Project description |
| `registered_at` | str | ISO 8601 registration timestamp |
| `ado_project` | str | Azure DevOps project name (for linking) |

**PmoCard:** A Kanban card representing a plan's lifecycle state.

| Field | Type | Description |
|-------|------|-------------|
| `card_id` | str | task_id from MachinePlan |
| `project_id` | str | Parent project ID |
| `program` | str | Program code |
| `title` | str | Task summary |
| `column` | str | Kanban column (see below) |
| `risk_level` | str | LOW/MEDIUM/HIGH/CRITICAL |
| `priority` | int | 0=normal, 1=high, 2=critical |
| `agents` | list[str] | Agent names involved |
| `steps_completed` | int | Steps finished |
| `steps_total` | int | Total steps planned |
| `gates_passed` | int | Number of gates passed |
| `current_phase` | str | Current phase name |
| `error` | str | Last error message (if failed) |
| `external_id` | str | Linked external work item ID |

**Kanban columns** and status mapping:

| Column | Description | Mapped From (ExecutionState.status) |
|--------|-------------|-------------------------------------|
| `queued` | Plan ready, awaiting execution | None / default |
| `planning` | Claude decomposing scope | (future) |
| `executing` | Baton steps running | `running`, `failed` |
| `awaiting_human` | Paused for human input | `approval_pending` |
| `validating` | Test suites running | `gate_pending` |
| `deployed` | Complete | `complete` |

**PmoSignal:** A signal (bug, escalation, blocker).

| Field | Type | Description |
|-------|------|-------------|
| `signal_id` | str | Unique identifier |
| `signal_type` | str | bug, escalation, blocker |
| `title` | str | Short description |
| `description` | str | Full description |
| `source_project_id` | str | Originating project |
| `severity` | str | low, medium, high, critical |
| `status` | str | open, triaged, resolved |
| `forge_task_id` | str | If this spawned a Forge plan |

**ProgramHealth:** Aggregate metrics for a program.

| Field | Type | Description |
|-------|------|-------------|
| `program` | str | Program code |
| `total_plans` | int | Total plans across all projects |
| `active` | int | Currently executing |
| `completed` | int | Deployed successfully |
| `blocked` | int | Awaiting human input |
| `failed` | int | Failed with errors |
| `completion_pct` | float | Percentage complete |

### 6.2 PMO Store

Two store implementations exist:

**PmoStore** (`core/pmo/store.py`) — Legacy JSON/JSONL file store:
- Config: `~/.baton/pmo-config.json` (atomic write via tmp+rename)
- Archive: `~/.baton/pmo-archive.jsonl` (append-only)

**PmoSqliteStore** (`core/storage/pmo_sqlite.py`) — SQLite-backed store
(current default), backed by either `~/.baton/pmo.db` or
`~/.baton/central.db`:
- Projects: CRUD operations on the `projects` table
- Programs: `add_program()`, `list_programs()`
- Signals: `add_signal()`, `resolve_signal()`, `get_open_signals()`,
  `get_signal()`
- Archive: `archive_card()`, `read_archive(limit)`
- Forge Sessions: `create_forge_session()`, `complete_forge_session()`,
  `list_forge_sessions(status)`
- Metrics: `record_metric()`, `read_metrics(metric_name, limit)`
- Config compatibility: `load_config()`, `save_config()` for backward
  compatibility with code that uses PmoConfig

**Factory functions** (`core/storage/__init__.py`):

```python
# Preferred: PMO backed by central.db (auto-migrates from pmo.db)
store = get_pmo_central_store()

# Legacy: PMO backed by standalone pmo.db
store = get_pmo_storage()

# Central read-only query interface
central = get_central_storage()

# Sync engine
engine = get_sync_engine()
```

### 6.3 PMO Scanner

`PmoScanner` (`core/pmo/scanner.py`) scans registered projects and builds
Kanban board state.

**`scan_project(project)`:**

1. Detect storage backend (SQLite vs file) for the project.
2. Load all ExecutionState entries from the project's storage.
3. Convert each to a PmoCard using `status_to_column()` mapping.
4. Check for saved plans without execution state (queued cards).

**`scan_all()`:**

1. Load all registered projects from the PMO config.
2. Scan each project for active cards.
3. Include archived (deployed) cards from the archive store.
4. Deduplicate by card_id.

**`program_health()`:**

- Groups all cards by program.
- Computes per-program metrics: total plans, active, completed, blocked,
  failed, completion percentage.

### 6.4 Smart Forge

`ForgeSession` (`core/pmo/forge.py`) provides AI-driven task planning
with interactive refinement. It delegates to `IntelligentPlanner` for plan
generation and does NOT call the Anthropic API directly.

**`create_plan(description, program, project_id)`:**
- Looks up the project in the PMO store.
- Delegates to `IntelligentPlanner.create_plan()`.
- Returns a `MachinePlan` for review.

**`save_plan(plan, project)`:**
- Writes `plan.json` and `plan.md` to the project's team-context directory.
- Uses task-scoped directories under `executions/<task-id>/`.

**`generate_interview(plan, feedback)`:**
- Deterministic rule-based analysis (not an LLM call).
- Examines plan structure for ambiguities and missing context.
- Returns 3-5 targeted `InterviewQuestion` objects.
- Questions cover: testing strategy, risk acknowledgement, multi-agent
  coordination, gate definitions, scope/priority.

**`regenerate_plan(description, project_id, answers)`:**
- Builds an enriched description by appending interview answers.
- Re-invokes the planner with the enriched context.

**`signal_to_plan(signal_id, project_id)`:**
- Looks up a signal, generates a bug-fix plan from its description.
- Links the signal to the plan (sets `forge_task_id`).
- Updates signal status to "triaged".

### 6.5 PMO CLI Commands

```bash
# Start the PMO HTTP server (includes UI)
baton pmo serve [--port 8741] [--host 127.0.0.1]

# Print terminal Kanban board summary
baton pmo status

# Register a project with the PMO
baton pmo add --id nds --name "NDS Project" --path /path/to/project --program NDS [--color blue]

# Print program health bar summary
baton pmo health
```

**`baton pmo status` output:**

```
PMO Board -- 3 projects registered

  nds     ########..  80%   (2 active, 5 deployed, 1 blocked)
  atl     ####......  40%   (3 active, 2 deployed)
  com     ..........   0%   (1 queued)

Cards:
  executing   task-abc    'Optimize flight ops'      NDS     step 3/5
  deployed    task-def    'Root cause tooling'        ATL     complete
  awaiting    task-ghi    'Revenue management'        COM     waiting
```

---

## 7. PMO UI (Frontend)

### 7.1 Application Architecture

The PMO UI is a **React/Vite** single-page application located at
`pmo-ui/`.

| Technology | Version | Purpose |
|------------|---------|---------|
| React | ^18.3.1 | UI framework |
| Vite | ^6.0.5 | Build tool and dev server |
| TypeScript | ^5.7.2 | Type safety |

**No routing library** is used. The app manages two views (`kanban` and
`forge`) via React state in `App.tsx`.

**Dark theme** with a design-token system in `styles/tokens.ts`
(backgrounds `#060a11` -> `#222d42`, text `#f1f5f9` -> `#334155`, accent
`#3b82f6`).

### 7.2 Build and Development

```bash
cd pmo-ui

# Development (with hot reload, proxies /api to localhost:8741)
npm install
npm run dev          # starts Vite dev server on port 3000

# Production build
npm run build        # outputs to pmo-ui/dist/
npm run preview      # preview the production build
```

**Vite configuration** (`vite.config.ts`):
- `base: '/pmo/'` -- all assets served under the `/pmo/` prefix
- Dev server on port 3000 with `/api` proxy to `http://localhost:8741`
- Production build output to `pmo-ui/dist/`

**Static file serving:** When `baton pmo serve` starts the FastAPI server,
it mounts the built `pmo-ui/dist/` directory at `/pmo/` as static files
with `html=True` for SPA routing.

### 7.3 API Client

`api/client.ts` wraps all API calls to the backend:

```typescript
const BASE = '/api/v1/pmo';

export const api = {
  getBoard(): Promise<BoardResponse>,
  getBoardByProgram(program): Promise<BoardResponse>,
  getProjects(): Promise<PmoProject[]>,
  getHealth(): Promise<Record<string, ProgramHealth>>,
  forgePlan(body): Promise<ForgePlanResponse>,
  forgeApprove(body): Promise<ForgeApproveResponse>,
  forgeInterview(body): Promise<InterviewResponse>,
  forgeRegenerate(body): Promise<ForgePlanResponse>,
  getSignals(): Promise<PmoSignal[]>,
  createSignal(body): Promise<PmoSignal>,
  resolveSignal(id): Promise<PmoSignal>,
  searchAdo(q): Promise<AdoSearchResponse>,
};
```

### 7.4 Key Components

**`App.tsx`** -- Root component. Manages navigation between the Kanban
board and The Forge. Top nav bar with "Baton PMO" branding and tab
switching.

**`KanbanBoard.tsx`** -- Main board view with:
- `HealthBar` showing per-program completion progress bars.
- Program filter buttons (All / per-program).
- Signals toggle button and panel.
- Status indicators (awaiting count, executing count, last updated).
- Six Kanban columns (queued, planning, executing, awaiting_human,
  validating, deployed).
- "+ New Plan" button to open The Forge.
- Auto-refresh every 5 seconds via `usePmoBoard` hook.

**`KanbanCard.tsx`** -- Individual card display with:
- Program dot (color-coded by program name hash).
- Title, task ID, priority chip, risk level chip.
- Step progress pips (small dots showing completion).
- Current phase or error message with color-coded left border.
- Footer with project ID, agent names, and timestamp.
- Expandable detail section showing program, gates passed, and agent chips.

**`HealthBar.tsx`** -- Program health progress bars. One bar per program
with completion percentage, plan counts, and status breakdown (active,
done, blocked, failed). Color-coded by program name hash.

**`SignalsBar.tsx`** -- Expandable signals panel:
- Lists open signals with severity indicators (color-coded border).
- "Add Signal" form (inline).
- "Forge" button per signal (opens The Forge with signal context).
- "Resolve" button per signal.

**`ForgePanel.tsx`** -- AI-driven plan creation workflow with phases:
- **Intake**: Project selector, task type, priority, description textarea,
  ADO import combobox, "Generate Plan" button.
- **Generating**: Loading state while plan is created.
- **Preview**: `PlanEditor` with "Approve & Queue" and "Regenerate" buttons.
- **Regenerating**: `InterviewPanel` with refinement questions.
- **Saved**: Success confirmation with saved path, "New Plan" and "Back to
  Board" buttons.

**`PlanEditor.tsx`** -- Interactive plan editor:
- Stats bar (phases, steps, gates, risk level).
- Collapsible phase sections with step lists.
- Inline step editing (click to edit task description).
- Step reordering (up/down buttons).
- Add/remove steps and phases.
- Agent name chips per step.

**`InterviewPanel.tsx`** -- Refinement questions panel:
- Numbered question cards with context.
- Choice-based answers (button selection) or text input.
- "Skip" option for each question.
- Submit button with answer count.

**`PlanPreview.tsx`** -- Read-only plan display with stat tiles, summary
section, and phase/step breakdown.

**`AdoCombobox.tsx`** -- Azure DevOps work item search with debounced
typeahead (300ms). Currently uses mock data (placeholder endpoint).

### 7.5 Data Flow Hook

`usePmoBoard` (`hooks/usePmoBoard.ts`) manages board state:

- Polls `GET /api/v1/pmo/board` every 5 seconds (`POLL_INTERVAL_MS`).
- Returns `{ cards, health, loading, error, refresh, lastUpdated }`.
- Handles mount/unmount lifecycle to prevent state updates on unmounted
  components.
- Optionally filters by program via `getBoardByProgram`.

### 7.6 PMO API Endpoints

All endpoints are prefixed with `/api/v1` and defined in
`agent_baton/api/routes/pmo.py`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/pmo/board` | Full Kanban board (cards + health) |
| GET | `/pmo/board/{program}` | Board filtered by program |
| GET | `/pmo/projects` | List registered projects |
| POST | `/pmo/projects` | Register a project |
| DELETE | `/pmo/projects/{project_id}` | Unregister a project |
| GET | `/pmo/health` | Program health metrics |
| POST | `/pmo/forge/plan` | Create a plan via IntelligentPlanner |
| POST | `/pmo/forge/approve` | Save approved plan to project |
| POST | `/pmo/forge/interview` | Generate refinement questions |
| POST | `/pmo/forge/regenerate` | Re-generate plan with answers |
| GET | `/pmo/signals` | List open signals |
| POST | `/pmo/signals` | Create a signal |
| POST | `/pmo/signals/{id}/resolve` | Resolve a signal |
| POST | `/pmo/signals/{id}/forge` | Triage signal into a plan |
| GET | `/pmo/ado/search` | Search ADO items (placeholder) |

**Dependency injection:** The API server uses FastAPI's `Depends()` system
with singletons initialized in `api/deps.py`:
- `get_pmo_store()` -- PmoSqliteStore backed by central.db
- `get_pmo_scanner()` -- PmoScanner wired to the store
- `get_forge_session()` -- ForgeSession wired to the planner and store

---

## 8. Database Schema Reference

Schema version: **2** (defined in `core/storage/schema.py`).

Three schema definitions exist:

- `PROJECT_SCHEMA_DDL` -- per-project baton.db
- `PMO_SCHEMA_DDL` -- global pmo.db (legacy, migrated to central)
- `CENTRAL_SCHEMA_DDL` -- global central.db

### 8.1 Project Database (baton.db)

**`_schema_version`** -- Schema version tracking.

| Column | Type | Notes |
|--------|------|-------|
| version | INTEGER | Current schema version |

**`executions`** -- Replaces execution-state.json.

| Column | Type | Notes |
|--------|------|-------|
| task_id | TEXT | PK |
| status | TEXT | running, complete, failed, gate_pending, approval_pending |
| current_phase | INTEGER | Current phase index |
| current_step_index | INTEGER | Current step within phase |
| started_at | TEXT | ISO 8601 |
| completed_at | TEXT | Nullable |
| created_at | TEXT | Auto-set |
| updated_at | TEXT | Auto-set |
| pending_gaps | TEXT | JSON array of KnowledgeGapSignal (v2) |
| resolved_decisions | TEXT | JSON array of ResolvedDecision (v2) |

Indexes: `idx_executions_status`, `idx_executions_started`

**`plans`** -- Replaces plan.json.

| Column | Type | Notes |
|--------|------|-------|
| task_id | TEXT | PK, FK -> executions |
| task_summary | TEXT | Natural-language description |
| risk_level | TEXT | LOW, MEDIUM, HIGH, CRITICAL |
| budget_tier | TEXT | minimal, standard, premium |
| execution_mode | TEXT | phased, concurrent |
| git_strategy | TEXT | commit-per-agent, feature-branch |
| shared_context | TEXT | Context shared across agents |
| pattern_source | TEXT | Source pattern ID, nullable |
| plan_markdown | TEXT | Human-readable plan |
| created_at | TEXT | ISO 8601 |
| explicit_knowledge_packs | TEXT | JSON array (v2) |
| explicit_knowledge_docs | TEXT | JSON array (v2) |
| intervention_level | TEXT | low, medium, high (v2) |
| task_type | TEXT | Nullable (v2) |

**`plan_phases`**

| Column | Type | Notes |
|--------|------|-------|
| task_id | TEXT | PK (composite), FK -> plans |
| phase_id | INTEGER | PK (composite) |
| name | TEXT | Phase name |
| approval_required | INTEGER | Boolean (0/1) |
| approval_description | TEXT | Why approval is needed |
| gate_type | TEXT | pytest, custom, etc. |
| gate_command | TEXT | Shell command to run |
| gate_description | TEXT | Human description |
| gate_fail_on | TEXT | JSON array of failure patterns |

**`plan_steps`**

| Column | Type | Notes |
|--------|------|-------|
| task_id | TEXT | PK (composite), FK -> plan_phases |
| step_id | TEXT | PK (composite) |
| phase_id | INTEGER | FK -> plan_phases |
| agent_name | TEXT | Agent to dispatch |
| task_description | TEXT | What the agent should do |
| model | TEXT | sonnet, opus, haiku |
| depends_on | TEXT | JSON array of step_ids |
| deliverables | TEXT | JSON array |
| allowed_paths | TEXT | JSON array of file paths |
| blocked_paths | TEXT | JSON array of file paths |
| context_files | TEXT | JSON array of file paths |
| knowledge_attachments | TEXT | JSON array (v2) |

Indexes: `idx_plan_steps_agent`, `idx_plan_steps_phase`

**`team_members`** -- Team members within a step (for concurrent steps).

| Column | Type | Notes |
|--------|------|-------|
| task_id, step_id, member_id | TEXT | PK (composite) |
| agent_name | TEXT | |
| role | TEXT | implementer, reviewer, etc. |
| task_description | TEXT | |
| model | TEXT | |
| depends_on | TEXT | JSON array |
| deliverables | TEXT | JSON array |

**`step_results`**

| Column | Type | Notes |
|--------|------|-------|
| task_id | TEXT | PK (composite), FK -> executions |
| step_id | TEXT | PK (composite) |
| agent_name | TEXT | |
| status | TEXT | complete, failed |
| outcome | TEXT | Summary of what was done |
| files_changed | TEXT | JSON array |
| commit_hash | TEXT | |
| estimated_tokens | INTEGER | |
| duration_seconds | REAL | |
| retries | INTEGER | |
| error | TEXT | Error message if failed |
| completed_at | TEXT | |

Indexes: `idx_step_results_status`, `idx_step_results_agent`

**`team_step_results`** -- Per-member results within a team step.

| Column | Type | Notes |
|--------|------|-------|
| task_id, step_id, member_id | TEXT | PK (composite) |
| agent_name | TEXT | |
| status | TEXT | |
| outcome | TEXT | |
| files_changed | TEXT | JSON array |

**`gate_results`**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| task_id | TEXT | FK -> executions |
| phase_id | INTEGER | |
| gate_type | TEXT | pytest, custom |
| passed | INTEGER | Boolean (0/1) |
| output | TEXT | Gate output/log |
| checked_at | TEXT | |

**`approval_results`**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| task_id | TEXT | FK -> executions |
| phase_id | INTEGER | |
| result | TEXT | approve, reject, approve-with-feedback |
| feedback | TEXT | |
| decided_at | TEXT | |

**`amendments`** -- Runtime plan modifications.

| Column | Type | Notes |
|--------|------|-------|
| task_id, amendment_id | TEXT | PK (composite) |
| trigger | TEXT | What triggered the amendment |
| trigger_phase_id | INTEGER | |
| description | TEXT | |
| phases_added | TEXT | JSON array |
| steps_added | TEXT | JSON array |
| feedback | TEXT | |
| created_at | TEXT | |

**`events`**

| Column | Type | Notes |
|--------|------|-------|
| event_id | TEXT | PK |
| task_id | TEXT | |
| timestamp | TEXT | |
| topic | TEXT | |
| sequence | INTEGER | |
| payload | TEXT | JSON |

Indexes: `idx_events_task`, `idx_events_topic`, `idx_events_task_seq`

**`usage_records`**

| Column | Type | Notes |
|--------|------|-------|
| task_id | TEXT | PK |
| timestamp | TEXT | |
| total_agents | INTEGER | |
| risk_level | TEXT | |
| sequencing_mode | TEXT | phased_delivery, concurrent |
| gates_passed | INTEGER | |
| gates_failed | INTEGER | |
| outcome | TEXT | |
| notes | TEXT | |

**`agent_usage`**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| task_id | TEXT | FK -> usage_records |
| agent_name | TEXT | |
| model | TEXT | |
| steps | INTEGER | |
| retries | INTEGER | |
| gate_results | TEXT | JSON array |
| estimated_tokens | INTEGER | |
| duration_seconds | REAL | |

**`telemetry`**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| timestamp | TEXT | |
| agent_name | TEXT | |
| event_type | TEXT | |
| tool_name | TEXT | |
| file_path | TEXT | |
| duration_ms | INTEGER | |
| details | TEXT | |
| task_id | TEXT | |

**`retrospectives`**

| Column | Type | Notes |
|--------|------|-------|
| task_id | TEXT | PK |
| task_name | TEXT | |
| timestamp | TEXT | |
| agent_count | INTEGER | |
| retry_count | INTEGER | |
| gates_passed | INTEGER | |
| gates_failed | INTEGER | |
| risk_level | TEXT | |
| duration_estimate | TEXT | |
| estimated_tokens | INTEGER | |
| markdown | TEXT | Full retrospective markdown |

**`retrospective_outcomes`**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| task_id | TEXT | FK -> retrospectives |
| category | TEXT | worked, didnt_work |
| agent_name | TEXT | |
| worked_well | TEXT | |
| issues | TEXT | |
| root_cause | TEXT | |

**`knowledge_gaps`**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| task_id | TEXT | FK -> retrospectives |
| description | TEXT | |
| affected_agent | TEXT | |
| suggested_fix | TEXT | |

**`roster_recommendations`**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| task_id | TEXT | FK -> retrospectives |
| action | TEXT | add, remove, retrain |
| target | TEXT | Agent name |
| reason | TEXT | |

**`sequencing_notes`**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| task_id | TEXT | FK -> retrospectives |
| phase | TEXT | |
| observation | TEXT | |
| keep | INTEGER | Boolean (0/1) |

**`traces`**

| Column | Type | Notes |
|--------|------|-------|
| task_id | TEXT | PK |
| plan_snapshot | TEXT | JSON |
| started_at | TEXT | |
| completed_at | TEXT | Nullable |
| outcome | TEXT | Nullable |

**`trace_events`**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| task_id | TEXT | FK -> traces |
| timestamp | TEXT | |
| event_type | TEXT | |
| agent_name | TEXT | Nullable |
| phase | INTEGER | |
| step | INTEGER | |
| details | TEXT | JSON |
| duration_seconds | REAL | Nullable |

**`learned_patterns`**

| Column | Type | Notes |
|--------|------|-------|
| pattern_id | TEXT | PK |
| task_type | TEXT | |
| stack | TEXT | Nullable |
| recommended_template | TEXT | |
| recommended_agents | TEXT | JSON array |
| confidence | REAL | 0.0 - 1.0 |
| sample_size | INTEGER | |
| success_rate | REAL | 0.0 - 1.0 |
| avg_token_cost | INTEGER | |
| evidence | TEXT | JSON array |
| created_at | TEXT | |
| updated_at | TEXT | |

**`budget_recommendations`**

| Column | Type | Notes |
|--------|------|-------|
| task_type | TEXT | PK |
| current_tier | TEXT | |
| recommended_tier | TEXT | |
| reason | TEXT | |
| avg_tokens_used | INTEGER | |
| median_tokens_used | INTEGER | |
| p95_tokens_used | INTEGER | |
| sample_size | INTEGER | |
| confidence | REAL | |
| potential_savings | INTEGER | |

**`mission_log_entries`**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| task_id | TEXT | FK -> executions |
| agent_name | TEXT | |
| status | TEXT | |
| assignment | TEXT | |
| result | TEXT | |
| files | TEXT | JSON array |
| decisions | TEXT | JSON array |
| issues | TEXT | JSON array |
| handoff | TEXT | |
| commit_hash | TEXT | |
| failure_class | TEXT | Nullable |
| timestamp | TEXT | |

**`shared_context`**

| Column | Type | Notes |
|--------|------|-------|
| task_id | TEXT | PK, FK -> executions |
| content | TEXT | Full context blob |
| task_title | TEXT | |
| stack | TEXT | |
| architecture | TEXT | |
| conventions | TEXT | |
| guardrails | TEXT | |
| agent_assignments | TEXT | |
| domain_context | TEXT | |
| updated_at | TEXT | Auto-set |

**`codebase_profile`** -- Singleton table.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK CHECK (id = 1) |
| content | TEXT | Profile content |
| updated_at | TEXT | Auto-set |

**`active_task`** -- Singleton table.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK CHECK (id = 1) |
| task_id | TEXT | Active task ID |

### 8.2 PMO Database (pmo.db / central.db PMO tables)

**`projects`**

| Column | Type | Notes |
|--------|------|-------|
| project_id | TEXT | PK |
| name | TEXT | |
| path | TEXT | Absolute filesystem path |
| program | TEXT | |
| color | TEXT | |
| description | TEXT | |
| registered_at | TEXT | ISO 8601 |
| ado_project | TEXT | For ADO linking |

**`programs`**

| Column | Type | Notes |
|--------|------|-------|
| name | TEXT | PK |

**`signals`**

| Column | Type | Notes |
|--------|------|-------|
| signal_id | TEXT | PK |
| signal_type | TEXT | bug, escalation, blocker |
| title | TEXT | |
| description | TEXT | |
| source_project_id | TEXT | |
| severity | TEXT | low, medium, high, critical |
| status | TEXT | open, triaged, resolved |
| created_at | TEXT | |
| resolved_at | TEXT | |
| forge_task_id | TEXT | Linked plan task_id |

**`archived_cards`**

| Column | Type | Notes |
|--------|------|-------|
| card_id | TEXT | PK |
| project_id | TEXT | |
| program | TEXT | |
| title | TEXT | |
| column_name | TEXT | |
| risk_level | TEXT | |
| priority | INTEGER | |
| agents | TEXT | JSON array |
| steps_completed | INTEGER | |
| steps_total | INTEGER | |
| gates_passed | INTEGER | |
| current_phase | TEXT | |
| error | TEXT | |
| created_at | TEXT | |
| updated_at | TEXT | |
| external_id | TEXT | |

**`forge_sessions`**

| Column | Type | Notes |
|--------|------|-------|
| session_id | TEXT | PK |
| project_id | TEXT | |
| title | TEXT | |
| status | TEXT | active, completed |
| created_at | TEXT | |
| completed_at | TEXT | Nullable |
| task_id | TEXT | Resulting plan task_id |
| notes | TEXT | |

**`pmo_metrics`**

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| timestamp | TEXT | |
| program | TEXT | |
| metric_name | TEXT | |
| metric_value | REAL | |
| details | TEXT | JSON |

### 8.3 Central Database Sync Infrastructure

**`sync_watermarks`** -- Per-table watermarks tracking the highest synced
rowid.

| Column | Type | Notes |
|--------|------|-------|
| project_id | TEXT | PK (composite) |
| table_name | TEXT | PK (composite) |
| last_rowid | INTEGER | Highest synced rowid |
| last_synced | TEXT | ISO 8601 |

**`sync_history`** -- History of sync runs for observability.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| project_id | TEXT | |
| started_at | TEXT | |
| completed_at | TEXT | |
| status | TEXT | running, success, partial |
| rows_synced | INTEGER | |
| tables_synced | INTEGER | |
| error | TEXT | |
| trigger | TEXT | manual, auto |

### 8.4 Central Database External Source Tables

**`external_sources`** -- Registered external work-tracking connections.

| Column | Type | Notes |
|--------|------|-------|
| source_id | TEXT | PK |
| source_type | TEXT | ado, jira, github, linear |
| display_name | TEXT | Human-readable name |
| config | TEXT | JSON with connection params |
| last_synced | TEXT | |
| enabled | INTEGER | Boolean (0/1) |

**`external_items`** -- Cached work items from external sources.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| source_id | TEXT | FK -> external_sources |
| external_id | TEXT | ID in the source system |
| item_type | TEXT | feature, bug, epic, story, task |
| title | TEXT | |
| description | TEXT | |
| state | TEXT | Workflow state |
| assigned_to | TEXT | |
| priority | TEXT | |
| parent_id | TEXT | Parent item external_id |
| tags | TEXT | JSON array |
| url | TEXT | Link to source UI |
| raw_data | TEXT | Full JSON from source API |
| fetched_at | TEXT | |
| updated_at | TEXT | |

UNIQUE constraint: `(source_id, external_id)`

**`external_mappings`** -- Links between external items and baton plans.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | PK AUTOINCREMENT |
| source_id | TEXT | |
| external_id | TEXT | |
| project_id | TEXT | Baton project ID |
| task_id | TEXT | Baton task/execution ID |
| mapping_type | TEXT | implements, blocks, related |
| created_at | TEXT | |

UNIQUE constraint: `(source_id, external_id, task_id)`

### 8.5 Central Database Synced Tables

All synced project tables in central.db mirror the project-level schema
with `project_id` prepended to the primary key. The composite PK becomes
`(project_id, <original_pk_columns>)`.

### 8.6 Central Database Analytics Views

**`v_agent_reliability`:**
```sql
SELECT agent_name, total_steps, successful_steps, success_rate,
       avg_retries, avg_duration_seconds, avg_tokens
FROM step_results GROUP BY agent_name
```

**`v_cost_by_task_type`:**
```sql
SELECT task_summary AS task_type_hint, task_count, total_tokens,
       avg_tokens_per_agent, total_duration_seconds, project_id
FROM plans JOIN agent_usage GROUP BY project_id, task_summary
```

**`v_recurring_knowledge_gaps`:**
```sql
SELECT description, affected_agent, project_count, projects
FROM knowledge_gaps GROUP BY description, affected_agent
HAVING project_count >= 2
```

**`v_project_failure_rate`:**
```sql
SELECT project_id, total_executions, failed_executions, failure_rate
FROM executions GROUP BY project_id
```

**`v_external_plan_mapping`:**
```sql
SELECT source_id, external_id, external_title, external_state,
       source_type, source_name, project_id, task_id,
       mapping_type, plan_summary
FROM external_mappings JOIN external_sources
     LEFT JOIN external_items LEFT JOIN plans
```

### 8.7 Schema Migrations

Migrations are stored in `schema.py` as a dict keyed by version number:

```python
SCHEMA_VERSION = 2

MIGRATIONS: dict[int, str] = {
    2: """
    ALTER TABLE plans ADD COLUMN explicit_knowledge_packs TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE plans ADD COLUMN explicit_knowledge_docs   TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE plans ADD COLUMN intervention_level        TEXT NOT NULL DEFAULT 'low';
    ALTER TABLE plans ADD COLUMN task_type                 TEXT;
    ALTER TABLE plan_steps ADD COLUMN knowledge_attachments TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE executions ADD COLUMN pending_gaps         TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE executions ADD COLUMN resolved_decisions   TEXT NOT NULL DEFAULT '[]';
    """,
}
```

The `ConnectionManager._run_migrations()` method applies these
sequentially when the stored version is less than `SCHEMA_VERSION`.

---

## 9. Configuration

### 9.1 Storage Paths

| Path | Purpose |
|------|---------|
| `<project>/.claude/team-context/baton.db` | Per-project execution data |
| `<project>/.claude/team-context/` | Legacy JSON/JSONL files |
| `~/.baton/central.db` | Central read-replica + PMO + external sources |
| `~/.baton/pmo.db` | Legacy PMO database (migrated to central.db) |
| `~/.baton/pmo-config.json` | Legacy PMO config (JSON file store) |
| `~/.baton/pmo-archive.jsonl` | Legacy PMO archive |
| `~/.baton/.pmo-migrated` | Migration marker file |

### 9.2 PMO Server

| Setting | Default | CLI Flag |
|---------|---------|----------|
| Host | `127.0.0.1` | `--host` |
| Port | `8741` | `--port` |
| Auth token | None (disabled) | `token` param in `create_app()` |

### 9.3 Sync Configuration

Sync is currently manual (triggered via `baton sync`). There is no
automatic sync daemon. The `auto_sync_current_project()` function can be
called from hooks or scripts to sync after each execution completes.

### 9.4 External Source Configuration

External source configs are stored as JSON in `external_sources.config`:

```json
{
  "org": "my-org",
  "project": "MyProject",
  "pat_env": "ADO_PAT",
  "url": ""
}
```

Credentials are read from environment variables at runtime and never
persisted to disk.

---

## 10. Troubleshooting

### Storage Issues

**"central.db not found":**
Run `baton pmo add` to register at least one project, which creates
central.db.

**"Project DB not found":**
Ensure `.claude/team-context/baton.db` exists in the project directory.
If the project uses legacy file storage, run the migration:
```python
from agent_baton.core.storage.migrate import StorageMigrator
migrator = StorageMigrator(Path(".claude/team-context"))
migrator.migrate()
```

**"database is locked":**
SQLite WAL mode is enabled with a 5-second busy timeout. If locks persist,
check for zombie processes holding the database open. The
`ConnectionManager` uses `timeout=10.0` on connections and
`busy_timeout=5000` PRAGMA.

**Schema version mismatch:**
If `_schema_version` in the database is older than `SCHEMA_VERSION` in
code, migrations run automatically on next connection. If the database
has a newer version than the code, you may need to update agent-baton.

### Sync Issues

**"No projects registered in central.db":**
Register projects first with `baton pmo add`.

**"Could not detect current project":**
The auto-detect matches `os.getcwd()` against registered project paths.
Ensure you are inside a registered project directory.

**Partial sync (some tables fail):**
`SyncResult.errors` lists which tables failed. Common cause: schema
mismatch between project baton.db and central.db. Re-run with `--rebuild`
to force a full re-sync.

**Watermark reset:**
To force a full re-sync without losing other projects' data, use
`--rebuild` which deletes all central rows for the project and resets
watermarks:
```bash
baton sync --rebuild
```

### PMO Issues

**"PMO Board -- no projects registered":**
Run `baton pmo add --id ID --name NAME --path PATH --program PROG`.

**"Install API extras":**
The PMO server requires FastAPI and uvicorn:
```bash
pip install -e ".[api]"
```

**PMO UI shows "API 500":**
Check that the backend server is running (`baton pmo serve`) and that
central.db is accessible. The UI proxies API calls to `localhost:8741`
during development.

**Legacy pmo.db migration:**
If `~/.baton/pmo.db` exists and `~/.baton/.pmo-migrated` does not,
the migration runs automatically on first `get_pmo_central_store()` call.
To force re-migration, delete the marker file:
```bash
rm ~/.baton/.pmo-migrated
```

### External Source Issues

**"ADO PAT not found":**
Set the environment variable specified by `pat_env_var` (default
`ADO_PAT`):
```bash
export ADO_PAT="your-personal-access-token"
```

**"No adapter available":**
Only the ADO adapter is currently implemented. For other sources, see
section 4.5 on adding new adapters.

**"WIQL query failed":**
Check that the PAT has at least **Work Items (Read)** scope and that the
organization and project names match exactly.

---

## File Reference

| File | Purpose |
|------|---------|
| `agent_baton/core/storage/__init__.py` | Factory functions, backend detection |
| `agent_baton/core/storage/protocol.py` | StorageBackend protocol definition |
| `agent_baton/core/storage/sqlite_backend.py` | SqliteStorage (project baton.db) |
| `agent_baton/core/storage/file_backend.py` | FileStorage (legacy JSON/JSONL) |
| `agent_baton/core/storage/connection.py` | ConnectionManager (thread-safe SQLite) |
| `agent_baton/core/storage/schema.py` | All DDL schemas and migrations |
| `agent_baton/core/storage/central.py` | CentralStore (read-only central.db) |
| `agent_baton/core/storage/sync.py` | SyncEngine (project -> central sync) |
| `agent_baton/core/storage/pmo_sqlite.py` | PmoSqliteStore (PMO SQLite store) |
| `agent_baton/core/storage/migrate.py` | StorageMigrator (JSON -> SQLite) |
| `agent_baton/core/storage/queries.py` | QueryEngine (typed read-only queries) |
| `agent_baton/core/storage/adapters/__init__.py` | ExternalSourceAdapter protocol, registry |
| `agent_baton/core/storage/adapters/ado.py` | AdoAdapter (Azure DevOps) |
| `agent_baton/core/pmo/__init__.py` | PMO subsystem package |
| `agent_baton/core/pmo/store.py` | PmoStore (legacy JSON file store) |
| `agent_baton/core/pmo/scanner.py` | PmoScanner (project scanning, board state) |
| `agent_baton/core/pmo/forge.py` | ForgeSession (AI-driven plan creation) |
| `agent_baton/models/pmo.py` | PMO data models (PmoCard, PmoProject, etc.) |
| `agent_baton/cli/commands/sync_cmd.py` | `baton sync` CLI command |
| `agent_baton/cli/commands/query_cmd.py` | `baton cquery` CLI command |
| `agent_baton/cli/commands/source_cmd.py` | `baton source` CLI command |
| `agent_baton/cli/commands/pmo_cmd.py` | `baton pmo` CLI command |
| `agent_baton/api/routes/pmo.py` | PMO API endpoints |
| `agent_baton/api/server.py` | FastAPI app factory (mounts PMO UI) |
| `agent_baton/api/deps.py` | Dependency injection (PMO singletons) |
| `pmo-ui/src/App.tsx` | React root component |
| `pmo-ui/src/api/client.ts` | API client wrapper |
| `pmo-ui/src/api/types.ts` | TypeScript type definitions |
| `pmo-ui/src/hooks/usePmoBoard.ts` | Board polling hook |
| `pmo-ui/src/components/KanbanBoard.tsx` | Main board view |
| `pmo-ui/src/components/KanbanCard.tsx` | Card display |
| `pmo-ui/src/components/ForgePanel.tsx` | Plan creation workflow |
| `pmo-ui/src/components/PlanEditor.tsx` | Interactive plan editor |
| `pmo-ui/src/components/InterviewPanel.tsx` | Refinement questions |
| `pmo-ui/src/components/SignalsBar.tsx` | Signals panel |
| `pmo-ui/src/components/HealthBar.tsx` | Program health bars |
| `pmo-ui/src/components/AdoCombobox.tsx` | ADO search combobox |
| `pmo-ui/src/components/PlanPreview.tsx` | Read-only plan display |
| `pmo-ui/src/styles/tokens.ts` | Design tokens and column definitions |
| `pmo-ui/vite.config.ts` | Vite build configuration |
| `pmo-ui/package.json` | Frontend dependencies |
