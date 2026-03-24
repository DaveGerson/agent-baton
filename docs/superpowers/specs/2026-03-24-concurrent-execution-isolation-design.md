# Concurrent Execution Isolation via Session-Scoped Task Binding

## Problem

When two terminal sessions run `baton execute start` in the same repo,
the second execution overwrites `active-task-id.txt`. Subsequent
`baton execute next` calls in the first terminal silently read the
wrong execution state, returning actions from the second plan.

This was observed on 2026-03-24 when a PMO UI test plan and a federated
sync plan were running concurrently. The PMO UI session's `next` call
returned a federated sync step, causing the session to dispatch work
against the wrong plan.

### Root Cause

The CLI resolves the target execution via a priority chain
(`execute.py:199-205`):

```
explicit --task-id flag → active-task-id.txt → None (legacy flat file)
```

`active-task-id.txt` is a singleton file. `baton execute start` always
overwrites it (`execute.py:224-230`). There is no per-session binding.

### What Already Works

The engine already supports concurrent executions at the storage layer:
- Task-scoped directories: `executions/<task-id>/execution-state.json`
- `StatePersistence.list_executions()` enumerates all running tasks
- `baton execute switch <TASK_ID>` exists (updates active marker)
- The `--task-id` flag is accepted on every subcommand

The gap is narrow: the CLI has no **session-local** way to remember
which task-id belongs to the current terminal.

## Solution: `BATON_TASK_ID` Environment Variable

Add `BATON_TASK_ID` as a session-local source in the task-id resolution
chain, between the explicit `--task-id` flag and the `active-task-id.txt`
file marker.

### New Resolution Order

```
--task-id flag → BATON_TASK_ID env var → active-task-id.txt → None
```

The `--task-id` flag remains highest priority because it is the most
explicit signal (typed for a specific invocation). The env var is the
per-session default. The active marker is the per-repo fallback.
This matches CLI conventions (e.g., `aws --profile` beats
`AWS_PROFILE`).

### Behavior Changes

#### 1. CLI task-id resolution (`execute.py:199-205`)

Before:
```python
task_id = getattr(args, "task_id", None)
context_root = Path(".claude/team-context").resolve()
if task_id is None and args.subcommand != "start":
    task_id = StatePersistence.get_active_task_id(
        Path(".claude/team-context")
    )
```

After:
```python
import os  # ← add to import block at top of file

# ... (in handler(), after parsing args)

task_id = getattr(args, "task_id", None)
context_root = Path(".claude/team-context").resolve()
if task_id is None:
    task_id = os.environ.get("BATON_TASK_ID")
if task_id is None and args.subcommand != "start":
    task_id = StatePersistence.get_active_task_id(
        Path(".claude/team-context")
    )
```

Note: on `start`, the resolved `task_id` is immediately overwritten by
`plan.task_id` at line 220. The env var check is harmless here — it
avoids adding a subcommand guard for simplicity.

Requires adding `import os` to the import block at the top of
`execute.py`.

#### 2. `baton execute start` emits export hint

After `_print_action()` returns (unconditionally, regardless of whether
the first action is DISPATCH, GATE, or any other type), print a
copyable export line using the plan's `task_id` (not the env var value,
which may be stale from a previous session):

```
Session binding: export BATON_TASK_ID=<plan-task-id>
```

This tells the user how to bind this terminal to the new execution.

**Edge case — stale env var on start:** When `BATON_TASK_ID` is set
to a previous task and `baton execute start` creates a new plan, the
export hint prints the **new** plan's task-id. The user must re-export
to bind to the new execution. This is the correct behavior: start
always creates a new execution from the plan file, and the env var
from a prior session should not silently redirect it.

**Agentic callers:** Claude Code's orchestrator reads `_print_action()`
output to drive the loop. The export hint is printed after
`--- End Prompt ---` and does not affect the action parser. Agentic
callers that want session isolation should pass `--task-id` on every
CLI call rather than relying on env var propagation (env vars do not
persist across independent `Bash` tool calls in Claude Code).

#### 3. `baton execute status` shows binding source

Add a `Bound via` line to status output so users can diagnose which
resolution path is active. Match existing field width formatting:

```
Task:    <task-id>
Bound:   BATON_TASK_ID      # or: --task-id / active-task-id.txt
Status:  running
Phase:   1
...
```

### Dual-Backend Compatibility

This design is backend-agnostic:

| Scenario | How task-id is used |
|----------|-------------------|
| **Without baton.db** (file storage) | Env var → `StatePersistence(context_root, task_id=...)` → reads `executions/<task-id>/execution-state.json` |
| **With baton.db** (SQLite storage) | Env var → `storage.load_execution(task_id)` → reads `executions` table row |
| **Legacy (no task-id)** | Falls through all sources → `StatePersistence(context_root, task_id=None)` → reads flat `execution-state.json` |

The env var operates at the CLI layer, upstream of both storage
backends. When baton.db becomes the single source of truth,
`active-task-id.txt` can be deprecated — but `BATON_TASK_ID` remains
necessary because "which terminal session owns which execution" is
inherently process-local state that no database can infer.

### What This Does NOT Change

- **`active-task-id.txt` behavior is unchanged.** It still exists as the
  fallback for single-execution workflows and backward compatibility.
- **`baton execute switch` is unchanged.** It updates the active marker,
  which still works when `BATON_TASK_ID` is not set.
- **`--task-id` flag beats env var.** A per-invocation `--task-id` flag
  overrides both the env var and the active marker. This lets users
  inspect a different execution without unsetting the env var.
- **Storage backends are unchanged.** No changes to `persistence.py`,
  `StatePersistence`, or the SQLite storage layer.

## Files to Modify

| File | Change |
|------|--------|
| `agent_baton/cli/commands/execution/execute.py` | Add `import os`. Insert env var check between `--task-id` and active marker (~3 lines). Print export hint after `_print_action()` on `start` (~2 lines). Add `Bound:` field to `status` output (~5 lines). |
| `tests/test_execute_task_id_resolution.py` (new) | Test: `--task-id` beats env var. Test: env var beats active marker. Test: fallback to active marker when env var unset. Test: export hint printed on start (after action delimiters). Test: `Bound:` field in status output. |
| `docs/invariants.md` | Add subsection under Invariant 1 (CLI Command Surface) documenting the `BATON_TASK_ID` resolution order. Note that the export hint is printed after `_print_action()` returns and is not part of the action protocol. |
| `references/baton-engine.md` | Add concurrent execution section. Update `baton execute status` output example to include `Bound:` field. |

Estimated change: ~30 lines of production code, ~50 lines of tests.

## Alternatives Considered

### TTY-keyed active task registry (Rejected)

Replace `active-task-id.txt` with `active-tasks.json` mapping TTY
device → task-id. Rejected because:
- Platform-dependent (Linux TTY paths differ from macOS, Windows has no
  TTY concept, tmux/SSH add edge cases)
- Requires stale entry cleanup (orphaned TTY mappings)
- Creates infrastructure that baton.db would supersede — baton.db can
  natively track running executions without a sidecar JSON file

### Conflict detection on start (Rejected as standalone)

Refuse to overwrite `active-task-id.txt` if another execution is
running. Rejected as standalone because:
- Adds friction to legitimate concurrent use
- Doesn't solve the session binding problem — even with `--task-id`
  required, the user must remember to pass it on every subsequent
  command
- The check itself ("is another execution running?") is trivial with
  baton.db but awkward with file scanning

Could be added as a quality-of-life warning (print a notice when
overwriting an active marker for a running execution) but is not
sufficient on its own.

## Test Plan

1. **Flag beats env var**: Set `BATON_TASK_ID=task-A`, pass
   `--task-id task-B`. Assert engine targets `task-B`.

2. **Env var beats active marker**: Set `BATON_TASK_ID=task-A`, write
   `active-task-id.txt` pointing to `task-B`. No `--task-id` flag.
   Assert engine targets `task-A`.

3. **Fallback when unset**: Unset `BATON_TASK_ID`, no `--task-id` flag.
   Assert resolution falls through to `active-task-id.txt` as before.

4. **Export hint on start**: Run `baton execute start`. Assert stdout
   contains `export BATON_TASK_ID=<plan-task-id>` after the action
   output (regardless of action type — works for DISPATCH, GATE, etc.).

5. **Export hint uses plan task-id, not env var**: Set
   `BATON_TASK_ID=old-task`. Start a new plan. Assert export hint
   contains the new plan's task-id, not `old-task`.

6. **Status shows binding source**: Set `BATON_TASK_ID`. Run
   `baton execute status`. Assert output contains
   `Bound:   BATON_TASK_ID`.

7. **Concurrent isolation**: Start two executions with different
   `BATON_TASK_ID` values. Verify each session's `next` returns
   actions from its own plan.
