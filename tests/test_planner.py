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

    def test_unknown_explicit_agent_is_recorded_as_capability_gap(self, tmp_path) -> None:
        from agent_baton.core.engine.planning.planner import IntelligentPlanner
        from agent_baton.core.engine.planning.talent_factory import (
            NullTalentBuilderDispatcher,
        )

        # NullTalentBuilderDispatcher keeps this hermetic (no live `claude`
        # subprocess) -- see TestPlannerTalentFactoryDispatch below for the
        # full generation-success path with a fake dispatcher.
        # project_root=tmp_path keeps the talent-factory scratch directory
        # (and any install attempt) out of the real repo tree.
        planner = IntelligentPlanner(talent_builder_dispatcher=NullTalentBuilderDispatcher())
        plan = planner.create_plan(
            "Add retry logic to the payment webhook handler",
            agents=["database-whisperer"],
            project_root=tmp_path,
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

        # P5.2: the decision is now ACTED on (P5.1 was diagnostic-only).
        # With no live dispatcher, the one bounded generation attempt
        # fails and resolves to the deterministic generic-agent fallback
        # -- an unresolved agent request must never survive into the
        # final roster (a plan step naming a nonexistent agent would fail
        # at execution time with no diagnosis of why).
        outcomes = plan.plan_diagnostics.get("talent_factory_outcomes", [])
        assert len(outcomes) == 1
        assert outcomes[0]["status"] == "generation_failed_fallback"
        assert outcomes[0]["resolved_agent_name"]
        assert "database-whisperer" not in plan.plan_diagnostics["selected_agents"]
        assert outcomes[0]["resolved_agent_name"] in plan.plan_diagnostics["selected_agents"]

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


# ---------------------------------------------------------------------------
# IntelligentPlanner.create_plan() -- full talent-factory dispatch (P5.2)
# ---------------------------------------------------------------------------


_GENERATED_AGENT_TEMPLATE = """---
name: {name}
description: |
  Specialist agent generated for this plan.
model: sonnet
permissionMode: default
color: teal
tools: Read, Glob, Grep
created_by: talent-builder
status: draft
version: 0.1.0
---

# {title}

## Mission

You are a specialist. Do the specialist thing.

## Before Starting

1. Read this entire agent definition.

## Knowledge References

No knowledge packs required for this role yet.

## Principles

- Be rigorous.

## Anti-Patterns

- Do not fabricate results.

## Output Format

Return a summary of findings.
"""


class _FakeSuccessDispatcher:
    """Writes a valid generated-agent artifact for every request."""

    def __init__(self) -> None:
        self.call_count = 0

    def dispatch(self, request):
        from agent_baton.core.engine.planning.talent_factory import DispatchOutcome

        self.call_count += 1
        request.output_dir.mkdir(parents=True, exist_ok=True)
        name = request.gap.requested_capability
        path = request.output_dir / f"{name}.md"
        path.write_text(
            _GENERATED_AGENT_TEMPLATE.format(name=name, title=name.replace("-", " ").title()),
            encoding="utf-8",
        )
        return DispatchOutcome(success=True, candidate_paths=[path])


class TestPlannerTalentFactoryDispatch:
    """End-to-end: capability gap -> generated, installed, re-planned step.

    This is the P5.2 behavioral contract: "A permitted, real capability gap
    triggers one scoped talent-builder run whose validated artifact is
    atomically installed, loaded, and used to re-plan the unresolved work;
    disabled or skipped initialization never generates talent."
    """

    def test_generation_success_resolves_and_installs(self, tmp_path) -> None:
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        dispatcher = _FakeSuccessDispatcher()
        planner = IntelligentPlanner(talent_builder_dispatcher=dispatcher)
        (tmp_path / ".claude").mkdir()

        plan = planner.create_plan(
            "Design a data-retention audit workflow for legacy archives",
            project_root=tmp_path,
            agents=["archive-retention-specialist"],
        )

        assert dispatcher.call_count == 1, "exactly one bounded dispatch attempt"

        outcomes = plan.plan_diagnostics["talent_factory_outcomes"]
        assert len(outcomes) == 1
        assert outcomes[0]["status"] == "generated"
        assert outcomes[0]["resolved_agent_name"] == "archive-retention-specialist"

        # The generated agent's name replaces the unresolved request in the
        # final roster -- every step re-plans onto the resolved name.
        step_agents = {s.agent_name for p in plan.phases for s in p.steps}
        assert "archive-retention-specialist" in step_agents

        installed = tmp_path / ".claude" / "agents" / "archive-retention-specialist.md"
        assert installed.is_file()

        # No leftover scratch state.
        scratch_root = tmp_path / ".claude" / "team-context" / "talent-builder"
        if scratch_root.is_dir():
            assert list(scratch_root.iterdir()) == []

    def test_skip_init_never_generates_talent(self, tmp_path) -> None:
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        dispatcher = _FakeSuccessDispatcher()
        planner = IntelligentPlanner(talent_builder_dispatcher=dispatcher)
        (tmp_path / ".claude").mkdir()

        planner.create_plan(
            "Design a data-retention audit workflow for legacy archives",
            project_root=tmp_path,
            agents=["archive-retention-specialist"],
            skip_init=True,
        )

        assert dispatcher.call_count == 0, "skip_init must never dispatch talent-builder"
        assert not (tmp_path / ".claude" / "agents").exists()

    def test_disabled_policy_never_generates_talent(self, tmp_path) -> None:
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        dispatcher = _FakeSuccessDispatcher()
        planner = IntelligentPlanner(talent_builder_dispatcher=dispatcher)
        (tmp_path / ".claude").mkdir()

        planner.create_plan(
            "Design a data-retention audit workflow for legacy archives",
            project_root=tmp_path,
            agents=["archive-retention-specialist"],
            allow_talent_builder=False,
        )

        assert dispatcher.call_count == 0, "allow_talent_builder=False must never dispatch"
        assert not (tmp_path / ".claude" / "agents").exists()

    def test_retry_budget_zero_never_generates_talent(self, tmp_path) -> None:
        """Phase 5 review regression: ``talent_factory.retry_budget: 0``
        means "no generation attempts permitted" and must actually reach
        the lifecycle decision — previously the config was parsed and
        threaded into create_plan but RosterStage decided with hardcoded
        defaults, so a zero budget still dispatched and installed."""
        from agent_baton.core.config.manager import TalentFactoryConfig
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        dispatcher = _FakeSuccessDispatcher()
        planner = IntelligentPlanner(talent_builder_dispatcher=dispatcher)
        (tmp_path / ".claude").mkdir()

        plan = planner.create_plan(
            "Design a data-retention audit workflow for legacy archives",
            project_root=tmp_path,
            agents=["archive-retention-specialist"],
            talent_factory_config=TalentFactoryConfig(retry_budget=0),
        )

        assert dispatcher.call_count == 0, "retry_budget=0 must never dispatch"
        assert not (tmp_path / ".claude" / "agents").exists()
        outcomes = plan.plan_diagnostics["talent_factory_outcomes"]
        assert len(outcomes) == 1
        assert outcomes[0]["status"] == "queued_for_manager"

    def test_duplicate_requests_generate_once_and_keep_the_generated_agent(
        self, tmp_path
    ) -> None:
        """Phase 5 review regression: a duplicated ``--agents`` entry must
        not create two gaps for the same capability — previously the
        second dispatch collided with the first's freshly installed
        artifact and its collision-fallback substitution rewrote the
        successful resolution back out of the roster, so the generated
        agent was installed but never causally used."""
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        dispatcher = _FakeSuccessDispatcher()
        planner = IntelligentPlanner(talent_builder_dispatcher=dispatcher)
        (tmp_path / ".claude").mkdir()

        plan = planner.create_plan(
            "Design a data-retention audit workflow for legacy archives",
            project_root=tmp_path,
            agents=["archive-retention-specialist", "archive-retention-specialist"],
        )

        assert dispatcher.call_count == 1, "one bounded attempt per distinct capability"
        outcomes = plan.plan_diagnostics["talent_factory_outcomes"]
        assert [o["status"] for o in outcomes] == ["generated"]

        step_agents = {s.agent_name for p in plan.phases for s in p.steps}
        assert "archive-retention-specialist" in step_agents, (
            "the generated agent must remain the one the plan actually uses"
        )
        assert (tmp_path / ".claude" / "agents" / "archive-retention-specialist.md").is_file()


# ---------------------------------------------------------------------------
# Registry reload + causal re-plan use of a newly generated capability
# ---------------------------------------------------------------------------


class TestPlannerTalentFactoryRegistryReloadAndReplan:
    """``talent_factory.registry_reload: immediate`` (the default) means the
    *same* in-process AgentRegistry a plan is built against picks up a
    freshly generated agent right away. The behavioral proof is causal: a
    second ``create_plan()`` call on the same planner instance, requesting
    the same capability, must resolve it directly from the roster -- no
    second capability gap, no second talent-builder dispatch -- because
    re-planning now has the capability it previously had to generate.
    See docs/internal/talent-factory-contract.md §4 (registry_reload) and
    §11 item 5.
    """

    def test_second_plan_call_reuses_generated_agent_without_redispatch(self, tmp_path) -> None:
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        dispatcher = _FakeSuccessDispatcher()
        planner = IntelligentPlanner(talent_builder_dispatcher=dispatcher)
        (tmp_path / ".claude").mkdir()

        first_plan = planner.create_plan(
            "Design a data-retention audit workflow for legacy archives",
            project_root=tmp_path,
            agents=["archive-retention-specialist"],
        )
        assert dispatcher.call_count == 1
        first_outcomes = first_plan.plan_diagnostics["talent_factory_outcomes"]
        assert len(first_outcomes) == 1
        assert first_outcomes[0]["status"] == "generated"

        # Re-plan: the *same* capability is requested again. The gap no
        # longer exists -- the in-process registry already has the agent
        # from the first call's install -- so this must resolve directly,
        # never re-dispatching talent-builder for work already done.
        second_plan = planner.create_plan(
            "Extend the data-retention audit workflow with a new report",
            project_root=tmp_path,
            agents=["archive-retention-specialist"],
        )

        assert dispatcher.call_count == 1, (
            "a capability the registry already has must be reused, not regenerated"
        )
        assert second_plan.plan_diagnostics.get("capability_gaps", []) == []
        assert second_plan.plan_diagnostics.get("talent_factory_outcomes", []) == []
        step_agents = {s.agent_name for p in second_plan.phases for s in p.steps}
        assert "archive-retention-specialist" in step_agents


# ---------------------------------------------------------------------------
# Telemetry: routing notes + plan_diagnostics shape for a talent-factory run
# ---------------------------------------------------------------------------


class TestPlannerTalentFactoryTelemetry:
    def test_generation_outcome_is_recorded_in_routing_notes_and_diagnostics(
        self, tmp_path
    ) -> None:
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        dispatcher = _FakeSuccessDispatcher()
        planner = IntelligentPlanner(talent_builder_dispatcher=dispatcher)
        (tmp_path / ".claude").mkdir()

        plan = planner.create_plan(
            "Design a data-retention audit workflow for legacy archives",
            project_root=tmp_path,
            agents=["archive-retention-specialist"],
        )

        assert any(
            note.startswith("[talent-factory]") and "generated" in note
            for note in planner._last_routing_notes
        ), planner._last_routing_notes

        outcomes = plan.plan_diagnostics["talent_factory_outcomes"]
        assert len(outcomes) == 1
        outcome = outcomes[0]
        assert set(outcome) >= {
            "requested_capability", "kind", "action", "status",
            "resolved_agent_name", "detail", "validation_errors",
        }
        assert outcome["requested_capability"] == "archive-retention-specialist"
        assert outcome["kind"] == "missing_role"
        assert outcome["action"] == "dispatch_talent_builder"
        assert outcome["status"] == "generated"
        assert outcome["resolved_agent_name"] == "archive-retention-specialist"
        assert outcome["validation_errors"] == []

        # The explanation surface (baton plan --explain) must also carry
        # the talent-factory note -- not just the raw diagnostics dict.
        explanation = planner.explain_plan(plan)
        assert "[talent-factory]" in explanation

    def test_fallback_outcome_is_recorded_with_nonempty_detail(self, tmp_path) -> None:
        from agent_baton.core.engine.planning.planner import IntelligentPlanner
        from agent_baton.core.engine.planning.talent_factory import (
            NullTalentBuilderDispatcher,
        )

        planner = IntelligentPlanner(talent_builder_dispatcher=NullTalentBuilderDispatcher())
        plan = planner.create_plan(
            "Add retry logic to the payment webhook handler",
            agents=["database-whisperer"],
            project_root=tmp_path,
        )

        outcomes = plan.plan_diagnostics["talent_factory_outcomes"]
        assert len(outcomes) == 1
        assert outcomes[0]["status"] == "generation_failed_fallback"
        assert outcomes[0]["detail"]  # never a silent/blank explanation
        assert any(
            note.startswith("[talent-factory]") for note in planner._last_routing_notes
        )
