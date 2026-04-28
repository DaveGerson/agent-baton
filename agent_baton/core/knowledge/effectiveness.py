"""Knowledge effectiveness scoring + ROI per document (K2.2).

Computes per-(pack, document) analytics from the orchestration store:

  * ``attachments``           — how many times a doc was delivered to a step
  * ``successes``             — attachments where the step finished cleanly
                                (status/outcome in COMPLETE_OUTCOMES)
  * ``failures``              — attachments where the step was rejected,
                                blocked, or errored
  * ``effectiveness_score``   — successes / attachments (0.0 - 1.0)
  * ``tokens_consumed``       — sum of token estimates across attachments
  * ``roi_score``             — (successes - 0.3 * failures) / kilo-tokens
  * ``last_used``             — ISO-8601 date of the most recent attachment

The module is *read-only*: it does not mutate the telemetry store and never
deletes a document (that is K2.3's job).

Data source
-----------
The default reader (``SqliteTelemetryReader``) joins
``plan_steps.knowledge_attachments`` with ``step_results`` in the local
``baton.db``.  This avoids depending on the planned F0.4
``KnowledgeTelemetryStore`` while still exposing a swappable
``KnowledgeTelemetryStore`` protocol so that — once F0.4 lands — callers
may inject a richer telemetry source without touching the scoring logic.

The protocol contract is intentionally minimal::

    class KnowledgeTelemetryStore(Protocol):
        def iter_attachments(self) -> Iterable[AttachmentRecord]: ...

Only the iterator is required; the rest is computed by ``compute_effectiveness``.

Design notes
------------
* Stdlib only — no pandas / sqlalchemy dependencies.
* All public APIs use ``from __future__ import annotations`` and are fully
  type-hinted.
* ``StaleDoc`` flags two failure modes (age and low effectiveness) so K2.3
  can act on either signal.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Protocol


# ---------------------------------------------------------------------------
# Outcome classification
# ---------------------------------------------------------------------------

# A step "succeeds" for knowledge-effectiveness purposes when its persisted
# status or outcome falls in this set.  We accept both because step_results
# carries both fields and downstream writers historically populated one or
# the other.
COMPLETE_OUTCOMES: frozenset[str] = frozenset(
    {"complete", "approved", "passed", "success"}
)

FAILURE_OUTCOMES: frozenset[str] = frozenset(
    {"rejected", "blocked", "error", "failed", "failure"}
)

# Default ROI penalty weight for failures.  Pulled from the spec; exposed
# as a module constant so tests and future tuning can rebalance without a
# code change.
ROI_FAILURE_WEIGHT: float = 0.3


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttachmentRecord:
    """A single (pack, document) attachment observation.

    One ``AttachmentRecord`` represents one time a document was attached to
    a step, plus the recorded outcome of that step.  ``outcome`` and
    ``status`` are normalised to lowercase strings; ``token_estimate`` is
    the planner's estimate at attach time.

    Attributes
    ----------
    pack_name:
        Owning pack name, or ``None`` for standalone documents.
    document_name:
        Name of the knowledge document.
    token_estimate:
        Planner's token estimate for this attachment.
    outcome:
        Free-text outcome string from ``step_results.outcome`` (lowercased).
    status:
        Persisted status from ``step_results.status`` (lowercased).
    completed_at:
        ISO-8601 timestamp of attachment / step completion (``""`` if absent).
    """

    pack_name: str | None
    document_name: str
    token_estimate: int
    outcome: str
    status: str
    completed_at: str


@dataclass
class DocEffectiveness:
    """Effectiveness rollup for a single (pack, document) pair."""

    pack_name: str | None
    document_name: str
    attachments: int = 0
    successes: int = 0
    failures: int = 0
    effectiveness_score: float = 0.0
    tokens_consumed: int = 0
    roi_score: float = 0.0
    last_used: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StaleDoc:
    """A doc flagged as a stale-deletion candidate.

    The ``reasons`` list explains why the doc is stale ("age" and / or
    "low_effectiveness"), so K2.3 can apply different treatments per
    signal.
    """

    pack_name: str | None
    document_name: str
    last_used: str
    attachments: int
    effectiveness_score: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Telemetry-store protocol + default SQLite reader
# ---------------------------------------------------------------------------


class KnowledgeTelemetryStore(Protocol):
    """Minimal read interface for knowledge telemetry.

    F0.4 will provide a richer store with ``record_used`` / ``record_outcome``
    write methods.  K2.2 only needs the read side.
    """

    def iter_attachments(self) -> Iterable[AttachmentRecord]:  # pragma: no cover
        ...


class SqliteTelemetryReader:
    """Default ``KnowledgeTelemetryStore`` reader against ``baton.db``.

    Joins ``plan_steps.knowledge_attachments`` (a JSON array) with
    ``step_results`` to materialise one ``AttachmentRecord`` per
    (step, attachment) pair.  Never writes.

    Parameters
    ----------
    db_path:
        Path to the project's ``baton.db``.  When ``None``, the standard
        ``.claude/team-context/baton.db`` location is used (relative to
        the current working directory).
    since:
        Optional cutoff — only attachments whose step completed at or after
        this UTC datetime are included.  Pass ``None`` for no cutoff.
    """

    DEFAULT_DB_RELATIVE = Path(".claude/team-context/baton.db")

    def __init__(
        self,
        db_path: Path | str | None = None,
        since: datetime | None = None,
    ) -> None:
        self._db_path = (
            Path(db_path)
            if db_path is not None
            else self.DEFAULT_DB_RELATIVE.resolve()
        )
        self._since = since

    def iter_attachments(self) -> Iterator[AttachmentRecord]:
        if not Path(self._db_path).exists():
            return
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            # Older databases may not have step_results / knowledge_attachments
            # yet; degrade silently.
            try:
                cursor = conn.execute(
                    """
                    SELECT
                        ps.knowledge_attachments AS attachments_json,
                        sr.status                AS status,
                        sr.outcome               AS outcome,
                        sr.completed_at          AS completed_at
                    FROM plan_steps ps
                    LEFT JOIN step_results sr
                      ON sr.task_id = ps.task_id AND sr.step_id = ps.step_id
                    """
                )
            except sqlite3.OperationalError:
                return

            since_iso = self._since.isoformat() if self._since else None
            for row in cursor:
                raw = row["attachments_json"] or "[]"
                try:
                    attachments = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                if not isinstance(attachments, list):
                    continue
                completed_at = row["completed_at"] or ""
                if since_iso and completed_at and completed_at < since_iso:
                    continue
                status = (row["status"] or "").lower()
                outcome = (row["outcome"] or "").lower()
                for att in attachments:
                    if not isinstance(att, dict):
                        continue
                    yield AttachmentRecord(
                        pack_name=att.get("pack_name"),
                        document_name=str(att.get("document_name", "")),
                        token_estimate=int(att.get("token_estimate", 0) or 0),
                        outcome=outcome,
                        status=status,
                        completed_at=completed_at,
                    )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _is_success(record: AttachmentRecord) -> bool:
    return (
        record.status in COMPLETE_OUTCOMES
        or record.outcome in COMPLETE_OUTCOMES
    )


def _is_failure(record: AttachmentRecord) -> bool:
    return (
        record.status in FAILURE_OUTCOMES
        or record.outcome in FAILURE_OUTCOMES
    )


def compute_effectiveness(
    pack: str | None = None,
    since_days: int = 30,
    *,
    store: KnowledgeTelemetryStore | None = None,
    db_path: Path | str | None = None,
) -> list[DocEffectiveness]:
    """Compute per-document effectiveness + ROI scores.

    Parameters
    ----------
    pack:
        If set, restrict the result to documents in this pack name.
        ``None`` returns all packs (including standalone docs).
    since_days:
        Only include attachments whose recorded ``completed_at`` is within
        the last ``since_days`` days.  ``0`` disables the cutoff.
    store:
        Optional custom ``KnowledgeTelemetryStore`` (e.g. an in-memory test
        double or a future F0.4 store).  Defaults to a
        ``SqliteTelemetryReader`` rooted at ``db_path``.
    db_path:
        Override path for the default SQLite reader.  Ignored when
        ``store`` is supplied.

    Returns
    -------
    list[DocEffectiveness]
        One row per (pack_name, document_name).  Sorted by ROI score
        descending so the most valuable docs surface first.
    """
    since: datetime | None = None
    if since_days and since_days > 0:
        since = datetime.now(timezone.utc) - timedelta(days=since_days)

    reader = store if store is not None else SqliteTelemetryReader(
        db_path=db_path, since=since
    )

    rollup: dict[tuple[str | None, str], DocEffectiveness] = {}
    last_seen: dict[tuple[str | None, str], str] = {}

    for record in reader.iter_attachments():
        if pack is not None and record.pack_name != pack:
            continue
        # When the store does not enforce ``since`` itself (e.g. a test
        # double), filter here as well.
        if since is not None and record.completed_at:
            if record.completed_at < since.isoformat():
                continue

        key = (record.pack_name, record.document_name)
        eff = rollup.get(key)
        if eff is None:
            eff = DocEffectiveness(
                pack_name=record.pack_name,
                document_name=record.document_name,
            )
            rollup[key] = eff

        eff.attachments += 1
        eff.tokens_consumed += max(0, record.token_estimate)
        if _is_success(record):
            eff.successes += 1
        elif _is_failure(record):
            eff.failures += 1

        prev = last_seen.get(key, "")
        if record.completed_at > prev:
            last_seen[key] = record.completed_at

    results: list[DocEffectiveness] = []
    for key, eff in rollup.items():
        if eff.attachments > 0:
            eff.effectiveness_score = round(eff.successes / eff.attachments, 4)
        kilo = eff.tokens_consumed / 1000.0
        if kilo > 0:
            eff.roi_score = round(
                (eff.successes - ROI_FAILURE_WEIGHT * eff.failures) / kilo, 4
            )
        else:
            # No token cost recorded — ROI is undefined; treat as the
            # net benefit (sucesses minus weighted failures).  This keeps
            # zero-cost docs from being penalised.
            eff.roi_score = round(
                float(eff.successes) - ROI_FAILURE_WEIGHT * eff.failures, 4
            )
        eff.last_used = last_seen.get(key, "")
        results.append(eff)

    results.sort(
        key=lambda r: (r.roi_score, r.effectiveness_score, r.attachments),
        reverse=True,
    )
    return results


def find_stale_docs(
    threshold_days: int = 90,
    *,
    min_attachments_for_low_eff: int = 10,
    low_effectiveness_threshold: float = 0.3,
    store: KnowledgeTelemetryStore | None = None,
    db_path: Path | str | None = None,
) -> list[StaleDoc]:
    """Return docs that should be reviewed for deletion (foreshadows K2.3).

    A document is *stale* when EITHER:

      * its ``last_used`` is older than ``threshold_days`` (or it has been
        attached but never associated with a recorded outcome timestamp);
      * OR its effectiveness score is below ``low_effectiveness_threshold``
        AND it has at least ``min_attachments_for_low_eff`` attachments
        (i.e. enough samples for the score to be meaningful).

    The returned ``StaleDoc.reasons`` list explains which signals fired.

    Parameters
    ----------
    threshold_days:
        Age cutoff in days for the "age" signal.  Pass ``0`` to disable.
    min_attachments_for_low_eff:
        Minimum attachments before the low-effectiveness signal is allowed
        to fire.  Avoids over-flagging brand-new docs.
    low_effectiveness_threshold:
        Effectiveness ratio below which the low-effectiveness signal fires.
    store:
        Optional custom telemetry store; see ``compute_effectiveness``.
    db_path:
        Override path for the default SQLite reader.
    """
    # Use full history (since_days=0) so age comparisons are not clipped
    # by the rolling window.
    rows = compute_effectiveness(
        since_days=0, store=store, db_path=db_path
    )

    cutoff_iso = ""
    if threshold_days and threshold_days > 0:
        cutoff_iso = (
            datetime.now(timezone.utc) - timedelta(days=threshold_days)
        ).isoformat()

    stale: list[StaleDoc] = []
    for row in rows:
        reasons: list[str] = []

        if cutoff_iso:
            # No last_used recorded means we never observed an outcome;
            # treat that as "age" stale only when the doc has been
            # attached at least once.
            if row.attachments > 0 and (
                not row.last_used or row.last_used < cutoff_iso
            ):
                reasons.append("age")

        if (
            row.attachments >= min_attachments_for_low_eff
            and row.effectiveness_score < low_effectiveness_threshold
        ):
            reasons.append("low_effectiveness")

        if reasons:
            stale.append(
                StaleDoc(
                    pack_name=row.pack_name,
                    document_name=row.document_name,
                    last_used=row.last_used,
                    attachments=row.attachments,
                    effectiveness_score=row.effectiveness_score,
                    reasons=reasons,
                )
            )

    return stale
