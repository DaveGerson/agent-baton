# 005b Phase 2 Design — ActionResolver Extraction

**Step:** 2.1 (architect)
**Branch:** `feat/005b-engine-decomposition`
**Target file:** `agent_baton/core/engine/executor.py` (6,986 lines)
**Source proposal:** `proposals/005b-implementation-plan.md` §3
**Skeleton:** `agent_baton/core/engine/resolver.py` (45-line stub from `3c3c548`)

---

## 1. Public API contract — `ExecutionEngine` (must remain byte-identical)

`ExecutionEngine` is the canonical implementation of the `ExecutionDriver` Protocol (`agent_baton/core/engine/protocols.py:22-292`). The Protocol freezes 15 method signatures.

### 1.1 Frozen `ExecutionDriver` methods

| Method | Source (lines in `protocols.py`) |
|---|---|
| `start(self, plan: MachinePlan) -> ExecutionAction` | 30–43 |
| `next_action(self) -> ExecutionAction` | 45–55 |
| `next_actions(self) -> list[ExecutionAction]` | 57–69 |
| `mark_dispatched(self, step_id: str, agent_name: str) -> None` | 71–81 |
| `record_step_result(...)` (9 kwargs in Protocol; 12 on engine) | 83–112 |
| `record_gate_result(self, phase_id: int, passed: bool, output: str = "") -> None` | 114–130 |
| `record_approval_result(self, phase_id, result, feedback="") -> None` | 132–149 |
| `record_feedback_result(self, phase_id, question_id, chosen_index) -> None` | 151–168 |
| `amend_plan(...)` (8 kwargs) | 170–202 |
| `record_team_member_result(...)` | 204–226 |
| `complete(self) -> str` | 228–238 |
| `status(self) -> dict` | 240–248 |
| `resume(self) -> ExecutionAction` | 250–259 |
| `provide_interact_input(...)` | 261–280 |
| `complete_interaction(self, step_id: str) -> None` | 282–292 |

**BEAD_DISCOVERY:** The Protocol has 15 methods. The engine's `record_step_result` adds 3 extra kwargs not in the Protocol (`session_id`, `step_started_at`, `outcome_spillover_path`). The contract test must pin both.

### 1.2 Constructor signature (frozen)

```python
def __init__(
    self,
    team_context_root: Path | None = None,
    bus: EventBus | None = None,
    task_id: str | None = None,
    storage=None,
    knowledge_resolver=None,
    policy_engine=None,
    enforce_token_budget: bool = True,
    token_budget: int | None = None,
    max_gate_retries: int = 3,
    force_override: bool = False,
    override_justification: str = "",
) -> None: ...
```

Phase 2 will **not** add a positional `resolver` parameter — see §3.

### 1.3 `next_action` body today (`executor.py:1511-1545`)

1. `state = self._load_execution()`
2. If `state is None` → return synthetic `ActionType.FAILED`.
3. `action = self._determine_action(state)` — **mutates `state`**.
4. `self._save_execution(state)`.
5. Return `action`.

Post-refactor `next_action()` keeps the same shape. The mutation that `_determine_action` performs today **stays on the engine side**, not in the resolver.

### 1.4 `record_step_result` extra kwargs (engine signature)

```python
def record_step_result(self, step_id, agent_name, status="complete", outcome="",
    files_changed=None, commit_hash="", estimated_tokens=0, duration_seconds=0.0,
    error="", session_id="", step_started_at="", outcome_spillover_path="") -> None
```

Not touched by Phase 2.

### 1.5 Engine attributes external callers depend on

`_task_id`, `_root`, `_storage`, `_bus`, `_bead_store`, `_team_registry`, `_worktree_mgr`, `_swarm`, `_compliance_log_path`, `_force_override`, `_override_justification`, `_max_gate_retries`, `_enforce_token_budget`, `_token_budget`, `_policy_approved_steps`, `_trace`, plus the public method `set_swarm_launcher(launcher)`.

