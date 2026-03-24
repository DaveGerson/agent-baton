# Smart Forge — Plan Generation & Interview Refinement

**Date:** 2026-03-24
**Status:** Design (rev 2)
**Story:** In "The Forge" I would like Generate Plan to actually generate a plan for the user. Additionally I would like it to have the ability to ask structured interview questions in the forge as part of the workflow generation process.

## Context

The Forge currently has a simple form (project, task type, priority, description) that calls `IntelligentPlanner.create_plan()` directly. The generated plans are heuristic-based (pattern matching, learned data from local store) and there is no mechanism for the user to refine the plan through structured feedback.

Two reference implementations in `reference_files/UIelements/` demonstrate the target UX:
- `baton_pmo_final.jsx` — wizard-style flow with step decomposition, gate configuration, and YAML generation
- `baton_pmo_mvp.jsx` — consultative chat with Claude, structured questions, YAML output, plan approval to board

## Design Decisions

1. **Wizard-first UX** — structured phases guide the user, not a free-form chat
2. **Direct Python planner calls** — `ForgeSession` calls `IntelligentPlanner` directly (no subprocess overhead). All AI work is local — no API keys. New interview and regeneration methods are added to `ForgeSession` using the same `IntelligentPlanner` infrastructure.
3. **Post-generation refinement** — plan generates immediately from description. If the user wants changes, "Regenerate" surfaces structured interview questions. Answers feed into re-generation with enriched context.
4. **Direct plan editing** — the plan preview is a CRUD editor for the plan DAG (add/remove/reorder phases and steps, change agents). No AI needed for manual edits. Reorder uses up/down arrow buttons (not drag-and-drop) to keep implementation simple.
5. **Forge ends at approval** — approved plans become "queued" Kanban cards via the existing `scanner.py` mechanism (it detects `plan.json` in `team-context/` and creates a card). No explicit push to the board needed.

## User Flow

```
Intake → Generate → Preview ←→ Regenerate (with interview)
                       ↓
                  Approve → plan.json written → scanner picks up → Kanban Board (queued)
```

### Phase 1: Smart Intake

A form with:
- **Task description** (textarea) — paste PRD, describe bug, or free-form
- **Project selector** (dropdown) — registered PMO projects
- **Task type** (dropdown) — auto-detect, feature, bugfix, refactor, analysis, migration
- **Priority** (dropdown) — P0/P1/P2
- **ADO import** (searchable combobox, placeholder) — eventually syncs ADO work items; selecting one pre-fills description, project, priority. For now returns mock data.
- **Signal context panel** — when entering from a signal, shows pre-filled context (signal ID, severity, prior decomposition if available)

Action: "Generate Plan" button.

### Phase 2: Generating

Loading state while `IntelligentPlanner.create_plan()` runs. Shows:
- Spinner with progress text
- **Timeout**: 120 seconds. On timeout, returns an error and transitions back to intake with the form preserved. The API endpoint returns HTTP 504 with `{"detail": "Plan generation timed out"}`.

### Phase 3: Preview

Visual plan display with:
- **Plan header** — title, stats (phase count, step count, gate count)
- **Phase/step tree** — each phase expandable, showing steps with agent tags, descriptions
- **Inline CRUD editing** — add/remove phases and steps, reorder with up/down arrows, edit descriptions and agent assignments inline. Edits update a local `editedPlan` state; "Approve & Queue" sends the edited version.
- **Three actions:**
  - **Approve & Queue** — saves edited plan to project, navigates back to board
  - **Regenerate** — transitions to interview questions, then re-generates
  - **Back to Intake** — start over

On approval, the `saved` confirmation phase from the current implementation is preserved — shows success with file path, then offers "Back to Board" and "New Plan" options.

### Phase 4: Regenerate (optional loop)

When the user clicks "Regenerate":
1. System calls `POST /pmo/forge/interview` which analyzes the current plan and generates 3-5 structured questions
2. Questions are displayed as a form — mix of multiple-choice chips and free-text inputs
3. Each question has a "skip" button (UI affordance — unanswered questions are omitted from the regeneration context, planner uses defaults)
4. Current plan summary shown as context sidebar
5. "Re-generate with Answers" submits answers + original plan to `POST /pmo/forge/regenerate`
6. Returns to Phase 2 (generating) → Phase 3 (preview) with updated plan

