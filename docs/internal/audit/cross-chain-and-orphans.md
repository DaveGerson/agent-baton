# Audit Report: Cross-Chain Composition Tests & Static Orphan Check

**Date:** 2026-03-24
**Auditor:** backend-engineer--python (claude-sonnet-4-6)
**Scope:** Four cross-chain composition tests + static orphan analysis of all 186 Python modules

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

## Cross-Chain Composition Tests

### Composition 1: Plan → Execute → Trace

**Question:** Does executing a plan produce a trace in the trace store?

**Expected result (from Chain 7 findings):** FAIL — `self._trace` is `None` when `complete()` is called in CLI mode.

#### Code trace

1. `baton execute start` → creates a fresh `ExecutionEngine` in `execute.py:handler()`. Calls `engine.start(plan)`. Inside `start()`, `executor.py:306` does:
   ```python
   self._trace = self._tracer.start_trace(task_id=plan.task_id, ...)
   ```
   The in-memory `TaskTrace` object is stored on `self._trace`. Process exits.

2. `baton execute record` (one or more calls) → creates a **new** `ExecutionEngine`. `self._trace` is `None` on `__init__` (`executor.py:162`). `record_step_result()` calls `_emit_trace_event()` which guards on `if self._trace is not None:` — no trace events are recorded.

3. `baton execute complete` → creates another **new** `ExecutionEngine`. `self._trace` is `None`. In `complete()` (`executor.py:666`):
   ```python
   if self._trace is not None:     # evaluates False in CLI mode
       finished_trace = self._trace
       trace_path = self._tracer.complete_trace(finished_trace, outcome="SHIP")
   ```
   `trace_path` stays `None`. `finished_trace` stays `None`. The SQLite path also guards on `finished_trace is not None` — no trace is saved.

4. Result: no file written to `.claude/team-context/traces/`, no row in `baton.db:traces`.

#### Empirical verification

The traces directory does not exist in the main project's `team-context`:

```
$ ls .claude/team-context/ | grep traces
(no output)
```

Of 9 completed executions on disk, zero produced a trace file. The `baton trace` command reports "No traces found." Confirmed by empirical audit run in chains-7-9.md.

The `resume()` method does attempt to reload a trace from disk (`executor.py:780-787`), but this only reloads traces that were already written by a single-session engine (daemon/worker mode). It cannot recover a trace that was never written.

**Note on storage mode vs file mode:** The main project runs in file mode (no `baton.db` in `.claude/team-context/`). In file mode, EventPersistence IS wired as a bus subscriber during `__init__`, so events ARE persisted when a bus is provided. However, the trace gap applies regardless of storage mode — traces depend on the in-memory `_trace` object.

#### Verdict: FAIL
**Score: 0/5**

The Plan → Execute → Trace composition is broken in all CLI-driven execution. Traces are only produced when the full execution lifecycle runs within a single engine instance (daemon worker or direct API). No traces exist in the project's real execution history.

---

### Composition 2: Plan with knowledge → Execute → Knowledge gap → Escalation

**Question:** Does a missing knowledge pack trigger gap detection and escalation?

**Expected result (from Chain 3 findings):** PARTIAL FAIL — gap detection logic exists and is reachable, but the `KnowledgeResolver` is never injected into `ExecutionEngine`, so auto-resolution always reports no match.

#### Code trace

**Gap detection path (works):**

1. `baton execute record --outcome "KNOWLEDGE_GAP: Need context on X\nCONFIDENCE: none\nTYPE: factual"` → `engine.record_step_result()` → calls `self._handle_knowledge_gap(outcome, step_id, agent_name, state)`.

2. `executor.py:1752`: `parse_knowledge_gap(outcome, ...)` — regex-based parser finds the `KNOWLEDGE_GAP:`, `CONFIDENCE:`, and `TYPE:` lines. Returns a `KnowledgeGapSignal`. This path works correctly.

3. `executor.py:1762`:
   ```python
   resolver = getattr(self, "_knowledge_resolver", None)
   ```
   `_knowledge_resolver` is never set on any `ExecutionEngine` instance by production code. The attribute does not appear in `__init__`, is not a constructor parameter, and no production code path assigns it after construction. `resolver` evaluates to `None`. `resolution_found` stays `False`.

4. `determine_escalation(signal, risk_level, intervention_level, resolution_found=False)` — the escalation matrix runs normally with `resolution_found=False`.

