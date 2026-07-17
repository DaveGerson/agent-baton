"""Tests for the persisted agent_synthesis dispatch (Phase 4, 4.3).

Covers:
- A team step's agent_synthesis strategy dispatches a real synthesis agent
  exactly once (persisted StepResult.synthesis_dispatched guards against a
  second dispatch on a repeated next_action()/next_actions() poll).
- Restart safety: reloading engine state mid-synthesis does not lose
  member_results or re-dispatch.
- The synthesis agent's own result is recorded via the ordinary
  record_step_result() call against the SAME step_id as the parent team
  step, so it flows through the identical pipeline non-team steps use, and
  completes the parent step with SynthesisState.SYNTHESIZED.
- conflict_handling applied exactly for auto_merge (default -- synthesis
  still dispatches so the agent can reconcile), escalate (pauses for
  approval, then resumes into a synthesis dispatch), and fail (terminates
  the step without ever dispatching a synthesis agent).
- concatenate/merge_files remain synchronous and unaffected (backward
  compatible; also covered by tests/test_phase3_team_maturation.py).
"""
from __future__ import annotations

from pathlib import Path

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanPhase,
    PlanStep,
    SynthesisSpec,
    SynthesisState,
    TeamMember,
)


def _make_engine(tmp_path: Path) -> ExecutionEngine:
    root = tmp_path / ".claude" / "team-context"
    root.mkdir(parents=True, exist_ok=True)
    return ExecutionEngine(team_context_root=root)


def _team_plan(synthesis: SynthesisSpec, task_id: str = "test-synth-dispatch") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Test synthesis dispatch",
        task_type="new-feature",
        risk_level="LOW",
        git_strategy="none",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="team",
                        task_description="Team work",
                        team=[
                            TeamMember(
                                member_id="1.1.a",
                                agent_name="backend-engineer",
                                role="implementer",
                                task_description="Backend",
                            ),
                            TeamMember(
                                member_id="1.1.b",
                                agent_name="frontend-engineer",
                                role="implementer",
                                task_description="Frontend",
                            ),
                        ],
                        synthesis=synthesis,
                    ),
                ],
            ),
        ],
    )


def _record_both_members(
    engine: ExecutionEngine,
    files_a: list[str] | None = None,
    files_b: list[str] | None = None,
) -> None:
    engine.record_team_member_result(
        "1.1", "1.1.a", "backend-engineer",
        status="complete", outcome="Backend impl",
        files_changed=files_a or ["backend.py"],
    )
    engine.record_team_member_result(
        "1.1", "1.1.b", "frontend-engineer",
        status="complete", outcome="Frontend impl",
        files_changed=files_b or ["frontend.tsx"],
    )


class TestSynthesisDispatchOnce:

    def test_dispatch_names_synthesis_agent_and_step_id(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(SynthesisSpec(
            strategy="agent_synthesis", synthesis_agent="architect",
        )))
        _record_both_members(engine)

        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        assert action.agent_name == "architect"
        # Same step_id as the parent team step -- NOT a synthetic id, so the
        # eventual `baton execute record --step 1.1 ...` call routes through
        # the ordinary (non-team-member) record_step_result CLI path.
        assert action.step_id == "1.1"
        assert "Backend impl" in action.delegation_prompt
        assert "Frontend impl" in action.delegation_prompt
        assert "1.1.a" in action.delegation_prompt
        assert "1.1.b" in action.delegation_prompt

    def test_default_synthesis_agent_is_code_reviewer(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(SynthesisSpec(strategy="agent_synthesis")))
        _record_both_members(engine)

        action = engine.next_action()
        assert action.agent_name == "code-reviewer"

    def test_repeated_next_action_does_not_redispatch(self, tmp_path: Path) -> None:
        """StepResult.synthesis_dispatched prevents a duplicate dispatch."""
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(SynthesisSpec(strategy="agent_synthesis")))
        _record_both_members(engine)

        first = engine.next_action()
        assert first.action_type == ActionType.DISPATCH

        # Simulate the orchestrator marking the step dispatched (as the
        # worker/CLI would do before the synthesis agent actually runs).
        engine.mark_dispatched(first.step_id, first.agent_name)

        second = engine.next_action()
        assert second.action_type == ActionType.WAIT

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.synthesis_dispatched is True
        assert result.synthesis_state == SynthesisState.SYNTHESIZING.value
        # member_results must survive mark_dispatched's carry-forward.
        assert len(result.member_results) == 2

    def test_next_actions_parallel_path_dispatches_once(self, tmp_path: Path) -> None:
        """The daemon's next_actions() batch path is also exactly-once."""
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(SynthesisSpec(strategy="agent_synthesis")))
        _record_both_members(engine)

        actions = engine.next_actions()
        assert len(actions) == 1
        assert actions[0].action_type == ActionType.DISPATCH
        assert actions[0].step_id == "1.1"

        engine.mark_dispatched(actions[0].step_id, actions[0].agent_name)

        again = engine.next_actions()
        assert again == []


