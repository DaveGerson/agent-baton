"""PMO (Portfolio Management Office) endpoints for the Agent Baton API.

GET  /pmo/board                          — Full Kanban board (cards + health)
GET  /pmo/board/{program}               — Filter board by program
GET  /pmo/cards/{card_id}               — Card detail (card + plan)
GET  /pmo/projects                      — List registered projects
POST /pmo/projects                      — Register a project
DELETE /pmo/projects/{project_id}       — Unregister a project
GET  /pmo/health                        — Program health metrics
GET  /pmo/events                        — SSE stream of board-relevant events
POST /pmo/forge/plan                    — Create a plan via IntelligentPlanner
POST /pmo/forge/approve                 — Save an approved plan to a project
GET  /pmo/signals                       — List all open signals
POST /pmo/signals                       — Create a signal
POST /pmo/signals/batch/resolve         — Resolve multiple signals in one call
POST /pmo/signals/{signal_id}/resolve   — Resolve a signal
POST /pmo/signals/{signal_id}/forge     — Triage signal into a plan
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agent_baton.api.deps import get_bus, get_forge_session, get_pmo_scanner, get_pmo_store
from agent_baton.core.events.bus import EventBus
from agent_baton.api.models.requests import (
    ApproveForgeRequest,
    BatchResolveRequest,
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
    PmoCardDetailResponse,
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
    """Return the full Kanban board with all cards and per-program health.

    GET /api/v1/pmo/board

    Scans all registered projects for execution states and maps each
    plan to a Kanban card with its lifecycle column (queued, planning,
    executing, gate_pending, deployed, failed).

    Args:
        scanner: Injected ``PmoScanner`` singleton.

    Returns:
        A ``PmoBoardResponse`` containing all Kanban cards and
        per-program health metrics.
    """
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
    """Return the Kanban board filtered to a single program.

    GET /api/v1/pmo/board/{program}

    Same as ``GET /pmo/board`` but only includes cards and health
    metrics for the specified program code.  The comparison is
    case-insensitive.

    Args:
        program: Program code to filter by (URL path parameter),
            e.g. ``"NDS"``, ``"ATL"``.
        scanner: Injected ``PmoScanner`` singleton.

    Returns:
        A ``PmoBoardResponse`` containing filtered cards and the
        matching program's health metrics.
    """
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


@router.get("/pmo/cards/{card_id}", response_model=PmoCardDetailResponse)
async def get_card(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
) -> PmoCardDetailResponse:
    """Return detailed information for a single card, including its plan.

    GET /api/v1/pmo/cards/{card_id}

    Scans all registered projects until a card whose ``card_id`` matches
    ``task_id`` is found.  If the card's plan file is accessible on disk,
    the full plan dict is included in the response.  Archived (deployed)
    cards are also searchable.

    Args:
        card_id: The task ID of the card to look up (URL path parameter).
        scanner: Injected ``PmoScanner`` singleton.

    Returns:
        A ``PmoCardDetailResponse`` with all card fields plus an optional
        ``plan`` dict.

    Raises:
        HTTPException 404: If no card with ``card_id`` is found.
    """
    try:
        card, plan_dict = scanner.find_card(card_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Card '{card_id}' not found.",
        )
    return PmoCardDetailResponse(
        card_id=card.card_id,
        project_id=card.project_id,
        program=card.program,
        title=card.title,
        column=card.column,
        risk_level=card.risk_level,
        priority=card.priority,
        agents=list(card.agents),
        steps_completed=card.steps_completed,
        steps_total=card.steps_total,
        gates_passed=card.gates_passed,
        current_phase=card.current_phase,
        error=card.error,
        created_at=card.created_at,
        updated_at=card.updated_at,
        external_id=card.external_id,
        plan=plan_dict,
    )


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


@router.get("/pmo/projects", response_model=list[PmoProjectResponse])
async def list_projects(
    store: PmoStore = Depends(get_pmo_store),
) -> list[PmoProjectResponse]:
    """Return all registered PMO projects.

    GET /api/v1/pmo/projects

    Args:
        store: Injected PMO store singleton (SQLite-backed).

    Returns:
        A list of ``PmoProjectResponse`` objects for every registered
        project.
    """
    config = store.load_config()
    return [_project_response(p) for p in config.projects]


@router.post("/pmo/projects", response_model=PmoProjectResponse, status_code=201)
async def register_project(
    req: RegisterProjectRequest,
    store: PmoStore = Depends(get_pmo_store),
) -> PmoProjectResponse:
    """Register a new project with the PMO.

    POST /api/v1/pmo/projects

    If a project with the same ``project_id`` already exists it is
    replaced -- this is intentional to allow re-registration after
    path changes.

    Args:
        req: Validated request body with project_id, name, path,
            program, and optional color/description.
        store: Injected PMO store singleton.

    Returns:
        A ``PmoProjectResponse`` for the newly registered project
        (201 Created).

    Raises:
        HTTPException 500: If the store fails to write or read back
            the project.
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

    DELETE /api/v1/pmo/projects/{project_id}

    Removes the project from the PMO registry.  Associated plans and
    execution states on disk are not deleted -- only the PMO registration
    is removed.

    Args:
        project_id: The project slug to remove (URL path parameter).
        store: Injected PMO store singleton.

    Raises:
        HTTPException 404: If no project with *project_id* exists.
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
    """Return aggregate health metrics per program.

    GET /api/v1/pmo/health

    Provides counts of total, active, completed, blocked, and failed
    plans plus a completion percentage for each program.

    Args:
        scanner: Injected ``PmoScanner`` singleton.

    Returns:
        A dict mapping program codes to ``ProgramHealthResponse``
        objects.
    """
    health_map = scanner.program_health()
    return {prog: _health_response(h) for prog, h in health_map.items()}


