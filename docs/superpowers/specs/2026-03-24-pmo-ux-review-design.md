# PMO UX Review — Multi-Agent Design Spec

## Overview

A parallel multi-agent UX review of the PMO system (Kanban board, Forge plan builder, signal management) focused on workflow completeness and interaction efficiency for busy managers and senior team leads managing larger products without dedicated support staff.

## Persona

**Target user:** Engineering manager or senior tech lead who:
- Manages a portfolio of products across multiple projects/programs
- Has no dedicated support staff — balances hands-on-keyboard delivery with oversight
- Needs to triage bugs and small fixes quickly so the team can focus on feature work
- Uses the Forge to decompose features/PRDs into executable plans
- Uses the Kanban board to stage and monitor plan execution

**Primary workflows (priority order):**
1. **Triage mode** — Signals (bugs, escalations) pile up; manager needs to forge fix plans and queue them fast
2. **Plan authoring** — Feature/PRD intake through Forge: decompose, refine via interview, approve
3. **Kanban one-shot** — Bug or small item on the board gets a single-click generate-and-queue without leaving the board
4. **Board oversight** — Check portfolio health, spot blockers, decide where to intervene

**Speed requirement:** Plans and work should flow from board items into the Forge and back to the board with minimal manual steps. One-shot plan generation for bugs is a key workflow that doesn't exist yet.

## Architecture: Parallel Fan-out + Synthesis

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  Scenario    │  │  Workflow    │  │  Interaction │  │  Architecture│
│  Writer      │  │  Auditor     │  │  Analyst     │  │  Fitness     │
│  (architect) │  │  (react-fe)  │  │  (react-fe)  │  │  (architect) │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │                 │
       ▼                 ▼                 ▼                 ▼
   scenarios.md    workflow-audit.md  interaction-     architecture-
                                     analysis.md      fitness.md
       │                 │                 │                 │
       └─────────────────┴────────┬────────┴─────────────────┘
                                  ▼
                    ┌──────────────────────┐
                    │  Synthesis + Plan    │
                    │  Builder             │
                    │  (orchestrator)      │
                    └──────────┬───────────┘
                               ▼
                  AUDIT.md  ISSUES.md  REMEDIATION-PLAN.md
