# agent_baton/core/engine/planning/ — plan generation pipeline

Turns a task description into a `Plan`. Inherits: [../../../../CLAUDE.md](../../../../CLAUDE.md), [../../CLAUDE.md](../../CLAUDE.md), [../CLAUDE.md](../CLAUDE.md).

## Architecture

Planning is a **multi-stage pipeline**, not a single function. The stages classify the task, decompose it into phases, enrich each phase with context and risk, build the roster, and assemble the final `Plan`.

```
input task ──► classification ──► decomposition ──► enrichment ──► research
                                                                        │
              assembly ◄── validation ◄── roster ◄── risk ◄────────────┘
                  │
                  ▼
              Plan object
```

## Files

| Path | Role |
|------|------|
| `planner.py` | Top-level entry point and stage orchestration (large; the spine) |
| `pipeline.py` | Stage runner — composes stages and threads context |
| `protocols.py` | Planning-specific `Stage` protocol (separate from `engine/protocols.py`) |
| `archetypes.py` | Task archetype detection (feature, bug-fix, migration, refactor, etc.) |
| `draft.py` | Initial plan-draft data structure used between stages |
| `services.py` | Plan-time services (LLM, registry lookups) injected into stages |
| `structured_spec.py` | Spec → plan-input shaping |
| `stages/` | One file per pipeline stage — `classification`, `decomposition`, `enrichment`, `research`, `risk`, `roster`, `assembly`, `validation` |
| `rules/` | Static planning rules — `concerns`, `default_agents`, `phase_roles`, `phase_templates`, `risk_signals`, `step_types`, `templates` |
| `utils/` | Stage-shared helpers — `context`, `gates`, `phase_builder`, `risk_and_policy`, `roster_logic`, `text_parsers` |

## Conventions

- **One stage per file in `stages/`.** Stage order is fixed in `pipeline.py`. Don't reorder without a planner test that asserts the new ordering.
- **Stages receive and return a draft via `draft.py`.** They mutate the draft, not external state. Side effects (logging, telemetry) flow through `services.py`.
- **`rules/` is data, `utils/` is logic.** New static templates or role definitions go in `rules/`. New behavior shared across stages goes in `utils/`.
- **Planner output is a `Plan` from `agent_baton/models/plan.py`.** Don't let internal draft types leak out of this directory.
- **Every stage has a unit test** under `tests/planning/` mirroring the file name.

## When you add a stage

1. Define the stage in `stages/<stage>.py` implementing the protocol in `protocols.py`.
2. Register it in `pipeline.py` at the correct ordinal position.
3. Add `tests/planning/test_<stage>.py`.
4. Update `references/planning-taxonomy.md` if the stage is observable to agents.

## Don'ts

- Don't call into `engine/dispatcher.py` or `engine/executor.py` from here. Planning is upstream of execution.
- Don't add a new task archetype without updating `archetypes.py` *and* `rules/phase_templates.py` *and* a test under `tests/test_archetype_classification.py`.
- Don't bypass the pipeline and write into `Plan` directly from outside `assembly.py`.
