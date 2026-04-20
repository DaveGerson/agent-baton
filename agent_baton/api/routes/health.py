"""Health and readiness endpoints for the Agent Baton API.

GET /health  — liveness probe (always returns 200 while the process is up).
GET /ready   — readiness probe (checks SQLite, engine status, and directory writability).
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from fastapi import APIRouter, Depends

from agent_baton.api.deps import get_decision_manager, get_engine, get_team_context_root
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
    team_context_root: Path = Depends(get_team_context_root),
) -> ReadyResponse:
    """Readiness probe — checks SQLite accessibility, engine health, and dir writability."""
    status = engine.status()
    daemon_running = status.get("status") not in ("no_active_execution",)

    try:
        pending = decision_manager.pending()
        pending_count = len(pending)
    except Exception:
        pending_count = 0

    # --- Check 1: SQLite accessible ---
    db_path = team_context_root / "baton.db"
    try:
        with sqlite3.connect(str(db_path)) as _conn:
            _conn.execute("SELECT 1")
    except Exception as exc:
        return ReadyResponse(
            ready=False,
            daemon_running=daemon_running,
            pending_decisions=pending_count,
            reason=f"SQLite unavailable: {exc}",
        )

    # --- Check 2: Engine not in failed state ---
    if status.get("status") == "failed":
        return ReadyResponse(
            ready=False,
            daemon_running=daemon_running,
            pending_decisions=pending_count,
            reason="Engine status is 'failed'",
        )

    # --- Check 3: State directory writable ---
    if not os.access(str(team_context_root), os.W_OK):
        return ReadyResponse(
            ready=False,
            daemon_running=daemon_running,
            pending_decisions=pending_count,
            reason=f"State directory not writable: {team_context_root}",
        )

    return ReadyResponse(
        ready=True,
        daemon_running=daemon_running,
        pending_decisions=pending_count,
    )
