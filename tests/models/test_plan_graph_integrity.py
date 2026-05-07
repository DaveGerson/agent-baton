"""Hole-6 plan-graph integrity validator tests.

Slice 11 of the migration plan — covers the @model_validator(mode="after")
on MachinePlan that catches structurally invalid plans at construction
time.  See docs/internal/migration-review-summary.md §3 (Hole-6).

The validator is the new safety net for LLM-output plans: bad shapes
that would later crash the executor (collisions in step_results, empty
agent dispatch, forward depends_on references) now raise ValidationError
at MachinePlan construction time instead.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_baton.models.execution import (
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)


def _step(step_id: str, agent: str = "x", depends_on: list[str] | None = None) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent,
        task_description="t",
        depends_on=depends_on or [],
    )


def _plan(phases: list[PlanPhase]) -> MachinePlan:
    return MachinePlan(
        task_id="task-graph",
        task_summary="graph integrity tests",
        phases=phases,
    )


class TestValidPlansPass:
    """Plans that satisfy every Hole-6 invariant construct cleanly."""

    def test_empty_plan_is_valid(self) -> None:
        plan = _plan([])
        assert plan.phases == []

    def test_single_phase_single_step(self) -> None:
        plan = _plan([PlanPhase(phase_id=0, name="p0", steps=[_step("0.1")])])
        assert plan.total_steps == 1

    def test_chain_of_dependencies(self) -> None:
        plan = _plan([
            PlanPhase(phase_id=0, name="p0", steps=[
                _step("0.1"),
                _step("0.2", depends_on=["0.1"]),
                _step("0.3", depends_on=["0.1", "0.2"]),
            ]),
        ])
        assert plan.total_steps == 3


class TestStepIdUniqueness:
    """Two phases sharing a step_id collide in ExecutionState.step_results."""

    def test_same_step_id_in_two_phases_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _plan([
                PlanPhase(phase_id=0, name="p0", steps=[_step("dup")]),
                PlanPhase(phase_id=1, name="p1", steps=[_step("dup")]),
            ])
        assert "step_id 'dup'" in str(exc.value)

    def test_same_step_id_in_one_phase_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _plan([
                PlanPhase(phase_id=0, name="p0", steps=[_step("a"), _step("a")]),
            ])
        assert "step_id 'a'" in str(exc.value)


class TestPhaseIdUniqueness:
    """Phase IDs collide in lookup-by-id used by amend / resolver."""

    def test_duplicate_phase_id_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _plan([
                PlanPhase(phase_id=1, name="p1", steps=[_step("1.1")]),
                PlanPhase(phase_id=1, name="other-p1", steps=[_step("1.2")]),
            ])
        assert "phase_id 1" in str(exc.value)


class TestEmptyAgentName:
    """Steps with empty agent_name cannot be dispatched."""

    def test_blank_agent_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _plan([
                PlanPhase(phase_id=0, name="p0", steps=[
                    PlanStep(step_id="0.1", agent_name="", task_description="t"),
                ]),
            ])
        assert "empty agent_name" in str(exc.value)


class TestDependsOnResolution:
    """Forward depends_on references would deadlock the dispatcher."""

    def test_forward_reference_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _plan([
                PlanPhase(phase_id=0, name="p0", steps=[
                    _step("0.1", depends_on=["0.2"]),  # 0.2 declared after
                    _step("0.2"),
                ]),
            ])
        assert "depends on '0.2'" in str(exc.value)

    def test_cross_phase_backward_reference_allowed(self) -> None:
        """A phase-2 step depending on a phase-1 step IS valid."""
        plan = _plan([
            PlanPhase(phase_id=0, name="p0", steps=[_step("0.1")]),
            PlanPhase(phase_id=1, name="p1", steps=[_step("1.1", depends_on=["0.1"])]),
        ])
        assert plan.total_steps == 2

    def test_unresolved_dep_to_nonexistent_step_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _plan([
                PlanPhase(phase_id=0, name="p0", steps=[
                    _step("0.1", depends_on=["does_not_exist"]),
                ]),
            ])
        assert "depends on 'does_not_exist'" in str(exc.value)


class TestRoundtripPreservesInvariants:
    """from_dict → to_dict → from_dict is invariant-preserving."""

    def test_valid_plan_roundtrip(self) -> None:
        original = _plan([
            PlanPhase(phase_id=0, name="p0", steps=[_step("0.1")]),
            PlanPhase(phase_id=1, name="p1", steps=[_step("1.1", depends_on=["0.1"])],
                      gate=PlanGate(gate_type="test", command="pytest")),
        ])
        d = original.to_dict()
        rebuilt = MachinePlan.from_dict(d)
        assert rebuilt.total_steps == original.total_steps
        assert rebuilt.phases[1].gate is not None
        assert rebuilt.phases[1].gate.command == "pytest"

    def test_legacy_dict_with_collision_loud_at_load(self) -> None:
        """A persisted state file with a colliding step_id raises on load.

        This is the safety the validator buys us: a malformed plan on
        disk surfaces immediately on from_dict, not later when the
        executor walks step_results.
        """
        bad = {
            "task_id": "t",
            "task_summary": "bad",
            "phases": [
                {"phase_id": 0, "name": "p0", "steps": [
                    {"step_id": "x", "agent_name": "a", "task_description": "t"},
                ]},
                {"phase_id": 1, "name": "p1", "steps": [
                    {"step_id": "x", "agent_name": "a", "task_description": "t"},
                ]},
            ],
        }
        with pytest.raises(ValidationError):
            MachinePlan.from_dict(bad)
