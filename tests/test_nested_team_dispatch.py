"""Tests for nested team dispatch in :class:`ExecutionEngine`.

A ``TeamMember`` with ``role == "lead"`` and a non-empty ``sub_team`` is
dispatched as a worker AND its sub-team members are dispatched in the same
wave.  The parent step completes only when the lead AND every
recursively-flattened sub-team member have recorded results.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanPhase,
    PlanStep,
    SynthesisSpec,
    TeamMember,
)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _engine(tmp_path: Path, *, with_storage: bool = False) -> ExecutionEngine:
    """Build an engine.  Pass ``with_storage=True`` to enable the team registry."""
    if with_storage:
        from agent_baton.core.storage.sqlite_backend import SqliteStorage
        storage = SqliteStorage(tmp_path / "baton.db")
        return ExecutionEngine(team_context_root=tmp_path, storage=storage)
    return ExecutionEngine(team_context_root=tmp_path)


def _nested_step() -> PlanStep:
    """A team step with a lead that has 3 sub_team implementers."""
    lead = TeamMember(
        member_id="1.1.a",
        agent_name="architect",
        role="lead",
        task_description="coordinate and unblock",
        sub_team=[
            TeamMember(
                member_id="1.1.a.b",
                agent_name="backend-engineer",
                role="implementer",
                task_description="build service",
            ),
            TeamMember(
                member_id="1.1.a.c",
                agent_name="test-engineer",
                role="implementer",
                task_description="write tests",
            ),
            TeamMember(
                member_id="1.1.a.d",
                agent_name="frontend-engineer",
                role="implementer",
                task_description="build ui",
            ),
        ],
        synthesis=SynthesisSpec(strategy="merge_files"),
    )
    return PlanStep(
        step_id="1.1",
        agent_name="team",
        task_description="Implement feature with lead + sub-team",
        team=[lead],
        synthesis=SynthesisSpec(strategy="merge_files"),
    )


def _plan() -> MachinePlan:
    return MachinePlan(
        task_id="task-nested",
        task_summary="Build nested feature",
        phases=[PlanPhase(phase_id=1, name="Implementation", steps=[_nested_step()])],
    )


# ---------------------------------------------------------------------------
# TestNestedDispatch
# ---------------------------------------------------------------------------


class TestNestedDispatch:
    def test_lead_and_subteam_dispatched_in_same_wave(self, tmp_path: Path) -> None:
        """Lead is dispatched alongside all 3 sub-team members."""
        engine = _engine(tmp_path)
        action = engine.start(_plan())

        assert action.action_type == ActionType.DISPATCH
        # 1 lead + 3 sub-members = 4 total dispatches.
        all_actions = [action, *action.parallel_actions]
        assert len(all_actions) == 4

    def test_lead_is_first_dispatched(self, tmp_path: Path) -> None:
        """Depth-first order: lead comes before its own sub-team."""
        engine = _engine(tmp_path)
        action = engine.start(_plan())

        assert action.step_id == "1.1.a"
        assert action.agent_name == "architect"

    def test_subteam_members_in_parallel_actions(self, tmp_path: Path) -> None:
        """All three sub-team members present in parallel_actions."""
        engine = _engine(tmp_path)
        action = engine.start(_plan())

        sub_ids = {a.step_id for a in action.parallel_actions}
        assert sub_ids == {"1.1.a.b", "1.1.a.c", "1.1.a.d"}

    def test_team_registry_records_parent_and_child(
        self, tmp_path: Path
    ) -> None:
        """Parent team + one child team are created on first dispatch."""
        engine = _engine(tmp_path, with_storage=True)
        engine.start(_plan())

        reg = engine._team_registry
        assert reg is not None
        parent = reg.get_team("task-nested", "team-1.1")
        assert parent is not None
        assert parent.leader_agent == "architect"

        children = reg.child_teams("task-nested", "team-1.1")
        assert len(children) == 1
        assert children[0].team_id == "1.1::1.1.a"
        assert children[0].leader_agent == "architect"
        assert children[0].leader_member_id == "1.1.a"


class TestNestedCompletion:
    def test_completes_only_after_lead_and_subteam_done(
        self, tmp_path: Path
    ) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())

        # Record three sub-team members only — lead still pending.
        engine.record_team_member_result(
            "1.1", "1.1.a.b", "backend-engineer",
            status="complete", outcome="service built",
            files_changed=["src/service.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.a.c", "test-engineer",
            status="complete", outcome="tests passed",
            files_changed=["tests/test_service.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.a.d", "frontend-engineer",
            status="complete", outcome="ui built",
            files_changed=["src/ui.tsx"],
        )

        state = engine._load_state()
        parent = state.get_step_result("1.1")
        assert parent is not None
        # Lead result not recorded yet → parent still dispatched.
        assert parent.status == "dispatched"

        # Now record the lead.
        engine.record_team_member_result(
            "1.1", "1.1.a", "architect",
            status="complete", outcome="coordination done",
            files_changed=["docs/plan.md"],
        )
        state = engine._load_state()
        parent = state.get_step_result("1.1")
        assert parent is not None
        assert parent.status == "complete"

    def test_synthesis_merges_files_across_lead_and_subteam(
        self, tmp_path: Path
    ) -> None:
        """merge_files synthesis dedupes and includes all flat member files."""
        engine = _engine(tmp_path)
        engine.start(_plan())
        engine.record_team_member_result(
            "1.1", "1.1.a", "architect", status="complete",
            outcome="coord", files_changed=["docs/plan.md"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.a.b", "backend-engineer", status="complete",
            outcome="svc", files_changed=["src/service.py", "docs/plan.md"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.a.c", "test-engineer", status="complete",
            outcome="t", files_changed=["tests/test_service.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.a.d", "frontend-engineer", status="complete",
            outcome="ui", files_changed=["src/ui.tsx"],
        )

        state = engine._load_state()
        parent = state.get_step_result("1.1")
        assert parent is not None
        assert parent.status == "complete"
        # merge_files dedupes "docs/plan.md".
        assert set(parent.files_changed) == {
            "docs/plan.md", "src/service.py",
            "tests/test_service.py", "src/ui.tsx",
        }


class TestNestedMemberIdRegex:
    def test_nested_id_still_matches_team_member_re(self) -> None:
        """Nested member IDs (1.1.a.b) must set is_team_member in action dict."""
        from agent_baton.models.execution import (
            ActionType,
            ExecutionAction,
            _TEAM_MEMBER_ID_RE,
        )
        assert _TEAM_MEMBER_ID_RE.match("1.1.a.b")
        assert _TEAM_MEMBER_ID_RE.match("1.1.a")
        assert _TEAM_MEMBER_ID_RE.match("1.1.a.b.c")
        assert not _TEAM_MEMBER_ID_RE.match("1.1")
        assert not _TEAM_MEMBER_ID_RE.match("1.1.a.B")  # uppercase rejected

        # parent_step_id truncation still yields the top-level step.
        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            agent_name="be",
            delegation_prompt="p",
            step_id="1.1.a.b",
        )
        d = action.to_dict()
        assert d["is_team_member"] is True
        assert d["parent_step_id"] == "1.1"
