"""State persistence for the execution engine.

Handles reading and writing ExecutionState to disk, supporting crash
recovery via atomic writes (write to tmp, then rename).
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.models.execution import ExecutionState

_STATE_FILENAME = "execution-state.json"


class StatePersistence:
    """Manages ExecutionState serialization to disk."""

    def __init__(self, context_root: Path) -> None:
        self._root = context_root
        self._state_path = context_root / _STATE_FILENAME

    def save(self, state: ExecutionState) -> None:
        """Atomically write state to disk (tmp + rename)."""
        self._root.mkdir(parents=True, exist_ok=True)
        tmp_path = self._state_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(state.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.rename(self._state_path)

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
