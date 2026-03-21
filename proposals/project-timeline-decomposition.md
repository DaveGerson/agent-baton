# Project Timeline Decomposition — Proposals 001, 002, 003

**Date**: 2026-03-21
**Program**: Agent Baton Platform Evolution
**Total Duration**: ~19 weeks (5 release cycles)
**Proposals**: 001 (Async Runtime), 002 (Service API), 003 (Learning Loop)

---

## Section 1: Orchestration-Ready Decomposition

Each release cycle below is structured as a `MachinePlan` the orchestrator
can execute directly. Phases map to `PlanPhase`, steps map to `PlanStep`,
gates map to `PlanGate`.

---

### Release Cycle 1 — "Foundation" (Weeks 1-4)

**Parallel workstreams**: 001-Phase1 + 001-Phase2 + 003-Phase1

```yaml
task_id: "2026-Q2-rc1-foundation"
task_summary: "Event bus, async worker, triggers & recommender"
risk_level: "HIGH"
budget_tier: "full"
execution_mode: "phased"
git_strategy: "branch-per-agent"
```

#### Phase 1: Event Bus + Triggers (Week 1-2)

| step_id | agent                    | task_description                                                              | depends_on | deliverables                                          |
|---------|--------------------------|-------------------------------------------------------------------------------|------------|-------------------------------------------------------|
| 1.1     | architect                | Design Event dataclass, EventBus pub/sub interface, topic routing scheme      | —          | `core/events/` module spec                            |
| 1.2     | backend-engineer--python | Implement `core/events/bus.py`, `events.py`, `persistence.py`                | 1.1        | EventBus with file-backed .jsonl persistence          |
| 1.3     | backend-engineer--python | Implement `core/events/projections.py` — materialized views from event stream | 1.2        | Projections for execution state                       |
| 1.4     | backend-engineer--python | Implement `core/improve/triggers.py` — TriggerEvaluator, Anomaly detection   | —          | Anomaly detection for 5 signal types                  |
| 1.5     | backend-engineer--python | Implement `core/learn/recommender.py` — unified Recommender                  | —          | Recommender consuming scorer + pattern_learner + tuner |
| 1.6     | test-engineer            | Tests for EventBus (80+), triggers (40+), recommender (30+)                  | 1.2-1.5    | 150+ new tests                                        |

**Gate**: `pytest tests/ -x --tb=short` — all tests pass

#### Phase 2: Async Worker + Improvement Proposals (Week 3-4)

| step_id | agent                    | task_description                                                                 | depends_on | deliverables                                       |
|---------|--------------------------|----------------------------------------------------------------------------------|------------|----------------------------------------------------|
| 2.1     | architect                | Design TaskWorker async loop, AgentLauncher protocol, StepScheduler interface    | 1.2        | `core/runtime/` module spec                        |
| 2.2     | backend-engineer--python | Implement `core/runtime/worker.py` + `scheduler.py`                              | 2.1        | Async execution with parallel dispatch             |
| 2.3     | backend-engineer--python | Add `next_actions()` (plural) to ExecutionEngine                                 | 2.2        | Non-breaking engine extension                      |
| 2.4     | backend-engineer--python | Implement `core/improve/proposals.py` — Recommendation lifecycle                 | 1.5        | Recommendation create/apply/reject/rollback        |
| 2.5     | backend-engineer--python | Wire EventBus into ExecutionEngine (publish on state changes)                    | 1.2, 2.3   | Engine emits step.*, gate.* events                 |
| 2.6     | backend-engineer--python | Wire TraceRecorder + UsageLogger as EventBus subscribers                         | 2.5        | Observability auto-fed from events                 |
| 2.7     | backend-engineer--python | CLI: `baton improve`, `baton anomalies`                                          | 1.4, 1.5   | 2 new CLI commands                                 |
| 2.8     | test-engineer            | Tests for worker (60+), proposals (30+), integration wiring (20+)                | 2.2-2.7    | 110+ new tests                                     |

**Gate**: `pytest tests/ -x --tb=short` + manual architect review of EventBus ↔ Engine integration

---

### Release Cycle 2 — "Autonomy" (Weeks 5-8)

**Parallel workstreams**: 001-Phase3 + 001-Phase4 + 003-Phase2

```yaml
task_id: "2026-Q2-rc2-autonomy"
task_summary: "Human decisions, daemon mode, experiment tracking"
risk_level: "HIGH"
budget_tier: "full"
execution_mode: "phased"
git_strategy: "branch-per-agent"
```

