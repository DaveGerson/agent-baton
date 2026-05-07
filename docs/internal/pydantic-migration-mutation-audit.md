# Pydantic Migration — Mutation Pattern Audit

Inventory of all in-place mutation patterns found in `agent_baton/` that
touch `ExecutionState`, `MachinePlan`, and related execution models.  This
is a pre-migration artifact for the `agent_baton/models/execution.py`
dataclass→Pydantic migration; it answers "what mutation patterns must keep
working after the types change?"

Generated from a grep pass against the worktree at commit HEAD of branch
`claude/review-execution-planning-KTQqv` on 2026-05-06.

**Scope**: production code only (`agent_baton/`).  Test code is excluded.

---

## Category 1 — Direct Attribute Set

Direct assignment to a scalar attribute on `ExecutionState` or a nested object.
Pydantic mutable models (`model_config = ConfigDict(frozen=False)`) handle
these without `validate_assignment=True` because scalar fields are not
validated on post-init mutation.

### `state.status = <str>`

| File | Line | Value |
|------|------|-------|
| `agent_baton/api/routes/executions.py` | 411 | `"failed"` |
| `agent_baton/cli/commands/execution/execute.py` | 1482 | `"cancelled"` |
| `agent_baton/core/engine/executor.py` | 1299 | `"failed"` |
| `agent_baton/core/engine/executor.py` | 2639 | `"gate_failed"` |
| `agent_baton/core/engine/executor.py` | 2718 | `"gate_pending"` |
| `agent_baton/core/engine/executor.py` | 2737 | `"failed"` |
| `agent_baton/core/engine/executor.py` | 2805 | `"paused-takeover"` |
| `agent_baton/core/engine/executor.py` | 2881 | `"failed"` |
| `agent_baton/core/engine/executor.py` | 3025 | `"paused-takeover"` |
| `agent_baton/core/engine/executor.py` | 3227 | `"complete"` |
| `agent_baton/core/engine/executor.py` | 3756 | `"failed"` |
| `agent_baton/core/engine/executor.py` | 3758 | `"running"` |
| `agent_baton/core/engine/executor.py` | 3767 | `"running"` |
| `agent_baton/core/engine/executor.py` | 4044 | `"approval_pending"` |
| `agent_baton/core/engine/executor.py` | 4276 | `"budget_exceeded"` |
| `agent_baton/core/engine/executor.py` | 4316 | `"running"` |
| `agent_baton/core/engine/executor.py` | 6122 | `"running"` |
| `agent_baton/core/engine/executor.py` | 6401 | `"failed"` |
| `agent_baton/core/engine/phase_manager.py` | 293 | `"running"` |
| `agent_baton/core/engine/phase_manager.py` | 326 | `"running"` |
| `agent_baton/core/engine/states.py` | 119 | `"gate_pending"` |
| `agent_baton/core/engine/states.py` | 124 | `"approval_pending"` |
| `agent_baton/core/engine/states.py` | 129 | `"feedback_pending"` |
| `agent_baton/core/engine/states.py` | 135 | `"failed"` |
| `agent_baton/core/engine/states.py` | 142 | `"failed"` |
| `agent_baton/core/engine/states.py` | 149 | `"failed"` |
| `agent_baton/core/engine/states.py` | 204 | `"failed"` |
| `agent_baton/core/runtime/worker.py` | 488 | `"failed"` |
| `agent_baton/core/runtime/worker.py` | 505 | `"failed"` |

**Migration note**: `status` is currently an unvalidated `str`.  A Pydantic
model with `validate_assignment=True` would validate on every assignment.
Given the number of distinct values (8+), prefer keeping `status` as a plain
`str` field (or a `Literal[...]` union) rather than an `Enum`, to avoid
breaking all assignment sites.  Do NOT add `validate_assignment=True` unless
all status values are covered by a literal.

### `state.current_step_index = <int>`

| File | Line | Note |
|------|------|------|
| `agent_baton/core/engine/phase_manager.py` | 291 | Reset to `0` on phase advance |
| `agent_baton/core/engine/phase_manager.py` | 323 | Set to skip-forward count |

### `state.completed_at = <str>`

| File | Line | Value |
|------|------|------|
| `agent_baton/cli/commands/execution/execute.py` | 1483 | ISO 8601 UTC string |
| `agent_baton/core/engine/executor.py` | 3228 | `_utcnow()` call |

### `state.consolidation_result = <ConsolidationResult>`

| File | Line | Note |
|------|------|------|
| `agent_baton/core/engine/executor.py` | 3328 | Assigned after consolidation run |

### `state.scope_expansions_applied = <int>`

| File | Line | Note |
|------|------|------|
| `agent_baton/core/engine/executor.py` | 3932 | Incremented with `+= 1` via getattr |

---

## Category 2 — List / Dict Mutation In Place

`list.append`, `list.insert`, `list[-1]`, and `dict[key] =` patterns on
collection fields.  Pydantic mutable models expose the underlying Python
`list`/`dict`, so `.append()` and item-assignment work unchanged.
No behavioural difference expected post-migration.

### `state.step_results.append(...)` / `state.step_results[idx] = ...`

| File | Line | Pattern |
|------|------|---------|
| `agent_baton/api/routes/pmo.py` | 1427 | `.append(skipped_result)` |
| `agent_baton/core/engine/executor.py` | 1793 | `.append(existing)` — re-append on interact update |
| `agent_baton/core/engine/executor.py` | 1929 | `[existing_idx] = result` — in-place replace by index |
| `agent_baton/core/engine/executor.py` | 1931 | `.append(result)` — new step result |
| `agent_baton/core/engine/executor.py` | 3976 | `.append(parent)` — team step synthetic parent |
| `agent_baton/core/storage/file_backend.py` | 112 | `.append(result)` |