---

## 2. ActionResolver extraction map

### 2.1 Anchor: `_determine_action`

**Location:** `executor.py:4866-5322` (~457 lines).

**Docstring claim:** "does NOT mutate state".

**BEAD_DISCOVERY: The docstring is wrong.** The method performs ~20 distinct state mutations and side effects:

| Lines | Mutation / side effect |
|---|---|
| 4941–4942 | `state.status = "failed"` + `_save_execution(state)` when gate fail count exceeds cap |
| 5057 | `state.status = "gate_pending"` |
| 5066–5070 | `_publish(evt.phase_completed(...))` |
| 5072 | `_synthesize_beads_post_phase()` |
| 5073–5074 | `state.current_phase += 1`; `state.current_step_index = 0` |
| 5077–5088 | `_publish(evt.phase_pre_start/started(...))` |
| 5089 | **Recursive call** `self._determine_action(state)` |
| 5094 | `state.status = "failed"` |
| 5097 | `_close_open_beads_at_terminal(state, succeeded=False)` |
| 5215 | `_bead_store.write(_bead)` (timeout warning) |
| 5225–5230 | Mutate `result.status/outcome/error/...`; `state.status = "failed"` |
| 5231–5237 | `record_step_result(...)` recursive call |
| 5270 | `state.status = "approval_pending"` |
| 5276 | `state.status = "feedback_pending"` |
| 5280 | `state.status = "gate_pending"` |
| 5294 | `_enforce_veto_before_advance(...)` (may raise; writes Override row) |
| 5297–5301 | `_publish(evt.phase_completed(...))` |
| 5304 | `_synthesize_beads_post_phase()` |
| 5305–5307 | `state.current_phase += 1`; `state.current_step_index = 0`; `state.status = "running"` |
| 5310–5321 | `_publish(evt.phase_pre_start/started(...))` |
| 5322 | **Recursive call** `self._determine_action(state)` |
| 5363 | `_check_policy_block(state, step)` (may mutate `state.status`/`state.approval_results`) |
| 5380–5395 | Mutate `existing_result.status` (interact); `_save_execution(state)` |
| 5530–5532 | `_persistence.save(state)` (delivered_knowledge) |
| 5540 | `_emit_knowledge_used(...)` |
| 5546 | `_compliance_dispatch(...)` |

A literal "extract to a pure function" approach is **infeasible** without behavior change. The proposal §3 says "It mutates nothing" — that is the *target* shape.

### 2.2 Decomposition strategy

**Slice A — Pure read-only resolver** (`ActionResolver.determine_next`): inspect state, compute a `ResolverDecision` intent. Does **not** mutate, publish events, or do I/O.

**Slice B — State-mutation epilogue** (stays on engine): phase advancement, status transitions, event publication, bead synthesis, VETO enforcement, compliance writes. Engine's `_drive_resolver_loop` calls the resolver, then applies the corresponding epilogue.

**Slice C — Dispatch action builder** (stays on engine): `_dispatch_action`, `_team_dispatch_action`, `_interact_action`, `_approval_action`, `_feedback_action`. They construct delegation prompts, check policy (mutating), emit telemetry, write compliance rows, persist `delivered_knowledge`. **Cannot be inside the pure resolver.**

The resolver returns a **lightweight intent object** describing what kind of action is wanted. The engine:
1. Applies the state mutation appropriate to that intent.
2. Calls the heavy builder when needed.
3. Saves state.
4. Returns the resulting `ExecutionAction`.

### 2.3 Helper-method classification

