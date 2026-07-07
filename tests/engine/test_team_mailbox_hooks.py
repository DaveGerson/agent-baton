"""Tests for the TEAM_DISPATCH ↔ TeamMailbox bridge (A2.b).

Pin the events the executor emits when a team is dispatched and when
individual members complete or fail. These are the inputs the future
A1 Claude-Teams backend will consume via its hook bridge.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.mailbox import TeamMailbox
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.models.execution import (
    MachinePlan,
    PlanPhase,
    PlanStep,
    TeamMember,
)


def _team_step() -> PlanStep:
    return PlanStep(
        step_id="1.1",
        agent_name="team",
        task_description="implement and review",
        model="sonnet",
        team=[
            TeamMember(
                member_id="1.1.a", agent_name="backend-engineer",
                role="implementer", task_description="write the service",
                model="sonnet",
            ),
            TeamMember(
                member_id="1.1.b", agent_name="code-reviewer",
                role="reviewer", task_description="review for security",
                model="sonnet",
            ),
        ],
    )


def _team_plan() -> MachinePlan:
    return MachinePlan(
        task_id="t-mb",
        task_summary="team mailbox test",
        phases=[PlanPhase(phase_id=1, name="Build", steps=[_team_step()])],
    )


def _solo_then_team_plan() -> MachinePlan:
    return MachinePlan(
        task_id="t-next-actions-team",
        task_summary="unlock team from next_actions",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Build",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="setup-agent",
                        task_description="prepare inputs",
                    ),
                    PlanStep(
                        step_id="1.2",
                        agent_name="team",
                        task_description="implement and review",
                        depends_on=["1.1"],
                        model="sonnet",
                        team=[
                            TeamMember(
                                member_id="1.2.a",
                                agent_name="backend-engineer",
                                role="implementer",
                                task_description="write the service",
                                model="sonnet",
                            ),
                            TeamMember(
                                member_id="1.2.b",
                                agent_name="code-reviewer",
                                role="reviewer",
                                task_description="review for security",
                                model="sonnet",
                            ),
                        ],
                    ),
                ],
            ),
        ],
    )


def _started(tmp_path: Path) -> tuple[ExecutionEngine, MachinePlan]:
    engine = ExecutionEngine(team_context_root=tmp_path)
    plan = _team_plan()
    engine.start(plan)
    return engine, plan


def _mailbox_for(tmp_path: Path) -> TeamMailbox:
    return TeamMailbox(tmp_path, "team-1.1")


class TestMailboxOnDispatch:
    def test_task_created_emitted_for_each_member(self, tmp_path: Path) -> None:
        engine, _ = _started(tmp_path)
        # Trigger team dispatch via the public next_action API.
        engine.next_action()
        events = _mailbox_for(tmp_path).read_all()
        types = [e.event_type for e in events]
        created = [e for e in events if e.event_type == "task_created"]
        assert len(created) == 2, types
        assert {e.to_member for e in created} == {"1.1.a", "1.1.b"}

    def test_task_created_carries_payload(self, tmp_path: Path) -> None:
        engine, _ = _started(tmp_path)
        engine.next_action()
        events = _mailbox_for(tmp_path).read_all()
        created = next(e for e in events if e.to_member == "1.1.a")
        assert created.payload["agent_name"] == "backend-engineer"
        assert created.payload["role"] == "implementer"
        assert created.payload["step_id"] == "1.1"

    def test_team_report_written_with_readiness_diagnostics(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("BATON_TEAMS_BACKEND", raising=False)
        monkeypatch.delenv("BATON_TEAMS_BACKEND_STRICT", raising=False)

        engine = ExecutionEngine(team_context_root=tmp_path)
        action = engine.start(_team_plan())

        report = tmp_path / "teams" / "team-1.1" / "team-report.json"
        assert report.exists()
        payload = json.loads(report.read_text(encoding="utf-8"))
        assert payload["backend"] == "worktree"
        assert payload["member_count"] == 2
        assert payload["nested_team_count"] == 0
        assert payload["synthesis_strategy"] == "concatenate"
        assert payload["conflict_strategy"] == "auto_merge"
        assert payload["warning_count"] == 0
        assert "Team readiness: backend=worktree" in action.message

        state = StatePersistence(tmp_path).load()
        assert state is not None
        assert (
            state.plan.plan_diagnostics["team_readiness"]["1.1"]["backend"]
            == "worktree"
        )

    def test_strict_unknown_backend_blocks_team_dispatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BATON_TEAMS_BACKEND", "not-real")
        monkeypatch.setenv("BATON_TEAMS_BACKEND_STRICT", "1")
        engine = ExecutionEngine(team_context_root=tmp_path)

        with pytest.raises(ValueError, match="Unknown BATON_TEAMS_BACKEND"):
            engine.start(_team_plan())

    def test_next_actions_persists_team_readiness_for_unlocked_team(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("BATON_TEAMS_BACKEND", raising=False)
        monkeypatch.delenv("BATON_TEAMS_BACKEND_STRICT", raising=False)
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_solo_then_team_plan())
        engine.record_step_result(
            step_id="1.1",
            agent_name="setup-agent",
            status="complete",
            outcome="inputs ready",
        )

        actions = engine.next_actions()

        assert actions
        state = StatePersistence(tmp_path).load()
        assert state is not None
        assert (
            state.plan.plan_diagnostics["team_readiness"]["1.2"]["backend"]
            == "worktree"
        )

    def test_resume_first_entry_persists_team_readiness(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """C1 regression: when resume() is the FIRST walk into a team step,
        the team_readiness diagnostic mutated by _team_dispatch_action must be
        persisted. resume() previously returned without saving, so the
        once-per-step diagnostic (gated on the parent StepResult being absent)
        was lost after a crash/resume boundary and never recomputed once
        team-record created the parent StepResult.
        """
        monkeypatch.delenv("BATON_TEAMS_BACKEND", raising=False)
        monkeypatch.delenv("BATON_TEAMS_BACKEND_STRICT", raising=False)
        engine = ExecutionEngine(team_context_root=tmp_path)
        engine.start(_solo_then_team_plan())
        engine.record_step_result(
            step_id="1.1",
            agent_name="setup-agent",
            status="complete",
            outcome="inputs ready",
        )

        # Persisted state now has no team_readiness for the still-locked 1.2.
        pre = StatePersistence(tmp_path).load()
        assert pre is not None
        assert "1.2" not in pre.plan.plan_diagnostics.get("team_readiness", {})

        # Simulate crash/resume: reconstruct a fresh engine and let resume()
        # perform the first walk into the team step.
        resumed = ExecutionEngine(team_context_root=tmp_path)
        action = resumed.resume()
        assert action is not None

        state = StatePersistence(tmp_path).load()
        assert state is not None
        assert (
            state.plan.plan_diagnostics["team_readiness"]["1.2"]["backend"]
            == "worktree"
        )


class TestMailboxOnMemberResult:
    def test_task_completed_event_on_success(self, tmp_path: Path) -> None:
        engine, _ = _started(tmp_path)
        engine.next_action()  # dispatches both members
        engine.record_team_member_result(
            step_id="1.1", member_id="1.1.a",
            agent_name="backend-engineer", status="complete",
            outcome="service implemented",
        )
        events = _mailbox_for(tmp_path).read_all()
        completed = [e for e in events if e.event_type == "task_completed"]
        assert len(completed) == 1
        assert completed[0].from_member == "1.1.a"
        assert "service implemented" in completed[0].body

    def test_task_failed_event_on_failure(self, tmp_path: Path) -> None:
        engine, _ = _started(tmp_path)
        engine.next_action()
        engine.record_team_member_result(
            step_id="1.1", member_id="1.1.a",
            agent_name="backend-engineer", status="failed",
            outcome="errored out",
        )
        events = _mailbox_for(tmp_path).read_all()
        failed = [e for e in events if e.event_type == "task_failed"]
        assert len(failed) == 1
        assert failed[0].from_member == "1.1.a"

    def test_teammate_idle_when_team_completes(self, tmp_path: Path) -> None:
        engine, _ = _started(tmp_path)
        engine.next_action()
        engine.record_team_member_result(
            step_id="1.1", member_id="1.1.a",
            agent_name="backend-engineer", status="complete",
            outcome="impl done",
        )
        engine.record_team_member_result(
            step_id="1.1", member_id="1.1.b",
            agent_name="code-reviewer", status="complete",
            outcome="LGTM",
        )
        events = _mailbox_for(tmp_path).read_all()
        idle = [e for e in events if e.event_type == "teammate_idle"]
        # One idle per member when the parent step finalises.
        assert len(idle) == 2
        assert {e.from_member for e in idle} == {"1.1.a", "1.1.b"}


class TestPlanApprovalFlow:
    """A2.c — lead-gated plan-approval via the team mailbox."""

    def test_request_then_approve(self, tmp_path: Path) -> None:
        engine, _ = _started(tmp_path)
        engine.request_team_member_plan_approval(
            step_id="1.1", member_id="1.1.a",
            plan_text="1) write the service\n2) wire the gate",
            agent_name="backend-engineer",
        )
        engine.decide_team_member_plan_approval(
            step_id="1.1", member_id="1.1.a",
            approved=True, feedback="proceed",
        )
        events = _mailbox_for(tmp_path).read_all()
        approval_events = [
            e for e in events if e.event_type.startswith("plan_approval_")
        ]
        kinds = [e.event_type for e in approval_events]
        assert kinds == ["plan_approval_requested", "plan_approval_decided"]
        decided = approval_events[1]
        assert decided.payload["approved"] is True
        assert decided.to_member == "1.1.a"

    def test_reject_requires_feedback(self, tmp_path: Path) -> None:
        engine, _ = _started(tmp_path)
        engine.request_team_member_plan_approval(
            step_id="1.1", member_id="1.1.a",
            plan_text="placeholder",
        )
        with pytest.raises(ValueError):
            engine.decide_team_member_plan_approval(
                step_id="1.1", member_id="1.1.a",
                approved=False, feedback="",
            )

    def test_reject_with_feedback_emits_decision(self, tmp_path: Path) -> None:
        engine, _ = _started(tmp_path)
        engine.request_team_member_plan_approval(
            step_id="1.1", member_id="1.1.a",
            plan_text="too risky as drafted",
        )
        engine.decide_team_member_plan_approval(
            step_id="1.1", member_id="1.1.a",
            approved=False, feedback="include rollback strategy",
        )
        events = _mailbox_for(tmp_path).read_all()
        decided = [e for e in events if e.event_type == "plan_approval_decided"]
        assert len(decided) == 1
        assert decided[0].payload["approved"] is False
        assert "rollback" in decided[0].body
