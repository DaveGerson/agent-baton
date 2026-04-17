# Agent Baton Competitive Audit — Synthesis v2 (Team Dialogue Method)

**Date:** 2026-04-16
**Branch:** `feat/actiontype-interact`
**Method:** Two-pass audit. Pass 1: 6 solo agents reviewed themes + personas. Pass 2: 6 persona+expert dialogue teams (12 items each, 72 total evaluations) with structured ask→investigate→probe→discover cycles.
**Finding:** The team dialogue method produced **75+ new findings** not captured by solo agents, downgrading ratings on 23 items across all personas.

---

## Executive Summary

The first-pass solo audit painted an encouraging picture: 22 of 36 user stories FULLY MET, strong learning pipeline, solid governance. The second-pass team audits **systematically uncovered deeper issues** that change the strategic picture:

1. **The engine's internal logic is strong.** Planning intelligence, risk classification, agent routing, pattern learning — these work as designed.

2. **The engine's external boundaries are porous.** Every persona who depends on the system behaving correctly at runtime boundaries — crash recovery, gate enforcement, cost limits, audit immutability, subprocess lifecycle — found gaps that the solo audit missed.

3. **The compliance story is worse than initially assessed.** David's team audit found 0 WORKS across 12 items (vs. solo audit's 7 WORKS). The mutable audit trail, absent human identity, and transient classification results are systemic issues, not isolated gaps.

4. **The learning system is mechanically present but statistically weak.** Tomoko's team audit downgraded from 13 WORKS to 1 WORKS. Experiments lack traffic splitting and statistical tests. The pattern learner's confidence formula requires 11+ samples to surface anything. Evolution proposals are static templates.

5. **Daemon mode has a critical behavioral split from CLI mode.** Carlos's team discovered that daemon mode auto-approves gates *without executing them* — test/build/lint gates are rubber-stamped. Only `baton execute run` runs real gate subprocesses. This undermines the entire governance proposition for headless execution.

---

## Persona Scorecard: Solo vs. Team Comparison

| Persona | Solo WORKS | Team WORKS | Solo BLOCKED | Team BLOCKED | Net Change |
|---------|:----------:|:----------:|:------------:|:------------:|:----------:|
| Maya (Solo Power User) | 9/13 | 5/12 | 0 | 1 | Significant downgrade |
| Carlos (Backlog Drainer) | 8/13 | 5/12 | 1 | 1 | Moderate downgrade |
| James (Eng Manager) | 6/10 | 1/12 | 0 | 2 | Major downgrade |
| David (Compliance) | 7/15 | 0/12 | 3 | 6 | Severe downgrade |
| Priya (Platform Eng) | 10/20 | 1/10 | 4 | 2 | Major downgrade |
| Tomoko (Workflow Designer) | 13/17 | 1/12 | 0 | 0 | Major downgrade |

**Why the systematic downgrade?** The solo agents checked "does the feature exist?" The team dialogues asked "does it actually work correctly under real conditions?" The probe→discover cycle caught runtime edge cases, enforcement gaps, and implementation stubs that surface-level code scanning missed.

---

## Top 10 Critical Findings (Team-Exclusive)

These findings were NOT identified by the solo audit and were only uncovered through the team dialogue's second-pass probing.

### 1. DAEMON GATES ARE RUBBER-STAMPED (Carlos — Team Exclusive)

**Severity: CRITICAL — Undermines core governance proposition**

In daemon mode (`baton daemon start`), the `TaskWorker` auto-approves programmatic gates (test/build/lint/spec) **without executing the gate command**. They are recorded as "passed" in the database with no actual subprocess execution. Only `baton execute run` mode runs gate commands as real shell subprocesses.

This means: the entire QA gate story — the single biggest competitive differentiator — **does not apply to headless/remote execution**, which is the Mode 2 flagship capability.

- Evidence: `worker.py:341-347` — programmatic gates auto-approved
- Contrast: `execute.py:1061-1091` — `baton execute run` runs real subprocesses

**Recommendation:** Wire `GateRunner.run()` into `TaskWorker` for programmatic gates. This is the highest-leverage fix in the entire audit.

