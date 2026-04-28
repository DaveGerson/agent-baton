# Agent Baton — Comprehensive Functionality Audit Report

**Date:** 2026-03-24
**Scope:** All 12 functional chains, 4 cross-chain compositions, static orphan check
**Method:** Top-down functional chain tracing with empirical CLI/API verification

## Executive Summary

Agent Baton has **38K LOC across 186 modules** with **3,744 tests** — and none of it is scaffolding in the traditional sense (no stubs, no NotImplementedError, no TODO comments). However, this audit reveals a **significant wiring gap pattern**: subsystems are individually well-implemented and well-tested, but several critical cross-subsystem integrations are broken or never connected in the production CLI path.

**The core orchestration workflow (plan → execute → record → complete) is solid.** Plans generate correctly, the state machine advances, persistence works, delegation prompts are well-formed. The weaknesses are in the secondary feedback loops (tracing, events, knowledge gap auto-resolution, telemetry) that were built to close the learning loop but were never wired into the CLI execution path.

## Scored Subsystem Matrix

### Chain Scores (weakest link determines chain score)

| # | Chain | Score | Weakest Link | Evidence |
|---|-------|-------|-------------|----------|
| 1 | **Plan Creation** | **3** | PatternLearner/BudgetTuner have no real-world data to learn from | Planner runs, routes correctly, generates valid plans. Learning subsystems return empty results gracefully. |
| 2 | **Execution Lifecycle** | **2** | EventPersistence not wired; FileStorage telemetry signature bug | State machine works. Persistence works. Events are ephemeral (created and GC'd per CLI call). Telemetry silently drops with TypeError. |
| 3 | **Knowledge Delivery** | **2** | `_knowledge_resolver` never injected on ExecutionEngine | Knowledge packs resolve at plan time and embed in prompts correctly. But runtime gap auto-resolution is dead code — resolver attribute never set. |
| 4 | **Federated Sync** | **4** | PMO migration boot path (score 3) | 251 rows synced live, watermarks work, idempotent. Strongest chain. |
| 5 | **Improvement Loop** | **3** | estimated_tokens always 0; insufficient data for pattern thresholds | All logic correct. Full loop runs without error. But has never produced a non-trivial recommendation due to upstream data gaps. |
| 6 | **Governance & Policy** | **3** | PolicyEngine/Compliance never exercised in real orchestration | Classifier works empirically (risk levels differentiate correctly). Downstream enforcement untested in production. |
| 7 | **Observability** | **3** | Traces never written in CLI mode | Dashboard, usage, telemetry work with real data from 7 sessions. But trace writing requires single-instance engine (daemon path only). |
| 8 | **Daemon/Async** | **3** | ClaudeCodeLauncher never tested (only DryRunLauncher) | Code compiles, dry-run path verified. Real subprocess launching untested. |
| 9 | **PMO** | **3** | 11 of 15 API routes have no HTTP tests | CLI and store are production-validated. React UI has no test coverage. |
| 10 | **Distribution** | **4** | `baton install` handler lacks dedicated tests | Full pipeline works: package → publish → pull → verify. 194KB tarball produced with 42 agents. |
| 11 | **API Server** | **1** | routes/pmo.py (12 endpoints) and routes/events.py have zero HTTP tests | App creates, all 9 route modules import, 41 routes registered. But 2 key modules completely untested at HTTP level. |
| 12 | **External Sources** | **4** / **0** | ADO adapter works; Jira/GitHub/Linear registered but unimplemented | Framework is solid. ADO adapter verified end-to-end. Other adapters are stubs. |

### Score Distribution

```
Score 5 (Production-validated): 0 chains
Score 4 (Integration-tested):   3 chains (Sync, Distribution, External Sources-ADO)
Score 3 (Unit-tested):          6 chains (Plan, Improve, Govern, Observe, Daemon, PMO)
Score 2 (Structurally tested):  2 chains (Execution Lifecycle, Knowledge Delivery)
Score 1 (Code exists):          1 chain  (API Server)
Score 0 (Stub):                 0 chains (but 3 adapter stubs in Chain 12)
```

## Cross-Chain Composition Results

| # | Composition | Verdict | Score | Root Cause |
|---|------------|---------|-------|------------|
| 1 | Plan → Execute → Trace | **FAIL** | 0 | Each CLI call creates fresh engine; `self._trace` is None at `complete()` |
| 2 | Plan + Knowledge → Execute → Gap → Escalation | **PARTIAL** | 2 | Gap parsing works; `_knowledge_resolver` never set so auto-resolve is unreachable |
| 3 | Execute → Complete → Retrospective → Scores | **PARTIAL** | 3 | Works in file mode (7 retros exist); broken in SQLite mode |
| 4 | Plan → Execute → Events → Projections | **FAIL** | 1 | EventPersistence not wired as bus subscriber when storage is not None |

## Dead Chain Report

### Fully Dead Paths (code exists, never executes in production)

1. **Knowledge gap auto-resolution** — `ExecutionEngine._handle_knowledge_gap()` checks `_knowledge_resolver` which is never set. All factual gaps escalate to human gates instead of auto-resolving.
2. **Event persistence in CLI mode** — `EventPersistence` subscriber is not registered when storage backend is active. Events exist only in-process memory.
3. **Trace writing in CLI mode** — `TraceRecorder` data is lost when engine instance is garbage-collected between CLI calls.
4. **Telemetry in file-mode projects** — `FileStorage.log_telemetry()` passes kwargs to `log_event()` which expects a positional `TelemetryEvent`. Silently drops with TypeError.

### Partially Dead Paths (works sometimes, not always)

5. **Retrospective → Scores in SQLite mode** — `PerformanceScorer` reads from filesystem; SQLite-mode retros go only to DB.
6. **BudgetTuner recommendations** — Logic correct but `estimated_tokens` is always 0 in usage records, making all budget recommendations inert.
7. **PatternLearner pattern extraction** — Logic correct but needs 5+ tasks per sequencing mode; current data has fewer.

## Bugs Found

| ID | Severity | Chain | Description |
|----|----------|-------|-------------|
| **BUG-1** | HIGH | 2, 7 | Traces never written in CLI mode — `self._trace` is None when `complete()` is called from a fresh engine instance |
| **BUG-2** | HIGH | 2, 4 (composition) | EventPersistence not wired as EventBus subscriber when storage backend is active |
| **BUG-3** | HIGH | 3 | `_knowledge_resolver` never injected on ExecutionEngine in any production code path |
| **BUG-4** | MEDIUM | 2 | FileStorage.log_telemetry signature mismatch: passes kwargs but log_event() expects positional TelemetryEvent |
| **BUG-5** | MEDIUM | 7 | Telemetry event naming inconsistency: `gate_passed` (direct logging) vs `gate.passed` (EventBus subscriber) |
| **BUG-6** | LOW | 3 (composition) | PerformanceScorer reads retros from filesystem but SQLite mode writes only to DB |

## Orphan Analysis

- **0 true dead modules** — all 186 modules are imported by something
- **37 CLI command modules** — dynamically discovered via pkgutil (not static imports), all active
- **1 duplicate shim** — `cli/commands/verify_package.py` duplicates `cli/commands/distribute/verify_package.py`; recommend removal
- **25 modules** imported by production code but not covered by any chain path — these are real functionality with a documentation gap (not a code gap)

## Prioritized Backlog

### P0 — Critical Wiring Fixes (raise chains 2, 3 from score 2 to 4)

| ID | Task | Chains Affected | Acceptance Criteria |
|----|------|----------------|---------------------|
| **FIX-1** | Wire EventPersistence as EventBus subscriber in CLI path | 2, composition 4 | Events written to JSONL/SQLite after `baton execute complete` |
| **FIX-2** | Inject KnowledgeResolver on ExecutionEngine at construction or via CLI | 3, composition 2 | KNOWLEDGE_GAP signals auto-resolve when registry has matching pack |
| **FIX-3** | Reconstruct trace from persisted ExecutionState in `complete()` when `_trace` is None | 2, 7, composition 1 | `baton trace` returns data after CLI-driven execution |
| **FIX-4** | Fix FileStorage.log_telemetry signature: `t.log_event(TelemetryEvent.from_dict(event))` | 2 | No TypeError in file-mode telemetry logging |

### P1 — Data Pipeline Fixes (raise chains 5, 6 from score 3 to 4)

| ID | Task | Chains Affected | Acceptance Criteria |
|----|------|----------------|---------------------|
| **FIX-5** | Populate estimated_tokens in usage records during execution | 5 | BudgetTuner produces non-zero recommendations |
| **FIX-6** | Wire PolicyEngine into `baton plan` CLI path (verify it's called) | 6 | `baton plan` on a regulated task shows policy enforcement in plan.md |
| **FIX-7** | Fix PerformanceScorer to read retros from SQLite when in SQLite mode | 7, composition 3 | `baton scores` returns data in SQLite-mode projects |
| **FIX-8** | Normalize telemetry event names to consistent format | 7 | Single naming convention across all event sources |

### P2 — Test Coverage Gaps (raise chains 9, 11 from score 1-3 to 4)

| ID | Task | Chains Affected | Acceptance Criteria |
|----|------|----------------|---------------------|
| **FIX-9** | Add HTTP-level tests for routes/pmo.py (12 endpoints) | 9, 11 | All PMO board/health/signals/CRUD routes have TestClient tests |
| **FIX-10** | Add HTTP-level tests for routes/events.py (SSE) | 11 | SSE stream endpoint tested |
| **FIX-11** | Add E2E smoke test for `baton daemon start --dry-run --foreground` | 8 | Daemon startup path verified in CI |

### P3 — Stubs and Cleanup

| ID | Task | Chains Affected | Acceptance Criteria |
|----|------|----------------|---------------------|
| **FIX-12** | Implement Jira adapter (or remove registration) | 12 | `baton source add --type jira` either works or isn't offered |
| **FIX-13** | Implement GitHub adapter (or remove registration) | 12 | Same |
| **FIX-14** | Implement Linear adapter (or remove registration) | 12 | Same |
| **FIX-15** | Remove duplicate `cli/commands/verify_package.py` shim | orphan | No duplicate subcommand registration |

## Domain Documentation

The 12 functional chains above constitute the canonical domain taxonomy for agent-baton. Each domain has a clear entry point, a traceable subsystem path, and defined contracts. This taxonomy should be written into `docs/architecture.md` as a formal "Functional Domains" section.
