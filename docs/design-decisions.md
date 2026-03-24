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
