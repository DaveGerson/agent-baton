"""Prometheus ``/metrics`` endpoint for the Agent Baton API.

Exposes the live state of the engine in Prometheus text exposition
format (version 0.0.4).  No external dependencies — the exposition
text is rendered by hand via
:mod:`agent_baton.core.observability.prometheus`.

Why this lives outside ``/api/v1``
-----------------------------------

Prometheus convention is ``GET /metrics`` at the root of the host.
Mounting it under ``/api/v1`` would force every scraper to override
its default scrape path.  We register the route at the root and add
``/metrics`` to the auth-exempt set so scrapers can poll without
credentials, matching how ``/health`` and ``/ready`` are exposed.

Data sources
------------

All metrics read from the per-project ``baton.db`` resolved from the
configured team-context root.  The endpoint opens a fresh read-only
connection per request and closes it before returning, so it never
holds a long-lived handle that could block writers.

If ``baton.db`` does not exist yet (fresh project, no plan ever
saved) the endpoint still returns a valid 200 with the metric
declarations and zero samples.  This is intentional — Prometheus
needs to scrape declared metric families even when they're empty.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Response

from agent_baton.api.deps import get_team_context_root
from agent_baton.core.observability.prometheus import (
    MetricFamily,
    MetricSample,
    to_text_exposition,
)

router = APIRouter()

# Prometheus 0.0.4 exposition content type — exact spelling matters,
# scrapers parse the version parameter to choose a parser.
_PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("/metrics", include_in_schema=False)
async def metrics(
    team_context_root: Path = Depends(get_team_context_root),
) -> Response:
    """Return live Prometheus metrics for the local Baton instance."""
    families = _build_families(team_context_root / "baton.db")
    body = to_text_exposition(families)
    return Response(content=body, media_type=_PROM_CONTENT_TYPE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_families(db_path: Path) -> list[MetricFamily]:
    """Collect every metric family from the project ``baton.db``.

    Falls back gracefully when the DB is missing or a table is absent
    (for example, on a fresh project the ``beads`` table is created on
    first migration; before that we still want the endpoint to return
    a valid response with zero samples).
    """
    plans = MetricFamily(
        name="baton_plans_total",
        type="counter",
        help_text="Total plans grouped by execution status.",
    )
    steps = MetricFamily(
        name="baton_steps_total",
        type="counter",
        help_text="Total step results grouped by agent, model and outcome status.",
    )
    tokens = MetricFamily(
        name="baton_tokens_total",
        type="counter",
        help_text="Total tokens (input + cache_read + output) grouped by model.",
    )
    active_executions = MetricFamily(
        name="baton_active_executions",
        type="gauge",
        help_text="Number of executions currently in 'running' state.",
    )
    open_beads = MetricFamily(
        name="baton_open_beads",
        type="gauge",
        help_text="Open beads grouped by bead type and severity (confidence proxy).",
    )
    chain_length = MetricFamily(
        name="baton_chain_length",
        type="gauge",
        help_text="Compliance-chain length grouped by entry type.",
    )

    if not db_path.exists():
        # No project DB yet — emit a single zero sample for the
        # active-executions gauge so scrapers see the metric is "live"
        # even on a fresh install.  Counters are left empty by design;
        # an empty counter is a valid Prometheus state.
        active_executions.samples = [MetricSample(value=0)]
        return [plans, steps, tokens, active_executions, open_beads, chain_length]

    # ``uri=True`` + ``mode=ro`` ensures we never accidentally write,
    # and ``check_same_thread=False`` is safe because we open + close
    # within the request handler with no shared state.
    uri = f"file:{db_path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    except sqlite3.Error:
        # Database briefly unreachable; return declarations with no samples.
        return [plans, steps, tokens, active_executions, open_beads, chain_length]

    try:
        plans.samples = _collect_plans(conn)
        steps.samples = _collect_steps(conn)
        tokens.samples = _collect_tokens(conn)
        active_executions.samples = _collect_active_executions(conn)
        open_beads.samples = _collect_open_beads(conn)
        chain_length.samples = _collect_chain_length(conn)
    finally:
        conn.close()

    return [plans, steps, tokens, active_executions, open_beads, chain_length]


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    """Return True if *table* exists in the connected SQLite DB."""
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ? LIMIT 1",
        (table,),
    )
    return cur.fetchone() is not None


def _collect_plans(conn: sqlite3.Connection) -> list[MetricSample]:
    """``baton_plans_total{status}`` counter from plans + executions.

    Plans inherit their status from the executions table since the
    plans table itself has no status column.  This matches how the
    rest of the engine reasons about plan lifecycle.
    """
    if not (_table_exists(conn, "plans") and _table_exists(conn, "executions")):
        return []
    cur = conn.execute(
        """
        SELECT COALESCE(e.status, 'unknown') AS status, COUNT(*) AS n
        FROM plans p
        LEFT JOIN executions e ON e.task_id = p.task_id
        GROUP BY status
        """
    )
    return [
        MetricSample(labels={"status": str(status)}, value=int(n))
        for status, n in cur.fetchall()
    ]


def _collect_steps(conn: sqlite3.Connection) -> list[MetricSample]:
    """``baton_steps_total{agent,model,outcome}`` from step_results.

    The ``model`` label uses ``model_id`` when available (populated by
    the JSONL token scanner) and falls back to the planner's intended
    model from ``plan_steps``.  ``outcome`` mirrors the
    ``status`` column on step_results (e.g. ``complete`` / ``failed``).
    """
    if not _table_exists(conn, "step_results"):
        return []
    cur = conn.execute(
        """
        SELECT
            sr.agent_name AS agent,
            CASE WHEN sr.model_id != '' THEN sr.model_id
                 ELSE COALESCE(ps.model, '') END AS model,
            sr.status AS outcome,
            COUNT(*) AS n
        FROM step_results sr
        LEFT JOIN plan_steps ps
            ON ps.task_id = sr.task_id AND ps.step_id = sr.step_id
        GROUP BY agent, model, outcome
        """
    )
    return [
        MetricSample(
            labels={
                "agent": str(agent or ""),
                "model": str(model or ""),
                "outcome": str(outcome or ""),
            },
            value=int(n),
        )
        for agent, model, outcome, n in cur.fetchall()
    ]


def _collect_tokens(conn: sqlite3.Connection) -> list[MetricSample]:
    """``baton_tokens_total{model}`` from step_results token columns.

    Sums input + cache_read + output across every persisted step.
    Per the v13 schema notes these are real token counts pulled from
    the Claude Code session JSONL files, not the legacy char/4
    heuristic.
    """
    if not _table_exists(conn, "step_results"):
        return []
    cur = conn.execute(
        """
        SELECT
            COALESCE(NULLIF(model_id, ''), 'unknown') AS model,
            COALESCE(SUM(input_tokens + cache_read_tokens + output_tokens), 0) AS total
        FROM step_results
        GROUP BY model
        """
    )
    return [
        MetricSample(labels={"model": str(model)}, value=int(total))
        for model, total in cur.fetchall()
    ]


def _collect_active_executions(conn: sqlite3.Connection) -> list[MetricSample]:
    """``baton_active_executions`` gauge — count of running tasks."""
    if not _table_exists(conn, "executions"):
        return [MetricSample(value=0)]
    cur = conn.execute(
        "SELECT COUNT(*) FROM executions WHERE status = 'running'"
    )
    (n,) = cur.fetchone()
    return [MetricSample(value=int(n))]


def _collect_open_beads(conn: sqlite3.Connection) -> list[MetricSample]:
    """``baton_open_beads{type,severity}`` from beads table.

    The beads schema has no dedicated severity column; we use the
    ``confidence`` value as a proxy (low/medium/high).  This is a
    coarse signal but consistent with how operators already reason
    about bead urgency in the dashboard.
    """
    if not _table_exists(conn, "beads"):
        return []
    cur = conn.execute(
        """
        SELECT bead_type, confidence, COUNT(*) AS n
        FROM beads
        WHERE status = 'open'
        GROUP BY bead_type, confidence
        """
    )
    return [
        MetricSample(
            labels={"type": str(t or ""), "severity": str(sev or "")},
            value=int(n),
        )
        for t, sev, n in cur.fetchall()
    ]


def _collect_chain_length(conn: sqlite3.Connection) -> list[MetricSample]:
    """``baton_chain_length{kind}`` gauge from compliance_log entries."""
    if not _table_exists(conn, "compliance_log"):
        return []
    cur = conn.execute(
        """
        SELECT entry_type, COUNT(*) AS n
        FROM compliance_log
        GROUP BY entry_type
        """
    )
    return [
        MetricSample(labels={"kind": str(kind or "")}, value=int(n))
        for kind, n in cur.fetchall()
    ]
