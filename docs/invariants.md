# System Invariants

These are the three interface boundaries that must not change without a
coordinated update to the orchestrator agent definition and any downstream
Claude configuration. They are not internal implementation details — they are
the protocol between Claude (Layer B) and the Python engine (Layer D).

Changing anything in this document without updating `agents/orchestrator.md`
will silently break orchestrated task execution.

---

## Invariant 1: CLI Command Surface

Claude drives execution by calling `baton` subcommands. These strings are the
control API between Claude and the engine.

| Command | Purpose |
|---------|---------|
| `baton plan "..." --save --explain` | Generate and persist an execution plan |
| `baton execute start` | Begin execution of the current plan |
| `baton execute next` | Get the next action to perform |
| `baton execute dispatched --step ID --agent NAME` | Mark a step as dispatched (in-flight) |
| `baton execute record --step-id ... --agent ... --status ...` | Record a completed step result |
| `baton execute gate --phase-id ... --result pass/fail` | Record a gate result |
| `baton execute approve --phase-id ... --result ...` | Record a human approval decision |
| `baton execute amend --description ... [--add-phase ...]` | Amend the running plan |
| `baton execute team-record --step-id ... --member-id ...` | Record a team member completion |
| `baton execute complete` | Finalize execution |
| `baton execute status` | Check current execution state |
| `baton execute resume` | Recover execution after a session crash |
| `baton execute run` | Autonomous execution loop (headless, no Claude Code session) |
| `baton execute list` | List all executions (active and completed) |
| `baton execute switch TASK_ID` | Switch the active execution to a different task |

**Rule**: Every command string in this table must continue to work identically
after any internal refactoring. Subcommand names are registered inside each
command module via `register(subparsers)` — they are not derived from filenames.
Moving command files to subdirectories does not change these strings, but
renaming the registered subcommand does.

### Task-ID Resolution Order

Every `baton execute` subcommand (except `list` and `switch`) resolves a
target task ID through a five-level priority chain:

```
--task-id flag  →  BATON_TASK_ID env var  →  SQLite active_task  →  active-task-id.txt  →  None
```

| Source | Scope | When to use |
|--------|-------|-------------|
| `--task-id FLAG` | Per-invocation | Inspect or drive a specific execution for a single command |
| `BATON_TASK_ID` | Per shell session | Bind a terminal session to one execution when multiple are running concurrently |
| SQLite `active_task` | Per repository | Preferred persistent lookup from `baton.db`; set by `start` and `switch` |
| `active-task-id.txt` | Per repository | File-based fallback; updated by `baton execute switch` |
| `None` | Legacy | Reads the flat `execution-state.json` without a task-scoped directory |

**Rules that must not change**:

1. `--task-id` always beats the env var. The env var always beats SQLite.
   SQLite always beats the file marker. This order matches the CLI
   convention where the most explicit signal wins.
2. On `start`, the resolved `task_id` is immediately overwritten by
   `plan.task_id`. The env var check is harmless on `start`.
3. The resolution chain in `handler()` (`execute.py`) is:
   `--task-id` guard, then `BATON_TASK_ID` env var, then SQLite
   `get_active_task()`, then `StatePersistence.get_active_task_id()`.
   Reordering these checks breaks the priority contract.

### Export Hint on `baton execute start`

After `_print_action()` returns (regardless of action type), `start` prints:

```
Session binding: export BATON_TASK_ID=<plan-task-id>
```

This line is printed **after** `--- End Prompt ---` and is **not** part of
the `_print_action()` protocol (Invariant 2). Agentic callers that parse
`_print_action()` output are not affected. The hint uses `plan.task_id`,
not the value of `BATON_TASK_ID` in the environment — the env var may be
stale from a previous session.

**Safeguard**: A frozen-set contract test asserts that all expected subcommand
names are registered by `cli/main.py`. The test fails if any command is
accidentally dropped during auto-discovery changes.

---

## Invariant 2: CLI Output Format (_print_action Protocol)

`_print_action()` in `cli/commands/execution/execute.py` produces the
structured text that Claude parses after every `baton execute next` call to
determine what action to take.

### Format Specification

**DISPATCH** (spawn a subagent):

```
ACTION: DISPATCH
  Agent: <agent-name>
  Model: <model-id>
  Step:  <phase.step>
  Message: <one-line summary>

--- Delegation Prompt ---
<full delegation prompt text>
--- End Prompt ---
```

