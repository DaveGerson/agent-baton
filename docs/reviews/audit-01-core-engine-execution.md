# Audit: Core Engine & Execution

**Date**: 2026-04-30
**Auditor**: architect
**Scope**: `agent_baton/core/engine/*.py` (excluding `planning/`), `agent_baton/core/exec/*.py`, `agent_baton/models/execution.py`, `agent_baton/core/engine/protocols.py`, `agent_baton/core/engine/states.py`

## Executive Summary

The core engine is architecturally sound and has undergone a disciplined refactoring (005b) that separated stateless resolution from stateful mutation. The resolver/state-handler/phase-manager decomposition is the strongest aspect of the codebase -- it makes the state machine verifiable and testable. The biggest risk is the executor itself: at 5700+ lines with 40+ methods, it has become a God class that accumulates every subsystem integration (beads, worktrees, souls, swarm, compliance, telemetry, knowledge, context harvesting, handoff synthesis, self-heal, speculation, takeover) via lazy imports and defensive try/except blocks. This works but creates a fragile center of gravity where a single file touches nearly every subsystem in the project.

## Dimension Scores

| # | Dimension | Score | One-Line Verdict |
|---|-----------|-------|------------------|
| 1 | Code Quality Improvement | B | Strong patterns in resolver/states/helpers; executor itself violates the very principles they established |
| 2 | Acceleration & Maintainability | C | New contributors face a 5700-line executor with 40+ methods and wave-numbered feature gates |
| 3 | Token/Quality Tradeoffs | B | Knowledge dedup, prompt routing by step_type, and bead budgets are well-tuned; some redundant reads |
| 4 | Implementation Completeness | B | All 9 ActionTypes wired end-to-end; self-heal dispatch is a placeholder hook (TODO at line 3104) |
| 5 | Silent Failure Risk | C | Pervasive try/except swallowing in 20+ locations; dual-write divergence detectable only via log grep |
| 6 | Code Smells | C | executor.py is a textbook God class; feature-flag functions are copy-pasted; deep nesting in record_step_result |
| 7 | User Discoverability | B | Protocol docstrings are excellent; env-var surface is documented but scattered across 8+ module-level functions |
| 8 | Extensibility | B | Protocol-based contracts enable alternative engines; but executor's monolithic init blocks extension without forking |

## Detailed Findings

### Dimension 1: Code Quality Improvement -- B

**Strengths:**

- The 005b refactoring was well-executed. `resolver.py` (442 lines) is a pure function from `ExecutionState` to `ResolverDecision` with zero I/O, zero mutation, zero imports from the executor. This is a textbook separation of concerns. The `DecisionKind` enum (21 values) provides exhaustive coverage of the state space.

- `_executor_helpers.py` enforces its own import boundary ("MUST NOT import from executor") and contains only pure read-only functions. Every function documents its edge-case behavior (empty input, parse error, not-found). This is the quality bar the rest of the engine should meet.

- `states.py` implements the state-handler pattern cleanly. Each handler has exactly one responsibility (the small mutation epilogue) and raises `RuntimeError` on illegal transitions. The `ExecutingPhaseState.handle()` method at line 92-157 is a model of exhaustive, documented dispatch.

- `phase_manager.py` has a single mutating method (`advance_phase`, 4 lines of mutation) with four pure evaluation methods. Zero collaborators. This is exactly the right granularity.

- `models/execution.py` uses consistent `to_dict`/`from_dict` patterns throughout. The `PlanGate.from_dict` (line 415) accepting both `"gate_type"` and `"type"` shows practical robustness against LLM output variance.

**Weaknesses:**

- `executor.py` at 5700+ lines contradicts every principle the refactoring established. The `__init__` method (lines 373-633, 260 lines) constructs 15+ optional collaborators with nested try/except blocks. `record_step_result` (lines 1784-2378, 594 lines) handles interacting status, token estimation, session scanning, spillover detection, bead signal extraction, bead feedback, deviation extraction, knowledge gap detection, context harvesting, event publication, OTel spans, and worktree fold-back -- all in a single method.

- `StepResult.from_dict` (line 944-958) mutates its input via `data.pop()` before passing remaining fields through `**{k: v for k, v in data.items() if k in cls.__dataclass_fields__}`. This is fragile: the pop-then-filter pattern means the order of field extraction matters, and adding a new field to the dataclass could silently be swallowed by the `**` expansion without appearing in the constructed object if it was popped earlier.

### Dimension 2: Acceleration & Maintainability -- C

**Strengths:**

- `protocols.py` is superb onboarding material. The `ExecutionDriver` protocol with 15 methods, each with a 3-5 line docstring explaining the behavioral contract, gives a new contributor the complete mental model of the engine in 293 lines.

