"""Tests for :mod:`agent_baton.core.workflow.applier`.

Pins the normative applier semantics of
docs/internal/adversarial-tdd-workflow-design.md §5 (and the stage table in
§3) against representative base plans built in ``tests/workflow/conftest.py``.

These are behaviour tests: everything is asserted through the reshaped
``MachinePlan`` (phases, steps, gates, approvals), its ``to_dict()`` output,
and the returned ``WorkflowDecisions`` record. No private helper of the
applier is touched.
"""
from __future__ import annotations

import json
import math
from typing import Any

import pytest

from agent_baton.core.config.workflow import StageOverride, WorkflowSettings
from agent_baton.core.workflow.applier import WorkflowApplier, WorkflowDecisions
from agent_baton.core.workflow.presets import ADVERSARIAL_TDD
from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep

from tests.workflow._plans import (
    build_large_team_plan,
    build_qualifying_phase_without_harvestable_steps_plan,
    build_units_plan,
    dispatch_units,
    phase_by_name,
)

# Spec §3, in engine order.
STAGE_PHASE_NAMES = [
    "Brainstorm & Spec",
    "Architecture",
    "Test Authoring",
    "Test Verification",
    "Implementation",
    "Implementation Verification",
    "Final Review",
]

# Acceptance criterion #1: per-step models fable/fable/opus/opus/sonnet/opus/fable.
STAGE_TIERS = {
    "Brainstorm & Spec": "fable",
    "Architecture": "fable",
    "Test Authoring": "opus",
    "Test Verification": "opus",
    "Implementation": "sonnet",
    "Implementation Verification": "opus",
    "Final Review": "fable",
}

