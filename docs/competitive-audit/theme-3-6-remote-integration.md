# Competitive Audit: Theme 3 (Remote & Headless) and Theme 6 (Integration & Extensibility)

**Auditor**: Claude Opus 4.6  
**Date**: 2026-04-16  
**Codebase revision**: `feat/actiontype-interact` branch  

---

## Summary Table

| Story | Title | Rating | Key Gap |
|-------|-------|--------|---------|
| 3.1 | Headless Execution on Remote VMs | **PARTIALLY MET** | No Dockerfile, no horizontal scaling, no standalone health endpoint outside API server |
| 3.2 | Crash Recovery with Context Reconstruction | **PARTIALLY MET** | SQLite + file-based recovery exists; no cross-machine portability, no deterministic reconstruction guarantee |
| 3.3 | Parallel Execution with Git Worktree Isolation | **NOT MET** | No worktree code exists in agent_baton; no merge-back, no conflict handling |
| 3.4 | API-Driven Execution Triggering | **PARTIALLY MET** | POST /plans and /executions exist with Bearer auth; no webhook subscription from API, no standalone OpenAPI spec file |
| 3.5 | Multi-Day Workflow Support | **PARTIALLY MET** | Pause/resume via persisted state exists; no explicit pause command, no timeline view, no checkpoint frequency config |
| 3.6 | Resource Governance and Quotas | **MINIMALLY MET** | ResourceLimits model + token budget warnings exist; no daily spend limit, no per-task cost cap enforcement, no circuit breakers, no emergency stop, no quota CLI |
| 6.1 | CI Pipeline Integration as Gate | **NOT MET** | Gates are shell commands only; no CI provider integration, no webhook-based blocking |
| 6.2 | Custom Agent Creation via Talent Builder | **PARTIALLY MET** | talent-builder.md agent definition exists with comprehensive instructions; no programmatic API, no test scaffolding, no base class inheritance |
| 6.3 | Webhook-Driven External Notifications | **PARTIALLY MET** | API webhook CRUD + HMAC signing + retry + Slack Block Kit exist; no CLI command, no Teams format, no dead letter queue |
| 6.4 | Structured Handoff Context Between Phases | **PARTIALLY MET** | Handoff from previous step output + resolved decisions + beads injected into delegation prompts; no auto-generated handoff documents, no git diff/gate results in handoff, not queryable |
| 6.5 | Exportable Audit Reports | **NOT MET** | No `baton export` command; no PDF/CSV generation; traces and retrospectives exist as JSON/JSONL but are not formatted for export |
| 6.6 | Plugin Architecture for Gate Types | **NOT MET** | GateRunner has fixed gate types (test/build/lint/spec/review); no plugin interface, no `baton plugin install`, no sandboxed execution |

---

## Detailed Evidence

---

### Story 3.1 -- Headless Execution on Remote VMs

**Rating: PARTIALLY MET**

#### What Exists

**`baton daemon start` -- fully implemented:**
- CLI command at `agent_baton/cli/commands/execution/daemon.py` (lines 27-497)
- Subcommands: `start`, `status`, `stop`, `list`
- Flags: `--plan`, `--max-parallel`, `--dry-run`, `--foreground`, `--resume`, `--project-dir`, `--serve`, `--port`, `--host`, `--token`, `--task-id`

**Agent dispatch via `claude --print` -- fully implemented:**
- `ClaudeCodeLauncher` at `agent_baton/core/runtime/claude_launcher.py` (lines 170-606) launches `claude --print --model MODEL --output-format json` as async subprocess
- `HeadlessClaude` at `agent_baton/core/runtime/headless.py` (lines 93-453) provides synchronous-style plan generation via same mechanism
- Security invariants: whitelisted environment, no shell interpolation, API key redaction, binary validation at construction time

**Autonomous gate processing -- fully implemented:**
- `TaskWorker` at `agent_baton/core/runtime/worker.py` (lines 329-395) auto-approves programmatic gates (test/build/lint/spec) and routes human-required gates through `DecisionManager`
- `baton execute run` at `agent_baton/cli/commands/execution/execute.py` (lines 883-1074) drives the full loop: start -> dispatch -> gate -> complete using shell subprocesses for gates

**Graceful shutdown -- implemented:**
- `WorkerSupervisor._run_with_signals()` at `agent_baton/core/runtime/supervisor.py` (lines 180-209): installs SIGTERM/SIGINT handlers via `SignalHandler`, 30-second drain timeout
- PID file locking via `flock()` at supervisor.py lines 347-363 prevents duplicate daemons

