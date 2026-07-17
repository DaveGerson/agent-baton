"""Tests for ``planning.utils.phase_normalize`` — reference normalization
after a phase-restructuring mutation (Phase 6, step 6.1).

Covers:
1. No-op when nothing was renumbered (identity snapshot).
2. Stale "from phase N (" text baked in by ``phase_builder.enrich_phases``
   is rewritten to the phase's new number after a restructuring insert.
3. ``depends_on`` edges follow renumbered step_ids.
4. Team-member ``depends_on``/``task_description`` are normalized too.
5. Without a pre-snapshot, normalization is a safe no-op (nothing to
   diff against).
"""
from __future__ import annotations

import re

from agent_baton.core.engine.planning.utils.phase_normalize import (
    normalize_phase_references,
    snapshot_phase_state,
)
from agent_baton.models.execution import PlanPhase, PlanStep, TeamMember


def _phase(phase_id: int, name: str, steps: list[PlanStep]) -> PlanPhase:
    return PlanPhase(phase_id=phase_id, name=name, steps=steps)


class TestSnapshotAndNoop:
    def test_noop_when_nothing_changed(self) -> None:
        phases = [
            _phase(1, "Design", [PlanStep(step_id="1.1", agent_name="architect", task_description="Design it")]),
            _phase(
                2, "Implement",
                [PlanStep(
                    step_id="2.1", agent_name="backend-engineer",
                    task_description="Implement it. Build on the design output from phase 1 (architect).",
                )],
            ),
        ]
        pre_phase_ids, pre_step_ids = snapshot_phase_state(phases)
        result = normalize_phase_references(
            phases, pre_phase_ids=pre_phase_ids, pre_step_ids=pre_step_ids,
        )
        assert result is phases
        assert phases[1].steps[0].task_description == (
            "Implement it. Build on the design output from phase 1 (architect)."
        )

    def test_noop_without_snapshot(self) -> None:
        phases = [_phase(1, "Implement", [PlanStep(step_id="1.1", agent_name="x", task_description="y")])]
        result = normalize_phase_references(phases)
        assert result is phases
        assert phases[0].phase_id == 1


class TestStaleReferenceRewriting:
    def test_baked_from_phase_text_follows_renumbering(self) -> None:
        """Simulate what ForesightEngine.analyze does: mutate phase_id/
        step_id on the SAME objects in place after a phase gets inserted
        ahead of an existing one, then verify normalize_phase_references
        repairs the now-stale baked-in "from phase 1" text.
        """
        design_step = PlanStep(step_id="1.1", agent_name="architect", task_description="Design it")
        impl_step = PlanStep(
            step_id="2.1",
            agent_name="backend-engineer",
            task_description=(
                "Implement it. Build on the design output from phase 1 (architect)."
            ),
        )
        design_phase = _phase(1, "Design", [design_step])
        impl_phase = _phase(2, "Implement", [impl_step])
        phases = [design_phase, impl_phase]

        pre_phase_ids, pre_step_ids = snapshot_phase_state(phases)

        # Simulate foresight inserting a new phase 1 ("Prep") ahead of
        # Design, pushing Design -> 2 and Implement -> 3 (in-place
        # mutation on the SAME objects, exactly like ForesightEngine).
        prep_step = PlanStep(step_id="1.1", agent_name="devops-engineer", task_description="Prep")
        prep_phase = _phase(1, "Prep", [prep_step])
        design_phase.phase_id = 2
        design_step.step_id = "2.1"
        impl_phase.phase_id = 3
        impl_step.step_id = "3.1"
        phases = [prep_phase, design_phase, impl_phase]

        normalize_phase_references(phases, pre_phase_ids=pre_phase_ids, pre_step_ids=pre_step_ids)

        assert "from phase 2 (" in impl_step.task_description
        assert "from phase 1 (" not in impl_step.task_description

    def test_depends_on_follows_step_id_renumbering(self) -> None:
        a = PlanStep(step_id="1.1", agent_name="x", task_description="a")
        b = PlanStep(step_id="2.1", agent_name="y", task_description="b", depends_on=["1.1"])
        phase_a = _phase(1, "A", [a])
        phase_b = _phase(2, "B", [b])
        phases = [phase_a, phase_b]

        pre_phase_ids, pre_step_ids = snapshot_phase_state(phases)

        # Renumber a's step_id (simulating a restructuring insert before it).
        a.step_id = "2.1"
        phase_a.phase_id = 2
        b.step_id = "3.1"
        phase_b.phase_id = 3

        normalize_phase_references(phases, pre_phase_ids=pre_phase_ids, pre_step_ids=pre_step_ids)

        assert b.depends_on == ["2.1"]

    def test_unrelated_text_is_left_untouched(self) -> None:
        """A director-authored task summary mentioning 'phase 3' for its
        own reasons must never be rewritten — only the narrow, self-
        generated 'from phase N (' pattern is a rewrite target.
        """
        step = PlanStep(
            step_id="1.1",
            agent_name="architect",
            task_description="This work is phase 3 of the migration; do not confuse with an earlier attempt.",
        )
        phase = _phase(1, "Design", [step])
        phases = [phase]
        pre_phase_ids, pre_step_ids = snapshot_phase_state(phases)
        phase.phase_id = 2
        step.step_id = "2.1"
        normalize_phase_references(phases, pre_phase_ids=pre_phase_ids, pre_step_ids=pre_step_ids)
        assert "phase 3 of the migration" in step.task_description

    def test_team_member_depends_on_and_text_normalized(self) -> None:
        design_step = PlanStep(step_id="1.1", agent_name="architect", task_description="Design it")
        design_phase = _phase(1, "Design", [design_step])

        member = TeamMember(
            member_id="2.1.a",
            agent_name="code-reviewer",
            task_description="Build on the design output from phase 1 (architect).",
            depends_on=["1.1"],
        )
        review_step = PlanStep(
            step_id="2.1", agent_name="team", task_description="Team review", team=[member],
        )
        review_phase = _phase(2, "Review", [review_step])
        phases = [design_phase, review_phase]

        pre_phase_ids, pre_step_ids = snapshot_phase_state(phases)

        # Simulate a phase inserted ahead of Design: Design 1->2 (step
        # 1.1->2.1), Review 2->3 (step 2.1->3.1) -- both objects present
        # in the snapshot, so both the depends_on edge and the baked
        # "from phase 1" text have a mapping to follow.
        design_phase.phase_id = 2
        design_step.step_id = "2.1"
        review_phase.phase_id = 3
        review_step.step_id = "3.1"

        normalize_phase_references(phases, pre_phase_ids=pre_phase_ids, pre_step_ids=pre_step_ids)

        assert member.depends_on == ["2.1"]
        assert "from phase 2 (" in member.task_description


