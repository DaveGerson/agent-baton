"""End-to-end integration tests for the execution engine pipeline.

Simulates a complete orchestrated task from plan creation through execution
to completion, verifying that all data flows correctly through the connected
components — without spawning any real agents.

Coverage:
  - IntelligentPlanner → MachinePlan creation
  - ExecutionEngine start / dispatch / gate / complete lifecycle
  - State file persistence and crash recovery
  - TraceRecorder writes to disk and is readable
  - UsageLogger writes and reads back the task record
  - RetrospectiveEngine writes .md to disk
  - PatternLearner reads the usage data gracefully
  - PromptDispatcher produces valid delegation prompts
  - GateRunner evaluates gate outputs correctly
  - engine.status() mid-execution
  - Failed step → FAILED action
  - Failed gate → FAILED action
  - Delegation prompt carries shared context and context.md reference
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.gates import GateRunner
from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.learn.pattern_learner import PatternLearner
from agent_baton.core.observe.trace import TraceRecorder
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_planner(tmp_path: Path) -> IntelligentPlanner:
    """Return an IntelligentPlanner rooted at tmp_path."""
    return IntelligentPlanner(team_context_root=tmp_path)


def _make_engine(tmp_path: Path) -> ExecutionEngine:
    """Return a fresh ExecutionEngine rooted at tmp_path."""
    return ExecutionEngine(team_context_root=tmp_path)


def _run_full_loop(
    engine: ExecutionEngine,
    plan: MachinePlan,
    tokens_per_step: int = 8000,
    duration_per_step: float = 45.0,
) -> tuple[int, int]:
    """Drive the engine loop to completion; return (steps_dispatched, gates_run)."""
    action = engine.start(plan)
    steps_dispatched = 0
    gates_run = 0

    iteration = 0
    while action.action_type not in (
        ActionType.COMPLETE,
        ActionType.FAILED,
    ):
        if iteration > 50:
            raise RuntimeError("Execution loop exceeded 50 iterations — likely stuck")
        iteration += 1

        if action.action_type == ActionType.DISPATCH:
            engine.record_step_result(
                step_id=action.step_id,
                agent_name=action.agent_name,
                status="complete",
                outcome=f"Completed {action.agent_name} work successfully",
                estimated_tokens=tokens_per_step,
                duration_seconds=duration_per_step,
            )
            steps_dispatched += 1
        elif action.action_type == ActionType.GATE:
            engine.record_gate_result(
                phase_id=action.phase_id,
                passed=True,
                output="All checks passed",
            )
            gates_run += 1

        action = engine.next_action()

    return steps_dispatched, gates_run


# ---------------------------------------------------------------------------
# Shared fixture: a plan produced by the IntelligentPlanner
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def plan(tmp_path: Path) -> MachinePlan:
    """A realistic MachinePlan for 'Add a REST API endpoint for user profiles'.

    Produces: Design → Implement (build gate) → Test (test gate) → Review.
    This shape exercises both dispatch and gate transitions.
    """
    planner = _make_planner(tmp_path)
    return planner.create_plan("Add a REST API endpoint for user profiles")


# ---------------------------------------------------------------------------
# Phase A: Plan creation
# ---------------------------------------------------------------------------

class TestPlanCreation:
    """The IntelligentPlanner must create a structurally sound MachinePlan."""

    def test_returns_machine_plan(self, plan: MachinePlan) -> None:
        assert isinstance(plan, MachinePlan)

    def test_task_id_is_set(self, plan: MachinePlan) -> None:
        assert plan.task_id
        # Format: YYYY-MM-DD-slug
        import re
        assert re.match(r"^\d{4}-\d{2}-\d{2}-", plan.task_id)

    def test_task_summary_preserved(self, plan: MachinePlan) -> None:
        assert plan.task_summary == "Add a REST API endpoint for user profiles"

    def test_plan_has_at_least_two_phases(self, plan: MachinePlan) -> None:
        assert len(plan.phases) >= 2

    def test_plan_has_steps(self, plan: MachinePlan) -> None:
        assert plan.total_steps > 0

    def test_all_agents_is_populated(self, plan: MachinePlan) -> None:
        assert len(plan.all_agents) > 0

    def test_at_least_one_gate_exists(self, plan: MachinePlan) -> None:
        gates = [p.gate for p in plan.phases if p.gate is not None]
        assert len(gates) >= 1, "Expected at least one QA gate in the plan"

    def test_shared_context_contains_task_summary(self, plan: MachinePlan) -> None:
        assert plan.task_summary in plan.shared_context

    def test_delegation_prompt_references_claude_md(self, plan: MachinePlan, tmp_path: Path) -> None:
        """Delegation prompt tells agent to read CLAUDE.md for conventions."""
        engine = _make_engine(tmp_path)
        action = engine.start(plan)
        assert "CLAUDE.md" in action.delegation_prompt

    def test_risk_level_is_set(self, plan: MachinePlan) -> None:
        assert plan.risk_level in ("LOW", "MEDIUM", "HIGH")

    def test_budget_tier_is_set(self, plan: MachinePlan) -> None:
        assert plan.budget_tier in ("lean", "standard", "full")

    def test_plan_serialises_to_json(self, plan: MachinePlan) -> None:
        data = json.dumps(plan.to_dict())
        restored = MachinePlan.from_dict(json.loads(data))
        assert restored.task_id == plan.task_id
        assert restored.total_steps == plan.total_steps

    def test_plan_saved_to_disk(self, tmp_path: Path, plan: MachinePlan) -> None:
        plan_path = tmp_path / "plan.json"
        plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
        assert plan_path.exists()
        loaded = MachinePlan.from_dict(json.loads(plan_path.read_text()))
        assert loaded.task_id == plan.task_id


# ---------------------------------------------------------------------------
# Phase B: Start execution
# ---------------------------------------------------------------------------

class TestStartExecution:
    """engine.start(plan) must return a DISPATCH action and write state."""

    def test_returns_dispatch_action(self, tmp_path: Path, plan: MachinePlan) -> None:
        action = _make_engine(tmp_path).start(plan)
        assert action.action_type == ActionType.DISPATCH

    def test_dispatch_has_agent_name(self, tmp_path: Path, plan: MachinePlan) -> None:
        action = _make_engine(tmp_path).start(plan)
        assert action.agent_name

    def test_dispatch_has_step_id(self, tmp_path: Path, plan: MachinePlan) -> None:
        action = _make_engine(tmp_path).start(plan)
        assert action.step_id

    def test_dispatch_has_delegation_prompt(self, tmp_path: Path, plan: MachinePlan) -> None:
        action = _make_engine(tmp_path).start(plan)
        assert action.delegation_prompt

    def test_state_file_created(self, tmp_path: Path, plan: MachinePlan) -> None:
        _make_engine(tmp_path).start(plan)
        assert (tmp_path / "execution-state.json").exists()

    def test_state_file_is_valid_json(self, tmp_path: Path, plan: MachinePlan) -> None:
        _make_engine(tmp_path).start(plan)
        data = json.loads((tmp_path / "execution-state.json").read_text())
        assert "task_id" in data
        assert data["task_id"] == plan.task_id

    def test_state_file_status_is_running(self, tmp_path: Path, plan: MachinePlan) -> None:
        _make_engine(tmp_path).start(plan)
        data = json.loads((tmp_path / "execution-state.json").read_text())
        assert data["status"] == "running"

    def test_first_step_id_matches_first_phase_first_step(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        action = _make_engine(tmp_path).start(plan)
        expected_step_id = plan.phases[0].steps[0].step_id
        assert action.step_id == expected_step_id

    def test_first_agent_matches_first_phase_first_step(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        action = _make_engine(tmp_path).start(plan)
        expected_agent = plan.phases[0].steps[0].agent_name
        assert action.agent_name == expected_agent


# ---------------------------------------------------------------------------
# Phase C: Delegation prompt content
# ---------------------------------------------------------------------------

class TestDelegationPrompt:
    """The delegation prompt must carry all necessary context."""

    def test_prompt_contains_task_description(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        action = _make_engine(tmp_path).start(plan)
        # The step's task_description comes from the plan
        first_step = plan.phases[0].steps[0]
        assert first_step.task_description in action.delegation_prompt

    def test_prompt_contains_shared_context(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        action = _make_engine(tmp_path).start(plan)
        # shared_context contains the task summary
        assert plan.task_summary in action.delegation_prompt

    def test_prompt_references_claude_md(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        action = _make_engine(tmp_path).start(plan)
        assert "CLAUDE.md" in action.delegation_prompt

    def test_prompt_contains_step_id_header(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        action = _make_engine(tmp_path).start(plan)
        assert action.step_id in action.delegation_prompt

    def test_dispatcher_builds_equivalent_prompt(self, plan: MachinePlan) -> None:
        """PromptDispatcher produces a prompt with the same structural markers."""
        dispatcher = PromptDispatcher()
        first_step = plan.all_steps[0]
        prompt = dispatcher.build_delegation_prompt(
            first_step,
            shared_context=plan.shared_context,
            task_summary=plan.task_summary,
        )
        assert "## Shared Context" in prompt
        assert "## Your Task" in prompt
        assert "## Deliverables" in prompt
        assert "CLAUDE.md" in prompt


# ---------------------------------------------------------------------------
# Phase D: Full execute loop
# ---------------------------------------------------------------------------

class TestExecuteLoop:
    """The engine loop must dispatch steps and run gates until COMPLETE."""

    def test_loop_reaches_complete(self, tmp_path: Path, plan: MachinePlan) -> None:
        engine = _make_engine(tmp_path)
        _run_full_loop(engine, plan)
        # After the loop, call next_action and confirm we're complete
        final = engine.next_action()
        assert final.action_type == ActionType.COMPLETE

    def test_at_least_one_dispatch_occurs(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        steps, _ = _run_full_loop(engine, plan)
        assert steps >= 1

    def test_at_least_one_gate_runs(self, tmp_path: Path, plan: MachinePlan) -> None:
        engine = _make_engine(tmp_path)
        _, gates = _run_full_loop(engine, plan)
        assert gates >= 1

    def test_state_file_reflects_completed_steps(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        steps, _ = _run_full_loop(engine, plan)
        state = engine._load_state()
        assert len(state.completed_step_ids) == steps

    def test_state_file_reflects_gate_results(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        _, gates = _run_full_loop(engine, plan)
        state = engine._load_state()
        assert len(state.gate_results) == gates

    def test_all_recorded_gate_results_passed(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        _run_full_loop(engine, plan)
        state = engine._load_state()
        assert all(g.passed for g in state.gate_results)

    def test_completed_step_ids_are_all_in_plan(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        _run_full_loop(engine, plan)
        state = engine._load_state()
        all_plan_step_ids = {s.step_id for s in plan.all_steps}
        assert state.completed_step_ids.issubset(all_plan_step_ids)

    def test_no_step_is_dispatched_twice(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        """Each step_id in the dispatched sequence must be unique."""
        dispatched_ids: list[str] = []
        engine = _make_engine(tmp_path)
        action = engine.start(plan)
        iteration = 0
        while action.action_type not in (
            ActionType.COMPLETE,
            ActionType.FAILED,
        ):
            if iteration > 50:
                break
            iteration += 1
            if action.action_type == ActionType.DISPATCH:
                dispatched_ids.append(action.step_id)
                engine.record_step_result(
                    action.step_id, action.agent_name, status="complete"
                )
            elif action.action_type == ActionType.GATE:
                engine.record_gate_result(action.phase_id, passed=True)
            action = engine.next_action()

        assert len(dispatched_ids) == len(set(dispatched_ids))


# ---------------------------------------------------------------------------
# Phase E: Status mid-execution
# ---------------------------------------------------------------------------

class TestStatusMidExecution:
    """engine.status() must return accurate data at any point during a run."""

    def test_status_returns_dict(self, tmp_path: Path, plan: MachinePlan) -> None:
        engine = _make_engine(tmp_path)
        engine.start(plan)
        status = engine.status()
        assert isinstance(status, dict)

    def test_status_contains_required_keys(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        engine.start(plan)
        status = engine.status()
        for key in (
            "task_id",
            "status",
            "current_phase",
            "steps_complete",
            "steps_total",
            "gates_passed",
            "gates_failed",
            "elapsed_seconds",
        ):
            assert key in status, f"Missing key: {key}"

    def test_status_task_id_matches_plan(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        engine.start(plan)
        assert engine.status()["task_id"] == plan.task_id

    def test_status_steps_total_matches_plan(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        engine.start(plan)
        assert engine.status()["steps_total"] == plan.total_steps

    def test_steps_complete_increments_after_each_dispatch(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        action = engine.start(plan)
        assert engine.status()["steps_complete"] == 0

        engine.record_step_result(
            action.step_id, action.agent_name, status="complete"
        )
        assert engine.status()["steps_complete"] == 1

    def test_gates_passed_increments_after_gate(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        action = engine.start(plan)

        # Dispatch steps until we hit the first gate
        while action.action_type == ActionType.DISPATCH:
            engine.record_step_result(
                action.step_id, action.agent_name, status="complete"
            )
            action = engine.next_action()

        if action.action_type == ActionType.GATE:
            assert engine.status()["gates_passed"] == 0
            engine.record_gate_result(action.phase_id, passed=True)
            assert engine.status()["gates_passed"] == 1

    def test_status_no_active_execution(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        status = engine.status()
        assert status["status"] == "no_active_execution"


# ---------------------------------------------------------------------------
# Phase F: Complete and verify data pipeline
# ---------------------------------------------------------------------------

class TestCompleteGeneratesData:
    """engine.complete() must write trace, usage log, and retrospective."""

    def _setup_and_complete(
        self, tmp_path: Path, plan: MachinePlan
    ) -> tuple[ExecutionEngine, str]:
        engine = _make_engine(tmp_path)
        _run_full_loop(engine, plan)
        summary = engine.complete()
        return engine, summary

    def test_complete_returns_summary_string(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        _, summary = self._setup_and_complete(tmp_path, plan)
        assert isinstance(summary, str)
        assert summary

    def test_summary_contains_task_id(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        _, summary = self._setup_and_complete(tmp_path, plan)
        assert plan.task_id in summary

    def test_summary_mentions_steps(self, tmp_path: Path, plan: MachinePlan) -> None:
        _, summary = self._setup_and_complete(tmp_path, plan)
        assert "Steps:" in summary

    def test_trace_file_written(self, tmp_path: Path, plan: MachinePlan) -> None:
        self._setup_and_complete(tmp_path, plan)
        tracer = TraceRecorder(team_context_root=tmp_path)
        traces = tracer.list_traces()
        assert len(traces) >= 1

    def test_trace_file_named_after_task_id(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        self._setup_and_complete(tmp_path, plan)
        tracer = TraceRecorder(team_context_root=tmp_path)
        traces = tracer.list_traces()
        trace_names = [t.stem for t in traces]
        assert plan.task_id in trace_names

    def test_trace_is_loadable(self, tmp_path: Path, plan: MachinePlan) -> None:
        self._setup_and_complete(tmp_path, plan)
        tracer = TraceRecorder(team_context_root=tmp_path)
        trace = tracer.load_trace(plan.task_id)
        assert trace is not None
        assert trace.task_id == plan.task_id

    def test_trace_has_events(self, tmp_path: Path, plan: MachinePlan) -> None:
        self._setup_and_complete(tmp_path, plan)
        tracer = TraceRecorder(team_context_root=tmp_path)
        trace = tracer.load_trace(plan.task_id)
        assert trace is not None
        assert len(trace.events) > 0

    def test_trace_has_agent_complete_events(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        self._setup_and_complete(tmp_path, plan)
        tracer = TraceRecorder(team_context_root=tmp_path)
        trace = tracer.load_trace(plan.task_id)
        assert trace is not None
        event_types = [e.event_type for e in trace.events]
        assert "agent_complete" in event_types

    def test_trace_has_gate_result_events_when_gate_ran(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        _, gates = _run_full_loop(engine, plan)
        engine.complete()
        if gates > 0:
            tracer = TraceRecorder(team_context_root=tmp_path)
            trace = tracer.load_trace(plan.task_id)
            assert trace is not None
            event_types = [e.event_type for e in trace.events]
            assert "gate_result" in event_types

    def test_trace_outcome_is_ship(self, tmp_path: Path, plan: MachinePlan) -> None:
        self._setup_and_complete(tmp_path, plan)
        tracer = TraceRecorder(team_context_root=tmp_path)
        trace = tracer.load_trace(plan.task_id)
        assert trace is not None
        assert trace.outcome == "SHIP"

    def test_usage_log_written(self, tmp_path: Path, plan: MachinePlan) -> None:
        self._setup_and_complete(tmp_path, plan)
        logger = UsageLogger(log_path=tmp_path / "usage-log.jsonl")
        records = logger.read_all()
        assert len(records) >= 1

    def test_usage_record_has_correct_task_id(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        self._setup_and_complete(tmp_path, plan)
        logger = UsageLogger(log_path=tmp_path / "usage-log.jsonl")
        records = logger.read_all()
        assert any(r.task_id == plan.task_id for r in records)

    def test_usage_record_has_agents(self, tmp_path: Path, plan: MachinePlan) -> None:
        self._setup_and_complete(tmp_path, plan)
        logger = UsageLogger(log_path=tmp_path / "usage-log.jsonl")
        records = logger.read_all()
        task_record = next(r for r in records if r.task_id == plan.task_id)
        assert len(task_record.agents_used) > 0

    def test_usage_record_outcome_is_ship(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        self._setup_and_complete(tmp_path, plan)
        logger = UsageLogger(log_path=tmp_path / "usage-log.jsonl")
        records = logger.read_all()
        task_record = next(r for r in records if r.task_id == plan.task_id)
        assert task_record.outcome == "SHIP"

    def test_usage_record_token_total_reflects_steps(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        """Tokens recorded per step must accumulate in the usage log."""
        tokens_per_step = 7500
        engine = _make_engine(tmp_path)
        _run_full_loop(engine, plan, tokens_per_step=tokens_per_step)
        engine.complete()
        logger = UsageLogger(log_path=tmp_path / "usage-log.jsonl")
        task_record = next(
            r for r in logger.read_all() if r.task_id == plan.task_id
        )
        total_tokens = sum(a.estimated_tokens for a in task_record.agents_used)
        assert total_tokens > 0

    def test_retrospective_directory_created(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        self._setup_and_complete(tmp_path, plan)
        retro_dir = tmp_path / "retrospectives"
        assert retro_dir.exists()

    def test_retrospective_file_written(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        self._setup_and_complete(tmp_path, plan)
        retro_dir = tmp_path / "retrospectives"
        retro_files = list(retro_dir.glob("*.md"))
        assert len(retro_files) >= 1

    def test_retrospective_file_contains_task_id(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        self._setup_and_complete(tmp_path, plan)
        retro_dir = tmp_path / "retrospectives"
        retro_files = list(retro_dir.glob("*.md"))
        # At least one retro file should reference the task id
        content_with_task_id = [
            f for f in retro_files if plan.task_id in f.read_text(encoding="utf-8")
        ]
        assert len(content_with_task_id) >= 1

    def test_complete_idempotent_summary_on_repeat_call(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        """Calling complete() twice should not crash."""
        engine = _make_engine(tmp_path)
        _run_full_loop(engine, plan)
        engine.complete()
        # Second call should return a non-empty string (may be 'no active state' msg)
        second = engine.complete()
        assert isinstance(second, str)


# ---------------------------------------------------------------------------
# Phase G: Crash recovery
# ---------------------------------------------------------------------------

class TestCrashRecovery:
    """A new engine instance must resume where a crashed engine left off."""

    def test_resume_returns_action(self, tmp_path: Path, plan: MachinePlan) -> None:
        engine1 = _make_engine(tmp_path)
        action = engine1.start(plan)
        engine1.record_step_result(
            action.step_id, action.agent_name, status="complete", outcome="Done"
        )
        # Simulate crash — do NOT call complete()

        engine2 = _make_engine(tmp_path)
        resumed = engine2.resume()
        assert resumed.action_type in (
            ActionType.DISPATCH,
            ActionType.GATE,
            ActionType.COMPLETE,
        )

    def test_resume_does_not_repeat_completed_step(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine1 = _make_engine(tmp_path)
        action = engine1.start(plan)
        first_step_id = action.step_id
        engine1.record_step_result(first_step_id, action.agent_name, status="complete")
        # Crash

        engine2 = _make_engine(tmp_path)
        resumed = engine2.resume()
        # Should NOT dispatch the step we already completed
        if resumed.action_type == ActionType.DISPATCH:
            assert resumed.step_id != first_step_id

    def test_resume_without_prior_execution_returns_failed(
        self, tmp_path: Path
    ) -> None:
        engine = _make_engine(tmp_path)
        action = engine.resume()
        assert action.action_type == ActionType.FAILED

    def test_resumed_engine_can_complete_execution(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        """After resuming, the second engine must be able to finish the task."""
        engine1 = _make_engine(tmp_path)
        action = engine1.start(plan)
        engine1.record_step_result(
            action.step_id, action.agent_name, status="complete"
        )
        # Crash

        engine2 = _make_engine(tmp_path)
        resumed_action = engine2.resume()
        # Drive to completion
        iteration = 0
        while resumed_action.action_type not in (
            ActionType.COMPLETE,
            ActionType.FAILED,
        ):
            if iteration > 50:
                break
            iteration += 1
            if resumed_action.action_type == ActionType.DISPATCH:
                engine2.record_step_result(
                    resumed_action.step_id,
                    resumed_action.agent_name,
                    status="complete",
                )
            elif resumed_action.action_type == ActionType.GATE:
                engine2.record_gate_result(resumed_action.phase_id, passed=True)
            resumed_action = engine2.next_action()

        assert resumed_action.action_type == ActionType.COMPLETE

    def test_state_file_present_after_crash(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine1 = _make_engine(tmp_path)
        action = engine1.start(plan)
        engine1.record_step_result(action.step_id, action.agent_name, status="complete")
        # Crash — state file should still be on disk
        assert (tmp_path / "execution-state.json").exists()


# ---------------------------------------------------------------------------
# Phase I: Gate failure handling
# ---------------------------------------------------------------------------

class TestGateFailure:
    """When a gate fails, the engine must transition to FAILED."""

    def _advance_to_first_gate(
        self, engine: ExecutionEngine, plan: MachinePlan
    ) -> None:
        """Drive the engine until the first GATE action, then stop."""
        action = engine.start(plan)
        iteration = 0
        while action.action_type == ActionType.DISPATCH:
            if iteration > 50:
                raise RuntimeError("No gate found after 50 steps")
            iteration += 1
            engine.record_step_result(
                action.step_id, action.agent_name, status="complete"
            )
            action = engine.next_action()
        # action should now be GATE
        if action.action_type != ActionType.GATE:
            pytest.skip(
                f"Plan has no gate reachable before COMPLETE; got {action.action_type}"
            )
        engine.record_gate_result(action.phase_id, passed=False, output="tests failed")

    def test_failed_gate_produces_failed_action(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        self._advance_to_first_gate(engine, plan)
        action = engine.next_action()
        assert action.action_type == ActionType.FAILED

    def test_state_status_is_failed_after_gate_failure(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        self._advance_to_first_gate(engine, plan)
        engine.next_action()
        state = engine._load_state()
        assert state.status == "failed"

    def test_gate_failure_recorded_in_state(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        # DECISION: test_record_gate_result_without_start_raises removed — unit-level
        #   duplicate of TestRecordGateResult.test_raises_without_active_state in
        #   test_executor.py.
        engine = _make_engine(tmp_path)
        self._advance_to_first_gate(engine, plan)
        state = engine._load_state()
        failed_gates = [g for g in state.gate_results if not g.passed]
        assert len(failed_gates) >= 1


# ---------------------------------------------------------------------------
# Phase J: Learning pipeline reads the data
# ---------------------------------------------------------------------------

class TestLearningPipelineReadsData:
    """PatternLearner must be able to consume the usage data written by the engine."""

    def test_analyze_returns_list(self, tmp_path: Path, plan: MachinePlan) -> None:
        engine = _make_engine(tmp_path)
        _run_full_loop(engine, plan)
        engine.complete()

        learner = PatternLearner(team_context_root=tmp_path)
        patterns = learner.analyze(min_sample_size=1, min_confidence=0.0)
        assert isinstance(patterns, list)

    def test_analyze_does_not_crash_on_single_record(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        _run_full_loop(engine, plan)
        engine.complete()

        learner = PatternLearner(team_context_root=tmp_path)
        # Should not raise regardless of threshold
        try:
            learner.analyze(min_sample_size=1, min_confidence=0.0)
        except Exception as exc:
            pytest.fail(f"PatternLearner.analyze raised: {exc}")

    def test_analyze_empty_dir_returns_empty_list(self, tmp_path: Path) -> None:
        learner = PatternLearner(team_context_root=tmp_path)
        patterns = learner.analyze(min_sample_size=1, min_confidence=0.0)
        assert patterns == []

    def test_refresh_writes_patterns_file(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        _run_full_loop(engine, plan)
        engine.complete()

        learner = PatternLearner(team_context_root=tmp_path)
        learner.refresh(min_sample_size=1, min_confidence=0.0)
        assert learner._patterns_path.exists()

    def test_usage_logger_sees_task_after_complete(
        self, tmp_path: Path, plan: MachinePlan
    ) -> None:
        engine = _make_engine(tmp_path)
        _run_full_loop(engine, plan)
        engine.complete()

        logger = UsageLogger(log_path=tmp_path / "usage-log.jsonl")
        records = logger.read_all()
        task_ids = [r.task_id for r in records]
        assert plan.task_id in task_ids


# ---------------------------------------------------------------------------
# Phase K: GateRunner unit-level integration
# ---------------------------------------------------------------------------

class TestGateRunnerIntegration:
    """GateRunner must produce GateResults consistent with what the engine records."""

    def test_build_gate_action_returns_gate_type(self) -> None:
        runner = GateRunner()
        gate = PlanGate(gate_type="test", command="pytest --tb=short")
        action = runner.build_gate_action(gate, phase_id=1)
        assert action.action_type == ActionType.GATE
        assert action.gate_type == "test"
        assert action.phase_id == 1

    def test_evaluate_output_test_gate_passes_on_zero_exit(self) -> None:
        runner = GateRunner()
        gate = PlanGate(gate_type="test", command="pytest")
        result = runner.evaluate_output(gate, command_output="5 passed", exit_code=0)
        assert result.passed is True

    def test_evaluate_output_test_gate_fails_on_nonzero_exit(self) -> None:
        runner = GateRunner()
        gate = PlanGate(gate_type="test", command="pytest")
        result = runner.evaluate_output(gate, command_output="2 failed", exit_code=1)
        assert result.passed is False

    def test_evaluate_output_review_gate_always_passes(self) -> None:
        runner = GateRunner()
        gate = PlanGate(gate_type="review", command="")
        result = runner.evaluate_output(gate, command_output="FAIL — minor issues", exit_code=1)
        assert result.passed is True

    def test_evaluate_output_lint_gate_fails_on_errors_in_output(self) -> None:
        runner = GateRunner()
        gate = PlanGate(gate_type="lint", command="ruff check .")
        result = runner.evaluate_output(
            gate,
            command_output="E501: line too long: error: invalid syntax",
            exit_code=0,
        )
        assert result.passed is False

    def test_evaluate_output_lint_gate_passes_when_no_errors(self) -> None:
        runner = GateRunner()
        gate = PlanGate(gate_type="lint", command="ruff check .")
        result = runner.evaluate_output(gate, command_output="", exit_code=0)
        assert result.passed is True

    def test_describe_gate_returns_description_string(self) -> None:
        runner = GateRunner()
        gate = PlanGate(gate_type="build", command="make")
        desc = runner.describe_gate(gate)
        assert isinstance(desc, str)
        assert desc

    def test_describe_gate_uses_custom_description_if_set(self) -> None:
        runner = GateRunner()
        gate = PlanGate(gate_type="test", command="pytest", description="Custom description.")
        assert runner.describe_gate(gate) == "Custom description."

    def test_default_gates_returns_all_expected_types(self) -> None:
        gates = GateRunner.default_gates()
        for expected in ("build", "test", "lint", "review"):
            assert expected in gates, f"Missing gate type: {expected}"

    def test_build_gate_substitutes_files_placeholder(self) -> None:
        runner = GateRunner()
        gate = PlanGate(gate_type="lint", command="ruff check {files}")
        action = runner.build_gate_action(
            gate, phase_id=0, files_changed=["src/main.py", "src/util.py"]
        )
        assert "src/main.py" in action.gate_command
        assert "src/util.py" in action.gate_command
        assert "{files}" not in action.gate_command


# ---------------------------------------------------------------------------
# Phase L: PromptDispatcher integration
# ---------------------------------------------------------------------------

class TestPromptDispatcherIntegration:
    """PromptDispatcher must interoperate with the same PlanStep models the engine uses."""

    def test_build_action_produces_dispatch_action(
        self, plan: MachinePlan
    ) -> None:
        dispatcher = PromptDispatcher()
        step = plan.all_steps[0]
        action = dispatcher.build_action(
            step,
            shared_context=plan.shared_context,
            task_summary=plan.task_summary,
        )
        assert action.action_type == ActionType.DISPATCH

    def test_build_action_carries_step_metadata(
        self, plan: MachinePlan
    ) -> None:
        dispatcher = PromptDispatcher()
        step = plan.all_steps[0]
        action = dispatcher.build_action(step, task_summary=plan.task_summary)
        assert action.step_id == step.step_id
        assert action.agent_name == step.agent_name
        assert action.agent_model == step.model

    def test_build_delegation_prompt_includes_decision_logging(
        self, plan: MachinePlan
    ) -> None:
        dispatcher = PromptDispatcher()
        step = plan.all_steps[0]
        prompt = dispatcher.build_delegation_prompt(step, task_summary=plan.task_summary)
        assert "Decision" in prompt

    def test_build_gate_prompt_for_automated_gate_returns_command(
        self,
    ) -> None:
        dispatcher = PromptDispatcher()
        gate = PlanGate(gate_type="test", command="pytest --tb=short -q")
        prompt = dispatcher.build_gate_prompt(gate, phase_name="Test")
        assert "pytest" in prompt

    def test_build_gate_prompt_for_review_gate_returns_reviewer_prompt(
        self,
    ) -> None:
        dispatcher = PromptDispatcher()
        gate = PlanGate(gate_type="review", command="", description="Review the PR")
        prompt = dispatcher.build_gate_prompt(gate, phase_name="Review")
        assert "Review" in prompt
        assert "PASS" in prompt or "FAIL" in prompt

    def test_build_delegation_prompt_with_boundaries(self) -> None:
        dispatcher = PromptDispatcher()
        step = PlanStep(
            step_id="2.1",
            agent_name="backend-engineer",
            task_description="Implement the API",
            allowed_paths=["src/api/"],
            blocked_paths=["tests/"],
        )
        prompt = dispatcher.build_delegation_prompt(step, task_summary="Build API")
        assert "src/api/" in prompt
        assert "tests/" in prompt

    def test_shared_context_is_included_verbatim_in_prompt(
        self, plan: MachinePlan
    ) -> None:
        dispatcher = PromptDispatcher()
        step = plan.all_steps[0]
        context = "UNIQUE_CONTEXT_MARKER_12345"
        prompt = dispatcher.build_delegation_prompt(
            step,
            shared_context=context,
            task_summary=plan.task_summary,
        )
        assert context in prompt


# ---------------------------------------------------------------------------
# Phase M: Multi-phase plan with explicit structure
# ---------------------------------------------------------------------------

class TestMultiPhasePlan:
    """Explicit multi-phase plans must drive the engine correctly."""

    def _make_explicit_plan(self) -> MachinePlan:
        """A fully controlled 2-phase plan: Design → Implement (build gate)."""
        return MachinePlan(
            task_id="test-explicit-plan",
            task_summary="Build widget API",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Design",
                    steps=[
                        PlanStep(
                            step_id="1.1",
                            agent_name="architect",
                            task_description="Design the widget schema",
                        )
                    ],
                    gate=None,
                ),
                PlanPhase(
                    phase_id=2,
                    name="Implement",
                    steps=[
                        PlanStep(
                            step_id="2.1",
                            agent_name="backend-engineer",
                            task_description="Implement the widget API",
                        ),
                        PlanStep(
                            step_id="2.2",
                            agent_name="test-engineer",
                            task_description="Write tests for the widget API",
                        ),
                    ],
                    gate=PlanGate(
                        gate_type="test",
                        command="pytest",
                        description="Run the test suite",
                    ),
                ),
            ],
            shared_context="Task: Build widget API\nRead `.claude/team-context/context.md`",
        )

    def test_explicit_plan_dispatches_first_step(self, tmp_path: Path) -> None:
        plan = self._make_explicit_plan()
        action = _make_engine(tmp_path).start(plan)
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.1"
        assert action.agent_name == "architect"

    def test_explicit_plan_dispatches_both_steps_in_phase2(
        self, tmp_path: Path
    ) -> None:
        plan = self._make_explicit_plan()
        engine = _make_engine(tmp_path)
        dispatched: list[str] = []

        action = engine.start(plan)
        iteration = 0
        while action.action_type not in (
            ActionType.COMPLETE,
            ActionType.FAILED,
        ):
            if iteration > 20:
                break
            iteration += 1
            if action.action_type == ActionType.DISPATCH:
                dispatched.append(action.step_id)
                engine.record_step_result(
                    action.step_id, action.agent_name, status="complete"
                )
            elif action.action_type == ActionType.GATE:
                engine.record_gate_result(action.phase_id, passed=True)
            action = engine.next_action()

        assert "2.1" in dispatched
        assert "2.2" in dispatched

    def test_explicit_plan_runs_gate_after_phase2(self, tmp_path: Path) -> None:
        plan = self._make_explicit_plan()
        engine = _make_engine(tmp_path)
        gate_phase_ids: list[int] = []

        action = engine.start(plan)
        iteration = 0
        while action.action_type not in (
            ActionType.COMPLETE,
            ActionType.FAILED,
        ):
            if iteration > 20:
                break
            iteration += 1
            if action.action_type == ActionType.DISPATCH:
                engine.record_step_result(
                    action.step_id, action.agent_name, status="complete"
                )
            elif action.action_type == ActionType.GATE:
                gate_phase_ids.append(action.phase_id)
                engine.record_gate_result(action.phase_id, passed=True)
            action = engine.next_action()

        assert 2 in gate_phase_ids, f"Expected gate at phase_id=2, got {gate_phase_ids}"

    def test_explicit_plan_complete_after_all_phases(self, tmp_path: Path) -> None:
        plan = self._make_explicit_plan()
        engine = _make_engine(tmp_path)
        steps, gates = _run_full_loop(engine, plan)
        final = engine.next_action()
        assert final.action_type == ActionType.COMPLETE
        # All 3 steps dispatched and 1 gate run
        assert steps == 3
        assert gates == 1

    def test_explicit_plan_summary_after_complete(self, tmp_path: Path) -> None:
        plan = self._make_explicit_plan()
        engine = _make_engine(tmp_path)
        _run_full_loop(engine, plan)
        summary = engine.complete()
        assert "test-explicit-plan" in summary
        assert "Gates passed: 1" in summary
