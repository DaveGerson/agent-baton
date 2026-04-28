"""Release readiness checker (R3.2).

``ReleaseReadinessChecker`` aggregates signals from multiple SQLite tables
to produce a single ``ReleaseReadinessReport``.  Every table query is
wrapped in a try/except so that missing tables (e.g. ``releases``,
``slo_measurements``, ``escalations``) degrade gracefully to zero without
crashing.

Scoring formula (starts at 100, clamped to 0):
  - 5  × open_warnings
  - 15 × open_critical_beads
  - 20 × failed_gates_7d
  - 10 × incomplete_plans
  - 15 × slo_breaches_7d
  - 25 × escalations

Status thresholds:
  - score >= 85  → READY
  - score >= 60  → RISKY
  - score <  60  → BLOCKED
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent_baton.models.release_readiness import ReleaseReadinessReport

_log = logging.getLogger(__name__)

# Score penalty weights
_WEIGHTS: dict[str, int] = {
    "open_warnings": 5,
    "open_critical_beads": 15,
    "failed_gates_7d": 20,
    "incomplete_plans": 10,
    "slo_breaches_7d": 15,
    "escalations": 25,
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _since_iso(days: int) -> str:
    """Return the ISO-8601 UTC boundary for ``days`` ago."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def _status(score: int) -> str:
    if score >= 85:
        return "READY"
    if score >= 60:
        return "RISKY"
    return "BLOCKED"


