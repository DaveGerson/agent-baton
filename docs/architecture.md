---
quadrant: explanation
audience: maintainers, adopters
see-also:
  - [architecture/high-level-design.md](architecture/high-level-design.md)
  - [architecture/technical-design.md](architecture/technical-design.md)
  - [design-decisions.md](design-decisions.md)
---

# Architecture

This page explains *why* Agent Baton is built the way it is. For component-level structure see [`architecture/high-level-design.md`](architecture/high-level-design.md). For internals (state machine, planner, executor, dispatcher, gates, persistence) see [`architecture/technical-design.md`](architecture/technical-design.md). For the package map with `path:line` cites see [`architecture/package-layout.md`](architecture/package-layout.md). For the action enum and transitions see [`architecture/state-machine.md`](architecture/state-machine.md).

## What problem Baton solves

Long Claude Code sessions on cross-cutting tasks tend to: lose context between subtasks, miss test coverage because gating depends on the operator remembering, leave no audit trail, and have no way to recover when the session crashes. Baton adds a project management layer that breaks work into phases, scopes each phase to one specialist agent, enforces automated gates, and persists state so a crashed session resumes cleanly.

The engine does **not** replace Claude. It serves Claude. All judgment and natural-language work stays with the model; the engine owns sequencing, persistence, gates, traces, and learning.

## Design philosophy

1. **Separation of concerns.** Claude owns intelligence (deciding what to do, generating code, reading natural language). The engine owns bookkeeping (state persistence, event tracking, plan sequencing, gate enforcement). Neither trespasses on the other's domain. The interface is the CLI.

2. **Crash recovery by default.** Every state mutation is persisted to disk before the next action is returned. A Claude Code session can be killed mid-execution; `baton execute resume` reconstructs state from the last checkpoint and continues. There is no "in-memory plan" to lose.

3. **Protocol-driven contracts.** The engine exposes formally defined protocols — `ExecutionDriver` (15 methods, in `agent_baton/core/engine/protocols.py`) for runtime consumers and `StorageBackend` for persistence backends. Tests inject lightweight protocol-conforming objects without subclassing concrete implementations.

4. **Layered dependency order.** A strict import hierarchy: `models` → `core subsystems` → `CLI/API`. No circular imports. Each layer depends only on layers below it. Cymbal-investigate any new symbol before adding it to confirm placement.

5. **Graceful degradation.** Historical data (patterns, budget tuning, retrospectives) enriches plans when present. When no prior data exists, the planner falls back to sensible defaults. No subsystem gates execution on the availability of another.

## The three load-bearing invariants

These are documented in detail in [`invariants.md`](invariants.md). In summary:

1. **Engine owns persistence; Claude owns intelligence.** The CLI is the boundary.
2. **Every action is replayable.** State is durable, idempotent, ordered.
3. **Risk classification gates the plan.** Tier (LOW / MEDIUM / HIGH / REGULATED) drives required reviewers, mandatory gates, and approval flow.

Anything that violates one of these is a bug.

## How a task flows through the system

```
User describes task
        │
        ▼
   baton plan ─────────► Risk classifier ─────► Knowledge resolver
        │                       │                       │
        ▼                       ▼                       ▼
   plan.json + plan.md   guardrail preset       reference packs
        │
        ▼
   baton execute start
        │
        ▼
   ┌─── Action loop (until COMPLETE) ───┐
   │                                    │
   │  next_action() ─► DISPATCH         │
   │                   │                │
   │                   ▼                │
   │       Orchestrator spawns agent    │
   │                   │                │
   │                   ▼                │
   │       record_step_result()         │
   │                                    │
   │  next_action() ─► GATE             │
   │                   │                │
   │                   ▼                │
   │       Run pytest/lint/etc          │
   │                   │                │
   │                   ▼                │
   │       record_gate_result()         │
   │                                    │
   │  next_action() ─► APPROVAL         │
   │                   │                │
   │                   ▼                │
   │       Wait for sign-off            │
   │                                    │
   └────────────────────────────────────┘
        │
        ▼
   baton execute complete ──► trace + retro + scores
        │
        ▼
   Learning loop folds outcomes back into routing/budgets
```

For the precise protocol contract between Claude and the CLI (the `_print_action()` shape, exit codes, environment variables) see [`references/baton-engine.md`](../references/baton-engine.md).

## The three interfaces

Baton exposes three surfaces to the outside world:

- **CLI** (`baton ...`) — the primary interface. Drives planning, execution, observation, governance, learning, distribution. See [`cli-reference.md`](cli-reference.md).
- **REST API** (FastAPI, optional) — exposes the same operations over HTTP for the PMO UI and external integrations. See [`api-reference.md`](api-reference.md).
- **PMO frontend** (React/Vite) — visualizes plans, executions, traces, retros, scores across projects. Served at `/pmo/`.

The engine itself is the same Python package behind all three; the surfaces are thin wrappers.

## Subsystem map

The following pages explain individual subsystems in depth:

- [`engine-and-runtime.md`](engine-and-runtime.md) — planner, executor, dispatcher, gate enforcement, INTERACT primitive, swarm dispatch
- [`governance-knowledge-and-events.md`](governance-knowledge-and-events.md) — risk classifier, policy engine, knowledge resolver, event bus
- [`observe-learn-and-improve.md`](observe-learn-and-improve.md) — tracing, telemetry, scoring, evolution proposals, learning automation
- [`storage-sync-and-pmo.md`](storage-sync-and-pmo.md) — SQLite layout, federated sync, PMO store, Smart Forge
- [`finops-chargeback.md`](finops-chargeback.md) — token/cost attribution model
- [`design-decisions.md`](design-decisions.md) — ADR log: every decision and what it superseded
- [`daemon-mode-evaluation.md`](daemon-mode-evaluation.md) — historical evaluation of the daemon-mode design

## Why "phases"?

A phase is a named, gated segment of a plan. The phase shape is load-bearing because:

- It bounds context. Each phase has its own set of files and concerns; the dispatched agent reads only what its phase needs.
- It bounds risk. The classifier assigns a tier per phase; HIGH-risk phases require approval before any agent runs.
- It bounds cost. Each phase has a budget tier (`tight` / `standard` / `generous`) and the run-level `BATON_RUN_TOKEN_CEILING` hard-stops the loop on overruns.
- It bounds rollback. If a phase fails, only that phase's commits need to be reverted — earlier phases remain.
- It composes. Phases can run sequentially or in parallel (when their concerns are disjoint).

Steps within a phase are smaller units: a DISPATCH (one agent), a GATE (one check), an INTERACT (one dialogue), or a SWARM_DISPATCH (parallel agents whose output is reconciled).

## Why a planner separate from execution?

Planning is fundamentally different work from execution. The planner reasons about scope, risk, and resource allocation; the executor runs a fixed sequence. Separating them lets us:

- Replan without re-executing. `baton execute amend-plan` can adjust phases mid-execution without losing state.
- Use different models for each. Planner uses opus by default; executor steps default to sonnet for backend/frontend work and haiku for triage.
- Inspect the plan before paying for execution. `baton plan ... --explain` shows the reasoning; `--save` persists it for later.

The Smart Forge subsystem can dispatch the planner's reasoning to a headless Claude Code subprocess when richer plan synthesis is needed; see [`storage-sync-and-pmo.md`](storage-sync-and-pmo.md).

## Why federated SQLite?

Each project owns its `baton.db`. A central database (`~/.config/agent-baton/central.db`) federates project data for cross-project views in the PMO UI. The split exists because:

- A project should remain useful when its central DB is unavailable.
- A central DB is convenient for cross-project search, scoring, learning rollups, but contains nothing a project itself relies on.
- Sync is bidirectional and idempotent; either side can be rebuilt from the other.

Treat the per-project DB as primary. Never query `project_id` from a per-project DB — that column lives only in `central.db`.

## Why a learning loop?

After every execution, the engine writes a trace, usage log, and retrospective. The `learning-analyst` agent reads these and proposes config improvements. The `system-maintainer` agent reads escalated proposals and conservatively applies safe changes to `learned-overrides.json` (never source code). Over time the routing, budget, and gating defaults converge toward what works on this codebase.

This loop is the closure that distinguishes Baton from a one-shot orchestrator: the engine learns from every run.

## Where to read next

- For *what's where* in the codebase: [`architecture/package-layout.md`](architecture/package-layout.md).
- For *how the state machine actually transitions*: [`architecture/state-machine.md`](architecture/state-machine.md).
- For *why specific calls were made*: [`design-decisions.md`](design-decisions.md).
- For *how to use Baton on a real task*: [`examples/first-run.md`](examples/first-run.md) (tutorial) or [`orchestrator-usage.md`](orchestrator-usage.md) (recipes).
