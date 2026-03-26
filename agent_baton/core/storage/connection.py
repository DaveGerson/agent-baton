"""Thread-safe SQLite connection manager with WAL mode.

Provides a reusable ``ConnectionManager`` that lazily creates one SQLite
connection per thread and caches it in ``threading.local`` storage.  All
connections are opened in WAL journal mode so that concurrent readers
(e.g. the CLI ``baton query`` command) do not block active writers
(e.g. the execution engine persisting step results).

This module is the lowest layer of the storage subsystem.  Higher-level
classes -- ``SqliteStorage``, ``PmoSqliteStore``, ``CentralStore``,
``SyncEngine``, and ``QueryEngine`` -- all delegate connection
management here.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import local


class ConnectionManager:
    """Thread-safe SQLite connection manager.

    One connection per thread, opened lazily and cached in thread-local
    storage.  All connections use WAL journal mode for concurrent reads
    during execution.

    Attributes:
        _db_path: Absolute path to the SQLite database file.
        _local: Thread-local storage holding the per-thread connection.
        _schema_ddl: DDL script to execute when creating a fresh database.
        _schema_version: Integer version applied to the ``_schema_version``
            table.  When an existing database has a lower version, migration
            scripts from ``schema.MIGRATIONS`` are applied sequentially.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._local = local()
        self._schema_ddl: str = ""
        self._schema_version: int = 1

    def configure_schema(self, ddl: str, version: int = 1) -> None:
        """Set the DDL to apply when creating a fresh database.

        Args:
            ddl: Full SQL script containing ``CREATE TABLE IF NOT EXISTS``
                statements for the target schema (project, PMO, or central).
            version: Schema version number.  The ``_schema_version`` table
                records this so that future upgrades can compare and apply
                migration scripts.
        """
        self._schema_ddl = ddl
        self._schema_version = version

    @property
    def db_path(self) -> Path:
        return self._db_path

    def get_connection(self) -> sqlite3.Connection:
        """Return (or create) a connection for the current thread.

        On first call per thread the connection is opened with:

        * ``journal_mode=WAL`` -- allows concurrent readers during writes.
        * ``foreign_keys=ON`` -- enforces referential integrity.
        * ``busy_timeout=5000`` -- retries on lock contention for up to 5 s.
        * ``sqlite3.Row`` row factory -- columns accessible by name.

        If ``configure_schema`` was called, the schema DDL is applied via
        ``_ensure_schema`` the first time a connection is created for a
        given database file.

        Returns:
            An open ``sqlite3.Connection`` bound to the current thread.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(
                str(self._db_path),
                timeout=10.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
            if self._schema_ddl:
                self._ensure_schema(conn)
        return conn

    def close(self) -> None:
        """Close the connection for the current thread."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        """Create tables if needed; run migrations if version changed.

        Checks the ``_schema_version`` table in ``sqlite_master``:

        * **Table absent** -- fresh database.  Executes the full schema DDL
          and inserts the current version number.
        * **Table present, version < target** -- stale database.  Runs
          migration scripts from ``schema.MIGRATIONS`` for each missing
          version (see ``_run_migrations``).
        * **Table present, version >= target** -- up to date.  No-op.

        Args:
            conn: An open SQLite connection for the current thread.
        """
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='_schema_version'"
        )
        if cur.fetchone() is None:
            conn.executescript(self._schema_ddl)
            conn.execute(
                "INSERT INTO _schema_version (version) VALUES (?)",
                (self._schema_version,),
            )
            conn.commit()
        else:
            row = conn.execute(
                "SELECT version FROM _schema_version"
            ).fetchone()
            current = row["version"] if row else 0
            if current < self._schema_version:
                self._run_migrations(conn, current, self._schema_version)
                conn.execute(
                    "UPDATE _schema_version SET version = ?",
                    (self._schema_version,),
                )
                conn.commit()

    def _run_migrations(
        self, conn: sqlite3.Connection, from_v: int, to_v: int
    ) -> None:
        """Apply sequential migration scripts between two schema versions.

        Iterates from ``from_v + 1`` through ``to_v`` inclusive, executing
        the DDL string registered in ``schema.MIGRATIONS`` for each version.
        Versions that have no entry in the dictionary are silently skipped.

        Args:
            conn: An open SQLite connection (within a transaction managed
                by the caller).
            from_v: Current schema version in the database.
            to_v: Target schema version to migrate to.
        """
        from agent_baton.core.storage.schema import MIGRATIONS
        for v in range(from_v + 1, to_v + 1):
            ddl = MIGRATIONS.get(v)
            if ddl:
                conn.executescript(ddl)
