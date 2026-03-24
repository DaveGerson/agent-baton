# Proposal 002: Service API Layer & External Integration Surface

**Status**: Draft
**Author**: Architecture Review
**Date**: 2026-03-21
**Risk**: MEDIUM — new package alongside existing CLI; no core changes
**Estimated Scope**: ~2,000 LOC new, ~200 LOC modified across 12-15 files
**Depends On**: Proposal 001 (Event Bus, DecisionManager)

---

## Problem Statement

Agent Baton is **CLI-only**. The sole interface to the execution engine,
observability stack, and governance layer is `baton <command>`. This works
for developers in a terminal but fails for the async human interaction
model:

1. **No programmatic access** — external tools (dashboards, Slack bots,
   CI/CD pipelines, web UIs) cannot query execution state, submit
   decisions, or trigger plans without shelling out to `baton`.
2. **No real-time updates** — a human checking on autonomous agents must
   poll `baton daemon status` or read files. No push mechanism exists.
3. **No webhook delivery** — when agents need human decisions, there is
   no way to notify Slack, email, PagerDuty, or a custom dashboard.
4. **No multi-user coordination** — in consulting delivery, multiple
   humans (tech lead, domain expert, client reviewer) may need to
   interact with the same running orchestration. CLI is single-user.
5. **No integration with project management** — no way to link task
   execution to Jira tickets, Linear issues, or GitHub PRs
   programmatically.

The absence of an API layer means Agent Baton cannot serve as the
backend for any user-facing product. It remains a power-user CLI tool.

---

## Proposed Architecture

### Design Principles

1. **API wraps existing modules** — the API layer calls the same Python
   classes the CLI does. No business logic in the API layer.
2. **Stateless HTTP** — the API server is a thin wrapper. All state
   lives in the file-based persistence layer (unchanged).
3. **Event streaming via SSE** — Server-Sent Events for real-time
   updates. No WebSocket complexity needed for unidirectional push.
4. **Auth is pluggable** — default is local-only (127.0.0.1). Token
   auth available for remote/team scenarios. No auth complexity baked
   into v1.
5. **OpenAPI spec generated** — FastAPI auto-generates docs. Clients
   can codegen from the spec.

### Module Structure

```
agent_baton/api/
├── __init__.py
├── server.py          # FastAPI app factory + CORS/auth middleware
├── routes/
│   ├── __init__.py
│   ├── plans.py       # POST /plans, GET /plans/:id
│   ├── executions.py  # POST /executions, GET /executions/:id, POST /executions/:id/record
│   ├── decisions.py   # GET /decisions, POST /decisions/:id/resolve
│   ├── agents.py      # GET /agents, GET /agents/:name
│   ├── events.py      # GET /events/:task_id (SSE stream)
│   ├── observe.py     # GET /dashboard, GET /traces/:id, GET /usage
│   └── health.py      # GET /health, GET /ready
├── models/
│   ├── __init__.py
│   ├── requests.py    # Pydantic request models
│   └── responses.py   # Pydantic response models
├── middleware/
│   ├── __init__.py
│   ├── auth.py        # Token auth (optional)
│   └── cors.py        # CORS configuration
└── deps.py            # Dependency injection (engine, bus, registry)
```

### API Endpoints

#### Plans

```
POST   /api/v1/plans
  Body: { description, task_type?, agents?, project_path? }
  Response: { plan_id, plan: MachinePlan, explanation? }
  → Calls IntelligentPlanner.create_plan()

GET    /api/v1/plans/:plan_id
  Response: { plan_id, plan: MachinePlan, created_at }
  → Reads from .claude/team-context/plan.json
```

#### Executions

```
POST   /api/v1/executions
  Body: { plan_id }  OR  { plan: MachinePlan }
  Response: { task_id, status: "started", next_actions: [...] }
  → Calls ExecutionEngine.start()

GET    /api/v1/executions/:task_id
  Response: { task_id, status, current_phase, steps_completed,
              steps_remaining, gates_passed, pending_decisions }
  → Reads ExecutionState from disk

POST   /api/v1/executions/:task_id/record
  Body: { step_id, agent, status, output_summary?, tokens?, duration_ms? }
  Response: { recorded: true, next_actions: [...] }
  → Calls engine.record_step_result() + engine.next_actions()

POST   /api/v1/executions/:task_id/gate
  Body: { phase_id, result: "pass"|"fail"|"pass_with_notes", notes? }
  Response: { recorded: true, next_actions: [...] }
  → Calls engine.record_gate_result()

POST   /api/v1/executions/:task_id/complete
  Response: { task_id, status: "complete", trace_id, usage_summary }
  → Calls engine.complete(), writes trace + usage

DELETE /api/v1/executions/:task_id
  Response: { cancelled: true }
  → Graceful cancellation (if daemon mode, signals worker)
```

#### Decisions (Human Interaction Surface)

