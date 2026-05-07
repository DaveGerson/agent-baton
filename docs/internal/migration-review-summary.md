# Migration Review Summary — SQLite parity, ExecutionRecord base, state-mutation funnel

**Status**: Draft
**Date**: 2026-05-07
**Branch**: `claude/review-execution-planning-KTQqv` @ `b453297`
**Reviews**:
  - [sqlite-parity-proposal.md](sqlite-parity-proposal.md)
  - [result-hierarchy-proposal.md](result-hierarchy-proposal.md)
  - [state-mutation-proposal.md](state-mutation-proposal.md)

**Bottom line**: All three proposals are sound in their core diagnoses. None are wrong. Two are over-scoped relative to the user's stated "small slice, safety-first" constraint. The integration plan recommends shipping the prototyped Pydantic leaf-tier and SQLite Phase A immediately, deferring the state-mutation refactor to a single I1 first-slice (not all three invariants at once), and explicitly putting OCC and the Pydantic queen-tier behind a "we have evidence we need this" gate before doing them.

---

## 1. Per-proposal critique

### 1.1 SQLite parity proposal

**Sound?** Yes, in core. Spot-checked the citations:

- `schema.py:43` `SCHEMA_VERSION = 35`: confirmed.
- `schema.py:1185` executions table DDL: confirmed (there is also a duplicate at `schema.py:2394` the proposal does not flag — see Gaps).
- `connection.py:80-92` WAL/FK/busy_timeout PRAGMAs: confirmed.
- `sqlite_backend.py:88` save_execution: confirmed. The INSERT lists exactly 9 columns (`task_id, status, current_phase, current_step_index, started_at, completed_at, updated_at, pending_gaps, resolved_decisions`) — every other field listed in the proposal is genuinely absent from the upsert.
- `sqlite_backend.py:347` load_execution: confirmed; reconstruction at line 546 passes only the same 9 columns.
- The 11 enumerated missing fields exist on `ExecutionState` (lines 1450-1529 in current file, **not** 1408-1457 as the proposal says — see Gaps) and are emitted by `to_dict` (lines 1572-1617).
- Migrator's `_insert_execution` at `migrate.py:671`: confirmed.

**Gaps:**

1. **Stale line numbers for `ExecutionState`.** The proposal cites lines 1408-1457; actual is 1450-1529. The shift comes from the ExecutionRecord base class added at line 97 in commit `06fbc15` (the result-hierarchy prototype). This is harmless mechanically but signals the proposal was written before the base class merged into HEAD. Easy follow-up: re-anchor by class name, not line number.
2. **Schema duplication.** `schema.py` has TWO `CREATE TABLE IF NOT EXISTS executions` blocks: one at 1185 (the one the proposal targets) and one at 2394 (the project-local schema mirror). Phase A's recipe says "update PROJECT_SCHEMA_DDL at line 1185" but that only patches one of the two. Whoever ships Phase A must update both — or the per-project schema for fresh installs will silently diverge from the migration path. The proposal does not call this out.
3. **`steps_ran_in_place` folding.** The proposal folds `steps_ran_in_place` (`dict[step_id, str]`) into `step_worktrees(ran_in_place_reason TEXT NULL)` as a column. But line 1487 in `execution.py` shows `steps_ran_in_place` is keyed independently — a step that ran in place may NOT have a `step_worktrees` row at all (because worktree creation failed). Folding it under that PK loses the separating semantics. The proposal needs either a separate child table for these, or `step_worktrees` must accept rows with `worktree_path = ""` to host the in-place reason. The proposal's table schema covers this with `DEFAULT ''` columns, but it should be made explicit, including a regression test.
4. **`speculations` doubles as `_phase_retries` scratchpad.** The proposal calls this out (Section 2.1) and proposes splitting `phase_retries_json` out — but does not enumerate what happens to existing dual-use writers. `phase_manager.py:310-314`, `executor.py:3371`, `resolver.py:441` all walk the dict expecting either shape. Phase B needs a code-side migration of these readers in the same commit, not just a schema migration.
5. **Cross-machine stance is correct but file-backend-on-NFS path is not addressed.** Today's file backend on a network FS works (one process at a time); the `BATON_DB_PATH` warning lands cleanly only after Phase D. Worth a note that "the file backend remains your only safe option for cross-machine until further notice."

**Recommendation justified?** Phase A: yes. It buys 6 of 11 fields with low risk and validates the migration discipline before the harder phases. The "smallest viable forward" framing is honest.