**KnowledgeResolver gap (broken):**

The `IntelligentPlanner.create_plan()` builds a `KnowledgeResolver` at step 9.5 (`planner.py:462-469`) and uses it to attach knowledge to plan steps at plan-creation time. This resolver is a local variable named `_resolver` — it is never passed to or set on an `ExecutionEngine`. The planner and engine have no shared channel for the resolver object.

Result: during execution, factual gaps with a registry match would be reported as "no match" because the resolver is absent, causing them to escalate to `queue-for-gate` or `best-effort` rather than `auto-resolve`. The auto-resolve path in `executor.py:1800-1808` is unreachable in production.

**Escalation path (works, with degraded quality):**

After `determine_escalation()` returns `"queue-for-gate"` or `"best-effort"`, the state machine queues an approval gate or logs and continues. This works. The gap record is written to `state.resolved_decisions` for `"auto-resolve"` (unreachable) or to a gate for human review. The gate flow then follows the normal gate path.

#### Empirical verification

From chains-1-3.md: "The `_knowledge_resolver` attribute on `ExecutionEngine` is never set by production code — only via tests that assign it directly." The `central.db:knowledge_gaps` table has 0 rows despite multiple completed executions with real usage data, consistent with the gap record never being written via the auto-resolve path.

#### Verdict: PARTIAL
**Score: 2/5**

Gap parsing and escalation matrix work. The auto-resolution path is dead because `_knowledge_resolver` is never injected. All gaps are treated as unresolved, causing unnecessary human escalations for factual gaps that the registry could satisfy.

---

### Composition 3: Execute → Complete → Retrospective → Scores

**Question:** Does completing an execution generate a retrospective that updates performance scores?

**Expected result:** PARTIAL — retrospectives ARE written in CLI mode, but PerformanceScorer reads from file paths that diverge from where retrospectives are written in storage mode.

#### Code trace

**Retrospective generation (works in file mode):**

1. `baton execute complete` → `engine.complete()`:
   - `executor.py:685-700`: `_build_retrospective_data(state)` extracts qualitative data from `ExecutionState`.
   - In file mode (`storage is None`): `self._retro_engine` is a live `RetrospectiveEngine` instance (`executor.py:150-152`). `_gen_engine.generate_from_usage(...)` builds a `Retrospective` object.
   - `_save_retro(retro)` → `self._retro_engine.save(retro)` writes `.claude/team-context/retrospectives/<task_id>.md` and `.json`.

2. Empirical: `.claude/team-context/retrospectives/` contains 7 files (5 `.md`, including one from 2026-03-24, and matching `.json` sidecars). The composition does write retrospectives.

**Score computation (works with data):**

3. `baton scores` → `cli/commands/improve/scores.py` → `PerformanceScorer(usage_logger=UsageLogger(), retro_engine=RetrospectiveEngine())`.
   - `UsageLogger` defaults to `.claude/team-context/usage-log.jsonl`. This file exists (4,320 bytes).
   - `RetrospectiveEngine` defaults to `.claude/team-context/retrospectives/`. This directory exists.
   - `score_all()` reads usage records and scans retrospective markdown for agent mentions.

4. `usage-log.jsonl` contains 7 task records with agent data. `PerformanceScorer.score_agent()` correctly computes `first_pass_rate`, `retry_rate`, and `avg_tokens` from this data.

**File mode gap in storage mode:**

When `storage is not None` (SQLite mode), `self._retro_engine = None` (`executor.py:126`). The retrospective is written via `self._storage.save_retrospective(retro)` to SQLite. However, `PerformanceScorer` reads from the filesystem (`RetrospectiveEngine.list_retrospectives()`), not SQLite. In SQLite mode, the retrospective is saved to the DB but not to the file system, so `PerformanceScorer` never sees it.

**Immediate verdict for the main project:** PASS, because the main project runs in file mode. The chain is intact: `complete()` → retrospective file written → `PerformanceScorer` reads it.

**For projects with baton.db (SQLite mode):** BROKEN — retrospectives are siloed in SQLite with no filesystem copy, so scorer gets no qualitative signal.

#### Empirical verification

```
$ baton scores
# Agent Performance Scorecards

Based on 19 total agent uses.

### backend-engineer--python
- Health: adequate
- Uses: 8
- First-pass rate: 100%
- Avg retries: 0.0
...
```

