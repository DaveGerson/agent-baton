"""Tests for decision-capture INTERACT step injection in EnrichmentStage.

Covers the trigger matrix:
- PHASED + HIGH → injection happens
- PHASED + CRITICAL → injection happens
- PHASED + LOW or MEDIUM → NO injection
- DIRECT or INVESTIGATIVE archetype → NO injection regardless of risk
- Idempotency: running enrichment twice does not produce two INTERACT steps
- The injected step is positioned at the head of the Design phase
"""
from __future__ import annotations

import pytest

from agent_baton.core.engine.planning.stages.enrichment import EnrichmentStage
from agent_baton.models.enums import RiskLevel
from agent_baton.models.execution import PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_design_phase(phase_id: int = 1) -> PlanPhase:
    """Build a minimal Design phase with one pre-existing step."""
    return PlanPhase(
        phase_id=phase_id,
        name="Design",
        steps=[
            PlanStep(
                step_id=f"{phase_id}.1",
                agent_name="architect",
                task_description="Design the system",
            )
        ],
    )


def _make_implement_phase(phase_id: int = 2) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name="Implement",
        steps=[
            PlanStep(
                step_id=f"{phase_id}.1",
                agent_name="backend-engineer",
                task_description="Build the feature",
            )
        ],
    )


def _make_services():
    """Return a minimal services stub with a real registry."""
    from unittest.mock import MagicMock
    from agent_baton.core.engine.planning.planner import IntelligentPlanner

    planner = IntelligentPlanner()
    services = MagicMock()
    services.registry = planner._registry
    services.bead_store = None
    services.project_config = None
    return services


def _stage() -> EnrichmentStage:
    return EnrichmentStage()


# ---------------------------------------------------------------------------
# Trigger: PHASED + HIGH
# ---------------------------------------------------------------------------

class TestPhasedHighRiskInjectsDecisionCapture:
    def test_inject_on_phased_high(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["architect"],
            services=services,
        )

        design_phase = phases[0]
        # The first step must be the injected decision-capture step
        assert design_phase.steps[0].interactive is True

    def test_injected_step_contains_canonical_header(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["architect"],
            services=services,
        )

        first_step = phases[0].steps[0]
        assert EnrichmentStage._DECISION_CAPTURE_HEADER in first_step.task_description

    def test_injected_step_contains_all_three_questions(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["architect"],
            services=services,
        )

        desc = phases[0].steps[0].task_description
        assert "What are the explicit success criteria for this task?" in desc
        assert "What constraints or non-goals must we respect?" in desc
        assert (
            "Any decisions already made (architecture, library, API shape) "
            "we should not relitigate?"
        ) in desc

    def test_injected_step_uses_consulting_step_type(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["architect"],
            services=services,
        )

        assert phases[0].steps[0].step_type == "consulting"

    def test_injected_step_has_five_minute_estimate(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["architect"],
            services=services,
        )

        assert phases[0].steps[0].max_estimated_minutes == 5

    def test_injected_step_agent_is_architect(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["backend-engineer"],
            services=services,
        )

        # When the resolved roster doesn't contain "architect", the first
        # non-reviewer from the roster is used (honours --agents overrides).
        assert phases[0].steps[0].agent_name == "backend-engineer"


# ---------------------------------------------------------------------------
# Trigger: PHASED + CRITICAL
# ---------------------------------------------------------------------------

class TestPhasedCriticalRiskInjectsDecisionCapture:
    def test_inject_on_phased_critical(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.CRITICAL,
            resolved_agents=["architect"],
            services=services,
        )

        assert phases[0].steps[0].interactive is True

    def test_critical_step_at_head_of_design(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.CRITICAL,
            resolved_agents=["architect"],
            services=services,
        )

        assert phases[0].steps[0].step_id == "1.1"


# ---------------------------------------------------------------------------
# No-trigger: PHASED + LOW or MEDIUM
# ---------------------------------------------------------------------------

class TestPhasedLowMediumRiskNoInjection:
    def test_no_inject_on_phased_low(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.LOW,
            resolved_agents=["architect"],
            services=services,
        )

        # No interactive step should be present
        design_steps = phases[0].steps
        assert not any(s.interactive for s in design_steps)

    def test_no_inject_on_phased_medium(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.MEDIUM,
            resolved_agents=["architect"],
            services=services,
        )

        design_steps = phases[0].steps
        assert not any(s.interactive for s in design_steps)

    def test_step_count_unchanged_for_low_risk(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        original_count = len(phases[0].steps)
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.LOW,
            resolved_agents=["architect"],
            services=services,
        )

        assert len(phases[0].steps) == original_count


