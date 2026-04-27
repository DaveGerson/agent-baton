"""Knowledge A/B testing service (K2.4).

Registers experiments that test two variants of the same knowledge document,
deterministically assigns one variant per (task_id, step_id) pair, records
outcomes, and computes winner statistics.

Engine integration (KnowledgeResolver hookup) is a follow-up task.
"""
from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.models.knowledge_ab import KnowledgeABAssignment, KnowledgeABExperiment

if TYPE_CHECKING:
    pass

_MIN_SAMPLES = 10
_WIN_MARGIN = 0.10  # 10 percentage points


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KnowledgeABService:
    """Manage knowledge document A/B experiments backed by a project baton.db.

    Args:
        store: An object exposing a ``_db_path`` attribute (``Path``) OR a
               ``sqlite3.Connection`` directly.  In practice the caller passes
               a ``BeadStore`` instance (which carries ``_db_path``) or a raw
               connection for tests.
    """

    def __init__(self, store: object) -> None:
        if isinstance(store, sqlite3.Connection):
            self._conn: sqlite3.Connection | None = store
            self._db_path: Path | None = None
        else:
            self._conn = None
            self._db_path = getattr(store, "_db_path", None)

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        if self._db_path is None:
            raise RuntimeError("KnowledgeABService: no database path available")
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _close_if_owned(self, conn: sqlite3.Connection) -> None:
        """Close the connection only when we opened it (not injected)."""
        if self._conn is None:
            conn.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_experiment(
        self,
        knowledge_id: str,
        variant_a_path: str,
        variant_b_path: str,
        split_ratio: float = 0.5,
    ) -> str:
        """Register a new A/B experiment.

        Args:
            knowledge_id:   Canonical pack/doc id (e.g. ``"security/owasp.md"``).
            variant_a_path: Relative path to the A variant document.
            variant_b_path: Relative path to the B variant document.
            split_ratio:    Fraction routed to A (0.0–1.0, default 0.5).

        Returns:
            The new ``experiment_id`` (UUID4 string).
        """
        experiment_id = str(uuid.uuid4())
        started_at = _now_iso()
        conn = self._get_conn()
        try:
            conn.execute(
                """
                INSERT INTO knowledge_ab_experiments
                    (experiment_id, knowledge_id, variant_a_path, variant_b_path,
                     split_ratio, status, started_at, stopped_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?, '')
                """,
                (experiment_id, knowledge_id, variant_a_path, variant_b_path,
                 split_ratio, started_at),
            )
            conn.commit()
        finally:
            self._close_if_owned(conn)
        return experiment_id

    def assign_variant(
        self,
        experiment_id: str,
        task_id: str,
        step_id: str = "",
    ) -> str:
        """Return ``"a"`` or ``"b"`` for this (task_id, step_id) pair.

        The assignment is deterministic: the same inputs always produce the
        same variant.  A new row is inserted on the first call; subsequent
        calls for the same (experiment_id, task_id, step_id) return the
        persisted value.

        Args:
            experiment_id: Target experiment.
            task_id:       Execution task.
            step_id:       Plan step within the task (``""`` for task-level).

        Returns:
            ``"a"`` or ``"b"``.
        """
        conn = self._get_conn()
        try:
            # Idempotent: return existing assignment if present.
            row = conn.execute(
                """
                SELECT variant FROM knowledge_ab_assignments
                WHERE experiment_id = ? AND task_id = ? AND step_id = ?
                """,
                (experiment_id, task_id, step_id),
            ).fetchone()
            if row:
                return row[0]

            # Fetch split_ratio for this experiment.
            exp_row = conn.execute(
                "SELECT split_ratio FROM knowledge_ab_experiments WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            split_ratio: float = exp_row[0] if exp_row else 0.5

            variant = _deterministic_variant(task_id, step_id, split_ratio)
            assigned_at = _now_iso()
            conn.execute(
                """
                INSERT INTO knowledge_ab_assignments
                    (experiment_id, task_id, step_id, variant, assigned_at, outcome)
                VALUES (?, ?, ?, ?, ?, '')
                """,
                (experiment_id, task_id, step_id, variant, assigned_at),
            )
            conn.commit()
        finally:
            self._close_if_owned(conn)
        return variant

    def record_outcome(
        self,
        experiment_id: str,
        task_id: str,
        outcome: str,
        step_id: str = "",
    ) -> None:
        """Persist the outcome for a task's assignment.

        Args:
            experiment_id: Target experiment.
            task_id:       Execution task.
            outcome:       ``"success"`` or ``"failure"``.
            step_id:       Plan step (``""`` for task-level assignments).
        """
        conn = self._get_conn()
        try:
            conn.execute(
                """
                UPDATE knowledge_ab_assignments
                SET outcome = ?
                WHERE experiment_id = ? AND task_id = ? AND step_id = ?
                """,
                (outcome, experiment_id, task_id, step_id),
            )
            conn.commit()
        finally:
            self._close_if_owned(conn)

    def compute_results(self, experiment_id: str) -> dict:
        """Compute success statistics for both variants.

        Returns a dict with keys:
        - ``a_count`` / ``b_count``: total assignments per variant.
        - ``a_success_rate`` / ``b_success_rate``: 0.0–1.0.
        - ``winner``: ``"a"``, ``"b"``, or ``None`` when insufficient data
          or no clear winner (threshold: >=10 samples each, >=10% margin).
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT variant,
                       COUNT(*) AS total,
                       SUM(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS successes
                FROM knowledge_ab_assignments
                WHERE experiment_id = ?
                GROUP BY variant
                """,
                (experiment_id,),
            ).fetchall()
        finally:
            self._close_if_owned(conn)

        stats: dict[str, dict] = {}
        for row in rows:
            stats[row[0]] = {"total": row[1], "successes": row[2]}

        a = stats.get("a", {"total": 0, "successes": 0})
        b = stats.get("b", {"total": 0, "successes": 0})

        a_rate = a["successes"] / a["total"] if a["total"] else 0.0
        b_rate = b["successes"] / b["total"] if b["total"] else 0.0

        winner: str | None = None
        if a["total"] >= _MIN_SAMPLES and b["total"] >= _MIN_SAMPLES:
            if a_rate - b_rate >= _WIN_MARGIN:
                winner = "a"
            elif b_rate - a_rate >= _WIN_MARGIN:
                winner = "b"

        return {
            "a_count": a["total"],
            "b_count": b["total"],
            "a_success_rate": round(a_rate, 4),
            "b_success_rate": round(b_rate, 4),
            "winner": winner,
        }

    def stop_experiment(self, experiment_id: str) -> None:
        """Mark an experiment as stopped.

        Args:
            experiment_id: Target experiment.
        """
        stopped_at = _now_iso()
        conn = self._get_conn()
        try:
            conn.execute(
                """
                UPDATE knowledge_ab_experiments
                SET status = 'stopped', stopped_at = ?
                WHERE experiment_id = ?
                """,
                (stopped_at, experiment_id),
            )
            conn.commit()
        finally:
            self._close_if_owned(conn)

    def list_experiments(self) -> list[KnowledgeABExperiment]:
        """Return all experiments ordered by started_at descending."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT experiment_id, knowledge_id, variant_a_path, variant_b_path,
                       split_ratio, status, started_at, stopped_at
                FROM knowledge_ab_experiments
                ORDER BY started_at DESC
                """,
            ).fetchall()
        finally:
            self._close_if_owned(conn)

        return [
            KnowledgeABExperiment(
                experiment_id=r[0],
                knowledge_id=r[1],
                variant_a_path=r[2],
                variant_b_path=r[3],
                split_ratio=r[4],
                status=r[5],
                started_at=r[6],
                stopped_at=r[7],
            )
            for r in rows
        ]

    def get_experiment(self, experiment_id: str) -> KnowledgeABExperiment | None:
        """Fetch a single experiment by id, or None if not found."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                """
                SELECT experiment_id, knowledge_id, variant_a_path, variant_b_path,
                       split_ratio, status, started_at, stopped_at
                FROM knowledge_ab_experiments
                WHERE experiment_id = ?
                """,
                (experiment_id,),
            ).fetchone()
        finally:
            self._close_if_owned(conn)

        if row is None:
            return None
        return KnowledgeABExperiment(
            experiment_id=row[0],
            knowledge_id=row[1],
            variant_a_path=row[2],
            variant_b_path=row[3],
            split_ratio=row[4],
            status=row[5],
            started_at=row[6],
            stopped_at=row[7],
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _deterministic_variant(task_id: str, step_id: str, split_ratio: float) -> str:
    """Hash (task_id + step_id) to a stable bucket and compare to split_ratio.

    Uses SHA-256 truncated to 8 bytes (64-bit unsigned integer) for a
    uniform distribution with negligible collision probability.
    """
    raw = f"{task_id}:{step_id}".encode()
    digest = hashlib.sha256(raw).digest()
    bucket = int.from_bytes(digest[:8], "big")
    # Normalise to [0, 1)
    normalised = bucket / (2**64)
    return "a" if normalised < split_ratio else "b"
