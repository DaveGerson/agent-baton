"""Plan visualization API routes.

GET /viz/{task_id}       -- serve plan visualization as a self-contained HTML page
GET /viz/{task_id}/data  -- return PlanSnapshot as JSON for custom rendering
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from agent_baton.cli._context import resolve_context_root

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /viz/{task_id} — HTML visualization
# ---------------------------------------------------------------------------

@router.get("/viz/{task_id}", response_class=HTMLResponse, tags=["viz"])
async def viz_html(task_id: str) -> HTMLResponse:
    """Serve plan visualization as a self-contained HTML page.

    Attempts to load execution state first (for running/completed
    executions), then falls back to ``plan.json`` (for unstarted plans).

    Args:
        task_id: Execution or plan identifier.

    Returns:
        A complete HTML page with embedded CSS/JS and plan data.

    Raises:
        HTTPException 404: If no execution state or plan.json is found.
    """
    from agent_baton.core.engine.persistence import StatePersistence
    from agent_baton.models.execution import MachinePlan
    from agent_baton.visualize.snapshot import PlanSnapshot
    from agent_baton.visualize.web_renderer import render_html

    context_root = resolve_context_root()

    # Try execution state first — has richer runtime data.
    try:
        sp = StatePersistence(context_root, task_id=task_id)
        state = sp.load()
        if state is not None:
            snapshot = PlanSnapshot.from_state(state)
            return HTMLResponse(content=render_html(snapshot))
    except Exception:
        pass

    # Fall back to plan.json.
    plan_path = context_root / "plan.json"
    if plan_path.exists():
        data = json.loads(plan_path.read_text(encoding="utf-8"))
        plan = MachinePlan.from_dict(data)
        # Only serve if task_id matches (or no specific task_id in plan).
        if plan.task_id == task_id or task_id == "latest":
            snapshot = PlanSnapshot.from_plan(plan)
            return HTMLResponse(content=render_html(snapshot))

    raise HTTPException(404, f"No execution or plan found for task_id={task_id}")


# ---------------------------------------------------------------------------
# GET /viz/{task_id}/data — JSON snapshot
# ---------------------------------------------------------------------------

@router.get("/viz/{task_id}/data", tags=["viz"])
async def viz_data(task_id: str) -> dict:
    """Return PlanSnapshot as JSON for custom rendering.

    Args:
        task_id: Execution identifier.

    Returns:
        The full PlanSnapshot dictionary.

    Raises:
        HTTPException 404: If no execution state is found.
    """
    from agent_baton.core.engine.persistence import StatePersistence
    from agent_baton.visualize.snapshot import PlanSnapshot

    context_root = resolve_context_root()
    sp = StatePersistence(context_root, task_id=task_id)
    state = sp.load()
    if state is None:
        raise HTTPException(404, f"No execution found for task_id={task_id}")
    snapshot = PlanSnapshot.from_state(state)
    return snapshot.to_dict()


