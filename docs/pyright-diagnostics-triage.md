# Pyright Diagnostics Triage

> Collected 2026-03-24 during documentation pass. Line numbers may have
> shifted slightly due to docstring insertions by parallel agents — verify
> against current source before fixing.

## Summary

| Severity | Count | Files |
|----------|-------|-------|
| Error (type violations) | ~60 | 14 files |
| Warning (unused symbols) | ~48 | 19 files |

---

## Errors — Type Violations

### `agent_baton/core/storage/sqlite_backend.py` — Undefined names (CRITICAL)

Multiple model types used in type annotations but never imported:

| Line (approx) | Symbol | Issue |
|---------------|--------|-------|
| 69, 74, 216, 238 | `ExecutionState` | Not defined |
| 394, 416, 430, 437, 456, 1354, 1376, 1465, 1487 | `MachinePlan` | Not defined |
| 424, 446, 475 | `StepResult` | Not defined |
| 475, 497, 535 | `GateResult` | Not defined |
| 495, 517, 563 | `ApprovalResult` | Not defined |
| 514, 536, 587 | `PlanAmendment` | Not defined |
| 623, 650 | `Event` | Not defined |
| 697, 753 | `TaskUsageRecord` | Not defined |

**Root cause**: Likely missing `from agent_baton.models.execution import ...` or
names are used in `TYPE_CHECKING` blocks that Pyright can't resolve.

**Fix**: Add proper imports (possibly under `if TYPE_CHECKING:` to avoid circular imports).

---

### `agent_baton/core/engine/executor.py` — Type assignability

| Line (approx) | Issue |
|---------------|-------|
| 249, 265, 268, 284 | `EventHandler` expects `-> None` return, but handler returns `Path` |
| 322, 328, 334, 513 | `ExecutionState` not assignable to `ExecutionState` (duplicate import path?) |
| 352, 355 | Return type `ExecutionState | None` not assignable to itself |
| 369 | `"log"` not a known attr of `None` (optional member access) |
| 396 | `"log_event"` not a known attr of `None` |
| 414 | `"save"` not a known attr of `None` |
| 479 | `MachinePlan` not assignable to `MachinePlan` |
| 526, 549 | More type assignability issues |
| 1787 | `str | None` passed where `str` expected (`task_type` in `build_delegation_prompt`) |

**Root cause**: The `ExecutionState`/`MachinePlan` self-assignability errors suggest
the same class is being imported from two different paths (e.g., re-export vs
direct import), creating distinct types at the Pyright level. The optional member
access errors suggest variables that could be `None` aren't guarded.

**Fix**:
1. Ensure a single canonical import path for `ExecutionState` and `MachinePlan`.
2. Add `None` guards before accessing `.log`, `.log_event`, `.save`.
3. Change `EventHandler` type or wrap handlers to return `None`.
4. Guard `task_type` with `or ""` or a default.

---

### `agent_baton/core/orchestration/router.py` — Tuple type mismatch

| Line (approx) | Issue |
|---------------|-------|
| 247, 249, 251, 253 | `tuple[str | None, str | None]` not assignable to `tuple[str, str | None]` in `.get()` |

**Root cause**: The dict key type expects `str` in the first position, but the
code passes a potentially-`None` value.

**Fix**: Guard with `if stack is not None:` before the `.get()` calls, or cast.

---

### `agent_baton/api/routes/pmo.py` — list assigned to str

| Line (approx) | Issue |
|---------------|-------|
| 251, 260, 276, 300, 320, 334, 346, 360 | `list[str]` cannot be assigned to `str` in `__setitem__` |

**Root cause**: A dict typed as `dict[str, str]` is being assigned `list[str]` values.

**Fix**: Update the dict type annotation to `dict[str, str | list[str]]` or
restructure the data.

---

### `agent_baton/core/engine/planner.py` — Optional member access

| Line (approx) | Issue |
|---------------|-------|
| 1517, 1538 | `"evaluate"` not a known attr of `None` |

**Root cause**: Object could be `None` — missing guard.

**Fix**: Add `if obj is not None:` guard before `.evaluate()` calls.

---

### `agent_baton/core/learn/pattern_learner.py` — Type mismatch

| Line (approx) | Issue |
|---------------|-------|
| 374 | `KnowledgeGapRecord` not assignable to `KnowledgeGapRecord` |

