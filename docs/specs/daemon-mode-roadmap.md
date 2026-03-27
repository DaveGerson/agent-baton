# Daemon Mode Roadmap — Closing the Gaps

**Date**: 2026-03-27
**Status**: Proposal
**Scope**: 5 phases, from foundational wiring through event-driven autonomy

---

## Executive Summary

Daemon mode today is a solid execution runtime — it takes a pre-built plan
and runs it to completion with parallel dispatch, crash recovery, and
real-time monitoring. But it cannot plan, cannot be triggered by external
events, and cannot adapt mid-execution. This roadmap closes those gaps
across five phases, building toward a daemon that can receive a bug report,
research it, propose a fix, get approval, execute with an agent team, and
report back — all without a human touching a terminal.

The PMO board is the user's primary interface throughout. Every phase
delivers value visible on the board.

---

## Current State (What Works)

| Capability | Status |
|---|---|
| Background execution (`baton daemon start --plan`) | Production (67/67 tests) |
| Parallel agent dispatch (`--max-parallel`) | Production |
| Crash recovery (`--resume`) | Production |
| Concurrent daemons (`--task-id`) | Production |
| API co-hosting (`--serve`) with SSE | Production |
| Graceful shutdown (SIGTERM, 30s drain) | Production |
| Status monitoring (`baton daemon status`) | Production |
| Outbound webhooks (HMAC, retry, auto-disable) | Production |
| PMO Forge (plan generation + interview) | Production |
| PMO Execute (launch headless from board) | Production |
| PMO Signals (create, triage → plan) | Production |
| External source adapter protocol (ADO impl) | Production |
| DecisionManager (file-based human-in-the-loop) | Production |

---

## Phase 1: Plan-Then-Execute (The Missing Bridge)

**Goal**: Eliminate the two-step `baton plan` → `baton daemon start` workflow.
Let the daemon plan its own work from a natural-language description.

### Problem

Today the daemon is headless but not brainless — it needs someone to hand
it a fully-formed plan. This makes it useless as an autonomous responder.
Every trigger workflow (webhook, signal, scheduled) must include an external
planning step before the daemon can start.

### Deliverables

**1.1 — `baton daemon run` command**

New subcommand that accepts a task description and produces a running daemon:

```bash
baton daemon run "Fix the authentication timeout bug in the login flow" \
  --knowledge docs/auth.md \
  --knowledge-pack security \
  --max-parallel 3 \
  --serve \
  --task-id auth-timeout-fix
```

Internally:
1. Calls `IntelligentPlanner.create_plan()` (or `ForgeSession` if
   HeadlessClaude is available) with the description and flags
2. Persists the plan to `executions/<task-id>/plan.json`
3. Passes the plan to `supervisor.start()` or `_run_daemon_with_api()`
4. Returns immediately (background) or blocks (foreground)

Flags inherited from `baton plan`: `--task-type`, `--agents`, `--complexity`,
`--knowledge`, `--knowledge-pack`, `--intervention`, `--model`.

**1.2 — PMO Execute upgrade: daemon mode option**

The `POST /api/v1/pmo/execute/{card_id}` endpoint currently spawns
`baton execute run` (sequential). Add a `parallel: true` option that
spawns `baton daemon start` instead, enabling parallel agent dispatch
from the board.

**1.3 — PMO "Quick Launch" endpoint**

New `POST /api/v1/pmo/launch` that accepts a raw description (no
pre-existing card required), generates a plan, creates a card, and
starts a daemon — one API call from description to running execution.

### User Stories Addressed

> *"I describe a task on the board and click Execute. The system plans and
> runs it without me touching the CLI."*

> *"I paste a bug description into the PMO quick-launch field and walk
> away. When I come back, the fix is in a branch waiting for my review."*

### Key Files

| File | Change |
|---|---|
| `cli/commands/execution/daemon.py` | Add `run` subcommand + handler |
| `api/routes/pmo.py` | Upgrade execute endpoint, add launch endpoint |
| `core/runtime/supervisor.py` | Accept plan-generation params in `start()` |

### Tests

- Unit: `daemon run` with dry-run launcher completes
- Unit: PMO execute with `parallel=true` spawns daemon process
- Integration: description → plan → daemon → completion (dry-run)

---

## Phase 2: Inbound Triggers (Event-Driven Daemon Launch)

