---
quadrant: reference
audience: both
see-also:
  - [architecture.md](architecture.md)
  - [architecture/state-machine.md](architecture/state-machine.md)
---

# Invariants

Three load-bearing invariants. Anything that violates them is a bug.

## 1. Engine owns persistence; Claude owns intelligence

Every state mutation is persisted by the Python engine before the next action is emitted. Claude (the orchestrator agent) is stateless across turns and recovers context by reading engine output. Neither side reaches into the other's domain — the engine never invents tasks, Claude never edits state directly.

**Where it lives:** `agent_baton/core/engine/state.py`, `agent_baton/core/engine/executor.py`. The `_print_action()` function in `agent_baton/cli/commands/execution/execute.py` is the contract surface between the two.

## 2. Every action is replayable

Crashing in the middle of a phase is a normal event. `baton execute resume` picks up from the last persisted action and proceeds without re-doing completed steps. This requires that every action — DISPATCH, GATE, APPROVAL, COMPLETE, FAILED, WAIT, FEEDBACK, INTERACT, SWARM_DISPATCH — is durable, idempotent, and ordered.

**Where it lives:** `agent_baton/models/execution.py` (`ActionType` enum, `ExecutionState`), `agent_baton/core/engine/state.py` transitions.

## 3. Risk classification gates the plan

The planner classifies every task into a risk tier (LOW, MEDIUM, HIGH, REGULATED) and applies the matching guardrail preset before any agent is dispatched. HIGH and REGULATED plans cannot proceed without explicit approval. The auditor agent has veto authority on MEDIUM+ work.

**Where it lives:** `agent_baton/core/govern/classifier.py`, `references/guardrail-presets.md`, the `APPROVAL` action in the state machine.

---

The detailed CLI surface contract, `_print_action()` output format, and `ExecutionState` disk schema (the operational specifics of these invariants) live in [`architecture/technical-design.md`](architecture/technical-design.md) and [`architecture/state-machine.md`](architecture/state-machine.md). For the rationale behind these invariants, see [`architecture.md`](architecture.md).
