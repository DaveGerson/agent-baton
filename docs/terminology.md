# Terminology

Consistent definitions for terms used across Agent Baton documentation.

---

## Execution Hierarchy

| Term | Definition |
|------|-----------|
| **Task** | The overall unit of work from plan creation to completion. Has a unique task ID. |
| **Phase** | A group of steps that execute together. Phases run sequentially. Each phase ends with a gate. |
| **Step** | An individual assignment given to one agent. Identified as `PHASE.STEP` (e.g., `2.1`). Steps within a phase may run in parallel. |
| **Gate** | A QA check that runs after a phase completes. Must pass before the next phase starts. |
| **Team Step** | A step that coordinates multiple agents simultaneously. Members identified as `PHASE.STEP.MEMBER` (e.g., `2.1.a`). |

## Agent Roles

| Term | Definition |
|------|-----------|
| **Orchestrator** | The top-level Claude instance that plans, dispatches agents, and drives execution via the `baton` CLI. |
| **Specialist Agent** (or **Subagent**) | A Claude instance dispatched by the orchestrator for a specific step. Does not spawn other agents. |
| **Agent Flavor** | A stack-specific variant of a base agent (e.g., `backend-engineer--python` is a Python flavor of `backend-engineer`). |
| **Skill** | An inline procedure run by the orchestrator itself, not a separate agent. |

## Plan & Execution

| Term | Definition |
|------|-----------|
| **Plan** | A structured execution blueprint created by `baton plan`. Contains phases, steps, agent assignments, and gates. |
| **Risk Level** | Classification of task sensitivity: LOW, MEDIUM, HIGH, CRITICAL. Determines guardrails and approval requirements. |
| **Budget Tier** | Resource allocation: `lean` (minimal agents), `standard` (balanced), `full` (comprehensive). |
| **Model Tier** | Which Claude model an agent runs on: `opus` (most capable), `sonnet` (balanced), `haiku` (fastest). |
| **Delegation Prompt** | The full prompt sent to a specialist agent, including task description, shared context, knowledge, and boundaries. |

## Infrastructure

| Term | Definition |
|------|-----------|
| **Knowledge Pack** | A collection of domain-specific documents in `.claude/knowledge/` that are injected into agent prompts at plan time. |
| **Reference Document** | A procedure or guide in `references/` (or `.claude/references/`) that agents read for workflow guidance. |
| **Guardrail Preset** | A pre-defined set of safety rules applied based on risk level (e.g., what permissions agents get). |
| **Mission Log** | Append-only record of agent dispatches and results in `.claude/team-context/mission-log.md`. |