---

### 2. ZERO HUMAN IDENTITY ACROSS ALL TABLES (David — Team Exclusive)

**Severity: CRITICAL — Systemic compliance failure**

The solo audit flagged missing `approved_by` on `approval_results`. The team audit discovered this is **systemic**: no table anywhere carries a human identity field. `DecisionResolution.resolved_by` defaults to the literal string `"human"`, not an actual person. `step_results`, `gate_results`, `trace_events`, `events` — none record who triggered or approved the action.

For compliance: you cannot prove WHO did ANYTHING in the entire audit trail.

**Recommendation:** Add `actor TEXT` column to `approval_results`, `gate_results`, `events`. Populate from `$USER` environment variable or API auth token identity. Schema migration v10.

---

### 3. READINESS PROBE ALWAYS RETURNS TRUE (Priya — Team Exclusive)

**Severity: HIGH — Production deployment hazard**

`health.py:55` hardcodes `ready=True`. The k8s readiness probe never fails, meaning pods are always considered ready even when the database is corrupt, the engine is in failed state, or disk is full. The `try/except` around `decision_manager.pending()` swallows all exceptions and defaults to 0.

**Recommendation:** Check SQLite connectivity, engine status != "failed", and disk space in the readiness probe. Return `ready=False` when any check fails.

---

### 4. ORPHANED CLAUDE SUBPROCESSES ON SHUTDOWN/CRASH (Priya — Team Exclusive)

**Severity: HIGH — Token leak and data corruption risk**

`start_new_session=True` in `claude_launcher.py:558` puts each `claude` subprocess in its own process group. When the daemon shuts down via SIGTERM, `CancelledError` propagates to the asyncio task but **does not signal the child processes**. They continue running as orphans, consuming tokens and potentially committing code after the daemon has exited.

**Recommendation:** Track active subprocess PIDs in `StepScheduler`. On `CancelledError`, explicitly `process.terminate()` each tracked process before exiting.

---

### 5. GATE FAILURE IS TERMINAL WITH NO RETRY (Maya — Team Exclusive)

**Severity: HIGH — UX and workflow blocker**

`record_gate_result(passed=False)` sets `state.status = "failed"` permanently. There is no way to fix the code and retry the failed gate. The entire execution must be restarted from scratch. For a solo developer who just needs to tweak one test, this is devastating — all prior phase work is effectively locked behind a permanent failure.

**Recommendation:** Add `baton execute retry-gate --phase-id N` that re-runs the gate command without re-dispatching the agent. If it passes, execution continues.

---

### 6. EXPERIMENTS ARE NOT A/B TESTS (Tomoko — Team Exclusive)

**Severity: MEDIUM — Learning system credibility**

The experiment system (`ExperimentManager`) does not split traffic between variants. There is no control group running simultaneously with the test group. "Experiments" are before/after comparisons on a time series, not concurrent A/B tests. The 5-sample minimum is statistically underpowered. No significance test (t-test, Mann-Whitney, etc.) is applied — just a simple percentage-change threshold at +/-5%.

**Recommendation:** Document the system honestly as "before/after monitoring" rather than "controlled experiments." For true A/B, would need: concurrent variant routing, independent sample collection, and statistical significance testing.

---

### 7. CLASSIFICATION RESULT IS TRANSIENT (David — Team Exclusive)

**Severity: MEDIUM — Audit trail gap**

`ClassificationResult` (produced by `DataClassifier.classify()`) is consumed by the planner and discarded. The signals, confidence score, and reasoning are not persisted to SQLite. For audit: you cannot reconstruct *what the classifier saw* when it made its risk determination.

**Recommendation:** Persist `ClassificationResult` to a `classification_results` table with `task_id`, `risk_level`, `signals_json`, `confidence`, `timestamp`.

---

### 8. CONTEXT HANDOFF IS SINGLE-STEP ONLY (Maya — Team Exclusive)

**Severity: MEDIUM — Quality degradation across long plans**

