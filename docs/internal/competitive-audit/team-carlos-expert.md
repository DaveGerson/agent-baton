# Team Audit: Carlos (Overnight Backlog Drainer) + Agent-Baton Expert

Structured dialogue audit examining whether agent-baton can serve as a
reliable overnight batch execution engine for a startup CTO managing
15-20 low-risk backlog items unattended.

---

## Item 1: Batch Plan Workflow

**Carlos:** I have 20 backlog items. Walk me through the exact workflow
to plan+queue all 20. Step by step, no hand-waving. I need to know if
this is a 5-minute setup or a 2-hour ceremony.

**Expert (Pass 1):** There is no `baton plan --batch` command. Plans are
created one at a time via `baton plan "description" --save`. Each call
overwrites `.claude/team-context/plan.json`, but each plan also receives
a unique task ID and gets task-scoped storage at
`.claude/team-context/executions/<task-id>/plan.json`.

The practical workflow for 20 items is a shell loop:

```bash
for task in "Fix login timeout" "Add CSV export" "Update README" ...; do
  baton plan "$task" --save --complexity light
done
```

Execution is then also serialized:

```bash
for id in $(baton execute list --format json | jq -r '.[].execution_id'); do
  BATON_TASK_ID="$id" baton execute run --max-steps 50
done
```

**Evidence:**
- `cli/commands/execution/plan_cmd.py` lines 42-123: no `--batch` flag
- `cli/commands/execution/execute.py` lines 173-183: `run` subcommand
- `cli/commands/execution/execute.py` line 907: `BATON_TASK_ID` env var

**Carlos (Probe):** So I have to write a wrapper script? What about
daemon mode -- can it run multiple plans?

**Expert (Pass 2):** Correct -- the daemon (`baton daemon start`) runs a
single plan per invocation. There is no queue mechanism. Each daemon
locks a PID file namespaced by task ID (`executions/<task-id>/worker.pid`
per `supervisor.py` line 66-67), so you can theoretically run N daemons
in parallel, but you must start each one separately.

The most practical overnight path is:
1. Loop `baton plan` to create 20 plans.
2. Loop `BATON_TASK_ID=X baton execute run` sequentially, or
3. Run N daemons with `baton daemon start --plan path --task-id ID` in
   parallel with `--max-parallel 1` each.

**Evidence:**
- `core/runtime/supervisor.py` lines 74-87: single plan per supervisor
- `supervisor.py` lines 288-343: `list_workers()` scans per-task dirs

**Joint Verdict:** PARTIAL

The building blocks exist (task IDs, env var binding, per-task
namespacing), but the user must build a batch orchestration script. No
first-class queue or batch command exists.

**Delta from Solo Audit:** Solo audit identified the same gap but missed
the daemon-per-task parallel option and the `BATON_TASK_ID` env var that
makes sequential scripting cleaner. Also missed that daemon PID files
are namespaced, allowing multiple concurrent daemons.

---

## Item 2: Cost Predictability

**Carlos:** Before running 20 tasks overnight, can I estimate total
cost? I need a ballpark before I sleep.

**Expert (Pass 1):** Budget tiers provide coarse estimates:
- `lean`: 50,000 tokens
- `standard`: 500,000 tokens
- `full`: 2,000,000 tokens

The planner auto-selects a tier based on agent count. For LOW-complexity
tasks with 1-2 agents, the tier is `lean` (50K tokens per task). 20
lean tasks would estimate at ~1M tokens.

After running `baton plan --save`, the plan includes `budget_tier` in its
output. You can inspect this before execution:

```bash
baton plan "task" --save --json | jq '.budget_tier'
```

Additionally, `baton query patterns` returns historical `avg_tokens` per
pattern (from `queries.py` line 448), and `baton query cost-by-agent`
returns per-agent token costs from past runs.

**Evidence:**
- `core/engine/planner.py` lines 2294-2313: `_select_budget_tier()` --
  uses BudgetTuner recommendations first, then agent-count heuristic
- `core/engine/executor.py` lines 2186-2198: threshold constants
- `core/storage/queries.py` lines 488-519: `cost_by_agent()` SQL query

**Carlos (Probe):** Those are token estimates, not dollars. And the
`lean` tier is just a warning threshold, right? What's my actual cost
model?

**Expert (Pass 2):** Correct -- there is no token-to-dollar conversion
anywhere in the codebase. The budget tiers are token-count warnings only.
No Anthropic API pricing model is embedded.

For cost prediction, Carlos would need to:
1. Run a few tasks and check `baton query cost-by-agent` for actual per-
   step token usage.
2. Multiply by Anthropic's published per-token pricing externally.
3. Budget tiers give order-of-magnitude guidance (lean ~$0.30-$0.75/task
   at current Sonnet pricing, standard ~$3-$7.50/task), but this is an
   outside calculation.

The BudgetTuner (`core/learn/budget_tuner.py`) does learn from historical
usage and recommends tier adjustments, which improves accuracy over time.

**Joint Verdict:** PARTIAL

Token-level estimation exists and improves with usage via the learning
pipeline. Dollar conversion is absent -- Carlos needs external
calculation. After 1-2 weeks of data, `baton query cost-by-agent` plus
`baton dashboard` would give reliable estimates.

