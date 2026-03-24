# Smart Forge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign The Forge to generate real plans via IntelligentPlanner, add a post-generation refinement loop with structured interview questions, and provide CRUD plan editing.

**Architecture:** ForgePanel gets a new phase state machine (intake, generating, preview, regenerating, saved). Two new backend endpoints (interview + regenerate) are added to the existing PMO routes. Interview questions are generated deterministically from plan structure analysis. A new `PlanEditor` component enables inline CRUD editing of the plan DAG. An `AdoCombobox` placeholder provides future ADO import capability.

**Tech Stack:** Python (FastAPI, Pydantic, dataclasses), React (TypeScript, Vite), existing design tokens system

**Spec:** `docs/superpowers/specs/2026-03-24-forge-smart-plan-generation-design.md`

---

## File Structure

**Backend (Python):**
- Modify: `agent_baton/models/pmo.py` — add `InterviewQuestion`, `InterviewAnswer` dataclasses
- Modify: `agent_baton/api/models/requests.py` — add `InterviewRequest`, `InterviewAnswerPayload`, `RegenerateRequest`
- Modify: `agent_baton/api/models/responses.py` — add `InterviewQuestionResponse`, `InterviewResponse`, `AdoWorkItemResponse`, `AdoSearchResponse`
- Modify: `agent_baton/core/pmo/forge.py` — add `generate_interview()`, `regenerate_plan()` methods
- Modify: `agent_baton/api/routes/pmo.py` — add `/forge/interview`, `/forge/regenerate`, `/ado/search` endpoints

**Frontend (TypeScript/React):**
- Modify: `pmo-ui/src/api/types.ts` — add `ForgePlanResponse`, `ForgePlanPhase`, `ForgePlanStep`, `InterviewQuestion`, `InterviewAnswer`, `AdoWorkItem` types
- Modify: `pmo-ui/src/api/client.ts` — add `forgeInterview()`, `forgeRegenerate()`, `searchAdo()` methods
- Create: `pmo-ui/src/components/InterviewPanel.tsx` — structured interview questions form
- Create: `pmo-ui/src/components/PlanEditor.tsx` — CRUD editor for plan phases/steps DAG
- Create: `pmo-ui/src/components/AdoCombobox.tsx` — searchable ADO import placeholder
- Modify: `pmo-ui/src/components/ForgePanel.tsx` — rewrite with new phase state machine
- Modify: `pmo-ui/src/components/PlanPreview.tsx` — integrate PlanEditor for inline editing

**Tests:**
- Create: `tests/test_forge_interview.py` — interview generation + regeneration
- Create: `tests/test_pmo_routes_forge.py` — new API endpoint tests

---

### Task 1: Add Interview/Answer dataclasses to models

**Files:**
- Modify: `agent_baton/models/pmo.py:222` (after PmoConfig)
- Modify: `agent_baton/models/__init__.py` (if needed for re-export)

- [ ] **Step 1: Write the test**

Create `tests/test_forge_interview.py`:
```python
"""Tests for interview question generation and plan regeneration."""
from agent_baton.models.pmo import InterviewQuestion, InterviewAnswer


def test_interview_question_to_dict():
    q = InterviewQuestion(
        id="q1",
        question="What testing strategy?",
        context="Plan has no test phase",
        answer_type="choice",
        choices=["unit", "integration", "both"],
    )
    d = q.to_dict()
    assert d["id"] == "q1"
    assert d["answer_type"] == "choice"
    assert d["choices"] == ["unit", "integration", "both"]


def test_interview_question_from_dict():
    d = {"id": "q2", "question": "Timeout?", "context": "Long task", "answer_type": "text"}
    q = InterviewQuestion.from_dict(d)
    assert q.id == "q2"
    assert q.choices is None


def test_interview_answer_to_dict():
    a = InterviewAnswer(question_id="q1", answer="both")
    d = a.to_dict()
    assert d == {"question_id": "q1", "answer": "both"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_forge_interview.py -v`
Expected: FAIL with `ImportError` — classes don't exist yet

- [ ] **Step 3: Implement the dataclasses**

Add to end of `agent_baton/models/pmo.py`:
```python
# ---------------------------------------------------------------------------
# Interview (Forge refinement)
# ---------------------------------------------------------------------------

@dataclass
class InterviewQuestion:
    """A structured question generated during Forge plan refinement."""
    id: str
    question: str
    context: str                            # why this question matters
    answer_type: str                        # "choice" or "text"
    choices: list[str] | None = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "question": self.question,
            "context": self.context,
            "answer_type": self.answer_type,
        }
        if self.choices is not None:
            d["choices"] = self.choices
        return d

    @classmethod
    def from_dict(cls, data: dict) -> InterviewQuestion:
        return cls(
            id=data["id"],
            question=data["question"],
            context=data.get("context", ""),
            answer_type=data.get("answer_type", "text"),
            choices=data.get("choices"),
        )


@dataclass
class InterviewAnswer:
    """User's answer to an interview question."""
    question_id: str
    answer: str

    def to_dict(self) -> dict:
        return {"question_id": self.question_id, "answer": self.answer}

    @classmethod
    def from_dict(cls, data: dict) -> InterviewAnswer:
        return cls(question_id=data["question_id"], answer=data["answer"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_forge_interview.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add agent_baton/models/pmo.py tests/test_forge_interview.py
git commit -m "feat(forge): add InterviewQuestion and InterviewAnswer dataclasses"
```

---

### Task 2: Add Pydantic request/response models for interview and ADO

**Files:**
- Modify: `agent_baton/api/models/requests.py:248` (after CreateSignalRequest)
- Modify: `agent_baton/api/models/responses.py:623` (after PmoBoardResponse)

- [ ] **Step 1: Write the test**

Add to `tests/test_forge_interview.py`:
```python
from agent_baton.api.models.requests import (
    InterviewAnswerPayload,
    InterviewRequest,
    RegenerateRequest,
)
from agent_baton.api.models.responses import (
    InterviewQuestionResponse,
    InterviewResponse,
    AdoWorkItemResponse,
    AdoSearchResponse,
)


def test_interview_request_validates():
    req = InterviewRequest(plan={"task_id": "t1"}, feedback="needs more tests")
    assert req.feedback == "needs more tests"


def test_regenerate_request_validates():
    req = RegenerateRequest(
        project_id="proj1",
        description="build a thing",
        original_plan={"task_id": "t1"},
        answers=[InterviewAnswerPayload(question_id="q1", answer="both")],
    )
    assert len(req.answers) == 1
    assert req.answers[0].question_id == "q1"


def test_interview_response_validates():
    resp = InterviewResponse(questions=[
        InterviewQuestionResponse(
            id="q1", question="Testing?", context="no test phase",
            answer_type="choice", choices=["unit", "e2e"],
        )
    ])
    assert len(resp.questions) == 1


def test_ado_search_response_validates():
    resp = AdoSearchResponse(items=[
        AdoWorkItemResponse(
            id="F-100", title="Feature", type="Feature",
            program="NDS", owner="Dave", priority="P0",
            description="Build it",
        )
    ])
    assert resp.items[0].id == "F-100"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_forge_interview.py::test_interview_request_validates -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Add request models**

Add to end of `agent_baton/api/models/requests.py`:
```python
# ---------------------------------------------------------------------------
# Forge interview / regeneration requests
# ---------------------------------------------------------------------------


class InterviewRequest(BaseModel):
    """Request body for POST /pmo/forge/interview."""

    plan: dict = Field(
        ...,
        description="Current plan dict (MachinePlan.to_dict() shape).",
    )
    feedback: Optional[str] = Field(
        default=None,
        description="Optional user feedback on what to change.",
    )


