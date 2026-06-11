"""Tests for multi-expert Review-phase fan-out in EnrichmentStage.

When the resolved roster carries two or more distinct reviewer-class agents,
``_ensure_review_phase`` builds the terminal Review phase as a *team step*:
one ``TeamMember`` per reviewer (role="reviewer", concern-scoped task) with a
``concatenate`` ``SynthesisSpec``.  A single reviewer keeps the existing
single-agent step.
"""
from __future__ import annotations

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.stages.enrichment import EnrichmentStage
from agent_baton.models.enums import RiskLevel
from agent_baton.models.execution import PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_services():
    from unittest.mock import MagicMock
    from agent_baton.core.engine.planning.planner import IntelligentPlanner

    planner = IntelligentPlanner()
    services = MagicMock()
    services.registry = planner._registry
    services.bead_store = None
    services.project_config = None
    return services


def _draft(resolved_agents: list[str], risk: RiskLevel) -> PlanDraft:
    draft = PlanDraft(task_summary="ship the auth refactor")
    draft.risk_level_enum = risk
    draft.resolved_agents = resolved_agents
    draft.plan_phases = [
        PlanPhase(
            phase_id=1,
            name="Implement",
            steps=[PlanStep(step_id="1.1", agent_name="backend-engineer",
                            task_description="build it")],
        )
    ]
    return draft


def _review_phase(draft: PlanDraft) -> PlanPhase | None:
    for p in draft.plan_phases:
        if p.name.lower() == "review":
            return p
    return None


# ---------------------------------------------------------------------------
# Multi-reviewer → team step
# ---------------------------------------------------------------------------

class TestMultiReviewerFanOut:
    def test_two_reviewers_produce_team_step(self):
        draft = _draft(
            ["backend-engineer", "code-reviewer", "security-reviewer"],
            RiskLevel.HIGH,
        )
        EnrichmentStage()._ensure_review_phase(draft, _make_services())

        review = _review_phase(draft)
        assert review is not None
        step = review.steps[0]
        assert len(step.team) == 2
        assert {m.agent_name for m in step.team} == {
            "code-reviewer", "security-reviewer",
        }

    def test_team_members_have_reviewer_role(self):
        draft = _draft(
            ["backend-engineer", "code-reviewer", "security-reviewer"],
            RiskLevel.CRITICAL,
        )
        EnrichmentStage()._ensure_review_phase(draft, _make_services())

        step = _review_phase(draft).steps[0]
        assert all(m.role == "reviewer" for m in step.team)

    def test_synthesis_is_concatenate(self):
        draft = _draft(
            ["code-reviewer", "security-reviewer"], RiskLevel.HIGH,
        )
        EnrichmentStage()._ensure_review_phase(draft, _make_services())

        step = _review_phase(draft).steps[0]
        assert step.synthesis is not None
        assert step.synthesis.strategy == "concatenate"

    def test_member_ids_follow_n_m_letter_convention(self):
        draft = _draft(
            ["code-reviewer", "security-reviewer"], RiskLevel.HIGH,
        )
        EnrichmentStage()._ensure_review_phase(draft, _make_services())

        review = _review_phase(draft)
        step = review.steps[0]
        ids = sorted(m.member_id for m in step.team)
        assert ids == [f"{step.step_id}.a", f"{step.step_id}.b"]

    def test_members_carry_distinct_concern_descriptions(self):
        draft = _draft(
            ["code-reviewer", "security-reviewer"], RiskLevel.HIGH,
        )
        EnrichmentStage()._ensure_review_phase(draft, _make_services())

        step = _review_phase(draft).steps[0]
        descs = {m.agent_name: m.task_description for m in step.team}
        assert "security" in descs["security-reviewer"].lower()
        assert descs["code-reviewer"] != descs["security-reviewer"]

    def test_auditor_excluded_from_review_fanout(self):
        # auditor is a reviewer-class agent but owns its own Audit phase; it
        # must NOT be folded into the code-review fan-out.
        draft = _draft(
            ["code-reviewer", "security-reviewer", "auditor"], RiskLevel.HIGH,
        )
        EnrichmentStage()._ensure_review_phase(draft, _make_services())

        step = _review_phase(draft).steps[0]
        assert "auditor" not in {m.agent_name for m in step.team}


# ---------------------------------------------------------------------------
# Single reviewer → unchanged single-agent step
# ---------------------------------------------------------------------------

class TestSingleReviewerNoTeam:
    def test_single_reviewer_is_not_a_team_step(self):
        draft = _draft(["backend-engineer", "code-reviewer"], RiskLevel.HIGH)
        EnrichmentStage()._ensure_review_phase(draft, _make_services())

        review = _review_phase(draft)
        assert review is not None
        assert all(not s.team for s in review.steps)

    def test_no_reviewers_falls_back_to_single_code_reviewer(self):
        draft = _draft(["backend-engineer"], RiskLevel.CRITICAL)
        EnrichmentStage()._ensure_review_phase(draft, _make_services())

        review = _review_phase(draft)
        assert review is not None
        assert all(not s.team for s in review.steps)


# ---------------------------------------------------------------------------
# Guard conditions
# ---------------------------------------------------------------------------

class TestReviewFanOutGuards:
    def test_low_risk_injects_no_review_phase(self):
        draft = _draft(
            ["code-reviewer", "security-reviewer"], RiskLevel.LOW,
        )
        EnrichmentStage()._ensure_review_phase(draft, _make_services())
        assert _review_phase(draft) is None

    def test_existing_review_phase_is_not_replaced(self):
        draft = _draft(
            ["code-reviewer", "security-reviewer"], RiskLevel.HIGH,
        )
        draft.plan_phases.append(
            PlanPhase(
                phase_id=2, name="Review",
                steps=[PlanStep(step_id="2.1", agent_name="code-reviewer",
                                task_description="existing review")],
            )
        )
        EnrichmentStage()._ensure_review_phase(draft, _make_services())

        review = _review_phase(draft)
        # The pre-existing single-agent review phase is left intact.
        assert not review.steps[0].team
        assert review.steps[0].task_description == "existing review"