```
GET    /api/v1/decisions
  Query: ?status=pending&task_id=...
  Response: { decisions: [DecisionRequest, ...] }
  → Calls DecisionManager.pending()

GET    /api/v1/decisions/:request_id
  Response: DecisionRequest with full context
  → Includes inline file contents for context_files (so UI doesn't
    need filesystem access)

POST   /api/v1/decisions/:request_id/resolve
  Body: { option, rationale?, resolved_by? }
  Response: { resolved: true, execution_resumed: true }
  → Calls DecisionManager.resolve() → publishes event → unblocks worker
```

**This is the critical async human interaction endpoint.** A dashboard,
Slack bot, or mobile app calls `GET /decisions` to show pending items,
then `POST /decisions/:id/resolve` when the human acts.

#### Real-Time Events (SSE)

```
GET    /api/v1/events/:task_id
  Headers: Accept: text/event-stream
  Response: SSE stream of Event objects

  Events emitted:
    event: step.dispatched
    data: { step_id, agent, phase }

    event: step.completed
    data: { step_id, agent, status, duration_ms }

    event: gate.required
    data: { phase_id, gate_type, checks }

    event: gate.result
    data: { phase_id, result, notes }

    event: human.decision_needed
    data: { request_id, decision_type, summary, options }

    event: human.decision_resolved
    data: { request_id, chosen_option }

    event: execution.complete
    data: { task_id, outcome, summary }
```

**SSE implementation**: EventBus subscriber that forwards events to
connected HTTP clients. Each SSE connection subscribes to all topics
for a specific `task_id`.

#### Agents & Observability

```
GET    /api/v1/agents
  Query: ?category=...&stack=...
  Response: { agents: [AgentDefinition, ...] }
  → Calls AgentRegistry.load_default_paths()

GET    /api/v1/agents/:name
  Response: AgentDefinition (parsed frontmatter + body)

GET    /api/v1/dashboard
  Response: { dashboard_markdown, metrics: {...} }
  → Calls DashboardGenerator.generate()

GET    /api/v1/traces/:task_id
  Response: TaskTrace (full event DAG)

GET    /api/v1/usage
  Query: ?since=...&agent=...
  Response: { records: [TaskUsageRecord, ...], summary: {...} }
```

#### Health

```
GET    /api/v1/health
  Response: { status: "ok", version, uptime_seconds }

GET    /api/v1/ready
  Response: { ready: true, daemon_running: bool, pending_decisions: int }
```

---

## Webhook Outbound System

For teams that want push notifications rather than polling:

```
agent_baton/api/webhooks/
├── __init__.py
├── dispatcher.py     # WebhookDispatcher: queue + retry + HMAC signing
├── registry.py       # WebhookRegistry: CRUD for webhook subscriptions
└── payloads.py       # Payload formatting per destination type
```

### Webhook Registration

```
POST   /api/v1/webhooks
  Body: {
    url: "https://hooks.slack.com/...",
    events: ["human.decision_needed", "execution.complete"],
    secret: "hmac-shared-secret"      # for payload signing
  }
  Response: { webhook_id, created }

DELETE /api/v1/webhooks/:webhook_id
```

### Webhook Delivery

```python
class WebhookDispatcher:
    """Subscribes to EventBus, delivers matching events to registered webhooks."""

    async def deliver(self, event: Event) -> None:
        for hook in self.registry.match(event.topic):
            payload = self.format(event, hook)
            signature = hmac.new(hook.secret, payload, sha256).hexdigest()
            try:
                await self.client.post(hook.url,
                    json=payload,
                    headers={"X-Baton-Signature": signature},
                    timeout=10
                )
            except Exception:
                await self.retry_queue.enqueue(hook, payload)
```

**Retry policy**: 3 attempts with exponential backoff (5s, 30s, 300s).
Failed deliveries logged to `.claude/team-context/webhook-failures.jsonl`.

### Slack Integration Example

```python
# Slack-formatted payload for human.decision_needed
{
    "blocks": [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Decision Required*\n\nTask `abc123` needs auditor approval for Phase 2 gate.\n\n*Options:* approve, reject, modify"
            }
        },
        {
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Approve"}, "action_id": "resolve_approve"},
                {"type": "button", "text": {"type": "plain_text", "text": "Reject"}, "action_id": "resolve_reject", "style": "danger"}
            ]
        }
    ]
}
```

Slack button callbacks POST to `/api/v1/decisions/:id/resolve` via a
small Slack app adapter (out of scope for v1, but the API surface
supports it directly).

---

## Dependency Management

### New Dependencies

```toml
# pyproject.toml — new optional dependency group
[project.optional-dependencies]
api = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "httpx>=0.27",          # webhook delivery + async HTTP client
    "sse-starlette>=2.0",  # SSE support for FastAPI
]
```

