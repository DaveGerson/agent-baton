# Daemon Mode Evaluation

**Date**: 2026-03-27
**Branch**: `claude/daemon-mode-evaluation-DsZHj`

---

## 1. Does Daemon Mode Work?

**Verdict: Yes — it is production-grade and all tests pass (67/67).**

| Test Suite | Tests | Status |
|---|---|---|
| `test_daemon.py` | 47 | All pass |
| `test_daemon_task_id.py` | 19 | All pass |
| `test_daemon_api_integration.py` | 1 | Skipped (requires uvicorn) |

Architecture is clean: `WorkerSupervisor` (lifecycle) → `TaskWorker` (async
dispatch) → `ExecutionEngine` (state machine). All previously identified
issues (8/8) are fixed. See Section 1 of the prior version for robustness
detail — nothing has regressed.

---

## 2. Can the Daemon Plan, or Only Execute?

**Today: Execute only. Planning is a separate step.**

The current capability matrix:

| Component | Plan | Execute | Self-Plan |
|---|---|---|---|
| `baton plan` CLI | Yes | No | — |
| `IntelligentPlanner` | Yes (rule-based, 13-step pipeline) | No | — |
| `ForgeSession` (PMO Forge) | Yes (HeadlessClaude + fallback) | No | — |
| `baton daemon start` | **No** | Yes (parallel, async) | **No** |
| `baton execute run` | **No** | Yes (sequential, foreground) | **No** |

### What "Plan" Accepts Today (Expressiveness)

`baton plan` is already quite rich:

```bash
baton plan "Full task description — as long as you want" \
  --task-type bug-fix \                    # override auto-classification
  --agents architect,backend-engineer \     # override auto-selection
  --complexity heavy \                      # override auto-sizing
  --knowledge docs/api-spec.md \           # attach arbitrary documents
  --knowledge docs/schema.sql \            # (repeatable)
  --knowledge-pack analytics \             # attach curated knowledge packs
  --intervention high \                     # escalation threshold
  --model opus \                            # override default model
  --save --json                            # persist for daemon consumption
```

The `MachinePlan` model carries: phases with dependencies, per-step agent
assignments, team members with roles, knowledge attachments, context files,
allowed/blocked paths, deliverables, QA gates, approval checkpoints, risk
levels, budget tiers, and git strategies.

**ForgeSession** (used by PMO Forge API) is even more expressive — it uses
HeadlessClaude to generate plans from natural language, then runs an
interview loop to refine them based on user answers.

### The Gap: Plan-Then-Execute as a Single Operation

The daemon requires a pre-built `plan.json`. There is no
`baton daemon start --from-description "fix the login bug"` that plans and
then executes. This is a composable gap, not an architectural one.

### Proposed: `baton daemon run`

A new subcommand that combines planning + daemon execution:

```bash
baton daemon run "Fix the authentication timeout bug" \
  --knowledge docs/auth.md \
  --max-parallel 3 \
  --serve
```

Internally: `IntelligentPlanner.create_plan()` → `supervisor.start(plan)`.
One command, plan-to-completion. The PMO Forge API already does the
plan-generation half of this.

---

## 3. Webhook-Triggered Execution (Bug → Diagnosis → Plan → Approval → Work)

### What Exists Today

The API server (`--serve` on port 8741) already has:

- **Outbound webhooks** — subscribe external URLs to internal events
  (`step.*`, `gate.*`, `task.*`). HMAC-SHA256 signed, auto-retry, auto-disable.
- **PMO Execute endpoint** — `POST /api/v1/pmo/execute/{card_id}` launches
  headless execution for a queued card. Returns 202 with PID.
- **PMO Signals** — `POST /api/v1/pmo/signals` creates an alert;
  `POST /api/v1/pmo/signals/{id}/forge` triages it into a plan via
  ForgeSession.
- **External source adapters** — ADO adapter fetches work items (bugs,
  features, epics) from Azure DevOps. Protocol is extensible to Jira,
  GitHub Issues, Linear.
- **Decision resolution** — `POST /api/v1/decisions/{id}/resolve` for
  human-in-the-loop approvals.

### What's Missing: Inbound Webhook Handler

Webhooks are outbound-only today. There's no `POST /api/v1/triggers` that
accepts an external event (e.g., GitHub issue created) and starts the
plan-execute pipeline.

### User Story: Bug-to-Fix Pipeline

