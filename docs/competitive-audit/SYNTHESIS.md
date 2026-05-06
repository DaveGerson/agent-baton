# Agent Baton Competitive Audit — Synthesis Report

**Date:** 2026-04-16
**Branch:** `feat/actiontype-interact`
**Method:** 6 parallel specialist agents audited the codebase against 36 user stories (6 themes) and 6 user archetypes from the competitive benchmark documents.

---

## Executive Summary

Agent Baton's **core orchestration engine and learning pipeline are exceptionally strong** — 22 of 36 user stories are FULLY MET, with particular depth in governance, observability, and continuous improvement. The system has genuine competitive advantages that no other tool in the ecosystem offers.

However, **three systemic gaps threaten the Mode 2 (team/remote) story** and would block adoption by platform engineers and compliance stakeholders:

1. **No containerization** (no Dockerfile, no Prometheus metrics, no structured logging)
2. **Mutable audit trail** (DELETE-then-INSERT patterns violate compliance requirements)
3. **Advisory-only resource governance** (no hard cost ceilings, no circuit breakers)

These gaps are concentrated in Themes 3 and 6, exactly the capabilities that differentiate Agent Baton from session managers.

---

## Story Scorecard (36 Stories)

| Rating | Count | Percentage |
|--------|-------|------------|
| FULLY MET | 22 | 61% |
| PARTIALLY MET | 8 | 22% |
| MINIMALLY MET | 2 | 6% |
| NOT MET | 4 | 11% |

### By Theme

| Theme | Fully | Partial | Minimal | Not Met | Health |
|-------|-------|---------|---------|---------|--------|
| 1. Governance & Quality (6) | 4 | 2 | 0 | 0 | Strong |
| 2. Visibility & Observability (6) | 5 | 1 | 0 | 0 | Strong |
| 3. Remote & Headless (6) | 0 | 4 | 1 | 1 | Weak |
| 4. Planning Intelligence (6) | 3 | 1 | 2 | 0 | Moderate |
| 5. Learning & Improvement (6) | 5 | 1 | 0 | 0 | Strong |
| 6. Integration & Extensibility (6) | 0 | 3 | 0 | 3 | Weak |

