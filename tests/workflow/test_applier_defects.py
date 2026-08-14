"""Regression tests for adversarially-confirmed ``WorkflowApplier`` defects.

Each class pins one defect that shipped in the first implementation of
docs/internal/adversarial-tdd-workflow-design.md §5. Every test here fails
against the pre-fix applier:

1. :class:`TestTeamMemberRekeying` — harvested team steps kept stale
   ``TeamMember.member_id`` values, so the engine derived the WRONG parent
   step from the member-id prefix (``execute.py`` /
   ``_validators.parent_step_id``) and either hard-errored or attached
   results to an unrelated step, re-dispatching the phase forever.
2. :class:`TestTeamConsolidatedAuditCarryover` — an Audit phase folded into a
   single ``agent_name="team"`` step by ``ValidationStage._consolidate_team``
   was dropped from audit carryover, because carryover only inspected
   ``step.agent_name``.
3. :class:`TestMixedAuditPhaseCarryover` — fallback harvest (a) stole the
   ``developing`` step out of an auditor-bearing phase, which then made the
   phase ineligible for carryover; the auditor vanished silently.
   :class:`TestDroppedStepVisibility` covers the same fix's reporting half.
4. :class:`TestBasePhaseOrdering` — flattening PREPARATION + IMPLEMENTATION +
   REMEDIATION phases into one Implementation phase produced a single
   dependency-free wave, losing the base plan's phase ordering.
5. :class:`TestReshapedPlanValidation` — nothing revalidated the reshaped
   plan, so an empty configured agent name reached ``plan.json`` and only
   died at ``baton execute start``.

Behaviour-only: everything is asserted through the reshaped ``MachinePlan``,
its ``to_dict()``, and the returned ``WorkflowDecisions``.
"""
from __future__ import annotations

import json

import pytest

from agent_baton.core.config.workflow import StageOverride, WorkflowSettings
from agent_baton.core.workflow.applier import WorkflowReshapeError
from agent_baton.models.execution import MachinePlan, PlanStep, TeamMember

from tests.workflow._plans import (
    build_audit_only_qualifying_phase_plan,
    build_carryover_team_step_plan,
    build_dropped_developing_step_plan,
    build_mixed_audit_phase_plan,
    build_multi_base_phase_ordering_plan,
    build_nested_team_consolidated_audit_plan,
    build_renumbered_team_step_plan,
    build_team_consolidated_audit_plan,
    phase_by_name,
)
from tests.workflow.test_applier import (
    apply_workflow,
    assert_graph_invariants,
    implementation_steps,
)


def parent_step_id(member_id: str) -> str:
    """The engine's own derivation of a member's parent step id.

    Copied deliberately from ``execute.py:663`` /
    ``cli/commands/execution/_validators.parent_step_id`` so these tests pin
    the protocol surface, not the applier's internals.
    """
    return ".".join(member_id.split(".")[:2])


def flatten_members(members: list[TeamMember]) -> list[TeamMember]:
    out: list[TeamMember] = []
    for member in members:
        out.append(member)
        out.extend(flatten_members(member.sub_team))
    return out


def all_team_steps(plan: MachinePlan) -> list[PlanStep]:
    return [step for step in plan.all_steps if step.team]


# ---------------------------------------------------------------------------
# Defect 1 — stale team member ids after renumbering
# ---------------------------------------------------------------------------

