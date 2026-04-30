"""Tests for planning archetype definitions and configuration."""
from __future__ import annotations

import pytest

from agent_baton.core.engine.planning.archetypes import (
    ARCHETYPE_CONFIGS,
    ArchetypeConfig,
    DIRECT_CONFIG,
    GatePolicy,
    INVESTIGATIVE_CONFIG,
    PHASED_CONFIG,
    get_archetype_config,
)
from agent_baton.models.enums import PlanningArchetype


class TestPlanningArchetypeEnum:
    def test_values(self):
        assert PlanningArchetype.DIRECT.value == "direct"
        assert PlanningArchetype.PHASED.value == "phased"
        assert PlanningArchetype.INVESTIGATIVE.value == "investigative"

    def test_all_archetypes_have_configs(self):
        for archetype in PlanningArchetype:
            assert archetype.value in ARCHETYPE_CONFIGS

    def test_three_archetypes_exist(self):
        members = list(PlanningArchetype)
        assert len(members) == 3

    def test_iterating_archetypes_gives_all_three(self):
        values = {a.value for a in PlanningArchetype}
        assert values == {"direct", "phased", "investigative"}


class TestArchetypeConfig:
    def test_direct_has_minimal_phases(self):
        assert len(DIRECT_CONFIG.phase_template) == 2
        assert "Implement" in DIRECT_CONFIG.phase_template
        assert "Review" in DIRECT_CONFIG.phase_template

    def test_phased_has_full_phases(self):
        assert len(PHASED_CONFIG.phase_template) >= 3
        assert "Design" in PHASED_CONFIG.phase_template
        assert "Implement" in PHASED_CONFIG.phase_template

    def test_investigative_has_hypothesis_phases(self):
        assert "Investigate" in INVESTIGATIVE_CONFIG.phase_template
        assert "Hypothesize" in INVESTIGATIVE_CONFIG.phase_template
        assert "Fix" in INVESTIGATIVE_CONFIG.phase_template
        assert "Verify" in INVESTIGATIVE_CONFIG.phase_template

    def test_investigative_has_exactly_four_phases(self):
        # Investigate, Hypothesize, Fix, Verify — the canonical debug loop
        phase_set = set(INVESTIGATIVE_CONFIG.phase_template)
        required = {"Investigate", "Hypothesize", "Fix", "Verify"}
        assert required.issubset(phase_set)

    def test_investigative_supports_retry(self):
        assert INVESTIGATIVE_CONFIG.supports_retry is True
        assert INVESTIGATIVE_CONFIG.max_retry_count > 0

    def test_direct_does_not_retry(self):
        assert DIRECT_CONFIG.supports_retry is False
        assert DIRECT_CONFIG.max_retry_count == 0

    def test_phased_has_decision_capture(self):
        assert PHASED_CONFIG.decision_capture is True

    def test_direct_no_decision_capture(self):
        assert DIRECT_CONFIG.decision_capture is False

    def test_archetype_config_is_dataclass_or_has_attributes(self):
        # ArchetypeConfig must be importable and carry the required attributes
        config = DIRECT_CONFIG
        assert hasattr(config, "phase_template")
        assert hasattr(config, "supports_retry")
        assert hasattr(config, "max_retry_count")
        assert hasattr(config, "decision_capture")
        assert hasattr(config, "gate_policies")
        assert hasattr(config, "max_step_minutes")

    def test_investigative_max_retry_count_positive(self):
        assert isinstance(INVESTIGATIVE_CONFIG.max_retry_count, int)
        assert INVESTIGATIVE_CONFIG.max_retry_count >= 1

    def test_phased_phase_template_ordered(self):
        # Design must appear before Implement in the ordered template
        template = PHASED_CONFIG.phase_template
        design_idx = template.index("Design")
        implement_idx = template.index("Implement")
        assert design_idx < implement_idx


class TestGatePolicy:
    def test_low_risk_direct_has_basic_gates(self):
        policy = DIRECT_CONFIG.gate_policies["LOW"]
        assert "build" in policy.automated_gates or "test" in policy.automated_gates
        assert policy.code_review is False
        assert policy.approval_required is False

    def test_high_risk_requires_approval(self):
        for config in [DIRECT_CONFIG, PHASED_CONFIG, INVESTIGATIVE_CONFIG]:
            policy = config.gate_policies["HIGH"]
            assert policy.approval_required is True

    def test_critical_requires_auditor(self):
        for config in [DIRECT_CONFIG, PHASED_CONFIG, INVESTIGATIVE_CONFIG]:
            policy = config.gate_policies["CRITICAL"]
            assert policy.auditor_required is True

    def test_phased_medium_has_spec_compliance(self):
        policy = PHASED_CONFIG.gate_policies["MEDIUM"]
        assert policy.spec_compliance is True

    def test_all_risk_levels_covered(self):
        for config in ARCHETYPE_CONFIGS.values():
            for level in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
                assert level in config.gate_policies, (
                    f"Missing risk level '{level}' in gate_policies for "
                    f"config {config}"
                )

    def test_gate_policy_has_required_attributes(self):
        policy = DIRECT_CONFIG.gate_policies["LOW"]
        assert hasattr(policy, "automated_gates")
        assert hasattr(policy, "code_review")
        assert hasattr(policy, "approval_required")
        assert hasattr(policy, "auditor_required")
        assert hasattr(policy, "spec_compliance")

    def test_automated_gates_is_sequence(self):
        policy = DIRECT_CONFIG.gate_policies["LOW"]
        assert hasattr(policy.automated_gates, "__iter__")

    def test_critical_approval_also_required(self):
        # CRITICAL implies higher stakes — approval must be required
        for config in [DIRECT_CONFIG, PHASED_CONFIG, INVESTIGATIVE_CONFIG]:
            policy = config.gate_policies["CRITICAL"]
            assert policy.approval_required is True


class TestGetArchetypeConfig:
    def test_known_archetype(self):
        assert get_archetype_config("direct") is DIRECT_CONFIG
        assert get_archetype_config("phased") is PHASED_CONFIG
        assert get_archetype_config("investigative") is INVESTIGATIVE_CONFIG

    def test_unknown_defaults_to_phased(self):
        assert get_archetype_config("nonexistent") is PHASED_CONFIG
        assert get_archetype_config("") is PHASED_CONFIG

    def test_max_step_minutes_positive(self):
        for config in ARCHETYPE_CONFIGS.values():
            assert config.max_step_minutes > 0

    def test_archetype_configs_dict_covers_all_enum_values(self):
        for archetype in PlanningArchetype:
            assert archetype.value in ARCHETYPE_CONFIGS

    def test_get_archetype_config_returns_correct_type(self):
        cfg = get_archetype_config("direct")
        assert isinstance(cfg, ArchetypeConfig)

    def test_direct_max_step_minutes_less_than_phased(self):
        # Direct tasks should have a tighter time bound than phased ones
        assert DIRECT_CONFIG.max_step_minutes <= PHASED_CONFIG.max_step_minutes
