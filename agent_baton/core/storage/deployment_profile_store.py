"""SQLite store for deployment profiles (R3.8).

All SQL is encapsulated here.  Callers receive and supply
:class:`~agent_baton.models.deployment_profile.DeploymentProfile` instances;
the store handles JSON encoding/decoding of list columns.
"""
from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.models.deployment_profile import DeploymentProfile


class DeploymentProfileStore:
    """CRUD store for the ``deployment_profiles`` table.

    Parameters
    ----------
    connection:
        An open :class:`sqlite3.Connection`.  The caller is responsible for
        lifecycle management (open, close, commit).
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def save(self, profile: "DeploymentProfile") -> None:
        """Insert or replace a deployment profile."""
        row = profile.to_dict()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO deployment_profiles
                (profile_id, name, environment, required_gates,
                 target_slos, allowed_risk_levels, description, created_at)
            VALUES
                (:profile_id, :name, :environment, :required_gates,
                 :target_slos, :allowed_risk_levels, :description, :created_at)
            """,
            row,
        )
        self._conn.commit()

    def delete(self, profile_id: str) -> None:
        """Remove a profile by ID (no-op if absent)."""
        self._conn.execute(
            "DELETE FROM deployment_profiles WHERE profile_id = ?",
            (profile_id,),
        )
        self._conn.commit()

    def attach_to_release(self, release_id: str, profile_id: str) -> None:
        """Set the ``deployment_profile_id`` FK on an existing release row.

        Creates a minimal release row with INSERT OR IGNORE so the FK update
        is safe even if the release was registered by another path.
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO releases (release_id) VALUES (?)",
            (release_id,),
        )
        self._conn.execute(
            "UPDATE releases SET deployment_profile_id = ? WHERE release_id = ?",
            (profile_id, release_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get(self, profile_id: str) -> "DeploymentProfile | None":
        """Return a profile by ID, or *None* if not found."""
        from agent_baton.models.deployment_profile import DeploymentProfile

        cur = self._conn.execute(
            "SELECT * FROM deployment_profiles WHERE profile_id = ?",
            (profile_id,),
        )
        cur.row_factory = sqlite3.Row
        # Re-query with row_factory on cursor level isn't directly possible;
        # use description to build a dict instead.
        row = self._conn.execute(
            "SELECT profile_id, name, environment, required_gates, "
            "target_slos, allowed_risk_levels, description, created_at "
            "FROM deployment_profiles WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            return None
        return DeploymentProfile.from_dict(self._row_to_dict(row))

    def list_all(self) -> list["DeploymentProfile"]:
        """Return all profiles ordered by created_at ascending."""
        from agent_baton.models.deployment_profile import DeploymentProfile

        rows = self._conn.execute(
            "SELECT profile_id, name, environment, required_gates, "
            "target_slos, allowed_risk_levels, description, created_at "
            "FROM deployment_profiles ORDER BY created_at ASC"
        ).fetchall()
        return [DeploymentProfile.from_dict(self._row_to_dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | tuple) -> dict[str, object]:  # type: ignore[type-arg]
        """Convert a sqlite3 row (tuple) to a plain dict using column order."""
        keys = (
            "profile_id",
            "name",
            "environment",
            "required_gates",
            "target_slos",
            "allowed_risk_levels",
            "description",
            "created_at",
        )
        if hasattr(row, "keys"):
            return dict(row)
        return dict(zip(keys, row))
