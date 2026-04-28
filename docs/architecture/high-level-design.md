# High-Level Design

> **Audience.** Engineers and operators who want a system-wide picture
> of Agent Baton: components, data flow, deployment topology, external
> dependencies. Read this first if you're new to the project. For the
> design philosophy, see [../architecture.md](../architecture.md). For
> internal patterns, see
> [technical-design.md](technical-design.md).

---

## 1. System overview

Agent Baton is a Python orchestration engine that drives Claude Code
subagents through structured execution plans. A single `agent_baton/`
package provides three coequal interfaces — CLI, HTTP API, and a React
PMO frontend — over a shared engine and storage layer.

```
+--------------------+   +--------------------+   +-----------------+
|  Claude Code +     |   |  HTTP clients      |   |  PMO Frontend   |
|  orchestrator      |   |  (curl, scripts,   |   |  (browser)      |
|  agent             |   |   webhooks)        |   |                 |
+----------+---------+   +----------+---------+   +--------+--------+
           | stdout                  | HTTPS                | HTTPS
           | + subprocess            | (Bearer token)       | (cookie /
           | (ACTION: ...)           |                      |  Bearer)
           v                         v                      v
+-------------------------------------------------------------------+
|                          baton CLI / API server                    |
|  cli/ (49 cmds) | api/ (FastAPI, 64 endpoints) | pmo-ui/dist/      |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|                       Python execution engine                      |
|  engine/ executor planner dispatcher gates persistence beads ...   |
|  runtime/ worker supervisor scheduler launchers headless           |
|  events/  bus persistence projections                              |
+-------------------------------------------------------------------+
        |                    |                       |
        v                    v                       v
   .claude/team-context/   ~/.baton/             external services
   baton.db (project)      central.db            - Anthropic API
   plan.json / .md         pmo data              - GitHub / GitLab
   traces/  events/        users + audit         - Azure DevOps
   retrospectives/         + 28 synced tables    - PagerDuty
                                                  - webhook targets
```

The engine is synchronous and stateless between CLI calls. State lives
on disk. The runtime layer wraps the engine in `asyncio` for daemon mode
and parallel dispatch.

---

## 2. Components

### 2.1 Distribution unit

The whole project ships as **one Python package** plus three sets of
distributable assets:

| Artifact | Location | Purpose |
|----------|----------|---------|
| `agent_baton/` Python package | `pyproject.toml` editable install | The engine, CLI, API, and bundled agents |
| `agents/` markdown | Installed to `~/.claude/agents/` by `scripts/install.sh` | 47 agent definitions |
| `references/` markdown | Installed to `~/.claude/references/` | 16 procedure references |
| `templates/` | Installed to project's `.claude/` | `CLAUDE.md` + `settings.json` + skills |
| `pmo-ui/dist/` | Built and served at `/pmo/` by FastAPI | React PMO frontend |

The `baton` console-script is registered by `pyproject.toml`.

### 2.2 Component map

| Component | Type | Where |
|-----------|------|-------|
| `baton` CLI | Console script | [`agent_baton/cli/main.py`](../../agent_baton/cli/main.py) |
| HTTP API | FastAPI app | [`agent_baton/api/server.py`](../../agent_baton/api/server.py) |
| PMO frontend | React/Vite SPA | [`pmo-ui/`](../../pmo-ui/) |
| Execution engine | State machine | [`agent_baton/core/engine/executor.py`](../../agent_baton/core/engine/executor.py) |
| Async runtime | `asyncio` driver | [`agent_baton/core/runtime/worker.py`](../../agent_baton/core/runtime/worker.py) |
| Per-project store | SQLite + JSON | `.claude/team-context/baton.db` (+ legacy JSON) |
| Federated store | SQLite (read replica) | `~/.baton/central.db` |
| Sync engine | Watermarked one-way | [`agent_baton/core/storage/sync.py`](../../agent_baton/core/storage/sync.py) |
| Event bus | In-process pub/sub | [`agent_baton/core/events/bus.py`](../../agent_baton/core/events/bus.py) |
| Webhook dispatcher | EventBus subscriber | [`agent_baton/api/webhooks/dispatcher.py`](../../agent_baton/api/webhooks/dispatcher.py) |
| Headless Claude | Subprocess wrapper | [`agent_baton/core/runtime/headless.py`](../../agent_baton/core/runtime/headless.py) |
| Daemon supervisor | UNIX double-fork + signal handling | [`agent_baton/core/runtime/supervisor.py`](../../agent_baton/core/runtime/supervisor.py) |

