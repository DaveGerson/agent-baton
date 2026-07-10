"""Tests for agent_baton planning layer.

PlanBuilder (agent_baton.core.orchestration.plan) was removed in Phase 1 of
the re-architecture.  All PlanBuilder tests have been deleted accordingly.

IntelligentPlanner tests live in test_intelligent_planner.py.

Talent-factory capability-gap model + lifecycle tests (Phase 5.1, see
docs/internal/talent-factory-contract.md) live below.
"""
from __future__ import annotations

import pytest

from agent_baton.core.engine.planning.capability_gap import (
    CapabilityGap,
    CapabilityGapEvidence,
    CapabilityGapKind,
    NON_GENERABLE_CAPABILITIES,
    PermittedArtifactType,
    TalentLifecycleAction,
    decide_talent_lifecycle,
    detect_missing_knowledge_gap,
    detect_missing_role_gap,
    detect_weak_description_gap,
)


# ---------------------------------------------------------------------------
# CapabilityGap / CapabilityGapEvidence — model invariants
# ---------------------------------------------------------------------------


class TestCapabilityGapEvidence:
    def test_requires_non_empty_source(self) -> None:
        with pytest.raises(ValueError):
            CapabilityGapEvidence(source="  ", detail="something")

    def test_requires_non_empty_detail(self) -> None:
        with pytest.raises(ValueError):
            CapabilityGapEvidence(source="roster_stage", detail="")

    def test_to_dict_round_trips_fields(self) -> None:
        ev = CapabilityGapEvidence(source="roster_stage", detail="no match")
        assert ev.to_dict() == {"source": "roster_stage", "detail": "no match"}


class TestCapabilityGap:
    def test_rejects_gap_without_evidence(self) -> None:
        """A gap with no evidence is a bug, not a valid model state."""
        with pytest.raises(ValueError):
            CapabilityGap(
                requested_capability="database-whisperer",
                kind=CapabilityGapKind.MISSING_ROLE,
                evidence=(),
            )

    def test_rejects_empty_requested_capability(self) -> None:
        with pytest.raises(ValueError):
            CapabilityGap(
                requested_capability="   ",
                kind=CapabilityGapKind.MISSING_ROLE,
                evidence=(CapabilityGapEvidence(source="x", detail="y"),),
            )

    def test_missing_role_defaults_to_agent_artifact(self) -> None:
        gap = CapabilityGap(
            requested_capability="database-whisperer",
            kind=CapabilityGapKind.MISSING_ROLE,
            evidence=(CapabilityGapEvidence(source="roster_stage", detail="no match"),),
        )
        assert gap.permitted_artifacts == (PermittedArtifactType.AGENT,)
        assert gap.fallback  # non-empty default fallback text

    def test_missing_knowledge_defaults_to_knowledge_pack_artifact(self) -> None:
        gap = CapabilityGap(
            requested_capability="backend-engineer",
            kind=CapabilityGapKind.MISSING_KNOWLEDGE,
            evidence=(CapabilityGapEvidence(source="knowledge_resolver", detail="no pack"),),
        )
        assert gap.permitted_artifacts == (PermittedArtifactType.KNOWLEDGE_PACK,)

    def test_weak_description_permits_no_artifacts_by_default(self) -> None:
        gap = CapabilityGap(
            requested_capability="fix it",
            kind=CapabilityGapKind.WEAK_TASK_DESCRIPTION,
            evidence=(CapabilityGapEvidence(source="classification", detail="2 words"),),
        )
        assert gap.permitted_artifacts == ()

    def test_explicit_permitted_artifacts_not_overridden(self) -> None:
        """A caller who explicitly asks for a skill keeps that override."""
        gap = CapabilityGap(
            requested_capability="deploy-runbook",
            kind=CapabilityGapKind.MISSING_ROLE,
            evidence=(CapabilityGapEvidence(source="user_request", detail="explicit skill ask"),),
            permitted_artifacts=(PermittedArtifactType.SKILL,),
        )
        assert gap.permitted_artifacts == (PermittedArtifactType.SKILL,)

    def test_to_dict_shape(self) -> None:
        gap = CapabilityGap(
            requested_capability="database-whisperer",
            kind=CapabilityGapKind.MISSING_ROLE,
            evidence=(CapabilityGapEvidence(source="roster_stage", detail="no match"),),
        )
        d = gap.to_dict()
        assert d["requested_capability"] == "database-whisperer"
        assert d["kind"] == "missing_role"
        assert d["evidence"] == [{"source": "roster_stage", "detail": "no match"}]
        assert d["permitted_artifacts"] == ["agent"]
        assert isinstance(d["fallback"], str) and d["fallback"]


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