Phase N+1 only receives Phase N's outcome text as handoff context. In a 5-phase plan, Phase 5 has zero direct context from Phases 1-3. The bead relay system partially compensates (selecting up to 5 relevant beads from all prior phases), but bead emission depends on agent compliance — agents that don't emit beads create information black holes.

**Recommendation:** Accumulate a rolling handoff summary: each phase appends its key decisions/outcomes to a growing context document injected into all subsequent phases.

---

### 9. INTERACTION_HISTORY AND FEEDBACK_RESULTS NOT PERSISTED (David — Team Exclusive)

**Severity: MEDIUM — Data loss on every save/load**

`interaction_history` (INTERACT action multi-turn dialogue) and `feedback_results` (structured feedback gate responses) exist as fields on `ExecutionState` but have no corresponding SQLite columns or backend write paths. They are lost on every `save_execution()` / `load_execution()` cycle.

**Recommendation:** Add `interaction_turns` and `feedback_results` tables to schema. Wire into `save_execution()` and `load_execution()`.

---

### 10. PATTERN LEARNER NEEDS 11+ SAMPLES (Tomoko — Team Exclusive)

**Severity: LOW-MEDIUM — Learning system underperforms expectations**

The confidence formula `min(1.0, (sample_size/15) * success_rate)` at the default `min_confidence=0.7` threshold means: even with 100% success rate, you need `0.7 * 15 = 10.5` → 11 samples before a pattern surfaces. With realistic 80% success rate: `0.7 / 0.8 * 15 = 13.1` → 14 samples. Most teams won't have 14 executions of the same task type for months.

**Recommendation:** Either lower the default `min_confidence` to 0.4, or restructure the formula to separate sample-size confidence from outcome confidence.

---

## Cross-Persona Issue Clusters

The 75+ individual findings cluster into 6 systemic themes:

### Cluster A: Runtime Boundary Failures
*Affects: All Mode 2 personas (Priya, Carlos, James, David)*

| Finding | Source | Severity |
|---------|--------|----------|
| Daemon gates rubber-stamped | Carlos #7 | CRITICAL |
| Orphaned subprocesses on shutdown | Priya #4 | HIGH |
| Gate failure is terminal | Maya #4 | HIGH |
| Readiness probe hardcoded true | Priya #2 | HIGH |
| Crash recovery loses in-flight work | Priya #3 | MEDIUM |
| Zombie dispatched steps in resume | Maya #6 | MEDIUM |
| Double-fork PID-1 trap in containers | Priya #1 | MEDIUM |

**Root cause:** The engine was built and tested in interactive CLI mode. Runtime behavior in headless/daemon mode diverges from CLI mode in several critical ways that were never reconciled.

### Cluster B: Identity and Immutability (Compliance)
*Affects: David, James*

| Finding | Source | Severity |
|---------|--------|----------|
| Zero human identity across all tables | David #2 | CRITICAL |
| DELETE-then-INSERT on audit tables | David #1 | CRITICAL |
| Events use INSERT OR REPLACE | David #1 | HIGH |
| delete_events() public API | David #1 | HIGH |
| ClassificationResult transient | David #4 | MEDIUM |
| interaction_history not persisted | David #9 | MEDIUM |
| No separation of duties enforcement | David #10 | MEDIUM |

**Root cause:** The storage layer was designed for operational convenience (full-state save/load), not audit integrity. No distinction between "working state" and "audit record."

### Cluster C: Cost and Resource Governance
*Affects: Carlos, James, Priya*

| Finding | Source | Severity |
|---------|--------|----------|
| Budget enforcement advisory-only | Carlos #3 | HIGH |
| max_tokens_per_minute unimplemented | Priya #5 | MEDIUM |
| No --max-steps on daemon | Carlos #4 | MEDIUM |
| No dollar cost conversion | James #4 | LOW |
| No per-task cost cap | Carlos #3 | HIGH |

**Root cause:** Resource governance model was defined but enforcement was never wired into the runtime.

### Cluster D: Learning System Rigor
*Affects: Tomoko*