- The `_STEP_STATUS_RANK` dict (executor.py:882-887) and the reconciliation logic in `_reconcile_states` are well-commented with clear "why" explanations.

**Weaknesses:**

- Wave-numbered comments (`Wave 1.3`, `Wave 2.1`, `Wave 2.2`, `Wave 3.1`, `Wave 3.2`, `Wave 4.1`, `Wave 5.1`, `Wave 5.2`, `Wave 5.3`, `Wave 6.1 Part A/B/C`, `Wave 6.2`) appear 50+ times in executor.py. A new contributor has no index of what these waves mean or when they landed. The comments reference design docs (`bd-86bf`, `bd-1483`, `bd-9839`, `bd-2b9f`, `bd-d975`) that are not discoverable without full codebase search.

- Eight feature-flag functions (lines 155-206: `_worktree_enabled`, `_takeover_enabled`, `_selfheal_enabled`, `_speculate_enabled`, `_souls_enabled`, `_swarm_enabled`) are nearly identical copy-paste. Each reads an env var and checks against `("0", "false", "False", "no")`. The only variation is the default (`"1"` for worktree/takeover, `"0"` for the rest). This is a textbook DRY violation.

- The `_dispatch_action` method (lines 5370-5616, 246 lines) has 5 different prompt-builder paths selected by `step_type` with nested try/except blocks for context harvesting, handoff synthesis, and bead selection. A new contributor modifying dispatch behavior must trace through all 5 paths.

- `ExecutionState` has 28 fields (lines 1301-1388), many guarded by `getattr(self, "...", default)` patterns in `to_dict()` (lines 1455-1464) despite being defined as class fields. This defensive coding suggests the fields were added iteratively without confidence that deserialization would handle them, creating cognitive load for anyone reading the code.

### Dimension 3: Token/Quality Tradeoffs -- B

**Strengths:**

- Session-level knowledge deduplication (`_apply_session_dedup` in dispatcher.py:262-314) prevents re-inlining the same document across multiple steps. This is a significant token saver for multi-step plans.

- Step-type routing in `_dispatch_action` sends consulting steps through a lightweight prompt (~3-5K tokens) and task steps through a minimal prompt (~1-3K tokens), versus the full delegation prompt (~10-15K tokens). Good discrimination.

- The bead selector (bead_selector.py) enforces a hard cap of 5 beads and a 4096-token budget per dispatch, preventing unbounded memory injection.

- Spillover handling (`_load_handoff_outcome`, lines 1718-1782) caps handoff content at 64KB and includes a path-traversal security guard (line 1750).

**Weaknesses:**

- `_SIGNALS_BLOCK` is injected into every delegation prompt (dispatcher.py:599) even though most agents never emit any signals. This adds ~150 tokens per dispatch. For a 10-step plan, that is 1500 tokens of boilerplate that will rarely be used.

- The `build_continuation_prompt` method (dispatcher.py:618-724) emits `_KNOWLEDGE_GAPS_LINE`, `_BEAD_SIGNALS_LINE`, and `_FLAG_SIGNALS_LINE` as separate entries, but since the backward-compat aliases (line 62-64) point all three to the same `_SIGNALS_BLOCK`, the consolidated block is emitted three times in the continuation prompt. This triples the signal documentation overhead (~450 tokens wasted per interactive continuation).

- `TaskViewSubscriber.__call__` (executor.py:259-268) replays the entire event history on every event publication to re-project the view. For long-running tasks with hundreds of events, this is O(n^2) total work. The NOTE at line 237 acknowledges this is unused in production.

### Dimension 4: Implementation Completeness -- B

**Strengths:**

- All 9 `ActionType` values (DISPATCH, GATE, COMPLETE, FAILED, WAIT, APPROVAL, FEEDBACK, INTERACT, SWARM_DISPATCH) have corresponding handler paths in `_apply_resolver_decision`.

- The `record_step_result` method handles every step status variant including the complex multi-turn interaction protocol with `INTERACT_COMPLETE` signal detection (line 1851), max-turn auto-completion (line 1868), and turn counting.

- Crash recovery is robust: `resume()` implements a 3-layer fallback (SQLite -> file -> reconciliation) with per-step status promotion that handles split-brain from prior write failures.

- Commit consolidation (`_should_consolidate`, line 3148-3166) correctly skips when there are failures or when git_strategy is "none".

**Weaknesses:**

- `_enqueue_selfheal` (lines 3057-3107) is explicitly a placeholder. The method logs intent but does nothing: "TODO(Wave 5.2 full dispatch): wire the SelfHealEscalator dispatch into next_action." The `SelfHealEscalator` class exists in `selfheal.py` but is never called from the executor's main action loop. Users who set `BATON_SELFHEAL_ENABLED=1` get compliance logging but no actual self-healing behavior.