### `state.gate_results.append(...)`

| File | Line | Pattern |
|------|------|---------|
| `agent_baton/core/engine/executor.py` | 2569 | `.append(gate_result)` |
| `agent_baton/core/storage/file_backend.py` | 118 | `.append(result)` |

### `state.approval_results.append(...)`

| File | Line | Pattern |
|------|------|---------|
| `agent_baton/core/engine/executor.py` | 3743 | `.append(approval)` |
| `agent_baton/core/storage/file_backend.py` | 124 | `.append(result)` |

### `state.amendments.append(...)`

| File | Line | Pattern |
|------|------|---------|
| `agent_baton/core/engine/executor.py` | 3839 | `.append(amendment)` |
| `agent_baton/core/engine/executor.py` | 6632 | `.append(amendment)` |
| `agent_baton/core/engine/executor.py` | 6741 | `.append(redispatch_amendment)` |
| `agent_baton/core/engine/executor.py` | 6932 | `.append(amendment)` |
| `agent_baton/core/storage/file_backend.py` | 130 | `.append(amendment)` |

### `state.feedback_results.append(...)`

| File | Line | Pattern |
|------|------|---------|
| `agent_baton/core/engine/executor.py` | 6080 | `.append(fb_result)` |

### `state.resolved_decisions.append(...)`

| File | Line | Pattern |
|------|------|---------|
| `agent_baton/core/engine/executor.py` | 6694 | `.append(decision)` |
| `agent_baton/core/engine/executor.py` | 6874 | `.append(decision)` |

### `state.pending_gaps.append(...)`

| File | Line | Pattern |
|------|------|---------|
| `agent_baton/core/engine/executor.py` | 6964 | `.append(signal)` |

---

## Category 3 — Nested Mutation

Mutations that go through the object graph: `state.plan.phases.insert(...)`,
`containing_phase.steps.append(...)`, `existing.interaction_history.append(...)`.
These traverse nested objects obtained by reference from `ExecutionState`.

Pydantic mutable models return the underlying Python list by reference, so
`.append()` / `.insert()` on the referenced list mutates the model in place —
identical behaviour to dataclass.

### `state.plan.phases.insert(...)` — plan amendment inserts new phases

| File | Line | Pattern |
|------|------|---------|
| `agent_baton/core/engine/executor.py` | 3824 | `state.plan.phases.insert(insert_idx + i, phase)` |

### `containing_phase.steps.append(...)` / `.insert(...)` — dynamic step injection

| File | Line | Context |
|------|------|---------|
| `agent_baton/core/engine/executor.py` | 6616 | Consulting step appended to containing phase |
| `agent_baton/core/engine/executor.py` | 6724 | Re-dispatch step appended after knowledge gap resolution |
| `agent_baton/core/engine/executor.py` | 6919 | Re-dispatch step appended after knowledge gap auto-resolution |
| `agent_baton/core/engine/planning/stages/enrichment.py` | 550 | `design_phase.steps.insert(0, capture_step)` during planning |
| `agent_baton/core/engine/planning/utils/phase_builder.py` | 247 | `phase.steps.append(...)` during plan building |
| `agent_baton/core/engine/planning/utils/phase_builder.py` | 344 | `phase.steps.append(...)` |
| `agent_baton/core/engine/planning/utils/phase_builder.py` | 357 | `phase.steps.append(...)` |
| `agent_baton/core/engine/strategies.py` | 1264 | `phase.steps.append(...)` |
| `agent_baton/core/engine/strategies.py` | 1361 | `phase.steps.append(...)` |
| `agent_baton/core/engine/strategies.py` | 1374 | `phase.steps.append(...)` |

### `step_result.interaction_history.append(...)` — multi-turn interaction append

| File | Line | Context |
|------|------|---------|
| `agent_baton/core/engine/executor.py` | 1812 | Append agent turn after interact dispatch |
| `agent_baton/core/engine/executor.py` | 1831 | Append human turn after interact resume |
| `agent_baton/core/engine/executor.py` | 1843 | Append agent turn on interact update |
| `agent_baton/core/engine/executor.py` | 5829 | Append initial agent turn |
| `agent_baton/core/engine/executor.py` | 6763 | Append agent turn on re-dispatch |

### `step_result.member_results.append(...)` — team step member collection

| File | Line | Context |
|------|------|---------|
| `agent_baton/core/engine/executor.py` | 3993 | `parent.member_results.append(member_result)` |

---

## Summary for Migration Agent

| Pattern category | Count | Pydantic mutable model impact |
|-----------------|-------|-------------------------------|
| Direct attribute set (`state.field = value`) | 34 | Safe with `frozen=False` (default). If `validate_assignment=True` is added, every `state.status = "..."` site must use a valid status value — validate the Literal coverage first. |
| List/dict mutation in place (`.append`, `[idx]=`) | 19 | Safe — Pydantic returns the underlying list by reference, same as dataclass. |
| Nested mutation (traversing the object graph) | 16 | Safe — same reason as above; nested Pydantic models also expose mutable lists. |

**Recommendation for the migration agent**: use `model_config = ConfigDict(frozen=False)` (the default for Pydantic BaseModel).  Do NOT enable `validate_assignment=True` on `ExecutionState` or `MachinePlan` without first auditing all 34 `status`-assignment sites and all `list`-mutation sites against the new field types.

The `state.step_results[existing_idx] = result` item-replacement at `executor.py:1929` is the only index-based replacement; all others are `.append()`.  Pydantic models support item assignment on `list` fields with `frozen=False`, so no change is needed.
