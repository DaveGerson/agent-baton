# Architecture Decision Records

These records document the rationale behind significant structural and
architectural decisions in the `agent_baton` package. ADRs 01-10 originate
from the 2026-03-23 re-architecture. Later ADRs document decisions made as
the system matured. Future contributors should consult this document before
proposing changes that would reverse these decisions.

---

## ADR-01: Remove the Dual Plan Model Hierarchy

**Decision**: Delete `ExecutionPlan`, `Phase`, `AgentAssignment`, `QAGate`,
and `PlanBuilder`; make `MachinePlan` the sole plan type.

**Context**: Two parallel plan hierarchies existed -- `models/plan.py`
(`ExecutionPlan`) from Epic 1 and `models/execution.py` (`MachinePlan`) from
Epic 2. The engine, runtime, and CLI all used `MachinePlan` exclusively.
`ExecutionPlan` was constructed only by `PlanBuilder` and consumed only by
`ContextManager.write_plan()`, which called `plan.to_markdown()` -- a method
`MachinePlan` already implements. The dual hierarchy caused contributor
confusion because the structurally similar names (`Phase` vs `PlanPhase`,
`ExecutionPlan` vs `MachinePlan`) gave no signal about which was active.
`MissionLogEntry` from `models/plan.py` was retained and moved to a dedicated
`models/mission_log.py`.

**Status**: Implemented (2026-03-23)

---

## ADR-02: Remove the Backward-Compatibility Shim Layer

**Decision**: Delete all 21 `core/*.py` shim files and migrate every consumer
to canonical sub-package import paths.

**Context**: When Epic 2 reorganized `core/` from a flat layout into
sub-packages, 21 single-line re-export shims were left at `core/*.py` so
existing imports would not break. All CLI commands and pre-reorganization tests
imported from these paths. Internal core code migrated to canonical paths
immediately. The shims imposed a cognitive cost: browsing `agent_baton/core/`
showed 22 flat files before the actual sub-packages, every class had two valid
import paths, and `core/__init__.py` re-exported 48 symbols with no documented
rationale. The migration was entirely mechanical -- find-and-replace of import
strings -- with Python's import errors providing an immediate safety net.

**Status**: Implemented (2026-03-23)

---

## ADR-03: Formalize the Worker-Engine Contract as a Protocol

**Decision**: Introduce `ExecutionDriver` in `core/engine/protocols.py` as a
`typing.Protocol` (runtime-checkable) that specifies the interface `TaskWorker`
and `WorkerSupervisor` use when calling the engine.

**Context**: `TaskWorker` called `ExecutionEngine` through eight distinct
methods (`start`, `next_action`, `next_actions`, `mark_dispatched`,
`record_step_result`, `record_gate_result`, `complete`, `status`). This was the
most critical runtime contract in the system, yet it was not declared anywhere.
There was no way to inject a mock engine into tests without subclassing
`ExecutionEngine` or monkey-patching. The Protocol approach requires no changes
to `ExecutionEngine` (structural typing satisfies it automatically) and allows
alternative engine implementations in tests and future integrations.

**Status**: Implemented (2026-03-23)

---

## ADR-04: Assign Exclusive Event Ownership to Prevent Duplicate Publishing

**Decision**: `ExecutionEngine` owns `task.*`, `phase.*`, and `gate.*` events.
`TaskWorker` owns `step.*` events. Neither publishes the other's topics.

**Context**: Both `ExecutionEngine.record_step_result()` and
`TaskWorker._execution_loop()` published `step.dispatched`, `step.completed`,
and `step.failed` to the same `EventBus`. `EventPersistence`, auto-wired as a
subscriber in the engine, wrote every event to a JSONL file. Each step
completion produced two identical JSONL records, causing projections (e.g.,
`project_task_view`) to silently double-count step activity. The fix assigns
authoritative ownership: the engine publishes lifecycle transitions it knows
about (task boundaries, phase transitions, gate results); the worker publishes
the step-level events it witnesses directly (dispatch, completion, failure).

**Status**: Implemented (2026-03-23)

---

## ADR-05: Extract StatePersistence from ExecutionEngine

**Decision**: Extract state I/O from `executor.py` into a `StatePersistence`
class in `core/engine/persistence.py`. `ExecutionEngine` accepts an optional
`StatePersistence` instance.

**Context**: At 878 lines, `executor.py` mixed four concerns: state machine
logic (~400 LOC), state persistence (~150 LOC), observability wiring (~150
LOC), and utilities (~180 LOC). The state machine could not be unit-tested
without real filesystem paths, because `_save_state` and `_load_state` were
private methods on the same class. Extracting `StatePersistence` as an
injectable dependency lets tests construct an engine with an in-memory state
provider, testing `_determine_action` without disk I/O.

**Status**: Implemented (2026-03-23)

---

## ADR-06: Document Core vs Peripheral Layering Explicitly

**Decision**: Reduce `core/__init__.py` to 3 canonical re-exports
(`AgentRegistry`, `AgentRouter`, `ContextManager`) and update
`agent_baton/__init__.py` to expose the primary execution surface directly from
canonical paths.

**Context**: All 9 sub-packages sat at the same directory level with no
indication of which formed the primary execution path. `agent_baton/__init__.py`
exposed only `PlanBuilder` and `ContextManager` -- not `ExecutionEngine` or
`TaskWorker` -- inverting the package's actual usage pattern. The package-level
exports now reflect what users actually need: `ExecutionEngine`, `TaskWorker`,
`MachinePlan`, `AgentLauncher`, and the orchestration types. The dependency
hierarchy (models -> events/observe/govern -> engine -> runtime -> CLI) is
documented in `core/__init__.py`.

**Status**: Implemented (2026-03-23)

---

## ADR-07: Gate Experimental distribute Modules Behind a Subdirectory