**Navigation-away behavior**: all fetch calls use `AbortController`. If the user navigates away from ForgePanel during generation or interview, the in-flight request is aborted. Progress is lost — user starts fresh. This matches the current behavior.

## Architecture

```
┌─────────────────────────────────┐
│  ForgePanel.tsx (React)         │
│  Phases: intake | generating |  │
│    preview | regenerating | saved│
└──────────┬──────────────────────┘
           │ HTTP
┌──────────▼──────────────────────┐
│  FastAPI endpoints              │
│  POST /pmo/forge/plan           │  → ForgeSession.create_plan()
│  POST /pmo/forge/interview      │  → ForgeSession.generate_interview()
│  POST /pmo/forge/regenerate     │  → ForgeSession.regenerate_plan()
│  POST /pmo/forge/approve        │  → ForgeSession.save_plan()
│  GET  /pmo/ado/search           │  → mock ADO data (placeholder)
└──────────┬──────────────────────┘
           │ direct call
┌──────────▼──────────────────────┐
│  IntelligentPlanner (local)     │
│  No API keys needed             │
└─────────────────────────────────┘
```

### Backend Changes

**ForgeSession** (`agent_baton/core/pmo/forge.py`):
- `create_plan()` — unchanged. Already calls `IntelligentPlanner.create_plan()` directly.
- `generate_interview(plan: MachinePlan, feedback: str | None) -> list[InterviewQuestion]` — **new**. Generates 3-5 targeted questions deterministically from plan structure: examines risk level, number of phases, agent diversity, gate coverage, and step descriptions to identify ambiguities and missing context. No LLM calls — this is rule-based analysis (e.g., "plan has no test phase → ask about testing strategy", "multiple agents involved → ask about coordination preferences").
- `regenerate_plan(description: str, project_id: str, task_type: str | None, priority: int, answers: list[InterviewAnswer]) -> MachinePlan` — **new**. Builds an enriched description by appending answered questions as structured context, then calls `IntelligentPlanner.create_plan()` with the enriched input.
- `save_plan()` — unchanged in contract, but has a **prerequisite bug fix**: line 88 references `plan_path` which is never assigned. Fix: capture the return value from `ctx.write_plan(plan)` before returning.

**New data classes** (`agent_baton/models/pmo.py`):
```python
@dataclass
class InterviewQuestion:
    id: str
    question: str
    context: str              # why this question matters
    answer_type: str          # "choice" or "text"
    choices: list[str] | None # for choice type

@dataclass
class InterviewAnswer:
    question_id: str
    answer: str               # selected choice or free-text
```

**New FastAPI routes** (added to existing `agent_baton/api/routes/pmo.py` router):
- `POST /pmo/forge/interview` — accepts current plan, returns interview questions
- `POST /pmo/forge/regenerate` — accepts enriched context + answers, returns new plan
- `GET /pmo/ado/search?q=<query>` — placeholder returning mock ADO work items

**New request/response models** (added to existing `requests.py` / `responses.py`):

In `requests.py`:
```python
class InterviewRequest(BaseModel):
    plan: dict
    feedback: str | None = None

class InterviewAnswerPayload(BaseModel):
    question_id: str
    answer: str

class RegenerateRequest(BaseModel):
    project_id: str
    description: str
    task_type: str | None = None
    priority: int = 0
    original_plan: dict
    answers: list[InterviewAnswerPayload]
```

In `responses.py`:
```python
class InterviewQuestionResponse(BaseModel):
    id: str
    question: str
    context: str
    answer_type: str
    choices: list[str] | None = None

class InterviewResponse(BaseModel):
    questions: list[InterviewQuestionResponse]

class AdoWorkItemResponse(BaseModel):
    id: str
    title: str
    type: str
    program: str
    owner: str
    priority: str
    description: str

class AdoSearchResponse(BaseModel):
    items: list[AdoWorkItemResponse]
```

### Frontend Changes

**ForgePanel.tsx** — rewritten with new phase state machine:
- `intake` — rich form with ADO combobox, signal context panel
- `generating` — loading state with timeout (120s)
- `preview` — plan visualization with CRUD editing + action buttons
- `regenerating` — interview questions form + re-generation
- `saved` — success confirmation (preserved from current implementation)

All fetch calls use `AbortController` for cleanup on unmount/navigation.

