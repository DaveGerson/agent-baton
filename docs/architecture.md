# Agent Baton Architecture

## 1. System Overview

Agent Baton is a multi-agent orchestration engine for Claude Code. It does not
replace Claude -- it serves it. The Python package implements a state machine
that plans, sequences, and tracks subagent execution. Claude reads the
orchestrator agent definition as part of its context, calls the `baton` CLI to
drive execution, and parses the CLI's structured output to decide what to do
next. All user-facing intelligence lives in the agent definitions; all
execution bookkeeping lives in the Python engine.

### Design Philosophy

1. **Separation of concerns.** Claude owns the intelligence (deciding what to
   do, understanding natural language, generating code). The engine owns the
   bookkeeping (state persistence, event tracking, plan sequencing, gate
   enforcement). Neither trespasses on the other's domain.

2. **Crash recovery by default.** Every state mutation is persisted to disk
   before the next action is returned. A Claude Code session can be killed
   mid-execution; `baton execute resume` reconstructs state from the last
   checkpoint and continues.

3. **Protocol-driven contracts.** The engine exposes two formally defined
   protocols -- `ExecutionDriver` (for runtime consumers) and `StorageBackend`
   (for persistence backends). Tests inject lightweight protocol-conforming
   objects without subclassing concrete implementations.

4. **Layered dependency order.** The package enforces a strict import hierarchy:
   `models` -> `core subsystems` -> `CLI/API`. No circular imports exist. Each
   layer depends only on layers below it.

5. **Graceful degradation.** Historical data (patterns, budget tuning,
   retrospectives) enriches plans when available. When no prior data exists,
   the planner falls back to sensible defaults. No subsystem gates execution
   on the availability of another.

### Three Interfaces

Agent Baton exposes three interfaces to the outside world:

```
+----------------+     +----------------+     +------------------+
|  baton CLI     |     |  HTTP API      |     |  PMO Frontend    |
|  (38 commands) |     |  (FastAPI)     |     |  (React/Vite)    |
+-------+--------+     +-------+--------+     +--------+---------+
        |                       |                       |
        +----------+------------+-----------+-----------+
                   |                        |
           +-------v--------+       +------v--------+
           |  Python Engine  |       |  central.db   |
           |  (agent_baton)  |       |  (read replica)|
           +-------+--------+       +------+--------+
                   |                        ^
           +-------v--------+       +------+--------+
           |  baton.db       +------>  SyncEngine    |
           |  (per-project)  |       |  (one-way)    |
           +----------------+       +---------------+
```

---

## Quick Navigation

