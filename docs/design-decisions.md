# Architecture Decision Records

These records document the rationale behind the ten structural decisions made
during the 2026-03-23 re-architecture of the `agent_baton` package. Future
contributors can consult this document to understand why the code looks the
way it does before proposing changes that would reverse these decisions.

---

## ADR-01: Remove the Dual Plan Model Hierarchy

**Decision**: Delete `ExecutionPlan`, `Phase`, `AgentAssignment`, `QAGate`,
and `PlanBuilder`; make `MachinePlan` the sole plan type.

**Context**: Two parallel plan hierarchies existed — `models/plan.py`
(`ExecutionPlan`) from Epic 1 and `models/execution.py` (`MachinePlan`) from
Epic 2. The engine, runtime, and CLI all used `MachinePlan` exclusively.
`ExecutionPlan` was constructed only by `PlanBuilder` and consumed only by
`ContextManager.write_plan()`, which called `plan.to_markdown()` — a method
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
rationale. The migration was entirely mechanical — find-and-replace of import
strings — with Python's import errors providing an immediate safety net.

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
exposed only `PlanBuilder` and `ContextManager` — not `ExecutionEngine` or
`TaskWorker` — inverting the package's actual usage pattern. The package-level
exports now reflect what users actually need: `ExecutionEngine`, `TaskWorker`,
`MachinePlan`, `AgentLauncher`, and the orchestration types. The dependency
hierarchy (models → events/observe/govern → engine → runtime → CLI) is
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
`register()` call, not by filename — so moving files into subdirectories
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

## ADR-11: Knowledge Delivery via Layered Pipeline (Registry → Resolver → Dispatcher)

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
agents had no mechanism to recognize or signal knowledge gaps — they guessed
rather than requesting help.

**Alternatives considered**:

- **Unified knowledge service**: A single class handling loading, matching,
  and delivery. Rejected because it conflates three different concerns
  (disk I/O and indexing, planning-time resolution, and prompt assembly)
  that operate at different lifecycle stages and have different testing
  requirements.

- **Event-driven delivery**: Agents request knowledge via events during
  execution rather than receiving it at dispatch time. Rejected because
  it adds asynchronous complexity without benefit — the agent's knowledge
  needs are largely predictable from the task description, and the plan
  review gate lets users correct mistakes before execution starts.

- **Global knowledge injection**: Attach all relevant packs to every step.
  Rejected because it causes context rot — agents receive irrelevant
  information that dilutes their focus and wastes context window budget.

**Key trade-offs**:

- **Hybrid matching (tags + TF-IDF)**: Strict tag/keyword matching is used
  first because it is deterministic and auditable. TF-IDF relevance fallback
  activates only when strict matching returns nothing. This preserves
  predictability for well-tagged packs while remaining useful for projects
  without curated metadata. An MCP RAG server replaces TF-IDF when available.

- **Runtime self-interruption**: Agents self-interrupt via a `KNOWLEDGE_GAP`
  signal rather than the executor polling for gaps between steps. This fits
  the stateless agent model — agents terminate cleanly, the executor handles
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

**Status**: Designed (2026-03-24)

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
  Rejected because it creates a single point of failure — if `central.db` is
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
  projections): Rejected because not all data is event-sourced — telemetry,
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
  global — they describe all projects, not any one. Keeping them in a
  separate file requires cross-file joins for every PMO query. Merging them
  eliminates the join and reduces the filesystem footprint to one central
  file. The cost is a one-time migration and a slightly richer schema.

- **Auto-sync on `baton execute complete`**: Sync fires automatically after
  every execution completes, keeping `central.db` fresh without manual
  intervention. The hook is wrapped in a best-effort `try/except` so sync
  failure never blocks execution completion. If auto-sync is too slow
  (threshold: 2s), it logs a warning and returns; the user can run
  `baton sync` manually.

- **ExternalSourceAdapter as a Protocol**: External integrations (ADO, Jira,
  GitHub, Linear) have heterogeneous APIs but a uniform normalized output
  (`ExternalItem`). The `typing.Protocol` approach means new adapters can be
  added without modifying any central code — they self-register via
  `AdapterRegistry.register()` on import. This also allows third-party
  adapters without subclassing.

- **PAT not stored in DB**: The ADO adapter reads the Personal Access Token
  from an environment variable whose name is stored in the `config` JSON
  column of `external_sources`. This means PAT rotation only requires
  updating the environment variable — no database writes, no migration.

**Status**: Designed (2026-03-24)
