# agent_baton/core/engine/ — protocol surface and state machine

The engine that turns a plan into actions. Inherits: [../../../CLAUDE.md](../../../CLAUDE.md), [../CLAUDE.md](../CLAUDE.md).

## Critical files (treat as public API)

- `protocols.py` — `ExecutionDriver` 15-method interface. **The seam between async runtime and the synchronous engine.** Adding/removing methods is a coordinated change with `TaskWorker` and `WorkerSupervisor`.
- `states.py` — `ExecutionPhaseState`. Encapsulates the mutation epilogue tied to each state cluster. Hybrid dispatch: the dispatch table stays keyed on `DecisionKind`; the state class is consulted only for the mutation tail.
- `executor.py` — drives one execution forward by one action.
- `dispatcher.py` — turns the next action into an `Agent` invocation.
- `planner.py` — produces `Plan` objects (consumed by `cli/commands/execution/`).
- `errors.py` — typed exception hierarchy. Raise these, not `Exception`.
- `flags.py` — feature-flag registry. Every experimental subsystem is gated here.

## Protocol-change discipline

A change is a **protocol change** (not a refactor) when it touches:

- The shape of `_print_action()` in `cli/commands/execution/execute.py`.
- `ActionType` in `models/execution.py`.
- The `ExecutionDriver` method set in `protocols.py`.
- The `ExecutionPhaseState` cluster in `states.py`.
- The CLI verbs `baton execute {start,run,record,gate,resume,complete}`.

Protocol changes require coordinated updates to:

1. `agents/orchestrator.md` — the agent that parses engine output.
2. `references/baton-engine.md` — agent-side protocol contract.
3. `docs/architecture/state-machine.md` and `docs/engine-and-runtime.md`.
4. A migration note in `docs/design-decisions.md`.

If you cannot update all four in the same change, stop and split the work.

## Subsystems within engine/

| Concern | Files |
|---------|-------|
| Planning | `planner.py`, `_planner_helpers.py`, `plan_reviewer.py`, `planning/` |
| Execution loop | `executor.py`, `_executor_helpers.py`, `phase_manager.py` |
| Gates | `gates.py`, `artifact_validator.py` (derives extra commands from agent-created CI workflows, npm scripts, Playwright config, Makefiles, pre-commit; appended to `gate.command` with `&&`) |
| Dispatch | `dispatcher.py`, `dry_run_launcher.py`, `worktree_manager.py` |
| Beads (signals) | `bead_signal.py`, `bead_store.py`, `bead_selector.py`, `bead_decay.py`, `bead_anchors.py` |
| Knowledge | `knowledge_resolver.py`, `knowledge_gap.py`, `knowledge_telemetry.py` |
| Self-heal | `selfheal.py`, `takeover.py` |
| Souls (identity) | `soul_registry.py`, `soul_router.py` |
| Speculation | `speculator.py`, `foresight.py` |
| Cost / classification | `cost_estimator.py`, `classifier.py` |
| Notes (worktree-aware persistence) | `notes_adapter.py`, `notes_replication.py` |
| Team coordination | `team_board.py`, `team_registry.py`, `team_tools.py` |

## Don'ts

- Don't add a state to `ExecutionState` without updating the dispatch table and the agent-side protocol.
- Don't bypass `dispatcher.py` to spawn agents directly from another module.
- Don't read or write `baton.db` from this directory — go through `core/storage/`.
