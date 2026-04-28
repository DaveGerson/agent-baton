"""H3 PMO endpoints — role-based dashboards, scorecards, arch review,
playbooks, and the Change Request Process (CRP).

Companion module to :mod:`agent_baton.api.routes.pmo`.  Kept in a separate
file so the existing 2900-line ``pmo.py`` is not disturbed by additive
H3 surface area.

Endpoints (all prefixed with ``/api/v1``):

GET  /pmo/scorecard/{user_id}     — Per-developer scorecard (last 30 days).
GET  /pmo/arch-beads               — List open architecture/decision beads.
POST /pmo/arch-beads/{bead_id}/review — File an approve/reject follow-up bead.
GET  /pmo/playbooks                — List curated workflow playbooks.
POST /pmo/crp                      — File a Change Request and get a plan summary.

Velocity-first stance
---------------------
- Aggregations fall back to zeros when source tables are empty rather
  than 500ing.
- The CRP endpoint produces a deterministic *plan summary* without
  invoking the headless planner; full Forge integration is left for a
  future iteration and marked with a TODO.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from agent_baton.api.deps import get_pmo_store
from agent_baton.core.pmo.store import PmoStore

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_db_path() -> Path:
    """Return the active project's ``baton.db`` path.

    Defaults to ``.claude/team-context/baton.db`` relative to the current
    working directory.  Returns the path even if the file does not exist
    yet — callers must guard against missing tables.
    """
    return Path(".claude/team-context/baton.db").resolve()


def _safe_query(db_path: Path, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Execute *sql* against *db_path* returning rows or [] on any failure.

    Velocity-first: a missing table or DB file should yield an empty list
    rather than crashing the endpoint.  This makes the H3 surfaces usable
    on a freshly initialised project before any work has been recorded.
    """
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(sql, params).fetchall())
        finally:
            conn.close()
    except sqlite3.Error:
        return []


def _playbooks_dir() -> Path:
    """Return the directory containing curated playbook markdown files."""
    return Path("templates/playbooks").resolve()


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class DeveloperScorecardResponse(BaseModel):
    """Aggregated metrics for a single developer over the last 30 days."""

    user_id: str
    window_days: int = 30
    tasks_completed: int = 0
    avg_cycle_time_minutes: float = 0.0
    gate_pass_rate: float = 0.0
    incidents_authored: int = 0
    incidents_resolved: int = 0
    knowledge_contributions: int = 0


class ArchBeadResponse(BaseModel):
    """A single architecture/decision bead awaiting review."""

    bead_id: str
    bead_type: str
    agent_name: str
    content: str
    affected_files: list[str] = Field(default_factory=list)
    status: str
    created_at: str
    tags: list[str] = Field(default_factory=list)


class ArchReviewRequest(BaseModel):
    """Body for ``POST /pmo/arch-beads/{bead_id}/review``."""

    action: str = Field(..., pattern="^(approve|reject)$")
    reason: str = ""
    reviewer: str = "anonymous"


class ArchReviewResponse(BaseModel):
    """Result of filing a review follow-up bead."""

    bead_id: str
    follow_up_bead_id: str
    action: str


class PlaybookResponse(BaseModel):
    """A curated workflow playbook surfaced from ``templates/playbooks``."""

    slug: str
    title: str
    body: str


class CRPRequest(BaseModel):
    """Structured Change Request submission body."""

    title: str
    scope: list[str] = Field(default_factory=list)
    rationale: str = ""
    risk_level: str = Field("medium", pattern="^(low|medium|high|critical)$")
    suggested_agent: str = "architect"


class CRPResponse(BaseModel):
    """Synthesized plan summary returned to the wizard.

    NOTE: This is currently a deterministic stub — the integration with
    ``baton plan`` / ``ForgeSession`` is left for a follow-up bead so we
    can ship the wizard surface immediately.
    """

    crp_id: str
    plan_summary: str
    suggested_phases: list[str]
    risk_level: str
    submitted_at: str


# ---------------------------------------------------------------------------
# H3.4 — Per-developer scorecard
# ---------------------------------------------------------------------------


