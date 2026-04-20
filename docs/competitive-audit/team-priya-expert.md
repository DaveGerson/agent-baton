# Team Audit: Priya (DevOps) + Agent-Baton Expert

Two-pass structured dialogue audit performed against the
`feat/actiontype-interact` branch. Each item follows the
Priya-asks / Expert-investigates / Priya-probes / Expert-discovers
pattern to extract findings that a single-pass review would miss.

---

### 1. Container Readiness

**Priya asks:** Can I build a Docker image from this repo today and run
it in k8s? What are the actual blockers -- is it just a missing
Dockerfile, or are there runtime assumptions that break in containers?

**Expert finds:** No `Dockerfile`, `docker-compose.yml`, or Helm chart
exists anywhere in the repository (`Glob("**/Dockerfile*")` returns
nothing). The project is pip-installable with optional extras (`[api]`,
`[classify]`, `[dev]`) via `pyproject.toml:25-49`. The CLI entry point
is `baton = "agent_baton.cli.main:main"` (`pyproject.toml:52`). The API
server binds via uvicorn and is configurable via `create_app()` in
`agent_baton/api/server.py:70-153`. The daemon supports `--foreground`
mode (`agent_baton/cli/commands/execution/daemon.py:46`).

**Priya probes:** Forget the Dockerfile for a second -- are there
runtime assumptions that would actively break in a container? I'm
thinking about filesystem layout, PID file locking, daemonization,
relative path assumptions.

**Expert discovers:** Three concrete issues found:

1. **Double-fork daemonization is POSIX-only and container-hostile.**
   `agent_baton/core/runtime/daemon.py:32-35` raises `RuntimeError` on
   Windows, but in a container this code should never run. The
   `--foreground` flag bypasses it, but this is an implicit requirement
   that must be documented. If someone passes `baton daemon start`
   without `--foreground` in a PID-1 container, the double-fork will
   orphan the grandchild process and the container will exit immediately
   (the parent exits at `daemon.py:45`).

2. **flock() on network filesystems is unreliable.** The supervisor
   comments acknowledge this at `supervisor.py:351`: "Note: flock() on
   network filesystems may not enforce mutual exclusion." In k8s with
   NFS-backed PVCs, the PID-file lock is advisory at best.

3. **Working directory preservation.** `daemon.py:76` explicitly
   preserves cwd: "Working directory is intentionally preserved --
   agent-baton resolves project-relative paths (e.g.
   .claude/team-context/) from cwd." This means the container must set
   `WORKDIR` to the project root or mount it correctly. The
   `StatePersistence` class (`persistence.py:57-64`) resolves paths
   relative to a `context_root` parameter, so this is configurable but
   the default assumes a project checkout exists.

**Joint verdict:** BLOCKED -- No container artifacts exist, and the
daemonization code would cause PID-1 issues if `--foreground` is not
used. Containerization is mechanically achievable but requires:
(a) Dockerfile with `--foreground` baked in, (b) documented volume mount
for `.claude/team-context/`, (c) avoiding `baton daemon start` without
`--foreground`.

**Delta from solo audit:** Solo audit noted "no Dockerfile" but did not
identify the double-fork PID-1 trap or the flock/NFS caveat. These are
the kind of issues that cause 2am pages when you deploy to k8s without
realizing the daemon silently exits.

---

### 2. Health Probes

**Priya asks:** Do the liveness and readiness probes actually check
meaningful state, or are they just "return 200 if the process is up"?
What's the failure mode if the engine is wedged but the HTTP server is
still responsive?

**Expert finds:** Two endpoints in `agent_baton/api/routes/health.py`:

- **Liveness** (`GET /api/v1/health`, line 30-37): Returns
  `HealthResponse(status="healthy", version, uptime_seconds)`. This is
  a pure liveness check -- it always returns 200 as long as the ASGI
  server is up. It does inject the engine via `Depends(get_engine)` but
  does not call any method on it (the argument is `noqa: ARG001`).

- **Readiness** (`GET /api/v1/ready`, line 40-59): Queries
  `engine.status()` and `decision_manager.pending()`. Returns
  `ReadyResponse(ready=True, daemon_running=bool, pending_decisions=int)`.
  Critically: `ready` is always `True` (line 55). The `daemon_running`
  field is `True` when status is not `"no_active_execution"`, and
  `pending_decisions` counts outstanding human decisions.

Both are auth-exempt via
`agent_baton/api/middleware/auth.py:26-34`.

**Priya probes:** Wait -- the readiness probe always returns
`ready=True`? That means k8s will route traffic to this pod even when
there's no active execution, no database connection, or the engine is
in a bad state. Can the readiness probe actually fail?