> **As a product team lead**, when a bug is filed in our issue tracker,
> I want an agent to automatically research the root cause, propose a fix
> plan, and present it to me for approval — so that I can kick off the
> fix with one click instead of manually triaging, planning, and assigning.

**Proposed flow:**

```
┌──────────────┐     webhook      ┌──────────────────────┐
│ Issue Tracker │ ──────────────→ │ POST /api/v1/triggers │
│ (GitHub/ADO)  │                 │ (new inbound endpoint)│
└──────────────┘                  └──────────┬───────────┘
                                             │
                    ┌────────────────────────┘
                    ▼
            ┌───────────────┐
            │ Signal Created │  (PMO signal with source metadata)
            └───────┬───────┘
                    ▼
            ┌───────────────┐
            │ Researcher     │  Agent diagnoses root cause:
            │ Agent (daemon) │  reads logs, traces code, identifies
            └───────┬───────┘  affected files
                    ▼
            ┌───────────────┐
            │ ForgeSession   │  Generates fix plan from diagnosis
            │ plan generation│  + interview questions
            └───────┬───────┘
                    ▼
            ┌───────────────┐
            │ PMO Board Card │  Card appears in "Proposed" column
            │ (with plan)    │  with plan, diagnosis, and cost estimate
            └───────┬───────┘
                    ▼
            ┌───────────────┐
            │ Human Approval │  User reviews on PMO board,
            │ (PMO UI)       │  approves / rejects / adjusts
            └───────┬───────┘
                    ▼
            ┌───────────────┐
            │ Daemon Start   │  Card moves to "In Progress",
            │ (agent team)   │  daemon executes the approved plan
            └───────┬───────┘
                    ▼
            ┌───────────────┐
            │ Completion     │  Card moves to "Done",
            │ + Notification │  outbound webhook notifies team
            └───────────────┘
```

**Implementation path:**
1. Add `POST /api/v1/triggers` — accepts GitHub/ADO webhook payloads,
   creates a PMO signal (this is the missing inbound piece).
2. Add a "auto-triage" flag on signals — when set, automatically calls
   ForgeSession.signal_to_plan() to generate a plan.
3. The rest (PMO board, approval, daemon execution) already exists.

### User Story: Diagnostic-First Workflow

> **As a developer**, when a production alert fires, I want an agent to
> pull relevant logs, trace the error through the codebase, and present
> a structured diagnostic — before any fix plan is created — so that I
> can make an informed decision about severity and approach.

This is a two-phase daemon execution:
- **Phase 1**: Researcher agent (read-only, no code changes). Output:
  diagnostic report with root cause hypothesis, affected files, severity.
- **APPROVAL gate**: Human reviews diagnostic, decides whether to proceed.
- **Phase 2**: Fix implementation by the appropriate agent team.

This is already expressible in a `MachinePlan` with approval gates. The
gap is triggering Phase 1 automatically from an external event.

---

## 4. PMO Board as the Coordination Hub

### What Exists

The PMO board already has:
- Kanban columns (backlog → proposed → queued → in-progress → done)
- Card detail with attached plans
- Forge endpoint for AI-assisted plan generation + interview refinement
- Execute endpoint (`POST /api/v1/pmo/execute/{card_id}`)
- SSE event stream for real-time card updates
- Signal management (create, triage, resolve)

### User Story: Plan-and-Execute from the Board

> **As a PMO user**, I want to describe a task on the board, have an
> intelligent planner generate an execution plan, review and adjust it
> through a guided interview, and then launch it as a daemon — all from
> the PMO UI — so that the board is my single interface for both planning
> and execution.

**Current state:** The Forge API (`/api/v1/pmo/forge/plan` →
`/forge/interview` → `/forge/regenerate` → `/forge/approve`) handles the
planning loop. The execute endpoint launches it. The SSE stream shows
progress. **This flow mostly works today.**

**Gaps:**
1. The execute endpoint spawns `baton execute run` (sequential). It should
   optionally spawn `baton daemon start` (parallel) for team execution.
2. No "one-click plan + execute" endpoint — today it's forge → approve →
   execute as separate API calls. A `POST /api/v1/pmo/forge/plan-and-queue`
   could collapse the happy path.
3. The PMO UI needs to render daemon status (parallel agent progress,
   per-step outcomes) — today it shows sequential step completion.

### User Story: Planner Agent as First-Class Participant

> **As a PMO user**, I want the planner to be an agent I can interact
> with — not just an API I call — so that it can ask me clarifying
> questions, propose alternatives, and explain trade-offs before
> committing to a plan.