#### Phase 1: Human Decisions + Experiments (Week 5-6)

| step_id | agent                    | task_description                                                              | depends_on | deliverables                                        |
|---------|--------------------------|-------------------------------------------------------------------------------|------------|-----------------------------------------------------|
| 1.1     | architect                | Design DecisionManager, notifier chain, decision resolution protocol          | RC1        | `core/runtime/decisions.py` spec                    |
| 1.2     | backend-engineer--python | Implement DecisionManager + file notifier + stdout notifier                   | 1.1        | Decision request/resolve lifecycle                  |
| 1.3     | backend-engineer--python | Wire ExecutionEngine WAIT actions to DecisionManager                          | 1.2        | Engine pauses at gates, resumes on resolution       |
| 1.4     | backend-engineer--python | CLI: `baton decide --list`, `baton decide --resolve`                          | 1.2        | 2 new CLI commands                                  |
| 1.5     | backend-engineer--python | Implement `core/improve/experiments.py` — ExperimentManager                   | RC1-1.5    | A/B experiment create, sample, evaluate, conclude   |
| 1.6     | backend-engineer--python | Implement `core/improve/rollback.py` — RollbackManager                       | 1.5        | Auto-rollback on degradation                        |
| 1.7     | backend-engineer--python | Wire ExperimentManager into UsageLogger for sample collection                 | 1.5        | Experiments auto-fed from usage data                |
| 1.8     | backend-engineer--python | CLI: `baton experiment list/show/conclude/rollback`                           | 1.5, 1.6   | 4 new CLI subcommands                               |
| 1.9     | test-engineer            | Tests for decisions (40+), experiments (50+), rollback (20+)                  | 1.2-1.8    | 110+ new tests                                      |

**Gate**: `pytest tests/ -x` + auditor review of auto-apply guardrails

#### Phase 2: Daemon Mode + Improvement Loop (Week 7-8)

| step_id | agent                    | task_description                                                              | depends_on | deliverables                                        |
|---------|--------------------------|-------------------------------------------------------------------------------|------------|-----------------------------------------------------|
| 2.1     | backend-engineer--python | Implement WorkerSupervisor — daemonize, health check, restart                 | RC1-2.2    | `core/runtime/supervisor.py`                        |
| 2.2     | backend-engineer--python | Implement signal handling — SIGTERM/SIGINT graceful drain                     | 2.1        | `core/runtime/signals.py`                           |
| 2.3     | backend-engineer--python | CLI: `baton daemon start/status/stop`                                         | 2.1        | 3 new CLI subcommands                               |
| 2.4     | backend-engineer--python | Implement `core/improve/loop.py` — ImprovementLoop orchestrator              | 1.5, 1.6   | Closed-loop: analyze → propose → apply → measure    |
| 2.5     | backend-engineer--python | Wire post-execution hook: ImprovementLoop.run_cycle() after engine.complete() | 2.4        | Auto-improvement after every task                   |
| 2.6     | backend-engineer--python | Implement auto-apply guardrails (8 non-negotiable rules from 003)             | 2.4        | Safety constraints on autonomous improvement        |
| 2.7     | test-engineer            | Tests for daemon (30+), improvement loop (40+), integration E2E (20+)         | 2.1-2.6    | 90+ new tests                                       |
| 2.8     | code-reviewer            | Full review of RC1 + RC2 — async runtime + learning loop                      | 2.7        | Review report with issues list                      |

**Gate**: `pytest tests/ -x` + integration test: daemon executes 3-phase plan to completion + improvement loop produces report

---

### Release Cycle 3 — "Surface" (Weeks 9-12)

**Parallel workstreams**: 002-Phase1 + 002-Phase2

```yaml
task_id: "2026-Q2-rc3-surface"
task_summary: "REST API, SSE streaming, decision endpoints"
risk_level: "MEDIUM"
budget_tier: "standard"
execution_mode: "phased"
git_strategy: "branch-per-agent"
```

#### Phase 1: Core API (Week 9-10)

