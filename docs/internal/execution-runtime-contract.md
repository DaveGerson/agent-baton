# Execution Runtime Contract — one lifecycle, four surfaces

**Status:** Draft
**Step:** Phase 2, 2.1 (architect) — agent-baton middle-manager hardening plan
**Scope:** `agent_baton/core/engine/executor.py`, `agent_baton/core/runtime/worker.py`,
`agent_baton/core/runtime/decisions.py`, `agent_baton/cli/commands/execution/{execute,run,daemon}.py`,
`agent_baton/api/routes/{pmo,decisions}.py`
**Non-goals of this document:** it does not change any of the above files. It is the
contract those files are audited against; implementation work to close the gaps
identified in §7 is intentionally left to later steps.

---

## 1. Why this document exists

Agent-baton has **four places that drive an execution forward**:

1. The CLI action loop (`baton execute {start,next,record,gate,approve,...}`),
   driven one command at a time by an orchestrator agent.
2. The CLI autonomous loop, which exists **twice**: `baton execute run` (a
   hand-rolled synchronous loop inside `execute.py`) and the top-level
   `baton run` (a thin wrapper around `TaskWorker` via `BatonRunner`).
3. The daemon `TaskWorker`, driven by `WorkerSupervisor` (`baton daemon start`),
   optionally paired with the REST API in the same process (`--serve`).
4. The REST API / PMO UI, which launches `baton execute run` as a detached
   subprocess (`POST /pmo/execute/{card_id}`) and controls it out-of-band via
   OS signals (`/pause`, `/resume`, `/cancel`) and a file-based decision queue
   (`/decisions`).

All four surfaces ultimately read and write the **same** persisted
`ExecutionState` (via `ExecutionEngine` / `StatePersistence` / the SQLite
project storage). That single state machine is the actual source of truth;
this document names it explicitly, assigns owners to each moving part, and
defines what "the same lifecycle" means well enough to test.

---

## 2. The canonical lifecycle

Every execution surface must be describable as a walk over this state
machine. Stage names on the left are the vocabulary this document (and the
test matrix in §8) uses; the columns on the right map each stage onto the
concrete types that already exist in the codebase — this is a naming and
ownership exercise, not a new state machine.

| # | Stage | `ExecutionState.status` before → after | `ActionType` returned by `next_action()` | Engine method that performs the transition |
|---|-------|------------------------------------------|-------------------------------------------|----------------------------------------------|
| 0 | **Start** | `pending` (implicit) → `running` (or `approval_pending` for a HIGH-risk pre-flight) | — | `ExecutionEngine.start(plan)` |
| 1 | **Dispatch** | `running` → `running` (step marked `dispatched`) | `DISPATCH` | `next_action()` / `next_actions()` + `mark_dispatched()` |
| 2 | **Persist result** | `running` → `running` (step marked `complete`/`failed`) | — | `record_step_result()` / `record_team_member_result()` |
| 3 | **Gate** | `running` → `gate_pending` → `running` (pass) or `gate_failed`/`failed` (fail) | `GATE` | `record_gate_result()` |
| 4 | **Request decision** | `running`/`gate_pending` → `approval_pending` / `feedback_pending` (or a `DecisionRequest` file for a `review`-type gate under async surfaces) | `APPROVAL` / `FEEDBACK` (sync) or a `human_decision_needed` event (async) | heavy builder inside `next_action()`, or `TaskWorker._handle_gate` / `_handle_approval` |
| 5 | **Pause** | *(see §6 — not a status value today)* | — | process-level only: `WorkerSupervisor.pause_worker()` |
| 6 | **Record decision** | `approval_pending`/`feedback_pending`/`gate_failed` → `running` (approve) or `failed` (reject) | — | `record_approval_result()` / `record_feedback_result()` / `DecisionManager.resolve()` + gate/approval poll loop |
| 7 | **Resume same task** | *(any non-terminal status)* → same status, trace reattached | whatever `next_action()` now returns | `ExecutionEngine.resume()` |
| 8 | **Complete** | `running`/blocked → `complete` / `failed` / `cancelled` | `COMPLETE` / `FAILED` | `complete()` / `transition_to_failed()` / `transition_to_cancelled()` |