**Expert discovers:** Confirmed: `health.py:55` hardcodes `ready=True`.
The only variable fields are `daemon_running` (informational) and
`pending_decisions` (informational). There is no check for:

- SQLite database connectivity (no `SELECT 1` probe)
- Disk space availability on the state directory
- Whether the `claude` binary is reachable (for headless mode)
- Whether the engine is in a `"failed"` status

The `engine.status()` call at line 46 could theoretically raise an
exception (e.g., corrupted state file), which would propagate as a 500
-- but this is an unintentional failure, not a deliberate readiness
signal.

Furthermore, the `try/except` block around `decision_manager.pending()`
(lines 49-53) swallows all exceptions and defaults to 0 pending
decisions, so even if the filesystem is unreadable, the probe still
returns 200 with `ready=True`.

**Joint verdict:** PARTIAL -- Liveness probe is correct (simple "am I
alive" check). Readiness probe is structurally present but functionally
useless as a k8s readiness gate -- it never returns `ready=False`. In
k8s this would mean the pod is always considered ready, defeating the
purpose of the probe.

**Delta from solo audit:** Solo audit rated health as "WORKS" and did
not examine the readiness probe's actual return logic. The team dialogue
revealed that `ready=True` is hardcoded, making the readiness probe
cosmetic rather than functional. This is a significant finding for
production deployment.

---

### 3. Crash Recovery Fidelity

**Priya asks:** When the daemon crashes mid-task -- maybe the machine
reboots, maybe OOM-killer fires -- what exactly gets reconstructed?
Does it pick up at the right step, or does it replay from scratch?

**Expert finds:** The crash recovery path is:

1. `baton daemon start --resume` calls `supervisor.start()` with
   `resume=True` (`supervisor.py:144-146`), which calls
   `engine.resume()`.

2. `engine.resume()` (`executor.py:1675-1736`) attempts to load state
   in this order:
   - Primary: `_load_execution()` reads from the file-based
     `execution-state.json` (atomic tmp+rename writes at
     `persistence.py:70-95`).
   - SQLite fallback: If file-based load returns None but a task_id is
     known, it reconstructs from `baton.db` via
     `storage.load_execution()` (executor.py:1696-1714).

3. After loading state, it reconnects the trace recorder
   (executor.py:1726-1734) and calls `_determine_action(state)` to
   figure out the next action.

4. `recover_dispatched_steps()` (`executor.py:1738-1760`) clears stale
   "dispatched" status markers so the engine re-dispatches those steps.

**Priya probes:** OK, so the plan state is recoverable. But what about
the in-flight agent subprocess? If step 2.1 was 80% done when the
crash happened -- its `claude` process was writing code -- that work is
just gone? And does the engine know the difference between "step 2.1
was dispatched and running" vs "step 2.1 completed but the result
wasn't recorded yet"?

**Expert discovers:** Correct, and this is a significant nuance:

1. **In-flight work is lost.** The `claude` subprocess is launched with
   `start_new_session=True` (`claude_launcher.py:558`), which puts it
   in its own process group. When the daemon crashes, these orphaned
   `claude` processes continue running but their output is never
   captured. The engine marks them as "dispatched" in the state file,
   and `recover_dispatched_steps()` strips these markers, causing a
   **full re-dispatch from scratch**. Any partial git commits the agent
   made remain in the working tree but are not tracked by the engine.

2. **There is no distinction between "dispatched and running" vs
   "completed but unrecorded."** Both show up as a `StepResult` with
   `status="dispatched"` in the state file. The recovery path
   (`executor.py:1752-1753`) removes ALL dispatched results
   unconditionally: `state.step_results = [r for r in
   state.step_results if r.status != "dispatched"]`. This means if a
   step completed successfully and the agent committed code, but the
   daemon crashed before `record_step_result()` persisted the result,
   the step will be re-dispatched. The agent will then try to redo
   work that may already be committed, potentially causing git
   conflicts.

3. **Atomic persistence helps but has a window.** State is saved via
   tmp+rename (`persistence.py:78-95`), which prevents partial writes.
   But the save happens in `_save_execution()` which is called after
   `record_step_result()`. Between the agent finishing and the state
   save completing, there is a crash vulnerability window.

**Joint verdict:** PARTIAL -- Plan-level state recovery works well
(dual-path: file + SQLite fallback). But step-level recovery is lossy:
in-flight agent work is lost and re-dispatched from scratch, with no
deduplication of partial git commits. The crash window between agent
completion and state persistence means completed work can be lost.

**Delta from solo audit:** Solo audit noted "SIGTERM handler with 30s
drain" and "atomic state persistence" but did not examine what happens
to orphaned `claude` subprocesses or the race between agent completion
and state save. The team dialogue revealed the lost-work scenario for
completed-but-unrecorded steps.

---

### 4. Graceful Shutdown

**Priya asks:** When I send SIGTERM to the daemon (k8s pre-stop hook,
rolling restart), does it actually drain cleanly? What happens to the
Claude subprocesses that are mid-execution?

**Expert finds:** The shutdown path is:

1. `SignalHandler` (`signals.py:48-64`) installs handlers for SIGTERM
   and SIGINT via `loop.add_signal_handler()`. On signal receipt, it
   sets an `asyncio.Event` (`signals.py:92-94`).

2. `supervisor._run_with_signals()` (`supervisor.py:180-209`) races
   two tasks: the worker and the signal waiter. When the signal task
   wins, it cancels the worker task and waits up to 30 seconds:
   ```python
   worker_task.cancel()
   await asyncio.wait_for(worker_task, timeout=30.0)
   ```

3. In the `finally` block (`supervisor.py:173-176`), status is written
   and PID file is removed.

4. The `stop()` method (`supervisor.py:251-284`) sends SIGTERM to the
   PID and polls for exit with a configurable timeout (default 30s).

**Priya probes:** OK, but `worker_task.cancel()` cancels the asyncio
task -- it does NOT send SIGTERM to the actual `claude` child
processes. Those are launched with `start_new_session=True`, which puts
them in a separate process group. So when the daemon exits, what
happens to those `claude` processes?

**Expert discovers:** This is a critical gap:

1. **`start_new_session=True`** (`claude_launcher.py:558`,
   `headless.py:246`) means each `claude` subprocess is in its own
   process group. When the daemon process exits, the OS does NOT
   automatically send SIGTERM to these children because they are in
   different sessions.

2. **The `StepScheduler`** (`scheduler.py:76-83`) uses
   `asyncio.Semaphore` for concurrency control but has no shutdown
   hook. When the worker task is cancelled, the `dispatch()` coroutine
   is interrupted inside `launcher.launch()`, which is waiting on
   `process.communicate()`. The `asyncio.CancelledError` propagates
   up, but the `claude` subprocess is never explicitly killed or
   signaled.

3. **Only timeout kills the process.** The only code path that kills a
   `claude` process is the timeout handler in
   `claude_launcher.py:581-586`:
   ```python
   except asyncio.TimeoutError:
       process.kill()
       await process.wait()
   ```
   But `CancelledError` (from the shutdown path) is NOT `TimeoutError`.
   The process handle goes out of scope and the `claude` process
   becomes an orphan.

4. **No process tracking.** There is no registry of active subprocess
   PIDs. The scheduler tracks `_active` count
   (`scheduler.py:54`) but not the actual process handles. There is no
   `cleanup()` or `shutdown()` method on `StepScheduler` or
   `ClaudeCodeLauncher`.

**Joint verdict:** PARTIAL -- The daemon itself shuts down cleanly with
proper state persistence. However, in-flight `claude` subprocesses are
orphaned because `start_new_session=True` isolates them and there is no
cleanup path for `CancelledError`. These orphaned processes continue
consuming tokens and may commit code after the daemon has exited. In
k8s, the container would exit but orphaned processes in the same PID
namespace would continue until the pod is forcefully terminated.

**Delta from solo audit:** Solo audit rated graceful shutdown as "WORKS"
based on the SIGTERM handler and 30s drain timeout. The team dialogue
revealed that the drain timeout is meaningless for `claude`
subprocesses because `CancelledError` does not propagate to child
processes launched with `start_new_session=True`. This is a token-leak
and data-corruption risk.

---

### 5. Resource Governance

**Priya asks:** Can I set hard limits on token spend so a runaway agent
doesn't burn through our API budget? What enforcement mechanisms exist?

**Expert finds:** Two resource governance mechanisms exist:

1. **Budget tier thresholds** (`executor.py:2174-2198`):
   `_check_token_budget()` sums `estimated_tokens` across all completed
   step results and compares against tier thresholds:
   - `lean`: 50,000 tokens
   - `standard`: 500,000 tokens
   - `full`: 2,000,000 tokens

   This is checked after each step result is recorded
   (`executor.py:1275`, `executor.py:2054`). When exceeded, it returns
   a **warning string** -- not an enforcement action.

2. **ResourceLimits** (`models/parallel.py:92-137`):
   - `max_concurrent_executions`: 3 (default)
   - `max_concurrent_agents`: 8 (default)
   - `max_tokens_per_minute`: 0 (unlimited by default)
   - `max_concurrent_per_project`: 2 (default)

   The `max_concurrent_agents` value is read by the supervisor
   (`supervisor.py:154-155`) and passed to `TaskWorker`. But
   `max_tokens_per_minute` is defined in the model with a default of 0
   (unlimited) and there is no code that enforces it.

**Priya probes:** So the budget check produces a warning, not a hard
stop? What actually happens when the warning fires? Does execution
continue, pause, or abort?

**Expert discovers:** Confirmed -- it is a soft warning only:

1. The `_check_token_budget()` return value is stored in a local
   variable `warning` (`executor.py:1275`, `executor.py:2054`) and
   logged/included in the action message, but **execution continues
   normally**. There is no circuit breaker, no pause, no escalation to
   an approval gate.

2. The `max_tokens_per_minute` field in `ResourceLimits` is defined
   with default 0 (`parallel.py:111`) but **never referenced** outside
   the model's `to_dict()`/`from_dict()` methods. A `Grep` for
   `max_tokens_per_minute` in the runtime/engine/worker code yields
   zero enforcement hits -- it is a planned-but-unimplemented field.

3. The per-step `timeout` in `ClaudeCodeLauncher` is the only hard
   limit on individual agent execution: 900s for opus, 600s for
   sonnet, 300s for haiku (`claude_launcher.py:73-77`). But these are
   time limits, not token limits.

4. No per-execution or per-project spending cap exists. A task with
   `budget_tier="full"` and multiple retries could consume well beyond
   2M tokens with only a logged warning.

**Joint verdict:** PARTIAL -- Budget tier thresholds exist but produce
warnings, not enforcement. `max_tokens_per_minute` is defined in the
model but never enforced. There is no hard spending cap, no circuit
breaker, and no per-project budget. The only hard limits are per-agent
timeouts (time-based, not token-based). An operator cannot guarantee a
maximum API spend.

**Delta from solo audit:** Solo audit noted "SQLite grows unbounded; no
DB size limits" but did not investigate token budget enforcement. The
team dialogue revealed that budget checks are advisory-only and
`max_tokens_per_minute` is an unimplemented stub -- a gap that matters
significantly for cost management.

---

### 6. Horizontal Scaling

**Priya asks:** Can I run multiple daemon instances across machines to
share a workload? What are the coordination mechanisms?

**Expert finds:** Same-machine concurrency is supported:

1. **Namespaced execution directories**
   (`supervisor.py:83-87`): Each task gets its own directory under
   `executions/<task_id>/` with separate PID files, logs, and status.

2. **flock-based PID locking** (`supervisor.py:347-363`): Prevents
   duplicate daemons for the same task_id on the same machine.

3. **ResourceLimits** (`parallel.py:92-137`) defines
   `max_concurrent_executions=3` and `max_concurrent_per_project=2`,
   but these limits are enforced only within a single supervisor
   instance.

4. **`list_workers()`** (`supervisor.py:288-343`) scans the filesystem
   for all running workers by iterating `executions/` directories and
   checking PID liveness.

**Priya probes:** But what about multi-machine? Can two daemons on
different hosts share the same SQLite database via NFS? Can they
coordinate at all?

**Expert discovers:** Multi-machine scaling is fundamentally blocked:

1. **SQLite is single-machine.** The documentation for WAL mode
   explicitly states it does not work reliably over network filesystems.
   The `ConnectionManager` (`connection.py:86`) sets WAL mode
   unconditionally. On NFS, this can lead to database corruption.

2. **flock is host-local.** The PID file locking (`supervisor.py:354`)
   uses `fcntl.flock()`, which is advisory and does not work across
   NFS hosts. Two daemons on different machines could both acquire the
   lock for the same task_id.

3. **No distributed coordination.** There is no Redis, no Postgres
   advisory locks, no ZooKeeper, no etcd. The `SyncEngine`
   (`sync.py:1-16`) is one-directional (project -> central) and does
   not provide write coordination.

4. **No work queue.** Task dispatch is pull-based within a single
   worker loop (`worker.py:117-297`). There is no message queue, no
   pub/sub, no task claiming mechanism that would allow multiple workers
   to share a pool of pending steps.

5. **Central DB is read-only.** `central.db` is explicitly described as
   a "read-only replica" (`sync.py:9`). It is the cross-project
   aggregation point but does not participate in write coordination.

**Joint verdict:** BLOCKED for multi-machine. Same-machine concurrency
works (namespaced directories, flock, PID files) but is limited by
SQLite's single-writer model. Cross-machine scaling would require
replacing SQLite with a shared-state backend (Postgres, Redis) and
adding a distributed work queue.

**Delta from solo audit:** Solo audit correctly identified "single-
machine concurrency only" and "no distributed lock." The team dialogue
additionally identified that `central.db` is explicitly read-only (not
a coordination backend), that flock fails on NFS, and that there is no
work queue abstraction -- meaning the architecture would need
significant rework for horizontal scaling, not just a database swap.

---

### 7. Secrets Management

**Priya asks:** Where do API keys end up? Are there any hardcoded
secrets, config file secrets, or secrets that could leak through logs
or error messages?

**Expert finds:** All secrets are environment-variable based:

1. `BATON_API_TOKEN` for API auth (`serve.py:72`)
2. `ANTHROPIC_API_KEY` for the Haiku classifier (`classifier.py:397`)
3. `ADO_PAT`, `GITHUB_TOKEN`, `JIRA_API_TOKEN`, `LINEAR_API_KEY` for
   external adapters (`source_cmd.py:348-367`)
4. Webhook secrets are stored per-registration in the webhook registry
   (in-memory + filesystem persistence).

Environment whitelist in `ClaudeCodeLauncher._build_env()`
(`claude_launcher.py:332-354`) explicitly constructs a new dict --
never `os.environ.copy()`.

**Priya probes:** Good on the whitelist. But what about API keys
leaking through error messages, logs, or agent output? If a `claude`
subprocess fails and dumps its stderr, could it contain the API key?

**Expert discovers:** There is active redaction, but with gaps:

1. **Stderr redaction is implemented.** `_redact_stderr()`
   (`claude_launcher.py:58-66`) applies `re.sub(r"sk-ant-[A-Za-z0-9_-]+",
   "sk-ant-***REDACTED***", text)` to all error output before storing
   in `LaunchResult.error`. The same pattern is used in
   `HeadlessClaude._run_once()` (`headless.py:279`).

2. **Agent output is NOT redacted.** The agent's `stdout` (which
   becomes `LaunchResult.outcome`) is parsed from JSON
   (`claude_launcher.py:394-396`) and stored unredacted. If an agent
   accidentally prints an API key in its output, it would be stored in
   `step_results.outcome` in `baton.db`, synced to `central.db`, and
   visible in `baton query` output.