| Finding | Source | Severity |
|---------|--------|----------|
| Experiments aren't A/B tests | Tomoko #3 | MEDIUM |
| Pattern learner needs 11+ samples | Tomoko #2 | MEDIUM |
| Evolution proposals are static templates | Tomoko #4 | MEDIUM |
| Bead quality scores are unvalidated | Tomoko #9 | LOW |
| No convergence dashboard | Tomoko #11 | LOW |
| Cross-project sharing loses 5 data categories | Tomoko #8 | LOW |

**Root cause:** The learning infrastructure was built as a proof-of-concept. The algorithms are present but haven't been validated against real execution data at scale.

### Cluster E: Installation and Onboarding
*Affects: Maya, Carlos*

| Finding | Source | Severity |
|---------|--------|----------|
| baton install can't find bundled agents after pip install | Maya #1 | HIGH |
| Not on PyPI | Maya #1, Carlos | MEDIUM |
| Install script required per project | Carlos #1 | MEDIUM |
| Frameworks without config files invisible to router | Maya #8 | LOW |

**Root cause:** The install path assumes repo-local development, not package-based distribution.

### Cluster F: Integration Surface Gaps
*Affects: James, Priya*

| Finding | Source | Severity |
|---------|--------|----------|
| Slack interactive buttons non-functional | James #6 | MEDIUM |
| No Dockerfile | Priya #1 | MEDIUM |
| No Prometheus metrics | Priya #8 | MEDIUM |
| No structured JSON logging | Priya #8 | MEDIUM |
| ITSM integration is read-only | David #8 | LOW |
| Single shared API token, no RBAC | Priya #12 | LOW |

**Root cause:** External integration surfaces were spec'd but not fully implemented.

---

## Recommended Execution Plan (Revised)

Based on the team findings, the execution plan from SYNTHESIS-v1 needs significant revision. The daemon gate enforcement finding alone changes the priority stack.

### Phase 0: Emergency Fixes (Before Any Release)

These must ship before agent-baton can credibly claim its governance story:

| # | Work Item | Cluster | Effort | Justification |
|---|-----------|---------|--------|---------------|
| 0.1 | **Wire GateRunner into TaskWorker** — daemon must execute real gates | A | 1-2d | Without this, the #1 differentiator (QA gates) doesn't work in headless mode |
| 0.2 | **Track and kill subprocess PIDs on shutdown** | A | 1d | Orphaned processes = token leak + data corruption |
| 0.3 | **Fix readiness probe** — check SQLite, engine status, disk | A | 0.5d | Hardcoded `ready=True` makes k8s deployment unsafe |
| 0.4 | **Wire `recover_dispatched_steps` into resume path** | A | 0.5d | Zombie steps after crash |

**Total: ~3-4 days. These are pre-release blockers.**

### Phase A: Compliance Foundation (v0.2)

| # | Work Item | Cluster | Effort |
|---|-----------|---------|--------|
| A1 | Append-only `audit_log` table + human identity (`actor`) column | B | 2-3d |
| A2 | Persist `ClassificationResult` to SQLite | B | 1d |
| A3 | Persist `interaction_history` and `feedback_results` | B | 1d |
| A4 | Hard cost enforcement (`--token-limit` that aborts) | C | 1-2d |
| A5 | Gate retry mechanism (`baton execute retry-gate`) | A | 1-2d |
| A6 | `baton export` command (CSV + JSON) | B | 2d |

### Phase B: Deployment Shell (v0.2-0.3)

| # | Work Item | Cluster | Effort |
|---|-----------|---------|--------|
| B1 | Dockerfile + docker-compose (with `--foreground`, volume mounts) | F | 1-2d |
| B2 | Structured JSON logging (`python-json-logger`) | F | 1d |
| B3 | Prometheus `/metrics` endpoint | F | 1-2d |
| B4 | Fix `baton install` to resolve agents from installed package | E | 1d |
| B5 | Publish to PyPI | E | 0.5d |
| B6 | Accumulating handoff context (rolling summary across phases) | A | 1-2d |

### Phase C: Governance Maturity (v0.3)

