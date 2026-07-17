"""``ScopeMapBuilder`` -- workstream decomposition of a manager-mode plan (M2).

See docs/internal/manager-mode-pmo-plan.md Wave 1 / Task 5 and PRD §4.1 /
§10.2 / §16 Milestone 2. Deterministic: one :class:`Workstream` per plan
phase, derived from the ``MachinePlan`` and the already-built
:class:`ProjectCharter` -- no clock, no randomness, no LLM calls.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.engine.planning.scope_contract import (
    ScopeContractError,
    derive_allowed_paths,
    diagnose_step_scope,
    is_write_capable,
    normalize_path_list,
)
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

    Write-scope derivation is deterministic (see ``agent_baton.core.
    engine.planning.scope_contract``): a workstream's ``allowed_paths`` is
    the normalized union of its steps' explicit paths, falling back
    through deliverable/context-file/repo-topology/agent-role evidence
    tiers only when no step supplied any -- never advisory guessing. A
    workstream whose steps are all intentionally read-only (see
    ``scope_contract.READ_ONLY_STEP_TYPES``) is left with an empty
    ``allowed_paths`` deliberately -- that is the valid representation of
    "this workstream does not write", not an omission to paper over.
    """

    def __init__(self, config: "ManagerConfig") -> None:
        self.config = config

    def build(
        self,
        charter: ProjectCharter,
        plan: "MachinePlan",
        *,
        project_root: "Path | None" = None,
        strict: bool = False,
        diagnostics: "list[str] | None" = None,
    ) -> ScopeMap:
        """Build the scope map for *plan*.

        *project_root*, when supplied and a real directory, unlocks the
        agent-role evidence tier of :func:`derive_allowed_paths` (role
        conventions are only ever used to select among directories
        confirmed to exist on disk -- never to invent one). Optional; the
        map still builds without it, just without that last fallback
        tier.

        *strict*, when ``True``, raises :class:`ScopeContractError` for a
        workstream that contains a write-capable step
        (``scope_contract.WRITE_CAPABLE_STEP_TYPES``) yet ends up with an
        empty, normalized ``allowed_paths`` -- i.e. ambiguous write scope.
        Defaults to ``False`` so existing advisory-mode callers are
        unaffected; ``agent_baton.core.manager.planner.ManagerModePlanner``
        opts in via its own ``strict_scope`` flag.

        *diagnostics*, when supplied, has one human-readable string
        appended per :class:`~agent_baton.core.engine.planning.
        scope_contract.ScopeDiagnostic` raised while building (regardless
        of *strict* -- diagnostics are always collected when a list is
        given; *strict* only controls whether ambiguous write scope also
        raises).
        """
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
        existing_dirs = _existing_dirs(project_root)

        workstreams = [
            self._build_workstream(
                phase,
                charter,
                plan,
                phase_to_ws_id=phase_to_ws_id,
                ordered_phase_ids=ordered_phase_ids,
                step_to_phase_id=step_to_phase_id,
                existing_dirs=existing_dirs,
                strict=strict,
                diagnostics=diagnostics,
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
        existing_dirs: frozenset[str],
        strict: bool,
        diagnostics: "list[str] | None",
    ) -> Workstream:
        ws_id = phase_to_ws_id[phase.phase_id]
        steps = phase.steps

        owner_role = _modal_value([s.agent_name for s in steps if s.agent_name])

        deliverables: list[str] = []
        for step in steps:
            for deliverable in step.deliverables:
                if deliverable not in deliverables:
                    deliverables.append(deliverable)

        allowed_paths = self._derive_workstream_allowed_paths(
            steps,
            charter,
            existing_dirs=existing_dirs,
            strict=strict,
            diagnostics=diagnostics,
        )

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

    def _derive_workstream_allowed_paths(
        self,
        steps: list["PlanStep"],
        charter: ProjectCharter,
        *,
        existing_dirs: frozenset[str],
        strict: bool,
        diagnostics: "list[str] | None",
    ) -> list[str]:
        """Deterministic write-scope for a phase's workstream.

        Every step's explicit ``allowed_paths`` is normalized and unioned
        first (decomposition evidence -- unchanged in spirit from the
        pre-existing behavior, just normalized). Only when that union is
        empty AND the phase contains at least one write-capable step
        (``scope_contract.WRITE_CAPABLE_STEP_TYPES``) does the fallback
        chain run, one step at a time in
        :func:`~agent_baton.core.engine.planning.scope_contract.
        derive_allowed_paths`'s tier order -- deliverables, context files,
        repo topology, agent role -- stopping at the first step that
        yields anything. A workstream whose steps are all intentionally
        read-only (see ``scope_contract.READ_ONLY_STEP_TYPES``) is left
        empty deliberately: that is the valid representation of a
        review-only phase, not an omission.
        """
        explicit = normalize_path_list(
            [path for step in steps for path in step.allowed_paths]
        )
        if explicit:
            self._record_diagnostics(steps, explicit, strict=strict, diagnostics=diagnostics)
            return explicit

        has_write_capable_step = any(is_write_capable(step.step_type) for step in steps)
        if not has_write_capable_step:
            # Every step is intentionally read-only (or an unclassified
            # step_type this contract doesn't enforce) -- an empty
            # allowed_paths is the correct representation, not a gap.
            # Contradiction checks (allowed vs. blocked collisions) still
            # apply regardless of step type, so diagnostics still run.
            self._record_diagnostics(steps, [], strict=strict, diagnostics=diagnostics)
            return []

        derived: list[str] = []
        for step in steps:
            paths, _source = derive_allowed_paths(
                explicit_paths=step.allowed_paths,
                deliverables=step.deliverables,
                context_files=step.context_files,
                likely_repo_areas=charter.likely_repo_areas,
                agent_base=step.agent_name,
                existing_dirs=existing_dirs,
            )
            for path in paths:
                if path not in derived:
                    derived.append(path)
            if derived:
                break

        self._record_diagnostics(steps, derived, strict=strict, diagnostics=diagnostics)
        return derived

    @staticmethod
    def _record_diagnostics(
        steps: list["PlanStep"],
        resolved_allowed_paths: list[str],
        *,
        strict: bool,
        diagnostics: "list[str] | None",
    ) -> None:
        """Diagnose every step in the workstream against its *final*
        resolved ``allowed_paths`` (the workstream's contract, mirroring
        what ``ScopeContractBuilder`` will hand each step at dispatch
        time). Appends a message per finding to *diagnostics* when
        supplied; raises :class:`ScopeContractError` on the first finding
        when *strict* is set.
        """
        for step in steps:
            effective_allowed_paths = (
                step.allowed_paths if step.allowed_paths else resolved_allowed_paths
            )
            diagnostic = diagnose_step_scope(
                step.step_id,
                step.step_type,
                effective_allowed_paths,
                step.blocked_paths,
            )
            if diagnostic is None:
                continue
            if diagnostics is not None:
                diagnostics.append(str(diagnostic))
            # Contradictory scope (allowed/blocked collision) is never
            # valid, regardless of strict mode; ambiguous ("missing")
            # scope only raises when the caller opted into strict mode.
            if diagnostic.severity == "critical" or strict:
                raise ScopeContractError(str(diagnostic))


def _existing_dirs(project_root: "Path | None") -> frozenset[str]:
    """Top-level directory names that actually exist under *project_root*.

    Empty when *project_root* is ``None`` or not a real directory -- the
    agent-role evidence tier only ever selects among confirmed-real
    directories (see ``scope_contract.derive_allowed_paths``), never
    invents one.
    """
    if project_root is None:
        return frozenset()
    root = Path(project_root)
    if not root.is_dir():
        return frozenset()
    return frozenset(
        entry.name
        for entry in root.iterdir()
        if entry.is_dir() and not entry.name.startswith(".")
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