**Delta from Solo Audit:** New finding -- the BudgetTuner learning
pipeline was not mentioned. It means cost prediction improves over time
automatically. Also new: the `baton query patterns` avg_tokens field
provides per-task-type historical averages, not just per-agent. The
`baton dashboard` command generates a full Markdown report with total
token counts, which the solo audit did not surface for this item.

---

## Item 3: Hard Spending Cap

**Carlos:** Can I set an absolute maximum that halts all execution?
Trace the exact enforcement path. I need to know the code line where it
halts.

**Expert (Pass 1):** The `_check_token_budget()` method at
`executor.py` line 2174 compares cumulative `estimated_tokens` against
the tier threshold. When exceeded, it returns a warning string.

The warning is consumed at line 1275:
```python
warning = self._check_token_budget(state)
if warning:
    _log.warning("Budget warning: %s", warning)
    result.deviations.append(f"TOKEN_BUDGET_WARNING: {warning}")
```

This is **advisory only** -- it logs a warning and appends a deviation,
but does NOT halt execution. No `raise`, no `state.status = "failed"`,
no `sys.exit()`.

There is a step-count cap: `baton execute run --max-steps 50` (line
180). When `steps_executed >= max_steps` (line 998), the process exits
with code 1. But this is a step count, not a token count.

**Evidence:**
- `core/engine/executor.py` lines 2174-2198: advisory warning only
- `core/engine/executor.py` lines 1274-1278: warning consumed as
  deviation annotation
- `cli/commands/execution/execute.py` lines 998-1000: max-steps abort

**Carlos (Probe):** So there is literally no way to stop execution when
I've spent too much? What if I set `--max-steps 5` per task?

**Expert (Pass 2):** `--max-steps` is the only hard cap available.
Setting it to a low value (e.g., 5-10 steps per task) provides a crude
cost ceiling since each step is one agent invocation.

For the daemon path (`baton daemon start`), there is no `--max-steps`
flag at all -- the daemon runs until COMPLETE or FAILED. The daemon does
have `--max-parallel` (line 38), which limits concurrency but not total
work.

The worker's `_execution_loop` at `worker.py` lines 117-297 has no
budget check whatsoever. It loops until COMPLETE or FAILED.

The only absolute protection Carlos has:
1. `--max-steps N` on `baton execute run` (not available on daemon)
2. External process monitoring (cron job that kills the daemon PID)
3. Anthropic API spending limits (set in the Anthropic console, outside
   baton)

**Joint Verdict:** BLOCKED

No hard spending cap exists in the execution engine. The `--max-steps`
flag on `baton execute run` is the closest proxy. The daemon has no
equivalent. This is the primary blocker for unattended overnight use.

**Delta from Solo Audit:** New finding -- the daemon path has NO step
limit at all, making it less safe than `baton execute run` for overnight
use. The solo audit mentioned `--max-steps 50` but did not note its
absence from daemon mode. Also new: the worker's execution loop
(`worker.py`) performs zero budget checks, confirmed by code trace.

---

## Item 4: Failure Isolation

**Carlos:** If task #7 of 20 fails catastrophically (infinite loop, OOM),
does it kill the other 19?

**Expert (Pass 1):** Depends on the execution mode:

**Sequential `baton execute run` loop (scripted):** Each task is a
separate process invocation. If task #7 crashes (OOM, infinite loop),
the process exits with a non-zero code. The bash loop would need `||
true` or `set +e` to continue to task #8. Without that guard, the loop
halts.

The Claude subprocess itself has a hard timeout per `ClaudeCodeConfig`
at `claude_launcher.py` line 106: `default_timeout_seconds: float =
600.0` (10 minutes), with per-model overrides. An infinite loop inside
a dispatched agent would be killed after the timeout.

Gate commands also have a 300-second timeout (`execute.py` line 1075:
`timeout=300`).

**Daemon mode (parallel):** Each daemon runs one plan. If a daemon
crashes via OOM, only that task is affected. Other daemons running other
tasks have independent PID files and processes (`supervisor.py` lines
288-343: `list_workers()` discovers per-task-id directories).

**Evidence:**
- `core/runtime/claude_launcher.py` lines 106-112: per-model timeouts,
  default 10 min
- `cli/commands/execution/execute.py` line 1075: gate timeout 300s
- `core/runtime/claude_launcher.py` lines 551-586: subprocess kill on
  timeout
- `core/runtime/supervisor.py` lines 166-177: exception handling in
  daemon with status writeback

**Carlos (Probe):** What if it's not the agent subprocess but baton
itself that OOMs? The Python process running the executor?

**Expert (Pass 2):** If the Python process OOMs:
- The PID file remains on disk. `baton daemon status` will detect it as
  stale (the `os.kill(pid, 0)` probe at `supervisor.py` line 227 will
  raise `OSError`).
- Execution state was persisted to SQLite after every state mutation
  (`_save_execution()` called at lines 1280, 1404, etc.).
- `baton execute resume` (or `baton daemon start --resume`) recovers
  from the last persisted checkpoint.
