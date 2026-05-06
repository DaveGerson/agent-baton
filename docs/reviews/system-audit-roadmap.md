# System Audit Roadmap -- Agent Baton Orchestration Engine

**Date**: 2026-04-30
**Synthesized from**: 9 domain audits (audit-01 through audit-09)
**Purpose**: Prioritized remediation plan executable without re-reading the source audits

---

## Part 1: System Health Dashboard

### Consolidated Scorecard

| # | Dimension | 01-Engine | 02-Planning | 03-Governance | 04-CLI | 05-API/PMO | 06-Agents | 07-Learning | 08-Storage | 09-Auxiliary | Median |
|---|-----------|-----------|-------------|---------------|--------|------------|-----------|-------------|------------|--------------|--------|
| 1 | Code Quality | B | A | A | B | B | B | B | A | B | B |
| 2 | Acceleration & Maintainability | C | B | B | B | B | B | B | A | B | B |
| 3 | Token/Quality Tradeoffs | B | B | A | A | A | A | B | B | A | A/B |
| 4 | Implementation Completeness | B | B | B | B | B | C | B | B | B | B |
| 5 | Silent Failure Risk | C | C | C | B | C | C | C | C | B | C |
| 6 | Code Smells | C | B | B | C | C | B | C | B | B | B/C |
| 7 | User Discoverability | B | B | B | B | B | B | B | B | C | B |
| 8 | Extensibility | B | A | A | A | A | A | A | A | B | A |

### Overall System Health Assessment

Agent Baton is an architecturally ambitious system that has achieved genuinely impressive structural outcomes in several domains. The planning pipeline (Stage protocol, rules-as-data), storage layer (protocol-based SQLite with proper transactional semantics), and governance framework (hash-chained compliance, multi-tier budget enforcement) demonstrate mature software architecture. Extensibility scores are consistently A-grade across 6 of 9 domains, meaning the system is well-positioned for growth.

The systemic weakness is Silent Failure Risk, which scores C in 7 of 9 domains. The codebase has a pervasive pattern of `except Exception: pass` or `except Exception: _log.debug(...)` that was likely introduced as pragmatic resilience during rapid development, but has accumulated to the point where entire subsystems (learning pipeline, bead memory, compliance audit, context harvesting) can degrade without any operator-visible signal. This is the single most important class of problem to address because it compounds: silent failures in bead extraction prevent the learning pipeline from learning, which prevents planning from improving, which increases token waste. The second systemic weakness is God class accumulation: `executor.py` (5700 lines), `pmo.py` (2950 lines), and `execute.py` (2300 lines) are the three largest files and are all in the system's critical path.

### Top 5 Systemic Patterns

1. **Silent failure via `except Exception: pass/debug`** -- Found in all 9 domains. 50+ locations total. The `debug` log level means default configurations never surface these failures. This is the single largest trust gap in the system.

2. **God class/module accumulation** -- `executor.py` (5700 lines, 40+ methods), `pmo.py` (2950 lines), `execute.py` (2300 lines), `BudgetEnforcer` (768 lines), `detect_stack` (268 lines). These files are change bottlenecks where any modification risks unintended side effects.

3. **Cross-module duplication** -- `_utcnow()` defined in 7 files, `_resolve_context_root` in 4 files, `_DEFAULT_AGENTS` in 2 files, feature-flag functions copy-pasted 6 times, two `prometheus.py` files, two hash-chain implementations.

4. **Advertised-but-unwired features** -- Self-heal dispatch (TODO at executor.py:3104), A/B testing (built but never called), swarm coalescer/reconciler (disconnected from dispatch), `LearnedOverrides.remove_override()` (always returns False), CRP wizard (stub).

5. **Compliance enforcement gap** -- Policy engine `require_agent`/`require_gate` rules are advisory (warnings only). Redaction failure writes un-redacted data to the compliance chain. Compliance audit writes are best-effort. These gaps mean the "Regulated Data" preset's safety guarantees are aspirational, not enforced.

---

## Part 2: Critical Issues Registry