For the full module-by-module map see
[package-layout.md](package-layout.md).

---

## 3. Data flow

A single user task touches every layer. Below is the full path from
"user types a sentence" to "PMO board shows merged".

### 3.1 Plan creation

```
user: baton plan "add OAuth2 to /login" --save --explain
                         |
                         v
            cli/commands/execution/plan_cmd.py
                         |
                         v
                 IntelligentPlanner
                         |
                         | reads:
                         |   AgentRegistry  (list of agents)
                         |   StackProfile   (package.json, pyproject.toml ...)
                         |   PatternLearner (historical patterns)
                         |   BudgetTuner    (token-tier history)
                         |   PerformanceScorer (agent health)
                         |   PolicyEngine   (guardrail rules)
                         |   KnowledgeRegistry (knowledge packs)
                         |   BeadAnalyzer   (historical beads)
                         |   FallbackClassifier (Haiku → keyword)
                         |   LearnedOverrides   (auto-applied corrections)
                         v
                    MachinePlan
                         |
                         v
            plan.json + plan.md → .claude/team-context/
            plans table → baton.db
```

### 3.2 Execution

```
user/Claude: baton execute start
                         |
                         v
              ExecutionEngine.start(plan)
                         |
                         v
              ExecutionState (in memory + disk)
                         |
                         v
              loop: next_action() → ActionType
                         |
              +----------+-----------+-------------+----------+
              |          |           |             |          |
              v          v           v             v          v
         DISPATCH      GATE      APPROVAL    SWARM_DISP.  FEEDBACK
         (Claude       (Claude   (human       (Reconciler  (multiple-
          spawns        runs      decides      runs)       choice)
          agent via     check)
          Agent tool)
              |          |           |             |          |
              +----+-----+-----+-----+------+------+--+-------+
                   |           |            |         |
                   v           v            v         v
                record_step record_gate record_appr.  record_fdbk
                   |           |            |         |
                   +-----------+------------+---------+
                               |
                               v
                       ExecutionState saved
                       (SQLite + JSON, atomic)
                       + EventBus published
                       + bead signals parsed
                       + knowledge gaps parsed
                               |
                               v
                       (loop until COMPLETE/FAILED)
                               |
                               v
                       baton execute complete
                               |
                               v
                  RetrospectiveEngine + UsageLogger
                  + TraceRecorder.write()
                  + auto_sync_current_project()
                       (best-effort, never blocks)
```

For per-action transitions see
[state-machine.md](state-machine.md). For the executor's
inner loop see
[technical-design.md](technical-design.md).

### 3.3 Federation

Per-project `baton.db` is the only write target during execution.
Cross-project visibility is provided by `central.db`:

```
project A baton.db
project B baton.db   --baton sync (or auto on complete)-->  ~/.baton/central.db
project C baton.db
                                                                    |
                                                                    v
                                                     PMO board (KanbanBoard)
                                                     baton query "..."
                                                     baton pmo status
                                                     CentralStore.fetch_*()
```

**Invariant.** No engine code writes to `central.db`. The lone writer is
[`SyncEngine`](../../agent_baton/core/storage/sync.py). Sync is
**watermarked at the row level** (not file-level), idempotent, and one-way.

28 project-scoped tables are mirrored; each row carries a `project_id`
prefix. `central.db` adds 6 cross-project views
(`v_agent_reliability`, `v_cost_by_task_type`,
`v_recurring_knowledge_gaps`, `v_project_failure_rate`,
`v_cross_project_discoveries`, `v_external_plan_mapping`).

### 3.4 Event flow

Every state mutation publishes a domain event to `EventBus`. The bus has
two production subscribers and one optional one:

```
  ExecutionEngine          TaskWorker
       |                       |
       | publish               | publish
       |   task.started        |   step.dispatched
       |   task.completed      |   step.completed
       |   phase.started       |   step.failed
       |   gate.passed         |
       |   bead.created        |
       v                       v
                EventBus
        (in-process, glob-routed)
                   |
       +-----------+-----------+--------------------+
       |           |           |                    |
       v           v           v                    v
  EventPersist.  TaskView   WebhookDispatcher  AgentTelemetry
  (jsonl log)    Subscriber (HMAC-signed       (catch-all,
                 (projection) HTTP delivery)    optional)
```