```

All 4 research agents launch simultaneously. Each reads the codebase, design specs, and PMO taxonomy independently. The synthesis agent runs after all 4 complete, merging findings into scored audit, actionable issues, and a baton-ready remediation plan.

**User touchpoint:** Review the 3 synthesis outputs only. No intermediate checkpoints.

## Agent Specifications

### Agent 1: Scenario Writer

- **Agent type:** architect
- **Mission:** Write 6-8 concrete user scenarios grounded in the persona
- **Input:** PMO taxonomy doc (`reference_files/baton-pmo/baton_pmo_taxonomy.md`), Forge design spec (`docs/superpowers/specs/2026-03-24-forge-smart-plan-generation-design.md`), PMO data models (`agent_baton/models/pmo.py`), API routes (`agent_baton/api/routes/pmo.py`)
- **Output:** `docs/reviews/pmo-ux/scenarios.md`
- **Scenario types:**
  - Triage 3 bugs into queued fix plans in under 5 minutes
  - Forge a feature plan from an ADO PRD/feature description
  - One-shot bug fix plan from a Kanban card (single action)
  - Check portfolio health across 4 projects, identify the worst blocker
  - Signal-to-plan escalation: bug signal → triage → forge plan → queue
  - Resume an interrupted forge session (tab closed mid-interview)
  - Batch triage: multiple signals → multiple plans in one pass
  - Board-to-forge: pull a queued card back into forge for re-planning

### Agent 2: Workflow Auditor

- **Agent type:** frontend-engineer--react
- **Mission:** Trace every user workflow through actual React components and API calls. Find dead ends, missing transitions, forced CLI fallbacks.
- **Input:** All `pmo-ui/src/` components, API routes (`agent_baton/api/routes/pmo.py`), ForgeSession (`agent_baton/core/pmo/forge.py`), PmoScanner (`agent_baton/core/pmo/scanner.py`), `pmo-ui/src/hooks/usePmoBoard.ts`, PMO taxonomy (`reference_files/baton-pmo/baton_pmo_taxonomy.md`), Forge design spec (`docs/superpowers/specs/2026-03-24-forge-smart-plan-generation-design.md`)
- **Output:** `docs/reviews/pmo-ux/workflow-audit.md`
- **Checks:**
  - Forge intake → generate → preview → edit → interview → approve flow completeness
  - Kanban card → detail expansion → available actions (what can you DO from a card?)
  - Signal create → triage → forge-plan link → card appears on board
  - Board ↔ Forge navigation and context carry (does switching lose state?)
  - Error recovery paths (API failure, generation timeout, network drop)
  - State persistence across tab switches and page reloads
  - One-shot plan path: does it exist? If not, what would need to be added?
  - Batch operations: can you act on multiple items at once?

### Agent 3: Interaction Analyst

- **Agent type:** frontend-engineer--react
- **Mission:** Count clicks, measure cognitive load, identify unnecessary friction. Focus on speed for a context-switching manager.
- **Input:** All UI components (ForgePanel, KanbanBoard, KanbanCard, PlanEditor, PlanPreview, InterviewPanel, AdoCombobox, HealthBar), design tokens, CSS, PMO taxonomy (`reference_files/baton-pmo/baton_pmo_taxonomy.md`), Forge design spec (`docs/superpowers/specs/2026-03-24-forge-smart-plan-generation-design.md`)
- **Output:** `docs/reviews/pmo-ux/interaction-analysis.md`
- **Measures:**
  - Clicks-to-complete for each key task (triage a bug, forge a plan, check health)
  - Information density per view — is the right data visible without drilling?
  - Scroll depth requirements — can key actions be reached above the fold?
  - Form field count vs. necessity in Forge intake
  - Keyboard navigation and shortcuts (do they exist?)
  - Missing batch operations (triage 3 bugs = 3× full forge flow?)
  - One-shot path assessment for Kanban items
  - Modal/panel transitions — do they feel fast or interruptive?
  - Data refresh behavior — does polling create visual jank?

### Agent 4: Architecture Fitness

- **Agent type:** architect
- **Mission:** Assess whether the backend API surface, data model, and integration points support the UX workflows or force the frontend to work around limitations.
- **Input:** API routes (`agent_baton/api/routes/pmo.py`), models (`agent_baton/models/pmo.py`), ForgeSession (`agent_baton/core/pmo/forge.py`), PmoScanner (`agent_baton/core/pmo/scanner.py`), SyncEngine (`agent_baton/core/storage/sync.py`), CentralStore (`agent_baton/core/storage/central.py`), ADO adapter (`agent_baton/core/storage/adapters/ado.py`), PMO taxonomy (`reference_files/baton-pmo/baton_pmo_taxonomy.md`), Forge design spec (`docs/superpowers/specs/2026-03-24-forge-smart-plan-generation-design.md`)
- **Output:** `docs/reviews/pmo-ux/architecture-fitness.md`
- **Checks:**
  - API endpoints vs. UI needs — are there missing routes the frontend needs?
  - Data model completeness for one-shot plans (can a card trigger plan generation?)
  - Scanner refresh latency vs. UX expectations (5s polling vs. push)
  - Forge session state management — is state recoverable after interruption?
  - Signal-to-plan data flow — does the full chain work end-to-end?
  - Kanban-to-execution bridging — can a plan be launched from the board?
  - Batch operation support in the API layer
  - ADO integration surface — what's stubbed vs. what's wired?
  - WebSocket/SSE readiness for real-time updates

### Agent 5: Synthesis + Plan Builder

- **Agent type:** general-purpose (dispatched as a subagent with full tool access)
- **Mission:** Merge all 4 agent findings into a single scored audit, deduplicate, severity-rank, and produce a baton-ready remediation plan.
- **Input:** All 4 research agent outputs + original design specs
- **Output:**
  - `docs/reviews/pmo-ux/AUDIT.md` — Scored dashboard, severity-ranked findings, per-workflow heatmap, executive summary
  - `docs/reviews/pmo-ux/ISSUES.md` — GitHub-issue-style tickets with repro steps, proposed fix, affected files, complexity estimate (S/M/L), inter-issue dependencies
  - `docs/reviews/pmo-ux/REMEDIATION-PLAN.md` — Sequenced phases grouped by workflow area, dependency-ordered, with agent assignments and gate criteria, structured for `baton plan` conversion

## Scoring Model

6 dimensions, each scored 0-10:

| Dimension | What it measures | Score 10 | Score 0 |
|-----------|-----------------|----------|---------|
| **Workflow Completeness** | Can every scenario complete end-to-end in UI? | All paths complete | Most require CLI |
| **Triage Velocity** | Speed of triaging N bugs into queued plans | Batch ops, one-shot, minimal clicks | Each bug = full Forge flow |
| **Forge Authoring Flow** | Intake → generate → edit → interview → approve smoothness | Intuitive, fast, recoverable | Confusing, lost state |
| **Board ↔ Forge Integration** | Bidirectional flow between Kanban and Forge | Seamless, context-carrying | Tab switch, no context |
| **Interaction Efficiency** | Clicks, cognitive load, information density | Minimal friction, keyboard shortcuts | Death by a thousand clicks |
| **API-UX Alignment** | Backend supports frontend needs | Purpose-built API for UI flows | Frontend hacking around gaps |

**Aggregation:** Report all 6 scores individually. Compute an unweighted average as the headline score. Flag any dimension scoring below 4 as a critical gap requiring priority remediation.

**Workflow tag glossary:**
| Tag | Maps to persona workflow |
|-----|------------------------|
| Triage | Triage mode (workflow 1) |
| Forge-Author | Plan authoring (workflow 2) |
| One-Shot | Kanban one-shot (workflow 3) |
| Kanban-Oversight | Board oversight (workflow 4) |
| Cross-cutting | Affects multiple workflows |

## Severity Levels

- **CRITICAL** — Workflow impossible, user blocked, no workaround
- **HIGH** — Workflow possible but painful, requires CLI workaround or excessive steps
- **MEDIUM** — Extra friction, suboptimal but functional
- **LOW** — Polish, minor UX improvement

## Finding Format (all research agents)

```markdown
### Finding F-{PREFIX}-{N}: {Title}
- **Severity:** CRITICAL | HIGH | MEDIUM | LOW
- **Workflow:** Triage | Forge-Author | Kanban-Oversight | One-Shot | Cross-cutting
- **Component(s):** {React component or API route}
- **Description:** What the issue is
- **Evidence:** Code reference or logic trace
- **Impact:** What happens to the user (in persona terms)
- **Recommendation:** Concrete fix with affected files
```

Prefixes: WF (Workflow), IA (Interaction), AF (Architecture)

**Note:** Agent 1 (Scenario Writer) produces scenarios, not findings. It does not use the finding format. Its output is consumed by Agent 5 as scenario definitions to evaluate findings against.

## Execution Strategy

1. All 4 research agents are dispatched as parallel subagents via the Agent tool
2. Each agent reads the codebase independently (no shared state)
3. Each writes its output to `docs/reviews/pmo-ux/{filename}.md`
4. After all 4 complete, the synthesis agent reads all outputs and produces AUDIT.md, ISSUES.md, and REMEDIATION-PLAN.md
5. The remediation plan is a human-readable phased document. Each phase includes a natural-language task description suitable for passing to `baton plan "..."`. The plan is not executable shell commands — it is structured input for an operator to review and feed into the engine.

## Output Location

All artifacts written to `docs/reviews/pmo-ux/`:
```
docs/reviews/pmo-ux/
  scenarios.md              ← Agent 1 output
  workflow-audit.md         ← Agent 2 output
  interaction-analysis.md   ← Agent 3 output
  architecture-fitness.md   ← Agent 4 output
  AUDIT.md                  ← Synthesis: scored dashboard
  ISSUES.md                 ← Synthesis: actionable tickets
  REMEDIATION-PLAN.md       ← Synthesis: baton-ready plan
```