Each issue is classified by type and priority:
- **Type**: BUG (broken now), SAFETY (trust/compliance gap), DRIFT (divergence that will worsen), DEBT (accumulation that slows work)
- **Priority**: P0 (fix this week), P1 (fix this month), P2 (fix this quarter)

### P0 -- Fix This Week

| ID | Domain | Type | Issue | Location | Impact |
|----|--------|------|-------|----------|--------|
| C01 | 03-Governance | SAFETY | `require_agent`/`require_gate` policy rules emit warnings, never block. Regulated-data tasks proceed without auditor. | `policy.py:563-577` | Regulated-domain safety guarantees are unenforceable |
| C02 | 03-Governance | SAFETY | Redaction failure writes un-redacted sensitive data to the compliance hash chain | `compliance.py:288-296` | Sensitive data persisted in plain text in audit trail |
| C03 | 01-Engine | BUG | `record_policy_approval` writes to computed property `failed_step_ids` (a `@property` returning new `set`). No-op. | `executor.py:1329` | Policy rejection creates inconsistent execution state |
| C04 | 09-Auxiliary | BUG | `InteractiveDecisionManager.get()` returns `DecisionResolution` instead of `DecisionRequest | None` | `runner.py:78-80` | Type violation works by coincidence; will break on any refactor |
| C05 | 09-Auxiliary | BUG | Worker bypasses engine state machine by directly mutating `_state.status = "failed"` | `worker.py:486-489` | State machine invariants violated; recovery may produce corrupt state |
| C06 | 08-Storage | BUG | Duplicate MIGRATIONS key `16` in `schema.py` silently drops deployment_profiles DDL | `schema.py:416/488` | Migration system lacks uniqueness validation; future duplicates possible |
| C07 | 05-API/PMO | SAFETY | Approval log write failures silently swallowed in `approve_gate`/`reject_gate` | `pmo.py:1787-1789, 1892-1897` | Audit trail for approval decisions has gaps |
| C08 | 05-API/PMO | SAFETY | Arch-bead review returns success with synthetic bead ID even when `BeadStore.write()` throws | `pmo_h3.py:404-408` | User believes bead was saved when it was not |

### P1 -- Fix This Month

| ID | Domain | Type | Issue | Location | Impact |
|----|--------|------|-------|----------|--------|
| C09 | 07-Learning | BUG | IncidentStore is memory-only; process restart loses all incident history | `observe/incidents.py:49` | Runtime incidents vanish on every restart |
| C10 | 07-Learning | BUG | `LearnedOverrides.remove_override()` always returns `False`; rollback is impossible | `learn/overrides.py:155-174` | Applied overrides cannot be reversed |
| C11 | 06-Agents | DRIFT | 11 subsystem agents (self-heal, immune, swarm, speculation, team) missing from `_bundled_agents/` | `_bundled_agents/` | pip-installed users get missing-agent errors on subsystem trigger |
| C12 | 02-Planning | DRIFT | `_DEFAULT_AGENTS` duplicated in `rules/default_agents.py:5-15` and `stages/classification.py:33-43` | Planning pipeline | Divergence produces different agent rosters for same input |
| C13 | 06-Agents | DRIFT | Agent roster doc has 4 incorrect model declarations (team-lead, task-runner, security-reviewer, subject-matter-expert) | `docs/agent-roster.md` | Users/orchestrator select wrong model tier |
| C14 | 01-Engine | DEBT | Self-heal is advertised but not wired; `_enqueue_selfheal` has TODO at line 3104 | `executor.py:3104` | Users who enable `BATON_SELFHEAL_ENABLED` get logging but no behavior |
| C15 | 04-CLI | BUG | `run.py` uses Pydantic v1 API (`parse_obj`) and constructs `ExecutionEngine()` with no arguments | `cli/commands/run.py` | Command is likely broken |
| C16 | 01-Engine | SAFETY | Compliance audit writes are best-effort; failures logged but not enforced | `executor.py:1032-1039` | Regulated-environment audit trails have gaps |
| C17 | 03-Governance | SAFETY | Budget bead-warning callback failures swallowed at DEBUG level | `budget.py:757-763` | Budget cap signals can vanish silently |

### P2 -- Fix This Quarter