**Decision**: Move `core/distribute/async_dispatch.py`, `incident.py`, and
`transfer.py` into `core/distribute/experimental/`. Retain `packager.py` and
`registry_client.py` at the top level as production modules.

**Context**: Three modules in `core/distribute/` were scaffolding-level
implementations not exercised in production execution paths. They coexisted
with production-ready `packager.py` and `registry_client.py` at the same level.
Contributors could not distinguish production from experimental modules without
reading each file. The `experimental/` subdirectory is a clear, searchable
signal. Any future caller that imports from `core.distribute.experimental`
knows explicitly what it is getting.

**Status**: Implemented (2026-03-23)

---

## ADR-08: Group CLI Commands into Domain Subdirectories

**Decision**: Reorganize the 35 flat `cli/commands/*.py` files into 6
subdirectory groups: `execution/`, `observe/`, `govern/`, `improve/`,
`distribute/`, `agents/`. Update `cli/main.py` auto-discovery to scan one level
of subdirectories.

**Context**: A flat directory of 35 files with no organizational structure made
it difficult to locate related commands or understand which domain a new command
should live in. The `cli/main.py` auto-discovery pattern
(`pkgutil.iter_modules`) already registered commands by their internal
`register()` call, not by filename -- so moving files into subdirectories
preserves all registered subcommand strings. The extended discovery scans both
the flat `commands/` directory and any non-underscore subdirectory one level
deep.

**Status**: Implemented (2026-03-23)

---

## ADR-09: Normalize Enum Usage to Typed Instances

**Decision**: Store `ActionType`, `StepStatus`, and `PhaseStatus` as typed
enum instances in dataclass fields. Serialize to `.value` strings only in
`to_dict()` methods.

**Context**: `ActionType`, `StepStatus`, and `PhaseStatus` were declared as
`Enum` classes in `models/execution.py` but `ExecutionAction.action_type` was
typed as `str` and populated with `.value` strings throughout. Comparisons used
the `.value` form (`action.action_type == ActionType.DISPATCH.value`), which
IDE type-checkers could not validate. The inconsistency with `models/enums.py`
(which used typed enum instances internally) made the codebase harder to
navigate. The normalized pattern stores enum instances in fields and reserves
string conversion for the serialization boundary, matching the pattern
established in `models/enums.py`.

**Status**: Implemented (2026-03-23)

---

## ADR-10: Replace Implicit EventBus Auto-Wiring with an Explicit Factory

**Decision**: Introduce `ExecutionContext` in `core/runtime/context.py` as a
factory that explicitly wires `EventBus` and `EventPersistence` together.
`ExecutionEngine` accepts the context object as an alternative to a bare `bus`
parameter.

**Context**: When an `EventBus` was passed to `ExecutionEngine.__init__`, the
constructor silently subscribed an `EventPersistence` instance to it. There was
no way to pass a bus without also triggering persistence, and constructing
engine + bus + persistence separately risked event routing silently missing the
persistence subscriber. The `ExecutionContext.build(events_dir, persist=True)`
classmethod makes the wiring explicit and testable: pass `persist=False` for
tests, `persist=True` for production. Callers using the old `bus=` parameter
continue to work unchanged.

**Status**: Implemented (2026-03-23)

---

## ADR-11: Knowledge Delivery via Layered Pipeline (Registry -> Resolver -> Dispatcher)

**Decision**: Implement knowledge delivery as a three-component pipeline:
`KnowledgeRegistry` (in `core/orchestration/`) for loading and indexing
packs, `KnowledgeResolver` (in `core/engine/`) for matching and budgeting,
and `PromptDispatcher` injection for prompt assembly. Runtime gaps are
handled by `core/engine/knowledge_gap.py` with a self-interrupt/re-dispatch
protocol. Feedback flows through the existing `core/observe/retrospective.py`
and `core/learn/pattern_learner.py` subsystems.

**Context**: Knowledge packs existed on disk but were never consumed by the
execution engine. Agents received generic shared context with no targeted
domain knowledge, wasting the value of specialist agents. Additionally,
agents had no mechanism to recognize or signal knowledge gaps -- they guessed
rather than requesting help.

**Alternatives considered**:

- **Unified knowledge service**: A single class handling loading, matching,
  and delivery. Rejected because it conflates three different concerns
  (disk I/O and indexing, planning-time resolution, and prompt assembly)
  that operate at different lifecycle stages and have different testing
  requirements.

- **Event-driven delivery**: Agents request knowledge via events during
  execution rather than receiving it at dispatch time. Rejected because
  it adds asynchronous complexity without benefit -- the agent's knowledge
  needs are largely predictable from the task description, and the plan
  review gate lets users correct mistakes before execution starts.

- **Global knowledge injection**: Attach all relevant packs to every step.
  Rejected because it causes context rot -- agents receive irrelevant
  information that dilutes their focus and wastes context window budget.

**Key trade-offs**:

- **Hybrid matching (tags + TF-IDF)**: Strict tag/keyword matching is used
  first because it is deterministic and auditable. TF-IDF relevance fallback
  activates only when strict matching returns nothing. This preserves
  predictability for well-tagged packs while remaining useful for projects
  without curated metadata. An MCP RAG server replaces TF-IDF when available.

- **Runtime self-interruption**: Agents self-interrupt via a `KNOWLEDGE_GAP`
  signal rather than the executor polling for gaps between steps. This fits
  the stateless agent model -- agents terminate cleanly, the executor handles
  escalation, and re-dispatch is a standard plan amendment. The alternative
  (streaming gap detection mid-execution) would require stateful agent
  connections that the current architecture does not support.

- **Intervention levels**: A plan-level `--intervention low|medium|high` flag
  shifts the escalation matrix thresholds. `low` (default) maximizes agent
  autonomy; `high` escalates on any unresolved gap. This lets users tune the
  human-in-the-loop ratio per task without changing agent definitions or
  system configuration.