3. **Daemon logs are plain text.** The `RotatingFileHandler`
   (`supervisor.py:388-398`) uses `%(asctime)s %(levelname)s
   %(message)s` format. Any `logger.info()` or `logger.exception()`
   call that includes sensitive data would be written in plain text.
   The daemon logs task_ids and exceptions but does not log API keys
   directly -- however, exception tracebacks could contain environment
   variables in some edge cases.

4. **Webhook secrets are stored on disk.** The webhook registry
   persists per-hook secrets to a JSON file. If the team-context
   directory is readable by other users, webhook HMAC secrets are
   exposed.

**Joint verdict:** WORKS with caveats -- The primary secret handling
(env vars, whitelist, stderr redaction) is solid. The gaps are: (a)
agent stdout is not redacted, (b) webhook secrets are file-persisted
without encryption, (c) daemon logs could theoretically contain
sensitive data in exception tracebacks. No hardcoded secrets found
anywhere in the codebase.

**Delta from solo audit:** Solo audit correctly identified env-var-based
secrets. The team dialogue additionally found that agent stdout is NOT
redacted (only stderr is), webhook secrets are file-persisted, and the
plain-text logging format has no redaction layer. The stdout gap is
particularly notable because agent output is persisted to SQLite and
synced cross-project.

---

