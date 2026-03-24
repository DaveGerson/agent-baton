# Re-Architecture Orchestration Plan
## Human-Readable Delivery Document (HDD)

**Document type**: Human-readable orchestration plan for technical leads and
project managers
**Status**: Ready for execution
**Last updated**: 2026-03-23
**Total proposals**: 10
**Estimated phases**: 4

---

## 1. Overview

This document describes the sequencing strategy for executing 10 structural
re-architecture proposals against the `agent_baton` Python package. It is
written for a human reader — a tech lead or PM tracking this work — not for
an agent.

### What we are doing

The codebase was built in two epics. Epic 1 produced a flat module layout
(`agent_baton/core/*.py`, one class per file) plus a `models/` layer. Epic 2
reorganised those modules into sub-packages (`core/engine/`, `core/observe/`,
`core/events/`, `core/runtime/`, etc.) but kept backward-compatible shims at
the old paths so no import would break. The result is a working but cluttered
structure:

- Two parallel plan hierarchies (`models/plan.py` and `models/execution.py`)
  that overlap in purpose
- ~20 shim files in `core/` that silently re-export from canonical sub-packages
- CLI commands still importing from the old `core/*.py` shim paths (~23
  command files, ~50 import statements)
- `core/engine/executor.py` is a 400+ line file doing state-machine work,
  dispatching, event-publishing, and persistence
- `core/events/` produces events from two sources (`executor.py` fires its
  own events internally; `worker.py` fires overlapping events)
- A `Worker → Engine` relationship that is undocumented as a protocol
- Experimental modules (`core/improve/evolution.py`,
  `core/distribute/async_dispatch.py`) that look equivalent to production
  modules
- `models/enums.py` defines enums whose string values are used inconsistently
  against the raw strings inside `models/execution.py`

None of these problems causes test failures today. The 1,977-test suite
passes. The issues are **architectural debt** that will slow future work,
produce merge conflicts between parallel contributors, and make onboarding
harder.

### Why sequencing matters

The 10 proposals are not independent. Several proposals remove the same
indirection layer, and doing them in the wrong order creates churn:

- P2 (remove shims) must follow P1 (remove PlanBuilder dependency inside the
  plan shim), otherwise the shim cleanup breaks the only remaining caller of
  the old PlanBuilder import
- P6 and P7 (restructure layers, gate experimental modules) need canonical
  imports from P2 before they can reason about which imports belong where
- P8 (reorganise CLI commands into groups) depends on P2 *and* P7 — moving
  command files is safe only after imports are canonical and experimental
  module boundaries are enforced
- P10 (EventBus wiring safety) needs P3 (typed Worker-Engine protocol) and
  P5 (split executor) so the event emission points are separated before we
  reason about which is the authoritative source

Doing these out of order would either force a second-pass cleanup or produce
a phase that mixes unrelated risk profiles.

---

## 2. Dependency Graph

The ten proposals and their declared dependencies:

```
P1  Remove dual plan model hierarchy
        │
        ▼
P2  Remove backward-compat shim layer (depends on P1)
       ├──────────────┐
       ▼              ▼
P6  Core vs         P7  Gate experimental modules
    peripheral          │
    layering            │
       │                │
       └────────┬───────┘
                ▼
P8  Group CLI commands (depends on P2 + P7)

P3  Formalize Worker-Engine protocol (independent)
        │
        ▼
P10 EventBus wiring safety (depends on P3 + P5)
        ▲
P5  Split executor.py (independent)

P4  De-duplicate event publishing (independent)
        │
        ▼
P9  Normalize enum usage (depends on P4)
```

**Critical path** (longest chain that blocks other work):

```
P1 → P2 → P7 → P8
```

**Independent tracks** that can run in parallel with the critical path:

| Track | Proposals | Blocks |
|-------|-----------|--------|
| A | P1 → P2 → P6, P7 → P8 | everything else |
| B | P3 → P10 | nothing |
| C | P4 → P9 | nothing |
| D | P5 (feeds P10) | P10 |

---

## 3. Phases

### Phase 1 — Foundation

**Goal**: Remove the oldest debt and establish clean interfaces for
everything that follows. This phase touches four independent proposals that
have no dependency on any other proposal.

**Proposals included**:

| ID | Proposal | Affected files |
|----|----------|---------------|
| P1 | Remove dual plan model hierarchy | `models/plan.py`, `core/orchestration/plan.py`, any caller of `ExecutionPlan`/`PlanBuilder` that does not go through `MachinePlan` |
| P3 | Formalize Worker-Engine protocol | `core/runtime/worker.py`, `core/engine/executor.py` |
| P4 | De-duplicate event publishing | `core/engine/executor.py`, `core/runtime/worker.py`, `core/events/events.py` |
| P5 | Split executor.py | `core/engine/executor.py` → up to 3 focused modules |

**Why this sequence**

P1 is the prerequisite for P2 (the largest change in the project). Starting
Phase 1 unlocks Phase 2. The remaining three proposals (P3, P4, P5) are
independent but grouped here because they are purely additive or internal
— they do not touch import paths that other proposals must stabilise first.
Doing them now means Phase 2 inherits cleaner executor and event code, which
reduces the scope of the import migration.

Context on each:

- P1: `models/plan.py` defines `ExecutionPlan` and `Phase` (human-readable).
  `models/execution.py` defines `MachinePlan` and `PlanPhase`
  (machine-readable, JSON-serialisable). These serve different purposes but
  the overlap in naming (`Phase` vs `PlanPhase`, `ExecutionPlan` vs
  `MachinePlan`) causes confusion. The `core/plan.py` shim re-exports
  `PlanBuilder` from `core/orchestration/plan.py`; removing this shim
  dependency is only safe after `PlanBuilder` callers have migrated to
  `MachinePlan`. P1 does that migration first.

- P3: `TaskWorker` (`core/runtime/worker.py`) wraps `ExecutionEngine` and
  calls `engine.next_action()`, `engine.mark_dispatched()`,
  `engine.record_step_result()`, etc. This is an informal protocol — there
  are no type annotations enforcing it, and the set of methods the worker
  relies on is not declared anywhere. P3 adds a `Protocol` class or typed
  interface so that `ExecutionEngine` can be replaced or mocked safely.

- P4: Both `ExecutionEngine` and `TaskWorker` publish overlapping events.
  The engine publishes `step.dispatched`, `step.completed`, `step.failed`,
  `gate.*`, and `task.*` events. The worker publishes `step.dispatched`,
  `step.completed`, and `step.failed` again from the same execution path.
  Any subscriber currently receives duplicates for every step. P4 designates
  a single authority per event type and removes the duplicate emit.

- P5: `core/engine/executor.py` mixes state-machine logic (advance phase,
  advance step, detect gate), persistence (read/write JSON), event publishing,
  and retrospective recording into one ~400-line class. P5 extracts these
  concerns into focused modules so each one can be tested and read in
  isolation.

**Entry criteria**

- All 1,977 tests pass on `master`
- Feature branch `rearch/phase-1` created from `master`

**Exit criteria (gate)**

- All tests pass
- No production code imports `ExecutionPlan` or `PlanBuilder` from the old
  path `agent_baton.models.plan`
- A typed `ExecutionDriver` protocol exists and `TaskWorker`'s
  dependency is declared against it
- `executor.py` (or its successor modules) no longer contains duplicate
  `EventBus.publish()` calls for events also published by `worker.py`
- Gate command: `pytest tests/ -x -q --tb=short` exits 0

**Risk assessment**: LOW

All four proposals are internal refactors. They do not change any public
API, CLI interface, or file-backed data format. Each can be implemented and
tested in isolation before the others in the same phase. The test suite
provides a complete safety net.

**Rollback strategy**

Revert the `rearch/phase-1` branch entirely. No downstream
phase will have started yet. No user-visible behaviour changes.

---

### Phase 2 — Shim Removal

**Goal**: Eliminate the backward-compatibility shim layer entirely. This is
a pure mechanical migration with no design decisions — only import path
rewrites.

**Proposals included**:

| ID | Proposal | Scale |
|----|----------|-------|
| P2 | Remove backward-compat shim layer | 21 shim files deleted, ~50 import sites updated |

**Why this phase is separate**

P2 is deliberately isolated into its own phase for two reasons.

First, it is the highest-volume mechanical change in the project. The 20
shim files at `agent_baton/core/*.py` each contain one to four lines that
re-export from the canonical sub-package. Every one of the 23 CLI command
modules imports from these shim paths. Doing this migration alongside
other changes would make diffs hard to review and increases the chance of
a missed import.

Second, P2 is the dependency blocker for P6, P7, and P8. Those proposals
need to reason about "which imports are canonical" — that reasoning only
makes sense after P2 is complete and every import in the codebase points
at the real module.