| Helper | Lines | Class | Notes |
|---|---|---|---|
| `_gate_passed_for_phase` | 5695–5701 (staticmethod) | **PURE** | Moves to resolver / shared helpers. |
| `_approval_passed_for_phase` | 5703–5709 (staticmethod) | **PURE** | Moves. |
| `_feedback_resolved_for_phase` | 5771–5782 (staticmethod) | **PURE** | Moves. |
| `_find_step` | (used at 5148, 5155, 5169) | **PURE** | Shared helpers. |
| `_effective_timeout` | (used at 5172) | **PURE** | Shared helpers. |
| `_approval_action` | 5711–5727 | **HEAVY-BUILDER** | Stays on engine. |
| `_feedback_action` | 5784–5807 | **HEAVY-BUILDER** | Stays. |
| `_interact_action` | 5581–5613 | **HEAVY-BUILDER** | Stays. |
| `_dispatch_action` | 5324–5570 | **HEAVY+MUTATING** | Stays. |
| `_team_dispatch_action` | 5998+ | **HEAVY** | Stays. |
| `_check_policy_block` | (within dispatch) | **MUTATING** | Stays. |
| `_check_timeout` | called at 5257 | **MUTATING** | Stays. |
| `_synthesize_beads_post_phase` | 5072, 5304 | **MUTATING** | Stays. |
| `_close_open_beads_at_terminal` | 5097 | **MUTATING** | Stays. |
| `_enforce_veto_before_advance` | 4796+ | **MUTATING** | Stays. |
| `_publish` | event bus | **MUTATING** | Stays. |

### 2.4 Pure-state inspection helpers the resolver uses

Already-pure properties of `ExecutionState`:
- `current_phase_obj`, `completed_step_ids`, `failed_step_ids`, `dispatched_step_ids`, `interrupted_step_ids`, `get_step_result(step_id)`

Plus the three classmethod helpers: `_gate_passed_for_phase`, `_approval_passed_for_phase`, `_feedback_resolved_for_phase`.

The resolver also reads (read-only): `state.status`, `state.current_phase`, `state.plan.phases`, `state.approval_results`, `state.gate_results`, `state.step_results`, `state.takeover_records`, `state.task_id`.

### 2.5 The policy-check mutation problem (BEAD_WARNING)

`_dispatch_action` calls `_check_policy_block(state, step)` (line 5362). On block-severity violation, it returns an `APPROVAL` action AND mutates `state.status`/`state.approval_results`. This mutation happens during action determination today.

After refactor:
- Resolver detects "next would be a dispatch of step X" → returns `DISPATCH_REQUESTED(step=X)`.
- Engine's `next_action()` driver calls `self._dispatch_action(step, state)` to materialise the action. **`_dispatch_action` continues to perform the policy check and continues to mutate state on block.**

Behavior preserved. Mutation does not move into the resolver.

### 2.6 The `ResolverDecision` dataclass (NEW)

Today `_determine_action` returns a fully-built `ExecutionAction`. **DESIGN_CHOICE: introduce an internal `ResolverDecision` dataclass.** The resolver returns small intent objects; the engine translates them into the final `ExecutionAction`.

```python
# agent_baton/core/engine/resolver.py
from dataclasses import dataclass
from enum import Enum

class DecisionKind(Enum):
    TERMINAL_COMPLETE = "terminal_complete"
    TERMINAL_FAILED   = "terminal_failed"
    APPROVAL_PENDING  = "approval_pending"
    FEEDBACK_PENDING  = "feedback_pending"
    GATE_PENDING      = "gate_pending"
    GATE_FAILED       = "gate_failed"
    PAUSED_TAKEOVER   = "paused_takeover"
    BUDGET_EXCEEDED   = "budget_exceeded"
    NO_PHASES_LEFT    = "no_phases_left"
    EMPTY_PHASE_GATE  = "empty_phase_gate"
    EMPTY_PHASE_ADVANCE = "empty_phase_advance"
    STEP_FAILED_IN_PHASE = "step_failed_in_phase"
    DISPATCH          = "dispatch"
    TEAM_DISPATCH     = "team_dispatch"
    INTERACT          = "interact"
    INTERACT_CONTINUE = "interact_continue"
    TIMEOUT           = "timeout"
    WAIT              = "wait"
    PHASE_NEEDS_APPROVAL = "phase_needs_approval"
    PHASE_NEEDS_FEEDBACK = "phase_needs_feedback"
    PHASE_NEEDS_GATE     = "phase_needs_gate"
    PHASE_ADVANCE_OK     = "phase_advance_ok"

@dataclass(frozen=True)
class ResolverDecision:
    kind: DecisionKind
    phase_id: int | None = None
    step_id: str | None = None
    failed_step_ids: tuple[str, ...] = ()
    fail_count: int = 0
    message: str = ""
    summary: str = ""
```