### 8. Observability

**Priya asks:** What can I actually feed into Grafana, Datadog, or
Prometheus? Give me the observability stack as it exists today.

**Expert finds:** The observability subsystem includes:

1. **Telemetry** (`observe/telemetry.py`): JSONL append log at
   `.claude/team-context/telemetry.jsonl`. Events include tool calls,
   file reads/writes, bash executions, errors. Rotated by
   `DataArchiver` to 10,000 lines.

2. **Traces** (`observe/trace.py`): Per-task execution traces stored
   in `traces` and `trace_events` tables in `baton.db`. Includes
   timestamps, agent names, phases, steps, durations.

3. **Usage records** (`observe/usage.py`): Per-task summaries with
   agent counts, risk levels, gate results, token estimates.

4. **Retrospectives** (`observe/retrospective.py`): Post-execution
   analysis with worked-well/issues/root-cause per agent.

5. **Domain events** (`events/bus.py`): In-process pub/sub with
   topics like `task.started`, `step.completed`, `gate.failed`.

6. **Dashboard** (`observe/dashboard.py`): Markdown-formatted
   summaries.

7. **Context profiler** (`observe/context_profiler.py`): Per-agent
   context window usage analysis.

**Priya probes:** None of that is Prometheus or OpenMetrics compatible.
Can I even get structured JSON out of the daemon logs, or am I stuck
parsing `%(asctime)s %(levelname)s %(message)s`?