- `recover_dispatched_steps()` at line 1738 clears stale "dispatched"
  markers so steps that were in-flight during the crash get re-dispatched.

Other tasks are unaffected because each task runs in its own process.

**Joint Verdict:** WORKS

Failures are isolated per-task when using separate processes. The
timeout, resume, and crash recovery paths are all implemented. The key
requirement is that the bash loop must handle non-zero exit codes (e.g.,
`set +e` or `|| true`).

**Delta from Solo Audit:** Entirely new item -- the solo audit did not
evaluate failure isolation at all. Key new findings: per-model agent
timeouts (default 10min), gate timeouts (5min), crash recovery via
`recover_dispatched_steps()`, and the importance of bash error handling
in the scripted loop.

---

## Item 5: Morning Review Efficiency

**Carlos:** I have 90 minutes to review 20 completed tasks. What tools
give me the fastest overview? What's the actual UX?

**Expert (Pass 1):** The review toolkit, ordered by speed:

1. **`baton execute list`** (~2 seconds): Shows all 20 tasks with status
   (complete/failed), step counts, risk level, budget tier, worker PIDs,
   and timestamps. This is the triage screen. Spot failures instantly.
   (`execute.py` lines 1140-1230)

2. **`baton query tasks --status failed`** (~1 second): Filter to only
   failed tasks for immediate attention.

3. **`baton dashboard`** (~3 seconds): Full Markdown report with token
   totals, agent utilization, retry rates, gate pass rates, outcome
   distribution, risk distribution, and model mix. Covers all 20 tasks
   in one view. (`observe/dashboard.py` lines 85-199)

4. **`baton query task-detail <task-id>`**: Per-task deep dive showing
   plan steps, step results with token counts, errors, gate results.
   (`query.py` lines 267-330)

5. **`baton trace --last`** or **`baton trace <task-id>`**: Timeline
   view of a specific task's execution events with timestamps.

6. **`baton retro --task-id <id>`**: Auto-generated retrospective with
   what worked, what didn't, knowledge gaps, and roster recommendations.

**Carlos (Probe):** Can I get a single-screen summary of all 20 tasks
without clicking through each one?

**Expert (Pass 2):** Yes -- `baton dashboard` is the closest to a
single-screen summary. It aggregates ALL tasks (not per-task). Output:

```
# Usage Dashboard
*20 tasks tracked*

## Overview
| Metric            | Value   |
|-------------------|---------|
| Total tasks       | 20      |
| Total agent uses  | 47      |
| Estimated tokens  | 823,000 |
| Avg agents/task   | 2.4     |
| Avg retries/agent | 0.12    |
| Gate pass rate    | 95%     |
```

Plus outcomes, risk distribution, model mix, agent utilization, and
sequencing mode tables.

For cross-project analytics: `baton cquery` runs SQL against
`central.db`, which federates data from all projects. Carlos could
create a weekly digest script.

The `baton query` system supports `--format json` and `--format csv` on
all 16 subcommands, enabling piped post-processing.

**Joint Verdict:** WORKS

The review toolkit is comprehensive. `baton execute list` for triage,
`baton dashboard` for aggregate view, `baton query task-detail` for
deep dives, `baton retro` for lessons learned. All support JSON/CSV
output for scripting. 90 minutes for 20 tasks is comfortable -- most
would take 2-3 minutes per task for the detail view, and the aggregate
views cover everything in under 5 minutes.

**Delta from Solo Audit:** New findings: `baton dashboard` was not
evaluated for Carlos's workflow in the solo audit. The aggregate
overview (total tokens, gate pass rates, outcome distribution) is
exactly what Carlos needs for a morning triage screen. Also new:
`--format csv/json` on all query subcommands enables scripted weekly
digests. The `baton cquery` cross-project path was mentioned but not
connected to Carlos's use case.

---

## Item 6: Daemon Reliability Overnight

**Carlos:** 8 hours unattended. What can go wrong? Network blip, API
rate limit, disk full, OOM. How does each failure mode manifest?

**Expert (Pass 1):**

| Failure Mode | How It Manifests | Recovery |
|---|---|---|
| **Network blip** | `httpx.RequestError` in webhook delivery; `claude` subprocess timeout or network error | Webhook: 3 retries with backoff (5s, 30s, 300s). Agent: `ClaudeCodeLauncher` has 3 rate-limit retries with exponential backoff (`claude_launcher.py` lines 114-118, 268-281). Non-rate-limit network errors are NOT retried -- the step fails. |
| **API rate limit** | `_is_rate_limit()` detects "rate limit" or "429" in stderr (`claude_launcher.py` lines 467-475) | Automatic retry up to 3 times with exponential backoff (5s, 10s, 20s). After 3 failures, step is marked failed. |
| **Disk full** | SQLite write fails; state persistence fails | `_save_execution()` catches and logs but does NOT halt execution. File writes (JSONL, webhooks.json) raise OSError and are caught with try/except in most paths. If the last state write fails, crash recovery cannot restore to the correct checkpoint. |
| **OOM** | Python process killed by OS (SIGKILL) | PID file becomes stale. `baton daemon status` detects it via `os.kill(pid, 0)`. Recovery: `baton daemon start --resume` reloads from last persisted SQLite state, calls `recover_dispatched_steps()` to clear in-flight markers. |
| **Agent subprocess hang** | `asyncio.wait_for` timeout at `claude_launcher.py` line 567-577 | Process killed after timeout (10min default for Opus, 600s for others). Step marked as failed. Execution continues to next step or fails the phase. |

