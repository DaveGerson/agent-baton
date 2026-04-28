# 005b Phase 3 Design — PhaseManager and the State Pattern

**Step:** 3.1 (architect)
**Branch:** `feat/005b-engine-decomposition`
**Target file:** `agent_baton/core/engine/executor.py` (~6,938 lines, post-Phase-2)
**Source proposal:** `proposals/005b-implementation-plan.md` §4
**Skeletons:**
- `agent_baton/core/engine/phase_manager.py` (45-line stub from `3c3c548`)
- `agent_baton/core/engine/states.py` (73-line stub from `3c3c548`)

---

## 0. Summary of Phase 3's reach

Phase 2 already cut the engine's `_determine_action()` god-method into a pure `ActionResolver` plus an engine-side dispatch table called `_apply_resolver_decision()`. The dispatch table is now the locus of phase-progression decisions. Phase 3 takes two further slices off it:

1. **PhaseManager** absorbs the *pure* phase-boundary inspection helpers (is-this-phase-complete, is-the-approval-gate-resolved, is-the-feedback-gate-resolved). It holds **one** crisp mutator — `advance_phase()` — that bumps `state.current_phase` / `state.current_step_index`. All heavy I/O (event publication, bead synthesis, VETO, audit row writes) **stays on the engine** because the engine alone owns the audit trail.

2. **State Pattern** (PlanningState / ExecutingPhaseState / AwaitingApprovalState / TerminalState) replaces the implicit `state.status`-string switching that today is spread across the dispatch table arms. After Phase 3 the dispatch table still receives the `DecisionKind`, but mutation epilogues (status flips, step-result updates) are looked up via a `state.status → state-class` map and run through a uniform `handle()` method.

Net target: ~50% LOC reduction in `_apply_resolver_decision`; `DecisionKind`-keyed dispatch preserved; heavy builders remain on the engine.

---

## 1. PhaseManager scope

### 1.1 Inventory of phase-progression logic in `executor.py` (post-Phase-2)

| DecisionKind arm | Lines (executor.py) | What it does |
|---|---|---|
| `EMPTY_PHASE_ADVANCE` | 5065-5090 | publish `phase_completed`, call `_synthesize_beads_post_phase`, bump `state.current_phase`/`current_step_index`, publish `phase_pre_start` + `phase_started`, return None to loop. |
| `PHASE_ADVANCE_OK` | 5253-5282 | call `_enforce_veto_before_advance`, publish `phase_completed`, call `_synthesize_beads_post_phase`, bump phase, set `status="running"`, publish events, return None. |
| `EMPTY_PHASE_GATE` | 5049-5062 | flip `state.status = "gate_pending"`, build GATE action. |
| `PHASE_NEEDS_GATE` | 5237-5250 | flip `state.status = "gate_pending"`, build GATE action. |
| `PHASE_NEEDS_APPROVAL` | 5223-5227 | flip `state.status = "approval_pending"`, call heavy builder. |
| `PHASE_NEEDS_FEEDBACK` | 5230-5234 | flip `state.status = "feedback_pending"`, call heavy builder. |

Pure helpers used by the *resolver* (already in `_executor_helpers.py`): `gate_passed_for_phase`, `approval_passed_for_phase`, `feedback_resolved_for_phase`. Read-only.

### 1.2 Method-by-method classification

