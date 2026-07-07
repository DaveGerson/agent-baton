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

Top-level `*_cmd.py` files are single-command entry points (`bead_cmd`, `config_cmd`, `daemon_immune_cmd`, `debate_cmd`, `merge_cmd`, `pmo_cmd`, `query_cmd`, `release_cmd`, `souls_cmd`, `source_cmd`, `spec_cmd`, `sync_cmd`, `tenancy_cmd`, `webhook_cmd`, `maintenance_cmd`, `assess_cmd`).

Subpackages (`agents/`, `distribute/`, `execution/`, `finops/`, `govern/`, `improve/`, `knowledge/`, `observe/`, `release/`, `specs/`, `tenancy/`) group multi-command surfaces.

## Critical: protocol surface

`commands/execution/execute.py` contains `_print_action()` — **the protocol contract** between the engine and the orchestrator agent. The output shape is parsed by agents in production. **Do not change its shape** without a coordinated update to `agents/orchestrator.md` and `references/baton-engine.md`.

## Conventions

- One command per file unless the group is tightly coupled (see `execution/`).
- All output that an agent will parse must go through `formatting.py` — never `print()` directly.
- Errors raise `BatonError` (or subclass) from `errors.py`; never bare `sys.exit(1)`.
- Long operations stream progress through the visualize package, not ad-hoc prints.
- **Module basenames must be unique across all of `commands/**`, not just within one subpackage.** `main.discover_commands()` keys its module map by the bare filename (e.g. `doctor_cmd`), not the qualified package path — a second `doctor_cmd.py` anywhere else under `commands/` silently shadows the first in that dict (whichever subpackage's `pkgutil.iter_modules()` returns last wins), so its `register()`/`handler()` are never actually wired into `main.py`'s dispatch table. `knowledge/doctor_cmd.py` already exists; that's why the agents-group doctor command lives in `commands/agents/agent_doctor_cmd.py`, not `commands/agents/doctor_cmd.py`. Grep `find agent_baton/cli/commands -name "*.py" | xargs -n1 basename | sort | uniq -c` before naming a new file.
- Multiple modules contributing subcommands to one shared parser (e.g. `agents doctor`, `knowledge doctor`, `release profile`) must cooperate through a shared-parser helper (see `commands/knowledge/__init__.py` or `commands/agents/__init__.py`) rather than each calling `subparsers.add_parser(<same name>)` directly — repeated calls don't raise, they just silently clobber each other's parser object.

## Adding a command

1. Add the module under `commands/` (or a new subpackage if it's a group).
2. Register it in `main.py` (or the subpackage's `__init__.py`).
3. Add a test under `tests/cli/`.
4. Update [docs/cli-reference.md](../../docs/cli-reference.md).