| step_id | agent                    | task_description                                                              | depends_on | deliverables                                        |
|---------|--------------------------|-----------------------------------------------------|------------|-----------------------------------------------------|
| 1.1     | architect                | Design API module structure, route contracts, Pydantic models                 | —          | `api/` package spec + OpenAPI draft                 |
| 1.2     | backend-engineer--python | Implement `api/server.py` — FastAPI app factory, CORS, local-only default     | 1.1        | Server bootstrap                                    |
| 1.3     | backend-engineer--python | Implement `api/routes/plans.py` + `executions.py`                             | 1.2        | POST/GET plans, POST/GET executions                 |
| 1.4     | backend-engineer--python | Implement `api/routes/agents.py` + `health.py` + `observe.py`                | 1.2        | Agent registry, health checks, dashboard/traces     |
| 1.5     | backend-engineer--python | Implement `api/models/requests.py` + `responses.py`                           | 1.3        | Pydantic request/response validation                |
| 1.6     | backend-engineer--python | Implement `api/deps.py` — dependency injection for engine, bus, registry      | 1.2        | Shared dependencies across routes                   |
| 1.7     | backend-engineer--python | CLI: `baton serve --port --host`                                              | 1.2        | 1 new CLI command                                   |
| 1.8     | test-engineer            | Tests for all endpoints (60+) with FastAPI TestClient                         | 1.3-1.7    | 60+ endpoint tests                                  |

**Gate**: `pytest tests/ -x` + OpenAPI spec generates cleanly + `curl` smoke test of all endpoints

#### Phase 2: Decisions + Events + Streaming (Week 11-12)

| step_id | agent                    | task_description                                                              | depends_on | deliverables                                        |
|---------|--------------------------|-------------------------------------------------------------------------------|------------|-----------------------------------------------------|
| 2.1     | backend-engineer--python | Implement `api/routes/decisions.py` — GET pending, POST resolve               | 1.2, RC2   | Decision endpoints wired to DecisionManager         |
| 2.2     | backend-engineer--python | Implement `api/routes/events.py` — SSE stream per task_id                     | 1.2, RC1   | Real-time event streaming                           |
| 2.3     | backend-engineer--python | Implement `api/middleware/auth.py` — optional token auth                      | 1.2        | Bearer token auth for remote access                 |
| 2.4     | backend-engineer--python | Add `[api]` optional dependency group to pyproject.toml                       | —          | `pip install agent-baton[api]`                      |
| 2.5     | test-engineer            | Tests for SSE streaming (20+), decision resolution via API (20+)              | 2.1-2.3    | 40+ new tests                                       |

**Gate**: `pytest tests/ -x` + integration test: SSE client receives live events during daemon execution

---

### Release Cycle 4 — "Integration" (Weeks 13-15)

**Parallel workstreams**: 002-Phase3 + 002-Phase4 + 003-Phase4

```yaml
task_id: "2026-Q2-rc4-integration"
task_summary: "Webhooks, daemon+API combo, improvement dashboard"
risk_level: "MEDIUM"
budget_tier: "standard"
execution_mode: "phased"
git_strategy: "branch-per-agent"
```

#### Phase 1: Webhooks + Dashboard Enhancement (Week 13-14)

| step_id | agent                    | task_description                                                              | depends_on | deliverables                                        |
|---------|--------------------------|-------------------------------------------------------------------------------|------------|-----------------------------------------------------|
| 1.1     | backend-engineer--python | Implement `api/webhooks/` — dispatcher, registry, HMAC signing                | RC3        | Webhook CRUD + delivery + retry                     |
| 1.2     | backend-engineer--python | Implement Slack payload formatter as reference integration                    | 1.1        | Slack-ready webhook payloads                        |
| 1.3     | backend-engineer--python | Enhance DashboardGenerator with improvement metrics + trend lines             | RC2        | Improvement history in dashboard                    |
| 1.4     | backend-engineer--python | Add improvement history to retrospectives                                     | RC2        | Retrospective reports include learning data         |
| 1.5     | backend-engineer--python | Enhance `baton scores --trends` with trend visualization                      | RC2        | Per-agent trend display                             |
| 1.6     | test-engineer            | Tests for webhooks (30+), dashboard enhancement (15+)                         | 1.1-1.5    | 45+ new tests                                       |

**Gate**: `pytest tests/ -x` + webhook delivery to test endpoint with valid HMAC

#### Phase 2: Combined Daemon+API + E2E Validation (Week 15)

