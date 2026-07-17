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


# ---------------------------------------------------------------------------
# TestNestedTeamRestartBetweenDispatchAndResult — a brand-new ExecutionEngine
# instance (fresh interpreter state, same on-disk persistence) must pick up
# a nested team exactly where a prior instance left off: no duplicate
# dispatch, no lost member results, correct completion once the restarted
# engine finishes recording the roster.
# ---------------------------------------------------------------------------


class TestNestedTeamRestartBetweenDispatchAndResult:

    def test_resume_after_dispatch_returns_same_wave_without_duplication(
        self, tmp_path: Path,
    ) -> None:
        engine1 = _engine(tmp_path)
        action1 = engine1.start(_plan())
        dispatched_1 = {action1.step_id} | {a.step_id for a in action1.parallel_actions}

        # Simulate a crash: brand-new engine object, same on-disk state.
        engine2 = _engine(tmp_path)
        action2 = engine2.resume()

        assert action2.action_type == ActionType.DISPATCH
        dispatched_2 = {action2.step_id} | {a.step_id for a in action2.parallel_actions}
        assert dispatched_2 == dispatched_1 == {
            "1.1.a", "1.1.a.b", "1.1.a.c", "1.1.a.d",
        }

    def test_partial_result_recorded_before_crash_survives_restart(
        self, tmp_path: Path,
    ) -> None:
        engine1 = _engine(tmp_path)
        engine1.start(_plan())
        engine1.record_team_member_result(
            "1.1", "1.1.a.b", "backend-engineer",
            status="complete", outcome="service built",
            files_changed=["src/service.py"],
        )

        # Crash + restart: fresh engine object, same tmp_path persistence.
        engine2 = _engine(tmp_path)
        state = engine2._load_state()
        parent = state.get_step_result("1.1")
        assert parent is not None
        assert parent.status == "dispatched"
        assert {m.member_id for m in parent.member_results} == {"1.1.a.b"}

        # Execution continues normally from the restarted engine — the
        # remaining members complete the nested team.
        engine2.record_team_member_result(
            "1.1", "1.1.a.c", "test-engineer",
            status="complete", outcome="tests passed",
            files_changed=["tests/test_service.py"],
        )
        engine2.record_team_member_result(
            "1.1", "1.1.a.d", "frontend-engineer",
            status="complete", outcome="ui built",
            files_changed=["src/ui.tsx"],
        )
        engine2.record_team_member_result(
            "1.1", "1.1.a", "architect",
            status="complete", outcome="coordination done",
            files_changed=["docs/plan.md"],
        )

        # Yet another restart confirms the final completion persisted.
        engine3 = _engine(tmp_path)
        state3 = engine3._load_state()
        parent3 = state3.get_step_result("1.1")
        assert parent3 is not None
        assert parent3.status == "complete"
        assert {m.member_id for m in parent3.member_results} == {
            "1.1.a", "1.1.a.b", "1.1.a.c", "1.1.a.d",
        }


# ---------------------------------------------------------------------------
# TestNestedTeamMemberFailurePropagation
# ---------------------------------------------------------------------------


class TestNestedTeamMemberFailurePropagation:

    def test_subteam_member_failure_fails_parent_and_preserves_sibling_results(
        self, tmp_path: Path,
    ) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())

        engine.record_team_member_result(
            "1.1", "1.1.a.b", "backend-engineer",
            status="complete", outcome="service built",
            files_changed=["src/service.py"],
        )
        engine.record_team_member_result(
            "1.1", "1.1.a.c", "test-engineer",
            status="failed", outcome="flaky test suite",
        )

        state = engine._load_state()
        parent = state.get_step_result("1.1")
        assert parent is not None
        assert parent.status == "failed"
        assert "1.1.a.c" in parent.error

        # The sibling's successful result is preserved, not discarded, by
        # the failure — a downstream retrospective/handoff still needs it.
        member_ids = {m.member_id for m in parent.member_results}
        assert {"1.1.a.b", "1.1.a.c"} <= member_ids
        succeeded = next(m for m in parent.member_results if m.member_id == "1.1.a.b")
        assert succeeded.status == "complete"
        assert succeeded.files_changed == ["src/service.py"]

    def test_lead_failure_fails_parent_even_when_whole_subteam_succeeded(
        self, tmp_path: Path,
    ) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan())

        for mid, agent in [
            ("1.1.a.b", "backend-engineer"),
            ("1.1.a.c", "test-engineer"),
            ("1.1.a.d", "frontend-engineer"),
        ]:
            engine.record_team_member_result(
                "1.1", mid, agent, status="complete", outcome="done",
            )
        engine.record_team_member_result(
            "1.1", "1.1.a", "architect",
            status="failed", outcome="could not integrate the pieces",
        )

        state = engine._load_state()
        parent = state.get_step_result("1.1")
        assert parent is not None
        assert parent.status == "failed"
        assert "1.1.a" in parent.error
        # All three successful sub-team results remain recorded.
        assert len(parent.member_results) == 4
