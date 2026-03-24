# Re-Architecture Technical Design Document

Machine-parseable spec. Each proposal is independent enough for parallel
subagent execution within the dependency constraints given. Agents must read
the codebase before executing; paths below are verified against the actual
source tree.

---

## Inventory: Verified file tree

```
agent_baton/
  __init__.py
  models/
    __init__.py        # exports legacy plan classes + execution classes
    plan.py            # ExecutionPlan, Phase, AgentAssignment, QAGate, MissionLogEntry
    execution.py       # MachinePlan, PlanStep, PlanPhase, PlanGate, ExecutionState,
                       # StepResult, GateResult, ExecutionAction, ActionType,
                       # StepStatus, PhaseStatus
    enums.py
    events.py
    decision.py
    (+ 9 other model files)
  core/
    __init__.py        # 50-line re-export facade (imports from sub-packages + shims)
    # ── Shim layer (21 files, all one-liners) ──────────────────────────────
    async_dispatch.py  → distribute/async_dispatch.py
    classifier.py      → govern/classifier.py
    compliance.py      → govern/compliance.py
    context.py         → orchestration/context.py
    dashboard.py       → observe/dashboard.py
    escalation.py      → govern/escalation.py
    evolution.py       → improve/evolution.py
    incident.py        → distribute/incident.py
    plan.py            → orchestration/plan.py
    policy.py          → govern/policy.py
    registry.py        → orchestration/registry.py
    retrospective.py   → observe/retrospective.py
    router.py          → orchestration/router.py
    scoring.py         → improve/scoring.py
    sharing.py         → distribute/sharing.py
    spec_validator.py  → govern/spec_validator.py
    telemetry.py       → observe/telemetry.py
    transfer.py        → distribute/transfer.py
    usage.py           → observe/usage.py
    validator.py       → govern/validator.py
    vcs.py             → improve/vcs.py
    # ── Sub-packages ───────────────────────────────────────────────────────
    engine/
      __init__.py      # exports ExecutionEngine, IntelligentPlanner,
                       #         PromptDispatcher, GateRunner
      executor.py      # ExecutionEngine — _save_state/_load_state inline
      planner.py       # IntelligentPlanner — imports PlanBuilder from
                       #                      orchestration/plan.py at line 206
      dispatcher.py    # PromptDispatcher
      gates.py         # GateRunner
    orchestration/
      __init__.py      # exports AgentRegistry, AgentRouter, PlanBuilder, ContextManager
      plan.py          # PlanBuilder class (uses ExecutionPlan from models/plan.py)
      context.py       # ContextManager (imports ExecutionPlan, MissionLogEntry
                       #                 from models/plan.py)
      registry.py
      router.py
    govern/
      __init__.py
      classifier.py
      compliance.py
      escalation.py
      policy.py
      spec_validator.py
      validator.py
    observe/
      __init__.py
      context_profiler.py
      dashboard.py
      retrospective.py
      telemetry.py
      trace.py
      usage.py
    improve/
      __init__.py
      evolution.py
      scoring.py
      vcs.py
    distribute/
      __init__.py
      async_dispatch.py
      incident.py
      packager.py
      registry_client.py
      sharing.py
      transfer.py
    events/
      __init__.py      # exports EventBus, EventPersistence, projections
      bus.py
      events.py
      persistence.py
      projections.py
    runtime/
      __init__.py      # exports AgentLauncher, DryRunLauncher, LaunchResult,
                       #         ClaudeCodeLauncher, ClaudeCodeConfig,
                       #         StepScheduler, SchedulerConfig, SignalHandler,
                       #         TaskWorker, DecisionManager, WorkerSupervisor
      claude_launcher.py
      daemon.py
      decisions.py
      launcher.py
      scheduler.py
      signals.py
      supervisor.py    # WorkerSupervisor — wires EventBus+Engine inline
      worker.py        # TaskWorker — publishes step events AND engine publishes them
    learn/
      __init__.py
      budget_tuner.py
      pattern_learner.py
  cli/
    __init__.py
    main.py            # discover_commands() scans commands/ flat dir only
    commands/
      __init__.py
      # 35 .py files — all import from core shim paths (see grep output above)
```

---

## P1: Remove Dual Plan Model Hierarchy

**Goal**: Eliminate the legacy `ExecutionPlan`/`Phase`/`AgentAssignment`/`QAGate`
model tree in `models/plan.py`. `MachinePlan` (in `models/execution.py`) is the
canonical plan type. `MissionLogEntry` is the only keeper from `models/plan.py`.
`PlanBuilder` in `orchestration/plan.py` uses `ExecutionPlan` exclusively — it
must be deleted or replaced (see precise changes below).

**Files to modify**:

| File | Change |
|------|--------|
| `agent_baton/models/plan.py` | Delete classes `ExecutionPlan`, `Phase`, `AgentAssignment`, `QAGate` and their import block (lines 1–115). Keep `MissionLogEntry` (lines 118–157). Update module-level imports to only what `MissionLogEntry` needs: `FailureClass` from `enums.py`. |
| `agent_baton/models/__init__.py` | Remove from import block and `__all__`: `AgentAssignment`, `ExecutionPlan`, `Phase`, `QAGate`. Keep `MissionLogEntry`. |
| `agent_baton/core/orchestration/plan.py` | Delete entire file. `PlanBuilder` is the sole class; `IntelligentPlanner` already imports `_PB._select_git_strategy` from it inline. See migration note below. |
| `agent_baton/core/engine/planner.py` | Line 17: remove `from agent_baton.core.orchestration.plan import PlanBuilder`. Line 97: replace `self._plan_builder = PlanBuilder()` with inline risk-assessment logic (see below). Lines 206–207: replace `from agent_baton.core.orchestration.plan import PlanBuilder as _PB` + `git_strategy = _PB._select_git_strategy(risk_level_enum).value` with a module-level private function `_select_git_strategy(risk: RiskLevel) -> str` (copy the static method body). |
| `agent_baton/core/orchestration/context.py` | Line 7: change `from agent_baton.models.plan import ExecutionPlan, MissionLogEntry` to `from agent_baton.models.plan import MissionLogEntry` and `from agent_baton.models.execution import MachinePlan`. Line 37: change signature `def write_plan(self, plan: ExecutionPlan) -> Path:` to `def write_plan(self, plan: MachinePlan) -> Path:`. |
| `agent_baton/core/orchestration/__init__.py` | Remove `PlanBuilder` from imports and `__all__`. |
| `agent_baton/core/__init__.py` | Remove `PlanBuilder` from import line 3 and from `__all__`. |
| `agent_baton/__init__.py` | Remove `PlanBuilder` from import line 5 and from `__all__`. |

