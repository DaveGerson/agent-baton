"""Tests for the agent-facing team tools in ``team_tools.py``.

The tools are Python-callable backings for the ``team_*`` agent tools
documented in ``references/team-messaging.md``.  Tests exercise
validation, the role-enforced ``team_dispatch`` tool, and end-to-end
flows over the :class:`TeamRegistry` + :class:`TeamBoard` stack.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.team_tools import (
    TeamToolError,
    team_add_task,
    team_claim_task,
    team_complete_task,
    team_dispatch,
    team_send_message,
)
from agent_baton.models.execution import (
    MachinePlan, PlanPhase, PlanStep, SynthesisSpec, TeamMember,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _engine_with_storage(tmp_path: Path) -> ExecutionEngine:
    from agent_baton.core.storage.sqlite_backend import SqliteStorage
    storage = SqliteStorage(tmp_path / "baton.db")
    return ExecutionEngine(team_context_root=tmp_path, storage=storage)


def _two_team_plan() -> MachinePlan:
    """Plan with two parallel team steps, each with a lead + implementer."""
    return MachinePlan(
        task_id="task-tools",
        task_summary="parallel teams",
        phases=[PlanPhase(
            phase_id=1, name="impl",
            steps=[
                PlanStep(
                    step_id="1.1", agent_name="team",
                    task_description="team a",
                    team=[
                        TeamMember(member_id="1.1.a", agent_name="architect",
                                   role="lead"),
                        TeamMember(member_id="1.1.b", agent_name="be",
                                   role="implementer"),
                    ],
                ),
                PlanStep(
                    step_id="1.2", agent_name="team",
                    task_description="team b",
                    team=[
                        TeamMember(member_id="1.2.a", agent_name="architect",
                                   role="lead"),
                        TeamMember(member_id="1.2.b", agent_name="te",
                                   role="implementer"),
                    ],
                ),
            ],
        )],
    )


@pytest.fixture
def engine(tmp_path: Path) -> ExecutionEngine:
    eng = _engine_with_storage(tmp_path)
    eng.start(_two_team_plan())
    # next_actions() returns every dispatchable team step and registers
    # their teams in the registry.  Both teams are now discoverable.
    eng.next_actions()
    return eng


# ---------------------------------------------------------------------------
# Messaging + task tools
# ---------------------------------------------------------------------------


class TestTeamSendMessage:
    def test_cross_team_message(self, engine: ExecutionEngine) -> None:
        bead_id = team_send_message(
            engine,
            task_id="task-tools",
            from_team="team-1.1", from_member="1.1.a",
            to_team="team-1.2", to_member="1.2.a",
            subject="heads up", body="schema change",
        )
        assert bead_id

    def test_missing_recipient_team_raises(
        self, engine: ExecutionEngine
    ) -> None:
        with pytest.raises(TeamToolError, match="Team 'team-missing'"):
            team_send_message(
                engine, task_id="task-tools",
                from_team="team-1.1", from_member="1.1.a",
                to_team="team-missing", to_member=None,
                subject="s", body="b",
            )

    def test_missing_sender_member_raises(
        self, engine: ExecutionEngine
    ) -> None:
        with pytest.raises(TeamToolError, match="Member 'nope'"):
            team_send_message(
                engine, task_id="task-tools",
                from_team="team-1.1", from_member="nope",
                to_team="team-1.2", to_member=None,
                subject="s", body="b",
            )


class TestTeamAddAndClaimTask:
    def test_add_and_claim_cycle(self, engine: ExecutionEngine) -> None:
        tid = team_add_task(
            engine, task_id="task-tools", team_id="team-1.1",
            author_member_id="1.1.a", title="t", detail="d",
        )
        assert tid
        team_claim_task(
            engine, task_id="task-tools",
            task_bead_id=tid, member_id="1.1.b",
        )
        team_complete_task(
            engine, task_id="task-tools",
            task_bead_id=tid, outcome="done",
        )


# ---------------------------------------------------------------------------
# team_dispatch — lead-only
# ---------------------------------------------------------------------------


class TestTeamDispatchRoleEnforcement:
    def test_non_lead_caller_raises(self, engine: ExecutionEngine) -> None:
        """An implementer calling team_dispatch gets a clear error."""
        with pytest.raises(TeamToolError, match="role='lead'"):
            team_dispatch(
                engine, task_id="task-tools",
                parent_team_id="team-1.1",
                caller_member_id="1.1.b",  # implementer, not lead
                members=[{"agent_name": "backend-engineer"}],
            )

    def test_lead_caller_creates_child_team(
        self, engine: ExecutionEngine
    ) -> None:
        child_id = team_dispatch(
            engine, task_id="task-tools",
            parent_team_id="team-1.1",
            caller_member_id="1.1.a",
            members=[
                {"agent_name": "backend-engineer"},
                {"agent_name": "test-engineer"},
            ],
        )
        assert child_id == "1.1::1.1.a"
        # Registry records child team under the parent.
        children = engine._team_registry.child_teams("task-tools", "team-1.1")
        assert len(children) == 1
        assert children[0].team_id == child_id
        assert children[0].leader_agent == "architect"

    def test_subteam_members_appended_to_lead(
        self, engine: ExecutionEngine
    ) -> None:
        team_dispatch(
            engine, task_id="task-tools",
            parent_team_id="team-1.1",
            caller_member_id="1.1.a",
            members=[{"agent_name": "backend-engineer"}],
        )
        state = engine._load_execution()
        lead = engine._find_team_member(state.plan.phases[0].steps[0].team, "1.1.a")
        assert lead is not None
        assert len(lead.sub_team) == 1
        assert lead.sub_team[0].agent_name == "backend-engineer"
        # Auto-generated member_id under the caller.
        assert lead.sub_team[0].member_id == "1.1.a.a"

    def test_custom_member_ids_honored(
        self, engine: ExecutionEngine
    ) -> None:
        team_dispatch(
            engine, task_id="task-tools",
            parent_team_id="team-1.1",
            caller_member_id="1.1.a",
            members=[
                {"agent_name": "be", "member_id": "1.1.a.impl"},
                {"agent_name": "te", "member_id": "1.1.a.tst"},
            ],
        )
        state = engine._load_execution()
        lead = engine._find_team_member(state.plan.phases[0].steps[0].team, "1.1.a")
        ids = [m.member_id for m in lead.sub_team]
        assert ids == ["1.1.a.impl", "1.1.a.tst"]

    def test_synthesis_passed_through(
        self, engine: ExecutionEngine
    ) -> None:
        team_dispatch(
            engine, task_id="task-tools",
            parent_team_id="team-1.1",
            caller_member_id="1.1.a",
            members=[{"agent_name": "be"}],
            synthesis={"strategy": "merge_files"},
        )
        state = engine._load_execution()
        lead = engine._find_team_member(state.plan.phases[0].steps[0].team, "1.1.a")
        assert lead.synthesis is not None
        assert lead.synthesis.strategy == "merge_files"


class TestTeamDispatchIntegrates:
    def test_next_dispatch_wave_includes_new_subteam(
        self, engine: ExecutionEngine
    ) -> None:
        """After team_dispatch, next_actions() includes the new sub-team members."""
        team_dispatch(
            engine, task_id="task-tools",
            parent_team_id="team-1.1",
            caller_member_id="1.1.a",
            members=[{"agent_name": "backend-engineer"}],
        )
        # Record any already-dispatched members as done so the next wave
        # fires fresh for the new sub-member.
        engine.record_team_member_result(
            "1.1", "1.1.a", "architect", status="complete", outcome="c")
        engine.record_team_member_result(
            "1.1", "1.1.b", "be", status="complete", outcome="b")
        actions = engine.next_actions()
        new_ids = {a.step_id for a in actions}
        # 1.1.a.a is the auto-generated sub-member_id.
        assert "1.1.a.a" in new_ids
