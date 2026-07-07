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


def _parse_iso(ts: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp (``Z`` or ``+00:00`` suffixed) to an aware
    :class:`datetime`, or ``None`` if *ts* is empty/unparseable.

    Bead timestamps (``Bead.created_at`` / ``closed_at``) are written as
    ``...Z``; :meth:`datetime.fromisoformat` only accepts that suffix on
    Python 3.11+, so it is normalized to ``+00:00`` first for portability.
    """
    if not ts:
        return None
    text = ts.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class DeveloperScorecardResponse(BaseModel):
    """Aggregated metrics for a single developer over the last 30 days.

    ``incidents_authored`` / ``incidents_resolved`` / ``knowledge_contributions``
    are bead-derived. ``bead_data_available`` (bd-y0d, additive field --
    existing consumers can ignore it) is ``False`` when the bead store could
    not be reached, so those three fields read as legitimate zeros only when
    ``bead_data_available`` is ``True``. ``tasks_completed`` /
    ``avg_cycle_time_minutes`` / ``gate_pass_rate`` are unaffected -- they
    are still sourced from the project's ``baton.db`` SQLite tables.
    """

    user_id: str
    window_days: int = 30
    tasks_completed: int = 0
    avg_cycle_time_minutes: float = 0.0
    gate_pass_rate: float = 0.0
    incidents_authored: int = 0
    incidents_resolved: int = 0
    knowledge_contributions: int = 0
    bead_data_available: bool = True


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


class BeadLinkResponse(BaseModel):
    """A single typed link from one bead to another."""

    target_bead_id: str
    link_type: str
    created_at: str = ""


class BeadResponse(BaseModel):
    """Full bead representation surfaced by ``GET /pmo/beads``.

    Mirrors :class:`agent_baton.models.bead.Bead` so the PMO UI's
    ``BeadGraphView`` and ``BeadTimelineView`` can render the graph and
    timeline without reaching into the SQLite layer.
    """

    bead_id: str
    task_id: str = ""
    step_id: str = ""
    agent_name: str = ""
    bead_type: str
    content: str = ""
    confidence: str = "medium"
    scope: str = "step"
    tags: list[str] = Field(default_factory=list)
    affected_files: list[str] = Field(default_factory=list)
    status: str = "open"
    created_at: str = ""
    closed_at: str = ""
    summary: str = ""
    links: list[BeadLinkResponse] = Field(default_factory=list)
    source: str = "agent-signal"
    token_estimate: int = 0
    quality_score: float = 0.0
    retrieval_count: int = 0


class BeadListResponse(BaseModel):
    """Envelope returned by ``GET /pmo/beads``."""

    beads: list[BeadResponse] = Field(default_factory=list)
    total: int = 0


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

    - ``agent_usage`` (``baton.db``) for tasks_completed (rows where
      ``agent_name`` matches the user-id are counted as their tasks).
    - ``step_results`` (``baton.db``) for cycle time (avg of
      ``duration_seconds``) and gate pass rate (rows where ``step_id``
      looks like a gate, ``gate-*``).
    - The bd-backed bead store (bd-y0d fix) for incidents authored /
      resolved and knowledge contributions. These were previously raw SQL
      against a ``beads`` table in ``baton.db`` that schema migration v42
      drops and never recreates -- ``_safe_query`` swallowed the resulting
      ``OperationalError`` and silently returned zeros forever. Beads have
      lived in the ``bd``-backed store (not ``baton.db``) since ADR-13b
      WP-G, so this now mirrors the sibling ``list_beads`` /
      ``list_arch_beads`` endpoints' ``make_bead_store()`` construction and
      error handling.

    ``baton.db``-sourced fields still contribute zero when their tables are
    missing/empty (unchanged, never raises). Bead-derived fields are zero
    AND ``bead_data_available=False`` both when the store could not be
    *constructed* (bd unavailable, workspace not initialised) and when a
    constructed store's ``query()`` fails at *runtime* (``strict=True`` --
    F3 fix: ``BdBeadStore.query`` otherwise swallows ``BdError`` and returns
    ``[]`` for its other callers, which would have made a runtime bd
    failure here indistinguishable from a genuinely-empty result). So a
    caller can always distinguish "genuinely zero" from "we don't know"
    (never a silent zero -- bd-y0d).
    """
    db_path = _project_db_path()
    now = datetime.now(timezone.utc)
    cutoff_dt = now - timedelta(days=30)
    cutoff = cutoff_dt.isoformat()

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

    # incidents authored / resolved / knowledge contributions (bd-y0d):
    # mirrors list_arch_beads' construction + error handling exactly --
    # construct via make_bead_store() and treat ANY failure (bd unavailable,
    # workspace not initialised, ...) as "bead data unavailable" rather than
    # a silent zero.
    incidents_authored = 0
    incidents_resolved = 0
    knowledge_contributions = 0
    bead_data_available = True

    try:
        from agent_baton.core.engine.bead_backend import make_bead_store

        store = make_bead_store(db_path, repo_root=db_path.parent.parent.parent)
        # strict=True: a store that constructs fine but whose bd invocations
        # fail at query time must also flip bead_data_available=False rather
        # than silently reporting an empty (indistinguishable from
        # genuinely-zero) result set (F3).
        user_beads = store.query(agent_name=user_id, status=None, limit=1000, strict=True)
    except Exception:
        bead_data_available = False
        user_beads = []

    if bead_data_available:
        for bead in user_beads:
            bead_type = bead.bead_type
            if bead_type in ("warning", "incident", "bug"):
                created = _parse_iso(bead.created_at)
                if created is not None and created >= cutoff_dt:
                    incidents_authored += 1
                if bead.status == "closed":
                    closed = _parse_iso(bead.closed_at)
                    if closed is not None and closed >= cutoff_dt:
                        incidents_resolved += 1
            elif bead_type in ("knowledge", "decision", "pattern"):
                created = _parse_iso(bead.created_at)
                if created is not None and created >= cutoff_dt:
                    knowledge_contributions += 1

    return DeveloperScorecardResponse(
        user_id=user_id,
        window_days=30,
        tasks_completed=tasks_completed,
        avg_cycle_time_minutes=avg_cycle_time_minutes,
        gate_pass_rate=gate_pass_rate,
        incidents_authored=incidents_authored,
        incidents_resolved=incidents_resolved,
        knowledge_contributions=knowledge_contributions,
        bead_data_available=bead_data_available,
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

    ADR-13b WP-2: reads via ``make_bead_store(...)`` so the bd backend is used
    when ``BATON_BD_BACKEND=bd``.  Tags are filtered in Python rather than
    via a JOIN so the same code works for both backends.
    """
    db_path = _project_db_path()

    try:
        from agent_baton.core.engine.bead_backend import make_bead_store

        store = make_bead_store(db_path, repo_root=db_path.parent.parent.parent)
        # Query without a bead_type filter and filter in Python so we can
        # match two types in one pass, compatible with both backends.
        status_filter: str | None = status if status not in ("", "all") else None
        raw_beads = store.query(
            bead_type=None,
            status=status_filter,
            limit=100,
        )
    except Exception:
        return []

    results: list[ArchBeadResponse] = []
    for b in raw_beads:
        if b.bead_type not in ("architecture", "decision"):
            continue
        results.append(
            ArchBeadResponse(
                bead_id=b.bead_id,
                bead_type=b.bead_type,
                agent_name=b.agent_name or "",
                content=b.content or "",
                affected_files=list(b.affected_files or []),
                status=b.status or "open",
                created_at=b.created_at or "",
                tags=list(b.tags or []),
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
            from agent_baton.core.engine.bead_backend import make_bead_store
            from agent_baton.models.bead import Bead, BeadLink

            store = make_bead_store(db_path, repo_root=db_path.parent.parent.parent)
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


# ---------------------------------------------------------------------------
# DX.6 — Bead listing for PMO graph + timeline (bd-aade)
# ---------------------------------------------------------------------------


@router.get("/pmo/beads", response_model=BeadListResponse)
async def list_beads(
    status: str | None = Query(
        "open",
        description="Filter by bead status (open|closed|archived). "
        "Pass an empty string or 'all' to skip status filtering.",
    ),
    bead_type: str | None = Query(
        None, description="Filter to a specific bead type (e.g. 'warning')."
    ),
    tags: str | None = Query(
        None,
        description="Comma-separated list of tags; AND semantics — "
        "returned beads must carry every tag in the list.",
    ),
    task_id: str | None = Query(
        None, description="Filter to beads from a specific task/execution."
    ),
    limit: int = Query(
        200, ge=1, le=1000, description="Maximum number of beads to return."
    ),
) -> BeadListResponse:
    """List beads from the project's ``baton.db`` for the PMO Beads view.

    Returns the full bead shape — including links, tags, and affected
    files — matching :class:`agent_baton.models.bead.Bead` so the UI's
    ``BeadGraphView`` and ``BeadTimelineView`` can render without
    additional round-trips.

    The endpoint degrades gracefully when the bead store is unavailable
    (no DB file, missing tables) by returning an empty envelope rather
    than 500ing — the PMO can still render its empty state.
    """
    db_path = _project_db_path()

    # Empty string or "all" disables the status filter.
    status_filter: str | None = status
    if status in ("", "all", None):
        status_filter = None

    parsed_tags: list[str] | None = None
    if tags:
        parsed_tags = [t.strip() for t in tags.split(",") if t.strip()]
        if not parsed_tags:
            parsed_tags = None

    if not db_path.exists():
        return BeadListResponse(beads=[], total=0)

    try:
        from agent_baton.core.engine.bead_backend import make_bead_store

        store = make_bead_store(db_path, repo_root=db_path.parent.parent.parent)
        beads = store.query(
            task_id=task_id,
            bead_type=bead_type,
            status=status_filter,
            tags=parsed_tags,
            limit=limit,
        )
    except Exception:
        # Velocity-first: never 500 on a storage hiccup.
        return BeadListResponse(beads=[], total=0)

    items: list[BeadResponse] = []
    for b in beads:
        items.append(
            BeadResponse(
                bead_id=b.bead_id,
                task_id=b.task_id or "",
                step_id=b.step_id or "",
                agent_name=b.agent_name or "",
                bead_type=b.bead_type,
                content=b.content or "",
                confidence=b.confidence or "medium",
                scope=b.scope or "step",
                tags=list(b.tags or []),
                affected_files=list(b.affected_files or []),
                status=b.status or "open",
                created_at=b.created_at or "",
                closed_at=b.closed_at or "",
                summary=b.summary or "",
                links=[
                    BeadLinkResponse(
                        target_bead_id=lnk.target_bead_id,
                        link_type=lnk.link_type,
                        created_at=getattr(lnk, "created_at", "") or "",
                    )
                    for lnk in (b.links or [])
                ],
                source=b.source or "agent-signal",
                token_estimate=int(b.token_estimate or 0),
                quality_score=float(getattr(b, "quality_score", 0.0) or 0.0),
                retrieval_count=int(
                    getattr(b, "retrieval_count", 0) or 0
                ),
            )
        )

    return BeadListResponse(beads=items, total=len(items))