class TestSynthesisRestartSafety:

    def test_reload_after_dispatch_preserves_state_and_no_redispatch(
        self, tmp_path: Path
    ) -> None:
        engine = _make_engine(tmp_path)
        plan = _team_plan(SynthesisSpec(strategy="agent_synthesis"), task_id="restart-safety")
        engine.start(plan)
        _record_both_members(engine)

        action = engine.next_action()
        engine.mark_dispatched(action.step_id, action.agent_name)

        # Fresh engine instance against the same persisted store -- mirrors
        # a process restart / crash-resume.  No explicit task_id: matches
        # engine1's construction (_make_engine never passes one either), so
        # both resolve the same flat execution-state.json / active-task
        # pointer rather than the namespaced per-task path a freshly
        # task_id-scoped instance would look under.
        engine2 = ExecutionEngine(
            team_context_root=tmp_path / ".claude" / "team-context",
        )
        state = engine2._load_state()
        result = state.get_step_result("1.1")
        assert result.synthesis_dispatched is True
        assert result.synthesis_state == SynthesisState.SYNTHESIZING.value
        assert len(result.member_results) == 2

        # A poll after restart must not re-dispatch.
        resumed_action = engine2.next_action()
        assert resumed_action.action_type == ActionType.WAIT


class TestSynthesisCompletion:

    def test_synthesis_result_completes_parent_step(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(SynthesisSpec(strategy="agent_synthesis")))
        _record_both_members(engine, files_a=["backend.py"], files_b=["frontend.tsx"])

        action = engine.next_action()
        engine.mark_dispatched(action.step_id, action.agent_name)

        # The synthesis agent's own result is recorded via the ordinary
        # record_step_result() call against the SAME step_id.
        engine.record_step_result(
            step_id="1.1",
            agent_name=action.agent_name,
            status="complete",
            outcome="Merged backend and frontend work into one consistent change.",
            files_changed=["backend.py", "frontend.tsx", "glue.py"],
            commit_hash="abc123",
        )

        result = engine._load_state().get_step_result("1.1")
        assert result.status == "complete"
        assert result.synthesis_state == SynthesisState.SYNTHESIZED.value
        assert result.commit_hash == "abc123"
        assert result.files_changed == ["backend.py", "frontend.tsx", "glue.py"]
        assert "Merged backend" in result.outcome
        # member provenance survives through to the final result.
        assert len(result.member_results) == 2
        assert {m.member_id for m in result.member_results} == {"1.1.a", "1.1.b"}

        # Engine considers the phase/task done.
        final_action = engine.next_action()
        assert final_action.action_type == ActionType.COMPLETE

    def test_synthesis_agent_failure_fails_parent_step(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(SynthesisSpec(strategy="agent_synthesis")))
        _record_both_members(engine)

        action = engine.next_action()
        engine.mark_dispatched(action.step_id, action.agent_name)
        engine.record_step_result(
            step_id="1.1",
            agent_name=action.agent_name,
            status="failed",
            error="synthesis agent could not reconcile the changes",
        )

        result = engine._load_state().get_step_result("1.1")
        assert result.status == "failed"
        assert result.synthesis_state == SynthesisState.FAILED.value


class TestConflictHandlingPolicy:

    def test_auto_merge_still_dispatches_synthesis_on_conflict(
        self, tmp_path: Path
    ) -> None:
        """Default auto_merge lets the synthesis agent reconcile conflicts."""
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(SynthesisSpec(
            strategy="agent_synthesis", conflict_handling="auto_merge",
        )))
        _record_both_members(engine, files_a=["shared.py"], files_b=["shared.py"])

        action = engine.next_action()
        assert action.action_type == ActionType.DISPATCH
        # The conflict is surfaced to the synthesis agent's prompt.
        assert "Conflicts detected" in action.delegation_prompt
        assert "conflict_id=" in action.delegation_prompt

    def test_fail_on_conflict_terminates_without_dispatch(
        self, tmp_path: Path
    ) -> None:
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(SynthesisSpec(
            strategy="agent_synthesis", conflict_handling="fail",
        )))
        _record_both_members(engine, files_a=["shared.py"], files_b=["shared.py"])

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "failed"
        assert result.synthesis_state == SynthesisState.FAILED.value

        # No synthesis agent is ever dispatched for a failed step.
        final_action = engine.next_action()
        assert final_action.action_type == ActionType.FAILED

    def test_escalate_on_conflict_then_resume_dispatches_synthesis(
        self, tmp_path: Path
    ) -> None:
        engine = _make_engine(tmp_path)
        engine.start(_team_plan(SynthesisSpec(
            strategy="agent_synthesis", conflict_handling="escalate",
        )))
        _record_both_members(engine, files_a=["shared.py"], files_b=["shared.py"])

        state = engine._load_state()
        assert state.status == "approval_pending"
        result = state.get_step_result("1.1")
        assert result.status == "dispatched"
        assert result.synthesis_state == SynthesisState.ESCALATED.value

        # While escalated, no synthesis dispatch should be offered.
        waiting_action = engine.next_action()
        assert waiting_action.action_type == ActionType.APPROVAL

        engine.record_approval_result(phase_id=1, result="approve")

        resumed_action = engine.next_action()
        assert resumed_action.action_type == ActionType.DISPATCH
        assert resumed_action.step_id == "1.1"

        result_after = engine._load_state().get_step_result("1.1")
        assert result_after.synthesis_state == SynthesisState.SYNTHESIZING.value
        assert result_after.synthesis_dispatched is True