| ID | Domain | Type | Issue | Location | Impact |
|----|--------|------|-------|----------|--------|
| C18 | 06-Agents | DRIFT | No sync mechanism between `agents/` and `_bundled_agents/` | Both directories | Bug fixes silently missing for bundled users |
| C19 | 07-Learning | DEBT | Pervasive silent failure in learning engine detection pipeline | `learn/engine.py:97-285` | Learning degradation goes unnoticed |
| C20 | 02-Planning | DEBT | Post-pipeline Opus review runs outside stage system | `planner.py:289-485` | Breaks the pipeline's single-pass design principle |
| C21 | 09-Auxiliary | DEBT | Swarm coalescer and reconciler disconnected from dispatch path | `swarm/` | Conflicts won't be detected if swarm is enabled |
| C22 | 01-Engine | DEBT | Triple signal block in continuation prompts wastes ~450 tokens/turn | `dispatcher.py:716-719` | Token waste on every interactive turn |

---

## Part 3: Silent Failure Map

### Ranked by Risk and Compounding Effect

#### Group A: Exception Swallowing (27 locations)

| Rank | Location | What Fails | Risk | Compounds Into |
|------|----------|-----------|------|----------------|
| 1 | `learn/engine.py:97-285` | All 5 detection blocks in learning engine | HIGH | Planning quality never improves |
| 2 | `executor.py:2055-2086` | Bead signal extraction | HIGH | Learning pipeline has no data -> planning degrades |
| 3 | `improve/loop.py:181-185` | Learning engine analysis | HIGH | Same as above |
| 4 | `executor.py:1032-1039` | Compliance audit write | HIGH | Audit trail has silent gaps |
| 5 | `budget.py:757-763` | Budget bead-warning callback | HIGH | Budget exhaustion goes unnoticed |
| 6 | `roster.py:123-139` | Pattern learner lookup | HIGH | Plans don't benefit from learned patterns |
| 7 | `risk.py:145-146` | Data sensitivity classification | HIGH | Governance layer blind to data sensitivity |
| 8 | `validation.py:156-165` | Policy engine validation | HIGH | Compliance checks produce zero results |
| 9 | `executor.py:2271-2291` | Context harvesting | MEDIUM | Agents lose context continuity |
| 10 | `executor.py:4475-4505` | Knowledge telemetry | MEDIUM | Knowledge effectiveness unmeasurable |
| 11 | `executor.py:534-562` | BeadStore/SoulRouter init | MEDIUM | All bead operations silently unavailable |
| 12 | `assembly.py:148-166` | Planning bead capture | MEDIUM | Observability signal missing from plans |
| 13 | `decomposition.py:158-161` | Knowledge resolution per step | MEDIUM | Steps miss relevant knowledge |
| 14 | `improve/scoring.py:323-333` | Bead store query in scoring | MEDIUM | Performance scoring reports 0.0 quality |
| 15 | `improve/loop.py:195-210` | Pattern learner refresh | MEDIUM | Stale patterns persist |
| 16 | `triggers.py:173-203` | Supplementary trigger signals | MEDIUM | Improvement triggers don't fire |
| 17 | `context_harvester.py:212` | All harvest failures | LOW | Context continuity degraded |
| 18 | `observe/archiver.py:186-196` | VACUUM failure | LOW | DB size grows |
| 19 | `execute.py:969-975` (et al.) | Auto-viz exceptions (4 locations) | LOW | Visualization missing |

#### Group B: Missing Persistence (3 locations)

| Rank | Location | What's Lost | Risk |
|------|----------|-------------|------|
| 1 | `observe/incidents.py:49` | Runtime incidents (memory-only) | HIGH |
| 2 | `pmo_h3.py:404-408` | Arch-bead review (returns fake success) | HIGH |
| 3 | `pmo.py:1787-1789, 1892-1897` | Approval/rejection log entries | HIGH |

#### Group C: Type Violations and Contract Breaches (4 locations)