class TestTeamMemberRekeying:
    def test_member_ids_follow_the_renumbered_step(self) -> None:
        plan = build_renumbered_team_step_plan()
        apply_workflow(plan)

        step = next(s for s in implementation_steps(plan) if s.team)
        assert step.step_id != "3.1", "fixture must actually renumber the step"
        assert [m.member_id for m in step.team] == [
            f"{step.step_id}.a",
            f"{step.step_id}.b",
        ]

    def test_nested_sub_team_member_ids_are_rekeyed(self) -> None:
        plan = build_renumbered_team_step_plan()
        apply_workflow(plan)

        step = next(s for s in implementation_steps(plan) if s.team)
        lead = next(m for m in step.team if m.role == "lead")
        assert [m.member_id for m in lead.sub_team] == [f"{step.step_id}.a.i"]

    def test_engine_derived_parent_step_resolves_to_the_team_step(self) -> None:
        # The actual failure mode: DISPATCH prints
        # `Parent-Step: <prefix>` / `Record-With: team-record --step-id
        # <prefix>`. That id must be THIS team step, not a stale one.
        plan = build_renumbered_team_step_plan()
        apply_workflow(plan)

        by_id = {step.step_id: step for step in plan.all_steps}
        for step in all_team_steps(plan):
            for member in flatten_members(step.team):
                parent = parent_step_id(member.member_id)
                assert parent == step.step_id
                assert by_id[parent].team, "parent must be a team step"

    def test_stale_member_prefix_would_have_hit_an_unrelated_step(self) -> None:
        # Guards the fixture's teeth: pre-fix, members read "3.1.*" and "3.1"
        # is a live id in the *base* plan belonging to a different phase, so
        # the bug silently mis-attributed results rather than erroring.
        plan = build_renumbered_team_step_plan()
        assert any(step.step_id == "3.1" for step in plan.all_steps)

        apply_workflow(plan)
        member_ids = {
            member.member_id
            for step in all_team_steps(plan)
            for member in flatten_members(step.team)
        }
        assert not any(mid.startswith("3.1.") for mid in member_ids)

    def test_intra_team_dependencies_are_remapped(self) -> None:
        plan = build_renumbered_team_step_plan()
        apply_workflow(plan)

        step = next(s for s in implementation_steps(plan) if s.team)
        follower = next(m for m in step.team if m.role != "lead")
        lead = next(m for m in step.team if m.role == "lead")
        assert follower.depends_on == [lead.member_id]
        assert follower.depends_on != ["3.1.a"]

    def test_member_ids_are_unique_after_reshape(self) -> None:
        plan = build_renumbered_team_step_plan()
        apply_workflow(plan)

        member_ids = [
            member.member_id
            for step in all_team_steps(plan)
            for member in flatten_members(step.team)
        ]
        assert len(member_ids) == len(set(member_ids))
        assert_graph_invariants(plan)

    def test_carryover_team_member_ids_are_rekeyed_too(self) -> None:
        # Carryover steps are renumbered as well (base 2.1 -> 8.1 after the
        # seven preset stages), so they carry the identical hazard.
        plan = build_carryover_team_step_plan()
        apply_workflow(plan)

        carried = plan.phases[-1]
        step = carried.steps[0]
        assert step.step_id == f"{carried.phase_id}.1"
        assert [m.member_id for m in step.team] == [
            f"{step.step_id}.a",
            f"{step.step_id}.b",
        ]
        lead = step.team[0]
        assert [m.member_id for m in lead.sub_team] == [f"{step.step_id}.a.i"]
        assert step.team[1].depends_on == [lead.member_id]

    def test_every_member_prefix_resolves_across_the_whole_plan(self) -> None:
        plan = build_carryover_team_step_plan()
        apply_workflow(plan)

        by_id = {step.step_id: step for step in plan.all_steps}
        for step in all_team_steps(plan):
            for member in flatten_members(step.team):
                assert parent_step_id(member.member_id) == step.step_id
                assert by_id[step.step_id] is step

    def test_rekeying_survives_the_json_round_trip(self) -> None:
        plan = build_renumbered_team_step_plan()
        apply_workflow(plan)
        reloaded = MachinePlan.from_dict(json.loads(json.dumps(plan.to_dict())))

        step = next(s for s in implementation_steps(reloaded) if s.team)
        assert [m.member_id for m in step.team] == [
            f"{step.step_id}.a",
            f"{step.step_id}.b",
        ]


# ---------------------------------------------------------------------------
# Defect 2 — team-consolidated Audit phases dropped from carryover
# ---------------------------------------------------------------------------

