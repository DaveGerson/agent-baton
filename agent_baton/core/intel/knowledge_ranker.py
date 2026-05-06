"""Knowledge attachment ranker — effectiveness-aware ordering (bd-0184).

Reads the ``v_knowledge_effectiveness`` SQLite view and assigns a composite
score to each candidate :class:`~agent_baton.models.knowledge.KnowledgeAttachment`
so the planner can prefer historically effective documents and avoid stale ones.

Scoring formula (all components 0.0 – 1.0)::

    effectiveness_score = avg_outcome_score   (default 0.5 when no telemetry)
    recency_factor      = max(0, 1 - days_since_modified / stale_after_days)
    usage_factor        = min(1.0, total_uses / 10)
    final               = effectiveness_score * 0.6
                        + recency_factor      * 0.2
                        + usage_factor        * 0.2

Design constraints
------------------
- Best-effort: any DB error, missing view, or missing column returns the
  original list unchanged.  The planner must never be degraded by a ranker
  failure.
- No ML, no similarity scoring.  Pure deterministic arithmetic.
- Backward-compatible: candidates with no telemetry receive ``final ≈ 0.5``
  (the middle of the range), which preserves the original ordering relative
  to other no-telemetry docs.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.models.knowledge import KnowledgeAttachment

logger = logging.getLogger(__name__)

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"

# Weights must sum to 1.0
_W_EFFECTIVENESS = 0.6
_W_RECENCY = 0.2
_W_USAGE = 0.2

# Normalisation constants
_USAGE_CAP = 10          # total_uses >= this → usage_factor = 1.0
_DEFAULT_EFFECTIVENESS = 0.5  # no telemetry → neutral score


@dataclass(frozen=True)
class RankedDoc:
    """A single ranked knowledge attachment with its score breakdown.

    Attributes:
        document_name: Document identifier.
        pack_name: Owning pack name, or empty string for standalone docs.
        effectiveness_score: avg_outcome_score from telemetry (0.0–1.0).
        recency_factor: Penalty for age relative to stale_after_days (0.0–1.0).
        usage_factor: Reliability signal from total_uses (0.0–1.0).
        final_score: Weighted composite score (0.0–1.0).
    """

    document_name: str
    pack_name: str
    effectiveness_score: float
    recency_factor: float
    usage_factor: float
    final_score: float

    def to_dict(self) -> dict[str, object]:
        return {
            "document_name": self.document_name,
            "pack_name": self.pack_name,
            "effectiveness_score": round(self.effectiveness_score, 4),
            "recency_factor": round(self.recency_factor, 4),
            "usage_factor": round(self.usage_factor, 4),
            "final_score": round(self.final_score, 4),
        }


def _compute_final(
    effectiveness_score: float,
    recency_factor: float,
    usage_factor: float,
) -> float:
    return (
        effectiveness_score * _W_EFFECTIVENESS
        + recency_factor * _W_RECENCY
        + usage_factor * _W_USAGE
    )


def _row_to_scores(row: sqlite3.Row) -> tuple[float, float, float]:
    """Extract (effectiveness, recency, usage) from a v_knowledge_effectiveness row."""
    keys = row.keys()

    # effectiveness_score
    avg = row["avg_outcome_score"] if "avg_outcome_score" in keys else None
    effectiveness = float(avg) if avg is not None else _DEFAULT_EFFECTIVENESS

    # recency_factor
    if "days_since_modified" in keys and "stale_after_days" in keys:
        days = row["days_since_modified"]
        stale = row["stale_after_days"]
        if days is not None and stale and int(stale) > 0:
            recency = max(0.0, 1.0 - int(days) / int(stale))
        else:
            recency = 1.0  # unknown age → no staleness penalty
    else:
        recency = 1.0

    # usage_factor
    if "total_uses" in keys:
        uses = row["total_uses"]
        usage = min(1.0, int(uses) / _USAGE_CAP) if uses is not None else 0.0
    else:
        usage = 0.0

    return effectiveness, recency, usage


class KnowledgeRanker:
    """Rank a list of :class:`KnowledgeAttachment` objects by historical effectiveness.

    Args:
        db_path: Path to the SQLite database that contains
            ``v_knowledge_effectiveness``.  Defaults to ``~/.baton/central.db``.

    Example::

        ranker = KnowledgeRanker()
        ranked = ranker.rank(candidates, conn=conn)
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = (db_path or _CENTRAL_DB_DEFAULT).resolve()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def rank(
        self,
        candidate_docs: list[KnowledgeAttachment],
        conn: sqlite3.Connection | None = None,
    ) -> list[KnowledgeAttachment]:
        """Return *candidate_docs* re-ordered by descending final score.

        When no telemetry is available for a candidate, it receives a neutral
        score (``0.5``) so documents with no history sort stably among each
        other without any preference.

        Args:
            candidate_docs: The attachments to rank.  The list is not mutated.
            conn: Optional open SQLite connection.  When ``None`` a new
                connection to ``self._db_path`` is opened automatically.
                Passing a connection is useful in tests (in-memory DBs).

        Returns:
            A new list of the same attachments, sorted highest → lowest score.
            Returns the original list unchanged on any error.
        """
        if not candidate_docs:
            return candidate_docs

        try:
            return self._rank_impl(candidate_docs, conn)
        except Exception as exc:  # noqa: BLE001 — ranker must never crash the planner
            logger.debug("KnowledgeRanker.rank failed (returning input unchanged): %s", exc)
            return candidate_docs

    def rank_all_known(
        self,
        conn: sqlite3.Connection | None = None,
    ) -> list[RankedDoc]:
        """Return scored entries for every document in ``v_knowledge_effectiveness``.

        Used by the ``baton knowledge ranking`` CLI to display the full ranking
        table without needing the planner to be running.

        Args:
            conn: Optional open SQLite connection.

        Returns:
            List of :class:`RankedDoc` sorted by ``final_score`` descending.
            Returns an empty list on any error (e.g. view not yet created).
        """
        try:
            return self._rank_all_impl(conn)
        except Exception as exc:  # noqa: BLE001
            logger.debug("KnowledgeRanker.rank_all_known failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_conn(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _fetch_telemetry(
        self,
        doc_keys: set[tuple[str, str]],
        conn: sqlite3.Connection,
    ) -> dict[tuple[str, str], sqlite3.Row]:
        """Query v_knowledge_effectiveness for the requested (doc_name, pack_name) pairs.

        Returns a mapping of (doc_name, pack_name) → Row for found documents.
        Missing documents simply won't appear in the mapping.
        """
        rows = conn.execute(
            "SELECT * FROM v_knowledge_effectiveness"
        ).fetchall()
        result: dict[tuple[str, str], sqlite3.Row] = {}
        for row in rows:
            key = (row["doc_name"], row["pack_name"] or "")
            if key in doc_keys:
                result[key] = row
        return result

    def _score_attachment(
        self,
        att: KnowledgeAttachment,
        telemetry: dict[tuple[str, str], sqlite3.Row],
    ) -> float:
        """Compute the final score for a single attachment."""
        key = (att.document_name, att.pack_name or "")
        row = telemetry.get(key)
        if row is None:
            # No telemetry → neutral scores
            effectiveness, recency, usage = _DEFAULT_EFFECTIVENESS, 1.0, 0.0
        else:
            effectiveness, recency, usage = _row_to_scores(row)
        return _compute_final(effectiveness, recency, usage)

    def _rank_impl(
        self,
        candidate_docs: list[KnowledgeAttachment],
        conn: sqlite3.Connection | None,
    ) -> list[KnowledgeAttachment]:
        close_after = conn is None
        if conn is None:
            conn = self._open_conn()
        try:
            doc_keys: set[tuple[str, str]] = {
                (att.document_name, att.pack_name or "") for att in candidate_docs
            }
            telemetry = self._fetch_telemetry(doc_keys, conn)
        finally:
            if close_after:
                conn.close()

        scored = [
            (att, self._score_attachment(att, telemetry))
            for att in candidate_docs
        ]
        # Stable descending sort: Python's sort is stable, so equal scores
        # preserve the original order (i.e., the prior priority ordering).
        scored.sort(key=lambda t: t[1], reverse=True)
        return [att for att, _ in scored]

    def _rank_all_impl(
        self,
        conn: sqlite3.Connection | None,
    ) -> list[RankedDoc]:
        close_after = conn is None
        if conn is None:
            conn = self._open_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM v_knowledge_effectiveness"
            ).fetchall()
        finally:
            if close_after:
                conn.close()

        results: list[RankedDoc] = []
        for row in rows:
            effectiveness, recency, usage = _row_to_scores(row)
            final = _compute_final(effectiveness, recency, usage)
            results.append(
                RankedDoc(
                    document_name=row["doc_name"],
                    pack_name=row["pack_name"] or "",
                    effectiveness_score=effectiveness,
                    recency_factor=recency,
                    usage_factor=usage,
                    final_score=final,
                )
            )
        results.sort(key=lambda r: r.final_score, reverse=True)
        return results