| Rank | Location | Violation | Risk |
|------|----------|-----------|------|
| 1 | `executor.py:1329` | Writes to computed `@property` (no-op) | HIGH |
| 2 | `runner.py:78-80` | Returns wrong type; works by coincidence | HIGH |
| 3 | `worker.py:486-489` | Bypasses state machine; direct mutation | HIGH |
| 4 | `executor.py:2278, 5521` | Accesses private `_storage._conn()` | MEDIUM |

#### Group D: Data Loss / Corruption Risks (4 locations)

| Rank | Location | Risk Scenario | Risk |
|------|----------|---------------|------|
| 1 | `compliance.py:288-296` | Un-redacted sensitive data written to hash chain | HIGH |
| 2 | `schema.py:416/488` | Duplicate migration key drops DDL | HIGH (mitigated by v26) |
| 3 | `sqlite_backend.py:1164` | INSERT OR REPLACE destroys FK CASCADE children | LOW |
| 4 | `file_backend.py:109-113` | Read-modify-write race on concurrent step completion | LOW |

#### Compounding Chain

```
Bead signal extraction fails silently (executor.py:2055)
  -> Learning engine has no bead data (learn/engine.py:97)
    -> Pattern learner produces no patterns (roster.py:123)
      -> Planning does not benefit from learned patterns
        -> Agent routing is suboptimal
          -> Token waste increases
            -> Budget cap signals fail silently (budget.py:757)
              -> No feedback that the system is degrading
```

This is the most dangerous chain in the system. Fixing bead signal extraction unlocks the entire learning loop.

---

## Part 4: Phased Remediation Roadmap

### Phase 1: Trust Foundation

**Goal**: Eliminate safety-critical bugs and compliance gaps so the system's safety guarantees are real, not aspirational.

**Scope**:
- C01: Wire `require_agent`/`require_gate` enforcement into executor `start()` (`policy.py:563-577`)
- C02: Halt or skip compliance writes on redaction failure (`compliance.py:288-296`)
- C03: Fix `record_policy_approval` to create a `StepResult` instead of writing to computed property (`executor.py:1329`)
- C07: Surface approval log write failures as HTTP 500 in `approve_gate`/`reject_gate` (`pmo.py:1787-1789, 1892-1897`)
- C08: Surface `BeadStore.write()` failure as HTTP 500 in arch-bead review (`pmo_h3.py:404-408`)
- C16: Promote compliance audit write failures from best-effort to WARNING with optional hard-fail for HIGH/CRITICAL risk (`executor.py:1032-1039`)

**Dependencies**: None. These are all leaf fixes.

**Estimated Effort**: Medium (3-5 days)

**Success Criteria**:
- `PolicyEngine.validate_plan(policy, plan)` exists and is called in `ExecutionEngine.start()`
- A plan with "Regulated Data" preset that lacks `auditor` agent raises `PolicyViolationError`
- Redaction failure in compliance write either raises or skips (never writes un-redacted)
- `record_policy_approval` rejection produces a `StepResult` with `status="failed"` in `state.step_results`
- `approve_gate`/`reject_gate` return HTTP 500 when the log write fails
- Arch-bead review returns HTTP 500 when `BeadStore.write()` fails
- Test added for each fix

**Risk if Deferred**: The system claims regulated-data safety guarantees it cannot enforce. A compliance audit would find the gaps. Trust erosion with users who rely on these guarantees.

---

### Phase 2: Silent Failure Visibility

**Goal**: Promote all HIGH-risk silent failures from `debug` to `warning` level and add a health-check signal, so operators can detect degradation without log forensics.

**Scope**:
- All 19 exception-swallowing locations ranked HIGH or MEDIUM in Group A above
- Specific files:
  - `executor.py:2055-2086` (bead signal extraction)
  - `executor.py:2271-2291` (context harvesting)
  - `executor.py:4475-4505` (knowledge telemetry)
  - `executor.py:534-562` (BeadStore/SoulRouter init)
  - `learn/engine.py:97-285` (5 detection blocks)
  - `improve/loop.py:181-185` (learning engine analysis)
  - `improve/loop.py:195-210` (pattern learner refresh)
  - `improve/scoring.py:323-333` (bead store query)
  - `roster.py:123-139` (pattern lookup)
  - `risk.py:145-146` (data classification)
  - `validation.py:156-165` (policy validation)
  - `assembly.py:148-166` (planning bead capture)
  - `budget.py:757-763` (budget bead-warning callback)
  - `triggers.py:173-203` (supplementary triggers)
  - `daemon.py:324-328` (run-ceiling bead filing)
