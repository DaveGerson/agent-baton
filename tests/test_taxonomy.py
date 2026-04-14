"""Tests for the planning taxonomy module.

Verifies that:
- All enums are well-formed and have correct values.
- PlanningTaxonomy produces consistent, complete output.
- phase_archetype() maps known names and falls back correctly.
- Taxonomy serialization round-trips cleanly.
- Markdown rendering includes all sections.
"""
from __future__ import annotations

import pytest

from agent_baton.models.taxonomy import (
    AGENT_INTENT_AFFINITY,
    ForesightInsight,
    PhaseArchetype,
    PlanElementKind,
    PlanningTaxonomy,
    StepIntent,
    TaxonomyTerm,
    phase_archetype,
)


class TestStepIntent:
    """StepIntent enum completeness and semantics."""

    def test_all_intents_have_unique_values(self):
        values = [i.value for i in StepIntent]
        assert len(values) == len(set(values))

    def test_core_work_intents_present(self):
        core = {StepIntent.PRODUCE, StepIntent.TRANSFORM, StepIntent.VALIDATE, StepIntent.INTEGRATE}
        assert core.issubset(set(StepIntent))

    def test_support_intents_present(self):
        support = {StepIntent.SCAFFOLD, StepIntent.PROVISION, StepIntent.CONFIGURE, StepIntent.REMEDIATE}
        assert support.issubset(set(StepIntent))

    def test_governance_intents_present(self):
        gov = {StepIntent.REVIEW, StepIntent.AUDIT, StepIntent.APPROVE, StepIntent.GATE}
        assert gov.issubset(set(StepIntent))

    def test_foresight_intent_present(self):
        assert StepIntent.FORESIGHT in StepIntent
        assert StepIntent.FORESIGHT.value == "foresight"

    def test_total_count(self):
        assert len(StepIntent) == 13


class TestPhaseArchetype:
    """PhaseArchetype enum and mapping."""

    def test_all_archetypes_present(self):
        expected = {"discovery", "design", "preparation", "implementation",
                    "verification", "review", "remediation"}
        actual = {a.value for a in PhaseArchetype}
        assert actual == expected

    def test_phase_archetype_known_names(self):
        assert phase_archetype("Design") == PhaseArchetype.DESIGN
        assert phase_archetype("Implement") == PhaseArchetype.IMPLEMENTATION
        assert phase_archetype("Test") == PhaseArchetype.VERIFICATION
        assert phase_archetype("Review") == PhaseArchetype.REVIEW
        assert phase_archetype("Research") == PhaseArchetype.DISCOVERY
        assert phase_archetype("Investigate") == PhaseArchetype.DISCOVERY

    def test_phase_archetype_case_insensitive(self):
        assert phase_archetype("DESIGN") == PhaseArchetype.DESIGN
        assert phase_archetype("implement") == PhaseArchetype.IMPLEMENTATION

    def test_phase_archetype_unknown_falls_back(self):
        assert phase_archetype("CustomPhase") == PhaseArchetype.IMPLEMENTATION

    def test_preparation_archetype_for_foresight_phases(self):
        assert phase_archetype("Scaffold") == PhaseArchetype.PREPARATION
        assert phase_archetype("Provision") == PhaseArchetype.PREPARATION
        assert phase_archetype("Configure") == PhaseArchetype.PREPARATION
        assert phase_archetype("Prepare") == PhaseArchetype.PREPARATION


class TestPlanElementKind:
    """PlanElementKind enum."""

    def test_all_kinds_present(self):
        expected = {"plan", "phase", "step", "gate", "team", "member",
                    "amendment", "foresight_insight"}
        actual = {k.value for k in PlanElementKind}
        assert actual == expected


class TestAgentIntentAffinity:
    """Agent-to-intent affinity table."""

    def test_all_core_agents_mapped(self):
        core_agents = {
            "architect", "backend-engineer", "frontend-engineer",
            "test-engineer", "code-reviewer", "security-reviewer",
            "devops-engineer", "data-engineer", "data-analyst",
            "data-scientist", "auditor", "visualization-expert",
            "subject-matter-expert", "talent-builder",
        }
        assert core_agents == set(AGENT_INTENT_AFFINITY.keys())

    def test_affinity_values_are_step_intents(self):
        for agent, intents in AGENT_INTENT_AFFINITY.items():
            for intent in intents:
                assert isinstance(intent, StepIntent), \
                    f"{agent} has non-StepIntent value: {intent}"


class TestTaxonomyTerm:
    """TaxonomyTerm serialization."""

    def test_roundtrip(self):
        term = TaxonomyTerm(
            name="produce",
            label="Produce",
            family="core_work",
            definition="Create new artifacts.",
            examples=["Write code", "Create schema"],
        )
        d = term.to_dict()
        assert d["name"] == "produce"
        assert d["examples"] == ["Write code", "Create schema"]


class TestPlanningTaxonomy:
    """PlanningTaxonomy aggregation and rendering."""

    def setup_method(self):
        self.tax = PlanningTaxonomy()

    def test_step_intents_complete(self):
        terms = self.tax.step_intents()
        assert len(terms) == len(StepIntent)
        names = {t.name for t in terms}
        assert names == {i.value for i in StepIntent}

    def test_phase_archetypes_complete(self):
        terms = self.tax.phase_archetypes()
        assert len(terms) == len(PhaseArchetype)

    def test_plan_elements_complete(self):
        terms = self.tax.plan_elements()
        assert len(terms) == len(PlanElementKind)

    def test_to_dict_structure(self):
        d = self.tax.to_dict()
        assert "step_intents" in d
        assert "phase_archetypes" in d
        assert "plan_elements" in d
        assert "agent_intent_affinity" in d
        # Verify agent_intent_affinity values are strings
        for agent, intents in d["agent_intent_affinity"].items():
            for intent_val in intents:
                assert isinstance(intent_val, str)

    def test_to_markdown_has_all_sections(self):
        md = self.tax.to_markdown()
        assert "# Planning Taxonomy" in md
        assert "## Step Intents" in md
        assert "## Phase Archetypes" in md
        assert "## Plan Elements" in md
        assert "## Agent-Intent Affinity" in md
        # Foresight intent should appear
        assert "Foresight" in md
        assert "`foresight`" in md

    def test_to_markdown_families_grouped(self):
        md = self.tax.to_markdown()
        assert "### Core Work" in md
        assert "### Support" in md
        assert "### Governance" in md
        assert "### Foresight" in md


class TestForesightInsightModel:
    """ForesightInsight dataclass."""

    def test_defaults(self):
        insight = ForesightInsight(
            category="capability_gap",
            description="test",
            resolution="fix it",
        )
        assert insight.confidence == 0.8
        assert insight.source_rule == ""
        assert insight.inserted_step_ids == []

    def test_roundtrip(self):
        insight = ForesightInsight(
            category="prerequisite",
            description="Need rollback",
            resolution="Create rollback scripts",
            inserted_phase_name="Prepare: Migration Safety",
            inserted_step_ids=["0.1"],
            confidence=0.9,
            source_rule="foresight-migration-rollback",
        )
        d = insight.to_dict()
        restored = ForesightInsight.from_dict(d)
        assert restored.category == insight.category
        assert restored.confidence == insight.confidence
        assert restored.source_rule == insight.source_rule