The 19 event topics and their factories are listed in
[`core/events/events.py`](../../agent_baton/core/events/events.py).
EventBus topic ownership between engine and worker is recorded in
ADR-04.

---

## 4. Deployment model

### 4.1 Three deployment modes

| Mode | Driving session | When |
|------|-----------------|------|
| **CLI mode** | A live Claude Code session | Default. Claude reads `_print_action()` output and calls `baton execute *` between Agent-tool spawns. |
| **Daemon mode** | `WorkerSupervisor` background process | `baton daemon start [--serve]`. The async `TaskWorker` drives dispatch via `ClaudeCodeLauncher` (subprocess invocations of `claude --print`). API server can be co-started with `--serve`. |
| **Headless mode** | A single `claude --print` subprocess | `baton execute run`. Drives the full `start → dispatch → record → gate → complete` loop with no interactive Claude session. Used by the PMO Forge and the `/pmo/execute` endpoint. |

All three modes use the same `ExecutionEngine`. They differ only in
*who calls* it.

### 4.2 Process topology

A typical workstation install:

```
~/.claude/                  ← global agents, knowledge, settings
~/.baton/
  central.db                ← federated read replica
  .pmo-migrated             ← one-time migration marker
  identity.yaml             ← optional tenancy attribution

<each project>/
  .claude/
    agents/                 ← project-scoped agent overrides
    knowledge/              ← project-scoped knowledge packs
    references/             ← project-scoped reference docs
    skills/                 ← installed by scripts/install.sh
    settings.json           ← merged on install
    CLAUDE.md               ← project orchestration rules
    team-context/
      baton.db              ← per-project store (write target)
      plan.json / plan.md   ← legacy state
      execution-state.json  ← legacy state
      executions/<task_id>/ ← namespaced state directories
      traces/               ← per-task DAG dumps
      retrospectives/       ← post-task reports
      usage-log.jsonl       ← token / cost log
      telemetry.jsonl       ← tool-call telemetry
      events/               ← per-task event JSONL
      webhooks.json         ← webhook subscriptions
      learned-overrides.json ← auto-applied corrections
      worktrees/            ← Wave 1.3 per-step git worktrees
  baton.yaml                ← optional project config
```

The daemon (when enabled) writes `daemon.pid` and `daemon.log`
(or `worker.pid`/`worker.log` in namespaced mode) into the team-context
directory.

### 4.3 Service topology (when running as a server)

```
        Bearer-token clients          browser (cookie+Bearer)
                |                          |
                v                          v
          +---------------------------------------+
          |   FastAPI app (uvicorn or supervisor) |
          |  - CORS middleware                    |
          |  - TokenAuthMiddleware                |
          |  - UserIdentityMiddleware             |
          |                                       |
          |  Routes:                              |
          |    /api/v1/health, /ready  (auth-exempt)
          |    /api/v1/plans (2)                  |
          |    /api/v1/executions (6)             |
          |    /api/v1/agents (2)                 |
          |    /api/v1/observe (3)                |
          |    /api/v1/decisions (3)              |
          |    /api/v1/events (1, SSE)            |
          |    /api/v1/webhooks (3)               |
          |    /api/v1/pmo (36)                   |
          |    /api/v1/pmo/* (H3 endpoints, 6)    |
          |    /api/v1/learn (5)                  |
          |    /pmo/  (StaticFiles → pmo-ui/dist) |
          +-------------------+-------------------+
                              |
                              v
                       Engine + storage
```

Authentication is Bearer-token via `TokenAuthMiddleware` (no-op when
the configured token is `None`). Identity is resolved by
`UserIdentityMiddleware` from `X-Baton-User`, the Bearer token claim,
or `"local-user"` fallback.

The `BATON_APPROVAL_MODE` environment variable controls who may approve
gates: `local` permits self-approval; `team` requires a different
reviewer (recorded in `approval_log` in `central.db`).

---

## 5. External dependencies

The engine is **deliberately offline-capable**. It does not require
network access except for the optional integrations below.

### 5.1 Required runtime

| Dependency | Used by | Notes |
|-----------|---------|-------|
| Python ≥ 3.10 | Everything | Type hints, `match` statements |
| `sqlite3` | Storage | Standard library |
| `asyncio` | Runtime | Standard library |
| `argparse` | CLI | Standard library |
| `fastapi`, `uvicorn`, `pydantic` | API | `pip install agent-baton[api]` (stub label — see `pyproject.toml`) |