- Add a `baton health` CLI command that checks subsystem availability

**Dependencies**: Phase 1 (compliance write behavior must be decided before adjusting its log level)

**Estimated Effort**: Medium (3-5 days)

**Success Criteria**:
- Zero `except Exception: _log.debug(...)` blocks remain for HIGH-risk silent failures
- All promoted failures use `_log.warning()` with structured context (task_id, step_id, subsystem name)
- `baton health` returns a JSON report of subsystem availability
- Existing tests still pass

**Risk if Deferred**: The compounding chain continues: bead extraction fails -> learning stalls -> planning degrades -> token waste increases -> budget cap fails silently. Operators have no visibility into system health.

---

### Phase 3: Data Integrity Fixes

**Goal**: Fix all bugs that produce incorrect state, wrong types, or data loss.

**Scope**:
- C04: Fix `InteractiveDecisionManager.get()` return type (`runner.py:78-80`)
- C05: Add `fail_execution()` to `ExecutionDriver` protocol; wire worker to use it (`worker.py:486-489`)
- C06: Add uniqueness test for MIGRATIONS keys (`schema.py`)
- C09: Persist IncidentStore to SQLite (`observe/incidents.py:49`)
- C10: Implement `LearnedOverrides.remove_override()` (`learn/overrides.py:155-174`)
- C15: Remove or fix `run.py` (`cli/commands/run.py`)
- C22: Emit `_SIGNALS_BLOCK` once in continuation prompts (`dispatcher.py:716-719`)
- Fix `StepResult.from_dict` input mutation (`executor.py:944-958`)

**Dependencies**: Phase 1 (policy enforcement). Phase 2 (visibility into failures during testing).

**Estimated Effort**: Medium (3-5 days)

**Success Criteria**:
- `InteractiveDecisionManager.get()` returns `DecisionRequest | None` as specified by protocol
- `ExecutionDriver` protocol has a `fail_execution()` method
- `pytest` test validates MIGRATIONS dict key uniqueness
- IncidentStore persists to SQLite and survives process restart
- `remove_override()` actually removes the override and returns `True`
- `run.py` either removed or using current APIs
- `_SIGNALS_BLOCK` appears exactly once in continuation prompts

**Risk if Deferred**: State machine invariants remain violable. Incident data lost on restart. Override rollback impossible.

---

### Phase 4: Drift Prevention

**Goal**: Eliminate divergence vectors that silently worsen over time.

**Scope**:
- C11: Bundle the 11 missing subsystem agents or make subsystems gracefully degrade
- C12: Delete `_DEFAULT_AGENTS` duplicate in `stages/classification.py:33-43`
- C13: Fix 4 agent roster model mismatches in `docs/agent-roster.md`
- C18: Add CI check or build step to sync `agents/` and `_bundled_agents/`
- Document all undocumented env vars in `CLAUDE.md` (8 missing)
- C14: Either wire self-heal dispatch or gate behind `BATON_EXPERIMENTAL`
- Gate A/B testing behind `BATON_EXPERIMENTAL` or wire into engine
- Fix `PlannerServices` TYPE_CHECKING stale imports (`services.py:25-38`)

**Dependencies**: Phase 3 (data integrity fixes should be in place so newly bundled agents work correctly)

**Estimated Effort**: Medium (3-5 days)

**Success Criteria**:
- All 33 agents have corresponding entries in `_bundled_agents/`
- CI job fails if agents diverge
- `CLAUDE.md` environment variables table is complete
- `_DEFAULT_AGENTS` exists in exactly one location
- `docs/agent-roster.md` model declarations match agent definitions
- Self-heal and A/B testing are either wired or explicitly experimental

**Risk if Deferred**: Divergence accumulates silently. pip-installed users hit missing-agent errors. Documentation becomes unreliable.

---

### Phase 5: Structural Decomposition

