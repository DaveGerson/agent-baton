# Incident: Execution Permanently Stuck Due to Persistence Split-Brain

**Date:** 2026-04-17
**Severity:** Execution failure (required manual Python intervention to recover)

## What Happened

During execution of a baton plan, the orchestrator hit a split-brain persistence bug that caused the execution to get permanently stuck:

1. `baton execute dispatched --step 1.1` called `mark_dispatched()`, which called `record_step_result(status="dispatched")`. This loaded the current state, appended a "dispatched" `StepResult`, and called `save_execution()`. SQLite wrote the row successfully. The file dual-write also succeeded. Both backends agreed: step 1.1 = "dispatched".

2. The agent completed its work successfully.

3. `baton execute record --step 1.1 --status complete` called `record_step_result(status="complete")`. This loaded state (correctly found the dispatched row), replaced it in memory with a "complete" row, then called `save_execution(state)`.

4. `save_execution()` executed `DELETE FROM step_results WHERE task_id = ?` followed by `INSERT INTO step_results`. The INSERT failed with: `UNIQUE constraint failed: step_results.task_id, step_results.step_id`. The DELETE had apparently removed the row, but a prior incremental `save_step_result()` call (from an earlier concurrent path) had already re-inserted it within the same transaction boundary before the INSERT ran. SQLite raised UNIQUE constraint failure and rolled back the entire transaction — including the DELETE. The step row was restored to "dispatched" in SQLite.

5. The engine's `_save_execution()` fallback path caught the exception and called `self._persistence.save(state)`. However, `state` at that moment reflected the correct in-memory model (step 1.1 = "complete"), so the file write succeeded with the correct status. File: step 1.1 = "complete". SQLite: step 1.1 = "dispatched".

6. Both persistence backends were now divergent. SQLite and the file backend disagreed on the step status.

7. On the next call, `_load_execution()` tried SQLite first (the normal priority path) and returned the stale state with step 1.1 = "dispatched".

8. `_determine_action()` saw step 1.1 in `dispatched_step_ids`, placed it in the "occupied" set, found no dispatchable steps, saw "pending" steps (step 1.1 counted as pending because it was neither completed nor failed), and returned `ActionType.WAIT`.

9. Every subsequent `baton execute next` call returned `WAIT` forever. The execution was permanently stuck.

10. Manual Python intervention was required: connect to `baton.db` directly and `UPDATE step_results SET status='complete' WHERE task_id=... AND step_id='1.1'`.

## Why This Is Wrong

**Split-brain state is undetectable at runtime.** The engine has no mechanism to notice that SQLite and the file backend disagree. When `_load_execution()` returns SQLite state, it has no idea the file backend holds a more-advanced version of the same step.

**The file fallback is correct but invisible.** In step 5, the file fallback wrote the right state (complete). But because `_load_execution()` always prefers SQLite when a storage backend is configured, the correct state in the file was never consulted on subsequent calls. The fallback writes are consumed only when SQLite itself is unavailable — not when SQLite holds stale data.

**Silent data loss.** The rollback in step 4 silently restored "dispatched" to SQLite. No error surfaced to the user. The WARNING log message ("SQLite save failed, falling back to file persistence: ...") did not include the task ID, step ID, or intended status transition, making it impossible to diagnose from logs alone.

**No reconciliation on resume.** `resume()` had no logic to compare the two backends and take the more-advanced state. Even a `baton execute resume` call would have hit the same WAIT loop.

**`recover_dispatched_steps()` exists but is never called in normal resume paths.** This function (bug 0.4 in the original audit) would have cleared the stale dispatched marker, but it is only called manually — not automatically on `baton execute resume`.

## Root Cause Analysis

**Contributing factor 1: Plain INSERT in `save_execution` step_results loop.**
`save_execution()` used `DELETE FROM step_results` then `INSERT INTO step_results` (plain INSERT, not upsert) for each step result. While the DELETE should prevent duplicates within a normal call sequence, a concurrent `save_step_result()` call (which uses `INSERT OR REPLACE`) can re-insert a row between the DELETE and INSERT of the parent transaction under certain SQLite locking scenarios. Using `INSERT OR REPLACE` (upsert) in the loop makes the write idempotent regardless of what prior incremental writes have done.

**Contributing factor 2: No reconciliation logic in the load/resume path.**
`_load_execution()` has a strict priority order: SQLite first, file fallback only when SQLite raises an exception. There is no code path that compares the two backends and picks the more-advanced state. This design works correctly when writes succeed but has no recovery mechanism when they partially fail. Industry-standard dual-write systems include a read-reconciliation step that takes the most recent/most-advanced write.

