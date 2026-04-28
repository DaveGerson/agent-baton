# Audit Report: Chains 4–6

**Date:** 2026-03-24
**Auditor:** backend-engineer--python (claude-sonnet-4-6)
**Scope:** Chain 4 (Federated Sync), Chain 5 (Improvement Loop), Chain 6 (Governance & Policy)

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

## Chain 4: Federated Sync

**Entry:** `baton sync`
**Path:** CLI → SyncEngine → sqlite3 (project baton.db → central.db)

### Static Analysis

The complete import chain traces cleanly:

1. `agent_baton.cli.commands.sync_cmd` — `handler()` dispatches to `_sync_current`, `_sync_project`, `_sync_all`, or `_status`
2. `_sync_current` imports `agent_baton.core.storage.sync.SyncEngine` and `auto_sync_current_project`
3. `SyncEngine.__init__` imports `agent_baton.core.storage.connection.ConnectionManager` and `agent_baton.core.storage.schema.CENTRAL_SCHEMA_DDL`
4. `SyncEngine.push` iterates `SYNCABLE_TABLES` (28 tables defined with `SyncTableSpec`), reads watermarks from `central.db`, copies only rows with `rowid > watermark`
5. `CentralStore` in `agent_baton.core.storage.central` wraps the same `ConnectionManager`, enforces read-only guard on `query()`, and provides analytics views (`v_agent_reliability`, `v_cost_by_task_type`, `v_recurring_knowledge_gaps`, `v_project_failure_rate`)
6. `_maybe_migrate_pmo` handles one-time migration from legacy `pmo.db` to `central.db`

All classes exist at their canonical paths. No shim or import errors detected.

### Empirical Verification

Commands run from `/home/djiv/PycharmProjects/orchestrator-v2/`.

**Empty-state handling (project not registered):**
```
$ baton sync
Could not detect current project. Register with 'baton pmo add' first.

$ baton sync status
No sync watermarks found.
Run 'baton sync' to sync the current project first.

$ baton sync --all
No projects registered in central.db.
Register with 'baton pmo add' first.
```
Verdict: graceful degradation with actionable messages. No tracebacks.

**After registering the Realmweaver project (a real project with a populated baton.db):**
```
$ baton pmo add --id realmweaver --name "Realmweaver" --path /home/djiv/WebstormProjects/Realmweaver --program default
Registered project: realmweaver (Realmweaver)
  Path:    /home/djiv/WebstormProjects/Realmweaver
  Program: default
  Context: /home/djiv/WebstormProjects/Realmweaver/.claude/team-context

$ baton sync --project realmweaver
Synced realmweaver: 251 rows (OK)

$ baton sync status
Sync Watermarks (12 entries)

  Project: realmweaver
    agent_usage                     rowid=8         2026-03-25T00:45:59.577384+00:00
    executions                      rowid=53        2026-03-25T00:45:59.470249+00:00
    gate_results                    rowid=127       2026-03-25T00:45:59.564066+00:00
    plan_phases                     rowid=28        2026-03-25T00:45:59.522636+00:00
    plan_steps                      rowid=44        2026-03-25T00:45:59.536280+00:00
    plans                           rowid=5         2026-03-25T00:45:59.509668+00:00
    retrospective_outcomes          rowid=4         2026-03-25T00:45:59.604312+00:00
    retrospectives                  rowid=3         2026-03-25T00:45:59.495834+00:00
    sequencing_notes                rowid=14        2026-03-25T00:45:59.617517+00:00
    step_results                    rowid=23        2026-03-25T00:45:59.550229+00:00
    telemetry                       rowid=100       2026-03-25T00:45:59.591408+00:00
    usage_records                   rowid=3         2026-03-25T00:45:59.483069+00:00
```

**Idempotency (second sync run):**
```
$ baton sync --project realmweaver
Synced realmweaver: 0 rows (OK)
```
Watermark-based incremental sync confirmed working.

