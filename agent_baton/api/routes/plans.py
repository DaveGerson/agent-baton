"""Plan management endpoints for the Agent Baton API.

POST /plans            — generate a new execution plan via IntelligentPlanner.
GET  /plans/{plan_id}  — retrieve an existing plan from the active engine state.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from agent_baton.api.deps import get_engine, get_planner
from agent_baton.api.models.requests import CreatePlanRequest
from agent_baton.api.models.responses import PlanResponse
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.planner import IntelligentPlanner

router = APIRouter()


@router.post("/plans", response_model=PlanResponse, status_code=201)
async def create_plan(
    req: CreatePlanRequest,
    planner: IntelligentPlanner = Depends(get_planner),
) -> PlanResponse:
    """Generate a new execution plan from a natural-language description.

    POST /api/v1/plans

    The planner consults historical patterns, agent scores, and budget
    recommendations before producing the plan -- all core logic lives in
    ``IntelligentPlanner.create_plan()``.

    Args:
        req: Validated request body containing the task description and
            optional overrides (task_type, agents, project_path).
        planner: Injected ``IntelligentPlanner`` singleton.

    Returns:
        A ``PlanResponse`` with the generated plan (201 Created).

    Raises:
        HTTPException 400: If the request is semantically invalid (e.g.
            empty description after validation, contradictory agent list).
        HTTPException 500: If the planner encounters an unexpected internal
            error during plan generation.
    """
    try:
        project_path = Path(req.project_path) if req.project_path else None
        plan = planner.create_plan(
            task_summary=req.description,
            task_type=req.task_type,
            project_root=project_path,
            agents=req.agents,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Planning failed: {exc}") from exc

    return PlanResponse.from_dataclass(plan)


@router.get("/plans/{plan_id}", response_model=PlanResponse)
async def get_plan(
    plan_id: str,
    engine: ExecutionEngine = Depends(get_engine),
) -> PlanResponse:
    """Retrieve a plan by ID from the engine's active execution state.

    GET /api/v1/plans/{plan_id}

    DECISION: Plans are not stored independently -- they live inside the
    ``ExecutionState`` written by the engine.  We load the active state and
    check whether its ``task_id`` matches *plan_id*.  A 404 is returned if
    there is no active state or the IDs don't match.  A separate plan
    store is out of scope for this work package.

    Args:
        plan_id: The task/plan identifier to look up (URL path parameter).
        engine: Injected ``ExecutionEngine`` singleton.

    Returns:
        A ``PlanResponse`` with the full plan structure.

    Raises:
        HTTPException 404: If no active plan matches *plan_id*, or if the
            execution state file cannot be loaded from disk.
    """
    status = engine.status()
    active_task_id = status.get("task_id")

    if active_task_id is None or active_task_id != plan_id:
        raise HTTPException(
            status_code=404,
            detail=f"No active plan found with id '{plan_id}'.",
        )

    # Load the full state to access the plan dataclass.
    state = engine._load_state()  # noqa: SLF001 — private but intended for this use
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"Execution state for plan '{plan_id}' could not be loaded from disk.",
        )

    return PlanResponse.from_dataclass(state.plan)
