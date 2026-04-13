# Audit Report: PMO System — Capability Gaps

**Scope:** `core/pmo/`, `api/routes/pmo.py`, `pmo-ui/`
**Date:** 2026-04-13
**Context:** PMO is envisioned as the full GUI/remote interface for Agent Baton, not just a dashboard.

---

## What Already Exists (Production-Ready)

| Capability | Status | Notes |
|------------|--------|-------|
| Kanban board (5 columns) | EXISTS | Multi-project, multi-program, filters |
| Live execution status | EXISTS | Card detail shows phases, steps, gate status |
| Trigger plan execution | EXISTS | `POST /pmo/execute/{card_id}` spawns headless subprocess |
| Create new plans (Forge) | EXISTS | LLM-powered with fallback to rule-based planner |
| Plan refinement (interview) | EXISTS | 3-5 targeted questions, regenerate based on answers |
| Real-time updates (SSE) | EXISTS | 13 board-relevant topics, 30s keepalive |
| Multi-project view | EXISTS | Scanner iterates all registered projects |
| Agent analytics | PARTIAL | Success rates computed on-demand, no persistence |
| Execution history | PARTIAL | Archived cards visible, no query/search API |

## Critical Gaps (Recommended for Immediate Work)

### 1. Human-in-the-Loop Gate Approval (CRITICAL)

PMO can trigger execution but cannot stop it, approve/reject gates, or intervene mid-execution. This is the single biggest blocker to the "remote operation" vision.

**Needed:**
- Gate approval API endpoint (pause execution at gate, present context, accept approve/reject)
- UI gate approval panel (shows gate context, diff, allows approve/reject with notes)
- Execution engine integration to pause at gates and wait for API-driven approval

**Files:** `api/routes/pmo.py`, new `GateApprovalPanel.tsx` component

### 2. Authentication / Authorization (HIGH)

All API endpoints are public. No session management, no RBAC, no audit trail of who performed actions. Single-tenant only.

**Needed:**
- Auth middleware (token-based or session-based)
- User/role model
- Per-endpoint authorization checks
- Audit logging of all actions with user attribution

**Files:** New auth middleware, user model, API route decorators

### 3. Policy / Workflow Configuration (HIGH)

Plans are approved and saved immediately. No approval chains, no "awaiting review" state, no role-based sign-off requirements.

**Needed:**
- Policy schema for approval requirements (e.g., "security review required for regulated data plans")
- Approval chain engine
- UI workflow configuration panel
- Integration with governance enforcement

### 4. Cost / Token Usage Tracking (MEDIUM)

Dashboard shows agent success rates but no cost attribution. Cannot answer "which project consumed $X?"

**Needed:**
- Cost model per agent/execution
- Token consumption aggregation
- Budget forecasting API
- Cost analytics UI panel

### 5. Compliance / Audit Trail Queries (MEDIUM)

Archive is append-only but not queryable. Cannot generate reports for: who approved what, when, why.

**Needed:**
- Audit event schema (queryable SQLite table)
- Audit API endpoints (search, filter, export)
- Compliance report generation
- UI audit log viewer

---

## Full Gap Inventory (Deferred)

### Governance & Workflow
- Plan rejection/rollback capability
- Approval chains / sign-off workflows
- Execution pause/resume from UI
- Priority/urgency-based routing

### Security
- Encrypted credential storage in plans
- API rate limiting
- Session timeout management

### Operations & Analytics
- SLA/reliability metrics (vs. simple success rate)
- Agent lifecycle management (enable/disable/configure from UI)
- Performance dashboards (latency, throughput)
- Budget alerts/thresholds

### Admin & Configuration
- Agent configuration panel (model, permissions, knowledge packs)
- Execution environment configuration
- Plan template management
- System health monitoring (db size, sync status)

### Data & Reporting
- Historical trace query API (cross-trace search)
- Signal triage history search
- Plan version history / change log
- Cross-project portfolio metrics
- Export/integration with external BI tools

---

## Forge Assessment

The Forge is the system's strongest feature:
- Interview-driven refinement (3-5 deterministic questions)
- Headless Claude + fallback to rule-based planner
- Signal triage (converts incidents into bug-fix plans automatically)
- Plan persistence scoped per task_id

Production-grade for *creation* workflows but lacks *governance* around approval.