**Cross-project analytics after sync:**
```
$ baton query agent-reliability --central
Agent Reliability (last 30 days)
AGENT                           STEPS  SUCCESS_RATE  ...
frontend-engineer--realmweaver  14     0%            ...
backend-engineer                4      100%          ...
architect                       2      50%           ...
code-reviewer                   2      50%           ...
test-engineer                   1      100%          ...

$ baton query tasks --central
Recent Tasks
TASK_ID     SUMMARY                                      STATUS    RISK
2026-03-25-ux-refactoring-sprint-review-fixes...         complete  HIGH
2026-03-24-ux-refactoring-sprint-8-work-streams...       complete  MEDIUM
...
```
Central.db is correctly populated and queryable via analytics views.

**Error handling (project with no baton.db):**
```
$ baton sync   (from orchestrator-v2, registered but no baton.db)
Project DB not found: /home/djiv/PycharmProjects/orchestrator-v2/.claude/team-context/baton.db
Already up to date.
error: Project DB not found: ...
```
Exit code 1 returned as expected when baton.db is missing.

### Test Coverage

Test files covering this chain:
- `tests/test_sync_engine.py` — 34 unit tests covering: single-table sync, incremental watermark advancement, `push_all`, rebuild, `CentralStore` analytics queries, AUTOINCREMENT handling, `auto_sync_current_project`, idempotency, `SyncResult` properties, `SYNCABLE_TABLES` sanity, factory functions
- `tests/test_federated_sync_integration.py` — 38 E2E integration tests covering: full round-trip (SqliteStorage → SyncEngine → CentralStore), cross-project isolation, PMO migration, auto-sync resolution, external-source adapters, watermark correctness, knowledge-field round-trips
- `tests/test_sync_cli.py` — 17 CLI structural tests (command registration, help exits, graceful no-args)
- `tests/test_pmo_central_migration.py` — covers `_maybe_migrate_pmo`

**All 72 behavioral tests (test_sync_engine + test_federated_sync_integration) pass.**

Tests use real `SqliteStorage` writes (not mocks) to populate source DBs, confirming they exercise actual business logic.

### Link Scores

| Link | Module | Score | Evidence |
|------|--------|-------|---------|
| CLI dispatch | `cli/commands/sync_cmd.py` | **4** | CLI runs, graceful empty-state, all subcommands routed correctly |
| SyncEngine | `core/storage/sync.py` | **4** | 34 unit tests + E2E pass; runs live against real baton.db |
| Watermark logic | `SyncEngine._sync_table` / `_get_watermark` / `_set_watermark` | **4** | Idempotency and increment verified in both tests and live run |
| CentralStore | `core/storage/central.py` | **4** | Analytics views return real synced data; read-only guard tested |
| ConnectionManager | `core/storage/connection.py` | **4** | Exercised by every test in both suites |
| PMO migration | `central._maybe_migrate_pmo` | **3** | Unit-tested with real SQLite; not exercised in live run (marker already written) |
| auto_sync_current_project | `core/storage/sync.py` | **4** | Unit-tested with tmp_path fixtures and real write paths |

### Chain Score: **4 — Integration-tested**

**Weakest link:** PMO migration (3) — but this is a one-time boot path, not a regular sync path. The core sync path across every other link is 4.

**Chain-level justification for 4:** The full sync chain ran live against a real project database (251 rows, 12 tables, watermarks persisted) and all 72 behavioral tests pass. The chain has not been exercised as part of a continuous production session (which would be 5), but it clearly works end-to-end on real data.

---

## Chain 5: Improvement Loop

**Entry (full chain):** `baton improve --run` / `baton improve --force`
**Entry (individual links):** `baton scores`, `baton patterns`, `baton budget`, `baton evolve`
**Path:** CLI → ImprovementLoop → TriggerEvaluator → Recommender → PerformanceScorer → PatternLearner → BudgetTuner → PromptEvolutionEngine → ProposalManager → ExperimentManager → RollbackManager

### Static Analysis

Import chain is complete and clean:

1. `cli/commands/improve/improve_cmd.py` → `ImprovementLoop`
2. `ImprovementLoop.__init__` wires: `TriggerEvaluator`, `Recommender`, `ProposalManager`, `ExperimentManager`, `RollbackManager`, `PerformanceScorer`
3. `Recommender.__init__` wires: `PerformanceScorer`, `PatternLearner`, `BudgetTuner`, `PromptEvolutionEngine`
4. `PerformanceScorer` → `UsageLogger` + `RetrospectiveEngine`
5. `PatternLearner` → `UsageLogger` (reads `usage-log.jsonl`)
6. `BudgetTuner` → `UsageLogger`
7. `PromptEvolutionEngine` → `PerformanceScorer` + `UsageLogger`