**Context on the shim layer**

The shims were introduced at the end of Epic 1 so that CLI commands and
tests written against the flat `core/` layout would keep working when Epic 2
reorganised everything into sub-packages. Here is a representative sample:

```python
# agent_baton/core/plan.py (shim)
"""Backward-compatible shim — canonical location: core/orchestration/plan.py"""
from agent_baton.core.orchestration.plan import PlanBuilder, RISK_SIGNALS
__all__ = ["PlanBuilder", "RISK_SIGNALS"]
```

The ~23 CLI commands import via the old path, for example:

```python
# agent_baton/cli/commands/route.py
from agent_baton.core.registry import AgentRegistry
from agent_baton.core.router import AgentRouter
```

The canonical paths are:
- `core.registry` → `core.orchestration.registry`
- `core.router` → `core.orchestration.router`
- `core.plan` → `core.orchestration.plan`
- `core.context` → `core.orchestration.context`
- `core.usage` → `core.observe.usage`
- `core.telemetry` → `core.observe.telemetry`
- `core.dashboard` → `core.observe.dashboard`
- `core.retrospective` → `core.observe.retrospective`
- `core.scoring` → `core.improve.scoring`
- `core.vcs` → `core.improve.vcs`
- `core.evolution` → `core.improve.evolution`
- `core.transfer` → `core.distribute.transfer`
- `core.sharing` → `core.distribute.packager` (or `sharing`)
- `core.incident` → `core.distribute.incident`
- `core.async_dispatch` → `core.distribute.async_dispatch`
- `core.classifier` → `core.govern.classifier`
- `core.compliance` → `core.govern.compliance`
- `core.policy` → `core.govern.policy`
- `core.escalation` → `core.govern.escalation`
- `core.validator` → `core.govern.validator`
- `core.spec_validator` → `core.govern.spec_validator`

**Entry criteria**

- Phase 1 gate passed (`rearch/phase-1` merged to `master`)
- Feature branch `rearch/phase-2` created from `master`

**Exit criteria (gate)**

- No files remain at `agent_baton/core/<single-word>.py` that contain only
  re-export boilerplate
- `grep -r "from agent_baton.core\.[a-z_]+ import" agent_baton/cli/` returns
  only imports from canonical sub-package paths
- All tests pass
- Gate command: `pytest tests/ -x -q --tb=short && python -c "import agent_baton; print('OK')"` exits 0

**Risk assessment**: LOW

Despite the volume, each import change is mechanical and independently
verifiable. Python will throw an `ImportError` at test-collection time for
any missed migration. The 1,977-test suite exercises every CLI command; any
broken import will surface immediately. This phase carries no design risk.

**Rollback strategy**

Revert the `rearch/phase-2` branch. The shim files are still
in place on `master` (pre-merge), so reverting restores the working state
completely.

---

### Phase 3 — Structure

**Goal**: Reorganise the package for long-term clarity. Now that imports are
canonical and interfaces are typed, we can safely express "what belongs where"
without ambiguity.

**Proposals included**:

| ID | Proposal | Affected area |
|----|----------|--------------|
| P6 | Explicit core vs peripheral layering | Package-level `__init__.py`, `core/__init__.py`, module `__all__` exports |
| P7 | Gate experimental modules | `core/improve/evolution.py`, `core/distribute/async_dispatch.py`, `core/learn/` sub-package |
| P9 | Normalize enum usage | `models/enums.py`, `models/execution.py`, all enum comparisons across tests and production code |
| P10 | EventBus wiring safety | `core/engine/executor.py`, `core/runtime/worker.py`, `core/events/bus.py` |

**Why these belong together**

All four proposals are "polish after cleanup." They each require the shim
layer to be gone (so import paths are canonical) and they each carry MEDIUM
or below risk. They can run in parallel within the phase.

P6 and P7 can run concurrently: one team touches `__init__.py` exports and
layering documentation, the other adds experimental module markers. P9 is
completely orthogonal — it touches string literals and enum comparisons, not
module structure. P10 depends on P3 and P5 (both completed in Phase 1).

**Context on each proposal**

- P6: `core/__init__.py` currently re-exports 50+ names from all sub-packages
  without any documented layering. "Core" means something specific in a
  well-structured package: it is the minimal set of modules with no circular
  dependencies, depended on by everything. "Peripheral" modules (observe,
  improve, distribute, learn) are optional enhancements. P6 separates these
  in documentation and `__all__` structure so the distinction is
  machine-checkable.

