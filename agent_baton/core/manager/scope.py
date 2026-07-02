"""``ScopeMapBuilder`` -- workstream decomposition of a manager-mode plan (M2).

See docs/internal/manager-mode-pmo-plan.md Wave 1 / Task 5 and PRD §4.1 /
§10.2 / §16 Milestone 2. Deterministic: one :class:`Workstream` per plan
phase, derived from the ``MachinePlan`` and the already-built
:class:`ProjectCharter` -- no clock, no randomness, no LLM calls.
"""
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from agent_baton.models.manager import ProjectCharter, ScopeMap, Workstream

if TYPE_CHECKING:
    from agent_baton.core.config.manager import ManagerConfig
    from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


class ScopeMapBuilder:
    """Builds a :class:`ScopeMap` from a plan and its already-built charter.

    One workstream per plan phase (``id=f"ws-{i}"``, 1-based in phase
    order). This also covers the "light complexity + single phase" case
    from the PRD: a single-phase plan naturally yields exactly one
    workstream without any special-casing.
    """

    def __init__(self, config: "ManagerConfig") -> None:
        self.config = config

    def build(self, charter: ProjectCharter, plan: "MachinePlan") -> ScopeMap:
        phase_to_ws_id = {
            phase.phase_id: f"ws-{index}"
            for index, phase in enumerate(plan.phases, start=1)
        }
        ordered_phase_ids = list(phase_to_ws_id)
        step_to_phase_id = {
            step.step_id: phase.phase_id
            for phase in plan.phases
            for step in phase.steps
        }

        workstreams = [
            self._build_workstream(
                phase,
                charter,
                plan,
                phase_to_ws_id=phase_to_ws_id,
                ordered_phase_ids=ordered_phase_ids,
                step_to_phase_id=step_to_phase_id,
            )
            for phase in plan.phases
        ]

        return ScopeMap(
            task_id=charter.task_id,
            workstreams=workstreams,
            cross_cutting_concerns=_cross_cutting_concerns(plan),
            out_of_scope=list(charter.out_of_scope),
            scope_expansion_policy=self.config.scoping.scope_expansion_policy,
        )

    def _build_workstream(
        self,
        phase: "PlanPhase",
        charter: ProjectCharter,
        plan: "MachinePlan",
        *,
        phase_to_ws_id: dict[int, str],
        ordered_phase_ids: list[int],
        step_to_phase_id: dict[str, int],
    ) -> Workstream:
        ws_id = phase_to_ws_id[phase.phase_id]
        steps = phase.steps

        owner_role = _modal_value([s.agent_name for s in steps if s.agent_name])

        deliverables: list[str] = []
        for step in steps:
            for deliverable in step.deliverables:
                if deliverable not in deliverables:
                    deliverables.append(deliverable)

        allowed_paths: list[str] = []
        for step in steps:
            for path in step.allowed_paths:
                if path not in allowed_paths:
                    allowed_paths.append(path)
        if not allowed_paths:
            allowed_paths = list(charter.likely_repo_areas)

        likely_paths: list[str] = []
        for step in steps:
            for raw_path in (*step.allowed_paths, *step.context_files):
                segment = _first_segment(raw_path)
                if segment and segment not in likely_paths:
                    likely_paths.append(segment)
        if not likely_paths:
            likely_paths = list(charter.likely_repo_areas)

        dependencies = _dependencies(
            phase,
            steps,
            ws_id,
            phase_to_ws_id=phase_to_ws_id,
            ordered_phase_ids=ordered_phase_ids,
            step_to_phase_id=step_to_phase_id,
        )

        if phase.risk_level:
            risks = [f"Phase risk level: {phase.risk_level}."]
        else:
            risks = [
                f"No explicit risk level set for phase '{phase.name}'; "
                f"inherits plan-level risk ({plan.risk_level})."
            ]

        return Workstream(
            id=ws_id,
            name=phase.name,
            objective=phase.name or f"Workstream {ws_id}",
            likely_paths=likely_paths,
            allowed_paths=allowed_paths,
            owner_role=owner_role,
            dependencies=dependencies,
            deliverables=deliverables,
            risks=risks,
        )


def _modal_value(values: list[str]) -> str:
    """First value achieving the highest occurrence count.

    Deterministic tie-break: iterates in original order and returns the
    first value whose count equals the maximum, rather than relying on
    ``Counter``'s undocumented ordering.
    """
    if not values:
        return ""
    counts = Counter(values)
    top = max(counts.values())
    for value in values:
        if counts[value] == top:
            return value
    return values[0]


def _first_segment(path_str: str) -> str:
    normalized = path_str.strip().replace("\\", "/").strip("/")
    if not normalized:
        return ""
    return normalized.split("/", 1)[0]


def _dependencies(
    phase: "PlanPhase",
    steps: list["PlanStep"],
    ws_id: str,
    *,
    phase_to_ws_id: dict[int, str],
    ordered_phase_ids: list[int],
    step_to_phase_id: dict[str, int],
) -> list[str]:
    """``ws-<j>`` for any cross-phase ``depends_on`` edge; falls back to
    the previous phase's workstream when a phase has no explicit
    cross-phase dependency (a phased plan is inherently sequential)."""
    dependencies: list[str] = []
    for step in steps:
        for dep_step_id in step.depends_on:
            dep_phase_id = step_to_phase_id.get(dep_step_id)
            if dep_phase_id is None or dep_phase_id == phase.phase_id:
                continue
            dep_ws_id = phase_to_ws_id.get(dep_phase_id)
            if dep_ws_id and dep_ws_id != ws_id and dep_ws_id not in dependencies:
                dependencies.append(dep_ws_id)

    if not dependencies and ordered_phase_ids and phase.phase_id != ordered_phase_ids[0]:
        idx = ordered_phase_ids.index(phase.phase_id)
        prev_phase_id = ordered_phase_ids[idx - 1]
        dependencies.append(phase_to_ws_id[prev_phase_id])

    return dependencies


def _cross_cutting_concerns(plan: "MachinePlan") -> list[str]:
    """Step types recurring across more than one phase (e.g. "testing" in
    every phase) are treated as cross-cutting concerns."""
    phases_by_step_type: dict[str, set[int]] = {}
    for phase in plan.phases:
        for step in phase.steps:
            phases_by_step_type.setdefault(step.step_type, set()).add(phase.phase_id)
    return sorted(
        step_type
        for step_type, phases in phases_by_step_type.items()
        if len(phases) > 1
    )
