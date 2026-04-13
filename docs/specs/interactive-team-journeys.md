# Interactive Team Journeys — Analysis

**Date**: 2026-03-28
**Status**: Analysis (informs roadmap prioritization)

---

## Overview

Three target journeys that push beyond plan-then-execute into iterative,
conversational co-creation where humans and agents work together in tight
feedback loops.

All three share a common pattern: **the human does not know the full plan
upfront.** Each step's outcome shapes the next question. This is
fundamentally different from the engine's current model where a complete
plan is generated before execution begins.

---

## Journey 1: Co-Collaborative Design

> A mock user and a developer walk through a browser-based UX test,
> discussing functionalities and feasibility in real time. Applying
> changes and iteratively testing for fit.

### What it requires

An iterative design loop where a user persona and a developer are both
present. The user expresses opinions about UI behavior; the developer
makes changes; both observe the result. Tight feedback cycles measured
in minutes.

### Existing capabilities that help

| Capability | How it applies |
|---|---|
| `frontend-engineer` agent | Builds UI components, has Write/Edit tools |
| `subject-matter-expert` agent | Domain context, can serve as "mock user" voice |
| `architect` agent | Feasibility advisor (read-only) |
| `approve-with-feedback` mechanism | Inserts remediation phases from human feedback |
| `baton execute amend` | Injects new steps into a running plan |

### Gap analysis

1. **No conversational loop primitive.** The engine processes one action
   at a time: dispatch, record, next. No concept of "send a message to
   an already-running agent."
2. **No live preview integration.** No mechanism to launch a dev server,
   observe browser behavior, and feed observations back.
3. **Agents are stateless between dispatches.** Each dispatch creates a
   fresh prompt. No persistent session where an agent accumulates context.
4. **Approval gates are binary checkpoints**, not dialogue.

### Proposed approach: Micro-Cycle Plan

Model this as lightweight, single-step phases that repeat:

```
[SME reviews current state] -> [Human approves/redirects]
  -> [Frontend engineer applies change] -> [repeat]
```

Use `baton execute amend` to dynamically inject each next iteration based
on the prior result. The human (or an orchestrating Claude session)
evaluates and calls `amend` for the next micro-cycle.

The SME agent acts as the "mock user voice" — its delegation prompt says:
"Evaluate this UI from the perspective of [persona], identify what feels
wrong or confusing, suggest specific changes."

### Agent roster

| Role | Agent | Purpose |
|---|---|---|
| Mock user / UX evaluator | `subject-matter-expert` | React from user persona perspective |
| Implementer | `frontend-engineer` | Apply code changes to UI |
| Feasibility advisor | `architect` | Flag architecturally expensive changes |
| Quality check | `code-reviewer` | Gate between iterations |

### Execution flow

1. `baton plan "Test checkout flow UX with [persona]"` — generates Phase 1:
   SME evaluates current UI
2. SME returns: "Checkout button below fold; validation errors appear late"
3. APPROVAL gate — human confirms priorities
4. `baton execute amend` adds Phase 2: frontend-engineer implements top fix
5. `baton execute amend` adds Phase 3: SME re-evaluates
6. Repeat until satisfied → `baton execute complete`

### Key insight

The plan is a living document that grows one micro-phase at a time. The
`amend` mechanism supports this, but the human must manually drive each
cycle. The gap is **automation of the iteration loop** — the system should
propose the next micro-phase based on the previous result.

---

## Journey 2: Real-Time Deep Dive

> A business executive works with a data analyst and consultant live to
> request analytical capabilities in a dashboard and identify additional
> analytics for root cause analysis.

### What it requires

A collaborative analytical session where a non-technical stakeholder
directs analysis in real time. The executive asks "Why did revenue drop
in Q3?", the analyst runs queries, the consultant interprets business
implications, and follow-up questions emerge from what they see.

### Existing capabilities that help

| Capability | How it applies |
|---|---|
| `data-analyst` agent | SQL queries, metric definition, data exploration |
| `data-scientist` agent | Statistical analysis, root cause modeling |
| `visualization-expert` agent | Chart design, dashboard layout |
| `subject-matter-expert` agent | Business context interpretation |
| ForgeSession interview flow | Iterative refinement through Q&A |
| Knowledge attachments | Data dictionaries, business context docs |