- P7: Two modules are explicitly marked as experimental in their docstrings:
  `core/improve/evolution.py` ("Status: Experimental") and
  `core/distribute/async_dispatch.py` (a file-level task dispatch system that
  coexists with the `core/runtime/` async dispatch). The `core/learn/`
  sub-package is similarly nascent. P7 gates these behind an explicit API
  surface (e.g., `from agent_baton.experimental import ...`) or marks them
  with deprecation warnings so callers know what they are getting.

- P9: `models/enums.py` defines `RiskLevel`, `TrustLevel`, `BudgetTier`,
  `ExecutionMode`, `GateOutcome`, `FailureClass`, `GitStrategy`, and
  `AgentCategory` as proper Python enums. `models/execution.py` defines
  `StepStatus`, `PhaseStatus`, and `ActionType` as separate enums with raw
  string values stored in dataclass fields. This means code that compares
  risk levels does `if risk_level == "HIGH"` in some places and
  `if risk_level == RiskLevel.HIGH` in others. P9 normalises this by either
  migrating `execution.py` fields to use the typed enums from `models/enums.py`
  or vice versa.

- P10: After P3 (typed protocol) and P5 (split executor) are in place, the
  execution path has two potential event emission points. P10 establishes
  which component owns which event topic. The guard is: the
  `ExecutionEngine` owns `task.*`, `phase.*`, `gate.*` events. The
  `TaskWorker` owns `step.*` events. EventBus subscriptions are documented
  at their registration sites. The mechanism is a new `ExecutionContext`
  factory (`core/runtime/context.py`) that replaces the ad-hoc wiring in
  `WorkerSupervisor.start()` with a `build()` classmethod that ensures
  correct bus + engine + persistence wiring. The `status()` method's bare
  engine construction is unchanged (it is read-only).

**Entry criteria**

- Phase 2 gate passed (`rearch/phase-2` merged to `master`)
- Feature branch `rearch/phase-3` created from `master`
- For P10 specifically: Phase 1 gate passed (P3 and P5 both completed)

**Exit criteria (gate)**

- Package has documented core vs peripheral separation in `core/__init__.py`
  (docstring or comment block is acceptable; enforced exports preferred)
- Experimental modules are gated with warnings or moved to an `experimental/`
  namespace
- `grep -rn '== "HIGH"\|== "LOW"\|== "MEDIUM"\|== "CRITICAL"' agent_baton/`
  returns zero results in production code (test fixtures may use string
  literals for brevity, but comparison code must not)
- Event emission in `executor.py` and `worker.py` is non-overlapping with
  comments at each `bus.publish()` call stating which component owns that topic
- All tests pass
- Gate command: `pytest tests/ -x -q --tb=short` exits 0

**Risk assessment**: MEDIUM

P9 is the highest-risk proposal in the project. Enum normalization touches
comparison logic across the entire codebase. A single missed comparison that
was using a raw string will silently return the wrong boolean at runtime
without necessarily failing any test that covers the happy path. The risk
is managed by running `grep` sweeps as part of the exit gate and by
auditing test coverage for enum-comparison branches before starting.

P7 carries low-medium risk: marking modules as experimental and adding import
warnings could surprise callers if any test imports them directly without
expecting a warning.

P6 and P10 are LOW risk.

**Rollback strategy**

Revert the `rearch/phase-3` branch. Because this phase adds no
new behaviour (only restructures and normalises), reverting restores full
functionality. If P9 specifically causes a regression, it can be reverted in
isolation before retrying.

---

### Phase 4 — Polish

**Goal**: Complete the CLI reorganisation. This is the last and most
visible file-move operation.

**Proposals included**:

| ID | Proposal | Affected files |
|----|----------|---------------|
| P8 | Group CLI commands by domain | `agent_baton/cli/commands/` — 35 command files grouped into sub-namespaces |

**Why this comes last**

P8 is deliberately the final phase for three reasons.

First, it requires canonical imports (P2) and gated experimental modules
(P7) to be in place. Moving a command file to a new sub-package when it
still imports from a shim path would create two layers of indirection
simultaneously.

Second, CLI reorganisation is the change most visible to external users.
Doing it last means the existing `baton --help` surface is stable throughout
the entire re-architecture. No user workflow breaks until Phase 4, and by
then the underlying structure is already clean.