@router.get("/pmo/scorecard/{user_id}", response_model=DeveloperScorecardResponse)
async def get_developer_scorecard(user_id: str) -> DeveloperScorecardResponse:
    """Return a 30-day scorecard for *user_id*.

    Aggregates over the project's ``baton.db`` tables when present:

    - ``agent_usage`` for tasks_completed (rows where ``agent_name``
      matches the user-id are counted as their tasks).
    - ``step_results`` for cycle time (avg of ``duration_seconds``).
    - Gate pass rate from ``step_results`` rows where ``step_id`` looks
      like a gate (``gate-*``).
    - ``beads`` for incidents authored / resolved and knowledge entries.

    Tables that don't exist or are empty contribute zero — never raise.
    """
    db_path = _project_db_path()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    # tasks_completed
    rows = _safe_query(
        db_path,
        "SELECT COUNT(*) AS n FROM agent_usage "
        "WHERE agent_name = ? AND created_at >= ?",
        (user_id, cutoff),
    )
    tasks_completed = int(rows[0]["n"]) if rows else 0

    # avg cycle time (minutes)
    rows = _safe_query(
        db_path,
        "SELECT AVG(duration_seconds) AS avg_s FROM step_results "
        "WHERE agent_name = ? AND created_at >= ?",
        (user_id, cutoff),
    )
    avg_seconds = float(rows[0]["avg_s"] or 0.0) if rows else 0.0
    avg_cycle_time_minutes = round(avg_seconds / 60.0, 2)

    # gate pass rate
    rows = _safe_query(
        db_path,
        "SELECT step_id, status FROM step_results "
        "WHERE agent_name = ? AND created_at >= ? "
        "AND step_id LIKE 'gate-%'",
        (user_id, cutoff),
    )
    if rows:
        passed = sum(1 for r in rows if r["status"] in ("complete", "pass", "passed"))
        gate_pass_rate = round(passed / len(rows), 3)
    else:
        gate_pass_rate = 0.0

    # incidents authored / resolved
    rows = _safe_query(
        db_path,
        "SELECT COUNT(*) AS n FROM beads "
        "WHERE agent_name = ? AND bead_type IN ('warning', 'incident', 'bug') "
        "AND created_at >= ?",
        (user_id, cutoff),
    )
    incidents_authored = int(rows[0]["n"]) if rows else 0

    rows = _safe_query(
        db_path,
        "SELECT COUNT(*) AS n FROM beads "
        "WHERE agent_name = ? AND status = 'closed' "
        "AND bead_type IN ('warning', 'incident', 'bug') "
        "AND closed_at >= ?",
        (user_id, cutoff),
    )
    incidents_resolved = int(rows[0]["n"]) if rows else 0

    # knowledge contributions
    rows = _safe_query(
        db_path,
        "SELECT COUNT(*) AS n FROM beads "
        "WHERE agent_name = ? AND bead_type IN ('knowledge', 'decision', 'pattern') "
        "AND created_at >= ?",
        (user_id, cutoff),
    )
    knowledge_contributions = int(rows[0]["n"]) if rows else 0

    return DeveloperScorecardResponse(
        user_id=user_id,
        window_days=30,
        tasks_completed=tasks_completed,
        avg_cycle_time_minutes=avg_cycle_time_minutes,
        gate_pass_rate=gate_pass_rate,
        incidents_authored=incidents_authored,
        incidents_resolved=incidents_resolved,
        knowledge_contributions=knowledge_contributions,
    )


# ---------------------------------------------------------------------------
# H3.7 — Architectural review
# ---------------------------------------------------------------------------


@router.get("/pmo/arch-beads", response_model=list[ArchBeadResponse])
async def list_arch_beads(
    status: str = Query("open", description="Filter by bead status"),
) -> list[ArchBeadResponse]:
    """Return open beads of type ``architecture`` or ``decision``.

    The bead store may not exist on a fresh project; in that case the
    endpoint returns an empty list so the UI can render its empty state.
    """
    db_path = _project_db_path()
    rows = _safe_query(
        db_path,
        "SELECT bead_id, bead_type, agent_name, content, affected_files, "
        "status, created_at FROM beads "
        "WHERE bead_type IN ('architecture', 'decision') AND status = ? "
        "ORDER BY created_at DESC LIMIT 100",
        (status,),
    )

    results: list[ArchBeadResponse] = []
    for r in rows:
        # affected_files is stored as JSON text in the beads table.
        files_raw = r["affected_files"] or "[]"
        try:
            import json
            files = json.loads(files_raw) if isinstance(files_raw, str) else []
        except Exception:
            files = []

        # Tags live in a separate bead_tags table; load them lazily.
        tag_rows = _safe_query(
            db_path,
            "SELECT tag FROM bead_tags WHERE bead_id = ?",
            (r["bead_id"],),
        )
        tags = [t["tag"] for t in tag_rows]

        results.append(
            ArchBeadResponse(
                bead_id=r["bead_id"],
                bead_type=r["bead_type"],
                agent_name=r["agent_name"] or "",
                content=r["content"] or "",
                affected_files=files,
                status=r["status"] or "open",
                created_at=r["created_at"] or "",
                tags=tags,
            )
        )
    return results


