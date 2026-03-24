# Re-Architecture Orchestration Plan

## Metadata

- task_id: rearch-2026-03-23
- risk_level: MEDIUM
- execution_mode: hybrid (sequential phases, parallel steps within phases)
- git_strategy: feature-branch per phase

### Codebase State (verified on master, 2026-03-23)

> **IMPORTANT**: This plan was originally drafted against a worktree that was
> behind master (missing `runtime/` and `events/` sub-packages, 31 CLI commands
> instead of 35, 1730 tests instead of 1977). The facts below have been
> corrected to reflect master. Steps 1.3, 1.4, 1.5, and 3.4 have been
> annotated where their scope differs from the full master codebase; agents
> executing these steps MUST read the actual master source before starting.

| Fact | Value |
|------|-------|
| Test count | 1977 (master) |
| Shim files at `core/*.py` | 21 files (confirmed by directory listing) |
| `runtime/` sub-package | EXISTS on master (`core/runtime/`: worker.py, supervisor.py, scheduler.py, launcher.py, claude_launcher.py, daemon.py, decisions.py, signals.py) |
| `events/` sub-package | EXISTS on master (`core/events/`: bus.py, events.py, persistence.py, projections.py) |
| `ContextManager.write_plan()` | Accepts `ExecutionPlan`, not `MachinePlan` |
| CLI commands | 35 commands (master) |
| `IntelligentPlanner._plan_builder` | `PlanBuilder` instance (used for `assess_risk` and `_select_git_strategy`) |

---

## Phase 1: Foundation

Step 1.2 depends on 1.1. Steps 1.3, 1.4, and 1.5 are independent of each other
and of 1.1/1.2 -- they can execute in parallel. Step 1.6 depends on all of them.

---

### Step 1.1: Remove Vestigial Plan Models

- agent: backend-engineer--python
- model: sonnet
- task: Remove `ExecutionPlan`, `Phase`, `AgentAssignment`, and `QAGate` from
  `agent_baton/models/plan.py`. Keep `MissionLogEntry` — it is the only model
  in that file still in active use. Remove those four names from the import block
  and `__all__` list in `agent_baton/models/__init__.py`. Search for any remaining
  imports of these names in `agent_baton/` (excluding `core/orchestration/plan.py`
  which is handled in Step 1.2) and update them.
- context_files:
  - agent_baton/models/plan.py
  - agent_baton/models/__init__.py
  - agent_baton/models/execution.py
- deliverables: `models/plan.py` contains only `MissionLogEntry`; `models/__init__.py`
  exports only `MissionLogEntry` from that module
- allowed_paths: agent_baton/models/
- blocked_paths: agent_baton/core/, tests/
- depends_on: []

---

### Step 1.2: Remove PlanBuilder, Absorb into IntelligentPlanner

- agent: backend-engineer--python
- model: sonnet
- task: |
  `IntelligentPlanner` delegates to `PlanBuilder` for two things only:
  (a) `self._plan_builder.assess_risk(task_summary)` called inside `_assess_risk()`
  (b) `_PB._select_git_strategy(risk_level_enum).value` called inside `create_plan()`

  Steps:
  1. Copy the `RISK_SIGNALS` dict and `assess_risk()` instance method from
     `core/orchestration/plan.py` directly into `IntelligentPlanner` as
     `_assess_risk_keywords(task_description: str) -> RiskLevel`.
  2. Copy `_select_git_strategy(risk: RiskLevel) -> GitStrategy` as a static method
     on `IntelligentPlanner`. Copy `_risk_ordinal()` as well if needed.
  3. In `_assess_risk()`, replace `self._plan_builder.assess_risk(task_summary)` with
     `self._assess_risk_keywords(task_summary)`.
  4. In `create_plan()`, replace the local `_PB._select_git_strategy(...)` call with
     `self._select_git_strategy(risk_level_enum).value`.
  5. Remove `self._plan_builder = PlanBuilder()` from `__init__`.
  6. Remove `from agent_baton.core.orchestration.plan import PlanBuilder` from
     `planner.py` (both the module-level import at line 17 and the inline import
     at line 206 inside `create_plan()`).
  7. Update `ContextManager.write_plan()` in `core/orchestration/context.py` to
     accept `MachinePlan` instead of `ExecutionPlan`. Change the type annotation and
     the import: remove `ExecutionPlan` from the `from agent_baton.models.plan import`
     line; add `from agent_baton.models.execution import MachinePlan`. The method body
     calls `plan.to_markdown()` which exists on both types — no body change needed.
  8. Delete or empty `core/orchestration/plan.py` — it now has no callers outside tests.
     Replacing the file content with a single comment `# Removed — see IntelligentPlanner`
     is acceptable; do NOT delete the file yet (tests may still import it — Step 1.6
     handles test migration).
  9. Remove `PlanBuilder` from `core/orchestration/__init__.py` exports.
