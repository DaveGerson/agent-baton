# State Mutation Proposal — Closing the Hole 1 Logical Gap

**Status**: Draft (analysis only; no production code changes). Supersedes [pydantic-migration-mutation-audit.md](pydantic-migration-mutation-audit.md) — the audit's class-A/B/C/D taxonomy is correct but its line numbers are stale and its scope is one invariant; this document re-anchors by symbol and expands to the three Hole-1-class invariants (I1, I2, I9). Cross-walk with the SQLite parity and result-hierarchy proposals lives in [migration-review-summary.md](migration-review-summary.md).
**Scope**: `ExecutionState`, `MachinePlan` (and nested), and the result types — pre-Pydantic-migration.
**Recommendation in one sentence**: Adopt **alternative (d) — hybrid funnel**: route `status`, `pending_approval_request`, `completed_at`, and `takeover_records` writes through ~12 transition methods on `ExecutionState`, land it as a half-day slice **before** Pydantic Phase 1 (gated by an AST lint test), then promote those fields to `PrivateAttr` field privacy during the Pydantic migration. This closes I1 (Hole 1), I2 (terminal `completed_at`), and I9 (paused-takeover) by structural impossibility rather than by validator-after-the-fact, in **1.5–3 developer-days** total.

---

## 1. Mutation-site audit (verified independently)

I re-grepped HEAD. The Phase-0 audit at `docs/internal/pydantic-migration-mutation-audit.md` is approximately right but stale; new mutation sites have appeared since 2026-05-06 and line numbers do not match HEAD.

### Category A — Pure scalar updates with no invariant coupling

| Field | Sites |
|---|---|
| `state.run_cumulative_spend_usd` | accessor-driven; `executor.py:3914` reads it |
| `state.scope_expansions_applied` | `executor.py:4286` (`getattr(...) + 1`) |
| `state.completed_at` | `cli/commands/execution/execute.py:1483`, `executor.py:1086`, `executor.py:3473` |
| `state.consolidation_result` | `executor.py:3573` |

Bookkeeping fields. None feed an invariant other than "set at most once near termination."

### Category B — Coupled mutations (the dangerous class)

A single logical operation that MUST update two or more fields atomically.

**B1. Approval-pending coupling** — `state.status == "approval_pending"` ⇔ `state.pending_approval_request is not None`.

| Site | Status flip | Pending-row write | Order |
|---|---|---|---|
| `executor.py:4398` (team-conflict escalation) | `= "approval_pending"` | `executor.py:4419` `= PendingApprovalRequest(...)` | flip first, set row second — 21 lines apart, separated by branching. Risk: an early `return` between 4398 and 4419 leaves status flipped and row blank. |
| `executor.py:6286` (`_approval_action`) | NOT touched here — caller flipped via `states.py:124` | `= PendingApprovalRequest(...)` | row set, status flipped elsewhere. The two writes live in different files. **Exact seam Hole 1 came through.** |
| `executor.py:4086` (`record_approval_result`) | flipped to `"running"`/`"failed"` at lines 4099/4101/4121 | `= None` at 4086, BEFORE the status flip | row clear first, status flip second — 13–35 lines apart. Exception between 4086 and 4099 leaves row=None with status still `"approval_pending"` — the **other** direction of the same invariant violation. |
| `states.py:124` (`ExecutingPhaseState.handle`) | `= "approval_pending"` | NOT touched | The State Pattern explicitly separates the status flip from the audit-row write. Re-orderings or new dispatch arms can desync. |

**B2. Phase-progression coupling** — `current_phase` advances ⇒ `current_step_index` resets; status MAY flip.

| Site | Mutation | Notes |
|---|---|---|
| `phase_manager.py:290-293` (`advance_phase`) | `current_phase += 1`, `current_step_index = 0`, optional `status = "running"` | Already centralised. Good. |
| `phase_manager.py:323-326` (`retry_phase`) | `current_step_index = sum(...)`, `status = "running"`. Does NOT bump `current_phase`. | Already centralised. Good. |

The one coupled mutation correctly funnelled. **Proves funnel-through-method works.**

**B3. Terminal-state coupling** — `state.status in ("complete", "failed", "cancelled")` SHOULD imply `completed_at != ""`.