class TestTeamConsolidatedAuditCarryover:
    def test_consolidated_audit_phase_is_carried_over(self) -> None:
        plan = build_team_consolidated_audit_plan()
        decisions = apply_workflow(plan)

        assert decisions.carried_over_phases == ["Audit"]
        assert plan.phases[-1].name == "Audit"

    def test_auditor_member_survives_the_reshape(self) -> None:
        plan = build_team_consolidated_audit_plan()
        apply_workflow(plan)

        agents = {
            member.agent_name
            for step in all_team_steps(plan)
            for member in flatten_members(step.team)
        }
        assert "auditor" in agents

    def test_carried_over_consolidated_phase_keeps_gate_and_approval(self) -> None:
        plan = build_team_consolidated_audit_plan()
        apply_workflow(plan)

        carried = plan.phases[-1]
        assert carried.approval_required is True
        assert carried.gate is not None
        assert carried.gate.command == "baton evidence verify"
        assert {s.workflow_stage for s in carried.steps} == {"carryover"}

    def test_nested_sub_team_auditor_is_detected(self) -> None:
        plan = build_nested_team_consolidated_audit_plan()
        decisions = apply_workflow(plan)

        assert decisions.carried_over_phases == ["Compliance Sweep"]

    def test_consolidated_audit_gate_is_not_aliased_onto_implementation(self) -> None:
        # §5.6: the carryover phase's own gate must not double-run.
        plan = build_team_consolidated_audit_plan()
        apply_workflow(plan)

        implementation = phase_by_name(plan, "Implementation")
        assert implementation.gate is None or (
            implementation.gate is not plan.phases[-1].gate
        )

    def test_no_drop_warning_when_the_phase_is_carried_over(self) -> None:
        plan = build_team_consolidated_audit_plan()
        decisions = apply_workflow(plan)
        assert decisions.warnings == []

    def test_graph_invariants_hold(self) -> None:
        plan = build_team_consolidated_audit_plan()
        apply_workflow(plan)
        assert_graph_invariants(plan)


# ---------------------------------------------------------------------------
# Defect 3 — mixed phases lost their auditor via harvest/skip interaction
# ---------------------------------------------------------------------------

class TestMixedAuditPhaseCarryover:
    def test_auditor_bearing_phase_is_carried_over_whole(self) -> None:
        plan = build_mixed_audit_phase_plan()
        decisions = apply_workflow(plan)

        assert decisions.carried_over_phases == ["Audit"]
        carried = plan.phases[-1]
        assert [s.agent_name for s in carried.steps] == [
            "auditor",
            "backend-engineer",
        ]

    def test_fallback_harvest_skips_the_auditor_bearing_phase(self) -> None:
        # Fallback (a) must take the Refactor phase's developing step only --
        # taking the Audit phase's would strip the phase of carryover.
        plan = build_mixed_audit_phase_plan()
        apply_workflow(plan)

        assert [s.task_description for s in implementation_steps(plan)] == [
            "Extract the ledger export helpers"
        ]

    def test_auditor_step_is_not_silently_dropped(self) -> None:
        plan = build_mixed_audit_phase_plan()
        decisions = apply_workflow(plan)

        descriptions = [s.task_description for s in plan.all_steps]
        assert "Audit the SOX compliance controls" in descriptions
        assert not any("auditor" in warning for warning in decisions.warnings)

    def test_carryover_phase_keeps_its_approval(self) -> None:
        plan = build_mixed_audit_phase_plan()
        apply_workflow(plan)
        assert plan.phases[-1].approval_required is True

    def test_qualifying_phase_that_contributed_nothing_is_still_carried_over(
        self,
    ) -> None:
        # "Compliance Implementation" qualifies by NAME but yields no
        # harvested step (its only step is a reviewing auditor step), so it
        # must be carried over rather than discarded.
        plan = build_audit_only_qualifying_phase_plan()
        decisions = apply_workflow(plan)

        assert decisions.carried_over_phases == ["Compliance Implementation"]
        assert plan.phases[-1].steps[0].agent_name == "auditor"

    def test_auditor_inside_a_contributing_phase_still_warns_when_dropped(
        self, auditor_in_implement_phase_plan: MachinePlan
    ) -> None:
        # §5.7 keeps its ruling (inline audit work in a harvested phase is
        # NOT carried over) -- but the drop is now reported.
        decisions = apply_workflow(auditor_in_implement_phase_plan)

        assert decisions.carried_over_phases == []
        assert any("auditor" in warning for warning in decisions.warnings)
        assert any(
            entry["agent_name"] == "auditor" for entry in decisions.dropped_steps
        )

    def test_graph_invariants_hold(self) -> None:
        plan = build_mixed_audit_phase_plan()
        apply_workflow(plan)
        assert_graph_invariants(plan)


