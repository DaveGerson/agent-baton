"""Tests for the event bus system: models, bus, persistence, projections."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.models.events import Event
from agent_baton.core.events.bus import EventBus
from agent_baton.core.events.persistence import EventPersistence
from agent_baton.core.events.projections import (
    TaskView,
    PhaseView,
    StepView,
    project_task_view,
)
from agent_baton.core.events import events as evt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event(
    topic: str = "step.completed",
    task_id: str = "task-1",
    sequence: int = 1,
    payload: dict | None = None,
) -> Event:
    return Event(
        event_id="evt-test",
        timestamp="2026-03-22T10:00:00+00:00",
        topic=topic,
        task_id=task_id,
        sequence=sequence,
        payload=payload or {},
    )


# ===========================================================================
# Event model
# ===========================================================================

class TestEventModel:
    def test_to_dict_roundtrip(self) -> None:
        e = _event(payload={"key": "value"})
        restored = Event.from_dict(e.to_dict())
        assert restored.event_id == e.event_id
        assert restored.topic == e.topic
        assert restored.task_id == e.task_id
        assert restored.sequence == e.sequence
        assert restored.payload == e.payload

    def test_create_factory(self) -> None:
        e = Event.create(topic="step.completed", task_id="t1", payload={"x": 1})
        assert e.topic == "step.completed"
        assert e.task_id == "t1"
        assert e.payload == {"x": 1}
        assert len(e.event_id) == 12  # hex[:12]
        assert e.timestamp  # non-empty

    def test_create_auto_generates_unique_ids(self) -> None:
        ids = {Event.create(topic="a", task_id="t").event_id for _ in range(5)}
        assert len(ids) == 5


# ===========================================================================
# Domain event factories
# ===========================================================================

class TestDomainEvents:
    def test_step_dispatched(self) -> None:
        e = evt.step_dispatched("t1", "1.1", "backend-engineer--python", model="opus")
        assert e.topic == "step.dispatched"
        assert e.task_id == "t1"
        assert e.payload["step_id"] == "1.1"
        assert e.payload["agent_name"] == "backend-engineer--python"
        assert e.payload["model"] == "opus"

    def test_step_completed(self) -> None:
        e = evt.step_completed(
            "t1", "1.1", "backend",
            outcome="done", files_changed=["a.py"], duration_seconds=30.0,
        )
        assert e.topic == "step.completed"
        assert e.payload["outcome"] == "done"
        assert e.payload["files_changed"] == ["a.py"]
        assert e.payload["duration_seconds"] == 30.0

    def test_step_failed(self) -> None:
        e = evt.step_failed("t1", "1.2", "test-engineer", error="tests failed")
        assert e.topic == "step.failed"
        assert e.payload["error"] == "tests failed"

    def test_gate_required(self) -> None:
        e = evt.gate_required("t1", phase_id=1, gate_type="test", command="pytest")
        assert e.topic == "gate.required"
        assert e.payload["phase_id"] == 1
        assert e.payload["command"] == "pytest"

    def test_gate_passed(self) -> None:
        e = evt.gate_passed("t1", phase_id=1, gate_type="test", output="all green")
        assert e.topic == "gate.passed"
        assert e.payload["output"] == "all green"

    def test_gate_failed(self) -> None:
        e = evt.gate_failed("t1", phase_id=1, gate_type="test", output="3 failures")
        assert e.topic == "gate.failed"

    def test_human_decision_needed(self) -> None:
        e = evt.human_decision_needed(
            "t1", request_id="req-1", decision_type="gate_approval",
            summary="Review PR", options=["approve", "reject"],
        )
        assert e.topic == "human.decision_needed"
        assert e.payload["request_id"] == "req-1"
        assert e.payload["options"] == ["approve", "reject"]

    def test_human_decision_resolved(self) -> None:
        e = evt.human_decision_resolved(
            "t1", request_id="req-1", chosen_option="approve",
        )
        assert e.topic == "human.decision_resolved"
        assert e.payload["chosen_option"] == "approve"

    def test_task_started(self) -> None:
        e = evt.task_started("t1", task_summary="Build feature", total_steps=5)
        assert e.topic == "task.started"
        assert e.payload["total_steps"] == 5

    def test_task_completed(self) -> None:
        e = evt.task_completed("t1", steps_completed=5, gates_passed=2)
        assert e.topic == "task.completed"
        assert e.payload["steps_completed"] == 5

    def test_task_failed(self) -> None:
        e = evt.task_failed("t1", reason="gate failure", failed_step_id="1.3")
        assert e.topic == "task.failed"
        assert e.payload["reason"] == "gate failure"

    def test_phase_started(self) -> None:
        e = evt.phase_started("t1", phase_id=1, phase_name="Implementation")
        assert e.topic == "phase.started"
        assert e.payload["phase_name"] == "Implementation"

    def test_phase_completed(self) -> None:
        e = evt.phase_completed("t1", phase_id=1)
        assert e.topic == "phase.completed"


# ===========================================================================
# EventBus
# ===========================================================================

class TestEventBusPublish:
    def test_publish_invokes_matching_subscriber(self) -> None:
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("step.*", received.append)
        bus.publish(_event(topic="step.completed"))
        assert len(received) == 1
        assert received[0].topic == "step.completed"

    def test_publish_skips_non_matching(self) -> None:
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("gate.*", received.append)
        bus.publish(_event(topic="step.completed"))
        assert len(received) == 0

    def test_wildcard_matches_everything(self) -> None:
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("*", received.append)
        bus.publish(_event(topic="step.completed"))
        bus.publish(_event(topic="gate.passed"))
        assert len(received) == 2

    def test_exact_topic_match(self) -> None:
        bus = EventBus()
        received: list[Event] = []
        bus.subscribe("step.completed", received.append)
        bus.publish(_event(topic="step.completed"))
        bus.publish(_event(topic="step.failed"))
        assert len(received) == 1

    def test_multiple_subscribers_all_called(self) -> None:
        bus = EventBus()
        r1: list[Event] = []
        r2: list[Event] = []
        bus.subscribe("step.*", r1.append)
        bus.subscribe("step.*", r2.append)
        bus.publish(_event(topic="step.completed"))
        assert len(r1) == 1
        assert len(r2) == 1

    @pytest.mark.parametrize("same_task,expected_seq", [
        (True, (1, 2)),   # same task_id → monotonically increasing
        (False, (1, 1)),  # different task_ids → independent sequences
    ])
    def test_sequence_assignment(self, same_task, expected_seq) -> None:
        bus = EventBus()
        task_id2 = "t1" if same_task else "t2"
        e1 = _event(topic="step.completed", task_id="t1", sequence=0)
        e2 = _event(topic="step.failed", task_id=task_id2, sequence=0)
        bus.publish(e1)
        bus.publish(e2)
        assert (e1.sequence, e2.sequence) == expected_seq

    def test_preserves_explicit_sequence(self) -> None:
        bus = EventBus()
        e = _event(topic="step.completed", task_id="t1", sequence=42)
        bus.publish(e)
        assert e.sequence == 42


class TestEventBusSubscribe:
    def test_subscribe_and_unsubscribe(self) -> None:
        bus = EventBus()
        received: list[Event] = []
        sub_id = bus.subscribe("step.*", received.append)
        assert isinstance(sub_id, str) and len(sub_id) > 0
        assert bus.subscription_count == 1

        bus.unsubscribe(sub_id)
        bus.publish(_event(topic="step.completed"))
        assert len(received) == 0
        assert bus.subscription_count == 0

    def test_unsubscribe_unknown_is_noop(self) -> None:
        bus = EventBus()
        bus.unsubscribe("nonexistent")  # should not raise


class TestEventBusReplay:
    @pytest.mark.parametrize("from_seq,expected_count,expected_topics", [
        (None, 2, None),         # all events for task
        (2, 2, ["b", "c"]),      # from_seq=2 returns seq 2 and 3
    ])
    def test_replay_filtering(self, from_seq, expected_count, expected_topics) -> None:
        bus = EventBus()
        if from_seq is None:
            # Two tasks, filter by task id
            bus.publish(_event(topic="step.completed", task_id="t1", sequence=0))
            bus.publish(_event(topic="step.failed", task_id="t2", sequence=0))
            bus.publish(_event(topic="gate.passed", task_id="t1", sequence=0))
            result = bus.replay("t1")
            assert len(result) == expected_count
            assert all(e.task_id == "t1" for e in result)
        else:
            e1 = _event(topic="a", task_id="t1", sequence=0)
            e2 = _event(topic="b", task_id="t1", sequence=0)
            e3 = _event(topic="c", task_id="t1", sequence=0)
            bus.publish(e1)
            bus.publish(e2)
            bus.publish(e3)
            result = bus.replay("t1", from_seq=from_seq)
            assert len(result) == expected_count
            assert [e.topic for e in result] == expected_topics

    def test_replay_with_topic_filter(self) -> None:
        bus = EventBus()
        bus.publish(_event(topic="step.completed", task_id="t1", sequence=0))
        bus.publish(_event(topic="gate.passed", task_id="t1", sequence=0))
        result = bus.replay("t1", topic_pattern="step.*")
        assert len(result) == 1
        assert result[0].topic == "step.completed"

    def test_replay_empty_for_unknown_task(self) -> None:
        bus = EventBus()
        assert bus.replay("nonexistent") == []


class TestEventBusHistory:
    def test_history_returns_all(self) -> None:
        bus = EventBus()
        bus.publish(_event(topic="a", sequence=0))
        bus.publish(_event(topic="b", sequence=0))
        assert len(bus.history()) == 2

    def test_history_with_limit(self) -> None:
        bus = EventBus()
        for i in range(10):
            bus.publish(_event(topic=f"t{i}", sequence=0))
        result = bus.history(limit=3)
        assert len(result) == 3
        assert result[0].topic == "t7"

    def test_clear(self) -> None:
        bus = EventBus()
        bus.subscribe("*", lambda e: None)
        bus.publish(_event(sequence=0))
        bus.clear()
        assert bus.subscription_count == 0
        assert bus.history() == []


# ===========================================================================
# EventPersistence
# ===========================================================================

class TestEventPersistenceAppend:
    def test_append_creates_file(self, tmp_path: Path) -> None:
        p = EventPersistence(tmp_path)
        e = _event(task_id="task-1")
        path = p.append(e)
        assert path.exists()
        assert path.suffix == ".jsonl"

    def test_append_creates_parent_dirs(self, tmp_path: Path) -> None:
        p = EventPersistence(tmp_path / "deep" / "events")
        e = _event(task_id="task-1")
        path = p.append(e)
        assert path.exists()

    def test_multiple_appends_same_task(self, tmp_path: Path) -> None:
        p = EventPersistence(tmp_path)
        p.append(_event(task_id="t1", topic="a"))
        p.append(_event(task_id="t1", topic="b"))
        lines = (tmp_path / "t1.jsonl").read_text().strip().splitlines()
        assert len(lines) == 2


class TestEventPersistenceRead:
    def test_read_returns_events(self, tmp_path: Path) -> None:
        p = EventPersistence(tmp_path)
        p.append(_event(task_id="t1", topic="step.completed", sequence=1))
        p.append(_event(task_id="t1", topic="gate.passed", sequence=2))
        events = p.read("t1")
        assert len(events) == 2
        assert events[0].topic == "step.completed"
        assert events[1].topic == "gate.passed"

    def test_read_missing_or_empty_returns_empty(self, tmp_path: Path) -> None:
        p = EventPersistence(tmp_path)
        assert p.read("nonexistent") == []
        assert p.list_task_ids() == []
        p2 = EventPersistence(tmp_path / "nonexistent")
        assert p2.list_task_ids() == []

    def test_read_with_from_seq(self, tmp_path: Path) -> None:
        p = EventPersistence(tmp_path)
        p.append(_event(task_id="t1", sequence=1))
        p.append(_event(task_id="t1", sequence=2))
        p.append(_event(task_id="t1", sequence=3))
        events = p.read("t1", from_seq=2)
        assert len(events) == 2
        assert events[0].sequence == 2

    def test_read_with_topic_filter(self, tmp_path: Path) -> None:
        p = EventPersistence(tmp_path)
        p.append(_event(task_id="t1", topic="step.completed", sequence=1))
        p.append(_event(task_id="t1", topic="gate.passed", sequence=2))
        events = p.read("t1", topic_pattern="step.*")
        assert len(events) == 1
        assert events[0].topic == "step.completed"

    def test_read_last(self, tmp_path: Path) -> None:
        p = EventPersistence(tmp_path)
        for i in range(5):
            p.append(_event(task_id="t1", topic=f"t{i}", sequence=i + 1))
        last = p.read_last("t1", n=2)
        assert len(last) == 2
        assert last[0].sequence == 4
        assert last[1].sequence == 5

    def test_read_skips_malformed_lines(self, tmp_path: Path) -> None:
        p = EventPersistence(tmp_path)
        p.append(_event(task_id="t1", sequence=1))
        # Inject a malformed line.
        log_path = tmp_path / "t1.jsonl"
        with log_path.open("a") as f:
            f.write("not valid json\n")
        p.append(_event(task_id="t1", sequence=2))
        events = p.read("t1")
        assert len(events) == 2  # malformed line skipped


class TestEventPersistenceQuery:
    def test_list_task_ids(self, tmp_path: Path) -> None:
        p = EventPersistence(tmp_path)
        p.append(_event(task_id="alpha"))
        p.append(_event(task_id="beta"))
        ids = p.list_task_ids()
        assert ids == ["alpha", "beta"]

    def test_event_count(self, tmp_path: Path) -> None:
        p = EventPersistence(tmp_path)
        p.append(_event(task_id="t1"))
        p.append(_event(task_id="t1"))
        p.append(_event(task_id="t1"))
        assert p.event_count("t1") == 3
        assert p.event_count("nonexistent") == 0


class TestEventPersistenceDelete:
    def test_delete_removes_file(self, tmp_path: Path) -> None:
        p = EventPersistence(tmp_path)
        p.append(_event(task_id="t1"))
        assert p.delete("t1") is True
        assert p.read("t1") == []

    def test_delete_nonexistent_returns_false(self, tmp_path: Path) -> None:
        p = EventPersistence(tmp_path)
        assert p.delete("nonexistent") is False


# ===========================================================================
# Projections
# ===========================================================================

class TestProjectTaskViewEmpty:
    def test_empty_events_returns_unknown(self) -> None:
        view = project_task_view([])
        assert view.task_id == "unknown"
        assert view.status == "unknown"

    def test_empty_events_with_task_id(self) -> None:
        view = project_task_view([], task_id="t1")
        assert view.task_id == "t1"


class TestProjectTaskViewStarted:
    def test_task_started_sets_fields(self) -> None:
        events = [
            evt.task_started("t1", task_summary="Build X", risk_level="HIGH", total_steps=5),
        ]
        view = project_task_view(events)
        assert view.task_id == "t1"
        assert view.status == "running"
        assert view.risk_level == "HIGH"
        assert view.total_steps == 5


class TestProjectTaskViewSteps:
    def test_step_dispatched(self) -> None:
        events = [
            evt.task_started("t1"),
            evt.phase_started("t1", phase_id=1, phase_name="Phase 1"),
            evt.step_dispatched("t1", "1.1", "backend"),
        ]
        view = project_task_view(events)
        assert view.steps_dispatched == 1
        step = view.phases[1].steps["1.1"]
        assert step.status == "dispatched"
        assert step.agent_name == "backend"

    def test_step_completed(self) -> None:
        events = [
            evt.task_started("t1"),
            evt.phase_started("t1", phase_id=1),
            evt.step_dispatched("t1", "1.1", "backend"),
            evt.step_completed("t1", "1.1", "backend", outcome="done",
                               files_changed=["a.py"], duration_seconds=30.0),
        ]
        view = project_task_view(events)
        assert view.steps_completed == 1
        assert view.steps_dispatched == 0  # upgraded from dispatched to completed
        step = view.phases[1].steps["1.1"]
        assert step.status == "completed"
        assert step.files_changed == ["a.py"]

    def test_step_failed(self) -> None:
        events = [
            evt.task_started("t1"),
            evt.step_dispatched("t1", "1.1", "backend"),
            evt.step_failed("t1", "1.1", "backend", error="timeout"),
        ]
        view = project_task_view(events)
        assert view.steps_failed == 1


class TestProjectTaskViewGates:
    def test_gate_passed(self) -> None:
        events = [
            evt.task_started("t1"),
            evt.phase_started("t1", phase_id=1),
            evt.gate_required("t1", phase_id=1, gate_type="test"),
            evt.gate_passed("t1", phase_id=1, gate_type="test", output="ok"),
        ]
        view = project_task_view(events)
        assert view.gates_passed == 1
        assert view.phases[1].gate_status == "passed"

    def test_gate_failed(self) -> None:
        events = [
            evt.task_started("t1"),
            evt.gate_required("t1", phase_id=1, gate_type="test"),
            evt.gate_failed("t1", phase_id=1, gate_type="test", output="3 failures"),
        ]
        view = project_task_view(events)
        assert view.gates_failed == 1
        assert view.phases[1].gate_status == "failed"


class TestProjectTaskViewDecisions:
    def test_decision_needed_and_resolved(self) -> None:
        events = [
            evt.task_started("t1"),
            evt.human_decision_needed("t1", request_id="req-1",
                                      decision_type="gate_approval",
                                      summary="Review"),
        ]
        view = project_task_view(events)
        assert "req-1" in view.pending_decisions

        events.append(
            evt.human_decision_resolved("t1", request_id="req-1",
                                        chosen_option="approve")
        )
        view = project_task_view(events)
        assert "req-1" not in view.pending_decisions


class TestProjectTaskViewCompletion:
    def test_task_completed(self) -> None:
        events = [
            evt.task_started("t1", total_steps=2),
            evt.step_completed("t1", "1.1", "backend"),
            evt.step_completed("t1", "1.2", "test-engineer"),
            evt.task_completed("t1", steps_completed=2, elapsed_seconds=120.0),
        ]
        view = project_task_view(events)
        assert view.status == "completed"
        assert view.elapsed_seconds == 120.0

    def test_task_failed(self) -> None:
        events = [
            evt.task_started("t1"),
            evt.step_failed("t1", "1.1", "backend", error="crash"),
            evt.task_failed("t1", reason="step failure"),
        ]
        view = project_task_view(events)
        assert view.status == "failed"


class TestProjectTaskViewFullLifecycle:
    def test_full_lifecycle(self) -> None:
        """3-step, 2-phase plan with a gate and a decision."""
        events = [
            evt.task_started("t1", task_summary="Build feature", risk_level="MEDIUM",
                             total_steps=3),
            # Phase 1
            evt.phase_started("t1", phase_id=1, phase_name="Implementation", step_count=2),
            evt.step_dispatched("t1", "1.1", "backend"),
            evt.step_dispatched("t1", "1.2", "backend"),
            evt.step_completed("t1", "1.1", "backend", outcome="models done",
                               duration_seconds=45.0),
            evt.step_completed("t1", "1.2", "backend", outcome="api done",
                               duration_seconds=60.0),
            evt.gate_required("t1", phase_id=1, gate_type="test", command="pytest"),
            evt.gate_passed("t1", phase_id=1, gate_type="test"),
            evt.phase_completed("t1", phase_id=1),
            # Phase 2
            evt.phase_started("t1", phase_id=2, phase_name="Review", step_count=1),
            evt.step_dispatched("t1", "2.1", "code-reviewer"),
            evt.human_decision_needed("t1", request_id="rev-1",
                                      decision_type="code_review",
                                      summary="Review PR #42"),
            evt.human_decision_resolved("t1", request_id="rev-1",
                                        chosen_option="approve"),
            evt.step_completed("t1", "2.1", "code-reviewer", outcome="LGTM"),
            evt.phase_completed("t1", phase_id=2),
            # Done
            evt.task_completed("t1", steps_completed=3, gates_passed=1,
                               elapsed_seconds=300.0),
        ]
        view = project_task_view(events)

        assert view.task_id == "t1"
        assert view.status == "completed"
        assert view.risk_level == "MEDIUM"
        assert view.total_steps == 3
        assert view.steps_completed == 3
        assert view.steps_failed == 0
        assert view.steps_dispatched == 0
        assert view.gates_passed == 1
        assert view.gates_failed == 0
        assert view.elapsed_seconds == 300.0
        assert view.pending_decisions == []
        assert len(view.phases) == 2
        assert view.phases[1].phase_name == "Implementation"
        assert view.phases[2].phase_name == "Review"
        assert len(view.phases[1].steps) == 2
        assert len(view.phases[2].steps) == 1


# ===========================================================================
# Integration: Bus + Persistence
# ===========================================================================

class TestBusPersistenceIntegration:
    def test_bus_subscriber_persists_and_replays(self, tmp_path: Path) -> None:
        bus = EventBus()
        persistence = EventPersistence(tmp_path)

        # Wire persistence as a subscriber.
        bus.subscribe("*", persistence.append)

        bus.publish(evt.task_started("t1", task_summary="Test", total_steps=2))
        bus.publish(evt.step_dispatched("t1", "1.1", "backend"))
        bus.publish(evt.step_completed("t1", "1.1", "backend"))
        bus.publish(evt.step_completed("t1", "1.2", "test-engineer"))
        bus.publish(evt.task_completed("t1", steps_completed=2))

        # Read back from disk and verify ordering.
        events = persistence.read("t1")
        assert len(events) == 5
        assert events[0].topic == "task.started"
        assert events[1].topic == "step.dispatched"

        # Replay from disk and project.
        view = project_task_view(events)
        assert view.status == "completed"
        assert view.steps_completed == 2


# ===========================================================================
# Pre-lifecycle hook event factories
# ===========================================================================

class TestPreLifecycleHookEvents:
    # ── step.pre_dispatch ───────────────────────────────────────────────────

    def test_step_pre_dispatch_topic(self) -> None:
        e = evt.step_pre_dispatch("t1", "1.1", "backend-engineer")
        assert e.topic == "step.pre_dispatch"

    def test_step_pre_dispatch_task_id(self) -> None:
        e = evt.step_pre_dispatch("task-abc", "2.3", "test-engineer")
        assert e.task_id == "task-abc"

    def test_step_pre_dispatch_payload_keys(self) -> None:
        e = evt.step_pre_dispatch(
            "t1", "1.1", "backend-engineer--python",
            model="opus", delegation_prompt="Do the thing.", sequence=5,
        )
        assert e.payload["step_id"] == "1.1"
        assert e.payload["agent_name"] == "backend-engineer--python"
        assert e.payload["model"] == "opus"
        assert e.payload["delegation_prompt"] == "Do the thing."

    def test_step_pre_dispatch_defaults(self) -> None:
        e = evt.step_pre_dispatch("t1", "1.2", "frontend-engineer")
        assert e.payload["model"] == "sonnet"
        assert e.payload["delegation_prompt"] == ""
        assert e.sequence == 0

    # ── phase.pre_start ─────────────────────────────────────────────────────

    def test_phase_pre_start_topic(self) -> None:
        e = evt.phase_pre_start("t1", phase_id=1)
        assert e.topic == "phase.pre_start"

    def test_phase_pre_start_task_id(self) -> None:
        e = evt.phase_pre_start("task-xyz", phase_id=2)
        assert e.task_id == "task-xyz"

    def test_phase_pre_start_payload_keys(self) -> None:
        e = evt.phase_pre_start(
            "t1", phase_id=3, phase_name="Implementation", step_count=4, sequence=7,
        )
        assert e.payload["phase_id"] == 3
        assert e.payload["phase_name"] == "Implementation"
        assert e.payload["step_count"] == 4

    def test_phase_pre_start_defaults(self) -> None:
        e = evt.phase_pre_start("t1", phase_id=1)
        assert e.payload["phase_name"] == ""
        assert e.payload["step_count"] == 0
        assert e.sequence == 0

    # ── task.completing ─────────────────────────────────────────────────────

    def test_task_completing_topic(self) -> None:
        e = evt.task_completing("t1")
        assert e.topic == "task.completing"

    def test_task_completing_task_id(self) -> None:
        e = evt.task_completing("task-99")
        assert e.task_id == "task-99"

    def test_task_completing_payload_keys(self) -> None:
        e = evt.task_completing("t1", steps_completed=5, steps_failed=1, sequence=10)
        assert e.payload["steps_completed"] == 5
        assert e.payload["steps_failed"] == 1

    def test_task_completing_defaults(self) -> None:
        e = evt.task_completing("t1")
        assert e.payload["steps_completed"] == 0
        assert e.payload["steps_failed"] == 0
        assert e.sequence == 0

    # ── gate.pre_check ──────────────────────────────────────────────────────

    def test_gate_pre_check_topic(self) -> None:
        e = evt.gate_pre_check("t1", phase_id=1, gate_type="test")
        assert e.topic == "gate.pre_check"

    def test_gate_pre_check_task_id(self) -> None:
        e = evt.gate_pre_check("task-42", phase_id=2, gate_type="lint")
        assert e.task_id == "task-42"

    def test_gate_pre_check_payload_keys(self) -> None:
        e = evt.gate_pre_check(
            "t1", phase_id=2, gate_type="lint", command="ruff check .", sequence=3,
        )
        assert e.payload["phase_id"] == 2
        assert e.payload["gate_type"] == "lint"
        assert e.payload["command"] == "ruff check ."

    def test_gate_pre_check_defaults(self) -> None:
        e = evt.gate_pre_check("t1", phase_id=1, gate_type="review")
        assert e.payload["command"] == ""
        assert e.sequence == 0