class TestDetectMissingRoleGap:
    def test_known_agent_produces_no_gap(self) -> None:
        assert detect_missing_role_gap(
            "backend-engineer", known_agents={"backend-engineer", "architect"}
        ) is None

    def test_known_flavored_agent_produces_no_gap(self) -> None:
        """Flavored variants reduce to base name before the known-agents check."""
        assert detect_missing_role_gap(
            "backend-engineer--python", known_agents={"backend-engineer"}
        ) is None

    def test_unknown_agent_produces_missing_role_gap(self) -> None:
        gap = detect_missing_role_gap(
            "database-whisperer", known_agents={"backend-engineer", "architect"}
        )
        assert gap is not None
        assert gap.kind == CapabilityGapKind.MISSING_ROLE
        assert gap.requested_capability == "database-whisperer"
        assert len(gap.evidence) == 1
        assert "database-whisperer" in gap.evidence[0].detail


class TestDetectWeakDescriptionGap:
    def test_sufficient_words_produces_no_gap(self) -> None:
        assert detect_weak_description_gap("Add retry logic to the payment webhook handler") is None

    def test_too_few_words_produces_weak_description_gap(self) -> None:
        gap = detect_weak_description_gap("fix it")
        assert gap is not None
        assert gap.kind == CapabilityGapKind.WEAK_TASK_DESCRIPTION
        assert gap.permitted_artifacts == ()

    def test_empty_summary_produces_gap_with_placeholder_capability(self) -> None:
        gap = detect_weak_description_gap("   ")
        assert gap is not None
        assert gap.requested_capability == "(empty task summary)"


class TestDetectMissingKnowledgeGap:
    def test_always_returns_a_gap(self) -> None:
        gap = detect_missing_knowledge_gap("backend-engineer", domain="acme-billing-system")
        assert gap.kind == CapabilityGapKind.MISSING_KNOWLEDGE
        assert gap.requested_capability == "backend-engineer"
        assert gap.permitted_artifacts == (PermittedArtifactType.KNOWLEDGE_PACK,)


# ---------------------------------------------------------------------------
# decide_talent_lifecycle — bounded, policy-controlled lifecycle
# ---------------------------------------------------------------------------


def _missing_role_gap(name: str = "database-whisperer") -> CapabilityGap:
    return CapabilityGap(
        requested_capability=name,
        kind=CapabilityGapKind.MISSING_ROLE,
        evidence=(CapabilityGapEvidence(source="roster_stage", detail="no match"),),
    )


class TestDecideTalentLifecycle:
    def test_default_policy_dispatches_talent_builder(self) -> None:
        decision = decide_talent_lifecycle(_missing_role_gap())
        assert decision.action == TalentLifecycleAction.DISPATCH_TALENT_BUILDER

    def test_weak_description_always_requests_clarification(self) -> None:
        """Overrides even a maximally permissive policy -- never generates."""
        gap = CapabilityGap(
            requested_capability="fix it",
            kind=CapabilityGapKind.WEAK_TASK_DESCRIPTION,
            evidence=(CapabilityGapEvidence(source="classification", detail="2 words"),),
        )
        decision = decide_talent_lifecycle(
            gap,
            allow_talent_builder=True,
            skip_init=False,
            retry_budget=99,
        )
        assert decision.action == TalentLifecycleAction.REQUEST_CLARIFICATION

    def test_talent_builder_can_never_generate_itself(self) -> None:
        """Structural recursion guard -- independent of every policy knob."""
        gap = _missing_role_gap("talent-builder")
        decision = decide_talent_lifecycle(
            gap,
            allow_talent_builder=True,
            skip_init=False,
            retry_budget=99,
            max_recursion_depth=99,
        )
        assert decision.action == TalentLifecycleAction.FALLBACK_GENERIC_AGENT
        assert "talent-builder" in decision.gap.requested_capability

    def test_flavored_talent_builder_name_also_blocked(self) -> None:
        gap = _missing_role_gap("talent-builder--regulated")
        decision = decide_talent_lifecycle(gap)
        assert decision.action == TalentLifecycleAction.FALLBACK_GENERIC_AGENT

    def test_non_generable_capabilities_contains_talent_builder(self) -> None:
        assert "talent-builder" in NON_GENERABLE_CAPABILITIES

    def test_recursion_depth_exceeding_ceiling_queues_for_manager(self) -> None:
        decision = decide_talent_lifecycle(
            _missing_role_gap(),
            recursion_depth=1,
            max_recursion_depth=0,
        )
        assert decision.action == TalentLifecycleAction.QUEUE_FOR_MANAGER
        assert "recursion" in decision.reason

    def test_skip_init_falls_back_without_generating(self) -> None:
        decision = decide_talent_lifecycle(_missing_role_gap(), skip_init=True)
        assert decision.action == TalentLifecycleAction.FALLBACK_GENERIC_AGENT
        assert "skip-init" in decision.reason or "skip_init" in decision.reason

    def test_allow_talent_builder_false_falls_back(self) -> None:
        decision = decide_talent_lifecycle(_missing_role_gap(), allow_talent_builder=False)
        assert decision.action == TalentLifecycleAction.FALLBACK_GENERIC_AGENT
        assert "allow_talent_builder" in decision.reason

    def test_retry_budget_exhausted_queues_for_manager(self) -> None:
        decision = decide_talent_lifecycle(
            _missing_role_gap(), attempts_used=1, retry_budget=1
        )
        assert decision.action == TalentLifecycleAction.QUEUE_FOR_MANAGER
        assert "retry budget" in decision.reason

    def test_retry_budget_not_yet_exhausted_dispatches(self) -> None:
        decision = decide_talent_lifecycle(
            _missing_role_gap(), attempts_used=0, retry_budget=1
        )
        assert decision.action == TalentLifecycleAction.DISPATCH_TALENT_BUILDER

    def test_no_permitted_artifacts_falls_back(self) -> None:
        gap = CapabilityGap(
            requested_capability="database-whisperer",
            kind=CapabilityGapKind.MISSING_ROLE,
            evidence=(CapabilityGapEvidence(source="roster_stage", detail="no match"),),
            permitted_artifacts=(),
        )
        decision = decide_talent_lifecycle(gap)
        assert decision.action == TalentLifecycleAction.FALLBACK_GENERIC_AGENT

    def test_decision_to_dict_shape(self) -> None:
        decision = decide_talent_lifecycle(_missing_role_gap())
        d = decision.to_dict()
        assert d["action"] == "dispatch_talent_builder"
        assert isinstance(d["reason"], str) and d["reason"]
        assert d["gap"]["requested_capability"] == "database-whisperer"

    def test_check_order_skip_init_beats_retry_budget_exhaustion(self) -> None:
        """skip_init is an explicit opt-out and should short-circuit before
        the (less specific) retry-budget check is even evaluated."""
        decision = decide_talent_lifecycle(
            _missing_role_gap(),
            skip_init=True,
            attempts_used=5,
            retry_budget=1,
        )
        assert decision.action == TalentLifecycleAction.FALLBACK_GENERIC_AGENT
        assert "skip" in decision.reason.lower()