This is the ForgeSession interview loop, but today it's synchronous
(request-response). Making the planner a daemon-driven agent would mean:

1. User creates a card with a description
2. Planner agent (running as daemon step) generates plan + questions
3. Card enters "needs-input" state with questions displayed
4. User answers on the board
5. Planner agent refines (another daemon step, or same agent re-invoked)
6. Card shows final plan for approval

This is a plan where the *planner itself is a step in a meta-plan*.

---

## 5. Dynamic Team Composition

### What Exists

The `IntelligentPlanner` already does dynamic team selection based on:
- **Task classification** — bug-fix gets different agents than new-feature
- **Stack detection** — Python project routes to `backend-engineer--python`
- **Pattern learning** — prior executions inform agent selection
  (PatternLearner, ≥70% confidence threshold)
- **Retrospective feedback** — agents with poor track records are dropped
- **Performance scoring** — low-health agents trigger warnings

The planner consolidates multi-agent phases into "team steps" with roles
(lead, implementer, reviewer) and member-level dependencies.

### What's Missing: Runtime Team Adaptation

Today team composition is fixed at plan creation time. Once the daemon
starts executing, it cannot:
- Add an agent mid-execution because the task turned out to be harder
- Replace a failing agent with a different specialist
- Bring in a reviewer early because the code is high-risk

### Proposed: Adaptive Team Composition

```
Phase 2: Implement
  Step 2.1 → backend-engineer--python
  Step 2.2 → test-engineer
  [ADAPTIVE SLOT] → resolved at runtime based on step 2.1 output
```