Third, `cli/main.py` uses auto-discovery (`pkgutil.iter_modules`) to find
command modules. Moving command files into sub-namespaces requires updating
the discovery logic. Doing this at the end keeps the discovery change
isolated and testable by itself.

**Proposed groupings** (for reference — exact groupings are P8's design
decision):

| Group | Commands |
|-------|----------|
| `agents` | agents, validate, route, detect, spec-check |
| `observability` | usage, dashboard, telemetry, retro, trace, scores |
| `governance` | classify, compliance, policy, escalations |
| `distribute` | package, transfer, pull, publish, verify-package |
| `improve` | evolve, changelog, patterns, context-profile, budget |
| `execution` | plan, execute, status, async, install |
| `runtime` | (daemon commands if added) |

**Entry criteria**

- Phase 3 gate passed (`rearch/phase-3` merged to `master`)
- Feature branch `rearch/phase-4` created from `master`

**Exit criteria (gate)**

- `baton --help` lists all previously available commands (none dropped, none
  renamed without deprecation alias)
- `baton <any-existing-command> --help` works
- Auto-discovery in `cli/main.py` correctly finds commands in sub-namespaces
- All tests pass (including any integration tests that invoke the `baton` CLI)
- Gate command: `pytest tests/ -x -q --tb=short && python -m agent_baton.cli.main --help` exits 0

**Risk assessment**: LOW

This is a pure file-move. Python import mechanics will catch any missed
import update at collection time. The auto-discovery pattern in `main.py`
means there is no hand-maintained command registry to keep in sync.

**Rollback strategy**

Revert the `rearch/phase-4` branch. The CLI returns to the flat
command layout, which fully functional throughout the entire re-architecture.

---

## 4. Parallel Execution Opportunities

Within each phase, some proposals can be worked concurrently by different
contributors. This table shows what can be parallelised.

### Phase 1 (within the phase)

| Parallel group | Proposals | Notes |
|----------------|-----------|-------|
| Group 1 | P1 alone | Must finish before any P2 dependency can close |
| Group 2 | P3, P4, P5 in parallel | Fully independent of each other; each touches different files |

P3 (`core/runtime/worker.py`), P4 (`events/events.py` + `executor.py` emit
sites), and P5 (`executor.py` split) all touch `executor.py`. Recommend
assigning P4 and P5 to the same contributor or sequencing them within the
group to avoid merge conflicts on that one file.

### Phase 3 (within the phase)

| Parallel group | Proposals | Notes |
|----------------|-----------|-------|
| Group A | P6, P7 in parallel | P6 touches `__init__.py`s; P7 touches experimental module files; minimal overlap |
| Group B | P9 in parallel with Group A | Completely orthogonal — pure enum/string change |
| Group C | P10 after P3 and P5 confirmed | Requires Phase 1 outputs, but otherwise independent of P6/P7/P9 |

### Phases 2 and 4

Phase 2 (shim removal) is single-threaded by design: the migration is simple
enough that parallelism introduces more coordination cost than it saves.
Phase 4 (CLI grouping) is one proposal and can be executed as a single unit.

---

## 5. Testing Strategy

### Principle: the gate is always the same

Every phase uses the same primary gate command: `pytest --tb=short -q`. The
1,977-test suite is comprehensive enough that any broken import, any broken
model reference, and any broken CLI invocation will surface. We do not add
phase-specific test infrastructure.

### Phase-specific supplemental checks

These are not automated gates — they are manual verification steps performed
by the engineer before declaring a phase complete.

**Phase 1 — supplemental**

- `python -c "from agent_baton.models.plan import ExecutionPlan; print('FAIL: old model still importable')"` — should raise `ImportError` after P1
- Review that `TaskWorker.__init__` type-hints its `engine` parameter against the new protocol type (P3)
- Scan `executor.py` and `worker.py` for duplicate `bus.publish()` calls on the same topic (P4)
- Confirm `executor.py` line count is significantly reduced and new module files exist (P5)

**Phase 2 — supplemental**

```bash
# No shim files remain
find agent_baton/core -maxdepth 1 -name "*.py" \
  ! -name "__init__.py" \
  ! -name "*.pyi" \
  -exec grep -l "Backward-compatible shim" {} \;
# Should print nothing.

# No CLI command imports from old shim paths
grep -r "from agent_baton\.core\.[a-z_]\+ import" agent_baton/cli/
# Should print nothing (all imports use sub-package paths).
```