**Goal**: Break up God classes/modules to reduce change risk and improve maintainability.

**Scope**:
- `executor.py` (5700 lines) -> extract `WorktreeLifecycle`, `DispatchBuilder`, `CompletionHandler`, shared `_env_flag()` helper
- `pmo.py` (2950 lines) -> split into `pmo_board.py`, `pmo_forge.py`, `pmo_execution.py`, `pmo_gates.py`, `pmo_signals.py`, `pmo_external.py`, `pmo_changelist.py`
- `execute.py` (2300 lines) -> extract handler functions for each subcommand group
- `detect_stack()` (268 lines) -> decompose into private helpers
- Unify `_resolve_context_root` (4 copies -> 1)
- Deduplicate `_utcnow()` (7 copies -> 1 in `utils/`)
- Merge `observe/` and `observability/` packages
- Consolidate duplicate hash-chain implementations
- Integrate post-pipeline Opus review as a pipeline stage
- Remove backward-compat proxy methods (228 lines in `planner.py:530-757`)

**Dependencies**: Phases 1-4 (safety and correctness fixes before large-scale refactoring)

**Estimated Effort**: Large (1-2 weeks)

**Success Criteria**:
- `executor.py` under 2000 lines
- `pmo.py` split into 5+ modules, each under 600 lines
- `_resolve_context_root` in exactly one location
- `_utcnow` in exactly one location
- All existing tests pass after decomposition

**Risk if Deferred**: Every change to the God files carries elevated side-effect risk. New contributors face 5700-line files. Merge conflicts increase.

---

### Phase 6: Extensibility and User Experience

**Goal**: Polish the system for external users and third-party extension.

**Scope**:
- Standardize error handling across all CLI commands (`user_error()` instead of `print("Error: ...")`)
- Resolve dual registration of `handoff` command
- Wire missing spec endpoints or remove frontend mock fallbacks
- Add `beads` tab to `NAV_TABS` in `App.tsx`
- Fix `pmo_h3.py:_project_db_path()` to use dependency injection
- Document release tools, debate, and knowledge ranking in user-facing docs
- Add progressive disclosure to CLI help
- Fix `persist_debate()` to use WAL mode
- Implement actual parallel dispatch in swarm
- Wire swarm coalescer and reconciler
- Add test coverage for effectiveness, ADR harvester, and review harvester

**Dependencies**: Phase 5 (structural decomposition makes these changes safer)

**Estimated Effort**: Large (1-2 weeks)

**Success Criteria**:
- All CLI commands use `user_error()` for error reporting
- Frontend has no mock data fallbacks
- User-facing docs cover all discoverable features
- Swarm parallel dispatch actually dispatches in parallel

**Risk if Deferred**: User experience remains rough. Third-party extension requires reading source. Swarm won't work correctly when enabled.

---

## Part 5: Quick Wins

Issues fixable in under 30 minutes each, safe and isolated.

- [ ] Delete `_DEFAULT_AGENTS` duplicate in `stages/classification.py:33-43`; import from `rules/default_agents.py` (C12)
- [ ] Fix 4 agent roster model mismatches in `docs/agent-roster.md` (C13)
- [ ] Emit `_SIGNALS_BLOCK` once in continuation prompts (`dispatcher.py:716-719`) (C22)
- [ ] Extract feature-flag functions into shared `_env_flag(name, default)` helper (`executor.py:155-206`)
- [ ] Add MIGRATIONS key uniqueness test (`schema.py`)
- [ ] Deduplicate `_utcnow()` into `agent_baton/utils/time.py` and update 7 import sites
- [ ] Add missing env vars to `CLAUDE.md` environment variables table (8 undocumented vars)
- [ ] Remove dead code: `TaskViewSubscriber` (acknowledged unused in production)
- [ ] Remove dead code in `deployment_profile_store.py:81-85` (query result never used)
- [ ] Fix `StepResult.from_dict` to not mutate input dict (`executor.py:944-958` -- copy before pop)
- [ ] Fix `_ensure_predict_state()` in `BudgetEnforcer` to use explicit `Optional` instead of `hasattr` (`budget.py:529-535`)
- [ ] Fix `PlannerServices` TYPE_CHECKING stale import paths (`services.py:25-38`)
- [ ] Remove `_MOCK_SPECS` from `client.ts` (line 372)
- [ ] Standardize `daemon.py:302-313` to use `user_error()` instead of `print("Error: ...")`