Phases B-D: rationale solid but cost framing is optimistic. ~250 LOC for `sqlite_backend.py` in Phase B understates the Pydantic-migration cleanup that has to happen in the same code: every `to_dict()` site touched by Phase B becomes a Pydantic call site immediately afterward, doubling the diff per file. The proposal acknowledges this in §5 but treats it as "mechanical" — it's not, because the SQLite reads also reconstruct nested objects (`PendingApprovalRequest.from_dict(...)`) whose semantics change under Pydantic. Estimate is ~2x what the doc says.

**Failure mode:** A migration v36 that adds NOT NULL columns with defaults but mismatches the project-schema DDL at `schema.py:2394`. Fresh installs and migrated installs diverge silently; only surfaces when an integration test forces both code paths against the same row. **Mitigation**: every migration must touch BOTH the migration block AND `PROJECT_SCHEMA_DDL`, with a test that compares schemas between the two paths.

### 1.2 Result-hierarchy proposal

**Sound?** Yes, and the prototype works as advertised.

**Verification of the prototype:**

- `agent_baton/models/execution.py` lines 97-137 contain `ExecutionRecord` with `model_config = ConfigDict(extra="ignore", validate_assignment=False, arbitrary_types_allowed=False)` and inherited `to_dict()` / `from_dict()`.
- `GateResult` at line 1245 inherits from `ExecutionRecord`. No `to_dict` or `from_dict` overrides; relies on the base.
- `pytest tests/models/test_execution_roundtrip.py::TestGateResult tests/models/test_execution_sqlite_roundtrip.py -v` → **21 passed** (matches the proposal's claim).
- Manual probes confirm: `model_config` inherits cleanly in Pydantic v2 (subclass `model_config` returns the same dict the base set); `extra="ignore"` works on direct construction (`GateResult(phase_id=1, ..., _future='ignored')` succeeds); `validate_assignment=False` allows direct attribute mutation, including type-broken assignments (`g.passed = "not a bool"` succeeds — see Gap 1).

**Gaps:**

1. **`validate_assignment=False` is genuinely permissive** — it allows ANY assignment, including type-violating ones. `g.passed = "not a bool"` does not raise. This matches the dataclass behavior the proposal claims, but the proposal does not say "this is identical to today's dataclass laxness." Worth stating explicitly: this proposal does not buy you type safety on field writes, only on construction. If a future PR wants type-on-write enforcement, it has to go through the state-mutation funnel proposal, not this one.
2. **`from_dict` returns `ExecutionRecord` per its annotation, not the subclass.** Line 135: `def from_dict(cls, data: dict[str, Any]) -> ExecutionRecord`. Pyright will flag this on `GateResult.from_dict(...)` call sites that expect `GateResult`. Two options: typevar-based return (`from_dict(cls: type[T], data: dict) -> T`) or just `Self`. The proposal acknowledges the Pyright concern in §8.5 but does not fix it. Easy to patch when the next leaf type lands.
3. **Nested re-hydration story is incomplete.** Section 4.3 sketches `StepResult` keeping its own `from_dict` "until `TeamStepResult` and `InteractionTurn` are also Pydantic." But the prototype does not demonstrate this with a live conversion. The first leaf-type slice the user agrees to should be EXACTLY `PendingApprovalRequest` + `InteractionTurn` + `TeamStepResult` together — they have no nested-object dependencies. Then `GateResult` (already done). Then `StepResult` after, because at that point its nested types are Pydantic and no override is needed. The proposal's order ("seven mechanical conversions") doesn't sequence by dependency.
4. **`ExecutionRecord` says nothing about `ExecutionState` itself.** This is in scope per the question I was asked to answer. The proposal explicitly declares `MachinePlan` and `ExecutionState` out of scope (Section 8.4: "keep `ExecutionRecord` reserved for results"), which means the queen tier of the migration has no documented base. That's a hole — it should not silently grow a separate one. Recommend the proposal add: "`ExecutionState` and `MachinePlan` will inherit directly from `BaseModel` with their own model_config; they are NOT result types; the base is reserved for leaf records."

**Recommendation justified?** Yes for the result-hierarchy slice. The "single base, no inherited fields" decision is the right call — every promoted-field option breaks at least one subclass's on-disk shape. The `Outcome`/`Decision` two-tier alternative is correctly rejected.

**Failure mode:** A subclass forgets that the inherited `to_dict()` does NOT omit empty collections. `StepResult` and `ConsolidationResult` MUST override; the proposal flags this but the enforcement is documentation, not a test. Recommend: a test that loops every `ExecutionRecord` subclass and asserts each has either an explicit `to_dict` override or its empty-collection fields produce identical output to the dataclass version. One-time forcing function so future leaf types don't regress.

### 1.3 State-mutation proposal

**Sound?** Yes — and verifiably more so than the audit doc it supersedes. Re-grepped HEAD:

- 30 distinct `state.status = ...` writes total (counting the typo-tolerant grep).
- Outside `states.py`: 23 sites — proposal says "21 leaks" (excluding `phase_manager.py`'s 2 centralised flips). **Confirmed.**
- Pydantic-migration audit doc cites `state.status = "complete"` at executor.py:3227; current line is 3472. Audit is genuinely stale. **State-mutation proposal's claim that the audit is "approximately right but stale" is correct.** Audit should be marked Superseded.
- I1 sites (the Hole 1 invariant): I confirmed the four B1 sites the proposal lists (executor.py:4398/4419, executor.py:6286, executor.py:4086+4099/4101/4121, states.py:124). The 4086→4121 path also includes a `_save_execution` at line 4106 with `status` still `approval_pending` (because the flip is at 4121, after a `_amend_from_feedback` that itself saves). That's an in-flight invariant violation the proposal mentions abstractly but does not specifically enumerate.
- B3 (terminal `failed`): proposal says "six of fourteen" don't set `completed_at`. Actual count is 13 `state.status = "failed"` writes; 7 of them do not set `completed_at` (executor 1468, 2982, 3126, 4099, 6799; worker 488, 505; states.py 135, 142, 149, 204; api/routes 411). Closer to 11/13 than 6/14. Either the audit or my count is off (likely my count counts states.py which the proposal excludes). The shape of the claim — "Hole 1 has class-mates" — is correct regardless.

**Gaps:**

1. **The `_save_execution` at line 4106 issue.** When `record_approval_result` takes the "approve-with-feedback" branch, it saves state at 4106 with `status == "approval_pending"` and `pending_approval_request == None` (cleared at 4086). That's an I1 violation visible in the persisted state for the duration of `_amend_from_feedback`. If the process crashes between 4106 and the reload at 4114, **the next resume sees an inconsistent state.** The proposal flags I1 as Hole-1-class but doesn't flag this specific code path as currently broken. It is. The "before" slice should fix this site as part of the I1 first-slice.
2. **AST lint scope.** Section 8.6 proposes `tests/static/test_no_direct_status_writes.py` walking `agent_baton/**/*.py`. But CLI command files ALSO write status (`execute.py:1482` cancel verb). The proposal acknowledges this in Section 9.2 but punts the design decision to the user. Recommendation: the lint should be opt-in per file (use a magic comment `# noqa: state-mutation` or a registered allowlist). Otherwise the first thing the lint does is catch `cli/commands/execution/execute.py:1482` and force a refactor of the cancel verb in the same slice.
3. **`PrivateAttr` interaction with `to_dict`/`from_dict`.** Section 7 risk says "PrivateAttr is read inside `model_dump` if model writes a custom serializer; existing `to_dict` already does field-by-field copy." Mostly true, but Pydantic v2's `model_dump()` does NOT include `PrivateAttr` by default. The Pydantic-promotion slice will have to either (a) keep custom `to_dict` and read the underscored attr explicitly, or (b) use `@computed_field` and a property that exposes the private as a model field. The proposal lists (a) implicitly. (b) is more idiomatic. This is a real design decision that should be made before the "during" slice starts, not during.
4. **Cost estimate for the "before" slice is plausibly low.** "1.5–3 days, ~38 call-site rewrites." Each rewrite needs: (i) the new transition method on `ExecutionState`, (ii) a unit test per method, (iii) potentially a fix to the calling logic if the new precondition assertion catches a real bug, (iv) regression test. The 12 transition methods × ~10 test cases each = 120 unit tests minimum. 3 days is the floor, not the ceiling.
5. **No analysis of how the funnel interacts with the State Pattern.** `states.py` already has `ExecutingPhaseState`, `GatePendingPhaseState`, etc., that own status flips. The proposal says (Section 1, Category C) the State Pattern is "the right shape but leaky" but does not say whether the new transition methods live on `ExecutionState` (proposed) or on the state classes (existing pattern). If both, there's duplicate ownership. If only on `ExecutionState`, what happens to the state classes? They become unnecessary. The proposal needs to pick one and explain how the existing pattern collapses or coexists.

**Recommendation justified?** Mostly. Alternative (d) hybrid is genuinely better than (a) status-quo or (c) `validate_assignment=True`. The "before/during" sequencing is the right call (close the bug structurally before the migration touches the code).

But I disagree with the breadth of the first slice: I1 + I2 + I9 in one go is not "small slice." The proposal's own §6 even says "smallest meaningful: I1 only." That's the slice that should ship first. I2 (terminal `completed_at`) and I9 (paused-takeover) are real but not Hole-1-class urgent — they are "wrong duration in retrospective" and "tacit invariant the resolver relies on." Defer.

**Failure mode:** New transition methods that assert preconditions on `from_status` discover that the actual call sites violate them (i.e. real bugs they were silently hiding). The slice ships, tests pass, but production execution discovers a precondition assertion the test fixture didn't cover. **Mitigation**: every transition method needs an integration test that drives a full `baton execute` cycle through the transition, not just a unit test on the method itself.

---

## 2. Cross-proposal dependencies and contradictions

### 2.1 `validate_assignment=False` × `PrivateAttr`

The result-hierarchy proposal sets `validate_assignment=False` on `ExecutionRecord`. The state-mutation proposal proposes `PrivateAttr` on `status`/`pending_approval_request`/`completed_at`/`takeover_records`.

**Compatible.** `PrivateAttr` is independent of `validate_assignment`: it makes the field unwriteable from outside the class regardless. The state-mutation proposal is about `ExecutionState`, not the result types — `ExecutionState` is explicitly out of scope for the result-hierarchy base. So they don't even touch the same fields.

**One caveat:** if the state-mutation proposal's "during" slice ever wants to use `Field(frozen=True)` on a result type instead of `PrivateAttr` (its open question §9.3), `validate_assignment=False` would let the frozen field be reassigned anyway. They contradict in spirit but not in current Pydantic v2 mechanics — frozen fields are enforced separately. Worth a one-line note in the result-hierarchy doc.

### 2.2 SQLite Phase C × State-mutation hybrid funnel

SQLite Phase C says "lands AFTER Pydantic Phase 1." State-mutation proposal says "before slice lands BEFORE Phase 1, during slice lands DURING Phase 1." So:

- State-mutation "before" lands first (no Pydantic dep).
- Pydantic Phase 1 leaf-type lands.
- State-mutation "during" lands (PrivateAttr promotion).
- Pydantic Phase 2/3 (mid-tier and ExecutionState) lands.
- SQLite Phase C lands.

**The OCC retry pseudocode in SQLite §3.2 sets `state.status` directly** (`UPDATE executions SET status=? ... WHERE version=?`). That's SQL, not Python — it does not touch the transition-method funnel. The funnel only governs Python-side mutation of the in-memory state. The retry loop in the engine, however, will re-load and re-apply: "re-load → re-apply delta → retry the save." That re-apply step IS Python-side and DOES need the transition methods. The SQLite proposal does not explicitly say this; it should.

**Concretely:** the OCC retry handler needs to call `state.transition_to_X(...)` to re-apply the conflicting write, which means OCC depends on the funnel. This makes the dependency one-way — funnel before OCC — which matches the proposed sequencing. **Compatible, but the SQLite proposal should flag this dependency explicitly.**

### 2.3 `_loaded_version: int` × PrivateAttr

The SQLite proposal puts `_loaded_version: int` on `ExecutionState` as `Field(exclude=True, default=0)`. State-mutation proposal puts `status` etc. behind `PrivateAttr`.

**`_loaded_version` should also be `PrivateAttr`, by the same logic.** It's a transient field that should not appear in `to_dict` / `model_dump()` and should not be writeable from outside `ExecutionState`. The SQLite proposal's `Field(exclude=True)` is a softer form of the same idea. PrivateAttr is the cleaner version. The two proposals do not contradict, but they pick slightly different mechanisms. Pick PrivateAttr; document it.

### 2.4 Result-hierarchy says nothing about `ExecutionState`

This was raised in 1.2 Gap 4 above. The result-hierarchy explicitly punts to a separate `PlanModel(BaseModel)` for plan types and says nothing about `ExecutionState`. **`ExecutionState` therefore inherits directly from `BaseModel`** when its turn comes. That's fine, but the omission needs to be made explicit in writing.

### 2.5 Circular dependencies

None. The dependency graph is:

```
Result-hierarchy base (done, in HEAD)
   ↓
Pydantic leaf-type slice (next)
   ↓
State-mutation "during" slice (PrivateAttr promotion) ←─┐
   ↓                                                    │
Pydantic mid-tier (PlanStep, PlanGate, MachinePlan)     │
   ↓                                                    │
Pydantic queen-tier (ExecutionState)                    │
   ↓                                                    │
SQLite Phase C (OCC, depends on _loaded_version field)──┘

State-mutation "before" slice (transition methods + lint) — independent of Pydantic
SQLite Phase A (scalar columns) — independent of Pydantic
SQLite Phase B (collection tables) — independent of Pydantic; benefits from leaf-type slice
SQLite Phase D (file-backend deprecation) — depends only on Phase A reaching parity for "good enough"
```

**No cycles.** The state-mutation "before" slice and SQLite Phase A and Pydantic leaf-type slice are all independent — they can ship in any order.

### 2.6 The audit doc

The state-mutation proposal claims the audit is stale. **Verified.** The audit's line numbers for `state.status = "complete"` (executor.py:3227) are wrong; current is 3472. Audit was generated 2026-05-06; the branch has had several more commits since (Hole 1 fix, ExecutionRecord base, etc.).

**Recommendation:** mark the audit `Superseded by docs/internal/state-mutation-proposal.md` per the convention in `docs/internal/CLAUDE.md`. Do this NOW, regardless of which slice lands.

---

## 3. Risk reordering

The naive ordering proposed in the prompt is:

1. State mutation "before" slice (half a day, no Pydantic dep)
2. SQLite Phase A (low risk, scalar columns)
3. Pydantic Phase 1 leaf-type slice (3 types)
4. SQLite Phase B (collection tables)
5. Pydantic remaining leaf types
6. State mutation "during" slice (PrivateAttr promotion)
7. Pydantic Phase 2 (mid-tier)
8. Pydantic Phase 3 (ExecutionState)
9. SQLite Phase C (OCC)
10. SQLite Phase D (file-backend deprecation)

**Critique:**

- **Step 1 is too big as written.** The state-mutation "before" slice covers all three of I1/I2/I9 plus the AST lint plus 12 transition methods plus 38 site rewrites. That's 1.5-3 days, not "half a day." The proposal's own §6 even labels the **first sub-slice as "I1 only" — half a day.** Move that to step 1; defer the I2/I9 expansion until later (or drop entirely; see §4).

- **Step 2 is right.** SQLite Phase A is genuinely low risk and adds 6 fields with low diff.

- **Step 3 should be split.** The Pydantic leaf-type slice as proposed is "PendingApprovalRequest + InteractionTurn + GateResult" but `GateResult` is already done. The remaining slice is `PendingApprovalRequest + InteractionTurn` (2 types, no nested dependencies). Then `TeamStepResult`. Then `ApprovalResult` + `FeedbackResult` + `PlanAmendment` (independent). Then `StepResult` last because it depends on `TeamStepResult` and `InteractionTurn` being Pydantic. Then `ConsolidationResult`. **Five sub-slices, each one independently mergeable.**

- **The Hole-6 plan-graph integrity validator is missing.** Earlier-cycle work (the holes audit) flagged a plan-graph integrity issue. The state-mutation proposal lists invariants I1-I10 but they are all `ExecutionState` invariants. Plan-graph invariants (PlanStep ID uniqueness, phase reachability, gate-step adjacency) belong to `MachinePlan` and should land as `model_validator(mode="after")` validators when `MachinePlan` becomes Pydantic — which is Pydantic Phase 2 mid-tier. **Schedule the Hole-6 plan-graph validator as part of Phase 2 mid-tier, not earlier.** Earlier doesn't help (the dataclass is too lax to express the invariant cleanly); later means a release window with a known gap.

- **I1/I2/I9 audit-driven validators land where?** The proposal has them as transition methods (Python), not Pydantic validators. They're enforced at mutation time, not at construction. **Construction-time validators should ALSO exist** — at the very least an `@model_validator(mode="after")` on `ExecutionState` that asserts I1, so a state loaded from disk with a violating shape raises rather than silently runs. This is the "loud failure on resume" case the state-mutation proposal calls out as the worst-case for "after" sequencing. The validator should land **with the Pydantic queen-tier conversion (step 8)**, not earlier. It's a belt-and-suspenders for the funnel.

- **Step 6 ("during" slice) timing is right** but its exact landing window depends on which Pydantic Phase converts `ExecutionState`. If `ExecutionState` is Phase 3, "during" lands with Phase 3 (step 8). The proposal says "during Phase 1 or Phase 2" — but `status` is on `ExecutionState`, which is Phase 3. So during = Phase 3. The naive ordering already has step 6 before step 8; **swap them: do "during" as part of step 8, not before.**

- **Step 9 (OCC) should be gated by demand.** See §4.

- **Step 10 (file-backend deprecation) only blocks on Phase A reaching parity for the "good enough" threshold.** It does NOT need Phase B-C. After Phase A, file backend can be marked deprecated; deletion can wait for after C. Move step 10 forward to right after step 4 (Phase B), with deletion still gated on Phase C completion.

**Reordered:**

1. State-mutation I1 first-slice ONLY (half a day, no I2/I9)
2. Mark audit doc Superseded (5 minutes)
3. SQLite Phase A — scalar columns + DDL parity test (1 day)
4. Pydantic leaf-type slice 1: `PendingApprovalRequest` + `InteractionTurn` (½ day)
5. Pydantic leaf-type slice 2: `TeamStepResult` (½ day)
6. SQLite Phase B — collection tables (1.5 days)
7. Pydantic leaf-type slice 3: `ApprovalResult` + `FeedbackResult` + `PlanAmendment` (1 day)
8. Pydantic leaf-type slice 4: `StepResult` (½ day, depends on 4+5)
9. Pydantic leaf-type slice 5: `ConsolidationResult` (½ day, depends on 4)
10. File-backend deprecation Stage 1 (warning only, ½ day)
11. **DECISION GATE** — does the user actually want Pydantic Phase 2/3 + OCC? See §4 "do less" critique.
12. (If yes) Pydantic Phase 2 — `PlanStep`/`PlanGate`/`PlanPhase`/`MachinePlan` + Hole-6 graph validator (2-3 days)
13. (If yes) State-mutation expansion: I2 + I9 transition methods + AST lint enforcement
14. (If yes) Pydantic Phase 3 — `ExecutionState` + state-mutation "during" slice (PrivateAttr) + I1/I2/I9 model_validators (3-4 days)
15. (If yes) SQLite Phase C — OCC + `_loaded_version` PrivateAttr (1-2 days)
16. (If yes) File-backend deprecation Stages 2-3

---

## 4. The honest "do less" critique

The user has a small-slice mandate. Here is what each proposal could legitimately defer.

### 4.1 SQLite parity could stop at Phase A

**What's saved:** 4 days of Phases B-D. Concurrency story stays "single process per task_id" — same as today. PMO subscription path remains poll-based, unchanged.

**What's lost:** Lossy SQLite for collection-shaped fields means resume-from-SQLite-only does not preserve `takeover_records`, `selfheal_attempts`, `speculations`, `delivered_knowledge`, `step_worktrees`. Today this is hidden because the file backend dual-writes. **If a user has SQLite-only enabled, resume loses those fields silently.** Phase A does not fix that.

**Honest assessment:** Phase A is a half-measure. It buys fewer-fields-lost on resume, not zero. If the user is OK with continuing dual-write to the file backend indefinitely (and the cost is small), Phase A alone is defensible. If the user wants the file backend gone, Phase A is not enough. **Recommend Phase A + Phase B together as the "complete enough to deprecate file backend" slice.** Phases C and D can wait for evidence-based demand.

### 4.2 State-mutation could be I1-only, not I1+I2+I9

**What's saved:** ~2 days. AST lint stays narrow (only `pending_approval_request` and `status` flips that are coupled to it). No need to refactor the 11 `failed`-flip sites or the 2 paused-takeover sites in this round.

**What's lost:** I2 (terminal `completed_at`) bugs continue to exist. Symptom: retrospectives report duration `completed_at - started_at` where `completed_at` is `""`, so the retrospective shows "Inf duration" or skips the row. Already happening today; not a new regression. I9 (paused-takeover invariant) continues to be tacit. Symptom: the resolver's check at `resolver.py:236-240` may misfire if a takeover is set up out-of-order.

**Honest assessment:** I1 is the urgent one because it caused Hole 1, a real audit-trail data loss bug. I2 is a quality issue; I9 is a tacit-invariant issue that has not produced a reported bug. **Land I1 only as the first slice.** Schedule I2/I9 for "after Pydantic queen-tier" where they become trivial via `@model_validator(mode="after")`.

### 4.3 Pydantic past leaf types is debatable

The user's most concrete benefit is `MachinePlan` validation at LLM-output time. Today the planner produces a `MachinePlan` from LLM output and uses it without strong validation; bad plans surface later as runtime errors. Phase 2 (MachinePlan as Pydantic) gives `validate_call`-level guard at the boundary.

`ExecutionState` is internal — it's never received from outside, only constructed, mutated, persisted, reloaded. Pydantic's value-add is mostly "validate-on-load" (the `from_dict` path). The dataclass already has hand-rolled `from_dict` doing the same thing. **The marginal value of `ExecutionState` as Pydantic is small** unless OCC's `_loaded_version` PrivateAttr is wanted, which only matters if you want SQLite Phase C, which only matters if you want multi-process safety per task_id.

**Three plausible terminal states:**

A) **Stop after leaf types.** ExecutionState stays a dataclass forever. SQLite Phase A+B reaches parity. File backend deprecated and removed. Hole-6 plan-graph integrity becomes a hand-rolled validator on `MachinePlan` instead of a `@model_validator`. Saves ~6-8 days.

B) **Go to Phase 2 (MachinePlan) but stop there.** ExecutionState stays dataclass. Hole-6 lands cleanly. SQLite Phase A+B+D. No OCC. Saves ~3-4 days vs. full plan.

