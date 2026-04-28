# State Machine (Reference)

> **Audience.** Engineers debugging execution flow, writing engine
> tests, or implementing alternative drivers. This page enumerates
> every action type, every state transition, and every persistence
> touchpoint that the engine performs. For *why* the design is shaped
> this way, see [../architecture.md](../architecture.md). For the
> driving CLI loop, see
> [../engine-and-runtime.md](../engine-and-runtime.md).

---

## 1. The action enum

`ActionType` is defined at
[`agent_baton/models/execution.py:60`](../../agent_baton/models/execution.py).
It is an `enum.Enum`; values are lowercase strings used in JSON and on
the wire.

| Action | String | Meaning |
|--------|--------|---------|
| `DISPATCH` | `"dispatch"` | Spawn a subagent (or run an automation step). Carries `agent_name`, `agent_model`, `step_id`, `delegation_prompt`. |
| `GATE` | `"gate"` | Run a QA gate (test/build/lint/spec/review/ci). Carries `gate_type`, `gate_command`, `phase_id`. |
| `APPROVAL` | `"approval"` | Pause for human approval. Carries `phase_id`, `approval_context`, `approval_options`. |
| `FEEDBACK` | `"feedback"` | Present multiple-choice questions; the chosen option dispatches a follow-up step. Carries `phase_id`, `feedback_context`, `feedback_questions`. |
| `INTERACT` | `"interact"` | Multi-turn agent dialogue: the agent asked a clarifying question; pause for human reply. Carries `interact_step_id`, `interact_agent_name`, `interact_turn`, `interact_max_turns`, `interact_prompt`. |
| `WAIT` | `"wait"` | Parallel steps still in flight; caller should poll `next_action()` again. |
| `COMPLETE` | `"complete"` | Execution finished successfully. Carries `summary`. |
| `FAILED` | `"failed"` | Execution cannot continue. Carries `summary`. |
| `SWARM_DISPATCH` | `"swarm.dispatch"` | Wave 6.2 (bd-2b9f): trigger a `SwarmDispatcher` reconciliation run. |

The enum is consumed by `_print_action()` in
[`cli/commands/execution/execute.py:568`](../../agent_baton/cli/commands/execution/execute.py)
and by `TaskWorker` in
[`core/runtime/worker.py`](../../agent_baton/core/runtime/worker.py).

---

## 2. Step and phase status enums

Two more enums in the same module track per-step and per-phase progress
inside `ExecutionState`. Their values appear in `step_results` and the
phase progression of `current_phase`.

`StepStatus` ([`models/execution.py:37`](../../agent_baton/models/execution.py)):

| Value | When set |
|-------|---------|
| `pending` | Default; step has not yet been dispatched |
| `running` | Dispatched; agent is in flight |
| `complete` | `record_step_result(status="complete")` was called |
| `failed` | `record_step_result(status="failed")` was called |
| `skipped` | Step intentionally bypassed (e.g. amendment) |
| `interrupted` | Worker shutdown caught a signal mid-step |
| `interacting` | Agent emitted INTERACT; awaiting human reply |
| `interact_dispatched` | Human replied; agent re-dispatched for next turn |

`PhaseStatus` ([`models/execution.py:50`](../../agent_baton/models/execution.py)):

| Value | When set |
|-------|---------|
| `pending` | Phase not yet started |
| `running` | At least one step dispatched |
| `gate_pending` | All steps complete; gate not yet run |
| `complete` | Gate passed (or no gate); phase advanced |
| `failed` | Gate failed past retry cap |

`ExecutionState.status` is a free-form string with the canonical values
`running`, `gate_pending`, `approval_pending`, `feedback_pending`,
`complete`, `failed`, `cancelled`, `paused-takeover`, `budget_exceeded`
(see [`models/execution.py:1323`](../../agent_baton/models/execution.py)).

---

## 3. Transition table

