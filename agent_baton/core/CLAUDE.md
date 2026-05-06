# agent_baton/core/ — engine internals

The brain of `baton`. Cross-cutting rules: [../../CLAUDE.md](../../CLAUDE.md). Package-level rules: [../CLAUDE.md](../CLAUDE.md).

## Subsystems

| Path | Responsibility | Drill-in |
|------|----------------|----------|
| `engine/` | State machine, planner, dispatcher, executor, gates, beads — the protocol surface | [engine/CLAUDE.md](engine/CLAUDE.md) |
| `orchestration/` | Agent registry, knowledge registry, router, runner | [orchestration/CLAUDE.md](orchestration/CLAUDE.md) |
| `govern/` | Risk classifier, guardrails, compliance, regulated-data rules | [govern/CLAUDE.md](govern/CLAUDE.md) |
| `storage/` | SQLite + file persistence, migrations, sync, external adapters | [storage/CLAUDE.md](storage/CLAUDE.md) |
| `audit/` | Audit trail recording and replay | — |
| `exec/` | Sandboxed execution runner, auditor gate, script lint | — |
| `gates/` | QA gate implementations triggered by the engine | — |
| `immune/` | Immune-system (anti-rot) detectors and signals | — |
| `improve/` | Retrospective + improvement-loop logic | — |
| `intel/` | Intelligence enrichment (context profiles, scoring) | — |
| `knowledge/` | Knowledge packs and resolution | — |
| `learn/` | Learning cycle and pattern extraction | — |
| `observability/` | **Emit side** — OTEL exporter, Prometheus, attribution, chargeback | — |
| `observe/` | **Consumption side** — dashboard, telemetry, retrospectives, scanners, incidents | — |
| `pmo/` | PMO data layer powering the UI | — |
| `predict/` | Predictive dispatch | — |
| `release/` | Release readiness scoring | — |
| `runtime/` | Process / subprocess runtime helpers | — |
| `specs/` | Spec parsing and lifecycle | — |
| `swarm/` | Experimental swarm coordination (gated by `BATON_EXPERIMENTAL=swarm`) | — |
| `distribute/` | Install/uninstall and distribution helpers | — |
| `events/` | Internal event bus | — |
| `config/` | Settings loading and overrides | — |
| `git_manager.py` | Git worktree + branch helpers | — |

## Conventions

- **Dependency arrow points outward.** `core/` modules must not import from `cli/` or `api/`.
- **Storage access goes through `core/storage/`** — never raw SQL elsewhere.
- **Feature flags live in `engine/flags.py`.** Gate experimental work behind a flag and document it in the env-vars table at the root.
- **Errors raise typed exceptions from `engine/errors.py`.**
- One responsibility per submodule; new behavior either fits an existing area or earns a new top-level submodule under `core/`.

## Tests

Each subsystem has a matching `tests/<area>/` directory. Add tests there, not at the top level of `tests/`.
