"""PMO (Portfolio Management Office) endpoints for the Agent Baton API.

GET  /pmo/board                          — Full Kanban board (cards + health)
GET  /pmo/board/{program}               — Filter board by program
GET  /pmo/cards/{card_id}               — Card detail (card + plan)
GET  /pmo/cards/{card_id}/execution    — Execution progress events
GET  /pmo/projects                      — List registered projects
POST /pmo/projects                      — Register a project
DELETE /pmo/projects/{project_id}       — Unregister a project
GET  /pmo/health                        — Program health metrics
GET  /pmo/events                        — SSE stream of board-relevant events
POST /pmo/forge/plan                    — Create a plan via headless Claude
POST /pmo/forge/approve                 — Save an approved plan to a project
POST /pmo/execute/{card_id}             — Launch headless execution for a card
GET  /pmo/gates/pending                 — List executions awaiting gate approval
POST /pmo/gates/{task_id}/approve       — Approve a pending gate
POST /pmo/gates/{task_id}/reject        — Reject a pending gate
GET  /pmo/signals                       — List all open signals
POST /pmo/signals                       — Create a signal
POST /pmo/signals/batch/resolve         — Resolve multiple signals in one call
POST /pmo/signals/{signal_id}/resolve   — Resolve a signal
POST /pmo/signals/{signal_id}/forge     — Triage signal into a plan
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agent_baton.api.deps import get_bus, get_central_store, get_forge_session, get_pmo_scanner, get_pmo_store
from agent_baton.core.events.bus import EventBus
from agent_baton.api.models.requests import (
    ApproveForgeRequest,
    BatchResolveRequest,
    CreateForgeRequest,
    CreatePrRequest,
    CreateSignalRequest,
    ExecuteCardRequest,
    ForgeSignalRequest,
    GateApproveRequest,
    GateRejectRequest,
    InterviewRequest,
    MergeCardRequest,
    RegenerateRequest,
    RegisterProjectRequest,
    RequestReviewRequest,
    RetryStepRequest,
    SkipStepRequest,
)
from agent_baton.api.models.responses import (
    AdoSearchResponse,
    AdoWorkItemResponse,
    ApprovalLogEntry,
    ApprovalLogResponse,
    ChangelistResponse,
    CreatePrResponse,
    ExecuteCardResponse,
    ExecutionControlResponse,
    ExternalItemResponse,
    ExternalMappingResponse,
    ForgeApproveResponse,
    ForgePlanResponse,
    GateActionResponse,
    InterviewQuestionResponse,
    InterviewResponse,
    MergeResponse,
    PendingGateResponse,
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
# Forge progress SSE registry
# ---------------------------------------------------------------------------

# Maps session_id -> asyncio.Queue of forge progress event dicts.
# Entries are created by forge_plan and consumed by stream_forge_progress.
# Queues are sentinel-terminated (None) when generation completes.
_forge_progress_queues: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}

_FORGE_STAGES = [
    ("analyzing",   0,   "Analyzing codebase..."),
    ("routing",     25,  "Selecting agents..."),
    ("sizing",      50,  "Sizing budget..."),
    ("generating",  75,  "Generating plan..."),
    ("validating",  90,  "Validating plan..."),
    ("complete",    100, "Plan ready"),
]


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


class _StepEvent(BaseModel):
    event_type: str
    step_id: str
    agent: str | None = None
    status: str | None = None
    timestamp: str
    message: str | None = None


class _ExecutionDetailResponse(BaseModel):
    task_id: str
    status: str
    current_phase: str
    steps: list[_StepEvent]
    started_at: str
    elapsed_seconds: float


@router.get("/pmo/cards/{card_id}/execution")
async def get_card_execution(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
) -> _ExecutionDetailResponse:
    """Return execution progress events for a card.

    GET /api/v1/pmo/cards/{card_id}/execution

    Reads the event log for the card's task_id and returns step-level
    events for the execution progress monitor in the PMO UI.
    """
    from pathlib import Path
    from datetime import datetime, timezone

    try:
        card, _ = scanner.find_card(card_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Card '{card_id}' not found.")

    project_path = Path(card.project_id) if Path(card.project_id).is_dir() else None
    events: list[_StepEvent] = []
    started_at = card.created_at
    status = card.column

    if project_path:
        event_log = project_path / ".claude" / "team-context" / "executions" / card_id / "events.jsonl"
        if not event_log.exists():
            event_log = project_path / ".claude" / "team-context" / "events.jsonl"
        if event_log.exists():
            try:
                for line in event_log.read_text().splitlines():
                    if not line.strip():
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("task_id") and evt["task_id"] != card_id:
                        continue
                    event_type = evt.get("topic", evt.get("event_type", ""))
                    if not event_type:
                        continue
                    events.append(_StepEvent(
                        event_type=event_type,
                        step_id=evt.get("step_id", evt.get("payload", {}).get("step_id", "")),
                        agent=evt.get("agent", evt.get("payload", {}).get("agent")),
                        status=evt.get("status", evt.get("payload", {}).get("status")),
                        timestamp=evt.get("timestamp", evt.get("ts", "")),
                        message=evt.get("message", evt.get("payload", {}).get("message")),
                    ))
                    if event_type == "task.started":
                        started_at = evt.get("timestamp", started_at)
            except OSError:
                pass

    try:
        started_dt = datetime.fromisoformat(started_at)
    except (ValueError, TypeError):
        started_dt = datetime.now(timezone.utc)
    elapsed = (datetime.now(timezone.utc) - started_dt.replace(tzinfo=timezone.utc if started_dt.tzinfo is None else started_dt.tzinfo)).total_seconds()

    return _ExecutionDetailResponse(
        task_id=card_id,
        status=status,
        current_phase=card.current_phase or "",
        steps=events,
        started_at=started_at,
        elapsed_seconds=max(0, elapsed),
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
        "gate.approved",
        "gate.rejected",
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


@router.get(
    "/pmo/forge/progress/{session_id}",
    summary="Stream forge plan-generation progress over SSE",
    response_description="Server-Sent Event stream of forge progress payloads.",
    response_class=EventSourceResponse,
    tags=["pmo"],
)
async def stream_forge_progress(
    session_id: str,
    request: Request,
) -> EventSourceResponse:
    """Open a Server-Sent Events stream for forge plan-generation progress.

    GET /api/v1/pmo/forge/progress/{session_id}
    Accept: text/event-stream

    Streams best-effort cosmetic progress events emitted by the
    corresponding ``POST /pmo/forge/plan`` request.  The frontend
    obtains the ``session_id`` from the forge plan response and
    connects to this endpoint before or immediately after calling
    ``POST /pmo/forge/plan``.

    Each SSE frame carries a ``forge_progress`` event with data:

    .. code-block:: json

        {"stage": "analyzing", "progress_pct": 0, "message": "Analyzing codebase..."}

    The stream closes automatically after the ``complete`` event
    (``progress_pct: 100``) or when the client disconnects.  If the
    ``session_id`` is unknown the stream sends a single ``error``
    event and closes.

    Args:
        session_id: UUID returned by ``POST /pmo/forge/plan``.
        request: Injected by FastAPI; used to detect client disconnection.

    Returns:
        A streaming ``EventSourceResponse`` yielding SSE frames with
        ``event`` set to ``"forge_progress"`` and ``data`` as a JSON
        object.
    """

    async def event_generator():
        # Wait briefly for the producer to register its queue (race-safe).
        deadline = asyncio.get_event_loop().time() + 5.0
        while session_id not in _forge_progress_queues:
            if asyncio.get_event_loop().time() >= deadline:
                yield {
                    "event": "forge_progress",
                    "data": json.dumps(
                        {"stage": "error", "progress_pct": 0, "message": "Unknown session_id"}
                    ),
                }
                return
            await asyncio.sleep(0.1)

        queue = _forge_progress_queues[session_id]
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=30.0)
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
                    continue

                if item is None:
                    # Sentinel — generation complete, close the stream.
                    break

                yield {
                    "event": "forge_progress",
                    "data": json.dumps(item),
                }

                if item.get("progress_pct") == 100:
                    break
        finally:
            _forge_progress_queues.pop(session_id, None)

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Forge (plan creation + approval)
# ---------------------------------------------------------------------------


def _publish_forge_progress(
    queue: asyncio.Queue[dict[str, Any] | None],
    stage: str,
    progress_pct: int,
    message: str,
) -> None:
    """Push a forge progress event onto *queue* (non-blocking, best-effort)."""
    try:
        queue.put_nowait({"stage": stage, "progress_pct": progress_pct, "message": message})
    except asyncio.QueueFull:
        pass  # cosmetic feedback — drop silently if queue is full


@router.post("/pmo/forge/plan", response_model=ForgePlanResponse, status_code=201)
async def forge_plan(
    req: CreateForgeRequest,
    forge: ForgeSession = Depends(get_forge_session),
    store: PmoStore = Depends(get_pmo_store),
) -> ForgePlanResponse:
    """Create a plan via IntelligentPlanner for the given project.

    POST /api/v1/pmo/forge/plan

    The plan is returned as a raw dict for the UI to display and edit
    before approval.  It is NOT saved to disk at this stage -- call
    ``POST /pmo/forge/approve`` to persist it.

    A ``session_id`` is also returned so the frontend can subscribe to
    ``GET /pmo/forge/progress/{session_id}`` for real-time progress
    events during the (potentially slow) plan generation step.

    Args:
        req: Validated request body with description, program,
            project_id, and optional task_type/priority.
        forge: Injected ``ForgeSession`` singleton.
        store: Injected PMO store singleton (to verify project exists).

    Returns:
        A ``ForgePlanResponse`` with ``session_id`` and the generated
        ``plan`` dict (201 Created).

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

    session_id = uuid.uuid4().hex
    # maxsize=32 is generous for 6 events — prevents unbounded growth if the
    # SSE client never connects.
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=32)
    _forge_progress_queues[session_id] = queue

    try:
        _publish_forge_progress(queue, "analyzing", 0, "Analyzing codebase...")
        _publish_forge_progress(queue, "routing", 25, "Selecting agents...")
        _publish_forge_progress(queue, "sizing", 50, "Sizing budget...")
        _publish_forge_progress(queue, "generating", 75, "Generating plan...")

        loop = asyncio.get_event_loop()
        try:
            plan = await loop.run_in_executor(
                None,
                lambda: forge.create_plan(
                    description=req.description,
                    program=req.program,
                    project_id=req.project_id,
                    task_type=req.task_type,
                    priority=req.priority,
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Plan creation failed: {exc}",
            ) from exc

        _publish_forge_progress(queue, "validating", 90, "Validating plan...")
        _publish_forge_progress(queue, "complete", 100, "Plan ready")

    finally:
        # Sentinel: tells the SSE stream to close after draining remaining events.
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

    return ForgePlanResponse(session_id=session_id, plan=plan.to_dict())


@router.post("/pmo/forge/approve", response_model=ForgeApproveResponse, status_code=200)
async def forge_approve(
    req: ApproveForgeRequest,
    forge: ForgeSession = Depends(get_forge_session),
    store: PmoStore = Depends(get_pmo_store),
) -> ForgeApproveResponse:
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

    return ForgeApproveResponse(saved=True, path=str(saved_path))


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


# ---------------------------------------------------------------------------
# Execution launch
# ---------------------------------------------------------------------------


@router.post("/pmo/execute/{card_id}", response_model=ExecuteCardResponse, status_code=202)
async def execute_card(
    card_id: str,
    req: ExecuteCardRequest,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ExecuteCardResponse:
    """Launch headless execution for a queued card.

    POST /api/v1/pmo/execute/{card_id}

    Finds the card's plan on disk, then spawns an autonomous ``baton
    execute run`` subprocess that drives the full execution loop without
    an active Claude Code session.

    The execution runs in the background; the endpoint returns immediately
    with the task ID and PID of the spawned process.

    Args:
        card_id: The task ID of the card to execute (URL path parameter).
        req: Execution options (model, dry_run, max_steps).
        scanner: Injected ``PmoScanner`` singleton.
        store: Injected PMO store singleton.

    Returns:
        ``{"task_id": "<id>", "pid": <pid>, "status": "launched"}``
        (202 Accepted).

    Raises:
        HTTPException 404: If the card or its plan cannot be found.
        HTTPException 500: If the execution process fails to start.
    """
    import subprocess
    import sys

    # Find the card and its project
    try:
        card, plan_dict = scanner.find_card(card_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Card '{card_id}' not found.")

    if plan_dict is None:
        raise HTTPException(status_code=404, detail=f"No plan found for card '{card_id}'.")

    if card.column != "queued":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Card '{card_id}' is in column '{card.column}' and cannot be launched. "
                "Only cards in the 'queued' column may be executed."
            ),
        )

    project = store.get_project(card.project_id)
    if project is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project '{card.project_id}' not registered.",
        )

    # Locate the plan.json file on disk
    from pathlib import Path

    project_root = Path(project.path)
    plan_path = (
        project_root / ".claude" / "team-context" / "executions" / card_id / "plan.json"
    )
    if not plan_path.exists():
        # Try root-level fallback
        plan_path = project_root / ".claude" / "team-context" / "plan.json"
        if not plan_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Plan file not found for card '{card_id}'.",
            )

    # Build the baton execute run command
    cmd = [
        sys.executable, "-m", "agent_baton", "execute", "run",
        "--plan", str(plan_path),
        "--task-id", card_id,
        "--model", req.model,
        "--max-steps", str(req.max_steps),
    ]
    if req.dry_run:
        cmd.append("--dry-run")

    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(project_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to start execution process: {exc}",
        )

    return ExecuteCardResponse(
        task_id=card_id,
        pid=process.pid,
        status="launched",
        model=req.model,
        dry_run=req.dry_run,
    )


