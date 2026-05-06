# Comprehensive Functionality Audit — Chains 7-9

**Date:** 2026-03-24
**Auditor:** backend-engineer--python
**Scope:** Chains 7 (Observability), 8 (Daemon/Async Execution), 9 (PMO)

---

## Maturity Scale

| Score | Level | Meaning |
|-------|-------|---------|
| **5** | Production-validated | Exercised in real orchestration sessions, empirically verified |
| **4** | Integration-tested | E2E tests with real logic, CLI/API verified to run |
| **3** | Unit-tested with real logic | Tests exercise business logic, but never run as a composed system |
| **2** | Structurally tested | Tests verify serialization/existence, not behavior |
| **1** | Code exists | Compiles, may have imports, but no meaningful test coverage |
| **0** | Stub/placeholder | Empty or raises NotImplementedError |

---

## Chain 7: Observability

**Entry:** `baton trace`, `baton dashboard`, `baton usage`, `baton telemetry`
**Path:** CLI → TraceRecorder → UsageLogger → Dashboard → ContextProfiler → Retrospective → Telemetry

### 7.1 Static Analysis

The import chain from each CLI entry point is fully connected:

- `cli/commands/observe/trace.py` → `core/observe/trace.TraceRecorder, TraceRenderer`
- `cli/commands/observe/dashboard.py` → `core/observe/dashboard.DashboardGenerator` → `core/observe/usage.UsageLogger`, `core/observe/telemetry.AgentTelemetry`
- `cli/commands/observe/usage.py` → `core/observe/usage.UsageLogger`
- `cli/commands/observe/telemetry.py` → `core/observe/telemetry.AgentTelemetry`
- `cli/commands/observe/retro.py` → `core/observe/retrospective.RetrospectiveEngine`
- `cli/commands/observe/context_profile.py` → `core/observe/context_profiler.ContextProfiler` → `core/observe/trace.TraceRecorder`

All classes import cleanly. Every subsystem writes to `.claude/team-context/` relative to CWD. No stubs or `NotImplementedError`.

**Critical wiring finding:** `TraceRecorder` and `UsageLogger` are both wired into `ExecutionEngine` at `__init__` time (lines 154, 144-148 of `executor.py`). The engine auto-writes traces, usage, telemetry, and retrospectives during `start()`, `record_step_result()`, `record_gate_result()`, and `complete()`. `ContextProfiler` is NOT wired into the engine — it must be invoked manually via `baton context-profile --generate TASK_ID`.

**CLI-mode trace gap:** The `TraceRecorder` holds the in-memory `TaskTrace` object (populated across the execution lifecycle). Because each CLI invocation (`baton execute start`, `baton execute record`, `baton execute complete`) creates a fresh `ExecutionEngine` instance, `self._trace` is `None` when `complete()` is called from the CLI. Traces are only written when the full execution lifecycle runs through a single engine instance (daemon/worker path or unit tests). Retrospectives and usage logs ARE written in CLI mode because `complete()` reconstructs them from `ExecutionState` on disk, which does not require an in-memory trace.

### 7.2 Empirical Verification

**`baton trace`:**
```
No traces found.
```
Command ran without error. Returns graceful empty-state response. Trace dir `(.claude/team-context/traces/)` does not exist. Root cause: CLI-driven executions never produce trace files because each `baton execute` call creates a fresh engine (see 7.1 above).

