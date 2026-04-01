"""State persistence for the execution engine.

Handles reading and writing ExecutionState to disk, supporting crash
recovery via atomic writes (write to tmp, then rename).

Supports namespaced execution directories for concurrent plans::

    .claude/team-context/
      executions/
        <task-id-1>/execution-state.json
        <task-id-2>/execution-state.json
      active-task-id.txt          ← points to default task
      execution-state.json        ← legacy flat file (backward compat)
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.models.execution import ExecutionState

_STATE_FILENAME = "execution-state.json"
_EXECUTIONS_DIR = "executions"
_ACTIVE_TASK_FILE = "active-task-id.txt"


class StatePersistence:
    """Manages ExecutionState serialization to disk.

    Provides atomic read/write of ``ExecutionState`` as JSON files,
    supporting crash recovery.  Writes use a tmp-then-rename pattern
    to guarantee that readers never see a partially-written state file.

    When *task_id* is provided, state is stored under
    ``<context_root>/executions/<task_id>/execution-state.json``,
    enabling multiple concurrent executions.  Otherwise, falls back to
    the legacy flat path ``<context_root>/execution-state.json``.

    The class also manages the ``active-task-id.txt`` marker file that
    lets the CLI identify the default execution when no explicit task ID
    is provided.

    Attributes:
        _root: The team-context root directory.
        _task_id: Optional task ID for namespaced storage.
        _state_path: Resolved path to the execution-state.json file.
    """

    def __init__(
        self,
        context_root: Path,
        task_id: str | None = None,
    ) -> None:
        self._root = context_root
        self._task_id = task_id
        if task_id:
            self._state_path = (
                context_root / _EXECUTIONS_DIR / task_id / _STATE_FILENAME
            )
        else:
            self._state_path = context_root / _STATE_FILENAME

    @property
    def task_id(self) -> str | None:
        return self._task_id

    def save(self, state: ExecutionState) -> None:
        """Atomically write state to disk (tmp + rename).

        On Windows, retries ``Path.replace()`` up to 5 times with 50 ms
        backoff because antivirus, search indexer, or concurrent readers
        can momentarily hold the target file open.
        """
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._state_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        import sys
        if sys.platform == "win32":
            import time
            for attempt in range(5):
                try:
                    tmp_path.replace(self._state_path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05)
        else:
            tmp_path.replace(self._state_path)

    def load(self) -> ExecutionState | None:
        """Load state from disk. Returns None if no state or parse error."""
        if not self._state_path.exists():
            return None
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return ExecutionState.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def exists(self) -> bool:
        """Check if a state file exists on disk."""
        return self._state_path.exists()

    def clear(self) -> None:
        """Remove the state file."""
        self._state_path.unlink(missing_ok=True)

    @property
    def path(self) -> Path:
        return self._state_path

    # ── Active task management ─────────────────────────────────────────────

    def set_active(self) -> None:
        """Mark this task as the active (default) execution."""
        if not self._task_id:
            return
        active_path = self._root / _ACTIVE_TASK_FILE
        active_path.parent.mkdir(parents=True, exist_ok=True)
        active_path.write_text(self._task_id, encoding="utf-8")

    @staticmethod
    def get_active_task_id(context_root: Path) -> str | None:
        """Read the active task ID from disk. Returns None if not set."""
        active_path = context_root / _ACTIVE_TASK_FILE
        if not active_path.exists():
            return None
        task_id = active_path.read_text(encoding="utf-8").strip()
        return task_id if task_id else None

    # ── Discovery ──────────────────────────────────────────────────────────

    @staticmethod
    def list_executions(context_root: Path) -> list[str]:
        """List all namespaced task IDs that have execution state."""
        exec_dir = context_root / _EXECUTIONS_DIR
        if not exec_dir.is_dir():
            return []
        task_ids = []
        for child in sorted(exec_dir.iterdir()):
            if child.is_dir() and (child / _STATE_FILENAME).exists():
                task_ids.append(child.name)
        return task_ids

    @staticmethod
    def load_all(context_root: Path) -> list[ExecutionState]:
        """Load all execution states (namespaced + legacy flat file)."""
        states: list[ExecutionState] = []

        # Namespaced executions
        for task_id in StatePersistence.list_executions(context_root):
            sp = StatePersistence(context_root, task_id=task_id)
            state = sp.load()
            if state is not None:
                states.append(state)

        # Legacy flat file (only if no namespaced version of same task exists)
        legacy = StatePersistence(context_root)
        legacy_state = legacy.load()
        if legacy_state is not None:
            existing_ids = {s.task_id for s in states}
            if legacy_state.task_id not in existing_ids:
                states.append(legacy_state)

        return states