Real data from real executions. Scores are computed and meaningful.

The `central.db:retrospectives` table has 3 rows (synced from executions that ran through the file-mode engine). The `central.db:traces` table has 0 rows.

#### Verdict: PARTIAL (PASS in file mode / FAIL in SQLite mode)
**Score: 3/5**

The composition works end-to-end in file mode. It silently breaks in SQLite mode because retrospectives are saved to DB but PerformanceScorer only reads from the filesystem. Since the main project runs in file mode, real data exists and scores are meaningful.

---

### Composition 4: Plan → Execute → Events → Projections

**Question:** Do execution events persist and project correctly?

**Expected result (from Chain 2 findings):** PARTIAL — EventPersistence is NOT wired when `storage is not None` (SQLite mode). In file mode it IS wired when a bus is provided.

#### Code trace

**EventBus → EventPersistence wiring:**

In `ExecutionEngine.__init__()` (`executor.py:118-143`):

```python
if storage is not None:
    # SQLite mode
    self._event_persistence = None          # NOT wired
    # bus subscriber for EventPersistence never registered
else:
    # File mode
    if self._bus is not None:
        self._event_persistence = EventPersistence(events_dir=events_dir)
        self._bus.subscribe("*", self._event_persistence.append)
```

The CLI always provides a `bus=EventBus()` and `storage=get_project_storage(context_root)`. In the main project `get_project_storage()` detects `file` mode (no baton.db) and returns `FileStorage`. Since `storage is not None`, the `else` branch is taken only for the `file_backend.FileStorage` case — but wait, `storage is not None` is True for both `SqliteStorage` and `FileStorage`. Let me confirm:

`executor.py:118`: `if storage is not None:` — this is True whenever any storage backend object is passed. The EventPersistence wiring is in the `else` branch (lines 128-143), which only executes when `storage is None` (legacy no-storage mode).

**Current CLI path for the main project:**

1. `execute.py:211`: `storage = get_project_storage(context_root)` — returns `FileStorage` (because no baton.db).
2. `execute.py:212`: `engine = ExecutionEngine(bus=bus, task_id=task_id, storage=storage)`.
3. `storage is not None` → `_event_persistence = None`. Bus subscriber for EventPersistence is NOT registered.
4. Events published via `self._publish()` go to the bus but are never written to disk.

**Confirmed by empirical data:**

The `events/` directory at `.claude/team-context/events/` contains two JSONL files with a total of 7 events. These were written by the `2026-03-24-proposal-004-*` execution. Checking that execution's state reveals it ran under a code path that reached the `else` branch (before the `if storage is not None` guard was added or before `FileStorage` was introduced), OR it ran without a storage object.

All subsequent executions under the current CLI path produce no event files. Execution directories (`executions/2026-03-24-implement-*/`) contain only `execution-state.json`, `mission-log.md`, `plan.json`, and `plan.md` — no `events/` subdirectory.

The `central.db:events` table has 0 rows. No events have ever reached either persistence layer for recent executions.

**EventProjections:**

`core/events/projections.py:project_task_view()` is a pure function that takes a list of `Event` objects and returns a `TaskView`. It works correctly in isolation (tested). However, it can only project from events that were persisted. Since no events are persisted for current executions, the projection function has no input to work with. The `api/routes/events.py` route (Chain 11) reads events from `EventPersistence.read()`, which reads from JSONL files — which are empty for all recent executions.

#### Empirical verification

```
$ find .claude/team-context/executions -name "*.jsonl" 2>/dev/null
.../2026-03-24-proposal-004-.../events/2026-03-24-proposal-004-...jsonl  # 7 events
.../2026-03-24-proposal-004-.../telemetry.jsonl
# No other event files found.
```

Of 9 execution directories, 1 has event data (legacy run). 8 have no event data.

#### Verdict: FAIL
**Score: 1/5**

EventPersistence is not wired in the CLI production path. The `if storage is not None` guard in `ExecutionEngine.__init__()` short-circuits EventPersistence registration for both SQLite and FileStorage backends, leaving `_event_persistence = None`. Events published during execution are in-memory only and lost on process exit. The EventProjections function is correct but has no data to project from.

---

## Cross-Chain Composition Summary