| Proposed method | Body | Class |
|---|---|---|
| `is_phase_complete(state, phase_id) -> bool` | All dispatchable steps have terminal results. | **PURE** — moves to PhaseManager + helper shim in `_executor_helpers`. |
| `evaluate_phase_approval_gate(state, phase_id) -> ApprovalGateOutcome` | Wraps `approval_passed_for_phase` + `phase_obj.approval_required`. | **PURE** — PhaseManager. |
| `evaluate_phase_feedback_gate(state, phase_id) -> FeedbackGateOutcome` | Wraps `feedback_resolved_for_phase` (latent bug bd-f4e3). | **PURE** — PhaseManager. |
| `evaluate_phase_gate(state, phase_id) -> GateOutcome` | Wraps `gate_passed_for_phase`. | **PURE** — PhaseManager. |
| `advance_phase(state) -> None` | Bumps `state.current_phase`, resets `current_step_index`. Optional `state.status="running"` toggle. | **MUTATING (CRISP)** — PhaseManager. |
| `synthesize_beads_post_phase(state)` | Heavy I/O. | **STAYS ON ENGINE.** |
| `close_open_beads_at_terminal(state, *, succeeded)` | Heavy I/O + audit rows. | **STAYS ON ENGINE.** |
| `enforce_veto_before_advance(state, phase_obj)` | Compliance audit + may raise. | **STAYS ON ENGINE.** |
| Event publication | Bus emission. | **STAYS ON ENGINE.** |

### 1.3 `is_phase_complete` definition

A phase is complete when, for every `step` in `phase.steps`:
- `step.step_id` is in `state.completed_step_ids`, OR
- `step.step_id` is in `state.failed_step_ids`, OR
- `step.step_id` is in `state.interrupted_step_ids`.

In-flight statuses (`dispatched`, `interacting`, `interact_dispatched`) do **not** satisfy completion.

### 1.4 Outcome dataclasses (frozen, in `phase_manager.py`)

```python
@dataclass(frozen=True)
class ApprovalGateOutcome:
    required: bool
    satisfied: bool
    rejected: bool

@dataclass(frozen=True)
class FeedbackGateOutcome:
    required: bool
    satisfied: bool
    pending_question_ids: tuple[str, ...] = ()

@dataclass(frozen=True)
class GateOutcome:
    required: bool
    satisfied: bool
    fail_count: int = 0
```

### 1.5 `PhaseManager.__init__` signature

```python
class PhaseManager:
    def __init__(self) -> None:
        # No collaborators. Zero-arg singleton.
        pass
```

**BEAD_DECISION:** PhaseManager is constructor-free. CHOSE: zero collaborators. BECAUSE: every method is either pure or a crisp single-field mutator. Wiring a bead store / event bus / policy engine into PhaseManager would re-import the heavy I/O slice we placed on the engine.

### 1.6 Public surface

```python
class PhaseManager:
    def __init__(self) -> None: ...
    def is_phase_complete(self, state, phase_id) -> bool: ...
    def evaluate_phase_approval_gate(self, state, phase_id) -> ApprovalGateOutcome: ...
    def evaluate_phase_feedback_gate(self, state, phase_id) -> FeedbackGateOutcome: ...
    def evaluate_phase_gate(self, state, phase_id) -> GateOutcome: ...
    def advance_phase(self, state, *, set_status_running: bool = False) -> None: ...
```