class TestDroppedStepVisibility:
    def test_dropped_developing_step_is_recorded(self) -> None:
        plan = build_dropped_developing_step_plan()
        decisions = apply_workflow(plan)

        dropped = {entry["task_description"] for entry in decisions.dropped_steps}
        assert "Merge the quota telemetry streams" in dropped

    def test_dropped_entry_names_its_base_phase_and_agent(self) -> None:
        plan = build_dropped_developing_step_plan()
        decisions = apply_workflow(plan)

        entry = next(
            e
            for e in decisions.dropped_steps
            if e["task_description"] == "Merge the quota telemetry streams"
        )
        assert entry["phase_name"] == "Synthesize"
        assert entry["agent_name"] == "data-engineer"
        assert entry["step_id"] == "2.1"

    def test_harvested_and_carried_over_steps_are_not_reported_as_dropped(
        self,
    ) -> None:
        plan = build_mixed_audit_phase_plan()
        decisions = apply_workflow(plan)

        dropped = {entry["task_description"] for entry in decisions.dropped_steps}
        assert "Extract the ledger export helpers" not in dropped
        assert "Audit the SOX compliance controls" not in dropped
        assert "Review the ledger slice" in dropped

    def test_dropped_steps_reach_plan_diagnostics_and_survive_to_dict(self) -> None:
        plan = build_dropped_developing_step_plan()
        apply_workflow(plan)

        payload = json.loads(json.dumps(plan.to_dict()))
        record = payload["plan_diagnostics"]["workflow"]
        assert any(
            entry["step_id"] == "2.1" for entry in record["dropped_steps"]
        )
        assert record["warnings"] == []

    def test_nothing_dropped_leaves_the_list_empty(self) -> None:
        plan = build_multi_base_phase_ordering_plan()
        decisions = apply_workflow(plan)
        assert decisions.dropped_steps == []

    def test_guard_recompute_preserves_the_dropped_record(self) -> None:
        # §5.1: a second apply reports the same decisions, discards included.
        plan = build_dropped_developing_step_plan()
        first = apply_workflow(plan)
        second = apply_workflow(plan)
        assert second == first

    def test_guard_recompute_preserves_warnings(
        self, auditor_in_implement_phase_plan: MachinePlan
    ) -> None:
        first = apply_workflow(auditor_in_implement_phase_plan)
        assert first.warnings, "fixture must produce a warning"
        second = apply_workflow(auditor_in_implement_phase_plan)
        assert second.warnings == first.warnings
        assert second == first


# ---------------------------------------------------------------------------
# Defect 4 — base-phase ordering lost on flatten
# ---------------------------------------------------------------------------

