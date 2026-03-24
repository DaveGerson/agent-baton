"""Thread-safe SQLite connection manager with WAL mode."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import local


class ConnectionManager:
    """Thread-safe SQLite connection manager.

    One connection per thread, opened lazily and cached in thread-local
    storage. All connections use WAL journal mode for concurrent reads
    during execution.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._local = local()
        self._schema_ddl: str = ""
        self._schema_version: int = 1

    def configure_schema(self, ddl: str, version: int = 1) -> None:
        """Set the DDL to apply when creating a fresh database."""
        self._schema_ddl = ddl
        self._schema_version = version

    @property
    def db_path(self) -> Path:
        return self._db_path

    def get_connection(self) -> sqlite3.Connection:
        """Return (or create) a connection for the current thread."""
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
        """Create tables if needed; run migrations if version changed."""
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
        """Apply sequential migration scripts."""
        from agent_baton.core.storage.schema import MIGRATIONS
        for v in range(from_v + 1, to_v + 1):
            ddl = MIGRATIONS.get(v)
            if ddl:
                conn.executescript(ddl)
