"""Read-only active-task probes for project ``baton.db`` files."""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ActiveTaskProbe:
    """Result from a read-only active-task lookup."""

    task_id: str | None
    db_path: Path
    degraded: bool = False
    error: str | None = None
    error_type: str | None = None

    def degradation_details(self) -> dict[str, Any]:
        return {
            "status": "degraded",
            "db_path": str(self.db_path),
            "error": self.error or "",
            "error_type": self.error_type or "",
        }


def read_active_task_id_from_db_copy(db_path: Path) -> ActiveTaskProbe:
    """Read the active task ID from a temp copy of ``baton.db``.

    The source database may be in WAL mode. Opening it directly, even with
    ``mode=ro``, can create ``-wal`` and ``-shm`` sidecars next to the project
    database. Copying the main database into a temp directory confines any
    SQLite side effects to that temp directory.
    """
    if not db_path.is_file():
        return ActiveTaskProbe(task_id=None, db_path=db_path)

    try:
        with tempfile.TemporaryDirectory(prefix="baton-db-read-") as temp_dir:
            db_copy = Path(temp_dir) / db_path.name
            shutil.copy2(db_path, db_copy)
            for suffix in ("-wal", "-shm"):
                sidecar = db_path.with_name(f"{db_path.name}{suffix}")
                if sidecar.is_file():
                    shutil.copy2(sidecar, db_copy.with_name(f"{db_copy.name}{suffix}"))
            conn = sqlite3.connect(
                f"{db_copy.resolve().as_uri()}?mode=ro",
                uri=True,
            )
            try:
                has_active_task = conn.execute(
                    (
                        "SELECT 1 FROM sqlite_master "
                        "WHERE type = 'table' AND name = 'active_task' LIMIT 1"
                    )
                ).fetchone()
                if not has_active_task:
                    return ActiveTaskProbe(task_id=None, db_path=db_path)
                row = conn.execute(
                    (
                        "SELECT task_id FROM active_task "
                        "WHERE id = 1 LIMIT 1"
                    )
                ).fetchone()
            finally:
                conn.close()
    except Exception as exc:
        return ActiveTaskProbe(
            task_id=None,
            db_path=db_path,
            degraded=True,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    if not row:
        return ActiveTaskProbe(task_id=None, db_path=db_path)
    active_task_id = row[0]
    if isinstance(active_task_id, str):
        active_task_id = active_task_id.strip()
        if active_task_id:
            return ActiveTaskProbe(task_id=active_task_id, db_path=db_path)
    return ActiveTaskProbe(task_id=None, db_path=db_path)
