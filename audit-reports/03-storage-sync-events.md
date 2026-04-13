# Audit Report: Storage, Sync & Events Integration

**Scope:** `core/storage/`, `core/events/`, `models/events.py`, `models/session.py`
**Date:** 2026-04-13

---

## Findings

### PARTIAL: Event Bus — Step Lifecycle Events Never Emitted

**Severity: HIGH.** Domain event factories define `step_dispatched`, `step_completed`, `step_failed`, `team_member_completed`, `approval_required`, `approval_resolved`, `plan_amended`, `human_decision_needed`, and `human_decision_resolved`. The executor only publishes:

- `task.started` (line 556)
- `phase.started` (lines 564, 2175, 2264)
- `phase.completed` (lines 2166, 2254)
- `gate.passed` / `gate.failed` (lines 944, 936)
- `task.completed` (line 1076)
- `bead.created` / `bead.conflict` (lines 776, 2132)

`record_step_result()` (line 694) records trace events and persists to SQLite but **does not call `self._publish()`**. CLI-mode executions produce no step-level domain events. `TaskView.steps_completed` is always 0 when built from the event stream.

### DEAD-INFRA: SessionState Model

`models/session.py` defines `SessionState`, `SessionCheckpoint`, and `SessionParticipant` with full lifecycle support. Exported from `models/__init__.py` (line 46) but **never imported or used** anywhere in `core/` or `cli/`. Zero consumers, zero producers. Entirely dead code.

### DEAD-INFRA: task-view.json Written, Never Read

`task-view.json` is written by `TaskViewSubscriber` (`executor.py:508`) but never read by any subsystem. The `project_task_view()` function is also called by `baton events show` CLI (`events.py:89`), but that reads from the JSONL event log, not the materialized file.

### MANUAL-ONLY: Sync to central.db

Sync is triggered in exactly two places:
1. `baton execute complete` handler (`execute.py:526`)
2. `baton execute run` completion (`execute.py:758`)

Intermediate state changes (`record`, `gate`, `approve`) never trigger sync. If execution is abandoned before `complete`, no data reaches central.db.

### PARTIAL: Sync Trigger Always "manual"

`sync.py:525` hardcodes `trigger="manual"` as default for `_record_history()`. No caller overrides this. All sync runs — including automatic ones from `baton execute complete` — record as "manual", making it impossible to distinguish automatic from CLI-initiated syncs.

### PARTIAL: Central Store Analytics Disconnected from Learning

`CentralStore` exposes `agent_reliability()`, `cost_by_task_type()`, `recurring_knowledge_gaps()`, and `project_failure_rates()` (`central.py:221-307`). The `learn/` subsystem does **not import or query CentralStore**. Cross-project analytics exist for CLI display but do not feed back into learning or improvement pipelines.

### PARTIAL: External Source Adapters Standalone

All four adapters (ADO, GitHub, Jira, Linear) are fully implemented and registered via `AdapterRegistry`. But the planner has **zero imports** from adapters. External work items cannot influence plan generation. The `v_external_plan_mapping` view joins mappings to plans for display but creates no runtime behavior.

### ORPHAN-HANDLER: Projection Handlers for Never-Emitted Topics

`_apply_event()` in `projections.py` handles `step.dispatched` (line 249), `step.completed` (line 259), `step.failed` (line 271), `human.decision_needed` (line 300), `human.decision_resolved` (line 305). Since the executor never publishes these topics, these branches are dead code in production.

---

## Summary Table

| Category | Finding | Key File:Line |
|----------|---------|---------------|
| PARTIAL | 9 event factories defined, only 5 topics emitted | events/events.py, executor.py:694 |
| DEAD-INFRA | SessionState model unused everywhere | models/session.py:94 |
| DEAD-INFRA | task-view.json written, never read | executor.py:508 |
| MANUAL-ONLY | Sync only on `complete`, not intermediate steps | execute.py:526, execute.py:758 |
| PARTIAL | Central analytics not consumed by learn/improve | central.py:221-307 |
| PARTIAL | Adapters standalone, not integrated with planner | adapters/ vs engine/planner.py |
| ORPHAN-HANDLER | 5 projection handlers for never-emitted topics | projections.py:249-308 |
| PARTIAL | Sync trigger always "manual" even for auto syncs | sync.py:525 |
