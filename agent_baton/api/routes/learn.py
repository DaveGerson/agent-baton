"""Learning automation endpoints for the Agent Baton API.

GET  /learn/issues                       — list issues with optional filters
GET  /learn/issues/{issue_id}            — get issue detail with full evidence
POST /learn/analyze                      — trigger analysis cycle
POST /learn/issues/{issue_id}/apply      — apply a fix for an issue
PATCH /learn/issues/{issue_id}           — update issue status
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from agent_baton.api.deps import get_learning_engine
from agent_baton.api.models.requests import (
    ApplyLearningFixRequest,
    UpdateLearningIssueRequest,
)
from agent_baton.api.models.responses import (
    ApplyLearningFixResponse,
    LearningAnalyzeResponse,
    LearningIssueDetailResponse,
    LearningIssueResponse,
)
from agent_baton.core.learn.engine import LearningEngine

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /learn/issues — list issues
# ---------------------------------------------------------------------------

@router.get("/learn/issues")
async def list_issues(
    status: Optional[str] = Query(
        default=None,
        description=(
            "Filter by lifecycle status: open, investigating, proposed, "
            "applied, resolved, or wontfix."
        ),
    ),
    issue_type: Optional[str] = Query(
        default=None,
        description=(
            "Filter by issue type: routing_mismatch, agent_degradation, "
            "knowledge_gap, roster_bloat, gate_mismatch, pattern_drift, "
            "or prompt_evolution."
        ),
    ),
    severity: Optional[str] = Query(
        default=None,
        description="Filter by severity: low, medium, high, or critical.",
    ),
    engine: LearningEngine = Depends(get_learning_engine),
) -> dict:
    """Return learning issues with optional status/type/severity filters.

    GET /api/v1/learn/issues

    When no filters are provided all issues are returned ordered by
    ``last_seen`` descending.  Combine filters to narrow the result set
    (all supplied filters are ANDed together).

    Args:
        status: Optional lifecycle status filter.
        issue_type: Optional issue category filter.
        severity: Optional severity filter.
        engine: Injected ``LearningEngine`` singleton.

    Returns:
        A dict with ``count`` and ``issues`` (list of
        ``LearningIssueResponse`` dicts).
    """
    ledger = engine._ledger  # noqa: SLF001
    issues = ledger.get_all_issues(
        status=status,
        issue_type=issue_type,
        severity=severity,
    )
    responses = [LearningIssueResponse.from_dataclass(i) for i in issues]
    return {
        "count": len(responses),
        "issues": [r.model_dump() for r in responses],
    }


# ---------------------------------------------------------------------------
# GET /learn/issues/{issue_id} — get issue detail with evidence
# ---------------------------------------------------------------------------

@router.get("/learn/issues/{issue_id}")
async def get_issue(
    issue_id: str,
    engine: LearningEngine = Depends(get_learning_engine),
) -> dict:
    """Return a single learning issue including the full evidence list.

    GET /api/v1/learn/issues/{issue_id}

    Args:
        issue_id: UUID of the learning issue.
        engine: Injected ``LearningEngine`` singleton.

    Returns:
        A ``LearningIssueDetailResponse`` dict with all fields and
        the complete evidence list.

    Raises:
        HTTPException 404: If no issue with *issue_id* exists.
    """
    ledger = engine._ledger  # noqa: SLF001
    issue = ledger.get_issue(issue_id)
    if issue is None:
        raise HTTPException(
            status_code=404,
            detail=f"Learning issue '{issue_id}' not found.",
        )
    return LearningIssueDetailResponse.from_dataclass(issue).model_dump()


# ---------------------------------------------------------------------------
# POST /learn/analyze — trigger analysis cycle
# ---------------------------------------------------------------------------

@router.post("/learn/analyze")
async def analyze(
    engine: LearningEngine = Depends(get_learning_engine),
) -> dict:
    """Trigger an analysis cycle over open issues.

    POST /api/v1/learn/analyze

    Reads all open issues, computes confidence against per-type
    thresholds, and promotes eligible issues to ``"proposed"`` status.
    Issues that cross the auto-apply threshold are flagged in the
    response as candidates.

    Args:
        engine: Injected ``LearningEngine`` singleton.

    Returns:
        A ``LearningAnalyzeResponse`` dict with ``candidates`` and
        ``proposed_count``.
    """
    candidates = engine.analyze()
    responses = [LearningIssueResponse.from_dataclass(c) for c in candidates]
    proposed_count = sum(1 for r in responses if r.status == "proposed")
    result = LearningAnalyzeResponse(
        candidates=responses,
        proposed_count=proposed_count,
    )
    return result.model_dump()


# ---------------------------------------------------------------------------
# POST /learn/issues/{issue_id}/apply — apply a fix
# ---------------------------------------------------------------------------

@router.post("/learn/issues/{issue_id}/apply")
async def apply_fix(
    issue_id: str,
    req: ApplyLearningFixRequest,
    engine: LearningEngine = Depends(get_learning_engine),
) -> dict:
    """Apply the type-specific fix for a learning issue.

    POST /api/v1/learn/issues/{issue_id}/apply

    Dispatches to the appropriate resolver (routing, degradation,
    gate, knowledge, roster) and marks the issue as ``"applied"`` in
    the ledger.

    Args:
        issue_id: UUID of the issue to fix.
        req: Request body with ``resolution_type`` (auto, human, or
            interview).
        engine: Injected ``LearningEngine`` singleton.

    Returns:
        An ``ApplyLearningFixResponse`` dict with the resolution
        description and updated status.

    Raises:
        HTTPException 404: If no issue with *issue_id* exists.
        HTTPException 400: If the issue type does not support auto-apply.
    """
    try:
        resolution = engine.apply(issue_id, resolution_type=req.resolution_type)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Apply failed: {exc}",
        ) from exc

    result = ApplyLearningFixResponse(
        issue_id=issue_id,
        resolution=resolution,
        status="applied",
    )
    return result.model_dump()


# ---------------------------------------------------------------------------
# PATCH /learn/issues/{issue_id} — update issue status
# ---------------------------------------------------------------------------

@router.patch("/learn/issues/{issue_id}")
async def update_issue(
    issue_id: str,
    req: UpdateLearningIssueRequest,
    engine: LearningEngine = Depends(get_learning_engine),
) -> dict:
    """Update the lifecycle status of a learning issue.

    PATCH /api/v1/learn/issues/{issue_id}

    Writes the provided fields to the ledger via ``update_status()``.
    Only non-None values in the request body are applied.

    Args:
        issue_id: UUID of the issue to update.
        req: Request body with ``status`` (required) and optional
            ``resolution``, ``resolution_type``, and ``proposed_fix``.
        engine: Injected ``LearningEngine`` singleton.

    Returns:
        The updated ``LearningIssueResponse`` dict.

    Raises:
        HTTPException 404: If no issue with *issue_id* exists, or if
            ``update_status()`` found no matching row.
        HTTPException 400: If the supplied status value is not valid.
    """
    from agent_baton.models.learning import VALID_STATUSES

    if req.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid status '{req.status}'. "
                f"Valid values: {sorted(VALID_STATUSES)}"
            ),
        )

    ledger = engine._ledger  # noqa: SLF001

    # Verify the issue exists before attempting the update.
    existing = ledger.get_issue(issue_id)
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Learning issue '{issue_id}' not found.",
        )

    updated = ledger.update_status(
        issue_id,
        status=req.status,
        resolution=req.resolution,
        resolution_type=req.resolution_type,
        proposed_fix=req.proposed_fix,
    )
    if not updated:
        raise HTTPException(
            status_code=404,
            detail=f"Learning issue '{issue_id}' could not be updated.",
        )

    refreshed = ledger.get_issue(issue_id)
    if refreshed is None:
        raise HTTPException(
            status_code=404,
            detail=f"Learning issue '{issue_id}' not found after update.",
        )
    return LearningIssueResponse.from_dataclass(refreshed).model_dump()