**Contributing factor 3: Fallback log message lacks diagnostic context.**
When `_save_execution()` fell back to file persistence, the WARNING log said only "SQLite save failed, falling back to file persistence: <error>". It did not log the task_id, the step IDs being written, or their intended statuses. Without this information, the split-brain condition is invisible in production log streams until it manifests as a stuck execution.

**Contributing factor 4: No in-band split-brain detection.**
After a SQLite write failure, the engine continued running as if the failure had been fully recovered. There was no check on subsequent load operations to validate that SQLite and file state agreed. A one-time divergence check at resume() time — comparing step statuses across both backends — would have caught and healed the split-brain automatically.

**Contributing factor 5: `recover_dispatched_steps()` not called on resume.**
The `recover_dispatched_steps()` method clears stale "dispatched" markers introduced by crashes. It is not called in the `resume()` execution path, so stale dispatched state from a write failure (not a crash) is never automatically cleared.

## Industry Best Practice References

**Idempotent writes (WAL pattern).** Write-Ahead Log systems and event-sourced databases make writes idempotent by design: applying the same write twice produces the same result as applying it once. SQLite's `INSERT OR REPLACE` (also written `INSERT INTO ... ON CONFLICT DO REPLACE`) is the standard SQLite idiom for this. Using it in the step_results loop means the engine can safely re-record a step regardless of what the database already holds for that (task_id, step_id).

**Single source of truth.** The canonical pattern for dual-write systems (e.g., Kafka + database, primary + replica) is: designate one backend as authoritative. When both fail to agree, the more-advanced write wins (last-write-wins or highest-sequence-wins). The agent-baton dual-write was asymmetric: SQLite was always authoritative on read, even after a SQLite write failure, with no mechanism to take the file state as the authority.

**Reconciliation on load.** Systems that maintain multiple state replicas (e.g., multi-region databases, CQRS event stores) perform explicit reconciliation when loading state from potentially divergent sources. The standard algorithm is to compare a progress indicator (sequence number, status rank, timestamp) across all available replicas and take the most-advanced. Agent-baton now implements this in `resume()` via `_reconcile_states()`, which compares per-step status rank (dispatched < interrupted < failed < complete) across SQLite and file backends.

**Structured error logging.** Operations that can trigger split-brain state must log enough context to diagnose the divergence without running SQL queries. At minimum: the entity being written (task_id), the state transition being attempted (step_id, old_status → new_status), and the backend that failed.

## Corrective Actions

**Fix A — Idempotent step_results INSERT in `save_execution` (sqlite_backend.py).**
Changed the `INSERT INTO step_results` in the DELETE+INSERT loop to `INSERT OR REPLACE INTO step_results`. This makes the full-state save idempotent with respect to any prior incremental `save_step_result` writes. A duplicate (task_id, step_id) from a prior write is safely overwritten rather than raising UNIQUE constraint failure and rolling back the transaction.

**Fix B — Structured WARNING log in `_save_execution` (executor.py).**
The fallback log message now includes `task_id`, `status`, and a per-step `step_id=status` summary. Example: `"SQLite save failed for task 'task-abc' (status='running', steps=[1.1=complete]); falling back to file persistence — SQLite and file state may diverge."` This makes split-brain conditions identifiable from logs without DB access.

**Fix C — Reconciliation check in `resume()` (executor.py).**
Added `_reconcile_states()` helper and a reconciliation block in `resume()`. When both SQLite and file backends are available and both have state for the current task_id, `resume()` now compares per-step status ranks and promotes any step where the file backend is more advanced. The promotion is logged at WARNING level so it is visible in production. The reconciliation is non-destructive: it does not mutate either backend's stored state, only the in-memory state used for the current execution cycle. The corrected state is then used by `_determine_action()`, preventing the infinite WAIT loop.

**Fix D — `_reconcile_states` helper class method (executor.py).**
Added `ExecutionEngine._STEP_STATUS_RANK`, `ExecutionEngine._step_status_rank()`, and `ExecutionEngine._reconcile_states()`. The rank mapping is `dispatched=1, interrupted=2, failed=3, complete=4`. Unknown statuses rank at 0. The helper takes a primary and secondary state, returns a shallow copy of primary with any step results upgraded to secondary's status where secondary is more advanced.

## Open Items

**Bug 0.4 (pre-existing): `recover_dispatched_steps()` not called on `resume()`.**
This is a separate but related issue. Stale "dispatched" markers from daemon crashes are cleared by `recover_dispatched_steps()`, but this method is not called automatically in `baton execute resume`. The reconciliation fix (Fix C) addresses the split-brain scenario; crash-recovery for daemon-crashed dispatched steps remains a manual operation. A follow-up should wire `recover_dispatched_steps()` into the resume path (or expose it as an automatic step in `baton execute resume --recover`).
