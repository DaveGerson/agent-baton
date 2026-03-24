"""RollbackManager — restores agents to pre-experiment state via
AgentVersionControl.

Circuit breaker: 3+ rollbacks in 7 days pauses all auto-apply and escalates.
Logs to ``.claude/team-context/improvements/rollbacks.jsonl``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_baton.core.improve.vcs import AgentVersionControl
from agent_baton.models.improvement import Recommendation

_DEFAULT_DIR = Path(".claude/team-context/improvements")
_CIRCUIT_BREAKER_COUNT = 3
_CIRCUIT_BREAKER_WINDOW_DAYS = 7


class RollbackEntry:
    """A single rollback audit record."""

    def __init__(
        self,
        rec_id: str,
        agent_name: str,
        reason: str,
        rolled_back_at: str = "",
    ) -> None:
        self.rec_id = rec_id
        self.agent_name = agent_name
        self.reason = reason
        self.rolled_back_at = rolled_back_at or datetime.now(
            timezone.utc
        ).isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "rec_id": self.rec_id,
            "agent_name": self.agent_name,
            "reason": self.reason,
            "rolled_back_at": self.rolled_back_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RollbackEntry:
        return cls(
            rec_id=data.get("rec_id", ""),
            agent_name=data.get("agent_name", ""),
            reason=data.get("reason", ""),
            rolled_back_at=data.get("rolled_back_at", ""),
        )


class RollbackManager:
    """Manage rollbacks of applied recommendations.

    Rollback is ALWAYS automatic on degradation -- no human approval needed.
    Circuit breaker: 3+ rollbacks in 7 days pauses all auto-apply.
    """

    def __init__(
        self,
        vcs: AgentVersionControl | None = None,
        improvements_dir: Path | None = None,
    ) -> None:
        self._vcs = vcs or AgentVersionControl()
        self._dir = (improvements_dir or _DEFAULT_DIR).resolve()
        self._rollbacks_path = self._dir / "rollbacks.jsonl"

    @property
    def rollbacks_path(self) -> Path:
        return self._rollbacks_path

    # ------------------------------------------------------------------
    # Rollback execution
    # ------------------------------------------------------------------

    def rollback(self, recommendation: Recommendation, reason: str) -> RollbackEntry:
        """Execute a rollback for a recommendation and log the entry.

        If the recommendation targets an agent prompt, restores the latest
        backup via VCS.  For other categories (budget, routing, sequencing),
        the rollback_spec is logged for the caller to act on.

        Returns the created RollbackEntry.
        """
        entry = RollbackEntry(
            rec_id=recommendation.rec_id,
            agent_name=recommendation.target,
            reason=reason,
        )

        # Attempt VCS restore for prompt-related changes
        if recommendation.category == "agent_prompt":
            backups = self._vcs.list_backups(recommendation.target)
            if backups:
                # Find the agent file to restore to
                agent_path = self._vcs.agents_dir / f"{recommendation.target}.md"
                if agent_path.exists() and backups:
                    self._vcs.restore_backup(backups[0], agent_path)

        self._log_rollback(entry)
        return entry

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    def circuit_breaker_tripped(self) -> bool:
        """Return ``True`` if 3+ rollbacks occurred in the last 7 days.

        This indicates systemic issues and auto-apply should be paused.
        """
        recent = self.recent_rollbacks(days=_CIRCUIT_BREAKER_WINDOW_DAYS)
        return len(recent) >= _CIRCUIT_BREAKER_COUNT

    def recent_rollbacks(self, days: int = 7) -> list[RollbackEntry]:
        """Return rollbacks from the last N days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        all_entries = self.load_all()
        recent: list[RollbackEntry] = []
        for entry in all_entries:
            try:
                ts = datetime.fromisoformat(entry.rolled_back_at)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    recent.append(entry)
            except (ValueError, TypeError):
                continue
        return recent

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def load_all(self) -> list[RollbackEntry]:
        """Load all rollback entries from the JSONL file."""
        if not self._rollbacks_path.exists():
            return []

        entries: list[RollbackEntry] = []
        for line in self._rollbacks_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                entries.append(RollbackEntry.from_dict(data))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return entries

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _log_rollback(self, entry: RollbackEntry) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._rollbacks_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
