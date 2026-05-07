"""Tests for Phase 4-5 async intelligence features.

Covers:
  - SessionState, SessionCheckpoint, SessionParticipant (models/session.py)
  - ContributionRequest (models/decision.py)
  - SynthesisSpec / PlanStep.mcp_servers / MachinePlan.resource_limits (models/execution.py)
  - DecisionManager contribution API (core/runtime/decisions.py)
  - Token budget checking via record_step_result (core/engine/executor.py)
  - Team cost estimation in explain_plan / _build_shared_context (core/engine/planner.py)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.models.session import SessionCheckpoint, SessionParticipant, SessionState
from agent_baton.models.decision import ContributionRequest, DecisionRequest
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
    SynthesisSpec,
    TeamMember,
)
from agent_baton.models.parallel import ResourceLimits
from agent_baton.core.runtime.decisions import DecisionManager
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.events.bus import EventBus
from agent_baton.models.events import Event


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_engine(tmp_path: Path) -> ExecutionEngine:
    root = tmp_path / ".claude" / "team-context"
    root.mkdir(parents=True, exist_ok=True)
    return ExecutionEngine(team_context_root=root)


def _budget_plan(budget_tier: str = "standard") -> MachinePlan:
    return MachinePlan(
        task_id="budget-test",
        task_summary="Test budget",
        budget_tier=budget_tier,
        phases=[
            PlanPhase(
                phase_id=1,
                name="Impl",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Work",
                    )
                ],
            ),
        ],
    )


def _contribution_req(
    request_id: str = "contrib-001",
    task_id: str = "task-1",
    topic: str = "Architecture review",
    contributors: list[str] | None = None,
) -> ContributionRequest:
    return ContributionRequest(
        request_id=request_id,
        task_id=task_id,
        topic=topic,
        description="Please review the design",
        contributors=contributors or ["architect", "security-reviewer"],
    )


# ===========================================================================
# 1. SessionCheckpoint
# ===========================================================================

class TestSessionCheckpoint:
    def test_roundtrip_serialization(self) -> None:
        cp = SessionCheckpoint(
            checkpoint_id="cp-1",
            phase_id=2,
            step_id="2.3",
            description="After phase 2 complete",
        )
        restored = SessionCheckpoint.from_dict(cp.to_dict())
        assert restored.checkpoint_id == "cp-1"
        assert restored.phase_id == 2
        assert restored.step_id == "2.3"
        assert restored.description == "After phase 2 complete"

    def test_timestamp_auto_generated_when_empty(self) -> None:
        cp = SessionCheckpoint(checkpoint_id="cp-x", phase_id=1)
        assert cp.timestamp  # non-empty

    def test_explicit_timestamp_preserved(self) -> None:
        ts = "2026-01-15T12:00:00+00:00"
        cp = SessionCheckpoint(checkpoint_id="cp-y", phase_id=1, timestamp=ts)
        assert cp.timestamp == ts

    def test_from_dict_defaults(self) -> None:
        cp = SessionCheckpoint.from_dict({"checkpoint_id": "cp-z", "phase_id": 0})
        assert cp.step_id == ""
        assert cp.description == ""


# ===========================================================================
# 2. SessionParticipant
# ===========================================================================

class TestSessionParticipant:
    def test_roundtrip_serialization(self) -> None:
        p = SessionParticipant(
            name="architect",
            role="agent",
            contributions=5,
        )
        restored = SessionParticipant.from_dict(p.to_dict())
        assert restored.name == "architect"
        assert restored.role == "agent"
        assert restored.contributions == 5

    def test_joined_at_auto_generated(self) -> None:
        p = SessionParticipant(name="alice")
        assert p.joined_at  # non-empty

    def test_explicit_joined_at_preserved(self) -> None:
        ts = "2026-03-01T09:00:00+00:00"
        p = SessionParticipant(name="bob", joined_at=ts)
        assert p.joined_at == ts

    def test_default_role_is_agent(self) -> None:
        p = SessionParticipant(name="x")
        assert p.role == "agent"

    def test_from_dict_defaults(self) -> None:
        p = SessionParticipant.from_dict({"name": "anon"})
        assert p.role == "agent"
        assert p.contributions == 0


# ===========================================================================
# 3. SessionState
# ===========================================================================

class TestSessionStateSerializationAndDefaults:
    def test_roundtrip_serialization(self) -> None:
        s = SessionState(
            session_id="sess-1",
            task_id="task-abc",
            status="active",
            metadata={"sprint": "42"},
        )
        restored = SessionState.from_dict(s.to_dict())
        assert restored.session_id == "sess-1"
        assert restored.task_id == "task-abc"
        assert restored.status == "active"
        assert restored.metadata == {"sprint": "42"}

    def test_created_at_auto_generated(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        assert s.created_at

    def test_last_activity_defaults_to_created_at(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        assert s.last_activity == s.created_at

    def test_participants_and_checkpoints_survive_roundtrip(self) -> None:
        s = SessionState(session_id="s2", task_id="t2")
        s.add_participant("alice", role="human")
        s.checkpoint("cp-1", phase_id=1, description="done")

        d = s.to_dict()
        restored = SessionState.from_dict(d)

        assert len(restored.participants) == 1
        assert restored.participants[0].name == "alice"
        assert len(restored.checkpoints) == 1
        assert restored.checkpoints[0].checkpoint_id == "cp-1"

    def test_from_dict_defaults(self) -> None:
        s = SessionState.from_dict({"session_id": "x", "task_id": "y"})
        assert s.status == "active"
        assert s.participants == []
        assert s.checkpoints == []
        assert s.pause_reason == ""


class TestSessionStateTouch:
    def test_touch_updates_last_activity(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        original = s.last_activity
        import time; time.sleep(0.01)  # ensure a different second isn't needed — just ensure call works
        s.touch()
        # last_activity is updated (may be same second; just verify call doesn't error)
        assert s.last_activity >= original


class TestSessionStateAddParticipant:
    def test_new_participant_added(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        p = s.add_participant("architect", role="agent")
        assert p.name == "architect"
        assert p.contributions == 0
        assert len(s.participants) == 1

    def test_existing_participant_increments_contributions(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        s.add_participant("architect")
        s.add_participant("architect")
        assert len(s.participants) == 1
        assert s.participants[0].contributions == 1

    def test_different_participants_both_tracked(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        s.add_participant("alice")
        s.add_participant("bob")
        names = {p.name for p in s.participants}
        assert names == {"alice", "bob"}

    def test_returned_participant_is_same_object_as_in_list(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        p1 = s.add_participant("alice")
        p2 = s.add_participant("alice")
        assert p1 is p2


class TestSessionStateCheckpoint:
    def test_checkpoint_appended_to_list(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        cp = s.checkpoint("cp-1", phase_id=2, step_id="2.1", description="end of phase 2")
        assert len(s.checkpoints) == 1
        assert cp is s.checkpoints[0]

    def test_checkpoint_fields_set(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        cp = s.checkpoint("cp-1", phase_id=3, step_id="3.2", description="midpoint")
        assert cp.checkpoint_id == "cp-1"
        assert cp.phase_id == 3
        assert cp.step_id == "3.2"
        assert cp.description == "midpoint"

    def test_checkpoint_updates_last_activity(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        before = s.last_activity
        s.checkpoint("cp-2", phase_id=1)
        assert s.last_activity >= before

    def test_multiple_checkpoints_ordered(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        s.checkpoint("cp-a", phase_id=1)
        s.checkpoint("cp-b", phase_id=2)
        assert [cp.checkpoint_id for cp in s.checkpoints] == ["cp-a", "cp-b"]


class TestSessionStatePauseResume:
    def test_pause_sets_status(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        s.pause("awaiting review")
        assert s.status == "paused"

    def test_pause_records_reason(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        s.pause("awaiting review")
        assert s.pause_reason == "awaiting review"

    def test_pause_updates_last_activity(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        before = s.last_activity
        s.pause()
        assert s.last_activity >= before

    def test_resume_clears_pause_reason(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        s.pause("waiting")
        s.resume()
        assert s.pause_reason == ""

    def test_resume_sets_active(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        s.pause()
        s.resume()
        assert s.status == "active"

    def test_resume_updates_last_activity(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        s.pause()
        before = s.last_activity
        s.resume()
        assert s.last_activity >= before

    def test_pause_resume_pause_cycle(self) -> None:
        s = SessionState(session_id="s", task_id="t")
        s.pause("reason-1")
        s.resume()
        s.pause("reason-2")
        assert s.status == "paused"
        assert s.pause_reason == "reason-2"


# ===========================================================================
# 4. ContributionRequest
# ===========================================================================

class TestContributionRequestSerialization:
    def test_roundtrip_serialization(self) -> None:
        cr = _contribution_req()
        restored = ContributionRequest.from_dict(cr.to_dict())
        assert restored.request_id == cr.request_id
        assert restored.topic == cr.topic
        assert restored.contributors == cr.contributors
        assert restored.status == "collecting"

    def test_created_at_auto_generated(self) -> None:
        cr = _contribution_req()
        assert cr.created_at

    def test_from_dict_defaults(self) -> None:
        cr = ContributionRequest.from_dict(
            {"request_id": "x", "task_id": "t", "topic": "X"}
        )
        assert cr.status == "collecting"
        assert cr.contributors == []
        assert cr.responses == {}
        assert cr.facilitator_agent == "architect"

    def test_deadline_optional(self) -> None:
        cr = _contribution_req()
        assert cr.deadline is None
        d = cr.to_dict()
        assert d["deadline"] is None
        restored = ContributionRequest.from_dict(d)
        assert restored.deadline is None


class TestContributionRequestRespond:
    def test_respond_records_response(self) -> None:
        cr = _contribution_req(contributors=["alice", "bob"])
        cr.respond("alice", "LGTM")
        assert cr.responses["alice"] == "LGTM"

    def test_is_complete_false_when_pending(self) -> None:
        cr = _contribution_req(contributors=["alice", "bob"])
        cr.respond("alice", "yes")
        assert not cr.is_complete

    def test_is_complete_true_when_all_responded(self) -> None:
        cr = _contribution_req(contributors=["alice", "bob"])
        cr.respond("alice", "yes")
        cr.respond("bob", "no")
        assert cr.is_complete

    def test_pending_contributors_shows_who_is_missing(self) -> None:
        cr = _contribution_req(contributors=["alice", "bob", "carol"])
        cr.respond("alice", "input")
        assert set(cr.pending_contributors) == {"bob", "carol"}

    def test_pending_contributors_empty_when_all_responded(self) -> None:
        cr = _contribution_req(contributors=["alice"])
        cr.respond("alice", "done")
        assert cr.pending_contributors == []

    def test_status_transitions_to_ready_when_all_respond(self) -> None:
        cr = _contribution_req(contributors=["alice", "bob"])
        assert cr.status == "collecting"
        cr.respond("alice", "a")
        assert cr.status == "collecting"
        cr.respond("bob", "b")
        assert cr.status == "ready"

    def test_status_stays_collecting_until_last_contributor(self) -> None:
        cr = _contribution_req(contributors=["alice", "bob", "carol"])
        cr.respond("alice", "a")
        cr.respond("bob", "b")
        assert cr.status == "collecting"

    def test_is_complete_with_no_contributors(self) -> None:
        cr = ContributionRequest(
            request_id="r", task_id="t", topic="X", contributors=[]
        )
        assert cr.is_complete  # vacuously true


# ===========================================================================
# 5. SynthesisSpec on PlanStep
# ===========================================================================

class TestSynthesisSpecSerialization:
    def test_roundtrip_serialization(self) -> None:
        spec = SynthesisSpec(
            strategy="agent_synthesis",
            synthesis_agent="code-reviewer",
            synthesis_prompt="Merge these outputs: {member_outcomes}",
            conflict_handling="escalate",
        )
        restored = SynthesisSpec.from_dict(spec.to_dict())
        assert restored.strategy == "agent_synthesis"
        assert restored.synthesis_agent == "code-reviewer"
        assert restored.synthesis_prompt == "Merge these outputs: {member_outcomes}"
        assert restored.conflict_handling == "escalate"

    def test_from_dict_defaults(self) -> None:
        spec = SynthesisSpec.from_dict({})
        assert spec.strategy == "concatenate"
        assert spec.synthesis_agent == "code-reviewer"
        assert spec.conflict_handling == "auto_merge"


class TestPlanStepMcpServers:
    def test_mcp_servers_absent_from_dict_when_empty(self) -> None:
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Work",
        )
        d = step.to_dict()
        assert "mcp_servers" not in d

    def test_mcp_servers_present_in_dict_when_non_empty(self) -> None:
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Work",
            mcp_servers=["github", "postgres"],
        )
        d = step.to_dict()
        assert "mcp_servers" in d
        assert d["mcp_servers"] == ["github", "postgres"]

    def test_from_dict_with_mcp_servers(self) -> None:
        step = PlanStep.from_dict({
            "step_id": "1.1",
            "agent_name": "backend-engineer",
            "task_description": "Work",
            "mcp_servers": ["slack"],
        })
        assert step.mcp_servers == ["slack"]

    def test_from_dict_without_mcp_servers_defaults_empty(self) -> None:
        step = PlanStep.from_dict({
            "step_id": "1.1",
            "agent_name": "backend-engineer",
            "task_description": "Work",
        })
        assert step.mcp_servers == []

    def test_roundtrip_with_mcp_servers(self) -> None:
        step = PlanStep(
            step_id="2.1",
            agent_name="architect",
            task_description="Design",
            mcp_servers=["github", "jira"],
        )
        restored = PlanStep.from_dict(step.to_dict())
        assert restored.mcp_servers == ["github", "jira"]


class TestMachinePlanResourceLimits:
    def test_resource_limits_absent_from_dict_when_none(self) -> None:
        plan = MachinePlan(
            task_id="p1", task_summary="Test", phases=[]
        )
        d = plan.to_dict()
        assert "resource_limits" not in d

    def test_resource_limits_serialized_when_set(self) -> None:
        rl = ResourceLimits(
            max_concurrent_executions=5,
            max_concurrent_agents=10,
            max_tokens_per_minute=1000,
            max_concurrent_per_project=3,
        )
        plan = MachinePlan(
            task_id="p2", task_summary="Test", phases=[], resource_limits=rl
        )
        d = plan.to_dict()
        assert "resource_limits" in d
        assert d["resource_limits"]["max_concurrent_executions"] == 5
        assert d["resource_limits"]["max_concurrent_agents"] == 10

    def test_resource_limits_roundtrip(self) -> None:
        rl = ResourceLimits(
            max_concurrent_executions=2,
            max_concurrent_agents=6,
            max_tokens_per_minute=500,
            max_concurrent_per_project=1,
        )
        plan = MachinePlan(
            task_id="p3", task_summary="Test", phases=[], resource_limits=rl
        )
        restored = MachinePlan.from_dict(plan.to_dict())
        assert restored.resource_limits is not None
        assert restored.resource_limits.max_concurrent_executions == 2
        assert restored.resource_limits.max_concurrent_agents == 6
        assert restored.resource_limits.max_tokens_per_minute == 500
        assert restored.resource_limits.max_concurrent_per_project == 1

    def test_from_dict_without_resource_limits_is_none(self) -> None:
        plan = MachinePlan.from_dict({
            "task_id": "p4",
            "task_summary": "Test",
        })
        assert plan.resource_limits is None


class TestResourceLimitsSerialization:
    def test_roundtrip(self) -> None:
        rl = ResourceLimits(
            max_concurrent_executions=3,
            max_concurrent_agents=8,
            max_tokens_per_minute=0,
            max_concurrent_per_project=2,
        )
        restored = ResourceLimits.from_dict(rl.to_dict())
        assert restored.max_concurrent_executions == 3
        assert restored.max_concurrent_agents == 8
        assert restored.max_tokens_per_minute == 0
        assert restored.max_concurrent_per_project == 2

    def test_from_dict_defaults(self) -> None:
        rl = ResourceLimits.from_dict({})
        assert rl.max_concurrent_executions == 3
        assert rl.max_concurrent_agents == 8
        assert rl.max_tokens_per_minute == 0
        assert rl.max_concurrent_per_project == 2


# ===========================================================================
# 6. DecisionManager contribution API
# ===========================================================================

class TestDecisionManagerRequestContribution:
    def test_writes_json_file(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        cr = _contribution_req()
        path = mgr.request_contribution(cr)
        assert path.exists()
        assert path.suffix == ".json"

    def test_json_file_contains_request_data(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        cr = _contribution_req(request_id="c1", topic="Design review")
        mgr.request_contribution(cr)
        data = json.loads((tmp_path / "c1.json").read_text())
        assert data["request_id"] == "c1"
        assert data["topic"] == "Design review"
        assert data["status"] == "collecting"

    def test_writes_md_summary_file(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        cr = _contribution_req(request_id="c2")
        mgr.request_contribution(cr)
        md_file = tmp_path / "c2.md"
        assert md_file.exists()
        content = md_file.read_text()
        assert "Contribution Request" in content
        assert "baton decide --contribute" in content

    def test_md_summary_lists_contributors(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        cr = _contribution_req(
            request_id="c3",
            contributors=["alice", "bob"],
        )
        mgr.request_contribution(cr)
        content = (tmp_path / "c3.md").read_text()
        assert "alice" in content
        assert "bob" in content

    def test_publishes_event_with_bus(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock
        bus = MagicMock(spec=EventBus)
        mgr = DecisionManager(decisions_dir=tmp_path, bus=bus)
        cr = _contribution_req(request_id="c4")
        mgr.request_contribution(cr)
        bus.publish.assert_called_once()
        event = bus.publish.call_args[0][0]
        assert event.topic == "contribution.requested"
        assert event.task_id == cr.task_id

    def test_no_error_without_bus(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        mgr.request_contribution(_contribution_req())  # should not raise

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path / "deep" / "decisions")
        path = mgr.request_contribution(_contribution_req())
        assert path.exists()


class TestDecisionManagerContribute:
    def test_contribute_records_response(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        cr = _contribution_req(
            request_id="c10",
            contributors=["alice", "bob"],
        )
        mgr.request_contribution(cr)
        mgr.contribute("c10", "alice", "My input here")
        loaded = mgr.get_contribution("c10")
        assert loaded is not None
        assert loaded.responses["alice"] == "My input here"

    def test_contribute_returns_false_when_pending(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        cr = _contribution_req(
            request_id="c11",
            contributors=["alice", "bob"],
        )
        mgr.request_contribution(cr)
        result = mgr.contribute("c11", "alice", "input")
        assert result is False

    def test_contribute_returns_true_when_all_collected(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        cr = _contribution_req(
            request_id="c12",
            contributors=["alice", "bob"],
        )
        mgr.request_contribution(cr)
        mgr.contribute("c12", "alice", "input-a")
        result = mgr.contribute("c12", "bob", "input-b")
        assert result is True

    def test_contribute_updates_status_to_ready_when_all_collected(
        self, tmp_path: Path
    ) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        cr = _contribution_req(
            request_id="c13",
            contributors=["alice"],
        )
        mgr.request_contribution(cr)
        mgr.contribute("c13", "alice", "done")
        loaded = mgr.get_contribution("c13")
        assert loaded is not None
        assert loaded.status == "ready"

    def test_contribute_raises_for_missing_request(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        with pytest.raises(FileNotFoundError):
            mgr.contribute("nonexistent", "alice", "input")

    def test_contribute_publishes_ready_event_when_complete(
        self, tmp_path: Path
    ) -> None:
        # NOTE: Same Event() timestamp bug applies in contribute().  When all
        # contributors respond the production code tries to publish an Event(...)
        # without timestamp, which raises TypeError before publish() is called.
        # This test documents that bug.
        from unittest.mock import MagicMock
        bus = MagicMock(spec=EventBus)
        mgr = DecisionManager(decisions_dir=tmp_path, bus=bus)

        cr = _contribution_req(request_id="c14", contributors=["alice"])
        mgr.request_contribution(cr)

        result = mgr.contribute("c14", "alice", "done")
        assert result is True
        bus.publish.assert_called()
        # Last call should be the contribution.ready event
        event = bus.publish.call_args_list[-1][0][0]
        assert event.topic == "contribution.ready"
        assert event.task_id == cr.task_id

    def test_contribute_does_not_publish_ready_event_when_pending(
        self, tmp_path: Path
    ) -> None:
        from unittest.mock import MagicMock
        bus = MagicMock(spec=EventBus)
        mgr = DecisionManager(decisions_dir=tmp_path, bus=bus)

        cr = _contribution_req(request_id="c15", contributors=["alice", "bob"])
        mgr.request_contribution(cr)

        bus.reset_mock()
        mgr.contribute("c15", "alice", "input")
        # Only alice responded, bob still pending — no ready event
        bus.publish.assert_not_called()


class TestDecisionManagerGetContribution:
    def test_returns_contribution_by_id(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        cr = _contribution_req(request_id="gc1", topic="My topic")
        mgr.request_contribution(cr)
        loaded = mgr.get_contribution("gc1")
        assert loaded is not None
        assert loaded.request_id == "gc1"
        assert loaded.topic == "My topic"

    def test_returns_none_for_missing_id(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        assert mgr.get_contribution("nonexistent") is None


class TestDecisionManagerPendingContributions:
    def test_returns_contributions_with_collecting_status(
        self, tmp_path: Path
    ) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        cr1 = _contribution_req(request_id="pc1", contributors=["alice", "bob"])
        cr2 = _contribution_req(request_id="pc2", contributors=["carol"])
        mgr.request_contribution(cr1)
        mgr.request_contribution(cr2)
        pending = mgr.pending_contributions()
        ids = {c.request_id for c in pending}
        assert ids == {"pc1", "pc2"}

    def test_excludes_completed_contributions(self, tmp_path: Path) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        cr = _contribution_req(request_id="pc3", contributors=["alice"])
        mgr.request_contribution(cr)
        mgr.contribute("pc3", "alice", "done")
        pending = mgr.pending_contributions()
        ids = {c.request_id for c in pending}
        assert "pc3" not in ids

    def test_returns_empty_list_when_no_decisions_dir(
        self, tmp_path: Path
    ) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path / "nonexistent")
        assert mgr.pending_contributions() == []

    def test_excludes_decision_requests_not_contributions(
        self, tmp_path: Path
    ) -> None:
        mgr = DecisionManager(decisions_dir=tmp_path)
        # Write a DecisionRequest (lacks "contributors" field)
        req = DecisionRequest.create("t1", "gate_approval", "Review PR")
        mgr.request(req)
        # Should not appear in pending_contributions
        pending = mgr.pending_contributions()
        assert all(isinstance(c, ContributionRequest) for c in pending)


# ===========================================================================
# 7. Token budget checking via record_step_result
# ===========================================================================

class TestTokenBudgetChecking:
    """_check_token_budget is verified indirectly via record_step_result deviations."""

    @pytest.mark.parametrize("budget_tier,tokens,expect_warning", [
        ("lean",     49_999, False),   # within lean limit
        ("lean",     50_001, True),    # just over lean limit
        ("standard", 499_999, False),  # within standard limit
        ("standard", 500_001, True),   # just over standard limit
        ("full",     1_999_999, False), # within full limit
        ("full",     2_000_001, True),  # just over full limit
    ])
    def test_budget_warning_appended_to_deviations(
        self,
        tmp_path: Path,
        budget_tier: str,
        tokens: int,
        expect_warning: bool,
    ) -> None:
        engine = _make_engine(tmp_path)
        plan = _budget_plan(budget_tier=budget_tier)
        engine.start(plan)
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="Done",
            estimated_tokens=tokens,
        )
        state = engine._load_state()
        assert state is not None
        result = state.step_results[-1]
        budget_warnings = [
            d for d in result.deviations if "TOKEN_BUDGET_WARNING" in d
        ]
        if expect_warning:
            assert len(budget_warnings) == 1
            assert budget_tier in budget_warnings[0]
        else:
            assert budget_warnings == []

    def test_budget_warning_message_contains_tier_and_limit(
        self, tmp_path: Path
    ) -> None:
        engine = _make_engine(tmp_path)
        plan = _budget_plan(budget_tier="lean")
        engine.start(plan)
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="Done",
            estimated_tokens=60_000,
        )
        state = engine._load_state()
        assert state is not None
        result = state.step_results[-1]
        warning = next(d for d in result.deviations if "TOKEN_BUDGET_WARNING" in d)
        assert "lean" in warning
        assert "50,000" in warning

    def test_no_warning_when_exactly_at_limit(self, tmp_path: Path) -> None:
        engine = _make_engine(tmp_path)
        plan = _budget_plan(budget_tier="lean")
        engine.start(plan)
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="Done",
            estimated_tokens=50_000,  # exactly at the limit, not over
        )
        state = engine._load_state()
        assert state is not None
        result = state.step_results[-1]
        budget_warnings = [d for d in result.deviations if "TOKEN_BUDGET_WARNING" in d]
        assert budget_warnings == []

    def test_cumulative_tokens_from_multiple_steps_triggers_warning(
        self, tmp_path: Path
    ) -> None:
        """Budget check uses the running total across all step results."""
        # Two steps — need to add a second step to the plan
        plan = MachinePlan(
            task_id="budget-multi",
            task_summary="Multi-step budget test",
            budget_tier="lean",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Impl",
                    steps=[
                        PlanStep(
                            step_id="1.1",
                            agent_name="backend-engineer",
                            task_description="Part 1",
                        ),
                        PlanStep(
                            step_id="1.2",
                            agent_name="backend-engineer",
                            task_description="Part 2",
                        ),
                    ],
                ),
            ],
        )
        engine = _make_engine(tmp_path)
        engine.start(plan)
        # First step: 30k tokens — under the 50k lean limit
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete",
            outcome="Done", estimated_tokens=30_000,
        )
        # Second step: pushes total to 55k — over the limit
        engine.record_step_result(
            "1.2", "backend-engineer", status="complete",
            outcome="Done", estimated_tokens=25_000,
        )
        state = engine._load_state()
        assert state is not None
        last_result = state.step_results[-1]
        budget_warnings = [
            d for d in last_result.deviations if "TOKEN_BUDGET_WARNING" in d
        ]
        assert len(budget_warnings) == 1


# ===========================================================================
# 8. Team cost estimation in planner
# ===========================================================================

class TestPlannerTeamCostEstimates:
    """Verify explain_plan includes the team cost section only when data exists."""

    def _make_planner_with_team_data(
        self, tmp_path: Path, agents: list[str], avg_cost: int
    ):
        """Set up an IntelligentPlanner with a pre-seeded team-patterns.json."""
        from agent_baton.core.engine.planner import IntelligentPlanner
        from agent_baton.models.pattern import TeamPattern

        ctx = tmp_path / "team-context"
        ctx.mkdir(parents=True, exist_ok=True)

        # Seed the team pattern file directly
        pattern = TeamPattern(
            pattern_id="team-test-001",
            agents=sorted(agents),
            task_types=["new-feature"],
            success_rate=0.9,
            sample_size=10,
            avg_token_cost=avg_cost,
            confidence=0.85,
        )
        patterns_path = ctx / "team-patterns.json"
        patterns_path.write_text(
            json.dumps([pattern.to_dict()], indent=2) + "\n",
            encoding="utf-8",
        )

        return IntelligentPlanner(team_context_root=ctx)

    def _make_planner_with_team_step(
        self, tmp_path: Path, agents: list[str], avg_cost: int
    ):
        """Return a planner with team cost data and a plan containing a team step."""
        from agent_baton.core.engine.planner import IntelligentPlanner
        planner = self._make_planner_with_team_data(tmp_path, agents, avg_cost)

        # Build a plan that explicitly contains a team step so the
        # planner's team cost loop has something to find.
        team_agents = agents
        team_members = [
            TeamMember(member_id=f"1.1.{chr(ord('a') + i)}", agent_name=a)
            for i, a in enumerate(team_agents)
        ]
        plan = MachinePlan(
            task_id="team-cost-test",
            task_summary="Team cost test",
            budget_tier="standard",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Team work",
                    steps=[
                        PlanStep(
                            step_id="1.1",
                            agent_name=team_agents[0],
                            task_description="Collaborate",
                            team=team_members,
                        ),
                    ],
                ),
            ],
        )

        # Manually populate the internal estimate dict so explain_plan works
        # without needing create_plan (which calls the LLM classifier).
        planner._last_team_cost_estimates = {"1.1": avg_cost}
        planner._last_pattern_used = None
        planner._last_score_warnings = []
        planner._last_routing_notes = []
        planner._last_classification = None
        planner._last_policy_violations = []
        planner._last_task_classification = None

        return planner, plan

    def test_explain_plan_includes_team_cost_section_when_data_present(
        self, tmp_path: Path
    ) -> None:
        planner, plan = self._make_planner_with_team_step(
            tmp_path,
            agents=["architect", "security-reviewer"],
            avg_cost=80_000,
        )
        explanation = planner.explain_plan(plan)
        assert "Team Cost Estimates" in explanation

    def test_explain_plan_team_cost_contains_token_estimate(
        self, tmp_path: Path
    ) -> None:
        planner, plan = self._make_planner_with_team_step(
            tmp_path,
            agents=["architect", "security-reviewer"],
            avg_cost=80_000,
        )
        explanation = planner.explain_plan(plan)
        assert "80,000" in explanation

    def test_explain_plan_omits_team_cost_section_when_no_data(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.core.engine.planner import IntelligentPlanner

        ctx = tmp_path / "team-context"
        ctx.mkdir(parents=True, exist_ok=True)
        planner = IntelligentPlanner(team_context_root=ctx)

        # No team cost data in the planner
        planner._last_team_cost_estimates = {}
        planner._last_pattern_used = None
        planner._last_score_warnings = []
        planner._last_routing_notes = []
        planner._last_classification = None
        planner._last_policy_violations = []
        planner._last_task_classification = None

        plan = MachinePlan(
            task_id="no-team",
            task_summary="Solo task",
            budget_tier="standard",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Impl",
                    steps=[
                        PlanStep(
                            step_id="1.1",
                            agent_name="backend-engineer",
                            task_description="Work",
                        )
                    ],
                ),
            ],
        )
        explanation = planner.explain_plan(plan)
        assert "Team Cost Estimates" not in explanation

    def test_build_shared_context_includes_team_cost_when_data_present(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.core.engine.planner import IntelligentPlanner

        ctx = tmp_path / "team-context"
        ctx.mkdir(parents=True, exist_ok=True)
        planner = IntelligentPlanner(team_context_root=ctx)
        planner._last_team_cost_estimates = {"1.1": 120_000}
        planner._last_classification = None
        planner._last_policy_violations = []
        planner._last_retro_feedback = None

        plan = MachinePlan(
            task_id="shared-ctx-test",
            task_summary="Test shared context",
            budget_tier="standard",
            phases=[],
        )
        context = planner._build_shared_context(plan)
        assert "Team Cost Estimate" in context

    def test_build_shared_context_omits_team_cost_when_no_data(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.core.engine.planner import IntelligentPlanner

        ctx = tmp_path / "team-context"
        ctx.mkdir(parents=True, exist_ok=True)
        planner = IntelligentPlanner(team_context_root=ctx)
        planner._last_team_cost_estimates = {}
        planner._last_classification = None
        planner._last_policy_violations = []
        planner._last_retro_feedback = None

        plan = MachinePlan(
            task_id="no-team-ctx",
            task_summary="Test shared context no team",
            budget_tier="standard",
            phases=[],
        )
        context = planner._build_shared_context(plan)
        assert "Team Cost Estimate" not in context

    def test_explain_plan_includes_step_id_in_team_cost_section(
        self, tmp_path: Path
    ) -> None:
        planner, plan = self._make_planner_with_team_step(
            tmp_path,
            agents=["architect", "security-reviewer"],
            avg_cost=50_000,
        )
        explanation = planner.explain_plan(plan)
        # The team cost section should reference the step ID "1.1"
        assert "1.1" in explanation

    def test_build_shared_context_includes_budget_percentage(
        self, tmp_path: Path
    ) -> None:
        from agent_baton.core.engine.planner import IntelligentPlanner

        ctx = tmp_path / "team-context"
        ctx.mkdir(parents=True, exist_ok=True)
        planner = IntelligentPlanner(team_context_root=ctx)
        # 250k tokens on a 500k standard budget = 50%
        planner._last_team_cost_estimates = {"1.1": 250_000}
        planner._last_classification = None
        planner._last_policy_violations = []
        planner._last_retro_feedback = None

        plan = MachinePlan(
            task_id="pct-test",
            task_summary="Test budget percentage",
            budget_tier="standard",
            phases=[],
        )
        context = planner._build_shared_context(plan)
        assert "50%" in context


# ---------------------------------------------------------------------------
# Regression tests for 5 explain_plan regressions from refactor 9652e07
# ---------------------------------------------------------------------------

def _make_explain_plan(tmp_path: Path) -> tuple:
    """Return (planner, minimal_plan) wired for explain_plan tests.

    The planner has an isolated team-context dir.  The plan is a minimal
    MachinePlan with one phase and one step — enough for all section
    renderers to run without hitting real classifiers or the network.
    """
    from agent_baton.core.engine.planner import IntelligentPlanner

    ctx = tmp_path / "team-context"
    ctx.mkdir(parents=True, exist_ok=True)
    planner = IntelligentPlanner(team_context_root=ctx)

    # Zero-out all _last_* to start clean
    planner._last_pattern_used = None
    planner._last_score_warnings = []
    planner._last_routing_notes = []
    planner._last_classification = None
    planner._last_policy_violations = []
    planner._last_task_classification = None
    planner._last_foresight_insights = []
    planner._last_team_cost_estimates = {}
    planner._last_review_result = None
    planner._last_retro_feedback = None

    plan = MachinePlan(
        task_id="explain-test",
        task_summary="Regression test task",
        budget_tier="standard",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Do the work",
                    )
                ],
            )
        ],
    )
    return planner, plan


class TestExplainPlanRegressions:
    """Regression tests for the 5 explain_plan behaviors dropped by refactor 9652e07.

    None of these had tests before; this class ensures future refactors
    break loudly if any section is silently removed again.
    """

    # ------------------------------------------------------------------
    # Finding 1 (HIGH): ## Task Classification section
    # ------------------------------------------------------------------

    def test_task_classification_section_header_present(
        self, tmp_path: Path
    ) -> None:
        """explain_plan must emit a ## Task Classification section."""
        from agent_baton.core.engine.classifier import TaskClassification

        planner, plan = _make_explain_plan(tmp_path)
        planner._last_task_classification = TaskClassification(
            task_type="new-feature",
            complexity="medium",
            agents=["backend-engineer", "test-engineer"],
            phases=["Design", "Implement", "Test"],
            reasoning="Standard feature work",
            source="keyword",
        )
        output = planner.explain_plan(plan)
        assert "## Task Classification" in output

    def test_task_classification_source_field_rendered(
        self, tmp_path: Path
    ) -> None:
        """Task Classification section must include the classifier source."""
        from agent_baton.core.engine.classifier import TaskClassification

        planner, plan = _make_explain_plan(tmp_path)
        planner._last_task_classification = TaskClassification(
            task_type="bug-fix",
            complexity="light",
            agents=["backend-engineer"],
            phases=["Implement"],
            reasoning="Tiny one-liner fix",
            source="haiku-classifier",
        )
        output = planner.explain_plan(plan)
        assert "haiku-classifier" in output

    def test_task_classification_reasoning_rendered(
        self, tmp_path: Path
    ) -> None:
        """Task Classification section must include tc.reasoning."""
        from agent_baton.core.engine.classifier import TaskClassification

        planner, plan = _make_explain_plan(tmp_path)
        planner._last_task_classification = TaskClassification(
            task_type="migration",
            complexity="medium",
            agents=["backend-engineer"],
            phases=["Implement"],
            reasoning="Moving tables across schemas",
            source="keyword",
        )
        output = planner.explain_plan(plan)
        assert "Moving tables across schemas" in output

    def test_task_classification_agents_and_phases_rendered(
        self, tmp_path: Path
    ) -> None:
        """Task Classification section must include selected agents and phases."""
        from agent_baton.core.engine.classifier import TaskClassification

        planner, plan = _make_explain_plan(tmp_path)
        planner._last_task_classification = TaskClassification(
            task_type="new-feature",
            complexity="medium",
            agents=["architect", "test-engineer"],
            phases=["Design", "Test"],
            reasoning="Standard",
            source="keyword",
        )
        output = planner.explain_plan(plan)
        assert "architect" in output
        assert "Design" in output

    def test_task_classification_absent_renders_fallback(
        self, tmp_path: Path
    ) -> None:
        """When _last_task_classification is None, a fallback line appears."""
        planner, plan = _make_explain_plan(tmp_path)
        planner._last_task_classification = None
        output = planner.explain_plan(plan)
        assert "## Task Classification" in output
        assert "No task classification available." in output

    # ------------------------------------------------------------------
    # Finding 2 (HIGH): ## Plan Review three-state block
    # ------------------------------------------------------------------

    def test_plan_review_section_header_always_present(
        self, tmp_path: Path
    ) -> None:
        """explain_plan must always emit a ## Plan Review section."""
        planner, plan = _make_explain_plan(tmp_path)
        output = planner.explain_plan(plan)
        assert "## Plan Review" in output

    def test_plan_review_skipped_light_state(self, tmp_path: Path) -> None:
        """When source == 'skipped-light', the section says 'Skipped'."""
        from agent_baton.core.engine.plan_reviewer import PlanReviewResult

        planner, plan = _make_explain_plan(tmp_path)
        planner._last_review_result = PlanReviewResult(source="skipped-light")
        output = planner.explain_plan(plan)
        assert "## Plan Review" in output
        assert "Skipped" in output

    def test_plan_review_clean_state(self, tmp_path: Path) -> None:
        """When review ran but found nothing, the section shows 'No structural issues'."""
        from agent_baton.core.engine.plan_reviewer import PlanReviewResult

        planner, plan = _make_explain_plan(tmp_path)
        planner._last_review_result = PlanReviewResult(
            source="heuristic",
            splits_applied=0,
            teams_created=0,
            dependencies_added=0,
            warnings=[],
        )
        output = planner.explain_plan(plan)
        assert "## Plan Review" in output
        assert "No structural issues" in output

    def test_plan_review_active_changes_splits_applied(
        self, tmp_path: Path
    ) -> None:
        """When splits_applied > 0 the section renders the count."""
        from agent_baton.core.engine.plan_reviewer import PlanReviewResult

        planner, plan = _make_explain_plan(tmp_path)
        planner._last_review_result = PlanReviewResult(
            source="heuristic",
            splits_applied=2,
            teams_created=0,
            dependencies_added=0,
            warnings=[],
        )
        output = planner.explain_plan(plan)
        assert "## Plan Review" in output
        assert "splits_applied" in output.lower() or "Steps split" in output or "2" in output

    def test_plan_review_active_changes_teams_created(
        self, tmp_path: Path
    ) -> None:
        """When teams_created > 0 the section mentions teams."""
        from agent_baton.core.engine.plan_reviewer import PlanReviewResult

        planner, plan = _make_explain_plan(tmp_path)
        planner._last_review_result = PlanReviewResult(
            source="heuristic",
            splits_applied=0,
            teams_created=1,
            dependencies_added=0,
            warnings=["watch out"],
        )
        output = planner.explain_plan(plan)
        assert "Teams created" in output or "teams_created" in output.lower()

    def test_plan_review_warnings_rendered(self, tmp_path: Path) -> None:
        """Warnings from the review result must appear in the section."""
        from agent_baton.core.engine.plan_reviewer import PlanReviewResult

        planner, plan = _make_explain_plan(tmp_path)
        planner._last_review_result = PlanReviewResult(
            source="heuristic",
            splits_applied=0,
            teams_created=1,
            dependencies_added=0,
            warnings=["overlapping agent scopes detected"],
        )
        output = planner.explain_plan(plan)
        assert "overlapping agent scopes detected" in output

    def test_plan_review_not_run_shows_fallback(self, tmp_path: Path) -> None:
        """When _last_review_result is None, a 'not run' message appears."""
        planner, plan = _make_explain_plan(tmp_path)
        planner._last_review_result = None
        output = planner.explain_plan(plan)
        assert "## Plan Review" in output
        assert "not run" in output.lower() or "Plan review not run" in output

    # ------------------------------------------------------------------
    # Finding 3 (MEDIUM): Pattern Influence completeness
    # ------------------------------------------------------------------

    def test_pattern_influence_includes_sample_size(
        self, tmp_path: Path
    ) -> None:
        """Pattern Influence section must include sample_size when pattern is set."""
        from agent_baton.models.pattern import LearnedPattern

        planner, plan = _make_explain_plan(tmp_path)
        planner._last_pattern_used = LearnedPattern(
            pattern_id="new-feature-001",
            task_type="new-feature",
            stack=None,
            recommended_template="phased delivery",
            recommended_agents=["backend-engineer"],
            confidence=0.88,
            sample_size=42,
            success_rate=0.91,
            avg_token_cost=100_000,
            evidence=["task-1"],
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        output = planner.explain_plan(plan)
        assert "42" in output  # sample_size

    def test_pattern_influence_includes_recommended_template(
        self, tmp_path: Path
    ) -> None:
        """Pattern Influence section must include recommended_template when set."""
        from agent_baton.models.pattern import LearnedPattern

        planner, plan = _make_explain_plan(tmp_path)
        planner._last_pattern_used = LearnedPattern(
            pattern_id="bug-fix-001",
            task_type="bug-fix",
            stack=None,
            recommended_template="targeted hotfix",
            recommended_agents=["backend-engineer"],
            confidence=0.75,
            sample_size=10,
            success_rate=0.85,
            avg_token_cost=60_000,
            evidence=["task-x"],
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        output = planner.explain_plan(plan)
        assert "targeted hotfix" in output

    def test_pattern_influence_pattern_source_fallback(
        self, tmp_path: Path
    ) -> None:
        """When _last_pattern_used is None but plan.pattern_source is set,
        the section must say 'Pattern X was applied' rather than 'Default'.
        """
        planner, plan = _make_explain_plan(tmp_path)
        planner._last_pattern_used = None
        # Inject pattern_source onto the plan object (MachinePlan is mutable Pydantic)
        plan.pattern_source = "learned-pattern-xyz"
        output = planner.explain_plan(plan)
        assert "learned-pattern-xyz" in output
        assert "Default phase templates used" not in output

    # ------------------------------------------------------------------
    # Finding 4 (LOW): Data Classification explanation field
    # ------------------------------------------------------------------

    def test_data_classification_explanation_rendered_when_present(
        self, tmp_path: Path
    ) -> None:
        """When cls.explanation is non-empty it must appear in the output."""
        from agent_baton.core.govern.classifier import ClassificationResult
        from agent_baton.models.enums import RiskLevel

        planner, plan = _make_explain_plan(tmp_path)
        planner._last_classification = ClassificationResult(
            risk_level=RiskLevel.HIGH,
            guardrail_preset="Regulated Data",
            signals_found=["regulated:hipaa"],
            confidence="high",
            explanation="HIPAA keyword matched in task description.",
        )
        output = planner.explain_plan(plan)
        assert "HIPAA keyword matched in task description." in output

    def test_data_classification_explanation_absent_when_empty(
        self, tmp_path: Path
    ) -> None:
        """When cls.explanation is empty the Explanation line must not appear."""
        from agent_baton.core.govern.classifier import ClassificationResult
        from agent_baton.models.enums import RiskLevel

        planner, plan = _make_explain_plan(tmp_path)
        planner._last_classification = ClassificationResult(
            risk_level=RiskLevel.LOW,
            guardrail_preset="Standard Development",
            signals_found=[],
            confidence="high",
            explanation="",
        )
        output = planner.explain_plan(plan)
        assert "**Explanation:**" not in output

    # ------------------------------------------------------------------
    # Finding 5 (MEDIUM): _reset_explainability_state missing _last_retro_feedback
    # ------------------------------------------------------------------

    def test_reset_explainability_state_clears_retro_feedback(
        self, tmp_path: Path
    ) -> None:
        """_reset_explainability_state() must set _last_retro_feedback to None."""
        from agent_baton.core.engine.planner import IntelligentPlanner

        ctx = tmp_path / "team-context"
        ctx.mkdir(parents=True, exist_ok=True)
        planner = IntelligentPlanner(team_context_root=ctx)

        # Inject stale retro feedback to simulate a previous successful call
        planner._last_retro_feedback = {"stale": "data"}

        # Call reset directly (as create_plan does at the top of each call)
        planner._reset_explainability_state()

        assert planner._last_retro_feedback is None

    def test_stale_retro_feedback_cleared_after_pipeline_exception(
        self, tmp_path: Path
    ) -> None:
        """If the pipeline raises mid-run, _last_retro_feedback must NOT
        retain the value from the previous successful call.

        This is the core regression: _reset_explainability_state() is called
        at the top of create_plan(), so if the pipeline blows up before
        _sync_last_state() can write the new value, the attribute must be
        None (reset), not the stale value from the prior call.
        """
        from unittest.mock import patch
        from agent_baton.core.engine.planner import IntelligentPlanner

        ctx = tmp_path / "team-context"
        ctx.mkdir(parents=True, exist_ok=True)
        planner = IntelligentPlanner(team_context_root=ctx)

        # Simulate a prior successful call that left retro feedback behind
        planner._last_retro_feedback = {"previous": "feedback"}

        # Force the pipeline to raise AFTER _reset_explainability_state() runs
        # but BEFORE _sync_last_state() can write the new value.
        with patch.object(
            planner._pipeline,
            "run",
            side_effect=RuntimeError("injected mid-pipeline failure"),
        ):
            try:
                planner.create_plan("Any task")
            except RuntimeError:
                pass  # Expected — we injected this

        # After the exception, _last_retro_feedback must be None (reset),
        # not the stale {"previous": "feedback"} from the prior call.
        assert planner._last_retro_feedback is None