| Composition | Verdict | Score | Root Cause |
|-------------|---------|-------|-----------|
| Plan → Execute → Trace | FAIL | 0/5 | `self._trace` is in-memory; fresh engine per CLI call; `complete()` never sees the trace object |
| Plan → Execute → Knowledge Gap → Escalation | PARTIAL | 2/5 | Gap parsing works; `_knowledge_resolver` never injected; auto-resolve dead; escalation matrix runs but with degraded input |
| Execute → Complete → Retrospective → Scores | PARTIAL | 3/5 | Works end-to-end in file mode; silently broken in SQLite mode (scorer reads filesystem, retros in DB) |
| Plan → Execute → Events → Projections | FAIL | 1/5 | EventPersistence not wired when `storage is not None`; no events persisted for any recent execution |

**Composite cross-chain score: 1.5/5** (average of the four compositions)

---

## Static Orphan Check

### Methodology

186 Python modules exist under `agent_baton/`. For each module, a grep was run across the entire codebase (`agent_baton/` and `tests/`) for the full dotted module name. Modules with zero external importers (excluding self-references) were flagged as candidates. CLI command modules were cross-referenced against `cli/main.py`'s dynamic discovery mechanism (`pkgutil.iter_modules`), which auto-imports all modules with `register()` + `handler()` at startup.

### CLI Command Modules (Pseudo-Orphans)

The following 37 CLI command modules were flagged as unimported by static grep because they are loaded dynamically at runtime via `discover_commands()` in `cli/main.py`. They are NOT dead code — they are the active CLI surface. All are classified as **intentionally unused by static import** but **active at runtime**.

| Module | CLI Command | Chain |
|--------|-------------|-------|
| `cli/commands/agents/agents.py` | `baton agents` | Chain 1/3 |
| `cli/commands/agents/events.py` | `baton events` | Chain 2/4 |
| `cli/commands/agents/incident.py` | `baton incident` | Experimental |
| `cli/commands/agents/route.py` | `baton route` | Chain 1 |
| `cli/commands/distribute/install.py` | `baton install` | Chain 10 |
| `cli/commands/distribute/package.py` | `baton package` | Chain 10 |
| `cli/commands/distribute/publish.py` | `baton publish` | Chain 10 |
| `cli/commands/distribute/pull.py` | `baton pull` | Chain 10 |
| `cli/commands/distribute/transfer.py` | `baton transfer` | Experimental |
| `cli/commands/execution/async_cmd.py` | `baton async` | Chain 8 |
| `cli/commands/execution/decide.py` | `baton decide` | Chain 2/8 |
| `cli/commands/execution/status.py` | `baton status` | Chain 2 |
| `cli/commands/govern/classify.py` | `baton classify` | Chain 6 |
| `cli/commands/govern/compliance.py` | `baton compliance` | Chain 6 |
| `cli/commands/govern/detect.py` | `baton detect` | Chain 6 |
| `cli/commands/govern/escalations.py` | `baton escalations` | Chain 6 |
| `cli/commands/govern/policy.py` | `baton policy` | Chain 6 |
| `cli/commands/govern/spec_check.py` | `baton spec-check` | Chain 6 |
| `cli/commands/govern/validate.py` | `baton validate` | Chain 6 |
| `cli/commands/improve/anomalies.py` | `baton anomalies` | Chain 5 |
| `cli/commands/improve/budget.py` | `baton budget` | Chain 5 |
| `cli/commands/improve/changelog.py` | `baton changelog` | Chain 5 |
| `cli/commands/improve/evolve.py` | `baton evolve` | Chain 5 |
| `cli/commands/improve/experiment.py` | `baton experiment` | Chain 5 |
| `cli/commands/improve/improve_cmd.py` | `baton improve` | Chain 5 |
| `cli/commands/improve/patterns.py` | `baton patterns` | Chain 5 |
| `cli/commands/improve/scores.py` | `baton scores` | Chain 5 |
| `cli/commands/observe/cleanup.py` | `baton cleanup` | Chain 7 |
| `cli/commands/observe/context_profile.py` | `baton context-profile` | Chain 7 |
| `cli/commands/observe/dashboard.py` | `baton dashboard` | Chain 7 |
| `cli/commands/observe/retro.py` | `baton retro` | Chain 7 |
| `cli/commands/observe/telemetry.py` | `baton telemetry` | Chain 7 |
| `cli/commands/observe/trace.py` | `baton trace` | Chain 7 |
| `cli/commands/observe/usage.py` | `baton usage` | Chain 7 |
| `cli/commands/pmo_cmd.py` | `baton pmo` | Chain 9 |
| `cli/commands/serve.py` | `baton serve` | Chain 11 |
| `cli/commands/sync_cmd.py` | `baton sync` | Chain 4 |