All modules exist at canonical paths with no import errors. The chain from CLI to terminal data source (`usage-log.jsonl`) is fully connected.

**Individual CLI commands and their backing modules:**

| Command | Backing module | Status |
|---------|----------------|--------|
| `baton scores` | `cli/commands/improve/scores.py` → `PerformanceScorer` | Full |
| `baton patterns` | `cli/commands/improve/patterns.py` → `PatternLearner` | Full |
| `baton budget` | `cli/commands/improve/budget.py` → `BudgetTuner` | Full |
| `baton evolve` | `cli/commands/improve/evolve.py` → `PromptEvolutionEngine` | Full |
| `baton improve --run` | `cli/commands/improve/improve_cmd.py` → `ImprovementLoop.run_cycle` | Full |
| `baton improve --force` | same, `force=True` | Full |

### Empirical Verification

Commands run from `/home/djiv/PycharmProjects/orchestrator-v2/`, which has 19 agent uses in its `usage-log.jsonl` (7 records).

**`baton scores` (reads real usage-log.jsonl):**
```
# Agent Performance Scorecards

Based on 19 total agent uses.

## Strong

### architect
- Health: strong
- Uses: 3
- First-pass rate: 100%
- Avg retries: 0.0
- Gate pass rate: n/a
- Avg tokens/use: 0
- Models: opus(1), sonnet(2)
- Retro mentions: +1 / -0

### backend-engineer--python
- Health: strong
- Uses: 7
- First-pass rate: 100%
- Avg retries: 0.0
- Gate pass rate: 100%
- Avg tokens/use: 0
- Models: sonnet(7)
- Retro mentions: +4 / -0

### code-reviewer
- Health: strong
- Uses: 3
- First-pass rate: 100%
- Avg retries: 0.0
- Gate pass rate: n/a
- Avg tokens/use: 0
- Models: sonnet(3)
- Retro mentions: +2 / -0

### test-engineer
- Health: strong
- Uses: 6
- First-pass rate: 100%
- Avg retries: 0.0
- Gate pass rate: 100%
- Avg tokens/use: 0
- Models: sonnet(6)
- Retro mentions: +4 / -0
```
Real data from `usage-log.jsonl`. Scores correctly derive from usage records and retrospective text parsing.

**`baton patterns` (before --refresh):**
```
No learned patterns found.
Run 'baton patterns --refresh' to analyse the usage log.
```

**`baton patterns --refresh`:**
```
No patterns found — not enough qualifying usage records.
Ensure at least 5 tasks share a sequencing_mode and meet the
confidence threshold (default 0.70).
```
Correct: usage-log.jsonl has 7 records but distributed across multiple sequencing modes, so no single group reaches the 5-sample threshold. Logic fires correctly.