- **Feedback via existing subsystems**: `KnowledgeGapRecord` entries are
  written into retrospective JSON files that `PatternLearner` already reads.
  No new storage mechanism. This means the feedback loop activates
  automatically once the retrospective and learn subsystems are in use.

- **`KnowledgeGap` model replacement**: `models/retrospective.py` had a
  `KnowledgeGap` dataclass. The new `KnowledgeGapRecord` is a strict superset
  (adds resolution tracking, gap typing, and task context). The old model is
  replaced rather than extended to avoid a permanent dual-model situation
  analogous to ADR-01. Old retrospective JSON files are handled by
  `from_dict()` defaulting the new fields.

**Status**: Implemented (2026-03-24)

---

## ADR-12: Federated Sync as a Central Read Replica (Not Central-Write-Through)

**Decision**: Per-project `baton.db` files remain the sole write target for
execution. A central database at `~/.baton/central.db` is a read replica
populated exclusively by a one-way incremental sync mechanism (`SyncEngine`).
The existing `pmo.db` is merged into `central.db` rather than kept separate.
Sync uses row-level watermarks rather than file-level copy. External source
data (ADO, Jira, GitHub) is ingested directly into `central.db` via the
`ExternalSourceAdapter` protocol.

**Context**: Agent Baton can be used across multiple projects simultaneously.
The PMO scanner, performance scoring, and knowledge gap feedback loops all
benefit from cross-project data, but there was no aggregation layer. Each
project wrote to its own `baton.db` in `.claude/team-context/`, and the PMO
scanned individual project directories at query time. With N projects this
became O(N) filesystem reads per PMO status request.

**Alternatives considered**:

- **Central-write-through** (all projects write directly to `central.db`):
  Rejected because it creates a single point of failure -- if `central.db` is
  corrupted or on a slow filesystem, every execution in every project blocks.
  It also causes SQLite write-lock contention when multiple Claude sessions run
  simultaneously across projects. The replica approach means projects always
  work offline; `central.db` is rebuildable by re-running `baton sync --all`.

- **SQLite ATTACH for cross-project queries** (attach all project `baton.db`
  files at query time): Rejected because SQLite limits ATTACH to 10 databases
  by default, requires all project files to be accessible at the same time
  (fails for remote/archived projects), and provides no place for external
  source mappings or PMO tables that span projects.

- **Separate central database per concern** (keep `pmo.db` separate, add a
  third file for external sources): Rejected because it multiplies the number
  of files to manage, introduces cross-file joins that SQLite handles poorly,
  and gives the PMO scanner N+2 databases to query instead of one. The merged
  schema has a slightly larger DDL, which is an acceptable cost.

- **File-level copy** (copy the entire `baton.db` to `central.db` on sync):
  Rejected because it is O(total rows), not incremental. With large project
  histories, every sync re-copies the entire database. Row-level sync with
  watermarks is O(delta rows since last sync), idempotent on retry, and
  naturally deduplicates on concurrent syncs.

- **Event-sourcing sync** (replay the `events` table to build central
  projections): Rejected because not all data is event-sourced -- telemetry,
  retrospectives, learned patterns, and budget recommendations are direct
  writes that have no corresponding events. Row-level sync covers all tables
  uniformly with the same algorithm.

**Key trade-offs**:

- **Row-level vs. file-level sync**: Row-level sync with watermarks is
  O(delta), idempotent, and handles concurrent syncs gracefully via
  SQLite's WAL mode + busy_timeout. File-level copy is simpler to implement
  but O(n) on total data and requires merge logic when two sessions race.

- **Merging pmo.db into central.db**: The PMO tables (`projects`, `programs`,
  `signals`, `archived_cards`, `forge_sessions`, `pmo_metrics`) are already
  global -- they describe all projects, not any one. Keeping them in a
  separate file requires cross-file joins for every PMO query. Merging them
  eliminates the join and reduces the filesystem footprint to one central
  file. The cost is a one-time migration and a slightly richer schema.

- **Auto-sync on `baton execute complete`**: Sync fires automatically after
  every execution completes, keeping `central.db` fresh without manual
  intervention. The hook is wrapped in a best-effort `try/except` so sync
  failure never blocks execution completion. If auto-sync is too slow
  (threshold: 2s), it logs a warning and returns; the user can run
  `baton sync` manually.

---

## ADR-13: PAIR_DISPATCH — Sequential Two-Agent Adversarial Assessment

**Decision**: Add a `PAIR_DISPATCH` action type that runs two agents
sequentially on the same step, where the second agent receives the first
agent's output as input context for adversarial review or validation.

**Status**: Proposed (2026-04-03) — not yet implemented.

**Context**: During a multi-domain audit, an agent needed to pair a
visualization-expert with a subject-matter-expert per domain — one to assess
dashboard quality, the other to validate domain accuracy. Claude Code
dispatches single agents, and TEAM_DISPATCH (which runs members in parallel)
does not support one agent reviewing another's output. The workaround was a
single agent wearing both hats, which produced consensus rather than the
desired creative tension.

**Proposed design**:

1. **Action type**: `PAIR_DISPATCH` emitted by `baton execute next` when a
   step has exactly two team members and the step's `mode` field is `"pair"`.
2. **Wire format** (extends `_print_action` protocol):
   ```
   ACTION: PAIR_DISPATCH
     Primary Agent: <agent_a>
     Review Agent:  <agent_b>
     Step: <step_id>
     Mode: adversarial | validating | complementary
     Message: <description>

   --- Primary Prompt ---
   <prompt for agent_a>
   --- End Primary Prompt ---

   --- Review Prompt ---
   <prompt for agent_b, includes placeholder {primary_output}>
   --- End Review Prompt ---
   ```