**New components:**
- `PlanEditor.tsx` — CRUD editor for plan phases/steps. Renders the phase/step tree with inline edit fields. Exposes `onPlanChange(plan)` callback to parent. Reorder via up/down arrow buttons.
- `InterviewPanel.tsx` — renders structured questions (choice chips + text inputs), collects answers. Each question shows a "skip" button. Returns `InterviewAnswer[]` to parent.
- `AdoCombobox.tsx` — searchable dropdown for ADO work item import (placeholder data via `GET /pmo/ado/search`)

**Modified components:**
- `PlanPreview.tsx` — enhanced to wrap `PlanEditor` for inline editing

### Type Alignment: Backend → Frontend

The existing `MachinePlan.to_dict()` output uses different field names than the current TypeScript `PlanResponse` interface. The Forge endpoint (`POST /pmo/forge/plan`) returns `plan.to_dict()` raw, while other endpoints use `PlanResponse.from_dataclass()` which remaps fields. To avoid breaking existing endpoints, Forge gets its own TS interface.

| Python (`MachinePlan.to_dict()`) | Existing TS (`PlanResponse`) | Forge TS (`ForgePlanResponse`) | Notes |
|---|---|---|---|
| `task_id` | `plan_id` (remapped) | `task_id` (raw) | Existing `PlanResponse` kept as-is |
| `PlanStep.agent_name` | `PlanStep.agent` | `PlanStep.agent_name` | Forge uses raw names |
| `risk_level` | (missing) | `risk_level` | Only in Forge type |
| `budget_tier` | (missing) | `budget_tier` | Only in Forge type |

**Note:** `MachinePlan.to_dict()` does NOT include `all_agents` (it's a computed property). The Forge TS type omits it.

New TypeScript types (`api/types.ts`):

```typescript
// New Forge-specific type matching raw MachinePlan.to_dict() output
// Existing PlanResponse is NOT modified — it serves other endpoints
export interface ForgePlanResponse {
  task_id: string;
  task_summary: string;
  risk_level: string;
  budget_tier: string;
  phases: ForgePlanPhase[];
  project_id: string;
  program: string;
  task_type: string;
  priority: number;
}

export interface ForgePlanPhase {
  phase_id: number;
  name: string;
  description: string;
  steps: ForgePlanStep[];
}

export interface ForgePlanStep {
  step_id: string;        // string, e.g. "1.1"
  name: string;
  agent_name: string;     // raw field name from backend
  description: string;
}

// Update ForgeApproveBody to use ForgePlanResponse shape
export interface ForgeApproveBody {
  plan: ForgePlanResponse;  // was PlanResponse — must match raw to_dict() shape
  project_id: string;
}

// New types
export interface AdoWorkItem {
  id: string;
  title: string;
  type: string;           // "Feature" | "Bug" | "Story"
  program: string;
  owner: string;
  priority: string;
  description: string;
}

export interface InterviewQuestion {
  id: string;
  question: string;
  context: string;
  answer_type: "choice" | "text";
  choices?: string[];
}

export interface InterviewAnswer {
  question_id: string;
  answer: string;
}
```

## Reference Design Influence

From `baton_pmo_final.jsx`:
- Step wizard UX pattern (numbered phases with progress indicator)
- ADO Feature selection (adapted as searchable combobox)
- Step decomposition visualization (phase/step tree with mode indicators)
- Gate configuration concepts (simplified to plan preview stats)

From `baton_pmo_mvp.jsx`:
- Consultative approach (adapted as post-generation interview questions)
- Plan approval → board queue flow
- Signal-to-forge pre-fill pattern
- Session state management

## Scope Boundaries

**In scope:**
- ForgePanel rewrite with new phase flow (intake, generating, preview, regenerating, saved)
- Interview question generation and regeneration loop in `ForgeSession`
- Plan CRUD editing (add/remove/reorder phases and steps via up/down arrows)
- ADO combobox placeholder (mock data, UI wired up)
- Signal pre-fill (existing, enhanced)
- TypeScript type alignment with backend field names
- AbortController for navigation-away cleanup
- 120s timeout on plan generation

**Out of scope:**
- Live ADO API integration (placeholder only)
- Plan execution (board's domain)
- Streaming output to UI (future enhancement — use loading state for now)
- Changes to `baton plan` CLI command
- Changes to Kanban board or execution flow
- Drag-and-drop reordering (use arrow buttons)