| # | Work Item | Cluster | Effort |
|---|-----------|---------|--------|
| C1 | Named approver roles + risk-based routing | B | 2-3d |
| C2 | Git worktree isolation for parallel agents | A | 3-4d |
| C3 | CI pipeline gate type (GitHub Actions) | F | 3-4d |
| C4 | Slack interactive button callback endpoint | F | 1-2d |
| C5 | Daemon `--max-steps` flag | C | 0.5d |

### Phase D: Learning System Hardening (v0.5)

| # | Work Item | Cluster | Effort |
|---|-----------|---------|--------|
| D1 | Honest documentation of experiment system limitations | D | 0.5d |
| D2 | Lower pattern learner default `min_confidence` to 0.4 | D | 0.5d |
| D3 | Before/after trend visualization for convergence | D | 2d |
| D4 | Explicit plan template save/load | D | 2d |
| D5 | Per-stack scorecard filtering | D | 1d |

---

## Method Comparison: Solo vs. Team Audit

| Metric | Solo Audit (Pass 1) | Team Audit (Pass 2) |
|--------|:-------------------:|:-------------------:|
| Agents dispatched | 6 | 6 (+ Priya pilot) |
| Items evaluated | ~80 (across themes + personas) | 72 (12 per persona) |
| Unique findings | ~40 | 75+ (including 40+ team-exclusive) |
| Average rating | ~65% WORKS | ~18% WORKS |
| CRITICAL findings | 3 | 7 |
| Time per agent | ~4-5 min | ~6-7 min |

**The team method's value:** The structured probe→discover cycle caught enforcement gaps, runtime edge cases, and implementation stubs that surface-level feature checking missed. The persona framing ensured questions were operationally grounded ("what happens at 3 AM when this crashes") rather than abstract ("does crash recovery exist").

**Recommendation for future audits:** Always use the team dialogue method for production-readiness evaluation. Solo agents are sufficient for feature inventory.

---

## Audit Artifacts

All evidence is in `docs/competitive-audit/`:

### Pass 1 (Solo Agents)
| File | Scope |
|------|-------|
| `theme-1-4-governance-planning.md` | Stories 1.1-1.6, 4.1-4.6 |
| `theme-2-5-observability-learning.md` | Stories 2.1-2.6, 5.1-5.6 |
| `theme-3-6-remote-integration.md` | Stories 3.1-3.6, 6.1-6.6 |
| `persona-maya-carlos.md` | Solo persona walks |
| `persona-james-david.md` | Solo persona walks |
| `persona-priya-tomoko.md` | Solo persona walks |

### Pass 2 (Team Dialogues)
| File | Scope | New Findings |
|------|-------|:------------:|
| `team-priya-expert.md` | 10 production-readiness items | 5 |
| `team-maya-expert.md` | 12 solo-developer workflow items | 6 |
| `team-carlos-expert.md` | 12 overnight-batch items | 7 |
| `team-james-expert.md` | 12 manager-governance items | 16 |
| `team-david-expert.md` | 12 compliance deep-dive items | 15 |
| `team-tomoko-expert.md` | 12 learning/customization items | 29 |

---

## Bottom Line (Revised)

Agent Baton has built a **genuinely unique orchestration engine** — the governance pipeline, learning system, and observability stack exist at a depth no competitor offers. But the team audit reveals a consistent pattern: **features that are architecturally present are not always operationally sound**.

The single most important finding is that **daemon mode doesn't enforce gates** — the #1 competitive differentiator doesn't work in the #1 differentiated use case. Fixing this (Phase 0.1) is a 1-2 day effort that restores the credibility of the entire governance story.

After Phase 0 emergency fixes (~4 days), Phase A compliance work (~8 days) would unblock David's persona entirely — converting the compliance story from a liability to the strongest selling point. The raw data and control surfaces exist; the gaps are in enforcement, identity, and immutability — all addressable without architectural changes.

The learning system (Tomoko's domain) is the area where expectations most exceed reality. The infrastructure is impressive but the statistical rigor doesn't match the marketing. Honest documentation of current limitations (Phase D.1) costs nothing and builds trust; premature claims of "data-driven improvement" erode it.

**Estimated total effort for Phases 0+A+B:** ~15-20 days of focused work to transform Agent Baton from "impressive demo" to "production-ready for teams."