**Evidence:**
- `core/runtime/claude_launcher.py` lines 114-118: `max_retries=3`,
  `base_retry_delay=5.0`
- `core/runtime/supervisor.py` lines 180-209: `_run_with_signals()` --
  SIGTERM/SIGINT handling with 30s drain timeout
- `api/webhooks/dispatcher.py` lines 56-57: `_RETRY_BACKOFFS = [5.0, 30.0, 300.0]`
- `core/runtime/supervisor.py` lines 8-9: `RotatingFileHandler` (10MB, 3 backups)

**Carlos (Probe):** What about the log file? If the daemon runs 8 hours
and something goes wrong, can I read what happened?

**Expert (Pass 2):** The supervisor configures structured logging via
`RotatingFileHandler` (10 MB max, 3 backups) at `supervisor.py` line 8.
The log file is at `.claude/team-context/executions/<task-id>/worker.log`
(or `.claude/team-context/daemon.log` for legacy mode).

The daemon also writes a status snapshot to
`worker-status.json` on every status change and at exit (`supervisor.py`
line 174: `self._write_status(engine, summary=summary)`).

Events are persisted to both JSONL (`events.jsonl`) and the SQLite
`events` table, providing a durable event stream for post-mortem analysis.

**Joint Verdict:** PARTIAL

Most failure modes have reasonable handling. Rate limits are retried
automatically. Timeouts kill hung agents. Crash recovery works via
`--resume`. The gaps are:
- Non-rate-limit network errors are NOT retried at the agent level.
- Disk full during state persistence is logged but could leave execution
  in an unrecoverable state.
- No heartbeat or health-check endpoint to detect a wedged daemon.

**Delta from Solo Audit:** Entirely new item -- the solo audit did not
evaluate daemon reliability against specific failure modes. Key new
findings: rate-limit retry is automatic (3 attempts with backoff), agent
subprocess timeouts are configurable per-model, the supervisor has
rotating log files and status snapshots, but non-rate-limit network
errors cause immediate step failure with no retry.

---

## Item 7: Auto-Approve for LOW Risk

**Carlos:** Can tasks classified LOW automatically proceed through all
gates without my intervention? Trace the exact code path.

**Expert (Pass 1):** Yes. The path splits by execution mode:

**Planner level:** LOW-risk tasks never get approval gates. At
`planner.py` lines 1109-1114:
```python
if risk_level_enum in (RiskLevel.HIGH, RiskLevel.CRITICAL):
    for phase in plan_phases:
        if phase.name.lower() in ("design", "research"):
            phase.approval_required = True
```
LOW and MEDIUM risk skip this block entirely -- no `approval_required`
flag is set.

**`baton execute run` path:** Gates execute as shell subprocesses
(`execute.py` lines 1071-1090). The gate command runs, and pass/fail is
determined by exit code. No human intervention. Approvals prompt on
stdin (line 1111), but LOW tasks have no approval gates, so the prompt
never fires.

**Daemon/worker path:** `TaskWorker._handle_gate()` at `worker.py`
lines 329-394:
- Programmatic gates (test, build, lint, spec) are auto-approved
  immediately (lines 342-348).
- Other gates: auto-approved when no `DecisionManager` is configured
  (lines 351-357), which is the default for daemon mode.

`TaskWorker._handle_approval()` at lines 396-446: auto-approved when no
`DecisionManager` (lines 404-409).

**Evidence:**
- `core/engine/planner.py` lines 1109-1114: approval gates only for
  HIGH/CRITICAL
- `core/runtime/worker.py` lines 342-348: programmatic gate auto-approve
- `core/runtime/worker.py` lines 351-357: non-programmatic gate fallback
- `core/runtime/worker.py` lines 404-409: approval auto-approve

**Carlos (Probe):** What about programmatic gates in `baton execute run`
mode? Do the test/lint commands actually run or are they skipped?

**Expert (Pass 2):** In `baton execute run`, programmatic gates DO run
as real shell subprocesses:

```python
proc = _subprocess.run(
    gate_cmd, shell=True, capture_output=True, text=True,
    timeout=300, cwd=str(Path.cwd()),
)
passed = proc.returncode == 0
```

(`execute.py` lines 1073-1077)

This means `pytest` or `npm test` actually runs. If it fails, the gate
fails, and execution is halted for that task (state set to "failed" at
`executor.py` line 1389).

In daemon mode (`TaskWorker`), programmatic gates are auto-approved
WITHOUT running the actual command (lines 342-348: just records
`passed=True` with output "auto-approved (test)"). This is a design
trade-off for autonomous execution.

This is a significant behavioral difference between the two modes.

**Joint Verdict:** WORKS