3. **Orchestrator behavior**: The orchestrator spawns agent A, captures its
   full output, then spawns agent B with the review prompt where
   `{primary_output}` is replaced by agent A's actual output. Agent B's
   prompt includes the instruction: "Review and challenge the following
   assessment from {agent_a}. Identify disagreements, gaps, and risks."
4. **Recording**: The orchestrator calls `baton execute team-record` twice
   (once per member). The step is complete when both members finish.
5. **Planner integration**: `_consolidate_team_step` gains a `mode` parameter.
   When the task description contains adversarial/validation signals
   ("review", "validate", "challenge", "audit", "cross-check"), the mode is
   set to `"pair"` and the executor emits `PAIR_DISPATCH` instead of parallel
   `DISPATCH` actions.

**Alternatives considered**:

- **Sequential DISPATCH with manual context threading**: The orchestrator
  already captures agent output for `--outcome`. The second agent could be
  dispatched with the first agent's outcome pasted into its prompt. This
  works today with no engine changes but requires the orchestrator agent to
  implement the threading logic, which is fragile and not discoverable.
  PAIR_DISPATCH makes the pattern explicit and engine-driven.

- **Parallel DISPATCH with shared artifact**: Both agents run simultaneously
  and write to a shared file. Rejected because the review agent needs the
  primary agent's complete output before it can assess — parallel execution
  defeats the purpose of adversarial review.

- **Three-agent pattern (primary + reviewer + synthesizer)**: Adds a third
  agent to reconcile disagreements. Deferred as over-engineering for the
  initial implementation; can be added later as `PANEL_DISPATCH`.

**Key trade-offs**:

- **New action type vs. reusing DISPATCH**: Adding a new action type is a
  breaking change to the `_print_action` protocol (see `docs/invariants.md`).
  All orchestrator agent definitions must be updated to handle it.  The
  benefit is that the pairing semantics are explicit in the wire format rather
  than buried in prompt engineering.

- **Engine-driven vs. orchestrator-driven**: Making the engine emit
  PAIR_DISPATCH centralizes the pattern and makes it available to headless
  execution (`baton execute run`). The alternative — teaching the orchestrator
  agent to manually chain two dispatches — only works in interactive Claude
  Code sessions.

**Implementation order**:

1. Add `mode` field to `TeamMember` and `PlanStep` models.
2. Add `PAIR_DISPATCH` to `ActionType` enum in `models/execution.py`.
3. Implement `_pair_dispatch_action` in `executor.py`.
4. Update `_print_action` in `execute.py` with the new wire format.
5. Update `_consolidate_team_step` in `planner.py` to set mode.
6. Update orchestrator agent definition to handle `PAIR_DISPATCH`.
7. Update `docs/invariants.md` with the new protocol contract.
8. Add integration tests covering the full pair-dispatch loop.

- **ExternalSourceAdapter as a Protocol**: External integrations (ADO, Jira,
  GitHub, Linear) have heterogeneous APIs but a uniform normalized output
  (`ExternalItem`). The `typing.Protocol` approach means new adapters can be
  added without modifying any central code -- they self-register via
  `AdapterRegistry.register()` on import. This also allows third-party
  adapters without subclassing.

- **PAT not stored in DB**: The ADO adapter reads the Personal Access Token
  from an environment variable whose name is stored in the `config` JSON
  column of `external_sources`. This means PAT rotation only requires
  updating the environment variable -- no database writes, no migration.

**Status**: Implemented (2026-03-24)

---

## ADR-13: Native SQLite Bead Memory (Not Beads Go CLI Backend)

**Decision**: Implement structured agent memory as native SQLite tables in
`baton.db` (schema v4), adopting the concepts from Steve Yegge's Beads agent
memory system (beads-ai/beads-cli) but not the Go binary, Dolt backend, or
`.beads/` file format. Agents emit inline signals (`BEAD_DISCOVERY`,
`BEAD_DECISION`, `BEAD_WARNING`) that are parsed and persisted by the
executor, following the same pattern as the existing knowledge gap protocol.

**Context**: Beads (18.7k GitHub stars) introduced a compelling model for
agent memory -- hash-based IDs, typed dependency graphs, memory decay, and
a `bd ready` command for surfacing unblocked work items. Gastown (built on
Beads for 20-160+ concurrent agents) demonstrated that shared context reduces
token burn 60-80% and that design decisions become the execution bottleneck.
However, Beads requires a Go binary (breaks `pip install`), uses Dolt for
storage (heavy dependency, 3 storage migrations in 4 months), and solves
concurrent-write contention that Agent Baton doesn't have (serialized executor).

**Alternatives considered**:

- **Adopt Beads Go CLI as runtime dependency**: Rejected. Requires Go
  toolchain or prebuilt binaries, breaks the "pip install and go" developer
  experience. Beads had 3 storage backend migrations in 4 months (flatfile ->
  sqlite -> Dolt), indicating an unstable storage layer.

- **Adopt Dolt as storage backend**: Rejected. Dolt adds a 200MB+ binary
  dependency, requires a running server process, and is designed for
  concurrent multi-writer scenarios. Agent Baton's serialized executor
  has no concurrent-write problem -- SQLite WAL mode is sufficient.

- **Import Beads as a Python library (wrap the Go CLI)**: Rejected. FFI
  wrappers add brittleness, version coupling, and complicate debugging.
  The signal parsing and storage patterns are simple enough to implement
  natively in ~1000 lines of Python.

- **Use `.beads/` file format for interoperability**: Deferred as a
  pull-forward feature. The `ExternalSourceAdapter` protocol can bridge
  to `.beads/` directories when/if interop is needed, without changing
  the core engine.

**Key trade-offs**:

- **Native SQLite vs. Beads backend**: SQLite is already the storage layer
  for all other engine data. Using it for beads means one database, one
  sync pipeline, one backup story. The cost is implementing bead CRUD from
  scratch (~435 LOC for `BeadStore`), which is modest.