**GATE** (run a QA check):

```
ACTION: GATE
  Type:    <gate-type>
  Phase:   <phase-id>
  Command: <shell command to run>
  Message: <description>
```

**APPROVAL** (human-in-the-loop checkpoint):

```
ACTION: APPROVAL
  Phase:   <phase-id>
  Message: <one-line summary>

--- Approval Context ---
<summary of phase output for reviewer>
--- End Context ---

Options: approve, reject, approve-with-feedback
```

**COMPLETE** / **FAILED** (terminal actions):

```
ACTION: COMPLETE
  <completion summary>
```

```
ACTION: FAILED
  <failure summary>
```

**Other** (fallback for WAIT and any future types):

```
ACTION: <type>
  <message>
```

### Action Types

| Printed value | Enum value (lowercase) | Meaning |
|---------------|----------------------|---------|
| `ACTION: DISPATCH` | `dispatch` | Claude should invoke the named agent with the delegation prompt |
| `ACTION: GATE` | `gate` | Claude should run the QA gate command and record the result |
| `ACTION: APPROVAL` | `approval` | Execution paused for human review; respond with `baton execute approve` |
| `ACTION: COMPLETE` | `complete` | All phases done; execution is finished |
| `ACTION: FAILED` | `failed` | Execution cannot continue due to failure |
| `ACTION: wait` | `wait` | All pending steps are dispatched; wait for results (uses fallback format) |

**Rules that must not change**:

1. `ACTION:` prefix must be present on the first line. Claude pattern-matches
   on this prefix. The type keyword after `ACTION:` is printed as uppercase
   for DISPATCH, GATE, APPROVAL, COMPLETE, and FAILED (hardcoded in
   `_print_action()`). WAIT uses the lowercase enum value via the fallback
   branch.
2. Field labels (`Agent:`, `Model:`, `Step:`, `Message:` for DISPATCH;
   `Type:`, `Phase:`, `Command:`, `Message:` for GATE; `Phase:`, `Message:`
   for APPROVAL) must remain as shown. A label change breaks Claude's parser
   silently — no error is thrown; the wrong action is taken.
3. Section delimiters (`--- Delegation Prompt ---`, `--- End Prompt ---`) must
   remain verbatim.
4. The `ActionType` enum `.value` strings are **lowercase** (`dispatch`,
   `gate`, `complete`, `failed`, `wait`, `approval`). The `_print_action()`
   function compares against these values but prints **uppercase** labels.
   If `ActionType` values change, `_print_action()` comparisons must be
   updated simultaneously.
5. Section delimiters for APPROVAL (`--- Approval Context ---`,
   `--- End Context ---`) must remain verbatim.

**Safeguard**: A regression test asserts that `_print_action()` produces
exactly the expected text for each `ActionType` value. The test uses fixture
`ExecutionAction` objects and compares stdout byte-for-byte against known-good
strings. This test must run before any change to `_print_action()`, the
`ActionType` enum, or the `ExecutionAction` model.

A docstring on `_print_action()` reads:

> This function is the control protocol between Claude and the execution
> engine. Its output format is a public API. Do not change field labels,
> section delimiters, or ACTION type strings without updating the
> orchestrator agent definition and the contract test.

### Output Format Contract

Each `baton execute` subcommand has a defined output format. Some outputs
are human-readable (designed for Claude to parse as prose); others are
machine-readable JSON (designed for programmatic consumption).

| Subcommand | Format | Notes |
|------------|--------|-------|
| `start` | Session binding line + `_print_action()` | Human-readable |
| `next` | `_print_action()` | Human-readable |
| `next --all` | JSON array of action dicts | Machine-readable; may contain mixed action types |
| `dispatched` | JSON `{"status": "dispatched", "step_id": "..."}` | Machine-readable |
| `record` | Plain text confirmation | Human-readable |
| `gate` | Plain text confirmation | Human-readable |
| `approve` | Plain text confirmation | Human-readable |
| `amend` | Plain text confirmation | Human-readable |
| `team-record` | Plain text confirmation | Human-readable |
| `complete` | Engine summary text | Human-readable |
| `status` | Structured plain text | Human-readable |
| `list` | Formatted table | Human-readable |

**With `--all`**: Returns only actions that can be dispatched in parallel.
When no parallel actions exist, falls back to a single-element array
containing the next sequential action (GATE, APPROVAL, or COMPLETE).

