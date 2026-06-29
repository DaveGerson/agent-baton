---
quadrant: explanation
audience: users, maintainers
see-also:
  - [../pillars.md](../pillars.md)
  - [../engine-and-runtime.md](../engine-and-runtime.md)
  - [../storage-sync-and-pmo.md](../storage-sync-and-pmo.md)
---

# Pillar 3 — Right Agent, Right Problem, Right Time

!!! abstract "Pillar context"
    One of [the four pillars](../pillars.md) — the deterministic engine that sequences the work.

> **In one line:** each phase to the specialist that fits it, gated and resumable, from spec to merge.

---

## The vision

The ideal expressed by Pillar 3 is a dispatch loop that is **fully deterministic, fully resumable, and impossible to lose work in**.

- A plan arrives as a `MachinePlan` — a graph of phases, steps, dependencies, and gates.
- The engine walks that graph exactly, dispatching each step to the named specialist and nothing else.
- Between phases an automated QA gate fires. The next phase does not start until it passes.
- Human approval gates pause the loop and wait for a signed decision before advancing.
- If the process crashes at any point, `baton execute resume` picks up from the last persisted state — no steps repeat, no steps are lost.
- Parallel steps with disjoint file scopes run under separate git worktrees so they cannot silently clobber each other.
- The loop runs unattended in daemon mode, and the PMO UI organizes the whole effort from spec import through merged PR.

The long-form expression of that ideal: spec-to-merge with zero information loss, zero duplicated work after a crash, and zero specialist overlap — each agent gets a clean context window focused on exactly one problem.

---

## How it works today

### The state machine and 9 action types

The execution engine (`agent_baton/core/engine/executor.py`) is a **synchronous, stateless state machine**. Every call reads `ExecutionState` from disk, computes the next action, writes updated state atomically (write to `.json.tmp`, rename), and returns. This means a crash between any two calls loses nothing.

The engine returns one of 9 `ActionType` values defined in `agent_baton/models/execution.py`:

| Action | When returned |
|--------|--------------|
| `DISPATCH` | The next step is ready; spawn the named agent with the provided prompt |
| `GATE` | All steps in this phase are complete; run the QA gate command |
| `APPROVAL` | Phase requires human sign-off before advancing |
| `FEEDBACK` | Present multiple-choice questions; the chosen answer dispatches a follow-up step |
| `INTERACT` | Agent asked a clarifying question; pause for human reply, then re-dispatch |
| `WAIT` | Parallel steps are still in flight; call `next_action()` again |
| `COMPLETE` | All phases exhausted; execution is finished |
| `FAILED` | A step or gate failed unrecoverably |
| `CHECKPOINT` | Engine suggests saving state and opening a fresh session to prevent context rot |

The driving session (a Claude Code orchestrator or the headless `TaskWorker`) loops over these actions, records results with `baton execute record` and `baton execute gate`, and advances until `COMPLETE` or `FAILED`.

### The dispatch loop in practice

```
baton plan "task" --save --explain        # writes plan.json + plan.md
baton execute start                        # initialises ExecutionState, returns first action
baton execute run                          # headless loop: dispatches all steps autonomously
baton execute complete                     # writes trace, usage log, retrospective
```

For interactive use, the orchestrator agent drives the loop manually: `baton execute start` → agent reads the action, spawns the specialist, calls `baton execute record` → `baton execute next` → repeat until `COMPLETE`. Full recipe in `docs/orchestrator-usage.md` and `references/baton-engine.md`.

### Crash recovery

State is persisted after every `record_*` call. If the process dies between calls, `baton execute resume` loads the last good state and returns the next action exactly as if nothing happened. Steps already marked `complete` are skipped; steps marked `dispatched` (in-flight at crash time) are recovered by `recover_dispatched_steps()`, which clears the stale marker so the engine re-dispatches them.

Resume also restores the cumulative spend counter from `ExecutionState.run_cumulative_spend_usd` so the token ceiling continues counting from the right baseline rather than resetting to zero.

### Concurrency: `BATON_TASK_ID` and parallel execution

Multiple tasks can run simultaneously. `BATON_TASK_ID` (or `--task-id`) targets a specific execution when several are in flight. Task-ID resolution order: `--task-id` flag → `BATON_TASK_ID` env var → `active-task-id.txt` → error.

Within a task, steps whose `depends_on` sets are satisfied can run in parallel. `engine.next_actions()` returns all currently dispatchable steps in one call. The async `TaskWorker` (`agent_baton/core/runtime/worker.py`) collects this batch, marks each step dispatched, and runs them through `StepScheduler` (`agent_baton/core/runtime/scheduler.py`) — an `asyncio.Semaphore`-bounded pool capped at `max_concurrent` (default 3).

