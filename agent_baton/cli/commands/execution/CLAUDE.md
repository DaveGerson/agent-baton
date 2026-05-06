# agent_baton/cli/commands/execution/ — engine ↔ orchestrator protocol surface

This is where `baton execute` lives. **Editing here can break the contract that orchestrator agents parse at runtime.** Inherits: [../../../../CLAUDE.md](../../../../CLAUDE.md), [../../CLAUDE.md](../../CLAUDE.md).

## Critical: don't break the wire

`execute.py` contains `_print_action()` — its output is parsed by every orchestrator agent in production. The output shape is the **protocol contract**, not an implementation detail. Changing it requires coordinated updates to:

1. `agents/orchestrator.md` — the agent that parses the output.
2. `references/baton-engine.md` — agent-side protocol reference.
3. `agent_baton/models/execution.py::ActionType` — if action types change.
4. `docs/architecture/state-machine.md` and `docs/engine-and-runtime.md`.

If a change to `execute.py`'s output isn't accompanied by updates to all four, stop and split the work.

## Files

| File | `baton` verb | Role |
|------|--------------|------|
| `execute.py` | `baton execute {start, record, gate, resume, complete}` | The action loop driver — owns `_print_action()` |
| `run.py` | `baton execute run` | Non-interactive driver that loops actions automatically |
| `decide.py` | `baton execute decide` | Captures orchestrator decisions during INTERACT phases |
| `handoff.py` | `baton execute handoff` | Hands off between agents/branches |
| `status.py` | `baton execute status` | Inspects current execution state |
| `plan_cmd.py` | `baton plan` | Plan generation entry point |
| `plan_edit_cmd.py` | `baton plan edit` | Plan mutation |
| `plan_validate_cmd.py` | `baton plan validate` | Plan validation hook |
| `async_cmd.py` | `baton execute async` | Async-mode dispatch |
| `daemon.py` | `baton daemon ...` | Daemon-mode entry point |
| `_validators.py` | (internal) | Argument validators shared across the above |

## Conventions

- **All output an agent will parse goes through `_print_action()` in `execute.py`** — never `print()` directly.
- **CLI verbs are stable**, the help-text wording is stable. Renames are protocol changes.
- **Errors raise typed `BatonError`** from `cli/errors.py`. Don't `sys.exit(1)`.
- **Async and daemon modes are entry-point shims** — they construct the same engine objects as synchronous execute and delegate.
- **Tests live in `tests/cli/`** *and* `tests/engine_integration/`. Changes to `_print_action()` need an integration test that asserts the output shape.

## When you change execution output

1. Update `_print_action()` and add a snapshot test under `tests/cli/`.
2. Update `agents/orchestrator.md`'s parser instructions.
3. Update `references/baton-engine.md`.
4. Run the orchestrator dogfood test in `tests/test_dogfood_pipeline.py`.

## Don'ts

- Don't introduce a new action type only here. `ActionType` lives in `agent_baton/models/execution.py`.
- Don't add format flags (e.g., `--json`) without keeping the default text format identical to today's parsed contract.
- Don't catch and re-raise without preserving the typed exception class — orchestrators dispatch on it.