**Root cause**: Same duplicate-import-path issue as `ExecutionState`.

**Fix**: Ensure single canonical import path.

---

### `agent_baton/cli/commands/execution/execute.py` — Type mismatch

| Line (approx) | Issue |
|---------------|-------|
| 363 | `MachinePlan` not assignable to `MachinePlan` (same duplicate path issue) |

**Fix**: Canonical import path.

---

### `agent_baton/cli/commands/execution/daemon.py` — Optional to required

| Line (approx) | Issue |
|---------------|-------|
| 182, 433 | `MachinePlan | None` passed where `MachinePlan` required |

**Fix**: Add `None` guard before calling `.start(plan)`.

---

### `agent_baton/core/observe/dashboard.py` — Protocol incompatibility

| Line (approx) | Issue |
|---------------|-------|
| 46 | `SqliteStorage` incompatible with `StorageBackend` protocol — `read_mission_log` returns `list[Unknown]` vs expected `str | None` |

**Root cause**: `SqliteStorage.read_mission_log` signature diverges from the
`StorageBackend` protocol.

**Fix**: Align `SqliteStorage.read_mission_log` return type with the protocol.

---

### `agent_baton/core/govern/spec_validator.py` — Callable vs class

| Line (approx) | Issue |
|---------------|-------|
| 516 | Expected class but received callable in `isinstance`-like check |

**Fix**: Review the check — may need `callable()` instead of `isinstance()`.

---

## Warnings — Unused Symbols

### Unused imports

| File | Line (approx) | Symbol |
|------|---------------|--------|
| `models/retrospective.py` | 33 | `datetime` |
| `models/retrospective.py` | 40 | `KnowledgeGap` |
| `models/pmo.py` | 18 | `datetime`, `timezone` |
| `api/routes/pmo.py` | 11 | `datetime`, `timezone` |
| `cli/commands/observe/usage.py` | 27 | `AgentUsageRecord` |
| `cli/commands/govern/compliance.py` | 31 | `RiskLevel` |
| `cli/commands/execution/plan_cmd.py` | 27 | `sys` |
| `core/observe/telemetry.py` | 25 | `field` |
| `core/observe/archiver.py` | 22 | `json` |
| `core/improve/recommender.py` | 39 | `AgentScorecard` |
| `core/runtime/worker.py` | 29 | `json` |
| `core/runtime/worker.py` | 35 | `LaunchResult` |
| `core/runtime/supervisor.py` | 26 | `signal_module` |
| `core/runtime/supervisor.py` | 34 | `EventPersistence` |
| `core/runtime/supervisor.py` | 35 | `project_task_view` |
| `core/engine/executor.py` | 44 | `PlanGate` |
| `core/engine/executor.py` | 51 | `KnowledgeGapSignal` |

### Unused variables

| File | Line (approx) | Symbol | Notes |
|------|---------------|--------|-------|
| `core/govern/classifier.py` | 69, 84, 90 | `_higher` | Intentional discard? |
| `cli/main.py` | 103, 114 | `_mod_name` | Loop variable |
| `cli/commands/execution/execute.py` | 413, 474, 485, 504 | `plan` | Destructured but unused |
| `core/engine/planner.py` | 1008, 1029, 1034 | `pattern`, `task_summary` | Destructured |
| `core/orchestration/knowledge_registry.py` | 153, 158, 162, 168 | `pack` | Loop variable |
| `core/orchestration/knowledge_registry.py` | 386, 391, 395 | `pack` | Loop variable |
| `core/orchestration/knowledge_registry.py` | 399, 404, 408 | `_body` | Intentional discard |
| `core/runtime/context.py` | 38 | `_SHARED_DIR` | Module-level constant |
| `core/runtime/daemon.py` | 121 | `supervisor` | Assigned but unused |
| `core/runtime/daemon.py` | 219 | `pending` | Assigned but unused |
| `core/runtime/supervisor.py` | 183 | `pending` | Assigned but unused |
| `core/events/bus.py` | 93 | `sub_id` | Loop variable |

### Unreachable code

| File | Line (approx) | Notes |
|------|---------------|-------|
| `core/runtime/worker.py` | 236 | Structurally unreachable |

### Dead code

| File | Line (approx) | Symbol |
|------|---------------|--------|
| `core/engine/executor.py` | 2225 | `_build_delegation_prompt` — entire function unreferenced |