**Expert discovers:** Confirmed -- zero Prometheus/OpenMetrics support:

1. **No `/metrics` endpoint.** The API server (`server.py:52-63`)
   registers 10 route modules; none includes a metrics route. No
   `prometheus_client` in `pyproject.toml`.

2. **No structured logging.** Daemon logs use
   `logging.Formatter("%(asctime)s %(levelname)s %(message)s")`
   (`supervisor.py:394-395`). No `structlog`, no `python-json-logger`,
   no `JSONFormatter` in the codebase or dependencies.

3. **SQLite as observability store.** All observability data is in
   SQLite tables. You could build a sidecar that reads `baton.db` and
   exports to Prometheus, but this is a custom integration.

4. **JSONL telemetry is the closest to structured.** The
   `telemetry.jsonl` file has one JSON object per line, which could be
   ingested by Filebeat/Fluentd. But it uses custom fields, not any
   standard logging format (ECS, GELF, etc.).

5. **Webhooks as an export mechanism.** The webhook system
   (`api/webhooks/dispatcher.py`) delivers HMAC-signed event payloads
   for task lifecycle events. This could feed a metrics pipeline but
   requires custom webhook receivers.

6. **API query endpoints exist.** `baton query --sql` and `baton
   cquery` allow ad-hoc SQL against `baton.db` and `central.db`
   respectively, and the API exposes `/api/v1/observe/` routes. A
   periodic scraper could export these to metrics.