Frozen (immutability). Carries no `ExecutionAction` and no objects with state references — just IDs and primitive payload.

### 2.7 The recursion problem (BEAD_WARNING)

`_determine_action` recursively calls itself at lines 5089 and 5322 after advancing the phase, bounded by N = number of empty/transitive phases.

**Strategy:** the engine owns the recursion. Resolver returns `EMPTY_PHASE_ADVANCE` / `PHASE_ADVANCE_OK`; engine performs mutation, then **re-invokes the resolver**. Loop on engine side, bounded by phase count + small slack:

```python
def _drive_resolver_loop(self, state: ExecutionState) -> ExecutionAction:
    while True:
        decision = self._resolver.determine_next(state)
        action = self._apply_resolver_decision(state, decision)
        if action is not None:
            return action
        # otherwise loop (transitive advance)
```

---

## 3. Dependency injection plan

### 3.1 Corrected resolver constructor

```python
class ActionResolver:
    """Stateless evaluator. Computes the next ResolverDecision from state.

    No state held between calls. Caller (ExecutionEngine) is responsible
    for mutation, persistence, event publication, and constructing the
    final ExecutionAction.
    """
    def __init__(self, *, max_gate_retries: int = 3) -> None:
        self._max_gate_retries = max_gate_retries

    def determine_next(self, state: ExecutionState) -> ResolverDecision: ...
```

Only `max_gate_retries` is needed because the resolver compares gate fail counts against the cap to decide between `GATE_FAILED` (retry) and `TERMINAL_FAILED` (give up). Everything else (telemetry, beads, worktrees, swarm, policy) participates only in side-effect code paths that stay on the engine.

**Decision rule:** if the resolver needs a value to decide, accept via constructor. If it needs a value to *do* something, that "doing" stays on the engine.

### 3.2 No back-references to `ExecutionEngine`

The resolver imports nothing from `executor.py`. Type-only imports for `ExecutionState` come from `agent_baton.models.execution`. Avoid the `_TASK_TYPE_KEYWORDS` inverted-import pattern Phase 1 left as a follow-up.

### 3.3 Engine wiring

```python
# executor.py — inside ExecutionEngine.__init__
self._resolver = ActionResolver(max_gate_retries=self._max_gate_retries)
```

**BEAD_DECISION: add resolver as private attribute, not constructor kwarg. CHOSE: hidden internal dependency. BECAUSE: public constructor is frozen by API contract; tests that want to inject a fake resolver can monkeypatch `engine._resolver`.**

---

## 4. Engine wire-up plan

### 4.1 Post-refactor `next_action()`

```python
def next_action(self) -> ExecutionAction:
    state = self._load_execution()
    if state is None:
        task_hint = self._task_id or "(no task_id)"
        return ExecutionAction(
            action_type=ActionType.FAILED,
            message=(
                f"No execution state found for task '{task_hint}'. "
                f"Run 'baton execute start' to begin, or "
                f"'baton execute list' to find existing executions."
            ),
            summary=f"No execution state for '{task_hint}'.",
        )
    action = self._drive_resolver_loop(state)
    self._save_execution(state)
    return action
```

### 4.2 The `_drive_resolver_loop` method

