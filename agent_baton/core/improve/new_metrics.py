"""H3.5 — read-side performance metrics for the improvement pipeline.

Four velocity-zero metrics derived entirely from existing tables:

1. :func:`compute_spec_effectiveness` — fraction of spec-linked tasks that
   reached COMPLETE status without a REQUEST_CHANGES verdict from any
   reviewer step.  Grouped by spec author.
2. :func:`compute_delegation_roi` — coarse minutes-saved estimate per agent,
   weighted by acceptance vs. revision counts.  Approximates the
   "would a human have re-done this work?" question.
3. :func:`compute_knowledge_contribution` — for each (pack, doc) attachment,
   how often the receiving agent's step succeeded.  Builds on F0.4
   ``knowledge_telemetry`` joined to ``step_results``.
4. :func:`compute_review_quality` — verdict distribution per reviewer
   agent (BLOCK / FLAG / APPROVE) plus average minutes-to-review.

All four functions:

* Take an optional ``project_root: Path`` argument so tests can point at a
  fixture database (``baton.db`` lives at ``project_root / .claude/team-context``).
* Return plain dataclasses — JSON-serialisable via ``dataclasses.asdict``.
* Use :func:`get_project_storage` for project data and
  :func:`get_central_storage` only when a metric is naturally cross-project.
* Tolerate empty databases — every function returns the documented shape
  with zero counts when no rows exist.

Approximations (documented per the spec's "missing column" guidance):

* **Acceptance vs. revision counts** — the ``step_results`` table has no
  ``revision_count`` column.  We approximate:

  - ``accepted``  = count of step_results with ``status='complete'`` and
                    ``retries == 0``.
  - ``revised``   = count of step_results with ``retries > 0``.
  - ``rejected``  = count of step_results with ``status='failed'``.

  The 30-minute / 12-minute weights below are heuristics; tune via
  experimentation.

* **Reviewer time-to-review** — ``step_results.duration_seconds`` is the
  closest available signal (wall-clock duration of the reviewer's own
  step).  No "queued-for-review" timestamp exists today; tracking that
  would require a new column on ``plan_steps`` (see bead suggestion in
  the H3.5 changelog).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from agent_baton.core.govern.compliance import (
    AuditorVerdict,
    extract_verdict_from_text,
)
from agent_baton.core.storage import get_project_storage


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minutes credited per accepted dispatch (heuristic — represents the
# wall-clock time a human would have spent producing equivalent output).
_ACCEPTED_MINUTES = 30.0

# Minutes deducted per revised dispatch (overhead of human catch + redo).
_REVISION_PENALTY_MINUTES = 30.0

# Minutes deducted per rejected dispatch (full re-do by human).
_REJECTION_PENALTY_MINUTES = 45.0

# Reviewer agent names — drives :func:`compute_review_quality`.
# (The taxonomy in models/taxonomy.py is the source of truth; we duplicate
# the short list here to avoid a circular dependency on planning code.)
_REVIEWER_AGENTS: frozenset[str] = frozenset(
    {
        "auditor",
        "code-reviewer",
        "security-reviewer",
        "spec-document-reviewer",
        "plan-reviewer",
    }
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SpecAuthorStats:
    """Per-author breakdown for spec effectiveness.

    Attributes:
        author: Spec author identifier (typically a user_id).
        total_specs: Number of distinct specs by this author that link to
            at least one executed plan.
        complete_first_pass: Specs whose linked plan reached COMPLETE
            without any REQUEST_CHANGES (or stronger) reviewer verdict.
        rate: ``complete_first_pass / total_specs``, or 0.0 when zero.
    """

    author: str
    total_specs: int
    complete_first_pass: int
    rate: float


@dataclass
class SpecEffectivenessReport:
    """Top-level result of :func:`compute_spec_effectiveness`.

    Attributes:
        project_id: The project this report covers, or ``"all"`` when
            unscoped (project-local DBs always carry ``"default"``).
        total_specs: Total spec-linked tasks across all authors.
        complete_first_pass: How many of those reached COMPLETE on the
            first reviewer pass.
        rate: Aggregate first-pass rate (0.0 .. 1.0).
        sample_period: ``(earliest_link_date, latest_link_date)`` covering
            all links considered.  Both elements are ``None`` when empty.
        per_author: Per-author breakdown sorted by descending sample size.
    """

    project_id: str
    total_specs: int
    complete_first_pass: int
    rate: float
    sample_period: tuple[date | None, date | None]
    per_author: list[SpecAuthorStats] = field(default_factory=list)


@dataclass
class AgentROI:
    """Per-agent delegation ROI in minutes.

    Attributes:
        agent_name: Agent under measurement.
        total_dispatches: All step_results for this agent (any status).
        accepted: complete + zero retries (work used as-is).
        revised: complete with retries > 0 (had to be redone).
        rejected: status == 'failed'.
        roi_minutes: ``accepted * 30  -  revised * 30  -  rejected * 45``.
    """

    agent_name: str
    total_dispatches: int
    accepted: int
    revised: int
    rejected: int
    roi_minutes: float


@dataclass
class DocContribution:
    """Knowledge document contribution score.

    Attributes:
        pack: Pack name (``""`` when the doc was attached without a pack).
        doc: Document name.
        attachment_count: How many ``knowledge_telemetry`` rows reference
            this doc.
        success_count: How many of those rows correspond to a step_result
            row whose status is ``'complete'``.
        contribution_score: ``success_count / attachment_count`` (0..1).
    """

    pack: str
    doc: str
    attachment_count: int
    success_count: int
    contribution_score: float


@dataclass
class ReviewerStats:
    """Reviewer agent verdict distribution + responsiveness.

    Attributes:
        reviewer: Reviewer agent name.
        verdicts: Mapping from {APPROVE, FLAG, BLOCK, UNKNOWN} to count.
            "FLAG" aggregates ``APPROVE_WITH_CONCERNS`` and
            ``REQUEST_CHANGES``; "BLOCK" is ``VETO``.
        block_rate: ``BLOCK / total_with_verdict`` (0..1).
        approve_rate: ``APPROVE / total_with_verdict`` (0..1).
        avg_minutes: Mean of step_results.duration_seconds / 60 across the
            reviewer's complete steps; ``0.0`` when the reviewer has no
            timed runs.
    """

    reviewer: str
    verdicts: dict[str, int]
    block_rate: float
    approve_rate: float
    avg_minutes: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_db(project_root: Path | None) -> Path | None:
    """Return the path to baton.db for the project, or None if absent.

    Args:
        project_root: Repo root.  Defaults to CWD.
    """
    root = (project_root or Path.cwd()).resolve()
    candidate = root / ".claude" / "team-context"
    if not candidate.exists():
        return None
    storage = get_project_storage(candidate)
    db_path = getattr(storage, "db_path", None)
    if db_path is None or not db_path.exists():
        return None
    return db_path


def _open(db_path: Path) -> sqlite3.Connection:
    """Open a read-only-friendly sqlite3 connection with row factory."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _safe_rate(numerator: int | float, denominator: int | float) -> float:
    """Division that returns 0.0 instead of raising on zero divisor."""
    if not denominator:
        return 0.0
    return float(numerator) / float(denominator)