### Gap analysis

1. **No analytical session concept.** No mechanism for an agent to produce
   preliminary findings, have the human react, then dig deeper within the
   same dispatch.
2. **No intermediate result visibility.** Only the final `StepResult.outcome`
   is visible. No streaming of partial findings or "should I dig deeper?"
3. **No follow-up dispatch with accumulated context.** Each dispatch starts
   fresh with only `handoff_from` text.
4. **No multi-agent dialogue.** The engine dispatches agents independently,
   never in a conversational pattern.

### Proposed approach: Two-Mode Workflow

**Mode A — Exploration (conversational, human-driven):** Work directly in
a Claude Code session. The human asks questions, dispatches agents via the
Agent tool, and iterates. This is NOT orchestrated through `baton execute`
— it uses native Claude Code conversation. The SME provides business
context, the data-analyst runs queries, the data-scientist performs deeper
analysis.

**Mode B — Dashboard Build (plan-driven, engine-orchestrated):** Once
exploration identifies the required analytical capabilities, create a
`baton plan` that codifies them. Standard plan-execute flow: visualization-
expert designs, frontend-engineer builds, data-analyst writes backing
queries.

**The bridge**: A "findings document" — a markdown artifact from Mode A
capturing discovered metrics, root causes, drill-down capabilities, and
data quality issues. This becomes a knowledge attachment for Mode B's plan.

### Agent roster

| Role | Agent | Mode |
|---|---|---|
| Data exploration | `data-analyst` | A (conversational) |
| Root cause analysis | `data-scientist` | A (conversational) |
| Business context | `subject-matter-expert` | A (conversational) |
| Dashboard design | `visualization-expert` | B (planned) |
| Dashboard build | `frontend-engineer` | B (planned) |

### Key insight

This journey has two fundamentally different halves. The exploration phase
is inherently **conversational and non-plannable** — the executive doesn't
know what questions they'll ask until they see the previous answer. Forcing
this into a pre-planned flow produces a useless plan. The system needs to
recognize that some work is exploratory (conversation mode) and some is
structured (engine mode), with the findings document as the bridge.

---

## Journey 3: Financial Analyst + Cloud Expert

> A financial analyst estimates the total cost of ownership of an app
> alongside an expert in cloud hosting.

### What it requires

A collaborative estimation session where two specialists with
complementary expertise build a shared model. The analyst structures the
TCO framework (categories, time horizons, discount rates), the cloud
expert provides cost inputs (instance types, storage tiers, egress costs).
They iterate: analyst proposes, cloud expert fills numbers, analyst
identifies gaps, cloud expert suggests alternatives, they converge.

### Existing capabilities that help

| Capability | How it applies |
|---|---|
| `subject-matter-expert` agent | Cloud hosting domain knowledge |
| `data-analyst` agent | Financial modeling, sensitivity analysis |
| `architect` agent | Application resource requirements |
| Team steps with `depends_on` | Ordering within a team |
| `approve-with-feedback` | "Model one more scenario" loop |
| Knowledge packs | Cloud pricing docs, architecture diagrams |

### Gap analysis

1. **No "financial analyst" or "cloud expert" agent.** The SME is generic
   enough with proper prompting, but purpose-built agents would be better.
2. **No shared working artifact.** A TCO model is a single document both
   contribute to. `handoff_from` passes a summary, not a structured doc.
3. **No negotiation pattern.** "Model both scenarios" requires multiple
   dispatch cycles.

### Proposed approach: Structured Team Step + Shared Artifact

1. Create two custom agents via `talent-builder`: `financial-analyst` and
   `cloud-cost-expert`.
2. Multi-phase plan where each phase is one refinement cycle:
   - Phase 1: `architect` identifies required cloud resources
   - Phase 2 (team): `cloud-cost-expert` prices resources;
     `financial-analyst` builds TCO framework. Both read/write `tco-model.md`.
   - Phase 3: `financial-analyst` performs sensitivity analysis
   - Phase 4: APPROVAL gate — human reviews, requests adjustments
   - Phase 5+: Injected via `amend` based on feedback

### Key insight