### 5.2 Optional integrations

| Dependency | Trigger | Failure mode |
|-----------|---------|--------------|
| `claude` CLI | `ClaudeCodeLauncher`, `HeadlessClaude`, `HaikuClassifier` | Engine falls back to `KeywordClassifier`; daemon dispatch fails per-step. |
| `gh` CLI | CI gate (`CIGateRunner`) | Returns `passed=False` with conclusion `gh_unavailable`. |
| Anthropic API key (`ANTHROPIC_API_KEY`) | Haiku classifier, `claude --print` | Same as missing `claude` CLI. |
| `sse-starlette` | `/api/v1/events` SSE route | Route is skipped at registration time with a warning; rest of API works. |
| GitHub PR creation (`gh pr create`) | PMO `/cards/{id}/create-pr` | Endpoint returns 502; manual merge still works. |
| Azure DevOps PAT (env var) | `AdoAdapter` | `baton source sync` fails for that source; others continue. |

There is no required external network egress. A fully offline workstation
runs the full state machine, dry-run launchers, gate evaluation, and PMO
UI.

### 5.3 Operating-system assumptions

- **POSIX preferred.** `daemonize()` uses double-fork; `SignalHandler`
  uses `SIGTERM` / `SIGINT`. The CLI works on Windows; daemon mode is
  POSIX-only.
- **Atomic file writes.** `Path.replace()` is used for tmp+rename.
  On Windows the persistence layer retries `Path.replace()` up to 5×
  with 50 ms backoff to tolerate antivirus / search-indexer file holds
  ([`persistence.py:97`](../../agent_baton/core/engine/persistence.py)).
- **`git`** is required for git-strategy enforcement, worktree isolation
  (Wave 1.3), and the dispatch verifier. The engine never calls `git`
  unless the plan declares a git strategy.

---

## 6. Configuration surface

Configuration is **file-based, not environment-based**, with a small
set of env-var overrides for ops needs.

### 6.1 Files

| File | Owner | Purpose |
|------|-------|---------|
| `.claude/agents/*.md` | User | Agent definitions (frontmatter + body) |
| `.claude/knowledge/*/knowledge.yaml` | User | Knowledge pack manifests |
| `~/.claude/settings.json` | User (merged on install) | Claude Code global settings |
| `.claude/settings.json` | User (merged on install) | Per-project Claude Code settings |
| `baton.yaml` | User | Optional [`ProjectConfig`](../../agent_baton/core/config/project_config.py): `default_agents`, `default_gates`, `default_isolation`, `auto_route_rules`, `excluded_paths` |
| `~/.baton/identity.yaml` | Operator | F0.2 tenancy attribution (org/team/user/cost_center) |
| `.claude/team-context/learned-overrides.json` | Auto-written | Auto-applied learning corrections |
| `.claude/team-context/webhooks.json` | API-managed | Webhook subscriptions |

### 6.2 Environment variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `BATON_TASK_ID` | Bind a shell session to a specific task | unset |
| `BATON_APPROVAL_MODE` | `local` (self-approve OK) or `team` (different reviewer required) | `local` |
| `BATON_DB_PATH` | Override per-project `baton.db` location (subagents in worktrees can also rely on upward-walk discovery) | discovered |
| `BATON_OTEL_ENABLED` | Enable OTLP-shaped JSONL spans at three engine call sites | unset |
| `BATON_OTEL_PATH` | Destination JSONL for OTel spans | `.claude/team-context/otel-spans.jsonl` |
| `BATON_WORKTREE_ENABLED` | Set to `0` to disable Wave 1.3 worktree isolation | `1` |
| `BATON_TAKEOVER_ENABLED` | Wave 5.1 takeover | `1` |
| `BATON_SELFHEAL_ENABLED` | Wave 5.2 model-tier escalation | `0` |
| `BATON_SPECULATE_ENABLED` | Wave 5.3 speculative execution | `0` |
| `ANTHROPIC_API_KEY` | Required for `claude` CLI and Haiku classifier | unset |
| `BATON_ORG_ID`, `BATON_TEAM_ID`, `BATON_USER_ID`, `BATON_COST_CENTER` | F0.2 tenancy attribution overrides | from `identity.yaml` |

The full list lives in the project-root [`CLAUDE.md`](../../CLAUDE.md).

---

## 7. Failure and degradation model

The engine is built to **never fail completely** because of an optional
subsystem. Concretely:

| Failure | Effect |
|---------|--------|
| `claude` CLI missing | Plan creation falls back to keyword classification; dispatch fails per-step |
| `central.db` inaccessible | Auto-sync logs a warning; execution continues |
| `sse-starlette` missing | `/api/v1/events` route is skipped at startup; other routes register |
| `pmo-ui/dist/` missing | StaticFiles mount is skipped; API still works |
| SQLite write failure | Engine falls back to file persistence and logs a warning |
| Webhook delivery failure | Retried `[5s, 30s, 300s]`; auto-disabled after 10 consecutive failures; recorded in `webhook-failures.jsonl` |
| Gate failure | Phase retried up to `_max_gate_retries=3`; otherwise execution fails terminally |
| Crash mid-execution | `baton execute resume` rebuilds state from disk and continues |
| Corrupted state file | `StatePersistence.load()` returns `None` and logs a warning; caller sees "no state" |

Sync, retrospective, telemetry, and webhook failures are wrapped in
`try/except` at the call site. They emit a log warning, never raise to
the caller.

---

## 8. Security posture

| Concern | Control |
|---------|---------|
| API auth | Bearer token via `TokenAuthMiddleware` (auth-exempt: `/health`, `/ready`, `/openapi.json`, `/docs`, `/redoc`) |
| Approval audit | `users` + `approval_log` tables in `central.db`; populated by `UserIdentityMiddleware` |
| API key handling | `ClaudeCodeLauncher` uses an explicit env whitelist and exec-only subprocess; stderr redaction in `core/runtime/_redaction.py` |
| Path enforcement | `PromptDispatcher` injects bash guards reflecting `allowed_paths` / `blocked_paths`; post-hoc verification by `DispatchVerifier` |
| Worktree isolation | Wave 1.3 — each step runs in `.claude/worktrees/<task_id>/<step_id>/` |
| Webhook signing | HMAC-SHA256 over JSON payload when secret is configured |
| Package extraction | Path-traversal protection in `PackageBuilder` |
| Compliance audit | Hash-chained `compliance-audit.jsonl` (F0.3) for HIGH/CRITICAL phase overrides |

The `auditor` agent and the `Regulated Data` guardrail preset are
**mandatory** for tasks touching regulated/audit-controlled records.

---

## 9. Observability surface

Three layers of observability ship by default:

1. **Per-task trace** — JSON file under `.claude/team-context/traces/<task_id>.json`, written at `complete()`. Renderable via `baton trace`.
2. **Usage log** — JSONL at `.claude/team-context/usage-log.jsonl`. Each `TaskUsageRecord` carries agent names, models, token counts, retries, gate results, duration.
3. **Event log** — JSONL at `.claude/team-context/events/<task_id>.jsonl`, written by `EventPersistence` as a bus subscriber. Replayable.

Optional layers:

- **OTLP-shaped JSONL spans** at `.claude/team-context/otel-spans.jsonl` (env-gated by `BATON_OTEL_ENABLED`) — emitted at `Planner.create_plan` (`plan.create`), `ExecutionEngine.record_step_result` (`step.dispatch`), and `ExecutionEngine.record_gate_result` (`gate.run`).
- **Prometheus exposition** — `agent_baton/core/observability/prometheus.py`.
- **PagerDuty** — `agent_baton/core/observe/pagerduty.py`.
- **Webhook subscribers** — HMAC-signed HTTP delivery via `WebhookDispatcher`.

For the F0.2 cost-attribution layer (chargeback by org/team/project/user)
see [../finops-chargeback.md](../finops-chargeback.md).

---

## 10. What this document does *not* cover

- Per-action state transitions → [state-machine.md](state-machine.md)
- Module-by-module package map → [package-layout.md](package-layout.md)
- Planner/dispatcher/executor patterns → [technical-design.md](technical-design.md)
- HTTP endpoint surface → [../api-reference.md](../api-reference.md)
- CLI flag/option reference → [../cli-reference.md](../cli-reference.md)
- Knowledge subsystem internals → [../governance-knowledge-and-events.md](../governance-knowledge-and-events.md)
- Storage and sync internals → [../storage-sync-and-pmo.md](../storage-sync-and-pmo.md)
- Learning and improvement loop → [../observe-learn-and-improve.md](../observe-learn-and-improve.md)
- Engine + runtime in long form → [../engine-and-runtime.md](../engine-and-runtime.md)
- Vocabulary → [../terminology.md](../terminology.md)