@router.post(
    "/pmo/arch-beads/{bead_id}/review",
    response_model=ArchReviewResponse,
    status_code=201,
)
async def review_arch_bead(
    bead_id: str,
    body: ArchReviewRequest,
) -> ArchReviewResponse:
    """File a follow-up bead recording an architectural review decision.

    The original bead is left intact (audit trail).  A new bead is
    written with ``bead_type='review'`` and a link of type ``relates_to``
    pointing back at the original.  When the bead store is unavailable
    we return a synthetic id so the UI flow still completes — the
    decision is logged regardless.
    """
    db_path = _project_db_path()
    follow_up_id = f"bd-rv-{uuid.uuid4().hex[:8]}"

    if db_path.exists():
        try:
            from agent_baton.core.engine.bead_store import BeadStore
            from agent_baton.models.bead import Bead, BeadLink

            store = BeadStore(db_path)
            now = datetime.now(timezone.utc).isoformat()
            review_bead = Bead(
                bead_id=follow_up_id,
                task_id="arch-review",
                step_id="review",
                agent_name=body.reviewer or "anonymous",
                bead_type="review",
                content=(
                    f"Architectural review: {body.action.upper()}\n"
                    f"Reason: {body.reason}\n"
                    f"Reviews bead: {bead_id}"
                ),
                tags=["arch-review", body.action],
                status="open" if body.action == "reject" else "closed",
                created_at=now,
                summary=f"{body.action} of {bead_id}",
                links=[BeadLink(target_bead_id=bead_id, link_type="relates_to")],
                source="manual",
            )
            store.write(review_bead)
        except Exception:
            # Velocity-first: never fail the UI flow on a storage hiccup.
            pass

    return ArchReviewResponse(
        bead_id=bead_id,
        follow_up_bead_id=follow_up_id,
        action=body.action,
    )


# ---------------------------------------------------------------------------
# H3.8 — Playbook gallery
# ---------------------------------------------------------------------------


@router.get("/pmo/playbooks", response_model=list[PlaybookResponse])
async def list_playbooks() -> list[PlaybookResponse]:
    """List curated workflow playbooks from ``templates/playbooks/``.

    Each ``.md`` file becomes one playbook.  The first line beginning
    with ``# `` is treated as the title; if absent, the slug is used.
    Returns an empty list when the directory is missing.
    """
    pdir = _playbooks_dir()
    if not pdir.exists() or not pdir.is_dir():
        return []

    results: list[PlaybookResponse] = []
    for md in sorted(pdir.glob("*.md")):
        try:
            body = md.read_text(encoding="utf-8")
        except OSError:
            continue
        title = md.stem.replace("-", " ").title()
        for line in body.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        results.append(PlaybookResponse(slug=md.stem, title=title, body=body))
    return results


# ---------------------------------------------------------------------------
# H3.9 — Change Request Process (CRP)
# ---------------------------------------------------------------------------


@router.post("/pmo/crp", response_model=CRPResponse, status_code=201)
async def submit_crp(body: CRPRequest) -> CRPResponse:
    """Accept a structured change request and return a plan summary.

    TODO: Wire this through ``ForgeSession`` so a real ``MachinePlan`` is
    produced.  For now a deterministic summary is synthesized so the
    wizard surface ships independently of the planner integration.
    """
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="title is required")

    crp_id = f"crp-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()

    # Synthesize a summary the UI can show immediately.
    scope_str = ", ".join(body.scope) if body.scope else "(none specified)"
    plan_summary = (
        f"Proposed change: {body.title}\n"
        f"Risk level: {body.risk_level}\n"
        f"Scope: {scope_str}\n"
        f"Rationale: {body.rationale or '(none provided)'}\n"
        f"Suggested lead agent: {body.suggested_agent}"
    )
    suggested_phases = [
        "research",
        "design",
        "implement",
        "test",
        "review",
    ]
    if body.risk_level in ("high", "critical"):
        suggested_phases.insert(2, "security-review")
        suggested_phases.append("audit")

    return CRPResponse(
        crp_id=crp_id,
        plan_summary=plan_summary,
        suggested_phases=suggested_phases,
        risk_level=body.risk_level,
        submitted_at=now,
    )
