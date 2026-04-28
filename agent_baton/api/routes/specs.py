"""FastAPI routes for the Spec entity (F0.1).

Endpoints
---------
GET  /api/v1/specs                List specs (filterable by state/project/author)
POST /api/v1/specs                Create a new spec
GET  /api/v1/specs/{spec_id}      Get a single spec
POST /api/v1/specs/{spec_id}/approve   Approve a spec
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()

_DEFAULT_CENTRAL_DB = Path.home() / ".baton" / "central.db"


def _store():
    from agent_baton.core.specs.store import SpecStore
    return SpecStore()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class SpecCreateRequest(BaseModel):
    title: str
    content: str = ""
    task_type: str = ""
    template_id: str = "feature"
    author_id: str = "local-user"
    project_id: str = "default"


class SpecApproveRequest(BaseModel):
    actor: str = "local-user"


class SpecResponse(BaseModel):
    spec_id: str
    project_id: str
    author_id: str
    task_type: str
    template_id: str
    title: str
    state: str
    content: str
    content_hash: str
    score_json: str
    created_at: str
    updated_at: str
    approved_at: str
    approved_by: str
    linked_plan_ids: list[str]

    @classmethod
    def from_spec(cls, spec) -> "SpecResponse":
        return cls(**spec.to_dict())


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/specs", tags=["specs"])
def list_specs(
    project_id: str | None = Query(None),
    state: str | None = Query(None),
    author_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    """List specs with optional filters."""
    store = _store()
    specs = store.list(
        project_id=project_id,
        state=state,
        author_id=author_id,
        limit=limit,
    )
    return [s.to_dict() for s in specs]


@router.post("/specs", tags=["specs"], status_code=201)
def create_spec(req: SpecCreateRequest) -> dict[str, Any]:
    """Create a new Spec in draft state."""
    store = _store()
    spec = store.create(
        title=req.title,
        content=req.content,
        task_type=req.task_type,
        template_id=req.template_id,
        author_id=req.author_id,
        project_id=req.project_id,
    )
    return spec.to_dict()


@router.get("/specs/{spec_id}", tags=["specs"])
def get_spec(spec_id: str) -> dict[str, Any]:
    """Get a single spec by ID."""
    store = _store()
    spec = store.get(spec_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"Spec not found: {spec_id}")
    return spec.to_dict()


@router.post("/specs/{spec_id}/approve", tags=["specs"])
def approve_spec(spec_id: str, req: SpecApproveRequest) -> dict[str, Any]:
    """Approve a spec (transition state to 'approved')."""
    store = _store()
    try:
        spec = store.update_state(spec_id, "approved", actor=req.actor)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return spec.to_dict()
