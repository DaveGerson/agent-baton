# Agent Baton REST API Reference

This document is the complete reference for the Agent Baton HTTP API. The API
wraps the core orchestration engine, planner, agent registry, PMO subsystem,
and observability stack behind a versioned REST interface served by FastAPI.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Getting Started](#2-getting-started)
3. [Authentication](#3-authentication)
4. [Endpoints by Domain](#4-endpoints-by-domain)
   - [Health](#41-health)
   - [Plans](#42-plans)
   - [Executions](#43-executions)
   - [Agents](#44-agents)
   - [Decisions](#45-decisions)
   - [Events (SSE)](#46-events-sse)
   - [Observe](#47-observe)
   - [Webhooks](#48-webhooks)
   - [PMO](#49-pmo)
5. [Webhook System](#5-webhook-system)
6. [Request and Response Models](#6-request-and-response-models)
7. [CORS Configuration](#7-cors-configuration)
8. [Error Handling](#8-error-handling)
9. [Rate Limiting and Performance](#9-rate-limiting-and-performance)

---

## 1. Overview

The Agent Baton API exposes the orchestration engine over HTTP so that
external tools, dashboards (such as the PMO UI), and CI/CD pipelines can
drive plan creation, execution lifecycle, agent queries, and observability
without invoking the `baton` CLI directly.

**When to use the API vs the CLI:**

| Use case | Recommended interface |
|---|---|
| Interactive development with Claude Code | CLI (`baton plan`, `baton execute`) |
| PMO dashboard / React UI | API (the PMO UI ships as static files served at `/pmo/`) |
| CI/CD integration or external tooling | API |
| Webhook-driven notifications (Slack, etc.) | API (webhook registration + event bus) |
| Quick one-off commands | CLI |

**Key characteristics:**

- All endpoints are prefixed with `/api/v1`.
- The API is served by FastAPI with auto-generated OpenAPI docs at `/docs`
  (Swagger UI) and `/redoc` (ReDoc).
- A shared `EventBus` connects the execution engine, SSE stream, and
  webhook dispatcher so events are visible across all consumers.
- The server is designed to run locally (default bind: `127.0.0.1:8741`).

---

## 2. Getting Started

### Starting the API Server

The API server is started via the `baton daemon start --serve` command:

```bash
# Start daemon with API server (foreground)
baton daemon start --plan plan.json --serve --foreground

# Start daemon with API server (background)
baton daemon start --plan plan.json --serve

# Custom host, port, and auth token
baton daemon start --plan plan.json --serve \
    --host 0.0.0.0 --port 9000 --token my-secret-token

# Resume an existing execution with API
baton daemon start --resume --serve --foreground
```

### Default Configuration

| Parameter | Default | Description |
|---|---|---|
| `--host` | `127.0.0.1` | Bind address |
| `--port` | `8741` | Listen port |
| `--token` | *(none)* | Bearer token for auth (disabled when omitted) |
| `--max-parallel` | `3` | Maximum concurrent agent dispatches |

### Verifying the Server

```bash
curl http://127.0.0.1:8741/api/v1/health
```

Expected response:

```json
{
  "status": "healthy",
  "version": "0.5.0",
  "uptime_seconds": 12.34
}
```

### OpenAPI Documentation

Once the server is running, interactive API documentation is available at:

- **Swagger UI**: `http://127.0.0.1:8741/docs`
- **ReDoc**: `http://127.0.0.1:8741/redoc`
- **OpenAPI JSON**: `http://127.0.0.1:8741/openapi.json`

These paths are exempt from authentication.

### Programmatic Usage

```python
import uvicorn
from agent_baton.api.server import create_app

app = create_app(host="127.0.0.1", port=8741, token="secret")
uvicorn.run(app, host="127.0.0.1", port=8741)
```

---

## 3. Authentication

### Bearer Token Authentication

When a `token` is configured, every request (except exempt paths) must
include an `Authorization` header:

```
Authorization: Bearer <token>
```

If authentication is not configured (no `--token` flag), all requests are
passed through without credential checks.

### Auth-Exempt Paths

The following paths bypass token authentication regardless of configuration:

| Path | Purpose |
|---|---|
| `/api/v1/health` | Liveness probe |
| `/api/v1/ready` | Readiness probe |
| `/openapi.json` | OpenAPI schema |
| `/docs` | Swagger UI |
| `/docs/oauth2-redirect` | OAuth2 redirect for Swagger |
| `/redoc` | ReDoc documentation |

### Unauthorized Response

When authentication fails, the server returns:

```
HTTP/1.1 401 Unauthorized
Content-Type: application/json

{
  "error": "unauthorized",
  "detail": "Valid Bearer token required."
}
```

### Example with Authentication

```bash
# With token
curl -H "Authorization: Bearer my-secret-token" \
     http://127.0.0.1:8741/api/v1/agents

# Without token (when auth is disabled)
curl http://127.0.0.1:8741/api/v1/agents
```

---

## 4. Endpoints by Domain

### 4.1 Health

Health and readiness probes for liveness checks and container orchestrators.

#### `GET /api/v1/health`

Liveness probe. Returns 200 while the server process is running.

**Response Model:** `HealthResponse`

| Field | Type | Description |
|---|---|---|
| `status` | string | Service status (always `"healthy"`) |
| `version` | string | Agent Baton version string |
| `uptime_seconds` | float | Seconds since the server started |

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/health
```

```json
{
  "status": "healthy",
  "version": "0.5.0",
  "uptime_seconds": 142.7
}
```

---

#### `GET /api/v1/ready`

Readiness probe. Reports whether the engine has an active execution and
whether there are pending human decisions.

**Response Model:** `ReadyResponse`

| Field | Type | Description |
|---|---|---|
| `ready` | bool | Whether the service is ready to accept work |
| `daemon_running` | bool | Whether an active execution exists |
| `pending_decisions` | int | Number of unresolved human decisions |

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/ready
```

```json
{
  "ready": true,
  "daemon_running": true,
  "pending_decisions": 1
}
```

---

### 4.2 Plans

Plan generation and retrieval. Plans are execution blueprints produced by
the `IntelligentPlanner` from natural-language task descriptions.

#### `POST /api/v1/plans`

Generate a new execution plan from a natural-language description.

**Status Code:** `201 Created`

**Request Body:** `CreatePlanRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | Yes | Natural-language description of the task to plan (min 1 char) |
| `task_type` | string | No | Task classifier hint: `"feature"`, `"bugfix"`, `"refactor"`, etc. |
| `agents` | list[string] | No | Explicit agent roster override. Omit to let the planner select. |
| `project_path` | string | No | Absolute path to the target project. Defaults to daemon working directory. |

**Response Model:** `PlanResponse`

| Field | Type | Description |
|---|---|---|
| `plan_id` | string | Unique task/plan identifier |
| `task_summary` | string | Human-readable task description |
| `risk_level` | string | Risk classification: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| `budget_tier` | string | Budget tier: `lean`, `standard`, `full` |
| `execution_mode` | string | Execution strategy (e.g. `phased`) |
| `git_strategy` | string | Git commit strategy (e.g. `commit-per-agent`) |
| `phases` | list[PlanPhaseResponse] | Ordered execution phases |
| `total_steps` | int | Total number of steps across all phases |
| `agents` | list[string] | All agent names used in the plan |
| `pattern_source` | string | Learned pattern that influenced this plan (nullable) |
| `created_at` | string | ISO 8601 creation timestamp |

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Invalid request (e.g. empty description) |
| `500` | Planning failed internally |

**Example:**

```bash
curl -X POST http://127.0.0.1:8741/api/v1/plans \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Add user authentication with JWT tokens",
    "task_type": "feature",
    "project_path": "/home/user/my-project"
  }'
```

```json
{
  "plan_id": "task-abc123",
  "task_summary": "Add user authentication with JWT tokens",
  "risk_level": "MEDIUM",
  "budget_tier": "standard",
  "execution_mode": "phased",
  "git_strategy": "commit-per-agent",
  "phases": [
    {
      "phase_id": 0,
      "name": "Implementation",
      "steps": [
        {
          "step_id": "0.0",
          "agent_name": "backend-engineer--python",
          "task_description": "Implement JWT authentication middleware",
          "model": "sonnet",
          "depends_on": [],
          "deliverables": ["auth/jwt.py"],
          "allowed_paths": ["src/auth/"],
          "blocked_paths": [],
          "context_files": ["requirements.txt"]
        }
      ],
      "gate": {
        "gate_type": "test",
        "command": "pytest tests/",
        "description": "Run test suite",
        "fail_on": ["test failures"]
      }
    }
  ],
  "total_steps": 1,
  "agents": ["backend-engineer--python"],
  "pattern_source": null,
  "created_at": "2026-03-24T10:00:00+00:00"
}
```

---

#### `GET /api/v1/plans/{plan_id}`

Retrieve an existing plan by ID from the engine's active execution state.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `plan_id` | string | The plan/task identifier to retrieve |

**Response Model:** `PlanResponse` (same as above)

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | No active plan with the given ID |

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/plans/task-abc123
```

> **Note:** Plans are stored inside the `ExecutionState`, not independently.
> Only the currently active plan can be retrieved.

---

### 4.3 Executions

Execution lifecycle management: start, query, record results, complete,
and cancel executions.

#### `POST /api/v1/executions`

Begin executing a plan. Supply either a `plan_id` (referencing a previously
created plan) or an inline `plan` dict.

**Status Code:** `201 Created`

**Request Body:** `StartExecutionRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `plan_id` | string | One of `plan_id`/`plan` | ID of a previously created plan |
| `plan` | dict | One of `plan_id`/`plan` | Inline plan dict (MachinePlan shape) |

> Exactly one of `plan_id` or `plan` must be provided. Providing both or
> neither is a validation error.

**Response:** JSON object with two keys:

| Field | Type | Description |
|---|---|---|
| `execution` | ExecutionResponse | Current execution state |
| `next_actions` | list[ActionResponse] | Initial dispatchable actions |

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Both or neither of `plan_id`/`plan` provided, or invalid plan dict |
| `404` | `plan_id` references a non-existent plan |
| `500` | Engine failed to start or persist state |

**Example:**

```bash
curl -X POST http://127.0.0.1:8741/api/v1/executions \
  -H "Content-Type: application/json" \
  -d '{"plan_id": "task-abc123"}'
```

```json
{
  "execution": {
    "task_id": "task-abc123",
    "status": "running",
    "current_phase": 0,
    "current_step_index": 0,
    "steps_completed": 0,
    "steps_remaining": 3,
    "steps_failed": 0,
    "gates_passed": 0,
    "pending_decisions": 0,
    "step_results": [],
    "gate_results": [],
    "plan_id": "task-abc123",
    "started_at": "2026-03-24T10:01:00+00:00",
    "completed_at": ""
  },
  "next_actions": [
    {
      "action_type": "dispatch",
      "message": "Dispatch backend-engineer--python for step 0.0",
      "agent_name": "backend-engineer--python",
      "agent_model": "sonnet",
      "step_id": "0.0",
      "gate_type": null,
      "gate_command": null,
      "phase_id": null,
      "summary": null,
      "parallel_actions": []
    }
  ]
}
```

---

#### `GET /api/v1/executions/{task_id}`

Query the current execution state for a task.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `task_id` | string | Execution/task identifier |

**Response Model:** `ExecutionResponse`

| Field | Type | Description |
|---|---|---|
| `task_id` | string | Execution/task identifier |
| `status` | string | Current status: `running`, `gate_pending`, `complete`, `failed` |
| `current_phase` | int | Index of the active phase |
| `current_step_index` | int | Index of the active step within the phase |
| `steps_completed` | int | Number of steps finished successfully |
| `steps_remaining` | int | Number of steps not yet started |
| `steps_failed` | int | Number of steps that failed |
| `gates_passed` | int | Number of QA gates passed |
| `pending_decisions` | int | Number of unresolved human decisions |
| `step_results` | list[StepResultResponse] | Results for completed steps |
| `gate_results` | list[dict] | Results for completed gates |
| `plan_id` | string | ID of the plan being executed |
| `started_at` | string | ISO 8601 start timestamp |
| `completed_at` | string | ISO 8601 completion timestamp (empty if running) |

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | No active execution with the given `task_id` |

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/executions/task-abc123
```

---

#### `POST /api/v1/executions/{task_id}/record`

Record the outcome of a completed step and return the next batch of
dispatchable actions.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `task_id` | string | Execution/task identifier |

**Request Body:** `RecordStepRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `step_id` | string | Yes | Step identifier (e.g. `"1.1"`) |
| `agent` | string | Yes | Name of the agent that executed the step |
| `status` | string | Yes | Outcome: `"complete"`, `"failed"`, or `"dispatched"` |
| `output_summary` | string | No | Free-text summary of step output |
| `tokens` | int | No | Estimated token usage (>= 0) |
| `duration_ms` | int | No | Wall-clock duration in milliseconds (>= 0) |

**Response:**

| Field | Type | Description |
|---|---|---|
| `recorded` | bool | Always `true` on success |
| `next_actions` | list[ActionResponse] | Next dispatchable actions |

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Invalid step_id or status value |
| `404` | No active execution with the given `task_id` |
| `500` | Engine error during recording |

**Example:**

```bash
curl -X POST http://127.0.0.1:8741/api/v1/executions/task-abc123/record \
  -H "Content-Type: application/json" \
  -d '{
    "step_id": "0.0",
    "agent": "backend-engineer--python",
    "status": "complete",
    "output_summary": "Implemented JWT middleware in auth/jwt.py",
    "tokens": 15000,
    "duration_ms": 45000
  }'
```

```json
{
  "recorded": true,
  "next_actions": [
    {
      "action_type": "gate",
      "message": "Run QA gate: test",
      "gate_type": "test",
      "gate_command": "pytest tests/",
      "phase_id": 0
    }
  ]
}
```

---

#### `POST /api/v1/executions/{task_id}/gate`

Record the outcome of a QA gate check and return the next actions.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `task_id` | string | Execution/task identifier |

**Request Body:** `RecordGateRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `phase_id` | int | Yes | Phase index the gate belongs to |
| `result` | string | Yes | Gate outcome: `"pass"`, `"fail"`, or `"pass_with_notes"` |
| `notes` | string | No | Reviewer notes or command output |

**Response:**

| Field | Type | Description |
|---|---|---|
| `recorded` | bool | Always `true` on success |
| `next_actions` | list[ActionResponse] | Next dispatchable actions |

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | No active execution with the given `task_id` |
| `500` | Engine error during gate recording |

**Example:**

```bash
curl -X POST http://127.0.0.1:8741/api/v1/executions/task-abc123/gate \
  -H "Content-Type: application/json" \
  -d '{
    "phase_id": 0,
    "result": "pass",
    "notes": "All 47 tests passed"
  }'
```

---

#### `POST /api/v1/executions/{task_id}/complete`

Finalize a completed execution. Writes trace, usage log, and retrospective
data.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `task_id` | string | Execution/task identifier |

**Response:**

| Field | Type | Description |
|---|---|---|
| `task_id` | string | The completed task ID |
| `status` | string | Always `"complete"` |
| `summary` | string/dict | Final execution summary |

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | No active execution with the given `task_id` |
| `500` | Completion failed |

**Example:**

```bash
curl -X POST http://127.0.0.1:8741/api/v1/executions/task-abc123/complete
```

```json
{
  "task_id": "task-abc123",
  "status": "complete",
  "summary": "3 steps completed, 2 gates passed, outcome: SHIP"
}
```

---

#### `DELETE /api/v1/executions/{task_id}`

Cancel a running execution. Cancellation is best-effort: the engine state
transitions to `failed` but in-flight subagent processes are not terminated.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `task_id` | string | Execution/task identifier |

**Response:**

| Field | Type | Description |
|---|---|---|
| `cancelled` | bool | Always `true` on success |
| `task_id` | string | The cancelled task ID |

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | No active execution with the given `task_id` |

**Example:**

```bash
curl -X DELETE http://127.0.0.1:8741/api/v1/executions/task-abc123
```

```json
{
  "cancelled": true,
  "task_id": "task-abc123"
}
```

---

### 4.4 Agents

Agent registry queries. List available agents with filtering, or retrieve
a specific agent definition.

#### `GET /api/v1/agents`

List all available agents from the registry.

**Query Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `category` | string | No | Filter by agent category (case-insensitive). Valid values: `Engineering`, `Quality`, `Architecture`, `Data`, `Operations`, `Management`, `Security`. |
| `stack` | string | No | Filter by stack/flavor substring match (e.g. `"python"`, `"react"`, `"py"`). |

**Response Model:** `AgentListResponse`

| Field | Type | Description |
|---|---|---|
| `count` | int | Number of agents in the response |
| `agents` | list[AgentResponse] | Agent definitions |

Each `AgentResponse`:

| Field | Type | Description |
|---|---|---|
| `name` | string | Agent identifier (e.g. `"backend-engineer--python"`) |
| `description` | string | What this agent does |
| `model` | string | Default LLM model tier |
| `permission_mode` | string | Tool permission mode |
| `color` | string | Display color for dashboards (nullable) |
| `tools` | list[string] | Tools this agent may use |
| `category` | string | Agent category |
| `base_name` | string | Name without flavor suffix |
| `flavor` | string | Flavor suffix, if any (nullable) |

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Unknown category value |

**Example:**

```bash
# List all agents
curl http://127.0.0.1:8741/api/v1/agents

# Filter by category
curl "http://127.0.0.1:8741/api/v1/agents?category=Engineering"

# Filter by stack
curl "http://127.0.0.1:8741/api/v1/agents?stack=python"

# Combine filters
curl "http://127.0.0.1:8741/api/v1/agents?category=Engineering&stack=python"
```

```json
{
  "count": 2,
  "agents": [
    {
      "name": "backend-engineer--python",
      "description": "Python backend implementation",
      "model": "sonnet",
      "permission_mode": "default",
      "color": "#3776AB",
      "tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep"],
      "category": "Engineering",
      "base_name": "backend-engineer",
      "flavor": "python"
    }
  ]
}
```

---

#### `GET /api/v1/agents/{name}`

Retrieve a single agent definition by name.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `name` | string | Agent identifier |

**Response Model:** `AgentResponse`

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | Agent not found in registry |

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/agents/backend-engineer--python
```

> **Note:** The response omits `source_path` and `instructions` (the full
> markdown body) to keep payloads lightweight.

---

### 4.5 Decisions

Human decision management for gate approvals, escalations, and plan reviews
that require human intervention during execution.

#### `GET /api/v1/decisions`

List decision requests with optional filtering.

**Query Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `status` | string | No | Filter by status: `"pending"`, `"resolved"`, `"expired"` |
| `task_id` | string | No | Filter to a specific task |

Both filters may be combined.

**Response Model:** `DecisionListResponse`

| Field | Type | Description |
|---|---|---|
| `count` | int | Number of decisions in the list |
| `decisions` | list[DecisionResponse] | Decision request objects |

**Example:**

```bash
# All pending decisions
curl "http://127.0.0.1:8741/api/v1/decisions?status=pending"

# Decisions for a specific task
curl "http://127.0.0.1:8741/api/v1/decisions?task_id=task-abc123"
```

```json
{
  "count": 1,
  "decisions": [
    {
      "request_id": "dec-001",
      "task_id": "task-abc123",
      "decision_type": "gate_approval",
      "summary": "Test gate failed with 2 errors. Approve to continue?",
      "options": ["approve", "reject", "approve-with-feedback"],
      "deadline": "2026-03-24T12:00:00+00:00",
      "context_files": [".claude/team-context/gate-output.txt"],
      "created_at": "2026-03-24T10:30:00+00:00",
      "status": "pending",
      "context_file_contents": null
    }
  ]
}
```

---

#### `GET /api/v1/decisions/{request_id}`

Fetch a single decision with enriched context file contents. Context files
listed in the decision are read from disk and their contents are embedded
in the response so that remote UIs do not need filesystem access.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `request_id` | string | Unique decision request identifier |

**Response Model:** `DecisionResponse`

| Field | Type | Description |
|---|---|---|
| `request_id` | string | Unique decision request identifier |
| `task_id` | string | Task this decision belongs to |
| `decision_type` | string | Category: `gate_approval`, `escalation`, `plan_review` |
| `summary` | string | Human-readable context |
| `options` | list[string] | Available choices |
| `deadline` | string | ISO 8601 expiry timestamp (nullable) |
| `context_files` | list[string] | Paths to context files |
| `created_at` | string | ISO 8601 creation timestamp |
| `status` | string | `pending`, `resolved`, or `expired` |
| `context_file_contents` | dict[string, string] | Inline contents keyed by file path (nullable) |

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | Decision not found |

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/decisions/dec-001
```

---

#### `POST /api/v1/decisions/{request_id}/resolve`

Resolve a pending decision request. The resolution is persisted to disk and
an event is published on the shared `EventBus` so waiting workers can unblock.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `request_id` | string | Decision request identifier to resolve |

**Request Body:** `ResolveDecisionRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `option` | string | Yes | The chosen option (must be one of the decision's listed options, min 1 char) |
| `rationale` | string | No | Human rationale for the choice |
| `resolved_by` | string | No | Who resolved this (defaults to `"human"`) |

**Response Model:** `ResolveResponse`

| Field | Type | Description |
|---|---|---|
| `resolved` | bool | Whether the decision was successfully resolved |
| `execution_resumed` | bool | Whether execution automatically resumed |

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Decision is not in `pending` status |
| `404` | Decision not found |
| `409` | Concurrent modification prevented resolution |

**Example:**

```bash
curl -X POST http://127.0.0.1:8741/api/v1/decisions/dec-001/resolve \
  -H "Content-Type: application/json" \
  -d '{
    "option": "approve",
    "rationale": "Test failures are in unrelated modules",
    "resolved_by": "dave"
  }'
```

```json
{
  "resolved": true,
  "execution_resumed": false
}
```

---

### 4.6 Events (SSE)

Real-time event streaming over Server-Sent Events. Requires the
`sse-starlette` package.

#### `GET /api/v1/events/{task_id}`

Open a Server-Sent Events stream for a task.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `task_id` | string | The task whose event stream to subscribe to |

**Headers:**

```
Accept: text/event-stream
```

**Behavior:**

1. **Replay**: The stream begins with a replay of every event already stored
   in the bus for the requested task.
2. **Live**: After replay, newly published events are forwarded in real time.
3. **Keepalive**: A comment (`keepalive`) is sent every 30 seconds when the
   task produces no activity, preventing proxies from closing the connection.
4. **Cleanup**: The subscription is removed automatically when the client
   disconnects.

**SSE Event Format:**

Each event is a standard SSE message with:

| SSE Field | Value |
|---|---|
| `event` | The event topic (e.g. `step.completed`) |
| `id` | Unique event identifier |
| `data` | JSON-serialized event dict |

**Available Event Topics:**

| Topic | Description |
|---|---|
| `task.started` | Execution started |
| `task.completed` | Execution finished successfully |
| `task.failed` | Execution failed |
| `phase.started` | A new phase began |
| `phase.completed` | A phase finished |
| `step.dispatched` | A step was dispatched to an agent |
| `step.completed` | A step finished successfully |
| `step.failed` | A step failed |
| `gate.required` | A QA gate check is needed |
| `gate.passed` | A QA gate passed |
| `gate.failed` | A QA gate failed |
| `human.decision_needed` | A human decision is required |
| `human.decision_resolved` | A human decision was resolved |
| `approval.required` | An approval is required |
| `approval.resolved` | An approval was resolved |
| `plan.amended` | The plan was amended during execution |
| `team.member_completed` | A team member completed their part |

**Example:**

```bash
curl -N -H "Accept: text/event-stream" \
     http://127.0.0.1:8741/api/v1/events/task-abc123
```

```
event: step.dispatched
id: a1b2c3d4e5f6
data: {"event_id":"a1b2c3d4e5f6","timestamp":"2026-03-24T10:01:00+00:00","topic":"step.dispatched","task_id":"task-abc123","sequence":1,"payload":{"step_id":"0.0","agent":"backend-engineer--python"}}

event: step.completed
id: f6e5d4c3b2a1
data: {"event_id":"f6e5d4c3b2a1","timestamp":"2026-03-24T10:02:30+00:00","topic":"step.completed","task_id":"task-abc123","sequence":2,"payload":{"step_id":"0.0","agent":"backend-engineer--python","status":"complete"}}

: keepalive
```

**JavaScript Client Example:**

```javascript
const eventSource = new EventSource(
  'http://127.0.0.1:8741/api/v1/events/task-abc123'
);

eventSource.addEventListener('step.completed', (event) => {
  const data = JSON.parse(event.data);
  console.log('Step completed:', data.payload.step_id);
});

eventSource.addEventListener('human.decision_needed', (event) => {
  const data = JSON.parse(event.data);
  console.log('Decision needed:', data.payload.summary);
});
```

---

### 4.7 Observe

Observability endpoints for dashboards, execution traces, and usage analytics.

#### `GET /api/v1/dashboard`

Return the pre-rendered usage dashboard as markdown.

**Response Model:** `DashboardResponse`

| Field | Type | Description |
|---|---|---|
| `dashboard_markdown` | string | Pre-rendered markdown dashboard content |
| `metrics` | dict | Structured metrics (currently empty; reserved for future use) |

**Error Responses:**

| Status | Condition |
|---|---|
| `500` | Dashboard generation failed |

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/dashboard
```

```json
{
  "dashboard_markdown": "# Usage Dashboard\n\n## Summary\n- Total tasks: 12\n...",
  "metrics": {}
}
```

---

#### `GET /api/v1/traces/{task_id}`

Return the structured execution trace for a completed task.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `task_id` | string | Task identifier |

**Response Model:** `TraceResponse`

| Field | Type | Description |
|---|---|---|
| `task_id` | string | Task identifier |
| `plan_snapshot` | dict | Snapshot of the plan at execution start |
| `events` | list[TraceEventResponse] | Ordered trace events |
| `started_at` | string | ISO 8601 start timestamp |
| `completed_at` | string | ISO 8601 completion timestamp (nullable) |
| `outcome` | string | Final outcome: `SHIP`, `REVISE`, `BLOCK`, etc. (nullable) |

Each `TraceEventResponse`:

| Field | Type | Description |
|---|---|---|
| `timestamp` | string | ISO 8601 event timestamp |
| `event_type` | string | Event category (`agent_start`, `gate_check`, etc.) |
| `agent_name` | string | Agent involved (nullable) |
| `phase` | int | Phase index |
| `step` | int | Step index |
| `details` | dict | Event-specific details |
| `duration_seconds` | float | Duration in seconds (nullable) |

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | No trace found for the task |

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/traces/task-abc123
```

---

#### `GET /api/v1/usage`

Return usage records with optional filtering and summary statistics.

**Query Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `since` | string | No | ISO 8601 timestamp. Only return records at or after this time. |
| `agent` | string | No | Filter to records that include this agent name. |

**Response Model:** `UsageResponse`

| Field | Type | Description |
|---|---|---|
| `records` | list[TaskUsageResponse] | Individual task usage records |
| `summary` | dict | Aggregated summary |

Summary fields:

| Key | Type | Description |
|---|---|---|
| `total_tasks` | int | Total number of matching tasks |
| `total_tokens` | int | Sum of estimated tokens across all agents |
| `total_agents` | int | Sum of agents used across all tasks |
| `outcome_counts` | dict[string, int] | Counts by outcome (e.g. `{"SHIP": 5, "REVISE": 1}`) |

Each `TaskUsageResponse`:

| Field | Type | Description |
|---|---|---|
| `task_id` | string | Task identifier |
| `timestamp` | string | ISO 8601 timestamp |
| `agents_used` | list[AgentUsageResponse] | Per-agent usage breakdown |
| `total_agents` | int | Number of agents involved |
| `risk_level` | string | Risk classification |
| `sequencing_mode` | string | Execution sequencing mode |
| `gates_passed` | int | Gates passed |
| `gates_failed` | int | Gates failed |
| `outcome` | string | Final outcome |

**Error Responses:**

| Status | Condition |
|---|---|
| `500` | Failed to read usage log |

**Example:**

```bash
# All usage
curl http://127.0.0.1:8741/api/v1/usage

# Usage since a date
curl "http://127.0.0.1:8741/api/v1/usage?since=2026-03-01T00:00:00Z"

# Usage for a specific agent
curl "http://127.0.0.1:8741/api/v1/usage?agent=backend-engineer--python"
```

> **Note:** Filtering is performed in-memory. For large usage logs a future
> version may add cursor-based pagination.

---

### 4.8 Webhooks

Outbound webhook subscription management. Register endpoints to receive
POST callbacks when events occur.

#### `POST /api/v1/webhooks`

Register a new outbound webhook subscription.

**Status Code:** `201 Created`

**Request Body:** `RegisterWebhookRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `url` | string | Yes | HTTPS endpoint that will receive POST callbacks |
| `events` | list[string] | Yes | Event topic patterns to subscribe to (min 1). Glob-style patterns supported. |
| `secret` | string | No | Shared secret for HMAC-SHA256 signature verification |

**Response Model:** `WebhookResponse`

| Field | Type | Description |
|---|---|---|
| `webhook_id` | string | Auto-assigned unique identifier (16 hex chars) |
| `url` | string | Registered callback URL |
| `events` | list[string] | Subscribed event topic patterns |
| `created` | string | ISO 8601 registration timestamp |

**Example:**

```bash
curl -X POST http://127.0.0.1:8741/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/baton-hook",
    "events": ["step.*", "gate.required", "human.decision_needed"],
    "secret": "my-webhook-secret"
  }'
```

```json
{
  "webhook_id": "a1b2c3d4e5f67890",
  "url": "https://example.com/baton-hook",
  "events": ["step.*", "gate.required", "human.decision_needed"],
  "created": "2026-03-24T10:00:00+00:00"
}
```

---

#### `GET /api/v1/webhooks`

List all registered webhook subscriptions (enabled and disabled).

**Response:** `list[WebhookResponse]`

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/webhooks
```

```json
[
  {
    "webhook_id": "a1b2c3d4e5f67890",
    "url": "https://example.com/baton-hook",
    "events": ["step.*", "gate.required"],
    "created": "2026-03-24T10:00:00+00:00"
  }
]
```

---

#### `DELETE /api/v1/webhooks/{webhook_id}`

Remove a webhook subscription permanently.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `webhook_id` | string | Webhook identifier to delete |

**Response:**

```json
{"deleted": true}
```

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | Webhook not found |

**Example:**

```bash
curl -X DELETE http://127.0.0.1:8741/api/v1/webhooks/a1b2c3d4e5f67890
```

---

### 4.9 PMO

Portfolio Management Office endpoints for the Kanban board, project
registration, health metrics, plan forge, and signals.

#### Board

##### `GET /api/v1/pmo/board`

Return the full Kanban board with all cards and per-program health metrics.

**Response Model:** `PmoBoardResponse`

| Field | Type | Description |
|---|---|---|
| `cards` | list[PmoCardResponse] | All Kanban cards across all projects |
| `health` | dict[string, ProgramHealthResponse] | Per-program health keyed by program code |

Each `PmoCardResponse`:

| Field | Type | Description |
|---|---|---|
| `card_id` | string | Task ID from the underlying plan |
| `project_id` | string | Owning project ID |
| `program` | string | Program code |
| `title` | string | Plan task summary |
| `column` | string | Kanban column: `queued`, `planning`, `executing`, `gate_pending`, `deployed`, `failed` |
| `risk_level` | string | Risk classification |
| `priority` | int | Priority: 0=normal, 1=high, 2=critical |
| `agents` | list[string] | Agent names used |
| `steps_completed` | int | Steps completed |
| `steps_total` | int | Total steps |
| `gates_passed` | int | Gates passed |
| `current_phase` | string | Name of the active phase |
| `error` | string | Last failure error message |
| `created_at` | string | ISO 8601 plan creation timestamp |
| `updated_at` | string | ISO 8601 last-updated timestamp |

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/pmo/board
```

---

##### `GET /api/v1/pmo/board/{program}`

Return the Kanban board filtered to a single program.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `program` | string | Program code (case-insensitive) |

**Response Model:** `PmoBoardResponse` (same shape, filtered)

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/pmo/board/NDS
```

---

#### Projects

##### `GET /api/v1/pmo/projects`

List all registered PMO projects.

**Response:** `list[PmoProjectResponse]`

Each `PmoProjectResponse`:

| Field | Type | Description |
|---|---|---|
| `project_id` | string | Unique project slug |
| `name` | string | Human-readable project name |
| `path` | string | Absolute filesystem path |
| `program` | string | Program code |
| `color` | string | Display color |
| `description` | string | Project description |
| `registered_at` | string | ISO 8601 registration timestamp |

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/pmo/projects
```

---

##### `POST /api/v1/pmo/projects`

Register a new project with the PMO.

**Status Code:** `201 Created`

**Request Body:** `RegisterProjectRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `project_id` | string | Yes | Unique project slug (e.g. `"nds"`) |
| `name` | string | Yes | Human-readable project name |
| `path` | string | Yes | Absolute filesystem path to the project root |
| `program` | string | Yes | Program code (e.g. `"NDS"`, `"ATL"`) |
| `color` | string | No | Display color (e.g. `"#4A90E2"`) |
| `description` | string | No | Free-text project description |

**Response Model:** `PmoProjectResponse`

**Error Responses:**

| Status | Condition |
|---|---|
| `500` | Registration failed |

> **Note:** If a project with the same `project_id` already exists, it is
> replaced. This is intentional to support re-registration after path changes.

**Example:**

```bash
curl -X POST http://127.0.0.1:8741/api/v1/pmo/projects \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "nds",
    "name": "Network Data Systems",
    "path": "/home/user/projects/nds",
    "program": "NDS",
    "color": "#4A90E2",
    "description": "Core NDS analytics platform"
  }'
```

---

##### `DELETE /api/v1/pmo/projects/{project_id}`

Unregister a project from the PMO.

**Status Code:** `204 No Content`

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `project_id` | string | Project identifier to remove |

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | Project not found |

**Example:**

```bash
curl -X DELETE http://127.0.0.1:8741/api/v1/pmo/projects/nds
```

---

#### Health

##### `GET /api/v1/pmo/health`

Return aggregate health metrics per program.

**Response:** `dict[string, ProgramHealthResponse]`

Each `ProgramHealthResponse`:

| Field | Type | Description |
|---|---|---|
| `program` | string | Program code |
| `total_plans` | int | Total number of tracked plans |
| `active` | int | Plans currently in progress |
| `completed` | int | Plans in the deployed column |
| `blocked` | int | Plans awaiting human input |
| `failed` | int | Plans with a failure error |
| `completion_pct` | float | Percentage of plans completed |

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/pmo/health
```

```json
{
  "NDS": {
    "program": "NDS",
    "total_plans": 8,
    "active": 3,
    "completed": 4,
    "blocked": 0,
    "failed": 1,
    "completion_pct": 50.0
  }
}
```

---

#### Forge (Plan Creation and Approval)

##### `POST /api/v1/pmo/forge/plan`

Create a plan via IntelligentPlanner for a registered project. The plan is
returned for review but NOT saved to disk.

**Status Code:** `201 Created`

**Request Body:** `CreateForgeRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `description` | string | Yes | Natural-language task description (the PRD) |
| `program` | string | Yes | Program code for context |
| `project_id` | string | Yes | ID of the registered project |
| `task_type` | string | No | Task type hint: `"new-feature"`, `"bug-fix"`, `"refactor"` |
| `priority` | int | No | Plan priority: 0=normal, 1=high, 2=critical (default: 0) |

**Response:** Raw plan dict (MachinePlan shape)

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Invalid request |
| `404` | Project not found |
| `500` | Plan creation failed |

**Example:**

```bash
curl -X POST http://127.0.0.1:8741/api/v1/pmo/forge/plan \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Add cargo capacity optimization algorithm",
    "program": "COM",
    "project_id": "com-revenue",
    "task_type": "new-feature",
    "priority": 1
  }'
```

---

##### `POST /api/v1/pmo/forge/approve`

Save an approved plan to a project's team-context directory.

**Request Body:** `ApproveForgeRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `plan` | dict | Yes | Plan dict (MachinePlan shape), possibly edited by user |
| `project_id` | string | Yes | ID of the project that will receive the plan |

**Response:**

```json
{"saved": true, "path": "/home/user/project/.claude/team-context/plan.json"}
```

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Invalid plan payload |
| `404` | Project not found |
| `500` | Failed to save plan |

---

##### `POST /api/v1/pmo/forge/interview`

Generate structured interview questions for plan refinement.

**Request Body:** `InterviewRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `plan` | dict | Yes | Current plan dict (MachinePlan shape) |
| `feedback` | string | No | Optional user feedback on what to change |

**Response Model:** `InterviewResponse`

| Field | Type | Description |
|---|---|---|
| `questions` | list[InterviewQuestionResponse] | 3-5 structured interview questions |

Each `InterviewQuestionResponse`:

| Field | Type | Description |
|---|---|---|
| `id` | string | Question identifier |
| `question` | string | The question text |
| `context` | string | Why this question matters |
| `answer_type` | string | `"choice"` or `"text"` |
| `choices` | list[string] | Options for choice-type questions (nullable) |

**Error Responses:**

| Status | Condition |
|---|---|
| `400` | Invalid plan |

---

##### `POST /api/v1/pmo/forge/regenerate`

Re-generate a plan incorporating interview answers.

**Status Code:** `201 Created`

**Request Body:** `RegenerateRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `project_id` | string | Yes | Target project ID |
| `description` | string | Yes | Original task description |
| `task_type` | string | No | Task type hint |
| `priority` | int | No | Priority: 0-2 |
| `original_plan` | dict | Yes | Current plan to refine |
| `answers` | list[InterviewAnswerPayload] | Yes | Answered interview questions |

Each `InterviewAnswerPayload`:

| Field | Type | Description |
|---|---|---|
| `question_id` | string | ID of the question being answered |
| `answer` | string | User's answer |

**Response:** Regenerated plan dict

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | Project not found |
| `500` | Regeneration failed |

---

##### `GET /api/v1/pmo/ado/search`

Search Azure DevOps work items (currently returns mock/placeholder data).

**Query Parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `q` | string | No | Search query (matches against title, ID, and program) |

**Response Model:** `AdoSearchResponse`

| Field | Type | Description |
|---|---|---|
| `items` | list[AdoWorkItemResponse] | Matching work items |

---

#### Signals

##### `GET /api/v1/pmo/signals`

List all open (non-resolved) signals.

**Response:** `list[PmoSignalResponse]`

Each `PmoSignalResponse`:

| Field | Type | Description |
|---|---|---|
| `signal_id` | string | Unique signal identifier |
| `signal_type` | string | `bug`, `escalation`, or `blocker` |
| `title` | string | Short signal title |
| `description` | string | Additional context |
| `source_project_id` | string | Project that generated this signal |
| `severity` | string | `low`, `medium`, `high`, or `critical` |
| `status` | string | `open`, `triaged`, or `resolved` |
| `created_at` | string | ISO 8601 creation timestamp |
| `forge_task_id` | string | Plan task ID if triaged by Forge |

**Example:**

```bash
curl http://127.0.0.1:8741/api/v1/pmo/signals
```

---

##### `POST /api/v1/pmo/signals`

Create a new signal (bug, escalation, or blocker).

**Status Code:** `201 Created`

**Request Body:** `CreateSignalRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `signal_id` | string | Yes | Unique signal identifier |
| `signal_type` | string | Yes | `"bug"`, `"escalation"`, or `"blocker"` |
| `title` | string | Yes | Short, human-readable signal title |
| `description` | string | No | Additional context or reproduction steps |
| `source_project_id` | string | No | Project ID that generated this signal |
| `severity` | string | No | `"low"`, `"medium"`, `"high"`, or `"critical"` (default: `"medium"`) |

**Response Model:** `PmoSignalResponse`

**Error Responses:**

| Status | Condition |
|---|---|
| `500` | Failed to create signal |

**Example:**

```bash
curl -X POST http://127.0.0.1:8741/api/v1/pmo/signals \
  -H "Content-Type: application/json" \
  -d '{
    "signal_id": "sig-001",
    "signal_type": "bug",
    "title": "R2 blocks missing on Off day",
    "description": "Crew scheduling R2 blocks not appearing for off-day assignments",
    "source_project_id": "nds",
    "severity": "critical"
  }'
```

---

##### `POST /api/v1/pmo/signals/{signal_id}/resolve`

Mark a signal as resolved.

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `signal_id` | string | Signal identifier |

**Response:**

```json
{"resolved": true, "signal_id": "sig-001"}
```

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | Signal not found |

---

##### `POST /api/v1/pmo/signals/{signal_id}/forge`

Triage a signal into an execution plan via the Forge. Generates a bug-fix
plan from the signal description, links the signal to the plan, saves the
plan to the project's team-context, and updates the signal status to `triaged`.

**Status Code:** `201 Created`

**Path Parameters:**

| Parameter | Type | Description |
|---|---|---|
| `signal_id` | string | Signal identifier to triage |

**Request Body:** `ApproveForgeRequest`

| Field | Type | Required | Description |
|---|---|---|---|
| `plan` | dict | Yes | Ignored for this endpoint (Forge derives the plan from the signal) |
| `project_id` | string | Yes | Project that will receive the plan |

**Response:**

```json
{
  "signal_id": "sig-001",
  "plan_id": "task-xyz789",
  "path": "/home/user/project/.claude/team-context/plan.json"
}
```

**Error Responses:**

| Status | Condition |
|---|---|
| `404` | Project or signal not found |
| `500` | Forge triaging failed |

---

## 5. Webhook System

The webhook system delivers real-time event notifications to external
endpoints via HTTP POST requests.

### Architecture

```
EventBus ──> WebhookDispatcher ──> WebhookRegistry.match(topic)
                  │                        │
                  │                  ┌──────┴──────┐
                  │                  │ webhooks.json│
                  │                  └─────────────┘
                  ▼
         HTTP POST to endpoint
         (with optional HMAC signing)
```

1. The `WebhookDispatcher` subscribes to all events (`*`) on the shared
   `EventBus`.
2. When an event arrives, it queries the `WebhookRegistry` for webhooks
   whose event patterns match the topic.
3. For each match, an async delivery task is created (non-blocking to the
   bus).
4. Delivery includes HMAC signing, retry with backoff, and auto-disable
   on persistent failure.

### Registering a Webhook

```bash
curl -X POST http://127.0.0.1:8741/api/v1/webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://hooks.slack.com/services/T00/B00/xxxx",
    "events": ["human.decision_needed", "task.*"],
    "secret": "my-hmac-secret"
  }'
```

### Event Pattern Matching

Patterns use glob-style matching via `fnmatch`:

| Pattern | Matches |
|---|---|
| `step.completed` | Exactly `step.completed` |
| `step.*` | `step.completed`, `step.failed`, `step.dispatched` |
| `gate.*` | `gate.required`, `gate.passed`, `gate.failed` |
| `human.*` | `human.decision_needed`, `human.decision_resolved` |
| `task.*` | `task.started`, `task.completed`, `task.failed` |
| `*` | All events |

### Payload Formats

#### Generic Format (default)

All events are delivered as JSON using the `Event.to_dict()` shape:

```json
{
  "event_id": "a1b2c3d4e5f6",
  "timestamp": "2026-03-24T10:01:00+00:00",
  "topic": "step.completed",
  "task_id": "task-abc123",
  "sequence": 2,
  "payload": {
    "step_id": "0.0",
    "agent": "backend-engineer--python",
    "status": "complete"
  }
}
```

#### Slack Format

For `human.decision_needed` events and endpoints containing `slack.com` or
`hooks.slack` in the URL, a Slack Block Kit payload is sent:

```json
{
  "text": "Decision required for task `task-abc123`: gate_approval",
  "blocks": [
    {
      "type": "header",
      "text": {"type": "plain_text", "text": "Decision Required: gate_approval"}
    },
    {
      "type": "section",
      "text": {"type": "mrkdwn", "text": "Test gate failed. Approve to continue?"}
    },
    {
      "type": "section",
      "text": {"type": "mrkdwn", "text": "*Available options:*\n- `approve`\n- `reject`"}
    },
    {"type": "divider"},
    {
      "type": "context",
      "elements": [
        {"type": "mrkdwn", "text": "*Task:* `task-abc123` | *Request:* `dec-001` | *At:* 2026-03-24T10:30:00+00:00"}
      ]
    },
    {
      "type": "actions",
      "elements": [
        {"type": "button", "text": {"type": "plain_text", "text": "approve"}, "value": "dec-001::approve", "action_id": "decision_0"},
        {"type": "button", "text": {"type": "plain_text", "text": "reject"}, "value": "dec-001::reject", "action_id": "decision_1"}
      ]
    }
  ]
}
```

### HMAC Signature Verification

When a webhook is registered with a `secret`, every delivery includes an
`X-Baton-Signature` header containing the HMAC-SHA256 hex digest of the
request body, computed with the secret as key.

**Verification in Python:**

```python
import hashlib
import hmac

def verify_webhook(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

### Delivery Headers

Every webhook delivery includes:

| Header | Value |
|---|---|
| `Content-Type` | `application/json` |
| `User-Agent` | `AgentBaton-Webhook/1.0` |
| `X-Baton-Event` | Event topic (e.g. `step.completed`) |
| `X-Baton-Event-Id` | Unique event identifier |
| `X-Baton-Signature` | HMAC-SHA256 hex digest (only when `secret` is set) |

### Retry Behavior

Deliveries that fail (non-2xx response or network error) are retried with
exponential backoff:

| Attempt | Wait before retry |
|---|---|
| 1st failure | 5 seconds |
| 2nd failure | 30 seconds |
| 3rd failure | 300 seconds (5 minutes) |

After exhausting all 3 attempts, the webhook's `consecutive_failures`
counter is incremented. The counter resets to 0 on any successful delivery.

### Auto-Disable

After **10 consecutive delivery failures**, the webhook is automatically
disabled (`enabled: false`). Disabled webhooks are still listed in
`GET /webhooks` but do not receive events. To re-enable, delete and
re-register the webhook.

### Failure Logging

Failed delivery attempts are appended to `webhook-failures.jsonl` in the
team-context directory. Each line is a JSON object:

```json
{
  "timestamp": "2026-03-24T10:05:00+00:00",
  "webhook_id": "a1b2c3d4e5f67890",
  "url": "https://example.com/hook",
  "event_id": "f6e5d4c3b2a1",
  "event_topic": "step.completed",
  "task_id": "task-abc123",
  "attempt": 1
}
```

### HTTP Timeout

Each delivery attempt has a 10-second timeout. Connections that exceed this
timeout are treated as failures and trigger the retry logic.

---

## 6. Request and Response Models

All models are Pydantic `BaseModel` subclasses. Field constraints are
enforced at deserialization time and validation errors are returned as
`422 Unprocessable Entity` with detailed error messages.

### Request Models

| Model | Endpoint | Description |
|---|---|---|
| `CreatePlanRequest` | `POST /plans` | Plan generation parameters |
| `StartExecutionRequest` | `POST /executions` | Execution start (plan_id or inline plan) |
| `RecordStepRequest` | `POST /executions/{id}/record` | Step outcome recording |
| `RecordGateRequest` | `POST /executions/{id}/gate` | Gate result recording |
| `ResolveDecisionRequest` | `POST /decisions/{id}/resolve` | Decision resolution |
| `RegisterWebhookRequest` | `POST /webhooks` | Webhook registration |
| `RegisterProjectRequest` | `POST /pmo/projects` | PMO project registration |
| `CreateForgeRequest` | `POST /pmo/forge/plan` | Forge plan creation |
| `ApproveForgeRequest` | `POST /pmo/forge/approve` | Forge plan approval |
| `InterviewRequest` | `POST /pmo/forge/interview` | Interview question generation |
| `RegenerateRequest` | `POST /pmo/forge/regenerate` | Plan regeneration with answers |
| `CreateSignalRequest` | `POST /pmo/signals` | Signal creation |

### Response Models

| Model | Description |
|---|---|
| `HealthResponse` | Liveness probe result |
| `ReadyResponse` | Readiness probe result |
| `PlanResponse` | Full plan with phases, steps, and gates |
| `PlanPhaseResponse` | A phase grouping steps and an optional gate |
| `PlanStepResponse` | A single step within a plan phase |
| `PlanGateResponse` | A QA gate attached to a phase |
| `ExecutionResponse` | Current execution state |
| `ActionResponse` | Engine instruction (dispatch, gate, complete, wait) |
| `StepResultResponse` | Outcome of a completed step |
| `DecisionResponse` | A pending or resolved human decision |
| `DecisionListResponse` | List wrapper for decisions |
| `ResolveResponse` | Decision resolution confirmation |
| `DashboardResponse` | Dashboard markdown + metrics |
| `TraceResponse` | Structured execution trace |
| `TraceEventResponse` | Single event within a trace |
| `UsageResponse` | Usage records + summary |
| `TaskUsageResponse` | Task-level usage record |
| `AgentUsageResponse` | Per-agent usage within a task |
| `AgentResponse` | Agent definition from registry |
| `AgentListResponse` | List wrapper for agents |
| `WebhookResponse` | Registered webhook confirmation |
| `ErrorResponse` | Standard error body |
| `PmoBoardResponse` | Full Kanban board with health |
| `PmoCardResponse` | Single Kanban card |
| `PmoProjectResponse` | Registered PMO project |
| `PmoSignalResponse` | Signal (bug/escalation/blocker) |
| `ProgramHealthResponse` | Aggregate program health metrics |
| `InterviewResponse` | Interview questions for plan refinement |
| `InterviewQuestionResponse` | Single interview question |
| `AdoSearchResponse` | ADO work item search results |
| `AdoWorkItemResponse` | Single ADO work item |

---

## 7. CORS Configuration

CORS is configured via `CORSMiddleware` added before authentication so
pre-flight `OPTIONS` requests are answered without requiring a token.

### Default Configuration

By default, only localhost origins are permitted using a regex pattern:

```
https?://(localhost|127\.0\.0\.1)(:\d+)?
```

This allows any port on `localhost` or `127.0.0.1` (e.g.
`http://localhost:3000`, `http://localhost:5173` for the PMO UI dev server).

### Custom Origins

The `create_app()` factory accepts an `allowed_origins` parameter:

| Value | Effect |
|---|---|
| `None` (default) | Localhost/127.0.0.1 on any port |
| `[]` (empty list) | Same as `None` (falls back to regex) |
| `["*"]` | Allow all origins |
| `["https://app.example.com"]` | Allow only the specified origin(s) |

### Allowed Methods and Headers

All HTTP methods (`*`) and all headers (`*`) are permitted.
`allow_credentials` is set to `True`.

---

## 8. Error Handling

### Error Response Format

All error responses use FastAPI's standard JSON format:

```json
{
  "detail": "Human-readable error message describing what went wrong."
}
```

For validation errors (422), the response includes field-level details:

```json
{
  "detail": [
    {
      "loc": ["body", "description"],
      "msg": "String should have at least 1 character",
      "type": "string_too_short"
    }
  ]
}
```

### Common HTTP Status Codes

| Status | Meaning | When returned |
|---|---|---|
| `200` | OK | Successful GET, POST (non-creation), DELETE |
| `201` | Created | Successful resource creation (plans, executions, webhooks, signals) |
| `204` | No Content | Successful deletion with no response body (e.g. `DELETE /pmo/projects`) |
| `400` | Bad Request | Invalid request body, validation failure, or business rule violation |
| `401` | Unauthorized | Missing or invalid Bearer token |
| `404` | Not Found | Resource does not exist |
| `409` | Conflict | Concurrent modification prevented the operation |
| `422` | Unprocessable Entity | Pydantic validation failure (automatic from FastAPI) |
| `500` | Internal Server Error | Unexpected server-side failure |

### Standard Error Model

The `ErrorResponse` model is defined for documentation purposes:

| Field | Type | Description |
|---|---|---|
| `error` | string | Short error classification |
| `detail` | string | Additional context about the error (nullable) |

---

## 9. Rate Limiting and Performance

### Rate Limiting

The API does not currently implement rate limiting. It is designed for
local-network use where the primary consumers are the PMO UI dashboard and
CI/CD integrations. If the server is exposed to a wider network, consider
adding rate limiting via a reverse proxy (e.g. nginx, Caddy).

### Performance Considerations

- **Usage endpoint**: The `GET /usage` endpoint reads all records from the
  JSONL usage log and filters in-memory. For large logs this may be slow.
  A future version may add cursor-based pagination.

- **Webhook delivery**: Deliveries are async tasks scheduled on the event
  loop. The bus handler returns immediately so it does not block event
  propagation. Retry backoffs (up to 5 minutes) run in the background.

- **SSE connections**: Each SSE client creates a bus subscription and an
  `asyncio.Queue`. A large number of concurrent SSE clients will consume
  memory proportional to the event rate. The 30-second keepalive ensures
  idle connections are not dropped by proxies.

- **Dashboard generation**: The `GET /dashboard` endpoint generates the
  full dashboard markdown on every request. For dashboards with large
  usage histories, consider caching the response.

- **Webhook registry**: The `webhooks.json` file is re-read from disk on
  every event publication (via `WebhookRegistry.match()`). This ensures
  multi-process consistency but may be slow with a large number of webhooks.

### Dependency: httpx

Webhook delivery requires the `httpx` package. If it is not installed,
webhook deliveries will fail with a logged error. Install with:

```bash
pip install httpx
```

### Dependency: sse-starlette

The SSE event streaming endpoint requires the `sse-starlette` package. If
it is not installed, the `/events/{task_id}` route is skipped during server
startup (with a warning) and all other routes remain available.

```bash
pip install sse-starlette
```