# ---------------------------------------------------------------------------
# Execution interrupt controls
# ---------------------------------------------------------------------------


def _resolve_worker_context(
    card_id: str,
    scanner: PmoScanner,
    store: PmoStore,
) -> tuple:
    """Shared helper: resolve a card to its project root and context root.

    Returns ``(card, project_root, context_root)``.

    Raises:
        HTTPException 404: Card or project not found.
    """
    from pathlib import Path

    try:
        card, _ = scanner.find_card(card_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Card '{card_id}' not found.")

    project_root = _resolve_project_path(card, store)
    if project_root is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project path for card '{card_id}' could not be resolved.",
        )

    context_root = project_root / ".claude" / "team-context"
    return card, project_root, context_root


@router.post(
    "/pmo/execute/{card_id}/pause",
    response_model=ExecutionControlResponse,
    summary="Pause a running execution by sending SIGSTOP to its worker",
    tags=["pmo"],
)
async def pause_execution(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
    bus: EventBus = Depends(get_bus),
) -> ExecutionControlResponse:
    """Pause a running execution worker with SIGSTOP.

    POST /api/v1/pmo/execute/{card_id}/pause

    Locates the ``worker.pid`` file for the card's execution directory,
    sends ``SIGSTOP`` to suspend the worker process without terminating it,
    and publishes a ``task.paused`` event so the PMO board updates via SSE.

    The worker can be resumed at any point with the ``/resume`` endpoint.

    Args:
        card_id: The task ID of the card whose worker should be paused.
        scanner: Injected ``PmoScanner`` singleton.
        store: Injected PMO store singleton.
        bus: The shared ``EventBus`` (for SSE event emission).

    Returns:
        ``{"status": "paused", "task_id": "<id>"}``

    Raises:
        HTTPException 404: If the card, project, or PID file cannot be found.
        HTTPException 409: If the process is not running or cannot be signalled.
    """
    from agent_baton.core.runtime.supervisor import WorkerSupervisor
    from agent_baton.models.events import Event

    card, project_root, context_root = _resolve_worker_context(card_id, scanner, store)

    supervisor = WorkerSupervisor(team_context_root=context_root, task_id=card_id)
    try:
        pid = supervisor.pause_worker(card_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProcessLookupError, PermissionError, OSError) as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Could not pause worker for card '{card_id}': {exc}",
        ) from exc

    try:
        bus.publish(Event.create(
            topic="task.paused",
            task_id=card_id,
            payload={"pid": pid},
        ))
    except Exception:
        pass  # SSE emission is best-effort.

    return ExecutionControlResponse(
        status="paused",
        task_id=card_id,
        message=f"Sent SIGSTOP to worker process {pid}.",
    )


