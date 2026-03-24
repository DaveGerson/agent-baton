"""Agent Telemetry — log and read real-time agent tool-call events."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class TelemetryEvent:
    """A single agent tool-call event."""

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
        """Append event as a JSONL line."""
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
        """Read all events, optionally filtered by agent_name."""
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
        """Aggregate telemetry into a summary dict.

        Returns:
            total_events, events_by_agent, events_by_type,
            files_read, files_written
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