- **Signal protocol vs. structured API**: Agents emit signals as free-text
  markers (like `KNOWLEDGE_GAP`), not via a structured API. This works
  because agents write to stdout -- there is no function-call interface.
  Regex parsing is fragile in theory but robust in practice (the knowledge
  gap protocol has been running reliably since v2).

- **Schema v4 migration shared across DB types**: The `MIGRATIONS` dict is
  applied to both project and central databases. FK constraints that
  reference single-column PKs must be omitted from migrations because
  central tables use composite PKs. Fresh project DBs get FKs from
  `PROJECT_SCHEMA_DDL` directly.

- **Pull-forward hedge**: The `ExternalSourceAdapter` protocol and
  `StorageBackend` protocol preserve the option to integrate with the
  Beads ecosystem later without changing the core engine. If Beads
  stabilizes its storage layer, a `BeadsAdapter` could bridge the gap.

- **Tiers 2-4 extensions**: Tiers 2-4 added `BeadSelector` (forward relay
  into delegation prompts, ranked by dependency chain > same-phase >
  cross-phase), `BeadAnalyzer` (warning frequency, file clustering,
  decision reversal detection for plan enrichment), memory decay
  (auto-archive old closed beads), knowledge gap auto-resolution from
  high-confidence beads, bead-to-knowledge promotion (`baton beads promote`),
  cross-project analytics views in `central.db`, conflict detection on
  `contradicts`/`supersedes` links, and quality scoring with `BEAD_FEEDBACK`
  signals (schema v6 adds `quality_score` and `retrieval_count` columns).

**Status**: Implemented -- all four tiers (2026-04-13)

---

## ADR-14: Learning Automation System (Hybrid Ledger + Improvement Pipeline)

**Decision**: Build a closed-loop learning system using a new
`LearningLedger` (SQLite-backed issue tracker) that feeds into the
existing improvement pipeline (proposals, experiments, rollback).

**Context**: The system collected rich execution data (retrospectives,
scores, patterns, recommendations) but had three gaps: no structured
issue tracking, no systematized workflow, and no auto-application path.
Routing mismatches, agent degradations, and knowledge gaps required
manual discovery across scattered files.

**Alternatives considered**:

- **(A) Ledger-only**: New `LearningLedger` as single source of truth,
  replacing the improvement pipeline. Rejected -- duplicates existing
  safety mechanisms (experiments, rollback, circuit breaker).
- **(B) Pipeline extension**: Extend `ImprovementLoop` -> `Recommender`
  -> `ProposalManager` without a new storage layer. Rejected -- proposals
  lack lifecycle tracking, evidence accumulation, and cross-issue
  correlation.
- **(C) Hybrid** (chosen): New `LearningLedger` for structured issue
  tracking and evidence accumulation, feeding `Recommendation` objects
  into the existing proposal/experiment/rollback machinery.

**Key trade-offs**:

- **Auto-application via overrides file**: Corrections are persisted to
  `learned-overrides.json` rather than modifying Python source. This is
  reversible (delete the file), portable (per-project), and doesn't
  require code changes. Consumed by `AgentRouter.route()` and
  `IntelligentPlanner` at call time.
- **Graduated auto-apply thresholds**: Each issue type has its own
  occurrence threshold (routing=3, degradation=5, gate=2). Interview-
  only types (`pattern_drift`, `prompt_evolution`) never auto-apply.
- **Federation**: `learning_issues` table mirrors to `central.db` via
  `SyncEngine`, enabling cross-project pattern detection.
- **Structured interviews**: `LearningInterviewer` presents issues as
  multiple-choice dialogues, recording decisions back to the ledger.
  This replaces ad-hoc retrospective reading with a directed workflow.

**Status**: Implemented (2026-04-13)

---

## ADR-15: SQLite as Primary Store (Replacing JSON Flat Files)

**Decision**: Make SQLite (`baton.db`) the primary persistence layer for all
execution data, replacing the original JSON/JSONL flat-file storage. Implement
a `StorageBackend` protocol with two implementations: `SqliteStorage` (default
for all new projects) and `FileStorage` (retained for backward compatibility).
Auto-detection at startup selects the backend based on filesystem state.

**Context**: The original storage was a collection of JSON and JSONL files --
`execution-state.json`, `usage.jsonl`, `retrospectives.jsonl`,
`learned-patterns.json`, and others -- each managed by the component that owned
it. This approach worked for single-session development but became problematic
at scale: (1) concurrent reads during CLI queries and daemon execution caused
partial-read corruption, (2) JSONL append-only files grew without bound and
required full sequential scans for any query, (3) cross-entity queries (e.g.,
"show all step results for task X with their gate outcomes") required loading
and joining multiple files in Python, and (4) the flat-file approach consumed
context window budget when Claude sessions tried to read execution state.

**Alternatives considered**:

- **Keep JSON files with file-level locking**: Rejected. File locking in
  Python (`fcntl.flock`) is not portable to Windows, does not protect against
  partial writes on crash, and still requires full-file reads for any query.

- **Use a document database (TinyDB, MongoDB)**: Rejected. TinyDB uses JSON
  files internally (same problems) and MongoDB adds a server process
  dependency that contradicts the "pip install and go" philosophy.

- **PostgreSQL / MySQL**: Rejected. Requires a running server, configuration,
  connection strings. SQLite is zero-config, file-based, and embeds in the
  Python process.

**Key trade-offs**:

- **Transactional writes**: `SqliteStorage` wraps every public write method
  in an implicit transaction (`with conn:`). Multi-table upserts (e.g.,
  saving a plan with its phases, steps, and team members) are atomic --
  partial writes from crashes are eliminated.