@router.post(
    "/pmo/execute/{card_id}/resume",
    response_model=ExecutionControlResponse,
    summary="Resume a paused execution by sending SIGCONT to its worker",
    tags=["pmo"],
)
async def resume_execution(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
    bus: EventBus = Depends(get_bus),
) -> ExecutionControlResponse:
    """Resume a previously paused execution worker with SIGCONT.

    POST /api/v1/pmo/execute/{card_id}/resume

    Sends ``SIGCONT`` to the suspended worker process and publishes a
    ``task.resumed`` event so the PMO board updates via SSE.

    Args:
        card_id: The task ID of the card whose worker should be resumed.
        scanner: Injected ``PmoScanner`` singleton.
        store: Injected PMO store singleton.
        bus: The shared ``EventBus`` (for SSE event emission).

    Returns:
        ``{"status": "running", "task_id": "<id>"}``

    Raises:
        HTTPException 404: If the card, project, or PID file cannot be found.
        HTTPException 409: If the process cannot be signalled.
    """
    from agent_baton.core.runtime.supervisor import WorkerSupervisor
    from agent_baton.models.events import Event

    card, project_root, context_root = _resolve_worker_context(card_id, scanner, store)

    supervisor = WorkerSupervisor(team_context_root=context_root, task_id=card_id)
    try:
        pid = supervisor.resume_worker(card_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProcessLookupError, PermissionError, OSError) as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Could not resume worker for card '{card_id}': {exc}",
        ) from exc

    try:
        bus.publish(Event.create(
            topic="task.resumed",
            task_id=card_id,
            payload={"pid": pid},
        ))
    except Exception:
        pass

    return ExecutionControlResponse(
        status="running",
        task_id=card_id,
        message=f"Sent SIGCONT to worker process {pid}.",
    )


@router.post(
    "/pmo/execute/{card_id}/cancel",
    response_model=ExecutionControlResponse,
    summary="Cancel a running execution by sending SIGTERM to its worker",
    tags=["pmo"],
)
async def cancel_execution(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
    bus: EventBus = Depends(get_bus),
) -> ExecutionControlResponse:
    """Cancel a running execution worker with SIGTERM.

    POST /api/v1/pmo/execute/{card_id}/cancel

    Sends ``SIGTERM`` to the worker process (which triggers the worker's
    graceful shutdown path) and publishes a ``task.cancelled`` event so
    the PMO board updates via SSE.

    Args:
        card_id: The task ID of the card whose worker should be cancelled.
        scanner: Injected ``PmoScanner`` singleton.
        store: Injected PMO store singleton.
        bus: The shared ``EventBus`` (for SSE event emission).

    Returns:
        ``{"status": "cancelled", "task_id": "<id>"}``

    Raises:
        HTTPException 404: If the card, project, or PID file cannot be found.
        HTTPException 409: If the process cannot be signalled.
    """
    from agent_baton.core.runtime.supervisor import WorkerSupervisor
    from agent_baton.models.events import Event

    card, project_root, context_root = _resolve_worker_context(card_id, scanner, store)

    supervisor = WorkerSupervisor(team_context_root=context_root, task_id=card_id)
    try:
        pid = supervisor.cancel_worker(card_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ProcessLookupError, PermissionError, OSError) as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Could not cancel worker for card '{card_id}': {exc}",
        ) from exc

    try:
        bus.publish(Event.create(
            topic="task.cancelled",
            task_id=card_id,
            payload={"pid": pid},
        ))
    except Exception:
        pass

    return ExecutionControlResponse(
        status="cancelled",
        task_id=card_id,
        message=f"Sent SIGTERM to worker process {pid}.",
    )