LOW-risk tasks proceed fully autonomously through both gates and
approvals in both execution modes. The key insight: daemon mode
auto-approves programmatic gates without running them, while
`baton execute run` actually executes gate commands. Carlos should
choose based on whether he wants real test execution overnight.

**Delta from Solo Audit:** The solo audit correctly identified that LOW
tasks have no approval gates and daemon auto-approves. New finding: the
behavioral split between daemon mode (gates auto-approved without
running) and `baton execute run` mode (gates actually executed as shell
commands). This is a critical distinction Carlos needs to know -- daemon
mode skips tests, `execute run` mode runs them.

---

## Item 8: PR Attribution

**Carlos:** In his GitHub PR inbox, can he distinguish agent PRs from
human PRs? What metadata is in the commits/PR?

**Expert (Pass 1):** The `ClaudeCodeLauncher` captures git metadata
after each agent dispatch:

1. **Commit hash**: `claude_launcher.py` lines 284-289 -- after a
   successful agent run, it runs `git rev-parse HEAD` and compares
   before/after. If a new commit exists, it captures the hash.

2. **Files changed**: `_git_diff_files()` at lines 495-511 runs
   `git diff --name-only` between pre and post commits.

3. **StepResult fields**: The `LaunchResult` dataclass (`launcher.py`
   lines 22-52) carries `commit_hash` and `files_changed`, which are
   recorded in `step_results` in the execution state and SQLite.

However, there is NO automatic PR creation. The engine captures commits
but does not create branches, push, or open PRs. The orchestrator
agent (Claude Code) handles git operations based on the plan's
`git_strategy` field, and PR creation is a manual or agent-driven step.

**Evidence:**
- `core/runtime/claude_launcher.py` lines 284-289: post-launch git
  tracking
- `core/runtime/launcher.py` lines 22-52: LaunchResult with commit_hash
- `core/engine/planner.py` lines 75-84: `_select_git_strategy()`

**Carlos (Probe):** So the commits themselves -- what distinguishes
them? If an agent creates a commit, does it say "created by baton" or
something?

**Expert (Pass 2):** The agents are invoked via `claude --print`, which
is Claude Code in non-interactive mode. Commits created by Claude Code
agents typically include a `Co-Authored-By: Claude` trailer (as per
Claude Code conventions), but this is controlled by Claude Code itself,
not by baton.

Baton does NOT inject any commit message metadata, branch naming, or PR
labels. The `delegation_prompt` sent to agents includes the task
description and step context, but commit authorship depends on the
agent's Claude Code session.

For PR attribution, Carlos would need to:
- Rely on branch naming (the orchestrator creates feature branches per
  plan)
- Use `baton query task-detail <id>` to correlate commit hashes with
  tasks
- Add a post-execution script that creates PRs with baton metadata in
  the description

The `baton execute list` output includes task IDs that can be
cross-referenced with commits via the `step_results` table.

**Joint Verdict:** PARTIAL

Commit hashes and file lists are tracked per step in SQLite.
But there is no automatic PR creation, branch naming convention
enforcement, or commit metadata injection. Carlos can correlate
commits to tasks via `baton query`, but distinguishing agent PRs
from human PRs in the GitHub inbox requires manual workflow setup.

**Delta from Solo Audit:** Entirely new item. The solo audit evaluated
"commits per phase" (item 10) but not PR attribution specifically.
Key new finding: baton tracks commit hashes per step in SQLite
(`step_results.commit_hash`), which enables post-hoc correlation, but
the tool does not inject its own metadata into commits or PRs.

---

## Item 9: Cost-Per-Task Accounting

**Carlos:** After an overnight run, can I see "Task A cost 12K tokens,
Task B cost 45K tokens"? How?

**Expert (Pass 1):** Yes, through multiple paths:

1. **`baton query task-detail <task-id>`**: Shows per-step token counts
   (`query.py` lines 305-318):
   ```
   Step results (5):
     [+] 1.1    backend-engineer--python    tokens=12300  Added CSV export
     [+] 1.2    test-engineer               tokens=8700   Wrote tests
   ```

2. **`baton query cost-by-agent`**: SQL query against `step_results`
   (`queries.py` lines 488-519):
   ```sql
   SELECT agent_name,
          SUM(estimated_tokens) AS total_tokens,
          COUNT(*) AS total_steps,
          AVG(estimated_tokens) AS avg_tokens_per_step,
          SUM(duration_seconds) AS total_duration
   FROM step_results sr
   JOIN executions e ON e.task_id = sr.task_id
   GROUP BY sr.agent_name
   ```

3. **`baton query cost-by-type`**: Token costs grouped by task type.

4. **Ad-hoc SQL**: `baton query --sql "SELECT task_id, SUM(estimated_tokens) FROM step_results GROUP BY task_id"`

5. **`baton dashboard`**: Aggregate token totals across all tasks.

6. **Usage log**: `usage-log.jsonl` records per-task
   `TaskUsageRecord` with `agents_used[].estimated_tokens` fields
   (`observe/usage.py` lines 50-63).

**Evidence:**
- `core/storage/queries.py` lines 488-519: cost-by-agent SQL
- `core/observe/usage.py` lines 97-151: `summary()` aggregation
- `cli/commands/observe/query.py` lines 305-318: task-detail token display