- context_files:
  - agent_baton/core/engine/planner.py
  - agent_baton/core/orchestration/plan.py
  - agent_baton/core/orchestration/context.py
  - agent_baton/core/orchestration/__init__.py
- deliverables:
  - `IntelligentPlanner` self-contained (no `PlanBuilder` dependency)
  - `ContextManager.write_plan()` typed for `MachinePlan`
  - `core/orchestration/plan.py` emptied (stub comment only)
  - `PlanBuilder` removed from `core/orchestration/__init__.py`
- allowed_paths: agent_baton/core/orchestration/, agent_baton/core/engine/planner.py
- blocked_paths: tests/, agent_baton/models/
- depends_on: [1.1]

---

### Step 1.3: Formalize Engine Protocol Interface

- agent: backend-engineer--python
- model: sonnet
- task: |
  Create `agent_baton/core/engine/protocols.py` with a `typing.Protocol` class
  `ExecutionDriver` that formalizes the interface `ExecutionEngine` already
  implements. Methods to declare (match exact signatures from executor.py):

  ```
  start(plan: MachinePlan) -> ExecutionAction
  next_action() -> ExecutionAction
  next_actions() -> list[ExecutionAction]
  mark_dispatched(step_id: str, agent_name: str) -> None
  record_step_result(step_id, agent_name, status, outcome, files_changed,
                     commit_hash, estimated_tokens, duration_seconds, error) -> None
  record_gate_result(phase_id: int, passed: bool, output: str) -> None
  complete() -> str
  status() -> dict
  resume() -> ExecutionAction
  recover_dispatched_steps() -> int
  ```

  Import `MachinePlan` and `ExecutionAction` from `agent_baton.models.execution`.
  Add `from __future__ import annotations` and `from typing import Protocol`.
  Export `ExecutionDriver` from `agent_baton/core/engine/__init__.py`.

  Do NOT modify executor.py in this step — the engine will be typed against the
  Protocol in a future step. This step is purely additive.
- context_files:
  - agent_baton/core/engine/executor.py
  - agent_baton/core/engine/__init__.py
- deliverables: `agent_baton/core/engine/protocols.py` with `ExecutionDriver`;
  exported from `engine/__init__.py`
- allowed_paths: agent_baton/core/engine/protocols.py, agent_baton/core/engine/__init__.py
- blocked_paths: tests/, agent_baton/core/runtime/
- depends_on: []

---

### Step 1.4: De-duplicate Event Publishing

- agent: backend-engineer--python
- model: sonnet
- task: |
  > NOTE: This step was originally scoped for a worktree missing `core/runtime/`
  > and `core/events/`. On master, both sub-packages exist and `ExecutionEngine`
  > publishes events to `EventBus`. The scope below targets master.

  Both `ExecutionEngine.record_step_result()` and `TaskWorker._execution_loop()`
  publish `step.dispatched`, `step.completed`, and `step.failed` events. Remove
  the duplicate publishes from the engine. Specifically:

  1. In `executor.py` `record_step_result()`, remove the `if status == "complete"`
     / `elif status == "failed"` / `elif status == "dispatched"` block that calls
     `self._publish(evt.step_completed(...))`, `self._publish(evt.step_failed(...))`,
     and `self._publish(evt.step_dispatched(...))`. The `mark_dispatched()` method
     calls `record_step_result(status="dispatched")` which triggers the dispatched
     publish -- this path is also removed.

  2. Keep all other `self._publish()` calls: `task.started` in `start()`,
     `phase.started`/`phase.completed` in `_determine_action()`,
     `gate.passed`/`gate.failed` in `record_gate_result()`,
     `task.completed` in `complete()`.

  3. Add an ownership docstring to the `_publish` method documenting the contract:
     Engine publishes: task.*, phase.*, gate.* events.
     Worker publishes: step.* events.

  4. Add a module-level docstring to `core/events/events.py` documenting the same
     ownership split.
- context_files:
  - agent_baton/core/engine/executor.py
  - agent_baton/core/runtime/worker.py
  - agent_baton/core/events/events.py