# ---------------------------------------------------------------------------
# Real-time board updates via Server-Sent Events
# ---------------------------------------------------------------------------

# Topics that indicate a card's column or progress may have changed.
_PMO_BOARD_TOPICS = frozenset(
    [
        "step.completed",
        "step.failed",
        "step.dispatched",
        "gate.required",
        "gate.passed",
        "gate.failed",
        "task.started",
        "task.completed",
        "task.failed",
        "phase.started",
        "phase.completed",
        "approval.required",
        "approval.resolved",
    ]
)


@router.get(
    "/pmo/events",
    summary="Stream board-relevant events over SSE",
    response_description="Server-Sent Event stream of card_update payloads.",
    response_class=EventSourceResponse,
    tags=["pmo"],
)
async def stream_pmo_events(
    request: Request,
    bus: EventBus = Depends(get_bus),
) -> EventSourceResponse:
    """Open a Server-Sent Events stream for PMO board changes.

    GET /api/v1/pmo/events
    Accept: text/event-stream

    Subscribes to the shared ``EventBus`` and forwards every board-relevant
    event as a ``card_update`` payload:

    .. code-block:: json

        { "type": "card_update", "card_id": "<task_id>", "topic": "<topic>" }

    Only events whose ``topic`` maps to a visible board change are emitted
    (step/gate/task/phase/approval transitions).  Events unrelated to the
    board (e.g. usage tracking, webhook delivery) are silently dropped.

    A keepalive comment is sent every 30 seconds when no board event arrives,
    preventing proxies and browsers from closing idle connections.

    Args:
        request: Injected by FastAPI; used to detect client disconnection.
        bus: The shared ``EventBus`` instance.

    Returns:
        A streaming ``EventSourceResponse`` yielding SSE frames with
        ``event`` set to ``"card_update"`` and ``data`` as a JSON object.
    """

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        def on_event(event) -> None:
            if event.topic in _PMO_BOARD_TOPICS:
                queue.put_nowait(event)

        sub_id = bus.subscribe("*", on_event)

        try:
            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    payload = {
                        "type": "card_update",
                        "card_id": event.task_id,
                        "topic": event.topic,
                    }
                    yield {
                        "event": "card_update",
                        "id": event.event_id,
                        "data": json.dumps(payload),
                    }
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}

        finally:
            bus.unsubscribe(sub_id)

    return EventSourceResponse(event_generator())


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

    POST /api/v1/pmo/forge/plan

    The plan is returned as a raw dict for the UI to display and edit
    before approval.  It is NOT saved to disk at this stage -- call
    ``POST /pmo/forge/approve`` to persist it.

    Args:
        req: Validated request body with description, program,
            project_id, and optional task_type/priority.
        forge: Injected ``ForgeSession`` singleton.
        store: Injected PMO store singleton (to verify project exists).

    Returns:
        The generated plan as a raw dict (201 Created).

    Raises:
        HTTPException 400: If the description or parameters are
            semantically invalid.
        HTTPException 404: If the specified project is not registered.
        HTTPException 500: If the planner encounters an internal error.
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

    POST /api/v1/pmo/forge/approve

    The caller supplies the (possibly edited) plan dict and the target
    ``project_id``.  The plan is written as ``plan.json`` and
    ``plan.md`` under ``<project.path>/.claude/team-context/``.

    Args:
        req: Validated request body with ``plan`` (dict) and
            ``project_id``.
        forge: Injected ``ForgeSession`` singleton.
        store: Injected PMO store singleton (to resolve project path).

    Returns:
        ``{"saved": true, "path": "<plan.json path>"}``

    Raises:
        HTTPException 400: If the plan dict is malformed.
        HTTPException 404: If the specified project is not registered.
        HTTPException 500: If writing the plan files fails.
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
    """Generate structured interview questions for plan refinement.

    POST /api/v1/pmo/forge/interview

    Analyzes the provided plan and generates 3-5 targeted questions
    to help refine the plan based on ambiguities, missing context,
    or optimization opportunities.

    Args:
        req: Validated request body with the current ``plan`` dict
            and optional ``feedback`` text.
        forge: Injected ``ForgeSession`` singleton.

    Returns:
        An ``InterviewResponse`` containing the generated questions,
        each with an ``answer_type`` of ``"choice"`` or ``"text"``.

    Raises:
        HTTPException 400: If the plan dict is malformed.
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

    POST /api/v1/pmo/forge/regenerate

    Takes the original plan, the user's interview answers, and the
    original task description, then produces a refined plan that
    incorporates the additional context from the interview.

    Args:
        req: Validated request body with ``project_id``,
            ``description``, ``original_plan``, ``answers``, and
            optional ``task_type``/``priority``.
        forge: Injected ``ForgeSession`` singleton.
        store: Injected PMO store singleton (to verify project exists).

    Returns:
        The regenerated plan as a raw dict (201 Created).

    Raises:
        HTTPException 404: If the specified project is not registered.
        HTTPException 500: If the regeneration process fails.
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
    """Search Azure DevOps work items via the ADO adapter.

    GET /api/v1/pmo/ado/search

    When ``ADO_PAT`` is set (along with ``ADO_ORG`` and ``ADO_PROJECT``),
    the endpoint queries Azure DevOps via the REST API.  When not
    configured, it returns an empty list with a guidance message.

    Args:
        q: Optional search query string (query parameter).

    Returns:
        An ``AdoSearchResponse`` with matching work items.
    """
    import os

    pat = os.environ.get("ADO_PAT", "")
    if not pat:
        return AdoSearchResponse(
            items=[],
            message="ADO not configured. Set ADO_PAT environment variable.",
        )

    org = os.environ.get("ADO_ORG", "")
    project = os.environ.get("ADO_PROJECT", "")
    if not org or not project:
        return AdoSearchResponse(
            items=[],
            message="ADO not configured. Set ADO_ORG and ADO_PROJECT environment variables.",
        )

    from agent_baton.core.storage.adapters.ado import AdoAdapter

    adapter = AdoAdapter()
    try:
        adapter.connect({
            "organization": org,
            "project": project,
            "pat_env_var": "ADO_PAT",
        })
    except (ValueError, ImportError) as exc:
        return AdoSearchResponse(
            items=[],
            message=f"ADO connection failed: {exc}",
        )

    try:
        external_items = adapter.fetch_items()
    except RuntimeError as exc:
        return AdoSearchResponse(
            items=[],
            message=f"ADO query failed: {exc}",
        )

    # Convert ExternalItems to AdoWorkItemResponse and apply search filter
    results: list[AdoWorkItemResponse] = []
    query_lower = q.lower()
    for ei in external_items:
        item = AdoWorkItemResponse(
            id=ei.external_id,
            title=ei.title,
            type=ei.item_type,
            program=ei.tags[0] if ei.tags else "",
            owner=ei.assigned_to,
            priority=f"P{ei.priority}" if ei.priority else "",
            description=ei.description,
        )
        if query_lower:
            if not (
                query_lower in item.title.lower()
                or query_lower in item.id.lower()
                or query_lower in item.type.lower()
            ):
                continue
        results.append(item)

    return AdoSearchResponse(items=results)


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


@router.get("/pmo/signals", response_model=list[PmoSignalResponse])
async def list_signals(
    store: PmoStore = Depends(get_pmo_store),
) -> list[PmoSignalResponse]:
    """Return all open (non-resolved) signals.

    GET /api/v1/pmo/signals

    Args:
        store: Injected PMO store singleton.

    Returns:
        A list of ``PmoSignalResponse`` objects for all signals with
        status ``"open"`` or ``"triaged"``.
    """
    signals = store.get_open_signals()
    return [_signal_response(s) for s in signals]


@router.post("/pmo/signals", response_model=PmoSignalResponse, status_code=201)
async def create_signal(
    req: CreateSignalRequest,
    store: PmoStore = Depends(get_pmo_store),
) -> PmoSignalResponse:
    """Create a new signal (bug, escalation, or blocker).

    POST /api/v1/pmo/signals

    Args:
        req: Validated request body with signal_id, signal_type,
            title, and optional description/source_project_id/severity.
        store: Injected PMO store singleton.

    Returns:
        A ``PmoSignalResponse`` for the newly created signal
        (201 Created).

    Raises:
        HTTPException 500: If the store fails to write or read back
            the signal.
    """
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


@router.post("/pmo/signals/batch/resolve", response_model=dict)
async def batch_resolve_signals(
    req: BatchResolveRequest,
    store: PmoStore = Depends(get_pmo_store),
) -> dict:
    """Resolve multiple signals in a single request.

    POST /api/v1/pmo/signals/batch/resolve

    Each signal ID in the list is marked as ``"resolved"``.  IDs that
    do not match any known signal are collected in ``not_found`` and
    silently skipped rather than causing an error.

    Args:
        req: Validated request body with a non-empty ``signal_ids`` list.
        store: Injected PMO store singleton.

    Returns:
        A dict with ``resolved`` (list of IDs that were resolved),
        ``not_found`` (list of IDs that had no matching signal), and
        ``count`` (number of signals resolved).
    """
    resolved, not_found = store.resolve_signals(req.signal_ids)
    return {
        "resolved": resolved,
        "not_found": not_found,
        "count": len(resolved),
    }


@router.post("/pmo/signals/{signal_id}/resolve", response_model=dict)
async def resolve_signal(
    signal_id: str,
    store: PmoStore = Depends(get_pmo_store),
) -> dict:
    """Mark a signal as resolved.

    POST /api/v1/pmo/signals/{signal_id}/resolve

    Sets the signal's status to ``"resolved"``.  This is a one-way
    transition; resolved signals cannot be re-opened.

    Args:
        signal_id: The signal identifier (URL path parameter).
        store: Injected PMO store singleton.

    Returns:
        ``{"resolved": true, "signal_id": "<id>"}``

    Raises:
        HTTPException 404: If no signal with *signal_id* exists.
    """
    resolved = store.resolve_signal(signal_id)
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"Signal '{signal_id}' not found.",
        )
    # Return the full signal so the frontend can update its state correctly.
    # Fixes F-AF-2: frontend expected PmoSignal shape, got partial dict.
    if hasattr(store, "get_signal"):
        signal = store.get_signal(signal_id)
        if signal is not None:
            resp = _signal_response(signal).model_dump()
            resp["resolved"] = True
            return resp
    return {"resolved": True, "signal_id": signal_id}


@router.post("/pmo/signals/{signal_id}/forge", response_model=dict, status_code=201)
async def forge_signal(
    signal_id: str,
    req: ForgeSignalRequest,
    forge: ForgeSession = Depends(get_forge_session),
    store: PmoStore = Depends(get_pmo_store),
) -> dict:
    """Triage a signal into an execution plan via the Forge.

    POST /api/v1/pmo/signals/{signal_id}/forge

    Generates a bug-fix plan from the signal description, links the
    signal to the plan, and saves the plan to the project's
    team-context.  The signal status is updated to ``triaged``.

    Args:
        signal_id: The signal to triage (URL path parameter).
        req: Request body providing ``project_id`` (the ``plan``
            field is ignored).
        forge: Injected ``ForgeSession`` singleton.
        store: Injected PMO store singleton.

    Returns:
        A dict with ``signal_id``, ``plan_id``, and ``path`` to the
        saved plan file (201 Created).

    Raises:
        HTTPException 404: If the project or signal does not exist.
        HTTPException 500: If the Forge triaging process fails.
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
    """Convert a ``PmoProject`` dataclass to a ``PmoProjectResponse``.

    Args:
        p: A ``PmoProject`` dataclass instance from the PMO store.

    Returns:
        A Pydantic ``PmoProjectResponse`` suitable for JSON serialization.
    """
    return PmoProjectResponse(
        project_id=p.project_id,  # type: ignore[attr-defined]
        name=p.name,  # type: ignore[attr-defined]
        path=p.path,  # type: ignore[attr-defined]
        program=p.program,  # type: ignore[attr-defined]
        color=p.color,  # type: ignore[attr-defined]
        description=p.description,  # type: ignore[attr-defined]
        registered_at=p.registered_at,  # type: ignore[attr-defined]
        ado_project=p.ado_project,  # type: ignore[attr-defined]
    )


def _card_response(c: object) -> PmoCardResponse:
    """Convert a ``PmoCard`` dataclass to a ``PmoCardResponse``.

    Args:
        c: A ``PmoCard`` dataclass instance from the PMO scanner.

    Returns:
        A Pydantic ``PmoCardResponse`` suitable for JSON serialization.
    """
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
        external_id=c.external_id,  # type: ignore[attr-defined]
    )


def _health_response(h: object) -> ProgramHealthResponse:
    """Convert a ``ProgramHealth`` dataclass to a ``ProgramHealthResponse``.

    Args:
        h: A ``ProgramHealth`` dataclass instance from the PMO scanner.

    Returns:
        A Pydantic ``ProgramHealthResponse`` suitable for JSON
        serialization.
    """
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
    """Convert a ``PmoSignal`` dataclass to a ``PmoSignalResponse``.

    Args:
        s: A ``PmoSignal`` dataclass instance from the PMO store.

    Returns:
        A Pydantic ``PmoSignalResponse`` suitable for JSON serialization.
    """
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
