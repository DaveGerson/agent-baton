"""PMO (Portfolio Management Office) endpoints for the Agent Baton API.

GET  /pmo/board                       — Full Kanban board (cards + health)
GET  /pmo/board/{program}             — Filter board by program
GET  /pmo/projects                    — List registered projects
POST /pmo/projects                    — Register a project
DELETE /pmo/projects/{project_id}     — Unregister a project
GET  /pmo/health                      — Program health metrics
POST /pmo/forge/plan                  — Create a plan via IntelligentPlanner
POST /pmo/forge/approve               — Save an approved plan to a project
GET  /pmo/signals                     — List all open signals
POST /pmo/signals                     — Create a signal
POST /pmo/signals/{signal_id}/resolve — Resolve a signal
POST /pmo/signals/{signal_id}/forge     — Triage signal into a plan
POST /pmo/signals/batch/resolve         — Batch-resolve multiple signals
GET  /pmo/cards/{card_id}               — Card detail with optional plan data
GET  /pmo/forge/sessions                — List forge sessions
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from agent_baton.api.deps import get_forge_session, get_pmo_scanner, get_pmo_store
from agent_baton.api.models.requests import (
    ApproveForgeRequest,
    CreateForgeRequest,
    CreateSignalRequest,
    ForgeSignalRequest,
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
    ResolveSignalResponse,
)
from agent_baton.core.pmo.forge import ForgeSession
from agent_baton.core.pmo.scanner import PmoScanner
from agent_baton.core.pmo.store import PmoStore
from agent_baton.models.pmo import PmoProject, PmoSignal

router = APIRouter()


# ---------------------------------------------------------------------------
# Board
# ---------------------------------------------------------------------------


@router.get("/pmo/board", response_model=PmoBoardResponse)
async def get_board(
    scanner: PmoScanner = Depends(get_pmo_scanner),
) -> PmoBoardResponse:
    """Return the full Kanban board with all cards and per-program health."""
    cards = scanner.scan_all()
    health_map = scanner.program_health(cards=cards)

    card_responses = [_card_response(c) for c in cards]
    health_responses = {
        prog: _health_response(h) for prog, h in health_map.items()
    }
    return PmoBoardResponse(cards=card_responses, health=health_responses)


@router.get("/pmo/board/{program}", response_model=PmoBoardResponse)
async def get_board_by_program(
    program: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
) -> PmoBoardResponse:
    """Return the Kanban board filtered to a single program."""
    cards = scanner.scan_all()
    program_upper = program.upper()
    filtered = [c for c in cards if c.program.upper() == program_upper]
    health_map = scanner.program_health(cards=cards)

    card_responses = [_card_response(c) for c in filtered]
    health_responses = {
        prog: _health_response(h)
        for prog, h in health_map.items()
        if prog.upper() == program_upper
    }
    return PmoBoardResponse(cards=card_responses, health=health_responses)


# ---------------------------------------------------------------------------
# Card detail
# ---------------------------------------------------------------------------


@router.get("/pmo/cards/{card_id}", response_model=dict)
async def get_card(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> dict:
    """Return a single card by its task ID, including full plan data if available."""
    from pathlib import Path as _Path

    cards = scanner.scan_all()
    card = next((c for c in cards if c.card_id == card_id), None)
    if card is None:
        raise HTTPException(status_code=404, detail=f"Card '{card_id}' not found.")

    card_dict = _card_response(card).model_dump()

    project = store.get_project(card.project_id)
    if project is not None:
        context_root = _Path(project.path) / ".claude" / "team-context"
        plan_data: dict | None = None
        scoped_plan = context_root / "executions" / card_id / "plan.json"
        root_plan = context_root / "plan.json"
        for plan_path in (scoped_plan, root_plan):
            if plan_path.exists():
                try:
                    raw = json.loads(plan_path.read_text(encoding="utf-8"))
                    if raw.get("task_id") == card_id:
                        plan_data = raw
                        break
                except (json.JSONDecodeError, OSError):
                    pass
        card_dict["plan"] = plan_data

    return card_dict


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.get("/pmo/projects", response_model=list[PmoProjectResponse])
async def list_projects(
    store: PmoStore = Depends(get_pmo_store),
) -> list[PmoProjectResponse]:
    """Return all registered PMO projects."""
    config = store.load_config()
    return [_project_response(p) for p in config.projects]


@router.post("/pmo/projects", response_model=PmoProjectResponse, status_code=201)
async def register_project(
    req: RegisterProjectRequest,
    store: PmoStore = Depends(get_pmo_store),
) -> PmoProjectResponse:
    """Register a new project with the PMO.

    If a project with the same ``project_id`` already exists it is replaced
    — this is intentional to allow re-registration after path changes.
    """
    project = PmoProject(
        project_id=req.project_id,
        name=req.name,
        path=req.path,
        program=req.program,
        color=req.color,
        description=req.description,
    )
    try:
        store.register_project(project)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to register project: {exc}",
        ) from exc

    # Reload from store so registered_at is populated.
    saved = store.get_project(req.project_id)
    if saved is None:
        raise HTTPException(
            status_code=500,
            detail="Project was written but could not be read back.",
        )
    return _project_response(saved)


@router.delete("/pmo/projects/{project_id}", status_code=204)
async def unregister_project(
    project_id: str,
    store: PmoStore = Depends(get_pmo_store),
) -> None:
    """Unregister a project from the PMO.

    Returns 404 if no project with that ID exists.
    """
    removed = store.unregister_project(project_id)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"Project '{project_id}' not found.",
        )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/pmo/health", response_model=dict[str, ProgramHealthResponse])
async def get_health(
    scanner: PmoScanner = Depends(get_pmo_scanner),
) -> dict[str, ProgramHealthResponse]:
    """Return aggregate health metrics per program."""
    health_map = scanner.program_health()
    return {prog: _health_response(h) for prog, h in health_map.items()}


# ---------------------------------------------------------------------------
# Forge (plan creation + approval)
# ---------------------------------------------------------------------------


@router.post("/pmo/forge/plan", response_model=dict, status_code=201)
async def forge_plan(
    req: CreateForgeRequest,
    forge: ForgeSession = Depends(get_forge_session),
    store: PmoStore = Depends(get_pmo_store),
) -> dict:
    """Create a plan via IntelligentPlanner for the given project.

    The plan is returned as a raw dict for the UI to display and edit before
    approval.  It is NOT saved to disk at this stage — call
    ``POST /pmo/forge/approve`` to persist it.
    """
    project = store.get_project(req.project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project '{req.project_id}' not found.",
        )

    try:
        plan = forge.create_plan(
            description=req.description,
            program=req.program,
            project_id=req.project_id,
            task_type=req.task_type,
            priority=req.priority,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Plan creation failed: {exc}",
        ) from exc

    return plan.to_dict()


@router.post("/pmo/forge/approve", status_code=200)
async def forge_approve(
    req: ApproveForgeRequest,
    forge: ForgeSession = Depends(get_forge_session),
    store: PmoStore = Depends(get_pmo_store),
) -> dict:
    """Save an approved plan to the project's team-context directory.

    The caller supplies the (possibly edited) plan dict and the target
    project_id.  The plan is written as ``plan.json`` and ``plan.md`` under
    ``<project.path>/.claude/team-context/``.

    Returns ``{"saved": true, "path": "<plan.json path>"}`` on success.
    """
    project = store.get_project(req.project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project '{req.project_id}' not found.",
        )

    try:
        from agent_baton.models.execution import MachinePlan

        plan = MachinePlan.from_dict(req.plan)
        saved_path = forge.save_plan(plan, project)
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid plan payload: {exc}",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save plan: {exc}",
        ) from exc

    return {"saved": True, "path": str(saved_path)}


@router.post("/pmo/forge/interview", response_model=InterviewResponse)
async def forge_interview(
    req: InterviewRequest,
    forge: ForgeSession = Depends(get_forge_session),
) -> InterviewResponse:
    """Generate structured interview questions for plan refinement."""
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
    """Re-generate a plan incorporating interview answers."""
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


@router.get("/pmo/forge/sessions", response_model=list[dict])
async def list_forge_sessions(
    status: str | None = None,
    store: PmoStore = Depends(get_pmo_store),
) -> list[dict]:
    """List forge sessions, optionally filtered by status ('active' or 'completed')."""
    if hasattr(store, "list_forge_sessions"):
        return store.list_forge_sessions(status=status)  # type: ignore[union-attr]
    return []


@router.get("/pmo/ado/search", response_model=AdoSearchResponse)
async def ado_search(q: str = "") -> AdoSearchResponse:
    """Search Azure DevOps work items (placeholder with mock data)."""
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


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


@router.get("/pmo/signals", response_model=list[PmoSignalResponse])
async def list_signals(
    store: PmoStore = Depends(get_pmo_store),
) -> list[PmoSignalResponse]:
    """Return all open (non-resolved) signals."""
    signals = store.get_open_signals()
    return [_signal_response(s) for s in signals]


@router.post("/pmo/signals", response_model=PmoSignalResponse, status_code=201)
async def create_signal(
    req: CreateSignalRequest,
    store: PmoStore = Depends(get_pmo_store),
) -> PmoSignalResponse:
    """Create a new signal (bug, escalation, or blocker)."""
    signal = PmoSignal(
        signal_id=req.signal_id,
        signal_type=req.signal_type,
        title=req.title,
        description=req.description,
        source_project_id=req.source_project_id,
        severity=req.severity,
    )
    try:
        store.add_signal(signal)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create signal: {exc}",
        ) from exc

    # Reload to get the populated created_at.
    config = store.load_config()
    saved = next((s for s in config.signals if s.signal_id == req.signal_id), None)
    if saved is None:
        raise HTTPException(
            status_code=500,
            detail="Signal was written but could not be read back.",
        )
    return _signal_response(saved)


class BatchResolveRequest(BaseModel):
    """Request body for POST /pmo/signals/batch/resolve."""
    signal_ids: list[str]


@router.post("/pmo/signals/batch/resolve", response_model=dict)
async def batch_resolve_signals(
    req: BatchResolveRequest,
    store: PmoStore = Depends(get_pmo_store),
) -> dict:
    """Resolve multiple signals in a single request."""
    resolved: list[str] = []
    not_found: list[str] = []
    for sid in req.signal_ids:
        if store.resolve_signal(sid):
            resolved.append(sid)
        else:
            not_found.append(sid)
    return {"resolved": resolved, "not_found": not_found}


@router.post("/pmo/signals/{signal_id}/resolve", response_model=ResolveSignalResponse)
async def resolve_signal(
    signal_id: str,
    store: PmoStore = Depends(get_pmo_store),
) -> ResolveSignalResponse:
    """Mark a signal as resolved and return the updated signal.

    Sets the signal's status to ``"resolved"`` and returns the full updated
    signal so callers can synchronise their local state without a separate
    fetch.  Returns 404 if the signal does not exist.
    """
    found = store.resolve_signal(signal_id)
    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Signal '{signal_id}' not found.",
        )

    # Read the signal back from the store to get the authoritative, updated state.
    config = store.load_config()
    updated = next((s for s in config.signals if s.signal_id == signal_id), None)
    if updated is None:
        raise HTTPException(
            status_code=500,
            detail="Signal was resolved but could not be read back.",
        )

    sig = _signal_response(updated)
    return ResolveSignalResponse(
        resolved=True,
        signal_id=sig.signal_id,
        signal_type=sig.signal_type,
        title=sig.title,
        description=sig.description,
        source_project_id=sig.source_project_id,
        severity=sig.severity,
        status=sig.status,
        created_at=sig.created_at,
        forge_task_id=sig.forge_task_id,
    )


@router.post("/pmo/signals/{signal_id}/forge", response_model=dict, status_code=201)
async def forge_signal(
    signal_id: str,
    req: ForgeSignalRequest,
    forge: ForgeSession = Depends(get_forge_session),
    store: PmoStore = Depends(get_pmo_store),
) -> dict:
    """Triage a signal into an execution plan via the Forge.

    Generates a bug-fix plan from the signal description, links the signal
    to the plan, and saves the plan to the project's team-context.  The
    signal status is updated to ``triaged``.

    The ``project_id`` in the request body determines which project receives
    the plan.  The Forge derives the plan description from the signal itself
    — no ``plan`` payload is required.

    Returns the generated plan dict plus the saved path.
    """
    project = store.get_project(req.project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project '{req.project_id}' not found.",
        )

    try:
        plan = forge.signal_to_plan(signal_id=signal_id, project_id=req.project_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Forge triaging failed: {exc}",
        ) from exc

    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=f"Signal '{signal_id}' not found.",
        )

    saved_path = forge.save_plan(plan, project)
    return {
        "signal_id": signal_id,
        "plan_id": plan.task_id,
        "path": str(saved_path),
    }


# ---------------------------------------------------------------------------
# Internal conversion helpers
# ---------------------------------------------------------------------------


def _project_response(p: object) -> PmoProjectResponse:
    """Convert a PmoProject dataclass to a PmoProjectResponse."""
    return PmoProjectResponse(
        project_id=p.project_id,  # type: ignore[attr-defined]
        name=p.name,  # type: ignore[attr-defined]
        path=p.path,  # type: ignore[attr-defined]
        program=p.program,  # type: ignore[attr-defined]
        color=p.color,  # type: ignore[attr-defined]
        description=p.description,  # type: ignore[attr-defined]
        registered_at=p.registered_at,  # type: ignore[attr-defined]
        ado_project=getattr(p, "ado_project", ""),  # type: ignore[attr-defined]
    )


def _card_response(c: object) -> PmoCardResponse:
    """Convert a PmoCard dataclass to a PmoCardResponse."""
    return PmoCardResponse(
        card_id=c.card_id,  # type: ignore[attr-defined]
        project_id=c.project_id,  # type: ignore[attr-defined]
        program=c.program,  # type: ignore[attr-defined]
        title=c.title,  # type: ignore[attr-defined]
        column=c.column,  # type: ignore[attr-defined]
        risk_level=c.risk_level,  # type: ignore[attr-defined]
        priority=c.priority,  # type: ignore[attr-defined]
        agents=list(c.agents),  # type: ignore[attr-defined]
        steps_completed=c.steps_completed,  # type: ignore[attr-defined]
        steps_total=c.steps_total,  # type: ignore[attr-defined]
        gates_passed=c.gates_passed,  # type: ignore[attr-defined]
        current_phase=c.current_phase,  # type: ignore[attr-defined]
        error=c.error,  # type: ignore[attr-defined]
        created_at=c.created_at,  # type: ignore[attr-defined]
        updated_at=c.updated_at,  # type: ignore[attr-defined]
    )


def _health_response(h: object) -> ProgramHealthResponse:
    """Convert a ProgramHealth dataclass to a ProgramHealthResponse."""
    return ProgramHealthResponse(
        program=h.program,  # type: ignore[attr-defined]
        total_plans=h.total_plans,  # type: ignore[attr-defined]
        active=h.active,  # type: ignore[attr-defined]
        completed=h.completed,  # type: ignore[attr-defined]
        blocked=h.blocked,  # type: ignore[attr-defined]
        failed=h.failed,  # type: ignore[attr-defined]
        completion_pct=h.completion_pct,  # type: ignore[attr-defined]
    )


def _signal_response(s: object) -> PmoSignalResponse:
    """Convert a PmoSignal dataclass to a PmoSignalResponse."""
    return PmoSignalResponse(
        signal_id=s.signal_id,  # type: ignore[attr-defined]
        signal_type=s.signal_type,  # type: ignore[attr-defined]
        title=s.title,  # type: ignore[attr-defined]
        description=s.description,  # type: ignore[attr-defined]
        source_project_id=s.source_project_id,  # type: ignore[attr-defined]
        severity=s.severity,  # type: ignore[attr-defined]
        status=s.status,  # type: ignore[attr-defined]
        created_at=s.created_at,  # type: ignore[attr-defined]
        forge_task_id=s.forge_task_id,  # type: ignore[attr-defined]
    )