def _bucket_verdict(verdict: AuditorVerdict | None) -> str:
    """Collapse the 4-value enum into the 3 PMO buckets the spec asks for."""
    if verdict is None:
        return "UNKNOWN"
    if verdict is AuditorVerdict.APPROVE:
        return "APPROVE"
    if verdict is AuditorVerdict.VETO:
        return "BLOCK"
    return "FLAG"


def _parse_iso_date(value: str) -> date | None:
    """Parse a stored ISO-ish timestamp into a date, returning None on failure."""
    if not value:
        return None
    # Strip optional 'Z' and microseconds.
    stem = value.replace("Z", "")
    try:
        return datetime.fromisoformat(stem).date()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Metric 1 — Spec effectiveness
# ---------------------------------------------------------------------------


def compute_spec_effectiveness(
    project_id: str | None = None,
    *,
    project_root: Path | None = None,
) -> SpecEffectivenessReport:
    """Compute spec-effectiveness rate from spec_plan_links + step_results.

    A spec is "effective" (first-pass complete) when:

    * Its linked task_id has an executions row with status == 'complete', AND
    * No step_result.outcome under that task contained an ``AuditorVerdict``
      of ``REQUEST_CHANGES`` or stronger (``VETO``).

    Args:
        project_id: When provided, narrows aggregate counts to specs whose
            ``project_id`` matches.  Per-author rows are still grouped.
        project_root: Repo root override (mostly for tests).

    Returns:
        A :class:`SpecEffectivenessReport`.  Empty DB returns a report with
        zero counts and ``sample_period = (None, None)``.
    """
    db_path = _resolve_db(project_root)
    if db_path is None:
        return SpecEffectivenessReport(
            project_id=project_id or "all",
            total_specs=0,
            complete_first_pass=0,
            rate=0.0,
            sample_period=(None, None),
            per_author=[],
        )

    conn = _open(db_path)
    try:
        # Project-local DBs do not store project_id on per-task rows; the
        # value lives on `specs.project_id`.  We filter there.
        params: tuple = ()
        where = ""
        if project_id is not None:
            where = " AND s.project_id = ?"
            params = (project_id,)

        rows = conn.execute(
            f"""
            SELECT s.spec_id,
                   s.author_id,
                   s.project_id,
                   spl.task_id,
                   spl.linked_at,
                   e.status AS exec_status
              FROM specs s
              JOIN spec_plan_links spl ON spl.spec_id = s.spec_id
              LEFT JOIN executions e ON e.task_id = spl.task_id
             WHERE 1=1{where}
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return SpecEffectivenessReport(
            project_id=project_id or "all",
            total_specs=0,
            complete_first_pass=0,
            rate=0.0,
            sample_period=(None, None),
            per_author=[],
        )

    # Re-open for the per-task verdict lookup so we can keep the prior
    # cursor closed while we iterate.
    conn = _open(db_path)
    try:
        # Pre-fetch all step_result outcomes per task_id for verdict scan.
        task_ids = sorted({r["task_id"] for r in rows})
        verdicts_by_task: dict[str, list[str]] = {tid: [] for tid in task_ids}
        if task_ids:
            placeholders = ",".join("?" * len(task_ids))
            sr_rows = conn.execute(
                f"""
                SELECT task_id, outcome
                  FROM step_results
                 WHERE task_id IN ({placeholders})
                """,
                tuple(task_ids),
            ).fetchall()
            for sr in sr_rows:
                verdicts_by_task.setdefault(sr["task_id"], []).append(
                    sr["outcome"] or ""
                )
    finally:
        conn.close()

    def _had_request_changes(task_id: str) -> bool:
        for outcome in verdicts_by_task.get(task_id, []):
            v = extract_verdict_from_text(outcome)
            if v in (AuditorVerdict.REQUEST_CHANGES, AuditorVerdict.VETO):
                return True
        return False

    # Aggregate by spec_id (a spec may link to multiple tasks; we treat
    # the spec as effective iff EVERY linked task is first-pass complete).
    per_spec: dict[str, dict] = {}
    for r in rows:
        spec_id = r["spec_id"]
        entry = per_spec.setdefault(
            spec_id,
            {
                "author": r["author_id"] or "unknown",
                "tasks": [],
                "linked_dates": [],
            },
        )
        entry["tasks"].append((r["task_id"], r["exec_status"]))
        d = _parse_iso_date(r["linked_at"] or "")
        if d is not None:
            entry["linked_dates"].append(d)

    by_author: dict[str, dict[str, int]] = {}
    total = 0
    first_pass = 0
    all_dates: list[date] = []

    for spec_id, info in per_spec.items():
        total += 1
        author = info["author"]
        bucket = by_author.setdefault(author, {"total": 0, "first_pass": 0})
        bucket["total"] += 1
        all_dates.extend(info["linked_dates"])

        spec_first_pass = True
        for task_id, exec_status in info["tasks"]:
            if (exec_status or "") != "complete":
                spec_first_pass = False
                break
            if _had_request_changes(task_id):
                spec_first_pass = False
                break

        if spec_first_pass:
            first_pass += 1
            bucket["first_pass"] += 1

    per_author = [
        SpecAuthorStats(
            author=a,
            total_specs=v["total"],
            complete_first_pass=v["first_pass"],
            rate=_safe_rate(v["first_pass"], v["total"]),
        )
        for a, v in sorted(by_author.items(), key=lambda kv: -kv[1]["total"])
    ]

    sample_period: tuple[date | None, date | None]
    if all_dates:
        sample_period = (min(all_dates), max(all_dates))
    else:
        sample_period = (None, None)

    return SpecEffectivenessReport(
        project_id=project_id or "all",
        total_specs=total,
        complete_first_pass=first_pass,
        rate=_safe_rate(first_pass, total),
        sample_period=sample_period,
        per_author=per_author,
    )


# ---------------------------------------------------------------------------
# Metric 2 — Delegation ROI
# ---------------------------------------------------------------------------


def compute_delegation_roi(
    *,
    project_root: Path | None = None,
) -> list[AgentROI]:
    """Compute per-agent ROI in minutes from step_results.

    Approximation (documented in the module docstring):

        roi_minutes = accepted * 30  -  revised * 30  -  rejected * 45

    Returns:
        Sorted list of :class:`AgentROI` (highest ROI first).  Empty DB
        returns ``[]``.
    """
    db_path = _resolve_db(project_root)
    if db_path is None:
        return []

    conn = _open(db_path)
    try:
        rows = conn.execute(
            """
            SELECT agent_name,
                   COUNT(*)                                              AS total,
                   SUM(CASE WHEN status='complete' AND retries=0 THEN 1 ELSE 0 END) AS accepted,
                   SUM(CASE WHEN status='complete' AND retries>0 THEN 1 ELSE 0 END) AS revised,
                   SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END)                AS rejected
              FROM step_results
             GROUP BY agent_name
            """
        ).fetchall()
    finally:
        conn.close()

    out: list[AgentROI] = []
    for r in rows:
        accepted = int(r["accepted"] or 0)
        revised = int(r["revised"] or 0)
        rejected = int(r["rejected"] or 0)
        roi = (
            accepted * _ACCEPTED_MINUTES
            - revised * _REVISION_PENALTY_MINUTES
            - rejected * _REJECTION_PENALTY_MINUTES
        )
        out.append(
            AgentROI(
                agent_name=r["agent_name"] or "unknown",
                total_dispatches=int(r["total"] or 0),
                accepted=accepted,
                revised=revised,
                rejected=rejected,
                roi_minutes=roi,
            )
        )

    out.sort(key=lambda a: a.roi_minutes, reverse=True)
    return out


# ---------------------------------------------------------------------------
# Metric 3 — Knowledge contribution
# ---------------------------------------------------------------------------


def compute_knowledge_contribution(
    *,
    project_root: Path | None = None,
) -> list[DocContribution]:
    """Compute per-doc knowledge contribution from F0.4 telemetry.

    Joins ``knowledge_telemetry`` to ``step_results`` on (task_id, step_id);
    when the matching step_result row has status='complete' the attachment
    counts as a "success".

    Returns:
        Sorted list of :class:`DocContribution` (highest score first, then
        highest attachment_count).  Empty DB returns ``[]``.
    """
    db_path = _resolve_db(project_root)
    if db_path is None:
        return []

    conn = _open(db_path)
    try:
        rows = conn.execute(
            """
            SELECT kt.pack_name AS pack,
                   kt.doc_name  AS doc,
                   COUNT(*)     AS attachments,
                   SUM(CASE WHEN sr.status = 'complete' THEN 1 ELSE 0 END) AS successes
              FROM knowledge_telemetry kt
              LEFT JOIN step_results sr
                ON sr.task_id = kt.task_id AND sr.step_id = kt.step_id
             GROUP BY kt.pack_name, kt.doc_name
            """
        ).fetchall()
    finally:
        conn.close()

    out: list[DocContribution] = []
    for r in rows:
        attachments = int(r["attachments"] or 0)
        successes = int(r["successes"] or 0)
        out.append(
            DocContribution(
                pack=r["pack"] or "",
                doc=r["doc"] or "",
                attachment_count=attachments,
                success_count=successes,
                contribution_score=_safe_rate(successes, attachments),
            )
        )

    out.sort(key=lambda d: (-d.contribution_score, -d.attachment_count))
    return out


# ---------------------------------------------------------------------------
# Metric 4 — Review quality
# ---------------------------------------------------------------------------


def compute_review_quality(
    *,
    project_root: Path | None = None,
) -> list[ReviewerStats]:
    """Compute reviewer-agent verdict distribution + average minutes.

    Scans ``step_results`` whose ``agent_name`` is one of the reviewer
    agents in :data:`_REVIEWER_AGENTS`.  Verdicts are extracted from the
    ``outcome`` column via :func:`extract_verdict_from_text`.

    Returns:
        List of :class:`ReviewerStats` sorted by reviewer name.  Empty DB
        returns ``[]``.
    """
    db_path = _resolve_db(project_root)
    if db_path is None:
        return []

    placeholders = ",".join("?" * len(_REVIEWER_AGENTS))
    conn = _open(db_path)
    try:
        rows = conn.execute(
            f"""
            SELECT agent_name, outcome, duration_seconds
              FROM step_results
             WHERE agent_name IN ({placeholders})
            """,
            tuple(sorted(_REVIEWER_AGENTS)),
        ).fetchall()
    finally:
        conn.close()

    grouped: dict[str, dict] = {}
    for r in rows:
        name = r["agent_name"]
        bucket = grouped.setdefault(
            name,
            {"verdicts": {"APPROVE": 0, "FLAG": 0, "BLOCK": 0, "UNKNOWN": 0},
             "durations": []},
        )
        verdict = extract_verdict_from_text(r["outcome"] or "")
        bucket["verdicts"][_bucket_verdict(verdict)] += 1
        if r["duration_seconds"]:
            bucket["durations"].append(float(r["duration_seconds"]))

    out: list[ReviewerStats] = []
    for name, info in grouped.items():
        verdicts = info["verdicts"]
        total_known = (
            verdicts["APPROVE"] + verdicts["FLAG"] + verdicts["BLOCK"]
        )
        durations = info["durations"]
        avg_seconds = sum(durations) / len(durations) if durations else 0.0
        out.append(
            ReviewerStats(
                reviewer=name,
                verdicts=dict(verdicts),
                block_rate=_safe_rate(verdicts["BLOCK"], total_known),
                approve_rate=_safe_rate(verdicts["APPROVE"], total_known),
                avg_minutes=avg_seconds / 60.0,
            )
        )

    out.sort(key=lambda r: r.reviewer)
    return out


# ---------------------------------------------------------------------------
# Public report bundle (used by the CLI)
# ---------------------------------------------------------------------------


def compute_all_metrics(
    *,
    project_root: Path | None = None,
) -> dict[str, object]:
    """Compute all four metrics and return them as a JSON-friendly dict.

    Used by ``baton improve metrics show`` (default invocation).
    """
    return {
        "spec_effectiveness": compute_spec_effectiveness(
            project_root=project_root
        ),
        "delegation_roi": compute_delegation_roi(project_root=project_root),
        "knowledge_contribution": compute_knowledge_contribution(
            project_root=project_root
        ),
        "review_quality": compute_review_quality(project_root=project_root),
    }


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def to_jsonable(obj: object) -> object:
    """Recursively convert dataclasses + dates into JSON-friendly types."""
    from dataclasses import asdict, is_dataclass

    if is_dataclass(obj):
        return to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {k: to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


def to_json(obj: object) -> str:
    """Serialise the result of any compute_* function to JSON."""
    return json.dumps(to_jsonable(obj), indent=2, sort_keys=True)
