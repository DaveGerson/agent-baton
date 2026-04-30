"""SQLite-backed persistence for ``baton execute handoff`` records (DX.3).

``HandoffStore`` owns reads/writes against the project-local ``handoffs``
table introduced in schema v18.  It mirrors the design of
:class:`agent_baton.core.engine.bead_store.BeadStore`:

- One ``ConnectionManager`` per store, schema configured on first access.
- All SQL uses parameterised queries.
- Methods degrade gracefully when the table is absent (older schema /
  read-only environments) -- they return safe empty values rather than
  raising.

See ``agent_baton/core/improve/handoff_score.py`` for the score model
the CLI uses to compute ``quality_score`` and ``score_breakdown_json``
before calling :meth:`HandoffStore.record`.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from agent_baton.utils.time import utcnow_zulu as _utcnow_iso

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class HandoffRecord:
    """Single row in the ``handoffs`` table.

    The dataclass is the canonical in-memory representation passed to/from
    :class:`HandoffStore`.  ``score_breakdown`` is the unmarshalled form
    of ``score_breakdown_json`` (a ``{heuristic_name: points}`` map).
    """

    handoff_id: str = ""
    task_id: str = ""
    note: str = ""
    branch: str = ""
    commits_ahead: int = 0
    git_dirty: bool = False
    quality_score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow_iso)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class HandoffStore:
    """SQLite-backed persistence for session handoff notes.

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
                "WHERE type='table' AND name='handoffs'"
            ).fetchone()
            return row is not None
        except Exception:  # noqa: BLE001 - defensive
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        task_id: str,
        note: str,
        branch: str = "",
        commits_ahead: int = 0,
        git_dirty: bool = False,
        quality_score: float = 0.0,
        score_breakdown: dict[str, float] | None = None,
        handoff_id: str | None = None,
        created_at: str | None = None,
    ) -> str:
        """Persist a new handoff row and return its ``handoff_id``.

        Returns the empty string and logs a warning when the table is
        absent so callers never crash on stale schemas.
        """
        if not self._table_exists():
            _log.warning(
                "HandoffStore.record: handoffs table not found "
                "(schema v18 not yet applied) -- skipping"
            )
            return ""
        hid = handoff_id or f"ho-{uuid.uuid4().hex[:12]}"
        ts = created_at or _utcnow_iso()
        breakdown_json = json.dumps(score_breakdown or {}, sort_keys=True)
        try:
            conn = self._conn()
            conn.execute(
                """
                INSERT INTO handoffs (
                    handoff_id, task_id, note, branch, commits_ahead,
                    git_dirty, quality_score, score_breakdown_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    hid,
                    task_id or "",
                    note or "",
                    branch or "",
                    int(commits_ahead or 0),
                    1 if git_dirty else 0,
                    float(quality_score or 0.0),
                    breakdown_json,
                    ts,
                ),
            )
            conn.commit()
            return hid
        except Exception as exc:  # noqa: BLE001 - defensive
            _log.warning("HandoffStore.record failed: %s", exc)
            return ""

    def get(self, handoff_id: str) -> HandoffRecord | None:
        """Return the handoff with id *handoff_id*, or ``None`` if absent."""
        if not self._table_exists():
            return None
        try:
            row = self._conn().execute(
                """
                SELECT handoff_id, task_id, note, branch, commits_ahead,
                       git_dirty, quality_score, score_breakdown_json,
                       created_at
                FROM handoffs WHERE handoff_id = ?
                """,
                (handoff_id,),
            ).fetchone()
        except Exception as exc:  # noqa: BLE001 - defensive
            _log.warning("HandoffStore.get failed: %s", exc)
            return None
        if row is None:
            return None
        return _row_to_record(row)

    def list_recent(
        self,
        *,
        task_id: str | None = None,
        limit: int = 20,
    ) -> list[HandoffRecord]:
        """Return up to *limit* most-recent handoffs, newest first.

        When *task_id* is provided the result is filtered to that task.
        """
        if not self._table_exists():
            return []
        try:
            if task_id:
                cur = self._conn().execute(
                    """
                    SELECT handoff_id, task_id, note, branch, commits_ahead,
                           git_dirty, quality_score, score_breakdown_json,
                           created_at
                    FROM handoffs
                    WHERE task_id = ?
                    ORDER BY created_at DESC, handoff_id DESC
                    LIMIT ?
                    """,
                    (task_id, int(limit)),
                )
            else:
                cur = self._conn().execute(
                    """
                    SELECT handoff_id, task_id, note, branch, commits_ahead,
                           git_dirty, quality_score, score_breakdown_json,
                           created_at
                    FROM handoffs
                    ORDER BY created_at DESC, handoff_id DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                )
            return [_row_to_record(r) for r in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001 - defensive
            _log.warning("HandoffStore.list_recent failed: %s", exc)
            return []

    def has_any_for_task(self, task_id: str) -> bool:
        """Return True if at least one handoff exists for *task_id*.

        Used by the auto-print TTY nudge so we only nag the operator
        once per session.
        """
        if not task_id or not self._table_exists():
            return False
        try:
            row = self._conn().execute(
                "SELECT 1 FROM handoffs WHERE task_id = ? LIMIT 1",
                (task_id,),
            ).fetchone()
            return row is not None
        except Exception:  # noqa: BLE001 - defensive
            return False


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------


def _row_to_record(row) -> HandoffRecord:  # type: ignore[no-untyped-def]
    """Map a sqlite3.Row (or tuple) into a :class:`HandoffRecord`."""
    # Support both Row (column access) and plain tuples for portability.
    try:
        get = row.__getitem__
        breakdown_raw = row["score_breakdown_json"]
        rec = HandoffRecord(
            handoff_id=row["handoff_id"] or "",
            task_id=row["task_id"] or "",
            note=row["note"] or "",
            branch=row["branch"] or "",
            commits_ahead=int(row["commits_ahead"] or 0),
            git_dirty=bool(row["git_dirty"]),
            quality_score=float(row["quality_score"] or 0.0),
            created_at=row["created_at"] or "",
        )
    except (KeyError, IndexError, TypeError):
        get = row.__getitem__
        breakdown_raw = get(7)
        rec = HandoffRecord(
            handoff_id=get(0) or "",
            task_id=get(1) or "",
            note=get(2) or "",
            branch=get(3) or "",
            commits_ahead=int(get(4) or 0),
            git_dirty=bool(get(5)),
            quality_score=float(get(6) or 0.0),
            created_at=get(8) or "",
        )
    try:
        rec.score_breakdown = (
            json.loads(breakdown_raw) if breakdown_raw else {}
        )
    except (TypeError, ValueError):
        rec.score_breakdown = {}
    return rec