This is the most tractable journey within the existing architecture. There
is a clear deliverable (TCO model), a defined methodology, and the iteration
is about refining assumptions. The `approve-with-feedback` → remediation
pattern maps well to "model one more scenario." The main gap is agent
specialization, not architecture.

---

## Cross-Cutting Analysis

### Common Pattern: Steps as Conversations

All three journeys need something the engine doesn't have: **multi-turn
interaction within a single step.** The engine's atomic unit is dispatch-
and-return. Every journey needs agents that stay engaged across multiple
human interactions.

Four shared capability gaps:

1. **Conversational multi-turn within a step.** Agents that accumulate
   context across interactions rather than starting fresh.
2. **Shared mutable artifacts.** Multiple agents contributing to a single
   document (UI code, findings doc, TCO model).
3. **Human-in-the-loop at sub-step granularity.** Not just "approve the
   phase" but "dig deeper into that number."
4. **Dynamic agent re-engagement.** Re-dispatching the same agent with
   accumulated context.

### The Key Capability to Build: Iterative Step Execution

A new `mode: iterative` flag on plan steps that keeps the step open for
multiple human-agent exchanges:

```
PENDING -> RUNNING -> AWAITING_INPUT -> RUNNING -> AWAITING_INPUT -> ... -> COMPLETE
```

Concretely:
- New `ActionType.INTERACT` returning intermediate output + waiting for input
- Step stays `RUNNING` across interact cycles
- Accumulated context preserved in a growing `interaction_history` field
- Human ends interaction with explicit "done" signal → step COMPLETE

**Trade-offs:**
- Determinism: batch execution is reproducible; iterative depends on input
- Cost: no upper bound on tokens. Needs per-step cost limit warnings.
- Parallelism: iterative steps block on human input
- Headless: `baton execute run` can't drive interactive steps

### Recommended Sequencing

| Priority | Capability | Unlocks |
|---|---|---|
| **Now** | Use existing `amend` + micro-cycle pattern | Journey 1, Journey 3 (manual but functional) |
| **P1** | Purpose-built agents via `talent-builder` | Journey 3 (financial-analyst, cloud-cost-expert) |
| **P1** | Two-mode workflow (explore → plan) | Journey 2 (findings doc as bridge) |
| **P2** | `ActionType.INTERACT` (iterative steps) | All three journeys (native support) |
| **P2** | Shared mutable artifact protocol | All three (structured co-editing) |

### What Works Today (Without New Features)

**Journey 1**: Use a Claude Code session with the orchestrator agent. The
orchestrator dispatches `subject-matter-expert` and `frontend-engineer` in
alternating turns. The human reviews each round and provides direction. No
engine needed — pure conversation mode with agent dispatches.

**Journey 2**: Mode A (exploration) works today in a Claude Code session.
The user dispatches `data-analyst` and `data-scientist` interactively. Mode
B (dashboard build) works with `baton plan` + `baton execute`. The bridge
(findings.md as knowledge attachment) works today.

**Journey 3**: Use `baton plan` with `--agents architect,data-analyst` and
knowledge attachments for cloud pricing. Use `approve-with-feedback` at
each gate to inject "model this additional scenario." Functional today,
just requires more manual orchestration.

---

## Relationship to Agent Teams Spec

The agent-teams-enablement spec (Phases 1-4) provides the foundation for
these journeys:

- **Phase 1 (Team Execution)**: Wave dispatch and synthesis enable Journey 3's
  multi-specialist collaboration.
- **Phase 2 (Context Sharing)**: Decision log enables Journey 1's design
  decisions propagating from architect to frontend engineer.
- **Phase 3 (Patterns)**: The Challenge pattern maps to Journey 1's
  "propose → critique → revise" UX loop. The Panel pattern maps to
  Journey 3's multi-perspective estimation.
- **Phase 4 (Daemon Integration)**: Real-time monitoring enables all three
  journeys to show live progress on the PMO board.

The journeys push beyond the spec into **iterative execution** — the spec
assumes a known plan executed to completion, while the journeys assume an
evolving plan shaped by real-time human input. Building iterative step
execution (ActionType.INTERACT) on top of the agent teams foundation is the
logical Phase 5.
