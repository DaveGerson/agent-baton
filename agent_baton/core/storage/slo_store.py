"""SQLite-backed CRUD for the SLO + error-budget tables (O1.5).

This module is the persistence layer for ``SLODefinition``,
``SLOMeasurement``, and ``ErrorBudgetBurn``.  It follows the same
conventions as ``SqliteStorage``:

* one ``ConnectionManager`` per store, configured against
  ``PROJECT_SCHEMA_DDL`` so the SLO tables (added in migration v16) are
  created on fresh databases;
* every public write wraps the operation in ``with conn:`` for implicit
  transaction semantics;
* upsert via ``INSERT OR REPLACE`` for natural-PK rows
  (``slo_definitions``);
* append-only inserts for time-series rows (``slo_measurements``,
  ``error_budget_burns``);
* read methods return typed model instances, not raw rows.

The store is intentionally small -- it holds *no* business logic.
SLI computation, budget-formula evaluation, and burn-rate detection live
in :mod:`agent_baton.core.observe.slo_computer`.
"""
from __future__ import annotations

from pathlib import Path

from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION
from agent_baton.models.slo import (
    ErrorBudgetBurn,
    SLODefinition,
    SLOMeasurement,
)


class SLOStore:
    """Read/write access to the SLO + error-budget tables.

    The store can target any SQLite path -- ``.claude/team-context/baton.db``
    in production, ``:memory:`` in tests.  Schema initialisation is
    delegated to :class:`ConnectionManager`, which applies the standard
    project DDL (including the v16 SLO tables) on first use.

    Attributes:
        _conn_mgr: ``ConnectionManager`` that owns the per-thread
            connection lifecycle.
    """

    def __init__(self, db_path: Path) -> None:
        self._conn_mgr = ConnectionManager(db_path)
        self._conn_mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)

    @property
    def db_path(self) -> Path:
        return self._conn_mgr.db_path

    def close(self) -> None:
        """Close the connection for the current thread."""
        self._conn_mgr.close()

    # ------------------------------------------------------------------
    # SLO definitions
    # ------------------------------------------------------------------

    def upsert_definition(self, definition: SLODefinition) -> None:
        """Insert or replace an SLO definition keyed on ``name``."""
        conn = self._conn_mgr.get_connection()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO slo_definitions
                    (name, sli_query, target, window_days, description)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    definition.name,
                    definition.sli_query,
                    float(definition.target),
                    int(definition.window_days),
                    definition.description,
                ),
            )

    def get_definition(self, name: str) -> SLODefinition | None:
        conn = self._conn_mgr.get_connection()
        row = conn.execute(
            "SELECT name, sli_query, target, window_days, description "
            "FROM slo_definitions WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        return SLODefinition(
            name=row["name"],
            sli_query=row["sli_query"],
            target=float(row["target"]),
            window_days=int(row["window_days"]),
            description=row["description"] or "",
        )

    def list_definitions(self) -> list[SLODefinition]:
        conn = self._conn_mgr.get_connection()
        rows = conn.execute(
            "SELECT name, sli_query, target, window_days, description "
            "FROM slo_definitions ORDER BY name"
        ).fetchall()
        return [
            SLODefinition(
                name=r["name"],
                sli_query=r["sli_query"],
                target=float(r["target"]),
                window_days=int(r["window_days"]),
                description=r["description"] or "",
            )
            for r in rows
        ]

    def delete_definition(self, name: str) -> None:
        conn = self._conn_mgr.get_connection()
        with conn:
            conn.execute("DELETE FROM slo_definitions WHERE name = ?", (name,))

    # ------------------------------------------------------------------
    # SLO measurements
    # ------------------------------------------------------------------

    def insert_measurement(self, measurement: SLOMeasurement) -> int:
        """Append a new measurement; returns the auto-assigned row id."""
        conn = self._conn_mgr.get_connection()
        with conn:
            cur = conn.execute(
                """
                INSERT INTO slo_measurements
                    (slo_name, window_start, window_end, sli_value, target,
                     is_meeting, error_budget_remaining_pct, computed_at,
                     sample_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    measurement.slo_name,
                    measurement.window_start,
                    measurement.window_end,
                    float(measurement.sli_value),
                    float(measurement.target),
                    1 if measurement.is_meeting else 0,
                    float(measurement.error_budget_remaining_pct),
                    measurement.computed_at,
                    int(measurement.sample_size),
                ),
            )
            return int(cur.lastrowid or 0)

    def list_measurements(
        self,
        slo_name: str | None = None,
        limit: int | None = None,
    ) -> list[SLOMeasurement]:
        conn = self._conn_mgr.get_connection()
        sql = (
            "SELECT slo_name, window_start, window_end, sli_value, target, "
            "is_meeting, error_budget_remaining_pct, computed_at, sample_size "
            "FROM slo_measurements"
        )
        params: tuple = ()
        if slo_name is not None:
            sql += " WHERE slo_name = ?"
            params = (slo_name,)
        sql += " ORDER BY computed_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params = (*params, int(limit))
        rows = conn.execute(sql, params).fetchall()
        return [
            SLOMeasurement(
                slo_name=r["slo_name"],
                window_start=r["window_start"],
                window_end=r["window_end"],
                sli_value=float(r["sli_value"]),
                target=float(r["target"]),
                is_meeting=bool(r["is_meeting"]),
                error_budget_remaining_pct=float(r["error_budget_remaining_pct"]),
                computed_at=r["computed_at"],
                sample_size=int(r["sample_size"]),
            )
            for r in rows
        ]

    def latest_measurement(self, slo_name: str) -> SLOMeasurement | None:
        rows = self.list_measurements(slo_name=slo_name, limit=1)
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Error-budget burns
    # ------------------------------------------------------------------

    def insert_burn(self, burn: ErrorBudgetBurn) -> int:
        conn = self._conn_mgr.get_connection()
        with conn:
            cur = conn.execute(
                """
                INSERT INTO error_budget_burns
                    (slo_name, incident_id, burn_rate, budget_consumed_pct,
                     started_at, ended_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    burn.slo_name,
                    burn.incident_id or "",
                    float(burn.burn_rate),
                    float(burn.budget_consumed_pct),
                    burn.started_at,
                    burn.ended_at,
                ),
            )
            burn.id = int(cur.lastrowid or 0)
            return burn.id

    def list_burns(
        self,
        slo_name: str | None = None,
        since: str | None = None,
    ) -> list[ErrorBudgetBurn]:
        conn = self._conn_mgr.get_connection()
        sql = (
            "SELECT id, slo_name, incident_id, burn_rate, budget_consumed_pct, "
            "started_at, ended_at FROM error_budget_burns"
        )
        clauses: list[str] = []
        params: list = []
        if slo_name is not None:
            clauses.append("slo_name = ?")
            params.append(slo_name)
        if since is not None:
            clauses.append("started_at >= ?")
            params.append(since)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY started_at DESC"
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [
            ErrorBudgetBurn(
                slo_name=r["slo_name"],
                incident_id=(r["incident_id"] or None),
                burn_rate=float(r["burn_rate"]),
                budget_consumed_pct=float(r["budget_consumed_pct"]),
                started_at=r["started_at"],
                ended_at=r["ended_at"] or "",
                id=int(r["id"]),
            )
            for r in rows
        ]

    def close_burn(self, burn_id: int, ended_at: str) -> None:
        conn = self._conn_mgr.get_connection()
        with conn:
            conn.execute(
                "UPDATE error_budget_burns SET ended_at = ? WHERE id = ?",
                (ended_at, int(burn_id)),
            )