**Joint verdict:** BLOCKED for standard observability integration.
Rich data exists in SQLite, JSONL, and the event bus, but none of it
is exportable in Prometheus, OpenMetrics, StatsD, or structured JSON
log format. Integration with Grafana/Datadog would require either a
custom sidecar (reading SQLite), a webhook-to-metrics bridge, or a
logging retrofit.

**Delta from solo audit:** Solo audit identified the three individual
gaps (no Prometheus, no structured logging, no `/metrics`). The team
dialogue additionally identified that JSONL telemetry could serve as a
bridge (via Filebeat), that the webhook system could proxy events to a
metrics pipeline, and that the API query endpoints could support
periodic scraping. The data exists -- it is the export format that is
missing.

---

### 9. State Durability

**Priya asks:** SQLite under production load -- WAL mode, concurrent
writes, corruption risks. What are the actual durability guarantees?

**Expert finds:** `ConnectionManager` (`connection.py:60-92`)
configures every connection with:

- `PRAGMA journal_mode=WAL` (line 86) -- enables concurrent readers
  during writes.
- `PRAGMA foreign_keys=ON` (line 87) -- enforces referential integrity.
- `PRAGMA busy_timeout=5000` (line 88) -- retries on lock contention
  for 5 seconds.
- `sqlite3.connect(timeout=10.0)` (line 83) -- connection-level timeout.
- Thread-local connection caching (`threading.local()`, line 39) --
  one connection per thread.

State persistence uses atomic tmp+rename (`persistence.py:78-95`) for
`execution-state.json`. SQLite writes use implicit transactions via
`with conn:` context manager (`sqlite_backend.py:9-10`).

**Priya probes:** What about concurrent writes from multiple processes?
The daemon, the CLI, and the API server could all be writing to the
same `baton.db` at the same time. WAL mode helps with read concurrency,
but SQLite still has a single-writer constraint. What happens under
contention?

**Expert discovers:** The contention handling is present but has limits:

1. **WAL mode allows one writer + many readers.** This is correctly
   configured. The CLI reads (queries, status checks) will not block
   the daemon's writes.

2. **busy_timeout=5000ms** means a write attempt will retry for up to
   5 seconds if another writer holds the lock. If contention exceeds
   5 seconds, the write fails with `sqlite3.OperationalError:
   database is locked`.

3. **Sync engine disables FKs.** `SyncEngine._open_project_db()`
   (`sync.py:492-497`) opens project databases with
   `PRAGMA foreign_keys=OFF`. This is deliberate (to avoid FK
   violations during cross-table sync) but means sync operations do
   not enforce referential integrity.

4. **No WAL checkpoint management.** There is no `PRAGMA
   wal_checkpoint(TRUNCATE)` or periodic checkpoint call anywhere in
   the codebase. The WAL file can grow unbounded during sustained
   write activity. SQLite auto-checkpoints at ~1000 pages by default,
   but this is not configurable or monitored.

5. **No backup command.** There is no `baton db backup`, no `sqlite3
   .backup` integration, no `VACUUM` command. The JSON-to-SQLite
   migration tool exists (`migrate.py`) but operates in one direction.
   Standard SQLite backup tools work but must be scripted externally.

