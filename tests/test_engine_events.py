"""Tests for EventBus integration in ExecutionEngine."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.events.bus import EventBus
from agent_baton.models.events import Event
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Factories (mirroring test_executor.py helpers)
# ---------------------------------------------------------------------------

def _step(step_id: str = "1.1", agent: str = "backend", **kw) -> PlanStep:
    return PlanStep(step_id=step_id, agent_name=agent, task_description="task", **kw)


def _gate(gate_type: str = "test") -> PlanGate:
    return PlanGate(gate_type=gate_type, command="pytest")


def _phase(phase_id: int = 0, name: str = "P", steps=None, gate=None) -> PlanPhase:
    return PlanPhase(phase_id=phase_id, name=name, steps=steps or [_step()], gate=gate)


def _plan(task_id: str = "t1", phases=None) -> MachinePlan:
    return MachinePlan(
        task_id=task_id, task_summary="test plan",
        phases=phases or [_phase()],
    )


def _engine_with_bus(tmp_path: Path) -> tuple[ExecutionEngine, EventBus]:
    bus = EventBus()
    engine = ExecutionEngine(team_context_root=tmp_path, bus=bus)
    return engine, bus


def _topics(bus: EventBus, task_id: str = "t1") -> list[str]:
    """Return ordered list of event topics for a task."""
    return [e.topic for e in bus.replay(task_id)]


# ---------------------------------------------------------------------------
# Backward compatibility: bus=None
# ---------------------------------------------------------------------------

class TestBusNoneBackwardCompat:
    # DECISION: two tests merged into one — the full lifecycle test also exercises
    #   the start() return value, making the standalone start test redundant.

    def test_full_lifecycle_without_bus(self, tmp_path: Path) -> None:
        """bus=None must not raise; start() must return a DISPATCH action."""
        engine = ExecutionEngine(team_context_root=tmp_path)
        action = engine.start(_plan())
        assert action.action_type == ActionType.DISPATCH.value
        engine.record_step_result("1.1", "backend")
        engine.complete()


# ---------------------------------------------------------------------------
# start() publishes events
# ---------------------------------------------------------------------------

class TestStartEvents:
    def test_publishes_task_started(self, tmp_path: Path) -> None:
        engine, bus = _engine_with_bus(tmp_path)
        engine.start(_plan())
        topics = _topics(bus)
        assert "task.started" in topics

    def test_publishes_phase_started(self, tmp_path: Path) -> None:
        engine, bus = _engine_with_bus(tmp_path)
        engine.start(_plan())
        topics = _topics(bus)
        assert "phase.started" in topics

    def test_task_started_payload(self, tmp_path: Path) -> None:
        engine, bus = _engine_with_bus(tmp_path)
        engine.start(_plan(task_id="my-task"))
        evts = [e for e in bus.replay("my-task") if e.topic == "task.started"]
        assert len(evts) == 1
        assert evts[0].payload["task_summary"] == "test plan"

    def test_phase_started_payload(self, tmp_path: Path) -> None:
        engine, bus = _engine_with_bus(tmp_path)
        plan = _plan(phases=[_phase(phase_id=0, name="Build", steps=[_step()])])
        engine.start(plan)
        evts = [e for e in bus.replay("t1") if e.topic == "phase.started"]
        assert len(evts) == 1
        assert evts[0].payload["phase_name"] == "Build"


# ---------------------------------------------------------------------------
# record_step_result() publishes events
# ---------------------------------------------------------------------------

class TestStepResultEvents:
    def test_step_completed_event(self, tmp_path: Path) -> None:
        engine, bus = _engine_with_bus(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend", status="complete", outcome="done")
        evts = [e for e in bus.replay("t1") if e.topic == "step.completed"]
        assert len(evts) == 1
        assert evts[0].payload["step_id"] == "1.1"
        assert evts[0].payload["outcome"] == "done"

    def test_step_failed_event(self, tmp_path: Path) -> None:
        engine, bus = _engine_with_bus(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend", status="failed", error="boom")
        evts = [e for e in bus.replay("t1") if e.topic == "step.failed"]
        assert len(evts) == 1
        assert evts[0].payload["error"] == "boom"

    def test_step_dispatched_event(self, tmp_path: Path) -> None:
        engine, bus = _engine_with_bus(tmp_path)
        engine.start(_plan())
        engine.mark_dispatched("1.1", "backend")
        evts = [e for e in bus.replay("t1") if e.topic == "step.dispatched"]
        assert len(evts) == 1
        assert evts[0].payload["agent_name"] == "backend"


# ---------------------------------------------------------------------------
# record_gate_result() publishes events
# ---------------------------------------------------------------------------

class TestGateResultEvents:
    def test_gate_passed_event(self, tmp_path: Path) -> None:
        engine, bus = _engine_with_bus(tmp_path)
        plan = _plan(phases=[_phase(gate=_gate())])
        engine.start(plan)
        engine.record_step_result("1.1", "backend")
        engine.next_action()  # triggers gate_pending
        engine.record_gate_result(phase_id=0, passed=True, output="all green")
        evts = [e for e in bus.replay("t1") if e.topic == "gate.passed"]
        assert len(evts) == 1
        assert evts[0].payload["output"] == "all green"

    def test_gate_failed_event(self, tmp_path: Path) -> None:
        engine, bus = _engine_with_bus(tmp_path)
        plan = _plan(phases=[_phase(gate=_gate())])
        engine.start(plan)
        engine.record_step_result("1.1", "backend")
        engine.next_action()  # triggers gate_pending
        engine.record_gate_result(phase_id=0, passed=False, output="failures")
        evts = [e for e in bus.replay("t1") if e.topic == "gate.failed"]
        assert len(evts) == 1


# ---------------------------------------------------------------------------
# complete() publishes events
# ---------------------------------------------------------------------------

class TestCompleteEvents:
    def test_publishes_task_completed(self, tmp_path: Path) -> None:
        engine, bus = _engine_with_bus(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend")
        engine.complete()
        evts = [e for e in bus.replay("t1") if e.topic == "task.completed"]
        assert len(evts) == 1


# ---------------------------------------------------------------------------
# Phase transitions publish events
# ---------------------------------------------------------------------------

class TestPhaseTransitionEvents:
    def test_phase_advance_publishes_completed_and_started(self, tmp_path: Path) -> None:
        plan = _plan(phases=[
            _phase(phase_id=0, name="P1", steps=[_step("1.1")]),
            _phase(phase_id=1, name="P2", steps=[_step("2.1", agent="tester")]),
        ])
        engine, bus = _engine_with_bus(tmp_path)
        engine.start(plan)
        engine.record_step_result("1.1", "backend")
        engine.next_action()  # should advance to phase 1
        topics = _topics(bus)
        assert "phase.completed" in topics
        # Should have two phase.started: one for P1, one for P2
        started = [e for e in bus.replay("t1") if e.topic == "phase.started"]
        assert len(started) == 2


# ---------------------------------------------------------------------------
# Events persist to disk
# ---------------------------------------------------------------------------

class TestEventPersistence:
    def test_events_written_to_jsonl(self, tmp_path: Path) -> None:
        engine, bus = _engine_with_bus(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend")
        engine.complete()
        events_dir = tmp_path / "events"
        assert events_dir.is_dir()
        jsonl_files = list(events_dir.glob("*.jsonl"))
        assert len(jsonl_files) == 1
        lines = jsonl_files[0].read_text().strip().splitlines()
        assert len(lines) >= 3  # task.started + phase.started + step.completed + task.completed

    def test_sequence_numbers_are_monotonic(self, tmp_path: Path) -> None:
        engine, bus = _engine_with_bus(tmp_path)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend")
        engine.complete()
        events = bus.replay("t1")
        seqs = [e.sequence for e in events]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)  # all unique