- **DELETE-then-INSERT for child collections**: Step results, gate results,
  and retrospective outcomes use DELETE-then-INSERT rather than UPDATE to
  avoid stale rows when list items are removed. This is a deliberate
  trade-off: slightly more I/O per write, but simpler code with no stale
  data.

- **WAL mode for concurrent access**: All connections use WAL journal mode
  so that CLI queries (`baton query`) do not block the execution engine.
  `ConnectionManager` uses one connection per thread via `threading.local`
  storage.

- **Dual-write transition period**: During migration, the engine writes to
  both SQLite and JSON files so that older CLI versions can still read state.
  The dual-write is marked `TODO:T4` for removal once the flat-file code
  paths are fully deprecated.

- **Auto-detection**: `detect_backend()` in `core/storage/__init__.py` checks
  for `baton.db` (SQLite), then `execution-state.json` (file), defaulting to
  SQLite for new projects. This means zero configuration for users -- existing
  projects keep working, new projects get SQLite automatically.

**Status**: Implemented (2026-03-26). JSON flat-file dual-writes remain as
transitional. Full deprecation pending (tracked as T4).

---

## ADR-16: Synchronous In-Process Event Bus (Not Message Queue)

**Decision**: Implement the event system as a synchronous, in-process pub/sub
bus (`EventBus` in `core/events/bus.py`) with `fnmatch`-style glob topic
routing. Handlers execute inline during `publish()`. Persistence is handled
by an `EventPersistence` subscriber that writes append-only JSONL. No threads,
no queues, no external message broker.

**Context**: The execution engine needed an event backbone for four purposes:
(1) decoupling the engine from observability (traces, usage, dashboards),
(2) enabling webhook delivery to external systems, (3) supporting SSE
streaming to the PMO UI, and (4) providing crash-recovery replay via persisted
event logs. A traditional message queue (Redis, RabbitMQ, Kafka) would satisfy
all four, but Agent Baton runs in a single Python process per project with no
server infrastructure requirement.

**Alternatives considered**:

- **Redis pub/sub or RabbitMQ**: Rejected. Adds an external server dependency,
  configuration, and failure modes. Agent Baton's deployment model is "install
  a pip package and run CLI commands" -- requiring a message broker would be a
  non-starter for most users.

- **asyncio.Queue-based bus**: Rejected for the core bus because the engine is
  synchronous. An async bus would require `await publish()` in the engine's
  hot path, threading async through the entire state machine. The SSE endpoint
  bridges the sync bus to async via an `asyncio.Queue` adapter at the API
  boundary -- this isolates the complexity to one adapter.

- **Threading + queue**: Rejected. Concurrent handler execution introduces
  non-determinism and makes debugging harder. The execution engine is
  deterministic by design -- events should be too.

**Key trade-offs**:

- **Synchronous dispatch**: Handlers run inline during `publish()`. If a
  handler raises, the exception propagates to the publisher. This keeps
  execution deterministic and debuggable, but means a slow handler blocks
  the engine. In practice, handlers are fast (write a JSONL line, enqueue
  a webhook delivery, append to in-memory history).

- **Monotonic sequencing**: Each task gets an auto-incrementing sequence
  counter. Events arrive with `sequence == 0` and the bus assigns the next
  number, providing a total order per task without external coordination.

- **In-memory history + file persistence**: The bus retains all published
  events in memory for replay queries. `EventPersistence` subscribes to
  `*` and writes each event to a JSONL file. This gives both fast in-memory
  access for projections and durable storage for post-hoc analysis.

- **Projections as fold functions**: `project_task_view()` folds an event
  stream into materialized `TaskView`/`PhaseView`/`StepView` dataclasses.
  This is a pure function over the event log -- no state stored in a database,
  no ORM, just a sequential scan of events. Simple to test, simple to debug.

**Status**: Implemented (2026-03-23, with ADR-04 and ADR-10 refining ownership
and wiring)

---

## ADR-17: FastAPI Service Layer as Optional API (Not Required Infrastructure)

**Decision**: Add a FastAPI HTTP API (`agent_baton/api/`) as an optional
`[api]` dependency group. The API wraps the same core classes the CLI uses
(via `api/deps.py` singleton injection) and adds SSE event streaming, webhook
delivery with HMAC signing, and REST endpoints for plans, executions, agents,
decisions, and the PMO board. The CLI remains the primary interface and works
without the API installed.

**Context**: The PMO UI needed a backend, daemon mode needed a way to expose
decision requests to external operators, and external tools (CI systems,
Slack bots, monitoring dashboards) needed programmatic access to execution
state. Building a separate microservice would duplicate all business logic.
Embedding the API in the same package ensures it always uses the same engine
version and data models.

**Alternatives considered**:

- **MCP server only** (Model Context Protocol): Rejected as the sole
  interface because MCP is designed for LLM tool use, not for human-facing
  UIs or webhook delivery. The PMO React app needs standard REST + SSE.

- **Separate FastAPI microservice**: Rejected because it would duplicate
  models, engine logic, and storage access code. It would also require a
  deployment story (Docker, port configuration, process management) that
  contradicts the "pip install and go" philosophy.

- **Flask or Django**: Rejected. FastAPI provides native async support
  (needed for SSE streaming), automatic OpenAPI docs, Pydantic request/
  response validation, and dependency injection -- all of which reduce
  boilerplate compared to Flask.

**Key trade-offs**:

- **Optional dependency group**: `pip install agent-baton[api]` installs
  FastAPI, Uvicorn, and SSE-starlette. The base package has no web framework
  dependency. Route modules are imported lazily inside `create_app()` so that
  a missing optional dependency raises `ImportError` only if the route is
  actually used.

- **Singleton DI via module-level variables**: `api/deps.py` stores
  singletons as `_private` module-level variables initialized by
  `init_dependencies()`. This avoids a DI container while keeping FastAPI's
  `Depends()` declarations readable in route files.