**Goal**: External events (bug filed, alert fired, PR merged) automatically
trigger the daemon pipeline without human initiation.

### Problem

The daemon can run autonomously, but someone must start it. There's no way
for an external system to say "here's a bug, go fix it." Outbound webhooks
exist (mature, signed, retried), but inbound webhooks don't.

### Deliverables

**2.1 — Inbound trigger endpoint**

```
POST /api/v1/triggers
Content-Type: application/json
X-Trigger-Source: github
X-Trigger-Secret: <hmac-signature>

{
  "event_type": "issue.created",
  "source": "github",
  "payload": { ... raw GitHub webhook payload ... }
}
```

The endpoint:
1. Validates HMAC signature against a configured secret
2. Normalizes the payload into an `ExternalItem` (using source adapters)
3. Creates a PMO Signal with the normalized data
4. Optionally auto-triages (see 2.2)
5. Returns `202 Accepted` with signal ID

**2.2 — Auto-triage policy**

Configurable policy per source that determines what happens when a trigger
arrives:

```yaml
# .claude/team-context/trigger-policies.yaml
triggers:
  - source: github
    event: issue.created
    labels_match: ["bug"]        # only bugs, not features
    auto_triage: true            # auto-generate plan via ForgeSession
    auto_execute: false          # don't start daemon yet (needs approval)
    assign_program: "PRODUCT"

  - source: ado
    event: workitem.updated
    state_match: "Active"
    auto_triage: true
    auto_execute: true           # fully autonomous for low-risk items
    risk_threshold: "LOW"        # only auto-execute if planner rates LOW risk
```

When `auto_triage: true`:
- Calls `ForgeSession.signal_to_plan()` to generate a fix plan
- Creates a PMO card in "Proposed" (if `auto_execute: false`) or "Queued"
- If `auto_execute: true` AND risk ≤ threshold: starts daemon immediately

**2.3 — Source adapter for GitHub Issues/Webhooks**

New adapter alongside the existing ADO adapter:

```python
class GitHubAdapter(ExternalSourceAdapter):
    source_type = "github"

    def normalize_webhook(self, event_type: str, payload: dict) -> ExternalItem:
        """Convert GitHub webhook payload to ExternalItem."""
```

### User Stories Addressed

> *"A customer files a bug on GitHub. Within minutes, a card appears on my
> PMO board with a diagnosis and proposed fix plan. I review and approve
> with one click."*

> *"Low-severity bugs from our tracker are automatically triaged, planned,
> and executed overnight. I review the PRs in the morning."*

### The Bug-to-Fix Pipeline (End-to-End)

```
GitHub Issue Created
  → POST /api/v1/triggers (inbound webhook)
  → Signal created (PMO signal store)
  → Auto-triage: ForgeSession.signal_to_plan()
      → Phase 1: Researcher agent diagnoses root cause
      → Phase 2: Implementation (agent team)
      → Phase 3: Test + review gates
  → Card appears on PMO board in "Proposed" column
  → User reviews diagnosis + plan on board
  → User clicks "Approve & Execute"
  → Daemon starts (parallel dispatch)
  → Card moves through "In Progress" → "Done"
  → Outbound webhook notifies Slack: "Bug #123 fixed, PR ready"
```

### Key Files

| File | Change |
|---|---|
| `api/routes/triggers.py` | New route module for inbound triggers |
| `api/server.py` | Register triggers route |
| `core/storage/adapters/github.py` | New GitHub adapter |
| `core/pmo/store.py` | Add trigger policy loading |
| `models/triggers.py` | TriggerPolicy, InboundEvent models |

### Tests

- Unit: Trigger endpoint validates HMAC, rejects bad signatures
- Unit: Auto-triage creates signal + plan when policy matches
- Unit: Risk threshold blocks auto-execute for HIGH risk plans
- Integration: GitHub issue webhook → signal → plan → card on board

---

## Phase 3: Decision-Gated Autonomous Workflows

**Goal**: The daemon runs long workflows autonomously, pausing at
human checkpoints and resuming when decisions are made — all mediated
through the PMO board.

### Problem

The `DecisionManager` exists and the daemon polls it, but the UX for
making decisions is weak. Decisions are file-based and require CLI
interaction or direct API calls. The PMO board should be the decision
interface.

### Deliverables

**3.1 — Decision rendering on PMO board**

