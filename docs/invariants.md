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
| `baton execute record --step-id ... --agent ... --status ...` | Record a completed step result |
| `baton execute gate --phase-id ... --result pass/fail` | Record a gate result |
| `baton execute complete` | Finalize execution |
| `baton execute status` | Check current execution state |
| `baton execute resume` | Recover execution after a session crash |

**Rule**: Every command string in this table must continue to work identically
after any internal refactoring. Subcommand names are registered inside each
command module via `register(subparsers)` — they are not derived from filenames.
Moving command files to subdirectories does not change these strings, but
renaming the registered subcommand does.

**Safeguard**: A frozen-set contract test asserts that all expected subcommand
names are registered by `cli/main.py`. The test fails if any command is
accidentally dropped during auto-discovery changes.

---

## Invariant 2: CLI Output Format (_print_action Protocol)

`_print_action()` in `cli/commands/execute.py` produces the structured text
that Claude parses after every `baton execute next` call to determine what
action to take.

### Format Specification

```
ACTION: <TYPE>
  Agent: <agent-name>
  Model: <model-id>
  Step:  <phase.step>
  Message: <one-line summary>
--- Delegation Prompt ---
<full delegation prompt text>
--- End Prompt ---
```

For terminal actions (COMPLETE, WAIT, GATE_FAILED):

```
ACTION: COMPLETE
  Summary: <completion summary>
```

### Action Types

| Value | Meaning |
|-------|---------|
| `ACTION: DISPATCH` | Claude should invoke the named agent with the delegation prompt |
| `ACTION: COMPLETE` | All phases done; execution is finished |
| `ACTION: WAIT` | All pending steps are dispatched; wait for results |
| `ACTION: GATE_FAILED` | A phase gate failed; Claude should not proceed |

**Rules that must not change**:

1. `ACTION:` must be uppercase. Claude pattern-matches on this prefix.
2. Field labels (`Agent:`, `Model:`, `Step:`, `Message:`) must remain as
   shown. A label change breaks Claude's parser silently — no error is thrown;
   the wrong action is taken.
3. Section delimiters (`--- Delegation Prompt ---`, `--- End Prompt ---`) must
   remain verbatim.
4. The `ActionType` enum `.value` strings (`DISPATCH`, `COMPLETE`, `WAIT`,
   `GATE_FAILED`) must match the uppercase labels above. If `ActionType` values
   change, `_print_action()` must be updated simultaneously.

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
| `current_phase_id` | int | Index of the active phase |
| `current_step_id` | str | ID of the active step |
| `completed_step_ids` | list[str] | Steps already recorded as complete |
| `dispatched_step_ids` | list[str] | Steps sent to agents not yet complete |
| `gate_results` | dict | Phase gate outcomes keyed by phase ID |
| `status` | str | One of: `running`, `complete`, `failed`, `gate_failed` |

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