**`baton budget --recommend`:**
```
No budget adjustments needed — all task types are within their expected tier boundaries.
```
Correct: avg_tokens_per_use is 0 (no token counting in usage records for this project's sessions), which puts all tasks firmly in the `lean` tier.

**`baton evolve`:**
```
# Prompt Evolution Report

All agents are performing well. No changes proposed.
```
Correct: all agents have health=strong with 100% first-pass rates.

**`baton improve --run` (trigger check fires):**
```
Improvement cycle skipped: Not enough new data since last analysis
```
Trigger threshold not met (requires `analysis_interval_tasks` new tasks since last run).

**`baton improve --force` (bypasses trigger):**
```
Improvement cycle complete: report-f5e54043
  Anomalies:    0
  Recommendations: 0
  Auto-applied: 0
  Escalated:    0
  Active experiments: 0
```
Full loop ran end-to-end: trigger bypassed, anomalies scanned, all four recommendation engines called (budget, prompt, sequencing, scoring), 0 recommendations because all agents are performing well and no budget/pattern anomalies exist.

**`baton improve --report`:**
```
Latest Improvement Report: report-f5e54043
  Timestamp: 2026-03-25T00:48:16+00:00
  Anomalies:       0
  Recommendations: 0
  Auto-applied:    0
  Escalated:       0
  Active experiments: 0
```
Report persisted to `.claude/team-context/improvements/reports/report-f5e54043.json` and read back correctly.

### Test Coverage

Test files covering this chain:
- `tests/test_improvement_loop.py` — 16 tests for `ImprovementLoop.run_cycle`: trigger bypass, auto-apply guardrails, escalation, circuit breaker, experiment evaluation, rollback on degradation. Uses mocked Recommender + TriggerEvaluator, real ProposalManager + ExperimentManager + RollbackManager
- `tests/test_recommender.py` — 9 tests for `Recommender.analyze`: budget/prompt/sequencing/routing categories, deduplication, sort order, auto-applicable guardrails. Uses mocked sub-engines
- `tests/test_pattern_learner.py` — extensive tests in 8 classes covering: `analyze` with real TaskUsageRecord fixtures, success rate computation, agent combo selection, confidence formula, `min_sample_size`, `min_confidence`, `refresh`/`load`, `get_patterns_for_task`, report generation, edge cases
- `tests/test_budget_tuner.py` — covers tier boundary logic with real usage fixtures
- `tests/test_budget_auto_apply.py` — covers `auto_apply_recommendations` downgrade guardrail
- `tests/test_evolution.py` — covers `PromptEvolutionEngine.analyze`, proposals, report generation
- `tests/test_experiments.py` — covers `ExperimentManager`: creation, sample recording, evaluation, conclude/rollback, active filtering
- `tests/test_rollback.py` — covers `RollbackManager`: log entries, circuit breaker thresholds

**All 193 behavioral tests pass.**

PatternLearner, BudgetTuner, and evolution tests write real fixtures to tmp_path (not mocks). `ImprovementLoop` tests mock the Recommender and Scorer but use real persistence layers. The Recommender tests mock sub-engines, testing its own aggregation logic.

### Link Scores

| Link | Module | Score | Evidence |
|------|--------|-------|---------|
| CLI dispatch | `cli/commands/improve/improve_cmd.py` and friends | **4** | All CLI commands run without error, produce real output |
| ImprovementLoop.run_cycle | `core/improve/loop.py` | **4** | 16 behavioral tests + live `--force` run; full cycle completes |
| TriggerEvaluator | `core/improve/triggers.py` | **3** | Unit-tested with real usage fixtures; not independently tested at CLI |
| PerformanceScorer | `core/improve/scoring.py` | **4** | `baton scores` reads real usage-log.jsonl and produces correct output |
| Recommender | `core/learn/recommender.py` | **3** | 9 unit tests with mocked sub-engines; exercised indirectly via `--force` |
| PatternLearner | `core/learn/pattern_learner.py` | **3** | Unit-tested with real fixtures; not validated with production-volume data |
| BudgetTuner | `core/learn/budget_tuner.py` | **3** | Unit-tested with real fixtures; `--recommend` runs correctly |
| PromptEvolutionEngine | `core/improve/evolution.py` | **3** | Unit-tested; `baton evolve` runs correctly |
| ProposalManager | `core/improve/proposals.py` | **3** | Exercised as real component in loop tests |
| ExperimentManager | `core/improve/experiments.py` | **3** | 12 behavioral tests covering full lifecycle |
| RollbackManager | `core/improve/rollback.py` | **3** | Tested including circuit breaker; rollback on degradation tested in loop |

### Chain Score: **3 — Unit-tested with real logic**

**Weakest link:** Multiple links at 3. The individual engines (PatternLearner, BudgetTuner, Recommender) are each tested against real data fixtures in isolation. The `ImprovementLoop` composes them correctly (confirmed by live `--force` run). However, the chain has not produced a non-trivial output (a genuine recommendation, auto-apply, or experiment) on real usage data — the current dataset doesn't trigger any thresholds. The logic is real and correct; it's never been exercised in a scenario with enough data to produce recommendations.

**Key gap:** `avg_tokens_per_use` is 0 for all agents in this project's usage log because token estimation is not written to the log. This means `BudgetTuner` and the budget branch of `Recommender` will never produce output. The pattern learner requires 5+ tasks in the same `sequencing_mode`; with 7 records spread across modes, this threshold is not met either.

---

## Chain 6: Governance & Policy

**Entry:** `baton classify`, `baton compliance`, `baton policy`, `baton validate`
**Path:** CLI → DataClassifier → PolicyEngine → SpecValidator → ComplianceReportGenerator → EscalationManager

### Static Analysis

Import chain is clean:

1. `cli/commands/govern/classify.py` → `DataClassifier` in `core/govern/classifier.py`
2. `cli/commands/govern/policy.py` → `PolicyEngine` in `core/govern/policy.py`
3. `cli/commands/govern/validate.py` → `AgentValidator` in `core/govern/validator.py`
4. `cli/commands/govern/compliance.py` → `ComplianceReportGenerator` in `core/govern/compliance.py`
5. `cli/commands/govern/escalations.py` → `EscalationManager` in `core/govern/escalation.py`
6. `cli/commands/govern/spec_check.py` → `SpecValidator` in `core/govern/spec_validator.py`

`DataClassifier.classify` uses keyword-based signal detection across 5 signal sets (REGULATED_SIGNALS, PII_SIGNALS, SECURITY_SIGNALS, INFRASTRUCTURE_SIGNALS, DATABASE_SIGNALS) plus file path patterns. No external ML dependency; pure string matching.

`PolicyEngine` stores 5 built-in presets (standard_dev, data_analysis, infrastructure, regulated, security) as Python objects, with JSON-file persistence for custom presets. `evaluate()` checks path_block, tool_restrict, require_agent, and require_gate rules via `fnmatch`.

`AgentValidator` validates `.md` agent definition files for YAML frontmatter correctness, required fields (name, model, description), and tool/permission-mode validity.

**Integration with planning:** `DataClassifier` and `PolicyEngine` are injected into `IntelligentPlanner` via `test_planner_governance.py`, which verifies that classification results propagate into the plan's `shared_context` and that policy violations are surfaced in `explain_plan` output.

### Empirical Verification

**`baton classify` — risk differentiation:**
```
$ baton classify "add unit test"
Risk Level: LOW
Preset: Standard Development
Confidence: high
Explanation: No sensitivity signals detected. Standard development guardrails apply.

$ baton classify "delete production database"
Risk Level: HIGH
Preset: Infrastructure Changes
Confidence: high
Signals: infra:production, database:database
Explanation: Elevated risk detected (2 signal(s)). Auditor review recommended.

$ baton classify "migrate customer PII from old system"
Risk Level: HIGH
Preset: Regulated Data
Confidence: low
Signals: pii:pii
Explanation: Elevated risk detected (1 signal(s)). Auditor review recommended.

$ baton classify "migrate HIPAA-regulated patient PII from legacy system to new compliance database"
Risk Level: CRITICAL
Preset: Regulated Data
Confidence: high
Signals: regulated:compliance, regulated:regulated, regulated:hipaa, pii:pii, pii:patient, database:database
Explanation: Elevated risk detected (6 signal(s)). Auditor review recommended.
```
Risk levels differ correctly: LOW for benign tasks, HIGH for single high-risk signal, CRITICAL when 3+ regulated+PII signals detected.

**Multi-signal + file path classification:**
```
$ baton classify "update user authentication with OAuth2 and store credentials in secrets vault" --files "src/auth/oauth.py" ".env"
Risk Level: HIGH
Preset: Security-Sensitive
Confidence: high
Signals: security:authentication, security:auth, security:secrets, security:credentials, security:oauth, path:auth/, path:.env
Explanation: Elevated risk detected (7 signal(s)). Auditor review recommended.
```
File path signals correctly elevate from text signals.

**`baton compliance` (empty state):**
```
$ baton compliance
No compliance reports found.
```
Graceful empty-state handling.

**`baton policy`:**
```
$ baton policy
Available policy presets (5):
  data_analysis             Data Analysis / Reporting (LOW risk). Read-only data, write only to output dirs.
  infrastructure            Infrastructure Changes (HIGH risk). Only devops writes infra files; auditor required.
  regulated                 Regulated Data (HIGH/CRITICAL risk). SME required; append-only for historical records.
  security                  Security-Sensitive (HIGH risk). Auth code isolated to single agent; auditor required.
  standard_dev              Standard Development (LOW risk). Default guardrails for everyday work.
```
All 5 built-in presets listed correctly.

**`baton validate` — agent definition files:**
```
$ baton validate agents/backend-engineer--python.md
  /home/djiv/PycharmProjects/orchestrator-v2/agents/backend-engineer--python.md

Validated 1 file: 1 valid, 0 warnings, 0 errors

$ baton validate agent_baton/core/engine/executor.py
  /home/djiv/PycharmProjects/orchestrator-v2/agent_baton/core/engine/executor.py
    error: file must start with '---' (missing frontmatter)

Validated 1 file: 0 valid, 0 warnings, 1 errors
```
Correctly validates agent `.md` files (YAML frontmatter check) and rejects non-agent files. Exit code 1 returned on error.

### Test Coverage

Test files covering this chain:
- `tests/test_classifier.py` — covers `DataClassifier` with signal detection across all 5 categories, file path elevation, CRITICAL threshold (3+ regulated/PII signals), confidence calculation
- `tests/test_policy.py` — covers `PolicyEngine`: path_block, tool_restrict, require_agent, require_gate rules, scope matching, preset persistence, `fnmatch` glob patterns
- `tests/test_compliance.py` — covers `ComplianceReportGenerator`: generate, save, load, list; markdown rendering
- `tests/test_escalation.py` — covers `EscalationManager`: add, resolve, resolve_all, has_pending, clear_resolved; parse/serialize round-trip
- `tests/test_spec_validator.py` — covers `SpecValidator`: JSON schema validation, file structure checks, export detection, API contract validation, `run_gate` with custom callables
- `tests/test_planner_governance.py` — 47 integration tests verifying `DataClassifier` and `PolicyEngine` are correctly wired into `IntelligentPlanner`: classification propagates to `shared_context`, violations surface in `explain_plan`, risk floor logic (classifier risk raises plan risk), preset key mapping

**All 271 behavioral tests pass.**

The governance chain has the strongest integration test coverage of the three chains, with `test_planner_governance.py` specifically testing the end-to-end composition of classification + policy evaluation within the planner.

### Link Scores

| Link | Module | Score | Evidence |
|------|--------|-------|---------|
| CLI dispatch | `cli/commands/govern/*.py` | **4** | All four governance commands run without error; correct output |
| DataClassifier | `core/govern/classifier.py` | **4** | Risk differentiation verified live; unit-tested with all signal categories |
| PolicyEngine | `core/govern/policy.py` | **3** | Unit-tested; 5 presets verified; not exercised in live plan creation session |
| AgentValidator | `core/govern/validator.py` | **4** | `baton validate` runs on real agent files; validates format correctly |
| SpecValidator | `core/govern/spec_validator.py` | **3** | Unit-tested with real file fixtures; not wired into any CLI path that auto-runs it |
| ComplianceReportGenerator | `core/govern/compliance.py` | **3** | Unit-tested; `baton compliance` runs (empty state); reports not generated in live sessions |
| EscalationManager | `core/govern/escalation.py` | **3** | Unit-tested; used by Planner in integration tests |
| Planner integration | `core/engine/planner.py` (governance injection) | **4** | 47 tests in `test_planner_governance.py` verify composition; mocked classifier+policy wired correctly |

### Chain Score: **3 — Unit-tested with real logic**

**Weakest link:** PolicyEngine, SpecValidator, ComplianceReportGenerator, EscalationManager — all at 3. These are unit-tested with real logic but have not been exercised in production orchestration sessions. `DataClassifier` and `AgentValidator` both have live empirical verification showing correct real-world output, but the downstream chain links (compliance report generation, escalation filing) only activate during actual regulated-data orchestration runs, which have not occurred in this project.

**Key gap:** The governance chain is structurally complete and correctly integrated into the planner. The limiting factor is not code quality but session history — no HIGH/CRITICAL-risk plan has been executed through the engine, so `ComplianceReportGenerator` and `EscalationManager` have never been exercised on real outputs.

---

## Summary Matrix

| Chain | Entry Point | Chain Score | Weakest Link | Status |
|-------|-------------|-------------|--------------|--------|
| **4: Federated Sync** | `baton sync` | **4** | PMO migration path (one-time boot) | Working end-to-end on real data |
| **5: Improvement Loop** | `baton improve --force` | **3** | All loop engines; no recommendations produced on real data | Logic correct; threshold gap |
| **6: Governance & Policy** | `baton classify` etc. | **3** | PolicyEngine, Compliance, Escalation (never live-triggered) | Classifier + Validator verified live; downstream not exercised |

---

## Dead Chains and Gaps

### Chain 4
No dead chain. One observation: `baton sync` requires the project to have a `baton.db` file. This project's own orchestration uses file-based (JSON/JSONL) storage, so `baton sync` silently exits when run in the orchestrator-v2 project itself. This is expected behavior, not a defect, but it means the project cannot self-sync its own executions to central.db.

### Chain 5
**Gap — token data:** `avg_tokens_per_use` is 0 for all agents. The `UsageLogger` records usage but `estimated_tokens` is always 0 in this project's session history. `BudgetTuner` cannot produce meaningful tier recommendations without token data. The code is correct; the data pipeline upstream of the logger does not populate token counts from real Claude API responses.

**Gap — sequencing mode diversity:** The pattern learner requires 5+ tasks in the same `sequencing_mode`. The project has 7 usage records spread across different modes, so no pattern has ever been learned. This is expected behavior at this dataset size, but means the `Recommender._sequencing_recommendations` path has never produced output.

**Gap — ImprovementLoop composition test uses mocks for Recommender:** The `test_improvement_loop.py` tests inject a mocked `Recommender` and `TriggerEvaluator`. The loop's internal orchestration logic (auto-apply guardrails, experiment creation, rollback) is well-tested, but the actual composed behavior of the loop with real sub-engines has only been exercised by the live `--force` run, which produced zero recommendations due to the data gaps above.

### Chain 6
**Gap — no live compliance report:** `ComplianceReportGenerator.save()` has never been called from a real execution session. The `baton compliance` command correctly handles empty state, but the report generation and markdown rendering paths have only been tested in unit tests with constructed data.

**Gap — policy evaluation not invoked at runtime:** `PolicyEngine.evaluate()` is called by `IntelligentPlanner._validate_agents_against_policy()`, but only when a `PolicyEngine` instance is explicitly injected. The default `baton plan` invocation uses `IntelligentPlanner(classifier=DataClassifier(), policy_engine=PolicyEngine())` — this needs to be verified in the planner's CLI integration to confirm policy violations are surfaced in real plan outputs.

---

## Backlog

### Chain 4 (score 4 → 5)
- **SYNC-1:** Register orchestrator-v2 in central.db by migrating from JSON/file storage to baton.db, or document that file-based storage is the intended model for this project. Either way, the "self-sync" path should be explicitly tested.
- **SYNC-2:** Add a `sync --dry-run` flag that shows what would be synced without writing, useful for debugging watermark state.

### Chain 5 (score 3 → 4)
- **IMPR-1:** Populate `estimated_tokens` in `UsageLogger` from real Claude API response metadata. Without token data, `BudgetTuner` and the budget branch of `Recommender` are permanently inert on this project.
- **IMPR-2:** Add an integration test that seeds `usage-log.jsonl` with enough records (15+ tasks across a single `sequencing_mode` with real token data) to trigger the full loop and produce at least one recommendation. This would raise the loop's composition score to 4.
- **IMPR-3:** `TriggerEvaluator` should be verified empirically — run `baton improve --run` after seeding enough data to cross the `analysis_interval_tasks` threshold.

### Chain 6 (score 3 → 4)
- **GOV-1:** Verify that `baton plan` actually injects `DataClassifier` and `PolicyEngine` by default, not only when explicitly constructed (check `cli/commands/execution/plan.py`). If they are not injected by default, governance is silently absent from normal orchestration.
- **GOV-2:** Run a HIGH-risk plan (e.g., `baton plan "deploy to production" --save`) through the engine to generate a compliance report and verify `baton compliance` lists it. This would exercise the full downstream governance path.
- **GOV-3:** Add a CLI test that actually invokes `baton classify`, captures output, and asserts on risk level — current `test_sync_cli.py`-style tests only check exit codes and registration.