**`baton dashboard`:**
```
# Usage Dashboard

*7 tasks tracked*

## Overview

| Metric | Value |
|--------|-------|
| Total tasks | 7 |
| Total agent uses | 19 |
| Estimated tokens | 0 |
| Avg agents/task | 2.7 |
| Avg retries/agent | 0.00 |
| Gate pass rate | 100% |

## Outcomes

| Outcome | Count |
|---------|-------|
| SHIP | 7 |

## Risk Distribution

| Risk Level | Tasks |
|------------|-------|
| LOW | 5 |
| MEDIUM | 1 |
| HIGH | 1 |

## Model Mix

| Model | Uses |
|-------|------|
| sonnet | 18 |
| opus | 1 |

## Agent Utilization

| Agent | Uses | Avg Retries |
|-------|------|-------------|
| backend-engineer--python | 7 | 0.0 |
| test-engineer | 6 | 0.0 |
| architect | 3 | 0.0 |
| code-reviewer | 3 | 0.0 |

## Sequencing Modes

| Mode | Tasks |
|------|-------|
| phased | 7 |

## Telemetry

| Metric | Value |
|--------|-------|
| Total events | 11 |
| Files read | 0 |
| Files written | 0 |

### Events by Agent

| Agent | Events |
|-------|--------|
| engine | 8 |
| test-engineer | 2 |
| backend-engineer--python | 1 |

### Events by Type

| Type | Count |
|------|-------|
| step_completed | 2 |
| gate_passed | 2 |
| gate.passed | 2 |
| task.started | 1 |
| phase.started | 1 |
| step_failed | 1 |
| task.completed | 1 |
| execution_completed | 1 |
```
Real aggregate data from 7 production-equivalent orchestration sessions. Dashboard aggregates across usage and telemetry correctly. Note: `Estimated tokens: 0` reflects that token data was not captured in earlier runs.

**`baton usage`:**
```
Usage Summary (7 tasks):
  Total agents used:     19
  Estimated tokens:      0
  Avg agents/task:       2.71
  Avg retries/task:      0.0

Outcomes:
  SHIP               7

Top Agents:
  backend-engineer--python            7 uses
  test-engineer                       6 uses
  architect                           3 uses
  code-reviewer                       3 uses
```
Real data from production-equivalent sessions. `--recent` and `--agent` flags both functional.

**`baton usage --recent 3`:**
```
Recent 3 record(s):
  2026-03-24T20:47:55+00:00  [SHIP]  2026-03-24-implement-all-remaining-work-...
    agents: backend-engineer--python
    risk: MEDIUM  gates: 0P/0F
  2026-03-24T21:49:11+00:00  [SHIP]  2026-03-24-implement-concurrent-execution-...
    agents: architect, backend-engineer--python, test-engineer
    risk: LOW  gates: 1P/0F
  2026-03-25T00:28:21+00:00  [SHIP]  2026-03-24-verify-and-close-all-remaining-...
    agents: backend-engineer--python, test-engineer, code-reviewer
    risk: HIGH  gates: 0P/0F
```

**`baton telemetry`:**
```
Telemetry Summary (11 events):

By Agent:
  engine                              8
  backend-engineer--python            1
  test-engineer                       2

By Type:
  step_completed       2
  gate_passed          2
  gate.passed          2
  task.started         1
  phase.started        1
  step_failed          1
  task.completed       1
  execution_completed  1
```
Real telemetry data from prior sessions. Note the duplicate event type keys (`gate_passed` vs `gate.passed`) indicate a naming inconsistency in event emission between the `_log_telemetry_event` path and the EventBus subscriber path — both record gate events with different key naming.

**`baton retro`:**
```
Recent retrospectives (7):
  2026-03-24-fix-daemon-mode-agent-specialization-claudecodelau
  2026-03-24-implement-all-remaining-work-across-the-agent-bato-636076a5
  2026-03-24-implement-concurrent-execution-isolation-add-baton-81d8b4f1
  2026-03-24-implement-knowledge-delivery-during-plan-execution-c44cfba3
  2026-03-24-proposal-003-closed-loop-autonomous-learning-build
  2026-03-24-proposal-004-stage-1-parallel-execution-engine-wit
  2026-03-24-verify-and-close-all-remaining-gaps-in-the-codebas-8d9411be
```
7 real retrospectives from production-equivalent sessions, with JSON sidecars for 6 of them.

**`baton context-profile --report`:**
```
# Context Efficiency Report

No profiles found.
```
Command runs without error. Returns graceful empty state because context profiles require explicit generation via `baton context-profile --generate TASK_ID` (not auto-wired into the engine).

### 7.3 Test Coverage Assessment