Each row describes a single engine call; the action returned is what the
caller does next. Transitions occur in
[`core/engine/executor.py`](../../agent_baton/core/engine/executor.py).

| Caller invokes | Engine method | Inspects | Returns / next action |
|----------------|---------------|----------|----------------------|
| `baton execute start` | `start(plan)` (executor.py:1301) | New plan | `DISPATCH` for first ready step, or `COMPLETE` if plan is empty |
| `baton execute next` | `next_action()` (executor.py:1503) | Phase, deps, in-flight set | `DISPATCH` / `GATE` / `APPROVAL` / `FEEDBACK` / `INTERACT` / `WAIT` / `COMPLETE` / `FAILED` |
| (parallel poll) | `next_actions()` (executor.py:1539) | All ready steps | `list[DISPATCH]` (one per dispatchable step) |
| `baton execute dispatched` | `mark_dispatched(step_id, agent_name)` | Step exists | (none — state mutation only) |
| `baton execute record` | `record_step_result(...)` (executor.py:1730) | Outcome text | (none — parses KNOWLEDGE_GAP, BEAD_*, fires events) |
| `baton execute gate` | `record_gate_result(phase_id, passed, output)` (executor.py:2454) | Pass/fail | (none — advances phase or sets `failed`) |
| `baton execute approve` | `record_approval_result(phase_id, result, feedback)` (protocols.py:132) | `approve` / `reject` / `approve-with-feedback` | (none — `approve` resumes; `reject` fails; feedback inserts amendment) |
| `baton execute feedback` | `record_feedback_result(phase_id, question_id, chosen_index)` | Chosen option | (none — inserts dispatch step via amendment) |
| `baton execute amend` | `amend_plan(...)` (protocols.py:170) | New phases/steps | Returns `PlanAmendment` |
| `baton execute team-record` | `record_team_member_result(...)` (protocols.py:204) | Member outcome | (none — when last member done, parent step completes) |
| `baton execute interact` | `provide_interact_input(...)` / `complete_interaction(...)` (protocols.py:261, 282) | Human input | (none — flips status to `interact_dispatched` or `complete`) |
| `baton execute resume` | `resume()` (executor.py:3440) | On-disk state | The action that was pending when the session crashed |
| `baton execute complete` | `complete()` (executor.py:3121) | Final state | Returns summary string; writes trace, usage, retro |
| `baton execute status` | `status()` | State | Returns dict (task_id, status, progress, gates) |

The full method list lives on `ExecutionDriver`
([`core/engine/protocols.py:22`](../../agent_baton/core/engine/protocols.py))
— 15 methods. `TaskWorker.__init__` accepts `engine: ExecutionDriver`,
not the concrete `ExecutionEngine`, so any protocol-conforming object can
drive a worker (ADR-03).

---

## 4. Phase progression

A plan is a list of `PlanPhase`. The engine advances phases sequentially:

```
phase 0
  step 1.1 -> step 1.2 -> ... -> all complete
  (optional) APPROVAL
  (optional) GATE
  passed?  yes -> advance to phase 1
            no  -> retry (up to _max_gate_retries=3) or FAIL
phase 1
  ...
phase N
  complete() -> writes trace + usage + retro -> auto-sync
```

Within a phase, steps may run in parallel. `next_actions()` returns every
step whose `depends_on` is satisfied and that is not already dispatched,
complete, or failed. The caller can launch all of them concurrently;
`StepScheduler` ([`core/runtime/scheduler.py`](../../agent_baton/core/runtime/scheduler.py))
caps simultaneous launches at `max_concurrent` (default 3).

When a phase has an `approval_required: true` flag *and* a gate, the
engine emits `APPROVAL` first, then `GATE`. Either failing fails the
phase.

---

## 5. Persistence touchpoints

State is written to disk after **every** mutation. A single `record_*`
call may trigger several writes; failures are logged but do not raise
through to the caller (graceful degradation, rule 5).