Owns all state mutation. The dispatch table maps each `DecisionKind` to:
- the state mutation that today happens inside `_determine_action`,
- the heavy-builder call (`_dispatch_action`, etc.) when applicable,
- and either returns the final `ExecutionAction` or returns `None` to loop.

### 4.3 Same wire-up in `start()` and `resume()`

`start()` (line 1509) and `resume()` (line 3613) both call `self._determine_action(state)` directly. Replace with `self._drive_resolver_loop(state)`. Behaviour: byte-identical.

---

## 5. Helpers module rule (codified from Phase 1)

Phase 1 left an inverted-import follow-up for `_TASK_TYPE_KEYWORDS`. Phase 2 must avoid it.

1. **Create `agent_baton/core/engine/_executor_helpers.py`** with:
   - `_find_step(state, step_id) -> PlanStep | None`
   - `_effective_timeout(plan_step) -> int`
   - `_gate_passed_for_phase(state, phase_id) -> bool`
   - `_approval_passed_for_phase(state, phase_id) -> bool`
   - `_feedback_resolved_for_phase(state, phase_id) -> bool`

2. **No re-exports back into `executor.py`** unless tests directly import the symbol. Audit before deletion: `cymbal investigate _gate_passed_for_phase`. If a test imports from `executor`, leave a thin staticmethod shim that delegates.

3. **The resolver does not import from executor.py.** Hard rule. Validate via `cymbal impact ExecutionEngine` and `cymbal impact resolver`.

4. **`_executor_helpers.py` contains no engine-instance references.** Top-level pure functions only.

---

## 6. Risks and open questions

### Q1. The "this method is pure" docstring lie (BEAD_DISCOVERY)
`_determine_action`'s current docstring claims it does not mutate state. **It does** — extensively. The refactor must preserve every mutation, just relocated to the engine's `_apply_resolver_decision`. Run the full executor suite at every intermediate step.

### Q2. Threading / concurrency (BEAD_WARNING)
`next_action()` is called by both the synchronous CLI orchestrator and the async `TaskWorker`. Today the engine relies on the SQLite layer for atomicity at save points. The resolver is read-only and stateless across calls — no new concurrency concern. The engine's `_drive_resolver_loop` mutates in the same lifecycle moments as `_determine_action` today. **Pre-existing risk; Phase 2 doesn't address it.**

### Q3. ActionType handling — all 9 values (BEAD_DISCOVERY)
| ActionType | Source DecisionKind(s) |
|---|---|
| DISPATCH | DISPATCH, TEAM_DISPATCH, INTERACT_CONTINUE |
| GATE | GATE_PENDING, GATE_FAILED (retry), EMPTY_PHASE_GATE, PHASE_NEEDS_GATE |
| COMPLETE | TERMINAL_COMPLETE, NO_PHASES_LEFT, BUDGET_EXCEEDED |
| FAILED | TERMINAL_FAILED, STEP_FAILED_IN_PHASE, TIMEOUT, GATE_FAILED (terminal) |
| WAIT | PAUSED_TAKEOVER, WAIT |
| APPROVAL | APPROVAL_PENDING, PHASE_NEEDS_APPROVAL |
| FEEDBACK | FEEDBACK_PENDING, PHASE_NEEDS_FEEDBACK |
| INTERACT | INTERACT |
| SWARM_DISPATCH | **Not emitted by `_determine_action` today.** Triggered by `SwarmDispatcher`; resolver doesn't emit it. |

### Q4. INTERACT semantics (BEAD_DECISION)
**resolver decides at boundaries, engine drives the conversation.** CHOSE: keep all multi-turn driving on engine. BECAUSE: matches `interact_at_boundaries` feedback. Resolver is for state→intent decisions, not protocol orchestration.

