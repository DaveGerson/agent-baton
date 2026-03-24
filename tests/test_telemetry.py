"""Tests for agent_baton.core.telemetry.AgentTelemetry and TelemetryEvent."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.telemetry import AgentTelemetry, TelemetryEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event(
    agent_name: str = "backend-engineer",
    event_type: str = "tool_call",
    tool_name: str = "Read",
    file_path: str = "",
    duration_ms: int = 10,
    details: str = "",
    timestamp: str = "2026-03-20T10:00:00",
) -> TelemetryEvent:
    return TelemetryEvent(
        timestamp=timestamp,
        agent_name=agent_name,
        event_type=event_type,
        tool_name=tool_name,
        file_path=file_path,
        duration_ms=duration_ms,
        details=details,
    )


# ---------------------------------------------------------------------------
# TelemetryEvent — serialization roundtrip
# ---------------------------------------------------------------------------

class TestTelemetryEventFields:
    def test_roundtrip_preserves_all_fields(self) -> None:
        ev = _event(agent_name="security-reviewer", event_type="bash_exec",
                    details="ls -la", file_path="src/main.py", duration_ms=42)
        restored = TelemetryEvent.from_dict(ev.to_dict())
        assert restored.agent_name == ev.agent_name
        assert restored.event_type == ev.event_type
        assert restored.details == ev.details
        assert restored.file_path == ev.file_path
        assert restored.duration_ms == ev.duration_ms

    def test_from_dict_uses_defaults_for_missing_keys(self) -> None:
        ev = TelemetryEvent.from_dict({"timestamp": "t", "agent_name": "a", "event_type": "e"})
        assert ev.tool_name == ""
        assert ev.file_path == ""
        assert ev.duration_ms == 0
        assert ev.details == ""


# ---------------------------------------------------------------------------
# AgentTelemetry.log_event and read_events
# ---------------------------------------------------------------------------

class TestLogEvent:
    def test_log_creates_file_including_parent_dirs(self, tmp_path: Path) -> None:
        log_file = tmp_path / "deep" / "nested" / "tel.jsonl"
        tel = AgentTelemetry(log_file)
        tel.log_event(_event())
        assert log_file.exists()

    def test_log_appends_one_line_per_event(self, tmp_path: Path) -> None:
        log_file = tmp_path / "tel.jsonl"
        tel = AgentTelemetry(log_file)
        for name in ("a1", "a2", "a3"):
            tel.log_event(_event(name))
        lines = [l for l in log_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 3

    def test_read_events_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        assert AgentTelemetry(tmp_path / "missing.jsonl").read_events() == []

    def test_read_events_restores_written_events(self, tmp_path: Path) -> None:
        log_file = tmp_path / "tel.jsonl"
        tel = AgentTelemetry(log_file)
        tel.log_event(_event("arch", event_type="tool_call"))
        tel.log_event(_event("backend-engineer", event_type="file_read",
                             file_path="src/app.py"))
        events = tel.read_events()
        assert len(events) == 2
        assert events[0].agent_name == "arch"
        assert events[1].event_type == "file_read"

    def test_read_events_filters_by_agent(self, tmp_path: Path) -> None:
        log_file = tmp_path / "tel.jsonl"
        tel = AgentTelemetry(log_file)
        tel.log_event(_event("arch"))
        tel.log_event(_event("backend-engineer"))
        tel.log_event(_event("arch"))
        events = tel.read_events(agent_name="arch")
        assert len(events) == 2
        assert all(e.agent_name == "arch" for e in events)

    @pytest.mark.parametrize("bad_line", ["NOT_JSON\n", "\n\n"])
    def test_read_events_skips_malformed_and_blank_lines(
        self, tmp_path: Path, bad_line
    ) -> None:
        log_file = tmp_path / "tel.jsonl"
        tel = AgentTelemetry(log_file)
        tel.log_event(_event("good"))
        with log_file.open("a") as f:
            f.write(bad_line)
        tel.log_event(_event("also-good"))
        events = tel.read_events()
        assert len(events) == 2


# ---------------------------------------------------------------------------
# AgentTelemetry.read_recent
# ---------------------------------------------------------------------------

class TestReadRecent:
    def test_returns_last_n_events(self, tmp_path: Path) -> None:
        tel = AgentTelemetry(tmp_path / "tel.jsonl")
        for i in range(5):
            tel.log_event(_event(f"agent-{i}"))
        recent = tel.read_recent(3)
        assert [e.agent_name for e in recent] == ["agent-2", "agent-3", "agent-4"]

    def test_returns_all_when_count_exceeds_total(self, tmp_path: Path) -> None:
        tel = AgentTelemetry(tmp_path / "tel.jsonl")
        for i in range(3):
            tel.log_event(_event(f"agent-{i}"))
        assert len(tel.read_recent(10)) == 3

    def test_returns_empty_when_no_file(self, tmp_path: Path) -> None:
        assert AgentTelemetry(tmp_path / "missing.jsonl").read_recent(5) == []


# ---------------------------------------------------------------------------
# AgentTelemetry.summary
# ---------------------------------------------------------------------------

class TestSummary:
    def test_empty_log_returns_zeros(self, tmp_path: Path) -> None:
        s = AgentTelemetry(tmp_path / "tel.jsonl").summary()
        assert s["total_events"] == 0
        assert s["events_by_agent"] == {}
        assert s["events_by_type"] == {}
        assert s["files_read"] == []
        assert s["files_written"] == []

    @pytest.mark.parametrize("setup,field,expected", [
        (
            lambda t: [t.log_event(_event()) for _ in range(4)],
            "total_events", 4,
        ),
        (
            lambda t: (
                t.log_event(_event("arch")),
                t.log_event(_event("arch")),
                t.log_event(_event("be")),
            ),
            "events_by_agent", {"arch": 2, "be": 1},
        ),
        (
            lambda t: (
                t.log_event(_event(event_type="tool_call")),
                t.log_event(_event(event_type="tool_call")),
                t.log_event(_event(event_type="error")),
            ),
            "events_by_type", {"tool_call": 2, "error": 1},
        ),
    ])
    def test_summary_aggregation(self, tmp_path: Path, setup, field, expected) -> None:
        tel = AgentTelemetry(tmp_path / "tel.jsonl")
        setup(tel)
        assert tel.summary()[field] == expected

    def test_files_read_and_written_collected(self, tmp_path: Path) -> None:
        tel = AgentTelemetry(tmp_path / "tel.jsonl")
        tel.log_event(_event(event_type="file_read", file_path="src/app.py"))
        tel.log_event(_event(event_type="file_read", file_path="src/models.py"))
        tel.log_event(_event(event_type="file_write", file_path="output/report.md"))
        s = tel.summary()
        assert "src/app.py" in s["files_read"]
        assert "src/models.py" in s["files_read"]
        assert "output/report.md" in s["files_written"]

    def test_non_file_events_not_in_files_lists(self, tmp_path: Path) -> None:
        tel = AgentTelemetry(tmp_path / "tel.jsonl")
        tel.log_event(_event(event_type="tool_call", tool_name="Glob"))
        s = tel.summary()
        assert s["files_read"] == []
        assert s["files_written"] == []


# ---------------------------------------------------------------------------
# AgentTelemetry.clear
# ---------------------------------------------------------------------------

class TestClear:
    def test_clear_empties_log(self, tmp_path: Path) -> None:
        log_file = tmp_path / "tel.jsonl"
        tel = AgentTelemetry(log_file)
        tel.log_event(_event())
        tel.log_event(_event())
        tel.clear()
        assert tel.read_events() == []

    def test_clear_when_file_missing_is_noop(self, tmp_path: Path) -> None:
        AgentTelemetry(tmp_path / "missing.jsonl").clear()  # must not raise

    def test_log_after_clear_works(self, tmp_path: Path) -> None:
        log_file = tmp_path / "tel.jsonl"
        tel = AgentTelemetry(log_file)
        tel.log_event(_event("before"))
        tel.clear()
        tel.log_event(_event("after"))
        events = tel.read_events()
        assert len(events) == 1
        assert events[0].agent_name == "after"