class TestBasePhaseOrdering:
    def test_later_phase_group_depends_on_the_prior_group(self) -> None:
        plan = build_multi_base_phase_ordering_plan()
        apply_workflow(plan)

        prepare, impl_a, impl_b, remediate = implementation_steps(plan)
        assert prepare.depends_on == []
        assert impl_a.depends_on == [prepare.step_id]
        assert impl_b.depends_on == [prepare.step_id]
        assert set(remediate.depends_on) == {impl_a.step_id, impl_b.step_id}

    def test_intra_phase_parallelism_is_preserved(self) -> None:
        plan = build_multi_base_phase_ordering_plan()
        apply_workflow(plan)

        _prepare, impl_a, impl_b, _remediate = implementation_steps(plan)
        # Same wave: identical deps, disjoint paths -> still parallel.
        assert impl_a.depends_on == impl_b.depends_on
        assert impl_a.parallel_safe is True
        assert impl_b.parallel_safe is True

    def test_serialized_step_is_no_longer_advertised_as_parallel(self) -> None:
        plan = build_multi_base_phase_ordering_plan()
        apply_workflow(plan)

        remediate = implementation_steps(plan)[-1]
        assert remediate.parallel_safe is False
        assert "(parallel)" not in _markdown_line_for(plan, remediate.step_id)

    def test_no_edges_added_for_a_single_base_phase(
        self, base_plan: MachinePlan
    ) -> None:
        # Single-origin harvests keep their original (re-keyed) edges only.
        apply_workflow(base_plan)
        first, second = implementation_steps(base_plan)
        assert first.depends_on == []
        assert second.depends_on == [first.step_id]
        assert first.parallel_safe is True

    def test_existing_cross_phase_edge_is_not_duplicated(
        self, harvest_predicate_plan: MachinePlan
    ) -> None:
        apply_workflow(harvest_predicate_plan)
        fix_step, build_step = implementation_steps(harvest_predicate_plan)[:2]
        assert build_step.depends_on == [fix_step.step_id]

    def test_ordering_is_a_chain_across_every_base_phase(
        self, harvest_predicate_plan: MachinePlan
    ) -> None:
        apply_workflow(harvest_predicate_plan)
        steps = implementation_steps(harvest_predicate_plan)
        # Six harvested steps, one per base phase, in base-phase order.
        for previous, current in zip(steps, steps[1:]):
            assert previous.step_id in current.depends_on

    def test_graph_invariants_hold(self) -> None:
        plan = build_multi_base_phase_ordering_plan()
        apply_workflow(plan)
        assert_graph_invariants(plan)


def _markdown_line_for(plan: MachinePlan, step_id: str) -> str:
    """The ``plan.md`` heading line rendering *step_id* -- where the
    ``(parallel)`` tag is emitted (``MachinePlan.to_markdown``)."""
    prefix = f"### Step {step_id}:"
    for line in plan.to_markdown().splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"step {step_id!r} is not rendered in plan.md")


# ---------------------------------------------------------------------------
# Defect 5 — the reshaped plan was never revalidated
# ---------------------------------------------------------------------------

class TestReshapedPlanValidation:
    def test_empty_configured_agent_raises_a_typed_error(
        self, base_plan: MachinePlan
    ) -> None:
        settings = WorkflowSettings(stages={"spec": StageOverride(agent="")})
        with pytest.raises(WorkflowReshapeError):
            apply_workflow(base_plan, settings=settings)

    def test_error_message_is_actionable(self, base_plan: MachinePlan) -> None:
        settings = WorkflowSettings(stages={"spec": StageOverride(agent="")})
        with pytest.raises(WorkflowReshapeError) as excinfo:
            apply_workflow(base_plan, settings=settings)

        message = str(excinfo.value)
        assert "adversarial-tdd" in message
        assert "agent" in message
        assert "empty agent name" in message

    def test_valid_reshape_does_not_raise(self, base_plan: MachinePlan) -> None:
        apply_workflow(base_plan)
        assert base_plan.workflow == "adversarial-tdd"

    def test_validation_covers_every_shipped_fixture(
        self, request: pytest.FixtureRequest
    ) -> None:
        for fixture_name in (
            "base_plan",
            "team_step_plan",
            "harvest_predicate_plan",
            "audit_carryover_plan",
            "automation_step_plan",
        ):
            plan: MachinePlan = request.getfixturevalue(fixture_name)
            apply_workflow(plan)
            assert_graph_invariants(plan)
