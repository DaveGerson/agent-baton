"""SQLite-backed persistence for improvement-recommendation conflicts (L2.4).

``ConflictStore`` owns reads/writes against the project-local
``improvement_conflicts`` table introduced in schema v19 (bd-362f).  It
mirrors the design of :class:`agent_baton.core.storage.handoff_store.HandoffStore`:

- One ``ConnectionManager`` per store, schema configured on first access.
- All SQL uses parameterised queries.
- Methods degrade gracefully when the table is absent (older schema /
  read-only environments) -- they return safe empty values rather than
  raising.

Velocity-zero contract: this store records detection results only.
``acknowledge()`` flips the ``acknowledged_at`` timestamp -- it does NOT
change the status of the underlying recommendations.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from agent_baton.core.improve.conflict_detection import Conflict

_log = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ConflictStore:
    """SQLite-backed persistence for improvement-conflict records.

    Args:
        db_path: Absolute path to the project's ``baton.db``.
    """

    def __init__(self, db_path: Path) -> None:
        from agent_baton.core.storage.connection import ConnectionManager
        from agent_baton.core.storage.schema import (
            PROJECT_SCHEMA_DDL,
            SCHEMA_VERSION,
        )

        self._conn_mgr = ConnectionManager(db_path)
        self._conn_mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self):  # type: ignore[no-untyped-def]
        return self._conn_mgr.get_connection()

    def _table_exists(self) -> bool:
        try:
            row = self._conn().execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='improvement_conflicts'"
            ).fetchone()
            return row is not None
        except Exception:  # noqa: BLE001 - defensive
            return False

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def record(self, conflict: Conflict) -> str:
        """Persist a single conflict, returning its ``conflict_id``.

        Uses ``INSERT OR REPLACE`` so re-detecting the same logical conflict
        (same ``conflict_id``) is idempotent.  Returns the empty string and
        logs a warning when the table is absent so callers never crash on
        stale schemas.
        """
        if not self._table_exists():
            _log.warning(
                "ConflictStore.record: improvement_conflicts table not found "
                "(schema v19 not yet applied) -- skipping"
            )
            return ""
        try:
            conn = self._conn()
            conn.execute(
                """
                INSERT OR REPLACE INTO improvement_conflicts (
                    conflict_id, rec_ids_json, reason, severity,
                    detected_at, acknowledged_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    conflict.conflict_id,
                    json.dumps(list(conflict.rec_ids), sort_keys=False),
                    conflict.reason or "",
                    conflict.severity or "low",
                    conflict.detected_at or _utcnow_iso(),
                    conflict.acknowledged_at or "",
                ),
            )
            conn.commit()
            return conflict.conflict_id
        except Exception as exc:  # noqa: BLE001 - defensive
            _log.warning("ConflictStore.record failed: %s", exc)
            return ""

    def record_many(self, conflicts: list[Conflict]) -> list[str]:
        """Persist a batch of conflicts.  Returns the list of accepted ids."""
        accepted: list[str] = []
        for c in conflicts:
            cid = self.record(c)
            if cid:
                accepted.append(cid)
        return accepted

    def acknowledge(
        self,
        conflict_id: str,
        *,
        when: str | None = None,
    ) -> bool:
        """Stamp ``acknowledged_at`` on a conflict.  Returns True on success.

        Velocity-zero: this only flips the reviewed flag.  The underlying
        recommendations are not changed -- the operator decides what to do
        next (apply one, reject one, raise the auto-apply threshold, etc.).
        """
        if not self._table_exists():
            return False
        ts = when or _utcnow_iso()
        try:
            conn = self._conn()
            cur = conn.execute(
                "UPDATE improvement_conflicts "
                "SET acknowledged_at = ? WHERE conflict_id = ?",
                (ts, conflict_id),
            )
            conn.commit()
            return cur.rowcount > 0
        except Exception as exc:  # noqa: BLE001 - defensive
            _log.warning("ConflictStore.acknowledge failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def get(self, conflict_id: str) -> Conflict | None:
        """Return the conflict with id *conflict_id*, or ``None``."""
        if not self._table_exists():
            return None
        try:
            row = self._conn().execute(
                """
                SELECT conflict_id, rec_ids_json, reason, severity,
                       detected_at, acknowledged_at
                FROM improvement_conflicts WHERE conflict_id = ?
                """,
                (conflict_id,),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001 - defensive
            _log.warning("ConflictStore.get failed: %s", exc)
            return None
        if row is None:
            return None
        return _row_to_conflict(row)

    def list(
        self,
        *,
        status: str = "all",
        limit: int = 100,
    ) -> list[Conflict]:
        """List conflicts filtered by *status*.

        ``status`` values:

        * ``"active"`` -- ``acknowledged_at`` is empty (default for the CLI).
        * ``"resolved"`` -- ``acknowledged_at`` is set.  (Velocity-zero
          terminology: "resolved" means "operator reviewed" rather than
          "auto-fixed".)
        * ``"all"`` -- no filter.

        Sorted newest-first by ``detected_at`` then ``conflict_id``.
        """
        if not self._table_exists():
            return []
        status_norm = (status or "all").lower()
        try:
            base = (
                "SELECT conflict_id, rec_ids_json, reason, severity, "
                "detected_at, acknowledged_at FROM improvement_conflicts"
            )
            if status_norm == "active":
                sql = base + " WHERE acknowledged_at = '' ORDER BY detected_at DESC, conflict_id DESC LIMIT ?"
            elif status_norm == "resolved":
                sql = base + " WHERE acknowledged_at <> '' ORDER BY detected_at DESC, conflict_id DESC LIMIT ?"
            else:
                sql = base + " ORDER BY detected_at DESC, conflict_id DESC LIMIT ?"
            cur = self._conn().execute(sql, (int(limit),))
            return [_row_to_conflict(r) for r in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001 - defensive
            _log.warning("ConflictStore.list failed: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------


def _row_to_conflict(row) -> Conflict:  # type: ignore[no-untyped-def]
    """Map a sqlite3.Row (or tuple) into a :class:`Conflict`."""
    try:
        cid = row["conflict_id"] or ""
        ids_raw = row["rec_ids_json"]
        reason = row["reason"] or ""
        sev = row["severity"] or "low"
        det = row["detected_at"] or ""
        ack = row["acknowledged_at"] or ""
    except (KeyError, IndexError, TypeError):
        cid = row[0] or ""
        ids_raw = row[1]
        reason = row[2] or ""
        sev = row[3] or "low"
        det = row[4] or ""
        ack = row[5] or ""
    try:
        rec_ids = json.loads(ids_raw) if ids_raw else []
        if not isinstance(rec_ids, list):
            rec_ids = []
    except (TypeError, ValueError):
        rec_ids = []
    return Conflict(
        conflict_id=cid,
        rec_ids=[str(x) for x in rec_ids],
        reason=reason,
        severity=sev,
        detected_at=det,
        acknowledged_at=ack,
    )
