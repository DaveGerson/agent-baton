"""Federated sync engine — one-way push from per-project baton.db to central.db.

SyncEngine reads rows added since the last watermark from a project's
baton.db and writes them into central.db with project_id prepended to
the primary key.  Sync is incremental: only rows with rowid > last_rowid
are transferred.

Design invariants:
- baton.db is the sole write target; central.db is a read-only replica.
- Watermarks are stored in central.db (sync_watermarks table).
- AUTOINCREMENT id columns in project tables are dropped on insert into
  central.db so that central generates its own sequence.
- Sync is idempotent: pushing the same data twice produces no duplicates
  thanks to INSERT OR REPLACE (for natural-PK tables) or INSERT OR IGNORE
  (for AUTOINCREMENT tables where dedup relies on a UNIQUE constraint).
"""
from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

_log = logging.getLogger(__name__)

_CENTRAL_DB_DEFAULT = Path.home() / ".baton" / "central.db"


# ---------------------------------------------------------------------------
# Table specification
# ---------------------------------------------------------------------------


@dataclass
class SyncTableSpec:
    """Metadata for syncing one table from project DB to central DB.

    Attributes:
        name: Table name (same in both databases).
        pk_columns: Primary-key columns in the *project* table,
            excluding project_id (which central adds).
        has_autoincrement_pk: True when the table has a single INTEGER
            PRIMARY KEY AUTOINCREMENT column (named ``id``).  On insert
            into central the ``id`` column is skipped so central assigns
            its own sequence.
        watermark_column: Column used as the monotonic watermark.
            Almost always ``rowid``; override only when rowid is not
            available (e.g. WITHOUT ROWID tables — none exist here).
    """

    name: str
    pk_columns: list[str]
    has_autoincrement_pk: bool = False
    watermark_column: str = "rowid"


# Tables listed in FK dependency order so that parent rows are synced before
# child rows (avoids transient FK violations if the target ever enables them).
SYNCABLE_TABLES: list[SyncTableSpec] = [
    # -- Leaf tables (no FK parents in the syncable set) ------------------
    SyncTableSpec("executions", ["task_id"]),
    SyncTableSpec("usage_records", ["task_id"]),
    SyncTableSpec("retrospectives", ["task_id"]),
    SyncTableSpec("traces", ["task_id"]),
    SyncTableSpec("learned_patterns", ["pattern_id"]),
    SyncTableSpec("budget_recommendations", ["task_type"]),
    # -- Dependent on executions ------------------------------------------
    SyncTableSpec("plans", ["task_id"]),
    SyncTableSpec("plan_phases", ["task_id", "phase_id"]),
    SyncTableSpec("plan_steps", ["task_id", "step_id"]),
    SyncTableSpec("team_members", ["task_id", "step_id", "member_id"]),
    SyncTableSpec("step_results", ["task_id", "step_id"]),
    SyncTableSpec("team_step_results", ["task_id", "step_id", "member_id"]),
    SyncTableSpec(
        "gate_results",
        ["task_id", "phase_id", "gate_type", "checked_at"],
        has_autoincrement_pk=True,
    ),
    SyncTableSpec(
        "approval_results",
        ["task_id", "phase_id", "result", "decided_at"],
        has_autoincrement_pk=True,
    ),
    SyncTableSpec("amendments", ["task_id", "amendment_id"]),
    SyncTableSpec("events", ["event_id"]),
    SyncTableSpec(
        "agent_usage",
        ["task_id", "agent_name"],
        has_autoincrement_pk=True,
    ),
    SyncTableSpec(
        "telemetry",
        ["task_id", "timestamp", "agent_name", "event_type"],
        has_autoincrement_pk=True,
    ),
    # -- Dependent on retrospectives --------------------------------------
    SyncTableSpec(
        "retrospective_outcomes",
        ["task_id", "category", "agent_name"],
        has_autoincrement_pk=True,
    ),
    SyncTableSpec(
        "knowledge_gaps",
        ["task_id", "description"],
        has_autoincrement_pk=True,
    ),
    SyncTableSpec(
        "roster_recommendations",
        ["task_id", "action", "target"],
        has_autoincrement_pk=True,
    ),
    SyncTableSpec(
        "sequencing_notes",
        ["task_id", "phase", "observation"],
        has_autoincrement_pk=True,
    ),
    # -- Dependent on traces ----------------------------------------------
    SyncTableSpec(
        "trace_events",
        ["task_id", "timestamp", "event_type"],
        has_autoincrement_pk=True,
    ),
    # -- Dependent on executions (misc) -----------------------------------
    SyncTableSpec(
        "mission_log_entries",
        ["task_id", "agent_name", "timestamp"],
        has_autoincrement_pk=True,
    ),
    SyncTableSpec("shared_context", ["task_id"]),
    # -- Bead memory tables (schema v4, Inspired by beads-ai/beads-cli) ------
    # beads must come before bead_tags (FK dependency order).
    SyncTableSpec("beads", ["bead_id"]),
    SyncTableSpec("bead_tags", ["bead_id", "tag"]),
    # -- Learning automation tables (schema v5) ----------------------------
    SyncTableSpec("learning_issues", ["issue_id"]),
]

