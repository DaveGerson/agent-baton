"""Health and readiness endpoints for the Agent Baton API.

GET /health  — liveness probe (always returns 200 while the process is up).
GET /ready   — readiness probe (checks whether the engine has an active state).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from agent_baton.api.deps import get_decision_manager, get_engine
from agent_baton.api.models.responses import HealthResponse, ReadyResponse
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.runtime.decisions import DecisionManager

try:
    from agent_baton import __version__ as _VERSION
except ImportError:
    _VERSION = "0.1.0"

# DECISION: Module-level constant captures startup time so that uptime is
# measured from when this module is first imported (i.e. server startup),
# not from when the first /health request arrives.
_START_TIME: float = time.time()

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health(engine: ExecutionEngine = Depends(get_engine)) -> HealthResponse:  # noqa: ARG001
    """Liveness probe — returns 200 while the server process is running."""
    return HealthResponse(
        status="healthy",
        version=_VERSION,
        uptime_seconds=time.time() - _START_TIME,
    )


@router.get("/ready", response_model=ReadyResponse)
async def ready(
    engine: ExecutionEngine = Depends(get_engine),
    decision_manager: DecisionManager = Depends(get_decision_manager),
) -> ReadyResponse:
    """Readiness probe — reports whether an active execution state exists."""
    status = engine.status()
    daemon_running = status.get("status") not in ("no_active_execution",)

    try:
        pending = decision_manager.pending()
        pending_count = len(pending)
    except Exception:
        pending_count = 0

    return ReadyResponse(
        ready=True,
        daemon_running=daemon_running,
        pending_decisions=pending_count,
    )