**Health endpoint -- implemented (but only via API server):**
- `GET /api/v1/health` at `agent_baton/api/routes/health.py` (lines 30-37): returns status + version + uptime
- `GET /api/v1/ready` at same file (lines 40-59): reports daemon running state and pending decisions
- Requires `--serve` flag on `baton daemon start` to enable; no standalone health probe for daemon-only mode

**UNIX daemonization -- implemented:**
- Double-fork at `agent_baton/core/runtime/daemon.py` (lines 16-77)
- Proper stdio redirect to /dev/null, preserves working directory

**Combined daemon + API mode -- implemented:**
- `_run_daemon_with_api()` at daemon.py CLI (lines 109-267): runs TaskWorker and uvicorn server concurrently sharing a single EventBus
- SSE event streaming at `agent_baton/api/routes/events.py` for real-time monitoring

#### What Is Missing

- **No Dockerfile**: `find` returns no Dockerfile anywhere in the repository. There is no container-ready packaging.
- **No horizontal scaling**: Each daemon is single-process. No clustering, work distribution, or shared queue mechanism. `ResourceLimits.max_concurrent_executions` model exists but is not enforced by a multi-node scheduler.
- **Standalone health probe in daemon-only mode**: The health endpoint requires `--serve` to be active; a daemon running without `--serve` has no health check interface.

---

### Story 3.2 -- Crash Recovery with Context Reconstruction

**Rating: PARTIALLY MET**

#### What Exists

**`baton execute resume` -- fully implemented:**
- CLI subcommand at `agent_baton/cli/commands/execution/execute.py` line 194
- Engine method `resume()` at `agent_baton/core/engine/executor.py` (lines 1675-1736)
- Resolution order: (1) file-based state load, (2) SQLite fallback when file is overwritten by concurrent run, (3) trace reconnection from disk

**State persistence with atomic writes:**
- `StatePersistence` at `agent_baton/core/engine/persistence.py` (lines 30-178): atomic tmp-then-rename writes, namespace support for concurrent executions
- Cross-platform: Windows retry logic for `Path.replace()` (lines 84-95)

**SQLite-backed state reconstruction:**
- When `execution-state.json` is lost, the engine reconstructs from SQLite via `self._storage.load_execution(self._task_id)` (executor.py lines 1696-1714)

**Stale dispatch recovery:**
- `recover_dispatched_steps()` at executor.py (lines 1738-1760): clears `dispatched` status markers so the engine re-dispatches them

**`baton execute run` auto-resume:**
- The `_handle_run` function (execute.py lines 883-952) checks for existing execution state and auto-resumes if found in `running` or `pending` status

#### What Is Missing

- **No deterministic reconstruction guarantee**: Recovery depends on whichever state source is available (file or SQLite). There is no formal guarantee that reconstructed state matches the pre-crash state exactly.
- **No cross-machine portability**: State files and SQLite are local to the filesystem. There is no mechanism to transfer an execution to a different machine (e.g., checkpoint export/import).
- **No partial failure detection**: The engine can detect that steps were in `dispatched` status (stale markers), but there is no detection of partially-written agent output or mid-step crashes beyond the binary dispatched/complete/failed status.
- **No bead/git context reconstruction**: Recovery loads the plan state but does not reconstruct the full context that a dispatched agent would have had (beads, git state, shared context). Beads and knowledge are re-resolved on the next dispatch, which is a form of reconstruction, but git state (uncommitted changes from a crashed agent) is not recovered.

---

### Story 3.3 -- Parallel Execution with Git Worktree Isolation

**Rating: NOT MET**

#### Evidence