**Disposition: KEEP — all are active CLI surface.**

### Note on `cli/commands/verify_package.py`

`cli/commands/verify_package.py` is a backward-compatibility shim that re-exports `register` and `handler` from `cli/commands/distribute/verify_package.py`. Both modules are loaded by dynamic discovery, meaning the `baton verify-package` command registers twice. This causes a duplicate subcommand registration at startup. The shim was found to contain:

```python
from agent_baton.cli.commands.distribute.verify_package import handler, register
```

**Disposition: REVIEW — likely causes duplicate subcommand; shim should be removed or the discovery logic should skip shims.**

### Core Module: `core/events/events.py`

This module was flagged as unimported by full-dotted-name grep because it is always imported using a package alias pattern:

```python
from agent_baton.core.events import events as evt
```

Importers: `core/engine/executor.py`, `core/runtime/decisions.py`, `core/runtime/worker.py`, `tests/test_events.py`.

**Disposition: KEEP — active, well-used module. False positive from grep methodology.**

### True Orphans (No External Importers)

After filtering CLI dynamic-discovery modules and alias-imported modules, the following modules have no confirmed importers outside their own package:

#### `cli/commands/observe/migrate_storage.py`
- **What it does:** `baton migrate-storage` — one-time migration of legacy JSON files to SQLite.
- **Import status:** Dynamically loaded by `discover_commands()`.
- **Chain coverage:** Touches Chain 2 (Execution Lifecycle) and Chain 9 (PMO). Covered indirectly.
- **Disposition: KEEP** — operational utility, part of the SQLite migration path.

#### `cli/commands/observe/context_cmd.py`
- **What it does:** `baton context` — show or update shared context.
- **Import status:** Dynamically loaded. Not covered by any chain audit (not in chains 1-12 taxonomy).
- **Disposition: KEEP, ADD TO CHAIN COVERAGE** — real functionality around shared context; should be added to Chain 7 (Observability) coverage.

#### `cli/commands/observe/query.py`
- **What it does:** `baton query` — ad-hoc SQL against central.db.
- **Import status:** Dynamically loaded. Covered implicitly by Chain 4 (Federated Sync) but not by a named chain link.
- **Disposition: KEEP, ADD TO CHAIN COVERAGE** — active command, should be part of Chain 4 audit.

### Modules With Missing Chain Coverage

The following modules are imported by other code (not orphans) but were not audited under chains 1-12. They represent real functionality with a coverage gap:

| Module | Functionality | Missing From Chain |
|--------|--------------|-------------------|
| `core/observe/archiver.py` | Archive/cleanup of old execution data | Chain 7 (Observability) |
| `core/runtime/scheduler.py` | Task scheduling (cron-style) | Chain 8 (Daemon) |
| `core/runtime/context.py` | `ExecutionContext` factory | Chain 2/8 |
| `core/storage/migrate.py` | Schema migrations | Chain 2/9 |
| `core/storage/queries.py` | Cross-table SQL queries | Chain 4/9/11 |
| `core/storage/connection.py` | SQLite connection management | All chains using SQLite |
| `core/storage/file_backend.py` | `FileStorage` backend | Chain 2 (legacy path) |
| `core/storage/adapters/ado.py` | Azure DevOps adapter | Chain 12 |
| `core/govern/escalation.py` | `EscalationManager` | Chain 6 |
| `core/govern/spec_validator.py` | Spec validation | Chain 6 |
| `core/govern/validator.py` | Input validation helpers | Chain 6 |
| `core/improve/vcs.py` | VCS integration for rollback | Chain 5 |
| `core/improve/proposals.py` | Proposal persistence | Chain 5 |
| `core/improve/rollback.py` | Rollback + circuit breaker | Chain 5 |
| `core/improve/experiments.py` | Experiment tracking | Chain 5 |
| `core/improve/triggers.py` | Trigger evaluation | Chain 5 |
| `core/distribute/experimental/async_dispatch.py` | Async agent dispatch | Experimental — no chain |
| `core/distribute/experimental/incident.py` | Incident management | Experimental — no chain |
| `core/distribute/experimental/transfer.py` | Agent transfer | Experimental — no chain |
| `models/parallel.py` | Parallel execution models | Chain 2 |
| `models/reference.py` | Reference document models | Chain 1/3 |
| `models/registry.py` | Registry models | Chain 1 |
| `models/context_profile.py` | Context profile models | Chain 7 |
| `models/feedback.py` | Retrospective feedback models | Chain 5/7 |
| `utils/frontmatter.py` | YAML/markdown frontmatter parser | Chain 1/3/10 |