| Mutation point | What gets written | Where |
|----------------|-------------------|-------|
| `start()` | `ExecutionState` (full) | SQLite `executions` + JSON `execution-state.json` |
| `mark_dispatched()` | `step_results` row, status=`dispatched` | SQLite `step_results` + JSON |
| `record_step_result()` | `step_results` row, status=final | SQLite `step_results` + JSON |
| `record_step_result()` (side) | parsed beads → `BeadStore.create()` | SQLite `beads` + `bead_tags` |
| `record_step_result()` (side) | parsed knowledge gap → `pending_gaps` | JSON state |
| `record_step_result()` (side) | event publish | EventBus subscribers (incl. `EventPersistence` JSONL) |
| `record_gate_result()` | `gate_results` row | SQLite `gate_results` + JSON |
| `record_approval_result()` | `approval_results` row | SQLite `approval_results` + JSON |
| `record_feedback_result()` | `feedback_results` + `PlanAmendment` | SQLite `feedback_results`, `amendments` + JSON |
| `amend_plan()` | `PlanAmendment`, mutated `plan` | SQLite `amendments`, `plans` + JSON |
| `complete()` | trace, usage, retro, consolidation_result, `completed_at` | SQLite + `traces/` + `usage-log.jsonl` + `retrospectives/` |

Atomic-write contract:

- **JSON** files use tmp+rename
  ([`persistence.py:83`](../../agent_baton/core/engine/persistence.py)).
  On Windows, `Path.replace()` retries up to 5× with 50 ms backoff to
  tolerate antivirus / search-indexer holds.
- **SQLite** uses WAL mode with busy timeout
  ([`core/storage/connection.py`](../../agent_baton/core/storage/connection.py)).
- **Auto-sync** to `central.db` runs *after* `complete()` returns,
  wrapped in `try/except`. Sync failure never blocks completion.

The legacy flat path is `<context_root>/execution-state.json`. The
namespaced path is
`<context_root>/executions/<task-id>/execution-state.json`.
`StatePersistence.set_task_id()` (persistence.py:70) recomputes the
state path atomically — direct mutation of `_task_id` would leave the
path stale.

---

## 6. Crash recovery

On any crash mid-execution, the next CLI call is `baton execute resume`,
which runs `engine.resume()` (executor.py:3440). The engine:

1. Loads `ExecutionState` from disk via `StatePersistence.load()`.
2. Re-attaches the in-memory trace (zero-events; all prior events are in
   the `events.jsonl` log, replayable via `EventPersistence`).
3. Calls `_determine_action()` to recompute the action that was pending.
4. Returns it.

There is no "in-progress" step state to repair: a step is either
dispatched (return `DISPATCH` again — the agent will re-run; idempotency
is the agent's responsibility, enforced by the worktree-isolation
contract in [`core/engine/worktree_manager.py`](../../agent_baton/core/engine/worktree_manager.py))
or recorded as terminal (engine moves on).

For human-in-the-loop recovery (`takeover`, `self-heal`, `speculate`),
see [../engine-and-runtime.md §5](../engine-and-runtime.md).

---

## 7. Where to find each rule

| Rule | Code |
|------|------|
| Action types are an enum, not strings | [models/execution.py:60](../../agent_baton/models/execution.py) |
| Engine returns `ExecutionAction`, not raw dicts | [models/execution.py:1486](../../agent_baton/models/execution.py) |
| Driver contract is a `Protocol` | [core/engine/protocols.py:22](../../agent_baton/core/engine/protocols.py) |
| State writes are atomic | [core/engine/persistence.py:83](../../agent_baton/core/engine/persistence.py) |
| WAL mode for SQLite | [core/storage/connection.py](../../agent_baton/core/storage/connection.py) |
| Auto-sync is best-effort | `auto_sync_current_project()` in [core/storage/sync.py](../../agent_baton/core/storage/sync.py) |
| `_print_action()` is the wire format | [cli/commands/execution/execute.py:568](../../agent_baton/cli/commands/execution/execute.py) |