### Worktree isolation

When steps run concurrently, `WorktreeManager` (`agent_baton/core/engine/worktree_manager.py`) creates a separate git worktree for each step under `.claude/worktrees/<task_id>/<step_id>/`. The agent process runs with that worktree as its working directory. On success the worktree is folded back into the parent branch; on failure it is preserved for forensic inspection or developer takeover. Stale worktrees are reclaimed by `gc_stale()` after 72 hours (`BATON_WORKTREE_GC_HOURS`).

The `_WORKTREE_DISCIPLINE_BLOCK` injected into every isolation-mode prompt tells the agent exactly how to operate inside its worktree boundary.

### Team steps and synthesis strategies

A `PlanStep` with a non-empty `team` list is a team step. Each `TeamMember` carries a `member_id` (e.g. `"1.1.a"`), a `role` (lead/implementer/reviewer), and optional intra-step `depends_on` links.

When all members complete, outputs are merged via `SynthesisSpec`:

| Strategy | Behaviour |
|----------|-----------|
| `concatenate` (default) | Join outcomes with `"; "`; collect all files changed |
| `merge_files` | Same but deduplicate `files_changed` |
| `agent_synthesis` | Dispatch a synthesis agent to merge outputs (see gap below) |

Conflict handling when two members modify the same file is controlled by `SynthesisSpec.conflict_handling`: `auto_merge` (default — record in retrospective and complete), `escalate` (surface to human via `APPROVAL`), or `fail`.

Two team backends are available and both are supported (selected via `BATON_TEAMS_BACKEND`):

