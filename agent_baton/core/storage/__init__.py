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
    """Factory: return the PMO SQLite storage backend.

    Args:
        pmo_db_path: Path to pmo.db. Defaults to ~/.baton/pmo.db.
    """
    from agent_baton.core.storage.pmo_sqlite import PmoSqliteStore
    path = pmo_db_path or (Path.home() / ".baton" / "pmo.db")
    return PmoSqliteStore(path)