class InterviewAnswerPayload(BaseModel):
    """A single answered interview question."""

    question_id: str = Field(..., description="ID of the question being answered.")
    answer: str = Field(..., description="User's answer (selected choice or free text).")


class RegenerateRequest(BaseModel):
    """Request body for POST /pmo/forge/regenerate."""

    project_id: str = Field(..., min_length=1, description="Target project ID.")
    description: str = Field(..., min_length=1, description="Original task description.")
    task_type: Optional[str] = Field(default=None, description="Task type hint.")
    priority: int = Field(default=0, ge=0, le=2, description="Priority: 0-2.")
    original_plan: dict = Field(..., description="Current plan to refine.")
    answers: list[InterviewAnswerPayload] = Field(
        ...,
        description="Answered interview questions.",
    )
```

- [ ] **Step 4: Add response models**

Add to end of `agent_baton/api/models/responses.py`:
```python
# ---------------------------------------------------------------------------
# Forge interview / ADO responses
# ---------------------------------------------------------------------------


class InterviewQuestionResponse(BaseModel):
    """A single structured interview question."""

    id: str = Field(..., description="Question identifier.")
    question: str = Field(..., description="The question text.")
    context: str = Field(default="", description="Why this question matters.")
    answer_type: str = Field(..., description="'choice' or 'text'.")
    choices: Optional[list[str]] = Field(default=None, description="Options for choice type.")


class InterviewResponse(BaseModel):
    """Response from POST /pmo/forge/interview."""

    questions: list[InterviewQuestionResponse] = Field(
        ...,
        description="3-5 structured interview questions.",
    )


class AdoWorkItemResponse(BaseModel):
    """An Azure DevOps work item (placeholder)."""

    id: str = Field(..., description="Work item ID (e.g. 'F-4203').")
    title: str = Field(..., description="Work item title.")
    type: str = Field(..., description="Feature, Bug, or Story.")
    program: str = Field(..., description="Program code.")
    owner: str = Field(..., description="Assigned owner.")
    priority: str = Field(..., description="Priority level.")
    description: str = Field(default="", description="Work item description / PRD.")