class ReleaseReadinessChecker:
    """Compute a release readiness report from a project baton.db.

    Args:
        store: An object that exposes a ``connection`` property returning a
               ``sqlite3.Connection``, **or** a raw ``sqlite3.Connection``,
               **or** a ``Path`` to a baton.db file.  When a ``Path`` is
               supplied the checker opens its own connection so it can be
               used standalone (e.g. in tests) without any storage class.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        """Resolve the underlying connection from whatever *store* was given."""
        # Raw sqlite3 connection
        if isinstance(self._store, sqlite3.Connection):
            return self._store

        # Path → open our own connection
        if isinstance(self._store, Path):
            conn = sqlite3.connect(str(self._store))
            conn.row_factory = sqlite3.Row
            return conn

        # Duck-typed store with a .connection property
        if hasattr(self._store, "connection"):
            conn = self._store.connection
            if callable(conn):
                return conn()
            return conn

        # Duck-typed store with a ._conn() method (SqliteStorage / BeadStore)
        if hasattr(self._store, "_conn"):
            return self._store._conn()

        # Duck-typed store with a ._conn_mgr attribute
        if hasattr(self._store, "_conn_mgr"):
            return self._store._conn_mgr.get_connection()

        raise TypeError(
            f"Cannot resolve SQLite connection from store type {type(self._store)}"
        )

    def _query_int(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Execute *sql* and return the first column of the first row as int.

        Returns 0 on any error (table missing, schema mismatch, etc.).
        """
        try:
            conn = self._conn()
            row = conn.execute(sql, params).fetchone()
            if row is None:
                return 0
            return int(row[0] or 0)
        except Exception as exc:
            _log.debug("readiness query soft-skipped: %s | sql=%.80s", exc, sql)
            return 0

    def _query_rows(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[dict[str, Any]]:
        """Execute *sql* and return rows as list of dicts.

        Returns [] on any error.
        """
        try:
            conn = self._conn()
            cursor = conn.execute(sql, params)
            cols = [d[0] for d in cursor.description]
            return [dict(zip(cols, row)) for row in cursor.fetchall()]
        except Exception as exc:
            _log.debug("readiness rows soft-skipped: %s | sql=%.80s", exc, sql)
            return []

    # ------------------------------------------------------------------
    # Individual signal queries
    # ------------------------------------------------------------------

    def _count_open_warnings(self, release_id: str) -> int:
        """Count open beads of type 'warning'."""
        return self._query_int(
            """
            SELECT COUNT(*) FROM beads
            WHERE bead_type = 'warning'
              AND status = 'open'
            """,
        )

    def _count_open_critical_beads(self, release_id: str) -> int:
        """Count open beads tagged with severity=critical."""
        return self._query_int(
            """
            SELECT COUNT(DISTINCT b.bead_id)
            FROM beads b
            JOIN bead_tags t ON t.bead_id = b.bead_id
            WHERE b.status = 'open'
              AND t.tag = 'severity:critical'
            """,
        )

    def _count_failed_gates(self, release_id: str, since: str) -> int:
        """Count gate_results rows with passed=0 within the window."""
        return self._query_int(
            """
            SELECT COUNT(*) FROM gate_results
            WHERE passed = 0
              AND checked_at >= ?
            """,
            (since,),
        )

    def _count_incomplete_plans(self, release_id: str) -> int:
        """Count plans linked to this release that are not completed.

        Falls back gracefully if the release_id column does not exist on
        the plans table (pre-R3.1 schema).
        """
        # Try release_id column first (R3.1+)
        count = self._query_int(
            """
            SELECT COUNT(*) FROM plans p
            JOIN executions e ON e.task_id = p.task_id
            WHERE p.release_id = ?
              AND e.status != 'complete'
            """,
            (release_id,),
        )
        return count

    def _count_slo_breaches(self, release_id: str, since: str) -> int:
        """Count slo_measurements rows marked as breached within the window."""
        return self._query_int(
            """
            SELECT COUNT(*) FROM slo_measurements
            WHERE breached = 1
              AND measured_at >= ?
            """,
            (since,),
        )

    def _count_escalations(self, release_id: str) -> int:
        """Count open rows in the escalations table.

        Soft-skips if the table is absent.
        """
        return self._query_int(
            """
            SELECT COUNT(*) FROM escalations
            WHERE resolved = 0
            """,
        )

    def _build_breakdown(
        self,
        release_id: str,
        since: str,
        counts: dict[str, int],
    ) -> dict[str, Any]:
        """Build a per-category breakdown dict for operator review."""
        breakdown: dict[str, Any] = {}

        if counts["open_warnings"] > 0:
            rows = self._query_rows(
                """
                SELECT bead_id, content, created_at FROM beads
                WHERE bead_type = 'warning' AND status = 'open'
                ORDER BY created_at DESC LIMIT 5
                """
            )
            breakdown["warnings"] = rows

        if counts["open_critical_beads"] > 0:
            rows = self._query_rows(
                """
                SELECT DISTINCT b.bead_id, b.content, b.created_at
                FROM beads b
                JOIN bead_tags t ON t.bead_id = b.bead_id
                WHERE b.status = 'open' AND t.tag = 'severity:critical'
                ORDER BY b.created_at DESC LIMIT 5
                """
            )
            breakdown["critical_beads"] = rows

        if counts["failed_gates_7d"] > 0:
            rows = self._query_rows(
                """
                SELECT task_id, gate_type, output, checked_at FROM gate_results
                WHERE passed = 0 AND checked_at >= ?
                ORDER BY checked_at DESC LIMIT 5
                """,
                (since,),
            )
            breakdown["failed_gates"] = rows

        return breakdown

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute(
        self, release_id: str, *, since_days: int = 7
    ) -> ReleaseReadinessReport:
        """Compute and return a ``ReleaseReadinessReport`` for *release_id*.

        Args:
            release_id:  The release identifier to evaluate.
            since_days:  Lookback window in days for time-bounded signals.

        Returns:
            A fully-populated ``ReleaseReadinessReport``.
        """
        since = _since_iso(since_days)
        computed_at = _utcnow()

        counts: dict[str, int] = {
            "open_warnings": self._count_open_warnings(release_id),
            "open_critical_beads": self._count_open_critical_beads(release_id),
            "failed_gates_7d": self._count_failed_gates(release_id, since),
            "incomplete_plans": self._count_incomplete_plans(release_id),
            "slo_breaches_7d": self._count_slo_breaches(release_id, since),
            "escalations": self._count_escalations(release_id),
        }

        penalty = sum(
            counts[k] * _WEIGHTS[k]
            for k in _WEIGHTS
        )
        score = max(0, 100 - penalty)
        status = _status(score)
        breakdown = self._build_breakdown(release_id, since, counts)

        return ReleaseReadinessReport(
            release_id=release_id,
            computed_at=computed_at,
            status=status,
            score=score,
            open_warnings=counts["open_warnings"],
            open_critical_beads=counts["open_critical_beads"],
            failed_gates_7d=counts["failed_gates_7d"],
            incomplete_plans=counts["incomplete_plans"],
            slo_breaches_7d=counts["slo_breaches_7d"],
            escalations=counts["escalations"],
            breakdown=breakdown,
        )