---

## Part 6: Cross-Domain Dependency Map

### Improvement Dependencies

```
Phase 1: Trust Foundation
  |
  +-- Phase 2: Silent Failure Visibility
  |     |
  |     +-- Phase 3: Data Integrity Fixes
  |           |
  |           +-- Phase 4: Drift Prevention
  |                 |
  |                 +-- Phase 5: Structural Decomposition
  |                       |
  |                       +-- Phase 6: Extensibility & UX
  |
  +-- Quick Wins (can run in parallel with any phase)
```

### Cross-Domain Dependencies

| Change in Domain | Enables/Requires in Domain | Why |
|-----------------|---------------------------|-----|
| 01-Engine: Fix `record_policy_approval` | 03-Governance: Policy enforcement becomes meaningful | Policy engine can't enforce if the executor can't record rejections correctly |
| 03-Governance: Wire `require_agent` enforcement | 06-Agents: Missing bundled agents become blocking | Once policy blocks missing agents, bundled agents must exist |
| 01-Engine: Fix bead signal extraction logging | 07-Learning: Learning pipeline gets data | Bead data feeds the entire learning loop |
| 07-Learning: Fix learning engine silent failures | 02-Planning: Planning quality improves | Learned patterns feed the planning pipeline |
| 08-Storage: Migration key uniqueness test | All domains: Future schema changes are safer | Prevents the class of bug found in key 16 |
| 04-CLI: Remove/fix `run.py` | 01-Engine: One fewer construction site for `ExecutionEngine` | Eliminates stale API usage |
| 05-API/PMO: Split `pmo.py` | 03-Governance: Approval log fixes are easier | Gate approval logic is isolated in `pmo_gates.py` |
| 09-Auxiliary: Fix worker state mutation | 01-Engine: State machine protocol is complete | `ExecutionDriver.fail_execution()` added to protocol |
| 06-Agents: Sync CI check | 08-Distribution: Install script produces correct bundles | Ensures bundled and distributable agents match |

### Critical Path

1. **C03** (fix `record_policy_approval`) -- unblocks policy enforcement
2. **C01** (wire `require_agent`/`require_gate`) -- unblocks regulated-domain trust
3. **Bead signal extraction visibility** (executor.py:2055) -- unblocks learning pipeline
4. **Learning engine detection blocks** (learn/engine.py:97-285) -- unblocks planning quality improvement
5. **C11** (bundle missing agents) -- unblocks policy enforcement for pip users
6. **executor.py decomposition** -- unblocks safe future development

Items 1-2 are the safety-critical path. Items 3-4 are the quality-improvement path. Items 5-6 are the sustainability path.

---

## Appendix: Audit Source Index

| Audit | File | Critical Issues | Silent Failures |
|-------|------|-----------------|-----------------|
| 01 - Core Engine & Execution | `audit-01-core-engine-execution.md` | 2 | 9 |
| 02 - Planning & Routing | `audit-02-planning-routing.md` | 1 | 15 |
| 03 - Governance, Risk & Gates | `audit-03-governance-risk-gates.md` | 2 | 8 |
| 04 - CLI Surface | `audit-04-cli-surface.md` | 1 | 4 |
| 05 - API & PMO | `audit-05-api-pmo.md` | 2 | 6 |
| 06 - Agent & Knowledge Ecosystem | `audit-06-agent-knowledge-ecosystem.md` | 2 | 5 |
| 07 - Learning & Observability | `audit-07-learning-observability.md` | 2 | 7 |
| 08 - Storage & Distribution | `audit-08-storage-distribution.md` | 1 | 5 |
| 09 - Auxiliary Systems | `audit-09-auxiliary-systems.md` | 2 | 7 |
| **Total** | | **15** | **66** |

---

**End of roadmap. This document is the single source of truth for remediation priority. Individual audit documents remain authoritative for detailed findings and file-level references.**
