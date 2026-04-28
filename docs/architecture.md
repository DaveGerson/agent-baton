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
|  (49 commands) |     |  (FastAPI)     |     |  (React/Vite)    |
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
| What depends on what? | [4. Layered Architecture](#4-layered-architecture) |
| Where is the execution state machine? | [5. Core Subsystems](#5-core-subsystems) |
| What are the interface contracts? | [15. Dependency Graph](#15-dependency-graph) |
| How does knowledge delivery work? | [11. Knowledge Delivery](#11-knowledge-delivery-subsystem) |
| How does cross-project sync work? | [5.4 Storage](#54-storage-corestorage) |
| How does the bead memory system work? | [12. Bead Memory System](#12-bead-memory-system) |

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
  models/             Foundation layer. No internal deps. 24 modules.
  |  execution.py     MachinePlan, PlanPhase, PlanStep, PlanGate, TeamMember,
  |                   SynthesisSpec, ExecutionState, StepResult, TeamStepResult,
  |                   GateResult, ApprovalResult, PlanAmendment, ExecutionAction,
  |                   ActionType, StepStatus, PhaseStatus
  |  enums.py         RiskLevel, TrustLevel, BudgetTier, ExecutionMode,
  |                   GateOutcome, FailureClass, GitStrategy, AgentCategory
  |  agent.py         AgentDefinition (parsed from .md frontmatter)
  |  events.py        Event (topic + payload + sequence)
  |  knowledge.py     KnowledgeDocument, KnowledgePack, KnowledgeAttachment,
  |                   KnowledgeGapSignal, KnowledgeGapRecord, ResolvedDecision
  |  pmo.py           PmoProject, PmoCard, PmoSignal, ProgramHealth, PmoConfig,
  |                   InterviewQuestion, InterviewAnswer
  |  usage.py         AgentUsageRecord, TaskUsageRecord
  |  retrospective.py Retrospective, AgentOutcome, KnowledgeGap,
  |                   RosterRecommendation, SequencingNote,
  |                   TeamCompositionRecord, ConflictRecord
  |  trace.py         TaskTrace, TraceEvent
  |  decision.py      DecisionRequest, DecisionResolution, ContributionRequest
  |  pattern.py       LearnedPattern, PlanStructureHint, TeamPattern
  |  budget.py        BudgetRecommendation
  |  feedback.py      RetrospectiveFeedback
  |  context_profile.py  AgentContextProfile, TaskContextProfile
  |  registry.py      RegistryEntry, RegistryIndex
  |  escalation.py    Escalation
  |  improvement.py   Recommendation, Experiment, Anomaly, TriggerConfig,
  |                   ImprovementReport, ImprovementConfig,
  |                   RecommendationCategory, RecommendationStatus,
  |                   ExperimentStatus, AnomalySeverity
  |  learning.py      LearningEvidence, LearningIssue
  |  parallel.py      ExecutionRecord, ResourceLimits
  |  plan.py          MissionLogEntry
  |  reference.py     ReferenceDocument
  |  session.py       SessionCheckpoint, SessionParticipant, SessionState
  |  bead.py          Bead, BeadLink (structured agent memory,
  |                   inspired by beads-ai/beads-cli)
  |
  utils/
  |  frontmatter.py   parse_frontmatter() -- YAML frontmatter extraction
  |
  core/
  |  __init__.py      Re-exports: AgentRegistry, AgentRouter, ContextManager,
  |                   ExecutionEngine, IntelligentPlanner, PromptDispatcher,
  |                   GateRunner, ExecutionDriver, StatePersistence,
  |                   AgentLauncher, TaskWorker, WorkerSupervisor, EventBus.
  |                   Documents core vs peripheral layers.
  |
  |  engine/          ExecutionEngine, IntelligentPlanner, PromptDispatcher,
  |  |                GateRunner, StatePersistence, ExecutionDriver protocol,
  |  |                TaskClassifier protocol, KeywordClassifier, HaikuClassifier,
  |  |                FallbackClassifier, KnowledgeResolver, KnowledgeGap handler,
  |  |                BeadStore, BeadSelector, bead_signal, bead_decay,
  |  |                PlanReviewer, CommitConsolidator
  |  |
  |  runtime/         TaskWorker, WorkerSupervisor, StepScheduler,
  |  |                AgentLauncher protocol, DryRunLauncher, ClaudeCodeLauncher,
  |  |                HeadlessClaude, HeadlessConfig, HeadlessResult,
  |  |                DecisionManager, ExecutionContext factory, SignalHandler,
  |  |                daemonize()
  |  |
  |  orchestration/   AgentRegistry, AgentRouter (StackProfile), ContextManager,
  |  |                KnowledgeRegistry (_TFIDFIndex)
  |  |
  |  storage/         StorageBackend protocol, SqliteStorage, FileStorage,
  |  |                ConnectionManager, StorageMigrator, QueryEngine,
  |  |                SyncEngine, CentralStore, PmoSqliteStore,
  |  |                adapters/ (ExternalSourceAdapter, AdapterRegistry, AdoAdapter)
  |  |
  |  events/          EventBus, EventPersistence, domain event factories,
  |  |                projections (TaskView, PhaseView, StepView)
  |  |
  |  observe/         TraceRecorder, TraceRenderer, UsageLogger,
  |  |                DashboardGenerator, RetrospectiveEngine,
  |  |                AgentTelemetry, ContextProfiler, DataArchiver
  |  |
  |  govern/          DataClassifier, ComplianceReportGenerator, PolicyEngine,
  |  |                EscalationManager, AgentValidator, SpecValidator
  |  |
  |  improve/         PerformanceScorer (AgentScorecard, TeamScorecard),
  |  |                PromptEvolutionEngine, AgentVersionControl,
  |  |                ImprovementLoop, ExperimentManager, ProposalManager,
  |  |                RollbackManager, TriggerEvaluator
  |  |
  |  learn/           PatternLearner, BudgetTuner, LearningEngine,
  |  |                LearningLedger, LearnedOverrides, LearningInterviewer,
  |  |                Recommender, BeadAnalyzer
  |  |
  |  pmo/             PmoStore, PmoScanner, ForgeSession
  |  |
  |  distribute/      PackageBuilder, PackageVerifier, RegistryClient
  |     experimental/ AsyncDispatcher, IncidentManager, ProjectTransfer
  |
  api/
  |  server.py        create_app() factory -- FastAPI application
  |  deps.py          init_dependencies() -- singleton DI container
  |  middleware/
  |  |  auth.py       TokenAuthMiddleware (Bearer token, exempt health paths)
  |  |  cors.py       configure_cors() (localhost permissive by default)
  |  |  user_identity.py  UserIdentityMiddleware (X-Baton-User, approval mode)
  |  routes/
  |  |  health.py     /health, /ready (2 endpoints)
  |  |  plans.py      Plan CRUD (2 endpoints)
  |  |  executions.py Execution lifecycle (6 endpoints)
  |  |  agents.py     Agent registry (2 endpoints)
  |  |  observe.py    Dashboard, trace, usage (3 endpoints)
  |  |  decisions.py  Decision request/resolve (3 endpoints)
  |  |  events.py     SSE event stream (1 endpoint)
  |  |  webhooks.py   Webhook subscriptions (3 endpoints)
  |  |  pmo.py        PMO board/project/forge/execute/gates/changelist/review/signals (36 endpoints)
  |  |  pmo_h3.py     PMO H3 surfaces: scorecard, arch-review, playbooks, CRP (5 endpoints)
  |  |  learn.py      Learning issues and auto-correction (5 endpoints)
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
     colors.py        Terminal color constants
     errors.py        CLI error handling
     formatting.py    Output formatting utilities
     commands/
       execution/     execute.py, plan_cmd.py, status.py, daemon.py,
       |              async_cmd.py, decide.py
       observe/       dashboard.py, trace.py, usage.py, telemetry.py,
       |              context_profile.py, retro.py, cleanup.py,
       |              migrate_storage.py, context_cmd.py, query.py
       govern/        classify.py, compliance.py, policy.py, escalations.py,
       |              validate.py, spec_check.py, detect.py
       improve/       scores.py, evolve.py, patterns.py, budget.py,
       |              changelog.py, anomalies.py, experiment.py,
       |              improve_cmd.py, learn_cmd.py
       distribute/    package.py, publish.py, pull.py, verify_package.py,
       |              install.py, transfer.py
       agents/        agents.py, route.py, events.py, incident.py
       bead_cmd.py    baton beads list/show/ready/close/link/cleanup/promote/graph
       pmo_cmd.py     baton pmo serve/status/add/health
       sync_cmd.py    baton sync [--all] [status]
       query_cmd.py   baton query (cross-project SQL against central.db)
       source_cmd.py  baton source add/list/sync/remove/map
       serve.py       baton serve (standalone API server)
       uninstall.py   baton uninstall --scope project|user

pmo-ui/              React/Vite PMO frontend (served at /pmo/)
  src/
    main.tsx          Vite entry point
    App.tsx           Root component with routing
    components/       AdoCombobox, AnalyticsDashboard, ChangelistPanel,
    |                 ConfirmDialog, ExecutionProgress, ForgePanel,
    |                 GateApprovalPanel, HealthBar, InterviewPanel,
    |                 KanbanBoard, KanbanCard, KeyboardShortcutsDialog,
    |                 PlanEditor, PlanPreview, ReviewPanel, SignalsBar,
    |                 BeadGraphView, BeadTimelineView
    views/            H3 PMO views — RoleBasedDashboard (H3.2),
    |                 DeveloperScorecard (H3.4), ArchReviewPanel (H3.7),
    |                 PlaybookGallery (H3.8), CRPWizard (H3.9). Backed
    |                 by /api/v1/pmo/scorecard, /arch-beads, /playbooks,
    |                 /crp endpoints in routes/pmo_h3.py.
    contexts/         ToastContext
    hooks/            useHotkeys, usePersistedState, usePmoBoard
    api/              client.ts, types.ts
    styles/           index.css, tokens.ts
    test/             setup.ts (Vitest + jsdom + jest-dom matchers)
    utils/            agent-names.ts
agents/              Distributable agent definitions (19 .md files)
references/          Distributable reference docs (15 .md files)
templates/           CLAUDE.md + settings.json + skills/baton-help
scripts/             install.sh (Linux), install.ps1 (Windows)
tests/               Test suite (~6202 test functions, pytest)
docs/                Architecture documentation
```

---

## 4. Layered Architecture

### Layer Diagram

```
+=====================================================================+
| Layer 1: MODELS (Foundation)                                         |
| agent_baton/models/ -- 24 modules, dataclasses with to_dict/from_dict|
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
| runtime/ -- TaskWorker, WorkerSupervisor,          |
|            StepScheduler, Launchers, SignalHandler, |
|            HeadlessClaude, daemonize                |
+============+=======================================+
             |
             v
+============+==============================================+
| Layer 4: INTERFACES                                       |
| cli/ -- 49 command modules in 7 groups + 7 top-level      |
| api/ -- FastAPI app, 10 route modules (64 endpoints),     |
|         middleware, webhooks                               |
| pmo-ui/ -- React/Vite frontend                            |
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
| `executor.py` | `ExecutionEngine` | State machine (2844 LOC). Manages `ExecutionState`, determines next action, records step/gate/approval results, handles plan amendments, writes usage/telemetry/retrospective on completion. Also contains `TaskViewSubscriber` for event-driven view projection. |
| `planner.py` | `IntelligentPlanner` | Data-driven plan creator. Accepts a task description and produces a `MachinePlan`. Consults `AgentRouter` for stack detection, `PatternLearner` for historical patterns, `BudgetTuner` for tier recommendations, `PolicyEngine` for guardrail evaluation, `KnowledgeResolver` for knowledge attachment. Uses `RetroEngine` protocol for retrospective integration. |
| `dispatcher.py` | `PromptDispatcher` | Stateless prompt assembler. Builds delegation prompts from `PlanStep` + shared context + knowledge attachments + resolved decisions + selected beads. Builds team delegation prompts. Builds gate prompts. Generates path enforcement bash guards. |
| `gates.py` | `GateRunner` | Stateless gate evaluator. Builds `GATE` actions for the caller, evaluates gate command output (test, build, lint, spec, review types), provides default gate definitions. |
| `persistence.py` | `StatePersistence` | Atomic JSON file I/O for `ExecutionState`. Supports namespaced task directories (`executions/<task-id>/`) and legacy flat files. Manages the `active-task-id.txt` pointer. |
| `protocols.py` | `ExecutionDriver` | `typing.Protocol` (runtime-checkable) defining the 12-method interface between the async worker layer and the engine. |
| `classifier.py` | `TaskClassifier` protocol, `KeywordClassifier`, `HaikuClassifier`, `FallbackClassifier` | Task classification for plan sizing. `HaikuClassifier` calls Claude Haiku via `claude --print` for intelligent classification. `KeywordClassifier` is the deterministic fallback. `FallbackClassifier` tries Haiku first, degrades to keywords. Returns `TaskClassification` with `task_type`, `complexity` (light/medium/heavy), `agent_names`, and `max_agents`. |
| `knowledge_resolver.py` | `KnowledgeResolver` | 4-layer knowledge resolution pipeline: explicit -> agent-declared -> planner-matched (strict tag) -> planner-matched (TF-IDF relevance fallback). Per-step token budget governs inline vs. reference delivery decisions. |
| `knowledge_gap.py` | `parse_knowledge_gap()`, `determine_escalation()` | Parses `KNOWLEDGE_GAP` / `CONFIDENCE` / `TYPE` signals from agent output. Applies escalation matrix (gap type x risk level x intervention level) returning `auto-resolve`, `best-effort`, or `queue-for-gate`. |
| `bead_store.py` | `BeadStore` | SQLite-backed persistence for structured agent memory. CRUD for `beads` and `bead_tags` tables with query filters, dependency-aware `ready()`, decay for archiving old beads. Inspired by Steve Yegge's Beads (beads-ai/beads-cli). |
| `bead_signal.py` | `parse_bead_signals()`, `parse_bead_feedback()` | Parses `BEAD_DISCOVERY` / `BEAD_DECISION` / `BEAD_WARNING` signals from agent output. Called in `record_step_result()` after the knowledge gap block. Publishes `bead.created` events to the EventBus. Also parses `BEAD_USEFUL` / `BEAD_STALE` feedback for quality scoring. |
| `bead_selector.py` | `BeadSelector` | Selects and ranks beads for injection into delegation prompts. Three-tier selection: dependency-chain beads (highest priority), same-phase beads, cross-phase beads. Within each tier, ranks by type priority (warning > discovery > decision > outcome > planning) and quality score. Budget-trimmed output. |
| `bead_decay.py` | `decay_beads()` | Retention-based archival of old beads. Moves stale open beads to `archived` status based on configurable age thresholds. |

#### Expected Outcome (Demo Statement, Wave 3.1)

Every `PlanStep` carries an `expected_outcome` — a 1-sentence behavioral
statement of what should be observably true after the step. The planner
derives it deterministically from the step description, agent role, and
step type (no LLM call). The dispatcher prepends it as a `## Expected
Outcome` section in the delegation prompt; `plan.md` and the CLI
`DISPATCH` action surface it on their own lines. The goal is to anchor
`code-reviewer` and `test-engineer` on behavioral correctness rather
than "no errors". Empty string preserves back-compat for older plans.

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

#### CI Gates (Wave 4.1)

Plans may declare a `gate_type="ci"` gate whose `command` is a workflow
filename (e.g. `"ci.yml"`) or a JSON config (`{"provider": "github",
"workflow": "ci.yml", "timeout_s": 600}`). The CLI/executor invoke
`agent_baton.core.gates.ci_gate.CIGateRunner`, which polls
`gh run list/view` every 15 s for the current branch's HEAD commit and
returns a `CIGateResult` (passed, run_id, conclusion, url, log_excerpt).
CI gates are opt-in — default plans do not include one. Missing `gh`,
GitLab, and timeout are reported as `passed=False` with sentinel
conclusions (`gh_unavailable`, `not_implemented`, `timeout`).

---

### 5.2 Runtime (`core/runtime/`)

The runtime layer wraps the synchronous engine in an async execution loop,
manages concurrent agent launches, and provides daemon lifecycle support.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `worker.py` | `TaskWorker` | Async event loop driving a single task. Calls `engine.next_actions()` for parallel work, dispatches via `StepScheduler`, records results, publishes `step.*` events. Handles GATE and WAIT actions. |
| `supervisor.py` | `WorkerSupervisor` | Daemon lifecycle manager. PID file management, rotating log files, graceful shutdown via `SignalHandler`, status JSON snapshots. |
| `scheduler.py` | `StepScheduler` (`SchedulerConfig`) | Bounded-concurrency dispatcher using `asyncio.Semaphore`. Caps simultaneous agent launches at `max_concurrent` (default: 3). |
| `launcher.py` | `AgentLauncher` protocol, `DryRunLauncher`, `LaunchResult` | Protocol for launching agents. `DryRunLauncher` logs dispatches and returns synthetic results for testing. |
| `claude_launcher.py` | `ClaudeCodeLauncher` (`ClaudeCodeConfig`) | Real launcher that invokes the `claude` CLI as an async subprocess. Whitelist-based environment, exec-only (no shell), API key redaction in stderr. Configurable per-model timeouts. |
| `headless.py` | `HeadlessClaude` (`HeadlessConfig`, `HeadlessResult`) | Synchronous subprocess wrapper for `claude --print`. Used by `ForgeSession` for plan generation, `baton execute run` for autonomous execution, and the PMO execute endpoint for UI-launched execution. |
| `context.py` | `ExecutionContext` | Factory that wires `EventBus`, `ExecutionEngine`, and `EventPersistence` together correctly. Prevents duplicate event persistence subscriptions. |
| `decisions.py` | `DecisionManager` | Persists human decision requests to JSON files, writes companion `.md` summaries, publishes `human.decision_needed` / `human.decision_resolved` events. |
| `signals.py` | `SignalHandler` | POSIX signal handler (SIGTERM, SIGINT). Sets a cancellation event so the worker loop can drain in-flight agents before exiting. |
| `daemon.py` | `daemonize()` | Classic UNIX double-fork to detach from controlling terminal. Called before `asyncio.run()`. POSIX only. |

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
| `ExecutionEngine` | `task.started`, `task.completed`, `task.failed`, `phase.started`, `phase.completed`, `gate.passed`, `gate.failed`, `bead.created`, `bead.conflict` |
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
| `router.py` | `AgentRouter` (`StackProfile`) | Stack detection (scans for `package.json`, `pyproject.toml`, etc.) and flavor routing. Maps detected `(language, framework)` pairs to agent flavor suffixes. |
| `context.py` | `ContextManager` | Manages `.claude/team-context/` files: `plan.md`, `plan.json`, `context.md`, `mission-log.md`, `codebase-profile.md`. Supports task-scoped directories for concurrent plans. |
| `knowledge_registry.py` | `KnowledgeRegistry` (`_TFIDFIndex`) | Loads knowledge packs from `.claude/knowledge/` (project) and `~/.claude/knowledge/` (global). Indexes documents by tags and builds a TF-IDF index over metadata for relevance-based search. |

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

#### Stack Detection -> Flavor Routing

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

Pluggable persistence backends, federated cross-project sync, ad-hoc query
engine, and external source adapters.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `__init__.py` | `get_project_storage()`, `detect_backend()` | Factory: auto-detects SQLite or file backend. Also `get_pmo_central_store()`, `get_pmo_storage()`, `get_central_storage()`, `get_sync_engine()`. |
| `protocol.py` | `StorageBackend` | `typing.Protocol` (runtime-checkable). 34 methods for CRUD of executions, plans, steps, gates, usage, retrospectives, traces, events, patterns, budget, mission log, context, and profile data. |
| `sqlite_backend.py` | `SqliteStorage` | SQLite implementation of `StorageBackend`. Uses WAL mode, busy timeout, connection pooling. 31-table project schema. |
| `file_backend.py` | `FileStorage` | Legacy JSON/JSONL implementation of `StorageBackend`. Delegates to `StatePersistence`, `UsageLogger`, `TraceRecorder`, etc. |
| `schema.py` | DDL constants | `PROJECT_SCHEMA_DDL` (31 tables), `PMO_SCHEMA_DDL` (legacy), `CENTRAL_SCHEMA_DDL` (sync infrastructure + PMO + external sources + synced project mirrors + 6 views). Also `MIGRATIONS` dict for incremental schema upgrades. |
| `connection.py` | `ConnectionManager` | SQLite connection helper with WAL mode, busy timeout, PRAGMA tuning. Handles schema migrations via `_run_migrations()`. |
| `queries.py` | `QueryEngine` | Ad-hoc SQL query engine for `baton.db` and `central.db`. Provides structured helpers (`AgentStats`, `TaskSummary`, `KnowledgeGapReport`, `GateStats`, `CostReport`) plus raw SQL execution with write protection. |
| `migrate.py` | `StorageMigrator` | Schema migration and version management for project databases. |
| `sync.py` | `SyncEngine` (`SyncTableSpec`, `SyncResult`) | Incremental one-way sync: project `baton.db` -> `~/.baton/central.db`. Watermark-based (row-level, not file-level). 28 syncable tables. Idempotent. Also provides `auto_sync_current_project()` convenience function. |
| `central.py` | `CentralStore` | Read-only query interface for `central.db`. Cross-project views and ad-hoc SQL. Includes `_maybe_migrate_pmo()` for one-time `pmo.db` migration. |
| `pmo_sqlite.py` | `PmoSqliteStore` | SQLite storage for PMO data (projects, programs, signals, cards, metrics, forge sessions). Used for both legacy `pmo.db` and central.db. |
| `adapters/__init__.py` | `ExternalSourceAdapter` protocol, `ExternalItem`, `AdapterRegistry` | Protocol for external work trackers (ADO, Jira, GitHub). `AdapterRegistry` maps type strings to adapter classes. |
| `adapters/ado.py` | `AdoAdapter` | Azure DevOps adapter. Reads PAT from env var. Self-registers on import. |

#### Project Schema Tables (31 tables in `baton.db`)

```
_schema_version, executions, plans, plan_phases, plan_steps, team_members,
step_results, team_step_results, gate_results, approval_results, amendments,
events, usage_records, agent_usage, telemetry, retrospectives,
retrospective_outcomes, knowledge_gaps, roster_recommendations,
sequencing_notes, traces, trace_events, learned_patterns,
budget_recommendations, mission_log_entries, shared_context,
codebase_profile, active_task, learning_issues, beads, bead_tags
```

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
            | sync infrastructure       |
            |   sync_watermarks         |
            |   sync_history            |
            | PMO tables (merged)       |
            |   projects, programs,     |
            |   signals, archived_cards,|
            |   forge_sessions,         |
            |   pmo_metrics             |
            | external source tables    |
            |   external_sources        |
            |   external_items          |
            |   external_mappings       |
            | 28 synced project tables  |
            |   (all project tables     |
            |    mirrored with          |
            |    project_id prefix)     |
            | 6 cross-project views     |
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
| `v_cross_project_discoveries` | Discovery beads shared across projects |
| `v_external_plan_mapping` | External work items linked to baton plans |

---

### 5.5 Observe (`core/observe/`)

Observability subsystem: tracing, usage accounting, dashboards, retrospectives,
telemetry, context profiling, and data archival.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `trace.py` | `TraceRecorder`, `TraceRenderer` | Records structured task traces as JSON files under `traces/<task_id>.json`. Captures a DAG of timestamped events (agent starts, file reads/writes, completions). `TraceRenderer` formats traces as human-readable text. |
| `usage.py` | `UsageLogger` | Appends `TaskUsageRecord` entries to JSONL files. Each record captures agent names, models, token counts, retries, gate results, duration. |
| `telemetry.py` | `AgentTelemetry` (`TelemetryEvent`) | Logs real-time telemetry entries (tool calls, file operations, errors) to JSONL. Also subscribes to `EventBus` as a catch-all for domain events. |
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
| `classifier.py` | `DataClassifier` (`ClassificationResult`) | Auto-classifies task risk level (`LOW`/`MEDIUM`/`HIGH`/`CRITICAL`) and guardrail preset from task description keywords and file path analysis. Returns `ClassificationResult`. |
| `policy.py` | `PolicyEngine` (`PolicyRule`, `PolicyViolation`, `PolicySet`) | Evaluates agent assignments against `PolicySet` rules. Rule types: `path_block`, `path_allow`, `tool_restrict`, `require_agent`, `require_gate`. Five built-in presets: `standard-dev`, `data-analysis`, `infrastructure`, `regulated-data`, `security`. |
| `compliance.py` | `ComplianceReportGenerator` (`ComplianceEntry`, `ComplianceReport`) | Generates compliance reports from execution data. Checks agent assignments against policy sets, builds `ComplianceReport` with pass/fail entries. |
| `validator.py` | `AgentValidator` (`ValidationResult`) | Validates agent definition files: checks required frontmatter fields, model values, permission modes. |
| `spec_validator.py` | `SpecValidator` (`SpecCheck`, `SpecValidationResult`) | Validates agent output against declared specifications. Runs callable check functions and returns `SpecValidationResult`. |
| `escalation.py` | `EscalationManager` | Manages escalation records (risk-based, policy violation, gate failure). Persists and queries escalation history. |

---

### 5.7 Improve (`core/improve/`)

Agent performance scoring, prompt evolution proposals, experiment tracking,
rollback management, and version control.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `scoring.py` | `PerformanceScorer` (`AgentScorecard`, `TeamScorecard`) | Computes per-agent `AgentScorecard` from usage and retrospective data. Metrics: times used, first-pass rate, retry rate, gate pass rate, token consumption, positive/negative mentions, knowledge gaps cited. Health rating: `strong`, `adequate`, `needs-improvement`, `unused`. Also computes `TeamScorecard` for team composition effectiveness. |
| `evolution.py` | `PromptEvolutionEngine` (`EvolutionProposal`) | Generates `EvolutionProposal` objects with data-driven suggestions for improving agent prompts. Consults scorecards and retrospectives to identify issues and propose changes. |
| `vcs.py` | `AgentVersionControl` (`ChangelogEntry`) | Tracks changes to agent definition files with timestamped backups (`.bak` files) and a `changelog.md`. Supports backup, restore, and changelog append. |
| `loop.py` | `ImprovementLoop` | End-to-end improvement orchestrator. Runs scorer, evolution engine, pattern learner, and budget tuner to produce a consolidated `ImprovementReport`. |
| `experiments.py` | `ExperimentManager` | A/B experiment tracking for improvement proposals. Creates, concludes, and rolls back experiments. |
| `proposals.py` | `ProposalManager` | Manages `Recommendation` lifecycle: propose, apply, reject, track status. |
| `rollback.py` | `RollbackManager` (`RollbackEntry`) | Tracks applied changes with undo snapshots. Supports rollback of individual recommendations. |
| `triggers.py` | `TriggerEvaluator` | Evaluates trigger conditions for automated improvement actions based on `TriggerConfig`. |

---

### 5.8 Learn (`core/learn/`)

Pattern learning, budget optimization, closed-loop issue detection, and
bead-informed plan enrichment from historical execution data.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `pattern_learner.py` | `PatternLearner` | Derives recurring orchestration patterns from usage logs. Groups `TaskUsageRecord` entries by sequencing mode, computes per-group statistics (token usage, retry rates, gate pass rates). Surfaces groups meeting minimum sample size (5+) and confidence threshold (0.7) as `LearnedPattern` objects. Persists to `learned-patterns.json`. Also indexes knowledge gap records by `(agent_name, task_type)` for gap-suggested attachments. |
| `budget_tuner.py` | `BudgetTuner` | Analyzes historical token usage and recommends budget tier changes. Groups tasks by sequencing mode, computes median token usage per group, recommends upgrade/downgrade between `lean` (0-50K), `standard` (50K-500K), and `full` (500K+) tiers. Minimum 3 records per group before generating recommendations. |
| `engine.py` | `LearningEngine` | Closed-loop orchestrator: `detect(state)` scans execution results for routing mismatches, agent failures, gate/stack mismatches, and knowledge gaps -- writing issues to the `LearningLedger`. `analyze()` computes confidence from occurrence counts and proposes auto-applicable fixes. `apply(issue_id)` dispatches to type-specific resolvers and writes corrections to `learned-overrides.json`. |
| `ledger.py` | `LearningLedger` | SQLite-backed CRUD for `LearningIssue` records in `baton.db`. Deduplicates by `(issue_type, target)` -- repeated signals increment `occurrence_count` and append evidence. Semantic severity escalation (low < medium < high < critical). Federated to `central.db` via `SyncEngine`. |
| `overrides.py` | `LearnedOverrides` | Reads/writes `.claude/team-context/learned-overrides.json` -- the persistence layer for auto-applied corrections. Stores flavor map overrides, gate command overrides, and agent drops. Atomic write via tempfile+rename. Consumed by `AgentRouter.route()` and `IntelligentPlanner`. |
| `resolvers.py` | *(functions)* | Type-specific resolution strategies: `resolve_routing_mismatch` (writes FLAVOR_MAP override), `resolve_agent_degradation` (adds agent drop), `resolve_knowledge_gap` (creates knowledge pack stub), `resolve_gate_mismatch` (writes gate command override), `resolve_roster_bloat` (adjusts classifier settings). |
| `interviewer.py` | `LearningInterviewer` | Structured CLI dialogue for human-directed learning decisions. Presents issues one at a time with evidence summaries and multiple-choice options. Records decisions back to the ledger. Invoked via `baton learn interview`. |
| `recommender.py` | `Recommender` | Unified recommendation aggregator. Runs all analysis engines (budget tuner, pattern learner, performance scorer, prompt evolution engine) and produces a single, deduplicated, ranked list of `Recommendation` objects with guardrail enforcement (prompt changes never auto-apply, budget changes auto-apply only downward, routing changes require high confidence). |
| `bead_analyzer.py` | `BeadAnalyzer` | Mines historical beads to produce `PlanStructureHint` objects. Three analysis passes: warning frequency (recommend review phases), discovery file clustering (recommend context files), decision reversal detection (recommend approval gates). |

---

### 5.9 Events (`core/events/`)

In-process event bus, domain event factories, append-only persistence, and
materialized view projections.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `bus.py` | `EventBus` | In-process pub/sub with `fnmatch`-style glob topic routing. Synchronous: handlers called inline during `publish()`. Auto-assigns monotonic sequence numbers per `task_id`. Full in-memory history. |
| `events.py` | Factory functions | 19 domain event factories: `step_dispatched()`, `step_completed()`, `step_failed()`, `bead_created()`, `bead_conflict()`, `gate_required()`, `gate_passed()`, `gate_failed()`, `human_decision_needed()`, `human_decision_resolved()`, `task_started()`, `task_completed()`, `task_failed()`, `phase_started()`, `phase_completed()`, `approval_required()`, `approval_resolved()`, `plan_amended()`, `team_member_completed()`. Each returns an `Event` with the correct topic and payload. |
| `persistence.py` | `EventPersistence` | Append-only JSONL event log per task. Independent of `EventBus` -- can be wired as a subscriber or used standalone. Supports replay with sequence and topic filters. |
| `projections.py` | `project_task_view()`, `TaskView`, `PhaseView`, `StepView` | Materializes a `TaskView` (with `PhaseView` and `StepView` children) from a list of events. Read-only, never mutates events. Used by dashboard and status commands. |

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
projects, a consultative plan creation workflow, and end-to-end lifecycle
management from plan creation through code review and merge.

#### Components

| Module | Class | Role |
|--------|-------|------|
| `store.py` | `PmoStore` | Read/write PMO config (`pmo-config.json`) and completed-plan archive (`pmo-archive.jsonl`). Atomic writes via tmp+rename. |
| `scanner.py` | `PmoScanner` | Scans registered projects and builds Kanban board state. Reads execution state from each project's storage backend, maps `ExecutionState.status` to PMO columns (`queued`, `executing`, `awaiting_human`, `validating`, `review`, `deployed`). |
| `forge.py` | `ForgeSession` | Consultative plan creation with SSE progress streaming. Delegates to `IntelligentPlanner.create_plan()` with project-scoped context. Uses `HeadlessClaude` for LLM-quality plan generation when available. |

PMO data now lives in `central.db` (not a separate `pmo.db`). First-run
migration from legacy `pmo.db` is handled by `get_pmo_central_store()`.

#### PMO Workflow Lifecycle

The PMO UI supports a complete plan-to-merge lifecycle:

```
Forge (plan) -> Edit (refine) -> Execute (dispatch agents) -> Review (changelist) -> Merge/PR
```

1. **Plan creation** -- Forge generates a plan with SSE progress streaming
   through 5 stages (Analyzing, Routing, Sizing, Generating, Validating).
2. **Plan editing** -- PlanEditor supports model selection per step,
   dependency multi-select, tag inputs for deliverables/paths/context_files,
   and gate editing.
3. **Execution** -- Launch from Kanban board with pause/resume/cancel
   controls (SIGSTOP/SIGCONT/SIGTERM), retry-step and skip-step for
   failed steps, and bead alert flags for warning/incident signals.
4. **Code review** -- After execution, the `review` Kanban column presents
   ChangelistPanel with a file tree grouped by agent, diff stats, and
   merge/PR buttons. `CommitConsolidator` (lazily imported from
   `core/engine/consolidator`) handles cherry-pick rebase with
   topological sort for dependency ordering.
5. **Merge and PR** -- POST `/pmo/cards/{id}/merge` performs a fast-forward
   merge; POST `/pmo/cards/{id}/create-pr` creates a GitHub PR via `gh`.

#### Role-Based Approval

The `users` and `approval_log` tables in central.db track identity and
audit trail. `UserIdentityMiddleware` (`api/middleware/user_identity.py`)
resolves caller identity from `X-Baton-User` header, Bearer token, or
fallback to `"local-user"`. The `BATON_APPROVAL_MODE` environment variable
controls approval policy (`local` = self-approval permitted, `team` =
different user required).

---

### 5.11 Distribute (`core/distribute/`)

Packaging, verification, registry management, and experimental features.

#### Production Modules

| Module | Class | Role |
|--------|-------|------|
| `sharing.py` | `PackageBuilder` (`PackageManifest`) | Creates distributable `.tar.gz` archives with `manifest.json`, agent definitions, references, knowledge packs. Path traversal protection on extraction. |
| `packager.py` | `PackageVerifier` (`PackageDependency`, `EnhancedManifest`, `PackageValidationResult`) | Validates package archives: checksum verification, dependency tracking, structural checks. Returns `PackageValidationResult` with `valid`, `errors`, `warnings`, `checksums`. |
| `registry_client.py` | `RegistryClient` | Manages a local registry directory (typically a git repo) with an `index.json` and versioned `packages/` subdirectories. Handles publish and pull operations. |

#### Experimental Modules (`experimental/`)

| Module | Class | Role |
|--------|-------|------|
| `async_dispatch.py` | `AsyncDispatcher` (`AsyncTask`) | Scaffolding for async task dispatch. Not exercised in production. |
| `incident.py` | `IncidentManager` (`IncidentPhase`, `IncidentTemplate`) | Incident response templates and phase tracking (P1-P4 templates). Not exercised in production. |
| `transfer.py` | `ProjectTransfer` (`TransferManifest`) | Cross-project knowledge and configuration transfer. Not exercised in production. |

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
  3. FallbackClassifier.classify()       |
     (HaikuClassifier -> KeywordClassifier)
  4. PatternLearner.find_pattern()       |
  5. BudgetTuner.recommend()             |
  6. DataClassifier.classify()           |
  7. PolicyEngine.evaluate()             |
  8. AgentRouter.resolve_agents()        |
  9. KnowledgeResolver.resolve()         |
 10. BeadAnalyzer.analyze() (structure hints)
 11. Sequence into PlanPhase/PlanStep    |
 12. Assign gates and approvals          |
 13. Build MachinePlan                   |
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
     +-- parse_bead_signals(outcome) -> beads created
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

### 6.4 Headless Execution Flow

```
"baton execute run"
     |
     +-- HeadlessClaude
     |       |
     |       +-- claude --print (subprocess)
     |       +-- Drives full start -> dispatch -> gate -> complete loop
     |       +-- No Claude Code session required
     |
     +-- Also used by PMO UI execute endpoint
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
 |-- complexity: str (light | medium | heavy)
 |-- classification_source: str (haiku | keyword-fallback)
 |-- detected_stack: str | None
 |-- explicit_knowledge_packs: list[str]
 |-- explicit_knowledge_docs: list[str]
 |-- resource_limits: ResourceLimits | None
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
           |-- mcp_servers: list[str]
           |-- synthesis: SynthesisSpec | None
           |    |-- strategy: str (concatenate | merge_files | agent_synthesis)
           |    |-- synthesis_agent: str
           |    |-- synthesis_prompt: str
           |    |-- conflict_handling: str (auto_merge | escalate | fail)
           |-- team: list[TeamMember]
                |-- member_id: str (e.g., "1.1.a")
                |-- agent_name: str
                |-- role: str (lead | implementer | reviewer)
                |-- task_description: str
                |-- model: str
                |-- depends_on: list[str]
                |-- deliverables: list[str]
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

### 7.3 Bead Model

```
Bead
 |-- bead_id: str (e.g., "bd-a1b2")
 |-- task_id: str
 |-- step_id: str
 |-- agent_name: str
 |-- bead_type: str (discovery | decision | warning | outcome | planning)
 |-- content: str
 |-- confidence: str (high | medium | low)
 |-- scope: str (step | phase | task | project)
 |-- tags: list[str]
 |-- affected_files: list[str]
 |-- status: str (open | closed | archived)
 |-- created_at: str
 |-- closed_at: str
 |-- summary: str
 |-- links: list[BeadLink]
 |    |-- target_bead_id: str
 |    |-- link_type: str (blocks | blocked_by | relates_to |
 |    |                    discovered_from | validates | contradicts | extends)
 |    |-- created_at: str
 |-- source: str (agent-signal | planning-capture | retrospective | manual)
 |-- token_estimate: int
 |-- quality_score: float
 |-- retrieval_count: int
```

#### BeadSynthesizer (Wave 2.1)

`agent_baton/core/intel/bead_synthesizer.py` turns flat beads into a graph
post-phase. It infers undirected edges into `bead_edges`
(`file_overlap`, `tag_overlap`, `conflict`) using jaccard similarity, then
walks connected components over file-overlap edges with weight ≥ 0.3 to
populate `bead_clusters`. Conflict detection flags pairs of `warning` beads
that share a primary tag but have <0.2 content-token overlap. Synthesis is
fully deterministic (no embeddings, no LLM calls), idempotent, and
best-effort — failures log at debug and never block phase advancement.
CLI surface: `baton beads synthesize` (manual trigger) and `baton beads
clusters` (list components).

#### HandoffSynthesizer (Wave 3.2)

`agent_baton/core/intel/handoff_synthesizer.py` synthesizes a compact
(≤400-char) "Handoff from Prior Step" section when the dispatcher hands
off from agent N to agent N+1: top-5 files changed, discoveries (beads
created during the prior step), blockers (open `warning` beads whose
files/tags overlap the next step's domain), and a one-line outcome
summary. Persisted to `handoff_beads` (schema v29) for audit; listable
via `baton beads handoffs --task-id <id>`. Fully deterministic, single-
task scope, best-effort. Resolves bd-65d4 / bd-61a5.

#### Multi-Agent Debate (D4, Tier-4 research)

`agent_baton/core/intel/debate.py` runs a structured N-round debate
between 2-5 specialist agents (each given a distinct framing), then
dispatches a moderator agent to synthesize a recommendation plus a list
of unresolved disagreements. Sequential dispatch via a pluggable
`DebateRunner` (HeadlessClaude in production, stub in dry-run/tests).
Persisted to `debates` (schema v30); CLI surface: `baton debate`. Opt-in
only — never auto-invoked by the planner or engine.

### 7.4 Serialization

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
5. Lazily imports and registers 10 route modules
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
| `health.py` | `/api/v1` | 2 | `/health`, `/ready` -- liveness and readiness probes (auth-exempt) |
| `plans.py` | `/api/v1` | 2 | Plan create, list/get |
| `executions.py` | `/api/v1` | 6 | Start, next, record, gate, complete, status |
| `agents.py` | `/api/v1` | 2 | List, get agents |
| `observe.py` | `/api/v1` | 3 | Dashboard, traces, usage records |
| `decisions.py` | `/api/v1` | 3 | Request, resolve, list decisions |
| `events.py` | `/api/v1` | 1 | SSE event stream (requires `sse-starlette`) |
| `webhooks.py` | `/api/v1` | 3 | Register, list, delete/test webhooks |
| `pmo.py` | `/api/v1` | 36 | Board, projects, cards, health, forge (plan/approve/interview/regenerate/progress SSE), execute (launch/pause/resume/cancel/retry-step/skip-step), gates (pending/approve/reject), changelist/merge/create-pr, request-review/approval-log, ADO search, external items/mappings, signals (list/create/resolve/batch-resolve/forge-from-signal), SSE events |
| `learn.py` | `/api/v1` | 5 | Learning issues, detection, application |

**Total: 64 API endpoints across 10 route modules.**

### 8.4 Middleware Stack

```
Request -> CORS -> TokenAuth -> UserIdentity -> Route Handler -> Response
```

- **CORS**: Permits all localhost/127.0.0.1 origins by default. Configurable
  via `allowed_origins`.
- **TokenAuth**: Bearer token validation. Exempt paths: `/api/v1/health`,
  `/api/v1/ready`, `/openapi.json`, `/docs`, `/redoc`. No-op when token
  is None.
- **UserIdentity**: Resolves caller identity from `X-Baton-User` header,
  Bearer token, or `"local-user"` fallback. Sets `request.state.user_id`
  and `request.state.user_role`. Controlled by `BATON_APPROVAL_MODE` env
  var (`local` or `team`).

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
    main.tsx              Vite entry point
    App.tsx               Root component with routing
    components/
      AdoCombobox.tsx     Azure DevOps work item search
      AnalyticsDashboard.tsx  Program analytics and metrics
      ChangelistPanel.tsx Post-execution code review (file tree by agent, diff stats)
      ConfirmDialog.tsx   Confirmation modal
      ExecutionProgress.tsx  Live execution progress with interrupt controls
      ForgePanel.tsx      Plan creation wizard with SSE progress streaming
      GateApprovalPanel.tsx  Gate approval/rejection UI
      HealthBar.tsx       Program health visualization
      InterviewPanel.tsx  Forge interview flow
      KanbanBoard.tsx     Main board view (6 columns)
      KanbanCard.tsx      Card component with review/merge actions
      KeyboardShortcutsDialog.tsx  Keyboard shortcuts help
      PlanEditor.tsx      Advanced plan editing (model/deps/tags/gates)
      PlanPreview.tsx     Read-only plan display
      ReviewPanel.tsx     Role-based review and approval
      SignalsBar.tsx      PMO signal notifications
    contexts/
      ToastContext.tsx    Toast notification provider
    hooks/
      useHotkeys.ts       Keyboard shortcut bindings
      usePersistedState.ts  localStorage-backed state
      usePmoBoard.ts       Board data fetching hook
    api/
      client.ts           API client (fetch wrappers for /api/v1/pmo/*)
      types.ts            TypeScript type definitions
    styles/
      index.css           Global styles
      tokens.ts           Design tokens (6 Kanban columns, severity/priority colors)
    utils/
      agent-names.ts      Agent display name mapping
```

- Built assets are served at `/pmo/` by the FastAPI `StaticFiles` mount.
- The UI communicates exclusively through the REST API (`/api/v1/pmo/*`).
- No direct SQLite access from the frontend.
- Six Kanban columns: `queued`, `executing`, `awaiting_human`,
  `validating`, `review` (post-execution changelist), `deployed`.

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
| Observability | `observe/` | `dashboard`, `trace`, `usage`, `telemetry`, `context-profile`, `retro`, `cleanup`, `migrate-storage`, `context`, `query` |
| Governance | `govern/` | `classify`, `compliance`, `policy`, `escalations`, `validate`, `spec-check`, `detect` |
| Improvement | `improve/` | `scores`, `evolve`, `patterns`, `budget`, `changelog`, `anomalies`, `experiment`, `improve`, `learn` |
| Distribution | `distribute/` | `package`, `publish`, `pull`, `verify-package`, `install`, `transfer` |
| Agents | `agents/` | `agents`, `route`, `events`, `incident` |
| (top-level) | `commands/` | `pmo`, `sync`, `query`, `source`, `serve`, `beads`, `uninstall` |

**Total: 49 command modules across 7 groups.**

### Commands with Subcommands

Several top-level commands have their own subcommand trees:

| Command | Subcommands |
|---------|-------------|
| `baton beads` | `list`, `show`, `ready`, `close`, `link`, `cleanup`, `promote`, `graph` |
| `baton pmo` | `serve`, `status`, `add`, `health` |
| `baton source` | `add`, `list`, `sync`, `remove`, `map` |
| `baton learn` | `status`, `issues`, `detect`, `apply`, `interview`, `history`, `reset` |
| `baton experiment` | `list`, `show`, `conclude`, `rollback` |
| `baton context` | `current`, `briefing`, `gaps` |

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

## 12. Bead Memory System

### Overview

Beads are structured units of agent memory inspired by Steve Yegge's Beads
project (beads-ai/beads-cli). They capture discrete insights -- discoveries,
decisions, warnings, outcomes, and planning notes -- produced during execution.
Unlike raw agent output, beads are typed, queryable, and persist across
steps, phases, and executions.

### Bead Lifecycle

```
Agent output -> parse_bead_signals() -> BeadStore.create() -> EventBus (bead.created)
                                                                  |
                                                                  v
                                              BeadSelector.select() -> delegation prompt
                                                  (next step's agent inherits context)
                                                                  |
                                              parse_bead_feedback() -> quality_score update
                                                                  |
                                              decay_beads() -> archived (retention-based)
```

### Signal Protocol

Agents emit bead signals in their output:

```
BEAD_DISCOVERY: <insight text>
BEAD_DECISION: <decision text>
BEAD_WARNING: <warning text>
```

Agents provide feedback on inherited beads:

```
BEAD_USEFUL: bd-a1b2 0.9
BEAD_STALE: bd-c3d4 0.2
```

### Bead Selection (Tier System)

`BeadSelector` uses a three-tier priority system for prompt injection:

1. **Dependency-chain** (highest) -- beads from steps that the current step
   depends on (directly or transitively).
2. **Same-phase** -- beads from other steps in the same phase.
3. **Cross-phase** (lowest) -- beads from other phases.

Within each tier, beads are ranked by type (warning > discovery > decision >
outcome > planning) and by quality score. Total selection is constrained by
token budget (default 4096) and max bead count (default 5).

### Bead-Informed Planning

`BeadAnalyzer` mines historical beads to produce `PlanStructureHint` objects:

- **Warning frequency** -- when the same file appears in many warning beads,
  recommend adding a review phase.
- **Discovery clustering** -- when multiple discoveries reference the same
  file, surface it as a context file for the next agent.
- **Decision reversal** -- when a decision is later contradicted, recommend
  an approval gate.

### Bead ID Generation

Uses SHA-256 of `task_id:step_id:content:timestamp` with progressive scaling:

| Bead count | ID length | Namespace size |
|------------|-----------|----------------|
| < 500 | 4 hex chars | ~65K |
| 500-1499 | 5 hex chars | ~1M |
| >= 1500 | 6 hex chars | ~16M |

All IDs are prefixed with `bd-` (e.g., `bd-a1b2`).

---

## 12.5 Project Config (`baton.yaml`)

Optional, additive project-level config loaded by
`agent_baton.core.config.ProjectConfig.load()` (walks up from cwd).
Lets a project declare `default_agents`, `default_gates`,
`default_isolation`, `auto_route_rules`, and `excluded_paths` so
`baton plan` doesn't need repeated CLI flags. The planner applies these
in `_apply_project_config()` after stack-aware QA gates — empty/missing
configs are a complete no-op. Inspect/scaffold via `baton config show`,
`baton config init`, and `baton config validate`.

---

## 13. Cross-Cutting Concerns

### 13.1 Error Handling

- **State persistence**: Atomic writes (tmp+rename for JSON, WAL mode for
  SQLite). Parse errors in `from_dict()` fall through to `None` returns
  rather than raising.
- **Auto-sync**: Wrapped in `try/except` at `baton execute complete`. Sync
  failure never blocks execution completion.
- **API routes**: Missing route modules are skipped with a warning (graceful
  degradation if optional dependencies like `sse-starlette` are absent).
- **Storage fallback**: When SQLite save fails, the engine falls back to file
  persistence and logs a warning.

### 13.2 Logging

Module-level loggers via `logging.getLogger(__name__)`. The daemon configures
a `RotatingFileHandler` to `daemon.log` (or `worker.log` in namespaced mode).
CLI commands use stderr for user-facing messages.

### 13.3 Configuration

Configuration is file-based, not environment-variable-based:

- Agent definitions: `.claude/agents/*.md` (frontmatter + markdown body)
- Knowledge packs: `.claude/knowledge/*/knowledge.yaml` + document files
- PMO config: `~/.baton/pmo-config.json`
- Webhook subscriptions: `.claude/team-context/webhooks.json`
- Policy rules: loaded from JSON by `PolicyEngine`
- Learned overrides: `.claude/team-context/learned-overrides.json`

The environment variables the system reads are `BATON_TASK_ID` (for
session binding), `BATON_APPROVAL_MODE` (approval policy: `local` or
`team`), and adapter-specific PAT variables (e.g., the ADO adapter reads
the env var name stored in its config).

### 13.4 State Persistence Layout

```
.claude/team-context/
  baton.db                          SQLite database (new default)
  execution-state.json              Legacy flat state file
  active-task-id.txt                Pointer to default task
  learned-overrides.json            Auto-applied learning corrections
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

### 13.5 Dispatch Verification (bd-edbf)

`baton execute verify-dispatch <step_id>` and `baton execute audit-isolation`
provide read-only post-hoc compliance checks for the worktree-isolation
contract. The `DispatchVerifier` (`agent_baton/core/audit/`) compares each
recorded `StepResult.files_changed` against the dispatched `PlanStep.allowed_paths`
(falling back to `git diff-tree` when files_changed is empty but commit_hash
is present), and validates that any recorded commit hash resolves in the repo.
Both commands are read-only by contract — they never mutate state, plans, or
git — and exit non-zero on any definite violation so CI pipelines can gate on
isolation compliance without re-running the executor.

### 13.6 Wave 1.3 — Worktree Isolation

**Module:** `agent_baton/core/engine/worktree_manager.py` — `WorktreeManager`

Public API: `create(task_id, step_id, base_branch) -> WorktreeHandle`,
`fold_back(handle, commit_hash, strategy) -> str`,
`cleanup(handle, on_failure, force)`,
`handle_for(task_id, step_id) -> WorktreeHandle | None`,
`gc(max_age_hours, dry_run) -> list[str]`.

**Lifecycle:** `mark_dispatched()` calls `create()` to materialise a git
worktree under `.claude/worktrees/<task_id>/<step_id>/`.  On step completion,
`record_step_result()` calls `fold_back()` then `cleanup()`.  On step failure,
the worktree is **retained** for forensics / Wave 5.1 takeover (`on_failure=True`
is a no-op in `cleanup()`).

**State fields (ExecutionState):**
- `step_worktrees: dict[str, dict]` — maps `step_id` to serialised `WorktreeHandle`; absent in legacy files (all accessors use `getattr(..., {})`)
- `working_branch: str` — git branch captured at `start()` time, used as `base_branch` for every `create()` call
- `working_branch_head: str` — SHA of the rebased tip after the most-recent successful `fold_back()` (bd-def9)

**ExecutionAction additions:** `worktree_path: str` and `worktree_branch: str`
are populated on DISPATCH actions when isolation is `"worktree"`.

**CLI:** `baton execute worktree-gc [--max-age-hours N] [--dry-run]` reclaims
stale worktrees (retained failures older than N hours).

**Backward-compat toggle:** set `BATON_WORKTREE_ENABLED=0` to disable worktree
creation entirely; all lifecycle methods become no-ops.

See `docs/specs/velocity-engine-spec.md` (Wave 1.3) for the full design.

---

## 14. Extension Points

### 14.1 Adding a New Agent

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

### 14.2 Adding a New Storage Backend

Implement the `StorageBackend` protocol from `core/storage/protocol.py`.
The protocol has 34 methods covering execution state, plans, steps, gates,
usage, retrospectives, traces, events, patterns, budget, mission log,
context, and profile data. Register the backend in
`core/storage/__init__.py`'s `get_project_storage()` factory.

### 14.3 Adding a New External Source Adapter

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

### 14.4 Adding a New CLI Command

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

### 14.5 Adding a New Knowledge Pack

Create a directory under `.claude/knowledge/<pack-name>/` with:

```
knowledge.yaml      # name, description, tags, target_agents, documents list
doc1.md             # knowledge document with optional YAML frontmatter
doc2.md
```

The `KnowledgeRegistry` auto-discovers packs from `.claude/knowledge/`
(project) and `~/.claude/knowledge/` (global).

---

## 15. Dependency Graph

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
| `TaskClassifier` | `core/engine/classifier.py` | `IntelligentPlanner` |
| `RetroEngine` | `core/engine/planner.py` | `IntelligentPlanner` |
| `ExternalSourceAdapter` | `core/storage/adapters/__init__.py` | `AdoAdapter`, CLI source commands |
| `_print_action()` | `cli/commands/execution/execute.py` | Claude (parses stdout) |
| `execution-state.json` | `core/engine/persistence.py` | `baton execute resume` |

---

## 16. Functional Domains

### Domain 1: Plan Creation

| Attribute | Value |
|-----------|-------|
| Entry | `baton plan "task" [--save] [--explain] [--knowledge ...] [--knowledge-pack ...] [--intervention ...]` |
| Path | `cli/plan_cmd.py` -> `IntelligentPlanner` -> `FallbackClassifier` -> `AgentRouter` + `AgentRegistry` -> `PatternLearner` + `BudgetTuner` -> `PolicyEngine` -> `KnowledgeResolver` -> `BeadAnalyzer` |
| Output | `plan.json` + `plan.md` in `.claude/team-context/` |

### Domain 2: Execution Lifecycle

| Attribute | Value |
|-----------|-------|
| Entry | `baton execute start` / `next` / `record` / `gate` / `approve` / `complete` / `run` / `resume` / `dispatched` / `amend` / `team-record` / `list` / `switch` |
| Path | `cli/execute.py` -> `ExecutionEngine` -> `StatePersistence` / `SqliteStorage` -> `PromptDispatcher` -> `GateRunner` -> `EventBus` |
| Output | `execution-state.json`, delegation prompts via `_print_action()` |

### Domain 3: Knowledge Delivery

| Attribute | Value |
|-----------|-------|
| Entry | `--knowledge` / `--knowledge-pack` on `baton plan`; `KNOWLEDGE_GAP` in agent output |
| Path | `IntelligentPlanner` -> `KnowledgeRegistry` -> `KnowledgeResolver` -> `KnowledgeRanker` -> `PromptDispatcher` -> `KnowledgeGap` handler |
| Output | Knowledge blocks in delegation prompts; `KnowledgeGapRecord` in retrospectives |

#### Knowledge Ranking (bd-0184)

After `KnowledgeResolver` produces candidates for each step, `KnowledgeRanker`
(`agent_baton/core/intel/knowledge_ranker.py`) re-orders them by a deterministic
composite score: `effectiveness_score * 0.6 + recency_factor * 0.2 + usage_factor * 0.2`.
Scores are read from `v_knowledge_effectiveness` in `central.db`; missing telemetry
yields a neutral 0.5 so documents with no history sort stably. The planner then
caps the list at `BATON_MAX_KNOWLEDGE_PER_STEP` (default 8) before attaching to
the step. The full ranked table is exposed via `baton knowledge ranking`.

### Domain 4: Federated Sync

| Attribute | Value |
|-----------|-------|
| Entry | `baton sync` / `baton sync --all` / auto-sync on complete |
| Path | `cli/sync_cmd.py` -> `SyncEngine` -> sqlite3 (project -> central) |
| Output | Rows mirrored to `central.db` with `project_id` prepended |

### Domain 5: Improvement Loop

| Attribute | Value |
|-----------|-------|
| Entry | `baton scores` / `patterns` / `budget` / `evolve` / `changelog` / `improve` / `anomalies` / `experiment` |
| Path | `cli/improve/` -> `ImprovementLoop` -> `PerformanceScorer` -> `PatternLearner` -> `BudgetTuner` -> `PromptEvolutionEngine` -> `ExperimentManager` -> `ProposalManager` -> `RollbackManager` -> `AgentVersionControl` |
| Output | Scorecards, patterns, budget recommendations, evolution proposals, experiments, anomalies |

### Domain 6: Governance

| Attribute | Value |
|-----------|-------|
| Entry | `baton classify` / `compliance` / `policy` / `validate` / `spec-check` / `detect` / `escalations` |
| Path | `cli/govern/` -> `DataClassifier` -> `PolicyEngine` -> `ComplianceReportGenerator` -> `SpecValidator` -> `AgentValidator` -> `EscalationManager` |
| Output | Risk classification, policy violations, compliance reports, validation results |

### Domain 7: Observability

| Attribute | Value |
|-----------|-------|
| Entry | `baton trace` / `dashboard` / `usage` / `telemetry` / `retro` / `context-profile` / `cleanup` / `migrate-storage` / `context` / `query` |
| Path | `cli/observe/` -> `TraceRecorder` -> `UsageLogger` -> `DashboardGenerator` -> `RetrospectiveEngine` -> `AgentTelemetry` -> `ContextProfiler` -> `DataArchiver` -> `QueryEngine` |
| Output | Traces, usage reports, dashboards, retrospectives, telemetry events, context profiles, query results |

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
| Path | `cli/pmo_cmd.py` -> `PmoSqliteStore` -> `PmoScanner` -> `ForgeSession` -> API (`routes/pmo.py`) -> `CommitConsolidator` -> `UserIdentityMiddleware` |
| Output | PMO board data in `central.db`; React UI at `/pmo/`; approval audit trail in `approval_log` |

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
| Path | `cli/serve.py` -> `create_app()` -> 10 route modules -> backing subsystems |
| Output | HTTP API (64 endpoints), SSE event streams, webhook deliveries |

### Domain 12: External Sources

| Attribute | Value |
|-----------|-------|
| Entry | `baton source add ado` / `list` / `sync` / `remove` / `map` |
| Path | `cli/source_cmd.py` -> `ExternalSourceAdapter` protocol -> `AdoAdapter` -> `CentralStore` |
| Output | Source registrations, synced work items, mappings in `central.db` |

### Domain 13: Closed-Loop Learning

| Attribute | Value |
|-----------|-------|
| Entry | `baton learn status` / `issues` / `detect` / `apply` / `interview` / `history` / `reset` |
| Path | `cli/learn_cmd.py` -> `LearningEngine` -> `LearningLedger` -> `LearnedOverrides` -> `LearningInterviewer` -> resolvers |
| Output | Learning issues, auto-applied fixes in `learned-overrides.json`, interview transcripts |

### Domain 14: Bead Memory

| Attribute | Value |
|-----------|-------|
| Entry | `baton beads list` / `show` / `ready` / `close` / `link` / `cleanup` / `promote` / `graph` |
| Path | `cli/bead_cmd.py` -> `BeadStore` -> `BeadSelector` -> `BeadAnalyzer` -> `bead_decay` |
| Output | Bead CRUD in `baton.db`, bead injection into delegation prompts, plan structure hints |

### Domain 15: Cross-Project Query

| Attribute | Value |
|-----------|-------|
| Entry | `baton query "SQL"` / `baton query agents` / `baton query tasks` / `baton query gaps` / `baton query gates` / `baton query costs` |
| Path | `cli/query_cmd.py` -> `QueryEngine` -> `central.db` or `baton.db` |
| Output | Tabular query results from structured helpers or raw SQL |

---

## 17. Distributable Artifacts

### Agent Definitions (22 files in `agents/`)

| Agent | File |
|-------|------|
| `orchestrator` | `orchestrator.md` |
| `architect` | `architect.md` |
| `backend-engineer` | `backend-engineer.md` |
| `backend-engineer--python` | `backend-engineer--python.md` |
| `backend-engineer--node` | `backend-engineer--node.md` |
| `frontend-engineer` | `frontend-engineer.md` |
| `frontend-engineer--react` | `frontend-engineer--react.md` |
| `frontend-engineer--dotnet` | `frontend-engineer--dotnet.md` |
| `test-engineer` | `test-engineer.md` |
| `code-reviewer` | `code-reviewer.md` |
| `auditor` | `auditor.md` |
| `talent-builder` | `talent-builder.md` |
| `security-reviewer` | `security-reviewer.md` |
| `devops-engineer` | `devops-engineer.md` |
| `data-engineer` | `data-engineer.md` |
| `data-analyst` | `data-analyst.md` |
| `data-scientist` | `data-scientist.md` |
| `visualization-expert` | `visualization-expert.md` |
| `subject-matter-expert` | `subject-matter-expert.md` |

### Reference Documents (16 files in `references/`)

| Reference | File |
|-----------|------|
| Adaptive Execution | `adaptive-execution.md` |
| Agent Routing | `agent-routing.md` |
| Baton Engine Guide | `baton-engine.md` |
| Design Patterns | `baton-patterns.md` |
| Communication Protocols | `comms-protocols.md` |
| Cost and Budget | `cost-budget.md` |
| Decision Framework | `decision-framework.md` |
| Documentation Generation | `doc-generation.md` |
| Failure Handling | `failure-handling.md` |
| Git Strategy | `git-strategy.md` |
| Guardrail Presets | `guardrail-presets.md` |
| Hooks Enforcement | `hooks-enforcement.md` |
| Knowledge Architecture | `knowledge-architecture.md` |
| Research Procedures | `research-procedures.md` |
| Task Sequencing | `task-sequencing.md` |

### Knowledge Packs (3 packs in `.claude/knowledge/`)

| Pack | Documents |
|------|-----------|
| `agent-baton` | `agent-format.md`, `architecture.md`, `development-workflow.md` |
| `ai-orchestration` | `agent-evaluation.md`, `context-economics.md`, `multi-agent-patterns.md`, `prompt-engineering-principles.md` |
| `case-studies` | `failure-modes.md`, `orchestration-frameworks.md`, `scaling-patterns.md` |