# ---------------------------------------------------------------------------
# Integration: IntelligentPlanner represents capability gaps end-to-end
# ---------------------------------------------------------------------------


class TestPlannerCapabilityGapIntegration:
    """The planner can represent an evidence-backed capability gap and
    apply the bounded lifecycle -- this is the expected_outcome behavioral
    contract for P5.1 (docs/internal/talent-factory-contract.md)."""

    def test_unknown_explicit_agent_is_recorded_as_capability_gap(self) -> None:
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        planner = IntelligentPlanner()
        plan = planner.create_plan(
            "Add retry logic to the payment webhook handler",
            agents=["database-whisperer"],
        )
        gaps = plan.plan_diagnostics.get("capability_gaps", [])
        assert len(gaps) == 1
        assert gaps[0]["requested_capability"] == "database-whisperer"
        assert gaps[0]["kind"] == "missing_role"
        assert gaps[0]["evidence"]  # evidence-backed, never empty

        decisions = plan.plan_diagnostics.get("talent_lifecycle_decisions", [])
        assert len(decisions) == 1
        # Default policy (allow_talent_builder=True, skip_init=False)
        # dispatches talent-builder for a first-generation missing-role gap.
        assert decisions[0]["action"] == "dispatch_talent_builder"

        # The gap is diagnostic-only at plan time -- it must not mutate the
        # roster the caller explicitly asked for.
        assert "database-whisperer" in plan.plan_diagnostics["selected_agents"]

    def test_skip_init_falls_back_instead_of_dispatching(self) -> None:
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        planner = IntelligentPlanner()
        plan = planner.create_plan(
            "Add retry logic to the payment webhook handler",
            agents=["database-whisperer"],
            skip_init=True,
        )
        decisions = plan.plan_diagnostics.get("talent_lifecycle_decisions", [])
        assert len(decisions) == 1
        assert decisions[0]["action"] == "fallback_generic_agent"

    def test_allow_talent_builder_false_falls_back(self) -> None:
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        planner = IntelligentPlanner()
        plan = planner.create_plan(
            "Add retry logic to the payment webhook handler",
            agents=["database-whisperer"],
            allow_talent_builder=False,
        )
        decisions = plan.plan_diagnostics.get("talent_lifecycle_decisions", [])
        assert len(decisions) == 1
        assert decisions[0]["action"] == "fallback_generic_agent"

    def test_known_agent_produces_no_capability_gap(self) -> None:
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        planner = IntelligentPlanner()
        plan = planner.create_plan(
            "Add retry logic to the payment webhook handler",
            agents=["backend-engineer"],
        )
        assert plan.plan_diagnostics.get("capability_gaps", []) == []

    def test_requesting_talent_builder_itself_never_recurses(self) -> None:
        """Explicitly requesting talent-builder is not a gap at all (it's a
        known agent) -- but if it were somehow unresolved, the lifecycle
        guard still blocks recursive generation unconditionally."""
        gap = _missing_role_gap("talent-builder")
        decision = decide_talent_lifecycle(gap, allow_talent_builder=True, skip_init=False)
        assert decision.action == TalentLifecycleAction.FALLBACK_GENERIC_AGENT

    def test_default_create_plan_calls_unaffected(self) -> None:
        """Backward compatibility: omitting skip_init/allow_talent_builder
        behaves exactly as before this change for a plan with no gaps."""
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        planner = IntelligentPlanner()
        plan = planner.create_plan("Add retry logic to the payment webhook handler")
        assert plan.plan_diagnostics.get("capability_gaps") == []
