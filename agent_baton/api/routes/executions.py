"""Execution lifecycle endpoints for the Agent Baton API.

POST   /executions                      — start a new execution
GET    /executions/{task_id}            — query current execution state
POST   /executions/{task_id}/record     — record a step result
POST   /executions/{task_id}/gate       — record a gate result
POST   /executions/{task_id}/complete   — finalise a completed execution
DELETE /executions/{task_id}            — cancel a running execution
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from agent_baton.api.deps import get_decision_manager, get_engine
from agent_baton.api.models.requests import (
    RecordGateRequest,
    RecordStepRequest,
    StartExecutionRequest,
)
from agent_baton.api.models.responses import ActionResponse, ExecutionResponse
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.runtime.decisions import DecisionManager
from agent_baton.models.execution import MachinePlan

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /executions — start execution
# ---------------------------------------------------------------------------

@router.post("/executions", status_code=201)
async def start_execution(
    req: StartExecutionRequest,
    engine: ExecutionEngine = Depends(get_engine),
    decision_manager: DecisionManager = Depends(get_decision_manager),
) -> dict[str, Any]:
    """Begin executing a plan.

    POST /api/v1/executions

    Accepts either a ``plan_id`` (loads from active engine state) or an
    inline ``plan`` dict.  Returns the initial execution state and the
    first batch of dispatchable actions that the caller should dispatch
    to subagents.

    Args:
        req: Validated request body with exactly one of ``plan_id`` or
            ``plan``.
        engine: Injected ``ExecutionEngine`` singleton.
        decision_manager: Injected ``DecisionManager`` for pending
            decision counts.

    Returns:
        A dict with ``execution`` (the ``ExecutionResponse``) and
        ``next_actions`` (list of ``ActionResponse`` dicts) (201 Created).

    Raises:
        HTTPException 400: If the inline ``plan`` dict is malformed or
            missing required fields.
        HTTPException 404: If ``plan_id`` is provided but no active plan
            matches it.
        HTTPException 500: If the engine fails to start or persist state.
    """
    # Resolve the MachinePlan from either source.
    if req.plan_id is not None:
        state = engine._load_state()  # noqa: SLF001
        if state is None or state.task_id != req.plan_id:
            raise HTTPException(
                status_code=404,
                detail=f"No active plan found with id '{req.plan_id}'.",
            )
        plan = state.plan
    else:
        # req.plan is guaranteed non-None here (validated by the request model).
        try:
            plan = MachinePlan.from_dict(req.plan)  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid plan dict: {exc}",
            ) from exc

    # Start the engine; returns the first action.
    try:
        first_action = engine.start(plan)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Gather all immediately dispatchable parallel actions.
    try:
        parallel_actions = engine.next_actions()
    except Exception:
        parallel_actions = [first_action]

    # Load state to build the response — start() saves it to disk.
    state = engine._load_state()  # noqa: SLF001
    if state is None:
        raise HTTPException(status_code=500, detail="Engine failed to persist state after start().")

    pending_count = _count_pending(decision_manager)
    execution = ExecutionResponse.from_dataclass(state, pending_decisions=pending_count)

    next_actions = (
        [ActionResponse.from_dataclass(a) for a in parallel_actions]
        if parallel_actions
        else [ActionResponse.from_dataclass(first_action)]
    )

    return {
        "execution": execution.model_dump(),
        "next_actions": [a.model_dump() for a in next_actions],
    }


# ---------------------------------------------------------------------------
# GET /executions/{task_id} — query state
# ---------------------------------------------------------------------------

@router.get("/executions/{task_id}", response_model=ExecutionResponse)
async def get_execution(
    task_id: str,
    engine: ExecutionEngine = Depends(get_engine),
    decision_manager: DecisionManager = Depends(get_decision_manager),
) -> ExecutionResponse:
    """Return the current execution state for a task.

    GET /api/v1/executions/{task_id}

    Args:
        task_id: The execution/task identifier (URL path parameter).
        engine: Injected ``ExecutionEngine`` singleton.
        decision_manager: Injected ``DecisionManager`` for pending
            decision counts.

    Returns:
        An ``ExecutionResponse`` with step results, gate results,
        and progress counters.

    Raises:
        HTTPException 404: If no active execution matches *task_id*, or
            the state file cannot be loaded.
    """
    status = engine.status()
    active_id = status.get("task_id")

    if active_id is None or active_id != task_id:
        raise HTTPException(
            status_code=404,
            detail=f"No active execution found with task_id '{task_id}'.",
        )

    state = engine._load_state()  # noqa: SLF001
    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"Execution state for '{task_id}' could not be loaded from disk.",
        )

    pending_count = _count_pending(decision_manager)
    return ExecutionResponse.from_dataclass(state, pending_decisions=pending_count)


# ---------------------------------------------------------------------------
# POST /executions/{task_id}/record — record step result
# ---------------------------------------------------------------------------

@router.post("/executions/{task_id}/record")
async def record_step(
    task_id: str,
    req: RecordStepRequest,
    engine: ExecutionEngine = Depends(get_engine),
) -> dict[str, Any]:
    """Record the outcome of a completed step and return the next actions.

    POST /api/v1/executions/{task_id}/record

    After a subagent finishes (or fails), the orchestrator calls this
    endpoint to persist the result and advance the execution state.
    The response includes the next batch of dispatchable actions.

    Args:
        task_id: The execution/task identifier (URL path parameter).
        req: Validated request body with step_id, agent name, status,
            and optional outcome metadata (summary, tokens, duration).
        engine: Injected ``ExecutionEngine`` singleton.

    Returns:
        A dict with ``recorded: true`` and ``next_actions`` (list of
        ``ActionResponse`` dicts).

    Raises:
        HTTPException 400: If the step_id or status is invalid.
        HTTPException 404: If no active execution matches *task_id*.
        HTTPException 500: If the engine encounters an internal error
            while recording.
    """
    _assert_active_task(engine, task_id)

    try:
        engine.record_step_result(
            step_id=req.step_id,
            agent_name=req.agent,
            status=req.status,
            outcome=req.output_summary or "",
            estimated_tokens=req.tokens or 0,
            duration_seconds=(req.duration_ms / 1000.0) if req.duration_ms else 0.0,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    next_actions = _collect_next_actions(engine)
    return {
        "recorded": True,
        "next_actions": [a.model_dump() for a in next_actions],
    }


# ---------------------------------------------------------------------------
# POST /executions/{task_id}/gate — record gate result
# ---------------------------------------------------------------------------

@router.post("/executions/{task_id}/gate")
async def record_gate(
    task_id: str,
    req: RecordGateRequest,
    engine: ExecutionEngine = Depends(get_engine),
) -> dict[str, Any]:
    """Record the outcome of a QA gate check and return the next actions.

    POST /api/v1/executions/{task_id}/gate

    After running a gate command (test suite, lint, build), the
    orchestrator calls this endpoint with the pass/fail result.  On
    pass, execution advances to the next phase.

    Args:
        task_id: The execution/task identifier (URL path parameter).
        req: Validated request body with phase_id, result
            (pass/fail/pass_with_notes), and optional notes.
        engine: Injected ``ExecutionEngine`` singleton.

    Returns:
        A dict with ``recorded: true`` and ``next_actions``.

    Raises:
        HTTPException 404: If no active execution matches *task_id*.
        HTTPException 500: If the engine encounters an internal error.
    """
    _assert_active_task(engine, task_id)

    try:
        engine.record_gate_result(
            phase_id=req.phase_id,
            passed=(req.result in ("pass", "pass_with_notes")),
            output=req.notes or "",
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    next_actions = _collect_next_actions(engine)
    return {
        "recorded": True,
        "next_actions": [a.model_dump() for a in next_actions],
    }


# ---------------------------------------------------------------------------
# POST /executions/{task_id}/complete — finalise execution
# ---------------------------------------------------------------------------

@router.post("/executions/{task_id}/complete")
async def complete_execution(
    task_id: str,
    engine: ExecutionEngine = Depends(get_engine),
) -> dict[str, Any]:
    """Finalise a completed execution and write trace, usage, and retrospective.

    POST /api/v1/executions/{task_id}/complete

    Triggers the engine's ``complete()`` method which writes the
    execution trace, usage log entry, and auto-generated retrospective.
    After this call the execution state transitions to ``complete``.

    Args:
        task_id: The execution/task identifier (URL path parameter).
        engine: Injected ``ExecutionEngine`` singleton.

    Returns:
        A dict with ``task_id``, ``status: "complete"``, and a
        ``summary`` string from the engine.

    Raises:
        HTTPException 404: If no active execution matches *task_id*.
        HTTPException 500: If the completion process fails.
    """
    _assert_active_task(engine, task_id)

    try:
        summary = engine.complete()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Completion failed: {exc}") from exc

    return {
        "task_id": task_id,
        "status": "complete",
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# DELETE /executions/{task_id} — cancel execution
# ---------------------------------------------------------------------------

@router.delete("/executions/{task_id}")
async def cancel_execution(
    task_id: str,
    engine: ExecutionEngine = Depends(get_engine),
) -> dict[str, Any]:
    """Cancel a running execution.

    DELETE /api/v1/executions/{task_id}

    DECISION: Cancellation is best-effort.  If the task is active, we record
    a synthetic failure via the persistence layer so the engine's state
    transitions to ``failed``.  We do not attempt to terminate any in-flight
    subagent processes -- that is the responsibility of the caller.

    Args:
        task_id: The execution/task identifier (URL path parameter).
        engine: Injected ``ExecutionEngine`` singleton.

    Returns:
        ``{"cancelled": true, "task_id": "<id>"}``

    Raises:
        HTTPException 404: If no active execution matches *task_id*.
    """
    status = engine.status()
    active_id = status.get("task_id")

    if active_id is None or active_id != task_id:
        raise HTTPException(
            status_code=404,
            detail=f"No active execution found with task_id '{task_id}'.",
        )

    state = engine._load_state()  # noqa: SLF001
    if state is not None and state.status == "running":
        state.status = "failed"
        engine._save_execution(state)  # noqa: SLF001

    return {"cancelled": True, "task_id": task_id}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _assert_active_task(engine: ExecutionEngine, task_id: str) -> None:
    """Raise 404 if the engine has no active state matching *task_id*.

    Args:
        engine: The ``ExecutionEngine`` to query.
        task_id: Expected active task identifier.

    Raises:
        HTTPException 404: If no active execution matches *task_id*.
    """
    status = engine.status()
    active_id = status.get("task_id")
    if active_id is None or active_id != task_id:
        raise HTTPException(
            status_code=404,
            detail=f"No active execution found with task_id '{task_id}'.",
        )


def _collect_next_actions(engine: ExecutionEngine) -> list[ActionResponse]:
    """Return the next batch of dispatchable actions (parallel where possible).

    Attempts ``engine.next_actions()`` first for parallel dispatch.
    Falls back to ``engine.next_action()`` (single) if the parallel
    method fails.  Returns an empty list if both fail.

    Args:
        engine: The ``ExecutionEngine`` to query.

    Returns:
        A list of ``ActionResponse`` objects representing the next
        actions the orchestrator should dispatch.
    """
    import logging
    _log = logging.getLogger(__name__)
    try:
        parallel = engine.next_actions()
        if parallel:
            return [ActionResponse.from_dataclass(a) for a in parallel]
    except Exception:
        _log.warning("next_actions() failed, falling back to next_action()", exc_info=True)
    try:
        single = engine.next_action()
        return [ActionResponse.from_dataclass(single)]
    except Exception:
        _log.warning("next_action() failed, returning empty actions", exc_info=True)
        return []


def _count_pending(decision_manager: DecisionManager) -> int:
    """Return the count of pending decisions, defaulting to 0 on error.

    Args:
        decision_manager: The ``DecisionManager`` to query.

    Returns:
        Number of pending (unresolved) decisions, or 0 if an error
        occurs while reading the decision store.
    """
    try:
        return len(decision_manager.pending())
    except Exception:
        return 0
