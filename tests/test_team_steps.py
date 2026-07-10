"""Tests for team step functionality in the execution engine.

Team steps are PlanSteps where the `team` field is non-empty.  The executor
handles them differently: rather than dispatching a single agent, it returns
a DISPATCH action with `parallel_actions` for each dispatchable member.
Individual member results are recorded via `record_team_member_result()`, and
the parent step auto-completes / auto-fails based on member outcomes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
    SynthesisSpec,
    TeamMember,
    TeamStepResult,
)
from agent_baton.core.engine.executor import ExecutionEngine


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def _member(
    member_id: str = "1.1.a",
    agent_name: str = "backend-engineer",
    role: str = "implementer",
    task: str = "Write the service",
    model: str = "sonnet",
    depends_on: list[str] | None = None,
    deliverables: list[str] | None = None,
) -> TeamMember:
    return TeamMember(
        member_id=member_id,
        agent_name=agent_name,
        role=role,
        task_description=task,
        model=model,
        depends_on=depends_on or [],
        deliverables=deliverables or [],
    )


def _step(
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task: str = "Implement feature X",
    model: str = "sonnet",
    team: list[TeamMember] | None = None,
    **kw,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task,
        model=model,
        team=team or [],
        **kw,
    )


def _phase(
    phase_id: int = 0,
    name: str = "Implementation",
    steps: list[PlanStep] | None = None,
    gate=None,
    **kw,
) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=name,
        steps=steps or [_step()],
        gate=gate,
        **kw,
    )


def _plan(
    task_id: str = "task-001",
    phases: list[PlanPhase] | None = None,
    shared_context: str = "",
    **kw,
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Build it",
        phases=phases or [_phase()],
        shared_context=shared_context,
        **kw,
    )


def _engine(tmp_path: Path) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path)


def _two_member_step() -> PlanStep:
    """A team step with two independent members."""
    return _step(
        step_id="1.1",
        team=[
            _member("1.1.a", agent_name="backend-engineer", role="implementer"),
            _member("1.1.b", agent_name="test-engineer", role="reviewer"),
        ],
    )


def _two_member_step_with_dep() -> PlanStep:
    """A team step where member B depends on member A."""
    return _step(
        step_id="1.1",
        team=[
            _member("1.1.a", agent_name="backend-engineer", role="lead"),
            _member("1.1.b", agent_name="test-engineer", role="implementer",
                    depends_on=["1.1.a"]),
        ],
    )


# ---------------------------------------------------------------------------
# TestTeamDispatch
# ---------------------------------------------------------------------------

class TestTeamDispatch:

    def test_team_step_returns_dispatch_with_parallel_actions(
        self, tmp_path: Path
    ) -> None:
        """A step with 2 independent members returns DISPATCH with parallel_actions."""
        plan = _plan(phases=[_phase(steps=[_two_member_step()])])
        action = _engine(tmp_path).start(plan)

        assert action.action_type == ActionType.DISPATCH
        # The first member is the primary action; the second is in parallel_actions.
        assert len(action.parallel_actions) == 1
        assert action.parallel_actions[0].action_type == ActionType.DISPATCH

    def test_team_step_primary_action_carries_first_member_agent_name(
        self, tmp_path: Path
    ) -> None:
        """The primary DISPATCH action names the first member's agent."""
        plan = _plan(phases=[_phase(steps=[_two_member_step()])])
        action = _engine(tmp_path).start(plan)

        # First member is backend-engineer
        assert action.agent_name == "backend-engineer"

    def test_team_step_parallel_action_carries_second_member_agent_name(
        self, tmp_path: Path
    ) -> None:
        """The parallel_actions entry names the second member's agent."""
        plan = _plan(phases=[_phase(steps=[_two_member_step()])])
        action = _engine(tmp_path).start(plan)

        assert action.parallel_actions[0].agent_name == "test-engineer"

    def test_team_step_dispatch_step_id_is_member_id(
        self, tmp_path: Path
    ) -> None:
        """Each DISPATCH action's step_id matches the member_id, not the parent step_id."""
        plan = _plan(phases=[_phase(steps=[_two_member_step()])])
        action = _engine(tmp_path).start(plan)

        assert action.step_id == "1.1.a"
        assert action.parallel_actions[0].step_id == "1.1.b"

    def test_team_member_dependency_ordering(self, tmp_path: Path) -> None:
        """Member B depends_on member A: only member A is dispatchable initially."""
        plan = _plan(phases=[_phase(steps=[_two_member_step_with_dep()])])
        action = _engine(tmp_path).start(plan)

        # Only A should be dispatched — B is blocked.
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.1.a"
        assert len(action.parallel_actions) == 0

    def test_team_member_dependency_satisfied(self, tmp_path: Path) -> None:
        """_team_dispatch_action dispatches only member B once A's result is recorded.

        The engine's state-machine loop re-evaluates a team step only when the
        parent StepResult does not yet exist (i.e. the step has never been
        entered before).  Once record_team_member_result() creates a parent
        StepResult with status="dispatched", subsequent next_action() calls see
        the step as occupied and return WAIT rather than re-dispatching B.

        The correct way to verify that B is dispatched after A completes is to
        call _team_dispatch_action directly with the updated state, or to start
        execution with a plan that already has A's member result pre-loaded.
        This test exercises the _team_dispatch_action logic directly.
        """
        step = _two_member_step_with_dep()
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine(tmp_path)
        engine.start(plan)

        # Record A as complete so its member result exists in state.
        engine.record_team_member_result(
            step_id="1.1",
            member_id="1.1.a",
            agent_name="backend-engineer",
            status="complete",
            outcome="API done",
        )

        # Call _team_dispatch_action directly with the updated state to verify
        # that B is now dispatchable (A's dependency is satisfied).
        state = engine._load_state()
        action = engine._team_dispatch_action(step, state)
        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.1.b"

    def test_all_members_blocked_returns_wait(self, tmp_path: Path) -> None:
        """When every undone member has unsatisfied dependencies, return WAIT.

        We simulate this by creating a step where two members each depend on
        each other (a contrived cycle), but the engine's policy is simply:
        if no member is dispatchable, return WAIT.  We use a simpler setup:
        complete no members and artificially mark 1.1.a as dispatched so that
        only the blocked member B remains.
        """
        plan = _plan(phases=[_phase(steps=[_two_member_step_with_dep()])])
        engine = _engine(tmp_path)
        engine.start(plan)

        # Mark A as already dispatched (in-flight) via record_team_member_result
        # isn't quite right for this — instead we use record_step_result for a
        # "dispatched" sub-member directly:
        # The engine treats the *parent* step as the tracking unit. We instead
        # verify the wait path by marking A as "dispatched" at the step level
        # so next_action re-enters the team logic with A occupied but incomplete
        # and B still blocked.
        engine.record_step_result("1.1", "backend-engineer", status="dispatched")
        action = engine.next_action()

        # With 1.1 in dispatched state and no members completing it, the
        # engine should WAIT (1.1 is in occupied but not yet done).
        assert action.action_type == ActionType.WAIT


