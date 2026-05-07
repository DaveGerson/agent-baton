"""Storage subsystem — pluggable backends for execution data persistence.

Provides a ``StorageBackend`` protocol implemented by:

* ``SqliteStorage`` — SQLite database (the only project backend the
  factory now returns).

Slice 15 of the SQLite-parity migration removes ``FileStorage`` from
the factory.  ``FileStorage`` remains importable for legacy code paths
that still construct it directly (it emits ``DeprecationWarning`` on
construction per slice 10) and for the export helper
``dump_state_to_json``, but ``get_project_storage`` is now SQLite-only.

If a legacy file-only project needs upgrading, run
``baton storage migrate`` to move ``execution-state.json`` into
``baton.db``.

Usage::

    from agent_baton.core.storage import get_project_storage, StorageBackend

    storage = get_project_storage(context_root)
    engine = ExecutionEngine(storage=storage)
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

from agent_baton.core.storage.protocol import StorageBackend

_BATON_DB = "baton.db"
_TEAM_CONTEXT = ".claude/team-context"


def detect_backend(context_root: Path) -> str:
    """Detect whether a project uses 'sqlite' or 'file' storage.

    1. If baton.db exists → 'sqlite'
    2. If execution-state.json or executions/ dir exists → 'file'
    3. Default for new projects → 'sqlite'

    Slice 15: the factory no longer respects 'file', but
    ``detect_backend`` keeps reporting it so ``baton storage migrate``
    can offer a one-shot migration path.
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
    """Factory: return the project's SQLite storage backend.

    Slice 15 (file-backend deprecation Stage 3) removes ``FileStorage``
    from this factory.  Callers that explicitly pass ``backend="file"``
    receive a deprecation warning AND a SqliteStorage — the file backend
    was never safe under multi-process load and SQLite Phases A/B/C now
    reach lossless parity for ``ExecutionState``.

    Args:
        context_root: Path to ``.claude/team-context/``.
        backend: Historically ``"sqlite"`` or ``"file"``; the latter is
            now ignored with a warning.
    """
    if backend == "file":
        warnings.warn(
            "backend='file' is no longer supported in get_project_storage. "
            "Returning SqliteStorage. If a legacy "
            "execution-state.json needs migrating, run "
            "`baton storage migrate` first.",
            DeprecationWarning,
            stacklevel=2,
        )
    from agent_baton.core.storage.sqlite_backend import SqliteStorage
    return SqliteStorage(context_root / _BATON_DB)


def dump_state_to_json(
    task_id: str,
    *,
    context_root: Path,
    out_path: Path,
) -> None:
    """Snapshot-only export of an execution state to a JSON file.

    Stage 3 of the file-backend deprecation: the file backend is no
    longer a primary backend, but operators may still want a flat JSON
    snapshot of an execution for diff/inspection.  This helper loads
    via SQLite, calls ``state.to_dict()``, and writes the result to
    *out_path*.

    Used by the ``baton execute export`` CLI verb.

    Args:
        task_id: Task whose state to export.
        context_root: Path to ``.claude/team-context/`` — the same path
            ``get_project_storage`` consumes.
        out_path: Destination file path (typically ``execution-state.json``).

    Raises:
        FileNotFoundError: When *task_id* has no row in ``baton.db``.
    """
    storage = get_project_storage(context_root)
    state = storage.load_execution(task_id)
    if state is None:
        raise FileNotFoundError(
            f"No execution state found for task_id={task_id!r} in "
            f"{context_root / _BATON_DB}."
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


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
