"""Agent Telemetry -- log and read real-time agent tool-call events.

Telemetry captures the finest-grained execution data in the observe layer.
While :class:`~agent_baton.core.observe.trace.TraceRecorder` records
task-level events and :class:`~agent_baton.core.observe.usage.UsageLogger`
records per-task summaries, telemetry logs individual tool calls
(file reads, file writes, bash executions, errors) as they happen during
agent execution.

The telemetry stream is consumed by:

* :class:`~agent_baton.core.observe.dashboard.DashboardGenerator` -- appends
  a telemetry summary section to the usage dashboard.
* Human operators -- the ``baton telemetry`` CLI command surfaces recent
  events for live debugging.

Events are persisted as JSONL to ``.claude/team-context/telemetry.jsonl``
and are subject to rotation by
:class:`~agent_baton.core.observe.archiver.DataArchiver` (default:
keep the last 10,000 lines).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path


@dataclass
class TelemetryEvent:
    """A single agent tool-call event.

    Attributes:
        timestamp: ISO 8601 UTC timestamp of when the event occurred.
        agent_name: Name of the agent that produced this event.
        event_type: Kind of tool call.  One of ``"tool_call"``,
            ``"file_read"``, ``"file_write"``, ``"bash_exec"``, or
            ``"error"``.
        tool_name: Name of the tool invoked (e.g. ``"Read"``, ``"Edit"``).
        file_path: Filesystem path involved, if applicable.
        duration_ms: Wall-clock duration of the tool call in milliseconds.
        details: Free-form description or context string.
    """

    timestamp: str
    agent_name: str
    event_type: str  # "tool_call", "file_read", "file_write", "bash_exec", "error"
    tool_name: str = ""
    file_path: str = ""
    duration_ms: int = 0
    details: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TelemetryEvent:
        return cls(
            timestamp=data.get("timestamp", ""),
            agent_name=data.get("agent_name", ""),
            event_type=data.get("event_type", ""),
            tool_name=data.get("tool_name", ""),
            file_path=data.get("file_path", ""),
            duration_ms=data.get("duration_ms", 0),
            details=data.get("details", ""),
        )


class AgentTelemetry:
    """Log and read real-time agent tool-call events.

    Events are persisted as JSONL lines to .claude/team-context/telemetry.jsonl
    (or a custom path supplied at construction).
    """

    _DEFAULT_LOG_PATH = Path(".claude/team-context/telemetry.jsonl")

    def __init__(self, log_path: Path | None = None) -> None:
        self._log_path = (log_path or self._DEFAULT_LOG_PATH).resolve()

    @property
    def log_path(self) -> Path:
        return self._log_path

    # ── Write ──────────────────────────────────────────────────────────────

    def log_event(self, event: TelemetryEvent) -> None:
        """Append a telemetry event as a single JSONL line.

        Creates the parent directory if it does not exist.  The file is
        opened in append mode for safe concurrent writes.

        Args:
            event: The telemetry event to persist.
        """
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), separators=(",", ":"))
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ── Read ───────────────────────────────────────────────────────────────

    def _read_all_raw(self) -> list[TelemetryEvent]:
        """Read all events from log, skipping malformed lines."""
        if not self._log_path.exists():
            return []
        events: list[TelemetryEvent] = []
        with self._log_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    events.append(TelemetryEvent.from_dict(data))
                except (json.JSONDecodeError, KeyError):
                    continue
        return events

    def read_events(self, agent_name: str | None = None) -> list[TelemetryEvent]:
        """Read all events, optionally filtered by agent name.

        Args:
            agent_name: If provided, only events from this agent are
                returned.  ``None`` returns all events.

        Returns:
            List of telemetry events in chronological order.
        """
        events = self._read_all_raw()
        if agent_name is not None:
            events = [e for e in events if e.agent_name == agent_name]
        return events

    def read_recent(self, count: int = 50) -> list[TelemetryEvent]:
        """Read the most recent N events."""
        all_events = self._read_all_raw()
        return all_events[-count:] if count < len(all_events) else all_events

    # ── Aggregation ────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """Aggregate all telemetry events into a summary dict.

        Returns:
            A dict with the following keys:

            * ``total_events`` -- total number of telemetry events.
            * ``events_by_agent`` -- dict mapping agent name to event count.
            * ``events_by_type`` -- dict mapping event type to count.
            * ``files_read`` -- list of file paths from ``file_read`` events.
            * ``files_written`` -- list of file paths from ``file_write``
              events.
        """
        events = self._read_all_raw()
        events_by_agent: dict[str, int] = {}
        events_by_type: dict[str, int] = {}
        files_read: list[str] = []
        files_written: list[str] = []

        for ev in events:
            events_by_agent[ev.agent_name] = events_by_agent.get(ev.agent_name, 0) + 1
            events_by_type[ev.event_type] = events_by_type.get(ev.event_type, 0) + 1
            if ev.event_type == "file_read" and ev.file_path:
                files_read.append(ev.file_path)
            if ev.event_type == "file_write" and ev.file_path:
                files_written.append(ev.file_path)

        return {
            "total_events": len(events),
            "events_by_agent": events_by_agent,
            "events_by_type": events_by_type,
            "files_read": files_read,
            "files_written": files_written,
        }

    def clear(self) -> None:
        """Clear the telemetry log (between tasks)."""
        if self._log_path.exists():
            self._log_path.write_text("", encoding="utf-8")