When a daemon hits an APPROVAL or human GATE, the card on the board
shows:
- What the daemon has accomplished so far (completed phases/steps)
- What it's asking for (approval context, gate description)
- Action buttons: Approve / Reject / Approve with Feedback
- A text field for feedback (if "Approve with Feedback")

Resolving on the board calls `POST /api/v1/decisions/{id}/resolve`,
which the daemon picks up on its next poll cycle.

**3.2 — Diagnostic-first plans**

Plans generated for bug triage should default to a diagnostic-first
pattern:

```
Phase 1: Research (read-only agents)
  Step 1.1 → researcher: read logs, trace code, identify root cause
  APPROVAL GATE: "Review diagnosis before proceeding"

Phase 2: Implementation (if approved)
  Step 2.1 → backend-engineer: implement fix
  Step 2.2 → test-engineer: write regression test
  Gate: pytest passes

Phase 3: Review
  Step 3.1 → code-reviewer: review changes
```

The daemon runs Phase 1 autonomously, then pauses with the diagnosis
visible on the board. The user reads the diagnosis, decides whether the
approach is right, and approves (or adjusts) before any code is written.

**3.3 — Decision escalation via outbound webhook**

When a decision is needed, fire an outbound webhook (and optionally an
email/Slack notification) so the user doesn't have to watch the board:

```json
{
  "topic": "human.decision_needed",
  "task_id": "auth-timeout-fix",
  "payload": {
    "decision_type": "phase_approval",
    "summary": "Phase 1 complete: root cause identified as session cache TTL...",
    "options": ["approve", "reject", "approve-with-feedback"],
    "dashboard_url": "http://localhost:8741/pmo/#/cards/auth-timeout-fix"
  }
}
```

**3.4 — Decision timeout policy**

Configurable timeouts so daemons don't hang forever:

```yaml
decisions:
  default_timeout: 24h
  timeout_action: reject     # or "auto-approve" for low-risk
  reminder_interval: 4h     # re-send webhook reminder
```

### User Stories Addressed

> *"The daemon researched the bug and found the root cause. I see the
> diagnosis on the board with a one-click approve button. I approve
> and it implements the fix."*

> *"I got a Slack notification that a daemon needs my approval. I click
> the link, review the plan on the PMO board, and approve from my phone."*

### Key Files

| File | Change |
|---|---|
| `api/routes/pmo.py` | Decision rendering in card detail |
| `core/runtime/worker.py` | Decision timeout handling |
| `core/runtime/decisions.py` | Timeout policy, reminder scheduling |
| `core/engine/planner.py` | Diagnostic-first plan templates |

---

## Phase 4: Adaptive Execution (Runtime Plan Mutation)

**Goal**: The daemon can modify its own plan during execution based on
what agents discover — adding steps, bringing in new agents, or
re-routing work.

### Problem

Today the plan is fixed at creation time. If Step 2.1 reveals that the
bug is actually in the auth middleware (not the login handler), the
daemon can't pivot. It finishes the wrong plan or fails.

### Deliverables

**4.1 — Runtime amendment triggers**

The `TaskWorker` inspects step outcomes after each dispatch batch. If
an agent's output contains structured signals, the worker amends the
plan:

```python
# In TaskWorker._execution_loop(), after recording results:
for result in results:
    amendments = self._check_for_amendments(result)
    if amendments:
        for amendment in amendments:
            self._engine.amend(amendment)
```

Amendment triggers:
- **Agent requests help**: Output contains `NEED_AGENT: security-reviewer`
  → worker adds a security-review step to the current phase.
- **Scope expansion**: Output contains `SCOPE_CHANGE: also affects
  src/middleware/` → worker adds affected files to remaining steps'
  context.
- **Risk escalation**: Agent flags security concern → worker inserts an
  approval gate before the next phase.

**4.2 — Adaptive agent slots**

Plan steps can specify `agent_name: "auto"` with selection criteria:

```json
{
  "step_id": "2.3",
  "agent_name": "auto",
  "selection_criteria": {
    "if_files_touched_match": "*.sql",
    "then_agent": "data-engineer",
    "else_agent": "backend-engineer--python"
  }
}
```

The `TaskWorker` resolves `auto` agents at dispatch time using the
`AgentRouter` + context from prior steps.

**4.3 — Amendment audit trail**