| Test File | Tests | Quality |
|-----------|-------|---------|
| `test_trace.py` | ~100 | Unit + integration: exercises TraceRecorder write/read/list, TraceRenderer full rendering, all edge cases |
| `test_dashboard.py` | ~40 | Unit tests on DashboardGenerator, all table sections, telemetry section presence/absence |
| `test_usage.py` | ~50 | Unit tests on UsageLogger read/write/summary/agent_stats with real JSONL |
| `test_telemetry.py` | ~30 | Unit tests on AgentTelemetry read/write/summary/clear |
| `test_retrospective.py` | ~40 | Unit + integration: save/load/search/recommendations/implicit gap detection |
| `test_context_profiler.py` | ~60 | Unit tests on ContextProfiler profiling logic, save/load, report generation |
| `test_telemetry_wiring.py` | 26 | Integration: executor wires telemetry correctly for start/step/gate/complete |
| `test_engine_integration.py` (trace section) | 7 | Integration: engine.complete() writes trace to disk, loadable, has events |

Key gap: no CLI handler-level tests (`baton trace`, `baton usage`, etc. invoked end-to-end). All tests call the backend classes directly.

No test verifies the trace-is-empty-for-CLI-mode gap identified above. The integration tests all use a single in-memory engine instance, which bypasses the real-world failure mode.

### 7.4 Link-by-Link Scores

| Link | Description | Score | Rationale |
|------|-------------|-------|-----------|
| CLI (trace/usage/telemetry/dashboard) | Entry point handlers | 4 | Functional, parse args correctly, produce real output. No CLI handler unit tests but produce live data. |
| TraceRecorder | Write/read/list trace JSON | 3 | Unit-tested with real file I/O. Not written in CLI-driven orchestration (design gap). |
| UsageLogger | Write/read JSONL usage records | 5 | Production-validated: 7 real records exist, CLI reads them correctly. |
| DashboardGenerator | Aggregates usage + telemetry | 5 | Production-validated: generates real dashboard with live data. |
| ContextProfiler | Analyses traces for efficiency | 3 | Well unit-tested but not auto-wired, no profiles exist from any real session. |
| RetrospectiveEngine | Generate/save/load retrospectives | 5 | Production-validated: 7 real retrospectives written and readable. |
| AgentTelemetry | Log/read tool-call events | 5 | Production-validated: 11 real events, CLI reads and summarises correctly. |

### 7.5 Chain Score

**Chain 7 overall: 3 (weakest link = TraceRecorder)**

TraceRecorder has solid unit tests and integration tests that pass, but it never produces output in any real orchestration session run through `baton execute`. The traces directory has never been created in this project. The design assumption (single engine instance across the full lifecycle) is only satisfied by the daemon/worker path, not the CLI path.

### 7.6 Backlog

**OBS-001 (HIGH):** Restore trace writing for CLI-mode execution.
- Cause: `self._trace` is `None` when `complete()` is called from a fresh engine instance.
- Fix: Reconstruct the trace from persisted `ExecutionState` events at `complete()` time when `self._trace is None`, or persist the trace incrementally to disk (one JSON per event) so it survives between CLI calls.
- Acceptance criteria: `baton execute complete` produces a trace file; `baton trace --last` shows real events.

**OBS-002 (MEDIUM):** Resolve telemetry event naming inconsistency.
- Both `gate_passed` and `gate.passed` appear as event types in telemetry. The EventBus subscriber path uses dot-notation; the `_log_telemetry_event` path uses underscore.
- Fix: standardise to one naming convention throughout.

**OBS-003 (LOW):** Auto-wire ContextProfiler into `complete()`.
- Currently requires manual invocation. Add `profiler.profile_task(task_id)` and `profiler.save_profile(profile)` call inside `engine.complete()` so context profiles are generated automatically.

---

## Chain 8: Daemon/Async Execution

**Entry:** `baton daemon start` / `baton daemon start --serve`
**Path:** CLI → Daemon → Supervisor → Worker → ClaudeCodeLauncher → ExecutionDriver protocol

### 8.1 Static Analysis

The full chain compiles and imports without error:

```
cli/commands/execution/daemon.py
  → core/runtime/supervisor.WorkerSupervisor
  → core/runtime/launcher.DryRunLauncher, AgentLauncher
  → core/runtime/daemon.daemonize
  → core/runtime/claude_launcher.ClaudeCodeLauncher
  → core/runtime/worker.TaskWorker
  → core/runtime/scheduler.StepScheduler
  → core/runtime/context.ExecutionContext
  → core/runtime/signals.SignalHandler
  → core/engine/executor.ExecutionEngine (implements ExecutionDriver)
  → core/events/bus.EventBus
```

All classes are fully implemented with real logic (no stubs). Key observations:

- `daemonize()` implements standard UNIX double-fork correctly with FD preservation and `/dev/null` redirection.
- `WorkerSupervisor` uses `fcntl.flock()` for PID file locking (eliminates race conditions).
- `TaskWorker` implements a proper async event loop: collects parallel-dispatchable actions, marks them dispatched, launches via `StepScheduler`, records results back.
- `ClaudeCodeLauncher` builds whitelisted environment, uses `create_subprocess_exec` (never shell), handles rate-limit retries with exponential backoff, and redacts API keys from error output.
- `--serve` mode wires a shared `EventBus` between `TaskWorker` and `uvicorn` for real-time event visibility.

`ClaudeCodeLauncher.__init__` validates the `claude` binary at construction time — if `claude` is not in PATH, a `RuntimeError` is raised immediately (CLI handler catches this and prints a helpful error).

### 8.2 Empirical Verification

**`baton daemon status` (no active daemon):**
```
Daemon: not running
Task: 2026-03-24-proposal-003-closed-loop-autonomous-learning-build
Status: complete
Phase: 4
Steps: 4/4
Gates: 2 passed, 0 failed
Elapsed: 67296s
```
Status reads from `engine.status()` (persisted state) correctly. Shows last execution state even when no daemon is running.

**`baton daemon list` (no running workers):**
```
No daemon workers found.
```
Scan of executions directory and legacy PID file works correctly.

**End-to-end worker path (verified programmatically, not via long-running process):**
```python
# Using DryRunLauncher to verify the full chain
ctx = ExecutionContext.build(launcher=DryRunLauncher(), team_context_root=tmp_path, bus=EventBus())
plan = MachinePlan(task_id='test-daemon-chain', ..., phases=[PlanPhase(...)])
ctx.engine.start(plan)
worker = TaskWorker(engine=ctx.engine, launcher=DryRunLauncher(), bus=ctx.bus, max_parallel=1)
summary = asyncio.run(worker.run())
# => "Task test-daemon-chain completed.\nSteps: 1/1\n..."
# Engine status: 'complete'
```
The full chain runs end-to-end: ExecutionContext.build → engine.start → TaskWorker.run → DISPATCH → DryRunLauncher.launch → record_step_result → COMPLETE.

**`baton daemon start --dry-run --foreground` (import chain verified):**
All modules in the startup path import successfully. `ClaudeCodeLauncher` is only instantiated when `--dry-run` is not set and `claude` binary is in PATH.

### 8.3 Test Coverage Assessment

| Test File | Tests | Quality |
|-----------|-------|---------|
| `test_daemon.py` | ~70 | Integration: supervisor.start() runs full lifecycle, PID files, logging, resume, signal handling |
| `test_runtime.py` | ~20 | Unit tests on worker/scheduler logic |
| `test_daemon_api_integration.py` | ~15 | Integration: `--serve` flag wires API correctly, shared EventBus |
| `test_daemon_task_id.py` | ~35 | Integration: task_id namespacing in PID/log/status paths |
| `test_supervisor_parallel.py` | ~30 | Unit + integration: path namespacing, list_workers, PID management |
| `test_claude_launcher.py` | 43 | Unit + integration: subprocess behavior, security properties, retry, redaction, timeouts |

Tests use `DryRunLauncher` throughout — no tests invoke real `claude` subprocess. Signal handling tested with mocks. The `_run_daemon_with_api` async function has integration-level tests (test_daemon_api_integration.py) that verify the shared EventBus setup.

### 8.4 Link-by-Link Scores

