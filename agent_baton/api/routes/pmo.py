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
POST /pmo/signals/{signal_id}/forge   — Triage signal into a plan
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from agent_baton.api.deps import get_forge_session, get_pmo_scanner, get_pmo_store
from agent_baton.api.models.requests import (
    ApproveForgeRequest,
    CreateForgeRequest,
    CreateSignalRequest,
    RegisterProjectRequest,
)
from agent_baton.api.models.responses import (
    PmoBoardResponse,
    PmoCardResponse,
    PmoProjectResponse,
    PmoSignalResponse,
    ProgramHealthResponse,
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
    health_map = scanner.program_health()

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
    health_map = scanner.program_health()

    card_responses = [_card_response(c) for c in filtered]
    health_responses = {
        prog: _health_response(h)
        for prog, h in health_map.items()
        if prog.upper() == program_upper
    }
    return PmoBoardResponse(cards=card_responses, health=health_responses)


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


@router.post("/pmo/signals/{signal_id}/resolve", response_model=dict)
async def resolve_signal(
    signal_id: str,
    store: PmoStore = Depends(get_pmo_store),
) -> dict:
    """Mark a signal as resolved.

    Returns 404 if the signal does not exist.
    """
    resolved = store.resolve_signal(signal_id)
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"Signal '{signal_id}' not found.",
        )
    return {"resolved": True, "signal_id": signal_id}


@router.post("/pmo/signals/{signal_id}/forge", response_model=dict, status_code=201)
async def forge_signal(
    signal_id: str,
    req: ApproveForgeRequest,
    forge: ForgeSession = Depends(get_forge_session),
    store: PmoStore = Depends(get_pmo_store),
) -> dict:
    """Triage a signal into an execution plan via the Forge.

    Generates a bug-fix plan from the signal description, links the signal
    to the plan, and saves the plan to the project's team-context.  The
    signal status is updated to ``triaged``.

    The ``project_id`` in the request body determines which project receives
    the plan.  The ``plan`` field is ignored for this endpoint — the Forge
    derives the description from the signal itself.

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
