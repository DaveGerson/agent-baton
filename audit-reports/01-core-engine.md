# Audit Report: Core Engine Integrations

**Scope:** `core/engine/` — planner, executor, dispatcher, gates, persistence, knowledge_resolver, knowledge_gap, bead_store, bead_signal, bead_decay, bead_selector, classifier, protocols
**Date:** 2026-04-13

---

## Findings

### ISLAND: `gates.py` — No Observability Integration

`gates.py:53` (`GateRunner`) imports only `govern.spec_validator` and execution models. Zero integration with events, telemetry, or learning. The executor compensates (executor.py:912-933 emits trace + telemetry + domain events for gate results), so the gap is architectural, not functional — but GateRunner itself cannot be used standalone with any observability.

### ISLAND: `dispatcher.py` — Pure Function, No Subsystem Awareness

`dispatcher.py` has zero imports from events, observe, learn, or govern. It generates prompts purely from execution models. The planner consults `PatternLearner` and `BudgetTuner`, but the dispatcher does not receive those signals when formatting the actual delegation text. Dispatch prompt quality cannot improve over time.

### ISLAND: `knowledge_resolver.py` — No Learning Feedback Loop

Resolves knowledge but never reports resolution success/failure rates back to the learning system. Resolution outcomes are recorded only in `state.resolved_decisions` (`executor.py:2626`), not fed back to the knowledge registry for relevance tuning.

### ONE-WAY: `executor.record_step_result` — No Domain Events for Steps

Domain events `step_completed` and `step_failed` are defined in `core/events/events.py:52,99` and published by the runtime worker (`core/runtime/worker.py:212,225`), but the executor's `record_step_result` (`executor.py:694`) does **not** publish them. The CLI-driven execution loop (the primary path in CLAUDE.md) calls `record_step_result` directly, bypassing the worker. **CLI-mode executions produce trace events and telemetry but no step-level domain events.**

### ONE-WAY: `knowledge_gap` — Not Independently Persisted

`knowledge_gap.py` parses gap signals and determines escalation but has zero imports from learn/observe/events. The executor stores gaps in `state.pending_gaps` (`executor.py:2716`). The learning engine consumes them at completion time (`core/learn/engine.py:94,218`), but only from the `ExecutionState` object passed to `detect()`. If detection is skipped (`executor.py:1054-1058` is best-effort), gap data is lost.

### ORPHAN: `BeadStore.resolve_conflict` — Zero Production Callers

`bead_store.py:440` has no callers outside tests (`test_bead_tiers234.py:1030-1031`). The executor detects conflicts (`has_unresolved_conflicts` at `executor.py:2124`, emits `bead_conflict` event at `executor.py:2131`) but never resolves them. No CLI command exposes conflict resolution either. Bead conflicts are permanent.

### ORPHAN: `bead_signal.py` — No Event Bus Integration

Pure parsing utility. The executor publishes `bead_created` events after calling it (`executor.py:775-782`), but `parse_bead_feedback` results (`executor.py:798-808`) are applied silently to quality scores with no corresponding domain event or telemetry entry.

### SHALLOW: `classifier.py` — Engine-Internal Only

4 classifier implementations but only consumed by `planner.py:18-19,732`. `HaikuClassifier` (line 398) makes LLM calls but has no telemetry integration for tracking classification API cost or latency.

### SHALLOW: `bead_decay.py` / `bead_selector.py` — Minimal Integration

Both called only from executor.py and CLI. Neither emits events, telemetry, or learning signals. Decay silently archives beads; selection silently picks them. The selector's ranking algorithm is hardcoded with no learned weights.

---

## Summary Table

| Finding | Type | Location | Impact |
|---------|------|----------|--------|
| No step domain events in CLI mode | ONE-WAY | executor.py:694 vs worker.py:212 | Event subscribers miss step data for CLI executions |
| `resolve_conflict` unreachable | ORPHAN | bead_store.py:440 | Bead conflicts are permanent — no resolution path |
| GateRunner has no observability | ISLAND | gates.py:53 | Standalone gate use is invisible |
| Dispatcher ignores learned patterns | ISLAND | dispatcher.py:64 | Prompt quality does not improve over time |
| KnowledgeResolver has no feedback | ISLAND | knowledge_resolver.py | Resolution effectiveness is not tracked |
| Bead feedback has no event/telemetry | SHALLOW | executor.py:796-808 | Quality adjustments are invisible |
| Classifier has no cost telemetry | SHALLOW | classifier.py:398 | Haiku API costs not tracked |
| Knowledge gaps not independently persisted | ONE-WAY | knowledge_gap.py → executor.py:2716 | Gap data lost if learning detection fails |
