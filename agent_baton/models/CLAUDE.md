# agent_baton/models/ — Pydantic data models

Wire types and persisted shapes. Cross-cutting rules: [../../CLAUDE.md](../../CLAUDE.md).

## Critical models

- `execution.py` — `ActionType` enum (9 values: `DISPATCH`, `GATE`, `APPROVAL`, `COMPLETE`, `FAILED`, `WAIT`, `FEEDBACK`, `INTERACT`, `SWARM_DISPATCH`) and `ExecutionState`. **Adding a new `ActionType` is a protocol change** — coordinate with `agents/orchestrator.md`, `references/baton-engine.md`, and `cli/commands/execution/execute.py::_print_action`.
- `plan.py` — `Plan`, `Phase`, `Step` produced by the planner.
- `bead.py` — bead (signal) shape persisted by the bead store.
- `agent.py`, `team.py`, `registry.py` — agent and team identities.
- `decision.py`, `feedback.py`, `retrospective.py` — governance artifacts.
- `knowledge.py`, `knowledge_ab.py` — knowledge packs and A/B variants.

## Conventions

- All models are Pydantic — no plain dataclasses for persisted or serialized shapes.
- Enums live alongside the model that uses them, except the broadly-shared ones in `enums.py`.
- Migrations: changing a model that hits the DB requires a migration in `core/storage/`. Never silently change shape.
- Backwards-compat: persisted models keep field defaults so older `baton.db` files load. If you must rename, add a validator to read the old name.
- Don't put behavior here. Methods are limited to validation, normalization, and pure derivations. Business logic belongs in `core/`.

## When adding a model

1. Define it here.
2. If persisted: add a migration in `core/storage/`.
3. If exposed over HTTP: wrap it in a request/response model under `api/models/` rather than returning the engine type directly.
4. Add a test under `tests/models/`.