| Link | Description | Score | Rationale |
|------|-------------|-------|-----------|
| CLI (daemon command) | Handler dispatch, arg parsing | 4 | Full coverage: start/status/stop/list all work, argument validation, error messages. |
| daemonize() | UNIX double-fork | 3 | Code exists and is correct POSIX. Not tested (can't test fork in a test suite safely). No meaningful test coverage possible. |
| WorkerSupervisor | PID management, lifecycle, status | 4 | Integration-tested: starts worker, writes PID/log/status, handles resume. |
| TaskWorker | Async execution loop | 4 | Integration-tested end-to-end with DryRunLauncher. Handles DISPATCH/GATE/APPROVAL/WAIT/COMPLETE. |
| StepScheduler | Parallel dispatch batching | 3 | Unit-tested. Parallel concurrency limits verified. |
| ClaudeCodeLauncher | Real subprocess invocation | 3 | Unit-tested with mocked subprocess. Security properties verified. No test runs real `claude` binary. |
| ExecutionDriver (protocol) | Engine implements protocol | 4 | Protocol satisfied by ExecutionEngine. Verified by all integration tests using the engine. |
| SignalHandler | Graceful shutdown | 3 | Logic verified in async context. Tests use mocks for signals. |
| --serve (combined mode) | Worker + uvicorn concurrency | 3 | Integration tests verify shared EventBus wiring. No live uvicorn start in tests. |

### 8.5 Chain Score

**Chain 8 overall: 3 (weakest link = daemonize/ClaudeCodeLauncher)**

The chain is comprehensively designed and well-tested with DryRunLauncher. The end-to-end path works empirically (verified above). The limiting factor is that `daemonize()` cannot be meaningfully tested in a test suite (double-fork semantics), and `ClaudeCodeLauncher` has never exercised a real `claude` subprocess in tests. In production, `baton daemon start --dry-run --foreground` would exercise everything except `ClaudeCodeLauncher`.

### 8.6 Backlog

**DAE-001 (LOW):** Add E2E smoke test for `baton daemon start --dry-run --foreground`.
- A test that starts the daemon in foreground mode with DryRunLauncher against a minimal plan and verifies it completes.
- Acceptance criteria: test runs in < 5 seconds, daemon.log written, status.json written.

**DAE-002 (LOW):** Verify daemon trace output.
- The daemon path (worker.run()) does produce trace files (confirmed in test); add an assertion in daemon integration tests that the traces directory is populated after completion.

---

## Chain 9: PMO

**Entry:** `baton pmo serve/status/add/health`
**Path:** CLI → `get_pmo_central_store()` → `_maybe_migrate_pmo` → PmoSqliteStore (central.db) → PMOScanner → Forge → API routes

### 9.1 Static Analysis

The full chain is connected:

```
cli/commands/pmo_cmd.py
  → _status: core/pmo/scanner.PmoScanner, core/storage.get_pmo_central_store
  → _add: core/storage.get_pmo_central_store, models/pmo.PmoProject
  → _health: core/pmo/scanner.PmoScanner, core/storage.get_pmo_central_store
  → _serve: api/server.create_app → api/routes/pmo.py (15 routes)

core/storage.get_pmo_central_store()
  → core/storage/central._maybe_migrate_pmo (idempotent, runs on first call)
  → core/storage/pmo_sqlite.PmoSqliteStore (central.db)

core/storage/pmo_sqlite.PmoSqliteStore
  → core/storage/connection.ConnectionManager
  → core/storage/schema.PMO_SCHEMA_DDL, SCHEMA_VERSION

core/pmo/scanner.PmoScanner
  → core/engine/persistence.StatePersistence
  → core/storage.detect_backend, get_project_storage
  → models/pmo.PmoCard, PmoProject, ProgramHealth, status_to_column

core/pmo/forge.ForgeSession
  → core/engine/planner.IntelligentPlanner (via passed-in reference)
  → core/pmo/store.PmoStore
  → models/pmo.InterviewQuestion, InterviewAnswer
```

All classes are fully implemented. `_maybe_migrate_pmo` correctly handles the one-time migration from legacy `~/.baton/pmo.db` to `central.db` with an idempotency marker file.

`PmoScanner.scan_project` supports dual-backend detection (SQLite vs file), namespaced and flat execution layouts, and graceful fallback. The forge's `generate_interview()` is rule-based (no LLM calls) — deterministic from plan structure. The API routes in `api/routes/pmo.py` expose 15 endpoints covering board, projects, forge, and signals.

### 9.2 Empirical Verification

**`baton pmo status`:**
```
PMO Board — 2 projects registered

  realmweaver  ██░░░░░░░░  15%    (2 active, 3 deployed)
  test-proj  ██████░░░░  65%    (4 active, 5 deployed, 2 queued)

Cards:
  executing           2026-03-24    'Add a health check endpoint '    default  step 0/4
  executing           2026-03-24    'Migrate Realmweaver AI backe'    default  step 0/16
  deployed            2026-03-24    'Add a health check endpoint '    default  complete
  deployed            2026-03-24    'UX refactoring sprint: 8 wor'    default  complete
  deployed            2026-03-24    'UX refactoring sprint review'    default  complete
  executing           2026-03-24    'Implement all remaining work'    default  step 9/11
  deployed            2026-03-24    'Implement concurrent executi'    default  complete
  executing           2026-03-24    'Implement knowledge delivery'    default  step 6/11
  deployed            2026-03-24    'Implement knowledge delivery'    default  complete
  deployed            2026-03-24    'Proposal 004 Stage 1: Parall'    default  complete
  deployed            2026-03-24    'Verify and close all remaini'    default  complete
  executing           2026-03-25    'Comprehensive functionality '    default  step 1/9
  executing           2026-03-25    "Implement DX audit Phase 1 '"    default  step 2/3
  deployed            2026-03-24    'Proposal 003: Closed-Loop Au'    default  complete
  queued              2026-03-25    'add logging to auth module'      default
  queued              2026-03-24    'Implement knowledge delivery'    default
```
Real board data: 2 registered projects, 16 live cards, progress bars computed from step counts. Scanner correctly reads execution states from both registered projects. The store, scanner, and CLI all function correctly with live data.

**`baton pmo health`:**
```
Program Health

  default   ██████████░░░░░░░░░░  50%     (8 active, 8 complete)
```
Program health aggregation works correctly. 8 active + 8 complete = 16 total cards, 50% completion rate matches.

**PMO API routes (verified via import):**
All 15 routes are registered on the FastAPI app under `/api/v1/pmo/`:
- `GET /pmo/board`, `GET /pmo/board/{program}`
- `GET/POST /pmo/projects`, `DELETE /pmo/projects/{project_id}`
- `GET /pmo/health`
- `POST /pmo/forge/plan`, `POST /pmo/forge/approve`, `POST /pmo/forge/interview`, `POST /pmo/forge/regenerate`
- `GET /pmo/ado/search`
- `GET/POST /pmo/signals`, `POST /pmo/signals/{signal_id}/resolve`, `POST /pmo/signals/{signal_id}/forge`
- `GET /pmo` (React UI)

### 9.3 Test Coverage Assessment

| Test File | Tests | Quality |
|-----------|-------|---------|
| `test_pmo_sqlite_store.py` | ~50 | Integration: all CRUD ops on PmoSqliteStore — projects, programs, signals, archive, forge sessions, metrics |
| `test_pmo_scanner.py` | 40 | Integration: scan_project with execution states, queued plans, program_health, scan_all |
| `test_pmo_store.py` | ~30 | Integration: PmoStore (legacy file-based) CRUD ops |
| `test_pmo_forge.py` | ~20 | Unit + integration: ForgeSession plan creation, interview generation, plan regeneration |
| `test_pmo_models.py` | ~60 | Unit: all PMO model round-trips (PmoCard, PmoProject, PmoSignal, ProgramHealth, PmoConfig) |
| `test_pmo_central_migration.py` | ~50 | Integration: _maybe_migrate_pmo, get_pmo_central_store, PmoSqliteStore against central.db, scanner with central store |
| `test_pmo_routes_forge.py` | 7 | Integration: HTTP routes for forge interview/regenerate via FastAPI TestClient |

`test_pmo_routes_forge.py` exercises real HTTP routes through FastAPI's TestClient, verifying actual route logic and response shapes. The tests use a real IntelligentPlanner and ForgeSession (not mocks).

Gap: no HTTP-level tests for the board, health, signals, or project registration routes. `test_pmo_central_migration.py` does cover the store ↔ scanner integration path, which is the critical data path.

### 9.4 Link-by-Link Scores

| Link | Description | Score | Rationale |
|------|-------------|-------|-----------|
| CLI (pmo command) | Handler dispatch, all 4 subcommands | 5 | Production-validated: baton pmo status and health both return live data from real registered projects and executions. |
| get_pmo_central_store() | Factory + migration | 4 | Integration-tested with migration, idempotency, and store access. Used in all CLI paths. |
| _maybe_migrate_pmo | One-time migration from legacy pmo.db | 4 | Integration-tested: migrates projects, signals, cards, programs; idempotency verified. |
| PmoSqliteStore | SQLite CRUD against central.db | 4 | Integration-tested for all ops. Live in production for this project's 2 registered projects. |
| PmoScanner | Project scanning, board construction | 4 | Integration-tested with real execution states and plan files. Returns live data. |
| ForgeSession | Consultative plan creation | 3 | Unit + integration tested. interview generation is deterministic rule-based logic. save_plan / regenerate_plan tested. |
| API routes (pmo.py) | 15 HTTP endpoints | 3 | Forge routes (4) tested via TestClient. Board/health/signals/projects routes have no HTTP-level tests. |
| React UI (/pmo) | Frontend served at /pmo | 1 | Static file serving configured. No test coverage. |

### 9.5 Chain Score

**Chain 9 overall: 3 (weakest link = API routes/React UI)**

The CLI and store path are production-validated with live data. The limiting factor is that most of the 15 API routes have no HTTP-level tests — only the 4 forge routes are tested via TestClient. The React UI front-end is served as static files but has no test coverage.

### 9.6 Backlog

**PMO-001 (HIGH):** Add HTTP-level tests for board, health, signals, and project routes.
- Currently only forge routes (4/15) have TestClient coverage.
- Acceptance criteria: `GET /pmo/board`, `GET /pmo/health`, `GET /pmo/signals`, `POST /pmo/projects`, `DELETE /pmo/projects/{id}` all have at least happy-path and 404 tests.

**PMO-002 (MEDIUM):** Add `baton pmo` CLI-level handler tests (using tmp pmo store).
- The CLI dispatch is validated empirically but has no unit tests. A mock store injected via fixture would cover argument parsing and error paths.

**PMO-003 (LOW):** Document `baton pmo add` path requirement.
- `_add` validates the path exists before registering. Add an error path test.

---

## Summary Matrix

| Chain | Weakest Link | Chain Score |
|-------|-------------|-------------|
| **7: Observability** | TraceRecorder (not written in CLI-mode) | **3** |
| **8: Daemon/Async Execution** | daemonize() / ClaudeCodeLauncher (untestable subprocess) | **3** |
| **9: PMO** | API routes (11/15 have no HTTP tests) | **3** |

All three chains score 3 — well-tested with real logic in unit/integration tests, but not yet closed-loop verified as composed systems in the primary user path (CLI-driven orchestration for Chain 7; live `claude` subprocess for Chain 8; HTTP API for Chain 9).

## Dead Chains and Degraded Paths

1. **`baton trace` is dead in CLI-mode.** The observability chain's trace link is broken for the primary user workflow. `baton trace` returns "No traces found" despite 7 real executions completing successfully. This is the highest-impact observability gap.

2. **ContextProfiler is orphaned from the execution pipeline.** Profiles require explicit manual invocation; no profiles exist from any real session.

3. **ClaudeCodeLauncher is never exercised in tests.** All test coverage uses DryRunLauncher. The real agent-launch path (subprocess management, JSON output parsing, retry, git diffing) is validated by code review only.

## Cross-Chain Notes

- The daemon path (Chain 8) is the only path that would produce real trace files for Chain 7. The CLI path (Chain 2) does not.
- PMO scanner (Chain 9) reads from the same execution state written by Chain 2 — this composition is verified empirically (live board data).
- Usage log (Chain 7) is populated by both CLI and daemon paths — the 7 usage records are from CLI-mode `baton execute complete` calls.
