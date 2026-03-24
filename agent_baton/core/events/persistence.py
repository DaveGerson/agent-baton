"""Append-only JSONL event persistence for crash recovery.

Each task's events are stored in a separate ``.jsonl`` file under the
events directory (default: ``.claude/team-context/events/``).  Events are
appended on write and replayed on read — event sourcing lite.

This module is independent of the :class:`EventBus` so it can be wired
as a subscriber or used standalone for post-hoc analysis.
"""
from __future__ import annotations

import json
import re
from fnmatch import fnmatch
from pathlib import Path

from agent_baton.models.events import Event


class EventPersistence:
    """Append-only JSONL event log per task."""

    _DEFAULT_DIR = Path(".claude/team-context/events")

    def __init__(self, events_dir: Path | None = None) -> None:
        self._dir = (events_dir or self._DEFAULT_DIR).resolve()

    @property
    def events_dir(self) -> Path:
        return self._dir

    # ── Write ───────────────────────────────────────────────────────────────

    def append(self, event: Event) -> Path:
        """Append *event* to the JSONL file for its task_id.

        Creates the events directory and file if they don't exist.
        Returns the path to the JSONL file.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._task_log_path(event.task_id)
        line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(line)
        return path

    # ── Read ────────────────────────────────────────────────────────────────

    def read(
        self,
        task_id: str,
        from_seq: int = 0,
        topic_pattern: str | None = None,
    ) -> list[Event]:
        """Read events for *task_id* from disk.

        Optionally filter by minimum sequence number and/or topic pattern.
        """
        path = self._task_log_path(task_id)
        if not path.exists():
            return []

        events: list[Event] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                event = Event.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                continue
            if event.sequence < from_seq:
                continue
            if topic_pattern and not fnmatch(event.topic, topic_pattern):
                continue
            events.append(event)
        return events

    def read_last(self, task_id: str, n: int = 10) -> list[Event]:
        """Return the last *n* events for a task."""
        all_events = self.read(task_id)
        return all_events[-n:] if len(all_events) > n else all_events

    # ── Query across tasks ──────────────────────────────────────────────────

    def list_task_ids(self) -> list[str]:
        """Return task IDs that have event logs on disk."""
        if not self._dir.is_dir():
            return []
        return sorted(
            p.stem for p in self._dir.glob("*.jsonl")
        )

    def event_count(self, task_id: str) -> int:
        """Return the number of events stored for *task_id*."""
        path = self._task_log_path(task_id)
        if not path.exists():
            return 0
        count = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                count += 1
        return count

    # ── Housekeeping ────────────────────────────────────────────────────────

    def delete(self, task_id: str) -> bool:
        """Delete the event log for *task_id*.  Returns True if a file was removed."""
        path = self._task_log_path(task_id)
        if path.exists():
            path.unlink()
            return True
        return False

    # ── Internal ────────────────────────────────────────────────────────────

    def _task_log_path(self, task_id: str) -> Path:
        safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "-", task_id)
        return self._dir / f"{safe_id}.jsonl"