**Carlos (Probe):** Where do the token estimates come from? Are they
accurate or just guesses?

**Expert (Pass 2):** Token counts come from two sources:

1. **Claude CLI JSON output**: When `claude --print --output-format json`
   completes, the JSON output includes usage metadata. The
   `ClaudeCodeLauncher._parse_result()` method extracts `estimated_tokens`
   from this output. This is the agent's self-reported token usage.

2. **Fallback estimation**: When the agent subprocess doesn't report
   tokens (or reports 0), `executor.py` line 1104-1121 applies a fallback:
   ```python
   effective_tokens = estimated_tokens
   # ... fallback logic when estimated_tokens == 0
   ```
   And `_estimate_tokens_for_step()` at line 3863 provides a heuristic
   based on step type.

The Claude CLI's token reporting is reasonably accurate (it comes from
the API response), but baton only captures the total per-step, not
input/output/cache breakdowns.

**Joint Verdict:** WORKS

Per-task and per-step token accounting is available via multiple query
interfaces. Ad-hoc SQL allows any grouping. Token data comes from the
Claude CLI's usage report, which is reasonably accurate. No dollar
conversion exists, but token counts are reliable.

**Delta from Solo Audit:** New findings: the solo audit rated per-task
cost caps as PARTIAL but did not evaluate the per-task accounting query
separately. The ad-hoc SQL escape hatch (`baton query --sql`) enables
custom cost groupings the predefined queries don't cover. The fallback
token estimation for agents that don't report usage was not mentioned.

---

## Item 10: Retry Behavior

**Carlos:** When a gate fails, does the agent retry? How many times?
Can it spiral into expensive retry loops?

**Expert (Pass 1):** There are TWO distinct retry contexts:

**1. Gate failure (test/build/lint):** NO retry. When a gate fails:
- `executor.py` line 1389: `state.status = "failed"`
- On next `_determine_action()` call (line 2660-2680), the engine
  returns `ActionType.FAILED`.
- In `_handle_run()`, FAILED causes `sys.exit(1)` (line 996).
- The entire task stops. No re-run of the gate, no re-dispatch of
  agents.

**2. Agent launch (rate limit only):** YES, bounded retry.
- `claude_launcher.py` lines 268-281: If `_is_rate_limit(result.error)`
  returns True, the launcher retries up to `max_retries=3` with
  exponential backoff (5s, 10s, 20s).
- ONLY rate limits trigger retry. Other failures (network error,
  timeout, agent crash) cause immediate step failure.

**3. Webhook delivery:** Separate retry -- 3 attempts with backoff
(5s, 30s, 300s), auto-disable after 10 consecutive failures.

**Evidence:**
- `core/engine/executor.py` lines 1382-1404: gate failure -> status = "failed"
- `core/runtime/claude_launcher.py` lines 268-281: rate-limit retry loop
- `core/runtime/worker.py` lines 117-297: no retry logic in execution loop
- `api/webhooks/dispatcher.py` lines 131-183: webhook retry with backoff

**Carlos (Probe):** So a flaky test that fails once will kill my entire
task? And the `--max-steps` cap -- can retry loops burn through that?

**Expert (Pass 2):** Correct -- a single gate failure is fatal to the
task. There is no "retry gate" or "re-run phase" mechanism.

Regarding `--max-steps` and retry loops: since agent retries only happen
for rate limits and are bounded to 3, and since rate-limit retries DON'T
increment `steps_executed` (they happen inside the launcher, before
`record_step_result` is called), they cannot burn through the step limit.

The `steps_executed` counter only increments on successful dispatch
completion (`execute.py` line 1059: `steps_executed += 1`), which
happens after the launcher returns.

The worst case for an overnight batch: 20 tasks x 3 rate-limit retries
per step = at most 60 extra API calls with exponential backoff, adding
~2 minutes total. This is bounded and predictable.

**Joint Verdict:** WORKS (with caveat)

Retry behavior is conservative and bounded. Gate failures are fatal (no
retry spiral). Agent retries are rate-limit-only, capped at 3. The
`--max-steps` limit cannot be exhausted by retries. The caveat: flaky
tests will kill tasks, which may be overly aggressive for Carlos's
overnight workflow.

**Delta from Solo Audit:** The solo audit correctly identified no
aggressive retries. New findings: (1) rate-limit retries don't consume
`--max-steps` budget because they happen inside the launcher, (2) gate
failures are immediately fatal with no recovery path, (3) the exact
retry delay progression is 5s/10s/20s for rate limits (exponential
backoff from `base_retry_delay=5.0`).

---

## Item 11: Slack Notification Granularity

**Carlos:** Can I get notified only on failures, not successes? Can I
configure which events trigger notifications?

**Expert (Pass 1):** Yes -- the webhook system supports granular event
filtering via glob patterns.

**Registration**: Via API endpoint `POST /webhooks` (or programmatically
via `WebhookRegistry.register()`):
```json
{
  "url": "https://hooks.slack.com/services/T.../B.../xxx",
  "events": ["step.failed", "gate.failed", "task.failed"],
  "secret": "optional-hmac-secret"
}
```