- **Single shared EventBus**: All API components share one `EventBus`
  instance. SSE streaming and webhook delivery observe all engine events
  regardless of which component emitted them. The SSE adapter bridges the
  synchronous bus to async consumers via an `asyncio.Queue`.

- **`baton serve` + `baton daemon start --serve`**: The API can run
  standalone (`baton serve`) or combined with daemon execution
  (`baton daemon start --serve`). Combined mode starts both the worker
  loop and the API server in the same process, sharing the event bus.

**Status**: Implemented (2026-03-23)

---

## ADR-18: PMO as Kanban Board with Forge Plan Generation

**Decision**: Build the Portfolio Management Office (PMO) as a three-component
subsystem: `PmoScanner` (reads execution state across projects and produces
Kanban cards), `PmoStore`/`PmoSqliteStore` (persists projects, programs,
signals, and archived cards), and `ForgeSession` (consultative plan creation
via headless Claude). The PMO UI is a React/Vite SPA served at `/pmo/` by the
FastAPI server.

**Context**: Managing multiple concurrent agent executions across projects
required a unified view of what was queued, in progress, awaiting human input,
and completed. Without this, the operator had to run `baton status` in each
project directory individually. The PMO also needed to bridge the gap between
"I have a signal (bug report, feature request, incident)" and "I have an
execution plan" -- this is the Forge.

**Alternatives considered**:

- **CLI-only portfolio view** (`baton pmo status` as a table): Implemented
  as a baseline, but insufficient for at-a-glance triage of 10+ concurrent
  plans. A Kanban board provides spatial organization (columns = workflow
  state) that a text table cannot.

- **External tool integration only** (pipe to Jira/ADO, view there):
  Rejected as the primary view because it adds a hard dependency on an
  external system and introduces sync lag. External sources are supported
  via the `ExternalSourceAdapter` protocol (ADR-12) as supplementary data,
  not as the primary management surface.

- **Server-rendered HTML**: Rejected in favor of a React SPA because the
  Forge's interactive plan editing, drag-and-drop priority management, and
  real-time SSE updates require rich client-side state management.

**Key trade-offs**:

- **PmoScanner auto-detects storage backend**: For each registered project,
  the scanner calls `detect_backend()` and reads execution state from either
  SQLite or legacy JSON files. This means the PMO works across projects at
  different migration stages without configuration.

- **ForgeSession uses headless Claude**: Plan generation calls `claude
  --print` via `HeadlessClaude` to produce LLM-quality plans. When the
  Claude CLI is unavailable, it falls back to the rule-based
  `IntelligentPlanner`. This gives the best available quality without
  requiring an API key -- it uses the same authentication as the user's
  Claude Code session.

- **Interview-driven refinement**: After generating an initial plan, the
  Forge produces 3-5 structured questions about ambiguities (missing tests,
  no gates, high risk, multi-agent coordination). The user's answers are
  fed back to regenerate an improved plan. This catches planning errors
  before execution starts rather than during.

- **Signal triage flow**: PMO signals (production incidents, bug reports,
  feature requests) can be triaged directly into execution plans via
  `ForgeSession.triage_signal()`. This connects the "something happened"
  observation to the "here is what we will do about it" plan in a single
  workflow.

**Status**: Implemented (2026-03-26, with ongoing UX improvements)

---

## ADR-19: Headless Claude Execution via `claude --print` Subprocess

**Decision**: Implement autonomous execution via `HeadlessClaude`, a
subprocess wrapper around `claude --print` that enables plan generation and
agent dispatch without an active Claude Code interactive session. Used by
`ForgeSession` for LLM plan generation, `baton execute run` for autonomous
execution loops, and the PMO execute endpoint for UI-driven launches.

**Context**: Two capabilities required Claude interaction without a human
session: (1) the Forge needed LLM-quality plan generation from the PMO UI,
and (2) full autonomous execution needed to run a plan end-to-end without
an orchestrator agent driving the loop interactively. Both cases required
programmatic access to Claude in a non-interactive mode.

**Alternatives considered**:

- **Anthropic API SDK directly**: Rejected because it requires an API key
  separate from the user's Claude Code subscription, introduces billing
  complexity, and loses the environment context (CLAUDE.md, project files,
  agent definitions) that `claude --print` inherits from the project
  directory.

- **MCP tool call**: Rejected because MCP is designed for Claude calling
  tools, not for tools calling Claude. The control flow is inverted.

- **Persistent Claude Code session via stdin/stdout**: Rejected because
  Claude Code's interactive mode is designed for human conversation, not
  programmatic use. Its output format is not stable for machine parsing.
  `--print` mode with `--output-format json` provides a stable JSON
  response contract.

**Key trade-offs**:

- **Subprocess isolation**: Each headless invocation is a fresh subprocess
  with a sanitized environment (only allowlisted env vars pass through:
  `ANTHROPIC_API_KEY`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`,
  `AWS_PROFILE`, `AWS_REGION`). API keys are scrubbed from error logs via
  regex. This provides security isolation but adds per-invocation overhead
  (~2-5s startup).

- **Retry with exponential backoff**: `max_retries=2` with
  `base_retry_delay=5.0s`. Rate-limited or transient failures are handled
  automatically. The caller receives a `HeadlessResult` with
  `success=False` after exhausting retries rather than an exception.

- **Large prompt handling**: Prompts exceeding 128KB are sent via stdin
  rather than the `-p` flag to avoid shell argument-length limits.

- **`baton execute run` full loop**: Combines `HeadlessClaude` with the
  execution engine to drive the complete start -> dispatch -> gate ->
  complete cycle. Each agent dispatch spawns a fresh `claude --print`
  subprocess with the delegation prompt. No Claude Code session required.

**Status**: Implemented (2026-03-26)

---

## ADR-20: Haiku Classifier for Adaptive Plan Sizing

**Decision**: Introduce a `TaskClassifier` protocol with two implementations:
`HaikuClassifier` (calls Claude Haiku via `claude --print` for intelligent
classification) and `KeywordClassifier` (deterministic fallback using keyword
heuristics and registry-aware agent scoring). The planner uses
`FallbackClassifier` which tries Haiku first and degrades to keywords.
Classification output (`TaskClassification`) determines task type, complexity
tier, agent roster, and phase sequence.

**Context**: The planner originally used hardcoded heuristics to determine
plan structure -- every task got a similar number of phases and agents regardless
of actual complexity. Simple bug fixes received the same multi-phase plan as
complex cross-cutting refactors, wasting tokens and human review time. A "fix
a typo" task should not generate a 5-phase plan with 4 agents.

**Alternatives considered**:

- **Always use LLM classification**: Rejected because it adds latency and
  cost to every `baton plan` invocation. The keyword classifier handles
  the common case (obvious task types like "fix bug X" or "add feature Y")
  without any API call. LLM classification activates only for ambiguous
  or complex descriptions.

- **User-specified complexity**: Rejected as the primary mechanism because
  users consistently underestimate complexity. Auto-classification with
  user override (via `--complexity light|medium|heavy`) is the chosen
  pattern.

- **Single classifier, no protocol**: Rejected because it prevents testing
  the planner independently of the LLM. The protocol allows injecting a
  deterministic classifier in tests.

**Key trade-offs**:

- **Three complexity tiers**: `light` (1 agent, 1-2 phases), `medium`
  (up to 3 agents, 2-4 phases), `heavy` (up to 5 agents, 4+ phases).
  Agent roster caps (`_MAX_AGENTS_BY_COMPLEXITY`) prevent bloated plans
  regardless of how many agents the classifier suggests.

- **Registry-aware scoring**: The `KeywordClassifier` scores agents from
  the `AgentRegistry` by keyword overlap with the task summary, category
  affinity, and preferred-primary-implementer rules. This means
  classification adapts to the available agents -- a project with custom
  agents gets appropriate routing.

- **Word-boundary matching**: Keyword scoring uses `\b` regex boundaries
  rather than substring matching. This prevents false positives like "fix"
  matching inside "prefix" or "test" inside "latest". Multi-word keywords
  use substring matching since they are specific enough.

- **Haiku model choice**: Claude Haiku was chosen for classification because
  it is fast (~1-2s), cheap, and sufficient for structured classification
  tasks. Using Sonnet or Opus for classification would add unnecessary
  latency and cost. The `--model` flag on `HaikuClassifier` allows
  overriding if needed.

**Status**: Implemented (2026-03-26)

---

## ADR-21: Daemon Mode with Team Collaboration and Async Decisions

**Decision**: Implement daemon-mode execution via `WorkerSupervisor` (PID
file management, structured logging, signal handling) wrapping `TaskWorker`
(async execution loop). Add team collaboration infrastructure: `SynthesisSpec`
on `PlanStep` for team output coordination, conflict detection via file-overlap
heuristics, `DecisionManager` for file-based async human decisions, and
`ContributionRequest` for multi-party input collection. MCP server pass-through
is selective per step.

**Context**: Interactive orchestration via the Claude Code session works well
for attended development, but two scenarios required unattended execution:
(1) long-running plans (30+ minutes) where the developer wants to step away,
and (2) multi-developer workflows where different team members handle different
steps asynchronously. The daemon needs to run the full execution loop, handle
gates and approvals without a human present, and allow out-of-band interaction
when human input is required.

**Alternatives considered**:

- **Background shell process** (`nohup baton execute run &`): Rejected
  because it provides no PID management, no structured logging, no graceful
  shutdown, and no status querying. The developer has no way to check
  progress or intervene without parsing raw stdout.

- **Systemd service**: Rejected because it requires root access for
  installation, is Linux-only, and is too heavy for per-project
  per-execution lifecycle management. The supervisor handles the same
  concerns in user-space.

- **Central daemon managing all projects**: Rejected because it creates a
  single point of failure across projects. Per-project supervisors are
  independent -- one project's daemon crash does not affect others.

**Key trade-offs**:

- **PID file with flock**: `WorkerSupervisor` uses `flock()` on the PID file
  to prevent duplicate daemons. The OS releases the lock automatically when
  the process exits, eliminating the stale-PID-file race condition common
  with naive PID file approaches. On Windows, `msvcrt.locking` provides
  equivalent behavior.

- **File-based decision protocol**: `DecisionManager` writes decision
  requests as JSON files + companion `.md` summaries. Operators resolve
  decisions from a separate CLI session (`baton decide --resolve`). This
  avoids requiring a running server or shared database for human
  interaction -- files are the universal interface.

- **Team synthesis strategies**: `PlanStep.synthesis_spec` supports three
  strategies: `concatenate` (append outputs), `merge_files` (git-merge
  file changes), and `agent_synthesis` (dispatch a synthesis agent to
  combine outputs). The strategy is selected at plan time based on step
  type and team size.

- **Conflict escalation**: When team members modify overlapping files,
  `_detect_team_conflict()` produces a `ConflictRecord`. The default
  resolution is escalation to an APPROVAL gate; `auto_merge` is available
  for low-risk conflicts. Conflicts flow through to retrospectives for
  learning.

- **Selective MCP pass-through**: `PlanStep.mcp_servers` allows specific
  MCP servers to be forwarded to specific agents. This prevents agents
  from accessing tools they do not need while enabling those that do
  (e.g., a data-engineer step with database MCP access).

- **Namespaced execution directories**: When `task_id` is provided, all
  daemon files (`worker.pid`, `worker.log`, `worker-status.json`) are
  stored under `executions/<task_id>/`, enabling concurrent daemon
  executions within the same project.

**Status**: Implemented (2026-03-29, Phases 1-5)