# Fast lookup by name
_TABLE_SPEC_BY_NAME: dict[str, SyncTableSpec] = {
    s.name: s for s in SYNCABLE_TABLES
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SyncResult:
    """Summary of a single project sync run."""

    project_id: str
    tables_synced: int = 0
    rows_synced: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


# ---------------------------------------------------------------------------
# SyncEngine
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncEngine:
    """One-way incremental sync from per-project baton.db to central.db.

    The sync protocol works as follows:

    1. For each table in ``SYNCABLE_TABLES``, read the last-synced
       ``rowid`` watermark from ``sync_watermarks`` in central.db.
    2. Fetch all rows with ``rowid > watermark`` from the source table
       in the project's baton.db.
    3. Insert them into central.db with ``project_id`` prepended to the
       primary key.  Natural-PK tables use ``INSERT OR REPLACE``;
       auto-increment tables use ``INSERT OR IGNORE`` with a UNIQUE
       constraint on the natural key columns.
    4. Advance the watermark to the maximum ``rowid`` seen.

    Conflict resolution: central.db is a read replica -- the project's
    baton.db is always authoritative.  ``INSERT OR REPLACE`` overwrites
    any stale central row with the latest project data.

    Usage::

        engine = SyncEngine()
        result = engine.push("my-project", Path("...baton.db"))

    The engine is stateless between calls; all state is persisted in
    central.db (``sync_watermarks``, ``sync_history``).

    Attributes:
        _central_path: Resolved path to ``central.db``.
        _conn_mgr: ``ConnectionManager`` for the central database,
            initialized with ``CENTRAL_SCHEMA_DDL``.
    """

    def __init__(self, central_db_path: Path | None = None) -> None:
        from agent_baton.core.storage.connection import ConnectionManager
        from agent_baton.core.storage.schema import CENTRAL_SCHEMA_DDL, SCHEMA_VERSION

        resolved = central_db_path or _CENTRAL_DB_DEFAULT
        self._central_path = resolved
        self._conn_mgr = ConnectionManager(resolved)
        self._conn_mgr.configure_schema(CENTRAL_SCHEMA_DDL, SCHEMA_VERSION)
        _log.debug("SyncEngine initialised with central DB at %s", resolved)

    @property
    def central_db_path(self) -> Path:
        return self._central_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(
        self,
        project_id: str,
        project_db_path: Path,
        *,
        trigger: str = "manual",
    ) -> SyncResult:
        """Incrementally sync all syncable tables from *project_db_path*.

        Only rows whose rowid exceeds the stored watermark are copied.

        Args:
            project_id: Stable identifier for the project (used as PK).
            project_db_path: Absolute path to the project's baton.db.
            trigger: How the sync was initiated (``'manual'``, ``'auto'``,
                or ``'rebuild'``).  Recorded in sync_history for
                observability.

        Returns:
            SyncResult summarising the run.
        """
        result = SyncResult(project_id=project_id)
        started = _utcnow()
        t0 = time.monotonic()

        if not project_db_path.exists():
            msg = f"Project DB not found: {project_db_path}"
            _log.warning(msg)
            result.errors.append(msg)
            result.duration_seconds = time.monotonic() - t0
            return result

        src_conn = self._open_project_db(project_db_path)
        dst_conn = self._conn_mgr.get_connection()

        try:
            for spec in SYNCABLE_TABLES:
                if not self._table_exists(src_conn, spec.name):
                    _log.debug("Table %s not found in %s — skipping", spec.name, project_db_path)
                    continue
                watermark = self._get_watermark(dst_conn, project_id, spec.name)
                try:
                    rows_copied = self._sync_table(
                        src_conn, dst_conn, project_id, spec, watermark
                    )
                    if rows_copied > 0:
                        result.tables_synced += 1
                        result.rows_synced += rows_copied
                        _log.debug(
                            "Synced %d rows from %s.%s (watermark was %d)",
                            rows_copied, project_id, spec.name, watermark,
                        )
                except Exception as exc:  # noqa: BLE001
                    msg = f"{spec.name}: {exc}"
                    _log.error("Sync error for project %s table %s: %s", project_id, spec.name, exc)
                    result.errors.append(msg)

            result.duration_seconds = time.monotonic() - t0
            self._record_history(
                dst_conn,
                project_id=project_id,
                started_at=started,
                completed_at=_utcnow(),
                status="success" if result.success else "partial",
                rows_synced=result.rows_synced,
                tables_synced=result.tables_synced,
                error="; ".join(result.errors),
                trigger=trigger,
            )
        finally:
            src_conn.close()

        return result

    def push_all(self) -> list[SyncResult]:
        """Sync all registered projects from central.db's projects table.

        Returns:
            One SyncResult per registered project.
        """
        dst_conn = self._conn_mgr.get_connection()
        rows = dst_conn.execute("SELECT project_id, path FROM projects").fetchall()
        results: list[SyncResult] = []
        for row in rows:
            project_id = row["project_id"]
            project_path = Path(row["path"])
            db_path = project_path / ".claude" / "team-context" / "baton.db"
            _log.info("push_all: syncing project %s from %s", project_id, db_path)
            results.append(self.push(project_id, db_path))
        return results

    def rebuild(self, project_id: str, project_db_path: Path) -> SyncResult:
        """Delete all central rows for *project_id* and re-sync from scratch.

        Args:
            project_id: Project identifier to rebuild.
            project_db_path: Path to the project's baton.db.

        Returns:
            SyncResult for the fresh sync run.
        """
        dst_conn = self._conn_mgr.get_connection()
        _log.info("rebuild: purging all central rows for project %s", project_id)
        self._delete_project_rows(dst_conn, project_id)
        # Reset all watermarks for this project
        dst_conn.execute(
            "DELETE FROM sync_watermarks WHERE project_id = ?", (project_id,)
        )
        dst_conn.commit()
        return self.push(project_id, project_db_path, trigger="rebuild")

    # ------------------------------------------------------------------
    # Core sync algorithm
    # ------------------------------------------------------------------

    def _sync_table(
        self,
        src_conn: sqlite3.Connection,
        dst_conn: sqlite3.Connection,
        project_id: str,
        spec: SyncTableSpec,
        watermark: int,
    ) -> int:
        """Copy new rows from a project table to the corresponding central table.

        Algorithm:

        1. Read column names from the source table via ``PRAGMA table_info``.
        2. Build a ``SELECT ... WHERE rowid > ?`` query to fetch only
           rows added since the last sync.
        3. For each fetched row, prepend ``project_id`` to the values
           and execute the appropriate INSERT statement into central.db.
        4. After all rows are inserted, advance the watermark in
           ``sync_watermarks``.

        For tables with ``has_autoincrement_pk=True``, the source ``id``
        column is dropped from the INSERT so that central.db assigns its
        own auto-increment sequence.

        Args:
            src_conn: Read-only connection to the project's baton.db.
            dst_conn: Read-write connection to central.db.
            project_id: Project identifier prepended to every row.
            spec: Metadata describing the table being synced.
            watermark: The last ``rowid`` successfully synced.

        Returns:
            Number of rows successfully copied.
        """
        # Read column names from the source table to build the INSERT statement
        # dynamically (avoids breaking when new columns are added).
        src_cols = self._get_column_names(src_conn, spec.name)
        if not src_cols:
            return 0

        # For AUTOINCREMENT tables, skip the 'id' column so central assigns
        # its own sequence.
        if spec.has_autoincrement_pk:
            insert_cols = [c for c in src_cols if c != "id"]
        else:
            insert_cols = list(src_cols)

        # Destination table gets project_id prepended
        dst_insert_cols = ["project_id"] + insert_cols

        placeholders = ", ".join("?" * len(dst_insert_cols))
        dst_col_list = ", ".join(dst_insert_cols)

        if spec.has_autoincrement_pk:
            # Use INSERT OR IGNORE — uniqueness is enforced by the UNIQUE
            # constraint on the natural key columns in the central table.
            insert_sql = (
                f"INSERT OR IGNORE INTO {spec.name} ({dst_col_list}) "
                f"VALUES ({placeholders})"
            )
        else:
            # Natural PK tables: INSERT OR REPLACE handles updates correctly.
            insert_sql = (
                f"INSERT OR REPLACE INTO {spec.name} ({dst_col_list}) "
                f"VALUES ({placeholders})"
            )

        # Always alias the watermark column as _rowid to avoid key conflicts
        # with INTEGER PRIMARY KEY columns (which are aliases for rowid in
        # SQLite and produce duplicate key names when using SELECT rowid, *).
        _ROWID_ALIAS = "_rowid"
        fetch_sql = (
            f"SELECT {spec.watermark_column} AS {_ROWID_ALIAS}, "
            + ", ".join(src_cols)
            + f" FROM {spec.name} WHERE {spec.watermark_column} > ? "
            f"ORDER BY {spec.watermark_column}"
        )

        rows = src_conn.execute(fetch_sql, (watermark,)).fetchall()
        if not rows:
            return 0

        max_rowid = watermark
        count = 0

        for row in rows:
            row_rowid = row[_ROWID_ALIAS]
            if spec.has_autoincrement_pk:
                src_values = [row[c] for c in src_cols if c != "id"]
            else:
                src_values = [row[c] for c in src_cols]

            dst_values = [project_id] + src_values

            try:
                dst_conn.execute(insert_sql, dst_values)
                count += 1
            except sqlite3.IntegrityError as exc:
                _log.debug(
                    "IntegrityError syncing %s row (rowid=%s): %s — skipping",
                    spec.name, row_rowid, exc,
                )

            if row_rowid > max_rowid:
                max_rowid = row_rowid

        dst_conn.commit()
        if max_rowid > watermark:
            self._set_watermark(dst_conn, project_id, spec.name, max_rowid)

        return count

    # ------------------------------------------------------------------
    # Watermark helpers
    # ------------------------------------------------------------------

    def _get_watermark(
        self, dst_conn: sqlite3.Connection, project_id: str, table_name: str
    ) -> int:
        """Return the last-synced rowid for (project_id, table_name)."""
        row = dst_conn.execute(
            "SELECT last_rowid FROM sync_watermarks "
            "WHERE project_id = ? AND table_name = ?",
            (project_id, table_name),
        ).fetchone()
        return row["last_rowid"] if row else 0

    def _set_watermark(
        self,
        dst_conn: sqlite3.Connection,
        project_id: str,
        table_name: str,
        rowid: int,
    ) -> None:
        """Upsert the watermark for (project_id, table_name)."""
        dst_conn.execute(
            """
            INSERT OR REPLACE INTO sync_watermarks
                (project_id, table_name, last_rowid, last_synced)
            VALUES (?, ?, ?, ?)
            """,
            (project_id, table_name, rowid, _utcnow()),
        )
        dst_conn.commit()

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _open_project_db(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(path), timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=OFF")
        return conn

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _get_column_names(conn: sqlite3.Connection, table_name: str) -> list[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [r["name"] for r in rows]

    def _delete_project_rows(
        self, dst_conn: sqlite3.Connection, project_id: str
    ) -> None:
        """Delete all central rows belonging to *project_id*."""
        for spec in SYNCABLE_TABLES:
            if self._table_exists(dst_conn, spec.name):
                dst_conn.execute(
                    f"DELETE FROM {spec.name} WHERE project_id = ?",
                    (project_id,),
                )
        dst_conn.commit()

    def _record_history(
        self,
        dst_conn: sqlite3.Connection,
        *,
        project_id: str,
        started_at: str,
        completed_at: str,
        status: str,
        rows_synced: int,
        tables_synced: int,
        error: str,
        trigger: str = "manual",
    ) -> None:
        """Record a sync run in the ``sync_history`` table for observability.

        Args:
            dst_conn: Connection to central.db.
            project_id: The project that was synced.
            started_at: ISO-8601 timestamp when the sync started.
            completed_at: ISO-8601 timestamp when the sync finished.
            status: ``'success'`` or ``'partial'`` (if errors occurred).
            rows_synced: Total number of rows copied across all tables.
            tables_synced: Number of tables that had new rows.
            error: Semicolon-separated error messages, or empty string.
            trigger: How the sync was initiated (``'manual'``, ``'auto'``,
                or ``'rebuild'``).
        """
        dst_conn.execute(
            """
            INSERT INTO sync_history
                (project_id, started_at, completed_at, status,
                 rows_synced, tables_synced, error, trigger)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                started_at,
                completed_at,
                status,
                rows_synced,
                tables_synced,
                error,
                trigger,
            ),
        )
        dst_conn.commit()


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def auto_sync_current_project() -> SyncResult | None:
    """Resolve the current project from central.db and push an incremental sync.

    Looks up the project whose ``path`` in central.db matches (or is a
    prefix of) the current working directory.  Returns None if no match
    is found or if central.db does not yet exist.
    """
    import os

    central_path = _CENTRAL_DB_DEFAULT
    if not central_path.exists():
        _log.debug("auto_sync_current_project: central.db not found at %s", central_path)
        return None

    cwd = Path(os.getcwd()).resolve()

    engine = SyncEngine(central_path)
    dst_conn = engine._conn_mgr.get_connection()

    rows = dst_conn.execute("SELECT project_id, path FROM projects").fetchall()
    best_project_id: str | None = None
    best_path: Path | None = None

    for row in rows:
        candidate = Path(row["path"]).resolve()
        try:
            cwd.relative_to(candidate)
            # cwd is inside candidate; prefer the longest (most-specific) match
            if best_path is None or len(str(candidate)) > len(str(best_path)):
                best_project_id = row["project_id"]
                best_path = candidate
        except ValueError:
            pass

    if best_project_id is None or best_path is None:
        _log.debug(
            "auto_sync_current_project: no registered project matches cwd %s", cwd
        )
        return None

    db_path = best_path / ".claude" / "team-context" / "baton.db"
    _log.info(
        "auto_sync_current_project: syncing project %s from %s",
        best_project_id, db_path,
    )
    return engine.push(best_project_id, db_path, trigger="auto")
