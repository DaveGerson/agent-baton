"""Tests for parallel fan-out via research_concerns in DecompositionStage /
EnrichmentStage.

Covers:
1. research_concerns on the draft trigger concern-splitting for Audit phases.
2. Concern-split steps carry parallel_safe=True.
3. Without research_concerns a natural-language audit task is NOT split.
4. "audit" and "assess" are in the splittable phase-name set.
5. The regex-based (numbered-marker) path still works independently.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.stages.enrichment import EnrichmentStage
from agent_baton.models.execution import PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _audit_phase(phase_id: int = 2) -> PlanPhase:
    """Return a single-step Audit phase for use as test input."""
    return PlanPhase(
        phase_id=phase_id,
        name="Audit",
        steps=[
            PlanStep(
                step_id=f"{phase_id}.1",
                agent_name="auditor",
                task_description="Audit the system",
            )
        ],
    )


def _assess_phase(phase_id: int = 2) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name="Assess",
        steps=[
            PlanStep(
                step_id=f"{phase_id}.1",
                agent_name="architect",
                task_description="Assess the system",
            )
        ],
    )


_FIVE_CONCERNS: list[tuple[str, str]] = [
    ("1", "Planning engine and classifier pipeline"),
    ("2", "CLI surface and command routing"),
    ("3", "Execution engine and state machine"),
    ("4", "Knowledge system and resolver"),
    ("5", "Learning pipeline and pattern learner"),
]

_TWO_CONCERNS: list[tuple[str, str]] = [
    ("1", "Authentication subsystem"),
    ("2", "Authorization subsystem"),
]


# ---------------------------------------------------------------------------
# Test 1 — research_concerns split an Audit phase into parallel steps
# ---------------------------------------------------------------------------

class TestResearchConcernsSplitAuditPhase:
    def test_five_concerns_produce_five_steps(self) -> None:
        """When research_concerns are provided the Audit phase splits into
        one parallel step per concern."""
        phases = [_audit_phase()]
        stage = EnrichmentStage()

        stage._apply_approval_gates(
            phases,
            risk_level_enum=None,
            task_summary="Audit all components of the system",
            resolved_agents=["auditor", "architect"],
            research_concerns=_FIVE_CONCERNS,
        )

        audit = phases[0]
        assert len(audit.steps) == 5, (
            f"Expected 5 parallel steps, got {len(audit.steps)}"
        )

    def test_split_steps_are_parallel_safe(self) -> None:
        """Every concern-split step must carry parallel_safe=True."""
        phases = [_audit_phase()]
        stage = EnrichmentStage()

        stage._apply_approval_gates(
            phases,
            risk_level_enum=None,
            task_summary="Audit all components of the system",
            resolved_agents=["auditor", "architect"],
            research_concerns=_FIVE_CONCERNS,
        )

        assert all(s.parallel_safe for s in phases[0].steps), (
            "All concern-split steps should be parallel_safe=True"
        )

    def test_split_returns_phase_id_in_set(self) -> None:
        """_apply_approval_gates must include the split phase_id in its
        return value so downstream callers know which phases were split."""
        phases = [_audit_phase(phase_id=3)]
        stage = EnrichmentStage()

        split_ids = stage._apply_approval_gates(
            phases,
            risk_level_enum=None,
            task_summary="Audit all components of the system",
            resolved_agents=["auditor"],
            research_concerns=_FIVE_CONCERNS,
        )

        assert 3 in split_ids

    def test_two_concerns_split_correctly(self) -> None:
        """Splitting also works for the minimum meaningful case (2 concerns)."""
        phases = [_audit_phase()]
        stage = EnrichmentStage()

        stage._apply_approval_gates(
            phases,
            risk_level_enum=None,
            task_summary="Audit auth components",
            resolved_agents=["auditor"],
            research_concerns=_TWO_CONCERNS,
        )

        assert len(phases[0].steps) == 2


# ---------------------------------------------------------------------------
# Test 2 — Without research_concerns, no splitting occurs
# ---------------------------------------------------------------------------

class TestNoResearchConcernsNoSplitting:
    def test_natural_language_audit_stays_single_step(self) -> None:
        """Without research_concerns and without numbered markers in the
        summary, the Audit phase must NOT be split."""
        phases = [_audit_phase()]
        stage = EnrichmentStage()

        stage._apply_approval_gates(
            phases,
            risk_level_enum=None,
            task_summary="Audit all components of the system",
            resolved_agents=["auditor"],
            research_concerns=None,
        )

        assert len(phases[0].steps) == 1, (
            "Without research_concerns the step count must stay at 1"
        )

    def test_empty_list_research_concerns_no_split(self) -> None:
        """An explicitly empty list is falsy and must not trigger splitting."""
        phases = [_audit_phase()]
        stage = EnrichmentStage()

        stage._apply_approval_gates(
            phases,
            risk_level_enum=None,
            task_summary="Audit all components of the system",
            resolved_agents=["auditor"],
            research_concerns=[],
        )

        assert len(phases[0].steps) == 1

    def test_research_concerns_ignored_when_regex_finds_markers(self) -> None:
        """When the task summary itself contains numbered markers the regex
        path wins and research_concerns are not used as a fallback."""
        phases = [_audit_phase()]
        stage = EnrichmentStage()

        # Summary has 3 numbered markers — regex fires; research_concerns
        # provided too, but they should be irrelevant (regex already found
        # concerns, so _concerns is truthy before the fallback check).
        stage._apply_approval_gates(
            phases,
            risk_level_enum=None,
            task_summary=(
                "Audit: (1) auth layer (2) data pipeline (3) API surface"
            ),
            resolved_agents=["auditor"],
            # research_concerns is intentionally different — 5 items
            research_concerns=_FIVE_CONCERNS,
        )

        # The regex found 3 markers; only 3 steps should exist.
        assert len(phases[0].steps) == 3


# ---------------------------------------------------------------------------
# Test 3 — "audit" and "assess" are splittable phase names
# ---------------------------------------------------------------------------

class TestAuditAssessPhaseNamesAreSplittable:
    def test_audit_phase_name_is_splittable(self) -> None:
        """The 'audit' phase name (case-insensitive) must be in the
        set of phases eligible for concern-splitting."""
        phases = [_audit_phase()]
        stage = EnrichmentStage()

        stage._apply_approval_gates(
            phases,
            risk_level_enum=None,
            task_summary="Audit all components",
            resolved_agents=["auditor"],
            research_concerns=_FIVE_CONCERNS,
        )

        # If "audit" were not in the splittable set, steps would remain 1.
        assert len(phases[0].steps) == 5

    def test_assess_phase_name_is_splittable(self) -> None:
        """The 'assess' phase name must also be splittable."""
        phases = [_assess_phase()]
        stage = EnrichmentStage()

        stage._apply_approval_gates(
            phases,
            risk_level_enum=None,
            task_summary="Assess all components",
            resolved_agents=["architect"],
            research_concerns=_FIVE_CONCERNS,
        )

        assert len(phases[0].steps) == 5

    def test_implement_phase_still_splittable(self) -> None:
        """Regression: the pre-existing 'implement' phase must still split."""
        phases = [
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Implement the system",
                    )
                ],
            )
        ]
        stage = EnrichmentStage()

        stage._apply_approval_gates(
            phases,
            risk_level_enum=None,
            task_summary="Implement the system",
            resolved_agents=["backend-engineer", "frontend-engineer"],
            research_concerns=_TWO_CONCERNS,
        )

        assert len(phases[0].steps) == 2

    @pytest.mark.parametrize("phase_name", ["fix", "draft", "migrate"])
    def test_legacy_phase_names_still_splittable(self, phase_name: str) -> None:
        """Regression: all pre-existing splittable phase names must still work."""
        phases = [
            PlanPhase(
                phase_id=1,
                name=phase_name.capitalize(),
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description=f"{phase_name} the system",
                    )
                ],
            )
        ]
        stage = EnrichmentStage()

        stage._apply_approval_gates(
            phases,
            risk_level_enum=None,
            task_summary=f"{phase_name} the system",
            resolved_agents=["backend-engineer"],
            research_concerns=_TWO_CONCERNS,
        )

        assert len(phases[0].steps) == 2, (
            f"Phase '{phase_name}' should be splittable but step count stayed at 1"
        )


# ---------------------------------------------------------------------------
# Test 4 — PlanDraft carries research_concerns and research_context fields
# ---------------------------------------------------------------------------

class TestPlanDraftResearchFields:
    def test_research_concerns_defaults_to_none(self) -> None:
        draft = PlanDraft.from_inputs("Audit all components")
        assert draft.research_concerns is None

    def test_research_context_defaults_to_none(self) -> None:
        draft = PlanDraft.from_inputs("Audit all components")
        assert draft.research_context is None

    def test_research_concerns_can_be_set(self) -> None:
        draft = PlanDraft.from_inputs("Audit all components")
        draft.research_concerns = _FIVE_CONCERNS
        assert len(draft.research_concerns) == 5

    def test_research_context_can_be_set(self) -> None:
        draft = PlanDraft.from_inputs("Audit all components")
        draft.research_context = "Detailed context from ResearchStage."
        assert draft.research_context == "Detailed context from ResearchStage."


# ---------------------------------------------------------------------------
# Test 5 — DecompositionStage propagates research_concerns to draft.concerns
# ---------------------------------------------------------------------------

class TestDecompositionPropagatesResearchConcerns:
    def test_research_concerns_copied_to_draft_concerns(self) -> None:
        """When research_concerns is set on entry, DecompositionStage must
        copy them to draft.concerns so downstream stages can consume them."""
        from unittest.mock import MagicMock, patch
        from agent_baton.core.engine.planning.stages.decomposition import DecompositionStage

        draft = PlanDraft.from_inputs("Audit all components")
        draft.research_concerns = _FIVE_CONCERNS
        # Pre-populate minimal fields that _build_phases reads
        draft.resolved_agents = ["auditor"]
        draft.inferred_type = "audit"
        draft.inferred_complexity = "medium"

        stage = DecompositionStage()

        # Stub out every service _build_phases touches so no real infra needed.
        services = MagicMock()
        services.registry.get.return_value = None
        services.foresight_engine.analyze.return_value = ([], [])
        services.pattern_learner = None

        with patch(
            "agent_baton.core.engine.planning.stages.decomposition.default_phases",
            return_value=[_audit_phase()],
        ), patch(
            "agent_baton.core.engine.planning.stages.decomposition.enrich_phases",
            side_effect=lambda phases, *_a, **_kw: phases,
        ):
            stage.run(draft, services)

        assert draft.concerns == list(_FIVE_CONCERNS), (
            "draft.concerns must mirror research_concerns after DecompositionStage.run()"
        )

    def test_no_research_concerns_leaves_draft_concerns_empty(self) -> None:
        """When research_concerns is None, draft.concerns must remain empty."""
        from unittest.mock import MagicMock, patch
        from agent_baton.core.engine.planning.stages.decomposition import DecompositionStage

        draft = PlanDraft.from_inputs("Audit all components")
        # research_concerns not set — defaults to None
        draft.resolved_agents = ["auditor"]
        draft.inferred_type = "audit"
        draft.inferred_complexity = "medium"

        stage = DecompositionStage()

        services = MagicMock()
        services.registry.get.return_value = None
        services.foresight_engine.analyze.return_value = ([], [])
        services.pattern_learner = None

        with patch(
            "agent_baton.core.engine.planning.stages.decomposition.default_phases",
            return_value=[_audit_phase()],
        ), patch(
            "agent_baton.core.engine.planning.stages.decomposition.enrich_phases",
            side_effect=lambda phases, *_a, **_kw: phases,
        ):
            stage.run(draft, services)

        assert draft.concerns == [], (
            "draft.concerns must stay empty when research_concerns is None"
        )