- **Zero worktree references in agent_baton/**: `grep -ri worktree agent_baton/` returns no matches.
- `StepScheduler` at `agent_baton/core/runtime/scheduler.py` dispatches parallel agents via `asyncio.Semaphore` but all agents share the same working directory (`Path.cwd()`).
- `ClaudeCodeLauncher._run_once()` uses `cwd=str(self._config.working_directory or Path.cwd())` (claude_launcher.py line 558) -- same directory for all dispatches.
- There is a `.claude/worktrees/` directory in the repo (containing a `agent-a72b5fae` worktree), but this appears to be from an external tool (Superpowers), not from agent-baton's own code.

#### What Would Be Needed

- Automatic worktree creation per parallel agent dispatch
- Merge-back after gate pass
- Conflict detection and resolution
- Cleanup policy for completed worktrees

---

### Story 3.4 -- API-Driven Execution Triggering

**Rating: PARTIALLY MET**

#### What Exists

**POST /api/v1/plans -- implemented:**
- `agent_baton/api/routes/plans.py` (lines 21-61): accepts `CreatePlanRequest` with description, optional task_type, agents, project_path
- Returns `PlanResponse` (201 Created)

**POST /api/v1/executions -- implemented:**
- `agent_baton/api/routes/executions.py` (lines 35-116): accepts either `plan_id` or inline `plan` dict
- Returns execution state + first batch of dispatchable actions
- Additional endpoints: GET /executions/{task_id}, POST .../record, POST .../gate, POST .../complete, DELETE .../cancel, POST .../feedback

**Bearer token authentication -- implemented:**
- `TokenAuthMiddleware` at `agent_baton/api/middleware/auth.py` (lines 37-66)
- Token passed via `--token` flag or `BATON_API_TOKEN` environment variable
- Health/readiness probes and OpenAPI endpoints exempted from auth

**Webhook subscription (API-based) -- implemented:**
- POST /api/v1/webhooks, GET /webhooks, DELETE /webhooks/{id} at `agent_baton/api/routes/webhooks.py`
- HMAC-SHA256 signing, retry with exponential backoff, auto-disable after 10 failures

**SSE event streaming -- implemented:**
- GET /api/v1/events/{task_id} at `agent_baton/api/routes/events.py` (lines 35-125)
- Event replay for late-connecting clients, 30-second keepalive

**Auto-generated OpenAPI spec -- partially implemented:**
- FastAPI auto-generates `/openapi.json`, `/docs`, `/redoc` (server.py lines 120-132)
- No standalone OpenAPI spec file committed to the repo; generated at runtime only

#### What Is Missing

- **No standalone OpenAPI spec file**: The spec is auto-generated by FastAPI at runtime but not exported as a versioned file.
- **Webhook subscription from the API (for inbound triggers)**: Webhooks are outbound only (notify external systems). There is no inbound webhook endpoint that could trigger execution from external CI/CD or event sources.
- **No API key management**: Single shared token only; no per-client API keys, no key rotation, no scoped permissions.

---

### Story 3.5 -- Multi-Day Workflow Support

**Rating: PARTIALLY MET**

#### What Exists

**Execution pause/resume via persisted state:**
- Every state transition is persisted atomically to disk (executor.py uses `self._save_execution(state)` after every mutation)
- `baton execute resume` reconstructs and continues from any persisted state
- `baton daemon start --resume` continues a daemon execution

**Full state snapshots:**
- `ExecutionState.to_dict()` / `from_dict()` captures the complete plan, step results, gate results, amendments, interaction turns, resolved decisions, and all metadata
- Dual-write to both JSON file and SQLite for redundancy

**Namespaced concurrent executions:**
- `StatePersistence` supports `task_id`-namespaced directories under `executions/<task_id>/` for concurrent multi-day workflows

#### What Is Missing

- **No explicit `baton execute pause` command**: Execution can be interrupted (Ctrl+C or `baton daemon stop`) and later resumed, but there is no formal `pause` subcommand that creates a clean pause point.
- **No timeline view**: No CLI or UI command to show the temporal progression of a multi-day execution.
- **No checkpoint frequency configuration**: State is saved after every transition (always-on); there is no configurable checkpoint interval.
- **No state expiration or TTL**: Old execution states accumulate indefinitely.

---

### Story 3.6 -- Resource Governance and Quotas

**Rating: MINIMALLY MET**

#### What Exists

**Max concurrent agents config:**
- `ResourceLimits.max_concurrent_agents` at `agent_baton/models/parallel.py` (line 110): defaults to 8
- `StepScheduler` at `agent_baton/core/runtime/scheduler.py` (lines 29-108): uses `asyncio.Semaphore` to enforce concurrency cap
- `WorkerSupervisor.start()` at supervisor.py (lines 153-155): reads `plan.resource_limits.max_concurrent_agents` when set
- `--max-parallel` flag on `baton daemon start` (daemon.py line 37)

**Token budget warnings (advisory only):**
- `_check_token_budget()` at executor.py (lines 2174-2198): warns when cumulative tokens exceed tier threshold (lean: 50K, standard: 500K, full: 2M)
- Warning is appended to step result deviations but **does not halt execution**

**Budget tuner (learning, not enforcement):**
- `BudgetTuner` at `agent_baton/core/learn/budget_tuner.py`: recommends budget tier changes based on historical usage; does not enforce limits

**Circuit breaker in improvement model (unrelated to execution):**
- `ExperimentConfig.paused` at `agent_baton/models/improvement.py` (line 453): pauses improvement recommendations, not execution

#### What Is Missing

- **No daily token spend limit**: Token warnings are advisory; no hard enforcement.
- **No per-task cost cap**: The budget tier threshold generates a warning string but does not stop execution.
- **No circuit breaker for execution**: No mechanism to automatically halt when token consumption exceeds a threshold or when failures cascade.
- **No emergency stop (`baton daemon halt`)**: `baton daemon stop` sends SIGTERM for graceful shutdown, but there is no `halt` for immediate kill-all.
- **No quota CLI**: No `baton quota set/get/show` commands. `ResourceLimits` is set programmatically or via plan JSON only.
- **No `max_tokens_per_minute` enforcement**: The field exists in `ResourceLimits` (parallel.py line 111) but is never checked or enforced at runtime.

---

### Story 6.1 -- CI Pipeline Integration as Gate

**Rating: NOT MET**

#### Evidence

- `GateRunner` at `agent_baton/core/engine/gates.py` (lines 56-291) supports gate types: `build`, `test`, `lint`, `spec`, `review`. All are local shell commands or advisory.
- No CI provider integration (GitHub Actions, Jenkins, CircleCI, etc.) exists anywhere in the codebase.
- The only CI-adjacent mentions are in `agent_baton/api/webhooks/__init__.py` (docstring: "external systems (Slack, CI pipelines, custom endpoints)") and `agent_baton/core/distribute/experimental/async_dispatch.py` (docstring: "long-running CI pipelines") -- both are documentation comments, not implementation.
- Gates execute synchronously via `subprocess.run()` in the worker; there is no mechanism to trigger an external CI run and block until it completes.

#### What Would Be Needed

- A `ci` gate type that triggers an external CI pipeline (e.g., via GitHub API)
- Polling/webhook mechanism to wait for CI completion
- Support for multiple CI providers
- Fallback to internal gates when CI is unavailable

---

### Story 6.2 -- Custom Agent Creation via Talent Builder

**Rating: PARTIALLY MET**

#### What Exists

**Agent definition file:**
- `agents/talent-builder.md` (385 lines): comprehensive Opus-model agent with instructions for:
  - Domain research (light and deep)
  - Decision framework (5 tests for what to create)
  - Knowledge pack creation (structured format, quality checks)
  - Agent file creation (frontmatter template, quality checklist)
  - Skill creation (SKILL.md structure)
  - Enterprise patterns (domain onboarding, system integration, regulatory)

**The talent-builder is an agent prompt, not code:**
- It instructs the LLM to create `.md` files in `.claude/agents/` or `~/.claude/agents/`
- Created agents use the standard frontmatter format (name, description, model, permissionMode, tools)
- The `AgentRegistry` at `agent_baton/core/orchestration/registry.py` discovers agents dynamically from `.claude/agents/` directories

#### What Is Missing

- **No programmatic API**: Agent creation is purely prompt-driven; there is no `baton agent create` CLI command or Python API.
- **No base class inheritance**: Created agents are standalone Markdown files; there is no inheritance mechanism or shared base template enforced at runtime.
- **No test scaffolding**: No mechanism to generate test cases for a new agent or validate that it produces expected output.
- **No validation of generated agents**: The talent-builder includes a quality checklist in its instructions, but there is no automated validation step that verifies the generated agent file is well-formed.

---

### Story 6.3 -- Webhook-Driven External Notifications

**Rating: PARTIALLY MET**

#### What Exists

**Webhook registration (API-only):**
- POST /api/v1/webhooks at `agent_baton/api/routes/webhooks.py` (lines 23-61)
- GET /api/v1/webhooks and DELETE /webhooks/{id} for listing and removal
- `WebhookRegistry` at `agent_baton/api/webhooks/registry.py` (lines 32-162): CRUD persisted to `webhooks.json`, glob-style event pattern matching

**HMAC-SHA256 signing:**
- `WebhookDispatcher._sign_payload()` at `agent_baton/api/webhooks/dispatcher.py` (lines 269-283)
- Header: `X-Baton-Signature`

**Retry logic with exponential backoff:**
- `_deliver_with_retry()` at dispatcher.py (lines 131-183): 3 attempts with delays [5s, 30s, 300s]
- Auto-disable after 10 consecutive failures (lines 174-183)

**Slack Block Kit format:**
- `format_slack()` at `agent_baton/api/webhooks/payloads.py` (lines 32-151): rich Block Kit messages for `human.decision_needed` events with interactive action buttons

**Generic JSON format:**
- `format_generic()` at payloads.py (lines 17-29): wraps `Event.to_dict()` verbatim

**Failure logging:**
- JSONL failure log at `webhook-failures.jsonl` (dispatcher.py lines 287-308)

#### What Is Missing

- **No `baton webhook add` CLI command**: Webhook management is API-only; no CLI interface.
- **No Microsoft Teams payload format**: Only generic JSON and Slack Block Kit are implemented.
- **No dead letter queue**: Failed deliveries are logged to JSONL but there is no queue for retry/replay of exhausted deliveries.
- **No webhook test/ping endpoint**: No way to send a test payload to verify a webhook is correctly configured.

---

### Story 6.4 -- Structured Handoff Context Between Phases

**Rating: PARTIALLY MET**

#### What Exists

**Handoff from previous step output:**
- Executor at `agent_baton/core/engine/executor.py` (lines 2978-2987): finds the most recent completed step's outcome and passes it as `handoff_from` to the dispatcher
- Resolved knowledge gap decisions are appended to the handoff text via `_append_resolved_decisions()` (executor.py lines 3779-3796)

**Beads (structured memory) injected into prompts:**
- `BeadSelector.select()` called at executor.py (lines 2993-3007): selects up to 5 relevant beads (discoveries, decisions, warnings) from prior agents
- Injected as "## Prior Discoveries" section in delegation prompts (dispatcher.py lines 158-205)

**Knowledge sections in delegation prompts:**
- `PromptDispatcher._build_knowledge_section()` at dispatcher.py (lines 79-126): inline or referenced knowledge attachments per step

**Shared context propagation:**
- `shared_context` field on `MachinePlan` is passed to every delegation prompt (executor.py line 3028)

#### What Is Missing

- **No auto-generated handoff documents**: Handoff is a raw text string (previous step's outcome), not a structured document.
- **No git diff in handoff**: Files changed and commit hashes are recorded in `StepResult` but are not included in the handoff context passed to the next agent.
- **No gate results in handoff**: Gate pass/fail results are not included in the delegation prompt for the next phase.
- **Not queryable**: Handoff context is ephemeral (constructed at dispatch time); there is no `baton handoff show` command or API endpoint to inspect handoff between phases.

---

### Story 6.5 -- Exportable Audit Reports

**Rating: NOT MET**

#### Evidence

- No `baton export` command exists. The only `--export` flag is on `baton transfer --export` (distributing agent definitions, not audit data).
- Traces are stored as JSON (`TaskTrace` written by `TraceRecorder`), retrospectives as JSON, usage logs as JSONL. All are machine-readable but there is no human-formatted export.
- No PDF generation capability anywhere in the codebase.
- No CSV export.
- No custom report templates.
- No tamper detection hashes on exported data.

The raw data exists (traces, usage logs, retrospectives, compliance entries) but there is no formatting or export pipeline.

---

### Story 6.6 -- Plugin Architecture for Gate Types

**Rating: NOT MET**

#### Evidence

- `GateRunner` at `agent_baton/core/engine/gates.py` has a fixed set of gate types: `build`, `test`, `lint`, `spec`, `review`, plus an unknown-type fallback (lines 232-245).
- The gate runner is a single class with hardcoded evaluation logic per type. There is no plugin interface, no registration mechanism, and no dynamic loading.
- The CLI (`agent_baton/cli/main.py`) uses "plugin-based architecture" for command registration, but this applies only to CLI commands, not to gate types.
- No `baton plugin install` command exists.
- No sandboxed execution for custom gates.

The unknown-gate-type fallback (exit-code check) provides minimal extensibility -- any arbitrary shell command can be a gate by setting `gate_type` to an unrecognized value -- but this is not a plugin architecture.

---

## Cross-Cutting Observations

### Strengths

1. **Headless execution is production-quality**: The daemon/supervisor/worker/launcher stack is well-engineered with PID locking, signal handling, log rotation, and rate-limit retries.
2. **API surface is substantial**: 10 route modules covering plans, executions, agents, webhooks, events (SSE), decisions, observability, PMO, and learning.
3. **Webhook infrastructure is surprisingly mature**: HMAC signing, retry backoff, auto-disable, Slack Block Kit -- this exceeds what many competing tools offer.
4. **State persistence is robust**: Atomic writes, dual JSON+SQLite, namespaced concurrent executions, and SQLite fallback for crash recovery.

### Systemic Gaps

1. **No containerization**: No Dockerfile, no docker-compose, no container health checks. This is a prerequisite for remote VM deployment at scale.
2. **No git worktree integration**: Parallel agents all write to the same working directory. This is a fundamental limitation for safe parallel execution.
3. **No export pipeline**: Rich observability data (traces, retros, usage) exists but cannot be consumed outside the system.
4. **No gate extensibility**: The fixed gate type set limits integration with external quality systems.
5. **Resource governance is advisory-only**: Token budgets warn but do not enforce. No hard limits, no circuit breakers, no emergency stop.
