"""Knowledge lifecycle telemetry store (F0.4).

Records ``KnowledgeUsed`` events into the ``knowledge_telemetry`` SQLite table
and correlates outcomes via ``KnowledgeOutcome`` events from the retrospective
engine.  The view ``v_knowledge_effectiveness`` (defined in v16 migration)
aggregates these rows for the ``baton context effectiveness`` CLI.

This module is designed to be injected as an optional side-channel — callers
pass ``telemetry=KnowledgeTelemetryStore(...)`` and call ``record_used()`` /
``record_outcome()``.  Neither the resolver nor the retrospective engine has a
hard dependency on this module.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KnowledgeTelemetryStore:
    """Persist knowledge usage and outcome events.

    Args:
        db_path: Path to the SQLite database.  Defaults to
            ``~/.baton/central.db``.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = (db_path or _CENTRAL_DB_DEFAULT).resolve()

    def _connect(self) -> sqlite3.Connection:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record_used(
        self,
        *,
        doc_name: str,
        pack_name: str = "",
        task_id: str = "",
        step_id: str = "",
        delivery: str = "inline",
    ) -> int:
        """Insert a ``KnowledgeUsed`` event row.

        Args:
            doc_name: Knowledge document name.
            pack_name: Knowledge pack name (empty for standalone docs).
            task_id: Execution task ID.
            step_id: Step ID within the execution.
            delivery: How the document was delivered (``"inline"`` / ``"reference"``).

        Returns:
            The inserted row's ``id``.
        """
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO knowledge_telemetry
                    (doc_name, pack_name, task_id, step_id, used_at, delivery)
                VALUES (?,?,?,?,?,?)
                """,
                (doc_name, pack_name, task_id, step_id, _now_iso(), delivery),
            )
            conn.commit()
        return cur.lastrowid or 0

    def record_outcome(
        self,
        *,
        doc_name: str,
        pack_name: str = "",
        task_id: str = "",
        outcome_correlation: float,
    ) -> int:
        """Update outcome_correlation on the most recent telemetry row for this doc+task.

        Searches for the latest ``knowledge_telemetry`` row matching
        ``doc_name + pack_name + task_id`` and sets ``outcome_correlation``.
        If no matching row exists, inserts a new one with the correlation value.

        Args:
            doc_name: Knowledge document name.
            pack_name: Knowledge pack name.
            task_id: Execution task ID.
            outcome_correlation: Correlation score (0.0 = failed, 1.0 = perfect).

        Returns:
            Number of rows updated (0 or 1).
        """
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE knowledge_telemetry
                SET outcome_correlation = ?
                WHERE id = (
                    SELECT id FROM knowledge_telemetry
                    WHERE doc_name = ? AND pack_name = ? AND task_id = ?
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (outcome_correlation, doc_name, pack_name, task_id),
            )
            if cur.rowcount == 0:
                cur = conn.execute(
                    """
                    INSERT INTO knowledge_telemetry
                        (doc_name, pack_name, task_id, step_id, used_at, delivery, outcome_correlation)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (doc_name, pack_name, task_id, "", _now_iso(), "unknown",
                     outcome_correlation),
                )
            conn.commit()
        return cur.rowcount

    def upsert_doc_meta(
        self,
        doc_name: str,
        pack_name: str = "",
        *,
        last_modified: str = "",
        stale_after_days: int = 90,
    ) -> None:
        """Insert or update ``knowledge_doc_meta`` for a document.

        Args:
            doc_name: Document name.
            pack_name: Pack name.
            last_modified: ISO-8601 last-modified timestamp (git mtime).
            stale_after_days: Number of days before document is considered stale.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO knowledge_doc_meta (doc_name, pack_name, last_modified, stale_after_days)
                VALUES (?,?,?,?)
                ON CONFLICT(doc_name, pack_name) DO UPDATE SET
                    last_modified = excluded.last_modified,
                    stale_after_days = excluded.stale_after_days
                """,
                (doc_name, pack_name, last_modified, stale_after_days),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # Read / analytics
    # ------------------------------------------------------------------

    def effectiveness_summary(self, limit: int = 50) -> list[dict[str, Any]]:
        """Query ``v_knowledge_effectiveness`` for a summary table.

        Args:
            limit: Max rows to return.

        Returns:
            List of dicts with keys: ``doc_name``, ``pack_name``,
            ``total_uses``, ``avg_outcome_score``, ``last_modified``,
            ``stale_after_days``, ``days_since_modified``.
        """
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    f"SELECT * FROM v_knowledge_effectiveness ORDER BY total_uses DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            except sqlite3.OperationalError:
                # View doesn't exist on old DBs (pre-v16); graceful degradation
                return []
        return [dict(r) for r in rows]

    def doc_usage_count(self, doc_name: str, pack_name: str = "") -> int:
        """Return total uses for a specific document.

        Args:
            doc_name: Document name.
            pack_name: Pack name.

        Returns:
            Row count of usage events.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM knowledge_telemetry WHERE doc_name = ? AND pack_name = ?",
                (doc_name, pack_name),
            ).fetchone()
        return row["cnt"] if row else 0