### Q5. Timeout enforcement is a mutation (BEAD_WARNING)
Lines 5166-5243 mutate the step result, write a warning bead, and recursively call `record_step_result`. **Strategy:** extract pure `find_timed_out_step(state) -> StepResult | None`. Resolver returns `TIMEOUT` decision; engine's `_apply_resolver_decision` performs the mutation.

### Q6. Bead conflict warning (lines 5018-5034)
Side effect during decision today. **Strategy:** the engine performs this check inside `_drive_resolver_loop` once before invoking the resolver each iteration, or inside `next_action()` before the loop.

### Q7. `_check_policy_block` mutates during dispatch
Stays on engine, called by `_dispatch_action`. Resolver doesn't see policy.

### Q8. `start()` initialisation order
`start()` returns `self._drive_resolver_loop(state)` after the initial save and active-task pointer write, same as today. Verify with `tests/test_executor.py::TestStartFlow`.

### Q9. The "no execution state" failure path
Today `next_action` returns synthetic FAILED when state is None (lines 1531-1541). After refactor, this branch stays in `next_action` before the loop. Resolver type pins `ExecutionState`, not `ExecutionState | None`.

### Q10. Existing tests for `_determine_action` directly
**ACTION ITEM:** before deleting `_determine_action`, run `cymbal investigate _determine_action` and confirm no tests reference it directly. If they do, retain it as a one-line shim: `return self._drive_resolver_loop(state)`. Remove in Phase 3.

### Q11. Recursion bounding
Bound `_drive_resolver_loop` at `len(state.plan.phases) + 4` iterations. Raise on exceed — would indicate a resolver bug.

### Q12. Cyclomatic complexity in `_apply_resolver_decision`
Dispatch table will have 20+ branches. Fine for readability — each branch is small and named. Don't collapse via metaprogramming.

---

## 7. Implementation sequencing for Step 2.2

Run `pytest -q tests/test_executor.py` after each:

1. **Add API contract canary test** `tests/test_engine_api_contract.py` modelled on `tests/test_planner_api_contract.py`. Pin all 15 Protocol method signatures + the engine's extra kwargs.

2. **Create `_executor_helpers.py`** with the 5 pure helpers from §5. Update `_determine_action` to call them. Run tests.

3. **Define `ResolverDecision` and `DecisionKind` in `resolver.py`.** Replace skeleton `__init__(state)` with `__init__(*, max_gate_retries=3)`. Implement `determine_next(state)` translating each branch of `_determine_action` into a `ResolverDecision`. **Don't yet wire the engine to call it.** Add unit tests in `tests/test_action_resolver.py` covering every `DecisionKind`.

4. **Add `self._resolver = ActionResolver(...)` to `ExecutionEngine.__init__`.** Implement `_drive_resolver_loop(state)` and `_apply_resolver_decision(state, decision)`. Initially `_determine_action` is the live path; the new code is dead.

5. **Switch `next_action()` to `_drive_resolver_loop`.** Same for `start()` and `resume()`. Confirm canary test green and full executor suite green.

6. **Delete the dead `_determine_action`** from `executor.py` (or retain as a one-line shim if tests reference it directly per Q10).

7. **Cleanup pass:** confirm no inverted-import (`cymbal impact resolver` should show no `executor.py` consumers reaching back).

Estimated effort: ~3 days. Bulk of risk in step 4 (dispatch table) and step 5 (cutover).

---

## Files referenced

- `proposals/005b-implementation-plan.md` §3
- `agent_baton/core/engine/executor.py` (extraction source)
- `agent_baton/core/engine/resolver.py` (skeleton — replace)
- `agent_baton/core/engine/protocols.py` (frozen 15-method Protocol)
- `agent_baton/core/engine/_executor_helpers.py` (new)
- `agent_baton/models/execution.py`
- `tests/test_executor.py`
- `tests/test_engine_api_contract.py` (new canary)
- `tests/test_action_resolver.py` (new unit tests)
- `docs/internal/005b-phase1-design.md` (style reference + helpers-module rule)