C) **Full plan.** ExecutionState is Pydantic, OCC works, multi-process per task_id is safe. Original SQLite vision met. Cost as listed.

**Recommendation:** stop at (A) or (B) unless and until a real user files a "two processes ate each other's state" bug. The work is real but the demand is hypothetical. The user can revisit if `BATON_WORKTREE_ENABLED=1` is broken under concurrency in the field.

### 4.4 Stop point I'd actually advocate for

(B) terminal state. Reasons:
- Phase 2 (MachinePlan) is where Hole-6 plan-graph integrity earns its keep.
- LLM-produced plans are the highest-risk input boundary; that's where strong typing pays.
- ExecutionState as Pydantic costs days for marginal benefit unless OCC is in scope.
- OCC adds a real failure mode (false-positive retries) for a hypothetical demand.

**Do less proposal: 1+3+4+5+6+7+8+9+10+12 from the reordering above. Stop there. Flag step 14+15 as "deferred until concurrency demand surfaces."**

---

## 5. Recommended integration plan

Each slice is ≤1 day, independently mergeable, with a clear gate (test that must pass) before proceeding. Sequenced safest-first.

| # | Slice | Touches | Gate | Effort |
|---|-------|---------|------|--------|
| 1 | Mark `pydantic-migration-mutation-audit.md` Superseded; cross-link from state-mutation proposal | docs/internal | none (doc only) | 5 min |
| 2 | State-mutation I1 first-slice: 3 transition methods (`transition_to_approval_pending`, `clear_approval_pending`, `transition_to_running` with `from_status` literal) on `ExecutionState`; rewrite the 4 B1 sites; **fix the in-flight save at executor.py:4106** as part of this; unit tests per method | `models/execution.py`, `core/engine/executor.py`, `core/engine/states.py`, new errors entry, new test file | `pytest tests/models/test_execution_state_transitions.py tests/test_approval_and_amendments.py` | ½ day |
| 3 | SQLite Phase A: migration v36 (6 scalar columns), DDL parity in BOTH `schema.py:1185` and `schema.py:2394`, save/load plumbing, parity test, "intentionally lossy" comment scoped down | `core/storage/schema.py`, `core/storage/sqlite_backend.py`, `core/storage/migrate.py`, `tests/models/test_execution_sqlite_roundtrip.py` | `pytest tests/storage tests/models/test_execution_sqlite_roundtrip.py` | 1 day |
| 4 | Pydantic leaf-slice 1: `PendingApprovalRequest` + `InteractionTurn` to `ExecutionRecord`; `default_factory` replaces `__post_init__` for timestamps; Pyright `Self` return on `from_dict` | `models/execution.py`, golden tests untouched | `pytest tests/models/test_execution_roundtrip.py tests/models/test_execution_sqlite_roundtrip.py` | ½ day |
| 5 | Pydantic leaf-slice 2: `TeamStepResult` to `ExecutionRecord` | `models/execution.py` | same as above | ½ day |
| 6 | SQLite Phase B: migrations v37-v40 (JSON columns + 4 child tables); extend save/load; **migrate `_phase_retries` readers in same commit**; explicit handling of `steps_ran_in_place` separation | `core/storage/*`, `core/engine/phase_manager.py`, `core/engine/executor.py`, `core/engine/resolver.py` | full SQLite parity test + a regression test driving an in-flight execution to ensure no field is dropped on resume | 1.5 days (SPLIT into v37+v38 = 1 day, v39+v40 = ½ day if too big) |
| 7 | Pydantic leaf-slice 3: `ApprovalResult` + `FeedbackResult` + `PlanAmendment` to `ExecutionRecord` | `models/execution.py` | same | ½ day |
| 8 | Pydantic leaf-slice 4: `StepResult` to `ExecutionRecord` (depends on slices 4 & 5 for nested types) | `models/execution.py` | same | ½ day |
| 9 | Pydantic leaf-slice 5: `ConsolidationResult` + `FileAttribution` to `ExecutionRecord` | `models/execution.py` | same | ½ day |
| 10 | File-backend deprecation Stage 1: `DeprecationWarning` from `FileStorage.__init__`; doc updates | `core/storage/file_backend.py`, docs | existing tests pass with new warning suppressed | ¼ day |
| | --- **HARD GATE: USER DECISION** --- | does the user want Phase 2 (MachinePlan as Pydantic + Hole-6 graph validator) and beyond? | | |
| 11 | Pydantic Phase 2: `PlanStep`/`PlanGate`/`PlanPhase`/`MachinePlan` to Pydantic; `@model_validator(mode="after")` for Hole-6 plan-graph integrity (no two phases share a step ID, every step has a plan path, gate steps adjacency rules); LLM-output validation entry-point in planner | `models/execution.py`, `core/engine/planner.py`, `core/engine/planning/*` | `pytest tests/engine/planning tests/models/test_execution_roundtrip.py` | 2-3 days |
| | --- **STOP POINT IF (B) IS THE ANSWER** --- | | | |
| 12 | State-mutation expansion: I2 + I9 transition methods; rewrite the ~14 `completed_at` sites; AST lint test (allowlist `cli/commands/execution/execute.py` if needed) | `models/execution.py`, `core/engine/*`, `core/runtime/worker.py`, `api/routes/executions.py`, new test | `pytest -k transition_or_invariant`; AST-lint test passes | 1-1.5 days |
| 13 | Pydantic Phase 3: `ExecutionState` to Pydantic; State-mutation "during" slice (PrivateAttr promotion + properties); `@model_validator(mode="after")` for I1/I2/I9 belt-and-suspenders | `models/execution.py`, every consumer of `ExecutionState` | full `tests/` sweep | 2-3 days |
| 14 | SQLite Phase C: OCC `version` column, `_loaded_version: PrivateAttr` on `ExecutionState`, CAS update, `ConcurrentModificationError`, retry handler in executor | `core/storage/*`, `core/engine/executor.py`, `core/engine/errors.py` | new test driving two concurrent saves with disjoint deltas | 1-2 days |
| 15 | File-backend deprecation Stages 2-3: snapshot-only mode; `baton execute export`; remove from factory | `core/storage/*`, `cli/commands/execution/`, docs | full sweep | 1 day |

