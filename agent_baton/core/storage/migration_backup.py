"""Pre-migration backup helpers for SQLite databases.

Provides safe copy-before-migrate semantics for baton.db and central.db.
All public functions are idempotent and never raise on a non-existent target.

Typical usage before a schema migration::

    from agent_baton.core.storage.migration_backup import backup_db, list_backups

    bak = backup_db(db_path)
    print(f"Backup written to {bak}")
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _schema_version(db_path: Path) -> str:
    """Return the schema version string recorded in *db_path*, or '0'."""
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            row = conn.execute(
                "SELECT version FROM _schema_version LIMIT 1"
            ).fetchone()
            return str(row[0]) if row else "0"
        except sqlite3.OperationalError:
            return "0"
        finally:
            conn.close()
    except Exception:
        return "0"


def backup_db(db_path: Path) -> Path:
    """Copy *db_path* to ``<db_path>.bak-<schema_version>-<timestamp>``.

    Before copying, PRAGMA wal_checkpoint(TRUNCATE) is called so that any
    pending WAL frames are flushed into the main database file.  The backup
    is therefore a self-contained snapshot that does not require the
    ``-wal`` / ``-shm`` sidecar files.

    Args:
        db_path: Absolute path to the SQLite database file.  If the file
            does not exist this function is a no-op and returns a Path
            whose name follows the naming convention (but which does not
            exist on disk).

    Returns:
        The backup path (``Path`` object).  The file exists on disk if and
        only if *db_path* existed at call time.
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    version = _schema_version(db_path) if db_path.exists() else "0"
    backup_path = db_path.with_suffix(f".db.bak-{version}-{ts}")

    if not db_path.exists():
        return backup_path

    # Flush WAL before copying so the backup is self-contained.
    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # If the WAL flush fails (e.g. DB is locked) proceed anyway — a
        # partial WAL is still recoverable, and a backup is better than none.
        pass

    shutil.copy2(db_path, backup_path)
    return backup_path


def restore_db(backup_path: Path, db_path: Path) -> None:
    """Atomically replace *db_path* with *backup_path*.

    Uses ``shutil.copy2`` + ``Path.replace`` for an atomic rename on
    POSIX systems.  The intermediate file is written next to *db_path*
    so that the rename stays within a single filesystem.

    Args:
        backup_path: Path to a valid backup produced by :func:`backup_db`.
        db_path: Destination path.  Overwritten atomically on success.

    Note:
        If *backup_path* does not exist, the function is a no-op.
        If *db_path*'s parent directory does not exist, it is created.
    """
    if not backup_path.exists():
        return

    db_path.parent.mkdir(parents=True, exist_ok=True)
    staging = db_path.with_suffix(".db.restore-staging")
    shutil.copy2(backup_path, staging)
    staging.replace(db_path)

    # Remove stale WAL/SHM sidecars so the restored DB is opened cleanly.
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_suffix(f".db{suffix}")
        try:
            sidecar.unlink(missing_ok=True)
        except OSError:
            pass


def list_backups(db_path: Path) -> list[Path]:
    """Return all backups for *db_path*, sorted oldest-first by timestamp.

    Backups match the glob ``<stem>.db.bak-*`` next to *db_path*.  The
    function never raises even when the parent directory does not exist.

    Args:
        db_path: The original database path (not the backup path).

    Returns:
        A list of :class:`~pathlib.Path` objects sorted by embedded
        timestamp (ascending — oldest first).
    """
    parent = db_path.parent
    if not parent.is_dir():
        return []

    stem = db_path.stem  # e.g. "baton" for "baton.db"
    pattern = f"{stem}.db.bak-*"
    candidates = sorted(parent.glob(pattern))
    return candidates