- **`worktree`** (default): parallel `Agent` calls under git worktree isolation; resumable; full agent frontmatter honored.
- **`claude-teams`** (opt-in): native Agent Teams UX with inter-teammate messaging and shared task list; requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`.

### Selective MCP pass-through

Steps declare which MCP servers they need via `PlanStep.mcp_servers`. Only declared servers are passed to the agent subprocess via `--mcp-config`. Undeclared servers are excluded, preventing input-token bloat from tool schemas the agent will never call.

### Plan amendments mid-flight

If a gate fails or an approver returns feedback, `baton execute amend` inserts new phases or steps into the live plan without stopping execution. Every amendment writes a `PlanAmendment` audit record to `ExecutionState.amendments`. Goal-driven execution (`baton goal "<condition>"`, enabled via `MachinePlan.completion_condition`) automatically runs `amend_plan()` at phase boundaries when the `GoalEvaluator` determines the completion condition has not been met, up to `max_amend_cycles` (default 3) times.

### Headless execution and daemon mode

`baton execute run` uses `HeadlessClaude` (`agent_baton/core/runtime/headless.py`) to drive the full dispatch loop without a Claude Code session — each dispatch calls `claude --print` as a subprocess. This is the mode used by the PMO execute endpoint and by CI pipelines.

`baton daemon` daemonizes the `WorkerSupervisor` (`agent_baton/core/runtime/supervisor.py`) via UNIX double-fork (`agent_baton/core/runtime/daemon.py`). The supervisor manages the PID file, rotating log, and graceful shutdown on SIGTERM/SIGINT (30-second drain window). It is not available on Windows.

A separate `ImmuneDaemon` (`agent_baton/core/immune/daemon.py`) runs background anti-rot sweeps when `BATON_IMMUNE_ENABLED=1`. It ticks every 5 minutes, picking sweep targets from a SQLite queue and routing findings through `FindingTriage`. Its state is resumable after a crash.

### PMO plan-to-merge flow

The PMO REST API (`agent_baton/api/routes/spec_queue.py`) supports a structured plan-to-merge pipeline:

1. Import a spec from GitHub Issues or Azure DevOps, or submit one directly.
2. Enrich it with `DataClassifier` cost forecasting.
3. Senior review: approve or bounce.
4. Fire: trigger headless execution.
5. Monitor: the PMO board tracks per-step and per-phase status in real time.
6. Merge: the `CommitConsolidator` cherry-picks agent commits onto the feature branch; the changelist and attribution are visible in the UI.

---

## The gap today

Three honest gaps between the vision above and the current implementation.

### Gap 1 — Token ceiling warns but does not block individual dispatches (bd-3f80)

`BATON_RUN_TOKEN_CEILING` is a USD ceiling for the cumulative spend of the run. The `BudgetEnforcer` (`agent_baton/core/govern/budget.py`) raises `RunTokenCeilingExceeded` before any **immune-sweep LLM call** that would push cumulative spend past the ceiling.

However, `Executor.dispatch` does **not** call `enforce_run_ceiling()` before firing individual agent dispatches. The ceiling is enforced only through the policy-hook path — a `baton policy-check` hook evaluated at each tool call. If no such hook is wired up, the engine logs a warning at HIGH/CRITICAL risk start (`warn_if_ceiling_unset_for_high_risk`) and appends `TOKEN_BUDGET_WARNING` to step deviations after the fact, but it does not block a DISPATCH action that would exceed the ceiling.

**Practical effect**: on unattended HIGH/CRITICAL runs without a `BATON_POLICY_FAIL_CLOSED=1` policy hook, the ceiling is advisory, not a hard kill. Set `BATON_POLICY_FAIL_CLOSED=1` and wire `baton policy-check` into `PreToolUse` hooks to get hard enforcement.

### Gap 2 — `claude-teams` backend cannot resume in-flight teammates

The `worktree` backend (default) is fully resumable. The `claude-teams` backend is not: `baton execute resume` can reload `ExecutionState` and continue the enclosing plan, but it cannot revive Claude-Teams teammates that were in-flight when the session died. The native Agent Teams protocol has no in-process resumption mechanism.

Additional constraints of the `claude-teams` backend (documented in `agent_baton/core/engine/team_backends.py` and `docs/engine-and-runtime.md` §18): no nested teams, `skills` and `mcpServers` frontmatter on teammate definitions is not honored, one team at a time per lead session, and approximately 7x the token overhead of the worktree path.

`BATON_TEAMS_STRICT_RESUMABILITY=1` causes `baton plan` / `baton goal` to refuse to save a plan with team phases if the claude-teams backend is active and the budget tier is `long-running`. Default (`0`) downgrades to a warning.

### Gap 3 — `agent_synthesis` team strategy is declared but not yet dispatched

`SynthesisSpec.strategy = "agent_synthesis"` is the intended path for having a dedicated synthesis agent merge the outputs of multiple team members into a coherent whole. The enum value, the `synthesis_agent` field, and the `synthesis_prompt` template are all defined in `agent_baton/models/execution.py`. In the current executor (`agent_baton/core/engine/executor.py`), however, the synthesis agent dispatch is not wired: team step auto-completion uses only `concatenate`/`merge_files` semantics. The `agent_synthesis` value is safe to set in a plan but behaves identically to `concatenate` until the dispatch path is implemented.

---

## Where this lives

**Docs**

- `../engine-and-runtime.md` — full reference: state machine, planner, dispatcher, gate system, runtime, crash recovery, team backends
- `../storage-sync-and-pmo.md` — PMO plan-to-merge flow and spec queue
- `../architecture/state-machine.md` — every action type, every status value, every persistence touchpoint

**Code**

- `agent_baton/core/engine/executor.py` — `ExecutionEngine` (state machine, dispatch loop, budget checks)
- `agent_baton/core/engine/dispatcher.py` — `PromptDispatcher` (delegation prompts, worktree discipline block)
- `agent_baton/core/engine/gates.py` — `GateRunner`
- `agent_baton/core/engine/persistence.py` — `StatePersistence` (atomic writes, task-ID resolution)
- `agent_baton/core/engine/worktree_manager.py` — `WorktreeManager`
- `agent_baton/core/engine/team_backends.py` — `WorktreeTeamBackend`, `ClaudeTeamsBackend`
- `agent_baton/core/runtime/worker.py` — `TaskWorker` (async execution loop)
- `agent_baton/core/runtime/scheduler.py` — `StepScheduler` (bounded-concurrency dispatch)
- `agent_baton/core/runtime/headless.py` — `HeadlessClaude` (`baton execute run`)
- `agent_baton/core/runtime/daemon.py` — `daemonize()` (double-fork)
- `agent_baton/core/runtime/supervisor.py` — `WorkerSupervisor` (PID file, graceful shutdown)
- `agent_baton/core/immune/daemon.py` — `ImmuneDaemon` (background anti-rot sweeps)
- `agent_baton/core/govern/budget.py` — `BudgetEnforcer`, `RunTokenCeilingExceeded`
- `agent_baton/models/execution.py` — `ActionType` (9 values), `MachinePlan`, `ExecutionState`, `SynthesisSpec`

**Commands**

```bash
baton plan "<task>" --save --explain          # create plan.json + plan.md
baton execute start                            # initialise state, return first action
baton execute next                             # return next action (interactive loop)
baton execute record --step-id <id> ...        # record step result
baton execute gate --phase-id <id> --result pass|fail
baton execute resume                           # crash recovery
baton execute run                              # headless autonomous loop
baton daemon start                             # daemonised autonomous execution
baton daemon status                            # list running daemon workers
```