6. **No database size monitoring.** No command to check DB size, no
   alert when the database grows beyond a threshold, no pruning of
   old execution data. The `DataArchiver` handles JSONL rotation
   (10,000 lines) but not SQLite table pruning.

**Joint verdict:** PARTIAL -- WAL mode and busy_timeout provide
reasonable single-machine durability. The atomic tmp+rename pattern
protects `execution-state.json`. However: no WAL checkpoint management
(unbounded WAL growth), no backup command, no data pruning, no size
monitoring. Under sustained write load from concurrent daemon + CLI +
API processes, the 5-second busy_timeout could be exceeded.

**Delta from solo audit:** Solo audit noted "no backup/restore command"
and "SQLite grows unbounded." The team dialogue additionally discovered
the missing WAL checkpoint management (WAL file growth), the FK
disablement during sync operations, and the specific 5-second
busy_timeout as the contention ceiling -- all of which are important
for sustained production operation.

---

### 10. Upgrade Path

**Priya asks:** When I deploy a new version of agent-baton, how do
schema migrations work? Can I do rolling upgrades, or does every
instance need to stop simultaneously?

**Expert finds:** Schema migration is handled by `ConnectionManager`:

1. **Version tracking** (`schema.py:43`): `SCHEMA_VERSION = 9`.
   The `_schema_version` table stores the current version.

2. **Migration scripts** (`schema.py:46-191`): `MIGRATIONS` dict maps
   version numbers (2-9) to DDL strings. Each migration is ALTER TABLE
   or CREATE TABLE IF NOT EXISTS.

3. **Auto-migration on connect** (`connection.py:101-138`):
   `_ensure_schema()` runs on the first connection per thread. If the
   database version is less than the code version, it applies migration
   scripts sequentially. Idempotent: duplicate-column errors are
   silently skipped (`connection.py:186-195`).

4. **Forward-only.** There are no rollback scripts. `MIGRATIONS` are
   additive (ALTER TABLE ADD COLUMN, CREATE TABLE). No columns are
   dropped or renamed.

**Priya probes:** Auto-migration on connect sounds convenient but also
dangerous. If I have 3 daemon instances connecting simultaneously after
a deployment, do they all try to run migrations at the same time? Is
there a migration lock?

**Expert discovers:** This is a genuine race condition:

1. **No migration lock.** The `_ensure_schema()` method
   (`connection.py:101-138`) reads the version, runs migrations, and
   updates the version in a single thread's connection. There is no
   advisory lock, no mutex, no `SELECT ... FOR UPDATE` (SQLite does
   not support it in the SQL standard sense).

2. **SQLite's implicit locking helps but is not sufficient.** The
   `executescript()` call for fresh databases (`connection.py:121`)
   acquires an exclusive lock for the duration of the script. But the
   per-version migration loop (`_run_migrations()`,
   `connection.py:140-197`) executes individual statements -- each one
   acquires and releases the write lock independently. Two concurrent
   migrators could interleave.

3. **Idempotent DDL saves the day.** The migration code explicitly
   handles `duplicate column name` errors (`connection.py:187-195`)
   by skipping silently. Since all migrations are additive (ALTER TABLE
   ADD COLUMN, CREATE TABLE IF NOT EXISTS), concurrent execution is
   safe in practice -- the second migrator will just see "column
   already exists" and skip. This is correct but accidental robustness,
   not deliberate concurrency design.

4. **Central DB uses the same mechanism.** `central.db` shares the
   same `ConnectionManager` and migration path. Multiple projects
   syncing simultaneously could trigger concurrent migrations against
   central.db.

5. **No state compatibility checking.** The engine does not verify
   that its in-memory models match the database schema version. If a
   new code version adds a field to `ExecutionState` but the database
   has not been migrated yet, the `from_dict()` deserializer may
   silently use defaults (dataclass default values). This makes rolling
   upgrades technically possible (old data reads with defaults) but
   there is no explicit version compatibility check.

6. **Version rollback is blocked.** Downgrading to an older code
   version after migration will leave extra columns in the database
   that the old code does not know about. Since `SELECT *` is used in
   the sync engine and `sqlite3.Row` factory is used everywhere, extra
   columns are silently ignored. However, if a new-version daemon has
   written data using new columns, an old-version daemon will not read
   those columns.

**Joint verdict:** PARTIAL -- Forward-only additive migrations with
idempotent DDL make upgrades safe in practice. Concurrent migration is
handled by accident (duplicate-column-skip) rather than by design.
Rolling upgrades work because new columns have defaults and old code
ignores extra columns, but there is no explicit version compatibility
contract. Rollback to a previous version is possible (extra columns are
ignored) but data written using new fields would be invisible to the old
version.