**Available event topics** (from `core/events/events.py`):
- `step.dispatched`, `step.completed`, `step.failed`
- `gate.required`, `gate.passed`, `gate.failed`
- `task.started`, `task.completed`, `task.failed`
- `phase.started`, `phase.completed`
- `human.decision_needed`, `human.decision_resolved`
- `bead.created`, `bead.conflict`

**Pattern matching**: `WebhookRegistry.match()` uses `fnmatch` at
`registry.py` lines 117-140:
- `step.failed` -- only step failures
- `step.*` -- all step events
- `*.failed` -- all failure events across step/gate/task
- `*` -- catch-all

**Slack formatting**: Auto-detected for `slack.com` URLs
(`dispatcher.py` line 214). Human decision events get rich Block Kit
with action buttons; other events get plain text attachments.

**Evidence:**
- `api/webhooks/registry.py` lines 63-95: `register()` with events list
- `api/webhooks/registry.py` lines 117-140: `match()` with fnmatch
- `api/webhooks/dispatcher.py` lines 131-183: retry with backoff
- `api/webhooks/payloads.py` lines 54-69: Slack fallback for non-decision events

**Carlos (Probe):** Does this require the API server to be running?
What if I'm using `baton execute run` without `--serve`?

**Expert (Pass 2):** Yes -- webhooks require the API server. The
`WebhookDispatcher` subscribes to the `EventBus` and schedules async
delivery tasks via `asyncio.create_task()`. This requires a running
event loop (`dispatcher.py` lines 104-127).

In `baton execute run` mode, there is no API server and no async event
loop. The `EventBus` still fires events, but the webhook dispatcher
either (a) has no loop to schedule tasks on, or (b) was never
instantiated.

In daemon mode with `--serve` flag (`daemon.py` lines 57-63), the API
server co-locates with the worker, sharing the EventBus. This is the
path where webhooks work.

**Without `--serve`:** Events are still persisted to SQLite and JSONL,
but no webhooks fire. Carlos would need to poll `baton query tasks
--status failed` via cron for failure detection.

**Joint Verdict:** PARTIAL

The event filtering system is well-designed -- Carlos can subscribe to
exactly `["step.failed", "gate.failed", "task.failed"]` for
failure-only notifications. But this only works when using
`baton daemon start --serve`, not with `baton execute run`. Without
`--serve`, no webhooks fire.

**Delta from Solo Audit:** The solo audit identified the webhook system
as WORKS but noted the API server dependency as a caveat. New findings:
(1) the exact event topic list was not enumerated -- there are 14+
topics available for filtering, (2) the `*.failed` glob pattern provides
a one-line "notify on all failures" subscription, (3) non-`human.decision_needed`
events sent to Slack get a plain text attachment format, not Block Kit --
the rich formatting only fires for decision events. This means step/gate
failure notifications to Slack are less polished than decision
notifications.

---

## Item 12: Weekly ROI Calculation

**Carlos:** After 4 weeks of use, can I produce a "tasks completed,
total cost, equivalent developer-hours" report?

**Expert (Pass 1):** Partially. Here's what's available:

**Tasks completed + cost:**
- `baton dashboard` produces a full report with total tasks, total
  tokens, outcomes, agent utilization, and gate pass rates.
- `baton query tasks` lists all tasks with status.
- `baton query cost-by-agent` and `cost-by-type` provide breakdowns.
- `baton cquery` runs SQL against `central.db` for cross-project data.

**Ad-hoc SQL for a 4-week report:**
```sql
baton query --sql "
  SELECT COUNT(*) as tasks,
         SUM(sr.estimated_tokens) as total_tokens,
         SUM(sr.duration_seconds) as total_seconds,
         AVG(sr.estimated_tokens) as avg_tokens_per_step,
         COUNT(DISTINCT sr.task_id) as unique_tasks
  FROM step_results sr
  JOIN executions e ON e.task_id = sr.task_id
  WHERE e.started_at >= datetime('now', '-28 days')
"
```

**What's missing:**
- No "equivalent developer-hours" calculation. The system tracks
  `duration_seconds` (wall-clock agent execution time), not human
  effort equivalence.
- No dollar cost conversion (tokens to dollars).
- No built-in "ROI report" command.

**Evidence:**
- `core/observe/dashboard.py` lines 85-199: `generate()` method
- `core/observe/usage.py` lines 97-151: `summary()` aggregation
- `core/storage/queries.py` lines 488-519: cost-by-agent SQL

**Carlos (Probe):** Can I at least calculate developer-hours from the
data? What about task complexity and time saved?

**Expert (Pass 2):** The data to approximate developer-hours is there
but requires external mapping:

1. **Task types** are recorded (`plan.task_type` in the plans table).
   Carlos could assign estimated human hours per task type externally.

2. **Step counts and agent counts** per task are in the database.
   More steps/agents = higher complexity.

3. **Duration seconds** per step are recorded. This is agent wall-clock
   time, not human-equivalent time.

4. **Retrospectives** (`baton retro`) include what-worked / what-didn't
   analysis, which qualitatively supports ROI narratives.

