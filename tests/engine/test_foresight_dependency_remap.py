"""Regression tests for dependency-edge preservation in ``ForesightEngine.analyze``.

``analyze()`` inserts preparatory phases and then renumbers every phase and
every step id.  ``PlanStep.depends_on`` holds *step ids*, so a renumber that
does not rewrite those references silently re-points dependency edges at
whichever step happens to occupy the old id afterwards — usually a
foresight-inserted "Prepare" step.

These tests pin the behaviour: after ``analyze()``, every dependency edge must
still point at the same *step object* it pointed at before the call.
"""
from __future__ import annotations

from agent_baton.core.engine.foresight import ForesightEngine
from agent_baton.models.execution import PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _edge_map(phases: list[PlanPhase]) -> dict[int, list[int]]:
    """Map ``id(step) -> [id(dependency step), ...]`` for a phase list.

    Resolving edges to Python object identity makes the assertion independent
    of the numbering scheme: it asks "does this step still depend on the same
    work item?", not "does it carry the same string?".
    """
    by_step_id: dict[str, PlanStep] = {}
    for phase in phases:
        for step in phase.steps:
            by_step_id[step.step_id] = step

    edges: dict[int, list[int]] = {}
    for phase in phases:
        for step in phase.steps:
            resolved: list[int] = []
            for dep in step.depends_on:
                target = by_step_id.get(dep)
                # ``None`` (dangling) is encoded as a sentinel so a dangling
                # edge can never accidentally compare equal to a real one.
                resolved.append(id(target) if target is not None else -1)
            edges[id(step)] = resolved
    return edges


def _all_steps(phases: list[PlanPhase]) -> list[PlanStep]:
    return [step for phase in phases for step in phase.steps]


def _investigate_fix_phases() -> list[PlanPhase]:
    """Two phases where Fix depends on Investigate, and phase 1 fires a rule.

    The step text in phase 1 trips ``foresight-migration-rollback``, so the
    engine inserts a Prepare phase *before* phase 1 and shifts everything down
    by one.
    """
    investigate = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer",
        task_description=(
            "Investigate the failing database migration and identify the root cause"
        ),
    )
    fix = PlanStep(
        step_id="2.1",
        agent_name="backend-engineer",
        task_description="Apply the fix identified by the investigation",
        depends_on=["1.1"],
    )
    return [
        PlanPhase(phase_id=1, name="Investigate", steps=[investigate]),
        PlanPhase(phase_id=2, name="Fix", steps=[fix]),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestForesightDependencyRemap:
    """``analyze()`` must rewrite ``depends_on`` when it renumbers steps."""

    def test_dependency_follows_renumbered_step(self):
        """Fix must still depend on Investigate, not on the inserted step."""
        phases = _investigate_fix_phases()
        investigate = phases[0].steps[0]
        fix = phases[1].steps[0]

        result, insights = ForesightEngine().analyze(
            phases, "Fix the failing database migration"
        )

        assert insights, "expected a foresight insertion for this scenario"

        # The insertion landed before the original first phase, so Investigate
        # is now step 2.1 and Fix is now step 3.1.
        assert investigate.step_id == "2.1"
        assert fix.step_id == "3.1"

        # The edge must have been rewritten to follow the renumbering.
        assert fix.depends_on == [investigate.step_id]

    def test_no_step_depends_on_an_inserted_foresight_step(self):
        """Pre-existing work must never be re-pointed at a Prepare step."""
        phases = _investigate_fix_phases()
        original_steps = set(map(id, _all_steps(phases)))

        result, insights = ForesightEngine().analyze(
            phases, "Fix the failing database migration"
        )

        inserted_ids: set[str] = set()
        for insight in insights:
            inserted_ids.update(insight.inserted_step_ids)
        assert inserted_ids, "expected at least one inserted foresight step"

        for step in _all_steps(result):
            if id(step) not in original_steps:
                continue  # the inserted steps themselves
            overlap = set(step.depends_on) & inserted_ids
            assert not overlap, (
                f"pre-existing step {step.step_id!r} was silently re-pointed at "
                f"foresight-inserted step(s) {sorted(overlap)}"
            )

    def test_every_edge_still_resolves_to_the_same_step_object(self):
        """Property check: the dependency graph is preserved under renumbering."""
        phases = _investigate_fix_phases()
        before = _edge_map(phases)

        result, _insights = ForesightEngine().analyze(
            phases, "Fix the failing database migration"
        )

        after = _edge_map(result)
        for step in _all_steps(result):
            if id(step) not in before:
                continue  # inserted step — no prior edges to preserve
            assert after[id(step)] == before[id(step)], (
                f"dependency edges for step {step.step_id!r} "
                f"({step.task_description[:40]!r}) changed target under renumbering"
            )
            assert -1 not in after[id(step)], (
                f"step {step.step_id!r} has a dangling dependency: "
                f"{step.depends_on}"
            )

    def test_intra_phase_dependencies_survive_renumbering(self):
        """Edges between two steps of the same phase must also be rewritten."""
        first = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description=(
                "Write the reversible database migration that alters the table"
            ),
        )
        second = PlanStep(
            step_id="1.2",
            agent_name="backend-engineer",
            task_description="Review the migration script produced by the prior step",
            depends_on=["1.1"],
        )
        phases = [PlanPhase(phase_id=1, name="Implement", steps=[first, second])]

        result, insights = ForesightEngine().analyze(phases, "Ship a schema change")

        assert insights, "expected a foresight insertion for this scenario"
        assert second.depends_on == [first.step_id]
        assert first.step_id != "1.1", (
            "scenario is only meaningful when renumbering actually moved the steps"
        )

    def test_unmappable_dependency_is_dropped_not_silently_retained(self):
        """A dangling edge must not be left pointing at an unrelated step."""
        first = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description=(
                "Write the reversible database migration that alters the table"
            ),
        )
        # "9.9" refers to a step that does not exist in this plan.
        second = PlanStep(
            step_id="1.2",
            agent_name="backend-engineer",
            task_description="Review the migration script produced by the prior step",
            depends_on=["1.1", "9.9"],
        )
        phases = [PlanPhase(phase_id=1, name="Implement", steps=[first, second])]

        result, _insights = ForesightEngine().analyze(phases, "Ship a schema change")

        known_ids = {step.step_id for step in _all_steps(result)}
        for dep in second.depends_on:
            assert dep in known_ids, (
                f"dependency {dep!r} does not resolve to any step in the plan"
            )
        assert first.step_id in second.depends_on
