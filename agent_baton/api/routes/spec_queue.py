"""Spec Queue API routes for the Spec Federation MVP (007 Phase I).

POST   /pmo/specs                         — Submit a spec draft (201, auto-enrich async)
GET    /pmo/specs?status=&submitted_by=   — List spec drafts
GET    /pmo/specs/{id}                    — Get a single spec draft (404 if missing)
POST   /pmo/specs/{id}/enrich             — Synchronous re-enrich
POST   /pmo/specs/{id}/approve            — Approve (409 unless enriched; 403 self-approval in team mode)
POST   /pmo/specs/{id}/bounce             — Bounce with feedback (422 if feedback empty)
POST   /pmo/specs/{id}/fire               — Fire into plan generation (409 unless approved; 202)
POST   /pmo/specs/import                  — Import from GitHub/ADO (501 if unconfigured; 502 on error)
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agent_baton.api.models.requests import (
    BounceSpecDraftRequest,
    FireSpecDraftRequest,
    ImportSpecDraftRequest,
    SubmitSpecDraftRequest,
)
from agent_baton.api.models.responses import (
    FireSpecDraftResponse,
    SpecDraftResponse,
)
from agent_baton.api.planner_errors import plan_quality_error_detail
from agent_baton.core.engine.planning.stages.validation import PlanQualityError
from agent_baton.core.federate.spec_draft_store import SpecDraftStore
from agent_baton.models.spec_draft import ReviewData

logger = logging.getLogger(__name__)

router = APIRouter()

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"


def _get_store(db_path: Optional[Path] = None) -> SpecDraftStore:
    """Return a SpecDraftStore, honouring BATON_DB_PATH env var for tests."""
    _override = os.environ.get("BATON_SPEC_DRAFT_DB", "")
    if _override:
        return SpecDraftStore(db_path=Path(_override))
    if db_path is not None:
        return SpecDraftStore(db_path=db_path)
    return SpecDraftStore()


def _store_dep() -> SpecDraftStore:
    """FastAPI dependency: return the shared SpecDraftStore."""
    return _get_store()


# ---------------------------------------------------------------------------
# POST /pmo/specs  — submit
# ---------------------------------------------------------------------------

@router.post("/pmo/specs", response_model=SpecDraftResponse, status_code=201)
async def submit_spec_draft(
    req: SubmitSpecDraftRequest,
    request: Request,
    store: SpecDraftStore = Depends(_store_dep),
) -> SpecDraftResponse:
    """Submit a new spec draft and schedule async enrichment.

    POST /api/v1/pmo/specs

    Creates the draft in ``submitted`` status and immediately schedules
    auto-enrichment in the background (via ``run_in_executor``).

    Args:
        req: Validated request body.
        request: FastAPI request (for user identity).
        store: Injected SpecDraftStore.

    Returns:
        The created ``SpecDraftResponse`` (201 Created).
    """
    user_id: str = getattr(request.state, "user_id", "local-user")
    draft = store.create(
        title=req.title,
        body=req.body,
        source=req.source,
        source_ref=req.source_ref,
        submitted_by=user_id,
    )

    # Schedule async enrichment (best-effort — never blocks response)
    try:
        loop = asyncio.get_event_loop()
        from agent_baton.core.federate.enrich import _run_enrichment as _enrich_worker
        loop.run_in_executor(None, _enrich_worker, draft.id, _get_store())
    except Exception:  # noqa: BLE001
        pass

    return SpecDraftResponse.from_draft(draft)


# ---------------------------------------------------------------------------
# GET /pmo/specs  — list
# ---------------------------------------------------------------------------

@router.get("/pmo/specs", response_model=list[SpecDraftResponse])
async def list_spec_drafts(
    status: Optional[str] = None,
    submitted_by: Optional[str] = None,
    store: SpecDraftStore = Depends(_store_dep),
) -> list[SpecDraftResponse]:
    """List spec drafts with optional filtering.

    GET /api/v1/pmo/specs

    Args:
        status: Filter by lifecycle status.
        submitted_by: Filter by submitter user ID.
        store: Injected SpecDraftStore.

    Returns:
        List of ``SpecDraftResponse`` instances ordered newest-first.
    """
    drafts = store.list(status=status, submitted_by=submitted_by)
    return [SpecDraftResponse.from_draft(d) for d in drafts]


# ---------------------------------------------------------------------------
# GET /pmo/specs/{id}  — get single
# ---------------------------------------------------------------------------

@router.get("/pmo/specs/{spec_id}", response_model=SpecDraftResponse)
async def get_spec_draft(
    spec_id: str,
    store: SpecDraftStore = Depends(_store_dep),
) -> SpecDraftResponse:
    """Get a single spec draft by ID.

    GET /api/v1/pmo/specs/{id}

    Args:
        spec_id: The spec draft UUID.
        store: Injected SpecDraftStore.

    Returns:
        The ``SpecDraftResponse``.

    Raises:
        HTTPException 404: If the spec draft does not exist.
    """
    draft = store.get(spec_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"Spec draft '{spec_id}' not found.")
    return SpecDraftResponse.from_draft(draft)


# ---------------------------------------------------------------------------
# POST /pmo/specs/{id}/enrich  — sync re-enrich
# ---------------------------------------------------------------------------

@router.post("/pmo/specs/{spec_id}/enrich", response_model=SpecDraftResponse)
async def enrich_spec_draft(
    spec_id: str,
    store: SpecDraftStore = Depends(_store_dep),
) -> SpecDraftResponse:
    """Synchronously re-enrich a spec draft.

    POST /api/v1/pmo/specs/{id}/enrich

    Runs enrichment in the request thread (no executor).  Suitable for
    manual re-runs from the UI.

    Args:
        spec_id: The spec draft UUID.
        store: Injected SpecDraftStore.

    Returns:
        The updated ``SpecDraftResponse``.

    Raises:
        HTTPException 404: If the spec draft does not exist.
    """
    draft = store.get(spec_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"Spec draft '{spec_id}' not found.")

    loop = asyncio.get_event_loop()
    from agent_baton.core.federate.enrich import enrich
    enrichment = await loop.run_in_executor(None, enrich, draft.title, draft.body)
    updated = store.update_enrichment(spec_id, enrichment)
    return SpecDraftResponse.from_draft(updated)


# ---------------------------------------------------------------------------
# POST /pmo/specs/{id}/approve  — approve
# ---------------------------------------------------------------------------

@router.post("/pmo/specs/{spec_id}/approve", response_model=SpecDraftResponse)
async def approve_spec_draft(
    spec_id: str,
    request: Request,
    store: SpecDraftStore = Depends(_store_dep),
) -> SpecDraftResponse:
    """Approve an enriched spec draft.

    POST /api/v1/pmo/specs/{id}/approve

    Args:
        spec_id: The spec draft UUID.
        request: FastAPI request (for user identity + approval mode).
        store: Injected SpecDraftStore.

    Returns:
        The updated ``SpecDraftResponse``.

    Raises:
        HTTPException 404: If the spec draft does not exist.
        HTTPException 409: If the spec draft is not in ``enriched`` status.
        HTTPException 403: If the actor is the same as the submitter in
            ``team`` approval mode.
    """
    draft = store.get(spec_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"Spec draft '{spec_id}' not found.")
    if draft.status != "enriched":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot approve spec draft in status '{draft.status}'; must be 'enriched'.",
        )

    actor: str = getattr(request.state, "user_id", "local-user")
    approval_mode: str = getattr(request.state, "approval_mode", "local")
    if approval_mode == "team" and actor == draft.submitted_by:
        raise HTTPException(
            status_code=403,
            detail=(
                f"In team approval mode the approver must differ from the submitter "
                f"(both are '{actor}')."
            ),
        )

    review = ReviewData(action="approved", actor=actor)
    updated = store.update_status(spec_id, "approved", review=review)
    return SpecDraftResponse.from_draft(updated)


# ---------------------------------------------------------------------------
# POST /pmo/specs/{id}/bounce  — bounce
# ---------------------------------------------------------------------------

@router.post("/pmo/specs/{spec_id}/bounce", response_model=SpecDraftResponse)
async def bounce_spec_draft(
    spec_id: str,
    req: BounceSpecDraftRequest,
    request: Request,
    store: SpecDraftStore = Depends(_store_dep),
) -> SpecDraftResponse:
    """Bounce an enriched spec draft back to the submitter with feedback.

    POST /api/v1/pmo/specs/{id}/bounce

    Args:
        spec_id: The spec draft UUID.
        req: Validated request body (feedback required).
        request: FastAPI request (for user identity).
        store: Injected SpecDraftStore.

    Returns:
        The updated ``SpecDraftResponse``.

    Raises:
        HTTPException 404: If the spec draft does not exist.
        HTTPException 409: If the spec draft is not in ``enriched`` status.
        HTTPException 422: If feedback is empty (enforced by Pydantic min_length=1).
    """
    draft = store.get(spec_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"Spec draft '{spec_id}' not found.")
    if draft.status != "enriched":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot bounce spec draft in status '{draft.status}'; must be 'enriched'.",
        )

    actor: str = getattr(request.state, "user_id", "local-user")
    review = ReviewData(action="bounced", actor=actor, feedback=req.feedback)
    updated = store.update_status(spec_id, "bounced", review=review)
    return SpecDraftResponse.from_draft(updated)


# ---------------------------------------------------------------------------
# POST /pmo/specs/{id}/fire  — fire into plan generation
# ---------------------------------------------------------------------------

@router.post("/pmo/specs/{spec_id}/fire", response_model=FireSpecDraftResponse, status_code=202)
async def fire_spec_draft(
    spec_id: str,
    req: FireSpecDraftRequest,
    store: SpecDraftStore = Depends(_store_dep),
) -> FireSpecDraftResponse:
    """Fire an approved spec draft into plan generation.

    POST /api/v1/pmo/specs/{id}/fire

    Generates a plan via ForgeSession (mirroring the ``POST /pmo/forge/plan``
    flow) and records the resulting task_id.

    Args:
        spec_id: The spec draft UUID.
        req: Validated request body (project_id required).
        store: Injected SpecDraftStore.

    Returns:
        A ``FireSpecDraftResponse`` with 202 Accepted.

    Raises:
        HTTPException 404: If the spec draft does not exist.
        HTTPException 409: If the spec draft is not in ``approved`` status.
        HTTPException 500: If plan generation fails.
    """
    draft = store.get(spec_id)
    if draft is None:
        raise HTTPException(status_code=404, detail=f"Spec draft '{spec_id}' not found.")
    if draft.status != "approved":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot fire spec draft in status '{draft.status}'; must be 'approved'.",
        )

    # Mirror the forge_plan flow from pmo.py
    try:
        from agent_baton.api.deps import get_forge_session, get_pmo_store
        forge = get_forge_session()
        pmo_store = get_pmo_store()

        project = pmo_store.get_project(req.project_id)
        if project is None:
            raise HTTPException(
                status_code=404,
                detail=f"Project '{req.project_id}' not found.",
            )

        loop = asyncio.get_event_loop()
        plan = await loop.run_in_executor(
            None,
            lambda: forge.create_plan(
                description=draft.title + ("\n\n" + draft.body if draft.body else ""),
                program="",
                project_id=req.project_id,
            ),
        )
    except HTTPException:
        raise
    except PlanQualityError as exc:
        raise HTTPException(
            status_code=422,
            detail=plan_quality_error_detail(exc),
        ) from exc
    except Exception as exc:
        logger.error("fire_spec_draft: plan generation failed for %s: %s", spec_id, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Plan generation failed: {exc}",
        ) from exc

    updated = store.set_task_id(spec_id, plan.task_id)
    return FireSpecDraftResponse(
        spec_id=spec_id,
        task_id=plan.task_id,
        status="fired",
    )


# ---------------------------------------------------------------------------
# POST /pmo/specs/import  — import from external source
# ---------------------------------------------------------------------------

@router.post("/pmo/specs/import", response_model=SpecDraftResponse, status_code=201)
async def import_spec_draft(
    req: ImportSpecDraftRequest,
    request: Request,
    store: SpecDraftStore = Depends(_store_dep),
) -> SpecDraftResponse:
    """Import a spec draft from GitHub Issues or Azure DevOps.

    POST /api/v1/pmo/specs/import

    Args:
        req: Validated request body.
        request: FastAPI request (for user identity).
        store: Injected SpecDraftStore.

    Returns:
        The created ``SpecDraftResponse`` (201 Created).

    Raises:
        HTTPException 501: When the importer requires configuration that is absent.
        HTTPException 502: When the remote API call fails.
    """
    from agent_baton.core.federate.importers import get_importer

    try:
        importer = get_importer(req.source, owner=req.owner, repo=req.repo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        imported = importer.fetch(req.ref)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Upstream source returned an error: {exc}",
        ) from exc

    user_id: str = getattr(request.state, "user_id", "local-user")
    draft = store.create(
        title=imported.title,
        body=imported.body,
        source=req.source,
        source_ref=imported.source_ref,
        submitted_by=user_id,
    )

    # Schedule async enrichment
    try:
        loop = asyncio.get_event_loop()
        from agent_baton.core.federate.enrich import _run_enrichment as _enrich_worker
        loop.run_in_executor(None, _enrich_worker, draft.id, _get_store())
    except Exception:  # noqa: BLE001
        pass

    return SpecDraftResponse.from_draft(draft)
