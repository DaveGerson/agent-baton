"""NOC (Network Operations Centre) aggregate cross-project endpoints.

All endpoints query central.db via CentralStore.  When central.db does not
exist the endpoints return empty / zero-filled results rather than raising
errors — this lets the NOC dashboard load cleanly on a fresh install.

Endpoints
---------
GET /noc/projects
    List all known projects with summary stats (task count, last_active).

GET /noc/aggregate/usage
    Cross-project token usage rollup (total tokens per project, grand total).

GET /noc/aggregate/incidents
    Cross-project warning-bead count per project.

GET /noc/aggregate/throughput
    Tasks completed per project per day for the last 7 days.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from agent_baton.api.deps import get_central_store
from agent_baton.core.storage.central import CentralStore

_log = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Dependency — returns None when central.db is missing so callers degrade
# gracefully without raising RuntimeError.
# ---------------------------------------------------------------------------

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"


def get_central_store_optional() -> CentralStore | None:
    """Return a CentralStore, or None if central.db does not exist.

    NOC endpoints call this instead of the strict ``get_central_store``
    dependency so they can return empty results rather than HTTP 500 when
    the user has not yet run a sync.
    """
    if not _CENTRAL_DB_DEFAULT.exists():
        return None
    try:
        return get_central_store()
    except Exception:
        _log.debug("central.db not accessible; NOC returning empty results")
        return None


# ---------------------------------------------------------------------------
# /noc/projects
# ---------------------------------------------------------------------------


@router.get("/noc/projects")
async def list_noc_projects(
    store: CentralStore | None = Depends(get_central_store_optional),
) -> dict[str, Any]:
    """Return all known projects with summary stats.

    GET /api/v1/noc/projects

    Queries ``projects`` in central.db.  For each project also counts
    executions (tasks) and derives the last active timestamp from the
    most recent execution ``started_at``.

    Returns:
        A dict with a ``projects`` list.  Each entry contains:
        ``project_id``, ``project_name``, ``path``, ``program``,
        ``registered_at``, ``task_count``, ``last_active``.
        Returns ``{"projects": []}`` when central.db is absent.
    """
    if store is None:
        return {"projects": []}

    try:
        rows: list[dict[str, Any]] = store.query(
            """
            SELECT
                p.project_id,
                p.name          AS project_name,
                p.path,
                p.program,
                p.registered_at,
                COUNT(e.task_id) AS task_count,
                MAX(e.started_at) AS last_active
            FROM projects p
            LEFT JOIN executions e ON e.project_id = p.project_id
            GROUP BY p.project_id, p.name, p.path, p.program, p.registered_at
            ORDER BY last_active DESC NULLS LAST, p.name ASC
            """
        )
    except Exception as exc:
        _log.warning("NOC /projects query failed: %s", exc)
        return {"projects": []}

    return {"projects": rows}


# ---------------------------------------------------------------------------
# /noc/aggregate/usage
# ---------------------------------------------------------------------------


@router.get("/noc/aggregate/usage")
async def aggregate_usage(
    store: CentralStore | None = Depends(get_central_store_optional),
) -> dict[str, Any]:
    """Return cross-project token usage rollup.

    GET /api/v1/noc/aggregate/usage

    Sums ``estimated_tokens`` from ``agent_usage`` grouped by project.
    Also returns a ``total_tokens`` grand total across all projects.

    Returns:
        A dict with ``by_project`` (list of ``{project_id, total_tokens}``)
        and ``total_tokens`` (int).  Returns zeros when central.db is absent.
    """
    if store is None:
        return {"by_project": [], "total_tokens": 0}

    try:
        rows: list[dict[str, Any]] = store.query(
            """
            SELECT
                project_id,
                SUM(estimated_tokens) AS total_tokens
            FROM agent_usage
            GROUP BY project_id
            ORDER BY total_tokens DESC
            """
        )
    except Exception as exc:
        _log.warning("NOC /aggregate/usage query failed: %s", exc)
        return {"by_project": [], "total_tokens": 0}

    grand_total: int = sum(r.get("total_tokens") or 0 for r in rows)
    return {"by_project": rows, "total_tokens": grand_total}


# ---------------------------------------------------------------------------
# /noc/aggregate/incidents
# ---------------------------------------------------------------------------


@router.get("/noc/aggregate/incidents")
async def aggregate_incidents(
    store: CentralStore | None = Depends(get_central_store_optional),
) -> dict[str, Any]:
    """Return cross-project warning-bead count per project.

    GET /api/v1/noc/aggregate/incidents

    Counts beads with ``bead_type = 'warning'`` in central.db grouped by
    project.  Also includes a ``total_warnings`` grand total.

    Returns:
        A dict with ``by_project`` (list of ``{project_id, warning_count}``)
        and ``total_warnings`` (int).  Returns zeros when central.db is absent.
    """
    if store is None:
        return {"by_project": [], "total_warnings": 0}

    try:
        rows: list[dict[str, Any]] = store.query(
            """
            SELECT
                project_id,
                COUNT(*) AS warning_count
            FROM beads
            WHERE bead_type = 'warning'
            GROUP BY project_id
            ORDER BY warning_count DESC
            """
        )
    except Exception as exc:
        _log.warning("NOC /aggregate/incidents query failed: %s", exc)
        return {"by_project": [], "total_warnings": 0}

    grand_total: int = sum(r.get("warning_count") or 0 for r in rows)
    return {"by_project": rows, "total_warnings": grand_total}


# ---------------------------------------------------------------------------
# /noc/aggregate/throughput
# ---------------------------------------------------------------------------


@router.get("/noc/aggregate/throughput")
async def aggregate_throughput(
    store: CentralStore | None = Depends(get_central_store_optional),
) -> dict[str, Any]:
    """Return tasks completed per project per day for the last 7 days.

    GET /api/v1/noc/aggregate/throughput

    Counts ``executions`` rows whose ``status = 'complete'`` and whose
    ``completed_at`` timestamp falls within the past 7 days, grouped by
    ``(project_id, day)``.

    Returns:
        A dict with ``window_days`` (7) and ``by_project_day``
        (list of ``{project_id, day, tasks_completed}``).
        Returns an empty list when central.db is absent.
    """
    if store is None:
        return {"window_days": 7, "by_project_day": []}

    try:
        rows: list[dict[str, Any]] = store.query(
            """
            SELECT
                project_id,
                DATE(completed_at) AS day,
                COUNT(*) AS tasks_completed
            FROM executions
            WHERE status = 'complete'
              AND completed_at IS NOT NULL
              AND completed_at != ''
              AND DATE(completed_at) >= DATE('now', '-6 days')
            GROUP BY project_id, DATE(completed_at)
            ORDER BY project_id, day
            """
        )
    except Exception as exc:
        _log.warning("NOC /aggregate/throughput query failed: %s", exc)
        return {"window_days": 7, "by_project_day": []}

    return {"window_days": 7, "by_project_day": rows}