**Disposition: KEEP — all are imported by production code. Add to chain coverage in subsequent audit passes.**

### Dead Code Candidates

No modules were found that are both:
1. Not imported by any other module, AND
2. Not loaded dynamically by `discover_commands()`

The `cli/commands/verify_package.py` shim is the only structural anomaly. It imports from and duplicates `cli/commands/distribute/verify_package.py`. Whether it causes a runtime error depends on whether argparse allows duplicate subcommand registration (it typically silently uses the last-registered one).

**Disposition: INVESTIGATE AND REMOVE** the shim at `cli/commands/verify_package.py`, keeping only `cli/commands/distribute/verify_package.py`.

---

## Summary of All Findings

### Cross-Chain Composition Defects

| # | Defect | Severity | Affected Compositions |
|---|--------|----------|----------------------|
| CC-1 | `self._trace` lost between CLI calls; `complete()` never writes a trace | High | Comp 1 |
| CC-2 | `_knowledge_resolver` never injected into `ExecutionEngine`; auto-resolve dead | Medium | Comp 2 |
| CC-3 | `EventPersistence` not subscribed when `storage is not None`; all events ephemeral | High | Comp 4 |
| CC-4 | `PerformanceScorer` reads filesystem; retrospectives in SQLite mode not accessible | Medium | Comp 3 |

### Orphan Analysis Results

| Category | Count | Disposition |
|----------|-------|-------------|
| CLI dynamic-discovery modules (pseudo-orphans) | 37 | KEEP — active CLI surface |
| `core/events/events.py` false positive | 1 | KEEP — alias-imported |
| `cli/commands/verify_package.py` shim | 1 | INVESTIGATE AND REMOVE |
| Modules missing from chain coverage | 25 | KEEP + add coverage |
| True dead code | 0 | N/A |

### Prioritized Defect Backlog

**P0 — Trace never written (CC-1):**
Fix `complete()` to attempt loading the trace from disk before giving up:
```python
if self._trace is None:
    existing = self._tracer.load_trace(state.task_id)
    if existing is not None:
        self._trace = existing
```
This mirrors the pattern already in `resume()` (lines 779-787) and would make CLI-mode traces work correctly.

**P0 — EventPersistence not wired for FileStorage (CC-3):**
The guard `if storage is not None:` was intended for SQLite mode but also applies to FileStorage. Fix by checking the backend type:
```python
if isinstance(storage, SqliteStorage):
    self._event_persistence = None
else:
    # File mode OR no storage — wire EventPersistence
    if self._bus is not None:
        self._event_persistence = EventPersistence(events_dir=events_dir)
        self._bus.subscribe("*", self._event_persistence.append)
```
Or, simpler: always wire EventPersistence as a bus subscriber, regardless of storage backend. Events and storage are independent layers.

**P1 — `_knowledge_resolver` not injected (CC-2):**
Add `knowledge_resolver` as a constructor parameter to `ExecutionEngine`. The CLI's `execute.py:start` handler should receive the resolver from `plan_cmd.py` context, or re-build it from the saved plan's knowledge registry paths. Minimum fix: expose a `set_knowledge_resolver(resolver)` method and call it from CLI after engine construction.

**P2 — PerformanceScorer/retrospective mode divergence (CC-4):**
`PerformanceScorer` should accept a storage backend as an alternative data source. When SQLite mode is active, `score_agent()` should query `baton.db:retrospectives` rather than scanning filesystem markdown.

**P3 — Duplicate `verify-package` subcommand (orphan):**
Remove `cli/commands/verify_package.py` (the top-level shim). The canonical implementation at `cli/commands/distribute/verify_package.py` is sufficient.