| Site | Status flip | `completed_at` set? |
|---|---|---|
| `executor.py:3472-3473` (`complete()`) | `= "complete"` | YES |
| `cli/commands/execution/execute.py:1482-1483` (`cancel`) | `= "cancelled"` | YES |
| `executor.py:1085-1086` | `= "failed"` | YES |
| `executor.py:1468`, `2982`, `3126`, `3756`, `4099`, `6799`, `states.py:135/142/149/204`, `worker.py:488/505`, `api/routes/executions.py:411` | `= "failed"` | NOT set in any of these |

**Six of fourteen `failed`-flip sites do not set `completed_at`.** This is a second invariant gap of the same shape as Hole 1, hiding because the consequence is "wrong duration in retrospective" rather than "missing approval audit row."

**B4. Budget-gate coupling** — `state.status == "budget_exceeded"` ⇔ event published AND resume gate exists.

`executor.py:4651` sets and publishes in one block; `executor.py:4691` (`resume_budget`) flips back to `"running"` but doesn't clear `pending_approval_request` — currently safe by accident (resume_budget is human-triggered after operator inspection), but no invariant declares it.

### Category C — State-Pattern leaks

The State Pattern landed in 005b Phase 3. Four state classes own status-cluster mutation epilogues. **The pattern is the right shape but is leaky** — every direct `state.status = "..."` outside `states.py` is a side-wall leak.

Leaks today:
- `executor.py:1085, 1468, 2884, 2963, 2982, 3050, 3126, 3270, 3472, 4099, 4101, 4121, 4398, 4651, 4691, 6520, 6799` (17 leaks)
- `worker.py:488, 505` (2 leaks)
- `api/routes/executions.py:411` (1 leak — REST stop-execution endpoint)
- `cli/commands/execution/execute.py:1482` (1 leak — cancel verb)

**21 leaks total.** State Pattern was added for the resolver-decision path and stopped there.

### Category D — List/dict appends and nested mutations

`step_results.append`, `gate_results.append`, `approval_results.append`, etc. The audit's count of 19 + 16 is approximately right.

Coupling-light: appending a `step_result` doesn't need to bump `current_step_index` (executor recomputes from `current_phase` + step list). Exception: `step_results[idx] = result` at `executor.py:1929` (in-place replace by index) is internally coupled to `StepResult.status` but the coupling is local to one function — transition-method discipline overkill there.

---

## 2. Invariants on `ExecutionState`

Derived from `executor.py`, `resolver.py`, `states.py`, `phase_manager.py`, and field-level docstrings.

| # | Invariant | Stated where | Mutation sites that could violate |
|---|---|---|---|
| **I1** | `status == "approval_pending"` ⇔ `pending_approval_request is not None` | Tacit; comment at `executor.py:4086`. **This is Hole 1.** | B1 — 4 sites across 2 files |
| **I2** | `status in {"complete","failed","cancelled","budget_exceeded"}` ⇒ `completed_at != ""` for the first three | Tacit; not enforced anywhere | B3 — 14 sites, 6 currently violate |
| **I3** | `len(step_results) <= total_dispatchable_steps_in_plan` | Tacit | All `step_results.append` sites; safe in practice |
| **I4** | `current_phase` monotonically non-decreasing | `advance_phase` design; `retry_phase` doesn't advance | Only `phase_manager.py` mutates these |
| **I5** | `0 <= current_phase <= len(plan.phases)` | Tacit | Only `phase_manager.py` |
| **I6** | `0 <= current_step_index` consistent with prefix-sum of phase lengths | Tacit | Only `phase_manager.py` |
| **I7** | `status == "budget_exceeded"` ⇒ no new dispatches until `resume_budget` | Stated in `_check_budget` docstring; enforced by guards | Safe by construction |
| **I8** | `status == "failed"` and rejected approval exists ⇒ `approval_results[-1].result == "reject"` | Stated by `resolver.py:152-175` | `record_approval_result` — single site, safe |
| **I9** | `status == "paused-takeover"` ⇒ at least one `takeover_records` entry has empty `resumed_at` | Tacit; relied on by `resolver.py:236-240` | `executor.py:3050, 3270` flip status; takeover-record write happens in a different code path. **Same shape as Hole 1.** |
| **I10** | `step_worktrees[step_id]` exists ⇒ step is dispatched but not yet folded back | Tacit | Worktree manager and `record_step_result` |

**Hole-1-class invariants (`status` ⇔ sibling field) under threat from Category B mutations: I1, I2, I9.** Three. Not one.

---

## 3. Design alternatives

### (a) Status quo — `validate_assignment=False`, construction-time `model_validator(mode="after")`