**Files to delete**:

```
agent_baton/core/orchestration/plan.py
agent_baton/core/plan.py              # shim for orchestration/plan.py
```

**Precise change — planner.py risk assessment migration**:

Add this module-level function to `agent_baton/core/engine/planner.py` (after
the `_TASK_TYPE_KEYWORDS` constant block, before the `IntelligentPlanner` class):

```python
# ---------------------------------------------------------------------------
# Absorbed from the removed PlanBuilder / orchestration/plan.py
# ---------------------------------------------------------------------------

_RISK_SIGNALS: dict[str, str] = {
    "production": "HIGH",
    "infrastructure": "HIGH",
    "docker": "HIGH",
    "ci/cd": "HIGH",
    "deploy": "HIGH",
    "terraform": "HIGH",
    "compliance": "HIGH",
    "regulated": "HIGH",
    "audit": "HIGH",
    "migration": "MEDIUM",
    "database": "MEDIUM",
    "schema": "MEDIUM",
    "bash": "MEDIUM",
    "security": "HIGH",
    "authentication": "HIGH",
    "secrets": "HIGH",
}

_RISK_ORDINAL: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _assess_risk_from_keywords(task_description: str) -> str:
    """Return the highest risk level found from keyword signals."""
    description_lower = task_description.lower()
    highest = "LOW"
    for keyword, level in _RISK_SIGNALS.items():
        if keyword in description_lower:
            if _RISK_ORDINAL[level] > _RISK_ORDINAL[highest]:
                highest = level
    return highest


def _select_git_strategy(risk_level: str) -> str:
    if risk_level in ("HIGH", "CRITICAL"):
        return "branch-per-agent"
    return "commit-per-agent"
```

In `IntelligentPlanner.__init__`, remove `self._plan_builder = PlanBuilder()`.

In `IntelligentPlanner._assess_risk`, replace the call
`risk_enum = self._plan_builder.assess_risk(task_summary)` and subsequent
`keyword_score = {...}.get(risk_enum, 0)` with:
```python
keyword_risk_str = _assess_risk_from_keywords(task_summary)
score = max(score, _RISK_ORDINAL.get(keyword_risk_str, 0))
```
Note: `RiskLevel` enum import from `agent_baton.models.enums` is no longer
needed in `planner.py` after this change — remove it.

In `IntelligentPlanner._assess_risk`, replace the final return block (lines
614–616 of current source) with:
```python
_LEVELS = {0: "LOW", 1: "MEDIUM", 2: "HIGH"}
return _LEVELS[score]
```

In `IntelligentPlanner.create_plan` (lines 206–207), replace:
```python
from agent_baton.core.orchestration.plan import PlanBuilder as _PB
git_strategy = _PB._select_git_strategy(risk_level_enum).value
```
with:
```python
git_strategy = _select_git_strategy(risk_level)
```
Also remove `risk_level_enum = RiskLevel(risk_level)` on line 203 since
`RiskLevel` is no longer imported.

**Validation**:

```bash
python -c "from agent_baton.models.execution import MachinePlan; print('OK')"
python -c "from agent_baton.models.plan import MissionLogEntry; print('OK')"
python -c "from agent_baton.core.engine import IntelligentPlanner; print('OK')"
python -c "from agent_baton.core.orchestration import ContextManager; print('OK')"
# Verify deleted classes are gone
grep -r "ExecutionPlan\|PlanBuilder\|AgentAssignment\|class Phase\|class QAGate" \
  agent_baton/ --include="*.py" | grep -v __pycache__ | grep -v "test_"
# ^ should be empty
pytest tests/test_planner.py tests/test_context.py tests/test_engine_planner.py \
  tests/test_engine_integration.py -x -q
```

**Dependencies**: None. Execute first; P2 depends on this.

---

## P2: Remove Backward-Compatibility Shim Layer

**Goal**: Delete all 21 shim files under `agent_baton/core/*.py` and migrate
every consumer to canonical import paths.

**Files to delete**:

```
agent_baton/core/async_dispatch.py
agent_baton/core/classifier.py
agent_baton/core/compliance.py
agent_baton/core/context.py
agent_baton/core/dashboard.py
agent_baton/core/escalation.py
agent_baton/core/evolution.py
agent_baton/core/incident.py
agent_baton/core/plan.py            # deleted by P1; verify absent
agent_baton/core/policy.py
agent_baton/core/registry.py
agent_baton/core/retrospective.py
agent_baton/core/router.py
agent_baton/core/scoring.py
agent_baton/core/sharing.py
agent_baton/core/spec_validator.py
agent_baton/core/telemetry.py
agent_baton/core/transfer.py
agent_baton/core/usage.py
agent_baton/core/validator.py
agent_baton/core/vcs.py
```

**Import migration map — apply to all files in `agent_baton/cli/commands/` and `tests/`**:

| Old import (shim) | New import (canonical) |
|-------------------|------------------------|
| `from agent_baton.core.async_dispatch import X` | `from agent_baton.core.distribute.async_dispatch import X` |
| `from agent_baton.core.classifier import X` | `from agent_baton.core.govern.classifier import X` |
| `from agent_baton.core.compliance import X` | `from agent_baton.core.govern.compliance import X` |
| `from agent_baton.core.context import X` | `from agent_baton.core.orchestration.context import X` |
| `from agent_baton.core.dashboard import X` | `from agent_baton.core.observe.dashboard import X` |
| `from agent_baton.core.escalation import X` | `from agent_baton.core.govern.escalation import X` |
| `from agent_baton.core.evolution import X` | `from agent_baton.core.improve.evolution import X` |
| `from agent_baton.core.incident import X` | `from agent_baton.core.distribute.incident import X` |
| `from agent_baton.core.plan import X` | deleted in P1; X = `PlanBuilder`/`RISK_SIGNALS` no longer exist |
| `from agent_baton.core.policy import X` | `from agent_baton.core.govern.policy import X` |
| `from agent_baton.core.registry import X` | `from agent_baton.core.orchestration.registry import X` |
| `from agent_baton.core.retrospective import X` | `from agent_baton.core.observe.retrospective import X` |
| `from agent_baton.core.router import X` | `from agent_baton.core.orchestration.router import X` |
| `from agent_baton.core.scoring import X` | `from agent_baton.core.improve.scoring import X` |
| `from agent_baton.core.sharing import X` | `from agent_baton.core.distribute.sharing import X` |
| `from agent_baton.core.spec_validator import X` | `from agent_baton.core.govern.spec_validator import X` |
| `from agent_baton.core.telemetry import X` | `from agent_baton.core.observe.telemetry import X` |
| `from agent_baton.core.transfer import X` | `from agent_baton.core.distribute.transfer import X` |
| `from agent_baton.core.usage import X` | `from agent_baton.core.observe.usage import X` |
| `from agent_baton.core.validator import X` | `from agent_baton.core.govern.validator import X` |
| `from agent_baton.core.vcs import X` | `from agent_baton.core.improve.vcs import X` |

**Affected CLI command files (verified by grep)**:

| File | Shim used |
|------|-----------|
| `cli/commands/spec_check.py` | `core.spec_validator` |
| `cli/commands/package.py` | `core.sharing` |
| `cli/commands/usage.py` | `core.usage` |
| `cli/commands/policy.py` | `core.policy` |
| `cli/commands/scores.py` | `core.scoring` |
| `cli/commands/status.py` | `core.context` |
| `cli/commands/classify.py` | `core.classifier` |
| `cli/commands/dashboard.py` | `core.dashboard` |
| `cli/commands/telemetry.py` | `core.telemetry` |
| `cli/commands/changelog.py` | `core.vcs` |
| `cli/commands/escalations.py` | `core.escalation` |
| `cli/commands/route.py` | `core.registry`, `core.router` |
| `cli/commands/evolve.py` | `core.evolution` |
| `cli/commands/compliance.py` | `core.compliance` |
| `cli/commands/retro.py` | `core.retrospective` |
| `cli/commands/transfer.py` | `core.transfer` |
| `cli/commands/validate.py` | `core.validator` |
| `cli/commands/agents.py` | `core.registry` |
| `cli/commands/incident.py` | `core.incident` |
| `cli/commands/detect.py` | `core.registry`, `core.router` |
| `cli/commands/async_cmd.py` | `core.async_dispatch` |

**Affected test files (verified by grep)**:

| File | Shim used |
|------|-----------|
| `tests/test_telemetry.py` | `core.telemetry` |
| `tests/test_vcs.py` | `core.vcs` |
| `tests/test_retrospective.py` | `core.retrospective` |
| `tests/test_spec_validator.py` | `core.spec_validator` |
| `tests/test_escalation.py` | `core.escalation` |
| `tests/test_registry.py` | `core.registry` |
| `tests/test_async_dispatch.py` | `core.async_dispatch` |
| `tests/test_router.py` | `core.registry`, `core.router` |
| `tests/test_dashboard.py` | `core.usage`, `core.dashboard` |
| `tests/test_policy.py` | `core.policy` |
| `tests/test_sharing.py` | `core.sharing` |
| `tests/test_context.py` | `core.context` |
| `tests/test_compliance.py` | `core.compliance` |
| `tests/test_usage.py` | `core.usage` |
| `tests/test_transfer.py` | `core.transfer` |
| `tests/test_evolution.py` | `core.usage`, `core.retrospective`, `core.scoring`, `core.evolution` |
| `tests/test_scoring.py` | `core.usage`, `core.retrospective`, `core.scoring` |
| `tests/conftest.py` | `core.registry` |
| `tests/test_incident.py` | `core.incident` |
| `tests/test_validator.py` | `core.validator` |
| `tests/test_classifier.py` | `core.classifier` |
| `tests/test_planner.py` | `core.plan` (imports `PlanBuilder, RISK_SIGNALS` — both deleted in P1; this test file must be updated to test only `IntelligentPlanner` and the module-level `_assess_risk_from_keywords`/`_select_git_strategy` functions, or deleted if no longer applicable) |

**Files to modify — `agent_baton/core/__init__.py`**:

> **Consolidation note**: The orchestration plan (Step 2.3) takes a minimal
> approach: prune `core/__init__.py` to only 3 exports (`AgentRegistry`,
> `AgentRouter`, `ContextManager`), deferring the full public API expansion to
> P6. The spec below preserves all current exports from canonical paths. During
> execution, prefer the orchestration plan's minimal approach for P2 and expand
> in P6; the full facade below serves as reference for what P6 should restore.

Replace the full content with a slimmed facade. Remove all shim re-exports.
Keep only canonical sub-package re-exports. The file should import only from
sub-package paths:

```python
from __future__ import annotations

from agent_baton.core.orchestration import AgentRegistry, AgentRouter, ContextManager
from agent_baton.core.engine import ExecutionEngine, IntelligentPlanner, PromptDispatcher, GateRunner
from agent_baton.core.events import EventBus, EventPersistence, TaskView, PhaseView, StepView, project_task_view
from agent_baton.core.runtime import (
    AgentLauncher, DryRunLauncher, LaunchResult,
    ClaudeCodeLauncher, ClaudeCodeConfig,
    StepScheduler, SchedulerConfig,
    TaskWorker, DecisionManager, WorkerSupervisor,
)
from agent_baton.core.govern import EscalationManager, AgentValidator
from agent_baton.core.observe import UsageLogger, RetrospectiveEngine, DashboardGenerator
from agent_baton.core.improve import AgentVersionControl, ChangelogEntry, PerformanceScorer, AgentScorecard
from agent_baton.core.observe import AgentTelemetry, TelemetryEvent
from agent_baton.core.govern import (
    SpecValidator, SpecValidationResult, SpecCheck,
    DataClassifier, ClassificationResult,
    ComplianceReportGenerator, ComplianceReport, ComplianceEntry,
    PolicyEngine, PolicySet, PolicyRule, PolicyViolation,
)
from agent_baton.core.improve import PromptEvolutionEngine, EvolutionProposal
from agent_baton.core.distribute import (
    ProjectTransfer, TransferManifest,
    PackageBuilder, PackageManifest,
    IncidentManager, IncidentTemplate, IncidentPhase,
    AsyncDispatcher, AsyncTask,
    PackageVerifier, EnhancedManifest, PackageValidationResult,
    RegistryClient,
)
from agent_baton.core.observe import TraceRecorder, TraceRenderer, ContextProfiler
from agent_baton.core.learn import PatternLearner, BudgetTuner

__all__ = [
    "AgentRegistry", "AgentRouter", "ContextManager",
    "ExecutionEngine", "IntelligentPlanner", "PromptDispatcher", "GateRunner",
    "EventBus", "EventPersistence", "TaskView", "PhaseView", "StepView", "project_task_view",
    "AgentLauncher", "DryRunLauncher", "LaunchResult",
    "ClaudeCodeLauncher", "ClaudeCodeConfig",
    "StepScheduler", "SchedulerConfig",
    "TaskWorker", "DecisionManager", "WorkerSupervisor",
    "EscalationManager", "AgentValidator",
    "UsageLogger", "RetrospectiveEngine", "DashboardGenerator",
    "AgentVersionControl", "ChangelogEntry", "PerformanceScorer", "AgentScorecard",
    "AgentTelemetry", "TelemetryEvent",
    "SpecValidator", "SpecValidationResult", "SpecCheck",
    "DataClassifier", "ClassificationResult",
    "ComplianceReportGenerator", "ComplianceReport", "ComplianceEntry",
    "PolicyEngine", "PolicySet", "PolicyRule", "PolicyViolation",
    "PromptEvolutionEngine", "EvolutionProposal",
    "ProjectTransfer", "TransferManifest",
    "PackageBuilder", "PackageManifest",
    "IncidentManager", "IncidentTemplate", "IncidentPhase",
    "AsyncDispatcher", "AsyncTask",
    "PackageVerifier", "EnhancedManifest", "PackageValidationResult", "RegistryClient",
    "TraceRecorder", "TraceRenderer", "ContextProfiler",
    "PatternLearner", "BudgetTuner",
]
```

**Validation**:

```bash
# No shim files remain
ls agent_baton/core/*.py 2>/dev/null | grep -v __init__ | grep -v __pycache__
# ^ should produce no output

# All imports resolve
python -c "import agent_baton; print('OK')"
python -c "from agent_baton.core import AgentRegistry, ContextManager, ExecutionEngine; print('OK')"
pytest tests/ -x -q
```

**Dependencies**: P1 must complete first (plan.py shim and PlanBuilder removal
must precede this sweep to avoid broken test_planner.py).

---

## P3: Formalize Worker-Engine Protocol

**Goal**: Introduce a `Protocol` type so `TaskWorker` and `WorkerSupervisor`
depend on an interface rather than the concrete `ExecutionEngine` class.

**Files to create**:

`agent_baton/core/engine/protocols.py`

```python
"""Structural protocols for the execution engine."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agent_baton.models.execution import ExecutionAction, MachinePlan


@runtime_checkable
class ExecutionDriver(Protocol):
    """Structural protocol that the execution engine must satisfy.

    Consumers (TaskWorker, WorkerSupervisor) should type-hint against
    ExecutionDriver rather than the concrete ExecutionEngine so that
    test doubles can be passed without subclassing.
    """

    def start(self, plan: MachinePlan) -> ExecutionAction: ...

    def next_action(self) -> ExecutionAction: ...

    def next_actions(self) -> list[ExecutionAction]: ...

    def mark_dispatched(self, step_id: str, agent_name: str) -> None: ...

    def record_step_result(
        self,
        step_id: str,
        agent_name: str,
        status: str = "complete",
        outcome: str = "",
        files_changed: list[str] | None = None,
        commit_hash: str = "",
        estimated_tokens: int = 0,
        duration_seconds: float = 0.0,
        error: str = "",
    ) -> None: ...

    def record_gate_result(
        self,
        phase_id: int,
        passed: bool,
        output: str = "",
    ) -> None: ...

    def complete(self) -> str: ...

    def status(self) -> dict: ...

    def resume(self) -> ExecutionAction: ...

    def recover_dispatched_steps(self) -> int: ...
```

**Files to modify**:

`agent_baton/core/engine/__init__.py` — add to imports and `__all__`:
```python
from agent_baton.core.engine.protocols import ExecutionDriver
```
Add `"ExecutionDriver"` to `__all__`.

`agent_baton/core/runtime/worker.py` — change type hint only (no behavioral
change). Line 19: add `from agent_baton.core.engine.protocols import ExecutionDriver`.
Line 50: change `engine: ExecutionEngine` parameter type to `engine: ExecutionDriver`.
Remove `from agent_baton.core.engine.executor import ExecutionEngine` import if
it is no longer referenced anywhere else in the file (verify first).

`agent_baton/core/runtime/supervisor.py` — Line 21: add
`from agent_baton.core.engine.protocols import ExecutionDriver`.
Line 280: change `def _write_status(self, engine: ExecutionEngine, ...)` to
`def _write_status(self, engine: ExecutionDriver, ...)`.
Keep `from agent_baton.core.engine.executor import ExecutionEngine` since it is
used in `start()` (line 91) and `status()` (line 178) where `ExecutionEngine` is
instantiated directly.

**Validation**:

```bash
python -c "from agent_baton.core.engine.protocols import ExecutionDriver; print('OK')"
python -c "from agent_baton.core.engine import ExecutionDriver; print('OK')"
python -c "
from agent_baton.core.engine import ExecutionEngine, ExecutionDriver
assert isinstance(ExecutionEngine(), ExecutionDriver), 'Engine must satisfy protocol'
print('Protocol check OK')
"
pytest tests/test_runtime.py tests/test_executor.py -x -q
```

**Dependencies**: None. Can execute in parallel with P1.

---

## P4: De-duplicate Event Publishing

**Goal**: Each step-level event (`step.dispatched`, `step.completed`,
`step.failed`) is currently published by both `ExecutionEngine` (inside
`record_step_result` / `mark_dispatched`) and `TaskWorker` (after scheduling
results arrive). Remove the duplicate publishes from the engine. The engine
retains ownership of phase-level and task-level events. The worker retains
ownership of step-level events.

**Files to modify**:

`agent_baton/core/engine/executor.py`

Remove from `record_step_result()` (lines 298–325 of current source):

```python
# DELETE — entire block:
if status == "complete":
    self._publish(evt.step_completed(...))
elif status == "failed":
    self._publish(evt.step_failed(...))
elif status == "dispatched":
    step_model = _model_for_step(state.plan, step_id)
    self._publish(evt.step_dispatched(...))
```

Keep: all trace recording logic, `_save_state`, and the `_publish` calls in
`record_gate_result()`, `_determine_action()` (phase_started, phase_completed),
`start()` (task_started), and `complete()` (task_completed).

Also remove the `_model_for_step` call on the `step_dispatched` path since
that function call existed solely for the deleted event. If `_model_for_step`
has no other callers after this deletion, remove it from the file too.
Verify by running:
```bash
grep -n "_model_for_step" agent_baton/core/engine/executor.py
```
`_model_for_step` is also used in `_build_usage_record` (line 592) — do NOT
remove it.

Add docstring to the `_publish` method (line 551) clarifying ownership:
```python
def _publish(self, event: Event) -> None:
    """Publish an event if a bus is configured.

    Ownership contract:
      Engine publishes: task.started, phase.started, phase.completed,
                        gate.passed, gate.failed, task.completed.
      Worker publishes: step.dispatched, step.completed, step.failed.
    """
    if self._bus is not None:
        self._bus.publish(event)
```

**Files to modify — `agent_baton/core/events/events.py`**:

Add module docstring at top of file:
```python
"""Domain event factory functions.

Event ownership:
  ExecutionEngine: task.started, phase.started, phase.completed,
                   gate.passed, gate.failed, task.completed
  TaskWorker:      step.dispatched, step.completed, step.failed
"""
```

**Validation**:

```bash
# Engine no longer imports or calls step-level event factories for those three events
grep -n "step_dispatched\|step_completed\|step_failed" \
  agent_baton/core/engine/executor.py
# ^ should return no hits (or hits only in comments/docstrings)

# Worker still publishes them
grep -n "step_dispatched\|step_completed\|step_failed" \
  agent_baton/core/runtime/worker.py
# ^ should have hits at lines ~131-136, ~178-198

pytest tests/test_engine_events.py tests/test_executor.py tests/test_runtime.py -x -q
```

**Dependencies**: None. Can execute in parallel with P1 and P3.

---

## P5: Split executor.py — Extract StatePersistence

**Goal**: Extract `_save_state`/`_load_state` from `ExecutionEngine` into a
dedicated `StatePersistence` class that owns the `execution-state.json`
read/write contract.

**Files to create**:

`agent_baton/core/engine/persistence.py`

```python
"""StatePersistence — atomic read/write for execution-state.json."""
from __future__ import annotations

import json
import os
from pathlib import Path

from agent_baton.models.execution import ExecutionState


class StatePersistence:
    """Handles atomic read/write of execution state to disk.

    Uses a tmp+rename pattern to prevent corruption on crash mid-write.
    """

    _FILENAME = "execution-state.json"

    def __init__(self, context_root: Path) -> None:
        self._root = context_root

    @property
    def state_path(self) -> Path:
        return self._root / self._FILENAME

    def save(self, state: ExecutionState) -> Path:
        """Write *state* atomically.  Returns the state file path."""
        self._root.mkdir(parents=True, exist_ok=True)
        path = self.state_path
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.rename(str(tmp_path), str(path))
        return path

    def load(self) -> ExecutionState | None:
        """Load state from disk; return None if absent or malformed."""
        if not self.state_path.exists():
            return None
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            return ExecutionState.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def exists(self) -> bool:
        return self.state_path.exists()

    def clear(self) -> None:
        """Delete the state file if it exists."""
        if self.state_path.exists():
            self.state_path.unlink()
```

**Files to modify**:

`agent_baton/core/engine/executor.py`:

1. Add import: `from agent_baton.core.engine.persistence import StatePersistence`
2. In `__init__`, add: `self._persistence = StatePersistence(self._root)`
3. Replace `_save_state` method body with:
   ```python
   def _save_state(self, state: ExecutionState) -> Path:
       return self._persistence.save(state)
   ```
4. Replace `_load_state` method body with:
   ```python
   def _load_state(self) -> ExecutionState | None:
       return self._persistence.load()
   ```
   Keep both method signatures identical (they are called throughout the class).
   Do NOT inline the calls — the wrapper methods allow future subclassing.

`agent_baton/core/engine/__init__.py` — add:
```python
from agent_baton.core.engine.persistence import StatePersistence
```
Add `"StatePersistence"` to `__all__`.

**Validation**:

```bash
python -c "from agent_baton.core.engine.persistence import StatePersistence; print('OK')"
python -c "from agent_baton.core.engine import StatePersistence; print('OK')"
pytest tests/test_executor.py tests/test_engine_integration.py -x -q
```

**Dependencies**: None. Can execute in parallel with P1, P3, P4.

---

## P6: Explicit Core vs Peripheral Layering

**Goal**: Expose the execution core (engine, runtime, events) directly from the
top-level `agent_baton` package so callers do not need to navigate sub-packages.

**Files to modify**:

`agent_baton/__init__.py` — Replace with:

```python
from __future__ import annotations

__version__ = "0.1.0"

# Execution core
from agent_baton.core.engine import ExecutionEngine, IntelligentPlanner, PromptDispatcher, GateRunner
from agent_baton.core.runtime import (
    AgentLauncher, DryRunLauncher, LaunchResult,
    ClaudeCodeLauncher, ClaudeCodeConfig,
    TaskWorker, WorkerSupervisor,
)
from agent_baton.core.events import EventBus, EventPersistence
# Orchestration
from agent_baton.core.orchestration import AgentRegistry, AgentRouter, ContextManager

__all__ = [
    "__version__",
    # engine
    "ExecutionEngine",
    "IntelligentPlanner",
    "PromptDispatcher",
    "GateRunner",
    # runtime
    "AgentLauncher",
    "DryRunLauncher",
    "LaunchResult",
    "ClaudeCodeLauncher",
    "ClaudeCodeConfig",
    "TaskWorker",
    "WorkerSupervisor",
    # events
    "EventBus",
    "EventPersistence",
    # orchestration
    "AgentRegistry",
    "AgentRouter",
    "ContextManager",
]
```

`agent_baton/core/__init__.py` — Add a module-level docstring at the top:

```python
"""agent_baton.core — orchestration engine and peripheral services.

Core (always present, used by the runtime loop):
  engine/        ExecutionEngine, IntelligentPlanner, PromptDispatcher, GateRunner
  runtime/       TaskWorker, WorkerSupervisor, ClaudeCodeLauncher, StepScheduler
  events/        EventBus, EventPersistence, projections
  orchestration/ AgentRegistry, AgentRouter, ContextManager

Peripheral (feature services, used by CLI and optional integrations):
  govern/        Compliance, policy, classification, validation
  observe/       Usage, telemetry, dashboard, trace, context profiler, retrospective
  improve/       Scoring, evolution, VCS
  distribute/    Packaging, sharing, transfer, incident, async dispatch
  learn/         PatternLearner, BudgetTuner
"""
```

**Validation**:

```bash
python -c "
from agent_baton import (
    ExecutionEngine, IntelligentPlanner,
    EventBus, ClaudeCodeLauncher,
    AgentRegistry, ContextManager,
    TaskWorker, WorkerSupervisor,
)
print('OK')
"
pytest tests/ -x -q
```

**Dependencies**: P2 (shim removal simplifies the `core/__init__.py` docstring;
`agent_baton/__init__.py` no longer imports `PlanBuilder`).

---

## P7: Gate Experimental distribute Modules

**Goal**: Move the three experimental distribute modules (`incident`,
`async_dispatch`, `transfer`) into an `experimental/` sub-package to signal
their API stability level without breaking the main distribute package.

**Files to create**:

`agent_baton/core/distribute/experimental/__init__.py`

```python
"""Experimental distribute modules — APIs may change without notice."""
from __future__ import annotations

from agent_baton.core.distribute.experimental.incident import (
    IncidentManager, IncidentTemplate, IncidentPhase,
)
from agent_baton.core.distribute.experimental.async_dispatch import (
    AsyncDispatcher, AsyncTask,
)
from agent_baton.core.distribute.experimental.transfer import (
    ProjectTransfer, TransferManifest,
)

__all__ = [
    "IncidentManager", "IncidentTemplate", "IncidentPhase",
    "AsyncDispatcher", "AsyncTask",
    "ProjectTransfer", "TransferManifest",
]
```

**Files to move** (rename/copy then delete originals):

| From | To |
|------|----|
| `agent_baton/core/distribute/incident.py` | `agent_baton/core/distribute/experimental/incident.py` |
| `agent_baton/core/distribute/async_dispatch.py` | `agent_baton/core/distribute/experimental/async_dispatch.py` |
| `agent_baton/core/distribute/transfer.py` | `agent_baton/core/distribute/experimental/transfer.py` |

**Files to modify**:

`agent_baton/core/distribute/__init__.py` — Replace imports of the three moved
modules with imports from `experimental`:

```python
# Remove:
from agent_baton.core.distribute.transfer import ProjectTransfer, TransferManifest
from agent_baton.core.distribute.incident import IncidentManager, IncidentTemplate, IncidentPhase
from agent_baton.core.distribute.async_dispatch import AsyncDispatcher, AsyncTask

# Add:
from agent_baton.core.distribute.experimental import (
    ProjectTransfer, TransferManifest,
    IncidentManager, IncidentTemplate, IncidentPhase,
    AsyncDispatcher, AsyncTask,
)
```
Keep `__all__` unchanged — the public surface of `core.distribute` is unchanged.

`agent_baton/cli/commands/incident.py` — After P2 has run, import is already
`from agent_baton.core.distribute.incident import IncidentManager`. Change to:
`from agent_baton.core.distribute.experimental.incident import IncidentManager`

`agent_baton/cli/commands/transfer.py` — Change:
`from agent_baton.core.distribute.transfer import ProjectTransfer, TransferManifest`
to:
`from agent_baton.core.distribute.experimental.transfer import ProjectTransfer, TransferManifest`

`agent_baton/cli/commands/async_cmd.py` — Change:
`from agent_baton.core.distribute.async_dispatch import AsyncDispatcher, AsyncTask`
to:
`from agent_baton.core.distribute.experimental.async_dispatch import AsyncDispatcher, AsyncTask`

**Note**: Callers that import from `agent_baton.core.distribute` (the package)
are unaffected — `__all__` is unchanged and re-exports are maintained.

**Validation**:

```bash
python -c "
from agent_baton.core.distribute.experimental import (
    IncidentManager, AsyncDispatcher, ProjectTransfer
)
print('OK')
"
python -c "
from agent_baton.core.distribute import (
    IncidentManager, AsyncDispatcher, ProjectTransfer
)
print('backwards-compat OK')
"
pytest tests/test_incident.py tests/test_transfer.py tests/test_async_dispatch.py -x -q
```

**Dependencies**: P2 (canonical imports must be in place before moving files
to avoid confusing shim-path callers).

---

## P8: Group CLI Commands into Sub-directories