All runtime amendments are recorded:
- In the execution state (`amendments` list with timestamp, reason, diff)
- As `plan.amended` events on the EventBus
- Visible on the PMO board card's timeline

### User Stories Addressed

> *"The engineer agent found a SQL injection vulnerability while fixing
> the bug. The daemon automatically brought in the security reviewer
> before merging."*

> *"I launched a simple feature task. Mid-execution, the architect
> realized it needed database schema changes. The daemon added a
> data-engineer step and a migration gate without stopping."*

### Key Files

| File | Change |
|---|---|
| `core/runtime/worker.py` | Amendment detection loop |
| `core/engine/executor.py` | Extend `amend()` for auto-triggered amendments |
| `core/orchestration/router.py` | Resolve `auto` agent slots |
| `models/execution.py` | AmendmentRecord model, adaptive slot schema |

---

## Phase 5: Orchestrated Daemon Coordination

**Goal**: Multiple daemons coordinate across projects and tasks, managed
from a unified PMO view.

### Problem

Each daemon is isolated — it runs one plan in one project directory. There's
no way to express "update the API in the backend repo, then regenerate the
client SDK in the frontend repo, then run integration tests across both."

### Deliverables

**5.1 — Meta-plans (cross-project orchestration)**

A `MetaPlan` contains multiple `MachinePlan` instances with inter-plan
dependencies:

```json
{
  "meta_task_id": "api-contract-update",
  "plans": [
    {
      "plan_id": "backend-api-update",
      "project_dir": "~/repos/backend-api",
      "plan": { ... MachinePlan ... }
    },
    {
      "plan_id": "frontend-sdk-regen",
      "project_dir": "~/repos/frontend-app",
      "depends_on": ["backend-api-update"],
      "plan": { ... MachinePlan ... }
    }
  ]
}
```

A `MetaSupervisor` launches individual daemons per plan and manages
inter-plan dependencies (start plan B only when plan A completes).

**5.2 — PMO multi-project dashboard**

The PMO board shows cross-project execution as linked cards:

```
┌─────────────────────────────────────────────────────┐
│ Meta-Task: API Contract Update                       │
│                                                      │
│ [backend-api-update]  ──depends──▶  [frontend-sdk]  │
│  ✅ Phase 1: Design                 ⏳ Waiting...    │
│  ✅ Phase 2: Implement                               │
│  ✅ Phase 3: Test                                    │
│  ✅ Complete                                         │
└─────────────────────────────────────────────────────┘
```

**5.3 — Daemon-to-daemon event forwarding**

When daemon A completes, it publishes `task.completed`. The
`MetaSupervisor` subscribes to these events and starts dependent
daemons. This uses the existing EventBus + persistence — the meta
supervisor reads events from daemon A's JSONL log to detect completion.

### User Stories Addressed

> *"I need to update an API contract that spans two repos. I describe
> the change once on the PMO board and the system coordinates backend
> and frontend work automatically, in the right order."*

> *"Our monorepo migration involves 5 services. The PMO shows the
> dependency graph and each service's daemon progress. When service A
> finishes its API, service B's daemon starts automatically."*

### Key Files

| File | Change |
|---|---|
| `models/execution.py` | MetaPlan, PlanDependency models |
| `core/runtime/meta_supervisor.py` | New: cross-daemon coordination |
| `cli/commands/execution/daemon.py` | `baton daemon run-meta` subcommand |
| `api/routes/pmo.py` | Meta-task rendering |

---

## Phase Summary

| Phase | Delivers | PMO Impact | Depends On |
|---|---|---|---|
| **1: Plan-Then-Execute** | `daemon run`, PMO quick-launch | "Describe and go" from board | — |
| **2: Inbound Triggers** | Webhook intake, auto-triage, GitHub adapter | Bugs auto-appear as cards | Phase 1 |
| **3: Decision-Gated Workflows** | Board-based approvals, diagnostic-first plans | Board becomes decision hub | Phase 1 |
| **4: Adaptive Execution** | Runtime amendments, auto agent slots | Daemons self-correct | Phase 1 |
| **5: Cross-Project Coordination** | Meta-plans, multi-daemon orchestration | Unified multi-repo view | Phase 1-3 |

Phases 2, 3, and 4 can be developed in parallel after Phase 1. Phase 5
depends on the patterns established in Phases 1-3.