Pydantic re-runs validators only on `__init__` and `model_validate`. Per-attribute assignment is unvalidated. Migration ships invariants as `@model_validator(mode="after")` methods.

**Catches**: `from_dict` round-trips with bad data; plans constructed by hand in tests; states loaded from disk where the persisted snapshot already violates an invariant.

**Does NOT catch**: any in-memory mutation that drives I1/I2/I9 out of sync. Validator never runs again until next save+reload. **Hole 1 demonstration**: identical bug re-introduces. A future PR that adds `state.status = "running"` in `record_approval_result` and removes the line that sets `pending_approval_request = None` passes every test that doesn't save+reload between assertions.

**Cost**: zero refactor. Honest evaluation: a no-op against the actual failure mode.

### (b) Funnel through transition methods on `ExecutionState`

Every coupled-field mutation in Category B becomes a method on `ExecutionState`. Direct attribute writes to `status`, `pending_approval_request`, `completed_at` (when terminal), and `takeover_records` (when going to `paused-takeover`) become forbidden by convention and (later) by Pydantic field privacy.

```python
state.transition_to_approval_pending(*, phase_id: int, requester: str) -> None
state.clear_approval_pending() -> None
state.transition_to_running(*, from_status: Literal["approval_pending","feedback_pending","gate_pending","budget_exceeded","paused-takeover","pending"]) -> None
state.transition_to_failed(*, reason: str, completed_at: str | None = None) -> None
state.transition_to_complete() -> None
state.transition_to_cancelled() -> None
state.transition_to_gate_pending() -> None
state.transition_to_feedback_pending() -> None
state.transition_to_budget_exceeded() -> None
state.transition_to_paused_takeover(*, takeover_record: dict) -> None
state.resume_from_budget() -> None
state.advance_phase(*, set_status_running: bool = False) -> None  # move from PhaseManager
```

Each method: assert pre-condition on current `status` (raise `IllegalStateTransition`); atomically update status + coupled siblings; assert post-condition before returning.

**Hole 1 demonstration**: structurally impossible.

```python
# Before:
state.status = "running"   # forgot to clear pending_approval_request → Hole 1.

# After:
state.status = "running"   # AttributeError or convention-violation lint.
state.transition_to_running(from_status="approval_pending")
# inside the method: asserts status == "approval_pending"; sets pending_approval_request = None;
# sets status = "running"; asserts pending_approval_request is None and status == "running".
```

Enforcement against direct writes:
- **Phase 1 (convention)**: AST lint rule. A small visitor in `tests/conftest.py` walks imported source and rejects any `Attribute(value=Name('state'), attr='status')` on the LHS of an `Assign` outside `models/execution.py` and `tests/`.
- **Phase 2 (Pydantic)**: convert `status`, `pending_approval_request`, `completed_at`, `takeover_records` to private fields (`_status: str = PrivateAttr(...)`) with read-only properties.

**Cost**:
- ~21 status-write call sites rewritten.
- ~3 `pending_approval_request` writes fold into status methods.
- ~14 `completed_at` writes rewritten.
- ~2 `takeover_records` couplings rewritten.
- ~12 new methods on `ExecutionState`.

Estimate: ~50 call-site rewrites, ~12 new methods, ~250 LOC added on `execution.py`, ~100 LOC removed across executor/worker/CLI/API. **2-4 days** including tests for every transition.

### (c) `validate_assignment=True` with lenient validators

Pydantic re-runs validators on every set. Validators permit transient inconsistency inside a context manager:

```python
with state.transition():
    state.status = "running"
    state.pending_approval_request = None
# validator runs on context exit
```

**Problems**:
- Every list `.append()` triggers full-model revalidation (~200 list mutations per execution).
- "Lenient" requires either a flag or context manager — same call-site change as (b) without the type-system payoff.
- Transient inconsistency permitted by design. A bug that exits the context manager before writing the second field STILL triggers the validator, but at the wrong line, far from the cause. Worse debugging than (b).

**Hole 1 demonstration**: caught at context-manager exit, not at the offending line. Better than (a). Worse than (b). **Footgun confirmed.**

### (d) Hybrid: critical fields via methods, scalars stay direct

Only `status`, `pending_approval_request`, `completed_at`, and `takeover_records` move behind transition methods. All other mutations (Category A scalars, Category D appends) stay direct.

Same correctness guarantee as (b) for I1, I2, I9. Smaller refactor.