| step_id | agent                    | task_description                                                              | depends_on | deliverables                                        |
|---------|--------------------------|-------------------------------------------------------------------------------|------------|-----------------------------------------------------|
| 2.1     | backend-engineer--python | Wire `baton daemon start --serve --port` — worker + API in same process       | RC2, RC3   | Single-process daemon + API                         |
| 2.2     | test-engineer            | E2E integration: plan → daemon → SSE monitoring → decision via API → complete | 2.1        | Full lifecycle E2E test                             |
| 2.3     | test-engineer            | E2E integration: 10-task run → improvement loop → auto-apply → rollback       | RC2        | Learning loop E2E test                              |
| 2.4     | code-reviewer            | Full review of RC3 + RC4 — API surface + webhooks + dashboard                 | 2.1-2.3    | Review report                                       |
| 2.5     | auditor                  | Security review: auth middleware, webhook signing, file access boundaries      | 2.4        | Security audit report                               |

**Gate**: `pytest tests/ -x` + auditor sign-off + all 5 success criteria from each proposal validated

---

### Release Cycle 5 — "Polish" (Weeks 16-19)

```yaml
task_id: "2026-Q3-rc5-polish"
task_summary: "Documentation, CLI gaps, performance, stabilization"
risk_level: "LOW"
budget_tier: "lean"
execution_mode: "sequential"
git_strategy: "commit-per-agent"
```

#### Phase 1: Documentation + CLI Tests (Week 16-17)

| step_id | agent                    | task_description                                                              | depends_on | deliverables                                        |
|---------|--------------------------|-------------------------------------------------------------------------------|------------|-----------------------------------------------------|
| 1.1     | backend-engineer--python | Add CLI tests for all 32 existing commands (coverage gap from test audit)     | —          | 100+ CLI tests                                      |
| 1.2     | backend-engineer--python | Add CLI tests for all new commands (daemon, decide, improve, experiment, serve)| —          | 40+ CLI tests                                       |
| 1.3     | backend-engineer--python | OpenAPI spec finalization + curl example documentation                        | —          | API reference doc                                   |
| 1.4     | backend-engineer--python | Update `references/` with new module docs (events, runtime, improve, api)     | —          | 4 new reference docs                                |

**Gate**: `pytest tests/ -x` + `baton --help` renders all commands correctly

#### Phase 2: Stabilization (Week 18-19)

| step_id | agent                    | task_description                                                              | depends_on | deliverables                                        |
|---------|--------------------------|-------------------------------------------------------------------------------|------------|-----------------------------------------------------|
| 2.1     | test-engineer            | Stress test: 20-step plan with 5 parallel agents in daemon mode               | —          | Performance baseline                                |
| 2.2     | backend-engineer--python | Fix issues surfaced by stress test and code review                            | 2.1        | Bug fixes                                           |
| 2.3     | code-reviewer            | Final full-codebase review                                                    | 2.2        | Final review report                                 |
| 2.4     | auditor                  | Final security + compliance audit                                             | 2.3        | Audit sign-off                                      |

**Gate**: All tests pass + code review clean + auditor sign-off

---
---

## Section 2: Project Owner Release Cycle Summary

What you're approving, what ships, and what to watch for in each cycle.

---

### RC1 — "Foundation" (Weeks 1-4)

**What's happening**: Building the nervous system (event bus) and the brain
(anomaly detection + recommendations). These are invisible infrastructure
— no new UI, no new human-facing workflows yet. This is the concrete slab
before the house.

**What ships**:
- An event bus that records everything the engine does to `.jsonl` files
- An async worker that can run multiple agents in parallel (up to 3)
- Anomaly detection that flags: agent failure spikes, budget overruns,
  gate failure rates, retry spikes, pattern drift
- A unified recommender that consolidates scoring + patterns + budget
  tuning into ranked recommendations
- `baton improve` — run an improvement cycle manually
- `baton anomalies` — see what's going wrong
- ~260 new tests

**What to watch for**:
- Event bus design review (Phase 1 gate) — this is the most architectural
  decision in the entire program. Get it right here.
- The async worker introduces `asyncio` — first async code in the codebase.
  If the team has concerns about async complexity, raise them in Phase 2.

**Your checkpoint questions**:
1. "Can I run `baton anomalies` and see useful output?" → Yes after RC1
2. "Can agents run in parallel?" → Yes after RC1, but only programmatically
3. "Can I leave it running overnight?" → Not yet (no daemon mode until RC2)

---

### RC2 — "Autonomy" (Weeks 5-8)

