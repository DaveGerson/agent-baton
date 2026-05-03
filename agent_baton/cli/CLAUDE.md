# agent_baton/cli/ — `baton <command>` surface

The CLI is the agent's primary interface to the engine. Cross-cutting rules: [../../CLAUDE.md](../../CLAUDE.md).

## Layout

| Path | Role |
|------|------|
| `main.py` | Click entry point; registers all command groups |
| `_context.py` | Per-invocation context object passed to commands |
| `_override_helper.py` | CLI flag → environment variable resolution |
| `colors.py`, `formatting.py` | Terminal output helpers |
| `errors.py` | Structured CLI errors with stable exit codes |
| `commands/` | One module or subpackage per command group |

## Command groups under `commands/`

Top-level `*_cmd.py` files are single-command entry points (`bead_cmd`, `config_cmd`, `daemon_immune_cmd`, `debate_cmd`, `merge_cmd`, `pmo_cmd`, `predict_cmd`, `query_cmd`, `release_cmd`, `souls_cmd`, `source_cmd`, `spec_cmd`, `swarm_cmd`, `sync_cmd`, `tenancy_cmd`, `webhook_cmd`, `maintenance_cmd`, `assess_cmd`).

Subpackages (`agents/`, `distribute/`, `execution/`, `finops/`, `govern/`, `improve/`, `knowledge/`, `observe/`, `release/`, `specs/`, `tenancy/`) group multi-command surfaces.

## Critical: protocol surface

`commands/execution/execute.py` contains `_print_action()` — **the protocol contract** between the engine and the orchestrator agent. The output shape is parsed by agents in production. **Do not change its shape** without a coordinated update to `agents/orchestrator.md` and `references/baton-engine.md`.

## Conventions

- One command per file unless the group is tightly coupled (see `execution/`).
- All output that an agent will parse must go through `formatting.py` — never `print()` directly.
- Errors raise `BatonError` (or subclass) from `errors.py`; never bare `sys.exit(1)`.
- Long operations stream progress through the visualize package, not ad-hoc prints.

## Adding a command

1. Add the module under `commands/` (or a new subpackage if it's a group).
2. Register it in `main.py` (or the subpackage's `__init__.py`).
3. Add a test under `tests/cli/`.
4. Update [docs/cli-reference.md](../../docs/cli-reference.md).