**Phase 3 — supplemental**

```bash
# No raw enum string comparisons in production code
grep -rn '== "HIGH"\|== "LOW"\|== "MEDIUM"\|== "CRITICAL"\|== "complete"\|== "failed"\|== "pending"' \
  agent_baton/ --include="*.py" \
  | grep -v "test_" | grep -v "#"
# Should print nothing (or only comments).
```

**Phase 4 — supplemental**

```bash
# All commands still reachable
baton --help
baton agents --help
baton execute --help
# ...one invocation per group to confirm routing
```

### What we do not test

We do not add new tests for the re-architecture itself. The proposals are
structural, not behavioural. Adding tests that specifically target "shims are
gone" or "imports are canonical" would be testing the directory layout, not
the behaviour — and that is not a sustainable practice.

---

## 6. Communication Plan

### What to log, where, and when

| Event | Where | Format |
|-------|-------|--------|
| Phase branch created | Git commit message | `rearch/phase-N: create branch` |
| Proposal completed within phase | Git commit | `rearch(P#): <description>` (one commit per proposal) |
| Phase gate run | `.claude/team-context/mission-log.md` | Gate type, exit code, timestamp |
| Phase gate passed | Git commit | `rearch/phase-N: gate passed, merge to master` |
| Phase gate failed | GitHub issue or Slack | Gate output, failed check, proposed fix |
| Rollback decision | Git commit message + issue | `revert: rearch/phase-N <reason>` |

### Commit message convention

```
rearch(P1): remove ExecutionPlan from models/plan.py

The MachinePlan in models/execution.py is the canonical execution
plan. ExecutionPlan was the human-document model from Epic 1 and is
no longer used in production paths.

Affected: models/plan.py, core/orchestration/plan.py
Tests: all passing
```

### Phase completion checklist

Before merging a phase branch:

1. Gate command (`pytest --tb=short -q`) exits 0
2. Supplemental checks for the phase completed and noted in the PR
3. Any design decisions made during the phase documented in the PR
   description under a "Decisions" heading
4. No force-pushes to `master`; merge commit preserves the branch history

### Stakeholder summary (for non-engineering readers)

| Phase | Plain-English goal | Duration estimate | User-visible change |
|-------|--------------------|------------------|---------------------|
| 1 | Clean up the engine internals | 2-3 days | None |
| 2 | Remove 20 redirect files | 1-2 days | None |
| 3 | Organise the package properly | 2-3 days | None |
| 4 | Tidy the CLI command layout | 1 day | `baton --help` groups may reorganise |

All user-facing behaviour (`baton` commands, agent definitions, installed
CLAUDE.md templates) remains unchanged until Phase 4. Phase 4's CLI
reorganisation is backward-compatible as long as command names are preserved.

---

## Appendix: Proposal Reference Table

| ID | Name | Phase | Depends on | Risk | Primary files |
|----|------|-------|------------|------|---------------|
| P1 | Remove dual plan model hierarchy | 1 | — | LOW | `models/plan.py`, `models/execution.py` |
| P2 | Remove backward-compat shim layer | 2 | P1 | LOW | `core/*.py` (21 shim files), `cli/commands/*.py` (21 CLI files using shims) |
| P3 | Formalize Worker-Engine protocol | 1 | — | LOW | `core/engine/protocols.py` (new), `core/runtime/worker.py`, `core/runtime/supervisor.py` |
| P4 | De-duplicate event publishing | 1 | — | LOW | `core/engine/executor.py`, `core/events/events.py` |
| P5 | Split executor.py | 1 | — | LOW | `core/engine/executor.py`, `core/engine/persistence.py` (new) |
| P6 | Core vs peripheral layering | 3 | P2 | LOW | `core/__init__.py`, `agent_baton/__init__.py` |
| P7 | Gate experimental modules | 3 | P2 | LOW-MEDIUM | `core/distribute/async_dispatch.py`, `core/distribute/incident.py`, `core/distribute/transfer.py` |
| P8 | Group CLI commands | 4 | P2, P7 | LOW | `cli/commands/*.py`, `cli/main.py` |
| P9 | Normalize enum usage | 3 | P4 | MEDIUM | `models/execution.py`, `core/engine/executor.py`, `core/runtime/worker.py` |
| P10 | EventBus wiring safety | 3 | P3, P5 | LOW | `core/runtime/context.py` (new), `core/runtime/supervisor.py` |
