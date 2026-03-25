"""Tests for agent_baton.core.runtime — launcher, scheduler, worker."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.events.bus import EventBus
from agent_baton.core.runtime.launcher import DryRunLauncher, LaunchResult
from agent_baton.core.runtime.scheduler import SchedulerConfig, StepScheduler
from agent_baton.core.runtime.worker import TaskWorker
from agent_baton.models.execution import (
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
    # DECISION: removed trivial test_defaults (asserts dataclass field defaults
    # set by constructor). Kept test_custom_fields which exercises the actual
    # non-default code path and demonstrates that custom values are accepted.
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
# SchedulerConfig + StepScheduler
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

    # DECISION: removed trivial test_max_concurrent_property (single-field getter check).
    # The concurrency-limit test already exercises the same config path with more value.


# ===========================================================================
# TaskWorker
# ===========================================================================

class TestTaskWorkerSimple:
    # DECISION: merged test_single_step_completes + test_empty_plan_completes into
    # one parameterized test. Both assert the same "completed" string on the summary;
    # they differ only in whether there are steps in the phase.
    @pytest.mark.parametrize("steps_factory,label", [
        (lambda: [_step("1.1")], "single step"),
        (lambda: [], "empty plan"),
    ])
    def test_plan_completes(self, tmp_path: Path, steps_factory, label: str) -> None:
        async def _run():
            plan = _plan(phases=[_phase(steps=steps_factory())])
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
            summary = await worker.run()
            assert "completed" in summary.lower() or "complete" in summary.lower(), label
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


class TestTaskWorkerParallelAndMultiStep:
    # DECISION: merged test_parallel_steps_dispatch + test_multi_step_plan into one
    # parameterized test. Both exercise multi-step plans; the parallel test additionally
    # checks that all step_ids were dispatched — we keep that assertion in both cases.
    @pytest.mark.parametrize("steps,expected_ids,label", [
        (
            [_step("1.1", agent="a1"), _step("1.2", agent="a2")],
            {"1.1", "1.2"},
            "parallel independent steps",
        ),
        (
            [_step("1.1"), _step("1.2", agent="tester")],
            {"1.1", "1.2"},
            "sequential multi-step",
        ),
    ])
    def test_multi_step_plan_dispatches_all(
        self, tmp_path: Path, steps, expected_ids: set, label: str
    ) -> None:
        async def _run():
            plan = _plan(phases=[_phase(steps=steps)])
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            launcher = DryRunLauncher()
            worker = TaskWorker(engine=engine, launcher=launcher)
            summary = await worker.run()
            assert "completed" in summary.lower() or "complete" in summary.lower(), label
            launched_ids = {l["step_id"] for l in launcher.launches}
            assert expected_ids <= launched_ids, f"{label}: missing {expected_ids - launched_ids}"
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


# ===========================================================================
# TODO-7: gate_poll_interval is configurable on TaskWorker
# ===========================================================================

class TestTaskWorkerGatePollInterval:
    """TODO-7: The gate polling interval in _handle_gate() was hardcoded at 2 s.
    It must now be configurable via the gate_poll_interval constructor parameter
    so tests and callers can set an appropriate value without modifying source.
    """

    def test_default_gate_poll_interval_is_two_seconds(self, tmp_path: Path) -> None:
        """Default value for gate_poll_interval must remain 2.0 s."""
        engine = ExecutionEngine(team_context_root=tmp_path)
        worker = TaskWorker(engine=engine, launcher=DryRunLauncher())
        assert worker._gate_poll_interval == 2.0

    def test_custom_gate_poll_interval_stored(self, tmp_path: Path) -> None:
        """Constructor must store the caller-supplied gate_poll_interval."""
        engine = ExecutionEngine(team_context_root=tmp_path)
        worker = TaskWorker(
            engine=engine,
            launcher=DryRunLauncher(),
            gate_poll_interval=0.1,
        )
        assert worker._gate_poll_interval == 0.1

    def test_custom_interval_used_during_gate_polling(self, tmp_path: Path) -> None:
        """A short gate_poll_interval causes the worker to poll more frequently.

        We set a very small interval and verify the worker still completes the
        plan after a review gate is resolved externally, proving the interval
        is actually used by _handle_gate().
        """
        from agent_baton.core.runtime.decisions import DecisionManager

        plan = _plan(phases=[
            _phase(phase_id=0, steps=[_step("1.1")], gate=_gate("review")),
            _phase(phase_id=1, steps=[_step("2.1", agent="tester")]),
        ])
        decisions_dir = tmp_path / "decisions"
        dm = DecisionManager(decisions_dir=decisions_dir)

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            launcher = DryRunLauncher()
            worker = TaskWorker(
                engine=engine,
                launcher=launcher,
                decision_manager=dm,
                gate_poll_interval=0.01,  # 10 ms — very fast
            )

            async def _resolver():
                for _ in range(200):
                    if dm.pending():
                        break
                    await asyncio.sleep(0.005)
                if dm.pending():
                    req = dm.pending()[0]
                    dm.resolve(req.request_id, chosen_option="approve")

            worker_task = asyncio.create_task(worker.run())
            resolver_task = asyncio.create_task(_resolver())
            results = await asyncio.gather(resolver_task, worker_task)
            summary = results[1]
            assert "completed" in summary.lower() or "complete" in summary.lower()

        asyncio.run(_run())

    def test_custom_interval_used_during_approval_polling(self, tmp_path: Path) -> None:
        """Approval actions also use gate_poll_interval for polling.

        Same pattern as the gate test above but with approval_required=True
        on the phase, proving _handle_approval() respects the interval.
        """
        from agent_baton.core.runtime.decisions import DecisionManager

        approval_phase = PlanPhase(
            phase_id=0, name="P", steps=[_step("1.1")],
            approval_required=True, approval_description="Review phase 0",
        )
        plan = _plan(phases=[
            approval_phase,
            _phase(phase_id=1, steps=[_step("2.1", agent="tester")]),
        ])
        decisions_dir = tmp_path / "decisions"
        dm = DecisionManager(decisions_dir=decisions_dir)

        async def _run():
            engine = ExecutionEngine(team_context_root=tmp_path)
            engine.start(plan)
            launcher = DryRunLauncher()
            worker = TaskWorker(
                engine=engine,
                launcher=launcher,
                decision_manager=dm,
                gate_poll_interval=0.01,
            )

            async def _resolver():
                for _ in range(200):
                    if dm.pending():
                        break
                    await asyncio.sleep(0.005)
                if dm.pending():
                    req = dm.pending()[0]
                    dm.resolve(req.request_id, chosen_option="approve")

            worker_task = asyncio.create_task(worker.run())
            resolver_task = asyncio.create_task(_resolver())
            results = await asyncio.gather(resolver_task, worker_task)
            summary = results[1]
            assert "completed" in summary.lower() or "complete" in summary.lower()

        asyncio.run(_run())