- deliverables: No duplicate step-event publishing; ownership comments in place
- allowed_paths: agent_baton/core/engine/executor.py, agent_baton/core/events/events.py
- blocked_paths: tests/
- depends_on: []

---

### Step 1.5: Extract StatePersistence from executor.py

- agent: backend-engineer--python
- model: sonnet
- task: |
  Create `agent_baton/core/engine/persistence.py` with a `StatePersistence` class.
  Extract state I/O from `ExecutionEngine`:

  Methods to move into `StatePersistence`:
  - `_save_state(state: ExecutionState) -> Path` — write JSON to disk
  - `_load_state() -> ExecutionState | None` — read JSON from disk
  Add new convenience methods:
  - `exists() -> bool` — returns True if the state file exists on disk
  - `clear() -> None` — deletes the state file from disk

  Constructor: `StatePersistence(context_root: Path)`. The class should own the
  `_STATE_FILENAME = "execution-state.json"` constant (remove it from
  `ExecutionEngine`).

  Update `ExecutionEngine.__init__()` to instantiate:
  `self._persistence = StatePersistence(self._root)`

  Replace all `self._save_state(...)` calls in `ExecutionEngine` with
  `self._persistence.save(state)`, and all `self._load_state()` calls with
  `self._persistence.load()`. Rename the methods on `StatePersistence` to
  `save(state)` and `load()` (drop the leading underscore — they are the
  public API of the persistence object).

  `resume()` and `recover_dispatched_steps()` (if it exists) should delegate
  to `self._persistence.load()` rather than calling the old private methods.

  Export `StatePersistence` from `agent_baton/core/engine/__init__.py`.
- context_files:
  - agent_baton/core/engine/executor.py
- deliverables:
  - `agent_baton/core/engine/persistence.py` with `StatePersistence`
  - `executor.py` uses `self._persistence.save()` / `self._persistence.load()`
  - `StatePersistence` exported from `engine/__init__.py`
- allowed_paths: agent_baton/core/engine/
- blocked_paths: tests/, agent_baton/core/runtime/
- depends_on: []

---

### Step 1.6: Update Tests for Phase 1 Changes

- agent: test-engineer
- model: sonnet
- task: |
  Update all test files broken by Phase 1 changes. Specific migrations:

  1. Tests importing `ExecutionPlan`, `Phase`, `AgentAssignment`, `QAGate`:
     - `test_planner.py` — imports `PlanBuilder`/`ExecutionPlan` from
       `core/orchestration/plan.py`; migrate to `MachinePlan`/`IntelligentPlanner`
       from `core/engine/planner.py` and `models/execution.py`
     - `test_context.py` — `ContextManager.write_plan()` now takes `MachinePlan`;
       update fixtures accordingly
     - `test_models.py` — remove assertions on `ExecutionPlan`, `Phase`,
       `AgentAssignment`, `QAGate`; verify `MissionLogEntry` tests still pass

  2. Tests for executor / persistence split:
     - `test_executor.py` — add tests for `StatePersistence.exists()` and
       `StatePersistence.clear()`; verify engine delegates to persistence object
       (use `isinstance(engine._persistence, StatePersistence)`)

  3. Tests for protocols:
     - Add a small test in `test_executor.py` or a new `test_protocols.py` that
       imports `ExecutionDriver` from `core.engine` and asserts that
       `ExecutionEngine` is a structural subtype (use `isinstance` with
       `runtime_checkable` if the Protocol is decorated, or simply confirm the
       import resolves cleanly)

  Test files that reference `core/orchestration/plan.py` imports should be
  updated to use canonical `core.engine.planner` / `models.execution` paths.

  Run `pytest tests/ -x -q --tb=short` and iterate until all tests pass.
  Do NOT add new test files for features that do not exist yet — test only
  what was changed in Steps 1.1–1.5.
- context_files:
  - tests/conftest.py
  - tests/test_executor.py
  - tests/test_planner.py
  - tests/test_engine_planner.py
  - tests/test_engine_integration.py
  - tests/test_context.py
  - tests/test_models.py
- deliverables: All 1977 tests passing
- allowed_paths: tests/
- blocked_paths: agent_baton/
- depends_on: [1.1, 1.2, 1.3, 1.4, 1.5]

---

### Gate 1: Build + Test

- type: test
- command: `pytest tests/ -x -q --tb=short`
- fail_on: any test failure

---

## Phase 2: Shim Removal

Steps 2.1 and 2.2 are independent and can execute in parallel.
Step 2.3 depends on both.

