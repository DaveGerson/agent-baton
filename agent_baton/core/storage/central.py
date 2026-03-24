"""Read-only query interface for ~/.baton/central.db.

CentralStore wraps the central read-replica database and exposes
high-level cross-project analytics views plus a generic read-only
query method.  All write paths go through SyncEngine; this class
will raise ValueError if a mutating SQL statement is attempted
through the ``query`` method.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import CENTRAL_SCHEMA_DDL, SCHEMA_VERSION

_log = logging.getLogger(__name__)

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"

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


class CentralStore:
    """Read-only query interface for ~/.baton/central.db.

    Backed by a ConnectionManager that applies CENTRAL_SCHEMA_DDL on
    first access.  All public query methods are safe to call from any
    thread.  The ``query`` method enforces a read-only guard — it
    raises ValueError for any SQL that contains write keywords.
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
        """Return agent reliability stats from v_agent_reliability.

        Args:
            min_steps: Only return agents with at least this many total steps.
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
        """Return aggregated cost data from v_cost_by_task_type."""
        rows = self._conn().execute(
            """
            SELECT *
              FROM v_cost_by_task_type
             ORDER BY total_tokens DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def recurring_knowledge_gaps(self) -> list[dict]:
        """Return knowledge gaps that appear in 2 or more projects."""
        rows = self._conn().execute(
            """
            SELECT *
              FROM v_recurring_knowledge_gaps
             ORDER BY project_count DESC, description
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def project_failure_rates(self) -> list[dict]:
        """Return per-project failure rates from v_project_failure_rate."""
        rows = self._conn().execute(
            """
            SELECT *
              FROM v_project_failure_rate
             ORDER BY failure_rate DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def external_plan_mapping(self, source_type: str | None = None) -> list[dict]:
        """Return external-item-to-baton-plan mappings.

        Args:
            source_type: Optional filter by external source type (e.g. 'ado').
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
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        return self._conn_mgr.get_connection()
