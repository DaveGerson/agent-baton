"""ProposalManager — persists Recommendation lifecycle to JSONL.

Recommendations are stored as append-only entries in
``.claude/team-context/improvements/recommendations.jsonl``.
Status transitions: proposed -> applied -> rolled_back (or rejected).
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.models.improvement import Recommendation

_DEFAULT_DIR = Path(".claude/team-context/improvements")


class ProposalManager:
    """Manage the lifecycle of improvement recommendations on disk."""

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
        """Append a recommendation to the JSONL log."""
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

        Valid transitions:
        - proposed -> applied
        - proposed -> rejected
        - applied -> rolled_back

        Returns ``True`` if the recommendation was found and updated.
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