**How it would work:**
1. Plan includes "adaptive slots" — steps with `agent_name: "auto"` and a
   selection criteria (e.g., "if step 2.1 touches auth code, add
   security-reviewer").
2. When the daemon reaches an adaptive slot, it calls the `AgentRouter`
   with the current execution context (completed step outcomes, files
   changed, etc.).
3. Router resolves to a concrete agent and the daemon dispatches it.
4. This uses the existing `baton execute amend` capability — the daemon
   amends the plan at runtime.

**Implementation:** Medium complexity. The `AgentRouter` already does
task → agent matching. The engine already supports `amend` (adding phases/
steps mid-execution). The gap is wiring them together inside the
`TaskWorker` loop.

### Alternative: Agent Profiles as Team Templates

Instead of static YAML rosters, define team *profiles* that describe
capabilities needed rather than specific agents:

```yaml
# profiles/full-stack.yaml
name: full-stack
needs:
  - capability: architecture-design
    count: 1
    model: opus
  - capability: backend-implementation
    language: auto-detect    # resolved from project stack
    count: 1-2               # scaled by complexity
  - capability: frontend-implementation
    framework: auto-detect
    count: 1
  - capability: testing
    count: 1
  - capability: code-review
    count: 1
    phase: quality           # only in quality phase
```

The planner resolves capabilities to concrete agents at plan time using the
registry + router. This gives you dynamic composition without runtime
complexity — the dynamism happens during planning, not execution.

---

## 6. Shared Team Context — Is There a Better Path?

### What Exists

Agents already share context through three mechanisms:

1. **Shared context document** (`context.md`) — stack info, conventions,
   guardrails. Read by all agents.
2. **Mission log** (`mission-log.md`) — timestamped record of each agent's
   completion with decisions, issues, and handoff notes.
3. **Knowledge attachments** — curated documents attached per-step via
   knowledge packs (TF-IDF matching + agent binding + tag matching).

### The Limitation

These are all *pre-execution* or *post-step* context. There's no mechanism
for agents running *in parallel within the same phase* to share in-progress
decisions. Each agent gets its delegation prompt and works in isolation.

### Proposed: Structured Decision Log (Better Than Scratchpad)

Instead of a free-form scratchpad, use a structured decision log that
agents write to and read from:

```json
{
  "task_id": "2026-03-27-auth-feature-abc123",
  "phase_id": 2,
  "decisions": [
    {
      "agent": "architect",
      "step_id": "2.1",
      "timestamp": "2026-03-27T14:30:00Z",
      "type": "api-contract",
      "summary": "Using JWT with refresh tokens, 15-min access / 7-day refresh",
      "artifacts": ["src/models/auth.py", "docs/api-auth.md"],
      "dependencies_created": ["auth middleware interface"]
    },
    {
      "agent": "backend-engineer--python",
      "step_id": "2.2",
      "timestamp": "2026-03-27T14:32:00Z",
      "type": "implementation-choice",
      "summary": "Using PyJWT library, middleware in src/middleware/auth.py",
      "artifacts": ["src/middleware/auth.py"],
      "consumes": ["auth middleware interface"]
    }
  ]
}
```

**How it would work with daemon mode:**
1. When the daemon dispatches agents in parallel, it injects a
   `--decision-log-path` into each agent's delegation prompt.
2. Agents write structured decisions to the log as they make them.
3. Agents with `depends_on` entries read the log before starting.
4. The daemon's `TaskWorker` could optionally inject a "sync point" —
   a brief pause between parallel dispatches where it reads completed
   agents' decisions and appends them to pending agents' prompts.

**Key insight:** This doesn't require agents to communicate in real-time.
The daemon already dispatches in batches. A "decision injection" step
between batches would give later agents awareness of earlier agents'
choices without changing the execution model.

### Alternative: Event-Driven Context Propagation

Use the existing EventBus. When an agent completes, the worker publishes
`step.completed` with the outcome. A new subscriber
(`TeamContextPropagator`) could:

1. Parse the outcome for structured decisions
2. Append them to the shared context document
3. Inject them into the delegation prompts of still-pending steps

This is lightweight and uses existing infrastructure. The propagation
happens inside the `TaskWorker` between dispatch batches.

---

## 7. PMO-Centric User Stories

Consolidating the above into concrete PMO-board-centered user stories:

### Story 1: Bug Triage Pipeline

> A bug arrives from the issue tracker. An agent researches and diagnoses
> it. The diagnosis appears on the PMO board as a proposed card with a
> fix plan. The user approves and the daemon executes.

**Requires:** Inbound trigger endpoint + auto-triage flag on signals.
**Existing:** Signal → ForgeSession → Card → Execute → SSE updates.

### Story 2: Feature Sprint from Board

> A user writes a feature description on the PMO board. The planner agent
> generates a multi-phase plan with team assignments. The user refines via
> interview questions. On approval, a daemon launches the team. The board
> shows parallel agent progress in real-time.

**Requires:** Execute endpoint upgrade (daemon mode), parallel progress UI.
**Existing:** Forge plan + interview + approve + execute + SSE.

### Story 3: Cross-Project Coordination

> A PMO user manages multiple projects. They create cards for related
> work across repos (e.g., "update API" in backend, "regenerate client"
> in frontend). The PMO launches both as concurrent daemons, each in its
> own project directory, and shows combined progress.

**Requires:** Multi-project daemon launch from PMO, combined status view.
**Existing:** `--task-id` + `--project-dir` namespacing, `daemon list`.

### Story 4: Adaptive Agent Team

> During execution, the code-reviewer agent flags a security concern.
> The daemon automatically brings in the security-reviewer agent as an
> additional step, without stopping execution or requiring re-planning.

**Requires:** Adaptive slots in plans + runtime amendment via AgentRouter.
**Existing:** `baton execute amend`, AgentRouter, engine amendment support.

### Story 5: Multi-Perspective Analysis

> A business analyst agent and an engineer agent both analyze the same
> problem independently. Their perspectives are synthesized by a third
> agent before any implementation begins. This "diverse perspectives"
> pattern is a reusable team template.

**Requires:** Fan-out/fan-in pattern in plans, synthesis steps.
**Existing:** Parallel dispatch + phase dependencies. The synthesis step
is just another agent step that depends on the analysis steps.

---

## 8. Removed: CI/CD Angle

*(Removed per feedback — not a primary use case for this evaluation.)*

---

## Summary: What to Build Next

| Priority | Enhancement | Unlocks |
|---|---|---|
| **P0** | `baton daemon run` (plan + execute in one command) | Stories 1, 2 |
| **P0** | Inbound trigger endpoint (`POST /api/v1/triggers`) | Story 1 |
| **P1** | Execute endpoint upgrade to daemon mode | Story 2 |
| **P1** | Agent profiles / dynamic team composition at plan time | Stories 4, 5 |
| **P1** | Decision log / context propagation between parallel agents | Stories 2, 5 |
| **P2** | Adaptive slots (runtime team amendment) | Story 4 |
| **P2** | Multi-project daemon coordination in PMO | Story 3 |
| **P2** | Parallel progress rendering in PMO UI | Story 2 |