**Hole 1 demonstration**: identical to (b). Cannot recur.

**Cost**:
- ~21 status-write rewrites.
- ~3 `pending_approval_request` writes fold.
- ~14 `completed_at` writes fold.
- ~12 new methods.
- Category A scalars stay direct.
- Category D appends stay direct.

Estimate: ~38 call-site rewrites, ~12 new methods, ~150 LOC added, ~80 LOC removed. **1.5-3 days.**

Difference from (b): (b) folds Category A scalars into methods "for consistency" at the cost of more refactor. (d) accepts that Category A is genuinely uncoupled.

---

## 4. Recommendation

**Adopt alternative (d) — hybrid funnel.**

1. **Closes the actual logical gap.** Status quo is a stopgap because the failure mode (in-memory desync of coupled fields) is exactly the failure mode construction-time validators don't see. Moving status-cluster mutations behind transition methods makes desync structurally impossible.

2. **Catches I2 and I9, not just I1.** Hole 1 was the visible symptom of a class. Twelve `state.status = "failed"` sites currently fail to set `completed_at`. They are bugs, just less visible. (b) and (d) fix them as a side effect; (a) and (c) do not.

3. **Builds on a pattern already in the codebase.** `phase_manager.py:advance_phase()` is the existence proof. The State Pattern in `states.py` is a partial version — owns the status flip but not the audit-row write. (d) is the natural completion: if the state class is the only thing allowed to mutate `status`, the leaks close.

4. **Not a 1-month change.** ~1.5-3 days for the recommended slice.

5. **Rejects (b) on the marginal scalars.** Folding `run_cumulative_spend_usd += spend` into `state.add_run_spend(spend)` would buy zero correctness.

6. **Rejects (c) outright.** Footgun in this codebase.

**What (d) leaves on the table** (honesty):
- `step_results[idx] = result` at `executor.py:1929` (in-place replace by index). Coupled internally to `StepResult.status`. (d) does not address it because the coupling is local to one function.
- Plan-graph mutations already funnelled through `amend_plan`. No further work needed.
- Nested `interaction_history.append`. Local to step result; no cross-field invariant.

---

## 5. Sequencing with the Pydantic migration

**Recommendation: split (d) into a "before" slice and a "during" slice.**

### Before Phase 1 (leaf-type conversions): the convention-enforcement slice — half a day

Land transition methods on the existing `@dataclass ExecutionState`. They work on dataclasses; nothing in (d) requires Pydantic. Add the AST lint rule. Rewrite the 21 status-write sites + 14 `completed_at` sites + 3 pending-approval-request sites.

This is independent of the Pydantic migration. Doesn't delay it. **De-risks Phase 1**: by the time Phase 1 runs, status mutation has exactly one syntactic shape, which is the one Pydantic field-privacy can lock down trivially.

### During Phase 1 or Phase 2 (when `ExecutionState` becomes Pydantic): the field-privacy slice — half a day inside the migration window

Convert `status`, `pending_approval_request`, `completed_at`, `takeover_records` to `PrivateAttr` with read-only properties. Direct writes from outside become `AttributeError`. Lint rule from "before" slice can be deleted.

### Why not after?

"After" leaves I1/I2/I9 enforceable only via reload. Every Pydantic-migration PR between Phase 0 and the (d) refactor risks reintroducing a Hole-1-class bug because the migration itself is rewriting touched code. **The migration is the most dangerous moment to be running on convention.** Land convention first; then migration cannot reintroduce the bug because call sites no longer have a way to express it.

**Risk left on the table** if "after" is chosen: new bugs of the form "Pydantic validator passes at construction; in-memory mutation desyncs; next save+reload raises." That's a runtime crash on resume, not a silent data hole. Loud failure mode. But operator-facing failure is "execution refuses to resume after a CLI restart" — exactly the workflow `baton execute resume` exists for — so the loud failure happens at the worst possible time.

---

## 6. Estimate and first-slice proof

### Recommended approach (d) — total

| Item | Count |
|---|---|
| Call-site rewrites | ~38 (21 status + 14 completed_at + 3 pending-approval-request) |
| New methods on `ExecutionState` | ~12 |
| New custom exception (`IllegalStateTransition`) | 1 |
| New AST lint rule | ~30 LOC, one file |
| Tests for transition methods | ~12 unit tests, one per method |
| Net LOC change | +150 in `models/execution.py`, –80 across `executor.py`/`worker.py`/`api/`/`cli/` |