@router.post(
    "/pmo/execute/{card_id}/retry-step",
    response_model=ExecutionControlResponse,
    summary="Reset a failed step to pending so it will be re-dispatched",
    tags=["pmo"],
)
async def retry_step(
    card_id: str,
    req: RetryStepRequest,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ExecutionControlResponse:
    """Reset a failed step back to pending for re-dispatch.

    POST /api/v1/pmo/execute/{card_id}/retry-step

    Removes the failed ``StepResult`` for the specified step from the
    execution state and saves the updated state.  On the next loop
    iteration the execution engine will see the step as un-recorded and
    re-dispatch it.

    This endpoint is intended for use when execution has stopped due to
    a failed step and the operator wants to give the step another
    attempt without restarting the entire task.

    Args:
        card_id: The task ID of the card (URL path parameter).
        req: Request body containing ``step_id``.
        scanner: Injected ``PmoScanner`` singleton.
        store: Injected PMO store singleton.

    Returns:
        ``{"status": "retried", "task_id": "<id>", "step_id": "<step_id>"}``

    Raises:
        HTTPException 404: If the card, project, execution state, or step
            cannot be found.
        HTTPException 409: If the step is not currently in ``"failed"``
            status.
        HTTPException 500: If the storage layer fails to save the updated
            state.
    """
    from agent_baton.core.storage import detect_backend, get_project_storage

    card, project_root, context_root = _resolve_worker_context(card_id, scanner, store)

    try:
        backend = detect_backend(context_root)
        storage = get_project_storage(context_root, backend=backend)
        state = storage.load_execution(card_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load execution state: {exc}",
        ) from exc

    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No execution state found for card '{card_id}'.",
        )

    # Locate the step result to be retried.
    target = next(
        (r for r in state.step_results if r.step_id == req.step_id),
        None,
    )
    if target is None:
        raise HTTPException(
            status_code=404,
            detail=f"Step '{req.step_id}' not found in execution state for card '{card_id}'.",
        )
    if target.status != "failed":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Step '{req.step_id}' has status '{target.status}' and cannot be retried. "
                "Only steps in 'failed' status may be reset."
            ),
        )

    # Remove the failed result so the engine treats the step as pending.
    state.step_results = [r for r in state.step_results if r.step_id != req.step_id]

    try:
        storage.save_execution(state)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save updated execution state: {exc}",
        ) from exc

    return ExecutionControlResponse(
        status="retried",
        task_id=card_id,
        step_id=req.step_id,
        message=f"Step '{req.step_id}' reset to pending; it will be re-dispatched on next loop.",
    )


@router.post(
    "/pmo/execute/{card_id}/skip-step",
    response_model=ExecutionControlResponse,
    summary="Mark a failed step as skipped so execution can continue past it",
    tags=["pmo"],
)
async def skip_step(
    card_id: str,
    req: SkipStepRequest,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ExecutionControlResponse:
    """Mark a step as skipped so execution advances past it.

    POST /api/v1/pmo/execute/{card_id}/skip-step

    Upserts a ``StepResult`` with ``status="skipped"`` for the specified
    step and saves the updated execution state.  The execution engine
    treats a skipped step as complete for dependency-resolution purposes,
    allowing the phase to advance.

    Args:
        card_id: The task ID of the card (URL path parameter).
        req: Request body containing ``step_id`` and optional ``reason``.
        scanner: Injected ``PmoScanner`` singleton.
        store: Injected PMO store singleton.

    Returns:
        ``{"status": "skipped", "task_id": "<id>", "step_id": "<step_id>"}``

    Raises:
        HTTPException 404: If the card, project, or execution state cannot
            be found.
        HTTPException 409: If the step is already in ``"complete"`` status
            (skipping a completed step would silently discard its output).
        HTTPException 500: If the storage layer fails to save the updated
            state.
    """
    from datetime import datetime, timezone
    from agent_baton.core.storage import detect_backend, get_project_storage
    from agent_baton.models.execution import StepResult

    card, project_root, context_root = _resolve_worker_context(card_id, scanner, store)

    try:
        backend = detect_backend(context_root)
        storage = get_project_storage(context_root, backend=backend)
        state = storage.load_execution(card_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load execution state: {exc}",
        ) from exc

    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No execution state found for card '{card_id}'.",
        )

    # Guard: refuse to skip a step that has already completed successfully.
    existing = next(
        (r for r in state.step_results if r.step_id == req.step_id),
        None,
    )
    if existing is not None and existing.status == "complete":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Step '{req.step_id}' is already in 'complete' status and cannot be skipped."
            ),
        )

    # Remove any existing result for this step (failed/dispatched) and insert
    # a new skipped result so the engine sees it as resolved.
    state.step_results = [r for r in state.step_results if r.step_id != req.step_id]
    skipped_result = StepResult(
        step_id=req.step_id,
        agent_name="operator",
        status="skipped",
        outcome=req.reason or "Skipped by operator via PMO UI.",
        completed_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    state.step_results.append(skipped_result)

    try:
        storage.save_execution(state)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to save updated execution state: {exc}",
        ) from exc

    return ExecutionControlResponse(
        status="skipped",
        task_id=card_id,
        step_id=req.step_id,
        message=req.reason or "Skipped by operator via PMO UI.",
    )


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
# Gate approval (human-in-the-loop)
# ---------------------------------------------------------------------------


def _resolve_project_path(card: "PmoCard", store: PmoStore) -> "Path | None":  # type: ignore[name-defined]
    """Return the absolute project root path for *card*, or None."""
    from pathlib import Path

    project = store.get_project(card.project_id)
    if project is None:
        # card.project_id might already be an absolute path (file-backend projects).
        candidate = Path(card.project_id)
        return candidate if candidate.is_dir() else None
    return Path(project.path)


def _locate_awaiting_card(
    task_id: str,
    scanner: PmoScanner,
    store: PmoStore,
) -> tuple:
    """Shared guard: find a card that is in ``awaiting_human`` state.

    Returns ``(card, project_root_path)``.

    Raises:
        HTTPException 404: Card not found.
        HTTPException 409: Card found but not in ``awaiting_human`` column.
        HTTPException 404: Project path cannot be resolved.
    """
    from pathlib import Path

    try:
        card, _ = scanner.find_card(task_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Task '{task_id}' not found.",
        )

    if card.column != "awaiting_human":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Task '{task_id}' is in column '{card.column}' and is not awaiting approval. "
                "Only tasks in 'awaiting_human' can be approved or rejected."
            ),
        )

    project_root = _resolve_project_path(card, store)
    if project_root is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project path for task '{task_id}' could not be resolved.",
        )

    return card, project_root


