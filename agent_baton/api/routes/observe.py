"""Observability endpoints for the Agent Baton API.

GET /dashboard          — pre-rendered usage dashboard markdown.
GET /traces/{task_id}   — structured trace for a completed task.
GET /usage              — JSONL usage records with optional filtering.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from agent_baton.api.deps import get_dashboard, get_trace_recorder, get_usage_logger
from agent_baton.api.models.responses import DashboardResponse, TraceResponse, UsageResponse
from agent_baton.core.observe.dashboard import DashboardGenerator
from agent_baton.core.observe.trace import TraceRecorder
from agent_baton.core.observe.usage import UsageLogger

router = APIRouter()


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard_view(
    dashboard: DashboardGenerator = Depends(get_dashboard),
) -> DashboardResponse:
    """Return the pre-rendered usage dashboard as markdown.

    GET /api/v1/dashboard

    Delegates entirely to ``DashboardGenerator.generate()``.  The
    ``metrics`` dict is left empty -- all structured data is embedded
    in the markdown for now.  A future work package can expose
    structured metrics separately.

    Args:
        dashboard: Injected ``DashboardGenerator`` singleton.

    Returns:
        A ``DashboardResponse`` with ``dashboard_markdown`` containing
        the rendered markdown and an empty ``metrics`` dict.

    Raises:
        HTTPException 500: If the dashboard generator encounters an
            error (e.g. corrupt usage log).
    """
    try:
        markdown = dashboard.generate()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Dashboard generation failed: {exc}") from exc

    return DashboardResponse(dashboard_markdown=markdown, metrics={})


@router.get("/traces/{task_id}", response_model=TraceResponse)
async def get_trace(
    task_id: str,
    trace_recorder: TraceRecorder = Depends(get_trace_recorder),
) -> TraceResponse:
    """Return the structured execution trace for a task.

    GET /api/v1/traces/{task_id}

    The trace contains a chronologically ordered list of events
    (agent dispatches, gate checks, completions) plus a snapshot
    of the plan as it existed at execution start.

    Args:
        task_id: The task identifier to load the trace for (URL path
            parameter).
        trace_recorder: Injected ``TraceRecorder`` singleton.

    Returns:
        A ``TraceResponse`` with the plan snapshot, ordered events,
        timestamps, and final outcome.

    Raises:
        HTTPException 404: If no trace file exists for *task_id*.
    """
    trace = trace_recorder.load_trace(task_id)
    if trace is None:
        raise HTTPException(
            status_code=404,
            detail=f"No trace found for task_id '{task_id}'.",
        )
    return TraceResponse.from_dataclass(trace)


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    since: Optional[str] = Query(
        default=None,
        description="ISO 8601 timestamp. Only return records at or after this time.",
    ),
    agent: Optional[str] = Query(
        default=None,
        description="Filter records to those that include this agent name.",
    ),
    usage_logger: UsageLogger = Depends(get_usage_logger),
) -> UsageResponse:
    """Return usage records with optional filtering.

    GET /api/v1/usage

    Query parameters:

    - ``since``: ISO 8601 timestamp -- only records whose ``timestamp``
      is lexicographically >= this value are returned.  ISO 8601 strings
      sort correctly as strings, so no date parsing is needed.
    - ``agent``: return only records where at least one ``agents_used``
      entry has the given name.

    DECISION: Filtering is done in-memory after reading all records.  The
    JSONL log is append-only and unindexed, so there is no cheaper path.
    For large logs a future work package should add cursor-based pagination.

    Args:
        since: Optional ISO 8601 lower-bound timestamp filter.
        agent: Optional agent name filter.
        usage_logger: Injected ``UsageLogger`` singleton.

    Returns:
        A ``UsageResponse`` containing filtered ``records`` and an
        aggregated ``summary`` dict with ``total_tasks``,
        ``total_tokens``, ``total_agents``, and ``outcome_counts``.

    Raises:
        HTTPException 500: If the usage log file cannot be read.
    """
    try:
        records = usage_logger.read_all()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read usage log: {exc}") from exc

    # Apply since filter (string comparison — valid for ISO 8601 timestamps).
    if since is not None:
        records = [r for r in records if r.timestamp >= since]

    # Apply agent name filter.
    if agent is not None:
        records = [
            r for r in records
            if any(a.name == agent for a in r.agents_used)
        ]

    # Build summary from the filtered record set.
    summary: dict[str, Any] = _build_summary(records)

    from agent_baton.api.models.responses import TaskUsageResponse
    response_records = [TaskUsageResponse.from_dataclass(r) for r in records]

    return UsageResponse(records=response_records, summary=summary)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_summary(records: list) -> dict[str, Any]:
    """Build a lightweight summary dict from the filtered record list.

    Args:
        records: List of ``TaskUsageRecord`` dataclasses to aggregate.

    Returns:
        A dict with keys ``total_tasks``, ``total_tokens``,
        ``total_agents``, and ``outcome_counts`` (a dict mapping
        outcome strings to their frequency).
    """
    total_tasks = len(records)
    if total_tasks == 0:
        return {
            "total_tasks": 0,
            "total_tokens": 0,
            "total_agents": 0,
            "outcome_counts": {},
        }

    total_tokens = sum(
        a.estimated_tokens for r in records for a in r.agents_used
    )
    total_agents = sum(len(r.agents_used) for r in records)

    outcome_counts: dict[str, int] = {}
    for r in records:
        if r.outcome:
            outcome_counts[r.outcome] = outcome_counts.get(r.outcome, 0) + 1

    return {
        "total_tasks": total_tasks,
        "total_tokens": total_tokens,
        "total_agents": total_agents,
        "outcome_counts": outcome_counts,
    }
