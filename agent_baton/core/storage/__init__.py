"""Storage subsystem — pluggable backends for execution data persistence.

Provides a ``StorageBackend`` protocol implemented by:
- ``SqliteStorage`` — SQLite database (new default for all projects)
- ``FileStorage`` — Legacy JSON/JSONL flat files (backward compatible)

Usage::

    from agent_baton.core.storage import get_project_storage, StorageBackend

    storage = get_project_storage(context_root)  # auto-detects backend
    engine = ExecutionEngine(storage=storage)
"""
from __future__ import annotations

from agent_baton.core.storage.protocol import StorageBackend

from pathlib import Path

_BATON_DB = "baton.db"
_TEAM_CONTEXT = ".claude/team-context"


def detect_backend(context_root: Path) -> str:
    """Detect whether a project uses 'sqlite' or 'file' storage.

    1. If baton.db exists → 'sqlite'
    2. If execution-state.json or executions/ dir exists → 'file'
    3. Default for new projects → 'sqlite'
    """
    if (context_root / _BATON_DB).exists():
        return "sqlite"
    if (context_root / "execution-state.json").exists():
        return "file"
    if (context_root / "executions").is_dir():
        return "file"
    return "sqlite"


def get_project_storage(
    context_root: Path,
    backend: str | None = None,
):
    """Factory: return the appropriate project storage backend.

    Args:
        context_root: Path to .claude/team-context/.
        backend: Force 'sqlite' or 'file'. If None, auto-detect.
    """
    if backend is None:
        backend = detect_backend(context_root)

    if backend == "sqlite":
        from agent_baton.core.storage.sqlite_backend import SqliteStorage
        return SqliteStorage(context_root / _BATON_DB)
    else:
        from agent_baton.core.storage.file_backend import FileStorage
        return FileStorage(context_root)


def get_pmo_storage(pmo_db_path: Path | None = None):
    """Factory: return the PMO SQLite storage backend (legacy pmo.db path).

    Prefer :func:`get_pmo_central_store` for new code — it uses central.db.

    Args:
        pmo_db_path: Path to pmo.db. Defaults to ~/.baton/pmo.db.
    """
    from agent_baton.core.storage.pmo_sqlite import PmoSqliteStore
    path = pmo_db_path or (Path.home() / ".baton" / "pmo.db")
    return PmoSqliteStore(path)


def get_pmo_central_store(
    central_db_path: Path | None = None,
    pmo_db_path: Path | None = None,
    marker_path: Path | None = None,
):
    """Factory: return a PmoSqliteStore backed by central.db.

    On first call (when the migration marker is absent), this function
    automatically migrates any data from the legacy ``~/.baton/pmo.db`` into
    the PMO tables of ``central.db``.  The migration is idempotent — subsequent
    calls skip it immediately.

    Args:
        central_db_path: Path to central.db. Defaults to ~/.baton/central.db.
        pmo_db_path: Path to the legacy pmo.db used as migration source.
            Defaults to ~/.baton/pmo.db.
        marker_path: Path to the migration marker file.
            Defaults to ~/.baton/.pmo-migrated.  Pass a custom path in tests
            to keep each test isolated.

    Returns:
        A :class:`~agent_baton.core.storage.pmo_sqlite.PmoSqliteStore` whose
        underlying database is ``central.db``.
    """
    from agent_baton.core.storage.central import _maybe_migrate_pmo
    from agent_baton.core.storage.pmo_sqlite import PmoSqliteStore

    resolved_central = central_db_path or (Path.home() / ".baton" / "central.db")

    # Ensure the migration has run before handing back a write handle.
    _maybe_migrate_pmo(
        central_db_path=resolved_central,
        pmo_db_path=pmo_db_path,
        marker_path=marker_path,
    )

    # PmoSqliteStore uses PMO_SCHEMA_DDL, but central.db was initialised with
    # CENTRAL_SCHEMA_DDL which already contains all PMO tables.  We point
    # PmoSqliteStore at central.db — the schema is already in place so
    # configure_schema is a no-op for existing tables (CREATE TABLE IF NOT EXISTS).
    return PmoSqliteStore(resolved_central)


def get_central_storage(central_db_path: Path | None = None):
    """Factory: return the CentralStore read-only query interface.

    Args:
        central_db_path: Path to central.db. Defaults to ~/.baton/central.db.
    """
    from agent_baton.core.storage.central import CentralStore
    return CentralStore(central_db_path or (Path.home() / ".baton" / "central.db"))


def get_sync_engine(central_db_path: Path | None = None):
    """Factory: return a SyncEngine for one-way project → central sync.

    Args:
        central_db_path: Path to central.db. Defaults to ~/.baton/central.db.
    """
    from agent_baton.core.storage.sync import SyncEngine
    return SyncEngine(central_db_path)