- `record_policy_approval` (lines 1302-1340) has a bug: line 1329 calls `state.failed_step_ids.add(step_id)`, but `failed_step_ids` is a `@property` that returns a computed `set` from `step_results`. Adding to this computed set has no effect -- the step is not actually marked as failed. The `state.status = "failed"` on line 1330 does execute, so the execution fails, but the step itself remains in whatever status it was in, creating an inconsistent state where `state.status == "failed"` but no step has `status == "failed"`.

### Dimension 5: Silent Failure Risk -- C

**Strengths:**

- The dual-write pattern in `_save_execution` (lines 650-689) logs a detailed WARNING with task_id, status, and per-step status summary when SQLite fails and the file fallback activates. This makes split-brain visible.

- `_require_execution` (lines 765-781) provides actionable error messages with recovery commands when state is missing.

- `_verify_save_on_start` (lines 1490-1533) reads back from both backends after the initial save, detecting the case where neither backend persisted the state. This catches disk-full and schema-mismatch scenarios immediately rather than deferring failure to the next CLI call.

**Weaknesses (Silent Failure Inventory):**

1. **Bead signal extraction** (executor.py:2055-2086): `except Exception as _bead_exc: _log.debug(...)`. If bead parsing is fundamentally broken (e.g., regex change, model schema drift), every step's bead signals are silently dropped. The `debug` level means operators with default log configuration will never see this. **Risk: HIGH** -- bead memory degrades silently.

2. **Context harvesting** (executor.py:2271-2291): `except Exception as _hv_exc: _log.debug(...)`. Same pattern. If the context_harvester table schema drifts, all context learning stops silently. **Risk: MEDIUM.**

3. **Knowledge telemetry** (executor.py:4475-4505): `except Exception as exc: logger.debug(...)`. F0.4 telemetry that feeds the learning pipeline is swallowed at debug level. **Risk: MEDIUM.**

4. **Compliance chain writer** (executor.py:1032-1039): `except Exception as exc: _log.warning(...)`. Compliance audit entries are best-effort. For a system that claims tamper-evident audit logs, having the writer silently fail undermines the guarantee. **Risk: HIGH** for regulated environments.

5. **BeadStore init** (executor.py:534-562): Two nested try/except blocks (outer for BeadStore, inner for SoulRouter). If `BeadStore` construction fails, `self._bead_store = None` silently, and all bead operations degrade. There is no mechanism to detect that beads are unavailable short of checking `baton beads list` returns empty. **Risk: MEDIUM.**

6. **WorktreeManager GC thread** (executor.py:600-606): A daemon thread runs `gc_stale()` at engine init. If it raises, the thread dies silently. No health check, no telemetry. **Risk: LOW** (GC is best-effort by design).

7. **`ContextHarvester` internal connection access** (executor.py:2278, 5521): Both call `self._storage._conn()` -- accessing a private method on the storage backend. If the storage backend changes its connection management (e.g., adds pooling or context managers), these calls will break silently. **Risk: MEDIUM.**

### Dimension 6: Code Smells -- C

**God class: `ExecutionEngine`** (executor.py, 5700+ lines, 40+ methods). This is the single most significant code smell in the domain. The class has the following responsibilities:
- State machine transitions (start, next_action, resume, complete)
- Step/gate/approval/feedback result recording
- Prompt dispatch routing
- Worktree lifecycle management
- Bead memory integration
- Compliance audit trail writing
- Telemetry and tracing
- Knowledge gap detection and resolution
- Context harvesting
- Handoff synthesis
- Self-heal escalation queuing
- Speculative pipelining
- Developer takeover management
- Budget enforcement
- Policy enforcement
- CI gate dispatch
- Team step management
- Event bus publication
- Retrospective generation
- Usage logging
- Split-brain reconciliation
- Commit consolidation

**Feature-flag copy-paste** (executor.py:155-206): Six functions with identical structure. These should be a single parameterized helper.

**Deep nesting in `record_step_result`**: The worktree fold-back block (lines 2171-2260) reaches 6 levels of nesting.

**Duplicated `_utcnow` functions**: At least 5 separate `_utcnow()` definitions exist across the domain.

**`getattr` guards on dataclass fields** (executor.py:1455-1464): Defensive coding despite fields being defined in the dataclass.

**Dead code**: The `TaskViewSubscriber` is acknowledged as "not consumed by any production subsystem" but is kept for tests.

### Dimension 7: User Discoverability -- B

**Strengths:**