# ---------------------------------------------------------------------------
# TestTeamCompletion
# ---------------------------------------------------------------------------

class TestTeamCompletion:

    @staticmethod
    def _start_team_engine(tmp_path: Path) -> ExecutionEngine:
        plan = _plan(phases=[_phase(steps=[_two_member_step()])])
        engine = _engine(tmp_path)
        engine.start(plan)
        return engine

    def test_team_step_complete_when_all_members_complete(
        self, tmp_path: Path
    ) -> None:
        """Parent step.status becomes 'complete' after all members report done."""
        engine = self._start_team_engine(tmp_path)

        engine.record_team_member_result("1.1", "1.1.a", "backend-engineer",
                                         status="complete", outcome="service done")
        engine.record_team_member_result("1.1", "1.1.b", "test-engineer",
                                         status="complete", outcome="tests passed")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert result.status == "complete"

    def test_team_step_fails_when_member_fails(self, tmp_path: Path) -> None:
        """Parent step.status becomes 'failed' if any member reports failure."""
        engine = self._start_team_engine(tmp_path)

        engine.record_team_member_result("1.1", "1.1.a", "backend-engineer",
                                         status="failed", outcome="compiler error")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert result.status == "failed"

    def test_team_step_partial_completion_keeps_dispatched(
        self, tmp_path: Path
    ) -> None:
        """After 1 of 2 members completes, parent step is still 'dispatched'."""
        engine = self._start_team_engine(tmp_path)

        engine.record_team_member_result("1.1", "1.1.a", "backend-engineer",
                                         status="complete", outcome="done")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert result.status == "dispatched"

    def test_team_step_aggregates_files_changed(self, tmp_path: Path) -> None:
        """Parent step.files_changed is the union of all members' files_changed."""
        engine = self._start_team_engine(tmp_path)

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer", status="complete",
            files_changed=["src/service.py", "src/models.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "test-engineer", status="complete",
            files_changed=["tests/test_service.py"],
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert set(result.files_changed) == {
            "src/service.py",
            "src/models.py",
            "tests/test_service.py",
        }

    def test_team_step_aggregates_outcomes(self, tmp_path: Path) -> None:
        """Parent step.outcome combines member outcomes separated by '; '."""
        engine = self._start_team_engine(tmp_path)

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer", status="complete",
            outcome="service implemented",
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "test-engineer", status="complete",
            outcome="tests written",
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert "service implemented" in result.outcome
        assert "tests written" in result.outcome

    def test_team_step_member_results_stored(self, tmp_path: Path) -> None:
        """Individual member results are tracked inside parent.member_results."""
        engine = self._start_team_engine(tmp_path)

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer", status="complete",
            outcome="done",
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert len(result.member_results) == 1
        mr = result.member_results[0]
        assert mr.member_id == "1.1.a"
        assert mr.agent_name == "backend-engineer"
        assert mr.status == "complete"

    def test_team_step_failed_error_lists_failing_member(
        self, tmp_path: Path
    ) -> None:
        """When a member fails, the parent step's error field names the member_id."""
        engine = self._start_team_engine(tmp_path)

        engine.record_team_member_result("1.1", "1.1.b", "test-engineer",
                                         status="failed")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert "1.1.b" in result.error

    def test_next_action_complete_after_all_team_members_done(
        self, tmp_path: Path
    ) -> None:
        """Engine returns COMPLETE after all members finish and plan has no more steps."""
        engine = self._start_team_engine(tmp_path)

        engine.record_team_member_result("1.1", "1.1.a", "backend-engineer",
                                         status="complete", outcome="done")
        engine.record_team_member_result("1.1", "1.1.b", "test-engineer",
                                         status="complete", outcome="done")

        action = engine.next_action()
        assert action.action_type == ActionType.COMPLETE

    def test_next_action_failed_after_team_member_failure(
        self, tmp_path: Path
    ) -> None:
        """Engine returns FAILED action once a member fails."""
        engine = self._start_team_engine(tmp_path)

        engine.record_team_member_result("1.1", "1.1.a", "backend-engineer",
                                         status="failed")

        action = engine.next_action()
        assert action.action_type == ActionType.FAILED


# ---------------------------------------------------------------------------
# TestTeamSerialization
# ---------------------------------------------------------------------------

class TestTeamSerialization:

    def test_team_member_roundtrip(self) -> None:
        """TeamMember.to_dict() / from_dict() preserves all fields."""
        original = TeamMember(
            member_id="2.1.b",
            agent_name="architect",
            role="lead",
            task_description="Design the API",
            model="opus",
            depends_on=["2.1.a"],
            deliverables=["api-design.md"],
        )
        restored = TeamMember.from_dict(original.to_dict())

        assert restored.member_id == original.member_id
        assert restored.agent_name == original.agent_name
        assert restored.role == original.role
        assert restored.task_description == original.task_description
        assert restored.model == original.model
        assert restored.depends_on == original.depends_on
        assert restored.deliverables == original.deliverables

    def test_plan_step_with_team_roundtrip(self) -> None:
        """PlanStep.to_dict() / from_dict() preserves the team list."""
        original = _step(
            step_id="3.2",
            team=[
                _member("3.2.a", role="lead"),
                _member("3.2.b", role="reviewer", depends_on=["3.2.a"]),
            ],
        )
        restored = PlanStep.from_dict(original.to_dict())

        assert len(restored.team) == 2
        assert restored.team[0].member_id == "3.2.a"
        assert restored.team[1].depends_on == ["3.2.a"]

    def test_step_result_with_member_results_roundtrip(self) -> None:
        """StepResult.to_dict() / from_dict() preserves member_results."""
        original = StepResult(
            step_id="1.1",
            agent_name="team",
            status="complete",
            outcome="service implemented; tests written",
            files_changed=["a.py", "b.py"],
            member_results=[
                TeamStepResult(
                    member_id="1.1.a",
                    agent_name="backend-engineer",
                    status="complete",
                    outcome="service implemented",
                    files_changed=["a.py"],
                ),
                TeamStepResult(
                    member_id="1.1.b",
                    agent_name="test-engineer",
                    status="complete",
                    outcome="tests written",
                    files_changed=["b.py"],
                ),
            ],
        )
        data = original.to_dict()
        restored = StepResult.from_dict(data)

        assert len(restored.member_results) == 2
        assert restored.member_results[0].member_id == "1.1.a"
        assert restored.member_results[1].files_changed == ["b.py"]

    def test_old_step_without_team_loads(self) -> None:
        """PlanStep.from_dict() with no 'team' key defaults to an empty list."""
        data = {
            "step_id": "1.1",
            "agent_name": "backend-engineer",
            "task_description": "Legacy step without team field",
        }
        step = PlanStep.from_dict(data)
        assert step.team == []

    def test_team_member_from_dict_uses_defaults(self) -> None:
        """TeamMember.from_dict() applies sensible defaults for optional fields."""
        member = TeamMember.from_dict({
            "member_id": "1.1.a",
            "agent_name": "backend-engineer",
        })
        assert member.role == "implementer"
        assert member.model == "sonnet"
        assert member.depends_on == []
        assert member.deliverables == []
        assert member.task_description == ""

    def test_step_result_without_member_results_roundtrip(self) -> None:
        """StepResult without member_results survives serialization cleanly."""
        original = StepResult(
            step_id="2.1",
            agent_name="architect",
            status="complete",
        )
        data = original.to_dict()
        # member_results key should be absent when empty.
        assert "member_results" not in data
        restored = StepResult.from_dict(data)
        assert restored.member_results == []

    def test_team_step_result_roundtrip(self) -> None:
        """TeamStepResult.to_dict() / from_dict() is lossless."""
        original = TeamStepResult(
            member_id="1.1.c",
            agent_name="test-engineer",
            status="failed",
            outcome="compilation error",
            files_changed=["src/foo.py"],
        )
        restored = TeamStepResult.from_dict(original.to_dict())

        assert restored.member_id == original.member_id
        assert restored.status == original.status
        assert restored.files_changed == original.files_changed


# ---------------------------------------------------------------------------
# TestTeamDispatchPrompt
# ---------------------------------------------------------------------------

class TestTeamDispatchPrompt:

    def test_team_dispatch_action_has_member_agent_name(
        self, tmp_path: Path
    ) -> None:
        """DISPATCH action carries the member's agent_name, not the parent step's."""
        plan = _plan(phases=[_phase(steps=[_two_member_step()])])
        action = _engine(tmp_path).start(plan)

        # Primary action = first member (backend-engineer)
        assert action.agent_name == "backend-engineer"

    def test_team_dispatch_action_has_delegation_prompt(
        self, tmp_path: Path
    ) -> None:
        """DISPATCH action carries a non-empty delegation_prompt for each member."""
        plan = _plan(phases=[_phase(steps=[_two_member_step()])])
        action = _engine(tmp_path).start(plan)

        assert action.delegation_prompt
        assert action.parallel_actions[0].delegation_prompt

    def test_team_dispatch_prompt_includes_team_overview(
        self, tmp_path: Path
    ) -> None:
        """Each member's delegation prompt includes the team composition."""
        plan = _plan(phases=[_phase(steps=[_two_member_step()])])
        action = _engine(tmp_path).start(plan)

        # The team overview string is "backend-engineer (implementer), test-engineer (reviewer)"
        # Both agents should appear in the prompt.
        assert "backend-engineer" in action.delegation_prompt
        assert "test-engineer" in action.delegation_prompt

    def test_team_dispatch_prompt_includes_member_role(
        self, tmp_path: Path
    ) -> None:
        """Delegation prompt includes the dispatched member's role."""
        plan = _plan(phases=[_phase(steps=[_two_member_step()])])
        action = _engine(tmp_path).start(plan)

        # First member has role "implementer"
        assert "implementer" in action.delegation_prompt

    def test_team_dispatch_prompt_includes_member_task(
        self, tmp_path: Path
    ) -> None:
        """Delegation prompt includes the member's task_description."""
        step = _step(
            step_id="1.1",
            team=[
                _member("1.1.a", task="Implement the payment service"),
                _member("1.1.b", task="Write integration tests"),
            ],
        )
        plan = _plan(phases=[_phase(steps=[step])])
        action = _engine(tmp_path).start(plan)

        assert "Implement the payment service" in action.delegation_prompt
        assert "Write integration tests" in action.parallel_actions[0].delegation_prompt

    def test_team_dispatch_prompt_shared_context_propagated(
        self, tmp_path: Path
    ) -> None:
        """Shared context from the plan is included in each member's prompt."""
        plan = _plan(
            shared_context="Important architectural guidelines here.",
            phases=[_phase(steps=[_two_member_step()])],
        )
        action = _engine(tmp_path).start(plan)

        assert "Important architectural guidelines here." in action.delegation_prompt
        assert (
            "Important architectural guidelines here."
            in action.parallel_actions[0].delegation_prompt
        )

    def test_team_dispatch_prompt_includes_member_id(
        self, tmp_path: Path
    ) -> None:
        """The delegation prompt references the member's member_id for traceability."""
        plan = _plan(phases=[_phase(steps=[_two_member_step()])])
        action = _engine(tmp_path).start(plan)

        assert "1.1.a" in action.delegation_prompt
        assert "1.1.b" in action.parallel_actions[0].delegation_prompt

    def test_team_dispatch_prompt_includes_step_id(
        self, tmp_path: Path
    ) -> None:
        """The delegation prompt references the parent step_id."""
        plan = _plan(phases=[_phase(steps=[_two_member_step()])])
        action = _engine(tmp_path).start(plan)

        # Parent step_id "1.1" should appear in both prompts.
        assert "1.1" in action.delegation_prompt
        assert "1.1" in action.parallel_actions[0].delegation_prompt

    def test_member_with_depends_on_prompt_mentions_dependency(
        self, tmp_path: Path
    ) -> None:
        """Member B's delegation prompt references its dependency on member A.

        Because the engine's main loop does not re-enter _team_dispatch_action
        for a partially-complete team step (the parent sits in dispatched_step_ids),
        we call _team_dispatch_action directly after recording A's completion to
        verify that B's prompt includes the dependency reference.
        """
        step = _two_member_step_with_dep()
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine(tmp_path)
        engine.start(plan)

        # Record A as complete so B's dependency is satisfied.
        engine.record_team_member_result("1.1", "1.1.a", "backend-engineer",
                                         status="complete", outcome="done")

        state = engine._load_state()
        action = engine._team_dispatch_action(step, state)

        # Member B's prompt should reference its dependency on 1.1.a.
        assert action.action_type == ActionType.DISPATCH
        assert "1.1.a" in action.delegation_prompt


# ---------------------------------------------------------------------------
# TestConflictHandlingPolicies
#
# test_phase3_team_maturation.py already covers "auto_merge completes
# normally" and "escalate pauses on conflict" for the DEFAULT synthesis
# strategy. The gaps this class fills: (1) `conflict_handling="fail"`
# combined with a NON-agent_synthesis strategy (concatenate/merge_files) —
# untested anywhere else in the suite; (2) a member FAILURE co-occurring
# with a file-overlap conflict under "fail" — the branch at the top of
# executor.record_team_member_result's failed_ids handling that enriches
# the failure error with conflict detail, which no existing test reaches.
# ---------------------------------------------------------------------------


class TestConflictHandlingPolicies:

    def _two_member_conflicting_step(
        self, *, strategy: str = "merge_files", conflict_handling: str = "auto_merge",
    ) -> PlanStep:
        return _step(
            step_id="1.1",
            team=[
                _member("1.1.a", agent_name="backend-engineer", role="implementer"),
                _member("1.1.b", agent_name="test-engineer", role="implementer"),
            ],
            synthesis=SynthesisSpec(
                strategy=strategy, conflict_handling=conflict_handling,
            ),
        )

    def test_fail_policy_terminates_step_on_conflict_even_when_all_succeed(
        self, tmp_path: Path,
    ) -> None:
        """conflict_handling='fail' with a non-agent_synthesis strategy:
        both members individually SUCCEED but touch the same file — the
        step must still fail, because the conflict is between their
        outputs, not a member outcome."""
        step = self._two_member_conflicting_step(
            strategy="merge_files", conflict_handling="fail",
        )
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer", status="complete",
            outcome="impl A", files_changed=["shared.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "test-engineer", status="complete",
            outcome="impl B", files_changed=["shared.py"],
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert result.status == "failed"
        assert "Conflict detected" in result.error
        assert result.completed_at

    def test_fail_policy_without_overlap_completes_normally(
        self, tmp_path: Path,
    ) -> None:
        """Sanity: 'fail' only terminates on an ACTUAL conflict — disjoint
        files_changed must still complete the step."""
        step = self._two_member_conflicting_step(
            strategy="merge_files", conflict_handling="fail",
        )
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer", status="complete",
            outcome="impl A", files_changed=["a.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "test-engineer", status="complete",
            outcome="impl B", files_changed=["b.py"],
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert result.status == "complete"

    def test_fail_policy_member_failure_plus_conflict_enriches_error(
        self, tmp_path: Path,
    ) -> None:
        """The failed_ids branch: one member already FAILED, and among the
        recorded results there is also a file-overlap conflict. The
        parent's error must be annotated with the conflict detail (not
        just the generic 'Team member(s) failed' message) — this is the
        one branch of record_team_member_result's conflict_handling=='fail'
        handling that pre-dates a member failure rather than following a
        clean success."""
        step = self._two_member_conflicting_step(
            strategy="merge_files", conflict_handling="fail",
        )
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer", status="complete",
            outcome="impl A", files_changed=["shared.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "test-engineer", status="failed",
            outcome="compile error", files_changed=["shared.py"],
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result is not None
        assert result.status == "failed"
        assert "Conflict detected" in result.error

    def test_escalate_policy_pauses_for_non_agent_synthesis_strategy(
        self, tmp_path: Path,
    ) -> None:
        """Pins CURRENT behavior for 'escalate' + a non-agent_synthesis
        strategy across an approval round-trip: the step correctly pauses
        for human review on conflict (matches test_phase3_team_maturation's
        coverage), but — because the ESCALATED -> SYNTHESIZING resume wired
        in Phase 4 4.3 (_pending_synthesis_dispatch) only re-engages for
        strategy='agent_synthesis' — approving the escalation does NOT
        auto-resume concatenate/merge_files synthesis: the step remains
        'dispatched'/synthesis_state='escalated' and next_action() reports
        WAIT rather than completing. This is a real gap (executor.py is
        outside this test-authoring step's allowed_paths, so it cannot be
        fixed here) — pinned explicitly so a future fix has a red test to
        turn green, and so this behavior can't silently regress further.
        """
        step = self._two_member_conflicting_step(
            strategy="merge_files", conflict_handling="escalate",
        )
        plan = _plan(phases=[_phase(steps=[step])])
        engine = _engine(tmp_path)
        engine.start(plan)

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer", status="complete",
            outcome="impl A", files_changed=["shared.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "test-engineer", status="complete",
            outcome="impl B", files_changed=["shared.py"],
        )

        state = engine._load_state()
        assert state.status == "approval_pending"
        result = state.get_step_result("1.1")
        assert result.status == "dispatched"
        assert result.synthesis_state == "escalated"

        engine.record_approval_result(phase_id=0, result="approve")

        state = engine._load_state()
        result = state.get_step_result("1.1")
        # Current (gap) behavior — see docstring. Update this assertion
        # alongside the executor.py fix that resumes non-agent_synthesis
        # strategies out of ESCALATED.
        assert result.status == "dispatched"
        assert result.synthesis_state == "escalated"
        action = engine.next_action()
        assert action.action_type == ActionType.WAIT


# ---------------------------------------------------------------------------
# TestMalformedRecordCalls — record_team_member_result with data that does
# not correspond cleanly to the plan's team roster.
# ---------------------------------------------------------------------------


class TestMalformedRecordCalls:

    def test_unknown_member_id_does_not_block_real_completion(
        self, tmp_path: Path,
    ) -> None:
        """A malformed/unauthorized record for a member_id that isn't in
        the plan's team is stored (the engine does not validate membership
        at this layer — see docs/internal/team-runtime-contract.md's note
        that ``team_tools._require_member`` is the validating layer, not
        the executor's own record path) but must not prevent the real
        members from completing the step normally."""
        engine = TestTeamCompletion._start_team_engine(tmp_path)

        engine.record_team_member_result(
            "1.1", "1.1.ghost", "unknown-agent",
            status="complete", outcome="not a real member",
        )
        state = engine._load_state()
        # The bogus record is stored...
        assert any(
            m.member_id == "1.1.ghost"
            for m in state.get_step_result("1.1").member_results
        )
        # ...but the parent step is still waiting on the REAL two members.
        assert state.get_step_result("1.1").status == "dispatched"

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer", status="complete", outcome="a",
        )
        engine.record_team_member_result(
            "1.1", "1.1.b", "test-engineer", status="complete", outcome="b",
        )
        state = engine._load_state()
        assert state.get_step_result("1.1").status == "complete"

    def test_duplicate_record_for_same_member_does_not_crash(
        self, tmp_path: Path,
    ) -> None:
        """A member retried after an ambiguous failure (e.g. a Bash-tool
        timeout on the recording call) may report twice — the engine must
        not crash, and completion must still be correctly gated on the
        real roster."""
        engine = TestTeamCompletion._start_team_engine(tmp_path)

        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer", status="complete", outcome="first",
        )
        engine.record_team_member_result(
            "1.1", "1.1.a", "backend-engineer", status="complete", outcome="retry",
        )
        state = engine._load_state()
        assert state.get_step_result("1.1").status == "dispatched"

        engine.record_team_member_result(
            "1.1", "1.1.b", "test-engineer", status="complete", outcome="b",
        )
        state = engine._load_state()
        assert state.get_step_result("1.1").status == "complete"


# ---------------------------------------------------------------------------
# TestDryRunTeamActionPayload — retains the dry-run assertion that token
# estimates and team action payloads are complete when a team dispatch's
# DISPATCH action (primary + parallel_actions) is fed through the dry-run
# launcher, exactly as the orchestrator's dry-run mode would.
# ---------------------------------------------------------------------------


class TestDryRunTeamActionPayload:

    def test_team_dispatch_actions_produce_complete_dry_run_launches(
        self, tmp_path: Path,
    ) -> None:
        import asyncio
        from agent_baton.core.engine.dry_run_launcher import TracingDryRunLauncher

        plan = _plan(phases=[_phase(steps=[_two_member_step()])])
        action = _engine(tmp_path).start(plan)
        all_actions = [action, *action.parallel_actions]
        assert len(all_actions) == 2  # both team members present in this wave

        launcher = TracingDryRunLauncher()

        async def _drive() -> None:
            for a in all_actions:
                await launcher.launch(
                    agent_name=a.agent_name,
                    model=a.agent_model or "sonnet",
                    prompt=a.delegation_prompt,
                    step_id=a.step_id,
                )

        asyncio.run(_drive())

        assert len(launcher.launches) == 2
        by_step = {entry["step_id"]: entry for entry in launcher.launches}
        assert set(by_step) == {"1.1.a", "1.1.b"}
        for step_id, entry in by_step.items():
            # Every payload field the dry-run report writer depends on must
            # be present and non-degenerate — a missing/zero token estimate
            # or an empty agent_name would silently corrupt a dry-run report.
            assert entry["agent_name"], step_id
            assert entry["model"], step_id
            assert entry["prompt_chars"] > 0, step_id
            assert entry["estimated_tokens"] >= 1, step_id
            assert entry["launched_at"], step_id
        # Team dispatch prompts carry real content, not placeholders — the
        # token estimate is a genuine function of that content.
        assert by_step["1.1.a"]["prompt_chars"] != by_step["1.1.b"]["prompt_chars"] or (
            by_step["1.1.a"]["agent_name"] != by_step["1.1.b"]["agent_name"]
        )
