"""ProposalManager -- persists Recommendation lifecycle to JSONL.

The proposal manager is the persistence layer for recommendations produced
by :class:`~agent_baton.core.learn.recommender.Recommender`.  It tracks
each recommendation through its lifecycle:

Status transitions::

    proposed  -->  applied  -->  rolled_back
       |
       +------->  rejected

Recommendations are stored as append-only JSONL entries in
``.claude/team-context/improvements/recommendations.jsonl``.  Status
updates require a full file rewrite (the JSONL format is append-optimized
for recording but must be rewritten for in-place updates).

This module is used by :class:`~agent_baton.core.improve.loop.ImprovementLoop`
to persist and query the recommendation history.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.models.improvement import Recommendation

_DEFAULT_DIR = Path(".claude/team-context/improvements")


class ProposalManager:
    """Manage the lifecycle of improvement recommendations on disk.

    Provides append-only recording for new recommendations and rewrite-based
    status updates.  Supports querying by ID or by status for the
    improvement loop and CLI reporting.
    """

    def __init__(self, improvements_dir: Path | None = None) -> None:
        self._dir = (improvements_dir or _DEFAULT_DIR).resolve()
        self._recs_path = self._dir / "recommendations.jsonl"

    @property
    def recommendations_path(self) -> Path:
        return self._recs_path

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def record(self, rec: Recommendation) -> None:
        """Append a single recommendation to the JSONL log.

        Args:
            rec: The recommendation to persist.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._recs_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")

    def record_many(self, recs: list[Recommendation]) -> None:
        """Append multiple recommendations in one call."""
        if not recs:
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._recs_path.open("a", encoding="utf-8") as f:
            for rec in recs:
                f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")

    def update_status(self, rec_id: str, new_status: str) -> bool:
        """Update the status of a recommendation by rewriting the JSONL file.

        Loads all records, finds the matching ``rec_id``, updates its
        status, and rewrites the entire file.  This is intentionally
        simple at the cost of O(n) rewrites; the recommendation volume
        is low enough that this is not a bottleneck.

        Valid transitions:

        * ``proposed`` -> ``applied``
        * ``proposed`` -> ``rejected``
        * ``applied`` -> ``rolled_back``

        Args:
            rec_id: The recommendation identifier to update.
            new_status: The new status value.

        Returns:
            ``True`` if the recommendation was found and updated;
            ``False`` if no matching ``rec_id`` exists.
        """
        all_recs = self.load_all()
        found = False
        for rec in all_recs:
            if rec.rec_id == rec_id:
                rec.status = new_status
                found = True
                break

        if found:
            self._rewrite(all_recs)
        return found

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def load_all(self) -> list[Recommendation]:
        """Load all recommendations from the JSONL file."""
        if not self._recs_path.exists():
            return []

        recs: list[Recommendation] = []
        for line in self._recs_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                recs.append(Recommendation.from_dict(data))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
        return recs

    def get(self, rec_id: str) -> Recommendation | None:
        """Return a specific recommendation by ID."""
        for rec in self.load_all():
            if rec.rec_id == rec_id:
                return rec
        return None

    def get_by_status(self, status: str) -> list[Recommendation]:
        """Return all recommendations with the given status."""
        return [r for r in self.load_all() if r.status == status]

    def get_applied(self) -> list[Recommendation]:
        """Return all applied recommendations."""
        return self.get_by_status("applied")

    def get_proposed(self) -> list[Recommendation]:
        """Return all proposed (pending) recommendations."""
        return self.get_by_status("proposed")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _rewrite(self, recs: list[Recommendation]) -> None:
        """Rewrite the entire JSONL file with updated records."""
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._recs_path.open("w", encoding="utf-8") as f:
            for rec in recs:
                f.write(json.dumps(rec.to_dict(), ensure_ascii=False) + "\n")