# ---------------------------------------------------------------------------
# No-trigger: DIRECT or INVESTIGATIVE archetype
# ---------------------------------------------------------------------------

class TestNonPhasedArchetypeNoInjection:
    def test_no_inject_on_direct_high(self):
        # DIRECT has no Design phase, but the archetype guard should fire first
        phases = [
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[PlanStep(step_id="1.1", agent_name="backend-engineer",
                                task_description="Do it")],
            ),
        ]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="direct",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["backend-engineer"],
            services=services,
        )

        assert not any(s.interactive for s in phases[0].steps)

    def test_no_inject_on_investigative_critical(self):
        phases = [
            PlanPhase(
                phase_id=1,
                name="Investigate",
                steps=[PlanStep(step_id="1.1", agent_name="backend-engineer",
                                task_description="Investigate")],
            ),
        ]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="investigative",
            risk_level_enum=RiskLevel.CRITICAL,
            resolved_agents=["backend-engineer"],
            services=services,
        )

        assert not any(s.interactive for s in phases[0].steps)

    def test_no_inject_on_direct_critical(self):
        phases = [
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[PlanStep(step_id="1.1", agent_name="backend-engineer",
                                task_description="Implement")],
            ),
        ]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="direct",
            risk_level_enum=RiskLevel.CRITICAL,
            resolved_agents=["backend-engineer"],
            services=services,
        )

        assert not any(s.interactive for s in phases[0].steps)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestDecisionCaptureIdempotency:
    def test_calling_twice_does_not_duplicate_step(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        services = _make_services()
        stage = _stage()

        stage._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["architect"],
            services=services,
        )
        count_after_first = len(phases[0].steps)

        stage._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["architect"],
            services=services,
        )
        count_after_second = len(phases[0].steps)

        assert count_after_first == count_after_second, (
            f"Second call added a duplicate step: "
            f"{count_after_first} → {count_after_second}"
        )

    def test_idempotency_exactly_one_interact_step(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        services = _make_services()
        stage = _stage()

        for _ in range(3):
            stage._apply_decision_capture(
                phases,
                archetype="phased",
                risk_level_enum=RiskLevel.CRITICAL,
                resolved_agents=["architect"],
                services=services,
            )

        interact_steps = [s for s in phases[0].steps if s.interactive]
        assert len(interact_steps) == 1


# ---------------------------------------------------------------------------
# Step positioning
# ---------------------------------------------------------------------------

class TestDecisionCapturePosition:
    def test_injected_step_is_first_in_design_phase(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["architect"],
            services=services,
        )

        assert phases[0].steps[0].interactive is True

    def test_original_steps_still_present_after_injection(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        original_desc = phases[0].steps[0].task_description
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["architect"],
            services=services,
        )

        descriptions = [s.task_description for s in phases[0].steps]
        assert original_desc in descriptions, (
            "Original Design step description was lost after injection"
        )

    def test_step_count_increases_by_one(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        original_count = len(phases[0].steps)
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["architect"],
            services=services,
        )

        assert len(phases[0].steps) == original_count + 1

    def test_captured_step_id_is_phase_dot_one(self):
        phases = [_make_design_phase(phase_id=2), _make_implement_phase(phase_id=3)]
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["architect"],
            services=services,
        )

        # phase_id=2 so step_id must be "2.1"
        assert phases[0].steps[0].step_id == "2.1"

    def test_inject_does_not_touch_non_design_phases(self):
        phases = [_make_design_phase(), _make_implement_phase()]
        original_impl_count = len(phases[1].steps)
        services = _make_services()

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["architect"],
            services=services,
        )

        assert len(phases[1].steps) == original_impl_count


# ---------------------------------------------------------------------------
# Agent fallback
# ---------------------------------------------------------------------------

class TestDecisionCaptureAgentFallback:
    def test_fallback_to_first_resolved_agent_when_architect_absent(self):
        from unittest.mock import MagicMock

        services = _make_services()
        # Patch registry to report "architect" as not found
        original_get = services.registry.get

        def _patched_get(name: str):
            if name == "architect":
                return None
            return original_get(name)

        services.registry.get = _patched_get

        phases = [_make_design_phase(), _make_implement_phase()]

        _stage()._apply_decision_capture(
            phases,
            archetype="phased",
            risk_level_enum=RiskLevel.HIGH,
            resolved_agents=["custom-designer"],
            services=services,
        )

        assert phases[0].steps[0].agent_name == "custom-designer"