class AdoSearchResponse(BaseModel):
    """Response from GET /pmo/ado/search."""

    items: list[AdoWorkItemResponse] = Field(
        default_factory=list,
        description="Matching ADO work items.",
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_forge_interview.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add agent_baton/api/models/requests.py agent_baton/api/models/responses.py tests/test_forge_interview.py
git commit -m "feat(forge): add Pydantic models for interview, regenerate, and ADO search"
```

---

### Task 3: Implement `generate_interview()` and `regenerate_plan()` in ForgeSession

**Files:**
- Modify: `agent_baton/core/pmo/forge.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_forge_interview.py`:
```python
from unittest.mock import MagicMock
from agent_baton.core.pmo.forge import ForgeSession
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.pmo import InterviewAnswer


def _make_plan(*, phases=1, steps_per_phase=2, has_gate=False):
    """Build a minimal MachinePlan for testing."""
    plan_phases = []
    for pi in range(phases):
        steps = [
            PlanStep(
                step_id=f"{pi+1}.{si+1}",
                agent_name="backend-engineer",
                task_description=f"Step {pi+1}.{si+1}",
            )
            for si in range(steps_per_phase)
        ]
        plan_phases.append(PlanPhase(phase_id=pi, name=f"Phase {pi+1}", steps=steps))
    return MachinePlan(
        task_id="test-001",
        task_summary="Test plan",
        phases=plan_phases,
    )


def test_generate_interview_returns_questions():
    planner = MagicMock()
    store = MagicMock()
    forge = ForgeSession(planner=planner, store=store)

    plan = _make_plan(phases=2, steps_per_phase=3)
    questions = forge.generate_interview(plan)

    assert isinstance(questions, list)
    assert len(questions) >= 1
    for q in questions:
        assert q.id
        assert q.question
        assert q.answer_type in ("choice", "text")


def test_generate_interview_asks_about_missing_tests():
    planner = MagicMock()
    store = MagicMock()
    forge = ForgeSession(planner=planner, store=store)

    # Plan with no test-engineer steps
    plan = _make_plan(phases=1, steps_per_phase=2)
    questions = forge.generate_interview(plan)

    question_texts = [q.question.lower() for q in questions]
    assert any("test" in t for t in question_texts)


def test_regenerate_plan_calls_planner_with_enriched_context():
    planner = MagicMock()
    planner.create_plan.return_value = _make_plan()
    store = MagicMock()
    store.get_project.return_value = MagicMock(path="/tmp/proj")

    forge = ForgeSession(planner=planner, store=store)
    answers = [InterviewAnswer(question_id="q1", answer="unit tests")]

    result = forge.regenerate_plan(
        description="build a widget",
        project_id="proj1",
        answers=answers,
    )

    planner.create_plan.assert_called_once()
    call_kwargs = planner.create_plan.call_args
    # The enriched description should include the answer
    assert "unit tests" in call_kwargs.kwargs.get("task_summary", "") or \
           "unit tests" in (call_kwargs.args[0] if call_kwargs.args else "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_forge_interview.py::test_generate_interview_returns_questions -v`
Expected: FAIL with `AttributeError` — methods don't exist

- [ ] **Step 3: Implement the methods**

Add to `agent_baton/core/pmo/forge.py` (import `InterviewQuestion`, `InterviewAnswer` at top, add methods to `ForgeSession`):

At the top, **replace** the existing `from agent_baton.models.pmo import PmoProject` line (line 14) with:
```python
from agent_baton.models.pmo import InterviewQuestion, InterviewAnswer, PmoProject
```

Add these methods after `save_plan()` and before `signal_to_plan()`:

```python
    def generate_interview(
        self,
        plan: MachinePlan,
        feedback: str | None = None,
    ) -> list[InterviewQuestion]:
        """Generate structured interview questions from plan analysis.

        Examines the plan's structure to identify ambiguities and
        missing context. Returns 3-5 targeted questions. This is
        deterministic rule-based analysis, not an LLM call.
        """
        questions: list[InterviewQuestion] = []
        all_agents = {
            s.agent_name for p in plan.phases for s in p.steps
        }
        all_step_descs = [
            s.task_description.lower() for p in plan.phases for s in p.steps
        ]
        has_test_step = any("test" in d for d in all_step_descs)
        has_gate = any(p.gate is not None for p in plan.phases)
        phase_count = len(plan.phases)

        # Q: Testing strategy
        if not has_test_step:
            questions.append(InterviewQuestion(
                id="q-testing",
                question="No testing step was included. What testing strategy should be used?",
                context="Plans without explicit test steps risk shipping untested code.",
                answer_type="choice",
                choices=["Add unit tests", "Add integration tests", "Add both", "Skip testing"],
            ))

        # Q: Risk acknowledgement for HIGH/CRITICAL
        if plan.risk_level in ("HIGH", "CRITICAL"):
            questions.append(InterviewQuestion(
                id="q-risk",
                question=f"This plan is classified as {plan.risk_level} risk. Should additional review gates be added?",
                context="High-risk plans benefit from human checkpoints between phases.",
                answer_type="choice",
                choices=["Add review gate after each phase", "Add review gate before final phase only", "No additional gates"],
            ))

        # Q: Multi-agent coordination
        if len(all_agents) > 2:
            agents_str = ", ".join(sorted(all_agents))
            questions.append(InterviewQuestion(
                id="q-coordination",
                question=f"This plan involves {len(all_agents)} agents ({agents_str}). How should handoffs work?",
                context="Multi-agent plans need clear handoff points to avoid conflicts.",
                answer_type="choice",
                choices=["Sequential phases (strict order)", "Parallel where possible", "Let the planner decide"],
            ))

        # Q: No gates at all
        if not has_gate and phase_count > 1:
            questions.append(InterviewQuestion(
                id="q-gates",
                question="No QA gates are defined. Should validation be added between phases?",
                context="Gates catch issues early before downstream phases build on broken foundations.",
                answer_type="choice",
                choices=["Add test gate after each phase", "Add gate before final phase", "No gates needed"],
            ))

        # Q: Scope / priority clarification (always useful)
        if feedback:
            questions.append(InterviewQuestion(
                id="q-feedback",
                question="You mentioned: \"" + feedback[:200] + "\". Can you elaborate on what specifically should change?",
                context="Your feedback will be used to guide the re-generation.",
                answer_type="text",
            ))
        elif phase_count >= 3:
            questions.append(InterviewQuestion(
                id="q-scope",
                question=f"This plan has {phase_count} phases. Is the scope correct, or should any phases be removed or consolidated?",
                context="Larger plans take longer to execute and have more failure points.",
                answer_type="text",
            ))

        # Always ask about priorities if not already at max
        if len(questions) < 3:
            questions.append(InterviewQuestion(
                id="q-priority",
                question="Are there specific steps that should be prioritized or reordered?",
                context="Reordering can front-load the most valuable work.",
                answer_type="text",
            ))

        return questions[:5]

    def regenerate_plan(
        self,
        description: str,
        project_id: str,
        answers: list[InterviewAnswer],
        *,
        task_type: str | None = None,
        priority: int = 0,
    ) -> MachinePlan:
        """Re-generate a plan incorporating interview answers.

        Builds an enriched description by appending answered questions
        as structured context, then delegates to IntelligentPlanner.
        """
        # Build enriched description
        enriched_parts = [description, "\n\n--- Refinement Context ---"]
        for ans in answers:
            enriched_parts.append(f"- {ans.question_id}: {ans.answer}")
        enriched = "\n".join(enriched_parts)

        project = self._store.get_project(project_id)
        project_root = Path(project.path) if project else None

        plan: MachinePlan = self._planner.create_plan(
            task_summary=enriched,
            task_type=task_type,
            project_root=project_root,
        )
        return plan
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_forge_interview.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add agent_baton/core/pmo/forge.py tests/test_forge_interview.py
git commit -m "feat(forge): add generate_interview and regenerate_plan to ForgeSession"
```

---

### Task 4: Add FastAPI endpoints for interview, regenerate, and ADO search

**Files:**
- Modify: `agent_baton/api/routes/pmo.py`
- Create: `tests/test_pmo_routes_forge.py`

- [ ] **Step 1: Write the test**

Create `tests/test_pmo_routes_forge.py`:
```python
"""Tests for new Forge API endpoints."""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from agent_baton.api.server import create_app
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.pmo import InterviewQuestion


def _make_plan_dict():
    plan = MachinePlan(
        task_id="test-001",
        task_summary="Test plan",
        phases=[
            PlanPhase(
                phase_id=0,
                name="Phase 1",
                steps=[
                    PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="Do work")
                ],
            )
        ],
    )
    return plan.to_dict()


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def test_forge_interview_returns_questions(client):
    with patch("agent_baton.api.routes.pmo.get_forge_session") as mock_dep:
        mock_forge = MagicMock()
        mock_forge.generate_interview.return_value = [
            InterviewQuestion(id="q1", question="Testing?", context="No tests", answer_type="choice", choices=["yes", "no"]),
        ]
        mock_dep.return_value = mock_forge

        resp = client.post("/api/v1/pmo/forge/interview", json={
            "plan": _make_plan_dict(),
        })

    assert resp.status_code == 200
    data = resp.json()
    assert "questions" in data
    assert len(data["questions"]) >= 1


def test_ado_search_returns_mock_items(client):
    resp = client.get("/api/v1/pmo/ado/search?q=crew")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert isinstance(data["items"], list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pmo_routes_forge.py -v`
Expected: FAIL — endpoints don't exist (404)

- [ ] **Step 3: Add the endpoints**

Add to `agent_baton/api/routes/pmo.py`, after the existing forge endpoints and before the Signals section. Update the imports at the top:

```python
from agent_baton.api.models.requests import (
    ApproveForgeRequest,
    CreateForgeRequest,
    CreateSignalRequest,
    InterviewRequest,
    RegenerateRequest,
    RegisterProjectRequest,
)
from agent_baton.api.models.responses import (
    AdoSearchResponse,
    AdoWorkItemResponse,
    InterviewQuestionResponse,
    InterviewResponse,
    PmoBoardResponse,
    PmoCardResponse,
    PmoProjectResponse,
    PmoSignalResponse,
    ProgramHealthResponse,
)
```

Add these route handlers after `forge_approve` and before the Signals section:

```python
@router.post("/pmo/forge/interview", response_model=InterviewResponse)
async def forge_interview(
    req: InterviewRequest,
    forge: ForgeSession = Depends(get_forge_session),
) -> InterviewResponse:
    """Generate structured interview questions for plan refinement.

    Analyzes the current plan to identify ambiguities and returns
    3-5 targeted questions the user can answer to improve the plan.
    """
    from agent_baton.models.execution import MachinePlan

    try:
        plan = MachinePlan.from_dict(req.plan)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid plan: {exc}") from exc

    questions = forge.generate_interview(plan, feedback=req.feedback)
    return InterviewResponse(
        questions=[
            InterviewQuestionResponse(
                id=q.id,
                question=q.question,
                context=q.context,
                answer_type=q.answer_type,
                choices=q.choices,
            )
            for q in questions
        ]
    )


@router.post("/pmo/forge/regenerate", response_model=dict, status_code=201)
async def forge_regenerate(
    req: RegenerateRequest,
    forge: ForgeSession = Depends(get_forge_session),
    store: PmoStore = Depends(get_pmo_store),
) -> dict:
    """Re-generate a plan incorporating interview answers.

    Takes the original description plus answered questions and produces
    a new plan with enriched context.
    """
    project = store.get_project(req.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{req.project_id}' not found.")

    from agent_baton.models.pmo import InterviewAnswer

    answers = [
        InterviewAnswer(question_id=a.question_id, answer=a.answer)
        for a in req.answers
    ]

    try:
        plan = forge.regenerate_plan(
            description=req.description,
            project_id=req.project_id,
            answers=answers,
            task_type=req.task_type,
            priority=req.priority,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Regeneration failed: {exc}") from exc

    return plan.to_dict()


@router.get("/pmo/ado/search", response_model=AdoSearchResponse)
async def ado_search(q: str = "") -> AdoSearchResponse:
    """Search Azure DevOps work items (placeholder with mock data).

    Returns mock ADO work items matching the query string.
    This is a placeholder for future ADO API integration.
    """
    mock_items = [
        AdoWorkItemResponse(id="F-4202", title="Phase 3 Flight Ops Optimization", type="Feature", program="NDS", owner="Kyle", priority="P0", description="Optimize flight operations through constraint-based scheduling."),
        AdoWorkItemResponse(id="F-4203", title="FTE Migration — NDS Components", type="Feature", program="NDS", owner="Dave C", priority="P1", description="Migrate NDS analytical components from contractor codebase."),
        AdoWorkItemResponse(id="F-4212", title="Root Cause Systems — Leadership Dashboards", type="Feature", program="ATL", owner="Mandy", priority="P1", description="Root cause analysis tooling for KPI drill-down."),
        AdoWorkItemResponse(id="F-4230", title="Revenue Mgmt — Cargo Capacity", type="Feature", program="COM", owner="Pooja", priority="P0", description="Revenue management for cargo capacity optimization."),
        AdoWorkItemResponse(id="B-901", title="R2 blocks missing on Off day", type="Bug", program="NDS", owner="Unassigned", priority="P0", description="Crew scheduling R2 blocks not appearing for off-day assignments."),
    ]
    query_lower = q.lower()
    if query_lower:
        filtered = [item for item in mock_items if query_lower in item.title.lower() or query_lower in item.id.lower() or query_lower in item.program.lower()]
    else:
        filtered = mock_items
    return AdoSearchResponse(items=filtered)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pmo_routes_forge.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add agent_baton/api/routes/pmo.py tests/test_pmo_routes_forge.py
git commit -m "feat(forge): add interview, regenerate, and ADO search API endpoints"
```

---

### Task 5: Add frontend TypeScript types and API client methods

**Files:**
- Modify: `pmo-ui/src/api/types.ts`
- Modify: `pmo-ui/src/api/client.ts`

- [ ] **Step 1: Add new types to `types.ts`**

Add at the end of `pmo-ui/src/api/types.ts`:
```typescript
// ---------------------------------------------------------------------------
// Forge-specific plan types (match raw MachinePlan.to_dict() output)
// ---------------------------------------------------------------------------

export interface ForgePlanStep {
  step_id: string;
  agent_name: string;
  task_description: string;
  model: string;
  depends_on: string[];
  deliverables: string[];
  allowed_paths: string[];
  blocked_paths: string[];
  context_files: string[];
}

export interface ForgePlanGate {
  gate_type: string;
  command: string;
  description: string;
  fail_on: string[];
}

export interface ForgePlanPhase {
  phase_id: number;
  name: string;
  steps: ForgePlanStep[];
  gate?: ForgePlanGate;
}

export interface ForgePlanResponse {
  task_id: string;
  task_summary: string;
  risk_level: string;
  budget_tier: string;
  execution_mode: string;
  git_strategy: string;
  phases: ForgePlanPhase[];
  shared_context: string;
  pattern_source: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// Interview types
// ---------------------------------------------------------------------------

export interface InterviewQuestion {
  id: string;
  question: string;
  context: string;
  answer_type: 'choice' | 'text';
  choices?: string[];
}

export interface InterviewAnswer {
  question_id: string;
  answer: string;
}

export interface InterviewResponse {
  questions: InterviewQuestion[];
}

export interface RegenerateBody {
  project_id: string;
  description: string;
  task_type?: string;
  priority?: number;
  original_plan: ForgePlanResponse;
  answers: InterviewAnswer[];
}

// ---------------------------------------------------------------------------
// ADO placeholder
// ---------------------------------------------------------------------------

export interface AdoWorkItem {
  id: string;
  title: string;
  type: string;
  program: string;
  owner: string;
  priority: string;
  description: string;
}

export interface AdoSearchResponse {
  items: AdoWorkItem[];
}
```

- [ ] **Step 2: Add API client methods**

Add to `pmo-ui/src/api/client.ts` inside the `api` object, after `signalToForge`:

```typescript
  // Forge interview & regeneration
  forgeInterview(body: { plan: ForgePlanResponse; feedback?: string }): Promise<InterviewResponse> {
    return request('/forge/interview', { method: 'POST', body: JSON.stringify(body) });
  },
  forgeRegenerate(body: RegenerateBody): Promise<ForgePlanResponse> {
    return request('/forge/regenerate', { method: 'POST', body: JSON.stringify(body) });
  },

  // ADO search (placeholder)
  searchAdo(q: string): Promise<AdoSearchResponse> {
    return request(`/ado/search?q=${encodeURIComponent(q)}`);
  },
```

Update the imports at the top of `client.ts` to include the new types:
```typescript
import type {
  BoardResponse,
  PmoCard,
  PmoProject,
  ProgramHealth,
  PmoSignal,
  PlanResponse,
  ForgeApproveBody,
  ForgeApproveResponse,
  ForgePlanBody,
  ForgePlanResponse,
  InterviewResponse,
  RegenerateBody,
  AdoSearchResponse,
} from './types';
```

Update `ForgeApproveBody` to use `ForgePlanResponse` (spec requirement — raw `to_dict()` shape):
```typescript
export interface ForgeApproveBody {
  plan: ForgePlanResponse;
  project_id: string;
}
```

Also update `forgePlan()` return type to `ForgePlanResponse` since the endpoint returns raw `plan.to_dict()`:
```typescript
  forgePlan(body: ForgePlanBody): Promise<ForgePlanResponse> {
    return request('/forge/plan', { method: 'POST', body: JSON.stringify(body) });
  },
```

Update the re-export line at the bottom to include new types:
```typescript
export type { PmoCard, PmoProject, ProgramHealth, PmoSignal, BoardResponse, PlanResponse, ForgePlanBody, ForgePlanResponse, ForgeApproveBody, ForgeApproveResponse, InterviewResponse, RegenerateBody, AdoSearchResponse };
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd pmo-ui && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 4: Commit**

```bash
git add pmo-ui/src/api/types.ts pmo-ui/src/api/client.ts
git commit -m "feat(forge): add frontend types and API client for interview, regenerate, ADO"
```

---

### Task 6: Create InterviewPanel component

**Files:**
- Create: `pmo-ui/src/components/InterviewPanel.tsx`

- [ ] **Step 1: Create the component**

Create `pmo-ui/src/components/InterviewPanel.tsx`:
```tsx
import { useState } from 'react';
import { T } from '../styles/tokens';
import type { InterviewQuestion, InterviewAnswer } from '../api/types';

interface InterviewPanelProps {
  questions: InterviewQuestion[];
  onSubmit: (answers: InterviewAnswer[]) => void;
  onCancel: () => void;
  loading?: boolean;
}

export function InterviewPanel({ questions, onSubmit, onCancel, loading }: InterviewPanelProps) {
  const [answers, setAnswers] = useState<Record<string, string>>({});

  function setAnswer(questionId: string, value: string) {
    setAnswers(prev => ({ ...prev, [questionId]: value }));
  }

  function handleSubmit() {
    const result: InterviewAnswer[] = Object.entries(answers)
      .filter(([, v]) => v.trim())
      .map(([questionId, answer]) => ({ question_id: questionId, answer }));
    onSubmit(result);
  }

  const answeredCount = Object.values(answers).filter(v => v.trim()).length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{
        fontSize: 9,
        fontWeight: 700,
        color: T.yellow,
        textTransform: 'uppercase',
        letterSpacing: 0.5,
      }}>
        Refinement Questions
      </div>
      <div style={{ fontSize: 8, color: T.text3 }}>
        Answer what you can — unanswered questions use sensible defaults.
      </div>

      {questions.map((q, i) => (
        <div key={q.id} style={{
          background: T.bg1,
          borderRadius: 4,
          border: `1px solid ${T.border}`,
          padding: 10,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 4, marginBottom: 4 }}>
            <div style={{
              width: 16,
              height: 16,
              borderRadius: '50%',
              background: T.yellow + '20',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 8,
              fontWeight: 700,
              color: T.yellow,
              flexShrink: 0,
            }}>
              {i + 1}
            </div>
            <span style={{ fontSize: 9, fontWeight: 600, color: T.text0 }}>{q.question}</span>
          </div>
          {q.context && (
            <div style={{ fontSize: 8, color: T.text3, marginBottom: 6, marginLeft: 20 }}>
              {q.context}
            </div>
          )}

          {q.answer_type === 'choice' && q.choices ? (
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginLeft: 20 }}>
              {q.choices.map(choice => (
                <button
                  key={choice}
                  onClick={() => setAnswer(q.id, choice)}
                  style={{
                    padding: '3px 8px',
                    borderRadius: 3,
                    border: `1px solid ${answers[q.id] === choice ? T.accent + '66' : T.border}`,
                    background: answers[q.id] === choice ? T.accent + '15' : 'transparent',
                    color: answers[q.id] === choice ? T.accent : T.text2,
                    fontSize: 8,
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                >
                  {choice}
                </button>
              ))}
              <button
                onClick={() => setAnswer(q.id, '')}
                style={{
                  padding: '3px 8px',
                  borderRadius: 3,
                  border: `1px solid ${T.border}`,
                  background: 'transparent',
                  color: T.text3,
                  fontSize: 8,
                  cursor: 'pointer',
                }}
              >
                skip
              </button>
            </div>
          ) : (
            <div style={{ marginLeft: 20 }}>
              <input
                type="text"
                value={answers[q.id] ?? ''}
                onChange={e => setAnswer(q.id, e.target.value)}
                placeholder="Type your answer..."
                style={{
                  width: '100%',
                  padding: '4px 8px',
                  borderRadius: 3,
                  border: `1px solid ${T.border}`,
                  background: T.bg2,
                  color: T.text0,
                  fontSize: 9,
                  outline: 'none',
                }}
              />
            </div>
          )}
        </div>
      ))}

      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <button
          onClick={handleSubmit}
          disabled={loading || answeredCount === 0}
          style={{
            padding: '6px 16px',
            borderRadius: 4,
            border: 'none',
            background: loading || answeredCount === 0
              ? T.bg3
              : `linear-gradient(135deg, ${T.yellow}, #d97706)`,
            color: '#fff',
            fontSize: 9,
            fontWeight: 700,
            cursor: loading || answeredCount === 0 ? 'not-allowed' : 'pointer',
            opacity: loading || answeredCount === 0 ? 0.6 : 1,
          }}
        >
          {loading ? 'Re-generating...' : `Re-generate with ${answeredCount} answer${answeredCount !== 1 ? 's' : ''}`}
        </button>
        <button
          onClick={onCancel}
          style={{
            padding: '5px 10px',
            borderRadius: 4,
            border: `1px solid ${T.border}`,
            background: 'transparent',
            color: T.text2,
            fontSize: 9,
            cursor: 'pointer',
          }}
        >
          Back to Plan
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd pmo-ui && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add pmo-ui/src/components/InterviewPanel.tsx
git commit -m "feat(forge): add InterviewPanel component for structured refinement questions"
```

---

### Task 7: Create PlanEditor component

**Files:**
- Create: `pmo-ui/src/components/PlanEditor.tsx`

- [ ] **Step 1: Create the component**

Create `pmo-ui/src/components/PlanEditor.tsx`:
```tsx
import { useState } from 'react';
import { T } from '../styles/tokens';
import type { ForgePlanResponse, ForgePlanPhase, ForgePlanStep } from '../api/types';

interface PlanEditorProps {
  plan: ForgePlanResponse;
  onPlanChange: (plan: ForgePlanResponse) => void;
}

export function PlanEditor({ plan, onPlanChange }: PlanEditorProps) {
  const [expandedPhase, setExpandedPhase] = useState<number | null>(0);
  const [editingStep, setEditingStep] = useState<string | null>(null);

  const totalSteps = plan.phases.reduce((acc, ph) => acc + ph.steps.length, 0);
  const gateCount = plan.phases.filter(p => p.gate).length;

  function updatePhase(phaseIdx: number, updater: (phase: ForgePlanPhase) => ForgePlanPhase) {
    const newPhases = plan.phases.map((p, i) => i === phaseIdx ? updater({ ...p }) : p);
    onPlanChange({ ...plan, phases: newPhases });
  }

  function updateStep(phaseIdx: number, stepIdx: number, updater: (step: ForgePlanStep) => ForgePlanStep) {
    updatePhase(phaseIdx, phase => ({
      ...phase,
      steps: phase.steps.map((s, i) => i === stepIdx ? updater({ ...s }) : s),
    }));
  }

  function removeStep(phaseIdx: number, stepIdx: number) {
    updatePhase(phaseIdx, phase => ({
      ...phase,
      steps: phase.steps.filter((_, i) => i !== stepIdx),
    }));
  }

  function moveStep(phaseIdx: number, stepIdx: number, direction: -1 | 1) {
    updatePhase(phaseIdx, phase => {
      const steps = [...phase.steps];
      const newIdx = stepIdx + direction;
      if (newIdx < 0 || newIdx >= steps.length) return phase;
      [steps[stepIdx], steps[newIdx]] = [steps[newIdx], steps[stepIdx]];
      return { ...phase, steps };
    });
  }

  function addStep(phaseIdx: number) {
    updatePhase(phaseIdx, phase => ({
      ...phase,
      steps: [...phase.steps, {
        step_id: `${phase.phase_id + 1}.${phase.steps.length + 1}`,
        agent_name: 'backend-engineer',
        task_description: 'New step',
        model: 'sonnet',
        depends_on: [],
        deliverables: [],
        allowed_paths: [],
        blocked_paths: [],
        context_files: [],
      }],
    }));
  }

  function removePhase(phaseIdx: number) {
    onPlanChange({ ...plan, phases: plan.phases.filter((_, i) => i !== phaseIdx) });
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      {/* Stats bar */}
      <div style={{ display: 'flex', gap: 6 }}>
        <Stat label="Phases" value={String(plan.phases.length)} />
        <Stat label="Steps" value={String(totalSteps)} />
        <Stat label="Gates" value={String(gateCount)} color={T.yellow} />
        <Stat label="Risk" value={plan.risk_level} color={plan.risk_level === 'LOW' ? T.green : T.red} />
      </div>

      {/* Summary */}
      {plan.task_summary && (
        <div style={{
          padding: '8px 12px',
          background: T.bg2,
          borderRadius: 4,
          borderLeft: `3px solid ${T.accent}`,
        }}>
          <div style={{ fontSize: 7, color: T.text3, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 3 }}>Summary</div>
          <div style={{ fontSize: 10, color: T.text1, lineHeight: 1.55 }}>{plan.task_summary}</div>
        </div>
      )}

      {/* Phases */}
      {plan.phases.map((phase, pi) => {
        const isExpanded = expandedPhase === pi;
        return (
          <div key={phase.phase_id} style={{
            background: T.bg1,
            borderRadius: 4,
            border: `1px solid ${T.border}`,
            overflow: 'hidden',
          }}>
            {/* Phase header */}
            <div
              onClick={() => setExpandedPhase(isExpanded ? null : pi)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '6px 10px',
                background: T.bg2,
                borderBottom: isExpanded ? `1px solid ${T.border}` : 'none',
                cursor: 'pointer',
              }}
            >
              <div style={{
                width: 16, height: 16, borderRadius: 3,
                background: T.accent + '20', border: `1px solid ${T.accent}33`,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 8, fontWeight: 700, color: T.accent, flexShrink: 0,
              }}>
                {pi + 1}
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 9, fontWeight: 700, color: T.text0 }}>{phase.name}</div>
              </div>
              <span style={{ fontSize: 7, color: T.text3, background: T.bg3, padding: '1px 4px', borderRadius: 3 }}>
                {phase.steps.length} steps
              </span>
              {phase.gate && (
                <span style={{ fontSize: 7, color: T.yellow, background: T.yellow + '14', border: `1px solid ${T.yellow}22`, padding: '1px 4px', borderRadius: 3 }}>
                  gate
                </span>
              )}
              <button
                onClick={e => { e.stopPropagation(); removePhase(pi); }}
                style={{ background: 'none', border: 'none', color: T.text3, fontSize: 10, cursor: 'pointer', padding: '0 4px' }}
                title="Remove phase"
              >
                {'\u00d7'}
              </button>
            </div>

            {/* Steps (when expanded) */}
            {isExpanded && (
              <>
                {phase.steps.map((step, si) => (
                  <div key={step.step_id} style={{
                    display: 'flex', alignItems: 'flex-start', gap: 6, padding: '5px 10px',
                    borderBottom: si < phase.steps.length - 1 ? `1px solid ${T.border}` : 'none',
                  }}>
                    {/* Reorder buttons */}
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 1, flexShrink: 0 }}>
                      <button
                        onClick={() => moveStep(pi, si, -1)}
                        disabled={si === 0}
                        style={{ background: 'none', border: 'none', color: si === 0 ? T.bg3 : T.text3, fontSize: 8, cursor: si === 0 ? 'default' : 'pointer', padding: 0, lineHeight: 1 }}
                      >{'\u25b2'}</button>
                      <button
                        onClick={() => moveStep(pi, si, 1)}
                        disabled={si === phase.steps.length - 1}
                        style={{ background: 'none', border: 'none', color: si === phase.steps.length - 1 ? T.bg3 : T.text3, fontSize: 8, cursor: si === phase.steps.length - 1 ? 'default' : 'pointer', padding: 0, lineHeight: 1 }}
                      >{'\u25bc'}</button>
                    </div>

                    {/* Step content */}
                    <div style={{ flex: 1 }}>
                      {editingStep === step.step_id ? (
                        <input
                          autoFocus
                          value={step.task_description}
                          onChange={e => updateStep(pi, si, s => ({ ...s, task_description: e.target.value }))}
                          onBlur={() => setEditingStep(null)}
                          onKeyDown={e => e.key === 'Enter' && setEditingStep(null)}
                          style={{
                            width: '100%', padding: '2px 4px', borderRadius: 3,
                            border: `1px solid ${T.accent}`, background: T.bg2,
                            color: T.text0, fontSize: 9, outline: 'none',
                          }}
                        />
                      ) : (
                        <div
                          onClick={() => setEditingStep(step.step_id)}
                          style={{ fontSize: 9, color: T.text0, fontWeight: 500, cursor: 'text' }}
                          title="Click to edit"
                        >
                          {step.task_description}
                        </div>
                      )}
                    </div>

                    {/* Agent tag */}
                    <span style={{
                      fontSize: 7, color: T.cyan, background: T.cyan + '14',
                      border: `1px solid ${T.cyan}22`, padding: '1px 5px',
                      borderRadius: 3, whiteSpace: 'nowrap', flexShrink: 0,
                    }}>
                      {step.agent_name}
                    </span>

                    {/* Remove step */}
                    <button
                      onClick={() => removeStep(pi, si)}
                      style={{ background: 'none', border: 'none', color: T.text3, fontSize: 10, cursor: 'pointer', padding: '0 2px', flexShrink: 0 }}
                      title="Remove step"
                    >
                      {'\u00d7'}
                    </button>
                  </div>
                ))}

                {/* Add step button */}
                <div style={{ padding: '4px 10px' }}>
                  <button
                    onClick={() => addStep(pi)}
                    style={{
                      padding: '2px 8px', borderRadius: 3,
                      border: `1px dashed ${T.border}`, background: 'transparent',
                      color: T.text3, fontSize: 8, cursor: 'pointer',
                    }}
                  >
                    + Add step
                  </button>
                </div>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ padding: '4px 8px', background: T.bg2, borderRadius: 4 }}>
      <div style={{ fontSize: 7, color: T.text3, textTransform: 'uppercase' }}>{label}</div>
      <div style={{ fontSize: 12, fontWeight: 700, color: color ?? T.text0, fontFamily: 'monospace' }}>{value}</div>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd pmo-ui && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add pmo-ui/src/components/PlanEditor.tsx
git commit -m "feat(forge): add PlanEditor component for CRUD editing of plan DAG"
```

---

### Task 8: Create AdoCombobox placeholder component

**Files:**
- Create: `pmo-ui/src/components/AdoCombobox.tsx`

- [ ] **Step 1: Create the component**

Create `pmo-ui/src/components/AdoCombobox.tsx`:
```tsx
import { useState, useEffect, useRef } from 'react';
import { api } from '../api/client';
import { T } from '../styles/tokens';
import type { AdoWorkItem } from '../api/types';

interface AdoComboboxProps {
  onSelect: (item: AdoWorkItem) => void;
}

export function AdoCombobox({ onSelect }: AdoComboboxProps) {
  const [query, setQuery] = useState('');
  const [items, setItems] = useState<AdoWorkItem[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!query.trim()) { setItems([]); return; }
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const resp = await api.searchAdo(query);
        setItems(resp.items);
        setOpen(true);
      } catch { setItems([]); }
      setLoading(false);
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  function handleSelect(item: AdoWorkItem) {
    setQuery(item.title);
    setOpen(false);
    onSelect(item);
  }

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <input
        value={query}
        onChange={e => setQuery(e.target.value)}
        onFocus={() => items.length > 0 && setOpen(true)}
        placeholder="Search ADO work items (placeholder)..."
        style={{
          width: '100%', padding: '6px 8px', borderRadius: 4,
          border: `1px solid ${T.border}`, background: T.bg1,
          color: T.text0, fontSize: 10, outline: 'none',
        }}
      />
      {loading && (
        <div style={{ position: 'absolute', right: 8, top: 7, fontSize: 8, color: T.text3 }}>...</div>
      )}
      {open && items.length > 0 && (
        <div style={{
          position: 'absolute', top: '100%', left: 0, right: 0,
          marginTop: 2, background: T.bg1, border: `1px solid ${T.border}`,
          borderRadius: 4, maxHeight: 200, overflow: 'auto', zIndex: 10,
          boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
        }}>
          {items.map(item => (
            <div
              key={item.id}
              onClick={() => handleSelect(item)}
              style={{
                padding: '6px 8px', cursor: 'pointer',
                borderBottom: `1px solid ${T.border}`,
              }}
              onMouseEnter={e => (e.currentTarget.style.background = T.bg2)}
              onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ fontSize: 8, color: T.text3, fontFamily: 'monospace' }}>{item.id}</span>
                <span style={{ fontSize: 9, color: T.text0, fontWeight: 500 }}>{item.title}</span>
                <span style={{
                  fontSize: 7, color: T.accent, background: T.accent + '14',
                  border: `1px solid ${T.accent}22`, padding: '0 4px',
                  borderRadius: 2, marginLeft: 'auto',
                }}>{item.type}</span>
              </div>
              <div style={{ fontSize: 7, color: T.text3, marginTop: 1 }}>
                {item.program} · {item.owner} · {item.priority}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd pmo-ui && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add pmo-ui/src/components/AdoCombobox.tsx
git commit -m "feat(forge): add AdoCombobox placeholder component for ADO work item import"
```

---

### Task 9: Rewrite ForgePanel with new phase state machine

**Files:**
- Modify: `pmo-ui/src/components/ForgePanel.tsx` (full rewrite)

This is the largest task. The new ForgePanel integrates all new components and implements the full phase state machine: intake → generating → preview → regenerating → saved.

- [ ] **Step 1: Rewrite ForgePanel.tsx**

Replace the entire contents of `pmo-ui/src/components/ForgePanel.tsx` with the new implementation. Key changes from current:
- Phase type adds `'regenerating'`
- Imports `PlanEditor`, `InterviewPanel`, `AdoCombobox`
- Uses `ForgePlanResponse` instead of `PlanResponse`
- `handleGenerate()` and `handleApprove()` use `AbortController`
- New `handleRegenerate()` flow: fetch interview questions → show InterviewPanel → submit answers → re-generate
- Preview phase renders `PlanEditor` with `onPlanChange` for CRUD editing
- Regenerating phase renders `InterviewPanel`
- ADO combobox in intake form pre-fills description

```tsx
import { useState, useEffect, useRef } from 'react';
import { api } from '../api/client';
import { PlanEditor } from './PlanEditor';
import { InterviewPanel } from './InterviewPanel';
import { AdoCombobox } from './AdoCombobox';
import { T } from '../styles/tokens';
import type { PmoProject, PmoSignal, ForgePlanResponse, InterviewQuestion, InterviewAnswer } from '../api/types';

interface ForgePanelProps {
  onBack: () => void;
  initialSignal?: PmoSignal | null;
}

type Phase = 'intake' | 'generating' | 'preview' | 'regenerating' | 'saved';

const TASK_TYPES = [
  { value: '', label: 'Auto-detect' },
  { value: 'feature', label: 'New Feature' },
  { value: 'bugfix', label: 'Bug Fix' },
  { value: 'refactor', label: 'Refactor' },
  { value: 'analysis', label: 'Analysis' },
  { value: 'migration', label: 'Migration' },
];

const PRIORITIES = [
  { value: 2, label: 'P0 \u2014 Critical' },
  { value: 1, label: 'P1 \u2014 High' },
  { value: 0, label: 'P2 \u2014 Normal' },
];

export function ForgePanel({ onBack, initialSignal }: ForgePanelProps) {
  const [phase, setPhase] = useState<Phase>('intake');
  const [projects, setProjects] = useState<PmoProject[]>([]);
  const [projectsLoading, setProjectsLoading] = useState(true);

  const [description, setDescription] = useState(
    initialSignal
      ? `Signal: ${initialSignal.title}\n\nSeverity: ${initialSignal.severity}\nType: ${initialSignal.signal_type}\n\n${initialSignal.description ?? ''}`
      : ''
  );
  const [projectId, setProjectId] = useState('');
  const [taskType, setTaskType] = useState('');
  const [priority, setPriority] = useState<number>(1);

  const [plan, setPlan] = useState<ForgePlanResponse | null>(null);
  const [interviewQuestions, setInterviewQuestions] = useState<InterviewQuestion[]>([]);
  const [generateError, setGenerateError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savePath, setSavePath] = useState<string | null>(null);
  const [regenLoading, setRegenLoading] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const selectedProject = projects.find(p => p.project_id === projectId);

  useEffect(() => {
    api.getProjects()
      .then(ps => {
        setProjects(ps);
        if (ps.length > 0 && !projectId) setProjectId(ps[0].project_id);
      })
      .catch(() => {})
      .finally(() => setProjectsLoading(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    return () => { abortRef.current?.abort(); };
  }, []);

  async function handleGenerate() {
    if (!description.trim() || !projectId) return;
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setPhase('generating');
    setGenerateError(null);
    try {
      const result = await api.forgePlan({
        description: description.trim(),
        program: selectedProject?.program ?? '',
        project_id: projectId,
        task_type: taskType || undefined,
        priority,
      });
      setPlan(result);
      setPhase('preview');
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      setGenerateError(err instanceof Error ? err.message : 'Generation failed');
      setPhase('intake');
    }
  }

  async function handleStartRegenerate() {
    if (!plan) return;
    setRegenLoading(true);
    try {
      const resp = await api.forgeInterview({ plan });
      setInterviewQuestions(resp.questions);
      setPhase('regenerating');
    } catch (err) {
      setGenerateError(err instanceof Error ? err.message : 'Failed to generate questions');
    }
    setRegenLoading(false);
  }

  async function handleRegenerate(answers: InterviewAnswer[]) {
    if (!plan) return;
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setPhase('generating');
    setGenerateError(null);
    try {
      const result = await api.forgeRegenerate({
        project_id: projectId,
        description: description.trim(),
        task_type: taskType || undefined,
        priority,
        original_plan: plan,
        answers,
      });
      setPlan(result);
      setPhase('preview');
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      setGenerateError(err instanceof Error ? err.message : 'Re-generation failed');
      setPhase('preview');
    }
  }

  async function handleApprove() {
    if (!plan) return;
    setSaveError(null);
    try {
      const result = await api.forgeApprove({ plan, project_id: projectId });
      setSavePath(result.path);
      setPhase('saved');
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Save failed');
    }
  }

  const phaseLabel: Record<Phase, string> = {
    intake: 'Describe the work to generate a plan',
    generating: 'Generating plan...',
    preview: 'Review, edit, or regenerate',
    regenerating: 'Answer refinement questions',
    saved: 'Plan saved \u2014 ready to execute',
  };

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '7px 14px', borderBottom: `1px solid ${T.border}`,
        background: T.bg1, flexShrink: 0,
      }}>
        <button onClick={onBack} style={{
          padding: '3px 8px', borderRadius: 3, border: `1px solid ${T.border}`,
          background: 'transparent', color: T.text2, fontSize: 9, cursor: 'pointer',
        }}>{'\u2190'} Board</button>
        <div style={{ width: 1, height: 14, background: T.border }} />
        <span style={{ fontSize: 11, fontWeight: 700, color: T.text0 }}>The Forge</span>
        <span style={{ fontSize: 8, color: T.text3 }}>{phaseLabel[phase]}</span>
        {initialSignal && (
          <span style={{
            padding: '1px 6px', borderRadius: 3, fontSize: 7, fontWeight: 600,
            color: T.red, background: T.red + '14', border: `1px solid ${T.red}22`,
          }}>from signal: {initialSignal.signal_id}</span>
        )}
        <div style={{ flex: 1 }} />
        {phase === 'preview' && (
          <button onClick={() => setPhase('intake')} style={{
            padding: '3px 8px', borderRadius: 3, border: `1px solid ${T.border}`,
            background: 'transparent', color: T.text2, fontSize: 9, cursor: 'pointer',
          }}>{'\u2190'} Edit Intake</button>
        )}
      </div>

      {/* Body */}
      <div style={{ flex: 1, overflow: 'auto', padding: 16 }}>

        {/* INTAKE */}
        {(phase === 'intake' || phase === 'generating') && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 640 }}>
            <div style={{ fontSize: 9, fontWeight: 700, color: T.accent, textTransform: 'uppercase', letterSpacing: 0.5 }}>
              Define the Work
            </div>

            {/* ADO Import */}
            <FormField label="Import from ADO (placeholder)">
              <AdoCombobox onSelect={item => {
                setDescription(item.description || item.title);
              }} />
            </FormField>

            {/* Project selector */}
            <FormField label="Project *">
              {projectsLoading ? (
                <div style={{ fontSize: 8, color: T.text3, padding: 4 }}>Loading projects...</div>
              ) : projects.length === 0 ? (
                <div style={{ fontSize: 8, color: T.yellow, padding: 4 }}>
                  No projects registered. Use <code>baton pmo add</code> to register one.
                </div>
              ) : (
                <select value={projectId} onChange={e => setProjectId(e.target.value)} style={selectStyle}>
                  {projects.map(p => (
                    <option key={p.project_id} value={p.project_id}>{p.name} ({p.program})</option>
                  ))}
                </select>
              )}
            </FormField>

            <div style={{ display: 'flex', gap: 8 }}>
              <FormField label="Task Type" style={{ flex: 1 }}>
                <select value={taskType} onChange={e => setTaskType(e.target.value)} style={selectStyle}>
                  {TASK_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </FormField>
              <FormField label="Priority" style={{ flex: 1 }}>
                <select value={priority} onChange={e => setPriority(Number(e.target.value))} style={selectStyle}>
                  {PRIORITIES.map(p => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </FormField>
            </div>

            <FormField label="Task Description *">
              <textarea
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="Describe the work: what needs to be built, fixed, or analyzed."
                rows={9}
                style={{
                  width: '100%', padding: '8px 10px', borderRadius: 4,
                  border: `1px solid ${T.border}`, background: T.bg1,
                  color: T.text0, fontSize: 10, lineHeight: 1.55,
                  outline: 'none', resize: 'vertical', fontFamily: 'inherit',
                }}
              />
            </FormField>

            {generateError && (
              <div style={{ fontSize: 9, color: T.red, padding: '5px 8px', background: T.red + '12', borderRadius: 4 }}>
                {generateError}
              </div>
            )}

            <button
              onClick={handleGenerate}
              disabled={phase === 'generating' || !description.trim() || !projectId}
              style={{
                alignSelf: 'flex-start', padding: '7px 20px', borderRadius: 4,
                border: 'none',
                background: phase === 'generating' || !description.trim() || !projectId ? T.bg3 : `linear-gradient(135deg, ${T.accent}, #2563eb)`,
                color: '#fff', fontSize: 10, fontWeight: 700,
                cursor: phase === 'generating' || !description.trim() || !projectId ? 'not-allowed' : 'pointer',
                opacity: phase === 'generating' || !description.trim() || !projectId ? 0.6 : 1,
              }}
            >
              {phase === 'generating' ? 'Generating...' : 'Generate Plan \u2192'}
            </button>
          </div>
        )}

        {/* PREVIEW */}
        {phase === 'preview' && plan && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: T.text0 }}>Plan Ready</span>
              <div style={{ display: 'flex', gap: 6 }}>
                <button onClick={handleApprove} style={{
                  padding: '5px 16px', borderRadius: 4, border: 'none',
                  background: `linear-gradient(135deg, ${T.green}, #059669)`,
                  color: '#fff', fontSize: 9, fontWeight: 700, cursor: 'pointer',
                }}>Approve & Queue</button>
                <button onClick={handleStartRegenerate} disabled={regenLoading} style={{
                  padding: '5px 14px', borderRadius: 4,
                  border: `1px solid ${T.yellow}44`, background: 'transparent',
                  color: T.yellow, fontSize: 9, fontWeight: 600,
                  cursor: regenLoading ? 'not-allowed' : 'pointer',
                  opacity: regenLoading ? 0.6 : 1,
                }}>{regenLoading ? 'Loading...' : 'Regenerate'}</button>
              </div>
            </div>

            {saveError && (
              <div style={{ fontSize: 9, color: T.red, padding: '5px 8px', background: T.red + '12', borderRadius: 4 }}>
                {saveError}
              </div>
            )}

            <PlanEditor plan={plan} onPlanChange={setPlan} />
          </div>
        )}

        {/* REGENERATING */}
        {phase === 'regenerating' && (
          <InterviewPanel
            questions={interviewQuestions}
            onSubmit={handleRegenerate}
            onCancel={() => setPhase('preview')}
          />
        )}

        {/* SAVED */}
        {phase === 'saved' && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, paddingTop: 40 }}>
            <div style={{
              width: 48, height: 48, borderRadius: '50%',
              background: T.green + '20', border: `2px solid ${T.green}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 22, color: T.green,
            }}>{'\u2713'}</div>
            <div style={{ fontSize: 14, fontWeight: 700, color: T.green }}>Plan Saved & Queued</div>
            {savePath && (
              <div style={{ fontSize: 9, color: T.text3, fontFamily: 'monospace' }}>{savePath}</div>
            )}
            <div style={{ display: 'flex', gap: 8 }}>
              <button onClick={() => { setPhase('intake'); setDescription(''); setPlan(null); }} style={{
                padding: '5px 14px', borderRadius: 4, border: `1px solid ${T.border}`,
                background: 'transparent', color: T.text2, fontSize: 9, cursor: 'pointer',
              }}>New Plan</button>
              <button onClick={onBack} style={{
                padding: '5px 14px', borderRadius: 4, border: 'none',
                background: T.accent, color: '#fff', fontSize: 9, fontWeight: 600, cursor: 'pointer',
              }}>Back to Board</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function FormField({ label, children, style }: { label: string; children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={style}>
      <label style={{ fontSize: 8, color: T.text2, display: 'block', marginBottom: 4 }}>{label}</label>
      {children}
    </div>
  );
}

const selectStyle: React.CSSProperties = {
  width: '100%', padding: '6px 8px', borderRadius: 4,
  border: `1px solid ${T.border}`, background: T.bg1,
  color: T.text0, fontSize: 10, outline: 'none',
};
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd pmo-ui && npx tsc --noEmit`
Expected: No errors

- [ ] **Step 3: Build the UI**

Run: `cd pmo-ui && npm run build`
Expected: Build succeeds

- [ ] **Step 4: Commit**

```bash
git add pmo-ui/src/components/ForgePanel.tsx
git commit -m "feat(forge): rewrite ForgePanel with smart intake, plan editor, and regeneration loop"
```

---

### Task 10: Fix prerequisite bug in ForgeSession.save_plan()

**Files:**
- Modify: `agent_baton/core/pmo/forge.py:86-88`

- [ ] **Step 1: Write the test**

Add to `tests/test_forge_interview.py`:
```python
def test_save_plan_returns_path(tmp_path):
    """Verify save_plan returns the plan.json path without NameError."""
    planner = MagicMock()
    store = MagicMock()
    forge = ForgeSession(planner=planner, store=store)

    plan = _make_plan()
    project = MagicMock()
    project.path = str(tmp_path)

    path = forge.save_plan(plan, project)
    assert path is not None
    assert "plan.json" in str(path)
```

- [ ] **Step 2: Run test — verify it identifies the bug**

Run: `pytest tests/test_forge_interview.py::test_save_plan_returns_path -v`
Expected: Observe behavior (may pass or fail depending on ContextManager implementation)

- [ ] **Step 3: Verify and fix if needed**

Check that `forge.py` line 86-88 correctly returns a path. The current code calls `ctx.write_plan(plan)` and returns `ctx.plan_json_path`. If `plan_json_path` is a property that returns the correct path after `write_plan`, this is fine. If not, fix it:

```python
    def save_plan(self, plan: MachinePlan, project: PmoProject) -> Path:
        from agent_baton.core.orchestration.context import ContextManager
        context_root = Path(project.path) / ".claude" / "team-context"
        ctx = ContextManager(
            team_context_dir=context_root,
            task_id=plan.task_id,
        )
        ctx.write_plan(plan)
        return ctx.plan_json_path
```

- [ ] **Step 4: Run all tests**

Run: `pytest tests/test_forge_interview.py -v`
Expected: All PASS

- [ ] **Step 5: Commit (if changes were needed)**

```bash
git add agent_baton/core/pmo/forge.py tests/test_forge_interview.py
git commit -m "fix(forge): verify save_plan returns correct path"
```

---

### Task 11: Final integration verification

- [ ] **Step 1: Run all backend tests**

Run: `pytest tests/test_forge_interview.py tests/test_pmo_routes_forge.py -v`
Expected: All PASS

- [ ] **Step 2: Build frontend**

Run: `cd pmo-ui && npm run build`
Expected: Build succeeds

- [ ] **Step 3: Run full test suite (smoke check)**

Run: `pytest tests/ -x -q --timeout=30 2>&1 | tail -20`
Expected: No regressions in existing tests

- [ ] **Step 4: Commit any remaining fixes**

```bash
git add -A
git commit -m "chore: final integration fixes for Smart Forge"
```
