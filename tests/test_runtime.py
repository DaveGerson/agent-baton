"""Tests for agent_baton.core.runtime — launcher, scheduler, worker."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.events.bus import EventBus
from agent_baton.core.runtime.launcher import AgentLauncher, DryRunLauncher, LaunchResult
from agent_baton.core.runtime.scheduler import SchedulerConfig, StepScheduler
from agent_baton.core.runtime.worker import TaskWorker
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Plan factories
# ---------------------------------------------------------------------------

def _step(step_id: str = "1.1", agent: str = "backend", depends_on=None) -> PlanStep:
    return PlanStep(
        step_id=step_id, agent_name=agent, task_description="task",
        depends_on=depends_on or [],
    )


def _gate(gate_type: str = "test") -> PlanGate:
    return PlanGate(gate_type=gate_type, command="pytest")


def _phase(phase_id: int = 0, name: str = "P", steps=None, gate=None) -> PlanPhase:
    return PlanPhase(phase_id=phase_id, name=name, steps=steps or [_step()], gate=gate)


def _plan(task_id: str = "t1", phases=None) -> MachinePlan:
    return MachinePlan(
        task_id=task_id, task_summary="test plan",
        phases=phases or [_phase()],
    )


# ===========================================================================
# LaunchResult
# ===========================================================================

class TestLaunchResult:
    def test_defaults(self) -> None:
        r = LaunchResult(step_id="1.1", agent_name="be")
        assert r.status == "complete"
        assert r.outcome == ""
        assert r.error == ""
        assert r.files_changed == []

    def test_custom_fields(self) -> None:
        r = LaunchResult(
            step_id="1.1", agent_name="be", status="failed",
            error="timeout", duration_seconds=30.0,
        )
        assert r.status == "failed"
        assert r.error == "timeout"
        assert r.duration_seconds == 30.0


# ===========================================================================
# DryRunLauncher
# ===========================================================================

class TestDryRunLauncher:
    def test_default_launch_returns_complete(self) -> None:
        async def _run():
            launcher = DryRunLauncher()
            result = await launcher.launch("be", "sonnet", "do stuff", "1.1")
            assert result.status == "complete"
            assert result.step_id == "1.1"
            assert result.agent_name == "be"
        asyncio.run(_run())

    def test_records_launches(self) -> None:
        async def _run():
            launcher = DryRunLauncher()
            await launcher.launch("be", "sonnet", "prompt", "1.1")
            await launcher.launch("te", "haiku", "test", "1.2")
            assert len(launcher.launches) == 2
            assert launcher.launches[0]["agent_name"] == "be"
            assert launcher.launches[1]["agent_name"] == "te"
        asyncio.run(_run())

    def test_preconfigured_result(self) -> None:
        async def _run():
            launcher = DryRunLauncher()
            custom = LaunchResult(step_id="1.1", agent_name="be", status="failed", error="crash")
            launcher.set_result("1.1", custom)
            result = await launcher.launch("be", "sonnet", "p", "1.1")
            assert result.status == "failed"
            assert result.error == "crash"
        asyncio.run(_run())

    def test_unconfigured_step_uses_default(self) -> None:
        async def _run():
            launcher = DryRunLauncher()
            launcher.set_result("other", LaunchResult(step_id="other", agent_name="x"))
            result = await launcher.launch("be", "sonnet", "p", "1.1")
            assert result.status == "complete"
            assert "dry-run" in result.outcome
        asyncio.run(_run())


# ===========================================================================
# SchedulerConfig
# ===========================================================================

class TestSchedulerConfig:
    def test_default_max_concurrent(self) -> None:
        assert SchedulerConfig().max_concurrent == 3

    def test_custom_config(self) -> None:
        assert SchedulerConfig(max_concurrent=5).max_concurrent == 5


# ===========================================================================
# StepScheduler
# ===========================================================================

class TestStepScheduler:
    def test_single_dispatch(self) -> None:
        async def _run():
            sched = StepScheduler()
            launcher = DryRunLauncher()
            result = await sched.dispatch("be", "sonnet", "p", "1.1", launcher)
            assert result.status == "complete"
            assert result.step_id == "1.1"
        asyncio.run(_run())

    def test_batch_dispatch(self) -> None:
        async def _run():
            sched = StepScheduler()
            launcher = DryRunLauncher()
            steps = [
                {"agent_name": "be", "model": "sonnet", "prompt": "p1", "step_id": "1.1"},
                {"agent_name": "te", "model": "sonnet", "prompt": "p2", "step_id": "1.2"},
            ]
            results = await sched.dispatch_batch(steps, launcher)
            assert len(results) == 2
            ids = {r.step_id for r in results}
            assert ids == {"1.1", "1.2"}
        asyncio.run(_run())

    def test_concurrency_limit_respected(self) -> None:
        """Verify max_concurrent=2 never runs more than 2 simultaneously."""
        max_seen = 0
        active = 0

        class TrackingLauncher:
            async def launch(self, agent_name, model, prompt, step_id=""):
                nonlocal active, max_seen
                active += 1
                max_seen = max(max_seen, active)
                await asyncio.sleep(0.01)
                active -= 1
                return LaunchResult(step_id=step_id, agent_name=agent_name)

        async def _run():
            sched = StepScheduler(SchedulerConfig(max_concurrent=2))
            steps = [
                {"agent_name": f"a{i}", "model": "s", "prompt": "p", "step_id": f"{i}"}
                for i in range(5)
            ]
            await sched.dispatch_batch(steps, TrackingLauncher())

        asyncio.run(_run())
        assert max_seen <= 2

    def test_active_count_returns_to_zero(self) -> None:
        async def _run():
            sched = StepScheduler()
            launcher = DryRunLauncher()
            await sched.dispatch("be", "sonnet", "p", "1.1", launcher)
            assert sched.active_count == 0
        asyncio.run(_run())

    def test_max_concurrent_property(self) -> None:
        sched = StepScheduler(SchedulerConfig(max_concurrent=7))
        assert sched.max_concurrent == 7


# ===========================================================================
# TaskWorker
# ===========================================================================

class TestTaskWorkerSimple:
    def test_single_step_completes(self, tmp_path: Path) -> None:
        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(_plan())
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
            summary = await worker.run()
            assert "completed" in summary.lower() or "complete" in summary.lower()
        asyncio.run(_run())

    def test_is_running_tracks_state(self, tmp_path: Path) -> None:
        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(_plan())
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
            assert not worker.is_running
            summary = await worker.run()
            assert not worker.is_running
        asyncio.run(_run())

    def test_multi_step_plan(self, tmp_path: Path) -> None:
        async def _run():
            plan = _plan(phases=[
                _phase(steps=[_step("1.1"), _step("1.2", agent="tester")])
            ])
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
            summary = await worker.run()
            assert "completed" in summary.lower() or "complete" in summary.lower()
        asyncio.run(_run())


class TestTaskWorkerParallel:
    def test_parallel_steps_dispatch(self, tmp_path: Path) -> None:
        """Two independent steps should both be dispatched."""
        async def _run():
            plan = _plan(phases=[
                _phase(steps=[
                    _step("1.1", agent="a1"),
                    _step("1.2", agent="a2"),
                ])
            ])
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            launcher = DryRunLauncher()
            worker = TaskWorker(engine=engine, launcher=launcher)
            await worker.run()
            launched_ids = {l["step_id"] for l in launcher.launches}
            assert "1.1" in launched_ids
            assert "1.2" in launched_ids
        asyncio.run(_run())


class TestTaskWorkerGates:
    def test_gate_auto_approved(self, tmp_path: Path) -> None:
        async def _run():
            plan = _plan(phases=[
                _phase(phase_id=0, steps=[_step("1.1")], gate=_gate()),
                _phase(phase_id=1, steps=[_step("2.1", agent="te")]),
            ])
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
            summary = await worker.run()
            assert "completed" in summary.lower() or "complete" in summary.lower()
        asyncio.run(_run())


class TestTaskWorkerFailure:
    def test_failed_step_returns_failure(self, tmp_path: Path) -> None:
        async def _run():
            plan = _plan(phases=[_phase(steps=[_step("1.1")])])
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            launcher = DryRunLauncher()
            launcher.set_result(
                "1.1",
                LaunchResult(step_id="1.1", agent_name="backend", status="failed", error="crash"),
            )
            worker = TaskWorker(engine=engine, launcher=launcher)
            summary = await worker.run()
            assert "failed" in summary.lower()
        asyncio.run(_run())


class TestTaskWorkerEmptyPlan:
    def test_empty_plan_completes(self, tmp_path: Path) -> None:
        async def _run():
            plan = _plan(phases=[_phase(steps=[])])
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
            summary = await worker.run()
            assert "completed" in summary.lower() or "complete" in summary.lower()
        asyncio.run(_run())


class TestTaskWorkerBus:
    def test_worker_has_bus(self, tmp_path: Path) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        bus = EventBus()
        worker = TaskWorker(engine=engine, launcher=DryRunLauncher(), bus=bus)
        assert worker.bus is bus

    def test_worker_creates_default_bus(self, tmp_path: Path) -> None:
        engine = ExecutionEngine(team_context_root=tmp_path)
        worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
        assert worker.bus is not None