---

## Recommended Fix Priority

### P0 — Blocking / Data Integrity
1. **`sqlite_backend.py` undefined names** — 10+ undefined type references.
   These may cause runtime `NameError` if the annotations are evaluated
   (e.g., `from __future__ import annotations` not present).

### P1 — Type Safety
2. **Duplicate import paths** — `ExecutionState`, `MachinePlan`, `ExecutionAction`
   self-assignability failures across executor.py, execute.py, pattern_learner.py.
   Fix by establishing a single canonical import.
3. **Optional member access** — `None` guards needed in executor.py, planner.py.
4. **Protocol alignment** — `SqliteStorage.read_mission_log` vs `StorageBackend`.

### P2 — Code Hygiene
5. **Unused imports** — Remove ~17 unused imports across 10 files.
6. **Unused variables** — Review and either use or prefix with `_`.
7. **Dead code** — Remove `_build_delegation_prompt` if truly unreferenced.
8. **Unreachable code** — Remove or fix control flow in worker.py:236.

### P3 — Type Annotation Precision
9. **`pmo.py` routes** — Fix dict value type from `str` to `str | list[str]`.
10. **`router.py`** — Guard `None` stack before tuple key construction.
11. **`daemon.py`** — Guard `None` plan before passing to `.start()`.

---

## Additional Diagnostics (discovered during docstring pass)

### `agent_baton/core/storage/sqlite_backend.py` — More undefined names

| Line (approx) | Symbol | Issue |
|---------------|--------|-------|
| 907, 1037 | `Retrospective` | Not defined |
| 1158, 1207 | `TaskTrace` | Not defined |
| 1255, 1293 | `LearnedPattern` | Not defined |
| 1320, 1356 | `BudgetRecommendation` | Not defined |
| 1385, 1432 | `MissionLogEntry` | Not defined |
| 623, 650 | `Event` | Not defined |

**Same root cause as above** — extends the P0 fix to cover 5 additional types.

### `agent_baton/core/storage/file_backend.py` — Return type mismatch

| Line (approx) | Issue |
|---------------|-------|
| 177, 178, 183, 184 | `list[TelemetryEvent]` returned where `list[dict]` expected |

**Fix**: Change return type annotation to `list[TelemetryEvent]` or
use `Sequence` (covariant) as Pyright suggests.

### `agent_baton/core/improve/loop.py` — Attribute access on `object`

| Line (approx) | Issue |
|---------------|-------|
| 143, 149, 164 | `experiment_id` not known on `object` |
| 51 | `TriggerConfig` unused import |

**Fix**: Type the variable properly instead of `object`.

### `agent_baton/core/storage/sync.py` — Operator on None

| Line (approx) | Issue |
|---------------|-------|
| 565, 587, 601 | Operator `/` not supported for `None` (path operation on optional) |
| 25 | `Sequence` unused import |
| 136 | `_TABLE_SPEC_BY_NAME` unused module variable |

**Fix**: Add `None` guard before path `/` operations.

### Additional unused imports/variables

| File | Line (approx) | Symbol |
|------|---------------|--------|
| `core/improve/experiments.py` | 33 | `datetime`, `timezone` |
| `core/storage/file_backend.py` | 269, 275 | `content` |
| `cli/commands/improve/anomalies.py` | 21 | `TriggerConfig` |

---

## Updated Fix Priority

### P0 — Blocking / Data Integrity
1. **`sqlite_backend.py` undefined names** — now **15+ types** undefined.
   This is the single highest-impact fix. Likely needs a consolidated
   `TYPE_CHECKING` import block.

### P1 — Type Safety (unchanged + additions)
2. Duplicate import paths (executor, execute, pattern_learner)
3. Optional member access (executor, planner, sync)
4. Protocol alignment (SqliteStorage vs StorageBackend)
5. `file_backend.py` return type (`list[TelemetryEvent]` vs `list[dict]`)
6. `loop.py` attribute access on `object`

### P2 — Code Hygiene (expanded)
7. ~22 unused imports across 13 files
8. Unused variables — review and prefix with `_`
9. Dead code — `_build_delegation_prompt`, unreachable worker.py:236
10. Unused module-level variables (`_TABLE_SPEC_BY_NAME`, `_SHARED_DIR`)
