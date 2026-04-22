"""Tests for the extended :class:`TeamMember` — sub_team and synthesis.

sub_team and synthesis are optional fields on TeamMember.  Loading a
pre-change plan.json must yield empty sub_team and None synthesis — this
is the backward-compatibility contract.  A validator rejects sub_team on
non-lead members.
"""
from __future__ import annotations

import pytest

from agent_baton.models.execution import (
    PlanPhase,
    PlanStep,
    MachinePlan,
    SynthesisSpec,
    TeamMember,
)


class TestTeamMemberBackwardCompat:
    def test_from_dict_missing_sub_team_yields_empty_list(self) -> None:
        """Old plan.json entries without sub_team load cleanly."""
        old = {
            "member_id": "1.1.a",
            "agent_name": "backend-engineer",
            "role": "implementer",
            "task_description": "do it",
            "model": "sonnet",
            "depends_on": [],
            "deliverables": [],
        }
        m = TeamMember.from_dict(old)
        assert m.sub_team == []
        assert m.synthesis is None

    def test_to_dict_omits_sub_team_when_empty(self) -> None:
        """No spurious sub_team key in serialized output when empty."""
        m = TeamMember(member_id="1.1.a", agent_name="be")
        d = m.to_dict()
        assert "sub_team" not in d
        assert "synthesis" not in d


class TestTeamMemberSubTeam:
    def test_lead_with_sub_team_serializes(self) -> None:
        lead = TeamMember(
            member_id="1.1.a",
            agent_name="architect",
            role="lead",
            sub_team=[
                TeamMember(member_id="1.1.a.b", agent_name="be"),
                TeamMember(member_id="1.1.a.c", agent_name="te"),
            ],
            synthesis=SynthesisSpec(strategy="merge_files"),
        )
        d = lead.to_dict()
        assert "sub_team" in d
        assert len(d["sub_team"]) == 2
        assert d["synthesis"]["strategy"] == "merge_files"

    def test_roundtrip_preserves_sub_team_and_synthesis(self) -> None:
        original = TeamMember(
            member_id="1.1.a",
            agent_name="architect",
            role="lead",
            sub_team=[TeamMember(member_id="1.1.a.b", agent_name="be")],
            synthesis=SynthesisSpec(strategy="agent_synthesis",
                                    synthesis_agent="code-reviewer"),
        )
        restored = TeamMember.from_dict(original.to_dict())
        assert restored.role == "lead"
        assert len(restored.sub_team) == 1
        assert restored.sub_team[0].member_id == "1.1.a.b"
        assert restored.synthesis is not None
        assert restored.synthesis.strategy == "agent_synthesis"

    def test_recursive_sub_team_serializes(self) -> None:
        """Nested sub-teams (lead inside a sub-team) roundtrip."""
        inner_lead = TeamMember(
            member_id="1.1.a.b",
            agent_name="architect",
            role="lead",
            sub_team=[TeamMember(member_id="1.1.a.b.c", agent_name="be")],
        )
        outer_lead = TeamMember(
            member_id="1.1.a",
            agent_name="architect",
            role="lead",
            sub_team=[inner_lead],
        )
        restored = TeamMember.from_dict(outer_lead.to_dict())
        assert restored.sub_team[0].sub_team[0].member_id == "1.1.a.b.c"


class TestTeamMemberValidator:
    def test_sub_team_on_non_lead_rejected(self) -> None:
        bad = TeamMember(
            member_id="1.1.a",
            agent_name="be",
            role="implementer",  # not lead
            sub_team=[TeamMember(member_id="1.1.a.b", agent_name="te")],
        )
        with pytest.raises(ValueError, match="sub_team"):
            bad.validate()

    def test_sub_team_on_lead_passes(self) -> None:
        ok = TeamMember(
            member_id="1.1.a",
            agent_name="architect",
            role="lead",
            sub_team=[TeamMember(member_id="1.1.a.b", agent_name="be")],
        )
        ok.validate()  # no exception

    def test_empty_sub_team_on_non_lead_passes(self) -> None:
        """A plain implementer is fine as long as sub_team is empty."""
        ok = TeamMember(member_id="1.1.a", agent_name="be", role="implementer")
        ok.validate()


class TestPlanLoadsLegacyFormat:
    def test_legacy_plan_json_loads_without_sub_team(self) -> None:
        """A plan.json predating this change loads cleanly via MachinePlan.from_dict."""
        legacy_plan_dict = {
            "task_id": "t1",
            "task_summary": "legacy test",
            "phases": [
                {
                    "phase_id": 1,
                    "name": "Implementation",
                    "steps": [
                        {
                            "step_id": "1.1",
                            "agent_name": "team",
                            "task_description": "legacy team step",
                            "team": [
                                {
                                    "member_id": "1.1.a",
                                    "agent_name": "backend-engineer",
                                    "role": "lead",
                                    "task_description": "work",
                                },
                                {
                                    "member_id": "1.1.b",
                                    "agent_name": "test-engineer",
                                    "role": "implementer",
                                    "task_description": "test",
                                },
                            ],
                        },
                    ],
                }
            ],
        }
        plan = MachinePlan.from_dict(legacy_plan_dict)
        assert len(plan.phases) == 1
        step = plan.phases[0].steps[0]
        assert len(step.team) == 2
        for member in step.team:
            assert member.sub_team == []
            assert member.synthesis is None
