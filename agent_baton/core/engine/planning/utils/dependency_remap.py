"""Shared ``depends_on`` remap helper.

Two places in the planning pipeline rewrite ``PlanStep.step_id`` values
after phases have already been built, which means every ``depends_on``
edge pointing at an old id must be rewritten too or ``MachinePlan``'s
plan-graph invariant (models/execution.py) rejects the plan:

* ``ForesightEngine.analyze`` (engine/foresight.py) renumbers *every*
  pre-existing step id when it inserts preparatory phases.
* ``ValidationStage._consolidate_team`` (planning/stages/validation.py)
  collapses a multi-step phase into a single ``agent="team"`` step via
  ``consolidate_team_step`` (planning/utils/phase_builder.py), which
  deletes every non-surviving step id in that phase.

Both paths funnel through :func:`remap_dependencies` so the remap
semantics (self-dependency guard, duplicate collapsing, missing-id
handling) live in exactly one place.
"""
from __future__ import annotations

import logging

from agent_baton.models.execution import PlanPhase

logger = logging.getLogger(__name__)


def remap_dependencies(
    phases: list[PlanPhase],
    id_remap: dict[str, str],
    *,
    drop_unmapped: bool = False,
) -> None:
    """Rewrite every step's ``depends_on`` through ``id_remap``, in place.

    For each dependency id on each step in ``phases``:

    * If it appears in ``id_remap``, it is rewritten to the mapped id.
    * If it does not appear in ``id_remap``: it is left unchanged when
      ``drop_unmapped`` is ``False`` (the default — appropriate when
      ``id_remap`` only covers the ids that actually moved, e.g. team
      consolidation, so an untouched id is legitimately still valid).
      When ``drop_unmapped`` is ``True`` the edge is dropped with a
      warning instead (appropriate when ``id_remap`` is guaranteed to
      cover every pre-existing id, e.g. foresight renumbering, so a miss
      means the id never existed).
    * A dependency that would resolve to the step's own id (a
      self-dependency introduced by the collapse) is dropped with a
      warning rather than silently accepted — ``MachinePlan`` does not
      catch self-dependencies, and an undetected one would deadlock the
      dispatcher.
    * Duplicate ids introduced by the remap (e.g. two edges collapsing
      onto the same surviving step) are collapsed, preserving order.
    """
    for phase in phases:
        for step in phase.steps:
            if not step.depends_on:
                continue
            remapped: list[str] = []
            for dep in step.depends_on:
                if dep in id_remap:
                    new_dep = id_remap[dep]
                elif drop_unmapped:
                    logger.warning(
                        "Dependency remap dropped unmappable dependency "
                        "%r on step %r",
                        dep, step.step_id,
                    )
                    continue
                else:
                    new_dep = dep

                if new_dep == step.step_id:
                    logger.warning(
                        "Dependency remap dropped self-dependency on "
                        "step %r (was %r)",
                        step.step_id, dep,
                    )
                    continue

                if new_dep not in remapped:
                    remapped.append(new_dep)

            step.depends_on = remapped