**What's happening**: The system becomes autonomous. Agents can run without
you watching. When they need you, they write a decision request and wait.
The learning loop closes — the system improves itself after every task.

**What ships**:
- Human decision protocol: engine pauses at gates, writes decision files,
  resumes when you respond via `baton decide`
- Daemon mode: `baton daemon start` — runs in background, survives terminal close
- Experiment tracking: A/B tests for agent improvements with auto-rollback
- Closed-loop improvement: after every task, the system analyzes performance,
  auto-applies safe budget downgrades, escalates risky changes to you
- 8 safety guardrails hardcoded (prompt changes never auto-apply, rollback
  is always automatic, max 2 experiments per agent, etc.)
- ~200 new tests

**What to watch for**:
- Auditor gate after Phase 1 — the auto-apply guardrails are the most
  important safety decision. Review the 8 rules carefully.
- Daemon mode introduces process management (PID files, signal handling).
  This is the most operationally complex piece.

**Your checkpoint questions**:
1. "Can I start a plan and walk away?" → Yes, `baton daemon start --plan plan.json`
2. "How do I know when it needs me?" → `baton decide --list` or check
   `.claude/team-context/decisions/`
3. "What if an improvement makes things worse?" → Auto-rollback within
   2 task executions
4. "What improves automatically vs. what needs my approval?" → Budget
   downgrades (high confidence) auto-apply. Prompt changes, roster changes,
   gate changes always escalate.

---

### RC3 — "Surface" (Weeks 9-12)

**What's happening**: Everything that exists via CLI gets a REST API.
External tools can now talk to Agent Baton — dashboards, Slack bots,
CI/CD pipelines, mobile apps. Real-time streaming via Server-Sent Events.

**What ships**:
- FastAPI server: `baton serve --port 8741`
- REST endpoints for plans, executions, agents, decisions, dashboard, traces
- SSE streaming: connect to `/events/:task_id` and watch execution live
- Decision resolution via API (not just CLI) — a web UI or Slack bot can
  approve/reject gates
- Token auth for remote access (local-only by default)
- OpenAPI spec auto-generated (clients can codegen)
- `[api]` optional install: `pip install agent-baton[api]`
- ~100 new tests

**What to watch for**:
- FastAPI is a new dependency — it's optional (doesn't affect CLI users).
  But it's the first external web framework in the project.
- SSE streaming ties directly to the event bus from RC1. If event bus
  design was wrong, this is where you'll feel it.

**Your checkpoint questions**:
1. "Can a web dashboard show live execution?" → Yes, via SSE
2. "Can someone approve a gate from their phone?" → Yes, POST to
   `/decisions/:id/resolve`
3. "Does the CLI still work without the API?" → Yes, `[api]` is optional
4. "Is this secure?" → Local-only by default. Token auth for remote.
   No new file access beyond what CLI already has.

---

### RC4 — "Integration" (Weeks 13-15)

**What's happening**: Connecting Agent Baton to the outside world.
Webhooks push notifications to Slack/PagerDuty/custom endpoints.
The daemon and API server merge into one process. The improvement
dashboard gets visual trend lines.

**What ships**:
- Webhook system: register URLs, filter by event type, HMAC-signed payloads
- Slack payload formatter (reference implementation)
- `baton daemon start --serve` — daemon + API in one process
- Dashboard enhanced with improvement metrics and per-agent trend lines
- Retrospective reports include learning loop data
- Full E2E tests: plan → daemon → SSE → decision → complete
- Security audit of the entire API surface
- ~45 new tests

**What to watch for**:
- Webhook delivery introduces outbound HTTP. First time Agent Baton
  makes network calls to arbitrary URLs. Auditor reviews this.
- Combined daemon+API is the "production mode" — if this doesn't work
  smoothly, the whole platform story breaks.

**Your checkpoint questions**:
1. "Can I get a Slack message when agents need my approval?" → Yes
2. "Is there one command to run everything?" → Yes,
   `baton daemon start --plan plan.json --serve --port 8741`
3. "Can I see if agents are getting better over time?" → Yes,
   `baton scores --trends` and the enhanced dashboard

---

### RC5 — "Polish" (Weeks 16-19)

**What's happening**: Closing gaps, hardening, documenting. The existing
32 CLI commands had zero test coverage — that gets fixed. New modules
get reference docs. Stress testing under real-world load.