---

### Step 2.1: Migrate CLI Imports to Canonical Paths

- agent: backend-engineer--python
- model: sonnet
- task: |
  For every file in `agent_baton/cli/commands/*.py`, replace shim-path imports
  with canonical sub-package imports. Confirmed shim callers from directory scan:
  - `classify.py` → `from agent_baton.core.classifier import ...` (uses shim)
  - `async_cmd.py` → `from agent_baton.core.async_dispatch import ...` (uses shim)

  Apply the full migration map to all 35 command files:

  | Shim import | Canonical import |
  |-------------|-----------------|
  | `core.classifier` | `core.govern.classifier` |
  | `core.compliance` | `core.govern.compliance` |
  | `core.escalation` | `core.govern.escalation` |
  | `core.policy` | `core.govern.policy` |
  | `core.spec_validator` | `core.govern.spec_validator` |
  | `core.validator` | `core.govern.validator` |
  | `core.usage` | `core.observe.usage` |
  | `core.telemetry` | `core.observe.telemetry` |
  | `core.dashboard` | `core.observe.dashboard` |
  | `core.retrospective` | `core.observe.retrospective` |
  | `core.context` | `core.orchestration.context` |
  | `core.registry` | `core.orchestration.registry` |
  | `core.router` | `core.orchestration.router` |
  | `core.scoring` | `core.improve.scoring` |
  | `core.evolution` | `core.improve.evolution` |
  | `core.vcs` | `core.improve.vcs` |
  | `core.sharing` | `core.distribute.sharing` |
  | `core.transfer` | `core.distribute.transfer` |
  | `core.incident` | `core.distribute.incident` |
  | `core.async_dispatch` | `core.distribute.async_dispatch` |
  | `core.plan` | `core.engine.planner` (for IntelligentPlanner) or `core.orchestration.plan` (stub — leave as-is if the CLI doesn't use PlanBuilder after Step 1.2) |

  Do not touch `execute.py` — it already imports from `core.engine.executor`
  (the canonical path). Do not touch `plan_cmd.py` without first reading it to
  verify its imports.
- context_files:
  - agent_baton/cli/commands/ (all 31 .py files)
- deliverables: All CLI command files using canonical sub-package imports
- allowed_paths: agent_baton/cli/
- blocked_paths: agent_baton/core/, agent_baton/models/, tests/
- depends_on: [1.2]

---

### Step 2.2: Migrate Test Imports to Canonical Paths

- agent: test-engineer
- model: sonnet
- task: |
  Apply the same migration map from Step 2.1 to all test files in `tests/*.py`.
  Use grep to find every test file that imports from a shim path:
  `grep -rn "from agent_baton.core\.[a-z_]* import" tests/`
  — any import where the module is a flat name (not `core.govern.X`,
  `core.observe.X`, etc.) is a shim import.

  Do NOT change imports that already use canonical sub-package paths.
  Do NOT modify `test_planner.py` or `test_context.py` — those are handled
  by Step 1.6.

  Run `pytest tests/ -x -q --tb=short` after each batch of file updates.
- context_files:
  - tests/ (all test files)
- deliverables: All test files using canonical sub-package imports
- allowed_paths: tests/
- blocked_paths: agent_baton/
- depends_on: [1.6]

---

### Step 2.3: Delete Shim Files and Prune core/__init__.py

- agent: backend-engineer--python
- model: sonnet
- task: |
  Delete all 21 shim files at `agent_baton/core/*.py` (do NOT touch `__init__.py`).
  The confirmed shim files are:
  context.py, async_dispatch.py, classifier.py, compliance.py, dashboard.py,
  evolution.py, escalation.py, incident.py, vcs.py, spec_validator.py, router.py,
  transfer.py, scoring.py, telemetry.py, validator.py, registry.py, plan.py,
  policy.py, usage.py, sharing.py, retrospective.py

  Also delete `agent_baton/core/orchestration/plan.py` (the stub left by Step 1.2).

  Prune `agent_baton/core/__init__.py` to export only:
  - `AgentRegistry` (from `.orchestration.registry`)
  - `AgentRouter` (from `.orchestration.router`)
  - `ContextManager` (from `.orchestration.context`)

  Remove all other imports from `core/__init__.py`. The removed symbols are
  available via their canonical sub-package paths for any code that needs them.

  Update `agent_baton/__init__.py` to import directly from canonical paths:
  ```python
  from agent_baton.core.orchestration.registry import AgentRegistry
  from agent_baton.core.orchestration.router import AgentRouter
  from agent_baton.core.orchestration.context import ContextManager
  ```

  Verify: `python -c "import agent_baton; print('OK')"` must succeed.
- context_files:
  - agent_baton/core/__init__.py
  - agent_baton/__init__.py
- deliverables:
  - 21 shim files deleted
  - `core/orchestration/plan.py` stub deleted
  - Minimal `core/__init__.py` with 3 exports
  - `agent_baton/__init__.py` uses canonical imports
- allowed_paths: agent_baton/core/, agent_baton/__init__.py
- blocked_paths: tests/
- depends_on: [2.1, 2.2]

---

### Gate 2: Build + Test

- type: test
- command: `pytest tests/ -x -q --tb=short && python -c "import agent_baton; print('OK')"`
- fail_on: any test failure or import error

---

## Phase 3: Structure

Steps 3.1, 3.2, 3.3, and 3.4b are independent and can execute in parallel.
Step 3.4 depends on 3.3. Step 3.4b depends on Steps 1.3 and 1.5 (both in Phase 1).
Step 3.5 depends on all of 3.1–3.4b.

---

### Step 3.1: Explicit Core vs Peripheral Layering

- agent: backend-engineer--python
- model: sonnet
- task: |
  Update `agent_baton/__init__.py` to export the full execution core public API:
  ```python
  from agent_baton.core.engine.executor import ExecutionEngine
  from agent_baton.core.engine.planner import IntelligentPlanner
  from agent_baton.core.engine.dispatcher import PromptDispatcher
  from agent_baton.core.engine.gates import GateRunner
  from agent_baton.core.engine.persistence import StatePersistence
  from agent_baton.core.orchestration.registry import AgentRegistry
  from agent_baton.core.orchestration.router import AgentRouter
  from agent_baton.core.orchestration.context import ContextManager
  ```

  Update `__all__` to include all exported names.

  Add an architecture docstring to `agent_baton/core/__init__.py`:
  ```python
  """
  Core sub-packages:
    engine/       — ExecutionEngine, IntelligentPlanner, PromptDispatcher, GateRunner, StatePersistence
    orchestration/ — AgentRegistry, AgentRouter, ContextManager
    govern/       — DataClassifier, ComplianceReportGenerator, PolicyEngine, SpecValidator, AgentValidator, EscalationManager
    observe/      — UsageLogger, RetrospectiveEngine, DashboardGenerator, TraceRecorder, AgentTelemetry, ContextProfiler
    improve/      — PerformanceScorer, PromptEvolutionEngine, AgentVersionControl
    learn/        — PatternLearner, BudgetTuner
    distribute/   — PackageBuilder, PackageVerifier, RegistryClient, ProjectTransfer, IncidentManager, AsyncDispatcher
  """
  ```
- context_files:
  - agent_baton/__init__.py
  - agent_baton/core/__init__.py
  - agent_baton/core/engine/__init__.py
- deliverables: Clean public API in `agent_baton/__init__.py`; architecture docstring in `core/__init__.py`
- allowed_paths: agent_baton/__init__.py, agent_baton/core/__init__.py
- blocked_paths: tests/, agent_baton/core/engine/, agent_baton/core/orchestration/
- depends_on: [2.3]

---

### Step 3.2: Gate Experimental distribute Modules

- agent: backend-engineer--python
- model: sonnet
- task: |
  Create `agent_baton/core/distribute/experimental/__init__.py`.
  Move these three modules into it:
  - `core/distribute/incident.py` → `core/distribute/experimental/incident.py`
  - `core/distribute/async_dispatch.py` → `core/distribute/experimental/async_dispatch.py`
  - `core/distribute/transfer.py` → `core/distribute/experimental/transfer.py`

  Update `core/distribute/__init__.py`:
  - Remove imports of `IncidentManager`, `IncidentTemplate`, `IncidentPhase`,
    `AsyncDispatcher`, `AsyncTask`, `ProjectTransfer`, `TransferManifest`
  - Add a comment: `# Experimental modules available at core.distribute.experimental`
  - Keep all stable exports: `PackageBuilder`, `PackageManifest`, `PackageVerifier`,
    `EnhancedManifest`, `PackageValidationResult`, `RegistryClient`

  Export from `core/distribute/experimental/__init__.py`:
  ```python
  from agent_baton.core.distribute.experimental.incident import ...
  from agent_baton.core.distribute.experimental.async_dispatch import ...
  from agent_baton.core.distribute.experimental.transfer import ...
  ```

  Update the three CLI command files that import these modules:
  - `cli/commands/incident.py` → import from `core.distribute.experimental.incident`
  - `cli/commands/async_cmd.py` → import from `core.distribute.experimental.async_dispatch`
  - `cli/commands/transfer.py` → import from `core.distribute.experimental.transfer`

  Update `core/__init__.py` experimental imports if they are present there.
- context_files:
  - agent_baton/core/distribute/__init__.py
  - agent_baton/core/distribute/incident.py
  - agent_baton/core/distribute/async_dispatch.py
  - agent_baton/core/distribute/transfer.py
  - agent_baton/cli/commands/incident.py
  - agent_baton/cli/commands/async_cmd.py
  - agent_baton/cli/commands/transfer.py
- deliverables:
  - `core/distribute/experimental/` directory with 3 modules + `__init__.py`
  - `core/distribute/__init__.py` updated to stable-only exports
  - 3 CLI command files updated
- allowed_paths: agent_baton/core/distribute/, agent_baton/cli/commands/
- blocked_paths: tests/
- depends_on: [2.3]

---

### Step 3.3: Normalize Enum Usage

- agent: backend-engineer--python
- model: opus
- task: |
  In `agent_baton/models/execution.py`:
  1. Change `ExecutionAction.action_type` field type annotation from `str` to
     `ActionType`. Default value becomes `ActionType.DISPATCH` (or whichever is
     sensible — but action_type has no default, so the signature stays as a
     required arg in practice). Since `ExecutionAction` is a `@dataclass` and
     `action_type` is currently typed `str`, update the type hint only — the
     dataclass field is positional by definition here.
  2. In `ExecutionAction.to_dict()`, serialize as `self.action_type.value` instead
     of `self.action_type` (since `.value` is already used in the conditionals
     `if self.action_type == ActionType.DISPATCH.value`).
  3. In `ExecutionAction.to_dict()` conditionals, change comparisons from
     `== ActionType.X.value` to `== ActionType.X` (comparing enum to enum).

  In `agent_baton/core/engine/executor.py`:
  1. In `_determine_action()`, `_dispatch_action()`, and `complete()`, change
     all `ExecutionAction(action_type=ActionType.X.value, ...)` to
     `ExecutionAction(action_type=ActionType.X, ...)`.
  2. In the docstring and comments that reference `ActionType.DISPATCH.value` as a
     comparison target, update them.

  Leave `StepResult.status` as `str`. It takes "complete"/"failed"/"dispatched"
  (which are `StepStatus` values), but `ExecutionState.completed_step_ids`,
  `failed_step_ids`, and `dispatched_step_ids` all filter by string comparison
  and `StepResult.from_dict()` parses status as a plain string. Changing this
  field requires a wider migration (including `from_dict` deserialization) and
  is deferred.

  Before making changes, read `executor.py` and `worker.py` carefully to identify
  every comparison site. `worker.py` exists on master at
  `agent_baton/core/runtime/worker.py` and contains ~5 `ActionType` comparisons
  in `_execution_loop()` that must also be updated.
- context_files:
  - agent_baton/models/execution.py
  - agent_baton/core/engine/executor.py
  - agent_baton/core/runtime/worker.py
- deliverables: Consistent enum usage; no `.value` comparisons at call sites
- allowed_paths: agent_baton/models/execution.py, agent_baton/core/engine/, agent_baton/core/runtime/worker.py
- blocked_paths: tests/
- depends_on: [2.3]

---

### Step 3.4: Verify ContextManager and Add write_state()

- agent: backend-engineer--python
- model: sonnet
- task: |
  `ContextManager.write_plan()` was updated in Step 1.2 to accept `MachinePlan`.
  Verify that change is correct and complete:
  1. Read `core/orchestration/context.py` and confirm `write_plan()` signature
     and import are correct (accepts `MachinePlan`, not `ExecutionPlan`).
  2. Read `core/__init__.py` and confirm `ContextManager` is still exported.
  3. Scan all call sites of `ContextManager.write_plan()` in `cli/` and `core/`
     using grep. Confirm each call site passes a `MachinePlan` instance.
  4. If any call site still passes `ExecutionPlan`, update it.
  5. Add a `write_state(state: ExecutionState) -> Path` convenience method to
     `ContextManager` that writes `execution-state.json` to `self._dir`. This
     mirrors the pattern of `write_plan()` and allows the CLI to use
     `ContextManager` as the single directory abstraction. Import `ExecutionState`
     from `agent_baton.models.execution`.
- context_files:
  - agent_baton/core/orchestration/context.py
  - agent_baton/core/__init__.py
- deliverables:
  - `ContextManager.write_plan()` confirmed correct for `MachinePlan`
  - `ContextManager.write_state()` method added
- allowed_paths: agent_baton/core/orchestration/context.py
- blocked_paths: tests/
- depends_on: [3.3]

---

### Step 3.4b: EventBus Wiring Safety (P10)

- agent: backend-engineer--python
- model: sonnet
- task: |
  > NOTE: This step was originally dropped because `runtime/` was absent in the
  > drafting worktree. On master, `runtime/` exists. This step implements P10.

  Create `agent_baton/core/runtime/context.py` with an `ExecutionContext` factory
  dataclass that ensures correct wiring of `ExecutionEngine + EventBus +
  EventPersistence`. See TDD P10 for the full class definition.

  1. Create `core/runtime/context.py` with the `ExecutionContext` dataclass and
     its `build()` classmethod.
  2. In `core/runtime/supervisor.py` `start()`, replace the manual
     `bus = bus or EventBus(); engine = ExecutionEngine(...)` wiring with
     `ExecutionContext.build(launcher=launcher, team_context_root=self._root, ...)`.
  3. Leave the `status()` method's bare `ExecutionEngine(...)` construction
     unchanged -- it is read-only and does not need bus wiring.
  4. Export `ExecutionContext` from `core/runtime/__init__.py`.
- context_files:
  - agent_baton/core/runtime/supervisor.py
  - agent_baton/core/runtime/__init__.py
  - agent_baton/core/events/bus.py
  - agent_baton/core/events/persistence.py
- deliverables:
  - `core/runtime/context.py` with `ExecutionContext`
  - `WorkerSupervisor.start()` uses `ExecutionContext.build()`
  - `ExecutionContext` exported from `core/runtime/__init__.py`
- allowed_paths: agent_baton/core/runtime/
- blocked_paths: tests/
- depends_on: [1.3, 1.5]

---

### Step 3.5: Update Tests for Phase 3

- agent: test-engineer
- model: sonnet
- task: |
  Update tests broken by Phase 3 changes:

  1. Enum changes (Step 3.3):
     - `test_executor.py` — update any `action.action_type == "dispatch"` style
       comparisons to `action.action_type == ActionType.DISPATCH`
     - `test_models.py` — update `StepResult` and `ExecutionAction` assertions
       for the new enum types

  2. Experimental module move (Step 3.2):
     - `test_async_dispatch.py` — update import from
       `core.distribute.async_dispatch` to
       `core.distribute.experimental.async_dispatch`
     - `test_incident.py` — update import from `core.distribute.incident` to
       `core.distribute.experimental.incident`
     - `test_transfer.py` — update import from `core.distribute.transfer` to
       `core.distribute.experimental.transfer`

  3. ContextManager changes (Step 3.4):
     - `test_context.py` — add test for `write_state()` method

  4. Public API changes (Step 3.1):
     - Add an import smoke test: `from agent_baton import ExecutionEngine,
       IntelligentPlanner, PromptDispatcher, GateRunner` — verify these resolve.

  Run `pytest tests/ -x -q --tb=short` and fix any remaining failures.
- context_files:
  - tests/test_executor.py
  - tests/test_models.py
  - tests/test_async_dispatch.py
  - tests/test_incident.py
  - tests/test_transfer.py
  - tests/test_context.py
- deliverables: All tests passing
- allowed_paths: tests/
- blocked_paths: agent_baton/
- depends_on: [3.1, 3.2, 3.3, 3.4, 3.4b]

---

### Gate 3: Build + Test

- type: test
- command: `pytest tests/ -x -q --tb=short`
- fail_on: any test failure

---

## Phase 4: CLI Reorganization

Step 4.1 can execute after Gate 3. Step 4.2 depends on 4.1.

---

### Step 4.1: Create CLI Command Groups

- agent: backend-engineer--python
- model: sonnet
- task: |
  Create subdirectories under `agent_baton/cli/commands/` and move command
  files into them. Each group directory needs an `__init__.py`.

  File assignments (35 commands on master):

  | Target group | Files |
  |---|---|
  | `execution/` | execute.py, plan_cmd.py, status.py, daemon.py, async_cmd.py, decide.py |
  | `observe/` | dashboard.py, trace.py, usage.py, telemetry.py, context_profile.py, retro.py |
  | `govern/` | classify.py, compliance.py, policy.py, escalations.py, validate.py, spec_check.py, detect.py |
  | `improve/` | scores.py, evolve.py, patterns.py, budget.py, changelog.py |
  | `distribute/` | package.py, publish.py, pull.py, verify_package.py, install.py, transfer.py |
  | `agents/` | agents.py, route.py, events.py, incident.py |

  Update `agent_baton/cli/main.py` discovery logic to scan subdirectories
  recursively. Replace the `pkgutil.iter_modules(commands_pkg.__path__)` loop
  with a recursive scan:

  ```python
  import os
  import importlib

  def _discover_recursive(pkg_path: str, pkg_prefix: str) -> dict[str, types.ModuleType]:
      found = {}
      for root, dirs, files in os.walk(pkg_path):
          dirs[:] = [d for d in dirs if not d.startswith('_')]
          for fname in files:
              if not fname.endswith('.py') or fname.startswith('_'):
                  continue
              rel = os.path.relpath(os.path.join(root, fname), pkg_path)
              mod_path = pkg_prefix + '.' + rel[:-3].replace(os.sep, '.')
              try:
                  mod = importlib.import_module(mod_path)
                  if hasattr(mod, 'register') and hasattr(mod, 'handler'):
                      found[mod_path] = mod
              except ImportError:
                  pass
      return found
  ```

  Verify all 35 commands register correctly by running:
  `python -m agent_baton.cli.main --help`
  and confirming all subcommand names appear.
- context_files:
  - agent_baton/cli/main.py
  - agent_baton/cli/commands/ (all current files)
- deliverables:
  - 6 group subdirectories with `__init__.py`
  - All 35 command files moved
  - `main.py` recursive discovery
  - `--help` output lists all subcommands
- allowed_paths: agent_baton/cli/
- blocked_paths: agent_baton/core/, agent_baton/models/, tests/
- depends_on: [3.5]

---

### Step 4.2: Update Tests for CLI Changes

- agent: test-engineer
- model: sonnet
- task: |
  Update any test files that import CLI command modules by path. Use grep to
  find all test files importing from `agent_baton.cli.commands`:
  `grep -rn "from agent_baton.cli.commands" tests/`

  For each such import, update the path to the new group subdirectory.
  For example:
  - `from agent_baton.cli.commands.classify import ...`
    → `from agent_baton.cli.commands.govern.classify import ...`

  Also update any test that invokes `baton <command>` via subprocess — those
  should continue working unchanged since the command names are unchanged.

  Run `pytest tests/ -x -q --tb=short && python -m agent_baton.cli.main --help`
  to confirm both tests and CLI registration work.
- context_files:
  - tests/ (all test files)
- deliverables: All tests passing; CLI help lists all commands
- allowed_paths: tests/
- blocked_paths: agent_baton/
- depends_on: [4.1]

---

### Gate 4: Full Validation

- type: test
- command: `pytest tests/ -x -q --tb=short && python -m agent_baton.cli.main --help`
- fail_on: any test failure or missing CLI command

---

## Git Strategy

| Item | Convention |
|------|-----------|
| Branch | `rearch/phase-N` per phase (e.g. `rearch/phase-1`) |
| Commit message | `rearch(PN.S): <description>` (e.g. `rearch(P1.1): remove vestigial plan models`) |
| Merge strategy | Squash-merge each phase branch to master after its gate passes |
| Tags | `rearch-phase-N-complete` after each merge |

## Step Dependency Graph

```
1.1 ─┐
1.2 ─┤ (1.2 depends on 1.1)
1.3 ─┤ (parallel with 1.1, 1.2)
1.4 ─┤ (parallel with 1.1, 1.2, 1.3)
1.5 ─┤ (parallel with 1.1, 1.2, 1.3, 1.4)
     └─→ 1.6 ──→ Gate 1
                    │
              2.1 ──┤ (parallel)
              2.2 ──┤ (parallel)
                    └─→ 2.3 ──→ Gate 2
                                   │
                         3.1 ──────┤ (parallel)
                         3.2 ──────┤ (parallel)
                         3.3 ──────┤ (parallel)
                         3.4b ─────┤ (parallel; depends on 1.3+1.5 via Gate 1)
                                   └─→ 3.4 ──→ 3.5 ──→ Gate 3
                                                           │
                                                   4.1 ────┤
                                                           └─→ 4.2 ──→ Gate 4
```