**Top-line:** slices 1-10 are the genuinely safe slice. Stop there unless the user says go further. Slices 11-15 cost another 7-10 days for benefits that are real but not yet demanded.

---

## Files referenced

- `/home/user/agent-baton/agent_baton/models/execution.py` — `ExecutionRecord` at line 97, `GateResult` at 1245, `ExecutionState` at 1421, `to_dict` at 1572, `from_dict` at 1620.
- `/home/user/agent-baton/agent_baton/core/storage/sqlite_backend.py` — `save_execution` at line 88 with the 9-column INSERT at 122-150, `load_execution` reconstruction at line 546.
- `/home/user/agent-baton/agent_baton/core/storage/schema.py` — `SCHEMA_VERSION = 35` at line 43, `MIGRATIONS` dict at line 46, two `executions` DDL blocks at lines 1185 and 2394.
- `/home/user/agent-baton/agent_baton/core/storage/connection.py` — WAL/FK/busy_timeout PRAGMAs at lines 80-92.
- `/home/user/agent-baton/agent_baton/core/storage/migrate.py` — `_insert_execution` at line 671.
- `/home/user/agent-baton/agent_baton/core/engine/executor.py` — 17 `state.status =` writes; the I1 seam at lines 4086-4121 with the in-flight-save problem at 4106.
- `/home/user/agent-baton/agent_baton/core/engine/states.py` — 7 `state.status =` writes (lines 119-204); ExecutingPhaseState at line 71+.
- `/home/user/agent-baton/agent_baton/core/engine/phase_manager.py` — `advance_phase` at line ~283 (centralised, the existence proof for transition-method approach).
- `/home/user/agent-baton/agent_baton/core/runtime/worker.py` — 2 `_state.status = "failed"` writes at lines 488, 505.
- `/home/user/agent-baton/agent_baton/api/routes/executions.py` — `state.status = "failed"` at line 411.
- `/home/user/agent-baton/agent_baton/cli/commands/execution/execute.py` — `state.status = "cancelled"` and `state.completed_at = ...` at lines 1482-1483.
- `/home/user/agent-baton/tests/models/test_execution_roundtrip.py` — 71 tests, all passing.
- `/home/user/agent-baton/tests/models/test_execution_sqlite_roundtrip.py` — 18 tests, all passing.
- `/home/user/agent-baton/docs/internal/sqlite-parity-proposal.md` — proposal under review.
- `/home/user/agent-baton/docs/internal/result-hierarchy-proposal.md` — proposal under review.
- `/home/user/agent-baton/docs/internal/state-mutation-proposal.md` — proposal under review.
- `/home/user/agent-baton/docs/internal/pydantic-migration-mutation-audit.md` — stale; should be marked Superseded.
