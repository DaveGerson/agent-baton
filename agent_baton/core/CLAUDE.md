# agent_baton/core/ — engine internals

The brain of `baton`. Cross-cutting rules: [../../CLAUDE.md](../../CLAUDE.md).

## Subsystems

| Path | Responsibility |
|------|----------------|
| `engine/` | State machine, planner, dispatcher, executor, gates, beads, soul registry — the core action loop |
| `orchestration/` | Agent registry, knowledge registry, runner, router — coordination above a single execution |
| `govern/` | Risk classifier, guardrail presets, regulated-data rules |
| `audit/` | Audit trail recording and replay |
| `gates/` | QA gate implementations triggered by the engine |
| `immune/` | Immune-system (anti-rot) detectors and signals |
| `improve/` | Retrospective + improvement-loop logic |
| `intel/` | Intelligence enrichment (context profiles, scoring) |
| `knowledge/` | Knowledge packs, knowledge resolution, knowledge gap detection |
| `learn/` | Learning cycle and pattern extraction |
| `observability/`, `observe/` | OpenTelemetry export and observation primitives |
| `pmo/` | PMO data layer powering the UI |
| `predict/` | Predictive dispatch |
| `release/` | Release readiness scoring |
| `runtime/` | Process / subprocess runtime helpers |
| `specs/` | Spec parsing and lifecycle |
| `storage/` | SQLite persistence, migrations, sync |
| `swarm/` | Experimental swarm coordination (gated by `BATON_EXPERIMENTAL=swarm`) |
| `distribute/` | Install/uninstall and distribution helpers |
| `events/` | Internal event bus |
| `config/` | Settings loading and overrides |
| `git_manager.py` | Git worktree + branch helpers used by the dispatcher |

## Critical files (don't break)

- `engine/states.py` — `ExecutionState` machine. Adding states means updating the protocol and orchestrator agent.
- `engine/protocols.py` — `ExecutionDriver` 15-method interface; the engine's public seam.
- `engine/dispatcher.py` — turns the next action into `Agent` invocations.
- `engine/planner.py` — produces `Plan` objects consumed by `cli/commands/execution/`.

## Conventions

- One responsibility per submodule. New behavior either fits an existing area or creates a new top-level submodule under `core/`.
- Engine modules must not import from `cli/` or `api/` — dependency arrow points outward.
- Storage access goes through `core/storage/` — never raw SQL elsewhere.
- Feature flags live in `engine/flags.py`; gate experimental work behind a flag and document it in the env-vars table in [../../CLAUDE.md](../../CLAUDE.md).
- Errors raise typed exceptions from `engine/errors.py`.

## Tests

Each subsystem has a matching `tests/<area>/` directory. Add tests there, not at the top level of `tests/`.