**Delta from solo audit:** Solo audit noted "schema migrations exist
(versions 2-9)" and "no dedicated backup command." The team dialogue
revealed the concurrent migration race condition (no lock), the
accidental-but-functional idempotency, the lack of version
compatibility checking, and the asymmetric rollback behavior (works
but with data loss for new-version fields). These nuances matter for
a team operating this in production with multiple instances.

---

## Comparison: Solo Audit vs. Team Audit

The following table compares findings from the existing solo audit
(`persona-priya-tomoko.md`) against this team audit, highlighting new
findings that the two-pass dialogue approach uncovered.

| # | Topic | Solo Rating | Team Rating | New Findings from Team Dialogue |
|---|-------|-------------|-------------|--------------------------------|
| 1 | Container readiness | BLOCKED | BLOCKED | Double-fork PID-1 trap; flock/NFS caveat; cwd preservation requirement |
| 2 | Health probes | WORKS | PARTIAL | **Readiness probe always returns `ready=True`** -- functionally useless as k8s gate |
| 3 | Crash recovery | WORKS* | PARTIAL | Orphaned `claude` subprocesses; no distinction between dispatched-running vs completed-unrecorded; re-dispatch causes git conflicts |
| 4 | Graceful shutdown | WORKS | PARTIAL | **`CancelledError` does not propagate to child processes**; `start_new_session=True` isolates subprocesses from signal delivery; orphaned processes continue consuming tokens |
| 5 | Resource governance | PARTIAL* | PARTIAL | Budget checks are **advisory-only** (warnings, no enforcement); `max_tokens_per_minute` is an unimplemented stub; no hard spending cap |
| 6 | Horizontal scaling | PARTIAL | BLOCKED (multi) | `central.db` is explicitly read-only; flock fails on NFS; no work queue abstraction; architecture requires significant rework |
| 7 | Secrets management | WORKS | WORKS (caveats) | Agent stdout NOT redacted (only stderr); webhook secrets file-persisted; plain-text logs have no redaction layer |
| 8 | Observability | BLOCKED | BLOCKED | JSONL telemetry could bridge via Filebeat; webhook system could proxy to metrics; data exists, export format missing |
| 9 | State durability | PARTIAL | PARTIAL | Missing WAL checkpoint management; FK disabled during sync; 5-second busy_timeout is the contention ceiling |
| 10 | Upgrade path | PARTIAL* | PARTIAL | Concurrent migration race (no lock); accidental idempotency; no version compatibility check; asymmetric rollback |

*Solo audit addressed these topics partially across multiple items
rather than as dedicated evaluations.

### Key Findings Unique to the Team Approach

1. **Readiness probe is cosmetic** (item 2): `ready=True` is hardcoded.
   The solo audit rated health as WORKS without examining the return
   logic. This is the highest-impact new finding -- it means k8s
   deployment would have no effective readiness gating.

2. **Orphaned subprocess problem** (items 3, 4): `start_new_session=True`
   isolates `claude` processes from both crash recovery and graceful
   shutdown signal delivery. The solo audit noted the 30-second drain
   but did not investigate whether signals reach child processes. This
   is a token-leak and data-integrity risk.

3. **Budget enforcement is advisory-only** (item 5): The solo audit
   noted "no DB size limits" but the team dialogue specifically traced
   the code path and confirmed that `_check_token_budget()` produces
   a warning string that is logged but does not halt execution.

4. **Concurrent migration race condition** (item 10): The solo audit
   noted migrations exist. The team dialogue traced the code path and
   found that concurrent migrators rely on accidental idempotency
   (duplicate-column-skip) rather than a migration lock.

5. **Agent stdout is not redacted** (item 7): Only stderr passes
   through `_redact_stderr()`. Agent output stored in
   `step_results.outcome` and synced to `central.db` could contain
   secrets printed by agents.

### Summary Scorecard

| Rating | Count | Items |
|--------|-------|-------|
| WORKS (with caveats) | 1 | Secrets management |
| PARTIAL | 7 | Health probes, crash recovery, graceful shutdown, resource governance, state durability, upgrade path, observability (data exists, format missing) |
| BLOCKED | 2 | Container readiness, horizontal scaling (multi-machine) |

### Conclusion

The team dialogue approach produced 5 findings that the solo audit
missed entirely, and deepened the analysis on all 10 items. The most
operationally significant new findings are the hardcoded readiness
probe and the orphaned subprocess problem, both of which would cause
real incidents in a k8s deployment. The system has strong
fundamentals (atomic persistence, WAL mode, env-var secrets, idempotent
migrations) but the gap between "runs on a developer machine" and
"runs in production infrastructure" is wider than the solo audit
suggested.