@router.get(
    "/pmo/gates/pending",
    response_model=list[PendingGateResponse],
    summary="List executions awaiting gate approval",
    tags=["pmo"],
)
async def list_pending_gates(
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> list[PendingGateResponse]:
    """Return all executions currently paused and waiting for gate approval.

    GET /api/v1/pmo/gates/pending

    Scans all registered projects for cards in the ``awaiting_human``
    column and loads their execution state to extract the phase context,
    approval options, and review summary.  The ``approval_context`` field
    contains the markdown summary built by the engine during the
    ``APPROVAL`` action so the reviewer can make an informed decision
    from the PMO UI.

    Args:
        scanner: Injected ``PmoScanner`` singleton.
        store: Injected PMO store singleton (used to resolve project paths).

    Returns:
        A list of ``PendingGateResponse`` objects, one per paused execution.
    """
    from agent_baton.core.storage import detect_backend, get_project_storage
    from agent_baton.core.engine.executor import ExecutionEngine
    from agent_baton.core.events.bus import EventBus as _LocalBus

    all_cards = scanner.scan_all()
    pending = [c for c in all_cards if c.column == "awaiting_human"]

    results: list[PendingGateResponse] = []

    for card in pending:
        project_root = _resolve_project_path(card, store)
        if project_root is None:
            results.append(
                PendingGateResponse(
                    task_id=card.card_id,
                    project_id=card.project_id,
                    phase_id=0,
                    phase_name=card.current_phase,
                    task_summary=card.title,
                    current_phase_name=card.current_phase,
                )
            )
            continue

        context_root = project_root / ".claude" / "team-context"
        storage = None
        try:
            backend = detect_backend(context_root)
            storage = get_project_storage(context_root, backend=backend)
            state = storage.load_execution(card.card_id)
        except Exception:
            state = None

        if state is None:
            results.append(
                PendingGateResponse(
                    task_id=card.card_id,
                    project_id=card.project_id,
                    phase_id=0,
                    phase_name=card.current_phase,
                    task_summary=card.title,
                    current_phase_name=card.current_phase,
                )
            )
            continue

        # Use the engine to produce the same APPROVAL action the CLI would emit.
        try:
            engine = ExecutionEngine(
                team_context_root=context_root,
                bus=_LocalBus(),
                task_id=card.card_id,
                storage=storage,
            )
            action = engine.next_action()
            action_dict = action.to_dict()
        except Exception:
            action_dict = {}

        phase_obj = (
            state.plan.phases[state.current_phase]
            if state.current_phase < len(state.plan.phases)
            else None
        )
        phase_id = phase_obj.phase_id if phase_obj else 0
        phase_name = phase_obj.name if phase_obj else card.current_phase

        results.append(
            PendingGateResponse(
                task_id=card.card_id,
                project_id=card.project_id,
                phase_id=action_dict.get("phase_id", phase_id),
                phase_name=phase_name,
                approval_context=action_dict.get("approval_context", ""),
                approval_options=action_dict.get(
                    "approval_options",
                    ["approve", "reject", "approve-with-feedback"],
                ),
                task_summary=card.title,
                current_phase_name=phase_name,
            )
        )

    return results


@router.post(
    "/pmo/gates/{task_id}/approve",
    response_model=GateActionResponse,
    summary="Approve a pending gate",
    tags=["pmo"],
)
async def approve_gate(
    task_id: str,
    req: GateApproveRequest,
    request: Request,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
    bus: EventBus = Depends(get_bus),
    central: object = Depends(get_central_store),
) -> GateActionResponse:
    """Record a human approval decision for a paused execution.

    POST /api/v1/pmo/gates/{task_id}/approve

    Equivalent to running ``baton execute approve --phase-id N --result approve``
    from the CLI.  After recording the decision the execution engine advances
    to the next phase and an SSE ``gate.approved`` event is published so the
    PMO board updates in real time.  The decision is also written to the
    ``approval_log`` table in central.db for cross-project audit visibility.

    Args:
        task_id: The task ID of the paused execution (URL path parameter).
        req: Validated request body with ``phase_id`` and optional ``notes``.
        request: Injected FastAPI request (provides request.state.user_id).
        scanner: Injected ``PmoScanner`` singleton.
        store: Injected PMO store singleton (used to resolve the project path).
        bus: The shared ``EventBus`` (for SSE event emission).
        central: Injected ``CentralStore`` for writing to approval_log.

    Returns:
        ``GateActionResponse`` confirming the approval was recorded.

    Raises:
        HTTPException 404: If the task cannot be found.
        HTTPException 409: If the task is not in ``awaiting_human`` state.
        HTTPException 500: If the engine fails to record the approval.
    """
    import uuid
    from datetime import datetime, timezone
    from agent_baton.core.storage import detect_backend, get_project_storage
    from agent_baton.core.engine.executor import ExecutionEngine
    from agent_baton.core.storage.central import CentralStore
    from agent_baton.models.events import Event

    card, project_root = _locate_awaiting_card(task_id, scanner, store)

    context_root = project_root / ".claude" / "team-context"
    try:
        backend = detect_backend(context_root)
        storage = get_project_storage(context_root, backend=backend)
        engine = ExecutionEngine(
            team_context_root=context_root,
            bus=bus,
            task_id=task_id,
            storage=storage,
        )
        engine.record_approval_result(
            phase_id=req.phase_id,
            result="approve",
            feedback=req.notes or "",
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Write approval_log entry (best-effort — never block the response).
    user_id: str = getattr(request.state, "user_id", "local-user")
    try:
        central_store: CentralStore = central  # type: ignore[assignment]
        central_store.execute(
            """
            INSERT INTO approval_log (log_id, task_id, phase_id, user_id, action, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                task_id,
                str(req.phase_id),
                user_id,
                "approve",
                req.notes or "",
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )
    except Exception:
        pass

    # Emit SSE event so the PMO board refreshes without polling.
    try:
        bus.publish(Event.create(
            topic="gate.approved",
            task_id=task_id,
            payload={"phase_id": req.phase_id, "result": "approve"},
        ))
    except Exception:
        pass  # SSE emission is best-effort; never block the response.

    return GateActionResponse(
        task_id=task_id,
        phase_id=req.phase_id,
        result="approve",
        recorded=True,
    )


@router.post(
    "/pmo/gates/{task_id}/reject",
    response_model=GateActionResponse,
    summary="Reject a pending gate",
    tags=["pmo"],
)
async def reject_gate(
    task_id: str,
    req: GateRejectRequest,
    request: Request,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
    bus: EventBus = Depends(get_bus),
    central: object = Depends(get_central_store),
) -> GateActionResponse:
    """Record a human rejection decision for a paused execution.

    POST /api/v1/pmo/gates/{task_id}/reject

    Equivalent to running ``baton execute approve --phase-id N --result reject``
    from the CLI.  The execution is marked as failed and an SSE
    ``gate.rejected`` event is published.  The decision is also written to the
    ``approval_log`` table in central.db for cross-project audit visibility.

    Args:
        task_id: The task ID of the paused execution (URL path parameter).
        req: Validated request body with ``phase_id`` and required ``reason``.
        request: Injected FastAPI request (provides request.state.user_id).
        scanner: Injected ``PmoScanner`` singleton.
        store: Injected PMO store singleton (used to resolve the project path).
        bus: The shared ``EventBus`` (for SSE event emission).
        central: Injected ``CentralStore`` for writing to approval_log.

    Returns:
        ``GateActionResponse`` confirming the rejection was recorded.

    Raises:
        HTTPException 404: If the task cannot be found.
        HTTPException 409: If the task is not in ``awaiting_human`` state.
        HTTPException 500: If the engine fails to record the rejection.
    """
    import uuid
    from datetime import datetime, timezone
    from agent_baton.core.storage import detect_backend, get_project_storage
    from agent_baton.core.engine.executor import ExecutionEngine
    from agent_baton.core.storage.central import CentralStore
    from agent_baton.models.events import Event

    card, project_root = _locate_awaiting_card(task_id, scanner, store)

    context_root = project_root / ".claude" / "team-context"
    try:
        backend = detect_backend(context_root)
        storage = get_project_storage(context_root, backend=backend)
        engine = ExecutionEngine(
            team_context_root=context_root,
            bus=bus,
            task_id=task_id,
            storage=storage,
        )
        engine.record_approval_result(
            phase_id=req.phase_id,
            result="reject",
            feedback=req.reason,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Write approval_log entry (best-effort — never block the response).
    user_id: str = getattr(request.state, "user_id", "local-user")
    try:
        central_store: CentralStore = central  # type: ignore[assignment]
        central_store.execute(
            """
            INSERT INTO approval_log (log_id, task_id, phase_id, user_id, action, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                task_id,
                str(req.phase_id),
                user_id,
                "reject",
                req.reason,
                datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
        )
    except Exception:
        pass

    # Emit SSE event so the PMO board refreshes.
    try:
        bus.publish(Event.create(
            topic="gate.rejected",
            task_id=task_id,
            payload={"phase_id": req.phase_id, "result": "reject", "reason": req.reason},
        ))
    except Exception:
        pass

    return GateActionResponse(
        task_id=task_id,
        phase_id=req.phase_id,
        result="reject",
        recorded=True,
    )


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
# External items — adapter data surfaced in the PMO dashboard
# ---------------------------------------------------------------------------

_VALID_SOURCE_TYPES = frozenset(["ado", "github", "jira", "linear"])


@router.get("/pmo/external-items", response_model=list[ExternalItemResponse])
async def list_external_items(
    source: str | None = None,
    project_id: str | None = None,
    status: str | None = None,
    central: object = Depends(get_central_store),
) -> list[ExternalItemResponse]:
    """List external work items from central.db.

    GET /api/v1/pmo/external-items

    Returns items from the ``external_items`` table joined with
    ``external_sources`` so the ``source_type`` field is populated.
    All filters are optional; omitting them returns all items.

    Query parameters:
        source:     Filter by source type: ``ado``, ``github``,
                    ``jira``, or ``linear``.
        project_id: Filter to items mapped to this baton project ID
                    (requires a matching row in ``external_mappings``).
        status:     Filter by workflow state string (exact match).

    Returns:
        A list of ``ExternalItemResponse`` objects (empty when no
        adapters are configured or no items have been synced).

    Raises:
        HTTPException 400: If ``source`` is not a recognised type.
    """
    from agent_baton.core.storage.central import CentralStore

    if source is not None and source not in _VALID_SOURCE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown source type '{source}'. "
                f"Valid values: {', '.join(sorted(_VALID_SOURCE_TYPES))}."
            ),
        )

    store: CentralStore = central  # type: ignore[assignment]

    # Build the WHERE clauses incrementally.
    conditions: list[str] = []
    params: list[object] = []

    if source is not None:
        conditions.append("es.source_type = ?")
        params.append(source)

    if status is not None:
        conditions.append("ei.state = ?")
        params.append(status)

    if project_id is not None:
        # Only items that have at least one mapping to this project.
        conditions.append(
            "EXISTS ("
            "  SELECT 1 FROM external_mappings em"
            "  WHERE em.source_id = ei.source_id"
            "    AND em.external_id = ei.external_id"
            "    AND em.project_id = ?"
            ")"
        )
        params.append(project_id)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT
            ei.id,
            ei.source_id,
            ei.external_id,
            ei.item_type,
            ei.title,
            ei.description,
            ei.state,
            ei.assigned_to,
            ei.priority,
            ei.tags,
            ei.url,
            ei.updated_at,
            COALESCE(es.source_type, '') AS source_type
        FROM external_items ei
        LEFT JOIN external_sources es ON es.source_id = ei.source_id
        {where}
        ORDER BY ei.updated_at DESC
    """

    try:
        rows = store.query(sql, tuple(params))
    except Exception:
        # central.db may not exist or may have no external tables yet.
        return []

    results: list[ExternalItemResponse] = []
    for row in rows:
        import json as _json
        try:
            tags = _json.loads(row.get("tags") or "[]")
            if not isinstance(tags, list):
                tags = []
        except (ValueError, TypeError):
            tags = []
        results.append(
            ExternalItemResponse(
                id=row["id"],
                source_id=row["source_id"],
                external_id=row["external_id"],
                item_type=row.get("item_type", ""),
                title=row.get("title", ""),
                description=row.get("description", ""),
                state=row.get("state", ""),
                assigned_to=row.get("assigned_to", ""),
                priority=str(row.get("priority", "")),
                tags=tags,
                url=row.get("url", ""),
                updated_at=row.get("updated_at", ""),
                source_type=row.get("source_type", ""),
            )
        )
    return results


@router.get(
    "/pmo/external-items/{item_id}/mappings",
    response_model=list[ExternalMappingResponse],
)
async def get_external_item_mappings(
    item_id: int,
    central: object = Depends(get_central_store),
) -> list[ExternalMappingResponse]:
    """Return all plan/execution mappings for an external item.

    GET /api/v1/pmo/external-items/{item_id}/mappings

    Looks up all rows in ``external_mappings`` for the item identified
    by its ``external_items.id`` primary key, joining back to
    ``external_items`` and ``external_sources`` so the caller receives
    full item details in a single request.

    Args:
        item_id: The ``external_items.id`` row PK (URL path parameter).
        central: Injected ``CentralStore`` singleton.

    Returns:
        A list of ``ExternalMappingResponse`` objects.  Empty when the
        item has no mappings or does not exist.
    """
    from agent_baton.core.storage.central import CentralStore
    import json as _json

    store: CentralStore = central  # type: ignore[assignment]

    # First resolve the item to get source_id + external_id.
    try:
        item_rows = store.query(
            """
            SELECT
                ei.id, ei.source_id, ei.external_id, ei.item_type,
                ei.title, ei.description, ei.state, ei.assigned_to,
                ei.priority, ei.tags, ei.url, ei.updated_at,
                COALESCE(es.source_type, '') AS source_type
            FROM external_items ei
            LEFT JOIN external_sources es ON es.source_id = ei.source_id
            WHERE ei.id = ?
            """,
            (item_id,),
        )
    except Exception:
        return []

    if not item_rows:
        raise HTTPException(
            status_code=404,
            detail=f"External item {item_id} not found.",
        )

    item_row = item_rows[0]

    try:
        tags = _json.loads(item_row.get("tags") or "[]")
        if not isinstance(tags, list):
            tags = []
    except (ValueError, TypeError):
        tags = []

    item_resp = ExternalItemResponse(
        id=item_row["id"],
        source_id=item_row["source_id"],
        external_id=item_row["external_id"],
        item_type=item_row.get("item_type", ""),
        title=item_row.get("title", ""),
        description=item_row.get("description", ""),
        state=item_row.get("state", ""),
        assigned_to=item_row.get("assigned_to", ""),
        priority=str(item_row.get("priority", "")),
        tags=tags,
        url=item_row.get("url", ""),
        updated_at=item_row.get("updated_at", ""),
        source_type=item_row.get("source_type", ""),
    )

    # Now fetch all mappings for this (source_id, external_id) pair.
    try:
        mapping_rows = store.query(
            """
            SELECT id, source_id, external_id, project_id,
                   task_id, mapping_type, created_at
            FROM external_mappings
            WHERE source_id = ? AND external_id = ?
            ORDER BY created_at DESC
            """,
            (item_row["source_id"], item_row["external_id"]),
        )
    except Exception:
        mapping_rows = []

    return [
        ExternalMappingResponse(
            id=m["id"],
            source_id=m["source_id"],
            external_id=m["external_id"],
            project_id=m["project_id"],
            task_id=m.get("task_id", ""),
            mapping_type=m.get("mapping_type", ""),
            created_at=m.get("created_at", ""),
            item=item_resp,
        )
        for m in mapping_rows
    ]


# ---------------------------------------------------------------------------
# Changelist / merge / PR endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/pmo/cards/{card_id}/changelist",
    response_model=ChangelistResponse,
    summary="Return the consolidation changelist for a card",
    tags=["pmo"],
)
async def get_card_changelist(
    card_id: str,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> ChangelistResponse:
    """Return the consolidation result (changelist) for a completed card.

    GET /api/v1/pmo/cards/{card_id}/changelist

    Loads the execution state for the card's task_id and returns the
    ``consolidation_result`` recorded by ``CommitConsolidator`` when the
    task completed.

    Args:
        card_id: The task ID of the card (URL path parameter).
        scanner: Injected ``PmoScanner`` singleton.
        store: Injected PMO store singleton.

    Returns:
        A ``ChangelistResponse`` mirroring the ``ConsolidationResult`` fields.

    Raises:
        HTTPException 404: If the card, project, or consolidation result is
            not found.
    """
    from pathlib import Path
    from agent_baton.core.storage import detect_backend, get_project_storage

    try:
        card, _ = scanner.find_card(card_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Card '{card_id}' not found.")

    project_root = _resolve_project_path(card, store)
    if project_root is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project path for card '{card_id}' could not be resolved.",
        )

    context_root = project_root / ".claude" / "team-context"
    try:
        backend = detect_backend(context_root)
        storage = get_project_storage(context_root, backend=backend)
        state = storage.load_execution(card_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load execution state: {exc}",
        ) from exc

    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No execution state found for card '{card_id}'.",
        )

    cr = state.consolidation_result
    if cr is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No consolidation result for card '{card_id}'. "
                "The task may not have completed yet or git_strategy is 'none'."
            ),
        )

    return ChangelistResponse(
        status=cr.status,
        final_head=cr.final_head,
        base_commit=cr.base_commit,
        files_changed=cr.files_changed,
        total_insertions=cr.total_insertions,
        total_deletions=cr.total_deletions,
        rebased_commits=list(cr.rebased_commits),
        attributions=[a.to_dict() for a in cr.attributions],
        conflict_files=cr.conflict_files,
        conflict_step_id=cr.conflict_step_id,
        skipped_steps=cr.skipped_steps,
        started_at=cr.started_at,
        completed_at=cr.completed_at,
        error=cr.error,
    )


@router.post(
    "/pmo/cards/{card_id}/merge",
    response_model=MergeResponse,
    summary="Fast-forward merge a consolidated card onto the base branch",
    tags=["pmo"],
)
async def merge_card(
    card_id: str,
    req: MergeCardRequest,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
    bus: EventBus = Depends(get_bus),
) -> MergeResponse:
    """Perform a fast-forward merge for a card whose commits are consolidated.

    POST /api/v1/pmo/cards/{card_id}/merge

    The cherry-picks were already applied to the feature branch by
    ``CommitConsolidator.consolidate()``.  This endpoint records the
    current HEAD as the merge commit, removes agent worktrees, and
    publishes a ``card.merged`` event.

    Args:
        card_id: The task ID of the card (URL path parameter).
        req: Optional merge options (``force`` flag).
        scanner: Injected ``PmoScanner`` singleton.
        store: Injected PMO store singleton.
        bus: The shared ``EventBus`` (for SSE event emission).

    Returns:
        A ``MergeResponse`` with the merge commit hash and cleaned worktrees.

    Raises:
        HTTPException 404: If the card, project, or execution state is missing.
        HTTPException 409: If the consolidation status is not ``'success'``
            and ``force`` is False.
        HTTPException 500: If the git or cleanup operation fails.
    """
    import subprocess
    import shutil
    from agent_baton.core.storage import detect_backend, get_project_storage
    from agent_baton.models.events import Event

    try:
        card, _ = scanner.find_card(card_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Card '{card_id}' not found.")

    project_root = _resolve_project_path(card, store)
    if project_root is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project path for card '{card_id}' could not be resolved.",
        )

    context_root = project_root / ".claude" / "team-context"
    try:
        backend = detect_backend(context_root)
        storage = get_project_storage(context_root, backend=backend)
        state = storage.load_execution(card_id)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load execution state: {exc}",
        ) from exc

    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No execution state found for card '{card_id}'.",
        )

    cr = state.consolidation_result
    if cr is None:
        raise HTTPException(
            status_code=404,
            detail=f"No consolidation result for card '{card_id}'.",
        )

    if not req.force and cr.status != "success":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Consolidation status is '{cr.status}', not 'success'. "
                "Resolve conflicts before merging, or pass force=true to override."
            ),
        )

    # The cherry-picks already landed the commits; resolve HEAD as merge_commit.
    git_bin = shutil.which("git")
    if git_bin is None:
        raise HTTPException(status_code=500, detail="git binary not found on PATH.")

    try:
        proc = subprocess.run(
            [git_bin, "rev-parse", "HEAD"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        merge_commit = proc.stdout.strip()
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resolve HEAD: {exc.stderr.strip()}",
        ) from exc

    # Clean up agent worktrees.
    cleaned_worktrees: list[str] = []
    try:
        from agent_baton.core.engine.consolidator import CommitConsolidator
        _consolidator = CommitConsolidator(working_directory=project_root)
        cleaned_worktrees = _consolidator.cleanup_worktrees(state)
    except Exception as exc:
        # Non-fatal: log and continue — worktrees can be cleaned manually.
        import logging
        logging.getLogger(__name__).warning(
            "Worktree cleanup failed for card '%s' (non-fatal): %s", card_id, exc
        )

    # Publish card.merged event so the PMO board updates via SSE.
    try:
        bus.publish(Event.create(
            topic="card.merged",
            task_id=card_id,
            payload={
                "merge_commit": merge_commit,
                "cleaned_worktrees": cleaned_worktrees,
            },
        ))
    except Exception:
        pass  # SSE emission is best-effort.

    return MergeResponse(
        merge_commit=merge_commit,
        cleaned_worktrees=cleaned_worktrees,
    )


@router.post(
    "/pmo/cards/{card_id}/create-pr",
    response_model=CreatePrResponse,
    status_code=201,
    summary="Create a GitHub pull request for a consolidated card",
    tags=["pmo"],
)
async def create_card_pr(
    card_id: str,
    req: CreatePrRequest,
    scanner: PmoScanner = Depends(get_pmo_scanner),
    store: PmoStore = Depends(get_pmo_store),
) -> CreatePrResponse:
    """Open a GitHub pull request for the card's consolidated branch.

    POST /api/v1/pmo/cards/{card_id}/create-pr

    Invokes ``gh pr create`` in the project root.  If ``body`` is omitted
    the engine builds a description from the plan summary and step outcomes.

    Args:
        card_id: The task ID of the card (URL path parameter).
        req: PR title, optional body, and base branch.
        scanner: Injected ``PmoScanner`` singleton.
        store: Injected PMO store singleton.

    Returns:
        A ``CreatePrResponse`` with the PR URL and numeric PR number
        (201 Created).

    Raises:
        HTTPException 404: If the card or project cannot be found.
        HTTPException 500: If ``gh pr create`` fails or returns unexpected
            output.
    """
    import re
    import subprocess
    import shutil
    from agent_baton.core.storage import detect_backend, get_project_storage

    try:
        card, _ = scanner.find_card(card_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Card '{card_id}' not found.")

    project_root = _resolve_project_path(card, store)
    if project_root is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project path for card '{card_id}' could not be resolved.",
        )

    # Build PR body from plan + step outcomes when the caller omits it.
    pr_body = req.body
    if not pr_body:
        try:
            context_root = project_root / ".claude" / "team-context"
            backend = detect_backend(context_root)
            storage = get_project_storage(context_root, backend=backend)
            state = storage.load_execution(card_id)
            if state is not None:
                lines: list[str] = [
                    f"## {state.plan.task_summary}",
                    "",
                    "### Step outcomes",
                ]
                for sr in state.step_results:
                    status_icon = "+" if sr.status == "complete" else "x"
                    lines.append(
                        f"- [{status_icon}] **{sr.step_id}** ({sr.agent_name}): "
                        f"{sr.outcome or sr.status}"
                    )
                pr_body = "\n".join(lines)
        except Exception:
            pr_body = f"Automated PR for task {card_id}."

    gh_bin = shutil.which("gh")
    if gh_bin is None:
        raise HTTPException(
            status_code=500,
            detail="gh CLI not found on PATH. Install the GitHub CLI to use this endpoint.",
        )

    cmd = [
        gh_bin, "pr", "create",
        "--title", req.title,
        "--body", pr_body,
        "--base", req.base_branch,
    ]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(project_root),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to invoke gh CLI: {exc}",
        ) from exc

    if proc.returncode != 0:
        raise HTTPException(
            status_code=500,
            detail=f"gh pr create failed: {proc.stderr.strip() or proc.stdout.strip()}",
        )

    # gh pr create prints the PR URL on stdout (e.g. https://github.com/org/repo/pull/42).
    pr_url = proc.stdout.strip().splitlines()[-1].strip()
    match = re.search(r"/pull/(\d+)", pr_url)
    if not match:
        raise HTTPException(
            status_code=500,
            detail=f"Could not parse PR number from gh output: {pr_url!r}",
        )
    pr_number = int(match.group(1))

    return CreatePrResponse(pr_url=pr_url, pr_number=pr_number)


# ---------------------------------------------------------------------------
# Role-based approval endpoints
# ---------------------------------------------------------------------------

_APPROVAL_ENV = __import__("os").environ.get("BATON_APPROVAL_MODE", "local").lower()


@router.post(
    "/pmo/cards/{card_id}/request-review",
    response_model=dict,
    status_code=201,
    summary="Request peer review for a card",
    tags=["pmo"],
)
async def request_card_review(
    card_id: str,
    req: RequestReviewRequest,
    request: Request,
    bus: EventBus = Depends(get_bus),
    central: object = Depends(get_central_store),
) -> dict:
    """Submit a card for peer review before approval.

    POST /api/v1/pmo/cards/{card_id}/request-review

    Writes an ``approval_log`` entry with ``action="request_review"`` and
    publishes a ``card.review_requested`` SSE event so the PMO board can
    reflect the pending review state in real time.

    Args:
        card_id: Task ID of the card to submit for review.
        req: Optional target reviewer_id and reviewer notes.
        request: Injected FastAPI request (provides request.state.user_id).
        bus: Shared ``EventBus`` for SSE emission.
        central: Injected ``CentralStore`` for writing to approval_log.

    Returns:
        ``{"logged": true, "log_id": "<uuid>", "card_id": "<card_id>"}``
        (201 Created).
    """
    import uuid
    from datetime import datetime, timezone
    from agent_baton.core.storage.central import CentralStore
    from agent_baton.models.events import Event

    user_id: str = getattr(request.state, "user_id", "local-user")
    log_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    store: CentralStore = central  # type: ignore[assignment]
    notes = req.notes or ""
    if req.reviewer_id:
        notes = f"Requested reviewer: {req.reviewer_id}. {notes}".strip()

    try:
        store.execute(
            """
            INSERT INTO approval_log (log_id, task_id, phase_id, user_id, action, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (log_id, card_id, "", user_id, "request_review", notes, now),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write approval log: {exc}",
        ) from exc

    try:
        bus.publish(Event.create(
            topic="card.review_requested",
            task_id=card_id,
            payload={
                "user_id": user_id,
                "reviewer_id": req.reviewer_id,
                "notes": notes,
                "log_id": log_id,
            },
        ))
    except Exception:
        pass  # SSE emission is best-effort.

    return {"logged": True, "log_id": log_id, "card_id": card_id}


@router.get(
    "/pmo/cards/{card_id}/approval-log",
    response_model=ApprovalLogResponse,
    summary="Return the approval audit log for a card",
    tags=["pmo"],
)
async def get_card_approval_log(
    card_id: str,
    central: object = Depends(get_central_store),
) -> ApprovalLogResponse:
    """Return all approval log entries for a card, newest first.

    GET /api/v1/pmo/cards/{card_id}/approval-log

    Reads from the ``approval_log`` table in central.db filtered by
    ``task_id``.  Returns an empty list when no entries exist.

    Args:
        card_id: Task ID of the card to look up (URL path parameter).
        central: Injected ``CentralStore`` for querying approval_log.

    Returns:
        An ``ApprovalLogResponse`` with entries ordered newest-first.
    """
    from agent_baton.core.storage.central import CentralStore

    store: CentralStore = central  # type: ignore[assignment]

    try:
        rows = store.query(
            """
            SELECT log_id, task_id, phase_id, user_id, action, notes, created_at
            FROM approval_log
            WHERE task_id = ?
            ORDER BY created_at DESC
            """,
            (card_id,),
        )
    except Exception:
        # approval_log table may not exist on old central.db — return empty.
        return ApprovalLogResponse(entries=[])

    entries = [
        ApprovalLogEntry(
            log_id=row["log_id"],
            task_id=row["task_id"],
            phase_id=row.get("phase_id", ""),
            user_id=row.get("user_id", "local-user"),
            action=row["action"],
            notes=row.get("notes", ""),
            created_at=row.get("created_at", ""),
        )
        for row in rows
    ]
    return ApprovalLogResponse(entries=entries)


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