`set_status_running` distinguishes the two existing call sites: `EMPTY_PHASE_ADVANCE` (don't touch status) vs `PHASE_ADVANCE_OK` (yes, flip).

### 1.7 Helpers-module interaction

PhaseManager methods are thin adapters over `_executor_helpers.py` functions. Add **one** new helper:

```python
def is_phase_complete(state: ExecutionState, phase_id: int) -> bool:
    phase = next((p for p in state.plan.phases if p.phase_id == phase_id), None)
    if phase is None:
        return False
    terminal = (
        state.completed_step_ids
        | state.failed_step_ids
        | state.interrupted_step_ids
    )
    return all(s.step_id in terminal for s in phase.steps)
```

---

## 2. State Pattern scope

### 2.1 The `state.status` enumeration

| status | Resolver behaviour |
|---|---|
| `pending` | rare/transient — initial value before `start()` returns. |
| `running` | normal step-walking flow. |
| `paused` | takeover-style pause without takeover_records. |
| `paused-takeover` | `PAUSED_TAKEOVER` decision. |
| `gate_pending` | `GATE_PENDING` decision. |
| `gate_failed` | `GATE_FAILED` or `TERMINAL_FAILED`. |
| `approval_pending` | `APPROVAL_PENDING` decision. |
| `feedback_pending` | `FEEDBACK_PENDING` decision. |
| `complete` | `TERMINAL_COMPLETE`. |
| `failed` | `TERMINAL_FAILED`. |
| `budget_exceeded` | `BUDGET_EXCEEDED`. |

### 2.2 Mapping `state.status` to state classes

| State class | `state.status` values |
|---|---|
| `PlanningState` | `pending` |
| `ExecutingPhaseState` | `running` |
| `AwaitingApprovalState` | `approval_pending`, `feedback_pending`, `gate_pending`, `gate_failed`, `paused`, `paused-takeover` |
| `TerminalState` | `complete`, `failed`, `budget_exceeded` |

### 2.3 Which dispatch arms each state owns (mutation epilogue)

The engine dispatch table stays keyed on `DecisionKind`. The state class is consulted only for the small mutation tail. Concretely:

| DecisionKind | Owning state | Mutation |
|---|---|---|
| `TERMINAL_COMPLETE` | TerminalState | none (pure report) |
| `TERMINAL_FAILED` (running→failed) | ExecutingPhaseState→TerminalState | `state.status = "failed"`; persist. |
| `APPROVAL_PENDING` | AwaitingApprovalState | none (heavy builder) |
| `FEEDBACK_PENDING` | AwaitingApprovalState | none |
| `GATE_PENDING` | AwaitingApprovalState | none |
| `GATE_FAILED` | AwaitingApprovalState | none |
| `PAUSED_TAKEOVER` | AwaitingApprovalState | none |
| `BUDGET_EXCEEDED` | TerminalState | none |
| `NO_PHASES_LEFT` | ExecutingPhaseState | none |
| `EMPTY_PHASE_GATE` | ExecutingPhaseState | `state.status = "gate_pending"` |
| `EMPTY_PHASE_ADVANCE` | ExecutingPhaseState | delegates to `PhaseManager.advance_phase` |
| `STEP_FAILED_IN_PHASE` | ExecutingPhaseState→TerminalState | `state.status = "failed"` |
| `DISPATCH` / `TEAM_DISPATCH` / `INTERACT` / `INTERACT_CONTINUE` | ExecutingPhaseState | none (heavy builder) |
| `TIMEOUT` | ExecutingPhaseState→TerminalState | mutate `result.*`; `state.status = "failed"` |
| `WAIT` | ExecutingPhaseState | none |
| `PHASE_NEEDS_APPROVAL` | ExecutingPhaseState→AwaitingApprovalState | `state.status = "approval_pending"` |
| `PHASE_NEEDS_FEEDBACK` | ExecutingPhaseState→AwaitingApprovalState | `state.status = "feedback_pending"` |
| `PHASE_NEEDS_GATE` | ExecutingPhaseState→AwaitingApprovalState | `state.status = "gate_pending"` |
| `PHASE_ADVANCE_OK` | ExecutingPhaseState | delegates to `PhaseManager.advance_phase(set_status_running=True)` |

### 2.4 State class interface

```python
class ExecutionPhaseStateProtocol(Protocol):
    def handle(self, state: ExecutionState, decision: ResolverDecision) -> None: ...
```

- Single `handle()` method.
- `TerminalState.handle()` raises on any non-terminal decision.
- `AwaitingApprovalState.handle()` raises on DISPATCH-class decisions.

### 2.5 Open question RESOLVED — hybrid dispatch

**BEAD_DECISION:** dispatch table stays keyed on `DecisionKind`. CHOSE: hybrid (DecisionKind dispatch + status-class mutation epilogue). BECAUSE: state-class consulted only for the small mutation tail, while explicit DecisionKind branching remains the auditable protocol surface between resolver and engine.

### 2.6 State-class instantiation

Stateless singletons constructed once in `ExecutionEngine.__init__`:

```python
self._state_handlers: dict[str, ExecutionPhaseStateProtocol] = {
    "pending":           PlanningState(),
    "running":           ExecutingPhaseState(),
    "paused":            AwaitingApprovalState(),
    "paused-takeover":   AwaitingApprovalState(),
    "gate_pending":      AwaitingApprovalState(),
    "gate_failed":       AwaitingApprovalState(),
    "approval_pending":  AwaitingApprovalState(),
    "feedback_pending":  AwaitingApprovalState(),
    "complete":          TerminalState(),
    "failed":            TerminalState(),
    "budget_exceeded":   TerminalState(),
}

def _state_handler_for(self, status: str) -> ExecutionPhaseStateProtocol:
    handler = self._state_handlers.get(status)
    if handler is None:
        _log.warning("Unknown state.status %r — falling back to ExecutingPhaseState", status)
        return self._state_handlers["running"]
    return handler
```

### 2.7 What state classes are NOT for

- Not consulted for `record_step_result` / `record_gate_result` / `record_approval_result` / `record_feedback_result`.
- Don't drive event publication.
- Don't call `_synthesize_beads_post_phase`, `_close_open_beads_at_terminal`, `_enforce_veto_before_advance`.
- Don't import from `executor.py`.

---

## 3. Engine integration plan

### 3.1 Post-Phase-3 `_apply_resolver_decision` shape (illustrative, ~150-180 LOC down from ~370)

```python
def _apply_resolver_decision(self, state, decision):
    kind = decision.kind
    handler = self._state_handler_for(state.status)

    if kind == DecisionKind.TERMINAL_COMPLETE:
        return ExecutionAction(action_type=ActionType.COMPLETE, ...)
    if kind == DecisionKind.NO_PHASES_LEFT:
        return ExecutionAction(action_type=ActionType.COMPLETE, ...)
    if kind == DecisionKind.BUDGET_EXCEEDED:
        return ExecutionAction(action_type=ActionType.COMPLETE, ...)

    if kind == DecisionKind.TERMINAL_FAILED:
        handler.handle(state, decision)
        if state.status == "failed":
            self._save_execution(state)
        return ExecutionAction(action_type=ActionType.FAILED, ...)

    if kind == DecisionKind.STEP_FAILED_IN_PHASE:
        handler.handle(state, decision)
        self._close_open_beads_at_terminal(state, succeeded=False)
        return ExecutionAction(action_type=ActionType.FAILED, ...)

    if kind == DecisionKind.EMPTY_PHASE_GATE:
        handler.handle(state, decision)
        return self._build_gate_action(state.current_phase_obj)

    if kind == DecisionKind.PHASE_NEEDS_GATE:
        handler.handle(state, decision)
        return self._build_gate_action(state.current_phase_obj)

    if kind == DecisionKind.PHASE_NEEDS_APPROVAL:
        handler.handle(state, decision)
        return self._approval_action(state, state.current_phase_obj)

    if kind == DecisionKind.PHASE_NEEDS_FEEDBACK:
        handler.handle(state, decision)
        return self._feedback_action(state, state.current_phase_obj)

    if kind == DecisionKind.EMPTY_PHASE_ADVANCE:
        self._publish(evt.phase_completed(...))
        self._synthesize_beads_post_phase()
        self._phase_manager.advance_phase(state, set_status_running=False)
        self._publish_phase_started(state)
        return None

    if kind == DecisionKind.PHASE_ADVANCE_OK:
        self._enforce_veto_before_advance(state, state.current_phase_obj)
        self._publish(evt.phase_completed(...))
        self._synthesize_beads_post_phase()
        self._phase_manager.advance_phase(state, set_status_running=True)
        self._publish_phase_started(state)
        return None

    if kind == DecisionKind.TIMEOUT:
        self._write_timeout_bead(state, decision)
        handler.handle(state, decision)
        self.record_step_result(...)
        return ExecutionAction(action_type=ActionType.FAILED, ...)

    if kind == DecisionKind.DISPATCH:
        step = find_step(state, decision.step_id)
        return self._dispatch_action(step, state)

    # ... TEAM_DISPATCH, INTERACT, INTERACT_CONTINUE, WAIT,
    # APPROVAL_PENDING, FEEDBACK_PENDING, GATE_PENDING, GATE_FAILED,
    # PAUSED_TAKEOVER mirror existing structure ...

    raise RuntimeError(f"Unhandled DecisionKind: {kind!r}")
```

### 3.2 Engine `__init__` wire-up (after the resolver wire-up)

```python
from agent_baton.core.engine.phase_manager import PhaseManager
from agent_baton.core.engine.states import (
    PlanningState, ExecutingPhaseState, AwaitingApprovalState, TerminalState,
)

self._phase_manager = PhaseManager()
self._state_handlers = {...}  # see §2.6
```

Both private attributes. Tests monkeypatch as needed.

### 3.3 No changes to `next_action()`, `start()`, `resume()`

Already delegate to `_drive_resolver_loop`; behaviour unchanged.

---

## 4. Public API contract

Phase 3 must not change `ExecutionEngine`'s public API. Canary `tests/test_engine_api_contract.py` is the gate.

- 11-parameter constructor (Phase 2 §1.2) — frozen.
- 15 Protocol methods + `record_step_result` extras — frozen.
- Adds two **private** attributes (`_phase_manager`, `_state_handlers`) — must NOT be added to the contract test.
- No new positional/keyword args on the constructor.

---

## 5. Helpers-module rule (carried from Phases 1 & 2)

All new pure helpers go in `_executor_helpers.py`.

**Hard rules:**
1. PhaseManager imports from `_executor_helpers`. Engine imports from `_executor_helpers`. Resolver does not import from PhaseManager or state classes.
2. PhaseManager MUST NOT import from `executor.py`.
3. `states.py` MUST NOT import from `executor.py` or `phase_manager.py`.
4. Resolver MUST NOT import from `phase_manager.py` or `states.py`.

Net dependency graph:
```
                resolver.py ──┐
                              │
_executor_helpers.py ─── phase_manager.py ──── executor.py
                              │                  │
                          states.py ─────────────┘
```

---

## 6. Risks and open questions

### Q1. Latent bug bd-f4e3: `feedback_resolved_for_phase` ignores `phase_id` (BEAD_WARNING)

`_executor_helpers.feedback_resolved_for_phase()` reads `state.current_phase_obj`, not the phase identified by `phase_id`. For non-current-phase queries, returns wrong answer. **DESIGN_CHOICE — preserve in Phase 3 to keep the cutover behaviour-neutral.** Document on PhaseManager's wrapper. Defer fix to Phase 5.

### Q2. Concurrency

PhaseManager's `advance_phase` inherits the same threading caveats as `_drive_resolver_loop`. No new shared state. **No new concurrency risk introduced by Phase 3.**

### Q3. External callers

Action item before 3.4: `cymbal investigate _synthesize_beads_post_phase`, `cymbal investigate _close_open_beads_at_terminal`, etc. None expected to be public.

### Q4. Does `state.status` carry too much engine-specific meaning for the State Pattern?

**Verified — no.** Status values cluster cleanly into 4 groups matching the proposal's classes. Edge case `gate_failed` retry-budget logic stays in resolver (already done in Phase 2).

### Q5. Risk that State Pattern leaks (Phase 3.5 fallback)

Mitigated by hybrid dispatch (§2.5). **Kill switch:** if any state class needs an injected collaborator (bead store / event bus / VETO scanner), the State Pattern is leaking — defer State Pattern to Phase 3.5 and ship Phase 3 as PhaseManager-only.

### Q6. Test coverage

- `tests/test_phase_manager.py` (new) — pure-method coverage.
- `tests/test_execution_states.py` (new) — state-class coverage; assert each class raises on illegal transitions.
- Existing `tests/test_executor.py` exercises the dispatch table — runs unchanged.

### Q7. Recursion bound

Phase 2's `_drive_resolver_loop` bound stays. PhaseManager's `advance_phase` is O(1).

### Q8. Module sizes

Estimate post-3.4: `phase_manager.py` ~120-150 lines; `states.py` ~150-200 lines.

---

## 7. Implementation sequencing for Steps 3.2–3.4

### Step 3.2 — PhaseManager + tests (parallel-safe)

Files: `agent_baton/core/engine/phase_manager.py`, `agent_baton/core/engine/_executor_helpers.py`, `tests/test_phase_manager.py` (new).

1. Replace 45-line skeleton with §1 contract.
2. Add `is_phase_complete` to `_executor_helpers.py`.
3. Write `tests/test_phase_manager.py` covering every method + edge cases.
4. **Do not** wire PhaseManager into engine yet.

### Step 3.3 — State classes + tests (parallel-safe)

Files: `agent_baton/core/engine/states.py`, `tests/test_execution_states.py` (new).

1. Replace 73-line skeleton. Define `ExecutionPhaseStateProtocol`.
2. Implement four state classes per §2.3 / §2.4.
3. Write `tests/test_execution_states.py` covering every state class + illegal-transition raises.
4. **Do not** wire state classes into engine yet.

#### Parallelism note (mandatory)

**Steps 3.2 and 3.3 touch disjoint files** (`phase_manager.py` + `_executor_helpers.py` vs. `states.py`). They share **no symbols**. **Dispatch in parallel using `isolation: "worktree"`** per the project's concurrent-agent isolation rule, then re-merge before 3.4. The single shared write target across the two steps is `_executor_helpers.py` (`is_phase_complete` addition for 3.2). 3.3 does not modify it. No merge conflict.

### Step 3.4 — Wire both into ExecutionEngine + delete duplicates

Files: `agent_baton/core/engine/executor.py`.

1. Add `_phase_manager` + `_state_handlers` to `__init__`.
2. Add `_state_handler_for(status)`.
3. Add `_build_gate_action(phase_obj)` (DRY for the three GATE arms).
4. Add `_publish_phase_started(state)` (DRY for the two advance arms).
5. Refactor each arm of `_apply_resolver_decision` per §3.1.
6. Run full executor + canary suite. Zero regressions = the gate.
7. Cleanup: confirm no inverted imports.

Estimated effort: ~1.5 days. Risk: medium (cutover; touches dispatch table). Mitigated by canary + existing test_executor.py suite.

### Sequencing diagram

```
Step 3.2 (worktree A) ─┐
                       ├─ merge ─ Step 3.4 (engine wire-up)
Step 3.3 (worktree B) ─┘
```

Total wall-time: ~2.5 days with parallel 3.2/3.3.

---

## Files referenced

- `proposals/005b-implementation-plan.md` §4
- `agent_baton/core/engine/executor.py`
- `agent_baton/core/engine/phase_manager.py` (skeleton — replace per §1)
- `agent_baton/core/engine/states.py` (skeleton — replace per §2)
- `agent_baton/core/engine/resolver.py` (Phase 2; do not modify)
- `agent_baton/core/engine/_executor_helpers.py` (add `is_phase_complete`)
- `agent_baton/models/execution.py`
- `tests/test_engine_api_contract.py` (canary)
- `tests/test_executor.py` (behaviour)
- `tests/test_action_resolver.py` (Phase 2 tests)
- `tests/test_phase_manager.py` (NEW)
- `tests/test_execution_states.py` (NEW)
- `docs/internal/005b-phase1-design.md`, `docs/internal/005b-phase2-design.md`