**Pattern:** Themes 1, 2, and 5 (the engine's internal capabilities) are mature. Themes 3 and 6 (the external-facing integration surface) are the weakest. This aligns with a project that has been developed from the inside out — the engine is solid, but the deployment and integration shell is incomplete.

---

## Persona Scorecard (6 Archetypes)

| Persona | WORKS | PARTIAL | BLOCKED | Verdict |
|---------|-------|---------|---------|---------|
| Maya (Solo Power User) | 9/13 | 2/13 | 0 | **ADOPT** |
| Carlos (Backlog Drainer) | 8/13 | 3/13 | 1 | **TRIAL** (cost ceiling blocks overnight) |
| James (Eng Manager) | 6/10 | 4/10 | 0 | **PARTIAL PASS** |
| David (Compliance) | 7/15 | 5/15 | 3 | **FAIL** (3 compliance dealbreakers) |
| Priya (Platform Eng) | 10/20 | 6/20 | 4 | **CONDITIONAL PASS** |
| Tomoko (Workflow Designer) | 13/17 | 4/17 | 0 | **PASS** |

**Pattern:** Mode 1 personas (Maya, Tomoko) are well-served. Mode 2 personas (James, Priya, David, Carlos) all hit gaps in the deployment, governance, or cost-control surfaces. David's FAIL verdict is the most concerning — compliance is a stated differentiator but the audit trail has a fundamental integrity issue.

---

## Competitive Strengths (What's Working)

These capabilities exceed what any competitor in the ecosystem offers:

### 1. Governance Pipeline (Theme 1)
The risk classification → policy enforcement → auditor veto → approval workflow chain is **unique in the ecosystem**. Four risk tiers, five policy presets, a fully-defined auditor agent with three operating modes, and automatic injection of security-reviewer phases for HIGH-risk tasks. No other tool has this depth.

### 2. Learning Automation Loop (Theme 5)
`baton scores` → `baton patterns` → `baton evolve` → `baton experiment` forms a **genuine closed-loop improvement system**. Pattern detection with confidence scoring, OLS trend regression, prompt evolution with VCS backups, and controlled experiments with auto-rollback. 5 of 6 learning stories are FULLY MET. This is the long-term moat the strategy document identifies, and it's already built.

### 3. Observability Stack (Theme 2)
Real-time SSE-driven PMO dashboard, structured beads with dependency graphs and knowledge promotion, webhook infrastructure with HMAC signing and Slack Block Kit, automated retrospective generation, and 17+ predefined analytics queries with SQL escape hatch. The data richness exceeds what many commercial APM tools provide for their own domains.

### 4. Planning Intelligence (Theme 4)
Stack detection for 11 languages + 11 frameworks, risk-aware plan generation with automatic phase injection, plan amendment mid-execution with audit trail, and Haiku-based complexity classification. The planner is genuinely intelligent about what it produces.

### 5. CLI Surface Quality
190ms startup time, 49 commands across 6 groups, no required configuration, auto-detection of stack/risk/budget, JSON/CSV/table output formats. Maya's journey validation showed zero dealbreakers in CLI ergonomics.

---

## Critical Gaps (What's Blocking)

### Gap 1: Mutable Audit Trail (David — COMPLIANCE DEALBREAKER)

**Impact:** Blocks all regulated-industry adoption. Invalidates the compliance differentiation story.

The SQLite backend uses DELETE-then-INSERT patterns for child records (`step_results`, `gate_results`, `approval_results`). Historical data can be overwritten. There are no append-only tables, no SQLite triggers preventing UPDATE/DELETE, and no checksumming for tamper detection. Additionally, `approval_results` lacks `approved_by` and `justification` fields — an external auditor would reject this immediately.

**Recommendation:** Create a separate `audit_log` table that is INSERT-only (application-level + SQLite trigger enforcement). Add `approved_by TEXT`, `justification TEXT`, and `approver_role TEXT` to `approval_results` as schema migration v10. Add configurable pattern-matching redaction for secrets/PII before persistence.

**Effort:** Medium (2-3 days). **Impact:** Unblocks entire compliance persona.

---

### Gap 2: No Containerization or Production Observability (Priya — DEPLOYMENT BLOCKER)

**Impact:** Blocks team/remote deployment. The entire Mode 2 value proposition depends on agents running on infrastructure.

No Dockerfile exists anywhere in the repository. No Prometheus metrics endpoint. Logging uses plain-text `%(asctime)s %(levelname)s %(message)s` format — not structured JSON. No Helm chart, no k8s manifests, no container health check examples. Priya would need to write all deployment infrastructure from scratch.

**Recommendation:** 
1. Add a Dockerfile + docker-compose.yml with health probes and resource limits
2. Add `/metrics` endpoint with `prometheus_client` (counters: dispatched_steps, completed_tasks, gate_results, active_agents, token_consumption)
3. Retrofit structured JSON logging via `python-json-logger` or `structlog`

**Effort:** Medium (2-3 days). **Impact:** Unblocks Mode 2 deployment story.

---

### Gap 3: Advisory-Only Resource Governance (Carlos — OVERNIGHT BLOCKER)

**Impact:** Blocks the "overnight backlog drainer" use case — the most differentiated Mode 2 scenario.

`_check_token_budget()` returns a warning string but does not halt execution. There is no `--token-limit` flag, no per-task cost cap enforcement, no circuit breakers, no emergency stop (`baton daemon halt`), and no quota CLI. Carlos cannot safely leave agents running overnight without risk of unbounded API spend.

**Recommendation:** 
1. Add `--token-limit N` flag to `baton execute run` and `baton daemon start` that aborts on overspend
2. Add `--cost-cap-per-task N` on daemon for individual task limits
3. Add `baton daemon halt` for immediate kill-all
4. Wire `budget_deviation` anomaly to webhook system for real-time cost alerts

**Effort:** Low-Medium (1-2 days). **Impact:** Unblocks overnight autonomous execution.

---

### Gap 4: No Git Worktree Isolation (Story 3.3 — NOT MET)

**Impact:** Parallel agents all write to the same `Path.cwd()`. File conflicts during concurrent execution are unguarded.

Zero worktree references exist in `agent_baton/`. The `StepScheduler` dispatches parallel agents via `asyncio.Semaphore` but provides no filesystem isolation. This is a fundamental limitation for safe parallel execution at scale.

**Recommendation:** When daemon dispatches 3+ concurrent agents, auto-create git worktrees per agent. Merge back on gate pass. Preserve on gate fail for debugging. Configurable cleanup policy.

**Effort:** Medium (3-4 days). **Impact:** Enables reliable parallel execution.

---

### Gap 5: No CI Pipeline Integration (Story 6.1 — NOT MET)

**Impact:** Agent-produced code goes through internal gates but not the team's real CI pipeline. Creates a trust gap where baton gates pass but GitHub Actions fails.

Gates are local shell commands only. No CI provider integration exists. The strategy document explicitly identifies this as a key synergy from Multiclaude's architecture.

**Recommendation:** Add a `ci` gate type that triggers GitHub Actions via the Checks API, polls for completion, and returns CI result as the gate result. Start with GitHub Actions only; add GitLab CI and CircleCI later.

**Effort:** Medium (3-4 days). **Impact:** Bridges internal governance with external CI trust.

---

### Gap 6: No Exportable Audit Reports (Story 6.5 — NOT MET)

**Impact:** David cannot produce auditor-ready documentation without manually running multiple queries and assembling results.

Rich raw data exists (traces, retros, usage, compliance reports) but there is no formatting or export pipeline. No PDF generation, no consolidated audit report template, no tamper-detection hashes.

**Recommendation:** Add `baton export --task TASK_ID --format pdf|csv|json` that assembles execution timeline + gate results + approval records + bead decisions + cost breakdown into a single document. Use `weasyprint` for PDF.

**Effort:** Medium (2-3 days). **Impact:** Completes the compliance story for David.

---

## Moderate Gaps (Impactful but Not Blocking)

| Gap | Stories | Effort | Impact |
|-----|---------|--------|--------|
| Named approver roles + escalation chains | 1.4 | Medium | Operational governance at scale |
| `--skip-phase` flag on planner | 4.5 | Low | Power user convenience |
| Explicit plan template save/load | 4.6 | Low-Med | Workflow reuse for Tomoko |
| `expected_outcome` field on PlanStep | 4.4 | Low-Med | Verification-first execution quality |
| Webhook CLI (`baton webhook add`) | 6.3 | Low | Parity between API and CLI surfaces |
| Anomaly-to-webhook push alerting | 5.4 | Low | Real-time operational awareness |
| Dollar cost conversion | 2.4 | Low | Executive reporting for James |
| Structured handoff documents (git diff + gates) | 6.4 | Medium | Phase transition quality |
| Explicit `baton execute pause` command | 3.5 | Low | Multi-day workflow ergonomics |
| PyPI publication | — | Low | Adoption friction for all personas |

---

## Recommended Execution Plan

### Phase A: Compliance & Deployment Foundation (v0.2 priority)

**Goal:** Unblock David and Priya — the two personas whose approval determines enterprise adoption.

| # | Work Item | Stories | Persona | Effort | Priority |
|---|-----------|---------|---------|--------|----------|
| A1 | Append-only audit log + approval record fields | 2.2, 1.4 | David | 2-3d | CRITICAL |
| A2 | Dockerfile + docker-compose + Prometheus metrics | 3.1 | Priya | 2-3d | CRITICAL |
| A3 | Structured JSON logging | 3.1 | Priya | 1d | CRITICAL |
| A4 | Hard cost enforcement (`--token-limit`) | 3.6 | Carlos | 1-2d | CRITICAL |
| A5 | `baton export` command (CSV + JSON, PDF optional) | 6.5 | David | 2-3d | HIGH |
| A6 | Publish to PyPI | — | Maya, Carlos | 0.5d | HIGH |

**Total estimated effort:** 9-13 days
**Unlocks:** Compliance persona (David), platform persona (Priya), and overnight execution (Carlos)

### Phase B: Parallel Execution & Integration (v0.3 priority)

**Goal:** Deliver the Mode 2 flagship capabilities.

| # | Work Item | Stories | Persona | Effort |
|---|-----------|---------|---------|--------|
| B1 | Git worktree isolation for parallel agents | 3.3 | All Mode 2 | 3-4d |
| B2 | CI pipeline gate type (GitHub Actions) | 6.1 | Priya, James | 3-4d |
| B3 | Named approver roles + timeout escalation | 1.4 | James, David | 2-3d |
| B4 | Structured handoff documents (git diff + gates) | 6.4 | Maya, Tomoko | 2d |
| B5 | Anomaly-to-webhook push alerting | 5.4 | James, Priya | 1d |

### Phase C: Power User & Polish (v0.5)

| # | Work Item | Stories |
|---|-----------|---------|
| C1 | Explicit plan template save/load | 4.6 |
| C2 | `expected_outcome` field + gate validation | 4.4 |
| C3 | `--skip-phase`, `--budget` CLI flags | 4.5 |
| C4 | Plugin gate architecture | 6.6 |
| C5 | Dollar cost conversion + executive report templates | 2.4 |
| C6 | RBAC / scoped API tokens | 3.4 |

---

## Cross-Reference: Strategy Document Alignment

The strategy document (Part 1) identified 8 capabilities to "Keep and Strengthen." Here's how they score:

| Strategy Capability | Audit Verdict | Notes |
|---------------------|---------------|-------|
| CLI-Output-as-Contract | **Strong** | Working as designed; 190ms startup; clean boundary |
| SQLite-Backed State Machine | **Strong with caveat** | Robust persistence but mutable — needs audit-log hardening |
| QA Gates Between Phases | **Strong** | FULLY MET. Stack-adaptive, 9 languages, anomaly detection |
| Risk-Tiered Safety + Auditor | **Strong** | 4 tiers, 5 presets, auditor veto, approval workflows |
| Beads (Structured Memory) | **Strong** | 5 types, SQLite persistence, graph visualization, promotion |
| Daemon + Headless Execution | **Partial** | Engine works; deployment packaging missing (Docker, metrics, logging) |
| REST API + PMO Dashboard | **Strong** | 10 route modules, SSE, Kanban, analytics, data export |
| Learning Automation Loop | **Strong** | 5/6 stories FULLY MET; closed-loop with experiments |

The strategy document also identified capabilities to "Defer or Kill." Current state validates these recommendations:
- External source adapters: **4 adapters exist but are opt-in** — correctly scoped
- Agent roster trimming to 7-8: **20 agents exist** — still needs consolidation per strategy
- CLI consolidation to 15-20: **49 commands exist** — needs the "Getting Started" subset per strategy
- Cross-project intelligence: **Implemented in central.db** — usable but shouldn't be promoted yet

---

## Competitive Synergy Assessment

The strategy document identified 7 synergies from competing tools. Status:

| Synergy | Source | Status | Priority |
|---------|--------|--------|----------|
| Frictionless onboarding (pip install) | Claude Squad | **Unmet** — not on PyPI | Phase A |
| CI-as-quality-gate | Multiclaude | **Unmet** — no CI provider integration | Phase B |
| Git worktree isolation | Gastown | **Unmet** — zero worktree code | Phase B |
| Shared task list for agents | Agent Teams | **Partial** — execution state visible but not formatted for agent consumption | Phase C |
| Demo statements | CAS | **Minimal** — `deliverables` field exists but not validated | Phase C |
| Real-time SSE dashboard | Conductor | **Met** — SSE with Kanban, analytics, data export | Done |
| Declarative YAML config | Claude Swarm | **Unmet** — no `baton.yaml` workflow file | Future |

---

## Bottom Line

Agent Baton has built a genuinely unique orchestration engine with capabilities no competitor offers — the governance pipeline, learning automation, and observability stack are production-quality. The gap is in the **deployment and integration shell** that wraps this engine for external consumption.

The 6 critical items in Phase A represent approximately 2 weeks of focused work and would unblock 3 of the 4 personas that currently have reservations. The mutable audit trail fix is the single highest-leverage change: it converts a compliance FAIL to a compliance differentiator, which is the core of Agent Baton's positioning story.

---

## Audit Artifacts

All detailed evidence is in `docs/competitive-audit/`:

| File | Scope |
|------|-------|
| `theme-1-4-governance-planning.md` | Stories 1.1-1.6, 4.1-4.6 with line-level evidence |
| `theme-2-5-observability-learning.md` | Stories 2.1-2.6, 5.1-5.6 with line-level evidence |
| `theme-3-6-remote-integration.md` | Stories 3.1-3.6, 6.1-6.6 with line-level evidence |
| `persona-maya-carlos.md` | Solo Power User + Backlog Drainer journey walks |
| `persona-james-david.md` | Engineering Manager + Compliance Stakeholder journey walks |
| `persona-priya-tomoko.md` | Platform Engineer + Workflow Designer journey walks |
