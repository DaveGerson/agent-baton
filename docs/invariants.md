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

Crashing in the middle of a phase is a normal event. `baton execute resume` picks up from the last persisted action and proceeds without re-doing completed steps. This requires that every action — DISPATCH, GATE, APPROVAL, COMPLETE, FAILED, WAIT, FEEDBACK, INTERACT — is durable, idempotent, and ordered.

**Where it lives:** `agent_baton/models/execution.py` (`ActionType` enum, `ExecutionState`), `agent_baton/core/engine/state.py` transitions.

## 3. Risk classification gates the plan

The planner classifies every task into a risk tier (LOW, MEDIUM, HIGH, REGULATED) and applies the matching guardrail preset before any agent is dispatched. HIGH and REGULATED plans cannot proceed without explicit approval. The auditor agent has veto authority on MEDIUM+ work.

**Where it lives:** `agent_baton/core/govern/classifier.py`, `references/guardrail-presets.md`, the `APPROVAL` action in the state machine.

---

## 4. The `_print_action()` wire format is a public contract

The `_print_action()` function in [`cli/commands/execution/execute.py`](../agent_baton/cli/commands/execution/execute.py) emits one of the following block shapes per action. **The field labels, ordering, and delimiters are part of the contract** — orchestrator agents in production parse this text. Changing the shape requires coordinated updates to `agents/orchestrator.md`, `references/baton-engine.md`, `agent_baton/models/execution.py::ActionType`, and the state-machine docs.

```
ACTION: DISPATCH
  Agent: <agent_name>
  Model: <agent_model>
  Step: <step_id>
  Message: <description>
  Expected: <demo>            # optional

--- Delegation Prompt ---
<prompt_text>
--- End Prompt ---
```

```
ACTION: GATE
  Gate Type: <gate_type>
  Gate Command: <gate_command>
  Phase: <phase_id>
```

```
ACTION: APPROVAL
  Phase: <phase_id>
  Context: <approval_context>
  Options: <approval_options>
```

```
ACTION: FEEDBACK
  Phase: <phase_id>
  Questions: <q1>, <q2>, ...
```

```
ACTION: INTERACT
  Step: <step_id>
  Agent: <interact_agent_name>
  Turn: <interact_turn>/<interact_max_turns>
  Prompt: <interact_prompt>
```

```
ACTION: WAIT
  Message: <description>
```

```
ACTION: COMPLETE
  Summary: <summary>
```

```
ACTION: FAILED
  Summary: <summary>
```

```
ACTION: CHECKPOINT
  Message: <description>
```

### Adding a new ActionType

Adding a new `ActionType` is a protocol change. It must coordinate with:

1. `agents/orchestrator.md` — the agent that parses this output.
2. `references/baton-engine.md` — agent-side protocol contract.
3. `agent_baton/models/execution.py::ActionType` — enum definition.
4. `docs/architecture/state-machine.md` and `docs/engine-and-runtime.md`.
5. A migration note in `docs/design-decisions.md`.

The G1 `/goal` integration (ADR-24) explicitly avoided this path: goal evaluation runs internally inside `record_gate_result` and does NOT emit a new ActionType. G2 (first-class `GOAL` ActionType) is deferred until regulated-domain auditor work demands discrete goal-check events; this contract entry exists so that future work has an explicit baseline to extend.

---

The full operational specifics — `ExecutionState` disk schema and per-state mutation rules — live in [`architecture/technical-design.md`](architecture/technical-design.md) and [`architecture/state-machine.md`](architecture/state-machine.md). For the rationale behind these invariants, see [`architecture.md`](architecture.md).