class TestDecompositionStageForesightIntegration:
    """End-to-end: DecompositionStage._apply_foresight is where the real
    ForesightEngine can insert a phase ahead of an existing one, and
    where normalize_phase_references is actually wired in.
    """

    def test_no_stale_phase_reference_after_real_foresight_insertion(self) -> None:
        from agent_baton.core.engine.planner import IntelligentPlanner
        from agent_baton.core.engine.planning.draft import PlanDraft
        from agent_baton.core.engine.planning.stages.decomposition import DecompositionStage

        planner = IntelligentPlanner()
        services = planner._build_services(knowledge_registry=planner.knowledge_registry)

        design_step = PlanStep(
            step_id="1.1", agent_name="architect",
            task_description="Design the migration approach",
        )
        design_phase = PlanPhase(phase_id=1, name="Design", steps=[design_step])

        impl_step = PlanStep(
            step_id="2.1",
            agent_name="backend-engineer",
            task_description=(
                "Migrate the database schema: alter table users. "
                "Build on the design output from phase 1 (architect)."
            ),
        )
        impl_phase = PlanPhase(phase_id=2, name="Implement", steps=[impl_step])

        # "dropping" (any-agent-triggered "destructive-safety" rule) is in
        # the *task summary*, so it's visible to every step's combined
        # text -- including Design's, which is walked first -- and inserts
        # a prep phase ahead of Design itself, shifting Design's own
        # phase_id. That's what makes the baked "from phase 1" reference
        # genuinely stale (as opposed to the migration-rollback rule
        # below, which only inserts ahead of Implement and leaves Design
        # untouched).
        draft = PlanDraft.from_inputs(
            "Migrate the database schema by dropping a legacy column"
        )
        draft.plan_phases = [design_phase, impl_phase]
        draft.risk_level = "MEDIUM"
        draft.resolved_agents = ["architect", "backend-engineer"]

        stage = DecompositionStage()
        new_phases = stage._apply_foresight(
            plan_phases=draft.plan_phases, draft=draft, services=services,
        )

        assert len(new_phases) > 2, (
            "a migration-related foresight rule should have inserted at "
            f"least one prep phase; got phases: {[p.name for p in new_phases]}"
        )
        # Every "from phase N (" reference must still resolve to a real
        # phase in the final list, and that phase must still be the one
        # originally referenced (the "Design" phase) -- not a stale
        # number that now happens to land on one of the newly-inserted
        # prep phases or the Implement phase itself.
        by_id = {p.phase_id: p for p in new_phases}
        found_a_reference = False
        for phase in new_phases:
            for step in phase.steps:
                for match in re.finditer(r"from phase (\d+) \(", step.task_description):
                    found_a_reference = True
                    referenced_id = int(match.group(1))
                    referenced_phase = by_id.get(referenced_id)
                    assert referenced_phase is not None, (
                        f"step {step.step_id} in phase {phase.phase_id} "
                        f"references phase {referenced_id}, which no longer exists"
                    )
                    assert referenced_phase.name == "Design", (
                        f"step {step.step_id} in phase {phase.phase_id} "
                        f"references phase {referenced_id} ({referenced_phase.name!r}), "
                        "expected it to still resolve to the Design phase"
                    )
        assert found_a_reference, "test setup should have produced a baked phase reference to check"
