# agent_baton/ — Python orchestration engine

Source for the `baton` CLI. Cross-cutting rules live in [../CLAUDE.md](../CLAUDE.md).

## Layout

| Subpackage | Purpose | Drill-in |
|------------|---------|----------|
| `api/` | FastAPI app: routes, middleware, webhooks | [api/CLAUDE.md](api/CLAUDE.md) |
| `cli/` | `baton <command>` entry points (Click) | [cli/CLAUDE.md](cli/CLAUDE.md) |
| `core/` | Engine internals — state machine, planner, dispatcher, governance, storage | [core/CLAUDE.md](core/CLAUDE.md) |
| `models/` | Pydantic models — `ActionType`, `ExecutionState`, beads, plans | [models/CLAUDE.md](models/CLAUDE.md) |
| `testing/` | Test fixtures and helpers shipped with the package | — |
| `utils/` | Small shared helpers (`frontmatter`, `time`) — keep this package thin | — |
| `visualize/` | CLI + web renderers for execution snapshots | — |
| `_bundled_agents/` | Generated mirror of `/agents/` shipped inside the wheel — do not hand-edit | — |

## Import discipline

- Always use canonical paths: `from agent_baton.core.govern.classifier import DataClassifier`.
- Never re-export across submodules through `__init__.py` shortcuts.
- `__init__.py` files stay empty unless they expose an explicit public symbol.

## Adding a new module

1. Decide the home: data shape → `models/`; behavior → `core/<area>/`; HTTP surface → `api/routes/`; user-facing command → `cli/commands/`.
2. Add unit tests under the matching `tests/<area>/` path.
3. If the change is observable to users, update `docs/cli-reference.md`, `docs/api-reference.md`, or `docs/architecture.md` as appropriate.

## Regenerating bundled agents

`_bundled_agents/` is synced from the top-level `/agents/` directory by `scripts/sync_bundled_agents.sh`. Edit the source in `/agents/`, then run the script — never edit the bundled copy directly.
