"""Tests for TraceEvent, TaskTrace, TraceRecorder, and TraceRenderer."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from agent_baton.models.trace import TaskTrace, TraceEvent
from agent_baton.core.observe.trace import TraceRecorder, TraceRenderer


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

def _make_event(
    event_type: str = "agent_start",
    agent_name: str | None = "architect",
    phase: int = 1,
    step: int = 1,
    details: dict | None = None,
    duration_seconds: float | None = None,
    timestamp: str = "2026-03-20T14:30:00+00:00",
) -> TraceEvent:
    return TraceEvent(
        timestamp=timestamp,
        event_type=event_type,
        agent_name=agent_name,
        phase=phase,
        step=step,
        details=details or {},
        duration_seconds=duration_seconds,
    )


def _make_trace(
    task_id: str = "test-task",
    events: list[TraceEvent] | None = None,
    started_at: str = "2026-03-20T14:30:00+00:00",
    completed_at: str | None = "2026-03-20T14:35:00+00:00",
    outcome: str | None = "SHIP",
) -> TaskTrace:
    return TaskTrace(
        task_id=task_id,
        plan_snapshot={"phases": [{"name": "Design"}, {"name": "Implement"}]},
        events=events or [],
        started_at=started_at,
        completed_at=completed_at,
        outcome=outcome,
    )


# ---------------------------------------------------------------------------
# TraceEvent — dataclass fields and defaults
# ---------------------------------------------------------------------------

class TestTraceEventFields:
    def test_required_fields_stored(self) -> None:
        ev = TraceEvent(
            timestamp="2026-03-20T14:30:00+00:00",
            event_type="agent_start",
            agent_name="architect",
            phase=1,
            step=1,
        )
        assert ev.timestamp == "2026-03-20T14:30:00+00:00"
        assert ev.event_type == "agent_start"
        assert ev.agent_name == "architect"
        assert ev.phase == 1
        assert ev.step == 1

    def test_optional_fields_defaults(self) -> None:
        ev = TraceEvent(
            timestamp="t",
            event_type="decision",
            agent_name=None,
            phase=0,
            step=0,
        )
        assert ev.details == {}
        assert ev.duration_seconds is None

    def test_agent_name_can_be_none(self) -> None:
        ev = _make_event(agent_name=None, event_type="gate_check")
        assert ev.agent_name is None

    def test_details_is_independent_per_instance(self) -> None:
        ev1 = _make_event()
        ev2 = _make_event()
        ev1.details["key"] = "value"
        assert "key" not in ev2.details


# ---------------------------------------------------------------------------
# TraceEvent — serialisation round-trip
# ---------------------------------------------------------------------------

class TestTraceEventSerialisation:
    def test_to_dict_contains_all_keys(self) -> None:
        ev = _make_event(event_type="file_read", duration_seconds=1.5)
        d = ev.to_dict()
        for key in ("timestamp", "event_type", "agent_name", "phase", "step",
                    "details", "duration_seconds"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_values_match(self) -> None:
        ev = _make_event(
            event_type="decision",
            agent_name="backend-engineer",
            phase=2,
            step=3,
            details={"reason": "REST is simpler"},
            duration_seconds=42.0,
        )
        d = ev.to_dict()
        assert d["event_type"] == "decision"
        assert d["agent_name"] == "backend-engineer"
        assert d["phase"] == 2
        assert d["step"] == 3
        assert d["details"] == {"reason": "REST is simpler"}
        assert d["duration_seconds"] == 42.0

    def test_from_dict_roundtrip(self) -> None:
        ev = _make_event(event_type="gate_result", details={"result": "PASS"})
        restored = TraceEvent.from_dict(ev.to_dict())
        assert restored.timestamp == ev.timestamp
        assert restored.event_type == ev.event_type
        assert restored.agent_name == ev.agent_name
        assert restored.phase == ev.phase
        assert restored.step == ev.step
        assert restored.details == ev.details
        assert restored.duration_seconds == ev.duration_seconds

    def test_from_dict_none_agent_name(self) -> None:
        d = {
            "timestamp": "t", "event_type": "gate_check",
            "agent_name": None, "phase": 1, "step": 0,
        }
        ev = TraceEvent.from_dict(d)
        assert ev.agent_name is None

    def test_from_dict_uses_defaults_for_missing_keys(self) -> None:
        ev = TraceEvent.from_dict({"timestamp": "t", "event_type": "e",
                                   "agent_name": None, "phase": 0, "step": 0})
        assert ev.details == {}
        assert ev.duration_seconds is None

    def test_from_dict_handles_null_details(self) -> None:
        d = {"timestamp": "t", "event_type": "e", "agent_name": None,
             "phase": 0, "step": 0, "details": None}
        ev = TraceEvent.from_dict(d)
        assert ev.details == {}


# ---------------------------------------------------------------------------
# TaskTrace — dataclass fields and defaults
# ---------------------------------------------------------------------------

class TestTaskTraceFields:
    def test_required_fields_stored(self) -> None:
        trace = TaskTrace(task_id="my-task")
        assert trace.task_id == "my-task"

    def test_optional_fields_defaults(self) -> None:
        trace = TaskTrace(task_id="t")
        assert trace.plan_snapshot == {}
        assert trace.events == []
        assert trace.started_at == ""
        assert trace.completed_at is None
        assert trace.outcome is None

    def test_events_list_is_independent_per_instance(self) -> None:
        t1 = TaskTrace(task_id="t1")
        t2 = TaskTrace(task_id="t2")
        t1.events.append(_make_event())
        assert len(t2.events) == 0


# ---------------------------------------------------------------------------
# TaskTrace — serialisation round-trip
# ---------------------------------------------------------------------------

class TestTaskTraceSerialisation:
    def test_to_dict_contains_all_keys(self) -> None:
        trace = _make_trace()
        d = trace.to_dict()
        for key in ("task_id", "plan_snapshot", "events",
                    "started_at", "completed_at", "outcome"):
            assert key in d

    def test_events_serialised_as_list_of_dicts(self) -> None:
        trace = _make_trace(events=[_make_event(), _make_event(event_type="agent_complete")])
        d = trace.to_dict()
        assert isinstance(d["events"], list)
        assert len(d["events"]) == 2
        assert d["events"][0]["event_type"] == "agent_start"
        assert d["events"][1]["event_type"] == "agent_complete"

    def test_from_dict_roundtrip(self) -> None:
        original = _make_trace(
            task_id="roundtrip-task",
            events=[_make_event(), _make_event(event_type="decision")],
        )
        restored = TaskTrace.from_dict(original.to_dict())
        assert restored.task_id == original.task_id
        assert restored.started_at == original.started_at
        assert restored.completed_at == original.completed_at
        assert restored.outcome == original.outcome
        assert len(restored.events) == 2
        assert restored.events[1].event_type == "decision"

    def test_from_dict_empty_events(self) -> None:
        trace = TaskTrace.from_dict({"task_id": "empty", "events": []})
        assert trace.events == []

    def test_from_dict_handles_null_events(self) -> None:
        trace = TaskTrace.from_dict({"task_id": "t", "events": None})
        assert trace.events == []

    def test_from_dict_handles_missing_keys(self) -> None:
        trace = TaskTrace.from_dict({"task_id": "minimal"})
        assert trace.plan_snapshot == {}
        assert trace.completed_at is None
        assert trace.outcome is None

    def test_json_serialisable(self) -> None:
        trace = _make_trace(events=[_make_event()])
        json_str = json.dumps(trace.to_dict())
        restored = TaskTrace.from_dict(json.loads(json_str))
        assert restored.task_id == trace.task_id


# ---------------------------------------------------------------------------
# TraceRecorder — start_trace
# ---------------------------------------------------------------------------

class TestStartTrace:
    def test_returns_task_trace(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("my-task")
        assert isinstance(trace, TaskTrace)

    def test_task_id_stored(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("my-task")
        assert trace.task_id == "my-task"

    def test_started_at_is_populated(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("t")
        assert trace.started_at != ""

    def test_completed_at_is_none(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("t")
        assert trace.completed_at is None

    def test_plan_snapshot_stored(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        snap = {"phases": [{"name": "Build"}]}
        trace = rec.start_trace("t", plan_snapshot=snap)
        assert trace.plan_snapshot == snap

    def test_plan_snapshot_defaults_to_empty(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("t")
        assert trace.plan_snapshot == {}

    def test_events_list_starts_empty(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("t")
        assert trace.events == []


# ---------------------------------------------------------------------------
# TraceRecorder — record_event
# ---------------------------------------------------------------------------

class TestRecordEvent:
    def test_returns_trace_event(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("t")
        ev = rec.record_event(trace, "agent_start", agent_name="arch")
        assert isinstance(ev, TraceEvent)

    def test_event_appended_to_trace(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("t")
        rec.record_event(trace, "agent_start")
        rec.record_event(trace, "agent_complete")
        assert len(trace.events) == 2

    def test_event_fields_stored(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("t")
        ev = rec.record_event(
            trace, "decision",
            agent_name="backend-engineer",
            phase=2,
            step=3,
            details={"reason": "simpler"},
            duration_seconds=15.5,
        )
        assert ev.event_type == "decision"
        assert ev.agent_name == "backend-engineer"
        assert ev.phase == 2
        assert ev.step == 3
        assert ev.details == {"reason": "simpler"}
        assert ev.duration_seconds == 15.5

    def test_event_timestamp_is_populated(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("t")
        ev = rec.record_event(trace, "gate_check")
        assert ev.timestamp != ""

    def test_details_defaults_to_empty(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("t")
        ev = rec.record_event(trace, "agent_start")
        assert ev.details == {}

    def test_duration_seconds_defaults_to_none(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("t")
        ev = rec.record_event(trace, "agent_start")
        assert ev.duration_seconds is None


# ---------------------------------------------------------------------------
# TraceRecorder — complete_trace
# ---------------------------------------------------------------------------

class TestCompleteTrace:
    def test_returns_path(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("my-task")
        path = rec.complete_trace(trace)
        assert isinstance(path, Path)

    def test_file_written_to_traces_dir(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("file-test")
        path = rec.complete_trace(trace)
        assert path.exists()
        assert path.parent == tmp_path / "traces"
        assert path.name == "file-test.json"

    def test_creates_traces_dir_automatically(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path / "deep" / "context")
        trace = rec.start_trace("t")
        path = rec.complete_trace(trace)
        assert path.exists()

    def test_completed_at_set(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("t")
        rec.complete_trace(trace)
        assert trace.completed_at is not None

    def test_outcome_stored(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("t")
        rec.complete_trace(trace, outcome="SHIP")
        assert trace.outcome == "SHIP"

    def test_outcome_none_by_default(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("t")
        rec.complete_trace(trace)
        assert trace.outcome is None

    def test_written_file_is_valid_json(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("json-test")
        rec.record_event(trace, "agent_start", agent_name="arch")
        path = rec.complete_trace(trace, outcome="SHIP")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["task_id"] == "json-test"
        assert data["outcome"] == "SHIP"
        assert isinstance(data["events"], list)

    def test_events_preserved_in_file(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("events-test")
        rec.record_event(trace, "agent_start", agent_name="arch", phase=1, step=1)
        rec.record_event(trace, "agent_complete", agent_name="arch", phase=1, step=1)
        path = rec.complete_trace(trace)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data["events"]) == 2


# ---------------------------------------------------------------------------
# TraceRecorder — load_trace
# ---------------------------------------------------------------------------

class TestLoadTrace:
    def test_load_returns_task_trace(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("load-test")
        rec.complete_trace(trace, outcome="DONE")
        loaded = rec.load_trace("load-test")
        assert isinstance(loaded, TaskTrace)

    def test_load_restores_all_fields(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("full-restore", plan_snapshot={"phases": []})
        rec.record_event(trace, "decision", agent_name="arch",
                         details={"reason": "speed"})
        rec.complete_trace(trace, outcome="SHIP")

        loaded = rec.load_trace("full-restore")
        assert loaded is not None
        assert loaded.task_id == "full-restore"
        assert loaded.outcome == "SHIP"
        assert loaded.plan_snapshot == {"phases": []}
        assert len(loaded.events) == 1
        assert loaded.events[0].event_type == "decision"
        assert loaded.events[0].details == {"reason": "speed"}

    def test_load_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        result = rec.load_trace("does-not-exist")
        assert result is None

    def test_load_returns_none_for_malformed_json(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        (tmp_path / "traces").mkdir(parents=True)
        (tmp_path / "traces" / "bad.json").write_text("NOT JSON", encoding="utf-8")
        result = rec.load_trace("bad")
        assert result is None


# ---------------------------------------------------------------------------
# TraceRecorder — list_traces
# ---------------------------------------------------------------------------

class TestListTraces:
    def test_returns_empty_when_no_dir(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path / "nonexistent")
        assert rec.list_traces() == []

    def test_returns_paths(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        t1 = rec.start_trace("t1")
        rec.complete_trace(t1)
        paths = rec.list_traces()
        assert len(paths) == 1
        assert paths[0].suffix == ".json"

    def test_sorted_by_mtime_newest_first(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        for name in ("alpha", "beta", "gamma"):
            t = rec.start_trace(name)
            rec.complete_trace(t)
            # Small sleep to ensure distinct mtimes on filesystems with
            # low-resolution timestamps.
            time.sleep(0.01)

        paths = rec.list_traces(count=3)
        names = [p.stem for p in paths]
        assert names == ["gamma", "beta", "alpha"]

    def test_count_limits_results(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        for i in range(5):
            t = rec.start_trace(f"task-{i}")
            rec.complete_trace(t)
        assert len(rec.list_traces(count=3)) == 3

    def test_count_larger_than_available_returns_all(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        for i in range(3):
            t = rec.start_trace(f"task-{i}")
            rec.complete_trace(t)
        assert len(rec.list_traces(count=100)) == 3


# ---------------------------------------------------------------------------
# TraceRecorder — get_last_trace
# ---------------------------------------------------------------------------

class TestGetLastTrace:
    def test_returns_none_when_no_traces(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        assert rec.get_last_trace() is None

    def test_returns_most_recent_trace(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        t1 = rec.start_trace("first")
        rec.complete_trace(t1)
        time.sleep(0.01)
        t2 = rec.start_trace("second")
        rec.complete_trace(t2, outcome="SHIP")

        last = rec.get_last_trace()
        assert last is not None
        assert last.task_id == "second"

    def test_returns_only_trace_when_one_exists(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        t = rec.start_trace("solo")
        rec.complete_trace(t, outcome="DONE")
        last = rec.get_last_trace()
        assert last is not None
        assert last.task_id == "solo"


# ---------------------------------------------------------------------------
# TraceRecorder — concurrent / edge cases
# ---------------------------------------------------------------------------

class TestConcurrentTraces:
    def test_two_traces_coexist(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        t1 = rec.start_trace("task-a")
        t2 = rec.start_trace("task-b")
        rec.record_event(t1, "agent_start", agent_name="arch")
        rec.record_event(t2, "agent_start", agent_name="backend-engineer")
        rec.complete_trace(t1, outcome="SHIP")
        rec.complete_trace(t2, outcome="FAIL")

        loaded_a = rec.load_trace("task-a")
        loaded_b = rec.load_trace("task-b")
        assert loaded_a is not None and loaded_a.outcome == "SHIP"
        assert loaded_b is not None and loaded_b.outcome == "FAIL"
        assert len(loaded_a.events) == 1
        assert len(loaded_b.events) == 1

    def test_second_complete_overwrites_first(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        t = rec.start_trace("overwrite")
        rec.complete_trace(t, outcome="DRAFT")
        rec.complete_trace(t, outcome="FINAL")
        loaded = rec.load_trace("overwrite")
        assert loaded is not None
        assert loaded.outcome == "FINAL"


# ---------------------------------------------------------------------------
# TraceRenderer — render_timeline
# ---------------------------------------------------------------------------

class TestRenderTimeline:
    def _standard_trace(self) -> TaskTrace:
        events = [
            _make_event("agent_start", "architect", phase=1, step=1,
                        timestamp="2026-03-20T14:30:00+00:00"),
            _make_event("decision", "architect", phase=1, step=1,
                        details={"reason": "Chose REST over GraphQL"},
                        timestamp="2026-03-20T14:30:45+00:00"),
            _make_event("agent_complete", "architect", phase=1, step=1,
                        duration_seconds=80.0,
                        timestamp="2026-03-20T14:31:20+00:00"),
            _make_event("gate_check", None, phase=1, step=0,
                        details={"gate": "build_check"},
                        timestamp="2026-03-20T14:31:21+00:00"),
            _make_event("gate_result", None, phase=1, step=0,
                        details={"result": "PASS"},
                        timestamp="2026-03-20T14:31:25+00:00"),
            _make_event("agent_start", "backend-engineer", phase=2, step=1,
                        timestamp="2026-03-20T14:31:30+00:00"),
        ]
        return _make_trace(task_id="my-task-id", events=events,
                           started_at="2026-03-20T14:30:00+00:00")

    def test_header_contains_task_id(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_timeline(self._standard_trace())
        assert "my-task-id" in output

    def test_header_contains_started_at(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_timeline(self._standard_trace())
        assert "2026-03-20" in output

    def test_header_contains_outcome(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_timeline(self._standard_trace())
        assert "SHIP" in output

    def test_phase_headers_present(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_timeline(self._standard_trace())
        assert "Phase 1" in output
        assert "Phase 2" in output

    def test_phase_name_from_snapshot(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_timeline(self._standard_trace())
        assert "Design" in output
        assert "Implement" in output

    def test_event_types_appear(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_timeline(self._standard_trace())
        assert "agent_start" in output
        assert "decision" in output
        assert "agent_complete" in output
        assert "gate_check" in output
        assert "gate_result" in output

    def test_agent_names_appear(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_timeline(self._standard_trace())
        assert "architect" in output
        assert "backend-engineer" in output

    def test_duration_shown_for_agent_complete(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_timeline(self._standard_trace())
        assert "80s" in output

    def test_detail_reason_shown(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_timeline(self._standard_trace())
        assert "Chose REST over GraphQL" in output

    def test_gate_result_shown(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_timeline(self._standard_trace())
        assert "PASS" in output

    def test_empty_trace_handled(self) -> None:
        renderer = TraceRenderer()
        trace = _make_trace(events=[])
        output = renderer.render_timeline(trace)
        assert "no events" in output.lower() or "Task:" in output

    def test_time_formatted_as_hms(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_timeline(self._standard_trace())
        # Timestamps like 14:30:00 should appear (not raw ISO strings).
        assert "14:30:00" in output


# ---------------------------------------------------------------------------
# TraceRenderer — render_summary
# ---------------------------------------------------------------------------

class TestRenderSummary:
    def _traced_task(self) -> TaskTrace:
        events = [
            _make_event("agent_start", "architect", phase=1, step=1),
            _make_event("agent_complete", "architect", phase=1, step=1,
                        duration_seconds=60.0),
            _make_event("gate_result", None, phase=1, step=0,
                        details={"result": "PASS"}),
            _make_event("agent_start", "backend-engineer", phase=2, step=1),
        ]
        return _make_trace(
            task_id="summary-task",
            events=events,
            started_at="2026-03-20T14:30:00+00:00",
            completed_at="2026-03-20T14:35:00+00:00",
            outcome="SHIP",
        )

    def test_task_id_in_summary(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_summary(self._traced_task())
        assert "summary-task" in output

    def test_outcome_in_summary(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_summary(self._traced_task())
        assert "SHIP" in output

    def test_duration_in_summary(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_summary(self._traced_task())
        assert "5m" in output or "300s" in output or "5" in output

    def test_event_count_in_summary(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_summary(self._traced_task())
        assert "4" in output  # 4 events total

    def test_agent_count_and_names_in_summary(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_summary(self._traced_task())
        assert "architect" in output
        assert "backend-engineer" in output

    def test_gate_results_in_summary(self) -> None:
        renderer = TraceRenderer()
        output = renderer.render_summary(self._traced_task())
        assert "PASS" in output

    def test_empty_trace_summary(self) -> None:
        renderer = TraceRenderer()
        trace = _make_trace(events=[])
        output = renderer.render_summary(trace)
        assert "summary-task" in output or "test-task" in output

    def test_no_outcome_shown_as_na(self) -> None:
        renderer = TraceRenderer()
        trace = _make_trace(outcome=None)
        output = renderer.render_summary(trace)
        assert "N/A" in output

    def test_in_progress_when_no_completed_at(self) -> None:
        renderer = TraceRenderer()
        trace = _make_trace(completed_at=None, outcome=None)
        output = renderer.render_summary(trace)
        assert "in progress" in output or "Duration" in output


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_trace_with_no_phases_in_snapshot(self) -> None:
        """Renderer should not crash when plan_snapshot has no phases."""
        renderer = TraceRenderer()
        trace = TaskTrace(
            task_id="no-snapshot",
            events=[_make_event(phase=1)],
            started_at="2026-03-20T14:00:00+00:00",
        )
        output = renderer.render_timeline(trace)
        assert "no-snapshot" in output

    def test_record_event_with_all_valid_event_types(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("all-types")
        for et in ("agent_start", "agent_complete", "gate_check", "gate_result",
                   "escalation", "replan", "file_read", "file_write", "decision"):
            rec.record_event(trace, et)
        assert len(trace.events) == 9

    def test_load_nonexistent_traces_dir(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path / "no-such-dir")
        assert rec.load_trace("x") is None
        assert rec.list_traces() == []
        assert rec.get_last_trace() is None

    def test_empty_details_dict_roundtrips(self, tmp_path: Path) -> None:
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("empty-details")
        rec.record_event(trace, "agent_start", details={})
        path = rec.complete_trace(trace)
        loaded = rec.load_trace("empty-details")
        assert loaded is not None
        assert loaded.events[0].details == {}

    def test_plan_snapshot_preserved_through_disk(self, tmp_path: Path) -> None:
        snap = {"phases": [{"name": "Alpha"}, {"name": "Beta"}], "task": "test"}
        rec = TraceRecorder(tmp_path)
        trace = rec.start_trace("snapshot-test", plan_snapshot=snap)
        rec.complete_trace(trace)
        loaded = rec.load_trace("snapshot-test")
        assert loaded is not None
        assert loaded.plan_snapshot == snap
