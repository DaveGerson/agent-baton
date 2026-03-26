"""Read-only query interface for ~/.baton/central.db.

CentralStore wraps the central read-replica database and exposes
high-level cross-project analytics views plus a generic read-only
query method.  All write paths go through SyncEngine; this class
will raise ValueError if a mutating SQL statement is attempted
through the ``query`` method.

PMO migration
-------------
``_maybe_migrate_pmo()`` is called automatically by ``get_pmo_central_store``
on the first access after central.db exists.  It copies every row from the
legacy ``~/.baton/pmo.db`` PMO tables into the equivalent tables in
``central.db``.  A marker file ``~/.baton/.pmo-migrated`` is written on
success so the migration only runs once (idempotent).
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import CENTRAL_SCHEMA_DDL, SCHEMA_VERSION

_log = logging.getLogger(__name__)

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"
_PMO_DB_DEFAULT = Path.home() / ".baton" / "pmo.db"
_MIGRATION_MARKER = Path.home() / ".baton" / ".pmo-migrated"

# Tables to copy from pmo.db → central.db PMO tables.
# Each entry: (table_name, column_list)
_PMO_TABLES: list[tuple[str, list[str]]] = [
    (
        "projects",
        [
            "project_id", "name", "path", "program", "color",
            "description", "registered_at", "ado_project",
        ],
    ),
    ("programs", ["name"]),
    (
        "signals",
        [
            "signal_id", "signal_type", "title", "description",
            "source_project_id", "severity", "status",
            "created_at", "resolved_at", "forge_task_id",
        ],
    ),
    (
        "archived_cards",
        [
            "card_id", "project_id", "program", "title", "column_name",
            "risk_level", "priority", "agents", "steps_completed",
            "steps_total", "gates_passed", "current_phase", "error",
            "created_at", "updated_at", "external_id",
        ],
    ),
    (
        "forge_sessions",
        [
            "session_id", "project_id", "title", "status",
            "created_at", "completed_at", "task_id", "notes",
        ],
    ),
]

# SQL keywords that indicate a write operation — used by the read-only guard.
_WRITE_KEYWORDS = frozenset(
    [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "CREATE",
        "ALTER",
        "REPLACE",
        "ATTACH",
        "DETACH",
    ]
)


def _maybe_migrate_pmo(
    central_db_path: Path | None = None,
    pmo_db_path: Path | None = None,
    marker_path: Path | None = None,
) -> bool:
    """One-time migration from ~/.baton/pmo.db to central.db PMO tables.

    Copies every row from each PMO table in the source ``pmo.db`` into the
    corresponding table in ``central.db`` using ``INSERT OR REPLACE`` so the
    operation is safe to re-run (idempotent at the row level).

    A marker file is written after a successful migration.  Subsequent calls
    return immediately without touching either database.

    Args:
        central_db_path: Path to central.db.  Defaults to ``~/.baton/central.db``.
        pmo_db_path: Path to the legacy pmo.db.  Defaults to ``~/.baton/pmo.db``.
        marker_path: Path to the migration marker file.
            Defaults to ``~/.baton/.pmo-migrated``.

    Returns:
        ``True`` if rows were actually migrated, ``False`` if migration was
        skipped (marker present or pmo.db does not exist).
    """
    resolved_central = central_db_path or _CENTRAL_DB_DEFAULT
    resolved_pmo = pmo_db_path or _PMO_DB_DEFAULT
    resolved_marker = marker_path or _MIGRATION_MARKER

    # Already migrated?
    if resolved_marker.exists():
        _log.debug("_maybe_migrate_pmo: marker %s present — skipping", resolved_marker)
        return False

    # Nothing to migrate.
    if not resolved_pmo.exists():
        _log.debug("_maybe_migrate_pmo: pmo.db not found at %s — skipping", resolved_pmo)
        resolved_marker.parent.mkdir(parents=True, exist_ok=True)
        resolved_marker.write_text("no-source\n", encoding="utf-8")
        return False

    _log.info(
        "_maybe_migrate_pmo: migrating %s → %s",
        resolved_pmo,
        resolved_central,
    )

    # Open the source pmo.db read-only.
    src = sqlite3.connect(f"file:{resolved_pmo}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    # Open (or create) the destination central.db via CentralStore so that the
    # schema is guaranteed to be initialised first.
    store = CentralStore(resolved_central)
    dst = store._conn()

    total_rows = 0
    try:
        for table, columns in _PMO_TABLES:
            # Check if the table exists in the source db.
            tbl_exists = src.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not tbl_exists:
                _log.debug(
                    "_maybe_migrate_pmo: table %s not in source — skipping", table
                )
                continue

            cols_str = ", ".join(columns)
            placeholders = ", ".join("?" for _ in columns)
            insert_sql = (
                f"INSERT OR REPLACE INTO {table} ({cols_str}) "
                f"VALUES ({placeholders})"
            )

            rows = src.execute(
                f"SELECT {cols_str} FROM {table}"
            ).fetchall()

            for row in rows:
                dst.execute(insert_sql, tuple(row))
            total_rows += len(rows)
            _log.debug(
                "_maybe_migrate_pmo: copied %d rows from %s", len(rows), table
            )

        dst.commit()
    finally:
        src.close()
        store.close()

    # Write marker.
    resolved_marker.parent.mkdir(parents=True, exist_ok=True)
    resolved_marker.write_text(
        f"migrated {total_rows} rows\n", encoding="utf-8"
    )
    _log.info("_maybe_migrate_pmo: done — %d rows migrated", total_rows)
    return True


class CentralStore:
    """Read-only query interface for ``~/.baton/central.db``.

    Backed by a ``ConnectionManager`` that applies ``CENTRAL_SCHEMA_DDL``
    on first access.  All public query methods are safe to call from any
    thread (one WAL-mode connection per thread).

    The ``query`` method enforces a read-only guard -- it raises
    ``ValueError`` for any SQL whose first token is a write keyword.
    The ``execute`` method allows limited writes to the three
    external-source tables only; all other writes must go through
    ``SyncEngine``.

    Attributes:
        _conn_mgr: ``ConnectionManager`` for the central database.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        resolved = db_path or _CENTRAL_DB_DEFAULT
        self._conn_mgr = ConnectionManager(resolved)
        self._conn_mgr.configure_schema(CENTRAL_SCHEMA_DDL, SCHEMA_VERSION)
        _log.debug("CentralStore initialised at %s", resolved)

    @property
    def db_path(self) -> Path:
        return self._conn_mgr.db_path

    def close(self) -> None:
        """Close the connection for the current thread."""
        self._conn_mgr.close()

    # ------------------------------------------------------------------
    # Analytics views
    # ------------------------------------------------------------------

    def agent_reliability(self, min_steps: int = 5) -> list[dict]:
        """Return cross-project agent reliability stats.

        Queries the ``v_agent_reliability`` analytics view, which
        aggregates ``step_results`` across all synced projects.  Results
        are sorted by success rate descending, then total steps descending.

        Args:
            min_steps: Only return agents with at least this many total
                steps (filters out agents with insufficient data).

        Returns:
            List of dicts with keys: ``agent_name``, ``total_steps``,
            ``successful_steps``, ``success_rate``, ``avg_retries``,
            ``avg_duration_seconds``, ``avg_tokens``.
        """
        rows = self._conn().execute(
            """
            SELECT *
              FROM v_agent_reliability
             WHERE total_steps >= ?
             ORDER BY success_rate DESC, total_steps DESC
            """,
            (min_steps,),
        ).fetchall()
        return [dict(r) for r in rows]

    def cost_by_task_type(self) -> list[dict]:
        """Return cross-project token cost data grouped by task type.

        Queries the ``v_cost_by_task_type`` analytics view, which joins
        ``plans`` with ``agent_usage`` to compute token costs per task
        type, sorted by total tokens descending.

        Returns:
            List of dicts with keys: ``task_type_hint``, ``task_count``,
            ``total_tokens``, ``avg_tokens_per_agent``,
            ``total_duration_seconds``, ``project_id``.
        """
        rows = self._conn().execute(
            """
            SELECT *
              FROM v_cost_by_task_type
             ORDER BY total_tokens DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def recurring_knowledge_gaps(self) -> list[dict]:
        """Return knowledge gaps that recur across 2+ projects.

        Queries the ``v_recurring_knowledge_gaps`` analytics view, which
        groups ``knowledge_gaps`` rows by ``(description, affected_agent)``
        and filters to those appearing in at least 2 distinct projects.

        Returns:
            List of dicts with keys: ``description``, ``affected_agent``,
            ``project_count``, ``projects`` (comma-separated project IDs).
        """
        rows = self._conn().execute(
            """
            SELECT *
              FROM v_recurring_knowledge_gaps
             ORDER BY project_count DESC, description
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def project_failure_rates(self) -> list[dict]:
        """Return per-project execution failure rates.

        Queries the ``v_project_failure_rate`` analytics view, which
        counts total vs. failed executions per ``project_id``.

        Returns:
            List of dicts with keys: ``project_id``, ``total_executions``,
            ``failed_executions``, ``failure_rate``.  Sorted by failure
            rate descending.
        """
        rows = self._conn().execute(
            """
            SELECT *
              FROM v_project_failure_rate
             ORDER BY failure_rate DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def external_plan_mapping(self, source_type: str | None = None) -> list[dict]:
        """Return mappings between external work items and baton plans.

        Queries the ``v_external_plan_mapping`` analytics view, which
        joins ``external_mappings``, ``external_sources``,
        ``external_items``, and ``plans`` to provide a unified view of
        which external items are linked to which execution plans.

        Args:
            source_type: Optional filter by external source type
                (e.g. ``'ado'``).  If ``None``, all source types are
                included.

        Returns:
            List of dicts with keys: ``source_id``, ``external_id``,
            ``external_title``, ``external_state``, ``source_type``,
            ``source_name``, ``project_id``, ``task_id``,
            ``mapping_type``, ``plan_summary``.
        """
        if source_type is not None:
            rows = self._conn().execute(
                """
                SELECT *
                  FROM v_external_plan_mapping
                 WHERE source_type = ?
                 ORDER BY project_id, external_id
                """,
                (source_type,),
            ).fetchall()
        else:
            rows = self._conn().execute(
                """
                SELECT *
                  FROM v_external_plan_mapping
                 ORDER BY project_id, external_id
                """
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Generic read-only query
    # ------------------------------------------------------------------

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute an arbitrary read-only SQL statement.

        Args:
            sql: A SELECT (or similar read-only) statement.
            params: Positional bind parameters.

        Raises:
            ValueError: If *sql* contains write keywords (INSERT, UPDATE, etc.).
        """
        first_token = sql.strip().split()[0].upper() if sql.strip() else ""
        if first_token in _WRITE_KEYWORDS:
            raise ValueError(
                f"CentralStore.query is read-only; got statement starting with "
                f"'{first_token}'.  Use SyncEngine to write to central.db."
            )
        rows = self._conn().execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Write interface for external-source management
    # ------------------------------------------------------------------

    # Tables that external-source management is permitted to write directly.
    # Synced project tables must always go through SyncEngine.
    _WRITABLE_TABLES = frozenset(
        [
            "external_sources",
            "external_items",
            "external_mappings",
        ]
    )

    def execute(self, sql: str, params: tuple = ()) -> None:
        """Execute a write statement against one of the allowed external-source tables.

        Only INSERT, UPDATE, DELETE, and REPLACE against ``external_sources``,
        ``external_items``, or ``external_mappings`` are permitted.  All other
        write paths must go through SyncEngine.

        Args:
            sql: A DML statement targeting an external-source table.
            params: Positional bind parameters.

        Raises:
            ValueError: If *sql* targets a table outside the allowed set, or
                uses a statement type other than INSERT/UPDATE/DELETE/REPLACE.
        """
        stripped = sql.strip()
        first_token = stripped.split()[0].upper() if stripped else ""
        _ALLOWED_WRITE_KEYWORDS = frozenset(["INSERT", "UPDATE", "DELETE", "REPLACE"])
        if first_token not in _ALLOWED_WRITE_KEYWORDS:
            raise ValueError(
                f"CentralStore.execute only accepts DML statements; "
                f"got '{first_token}'.  Use query() for SELECT statements."
            )
        sql_upper = sql.upper()
        target_allowed = any(t.upper() in sql_upper for t in self._WRITABLE_TABLES)
        if not target_allowed:
            allowed = ", ".join(sorted(self._WRITABLE_TABLES))
            raise ValueError(
                f"CentralStore.execute may only write to external-source tables "
                f"({allowed}).  Use SyncEngine for project data."
            )
        conn = self._conn()
        conn.execute(sql, params)
        conn.commit()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        return self._conn_mgr.get_connection()
