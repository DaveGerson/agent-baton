"""Wave 6.2 Part B — SweepScheduler: rotating priority queue for immune sweeps.

Persists sweep targets in the per-project ``baton.db`` ``immune_queue`` table
(schema v31).  Returns the oldest-last-swept target weighted by recent change
frequency.

Schema contract:
    ``immune_queue`` is per-project — never stored in central.db.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_baton.utils.time import utcnow_zulu as _utcnow_str

_log = logging.getLogger(__name__)

__all__ = ["SweepScheduler", "SweepTarget"]

# ---------------------------------------------------------------------------
# How far forward to defer after a sweep (design: 30 days no-issue, 7 days issue)
# ---------------------------------------------------------------------------
_DEFER_NO_ISSUE_DAYS = 30
_DEFER_ISSUE_DAYS = 7


def _future_str(days: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SweepTarget:
    """A file+kind combination enqueued for immune scanning.

    Attributes:
        path: Absolute (or project-relative) path to scan.
        kind: Sweep kind, e.g. ``"deprecated-api"``.
        last_swept_at: ISO-8601 UTC timestamp of the last completed sweep.
        priority: Higher value → scanned sooner when timestamps tie.
    """

    path: Path
    kind: str
    last_swept_at: str
    priority: float


# ---------------------------------------------------------------------------
# SweepScheduler
# ---------------------------------------------------------------------------


class SweepScheduler:
    """Rotating priority queue backed by per-project SQLite.

    The scheduling policy combines two signals:

    1. ``last_swept_at`` — oldest first (ISO-8601 sort is stable).
    2. ``priority`` — tie-breaks in favour of higher values (e.g. recently
       changed files can receive a boosted priority).

    The combined index ``idx_immune_queue_priority`` on
    ``(last_swept_at, priority DESC)`` makes ``next_target`` a single
    ``SELECT … LIMIT 1`` with a covering index walk.

    Args:
        project_root: Root directory of the project being swept.
        conn: Open SQLite connection to the project ``baton.db``.
    """

    def __init__(self, project_root: Path, conn: sqlite3.Connection) -> None:
        self._root = project_root
        self._conn = conn
        self._ensure_table()

    # ------------------------------------------------------------------
    # Table bootstrap
    # ------------------------------------------------------------------

    def _ensure_table(self) -> None:
        """Create ``immune_queue`` if the migration hasn't run yet.

        Normally the schema migration in ``storage/schema.py`` creates this
        table.  This guard covers cases where the scheduler is instantiated
        against an older ``baton.db`` that hasn't been migrated.
        """
        try:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS immune_queue (
                    path            TEXT NOT NULL,
                    kind            TEXT NOT NULL,
                    last_swept_at   TEXT NOT NULL,
                    found_issue_at  TEXT,
                    priority        REAL NOT NULL DEFAULT 1.0,
                    PRIMARY KEY (path, kind)
                )
            """)
            self._conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_immune_queue_priority
                    ON immune_queue(last_swept_at, priority DESC)
            """)
            self._conn.commit()
        except sqlite3.Error as exc:
            _log.warning("SweepScheduler: could not ensure immune_queue: %s", exc)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def seed(self, paths: list[Path], kind: str) -> None:
        """Insert *paths* into the queue for *kind* if not already present.

        Rows that already exist are left untouched (``INSERT OR IGNORE``).

        Args:
            paths: Filesystem paths to enqueue.
            kind: Sweep kind (e.g. ``"deprecated-api"``).
        """
        now = _utcnow_str()
        try:
            for p in paths:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO immune_queue
                        (path, kind, last_swept_at, priority)
                    VALUES (?, ?, ?, 1.0)
                    """,
                    (str(p), kind, now),
                )
            self._conn.commit()
        except sqlite3.Error as exc:
            _log.warning("SweepScheduler.seed failed: %s", exc)

    def next_target(self) -> SweepTarget | None:
        """Return the next sweep target or ``None`` if none are ready.

        Selection policy: earliest ``last_swept_at``, then highest
        ``priority``.  Returns ``None`` when the table is empty.
        """
        try:
            row = self._conn.execute(
                """
                SELECT path, kind, last_swept_at, priority
                FROM immune_queue
                ORDER BY last_swept_at ASC, priority DESC
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.Error as exc:
            _log.warning("SweepScheduler.next_target failed: %s", exc)
            return None

        if row is None:
            return None
        return SweepTarget(
            path=Path(row[0]),
            kind=row[1],
            last_swept_at=row[2],
            priority=float(row[3]),
        )

    def mark_swept(self, target: SweepTarget, found_issue: bool) -> None:
        """Update ``last_swept_at`` after a sweep completes.

        Defer policy (from design):
        - No issue found → defer 30 days (next sweep in 30 days).
        - Issue found → defer 7 days (re-check soon).

        Args:
            target: The target that was just swept.
            found_issue: ``True`` when the sweep found a finding.
        """
        defer_days = _DEFER_ISSUE_DAYS if found_issue else _DEFER_NO_ISSUE_DAYS
        next_sweep = _future_str(defer_days)
        found_at = _utcnow_str() if found_issue else None
        try:
            self._conn.execute(
                """
                UPDATE immune_queue
                SET last_swept_at  = ?,
                    found_issue_at = COALESCE(?, found_issue_at)
                WHERE path = ? AND kind = ?
                """,
                (next_sweep, found_at, str(target.path), target.kind),
            )
            self._conn.commit()
        except sqlite3.Error as exc:
            _log.warning(
                "SweepScheduler.mark_swept failed for %s/%s: %s",
                target.path, target.kind, exc,
            )

    def queue_size(self) -> int:
        """Return the total number of entries in the queue."""
        try:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM immune_queue"
            ).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0
