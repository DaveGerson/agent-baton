---
quadrant: reference
audience: both
see-also:
  - [architecture.md](architecture.md)
  - [cli-reference.md](cli-reference.md)
---

# Terminology

Canonical terms used across the codebase, CLI, and docs. Alphabetical.

| Term | Meaning |
|------|---------|
| **Action** | A unit emitted by the engine to drive the orchestration loop. One of: DISPATCH, GATE, APPROVAL, COMPLETE, FAILED, WAIT, FEEDBACK, INTERACT, SWARM_DISPATCH. See `ActionType` in `agent_baton/models/execution.py`. |
| **Agent** | A distributable specialist defined in `agents/<name>.md` (frontmatter + prompt body). Dispatched by the orchestrator or invoked directly via Claude Code's `Agent` tool. |
| **Approval** | Explicit human (or designated reviewer) sign-off required for HIGH-risk plans and certain phase transitions. Driven by the APPROVAL action and `baton execute approve`. |
| **Bead** | A persistent incident or follow-up record in `baton.db`. Created with `baton beads create`. Used for autonomous bug filing, regression beads, audit trails. |
| **Baton** | The project name (capitalized in prose). |
| **`baton`** | The CLI binary (lowercase, monospace). |
| **`agent_baton`** | The Python package (snake_case, importable). |
| **Budget** | The token/cost ceiling for an execution. Tiers are `tight`, `standard`, `generous`. Set per phase by the planner. Run-level cap: `BATON_RUN_TOKEN_CEILING`. |
| **Central DB** | `~/.config/agent-baton/central.db` — federated registry of all projects' executions. Distinct from each project's `baton.db`. |
| **Classification** | Risk tier (LOW / MEDIUM / HIGH / REGULATED) assigned by the planner using `core/govern/classifier.py`. |
| **Daemon** | The optional long-running process (`baton daemon`) that watches for triggers and drives executions without explicit invocation. |
| **Dispatch** | The act of spawning a specialist agent for a step. Surfaced as the DISPATCH action. |
| **Engine** | The Python orchestration code in `agent_baton/core/engine/`. Owns state machine, persistence, gating, tracing. |
| **Federated sync** | Bidirectional sync between per-project `baton.db` files and the central database. Driven by `baton sync`. |
| **Forge** | The plan-generation subsystem. May invoke a headless Claude Code subprocess for plan synthesis. |
| **Gate** | An automated check between phases (typically `pytest`, lint, type check, link check). Emitted as the GATE action. Recorded with `baton execute gate`. |
| **Guardrail preset** | A bundle of risk-tier-specific rules: required reviewers, mandatory gates, allowed tools. See `references/guardrail-presets.md`. |
| **Immune system** | Background `immune-*` agents that proactively scan for drift (deprecated APIs, doc drift, stale comments, TODO rot, untested edges). |
| **INTERACT** | A multi-turn step primitive that pauses the loop for back-and-forth with a user or another agent. |
| **Knowledge pack** | A discoverable bundle of reference docs in `.claude/knowledge/<pack>/` with a `knowledge.yaml` manifest. Resolved by the knowledge router during planning. |
| **Orchestrator** | The agent (`agents/orchestrator.md`) that drives `baton plan` → `baton execute` for complex tasks. |
| **Phase** | A named segment of a plan. Composed of one or more steps and (optionally) a gate. Phases are sequential by default; some can run in parallel. |
| **Plan** | The output of `baton plan`. Two artifacts: machine-readable `plan.json` and human-readable `plan.md`, both in `.claude/team-context/`. |
| **PMO** | The optional REST API + React UI (served at `/pmo/`) that visualizes plans, executions, traces, retrospectives, and learning data across projects. |
| **Reference procedure** | A reusable, agent-readable doc in `references/<name>.md` that codifies a workflow contract (engine protocol, routing logic, guardrails, patterns). |
| **Retrospective** | The post-execution analysis written by the engine (and optionally augmented by `learning-analyst`). Stored in `.claude/team-context/retros/`. |
| **Self-heal** | The auto-fix subsystem with three tiers (`self-heal-haiku`, `self-heal-sonnet`, `self-heal-opus`) invoked when gates fail. |
| **Step** | A unit of work within a phase. Either a DISPATCH (agent), a GATE (check), or an INTERACT (dialogue). |
| **Subagent** | An agent dispatched by another agent (e.g., the orchestrator dispatches `backend-engineer`). |
| **Swarm** | The experimental parallel-dispatch subsystem. Gated behind `BATON_EXPERIMENTAL=swarm`. |
| **Trace** | The full event log of an execution. Generated automatically; inspected via `baton trace`. |

For the architectural concepts and how they relate, see [`architecture.md`](architecture.md).