**What ships**:
- 140+ CLI tests (32 existing commands + new commands)
- API reference documentation with curl examples
- 4 new reference docs for events, runtime, improve, api modules
- Stress test results (20-step plan, 5 parallel agents)
- Bug fixes from stress testing
- Final code review + security audit sign-off

**What to watch for**:
- This is the "boring but essential" cycle. Resist the urge to add features.
- Stress test may reveal concurrency bugs in the async worker — budget
  time for fixes.

**Your checkpoint questions**:
1. "Is this production-ready?" → After RC5 audit sign-off, yes
2. "What's the test coverage?" → ~2,300+ tests (up from 1,730)
3. "Can I hand this to another team?" → Yes, with reference docs + OpenAPI spec

---
---

## Section 3: User Journey — From → To

How each release cycle changes the lived experience for you (project owner)
and for developers using Agent Baton.

---

### RC1 — Foundation

#### For You (Project Owner)

| From | To |
|------|-----|
| You run `baton scores` manually and read walls of text to find problems | You run `baton anomalies` and get a prioritized list: "architect has 35% failure rate (critical), budget 50% over on refactor tasks (warning)" |
| You run 3 separate commands (`baton scores`, `baton patterns`, `baton budget`) to understand system health | You run `baton improve` and get a unified report with ranked recommendations across all dimensions |
| Agents execute one at a time — a 6-agent plan takes 6x as long | Parallel steps within a phase run concurrently (up to 3x faster for independent work) |
| You have no idea what's happening inside the engine | Every state change is recorded as an event in `.jsonl` — full audit trail |

#### For Developers

| From | To |
|------|-----|
| `ExecutionEngine.next_action()` returns one action; caller loops synchronously | `next_actions()` returns all parallel-ready actions; async worker dispatches concurrently |
| State changes are implicit (read files to discover what happened) | State changes are explicit events on a pub/sub bus — subscribe to `step.completed`, `gate.required`, etc. |
| Adding observability means editing TraceRecorder/UsageLogger directly | Add a new EventBus subscriber — zero changes to existing code |
| Testing requires running the full engine loop | EventBus replay enables deterministic test scenarios from recorded event logs |

---

### RC2 — Autonomy

#### For You (Project Owner)

| From | To |
|------|-----|
| You must watch the terminal while agents work — close it and everything stops | `baton daemon start --plan plan.json` — agents run in background, survive terminal close, run overnight |
| When agents hit a gate, the process just... stops. You find out when you check | Agents pause and write a decision request. You check `baton decide --list` when convenient |
| You manually read performance reports and decide what to improve | The system auto-applies safe improvements (budget downgrades) and escalates risky ones to you with evidence |
| An improvement that hurts performance stays until you notice | Experiments auto-rollback within 2 tasks if performance degrades >5% |
| You have no visibility into whether the system is getting smarter | `baton experiment list` shows active A/B tests, `baton improve --history` shows what changed and why |

#### For Developers

| From | To |
|------|-----|
| `AsyncDispatcher` writes task JSON but nothing reads it | `TaskWorker` + `StepScheduler` drive execution from those tasks, with configurable concurrency |
| No way to model "wait for human" in the execution engine | `ActionType.WAIT` + `DecisionManager` — engine pauses, publishes event, resumes on resolution |
| `Escalation` model exists but has no delivery mechanism | `DecisionManager` + `Notifier` chain delivers to file/stdout (webhook in RC4) |
| Process crash = manual state recovery | `WorkerSupervisor` restarts from last persisted state. Event log replay rebuilds projections |
| Improvement modules (scorer, pattern_learner, budget_tuner) are read-only analysis | `ImprovementLoop` closes the loop: analyze → propose → auto-apply/escalate → measure → rollback |

---

### RC3 — Surface

#### For You (Project Owner)

| From | To |
|------|-----|
| You interact with agents only via terminal on your development machine | `baton serve` exposes a REST API — check status from your phone, a web dashboard, or any HTTP client |
| To check execution progress: SSH into machine → `baton daemon status` | Open `http://localhost:8741/api/v1/executions/:id` in any browser |
| To approve a gate: find the terminal → `baton decide --resolve ...` | POST to `/api/v1/decisions/:id/resolve` from any device or tool |
| No real-time view of what agents are doing right now | SSE stream at `/events/:task_id` — watch steps dispatch and complete live |
| Sharing execution state with a colleague requires screen sharing | Give them the API URL — they can query plans, executions, traces independently |

