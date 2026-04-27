"""Profile checker for deployment profiles (R3.8).

:class:`ProfileChecker` compares a release's actual execution state against
the requirements declared by its attached :class:`DeploymentProfile` and
returns a dict of soft warnings — no execution is blocked.

Return structure::

    {
        "missing_gates":     ["test", ...],   # gate types in profile not found passed
        "untracked_slos":    ["p99_latency"], # SLO names in profile not in slo_definitions
        "risk_violations":   ["task-abc"],    # plan_ids whose risk_level is not allowed
    }

All three keys are always present; values are empty lists when the check
passes.  If a required table is missing the checker soft-skips that section
rather than raising an exception.
"""
from __future__ import annotations

import json
import sqlite3


class ProfileChecker:
    """Check a release against its attached deployment profile.

    Parameters
    ----------
    store:
        A :class:`~agent_baton.core.storage.deployment_profile_store.DeploymentProfileStore`
        instance bound to the project database connection.
    """

    def __init__(self, store: "DeploymentProfileStore") -> None:  # type: ignore[name-defined]  # noqa: F821
        from agent_baton.core.storage.deployment_profile_store import (
            DeploymentProfileStore,
        )

        self._store = store
        self._conn: sqlite3.Connection = store._conn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, release_id: str) -> dict[str, list[str]]:
        """Return a warnings dict for *release_id*.

        Keys: ``missing_gates``, ``untracked_slos``, ``risk_violations``.
        All values are lists (empty = OK).
        """
        result: dict[str, list[str]] = {
            "missing_gates": [],
            "untracked_slos": [],
            "risk_violations": [],
        }

        profile = self._get_profile_for_release(release_id)
        if profile is None:
            # No profile attached — nothing to check.
            return result

        result["missing_gates"] = self._check_gates(release_id, profile.required_gates)
        result["untracked_slos"] = self._check_slos(profile.target_slos)
        result["risk_violations"] = self._check_risk(release_id, profile.allowed_risk_levels)
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_profile_for_release(self, release_id: str) -> "DeploymentProfile | None":  # type: ignore[name-defined]  # noqa: F821
        """Look up the profile attached to *release_id* via the releases table."""
        from agent_baton.models.deployment_profile import DeploymentProfile

        if not self._table_exists("releases"):
            return None
        row = self._conn.execute(
            "SELECT deployment_profile_id FROM releases WHERE release_id = ?",
            (release_id,),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return self._store.get(str(row[0]))

    def _check_gates(self, release_id: str, required: list[str]) -> list[str]:
        """Return gate types from *required* that have no passing gate_result.

        Cross-references gate_results rows for all plan task_ids tagged with
        this release_id.  Soft-skips if gate_results or plans tables are missing.
        """
        if not required:
            return []
        if not self._table_exists("gate_results"):
            return list(required)

        # Gather task_ids for plans associated with this release.
        task_ids = self._task_ids_for_release(release_id)

        if not task_ids:
            # No plans linked — all required gates are missing.
            return list(required)

        placeholders = ",".join("?" * len(task_ids))
        rows = self._conn.execute(
            f"SELECT DISTINCT gate_type FROM gate_results "
            f"WHERE task_id IN ({placeholders}) AND passed = 1",
            task_ids,
        ).fetchall()
        passed_types = {r[0] for r in rows}
        return [g for g in required if g not in passed_types]

    def _check_slos(self, target_slos: list[str]) -> list[str]:
        """Return SLO names from *target_slos* absent from slo_definitions.

        Soft-skips if the table doesn't exist (returns all as untracked).
        """
        if not target_slos:
            return []
        if not self._table_exists("slo_definitions"):
            return list(target_slos)

        rows = self._conn.execute(
            "SELECT DISTINCT name FROM slo_definitions"
        ).fetchall()
        known = {r[0] for r in rows}
        return [s for s in target_slos if s not in known]

    def _check_risk(self, release_id: str, allowed: list[str]) -> list[str]:
        """Return plan task_ids whose risk_level is not in *allowed*.

        Soft-skips if the plans table is missing.
        """
        if not allowed:
            return []
        if not self._table_exists("plans"):
            return []

        task_ids = self._task_ids_for_release(release_id)
        if not task_ids:
            return []

        placeholders = ",".join("?" * len(task_ids))
        rows = self._conn.execute(
            f"SELECT task_id, risk_level FROM plans WHERE task_id IN ({placeholders})",
            task_ids,
        ).fetchall()
        allowed_upper = {r.upper() for r in allowed}
        return [r[0] for r in rows if str(r[1]).upper() not in allowed_upper]

    def _task_ids_for_release(self, release_id: str) -> list[str]:
        """Return task_ids of plans associated with *release_id*.

        Looks for a ``release_id`` column on ``plans`` (added by R3.1).
        Falls back to an empty list when the column is absent so all
        callers degrade gracefully.
        """
        if not self._table_exists("plans"):
            return []
        try:
            rows = self._conn.execute(
                "SELECT task_id FROM plans WHERE release_id = ?",
                (release_id,),
            ).fetchall()
            return [r[0] for r in rows]
        except sqlite3.OperationalError:
            # release_id column not yet present on this DB.
            return []

    def _table_exists(self, table: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None