- `ExecutionDriver` (protocols.py) is the best discoverability artifact in the codebase. A user wanting to understand "what can the engine do?" reads 293 lines and knows the complete API surface.

- Error messages are consistently actionable. `_require_execution` tells the user `"Run 'baton execute start' to begin, or 'baton execute list' to find existing ones."`

**Weaknesses:**

- The 8 env vars controlling subsystem behavior are scattered across module-level functions with no central registry. `CLAUDE.md` documents 6 env vars but misses `BATON_WORKTREE_ENABLED`, `BATON_TAKEOVER_ENABLED`, `BATON_SELFHEAL_ENABLED`, `BATON_SPECULATE_ENABLED`, and `BATON_SOULS_ENABLED`.

- The `SelfHealEscalator` class exists with full escalation-tier logic but is not wired into the action loop. A user reading `selfheal.py` would believe self-heal works. Only reading `_enqueue_selfheal` in executor.py reveals the TODO.

### Dimension 8: Extensibility -- B

**Strengths:**

- The `ExecutionDriver` protocol enables alternative engine implementations.
- The `_state_handlers` dispatch table is extensible.
- The `ActionResolver` is injected as `self._resolver`, and tests can monkeypatch it.
- `PhaseManager` is a zero-arg singleton with no collaborators.

**Weaknesses:**

- The `ExecutionEngine.__init__` (260 lines) is the extension bottleneck. There is no plugin mechanism, registry, or hook system.
- The `dispatcher.py` signal block is hardcoded.
- The bead subsystem integration is a sprawl of inline try/except blocks rather than a single interface.

## Critical Issues (Fix Now)

- **`record_policy_approval` writes to a computed property** (executor.py:1329): `state.failed_step_ids.add(step_id)` has no effect because `failed_step_ids` is a `@property` returning a new `set`. A policy rejection marks the execution as failed but the step remains in its prior status, creating an inconsistent state. Fix: create a `StepResult` with `status="failed"` for the rejected step_id and append to `state.step_results`.

- **Triple signal block in continuation prompts** (dispatcher.py:716-719): `_KNOWLEDGE_GAPS_LINE`, `_BEAD_SIGNALS_LINE`, and `_FLAG_SIGNALS_LINE` are all aliases to the same `_SIGNALS_BLOCK`. The continuation prompt emits all three, producing 3x the signal documentation (~450 wasted tokens per interactive turn). Fix: emit `_SIGNALS_BLOCK` once.

## Important Issues (Fix Soon)

- **Self-heal is advertised but not wired** (executor.py:3104): `_enqueue_selfheal` logs intent but has a TODO for the actual dispatch. Either wire the escalator or remove the feature flag and document it as unimplemented.

- **executor.py is a 5700-line God class**: The next decomposition targets should be: (a) a `WorktreeLifecycle` class; (b) a `DispatchBuilder` class; (c) a `CompletionHandler` class.

- **Compliance audit writes are best-effort** (executor.py:1038-1039): For HIGH/CRITICAL risk plans, compliance audit entries silently failing undermines the system's regulated-domain claims.

- **Env-var documentation gap**: 5 feature-flag env vars are undocumented in `CLAUDE.md`'s environment variables table.

## Improvement Opportunities (Fix Later)

- Extract feature-flag functions into a shared `_env_flag()` helper.
- Deduplicate `_utcnow()` across 5+ modules.
- Remove or gate `TaskViewSubscriber`.
- `StepResult.from_dict` should not mutate input.
- Add observability for try/except swallowed failures.
- Reduce `_SIGNALS_BLOCK` overhead for agents that never emit signals.

## Silent Failure Inventory

| Location | What Fails | Risk | Detection |
|---|---|---|---|
| executor.py:2055-2086 | Bead signal extraction | HIGH | `baton beads list` returns empty for tasks that should have beads |
| executor.py:1032-1039 | Compliance audit write | HIGH (regulated) | Missing entries in `compliance-audit.jsonl` |
| executor.py:534-562 | BeadStore/SoulRouter init | MEDIUM | `self._bead_store is None` silently; no health check |
| executor.py:2271-2291 | Context harvesting | MEDIUM | Agents lose context continuity without visible error |
| executor.py:4475-4505 | Knowledge telemetry | MEDIUM | Learning pipeline stalls silently |
| executor.py:2278, 5521 | `_storage._conn()` private access | MEDIUM | Will break if storage backend refactors connection management |
| executor.py:600-606 | Worktree GC daemon thread | LOW | Stale worktrees accumulate |
| executor.py:1329 | `failed_step_ids.add()` no-op | HIGH | Policy rejection records wrong state |
| dispatcher.py:716-719 | Triple signal block emission | LOW | 450 wasted tokens per interactive turn |