| Question | Section |
|----------|---------|
| How does Claude talk to the engine? | [2. Interaction Chain](#2-interaction-chain) |
| What's in each package? | [3. Package Layout](#3-package-layout) |
| What depends on what? | [4. Dependency Graph](#4-dependency-graph) |
| Where is the execution state machine? | [6. Key Contracts](#6-key-contracts) |
| What are the interface contracts? | [6. Key Contracts](#6-key-contracts) |
| How does knowledge delivery work? | [9. Knowledge Delivery](#9-knowledge-delivery-subsystem) |
| How does cross-project sync work? | [10. Federated Sync](#10-federated-sync-architecture) |

---

## 2. Interaction Chain

```
Human User  <-->  Claude Code  <-->  baton CLI  <-->  Python Engine
             Layer A            Layer B            Layer C           Layer D
          (natural language) (structured text) (subprocess I/O) (state machine)
```

| Layer | Responsibility | Technology |
|-------|---------------|------------|
| A | Human intent | Natural language |
| B | Orchestration decisions | Claude reads agent definitions, parses CLI output |
| C | Control protocol | `baton` CLI commands, stdout structured text |
| D | Execution bookkeeping | Python package (`agent_baton`) |

Claude never imports the Python package directly. It reads text output from
`baton` commands and acts on it. This separation is load-bearing: the CLI
output format and command surface are the only contracts Claude depends on.
See `docs/invariants.md` for the three system invariants that formalize this.

---

## 3. Package Layout

```
agent_baton/
  __init__.py         Exports: ExecutionEngine, TaskWorker, MachinePlan,
  |                            AgentRegistry, AgentRouter, ContextManager,
  |                            IntelligentPlanner, AgentLauncher, DryRunLauncher,
  |                            PromptDispatcher, GateRunner, ExecutionDriver,
  |                            StatePersistence, WorkerSupervisor, EventBus
  |
  models/             Foundation layer. No internal deps.
  |  execution.py     MachinePlan, PlanPhase, PlanStep, PlanGate, TeamMember,
  |                   ExecutionState, StepResult, TeamStepResult, GateResult,
  |                   ApprovalResult, PlanAmendment, ExecutionAction, ActionType,
  |                   StepStatus, PhaseStatus
  |  enums.py         RiskLevel, TrustLevel, BudgetTier, ExecutionMode,
  |                   GateOutcome, FailureClass, GitStrategy, AgentCategory
  |  agent.py         AgentDefinition (parsed from .md frontmatter)
  |  events.py        Event (topic + payload + sequence)
  |  knowledge.py     KnowledgeDocument, KnowledgePack, KnowledgeAttachment,
  |                   KnowledgeGapSignal, KnowledgeGapRecord, ResolvedDecision
  |  pmo.py           PmoProject, PmoCard, PmoConfig, PmoSignal, ProgramHealth
  |  usage.py         AgentUsageRecord, TaskUsageRecord
  |  retrospective.py Retrospective, AgentOutcome, KnowledgeGap,
  |                   RosterRecommendation, SequencingNote
  |  trace.py         TaskTrace, TraceEvent
  |  decision.py      DecisionRequest, DecisionResolution
  |  pattern.py       LearnedPattern
  |  budget.py        BudgetRecommendation
  |  feedback.py      RetrospectiveFeedback
  |  context_profile.py  AgentContextProfile, TaskContextProfile
  |  registry.py      RegistryEntry, RegistryIndex
  |  escalation.py    Escalation
  |  improvement.py   (improvement models)
  |  parallel.py      (parallel execution models)
  |  mission_log.py   MissionLogEntry
  |  plan.py          MissionLogEntry (canonical location)
  |  reference.py     ReferenceDocument
  |  bead.py          Bead, BeadLink (structured agent memory,
  |                   inspired by beads-ai/beads-cli)
  |
  utils/
  |  frontmatter.py   parse_frontmatter() — YAML frontmatter extraction
  |
  core/
  |  __init__.py      3 canonical re-exports: AgentRegistry, AgentRouter,
  |                   ContextManager. Documents core vs peripheral layers.
  |
  |  engine/          ExecutionEngine, IntelligentPlanner, PromptDispatcher,
  |  |                GateRunner, StatePersistence, ExecutionDriver protocol,
  |  |                KnowledgeResolver, KnowledgeGap handler,
  |  |                BeadStore, bead_signal (structured memory)
  |  |
  |  runtime/         TaskWorker, WorkerSupervisor, StepScheduler,
  |  |                AgentLauncher protocol, DryRunLauncher, ClaudeCodeLauncher,
  |  |                DecisionManager, ExecutionContext factory, SignalHandler
  |  |
  |  orchestration/   AgentRegistry, AgentRouter, ContextManager,
  |  |                KnowledgeRegistry
  |  |
  |  storage/         StorageBackend protocol, SqliteStorage, FileStorage,
  |  |                SyncEngine, CentralStore, PmoSqliteStore,
  |  |                adapters/ (ExternalSourceAdapter, AdoAdapter)
  |  |
  |  events/          EventBus, EventPersistence, domain events, projections
  |  |
  |  observe/         TraceRecorder, TraceRenderer, UsageLogger,
  |  |                DashboardGenerator, RetrospectiveEngine,
  |  |                AgentTelemetry, ContextProfiler, DataArchiver
  |  |
  |  govern/          DataClassifier, ComplianceReportGenerator, PolicyEngine,
  |  |                EscalationManager, AgentValidator, SpecValidator
  |  |
  |  improve/         PerformanceScorer, PromptEvolutionEngine,
  |  |                AgentVersionControl
  |  |
  |  learn/           PatternLearner, BudgetTuner
  |  |
  |  pmo/             PmoStore, PmoScanner, ForgeSession
  |  |
  |  distribute/      PackageBuilder, PackageVerifier, RegistryClient
  |     experimental/ AsyncDispatcher, IncidentManager, ProjectTransfer
  |
  api/
  |  server.py        create_app() factory — FastAPI application
  |  deps.py          init_dependencies() — singleton DI container
  |  middleware/
  |  |  auth.py       TokenAuthMiddleware (Bearer token, exempt health paths)
  |  |  cors.py       configure_cors() (localhost permissive by default)
  |  routes/
  |  |  health.py     /health, /ready
  |  |  plans.py      Plan CRUD endpoints
  |  |  executions.py Execution state endpoints
  |  |  agents.py     Agent registry endpoints
  |  |  observe.py    Dashboard, trace, usage endpoints
  |  |  decisions.py  Decision request/resolve endpoints
  |  |  events.py     SSE event stream endpoint
  |  |  webhooks.py   Webhook subscription endpoints
  |  |  pmo.py        PMO board/project endpoints (12 endpoints)
  |  models/
  |  |  requests.py   Pydantic request bodies
  |  |  responses.py  Pydantic response schemas
  |  webhooks/
  |     dispatcher.py WebhookDispatcher (HMAC-signed, retry, auto-disable)
  |     registry.py   WebhookRegistry (persisted to webhooks.json)
  |     payloads.py   Webhook payload formatters
  |
  cli/
     main.py          Auto-discovers commands from commands/ subdirectories
     commands/
       execution/     execute.py, plan_cmd.py, status.py, daemon.py,
       |              async_cmd.py, decide.py
       observe/       dashboard.py, trace.py, usage.py, telemetry.py,
       |              context_profile.py, retro.py
       govern/        classify.py, compliance.py, policy.py, escalations.py,
       |              validate.py, spec_check.py, detect.py
       improve/       scores.py, evolve.py, patterns.py, budget.py,
       |              changelog.py
       distribute/    package.py, publish.py, pull.py, verify_package.py,
       |              install.py, transfer.py
       agents/        agents.py, route.py, events.py, incident.py
       pmo_cmd.py     pmo serve, pmo status, pmo add, pmo health
       sync_cmd.py    baton sync, baton sync --all, baton sync status
       query_cmd.py   baton query (cross-project SQL against central.db)
       source_cmd.py  baton source add/list/sync/remove/map
       serve.py       baton serve (standalone API server)

pmo-ui/              React/Vite PMO frontend (served at /pmo/)
  src/
    App.tsx           Main application component
    components/       UI components
    hooks/            React hooks
    api/              API client functions
    styles/           CSS/styling
agents/              Distributable agent definitions (19 .md files)
references/          Distributable reference docs (13 .md files)
templates/           CLAUDE.md + settings.json installed to target projects
scripts/             Install scripts (Linux + Windows)
tests/               Test suite (~4665 tests, pytest)
docs/                Architecture documentation
```

---

## 4. Layered Architecture

### Layer Diagram

```
+=====================================================================+
| Layer 1: MODELS (Foundation)                                         |
| agent_baton/models/ — 18 modules, dataclasses with to_dict/from_dict |
| No imports from core/. Pure data structures.                         |
+============+========================+================================+
             |                        |
             v                        v
+============+============+  +========+=============================+
| Layer 2a: PERIPHERAL    |  | Layer 2b: CORE EXECUTION             |
| observe/ govern/        |  | events/ orchestration/ engine/        |
| improve/ learn/         |  | storage/ pmo/                        |
| distribute/             |  |                                       |
+============+============+  +=====+==========+=====================+
             |                     |          |
             v                     v          v
+============+=========================+======+=====+
| Layer 3: RUNTIME                                   |
| runtime/ — TaskWorker, WorkerSupervisor,           |
|            StepScheduler, Launchers, SignalHandler  |
+============+=======================================+
             |
             v
+============+==============================================+
| Layer 4: INTERFACES                                       |
| cli/ — 38 commands in 6 groups + 4 top-level              |
| api/ — FastAPI app, 9 route modules, middleware, webhooks  |
| pmo-ui/ — React/Vite frontend                             |
+===========================================================+
```

### Dependency Rules

1. **Models depend on nothing** (within the package). The `models/` directory
   imports only from the Python standard library. All other layers import
   from models.

2. **Peripheral subsystems depend on models and on each other but never on
   engine/runtime.** `observe/`, `govern/`, `improve/`, `learn/` can be
   imported independently. The engine imports them for optional wiring (usage
   logging, telemetry, retrospectives).

3. **Core execution depends on models + peripherals.** `engine/` imports from
   `models/`, `events/`, `observe/`, `govern/`, `orchestration/`. This is the
   widest dependency set in the package.

4. **Runtime depends on engine.** `runtime/` imports `ExecutionDriver` (the
   protocol from `engine/protocols.py`) and `EventBus` from `events/`. It
   never imports the concrete `ExecutionEngine` except in `supervisor.py`
   (which constructs an engine for daemon mode).

5. **Interfaces depend on everything.** CLI commands and API routes import
   freely from any layer, but always through canonical sub-package paths
   (e.g., `from agent_baton.core.govern.classifier import DataClassifier`).
   There are no backward-compatibility shims (removed per ADR-02).

6. **Storage has no engine dependency.** `core/storage/` depends only on
   `models/` and `sqlite3`. The auto-sync hook in `cli/commands/execution/
   execute.py` imports `SyncEngine` lazily so the CLI remains functional
   even if `central.db` is inaccessible.

---

## 5. Core Subsystems

### 5.1 Engine (`core/engine/`)

The execution engine is the heart of Agent Baton. It implements a deterministic
state machine that advances through plan phases and steps, returning actions
for the driving session (Claude or daemon) to perform.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `executor.py` | `ExecutionEngine` | State machine (2211 LOC). Manages `ExecutionState`, determines next action, records step/gate/approval results, handles plan amendments, writes usage/telemetry/retrospective on completion. |
| `planner.py` | `IntelligentPlanner` | Data-driven plan creator. Accepts a task description and produces a `MachinePlan`. Consults `AgentRouter` for stack detection, `PatternLearner` for historical patterns, `BudgetTuner` for tier recommendations, `PolicyEngine` for guardrail evaluation, `KnowledgeResolver` for knowledge attachment. |
| `dispatcher.py` | `PromptDispatcher` | Stateless prompt assembler. Builds delegation prompts from `PlanStep` + shared context + knowledge attachments + resolved decisions. Builds team delegation prompts. Builds gate prompts. Generates path enforcement bash guards. |
| `gates.py` | `GateRunner` | Stateless gate evaluator. Builds `GATE` actions for the caller, evaluates gate command output (test, build, lint, spec, review types), provides default gate definitions. |
| `persistence.py` | `StatePersistence` | Atomic JSON file I/O for `ExecutionState`. Supports namespaced task directories (`executions/<task-id>/`) and legacy flat files. Manages the `active-task-id.txt` pointer. |
| `protocols.py` | `ExecutionDriver` | `typing.Protocol` (runtime-checkable) defining the 12-method interface between the async worker layer and the engine. |
| `knowledge_resolver.py` | `KnowledgeResolver` | 4-layer knowledge resolution pipeline: explicit -> agent-declared -> planner-matched (strict tag) -> planner-matched (TF-IDF relevance fallback). Per-step token budget governs inline vs. reference delivery decisions. |
| `knowledge_gap.py` | `parse_knowledge_gap()`, `determine_escalation()` | Parses `KNOWLEDGE_GAP` / `CONFIDENCE` / `TYPE` signals from agent output. Applies escalation matrix (gap type x risk level x intervention level) returning `auto-resolve`, `best-effort`, or `queue-for-gate`. |
| `bead_store.py` | `BeadStore` | SQLite-backed persistence for structured agent memory (schema v4). CRUD for `beads` and `bead_tags` tables with query filters, dependency-aware `ready()`, decay for archiving old beads. Inspired by Steve Yegge's Beads (beads-ai/beads-cli). |
| `bead_signal.py` | `parse_bead_signals()` | Parses `BEAD_DISCOVERY` / `BEAD_DECISION` / `BEAD_WARNING` signals from agent output. Called in `record_step_result()` after the knowledge gap block. Publishes `bead.created` events to the EventBus. |

#### ExecutionEngine Lifecycle

```
engine = ExecutionEngine(team_context_root, bus, task_id, storage)
action = engine.start(plan)          # -> ActionType.DISPATCH

loop:
    match action.action_type:
        case DISPATCH:
            engine.mark_dispatched(step_id, agent_name)
            # ... caller spawns agent ...
            engine.record_step_result(step_id, agent_name, status, outcome, ...)
            action = engine.next_action()
        case GATE:
            # ... caller runs gate command ...
            engine.record_gate_result(phase_id, passed, output)
            action = engine.next_action()
        case APPROVAL:
            # ... caller presents to user ...
            engine.record_approval_result(phase_id, result, feedback)
            action = engine.next_action()
        case WAIT:
            # parallel steps still in-flight
            action = engine.next_action()
        case COMPLETE:
            summary = engine.complete()
            break
        case FAILED:
            break
```

#### State Persistence Strategy

The engine supports two persistence backends:

1. **SQLite** (`SqliteStorage`): New default. Writes to `baton.db` via the
   `StorageBackend` protocol. Dual-writes to JSON files for backward
   compatibility during transition.

2. **File** (`FileStorage`): Legacy. Writes `execution-state.json` via
   `StatePersistence`. Still supported for projects that predate the SQLite
   backend.

State is saved after every mutation (step result, gate result, approval,
amendment). Writes are atomic: JSON uses tmp+rename, SQLite uses WAL mode.

#### ExecutionDriver Protocol

```python
class ExecutionDriver(Protocol):
    def start(self, plan: MachinePlan) -> ExecutionAction: ...
    def next_action(self) -> ExecutionAction: ...
    def next_actions(self) -> list[ExecutionAction]: ...
    def mark_dispatched(self, step_id: str, agent_name: str) -> None: ...
    def record_step_result(self, step_id, agent_name, status, ...) -> None: ...
    def record_gate_result(self, phase_id, passed, output) -> None: ...
    def record_approval_result(self, phase_id, result, feedback) -> None: ...
    def amend_plan(self, description, new_phases, ...) -> PlanAmendment: ...
    def record_team_member_result(self, step_id, member_id, ...) -> None: ...
    def complete(self) -> str: ...
    def status(self) -> dict: ...
    def resume(self) -> ExecutionAction: ...
```

`TaskWorker.__init__` accepts `engine: ExecutionDriver`, not the concrete
`ExecutionEngine`. Tests inject lightweight protocol-conforming objects
without subclassing (ADR-03).

---

### 5.2 Runtime (`core/runtime/`)

The runtime layer wraps the synchronous engine in an async execution loop,
manages concurrent agent launches, and provides daemon lifecycle support.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `worker.py` | `TaskWorker` | Async event loop driving a single task. Calls `engine.next_actions()` for parallel work, dispatches via `StepScheduler`, records results, publishes `step.*` events. Handles GATE and WAIT actions. |
| `supervisor.py` | `WorkerSupervisor` | Daemon lifecycle manager. PID file management, rotating log files, graceful shutdown via `SignalHandler`, status JSON snapshots. |
| `scheduler.py` | `StepScheduler` | Bounded-concurrency dispatcher using `asyncio.Semaphore`. Caps simultaneous agent launches at `max_concurrent` (default: 3). |
| `launcher.py` | `AgentLauncher` | Protocol for launching agents. `DryRunLauncher` logs dispatches and returns synthetic results for testing. |
| `claude_launcher.py` | `ClaudeCodeLauncher` | Real launcher that invokes the `claude` CLI as an async subprocess. Whitelist-based environment, exec-only (no shell), API key redaction in stderr. Configurable per-model timeouts. |
| `context.py` | `ExecutionContext` | Factory that wires `EventBus`, `ExecutionEngine`, and `EventPersistence` together correctly. Prevents duplicate event persistence subscriptions. |
| `decisions.py` | `DecisionManager` | Persists human decision requests to JSON files, writes companion `.md` summaries, publishes `human.decision_needed` / `human.decision_resolved` events. |
| `signals.py` | `SignalHandler` | POSIX signal handler (SIGTERM, SIGINT). Sets a cancellation event so the worker loop can drain in-flight agents before exiting. |

#### TaskWorker Execution Flow

```
TaskWorker(engine, launcher, bus, max_parallel=3)
    |
    +-- engine.next_actions() -> [action1, action2]    (parallel steps)
    |
    +-- StepScheduler.dispatch_batch(steps, launcher)
    |       |
    |       +-- Semaphore(3) limits concurrency
    |       +-- launcher.launch() per step (async)
    |       +-- Returns [LaunchResult, ...]
    |
    +-- engine.record_step_result() for each result
    |
    +-- bus.publish(step_completed / step_failed)       (step events)
    |
    +-- Loop until COMPLETE or FAILED
```

#### EventBus Ownership

Event topic ownership is divided between the engine and the worker (ADR-04):

| Owner | Topics |
|-------|--------|
| `ExecutionEngine` | `task.started`, `task.completed`, `phase.started`, `phase.completed`, `gate.passed`, `gate.failed` |
| `TaskWorker` | `step.dispatched`, `step.completed`, `step.failed` |

Each step transition produces exactly one event. `EventPersistence` writes
all events to a JSONL file via a bus subscription wired by
`ExecutionContext.build()`.

---

### 5.3 Orchestration (`core/orchestration/`)

Agent discovery, stack detection, routing, shared context management, and
knowledge pack indexing.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `registry.py` | `AgentRegistry` | Loads `.md` agent definitions from disk. Searches global (`~/.claude/agents/`) and project-level (`.claude/agents/`) directories, with project taking precedence. Supports flavored agents (e.g., `backend-engineer--python`). |
| `router.py` | `AgentRouter` | Stack detection (scans for `package.json`, `pyproject.toml`, etc.) and flavor routing. Maps detected `(language, framework)` pairs to agent flavor suffixes. |
| `context.py` | `ContextManager` | Manages `.claude/team-context/` files: `plan.md`, `plan.json`, `context.md`, `mission-log.md`, `codebase-profile.md`. Supports task-scoped directories for concurrent plans. |
| `knowledge_registry.py` | `KnowledgeRegistry` | Loads knowledge packs from `.claude/knowledge/` (project) and `~/.claude/knowledge/` (global). Indexes documents by tags and builds a TF-IDF index over metadata for relevance-based search. |

#### Agent Discovery

```
AgentRegistry.load_default_paths()
    |
    +-- ~/.claude/agents/*.md        (global agents)
    +-- .claude/agents/*.md          (project override, takes precedence)
    |
    +-- parse_frontmatter() -> AgentDefinition
            name, model, description, tools, knowledge_packs, instructions
```

#### Stack Detection → Flavor Routing

```
AgentRouter.detect_stack(project_root)
    |
    +-- Scan root + 2 levels of subdirectories
    +-- Match against PACKAGE_SIGNALS and FRAMEWORK_SIGNALS
    +-- Return StackProfile(language, framework, detected_files)

AgentRouter.resolve_agent("backend-engineer", profile)
    |
    +-- Look up (language, framework) in FLAVOR_MAP
    +-- Return "backend-engineer--python" if python detected
```

---

### 5.4 Storage (`core/storage/`)

Pluggable persistence backends, federated cross-project sync, and external
source adapters.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `__init__.py` | `get_project_storage()` | Factory: auto-detects SQLite or file backend. Also `get_pmo_central_store()`, `get_central_storage()`, `get_sync_engine()`. |
| `protocol.py` | `StorageBackend` | `typing.Protocol` (runtime-checkable). 20+ methods for CRUD of executions, plans, steps, gates, usage, retrospectives, traces, events, patterns, budget. |
| `sqlite_backend.py` | `SqliteStorage` | SQLite implementation of `StorageBackend`. Uses WAL mode, busy timeout, connection pooling. 29-table schema. |
| `file_backend.py` | `FileStorage` | Legacy JSON/JSONL implementation of `StorageBackend`. Delegates to `StatePersistence`, `UsageLogger`, `TraceRecorder`, etc. |
| `schema.py` | DDL constants | Complete schema for both project `baton.db` (29 tables) and `central.db` (project tables + PMO tables + sync tables + views). |
| `sync.py` | `SyncEngine` | Incremental one-way sync: project `baton.db` -> `~/.baton/central.db`. Watermark-based (row-level, not file-level). Idempotent. |
| `central.py` | `CentralStore` | Read-only query interface for `central.db`. Cross-project views and ad-hoc SQL. Includes `_maybe_migrate_pmo()` for one-time `pmo.db` migration. |
| `connection.py` | `ConnectionManager` | SQLite connection helper with WAL mode, busy timeout, PRAGMA tuning. |
| `queries.py` | Query builders | SQL query helpers for complex storage queries. |
| `migrate.py` | Migration functions | Schema migration and version management. |
| `pmo_sqlite.py` | `PmoSqliteStore` | SQLite storage for PMO data (projects, programs, signals, cards, metrics, forge sessions). Used for both legacy `pmo.db` and central.db. |
| `adapters/__init__.py` | `ExternalSourceAdapter` | Protocol for external work trackers (ADO, Jira, GitHub). `AdapterRegistry` maps type strings to adapter classes. |
| `adapters/ado.py` | `AdoAdapter` | Azure DevOps adapter. Reads PAT from env var. Self-registers on import. |

#### Federated Sync Architecture

```
  Project A (.claude/team-context/baton.db)
  Project B (.claude/team-context/baton.db)
  Project C (.claude/team-context/baton.db)
       |              |              |
       +-- baton sync -+-- auto on --+
       |               |   complete  |
       v               v             v
            ~/.baton/central.db
            +---------------------------+
            | project-scoped mirrors    |
            | of 27 syncable tables     |
            | + PMO tables (merged)     |
            | + sync_watermarks         |
            | + sync_history            |
            | + external_items          |
            | + external_mappings       |
            | + 5 cross-project views   |
            +---------------------------+
                        |
                        v
            PMO UI / baton query / baton pmo status
```

**Core invariants:**

- Per-project `baton.db` is the sole write target for execution. No execution
  code writes to `central.db`.
- `central.db` is a read replica populated exclusively by the sync mechanism.
- Sync is one-way: project -> central. Never the reverse.
- Auto-sync fires at `baton execute complete` inside a best-effort
  `try/except`. Sync failure never blocks execution completion.

#### Cross-Project Views in central.db

| View | Purpose |
|------|---------|
| `v_agent_reliability` | Agent success rate, retry count, token cost, project count |
| `v_cost_by_task_type` | Average tokens per task type across all projects |
| `v_recurring_knowledge_gaps` | Gaps appearing in 2+ projects |
| `v_project_failure_rate` | Failure rate per project |
| `v_external_plan_mapping` | External work items linked to baton plans |

---

### 5.5 Observe (`core/observe/`)

Observability subsystem: tracing, usage accounting, dashboards, retrospectives,
telemetry, context profiling, and data archival.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `trace.py` | `TraceRecorder` | Records structured task traces as JSON files under `traces/<task_id>.json`. Captures a DAG of timestamped events (agent starts, file reads/writes, completions). `TraceRenderer` formats traces as human-readable text. |
| `usage.py` | `UsageLogger` | Appends `TaskUsageRecord` entries to JSONL files. Each record captures agent names, models, token counts, retries, gate results, duration. |
| `telemetry.py` | `AgentTelemetry` | Logs real-time `TelemetryEvent` entries (tool calls, file operations, errors) to JSONL. Also subscribes to `EventBus` as a catch-all for domain events. |
| `dashboard.py` | `DashboardGenerator` | Produces a markdown usage dashboard from JSONL logs: cost trends, agent utilization, retry rates, model mix, risk distribution. |
| `retrospective.py` | `RetrospectiveEngine` | Generates structured retrospectives from usage records + qualitative input. Scans narrative for implicit knowledge gap signals. Persists as markdown and JSON. |
| `context_profiler.py` | `ContextProfiler` | Analyzes trace data to compute per-agent context efficiency metrics (files read vs. files written, redundancy across agents). |
| `archiver.py` | `DataArchiver` | Retention-based cleanup of old execution artifacts (traces, events, retrospectives, telemetry). Scans by age, supports archive or delete modes. |

---

### 5.6 Govern (`core/govern/`)

Policy enforcement, data classification, compliance reporting, agent validation,
spec validation, and escalation management.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `classifier.py` | `DataClassifier` | Auto-classifies task risk level (`LOW`/`MEDIUM`/`HIGH`/`CRITICAL`) and guardrail preset from task description keywords and file path analysis. Returns `ClassificationResult`. |
| `policy.py` | `PolicyEngine` | Evaluates agent assignments against `PolicySet` rules. Rule types: `path_block`, `path_allow`, `tool_restrict`, `require_agent`, `require_gate`. Returns `PolicyViolation` list. |
| `compliance.py` | `ComplianceReportGenerator` | Generates compliance reports from execution data. Checks agent assignments against policy sets, builds `ComplianceReport` with pass/fail entries. |
| `validator.py` | `AgentValidator` | Validates agent definition files: checks required frontmatter fields, model values, permission modes. |
| `spec_validator.py` | `SpecValidator` | Validates agent output against declared specifications. Runs callable check functions and returns `SpecValidationResult`. |
| `escalation.py` | `EscalationManager` | Manages escalation records (risk-based, policy violation, gate failure). Persists and queries escalation history. |

---

### 5.7 Improve (`core/improve/`)

Agent performance scoring, prompt evolution proposals, and version control.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `scoring.py` | `PerformanceScorer` | Computes per-agent `AgentScorecard` from usage and retrospective data. Metrics: times used, first-pass rate, retry rate, gate pass rate, token consumption, positive/negative mentions, knowledge gaps cited. Health rating: `strong`, `adequate`, `needs-improvement`, `unused`. |
| `evolution.py` | `PromptEvolutionEngine` | Generates `EvolutionProposal` objects with data-driven suggestions for improving agent prompts. Consults scorecards and retrospectives to identify issues and propose changes. |
| `vcs.py` | `AgentVersionControl` | Tracks changes to agent definition files with timestamped backups (`.bak` files) and a `changelog.md`. Supports backup, restore, and changelog append. |

---

### 5.8 Learn (`core/learn/`)

Pattern learning and budget optimization from historical execution data.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `pattern_learner.py` | `PatternLearner` | Derives recurring orchestration patterns from usage logs. Groups `TaskUsageRecord` entries by sequencing mode, computes per-group statistics (token usage, retry rates, gate pass rates). Surfaces groups meeting minimum sample size (5+) and confidence threshold (0.7) as `LearnedPattern` objects. Persists to `learned-patterns.json`. Also indexes knowledge gap records by `(agent_name, task_type)` for gap-suggested attachments. |
| `budget_tuner.py` | `BudgetTuner` | Analyzes historical token usage and recommends budget tier changes. Groups tasks by sequencing mode, computes median token usage per group, recommends upgrade/downgrade between `lean` (0-50K), `standard` (50K-500K), and `full` (500K+) tiers. Minimum 3 records per group before generating recommendations. |

---

### 5.9 Events (`core/events/`)

In-process event bus, domain event factories, append-only persistence, and
materialized view projections.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `bus.py` | `EventBus` | In-process pub/sub with `fnmatch`-style glob topic routing. Synchronous: handlers called inline during `publish()`. Auto-assigns monotonic sequence numbers per `task_id`. Full in-memory history. |
| `events.py` | Factory functions | `step_dispatched()`, `step_completed()`, `step_failed()`, `task_started()`, `task_completed()`, `phase_started()`, `phase_completed()`, `gate_passed()`, `gate_failed()`, `human_decision_needed()`, `human_decision_resolved()`. Each returns an `Event` with the correct topic and payload. |
| `persistence.py` | `EventPersistence` | Append-only JSONL event log per task. Independent of `EventBus` -- can be wired as a subscriber or used standalone. Supports replay with sequence and topic filters. |
| `projections.py` | `project_task_view()` | Materializes a `TaskView` (with `PhaseView` and `StepView` children) from a list of events. Read-only, never mutates events. Used by dashboard and status commands. |

#### Event Model

```python
@dataclass
class Event:
    event_id: str       # uuid hex (12 chars)
    timestamp: str      # UTC ISO 8601
    topic: str          # e.g., "step.completed", "gate.passed"
    task_id: str        # links event to an execution
    sequence: int       # monotonic per task_id (auto-assigned by bus)
    payload: dict       # event-type-specific data
```

---

### 5.10 PMO (`core/pmo/`)

Portfolio management overlay that provides a Kanban board view across
projects and a consultative plan creation workflow.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `store.py` | `PmoStore` | Read/write PMO config (`pmo-config.json`) and completed-plan archive (`pmo-archive.jsonl`). Atomic writes via tmp+rename. |
| `scanner.py` | `PmoScanner` | Scans registered projects and builds Kanban board state. Reads execution state from each project's storage backend, maps `ExecutionState.status` to PMO columns (`queued`, `planning`, `executing`, `awaiting_human`, `validating`, `deployed`). |
| `forge.py` | `ForgeSession` | Consultative plan creation. Delegates to `IntelligentPlanner.create_plan()` with project-scoped context. No direct Anthropic API calls. |

PMO data now lives in `central.db` (not a separate `pmo.db`). First-run
migration from legacy `pmo.db` is handled by `get_pmo_central_store()`.

---

### 5.11 Distribute (`core/distribute/`)

Packaging, verification, registry management, and experimental features.

#### Production Modules

| Module | Class | Role |
|--------|-------|------|
| `sharing.py` | `PackageBuilder` | Creates distributable `.tar.gz` archives with `manifest.json`, agent definitions, references, knowledge packs. Path traversal protection on extraction. |
| `packager.py` | `PackageVerifier` | Validates package archives: checksum verification, dependency tracking, structural checks. Returns `PackageValidationResult` with `valid`, `errors`, `warnings`, `checksums`. |
| `registry_client.py` | `RegistryClient` | Manages a local registry directory (typically a git repo) with an `index.json` and versioned `packages/` subdirectories. Handles publish and pull operations. |

#### Experimental Modules (`experimental/`)

| Module | Class | Role |
|--------|-------|------|
| `async_dispatch.py` | `AsyncDispatcher` | Scaffolding for async task dispatch. Not exercised in production. |
| `incident.py` | `IncidentManager` | Incident response templates and phase tracking. Not exercised in production. |
| `transfer.py` | `ProjectTransfer` | Cross-project knowledge and configuration transfer. Not exercised in production. |

---

## 6. Data Flow

### 6.1 Planning Flow

```
User: "baton plan 'add auth middleware' --save --explain"
                          |
                          v
           +-----------------------------+
           |     IntelligentPlanner       |
           +-----------------------------+
           |                             |
  1. Parse task description              |
  2. AgentRouter.detect_stack()          |
  3. PatternLearner.find_pattern()       |
  4. BudgetTuner.recommend()             |
  5. DataClassifier.classify()           |
  6. PolicyEngine.evaluate()             |
  7. AgentRouter.resolve_agents()        |
  7.5 KnowledgeResolver.resolve()        |
  8. Sequence into PlanPhase/PlanStep    |
  9. Assign gates and approvals          |
  10. Build MachinePlan                  |
           +-----------------------------+
                          |
                          v
        plan.json + plan.md -> .claude/team-context/
```

### 6.2 Execution Flow (CLI-Driven)

```
"baton execute start"
     |
     +-- Load plan.json -> MachinePlan
     +-- ExecutionEngine.start(plan) -> ExecutionAction(DISPATCH)
     +-- StatePersistence.save(state) / SqliteStorage.save_execution(state)
     +-- _print_action() -> stdout (Claude parses this)
     |
"baton execute next"
     |
     +-- ExecutionEngine.next_action() -> ExecutionAction
     +-- _print_action() -> stdout
     |
"baton execute record --step-id 1.1 --agent backend-engineer --status complete"
     |
     +-- ExecutionEngine.record_step_result(...)
     +-- parse_knowledge_gap(outcome) -> signal or None
     +-- EventBus.publish(step.completed) [if bus wired]
     +-- State persisted to disk
     |
"baton execute gate --phase-id 1 --result pass"
     |
     +-- ExecutionEngine.record_gate_result(...)
     +-- Advance to next phase
     |
"baton execute complete"
     |
     +-- ExecutionEngine.complete() -> summary
     +-- Write usage record, retrospective, trace
     +-- Auto-sync to central.db (best-effort)
```

### 6.3 Execution Flow (Daemon-Driven)

```
"baton daemon start --serve"
     |
     +-- WorkerSupervisor
     |       |
     |       +-- Write daemon.pid
     |       +-- Configure rotating log
     |       +-- SignalHandler.install()
     |       +-- ExecutionContext.build(launcher, bus, persist_events=True)
     |       |
     |       +-- TaskWorker.run()
     |       |       |
     |       |       +-- engine.next_actions() -> [parallel actions]
     |       |       +-- StepScheduler.dispatch_batch() -> [LaunchResult]
     |       |       +-- engine.record_step_result() per result
     |       |       +-- bus.publish(step.*) events
     |       |       +-- Loop until COMPLETE
     |       |
     |       +-- Co-start API server (if --serve)
     |
     +-- Graceful shutdown on SIGTERM/SIGINT
```

---

## 7. Data Model

### 7.1 Plan Hierarchy

`MachinePlan` is the sole plan type in the system (ADR-01). It is used by
the engine, runtime, CLI, API, and all tests.

```
MachinePlan
 |-- task_id: str
 |-- task_summary: str
 |-- risk_level: str (LOW | MEDIUM | HIGH | CRITICAL)
 |-- budget_tier: str (lean | standard | full)
 |-- execution_mode: str (phased | parallel | sequential)
 |-- git_strategy: str (commit-per-agent | branch-per-agent | none)
 |-- task_type: str | None
 |-- intervention_level: str (low | medium | high)
 |-- explicit_knowledge_packs: list[str]
 |-- explicit_knowledge_docs: list[str]
 |-- phases: list[PlanPhase]
      |-- phase_id: int
      |-- name: str
      |-- approval_required: bool
      |-- approval_description: str
      |-- gate: PlanGate | None
      |    |-- gate_type: str (build | test | lint | spec | review)
      |    |-- command: str
      |    |-- description: str
      |    |-- fail_on: list[str]
      |-- steps: list[PlanStep]
           |-- step_id: str (e.g., "1.1")
           |-- agent_name: str
           |-- task_description: str
           |-- model: str
           |-- depends_on: list[str]
           |-- deliverables: list[str]
           |-- allowed_paths: list[str]
           |-- blocked_paths: list[str]
           |-- context_files: list[str]
           |-- knowledge: list[KnowledgeAttachment]
           |-- team: list[TeamMember]
                |-- member_id: str (e.g., "1.1.a")
                |-- agent_name: str
                |-- role: str (lead | implementer | reviewer)
                |-- task_description: str
                |-- depends_on: list[str]
```

### 7.2 Execution State

`ExecutionState` is persisted after every mutation for crash recovery.

```
ExecutionState
 |-- task_id: str
 |-- plan: MachinePlan
 |-- current_phase: int
 |-- current_step_index: int
 |-- status: str (running | gate_pending | approval_pending | complete | failed)
 |-- step_results: list[StepResult]
 |-- gate_results: list[GateResult]
 |-- approval_results: list[ApprovalResult]
 |-- amendments: list[PlanAmendment]
 |-- pending_gaps: list[KnowledgeGapSignal]
 |-- resolved_decisions: list[ResolvedDecision]
 |-- started_at: str
 |-- completed_at: str
```

### 7.3 Serialization

All model types implement `to_dict()` / `from_dict()` class methods for JSON
serialization. Enum fields use typed enum instances internally and serialize
to `.value` strings only at the `to_dict()` boundary (ADR-09).

`MachinePlan.to_markdown()` renders a human-readable plan (`plan.md`) with
knowledge attachments, team composition, gates, and approval checkpoints.

---

## 8. API Architecture

### 8.1 Application Factory

`agent_baton/api/server.py` provides `create_app()`, a pure FastAPI factory:

```python
app = create_app(
    host="127.0.0.1",    # informational only (OpenAPI servers list)
    port=8741,
    token="secret",      # None disables auth
    team_context_root=Path(".claude/team-context"),
    allowed_origins=None, # localhost permissive by default
    bus=EventBus(),      # shared event bus
)
```

The factory:
1. Calls `init_dependencies()` to create module-level singletons
2. Wires `WebhookDispatcher` to the shared `EventBus`
3. Configures CORS middleware (outermost)
4. Adds `TokenAuthMiddleware` (no-op when token is None)
5. Lazily imports and registers 9 route modules
6. Mounts PMO UI static files if `pmo-ui/dist/` exists

### 8.2 Dependency Injection

`agent_baton/api/deps.py` owns module-level singleton instances. Each
singleton has a corresponding `get_*()` function that FastAPI route handlers
use via `Depends()`:

| Provider | Returns |
|----------|---------|
| `get_bus()` | Shared `EventBus` |
| `get_engine()` | `ExecutionEngine` (wired with bus and storage) |
| `get_planner()` | `IntelligentPlanner` (wired with retro, classifier, policy) |
| `get_registry()` | `AgentRegistry` (eagerly loaded) |
| `get_decision_manager()` | `DecisionManager` (wired with bus) |
| `get_dashboard()` | `DashboardGenerator` |
| `get_usage_logger()` | `UsageLogger` |
| `get_trace_recorder()` | `TraceRecorder` |
| `get_webhook_registry()` | `WebhookRegistry` |
| `get_pmo_store()` | `PmoSqliteStore` (backed by central.db) |
| `get_pmo_scanner()` | `PmoScanner` |
| `get_forge_session()` | `ForgeSession` |
| `get_classifier()` | `DataClassifier` |
| `get_policy_engine()` | `PolicyEngine` |

All singletons share a single `EventBus` instance, so events flow through one
bus regardless of which component emits them.

### 8.3 Route Modules

| Module | Prefix | Endpoints | Key Operations |
|--------|--------|-----------|---------------|
| `health.py` | `/api/v1` | `/health`, `/ready` | Liveness and readiness probes (auth-exempt) |
| `plans.py` | `/api/v1` | Plan CRUD | Create, list, get, delete plans |
| `executions.py` | `/api/v1` | Execution lifecycle | Start, next, record, gate, complete, status |
| `agents.py` | `/api/v1` | Agent registry | List, get, search agents |
| `observe.py` | `/api/v1` | Observability | Dashboard, traces, usage records |
| `decisions.py` | `/api/v1` | Human decisions | Request, resolve, list decisions |
| `events.py` | `/api/v1` | SSE stream | Server-sent event stream (requires `sse-starlette`) |
| `webhooks.py` | `/api/v1` | Webhook subscriptions | Register, list, delete, test webhooks |
| `pmo.py` | `/api/v1` | PMO board | 12 endpoints: projects, programs, cards, signals, health, forge |

### 8.4 Middleware Stack

```
Request -> CORS -> TokenAuth -> Route Handler -> Response
```

- **CORS**: Permits all localhost/127.0.0.1 origins by default. Configurable
  via `allowed_origins`.
- **TokenAuth**: Bearer token validation. Exempt paths: `/api/v1/health`,
  `/api/v1/ready`, `/openapi.json`, `/docs`, `/redoc`. No-op when token
  is None.

### 8.5 Webhook System

```
EventBus.publish(event)
     |
     +-- WebhookDispatcher._on_event(event)     (bus subscriber)
            |
            +-- WebhookRegistry.match(event.topic)
            |
            +-- For each matching subscription:
                 +-- HMAC-SHA256 sign payload (if secret configured)
                 +-- asyncio.create_task(deliver)
                 +-- Retry: [5s, 30s, 300s] backoff
                 +-- Auto-disable after 10 consecutive failures
                 +-- Log failures to webhook-failures.jsonl
```

---

## 9. Frontend Architecture

### 9.1 PMO UI

The PMO frontend is a React/Vite single-page application at `pmo-ui/`.

```
pmo-ui/
  src/
    main.tsx          Vite entry point
    App.tsx           Root component with routing
    components/       UI components (board, cards, projects, programs)
    hooks/            React hooks for data fetching
    api/              API client functions (fetch wrappers for /api/v1/pmo/*)
    styles/           CSS styling
```

- Built assets are served at `/pmo/` by the FastAPI `StaticFiles` mount.
- The UI communicates exclusively through the REST API (`/api/v1/pmo/*`).
- No direct SQLite access from the frontend.

---

## 10. CLI Structure

`cli/main.py` uses `pkgutil.iter_modules` to auto-discover command modules
from `commands/` and its subdirectories:

```python
for info in pkgutil.iter_modules(commands_pkg.__path__):
    if info.ispkg:
        # scan subdirectory package
        for sub_info in pkgutil.iter_modules(subpkg.__path__):
            # register command module
    else:
        # register top-level command module
```

Each command module exports:
- `register(subparsers) -> ArgumentParser` -- registers the subcommand name
- `handler(args) -> None` -- executes the command

Subcommand names are set inside each module's `register()` call, not derived
from filenames. Moving files between directories does not change the command
surface.

### Command Groups

| Group | Directory | Commands |
|-------|-----------|----------|
| Execution | `execution/` | `execute`, `plan`, `status`, `daemon`, `async`, `decide` |
| Observability | `observe/` | `dashboard`, `trace`, `usage`, `telemetry`, `context-profile`, `retro` |
| Governance | `govern/` | `classify`, `compliance`, `policy`, `escalations`, `validate`, `spec-check`, `detect` |
| Improvement | `improve/` | `scores`, `evolve`, `patterns`, `budget`, `changelog` |
| Distribution | `distribute/` | `package`, `publish`, `pull`, `verify-package`, `install`, `transfer` |
| Agents | `agents/` | `agents`, `route`, `events`, `incident` |
| (top-level) | `commands/` | `pmo`, `sync`, `query`, `source`, `serve` |

### Task-ID Resolution

Every `baton execute` subcommand resolves a target task ID through a
three-level priority chain:

```
--task-id flag  ->  BATON_TASK_ID env var  ->  active-task-id.txt  ->  None
```

---

## 11. Knowledge Delivery Subsystem

### Pipeline Architecture

```
KnowledgeRegistry (curated packs)  --+
                                      +---> KnowledgeResolver ---> PromptDispatcher
MCP RAG Server (broad org knowledge) --+     (match + budget)      (prompt assembly)
```

### Discovery Layers (resolved at plan time)

Layers execute in order. Documents resolved in an earlier layer are not
duplicated:

1. **Explicit** -- user passes `--knowledge path` or `--knowledge-pack name`
2. **Agent-declared** -- agent frontmatter `knowledge_packs` field
3. **Planner-matched (strict)** -- keywords matched against registry tags
4. **Planner-matched (relevance fallback)** -- TF-IDF over registry metadata
   (or MCP RAG when available)
5. **Plan review** -- `plan.md` shows each step's attachments; user can
   add/remove before execution starts

### Delivery Decisions

The `KnowledgeResolver` applies a per-step token budget (default 32,000)
and per-document token cap (default 8,000):

- Document <= cap and fits budget: **inline** (full content in prompt)
- Document > cap or budget exhausted: **reference** (path + retrieval hint)

### Runtime Knowledge Acquisition

Agents self-interrupt with:

```
KNOWLEDGE_GAP: <description>
CONFIDENCE: none | low | partial
TYPE: factual | contextual
```

The escalation matrix (`determine_escalation()`) decides the action:

| Gap type | Resolution found | Risk x Intervention | Action |
|----------|-----------------|---------------------|--------|
| factual | yes | any | `auto-resolve` |
| factual | no | LOW + low | `best-effort` |
| factual | no | LOW + medium/high | `queue-for-gate` |
| factual | no | MEDIUM+ any | `queue-for-gate` |
| contextual | -- | any | `queue-for-gate` |

---

## 12. Cross-Cutting Concerns

### 12.1 Error Handling

- **State persistence**: Atomic writes (tmp+rename for JSON, WAL mode for
  SQLite). Parse errors in `from_dict()` fall through to `None` returns
  rather than raising.
- **Auto-sync**: Wrapped in `try/except` at `baton execute complete`. Sync
  failure never blocks execution completion.
- **API routes**: Missing route modules are skipped with a warning (graceful
  degradation if optional dependencies like `sse-starlette` are absent).
- **Storage fallback**: When SQLite save fails, the engine falls back to file
  persistence and logs a warning.

### 12.2 Logging

Module-level loggers via `logging.getLogger(__name__)`. The daemon configures
a `RotatingFileHandler` to `daemon.log` (or `worker.log` in namespaced mode).
CLI commands use stderr for user-facing messages.

### 12.3 Configuration

Configuration is file-based, not environment-variable-based:

- Agent definitions: `.claude/agents/*.md` (frontmatter + markdown body)
- Knowledge packs: `.claude/knowledge/*/pack.yaml` + document files
- PMO config: `~/.baton/pmo-config.json`
- Webhook subscriptions: `.claude/team-context/webhooks.json`
- Policy rules: loaded from JSON by `PolicyEngine`

The only environment variable the system reads is `BATON_TASK_ID` (for
session binding) and adapter-specific PAT variables (e.g., the ADO adapter
reads the env var name stored in its config).

### 12.4 State Persistence Layout

```
.claude/team-context/
  baton.db                          SQLite database (new default)
  execution-state.json              Legacy flat state file
  active-task-id.txt                Pointer to default task
  executions/
    <task-id>/
      execution-state.json          Per-task state (file backend)
      events/
        <task-id>.jsonl             Domain events
      worker.pid                    Daemon PID (namespaced)
      worker.log                    Daemon log (namespaced)
  plan.json                         Current plan (legacy)
  plan.md                           Human-readable plan (legacy)
  context.md                        Shared context (legacy)
  mission-log.md                    Mission log (legacy)
  usage-log.jsonl                   Usage records
  telemetry.jsonl                   Telemetry events
  traces/
    <task-id>.json                  Execution traces
  retrospectives/
    <task-id>.md                    Retrospective reports
  context-profiles/
    <task-id>.json                  Context efficiency profiles
  decisions/
    <request-id>.json               Decision requests
    <request-id>.md                 Human-readable summaries
    <request-id>-resolution.json    Decision resolutions
  webhooks.json                     Webhook subscriptions
  webhook-failures.jsonl            Failed delivery log

~/.baton/
  central.db                        Cross-project read replica
  .pmo-migrated                     One-time migration marker
```

---

## 13. Extension Points

### 13.1 Adding a New Agent

Create a markdown file in `agents/` with YAML frontmatter:

```yaml
---
name: my-agent
model: sonnet
description: What this agent does
tools:
  - Read
  - Edit
  - Bash
knowledge_packs:
  - my-knowledge-pack
---

Agent instructions in markdown...
```

Run `scripts/install.sh` to make it available globally. The `AgentRegistry`
auto-discovers it from `~/.claude/agents/` or `.claude/agents/`.

### 13.2 Adding a New Storage Backend

Implement the `StorageBackend` protocol from `core/storage/protocol.py`.
The protocol has ~20 methods covering execution state, plans, steps, gates,
usage, retrospectives, traces, events, patterns, and budget data. Register
the backend in `core/storage/__init__.py`'s `get_project_storage()` factory.

### 13.3 Adding a New External Source Adapter

Create `core/storage/adapters/<type>.py` implementing the
`ExternalSourceAdapter` protocol:

```python
class ExternalSourceAdapter(Protocol):
    source_type: str
    def connect(self, config: dict) -> None: ...
    def fetch_items(self, **kwargs) -> list[ExternalItem]: ...
    def fetch_item(self, item_id: str) -> ExternalItem | None: ...
```

Call `AdapterRegistry.register(MyAdapter)` at module level for
self-registration on import.

### 13.4 Adding a New CLI Command

Create a module in the appropriate `cli/commands/<group>/` directory with:

```python
def register(subparsers) -> argparse.ArgumentParser:
    parser = subparsers.add_parser("my-command", help="...")
    # add arguments
    return parser

def handler(args) -> None:
    # implementation
    pass
```

The command is auto-discovered by `cli/main.py` without any registration
boilerplate.

### 13.5 Adding a New Knowledge Pack

Create a directory under `.claude/knowledge/<pack-name>/` with:

```
pack.yaml           # name, description, tags, target_agents, documents list
doc1.md             # knowledge document with optional YAML frontmatter
doc2.md
```

The `KnowledgeRegistry` auto-discovers packs from `.claude/knowledge/`
(project) and `~/.claude/knowledge/` (global).

---

## 14. Dependency Graph

### Subsystem Dependencies (ASCII)

```
                    +----------+
                    |  models/ |
                    +----+-----+
                         |
      +--------+---------+----------+-----------+----------+
      |        |         |          |           |          |
      v        v         v          v           v          v
  +------+ +------+ +--------+ +--------+ +--------+ +--------+
  |events| |govern| |observe | |improve | | learn  | |orchestr.|
  +--+---+ +--+---+ +---+----+ +---+----+ +---+----+ +---+----+
     |        |          |          |          |          |
     +--------+-----+----+----------+----------+----------+
                    |
               +----v----+
               | engine/ |
               +----+----+
                    |
               +----v----+
               | runtime/|
               +----+----+
                    |
     +--------------+-------------+
     |              |             |
+----v---+    +----v----+   +----v-----+
| cli/   |    |  api/   |   | pmo-ui/  |
+--------+    +---------+   +----------+

         +----------+
         | storage/ |  (depends on models/ only,
         +----+-----+   consumed by cli/ and api/)
              |
     +--------+-------+
     |                 |
+----v-----+    +------v------+
| baton.db |    | central.db  |
| (project)|    | (federated) |
+-----------+   +-------------+
```

### Dependency Order (no circular imports)

```
models  -->  events, observe, govern, learn, improve, distribute, orchestration, storage
         -->  engine  -->  runtime  -->  CLI / API
```

### Key Contract Boundaries

| Contract | Location | Consumers |
|----------|----------|-----------|
| `ExecutionDriver` | `core/engine/protocols.py` | `TaskWorker`, `WorkerSupervisor` |
| `StorageBackend` | `core/storage/protocol.py` | `ExecutionEngine`, CLI commands |
| `AgentLauncher` | `core/runtime/launcher.py` | `StepScheduler`, `TaskWorker` |
| `ExternalSourceAdapter` | `core/storage/adapters/__init__.py` | `AdoAdapter`, CLI source commands |
| `_print_action()` | `cli/commands/execution/execute.py` | Claude (parses stdout) |
| `execution-state.json` | `core/engine/persistence.py` | `baton execute resume` |

---

## 15. Functional Domains

### Domain 1: Plan Creation

| Attribute | Value |
|-----------|-------|
| Entry | `baton plan "task" [--save] [--explain] [--knowledge ...] [--knowledge-pack ...]` |
| Path | `cli/plan_cmd.py` -> `IntelligentPlanner` -> `AgentRouter` + `AgentRegistry` -> `PatternLearner` + `BudgetTuner` -> `PolicyEngine` -> `KnowledgeResolver` |
| Output | `plan.json` + `plan.md` in `.claude/team-context/` |

### Domain 2: Execution Lifecycle

| Attribute | Value |
|-----------|-------|
| Entry | `baton execute start` / `next` / `record` / `gate` / `approve` / `complete` |
| Path | `cli/execute.py` -> `ExecutionEngine` -> `StatePersistence` / `SqliteStorage` -> `PromptDispatcher` -> `GateRunner` -> `EventBus` |
| Output | `execution-state.json`, delegation prompts via `_print_action()` |

### Domain 3: Knowledge Delivery

| Attribute | Value |
|-----------|-------|
| Entry | `--knowledge` / `--knowledge-pack` on `baton plan`; `KNOWLEDGE_GAP` in agent output |
| Path | `IntelligentPlanner` -> `KnowledgeRegistry` -> `KnowledgeResolver` -> `PromptDispatcher` -> `KnowledgeGap` handler |
| Output | Knowledge blocks in delegation prompts; `KnowledgeGapRecord` in retrospectives |

### Domain 4: Federated Sync

| Attribute | Value |
|-----------|-------|
| Entry | `baton sync` / `baton sync --all` / auto-sync on complete |
| Path | `cli/sync_cmd.py` -> `SyncEngine` -> sqlite3 (project -> central) |
| Output | Rows mirrored to `central.db` with `project_id` prepended |

### Domain 5: Improvement Loop

| Attribute | Value |
|-----------|-------|
| Entry | `baton scores` / `patterns` / `budget` / `evolve` / `changelog` |
| Path | `cli/improve/` -> `PerformanceScorer` -> `PatternLearner` -> `BudgetTuner` -> `PromptEvolutionEngine` -> `AgentVersionControl` |
| Output | Scorecards, patterns, budget recommendations, evolution proposals |

### Domain 6: Governance

| Attribute | Value |
|-----------|-------|
| Entry | `baton classify` / `compliance` / `policy` / `validate` / `spec-check` / `detect` / `escalations` |
| Path | `cli/govern/` -> `DataClassifier` -> `PolicyEngine` -> `ComplianceReportGenerator` -> `SpecValidator` -> `EscalationManager` |
| Output | Risk classification, policy violations, compliance reports |

### Domain 7: Observability

| Attribute | Value |
|-----------|-------|
| Entry | `baton trace` / `dashboard` / `usage` / `telemetry` / `retro` / `context-profile` |
| Path | `cli/observe/` -> `TraceRecorder` -> `UsageLogger` -> `DashboardGenerator` -> `RetrospectiveEngine` -> `AgentTelemetry` -> `ContextProfiler` |
| Output | Traces, usage reports, dashboards, retrospectives, telemetry events |

### Domain 8: Daemon and Async Execution

| Attribute | Value |
|-----------|-------|
| Entry | `baton daemon start [--foreground] [--dry-run] [--serve]` / `baton async` |
| Path | `cli/daemon.py` -> `WorkerSupervisor` -> `TaskWorker` -> `ClaudeCodeLauncher` / `DryRunLauncher` -> `ExecutionDriver` |
| Output | Background process managing execution; optional co-started API server |

### Domain 9: PMO

| Attribute | Value |
|-----------|-------|
| Entry | `baton pmo serve` / `status` / `add` / `health` |
| Path | `cli/pmo_cmd.py` -> `PmoSqliteStore` -> `PmoScanner` -> `ForgeSession` -> API (`routes/pmo.py`) |
| Output | PMO board data in `central.db`; React UI at `/pmo/` |

### Domain 10: Distribution

| Attribute | Value |
|-----------|-------|
| Entry | `baton package` / `publish` / `pull` / `verify-package` / `install` / `transfer` |
| Path | `cli/distribute/` -> `PackageBuilder` -> `PackageVerifier` -> `RegistryClient` |
| Output | `.tar.gz` archive with `manifest.json`, agents, references, knowledge packs |

### Domain 11: API Server

| Attribute | Value |
|-----------|-------|
| Entry | `baton serve` (standalone) or `baton daemon start --serve` |
| Path | `cli/serve.py` -> `create_app()` -> 9 route modules -> backing subsystems |
| Output | HTTP API (41 routes), SSE event stream, webhook deliveries |

### Domain 12: External Sources

| Attribute | Value |
|-----------|-------|
| Entry | `baton source add ado` / `list` / `sync` / `remove` / `map` |
| Path | `cli/source_cmd.py` -> `ExternalSourceAdapter` protocol -> `AdoAdapter` -> `CentralStore` |
| Output | Source registrations, synced work items, mappings in `central.db` |
