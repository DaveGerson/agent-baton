"""SQLite-backed persistence for the :class:`Team` registry.

Owns all reads and writes to the ``teams`` table added in schema v15.
Mirrors the graceful-degradation pattern used by
:class:`~agent_baton.core.engine.bead_store.BeadStore`: if the underlying
table is missing (older schema), every method returns a safe empty/None
value and logs a debug line rather than raising.

The registry is keyed by ``(task_id, team_id)``; a single ``leader_agent``
may appear in any number of rows, by design.
"""
from __future__ import annotations

import logging
from pathlib import Path

from agent_baton.models.team import Team

_log = logging.getLogger(__name__)


class TeamRegistry:
    """Persistent team registry for multi-team orchestration.

    Args:
        db_path: Absolute path to the project's ``baton.db``.
    """

    def __init__(self, db_path: Path) -> None:
        from agent_baton.core.storage.connection import ConnectionManager
        from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION

        self._conn_mgr = ConnectionManager(db_path)
        self._conn_mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self):  # type: ignore[return]
        return self._conn_mgr.get_connection()

    def _table_exists(self) -> bool:
        try:
            row = self._conn().execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='teams'"
            ).fetchone()
            return row is not None
        except Exception:
            return False

    @staticmethod
    def _row_to_team(row) -> Team:
        return Team(
            team_id=row["team_id"],
            task_id=row["task_id"],
            step_id=row["step_id"] or "",
            leader_agent=row["leader_agent"] or "",
            leader_member_id=row["leader_member_id"] or "",
            parent_team_id=row["parent_team_id"] or "",
            status=row["status"] or "active",
            created_at=row["created_at"] or "",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_team(
        self,
        *,
        task_id: str,
        team_id: str,
        step_id: str,
        leader_agent: str,
        leader_member_id: str,
        parent_team_id: str = "",
    ) -> Team | None:
        """Insert a new team row and return the persisted :class:`Team`.

        If a row with the same ``(task_id, team_id)`` already exists, the
        existing team is returned unchanged — this is a no-op so callers
        (e.g. ``_team_dispatch_action`` on re-entry) can safely call
        ``create_team`` idempotently.
        """
        if not self._table_exists():
            return None
        try:
            existing = self.get_team(task_id, team_id)
            if existing is not None:
                return existing
            team = Team(
                team_id=team_id,
                task_id=task_id,
                step_id=step_id,
                leader_agent=leader_agent,
                leader_member_id=leader_member_id,
                parent_team_id=parent_team_id,
            )
            conn = self._conn()
            conn.execute(
                """
                INSERT INTO teams (
                    task_id, team_id, step_id, parent_team_id,
                    leader_agent, leader_member_id, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team.task_id,
                    team.team_id,
                    team.step_id,
                    team.parent_team_id,
                    team.leader_agent,
                    team.leader_member_id,
                    team.status,
                    team.created_at,
                ),
            )
            conn.commit()
            return team
        except Exception as exc:
            _log.warning(
                "TeamRegistry.create_team failed for (%s, %s): %s",
                task_id, team_id, exc,
            )
            return None

    def get_team(self, task_id: str, team_id: str) -> Team | None:
        """Look up a single team by composite key."""
        if not self._table_exists():
            return None
        try:
            row = self._conn().execute(
                "SELECT * FROM teams WHERE task_id = ? AND team_id = ?",
                (task_id, team_id),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_team(row)
        except Exception as exc:
            _log.debug("TeamRegistry.get_team failed: %s", exc)
            return None

    def list_teams(
        self,
        task_id: str,
        *,
        leader_agent: str | None = None,
        parent_team_id: str | None = None,
        status: str | None = None,
    ) -> list[Team]:
        """List teams scoped to *task_id* with optional filters.

        Multiple teams may share the same ``leader_agent`` — this is the
        point of the registry, and ``leader_agent`` is not unique.
        """
        if not self._table_exists():
            return []
        try:
            conditions = ["task_id = ?"]
            params: list[object] = [task_id]
            if leader_agent is not None:
                conditions.append("leader_agent = ?")
                params.append(leader_agent)
            if parent_team_id is not None:
                conditions.append("parent_team_id = ?")
                params.append(parent_team_id)
            if status is not None:
                conditions.append("status = ?")
                params.append(status)
            where = " AND ".join(conditions)
            rows = self._conn().execute(
                f"SELECT * FROM teams WHERE {where} ORDER BY created_at ASC",
                params,
            ).fetchall()
            return [self._row_to_team(r) for r in rows]
        except Exception as exc:
            _log.debug("TeamRegistry.list_teams failed: %s", exc)
            return []

    def child_teams(self, task_id: str, parent_team_id: str) -> list[Team]:
        """Return child teams whose ``parent_team_id`` matches."""
        return self.list_teams(task_id, parent_team_id=parent_team_id)

    def has_child_team(self, task_id: str, parent_team_id: str) -> bool:
        """Return True if *parent_team_id* already has at least one child team."""
        return bool(self.child_teams(task_id, parent_team_id))

    def set_status(self, task_id: str, team_id: str, status: str) -> None:
        """Mark a team ``active`` | ``complete`` | ``failed``.

        No-op if the row is absent or the table is missing.
        """
        if not self._table_exists():
            return
        try:
            conn = self._conn()
            conn.execute(
                "UPDATE teams SET status = ? WHERE task_id = ? AND team_id = ?",
                (status, task_id, team_id),
            )
            conn.commit()
        except Exception as exc:
            _log.warning(
                "TeamRegistry.set_status failed for (%s, %s): %s",
                task_id, team_id, exc,
            )