**Total effort: 1.5-3 developer-days for "before" slice; +0.5 days for "during" slice.**

### First slice (proves the design before committing the rest)

Smallest meaningful: I1 only. Implement three methods — `transition_to_approval_pending`, `clear_approval_pending`, `transition_to_running` (with `from_status` literal). Rewrite the 4 sites in B1. Add unit tests. **Half a day.**

If clean, scale to I2 (terminal flips) and I9 (paused-takeover). If not, the design is wrong and (d) gets revisited before touching the other 30 sites.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| AST lint rule produces false positives on test code | Scope rule to non-test source; allow direct writes in `tests/` and `models/execution.py`. |
| Engine code paths calling `state.status = ` from inside `ExecutionEngine` methods need to call `state.transition_to_X()` | Desired direction. Seam becomes `ExecutionEngine` → `ExecutionState`-method, not `ExecutionEngine` ↔ `ExecutionState`-fields. |
| Some transition methods need engine-only context (e.g. terminal-failed wants the bead-id) | Pass as argument. State method doesn't call into engine; engine builds audit row, then passes the dict. State stays pure data layer. |
| Pydantic `PrivateAttr` makes `to_dict`/`from_dict` round-trip awkward | `PrivateAttr` is read inside `model_dump` if model writes a custom serializer; existing `to_dict` already does field-by-field copy. `from_dict` constructs with the private attr passed by name — unaffected. |
| API consumers reading `state.status` over HTTP rely on it being a string | Properties expose it as a string. No public API change. |

---

## 8. Implementation guidance

### "Before" slice (no Pydantic dependency)

1. Add `IllegalStateTransition(RuntimeError)` to `agent_baton/core/engine/errors.py`. Carries `from_status`, `to_status`, `task_id`, `context`.

2. Add the 12 transition methods directly on `ExecutionState`. Each:
   - Reads `self.status`.
   - Validates against allowed `from_status` set; otherwise raises `IllegalStateTransition`.
   - Atomically updates coupled fields.
   - Writes new status last.
   - Optionally asserts post-conditions.

3. Move `advance_phase` from `phase_manager.py` to `ExecutionState`; keep `PhaseManager.advance_phase()` as thin pass-through.

4. Replace 38 call sites. Order: B1 (4 sites) first to kill Hole 1 specifically, then B3 (14 sites), then B2/B4/I9.

5. Add `tests/models/test_execution_state_transitions.py` with one test per method.

6. Add `tests/static/test_no_direct_status_writes.py` — AST lint test. Walks `agent_baton/**/*.py` excluding `models/execution.py`, parses with `ast`, fails if any `Assign` node's targets include `Attribute(value=Name(id="state" or "_state"), attr="status")` (or `pending_approval_request`, `completed_at`, `takeover_records`).

7. Update `agent_baton/core/engine/CLAUDE.md`: "Direct mutation of `state.status` (and coupled fields) is forbidden outside `models/execution.py`. Use `transition_to_*` methods. Static gate enforces this."

8. Update `docs/internal/pydantic-migration-mutation-audit.md` status: `Superseded by docs/internal/state-mutation-proposal.md`.

### "During Pydantic" slice

9. When `ExecutionState` converts to `BaseModel`, change `status`, `pending_approval_request`, `completed_at`, `takeover_records` to `PrivateAttr`. Add read-only `@property`. Delete AST lint test — type system enforces it.

---

## 9. Blocking questions for the user

1. **Scope of the lint rule**: also reject direct writes to `current_phase` and `current_step_index`? Coupled (I4-I6) but already correctly funnelled — belt-and-suspenders.

2. **Where does the API-route status write live?** `api/routes/executions.py:411` flips `state.status = "failed"` when a REST stop endpoint is called. Does that become `state.transition_to_failed(reason="api-stopped", ...)` on the engine's state, or should the REST endpoint go through a CLI-equivalent engine method (`engine.stop_execution(...)`) that owns the transition? Recommendation: latter, because mutating engine state directly from a route handler is a layering smell — but slightly bigger change. Confirm or push back.

3. **`PrivateAttr` versus `Field(frozen=True)` with a setter method**: in the Pydantic-promotion slice, prefer private attrs + properties (cleaner public API but less idiomatic Pydantic) or frozen fields + setter methods (idiomatic but mutates field-write semantics for downstream serializers)? Lean private attributes; flag if revisit.