**With `--output json`**: All subcommands that accept `--output` return JSON
to stdout instead of human-readable text. Text mode (default) is unchanged
from the documented format above. The `list` and `switch` subcommands do not
accept `--output` (they use separate parsers with no shared parent).

| Subcommand | JSON shape (`--output json`) |
|------------|------------------------------|
| `start` | `{"task_id": "...", "action": <action-dict>}` |
| `next` | `[<action-dict>]` (always a single-element array in non-`--all` mode) |
| `next --all` | `[<action-dict>, ...]` |
| `record` | `{"status": "recorded", "step_id": "...", "agent": "...", "result": "..."}` |
| `gate` | `{"status": "recorded", "phase_id": N, "result": "pass\|fail"}` |
| `approve` | `{"status": "recorded", "phase_id": N, "result": "..."}` |
| `amend` | `{"status": "amended", "amendment_id": "...", "description": "..."}` |
| `team-record` | `{"status": "recorded", "step_id": "...", "member_id": "...", "agent": "...", "result": "..."}` |
| `complete` | `{"status": "complete", "summary": "..."}` |
| `status` | Raw status dict (same schema as `engine.status()`) |
| `resume` | `{"action": <action-dict>}` |

Note: `gate` uses `--gate-output` (not `--output`) for capturing gate command
output text, because `--output` is reserved for the format flag on all
subcommands that inherit from the shared parent parser.

---

## Invariant 3: Execution State Disk Schema

The engine persists execution state to `.claude/team-context/execution-state.json`
after every step. This file is what makes `baton execute resume` work when a
Claude Code session is interrupted mid-execution.

### Schema

`ExecutionState.to_dict()` / `from_dict()` define the schema. Key fields:

| Field | Type | Description |
|-------|------|-------------|
| `task_id` | str | Unique identifier for the current task |
| `plan` | dict | `MachinePlan.to_dict()` output |
| `current_phase` | int | Index into `plan.phases` (the active phase) |
| `current_step_index` | int | Index into the current phase's steps |
| `status` | str | One of: `running`, `gate_pending`, `approval_pending`, `complete`, `failed` |
| `step_results` | list[dict] | `StepResult.to_dict()` for each recorded step |
| `gate_results` | list[dict] | `GateResult.to_dict()` for each gate check |
| `approval_results` | list[dict] | `ApprovalResult.to_dict()` for each approval |
| `amendments` | list[dict] | `PlanAmendment.to_dict()` audit trail |
| `started_at` | str | ISO 8601 execution start time |
| `completed_at` | str | ISO 8601 completion time (empty if still running) |
| `pending_gaps` | list[dict] | Unresolved `KnowledgeGapSignal` objects |
| `resolved_decisions` | list[dict] | Resolved gaps injected on re-dispatch |

Note: `completed_step_ids`, `dispatched_step_ids`, `failed_step_ids`, and
`interrupted_step_ids` are **computed properties** on `ExecutionState` derived
from `step_results` — they are not serialized to disk.

### Companion files

| Path | Purpose |
|------|---------|
| `.claude/team-context/execution-state.json` | Live execution state (read by `resume`) |
| `.claude/team-context/plan.json` | Serialized `MachinePlan` |

**Rules that must not change**:

1. The file path `.claude/team-context/execution-state.json` is hardcoded in
   `StatePersistence` and is what `baton execute resume` reads. Changing it
   without updating both the engine and any in-flight sessions will make
   recovery impossible.
2. Any field removed from `ExecutionState.to_dict()` makes existing on-disk
   state files unreadable by `from_dict()`. A migration or version field is
   required before removing fields.
3. `plan.json` path and `MachinePlan.to_dict()` schema are similarly stable:
   `baton execute status` reads `plan.json` to display progress.

**Safeguard**: A serialization round-trip test asserts that
`ExecutionState.from_dict(state.to_dict()) == state` for a representative
state object. Any schema-breaking change causes this test to fail before
reaching production.

---

## Change Checklist

When modifying anything that touches these invariants, work through this
checklist before merging:

- [ ] All existing `baton <subcommand>` strings listed in Invariant 1 still
      work (`baton execute next`, `baton plan`, etc.)
- [ ] The subcommand registration test passes with the frozen expected set
- [ ] `_print_action()` output regression test passes
- [ ] `ExecutionState` round-trip test passes
- [ ] `agents/orchestrator.md` is updated if any command string or output
      format changed
- [ ] `pytest tests/ -x -q` exits 0