Stages 1–4 and 6–8 are not a strict sequence — the resolver
(`agent_baton/core/engine/resolver.py`) and the per-status handler classes in
`states.py` (`ExecutingPhaseState`, `AwaitingApprovalState`, `TerminalState`)
already define the legal transition graph and raise `RuntimeError` on an
illegal one. This document does not re-derive that graph; it asserts that
**every surface must reach it through the same engine calls**, never through
a parallel status mutation.

### 2.1 Status vocabulary (from `agent_baton/models/execution.py`)

`pending`, `running`, `gate_pending`, `gate_failed`, `approval_pending`,
`feedback_pending`, `interacting`, `paused-takeover`, `budget_exceeded`,
`complete`, `failed`, `cancelled`, and the dormant `paused` (§6).

---

## 3. Authoritative owners

| Component | Owns | Does **not** own |
|---|---|---|
| `ExecutionEngine` (`core/engine/executor.py`) | The single writable copy of `ExecutionState`; all status transitions; task-level and phase-level event emission (`task.*`, `phase.*`, `gate.passed`/`gate.failed`); plan amendment; trace lifecycle. | Process lifecycle (PID files, signals); how/whether an agent subprocess is actually launched; human-facing decision routing. |
| `TaskWorker` (`core/runtime/worker.py`) | The async dispatch loop for **one** task: turning `DISPATCH` actions into concurrent agent launches via `StepScheduler`/`AgentLauncher`, running programmatic gates as subprocesses, and routing human-required gates/approvals to `DecisionManager` (or auto-approving when none is configured). Step-level event emission (`step.*`, `gate.pre_check`). | Persisted state — every mutation goes back through the `ExecutionDriver` methods on the engine it wraps. Process supervision (that's `WorkerSupervisor`). |
| `WorkerSupervisor` (`core/runtime/supervisor.py`) | Process lifecycle for daemon mode: PID file + `flock`, log rotation, SIGTERM/SIGINT drain, `pause_worker`/`resume_worker`/`cancel_worker` (OS signals), `daemon-status.json` snapshots, `recover_dispatched_steps()` after a crash. | State transitions — it constructs a `TaskWorker`/`ExecutionEngine` pair and gets out of the way. |
| `DecisionManager` (`core/runtime/decisions.py`) | The file-based human-decision queue used by async surfaces (daemon `TaskWorker`, interactive `BatonRunner`): `DecisionRequest`/`DecisionResolution` JSON + Markdown sidecars under `<context_root>/decisions/`, and `human_decision_needed`/`human_decision_resolved` events. | The execution status itself — resolving a decision only unblocks the poll loop in `TaskWorker._handle_gate`/`_handle_approval`, which then calls back into the engine's `record_gate_result`/`record_approval_result`. |
| State storage (`core/storage/` + `core/engine/persistence.py::StatePersistence`) | Durable read/write of `ExecutionState`, keyed by `task_id`, with a SQLite-primary / JSON-file-fallback strategy and reconciliation on divergence (see `ExecutionEngine.resume()`). | Deciding *when* to persist — the engine calls `_save_execution()` after every mutating call; storage never persists speculatively. |
| Event emission (`core/events/bus.py` + `core/events/events.py`) | Typed builders for every documented topic (`task.*`, `phase.*`, `step.*`, `gate.*`, `human_decision_*`, `approval_*`, `budget_exceeded`, `plan_amended`, `team_member_completed`). | Delivery guarantees beyond in-process pub/sub — SSE and webhook fan-out are downstream consumers (`api/routes/pmo.py::stream_pmo_events`, `api/webhooks/dispatcher.py`), not part of this contract. |
| REST API / PMO (`api/routes/pmo.py`, `api/routes/decisions.py`) | Launching new executions (`POST /pmo/execute/{card_id}`, by shelling out to `baton execute run`), process-level pause/resume/cancel, and a decision inbox UI (`GET/POST /decisions*`) that is a thin wrapper over `DecisionManager`. | The state machine itself. The API never mutates `ExecutionState` directly; every route either spawns a CLI subprocess, signals a PID, or delegates to `DecisionManager`. |

**Rule:** any new code path that needs to change execution status must go
through an `ExecutionEngine`/`ExecutionDriver` method. Direct writes to
`state.status` outside `models/execution.py` are already blocked by
`tests/static/test_no_direct_status_writes.py`; this document is the process
contract that lint enforces mechanically.

---

## 4. Idempotency semantics

| Operation | Idempotent? | Mechanism |
|---|---|---|
| `ExecutionEngine.start(plan)` on a `task_id` that already has a **non-terminal** persisted execution | **No — by design, it is refused, not silently repeated.** `baton execute run` / `baton execute start` resolve the active task first (`--task-id` → `BATON_TASK_ID` → SQLite active-task pointer → `active-task-id.txt`) and treat any status in `{running, pending, approval_pending, feedback_pending, gate_pending, gate_failed, budget_exceeded}` as *resume, don't restart* (`_RESUMABLE_STATUSES` in `execute.py`). A terminal status (`complete`/`failed`/`cancelled`) raises a user-facing error instead of silently overwriting history. |
| `ExecutionEngine.next_action()` / `next_actions()` called repeatedly with no new results recorded | **Yes.** Steps already in `dispatched_step_ids` are excluded from re-dispatch; the same `DISPATCH`/`GATE`/`APPROVAL` action is returned until a result is recorded. |
| `record_step_result()` called twice for the same `step_id` | **Not idempotent — second call is a bug in the caller.** The engine does not currently de-duplicate by `step_id`; a duplicate `record` call appends a second `StepResult`. Both `TaskWorker` and the CLI loop guarantee single-call-per-dispatch by construction (a step is only in the dispatch set once), but there is no engine-side guard. **Gap — see §7.1.** |
| `record_gate_result()` on a phase whose gate has already passed | **Not idempotent** in the same sense — a second `pass` is a no-op-ish re-advance (harmless because `advance_phase()` is itself idempotent once the phase pointer has moved), but a second call after a `fail` retries the gate-retry counter. Callers should not call this more than once per gate evaluation. |
| `DecisionManager.resolve()` on an already-resolved request | **Yes.** Returns `False` without mutating anything (`req.status != "pending"` guard). The API route (`POST /decisions/{id}/resolve`) surfaces this as `409` if the second caller raced past the `get()` check, or `400` if `get()` already saw `resolved`. |
| `ExecutionEngine.resume()` called on an already-terminal task | **Yes.** Returns the same `COMPLETE`/`FAILED` action every time; does not re-run anything. |
| `WorkerSupervisor.pause_worker()` / `resume_worker()` called twice in a row | **Yes at the OS level** (a second `SIGSTOP` to an already-stopped process, or `SIGCONT` to an already-running one, is a no-op), **but not observable in `ExecutionState`** — see §6. |
| `POST /pmo/execute/{card_id}` called twice for the same card | **No.** The route only checks `card.column == "queued"`; it does not check for an already-running worker PID for that `task_id`. Calling it twice launches two `baton execute run` subprocesses against the same `task_id`. Both subprocesses hit the same active-task resolution + `_RESUMABLE_STATUSES` guard in `_handle_run`, so the second process will *not* restart the plan, but the two processes will race on `next_action()`/`record_step_result()` with no cross-process lock beyond the SQLite OCC retry (`_save_execution_with_occ_retry`). **Gap — see §7.2.** |

---

## 5. Restart semantics

"Restart" here means: the process driving execution dies (crash, `SIGKILL`,
container restart, CLI process exits between commands) and a **new** process
picks the same `task_id` back up.

1. **State is durable at every transition boundary**, not just at pause
   points. `ExecutionEngine._save_execution()` runs after `start()`,
   `mark_dispatched()`, every `record_*` call, `record_gate_result()`,
   `record_approval_result()`, and `complete()`. There is no separate
   "checkpoint" concept — the persisted `ExecutionState` *is* the checkpoint,
   always current as of the last completed engine call.
2. **The one blind spot is mid-dispatch.** If a step was marked `dispatched`
   (§ stage 1) but the process died before `record_step_result()` (§ stage 2)
   ran, the step is stuck `dispatched` on disk with no agent actually
   running. Recovery is explicit, not automatic:
   - `WorkerSupervisor.start(resume=True)` calls
     `ExecutionEngine.recover_dispatched_steps()` before resuming, which
     clears `dispatched`-status step results back to re-dispatchable
     (verified by `tests/test_daemon.py::TestRecoverDispatchedSteps`).
   - `baton execute run` / `baton execute resume` do **not** currently call
     `recover_dispatched_steps()` — a step left `dispatched` by a killed CLI
     process stays stuck until an operator notices. **Gap — see §7.3.**
3. **Restart uses `resume()`, never `start()`.** `ExecutionEngine.resume()` is
   the one restart entry point implemented by all four surfaces:
   - CLI: `baton execute resume` calls it directly; `baton execute run` calls
     `next_action()` on an already-`_RESUMABLE_STATUSES` task, which is
     equivalent (both load from disk and reattach the trace).
   - Daemon: `WorkerSupervisor.start(resume=True)` → `engine.resume()`.
   - Top-level `baton run --resume`: `ExecutionEngine()` (no `plan=`) →
     `engine.start()` is skipped, `TaskWorker.run()` calls `next_action()`
     immediately, which again loads from disk.
   - API: does not call `resume()` itself; it launches a **new**
     `baton execute run` subprocess, which performs the same active-task
     resolution as the plain CLI path above.
4. **Reconciliation on split brain.** When both a SQLite backend and a file
   backend hold state for the same `task_id` (possible if a previous write
   partially failed), `resume()` compares per-step statuses and promotes
   whichever backend is more advanced (`_reconcile_states`). This is the
   mechanism that makes "restart" safe across the SQLite/file dual-write
   design without a distributed lock.

---

## 6. The pause-and-resume contract (as it exists today)

This is the part of the lifecycle that is **not yet uniform**, and the task
that produced this document calls it out explicitly, so it gets its own
section rather than being folded into §4/§5.

There are, today, **two unrelated things called "pause"**:

1. **Process-level pause** (`WorkerSupervisor.pause_worker`/`resume_worker`,
   surfaced at `POST /pmo/execute/{card_id}/pause`|`/resume`). This sends
   `SIGSTOP`/`SIGCONT` to the daemon worker's OS process. It freezes the
   process's scheduler slot; it does **not** write anything to
   `ExecutionState`. A paused worker's persisted status is whatever it was
   at the last completed engine call before the signal arrived (per §5.1),
   and it stays exactly that until the process is resumed. Publishes
   `task.paused`/`task.resumed` events directly via `Event.create(...)` in
   `api/routes/pmo.py`, *not* through a typed builder in `core/events/events.py`
   (every other task-level event has one).
2. **A `"paused"` status value** already exists in `ExecutionState` and is
   wired into the state-handler dispatch table
   (`executor.py`: `"paused": AwaitingApprovalState()`), but **nothing in the
   codebase ever sets it.** It is reserved, not implemented — grep confirms
   the only two writers of `status = "paused-takeover"` (developer takeover,
   Wave 5.1) exist, but no code path assigns the bare `"paused"` string.

**Why this still counts as "durable" today:** because of §5.1, the *durable*
unit is not "pause" — it is "the last completed step/gate/approval". A
worker that is `SIGSTOP`-frozen has, by construction, already persisted
everything up to that point; `SIGCONT` (or a fresh process calling
`resume()`) picks up exactly there. So the pause-and-resume contract holds
**as an emergent property of restart semantics**, not because pause is a
first-class state. That is a real gap for observability (a paused execution
reports `status: "running"` everywhere — PMO board, `baton daemon status`,
`baton execute status` — with no signal that it is actually frozen) and for
any surface that does not have OS-level process control (the CLI action-loop
surface has no pause primitive at all; an orchestrator agent driving `baton
execute next` step by step simply... doesn't call `next` again).

**Recommendation for the implementation step that follows this document**
(explicitly out of scope here): promote pause to a first-class,
engine-owned transition —
`ExecutionEngine.pause(reason: str) -> None` sets `status = "paused"` via a
new `ExecutionState.transition_to_paused()` (coupled-field write, per the
I1/I2/I9 discipline in `core/engine/CLAUDE.md`), and
`ExecutionEngine.resume()` already handles any non-terminal status, so no
change is needed there. `WorkerSupervisor.pause_worker`/`resume_worker`
would call `engine.pause()`/rely on `next_action()`'s normal resume path
*before* sending the OS signal, so the persisted state and the OS process
state can never disagree, and every surface (not just daemon+PMO) gains a
pause primitive. `task.paused`/`task.resumed` should move to typed builders
in `core/events/events.py` alongside the rest of the task-level events.

---

## 7. Known gaps (compensating controls, not fixes — tracked here for the next step)

### 7.1 `record_step_result()` has no duplicate-call guard
No engine-side idempotency check exists for calling `record_step_result()`
twice with the same `step_id`. Compensating control: both `TaskWorker` and
the CLI loop only call `record_step_result()` once per dispatch by
construction (a step leaves the dispatchable set the instant it is marked
`dispatched`). Recommendation: add a guard that rejects (or upgrades to a
warning + no-op) a `record_step_result()` call for a `step_id` that already
has a terminal (`complete`/`failed`) `StepResult`.

### 7.2 `POST /pmo/execute/{card_id}` has no PID-collision guard
The route checks `card.column == "queued"` but not whether a worker is
already running for that `task_id`. Compensating control: the SQLite OCC
retry (`_save_execution_with_occ_retry`) prevents silent data loss on a
concurrent write race, but two live subprocesses double the resource cost
and produce confusing duplicate agent dispatches until one of them loses the
race. Recommendation: check `<context_root>/executions/{card_id}/worker.pid`
for a live PID before spawning, mirroring the daemon's own single-instance
check in `cli/commands/execution/daemon.py`.

### 7.3 `baton execute run`/`baton execute resume` don't call `recover_dispatched_steps()`
Only the daemon's `WorkerSupervisor.start(resume=True)` path clears stuck
`dispatched` steps before resuming. A CLI process killed mid-dispatch leaves
that step stuck until an operator runs the daemon path or manually
intervenes. Recommendation: call `recover_dispatched_steps()` from the same
resume branches identified in §5.3 for the CLI surfaces.

### 7.4 Duplicate top-level `baton run` vs `baton execute run` (compatibility plan)

Two independent implementations exist:

| | `baton run` (`cli/commands/execution/run.py`) | `baton execute run` (`_handle_run` in `cli/commands/execution/execute.py`) |
|---|---|---|
| Dispatch mechanism | `TaskWorker` (async, `StepScheduler`, bounded concurrency) | Hand-rolled synchronous `while True` loop (`_run_loop`), one step at a time |
| Gate/approval routing | `TaskWorker._handle_gate`/`_handle_approval` → `InteractiveDecisionManager` (blocking `input()` prompt) | Inline in `_run_loop` — separately implemented subprocess-based gate execution and `input()`-based approval prompt |
| Active-task resolution | None — always requires an explicit plan or `--resume`, does not consult the SQLite/file active-task marker | Full resolution chain (`--task-id` → env → SQLite → file), plus the `_RESUMABLE_STATUSES`/`_TERMINAL_STATUSES` restart guard from §4 |
| Parallel dispatch | Yes, via `TaskWorker`/`StepScheduler` | No — one step at a time |
| Registered as | Top-level command (`baton run`) | Subcommand of `baton execute` |
| Consumers | Documented in `docs/cli-reference.md` as the "autonomous foreground" entry point | Used internally by `api/routes/pmo.py::execute_card` to launch headless execution |

These two loops can and do diverge (different gate semantics, different
resume guards, different parallelism), which is exactly the "duplicate
top-level `baton run` surface" this plan step is asked to produce a
compatibility plan for. **Compatibility plan (for the implementation step
that follows this document):**

1. **Do not remove either CLI verb in this cycle.** `baton run` and
   `baton execute run` are both public, documented commands; removing either
   is a breaking CLI change requiring a deprecation cycle per the root
   `CLAUDE.md` compatibility rule ("Preserve public CLI ... compatibility
   unless a step explicitly defines a migration").
2. **Converge the implementation, not the surface.** Re-point `baton run`'s
   handler at the same active-task-resolution + `_RESUMABLE_STATUSES` guard
   that `_handle_run` already implements (extract that block into a shared
   helper in `cli/commands/execution/_run_shared.py` or similar), so both
   verbs make identical decisions about "resume vs. refuse vs. start fresh".
   `baton run` keeps its `TaskWorker`-based parallel dispatch (it is the more
   capable of the two); `baton execute run` keeps its simpler synchronous
   loop for now, but both drive the engine through identical `ExecutionDriver`
   calls, so their persisted end state is provably the same shape.
3. **Mark `baton run` as the long-term canonical autonomous entry point** in
   `docs/cli-reference.md` (deprecation note only, no functional change), and
   have `api/routes/pmo.py::execute_card` continue shelling out to whichever
   verb wins the convergence in step 2 — that call site is oblivious to which
   command it invokes, so the migration is contained to the CLI/runner layer.
4. **Do not silently alias one to the other** in this step: `baton run`
   lacks `--dry-run`'s max-steps-abort ergonomics and the VETO
   override flags (`--force`/`--justification`) that `execute run` has;
   aliasing before those are ported would be a silent capability regression.

---

## 8. State-transition and compatibility test matrix

This table is the index for the executable tests added alongside this
document. "Surface" columns marked `✓` have a passing test in the listed
file that exercises stage in the canonical lifecycle (§2) through that
surface's real entry point (not a mock of the engine).

| Stage (§2) | CLI action-loop | CLI `execute run` | Daemon `TaskWorker` | REST API / decisions |
|---|---|---|---|---|
| 0 Start / resume-vs-restart guard | `tests/test_execute_run.py::TestMissingPlanFile`, `TestPlanLoading` (existing) | `tests/test_execute_run.py::TestLifecycleContract::test_resumable_status_is_resumed_not_restarted`, `test_terminal_status_refuses_restart` (new) | `tests/test_daemon.py::TestSupervisorResume` (existing) | — (API delegates to CLI subprocess) |
| 1–2 Dispatch → persist result | — (covered by engine unit tests outside this step's scope) | `tests/test_execute_run.py` (existing dry-run tests) | `tests/test_daemon.py::TestWorkerDecisionIntegration` (existing) | — |
| 3 Gate | — | — | `tests/test_daemon.py::TestWorkerDecisionIntegration::test_auto_approve_for_test_gate` (existing) | — |
| 4 Request decision | — | — | `tests/test_daemon.py::TestWorkerDecisionIntegration::test_review_gate_creates_decision_request` (existing) | `tests/test_api_decisions.py::TestListDecisions`, `TestGetDecision` (existing) |
| 5 Pause | — | — | `tests/test_daemon.py::TestSharedLifecycleContract::test_pause_does_not_mutate_persisted_status`, `test_pause_then_resume_signal_roundtrip` (new — documents §6's gap) | — |
| 6 Record decision | — | — | `tests/test_daemon.py::TestWorkerDecisionIntegration::test_review_gate_reject_marks_failed` (existing) | `tests/test_api_decisions.py::TestResolveDecision` (existing), `TestDecisionResolveIdempotency` (new — event-emission idempotency) |
| 7 Resume same task | `tests/test_execute_run.py::TestLifecycleContract` (new) | — | `tests/test_daemon.py::TestSupervisorResume`, `TestRecoverDispatchedSteps` (existing) | — |
| 8 Complete | (existing dry-run tests reach `COMPLETE`) | (existing dry-run tests reach `COMPLETE`) | `tests/test_daemon.py::TestSharedLifecycleContract::test_worker_and_direct_engine_calls_reach_equivalent_terminal_state` (new — cross-surface equivalence) | — |
| Compat: two decision systems | — | — | — | `tests/test_approval_workflow.py::TestApprovalLogVsDecisionManagerIndependence` (new — documents that PMO's `approval_log` audit trail and `DecisionManager`'s gate/approval queue are two independent, non-conflicting systems keyed by `task_id`, per §3) |

New tests added by this step are characterization tests: they pin down
*current* behavior (including the gaps in §7) so that the follow-up
implementation step has a green baseline to work from and cannot
regress the parts of the contract that already hold.

---

## 9. Summary — what "one shared lifecycle" means operationally

- One writer of execution status: `ExecutionEngine`, via `ExecutionDriver`.
- One persistence layer: `StatePersistence` + SQLite project storage, with
  `resume()` as the single reconciliation/restart entry point.
- One decision queue for anything requiring human input in an unattended
  context: `DecisionManager`, file-backed, polled by whichever async surface
  is waiting.
- Every surface differs only in **how it drives** the engine (one command at
  a time vs. an autonomous loop vs. an async worker vs. a subprocess spawned
  by the API) — never in **what the engine does** once driven.
- Pause is durable today only as an emergent property of "state is saved
  after every completed step" (§5.1), not as an explicit status (§6) — this
  is the one place the four surfaces are not yet uniform, and §7.4 is the
  concrete compatibility plan for the other asymmetry this step was asked to
  resolve (the duplicate `baton run` entry points).