STAGE_IDS = {
    "Brainstorm & Spec": "spec",
    "Architecture": "architecture",
    "Test Authoring": "test_authoring",
    "Test Verification": "test_verification",
    "Implementation": "implementation",
    "Implementation Verification": "implementation_verification",
    "Final Review": "final_review",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def apply_workflow(
    plan: MachinePlan,
    *,
    settings: WorkflowSettings | None = None,
    fallback_gate: PlanGate | None = None,
) -> WorkflowDecisions:
    """Run the applier with ``adversarial-tdd`` and default settings."""
    return WorkflowApplier().apply(
        plan,
        ADVERSARIAL_TDD,
        settings if settings is not None else WorkflowSettings(),
        fallback_gate=fallback_gate,
    )


def workflow_phases(plan: MachinePlan) -> list[PlanPhase]:
    """The seven preset stage phases (anything after them is carryover)."""
    return plan.phases[: len(STAGE_PHASE_NAMES)]


def implementation_steps(plan: MachinePlan) -> list[PlanStep]:
    return list(phase_by_name(plan, "Implementation").steps)


def descriptions(plan: MachinePlan) -> list[str]:
    return [s.task_description for s in plan.all_steps]


def entry_count(value: Any) -> int:
    """Normalise a decisions ``steps`` entry (id list or plain count)."""
    return value if isinstance(value, int) else len(value)


def assert_graph_invariants(plan: MachinePlan) -> None:
    """§5.12: the reshaped plan must satisfy the MachinePlan validators.

    ``validate_assignment=False`` means in-place mutation never re-runs the
    model validator, so re-hydrating through ``from_dict`` is what actually
    proves the output is constructible.
    """
    seen: set[str] = set()
    phase_ids: set[int] = set()
    for phase in plan.phases:
        assert phase.phase_id not in phase_ids, f"duplicate phase_id {phase.phase_id}"
        phase_ids.add(phase.phase_id)
        for index, step in enumerate(phase.steps, start=1):
            assert step.agent_name, f"step {step.step_id!r} has an empty agent_name"
            assert step.step_id not in seen, f"duplicate step_id {step.step_id!r}"
            seen.add(step.step_id)
            for dep in step.depends_on:
                assert dep in seen, (
                    f"step {step.step_id!r} forward-references {dep!r}"
                )
            assert step.step_id == f"{phase.phase_id}.{index}", (
                "§3: step ids are renumbered '<phase_id>.<n>'"
            )

    # Authoritative: re-running the model validator on a JSON round-trip.
    MachinePlan.from_dict(json.loads(json.dumps(plan.to_dict())))


def expected_reviewers(units: int, *, divisor: int = 4, cap: int = 3) -> int:
    """§5.8 reviewer-count formula, restated independently of the applier."""
    return min(cap, max(1, math.ceil(units / divisor)))


# ---------------------------------------------------------------------------
# Stage reshape (§5.4)
# ---------------------------------------------------------------------------

class TestStageReshape:
    def test_phases_are_the_seven_stages_in_order(self, base_plan: MachinePlan) -> None:
        apply_workflow(base_plan)
        assert [p.name for p in base_plan.phases] == STAGE_PHASE_NAMES

    def test_phase_ids_are_unique_and_ascending(self, base_plan: MachinePlan) -> None:
        apply_workflow(base_plan)
        ids = [p.phase_id for p in base_plan.phases]
        assert ids == sorted(ids)
        assert len(ids) == len(set(ids))

    def test_created_stages_have_their_preset_agent(self, base_plan: MachinePlan) -> None:
        apply_workflow(base_plan)
        expected = {
            "Brainstorm & Spec": "architect",
            "Architecture": "architect",
            "Test Authoring": "test-engineer",
            "Test Verification": "test-adequacy-reviewer",
            "Implementation Verification": "code-reviewer",
            "Final Review": "code-reviewer",
        }
        for phase_name, agent in expected.items():
            phase = phase_by_name(base_plan, phase_name)
            agents = {a for step in phase.steps for a, _m, _b in dispatch_units(step)}
            assert agents == {agent}, phase_name

    def test_created_stages_carry_their_preset_step_type(
        self, base_plan: MachinePlan
    ) -> None:
        expected = {
            "Brainstorm & Spec": "planning",
            "Architecture": "planning",
            "Test Authoring": "testing",
            "Test Verification": "reviewing",
            "Implementation Verification": "reviewing",
            "Final Review": "reviewing",
        }
        apply_workflow(base_plan)
        for phase_name, step_type in expected.items():
            phase = phase_by_name(base_plan, phase_name)
            assert {s.step_type for s in phase.steps} == {step_type}, phase_name

    def test_every_created_stage_has_at_least_one_step(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        for phase in workflow_phases(base_plan):
            assert phase.steps, f"{phase.name} has no steps"

    def test_briefings_mention_the_task(self, base_plan: MachinePlan) -> None:
        apply_workflow(base_plan)
        spec_phase = phase_by_name(base_plan, "Brainstorm & Spec")
        blob = " ".join(s.task_description for s in spec_phase.steps)
        assert base_plan.task_summary in blob

    def test_stage_briefings_have_no_unexpanded_placeholders(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        for step in base_plan.all_steps:
            assert "{task_summary}" not in step.task_description


# ---------------------------------------------------------------------------
# Stage scoping (§3 stages 1 and 4)
# ---------------------------------------------------------------------------

def spec_path(plan: MachinePlan) -> str:
    """The artifact stage 1 writes and stage 4 reads."""
    return f".claude/team-context/executions/{plan.task_id}/spec.md"


class TestStageScoping:
    def test_spec_step_declares_the_spec_file_as_a_deliverable(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        step = phase_by_name(base_plan, "Brainstorm & Spec").steps[0]
        assert any(spec_path(base_plan) in d for d in step.deliverables)

    def test_spec_step_briefing_names_the_spec_file(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        step = phase_by_name(base_plan, "Brainstorm & Spec").steps[0]
        assert spec_path(base_plan) in step.task_description

    def test_test_verification_context_is_only_the_spec(
        self, base_plan: MachinePlan
    ) -> None:
        # §3 stage 4: "context_files carries only the spec path" — the
        # reviewer must not be handed the implementation plan.
        apply_workflow(base_plan)
        step = phase_by_name(base_plan, "Test Verification").steps[0]
        assert step.context_files == [spec_path(base_plan)]

    def test_test_verification_briefing_points_at_the_authored_tests(
        self, base_plan: MachinePlan
    ) -> None:
        # §3 stage 4: the briefing instructs locating the tests from the
        # test-authoring step's deliverables, so those deliverables must
        # exist and be named in the reviewer's briefing.
        apply_workflow(base_plan)
        authoring = phase_by_name(base_plan, "Test Authoring").steps[0]
        verification = phase_by_name(base_plan, "Test Verification").steps[0]

        assert authoring.deliverables, "test-authoring step must declare deliverables"
        for deliverable in authoring.deliverables:
            assert deliverable in verification.task_description


# ---------------------------------------------------------------------------
# Research-support briefing guidance (§3 Notes)
# ---------------------------------------------------------------------------

class TestResearchSupportGuidance:
    def test_every_created_steps_briefing_mentions_research_support(
        self, base_plan: MachinePlan
    ) -> None:
        # §3 Notes: "stage briefings state that the orchestrator may
        # dispatch sonnet general-purpose/domain agents for research at
        # any stage" -- briefing guidance, not extra planned steps. Every
        # applier-created (non-harvested) step must carry it; the
        # automation external-verifier step (a scripted command, not an
        # agent briefing) is excluded below.
        apply_workflow(base_plan)
        for phase in workflow_phases(base_plan):
            if phase.name == "Implementation":
                continue  # harvested steps are pre-existing work, not created
            for step in phase.steps:
                if step.step_type == "automation":
                    continue
                assert "research support" in step.task_description.lower(), (
                    phase.name,
                    step.task_description,
                )

    def test_final_review_reviewer_briefings_mention_research_support(self) -> None:
        plan = build_units_plan(8)
        apply_workflow(plan)
        final = phase_by_name(plan, "Final Review")
        for step in final.steps:
            assert "research support" in step.task_description.lower()

    def test_automation_step_does_not_carry_research_support_guidance(
        self, base_plan: MachinePlan
    ) -> None:
        settings = WorkflowSettings(external_command="codex verify")
        apply_workflow(base_plan, settings=settings)
        phase = phase_by_name(base_plan, "Implementation Verification")
        automation = next(s for s in phase.steps if s.step_type == "automation")
        assert "research support" not in automation.task_description.lower()


# ---------------------------------------------------------------------------
# Model tiers + stamping (§3, §5.5, §5.10)
# ---------------------------------------------------------------------------

class TestTiersAndStamping:
    def test_model_tier_per_stage(self, base_plan: MachinePlan) -> None:
        apply_workflow(base_plan)
        for phase in workflow_phases(base_plan):
            expected = STAGE_TIERS[phase.name]
            models = {m for step in phase.steps for _a, m, _b in dispatch_units(step)}
            assert models == {expected}, f"{phase.name}: {models}"

    def test_tier_sequence_matches_acceptance_criterion(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        sequence = [
            {m for step in phase.steps for _a, m, _b in dispatch_units(step)}.pop()
            for phase in workflow_phases(base_plan)
        ]
        assert sequence == [
            "fable",
            "fable",
            "opus",
            "opus",
            "sonnet",
            "opus",
            "fable",
        ]

    def test_workflow_stage_stamped_on_every_step(self, base_plan: MachinePlan) -> None:
        apply_workflow(base_plan)
        for phase in workflow_phases(base_plan):
            expected = STAGE_IDS[phase.name]
            assert {s.workflow_stage for s in phase.steps} == {expected}, phase.name

    def test_plan_workflow_field_stamped(self, base_plan: MachinePlan) -> None:
        apply_workflow(base_plan)
        assert base_plan.workflow == "adversarial-tdd"


# ---------------------------------------------------------------------------
# Harvesting (§5.2) + field preservation (§5.5) + discard rule (§5.3)
# ---------------------------------------------------------------------------

class TestHarvesting:
    def test_only_non_review_non_planning_steps_are_harvested(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        harvested = implementation_steps(base_plan)
        assert [s.agent_name for s in harvested] == [
            "backend-engineer",
            "frontend-engineer",
        ]

    def test_reviewer_step_inside_implement_phase_is_excluded(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        assert "Review the limiter diff" not in descriptions(base_plan)

    def test_non_harvested_base_steps_are_discarded(
        self, base_plan: MachinePlan
    ) -> None:
        # §5.3: their work is assumed re-covered by the preset stages.
        apply_workflow(base_plan)
        remaining = descriptions(base_plan)
        for discarded in (
            "Sketch the limiter design",
            "Cover the limiter with tests",
            "Final read of the slice",
        ):
            assert discarded not in remaining

    def test_harvested_steps_preserve_original_order(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        assert [s.task_description for s in implementation_steps(base_plan)] == [
            "Build the token-bucket limiter",
            "Surface quota headers in the client",
        ]

    def test_harvested_step_fields_are_preserved(self, base_plan: MachinePlan) -> None:
        apply_workflow(base_plan)
        step = implementation_steps(base_plan)[0]
        assert step.agent_name == "backend-engineer"
        assert step.task_description == "Build the token-bucket limiter"
        assert step.deliverables == ["limiter module"]
        assert step.allowed_paths == ["app/api/limiter.py"]
        assert step.blocked_paths == ["migrations/"]
        assert step.context_files == ["docs/api.md"]
        assert step.expected_outcome == "Requests over the quota return 429."
        assert step.parallel_safe is True
        assert step.max_estimated_minutes == 30
        assert step.mcp_servers == ["postgres"]

    def test_harvested_step_knowledge_is_preserved(
        self, base_plan: MachinePlan
    ) -> None:
        # §5.5 is an *inverted* rule: everything survives except the four
        # named exceptions, so planner-resolved knowledge must come through.
        apply_workflow(base_plan)
        step = implementation_steps(base_plan)[0]
        assert [k.document_name for k in step.knowledge] == ["api-conventions"]
        assert step.knowledge[0].pack_name == "coding-conventions"

    def test_harvested_step_interactivity_is_preserved(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        step = implementation_steps(base_plan)[0]
        assert step.interactive is True
        assert step.max_turns == 4

    def test_harvested_step_type_is_not_restamped(self, base_plan: MachinePlan) -> None:
        # §5.5: step_type is stamped only on applier-created steps.
        apply_workflow(base_plan)
        assert [s.step_type for s in implementation_steps(base_plan)] == [
            "developing",
            "developing",
        ]

    def test_harvested_steps_are_retiered_to_sonnet(
        self, base_plan: MachinePlan
    ) -> None:
        # Base models are opus/haiku, so this cannot pass vacuously.
        apply_workflow(base_plan)
        assert {s.model for s in implementation_steps(base_plan)} == {"sonnet"}

    def test_intra_implementation_dependencies_are_rekeyed(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        first, second = implementation_steps(base_plan)
        assert second.depends_on == [first.step_id]

    def test_cross_phase_dependency_on_discarded_step_is_dropped(
        self, base_plan: MachinePlan
    ) -> None:
        # Base step 2.1 depended on the discarded Design step 1.1 (§3: "no
        # cross-phase step deps").
        apply_workflow(base_plan)
        first = implementation_steps(base_plan)[0]
        assert first.depends_on == []

    def test_harvested_step_ids_are_renumbered_under_the_new_phase(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        phase = phase_by_name(base_plan, "Implementation")
        assert [s.step_id for s in phase.steps] == [
            f"{phase.phase_id}.1",
            f"{phase.phase_id}.2",
        ]

    def test_harvested_steps_are_stamped_implementation(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        assert {s.workflow_stage for s in implementation_steps(base_plan)} == {
            "implementation"
        }


# ---------------------------------------------------------------------------
# Harvest predicate (§5.2 explicit-only archetype mapping)
# ---------------------------------------------------------------------------

class TestHarvestPredicate:
    def test_all_implement_like_phases_are_harvested_in_order(
        self, harvest_predicate_plan: MachinePlan
    ) -> None:
        apply_workflow(harvest_predicate_plan)
        assert [
            s.task_description for s in implementation_steps(harvest_predicate_plan)
        ] == [
            "Repair the quota reset arithmetic",          # Fix
            "Compile the release artifact",               # Build
            "Add the throttle middleware to the API layer",  # Implement: API Layer
            "Wire the quota store into the backend",      # Backend Implementation
            "Provision the redis quota cache",            # Prepare
            "Roll back the broken quota migration",       # Remediate
        ]

    @pytest.mark.parametrize(
        ("phase_name", "description"),
        [
            ("Fix", "Repair the quota reset arithmetic"),
            ("Build", "Compile the release artifact"),
            ("Implement: API Layer", "Add the throttle middleware to the API layer"),
            ("Backend Implementation", "Wire the quota store into the backend"),
            ("Prepare", "Provision the redis quota cache"),
            ("Remediate", "Roll back the broken quota migration"),
        ],
    )
    def test_phase_name_is_harvested(
        self, harvest_predicate_plan: MachinePlan, phase_name: str, description: str
    ) -> None:
        # "Implement: API Layer" (colon strip) and "Backend Implementation"
        # (last-word key + the implementation->implement alias) are the two
        # rows an explicit full-name table lookup would miss; "Prepare" and
        # "Remediate" pin the PREPARATION / REMEDIATION archetypes.
        apply_workflow(harvest_predicate_plan)
        harvested = [
            s.task_description for s in implementation_steps(harvest_predicate_plan)
        ]
        assert description in harvested, f"{phase_name} must be harvested"

    def test_planning_step_inside_an_implement_like_phase_is_excluded(
        self, harvest_predicate_plan: MachinePlan
    ) -> None:
        # The step-type exclusion applies within a qualifying phase too.
        apply_workflow(harvest_predicate_plan)
        assert (
            "Draft the middleware interface"
            not in descriptions(harvest_predicate_plan)
        )

    def test_cross_phase_dependency_between_harvested_steps_is_rekeyed(
        self, harvest_predicate_plan: MachinePlan
    ) -> None:
        # Build's step depended on Fix's step; both are harvested, so the
        # edge must survive re-keyed rather than being dropped.
        apply_workflow(harvest_predicate_plan)
        fix_step, build_step = implementation_steps(harvest_predicate_plan)[:2]
        assert build_step.depends_on == [fix_step.step_id]

    def test_security_review_phase_is_not_harvested(
        self, harvest_predicate_plan: MachinePlan
    ) -> None:
        # Last-word keying maps "Security Review" -> "review" -> REVIEW.
        apply_workflow(harvest_predicate_plan)
        assert (
            "Patch the header leak found in triage"
            not in descriptions(harvest_predicate_plan)
        )

    def test_unrecognized_phase_name_is_not_harvested(
        self, harvest_predicate_plan: MachinePlan
    ) -> None:
        # §5.2: the phase_archetype() fallback to IMPLEMENTATION must NOT be
        # used for the harvest predicate.
        apply_workflow(harvest_predicate_plan)
        assert (
            "Frobnicate the widget registry"
            not in descriptions(harvest_predicate_plan)
        )

    def test_developing_fallback_when_no_phase_qualifies(
        self, developing_fallback_plan: MachinePlan
    ) -> None:
        # Fallback (a): harvest all step_type == "developing" steps anywhere.
        decisions = apply_workflow(developing_fallback_plan)
        harvested = implementation_steps(developing_fallback_plan)
        assert [s.task_description for s in harvested] == [
            "Ship the limiter behind a flag"
        ]
        assert decisions.implementation_units == 1

    def test_developing_fallback_ignores_non_developing_steps(
        self, developing_fallback_plan: MachinePlan
    ) -> None:
        apply_workflow(developing_fallback_plan)
        remaining = descriptions(developing_fallback_plan)
        assert "Chart the current throttling behaviour" not in remaining
        assert "Check the rollout runbook" not in remaining

    def test_fallback_fires_when_qualifying_phase_yields_no_harvested_steps(
        self,
    ) -> None:
        # §5.2(a) amended: fallback (a) gates on "no harvested steps", not
        # "no qualifying phase" -- an "Implementation" phase that qualifies
        # but contains only a reviewing step must still trigger fallback
        # (a), harvesting the "Design" phase's developing step, rather than
        # synthesizing (fallback (b)) or harvesting nothing.
        plan = build_qualifying_phase_without_harvestable_steps_plan()
        decisions = apply_workflow(plan)

        harvested = implementation_steps(plan)
        assert [s.task_description for s in harvested] == [
            "Prototype the limiter approach"
        ]
        assert decisions.implementation_units == 1
        # The qualifying phase's reviewing step must still be discarded.
        assert "Review the limiter approach" not in descriptions(plan)
        assert_graph_invariants(plan)


# ---------------------------------------------------------------------------
# Synthesized fallback (§5.2 fallback b)
# ---------------------------------------------------------------------------

class TestSynthesizedImplementationStep:
    def test_single_step_is_synthesized(
        self, no_implementation_plan: MachinePlan
    ) -> None:
        apply_workflow(no_implementation_plan)
        assert len(implementation_steps(no_implementation_plan)) == 1

    def test_synthesized_step_uses_the_implement_fallback_agent(
        self, no_implementation_plan: MachinePlan
    ) -> None:
        apply_workflow(no_implementation_plan)
        step = implementation_steps(no_implementation_plan)[0]
        assert step.agent_name == "backend-engineer"

    def test_synthesized_step_is_briefed_from_the_task_summary(
        self, no_implementation_plan: MachinePlan
    ) -> None:
        apply_workflow(no_implementation_plan)
        step = implementation_steps(no_implementation_plan)[0]
        assert no_implementation_plan.task_summary in step.task_description

    def test_synthesized_step_is_sonnet_tier_and_stamped(
        self, no_implementation_plan: MachinePlan
    ) -> None:
        apply_workflow(no_implementation_plan)
        step = implementation_steps(no_implementation_plan)[0]
        assert step.model == "sonnet"
        assert step.workflow_stage == "implementation"

    def test_synthesized_step_counts_as_one_unit(
        self, no_implementation_plan: MachinePlan
    ) -> None:
        decisions = apply_workflow(no_implementation_plan)
        assert decisions.implementation_units == 1
        assert decisions.final_review_reviewers == 1


# ---------------------------------------------------------------------------
# Team steps (§5.5 recursive re-tier, §5.8 unit counting)
# ---------------------------------------------------------------------------

class TestTeamSteps:
    def test_team_step_survives_the_harvest(self, team_step_plan: MachinePlan) -> None:
        apply_workflow(team_step_plan)
        team_steps = [s for s in implementation_steps(team_step_plan) if s.team]
        assert len(team_steps) == 1
        # §5.5 ruling: member_id values are preserved VERBATIM — they are not
        # re-keyed to the renumbered step id, because member `depends_on`
        # references member ids and re-keying is not free. So these still read
        # "1.1.a"/"1.1.b" even though the step itself is renumbered.
        assert [m.member_id for m in team_steps[0].team] == ["1.1.a", "1.1.b"]

    def test_nested_member_ids_are_also_preserved(
        self, team_step_plan: MachinePlan
    ) -> None:
        apply_workflow(team_step_plan)
        step = next(s for s in implementation_steps(team_step_plan) if s.team)
        lead = next(m for m in step.team if m.role == "lead")
        assert [m.member_id for m in lead.sub_team] == ["1.1.a.i"]

    def test_team_member_models_are_retiered(self, team_step_plan: MachinePlan) -> None:
        apply_workflow(team_step_plan)
        step = next(s for s in implementation_steps(team_step_plan) if s.team)
        assert {m.model for m in step.team} == {"sonnet"}

    def test_nested_sub_team_models_are_retiered(
        self, team_step_plan: MachinePlan
    ) -> None:
        apply_workflow(team_step_plan)
        step = next(s for s in implementation_steps(team_step_plan) if s.team)
        lead = next(m for m in step.team if m.role == "lead")
        assert lead.sub_team, "sub_team must be preserved"
        assert {m.model for m in lead.sub_team} == {"sonnet"}

    def test_team_membership_details_are_preserved(
        self, team_step_plan: MachinePlan
    ) -> None:
        apply_workflow(team_step_plan)
        step = next(s for s in implementation_steps(team_step_plan) if s.team)
        assert [m.agent_name for m in step.team] == [
            "backend-engineer",
            "frontend-engineer",
        ]
        assert step.synthesis is not None
        assert step.synthesis.strategy == "concatenate"

    def test_team_step_contributes_len_team_units(
        self, team_step_plan: MachinePlan
    ) -> None:
        # 2 team members + 1 plain step = 3 units (§5.8).
        decisions = apply_workflow(team_step_plan)
        assert decisions.implementation_units == 3


# ---------------------------------------------------------------------------
# Automation / task steps (§5.5 model-stamp exception)
# ---------------------------------------------------------------------------

class TestAutomationAndTaskSteps:
    def test_all_three_step_types_are_harvested(
        self, automation_step_plan: MachinePlan
    ) -> None:
        apply_workflow(automation_step_plan)
        assert [s.step_type for s in implementation_steps(automation_step_plan)] == [
            "developing",
            "automation",
            "task",
        ]

    def test_automation_and_task_models_are_untouched(
        self, automation_step_plan: MachinePlan
    ) -> None:
        apply_workflow(automation_step_plan)
        _dev, automation, task = implementation_steps(automation_step_plan)
        assert automation.model == "haiku"
        assert task.model == "haiku"

    def test_developing_sibling_is_still_retiered(
        self, automation_step_plan: MachinePlan
    ) -> None:
        apply_workflow(automation_step_plan)
        developing = implementation_steps(automation_step_plan)[0]
        assert developing.model == "sonnet"

    def test_automation_command_and_timeout_are_preserved(
        self, automation_step_plan: MachinePlan
    ) -> None:
        apply_workflow(automation_step_plan)
        automation = implementation_steps(automation_step_plan)[1]
        assert automation.command == "make migrate"
        assert automation.timeout_seconds == 120

    def test_automation_dependency_is_rekeyed(
        self, automation_step_plan: MachinePlan
    ) -> None:
        apply_workflow(automation_step_plan)
        developing, automation, _task = implementation_steps(automation_step_plan)
        assert automation.depends_on == [developing.step_id]


# ---------------------------------------------------------------------------
# Gates and approvals (§5.6)
# ---------------------------------------------------------------------------

class TestGatesAndApprovals:
    def test_first_test_or_build_gate_moves_to_implementation(
        self, base_plan: MachinePlan
    ) -> None:
        # The Implement phase's *lint* gate must not win over the Test
        # phase's test gate.
        apply_workflow(base_plan)
        gate = phase_by_name(base_plan, "Implementation").gate
        assert gate is not None
        assert gate.gate_type == "test"
        assert gate.command == "pytest tests/api"
        assert gate.fail_on == ["test failure"]

    def test_other_stage_phases_have_no_gate(self, base_plan: MachinePlan) -> None:
        apply_workflow(base_plan)
        for phase in workflow_phases(base_plan):
            if phase.name == "Implementation":
                continue
            assert phase.gate is None, phase.name

    def test_build_gate_also_satisfies_the_first_gate_rule(
        self, build_gate_plan: MachinePlan
    ) -> None:
        # §5.6 says "test/build" — a plan whose only qualifying gate is a
        # build gate must not fall through to fallback_gate.
        apply_workflow(
            build_gate_plan,
            fallback_gate=PlanGate(gate_type="test", command="SHOULD-NOT-BE-USED"),
        )
        gate = phase_by_name(build_gate_plan, "Implementation").gate
        assert gate is not None
        assert gate.gate_type == "build"
        assert gate.command == "python -m build"

    def test_fallback_gate_used_when_base_plan_has_none(
        self, gateless_plan: MachinePlan
    ) -> None:
        fallback = PlanGate(
            gate_type="test", command="pytest", description="Full suite."
        )
        apply_workflow(gateless_plan, fallback_gate=fallback)
        gate = phase_by_name(gateless_plan, "Implementation").gate
        assert gate is not None
        assert gate.command == "pytest"

    def test_no_gate_at_all_when_base_and_fallback_are_empty(
        self, gateless_plan: MachinePlan
    ) -> None:
        apply_workflow(gateless_plan, fallback_gate=None)
        assert phase_by_name(gateless_plan, "Implementation").gate is None

    def test_base_gate_wins_over_fallback(self, base_plan: MachinePlan) -> None:
        apply_workflow(
            base_plan, fallback_gate=PlanGate(gate_type="test", command="pytest -q")
        )
        gate = phase_by_name(base_plan, "Implementation").gate
        assert gate is not None
        assert gate.command == "pytest tests/api"

    def test_final_review_approval_is_the_or_of_base_phases(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        final = phase_by_name(base_plan, "Final Review")
        assert final.approval_required is True

    def test_no_approval_when_no_base_phase_required_one(
        self, gateless_plan: MachinePlan
    ) -> None:
        apply_workflow(gateless_plan)
        assert phase_by_name(gateless_plan, "Final Review").approval_required is False

    def test_intermediate_stage_phases_do_not_require_approval(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        for phase in workflow_phases(base_plan):
            if phase.name == "Final Review":
                continue
            assert phase.approval_required is False, phase.name


# ---------------------------------------------------------------------------
# Final-review fan-out (§5.8)
# ---------------------------------------------------------------------------

class TestFinalReviewFanOut:
    @pytest.mark.parametrize(
        ("units", "reviewers"),
        [(1, 1), (4, 1), (5, 2), (8, 2), (12, 3), (20, 3)],
    )
    def test_reviewer_count(self, units: int, reviewers: int) -> None:
        plan = build_units_plan(units)
        decisions = apply_workflow(plan)

        assert decisions.implementation_units == units
        assert decisions.final_review_reviewers == reviewers
        assert reviewers == expected_reviewers(units)

        final = phase_by_name(plan, "Final Review")
        assert sum(len(dispatch_units(s)) for s in final.steps) == reviewers

    def test_cap_is_three_reviewers(self) -> None:
        plan = build_units_plan(40)
        decisions = apply_workflow(plan)
        assert decisions.final_review_reviewers == 3

    def test_single_unit_gets_a_single_reviewer(self) -> None:
        plan = build_units_plan(1)
        decisions = apply_workflow(plan)
        assert decisions.final_review_reviewers == 1

    def test_reviewer_briefings_partition_the_harvested_steps(self) -> None:
        plan = build_units_plan(8)
        apply_workflow(plan)

        impl_descriptions = [s.task_description for s in implementation_steps(plan)]
        final = phase_by_name(plan, "Final Review")
        briefings = [b for step in final.steps for _a, _m, b in dispatch_units(step)]
        assert len(briefings) == 2

        groups = [
            {i for i, desc in enumerate(impl_descriptions) if desc in briefing}
            for briefing in briefings
        ]
        # Every implementation step is reviewed by exactly one reviewer.
        assert set().union(*groups) == set(range(8))
        assert groups[0].isdisjoint(groups[1])
        # Contiguous partition.
        for group in groups:
            ordered = sorted(group)
            assert ordered == list(range(ordered[0], ordered[-1] + 1))

    def test_each_briefing_embeds_exactly_its_own_groups_allowed_paths(self) -> None:
        # Per-reviewer, not plan-wide: a reviewer that embedded *every*
        # step's paths would defeat the point of partitioning the slice.
        plan = build_units_plan(8)
        apply_workflow(plan)

        impl = implementation_steps(plan)
        all_paths = {p for s in impl for p in s.allowed_paths}
        final = phase_by_name(plan, "Final Review")
        briefings = [b for step in final.steps for _a, _m, b in dispatch_units(step)]

        for briefing in briefings:
            group = [s for s in impl if s.task_description in briefing]
            assert group, "each reviewer must own at least one step"
            expected = {p for s in group for p in s.allowed_paths}
            embedded = {p for p in all_paths if p in briefing}
            assert embedded == expected

    def test_fanout_divisor_and_cap_are_configurable(self) -> None:
        plan = build_units_plan(8)
        settings = WorkflowSettings(
            final_review_fanout_divisor=2, final_review_max_reviewers=5
        )
        decisions = apply_workflow(plan, settings=settings)
        assert decisions.final_review_reviewers == 4

    def test_configured_cap_is_respected(self) -> None:
        plan = build_units_plan(20)
        settings = WorkflowSettings(
            final_review_fanout_divisor=2, final_review_max_reviewers=2
        )
        decisions = apply_workflow(plan, settings=settings)
        assert decisions.final_review_reviewers == 2


# ---------------------------------------------------------------------------
# Final-review fan-out by dispatch unit, not harvested step count (§5.8
# amended) -- a single team step must never be clamped to 1 reviewer.
# ---------------------------------------------------------------------------

class TestFinalReviewFanOutByDispatchUnit:
    def test_single_team_step_with_eight_members_yields_two_reviewers(self) -> None:
        plan = build_large_team_plan()
        decisions = apply_workflow(plan)

        assert decisions.implementation_units == 8
        assert decisions.final_review_reviewers == 2
        # Exactly one harvested STEP -- the formula must not clamp to 1
        # reviewer just because there is only one step to harvest from.
        assert len(implementation_steps(plan)) == 1

        final = phase_by_name(plan, "Final Review")
        assert sum(len(dispatch_units(s)) for s in final.steps) == 2

    def test_member_dispatch_units_partition_contiguously_and_disjointly(
        self,
    ) -> None:
        plan = build_large_team_plan()
        apply_workflow(plan)

        impl_step = implementation_steps(plan)[0]
        member_descriptions = [m.task_description for m in impl_step.team]
        assert len(member_descriptions) == 8

        final = phase_by_name(plan, "Final Review")
        briefings = [b for step in final.steps for _a, _m, b in dispatch_units(step)]
        assert len(briefings) == 2

        groups = [
            {i for i, desc in enumerate(member_descriptions) if desc in briefing}
            for briefing in briefings
        ]
        # Every team member is reviewed by exactly one reviewer.
        assert set().union(*groups) == set(range(8))
        assert groups[0].isdisjoint(groups[1])
        # Contiguous partition.
        for group in groups:
            ordered = sorted(group)
            assert ordered == list(range(ordered[0], ordered[-1] + 1))

    def test_reviewer_count_scales_with_configured_divisor(self) -> None:
        plan = build_large_team_plan()
        settings = WorkflowSettings(
            final_review_fanout_divisor=2, final_review_max_reviewers=5
        )
        decisions = apply_workflow(plan, settings=settings)
        # ceil(8 / 2) = 4 reviewers from a SINGLE harvested step.
        assert decisions.final_review_reviewers == 4

    def test_graph_invariants_hold_with_large_team_fanout(self) -> None:
        plan = build_large_team_plan()
        apply_workflow(plan)
        assert_graph_invariants(plan)


# ---------------------------------------------------------------------------
# Audit carryover (§3, §5.7)
# ---------------------------------------------------------------------------

class TestAuditCarryover:
    def test_audit_phase_is_appended_after_final_review(
        self, audit_carryover_plan: MachinePlan
    ) -> None:
        apply_workflow(audit_carryover_plan)
        assert [p.name for p in audit_carryover_plan.phases] == [
            *STAGE_PHASE_NAMES,
            "Audit",
        ]

    def test_carryover_steps_are_preserved(
        self, audit_carryover_plan: MachinePlan
    ) -> None:
        apply_workflow(audit_carryover_plan)
        audit = phase_by_name(audit_carryover_plan, "Audit")
        assert len(audit.steps) == 1
        step = audit.steps[0]
        assert step.agent_name == "auditor"
        assert step.task_description == (
            "Confirm the retention controls are auditable"
        )
        assert step.deliverables == ["audit memo"]
        assert step.step_type == "reviewing"

    def test_carryover_step_model_is_untouched(
        self, audit_carryover_plan: MachinePlan
    ) -> None:
        # "preserved verbatim" — no re-tiering for carried-over phases.
        apply_workflow(audit_carryover_plan)
        assert phase_by_name(audit_carryover_plan, "Audit").steps[0].model == "opus"

    def test_carryover_steps_are_stamped_carryover(
        self, audit_carryover_plan: MachinePlan
    ) -> None:
        apply_workflow(audit_carryover_plan)
        audit = phase_by_name(audit_carryover_plan, "Audit")
        assert {s.workflow_stage for s in audit.steps} == {"carryover"}

    def test_carryover_gate_and_approval_are_preserved(
        self, audit_carryover_plan: MachinePlan
    ) -> None:
        apply_workflow(audit_carryover_plan)
        audit = phase_by_name(audit_carryover_plan, "Audit")
        assert audit.approval_required is True
        assert audit.approval_description == "Compliance officer sign-off required."
        assert audit.gate is not None
        assert audit.gate.gate_type == "review"
        assert audit.gate.command == "baton evidence verify"
        assert audit.gate.fail_on == ["missing evidence"]

    def test_carryover_step_ids_are_renumbered(
        self, audit_carryover_plan: MachinePlan
    ) -> None:
        apply_workflow(audit_carryover_plan)
        audit = phase_by_name(audit_carryover_plan, "Audit")
        assert [s.step_id for s in audit.steps] == [f"{audit.phase_id}.1"]

    def test_carryover_recorded_in_decisions(
        self, audit_carryover_plan: MachinePlan
    ) -> None:
        decisions = apply_workflow(audit_carryover_plan)
        assert decisions.carried_over_phases == ["Audit"]

    def test_plan_without_auditor_steps_carries_nothing_over(
        self, base_plan: MachinePlan
    ) -> None:
        decisions = apply_workflow(base_plan)
        assert decisions.carried_over_phases == []
        assert len(base_plan.phases) == len(STAGE_PHASE_NAMES)

    def test_auditor_inside_a_harvested_phase_does_not_trigger_carryover(
        self, auditor_in_implement_phase_plan: MachinePlan
    ) -> None:
        # §5.7 scopes carryover to NON-harvested phases. The "Implement"
        # phase here is harvested, so its auditor step is discarded like any
        # other reviewing step rather than resurrecting the whole phase.
        decisions = apply_workflow(auditor_in_implement_phase_plan)

        assert decisions.carried_over_phases == []
        assert [p.name for p in auditor_in_implement_phase_plan.phases] == (
            STAGE_PHASE_NAMES
        )
        assert "auditor" not in auditor_in_implement_phase_plan.all_agents

    def test_graph_invariants_hold_with_carryover(
        self, audit_carryover_plan: MachinePlan
    ) -> None:
        apply_workflow(audit_carryover_plan)
        assert_graph_invariants(audit_carryover_plan)


# ---------------------------------------------------------------------------
# External verifier (§5.9)
# ---------------------------------------------------------------------------

class TestExternalVerifier:
    def test_no_automation_step_when_command_is_empty(
        self, base_plan: MachinePlan
    ) -> None:
        decisions = apply_workflow(base_plan)
        phase = phase_by_name(base_plan, "Implementation Verification")
        assert [s.step_type for s in phase.steps] == ["reviewing"]
        assert decisions.external_verifier is False

    def test_automation_step_appended_when_command_is_set(
        self, base_plan: MachinePlan
    ) -> None:
        settings = WorkflowSettings(external_command="gemini verify --spec spec.md")
        decisions = apply_workflow(base_plan, settings=settings)

        phase = phase_by_name(base_plan, "Implementation Verification")
        assert [s.step_type for s in phase.steps] == ["reviewing", "automation"]
        assert decisions.external_verifier is True

    def test_automation_step_shape(self, base_plan: MachinePlan) -> None:
        settings = WorkflowSettings(
            external_command="gemini verify --spec spec.md",
            external_timeout_seconds=900,
        )
        apply_workflow(base_plan, settings=settings)

        step = phase_by_name(base_plan, "Implementation Verification").steps[-1]
        assert step.agent_name == "task-runner"
        assert step.command == "gemini verify --spec spec.md"
        assert step.timeout_seconds == 900
        assert step.workflow_stage == "implementation_verification"

    def test_automation_step_model_is_left_at_the_default(
        self, base_plan: MachinePlan
    ) -> None:
        # §5.9: "model left default — automation dispatch bypasses it", i.e.
        # it is NOT stamped with the stage's opus tier.
        settings = WorkflowSettings(external_command="codex verify")
        apply_workflow(base_plan, settings=settings)
        step = phase_by_name(base_plan, "Implementation Verification").steps[-1]
        assert step.model == "sonnet"

    def test_default_external_timeout_is_stamped(self, base_plan: MachinePlan) -> None:
        settings = WorkflowSettings(external_command="codex verify")
        apply_workflow(base_plan, settings=settings)
        step = phase_by_name(base_plan, "Implementation Verification").steps[-1]
        assert step.timeout_seconds == 1800


# ---------------------------------------------------------------------------
# Stage overrides (§5.11)
# ---------------------------------------------------------------------------

class TestStageOverrides:
    def test_agent_override_for_a_created_stage(self, base_plan: MachinePlan) -> None:
        settings = WorkflowSettings(
            stages={"spec": StageOverride(agent="subject-matter-expert")}
        )
        apply_workflow(base_plan, settings=settings)
        phase = phase_by_name(base_plan, "Brainstorm & Spec")
        assert {a for s in phase.steps for a, _m, _b in dispatch_units(s)} == {
            "subject-matter-expert"
        }

    def test_model_override_for_a_created_stage(self, base_plan: MachinePlan) -> None:
        settings = WorkflowSettings(
            stages={"test_authoring": StageOverride(model="sonnet")}
        )
        apply_workflow(base_plan, settings=settings)
        phase = phase_by_name(base_plan, "Test Authoring")
        assert {m for s in phase.steps for _a, m, _b in dispatch_units(s)} == {"sonnet"}

    def test_model_override_applies_to_the_harvesting_stage(
        self, base_plan: MachinePlan
    ) -> None:
        settings = WorkflowSettings(
            stages={"implementation": StageOverride(model="haiku")}
        )
        apply_workflow(base_plan, settings=settings)
        assert {s.model for s in implementation_steps(base_plan)} == {"haiku"}

    def test_agent_override_is_ignored_for_the_harvesting_stage(
        self, base_plan: MachinePlan
    ) -> None:
        # §5.11: for the harvesting stage only `model` applies — agents come
        # from the base plan.
        settings = WorkflowSettings(
            stages={"implementation": StageOverride(agent="data-engineer")}
        )
        apply_workflow(base_plan, settings=settings)
        assert [s.agent_name for s in implementation_steps(base_plan)] == [
            "backend-engineer",
            "frontend-engineer",
        ]

    def test_decisions_record_the_effective_tier(
        self, base_plan: MachinePlan
    ) -> None:
        # §4: the stage entry's "model" is the EFFECTIVE tier, i.e. after
        # overrides — not the preset default.
        settings = WorkflowSettings(
            stages={"test_authoring": StageOverride(model="sonnet")}
        )
        decisions = apply_workflow(base_plan, settings=settings)
        entry = next(e for e in decisions.stages if e["stage_id"] == "test_authoring")
        assert entry["model"] == "sonnet"

    def test_decisions_record_the_effective_agent(
        self, base_plan: MachinePlan
    ) -> None:
        settings = WorkflowSettings(
            stages={"spec": StageOverride(agent="subject-matter-expert")}
        )
        decisions = apply_workflow(base_plan, settings=settings)
        entry = next(e for e in decisions.stages if e["stage_id"] == "spec")
        assert set(entry["agents"]) == {"subject-matter-expert"}

    def test_unrelated_stages_keep_their_preset_defaults(
        self, base_plan: MachinePlan
    ) -> None:
        settings = WorkflowSettings(stages={"spec": StageOverride(model="haiku")})
        apply_workflow(base_plan, settings=settings)
        assert {
            m
            for s in phase_by_name(base_plan, "Final Review").steps
            for _a, m, _b in dispatch_units(s)
        } == {"fable"}


# ---------------------------------------------------------------------------
# Decisions record (§4, §5.10)
# ---------------------------------------------------------------------------

class TestDecisionsRecord:
    def test_decisions_summarise_the_reshape(self, base_plan: MachinePlan) -> None:
        decisions = apply_workflow(base_plan)
        assert decisions.workflow == "adversarial-tdd"
        assert decisions.implementation_units == 2
        assert decisions.final_review_reviewers == 1
        assert decisions.external_verifier is False
        assert decisions.carried_over_phases == []

    def test_one_stage_entry_per_preset_stage_in_order(
        self, base_plan: MachinePlan
    ) -> None:
        decisions = apply_workflow(base_plan)
        assert [entry["stage_id"] for entry in decisions.stages] == [
            stage.stage_id for stage in ADVERSARIAL_TDD.stages
        ]

    def test_stage_entry_shape(self, base_plan: MachinePlan) -> None:
        decisions = apply_workflow(base_plan)
        for entry, phase in zip(decisions.stages, workflow_phases(base_plan)):
            # §4: the entry is a superset — extra diagnostic keys are allowed.
            assert {
                "stage_id",
                "phase_name",
                "agents",
                "model",
                "steps",
            } <= set(entry)
            assert entry["phase_name"] == phase.name
            assert entry["model"] == STAGE_TIERS[phase.name]
            assert entry_count(entry["steps"]) == len(phase.steps)

    def test_stage_entry_agents_match_the_phase(self, base_plan: MachinePlan) -> None:
        decisions = apply_workflow(base_plan)
        impl_entry = next(
            e for e in decisions.stages if e["stage_id"] == "implementation"
        )
        assert set(impl_entry["agents"]) == {"backend-engineer", "frontend-engineer"}

    def test_decisions_recorded_in_plan_diagnostics(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        record = base_plan.plan_diagnostics["workflow"]
        assert isinstance(record, dict)
        assert record["workflow"] == "adversarial-tdd"
        assert record["implementation_units"] == 2
        assert record["final_review_reviewers"] == 1
        assert record["external_verifier"] is False
        assert record["carried_over_phases"] == []

    def test_plan_diagnostics_record_is_json_serialisable(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        json.dumps(base_plan.plan_diagnostics["workflow"])

    def test_plan_diagnostics_record_survives_to_dict(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        payload = json.loads(json.dumps(base_plan.to_dict()))
        assert payload["plan_diagnostics"]["workflow"]["workflow"] == "adversarial-tdd"


# ---------------------------------------------------------------------------
# Purity + idempotency (§2 decision #4, §5.1)
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_second_apply_does_not_change_the_plan(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        first = json.loads(json.dumps(base_plan.to_dict()))

        apply_workflow(base_plan)
        second = json.loads(json.dumps(base_plan.to_dict()))

        assert second == first

    def test_second_apply_does_not_duplicate_phases(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        apply_workflow(base_plan)
        assert [p.name for p in base_plan.phases] == STAGE_PHASE_NAMES

    def test_second_apply_does_not_duplicate_steps(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        step_ids = [s.step_id for s in base_plan.all_steps]
        apply_workflow(base_plan)
        assert [s.step_id for s in base_plan.all_steps] == step_ids

    def test_second_apply_recomputes_the_same_decisions(
        self, base_plan: MachinePlan
    ) -> None:
        first = apply_workflow(base_plan)
        second = apply_workflow(base_plan)
        assert second == first

    def test_idempotent_with_carryover(
        self, audit_carryover_plan: MachinePlan
    ) -> None:
        apply_workflow(audit_carryover_plan)
        first = json.loads(json.dumps(audit_carryover_plan.to_dict()))
        apply_workflow(audit_carryover_plan)
        assert json.loads(json.dumps(audit_carryover_plan.to_dict())) == first

    def test_guard_ignores_different_settings_on_the_second_apply(
        self, base_plan: MachinePlan
    ) -> None:
        """The differentiator between a real §5.1 no-op guard and an
        accidental fixed point.

        Re-applying with the *same* inputs can pass by coincidence: a
        stateless applier that re-derives everything from the already-shaped
        plan may land on the same output. Re-applying with settings that
        *would* change the output (external_command now set) can only leave
        the plan untouched if the guard actually short-circuits.
        """
        apply_workflow(base_plan)
        first = json.loads(json.dumps(base_plan.to_dict()))

        apply_workflow(
            base_plan, settings=WorkflowSettings(external_command="codex verify")
        )
        second = json.loads(json.dumps(base_plan.to_dict()))

        assert second == first
        assert (
            second["plan_diagnostics"]["workflow"]
            == first["plan_diagnostics"]["workflow"]
        )

    def test_guard_does_not_append_a_late_external_verifier(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(base_plan)
        apply_workflow(
            base_plan, settings=WorkflowSettings(external_command="codex verify")
        )

        phase = phase_by_name(base_plan, "Implementation Verification")
        assert [s.step_type for s in phase.steps] == ["reviewing"]
        assert all(s.command == "" for s in phase.steps)

    def test_guard_recomputes_decisions_from_the_shaped_plan(
        self, base_plan: MachinePlan
    ) -> None:
        # §5.1: decisions are recomputed from the already-shaped plan, so they
        # must describe the plan as it IS, not the settings just passed in.
        apply_workflow(base_plan)
        decisions = apply_workflow(
            base_plan, settings=WorkflowSettings(external_command="codex verify")
        )
        assert decisions.external_verifier is False

    def test_idempotent_with_external_verifier(self, base_plan: MachinePlan) -> None:
        settings = WorkflowSettings(external_command="codex verify")
        apply_workflow(base_plan, settings=settings)
        first = json.loads(json.dumps(base_plan.to_dict()))
        apply_workflow(base_plan, settings=settings)
        assert json.loads(json.dumps(base_plan.to_dict())) == first


# ---------------------------------------------------------------------------
# Serialisation round-trip (§2 decision #5)
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_new_fields_survive_from_dict_to_dict(self, base_plan: MachinePlan) -> None:
        apply_workflow(base_plan)
        reloaded = MachinePlan.from_dict(json.loads(json.dumps(base_plan.to_dict())))

        assert reloaded.workflow == "adversarial-tdd"
        assert [s.workflow_stage for s in reloaded.all_steps] == [
            s.workflow_stage for s in base_plan.all_steps
        ]

    def test_reloaded_plan_has_the_same_shape(self, base_plan: MachinePlan) -> None:
        apply_workflow(base_plan)
        reloaded = MachinePlan.from_dict(json.loads(json.dumps(base_plan.to_dict())))
        assert reloaded.to_dict() == base_plan.to_dict()

    def test_markdown_renders_the_workflow(self, base_plan: MachinePlan) -> None:
        apply_workflow(base_plan)
        assert "adversarial-tdd" in base_plan.to_markdown()


# ---------------------------------------------------------------------------
# Graph invariants (§5.12)
# ---------------------------------------------------------------------------

class TestGraphInvariants:
    @pytest.mark.parametrize(
        "fixture_name",
        [
            "base_plan",
            "team_step_plan",
            "harvest_predicate_plan",
            "developing_fallback_plan",
            "no_implementation_plan",
            "audit_carryover_plan",
            "automation_step_plan",
            "gateless_plan",
            "auditor_in_implement_phase_plan",
            "build_gate_plan",
        ],
    )
    def test_output_satisfies_plan_graph_invariants(
        self, request: pytest.FixtureRequest, fixture_name: str
    ) -> None:
        plan: MachinePlan = request.getfixturevalue(fixture_name)
        apply_workflow(plan)
        assert_graph_invariants(plan)

    def test_output_with_external_verifier_is_valid(
        self, base_plan: MachinePlan
    ) -> None:
        apply_workflow(
            base_plan, settings=WorkflowSettings(external_command="codex verify")
        )
        assert_graph_invariants(base_plan)

    def test_output_with_multiple_reviewers_is_valid(self) -> None:
        plan = build_units_plan(20)
        apply_workflow(plan)
        assert_graph_invariants(plan)