**Goal**: Restructure `agent_baton/cli/commands/` from 35 flat files into 6
sub-packages grouped by domain. Update the auto-discovery in `cli/main.py` to
scan recursively.

**Directory structure to create**:

```
agent_baton/cli/commands/
  execution/
    __init__.py
    execute.py       ← move from commands/execute.py
    plan_cmd.py      ← move from commands/plan_cmd.py
    status.py        ← move from commands/status.py
    daemon.py        ← move from commands/daemon.py
    async_cmd.py     ← move from commands/async_cmd.py
    decide.py        ← move from commands/decide.py
  observe/
    __init__.py
    dashboard.py     ← move from commands/dashboard.py
    trace.py         ← move from commands/trace.py
    usage.py         ← move from commands/usage.py
    telemetry.py     ← move from commands/telemetry.py
    context_profile.py ← move from commands/context_profile.py
    retro.py         ← move from commands/retro.py
  govern/
    __init__.py
    classify.py      ← move from commands/classify.py
    compliance.py    ← move from commands/compliance.py
    policy.py        ← move from commands/policy.py
    escalations.py   ← move from commands/escalations.py
    validate.py      ← move from commands/validate.py
    spec_check.py    ← move from commands/spec_check.py
    detect.py        ← move from commands/detect.py
  improve/
    __init__.py
    scores.py        ← move from commands/scores.py
    evolve.py        ← move from commands/evolve.py
    patterns.py      ← move from commands/patterns.py
    budget.py        ← move from commands/budget.py
    changelog.py     ← move from commands/changelog.py
  distribute/
    __init__.py
    package.py       ← move from commands/package.py
    publish.py       ← move from commands/publish.py
    pull.py          ← move from commands/pull.py
    verify_package.py ← move from commands/verify_package.py
    install.py       ← move from commands/install.py
    transfer.py      ← move from commands/transfer.py
  agents/
    __init__.py
    agents.py        ← move from commands/agents.py
    route.py         ← move from commands/route.py
    events.py        ← move from commands/events.py
    incident.py      ← move from commands/incident.py
```

Each `__init__.py` in sub-packages is empty (or has only a docstring).

**Files to modify**:

`agent_baton/cli/main.py` — Replace `discover_commands()` with a recursive
implementation:

```python
def discover_commands() -> dict[str, types.ModuleType]:
    """Recursively discover command modules in cli/commands/ and sub-packages.

    Each module must expose register(subparsers) and handler(args).
    Recurses one level deep into sub-packages.
    """
    found: dict[str, types.ModuleType] = {}

    def _scan(pkg: types.ModuleType) -> None:
        for info in pkgutil.iter_modules(pkg.__path__):
            full_name = f"{pkg.__name__}.{info.name}"
            if info.ispkg:
                # Recurse into sub-package (one level only)
                sub_pkg = importlib.import_module(full_name)
                _scan(sub_pkg)
            else:
                mod = importlib.import_module(full_name)
                if hasattr(mod, "register") and hasattr(mod, "handler"):
                    found[info.name] = mod

    _scan(commands_pkg)
    return found
```

**Validation**:

```bash
# All 35 commands still reachable
python -m agent_baton.cli.main --help 2>&1 | grep -c "^\s"
# ^ count should be >= 35 (one line per command in help output)
baton --help   # if installed
pytest tests/ -x -q
```

**Dependencies**: P2 (canonical imports must be in place in the command files
before moving them, otherwise moved files carry broken shim imports into new
locations), P7 (experimental module moves should precede CLI reorganisation).

---

## P9: Normalize Enum Usage in ExecutionAction and StepResult

**Goal**: `ExecutionAction.action_type` is typed `str` but compared against
`ActionType.DISPATCH.value`, `ActionType.GATE.value`, etc. throughout the
codebase. Change the field type to `ActionType` and update all comparison
sites. Leave `StepResult.status` as `str` (it takes "complete"/"failed"/
"dispatched" — not an enum member, and `ExecutionState.completed_step_ids`
filters by string value).

**Files to modify**:

`agent_baton/models/execution.py`:

1. Change `ExecutionAction.action_type: str` (line 381) to
   `action_type: ActionType`.

2. Update `ExecutionAction.__init__` default: change the field declaration. It
   has no explicit default in the dataclass; callers always pass it. No default
   change needed.

3. In `ExecutionAction.to_dict()` (line 402), change:
   ```python
   d = {"action_type": self.action_type, ...}
   if self.action_type == ActionType.DISPATCH.value:
   ```
   to:
   ```python
   d = {"action_type": self.action_type.value, ...}
   if self.action_type == ActionType.DISPATCH:
   ```
   Apply same `.value` serialization and enum comparison to all `if` branches
   (lines 404, 412, 418).

4. There is no `from_dict` on `ExecutionAction`; it is only constructed in
   code, never deserialized. No deserialization change needed.

**Files to modify — comparison sites**:

`agent_baton/core/engine/executor.py` — All comparisons of the form
`action.action_type == ActionType.X.value` (there are none in this file;
the engine constructs actions with `action_type=ActionType.DISPATCH.value`).
Change construction sites to pass the enum directly:

In `_dispatch_action` (line 803):
```python
# Change:
action_type=ActionType.DISPATCH.value,
# To:
action_type=ActionType.DISPATCH,
```

In `_determine_action`, all `ExecutionAction(action_type=ActionType.X.value, ...)`:
change each `.value` to drop `.value`. There are approximately 10 construction
sites in the method.

In `next_action`, `resume`, `recover_dispatched_steps`: same pattern — search for
`ActionType.FAILED.value` and `ActionType.COMPLETE.value` in constructor calls.

`agent_baton/core/runtime/worker.py` — All comparison sites:
```python
# Change pattern:
if action.action_type == ActionType.COMPLETE.value:
# To:
if action.action_type == ActionType.COMPLETE:
```
Apply to all five `ActionType` comparisons in `_execution_loop` (lines 98, 102,
105, 111, 115).

`agent_baton/cli/commands/execute.py` — `_print_action` uses `atype =
action.get("action_type", "")` from a `to_dict()` result — this path already
uses string values from `to_dict()`. No change needed here since `to_dict()`
will still serialize to `.value`.

