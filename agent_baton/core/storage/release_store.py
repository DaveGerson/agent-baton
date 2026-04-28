"""SQLite-backed store for :class:`Release` entities and plan tagging (R3.1).

Provides minimal CRUD on the ``releases`` table plus tag/untag helpers that
toggle ``plans.release_id`` for already-persisted plans.  Tagging is purely
metadata — no execution/gating side-effects in this layer (R3.5 will add
freeze gating later).

Database tables accessed:
    ``releases``  -- PK ``release_id``.
    ``plans``     -- updates only the ``release_id`` column.

The store opens its own ``ConnectionManager`` against ``baton.db`` and
configures the project schema, so it can be used standalone (CLI commands,
tests) without depending on :class:`SqliteStorage` first having initialised
the schema.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION
from agent_baton.models.release import RELEASE_STATUSES, Release


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReleaseStore:
    """SQLite-backed CRUD + plan-tagging for :class:`Release` entities."""

    def __init__(self, db_path: Path) -> None:
        self._conn_mgr = ConnectionManager(db_path)
        self._conn_mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)

    @property
    def db_path(self) -> Path:
        return self._conn_mgr.db_path

    def close(self) -> None:
        self._conn_mgr.close()

    def _conn(self) -> sqlite3.Connection:
        return self._conn_mgr.get_connection()

    # ------------------------------------------------------------------
    # Release CRUD
    # ------------------------------------------------------------------

    def create(self, release: Release) -> str:
        """Insert (or replace) *release* and return its ``release_id``.

        Uses ``INSERT OR REPLACE`` so re-creating an existing release is
        idempotent — useful for ``baton release create --id v2.5.0`` retries.
        """
        if not release.created_at:
            release.created_at = _utcnow()
        conn = self._conn()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO releases
                    (release_id, name, target_date, status, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    release.release_id,
                    release.name,
                    release.target_date,
                    release.status,
                    release.notes,
                    release.created_at,
                ),
            )
        return release.release_id

    def get(self, release_id: str) -> Release | None:
        """Fetch a release by id; ``None`` when not found."""
        conn = self._conn()
        row = conn.execute(
            """
            SELECT release_id, name, target_date, status, notes, created_at
            FROM releases
            WHERE release_id = ?
            """,
            (release_id,),
        ).fetchone()
        if row is None:
            return None
        return Release(
            release_id=row["release_id"],
            name=row["name"],
            target_date=row["target_date"],
            status=row["status"],
            notes=row["notes"],
            created_at=row["created_at"],
        )

    def list(self, status: str | None = None) -> list[Release]:
        """List releases, optionally filtered by ``status``.

        Ordered by ``target_date`` ascending (empty target_date sorts last)
        then by ``release_id`` for stable output.
        """
        conn = self._conn()
        if status is not None:
            cur = conn.execute(
                """
                SELECT release_id, name, target_date, status, notes, created_at
                FROM releases
                WHERE status = ?
                ORDER BY (target_date = '') ASC, target_date ASC, release_id ASC
                """,
                (status,),
            )
        else:
            cur = conn.execute(
                """
                SELECT release_id, name, target_date, status, notes, created_at
                FROM releases
                ORDER BY (target_date = '') ASC, target_date ASC, release_id ASC
                """
            )
        return [
            Release(
                release_id=r["release_id"],
                name=r["name"],
                target_date=r["target_date"],
                status=r["status"],
                notes=r["notes"],
                created_at=r["created_at"],
            )
            for r in cur.fetchall()
        ]

    def update_status(self, release_id: str, new_status: str) -> bool:
        """Update a release's lifecycle status.

        Returns ``True`` when a row was updated, ``False`` when the release
        does not exist.  Raises :class:`ValueError` for invalid status
        values so callers fail fast on typos.
        """
        if new_status not in RELEASE_STATUSES:
            raise ValueError(
                f"invalid status {new_status!r}; "
                f"expected one of {RELEASE_STATUSES}"
            )
        conn = self._conn()
        with conn:
            cur = conn.execute(
                "UPDATE releases SET status = ? WHERE release_id = ?",
                (new_status, release_id),
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Plan tagging
    # ------------------------------------------------------------------

    def tag_plan(self, plan_id: str, release_id: str) -> bool:
        """Tag a plan with *release_id*.

        Returns ``True`` when the update affected a row.  The release does
        not need to exist (foreign-key enforcement is the soft-FK column
        only) but for ergonomics callers may verify with :meth:`get` first.
        """
        conn = self._conn()
        with conn:
            cur = conn.execute(
                "UPDATE plans SET release_id = ? WHERE task_id = ?",
                (release_id, plan_id),
            )
        return cur.rowcount > 0

    def untag_plan(self, plan_id: str) -> bool:
        """Clear *plan_id*'s release tag.  Returns True when a row updated."""
        conn = self._conn()
        with conn:
            cur = conn.execute(
                "UPDATE plans SET release_id = NULL WHERE task_id = ?",
                (plan_id,),
            )
        return cur.rowcount > 0

    def list_plans_for_release(self, release_id: str) -> list[dict]:
        """Return ``[{task_id, task_summary, risk_level, created_at}, ...]``
        for every plan tagged against *release_id*.  Used by ``release show``.
        """
        conn = self._conn()
        cur = conn.execute(
            """
            SELECT task_id, task_summary, risk_level, created_at
            FROM plans
            WHERE release_id = ?
            ORDER BY created_at ASC, task_id ASC
            """,
            (release_id,),
        )
        return [
            {
                "task_id": r["task_id"],
                "task_summary": r["task_summary"],
                "risk_level": r["risk_level"],
                "created_at": r["created_at"],
            }
            for r in cur.fetchall()
        ]