5. **The learning pipeline** (`baton query patterns`) tracks success
   rates and token costs per task type, which trends over time show
   efficiency gains.

A practical approach: Carlos exports 4 weeks of data via
`baton query --sql ... --format csv`, loads into a spreadsheet, applies
his own task-type-to-hours mapping, and calculates ROI.

**Joint Verdict:** PARTIAL

Raw data for ROI calculation exists (tasks, tokens, durations,
outcomes). The query system supports ad-hoc SQL with CSV export.
But there is no built-in developer-hours equivalent, no dollar
conversion, and no "ROI report" command. Carlos needs a spreadsheet
layer on top.

**Delta from Solo Audit:** Entirely new item. The solo audit did not
evaluate ROI reporting. Key new findings: (1) the `--format csv` export
enables spreadsheet integration, (2) task types in the plans table
provide a basis for effort classification, (3) retrospective analysis
provides qualitative ROI support, (4) the learning pipeline's pattern
confidence and success rate trends show efficiency improvement over time.

---

## Summary Table

| # | Item | Verdict | Key Finding |
|---|------|---------|-------------|
| 1 | Batch plan workflow | PARTIAL | No batch command; scripting required via shell loop + `BATON_TASK_ID` |
| 2 | Cost predictability | PARTIAL | Token estimates per tier; BudgetTuner improves over time; no dollar conversion |
| 3 | Hard spending cap | BLOCKED | Advisory warnings only; `--max-steps` is crude proxy; daemon has NO limit |
| 4 | Failure isolation | WORKS | Per-process isolation; timeouts on agents (10min) and gates (5min); crash recovery |
| 5 | Morning review | WORKS | `baton execute list` for triage, `baton dashboard` for aggregate, `baton query` for detail |
| 6 | Daemon reliability | PARTIAL | Rate-limit retries work; non-rate-limit errors fatal; no health-check endpoint |
| 7 | Auto-approve LOW risk | WORKS | No approval gates for LOW; daemon auto-approves programmatic gates (without running them) |
| 8 | PR attribution | PARTIAL | Commit hashes tracked per step; no auto PR creation or commit metadata injection |
| 9 | Cost-per-task accounting | WORKS | Per-step token counts from Claude CLI; ad-hoc SQL for custom groupings |
| 10 | Retry behavior | WORKS | Bounded rate-limit retries (3x); gate failure is fatal (no retry spiral); safe |
| 11 | Slack notification granularity | PARTIAL | 14+ event topics with glob filtering; requires `--serve` flag; rich format only for decisions |
| 12 | Weekly ROI calculation | PARTIAL | Raw data + SQL + CSV export exist; no developer-hours equiv or dollar conversion |

## Composite Score

- **WORKS:** 5/12 (items 4, 5, 7, 9, 10)
- **PARTIAL:** 6/12 (items 1, 2, 6, 8, 11, 12)
- **BLOCKED:** 1/12 (item 3)

## Delta Summary vs. Solo Audit

The solo audit covered Carlos with 13 evaluation points. This team audit
examined 12 items, of which **5 are entirely new** (items 4, 6, 8, 11
granularity, 12). Key new findings across all items:

1. **Daemon has no step limit** -- `--max-steps` only exists on
   `baton execute run`, not daemon mode. This makes daemon less safe for
   overnight use (Item 3).

2. **Daemon auto-approves gates without running them** -- In daemon
   mode, programmatic gates (test/build/lint) are recorded as
   "auto-approved" without executing the actual command. In
   `baton execute run`, gates run as real shell subprocesses (Item 7).

3. **Rate-limit retries don't consume `--max-steps` budget** -- Retries
   happen inside the launcher before step recording, so they can't
   exhaust the step limit (Item 10).

4. **BudgetTuner improves cost prediction over time** -- The learning
   pipeline recommends budget tier adjustments based on historical usage,
   making cost estimates more accurate with use (Item 2).

5. **`baton dashboard` provides aggregate overnight summary** -- Not
   evaluated in solo audit for Carlos's workflow; it provides the exact
   "morning triage screen" he needs (Item 5).

6. **Non-decision Slack notifications are plain text** -- Only
   `human.decision_needed` events get rich Block Kit formatting; step
   failures go as minimal text attachments (Item 11).

7. **`BATON_TASK_ID` environment variable** enables clean scripted batch
   workflows without complex argument passing (Item 1).

## Critical Path for Carlos's Adoption

To make agent-baton viable for overnight batch execution:

1. **P0 (Blocker):** Add `--token-limit N` flag to both
   `baton execute run` AND `baton daemon start` that converts the
   advisory `_check_token_budget()` into a hard abort.

2. **P1 (High):** Add `baton plan --batch tasks.txt` for first-class
   multi-task planning.

3. **P1 (High):** Enable real gate execution in daemon mode (opt-in
   `--run-gates` flag) so overnight tasks get real test coverage.

4. **P2 (Medium):** Add `--notify-on-failure EMAIL/SLACK_URL` flag to
   `baton execute run` that doesn't require the full API server.

5. **P2 (Medium):** Add a `baton report --period 7d` command that
   computes tasks completed, total cost, and basic efficiency metrics.