#### For Developers

| From | To |
|------|-----|
| Integrating with Agent Baton means importing Python modules or shelling out to `baton` | Standard REST API with OpenAPI spec — integrate from any language, any platform |
| Building a dashboard means parsing `.jsonl` files and team-context directories | `GET /dashboard`, `GET /traces/:id`, `GET /usage` — structured JSON responses |
| No way to embed Agent Baton in a CI/CD pipeline without CLI parsing | `POST /plans` → `POST /executions` → poll `GET /executions/:id` — clean API-driven automation |
| Agent registry is only accessible via Python imports | `GET /agents` + `GET /agents/:name` — browse available agents from any client |

---

### RC4 — Integration

#### For You (Project Owner)

| From | To |
|------|-----|
| You must actively check for pending decisions | Slack message arrives: "Task abc123 needs auditor approval — [Approve] [Reject]" |
| Running daemon + API requires two separate processes | `baton daemon start --plan plan.json --serve` — one command, one process |
| Improvement data is scattered across reports and CLI output | Dashboard shows trend lines: agent performance over time, active experiments, applied vs. rolled-back changes |
| Retrospective reports don't mention what the system learned | Retrospectives include: "Auto-applied 2 budget downgrades, escalated 1 prompt change, rolled back 0 experiments" |

#### For Developers

| From | To |
|------|-----|
| Notifying external systems requires custom code per destination | Register webhook URLs with event filters — `POST /webhooks` with `events: ["human.decision_needed"]` |
| No way to verify webhook payload authenticity | HMAC-SHA256 signed payloads with `X-Baton-Signature` header |
| Slack integration requires building from scratch | Reference Slack payload formatter included — buttons map to decision resolution API |
| External dashboard must poll for updates | Webhook push on every relevant event — near-real-time external integration |

---

### RC5 — Polish

#### For You (Project Owner)

| From | To |
|------|-----|
| 32 CLI commands have zero test coverage — silent regressions possible | 140+ CLI tests ensure every command works as documented |
| New team members must read source code to understand modules | Reference docs for events, runtime, improve, api — onboarding in hours not days |
| Unknown performance characteristics under load | Stress-tested: 20-step plan, 5 parallel agents, with measured performance baseline |
| Informal "it seems to work" confidence level | Auditor sign-off + 2,300+ tests + security review = production-ready confidence |

#### For Developers

| From | To |
|------|-----|
| CLI changes may break silently (no test coverage) | CLI regression suite catches breakage immediately |
| API endpoint contracts documented only in source code | OpenAPI spec + curl examples — API reference is auto-generated and always current |
| Performance limits are unknown | Documented concurrency limits, rate limit guidance, and stress test results |
| Module boundaries are implicit in code structure | Reference docs explicitly define interfaces, events, and extension points |

---
---

## Cumulative Timeline View

```
Week  1-2  ░░░░░░░░░░ RC1 Phase 1: Event Bus + Triggers + Recommender
Week  3-4  ░░░░░░░░░░ RC1 Phase 2: Async Worker + Proposals + Wiring
Week  5-6  ▓▓▓▓▓▓▓▓▓▓ RC2 Phase 1: Human Decisions + Experiments
Week  7-8  ▓▓▓▓▓▓▓▓▓▓ RC2 Phase 2: Daemon Mode + Improvement Loop
Week  9-10 ██████████ RC3 Phase 1: Core API (plans, executions, agents)
Week 11-12 ██████████ RC3 Phase 2: Decision + Event Endpoints + SSE
Week 13-14 ▒▒▒▒▒▒▒▒▒▒ RC4 Phase 1: Webhooks + Dashboard Enhancement
Week    15 ▒▒▒▒▒▒▒▒▒▒ RC4 Phase 2: Daemon+API Combo + E2E Validation
Week 16-17 ░░░░░░░░░░ RC5 Phase 1: CLI Tests + Documentation
Week 18-19 ░░░░░░░░░░ RC5 Phase 2: Stress Test + Stabilization + Audit
```

**Cumulative new tests**: ~260 + ~200 + ~100 + ~45 + ~140 = **~745 new tests**
**Cumulative new LOC**: ~2,500 + ~1,800 + ~2,000 + ~800 + ~600 = **~7,700 LOC**
**Total test count**: 1,730 (current) + 745 = **~2,475 tests**

