"""Tests for archetype-related execution model changes."""
from __future__ import annotations

import pytest

from agent_baton.models.execution import (
    ActionType,
    ExecutionAction,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


class TestActionTypeCheckpoint:
    def test_checkpoint_exists(self):
        assert hasattr(ActionType, 'CHECKPOINT')
        assert ActionType.CHECKPOINT.value == "checkpoint"

    def test_backward_compat_existing_types(self):
        assert ActionType.DISPATCH.value == "dispatch"
        assert ActionType.GATE.value == "gate"
        assert ActionType.COMPLETE.value == "complete"

    def test_checkpoint_is_enum_member(self):
        assert ActionType.CHECKPOINT in ActionType

    def test_all_original_nine_types_still_present(self):
        original = {
            "dispatch", "gate", "complete", "failed", "wait",
            "approval", "feedback", "interact", "swarm.dispatch",
        }
        current = {at.value for at in ActionType}
        assert original.issubset(current)

    def test_checkpoint_value_is_string(self):
        assert isinstance(ActionType.CHECKPOINT.value, str)


class TestMachinePlanArchetype:
    def _minimal_plan(self, **kwargs):
        return MachinePlan(
            task_id="test-123",
            task_summary="test task",
            **kwargs,
        )

    def test_default_archetype_is_phased(self):
        plan = self._minimal_plan()
        assert plan.archetype == "phased"

    def test_explicit_archetype(self):
        plan = self._minimal_plan(archetype="investigative")
        assert plan.archetype == "investigative"

    def test_direct_archetype(self):
        plan = self._minimal_plan(archetype="direct")
        assert plan.archetype == "direct"

    def test_max_retry_phases_default_zero(self):
        plan = self._minimal_plan()
        assert plan.max_retry_phases == 0

    def test_investigative_with_retries(self):
        plan = self._minimal_plan(archetype="investigative", max_retry_phases=3)
        assert plan.max_retry_phases == 3

    def test_to_dict_includes_archetype(self):
        plan = self._minimal_plan(archetype="direct")
        d = plan.to_dict()
        assert d["archetype"] == "direct"

    def test_to_dict_includes_max_retry_phases(self):
        plan = self._minimal_plan(archetype="investigative", max_retry_phases=2)
        d = plan.to_dict()
        assert d["max_retry_phases"] == 2

    def test_from_dict_with_archetype(self):
        d = {
            "task_id": "test-123",
            "task_summary": "test",
            "archetype": "investigative",
            "max_retry_phases": 3,
        }
        plan = MachinePlan.from_dict(d)
        assert plan.archetype == "investigative"
        assert plan.max_retry_phases == 3

    def test_from_dict_backward_compat(self):
        # Old plan without archetype field must deserialise cleanly
        d = {
            "task_id": "test-123",
            "task_summary": "test",
        }
        plan = MachinePlan.from_dict(d)
        assert plan.archetype == "phased"
        assert plan.max_retry_phases == 0

    def test_to_markdown_shows_archetype(self):
        plan = self._minimal_plan(archetype="investigative")
        md = plan.to_markdown()
        assert "investigative" in md.lower()

    def test_archetype_round_trip_via_dict(self):
        plan = self._minimal_plan(archetype="direct", max_retry_phases=0)
        d = plan.to_dict()
        restored = MachinePlan.from_dict(d)
        assert restored.archetype == "direct"
        assert restored.max_retry_phases == 0

    def test_investigative_round_trip_preserves_retry_count(self):
        plan = self._minimal_plan(archetype="investigative", max_retry_phases=5)
        d = plan.to_dict()
        restored = MachinePlan.from_dict(d)
        assert restored.archetype == "investigative"
        assert restored.max_retry_phases == 5

    def test_archetype_field_is_string(self):
        plan = self._minimal_plan()
        assert isinstance(plan.archetype, str)

    def test_max_retry_phases_is_int(self):
        plan = self._minimal_plan(max_retry_phases=2)
        assert isinstance(plan.max_retry_phases, int)


class TestPlanStepMaxEstimatedMinutes:
    def test_default_zero(self):
        step = PlanStep(step_id="1.1", agent_name="be", task_description="test")
        assert step.max_estimated_minutes == 0

    def test_to_dict_excludes_when_zero(self):
        step = PlanStep(step_id="1.1", agent_name="be", task_description="test")
        d = step.to_dict()
        assert "max_estimated_minutes" not in d

    def test_to_dict_includes_when_set(self):
        step = PlanStep(
            step_id="1.1", agent_name="be", task_description="test",
            max_estimated_minutes=15,
        )
        d = step.to_dict()
        assert d["max_estimated_minutes"] == 15

    def test_from_dict_backward_compat(self):
        d = {"step_id": "1.1", "agent_name": "be", "task_description": "test"}
        step = PlanStep.from_dict(d)
        assert step.max_estimated_minutes == 0

    def test_round_trip_preserves_value(self):
        step = PlanStep(
            step_id="2.1", agent_name="architect", task_description="design it",
            max_estimated_minutes=30,
        )
        d = step.to_dict()
        restored = PlanStep.from_dict(d)
        assert restored.max_estimated_minutes == 30

    def test_max_estimated_minutes_is_int(self):
        step = PlanStep(
            step_id="1.1", agent_name="be", task_description="test",
            max_estimated_minutes=10,
        )
        assert isinstance(step.max_estimated_minutes, int)


class TestExecutionActionCheckpoint:
    def test_checkpoint_suggested_default_false(self):
        action = ExecutionAction(action_type=ActionType.DISPATCH)
        assert action.checkpoint_suggested is False

    def test_to_dict_includes_when_true(self):
        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            checkpoint_suggested=True,
        )
        d = action.to_dict()
        assert d.get("checkpoint_suggested") is True

    def test_to_dict_omits_when_false(self):
        # When False, to_dict should omit the field (sparse representation)
        action = ExecutionAction(action_type=ActionType.DISPATCH)
        d = action.to_dict()
        # Either not present or explicitly False is acceptable
        assert not d.get("checkpoint_suggested", False)

    def test_checkpoint_suggested_is_bool(self):
        action = ExecutionAction(
            action_type=ActionType.DISPATCH,
            checkpoint_suggested=True,
        )
        assert isinstance(action.checkpoint_suggested, bool)

    def test_checkpoint_action_type_serialises(self):
        action = ExecutionAction(action_type=ActionType.CHECKPOINT)
        d = action.to_dict()
        assert d["action_type"] == "checkpoint"