**API is optional.** `pip install agent-baton` works without it.
`pip install agent-baton[api]` adds the API layer. CLI continues to
work regardless.

### CLI Integration

```bash
# New CLI command to start the API server
baton serve --port 8741 --host 127.0.0.1
baton serve --port 8741 --host 0.0.0.0 --token "secret"  # remote access

# Combined daemon + API
baton daemon start --plan plan.json --serve --port 8741
# → starts execution worker + HTTP API in same process
```

---

## Architecture Diagram

```
                    ┌─────────────────────────────────────┐
                    │           External Clients           │
                    │  Dashboard │ Slack Bot │ CI Pipeline  │
                    └──────┬──────────┬──────────┬────────┘
                           │          │          │
                    ┌──────▼──────────▼──────────▼────────┐
                    │         FastAPI Server               │
                    │  /plans  /executions  /decisions     │
                    │  /events(SSE)  /agents  /webhooks    │
                    └──────┬──────────┬──────────┬────────┘
                           │          │          │
              ┌────────────▼──┐  ┌────▼─────┐  ┌▼──────────────┐
              │ IntelligentPlan│  │Execution │  │ Decision      │
              │ ner            │  │ Engine   │  │ Manager       │
              └────────────────┘  └────┬─────┘  └───────┬───────┘
                                       │                │
                                  ┌────▼────────────────▼───┐
                                  │        EventBus         │
                                  │   (Proposal 001)        │
                                  └────┬────────────────────┘
                                       │
                    ┌──────────────────┬┴───────────────────┐
                    │                  │                    │
              ┌─────▼──────┐  ┌───────▼────────┐  ┌───────▼──────┐
              │ TraceRecord │  │ UsageLogger    │  │ Webhook      │
              │ er          │  │                │  │ Dispatcher   │
              └─────────────┘  └────────────────┘  └──────────────┘
```

---

## Security Considerations

### Local-Only by Default

```python
# server.py defaults
app = create_app(
    host="127.0.0.1",    # loopback only
    allowed_origins=["http://localhost:*"],
    auth_required=False,  # no auth needed on loopback
)
```

### Token Auth for Remote Access

```python
# When --token is provided or BATON_API_TOKEN env var is set
@app.middleware("http")
async def auth_middleware(request, call_next):
    if request.url.path.startswith("/api/v1/health"):
        return await call_next(request)
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if token != settings.api_token:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return await call_next(request)
```

### Webhook Signing

All outbound webhooks include `X-Baton-Signature` header (HMAC-SHA256).
Receivers verify payload integrity.

### File Access Boundary

The API server runs in the same process as the execution engine.
It has the same file access as the CLI. No additional attack surface
beyond what `baton` already has — the API is a convenience wrapper,
not a privilege escalation.

---

## Migration Strategy

### Phase 1: Core API (Week 1-2)
1. Create `agent_baton/api/` package
2. Implement routes: plans, executions, agents, health
3. Implement `baton serve` CLI command
4. Tests: 60+ endpoint tests with TestClient
5. Generate OpenAPI spec

### Phase 2: Decision & Event Endpoints (Week 3-4)
1. Implement decisions routes (depends on Proposal 001 DecisionManager)
2. Implement SSE event streaming (depends on Proposal 001 EventBus)
3. Tests: 40+ tests for SSE streaming, decision resolution

### Phase 3: Webhook System (Week 5-6)
1. Implement webhook registry + dispatcher
2. Implement retry queue
3. Slack payload formatter as reference implementation
4. Tests: 30+ tests for webhook delivery, retry, signing

### Phase 4: Combined Daemon+API (Week 7)
1. Wire `baton daemon start --serve` to run worker + API in same process
2. Integration test: full plan execution monitored via SSE + decisions
   resolved via API
3. Documentation: OpenAPI spec + curl examples

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| FastAPI dependency adds weight | Optional install group `[api]`. CLI works without it. |
| API server as attack surface | Local-only by default. Token auth for remote. No new file access. |
| SSE connections accumulate | Per-task subscription with auto-cleanup on execution complete. Max 50 concurrent connections. |
| Webhook failures flood retries | 3-attempt cap. Exponential backoff. Auto-disable after 10 consecutive failures. |
| API and CLI fight over state files | Both use same Python classes. File locking via `fcntl.flock()` on execution-state.json. |

---

## Success Criteria

1. `curl POST /api/v1/plans -d '{"description":"..."}'` returns a valid
   MachinePlan identical to `baton plan "..."`.
2. A web dashboard (even a simple HTML page) can show live execution
   progress via SSE without any filesystem access.
3. A pending decision created by the daemon can be resolved via
   `curl POST /api/v1/decisions/:id/resolve` and execution resumes
   within 2 seconds.
4. Webhook delivery to a test endpoint succeeds with valid HMAC signature.
5. `baton daemon start --plan plan.json --serve` runs headlessly,
   accepting API requests and executing the plan concurrently.