**Validation**:

```bash
# No remaining .value comparisons on ActionType in construction or comparison
grep -n "ActionType\.[A-Z]*\.value" \
  agent_baton/core/engine/executor.py \
  agent_baton/core/runtime/worker.py
# ^ should be empty after changes

pytest tests/test_executor.py tests/test_runtime.py tests/test_models.py \
  tests/test_engine_events.py -x -q
```

**Dependencies**: P4 (event dedup eliminates some ActionType usage in executor,
reducing the diff surface before enum normalization).

---

## P10: EventBus Wiring Safety — ExecutionContext

**Goal**: Replace the ad-hoc wiring of `ExecutionEngine + EventBus +
EventPersistence` in `WorkerSupervisor.start()` with a factory dataclass
`ExecutionContext` that ensures consistent wiring and makes the dependency
graph explicit.

**Files to create**:

`agent_baton/core/runtime/context.py`

```python
"""ExecutionContext — factory for correctly wired engine/bus/persistence."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.events.bus import EventBus
from agent_baton.core.events.persistence import EventPersistence
from agent_baton.core.runtime.launcher import AgentLauncher


@dataclass
class ExecutionContext:
    """A correctly wired execution context: engine, bus, launcher, persistence.

    Always build via ExecutionContext.build() to guarantee consistent wiring.
    Direct instantiation is permitted for testing with pre-built components.
    """

    engine: ExecutionEngine
    bus: EventBus
    launcher: AgentLauncher
    persistence: EventPersistence | None = None

    @classmethod
    def build(
        cls,
        *,
        launcher: AgentLauncher,
        team_context_root: Path | None = None,
        bus: EventBus | None = None,
        persist_events: bool = True,
    ) -> ExecutionContext:
        """Create a fully wired ExecutionContext.

        Args:
            launcher: Agent launcher (ClaudeCodeLauncher, DryRunLauncher, etc.)
            team_context_root: Base directory for state and event files.
                               Defaults to Path(".claude/team-context").
            bus: Existing EventBus to reuse.  A new bus is created if None.
            persist_events: When True, wire EventPersistence as a bus subscriber
                            so all events are appended to disk.

        Returns:
            ExecutionContext with engine subscribed to bus, persistence wired
            as a subscriber if persist_events=True.
        """
        root = team_context_root or Path(".claude/team-context")
        bus = bus or EventBus()

        # Engine subscribes to the same bus it publishes on.
        engine = ExecutionEngine(team_context_root=root, bus=bus)

        persistence: EventPersistence | None = None
        if persist_events:
            persistence = EventPersistence(events_dir=root / "events")
            bus.subscribe("*", persistence.append)

        return cls(
            engine=engine,
            bus=bus,
            launcher=launcher,
            persistence=persistence,
        )
```

**Files to modify**:

`agent_baton/core/runtime/supervisor.py` — In `start()`, replace manual
wiring (lines 90–105 of current source) with `ExecutionContext.build()`:

```python
# Add import at top of file:
from agent_baton.core.runtime.context import ExecutionContext

# In start() method, replace:
bus = bus or EventBus()
engine = ExecutionEngine(team_context_root=self._root, bus=bus)
# ...
worker = TaskWorker(engine=engine, launcher=launcher, bus=bus, ...)

# With:
ctx = ExecutionContext.build(
    launcher=launcher,
    team_context_root=self._root,
    bus=bus,
    persist_events=True,
)
if resume:
    logger.info("Daemon resuming: task=%s", plan.task_id)
    ctx.engine.resume()
else:
    logger.info("Daemon starting: task=%s", plan.task_id)
    ctx.engine.start(plan)
worker = TaskWorker(
    engine=ctx.engine,
    launcher=ctx.launcher,
    bus=ctx.bus,
    max_parallel=max_parallel,
)
```

Remove the now-unused `from agent_baton.core.events.bus import EventBus` import
if it is no longer referenced directly in `supervisor.py` — verify before removing.

Also update `_write_status(self, engine: ExecutionEngine, ...)`: the parameter
type remains `ExecutionEngine` (not `ExecutionDriver`) for now since
`_write_status` calls `engine.status()` — if P3 is complete, change to
`ExecutionDriver`.

`agent_baton/core/runtime/__init__.py` — Add:
```python
from agent_baton.core.runtime.context import ExecutionContext
```
Add `"ExecutionContext"` to `__all__`.

**Validation**:

```bash
python -c "from agent_baton.core.runtime.context import ExecutionContext; print('OK')"
python -c "from agent_baton.core.runtime import ExecutionContext; print('OK')"
python -c "
from agent_baton.core.runtime import ExecutionContext
from agent_baton.core.runtime.launcher import DryRunLauncher
ctx = ExecutionContext.build(launcher=DryRunLauncher())
assert ctx.engine is not None
assert ctx.bus is not None
assert ctx.persistence is not None
print('build OK')
"
pytest tests/test_runtime.py tests/test_daemon.py -x -q
```

**Dependencies**: P3 (protocol definition should exist before factory is
written so `ExecutionContext` can optionally type `engine` as `ExecutionDriver`
in a follow-up), P5 (persistence extraction should precede this wiring
factory to avoid duplicating persistence logic).

---

## Execution Order Summary

| Proposal | Can start after | Parallelism |
|----------|-----------------|-------------|
| P1 | — | Immediate |
| P3 | — | Immediate (parallel with P1) |
| P4 | — | Immediate (parallel with P1, P3) |
| P5 | — | Immediate (parallel with P1, P3, P4) |
| P2 | P1 | After P1 |
| P6 | P2 | After P2 |
| P7 | P2 | After P2 (parallel with P6) |
| P9 | P4 | After P4 |
| P8 | P2, P7 | After P2 and P7 |
| P10 | P3, P5 | After P3 and P5 |

**Minimum critical path**: P1 → P2 → P8

**Recommended wave order**:
- Wave 1 (parallel): P1, P3, P4, P5
- Wave 2 (parallel): P2, P9, P10
- Wave 3 (parallel): P6, P7
- Wave 4: P8
