"""Decision endpoints for the Agent Baton API.

GET    /decisions                       — list decisions (filterable by status and task_id)
GET    /decisions/{request_id}          — fetch a single decision with enriched context files
POST   /decisions/{request_id}/resolve  — resolve a pending decision
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query

from agent_baton.api.deps import get_decision_manager
from agent_baton.api.models.requests import ResolveDecisionRequest
from agent_baton.api.models.responses import (
    DecisionListResponse,
    DecisionResponse,
    ResolveResponse,
)
from agent_baton.core.runtime.decisions import DecisionManager

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /decisions — list decisions
# ---------------------------------------------------------------------------

@router.get("/decisions", response_model=DecisionListResponse)
async def list_decisions(
    status: str | None = Query(
        default=None,
        description=(
            "Filter by status.  Use 'pending' to return only pending decisions, "
            "or any other status string ('resolved', 'expired') to filter list_all()."
        ),
    ),
    task_id: str | None = Query(
        default=None,
        description="Filter decisions to a specific task.",
    ),
    decision_manager: DecisionManager = Depends(get_decision_manager),
) -> DecisionListResponse:
    """Return a list of decision requests.

    GET /api/v1/decisions

    - If ``status="pending"``, calls ``DecisionManager.pending()`` directly.
    - If any other ``status`` is supplied, filters the full list by that
      status string.
    - If ``task_id`` is supplied, filters the result set by task.
    - Both filters may be combined.

    Args:
        status: Optional query parameter to filter by decision status
            (``"pending"``, ``"resolved"``, ``"expired"``).
        task_id: Optional query parameter to filter decisions belonging
            to a specific task.
        decision_manager: Injected ``DecisionManager`` singleton.

    Returns:
        A ``DecisionListResponse`` with the matching decisions and a
        count.
    """
    if status == "pending":
        items = decision_manager.pending()
    elif status is not None:
        items = [r for r in decision_manager.list_all() if r.status == status]
    else:
        items = decision_manager.list_all()

    if task_id is not None:
        items = [r for r in items if r.task_id == task_id]

    return DecisionListResponse.from_dataclass_list(items)


# ---------------------------------------------------------------------------
# GET /decisions/{request_id} — fetch one decision with context file contents
# ---------------------------------------------------------------------------

@router.get("/decisions/{request_id}", response_model=DecisionResponse)
async def get_decision(
    request_id: str,
    decision_manager: DecisionManager = Depends(get_decision_manager),
) -> DecisionResponse:
    """Return a single decision request by ID.

    GET /api/v1/decisions/{request_id}

    Context files listed in the decision are read from disk and their
    contents are embedded in the ``context_file_contents`` field so
    that remote UIs do not need filesystem access.  Files that cannot
    be read (missing, permissions) are silently omitted from the
    enrichment.

    Args:
        request_id: Unique decision request identifier (URL path
            parameter).
        decision_manager: Injected ``DecisionManager`` singleton.

    Returns:
        A ``DecisionResponse`` with the decision details and optional
        inline context file contents.

    Raises:
        HTTPException 404: If no decision with the given *request_id*
            exists.
    """
    req = decision_manager.get(request_id)
    if req is None:
        raise HTTPException(
            status_code=404,
            detail=f"Decision '{request_id}' not found.",
        )

    response = DecisionResponse.from_dataclass(req)

    # Enrich with context file contents so remote UIs are self-contained.
    context_contents: dict[str, str] = {}
    for file_path in req.context_files:
        try:
            contents = Path(file_path).read_text(encoding="utf-8")
            context_contents[file_path] = contents
        except (OSError, PermissionError):
            # Silently omit files that cannot be read.
            pass

    if context_contents:
        response.context_file_contents = context_contents

    return response


# ---------------------------------------------------------------------------
# POST /decisions/{request_id}/resolve — resolve a decision
# ---------------------------------------------------------------------------

@router.post("/decisions/{request_id}/resolve", response_model=ResolveResponse)
async def resolve_decision(
    request_id: str,
    body: ResolveDecisionRequest,
    decision_manager: DecisionManager = Depends(get_decision_manager),
) -> ResolveResponse:
    """Resolve a pending decision request.

    POST /api/v1/decisions/{request_id}/resolve

    The resolution is persisted to disk and a ``decision.resolved``
    event is published on the shared ``EventBus`` so waiting workers
    can unblock.

    Args:
        request_id: Unique decision request identifier (URL path
            parameter).
        body: Validated request body with the chosen option, optional
            rationale, and optional resolved_by identity.
        decision_manager: Injected ``DecisionManager`` singleton.

    Returns:
        A ``ResolveResponse`` confirming the resolution.

    Raises:
        HTTPException 400: If the decision has already been resolved
            or has expired.
        HTTPException 404: If no decision with the given *request_id*
            exists.
        HTTPException 409: If a concurrent modification prevented the
            resolution from being written.
    """
    existing = decision_manager.get(request_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Decision '{request_id}' not found.",
        )

    if existing.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Decision '{request_id}' cannot be resolved — "
                f"current status is '{existing.status}'."
            ),
        )

    resolved_by = body.resolved_by or "human"
    success = decision_manager.resolve(
        request_id=request_id,
        chosen_option=body.option,
        rationale=body.rationale,
        resolved_by=resolved_by,
    )

    if not success:
        # resolve() returns False if the request disappeared between our get()
        # check and the actual write — treat it as a 409 conflict.
        raise HTTPException(
            status_code=409,
            detail=f"Decision '{request_id}' could not be resolved (concurrent modification).",
        )

    # execution_resumed is optimistic — the bus event was published but we
    # don't verify a worker is listening.  Callers should poll /executions.
    return ResolveResponse(resolved=True, execution_resumed=False)
