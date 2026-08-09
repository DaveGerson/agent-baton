"""``WorkflowApplier`` — reshapes an assembled ``MachinePlan`` into a named
workflow preset's stage phases. Spec: docs/internal/adversarial-tdd-workflow-design.md §5.

Pure post-processor: no clock reads, no filesystem/network IO. Any
IO-dependent input (the stack-detected fallback gate) is computed by the CLI
wiring layer (``cli/commands/execution/plan_cmd.py``) and passed in via
``fallback_gate``. Mirrors ``core/manager/phase_policy.py``'s
``PhasePolicyApplier`` — the other pure post-processor that mutates a
``MachinePlan`` after ``IntelligentPlanner.create_plan()``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from agent_baton.core.config.workflow import WorkflowSettings
from agent_baton.core.workflow.presets import WorkflowPreset, WorkflowStage
from agent_baton.models.execution import (
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    TeamMember,
)
from agent_baton.models.taxonomy import _PHASE_NAME_TO_ARCHETYPE, PhaseArchetype

# §5.2: explicit-only archetype mapping — the phase_archetype() fallback to
# IMPLEMENTATION for unrecognized names must NOT be reused here (it would
# harvest compound names like "Security Review").
_HARVEST_ARCHETYPES = {
    PhaseArchetype.IMPLEMENTATION,
    PhaseArchetype.PREPARATION,
    PhaseArchetype.REMEDIATION,
}
_EXCLUDED_HARVEST_STEP_TYPES = {"reviewing", "planning"}
_UNRETIERED_STEP_TYPES = {"automation", "task"}
_GATE_TYPES_THAT_MOVE = {"test", "build"}
_IMPLEMENT_FALLBACK_AGENT = "backend-engineer"
_CARRYOVER_STAGE_ID = "carryover"

# §3 Notes ("Research support"): every applier-created stage briefing states
# that the orchestrator may dispatch sonnet general-purpose/domain agents for
# research at any stage -- briefing guidance only, never an extra planned
# step. Appended verbatim to every created (non-automation) step's briefing
# so the sentence is byte-identical across stages.
_RESEARCH_SUPPORT_NOTE = (
    " The orchestrator may dispatch sonnet general-purpose/domain agents "
    "for research support at any stage."
)


@dataclass
class WorkflowDecisions:
    """Pure summary of one :meth:`WorkflowApplier.apply` call.

    ``stages`` entries are supersets of ``{stage_id, phase_name, agents,
    model, steps}`` — ``model`` is the EFFECTIVE tier (after
    :class:`WorkflowSettings` overrides), ``agents`` is the list of distinct
    agent names dispatched in that phase, and ``steps`` is the step count.
    """

    workflow: str
    stages: list[dict[str, Any]] = field(default_factory=list)
    implementation_units: int = 0
    final_review_reviewers: int = 0
    external_verifier: bool = False
    carried_over_phases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "stages": [dict(entry) for entry in self.stages],
            "implementation_units": self.implementation_units,
            "final_review_reviewers": self.final_review_reviewers,
            "external_verifier": self.external_verifier,
            "carried_over_phases": list(self.carried_over_phases),
        }


# ---------------------------------------------------------------------------
# Harvest predicate (§5.2)
# ---------------------------------------------------------------------------

def _harvest_phase_key(name: str) -> str:
    """Normalize *name* the same way ``ValidationStage._phase_key`` does:
    colon-strip, last-word keying, then the implementation/development
    aliases. See ``core/engine/planning/stages/validation.py``."""
    raw = (name or "").lower().split(":")[0].strip()
    key = raw.split()[-1] if raw else ""
    if key == "implementation":
        return "implement"
    if key == "development":
        return "develop"
    return key


def _phase_qualifies_for_harvest(phase: PlanPhase) -> bool:
    archetype = _PHASE_NAME_TO_ARCHETYPE.get(_harvest_phase_key(phase.name))
    return archetype in _HARVEST_ARCHETYPES


def _harvest_steps(phases: list[PlanPhase]) -> list[PlanStep]:
    """§5.2: harvest implementation-like steps, with fallbacks (a)/(b).

    Fallback (a) (amended) fires whenever the primary predicate yields no
    harvested steps at all -- either because no phase qualifies, OR because
    every qualifying phase's steps are all excluded step types (e.g. a
    qualifying "Implementation" phase containing only a reviewing step).
    Gated on ``not harvested``, not on ``not qualifying``.
    """
    qualifying = [p for p in phases if _phase_qualifies_for_harvest(p)]
    harvested: list[PlanStep] = []
    for phase in qualifying:
        for step in phase.steps:
            if step.step_type not in _EXCLUDED_HARVEST_STEP_TYPES:
                harvested.append(step)
    if not harvested:
        # Fallback (a): harvest every "developing" step anywhere in the plan.
        for phase in phases:
            for step in phase.steps:
                if step.step_type == "developing":
                    harvested.append(step)
    return harvested


def _synthesize_fallback_step(plan: MachinePlan, model: str) -> PlanStep:
    """Fallback (b): no harvestable step anywhere — synthesize one."""
    return PlanStep(
        step_id="synthesized",
        agent_name=_IMPLEMENT_FALLBACK_AGENT,
        task_description=f"Implement: {plan.task_summary}.",
        model=model,
        step_type="developing",
        workflow_stage="implementation",
    )


def _carryover_phases(phases: list[PlanPhase], harvested_ids: set[str]) -> list[PlanPhase]:
    """§5.7: non-harvested base phases containing at least one auditor step."""
    result: list[PlanPhase] = []
    for phase in phases:
        if any(step.step_id in harvested_ids for step in phase.steps):
            continue
        if any(step.agent_name.split("--")[0] == "auditor" for step in phase.steps):
            result.append(phase)
    return result


def _first_moveable_gate(phases: list[PlanPhase]) -> PlanGate | None:
    for phase in phases:
        gate = phase.gate
        if gate is not None and gate.gate_type in _GATE_TYPES_THAT_MOVE:
            return gate
    return None


def _any_approval_required(phases: list[PlanPhase]) -> bool:
    return any(phase.approval_required for phase in phases)


# ---------------------------------------------------------------------------
# Field preservation / re-tiering (§5.5)
# ---------------------------------------------------------------------------

def _retier_team(members: list[TeamMember], model: str) -> None:
    for member in members:
        member.model = model
        if member.sub_team:
            _retier_team(member.sub_team, model)


def _step_units(step: PlanStep) -> int:
    return max(1, len(step.team))


def _implementation_units(steps: list[PlanStep]) -> int:
    return sum(_step_units(s) for s in steps)


def _dispatch_units(steps: list[PlanStep]) -> list[tuple[str, list[str]]]:
    """§5.8 (amended): one fan-out unit per step, or one per team member for
    a team step -- each unit carries a task description and the *step's*
    ``allowed_paths`` (``TeamMember`` has no ``allowed_paths`` of its own).
    ``len(_dispatch_units(steps)) == _implementation_units(steps)`` always.
    """
    units: list[tuple[str, list[str]]] = []
    for step in steps:
        if step.team:
            for member in step.team:
                units.append((member.task_description, list(step.allowed_paths)))
        else:
            units.append((step.task_description, list(step.allowed_paths)))
    return units


# ---------------------------------------------------------------------------
# Overrides (§5.11)
# ---------------------------------------------------------------------------

def _effective_agent_model(
    stage: WorkflowStage, settings: WorkflowSettings
) -> tuple[str, str]:
    override = settings.stages.get(stage.stage_id)
    agent = stage.agent_name
    model = stage.model
    if override is not None:
        if override.agent is not None and not stage.harvests_implementation:
            agent = override.agent
        if override.model is not None:
            model = override.model
    return agent, model


# ---------------------------------------------------------------------------
# Final-review fan-out (§5.8)
# ---------------------------------------------------------------------------

def _reviewer_count(units: int, settings: WorkflowSettings) -> int:
    """§5.8 (amended): the formula is exact -- NEVER clamped by the
    harvested *step* count (a single step with an 8-member team must still
    yield ``ceil(8/divisor)`` reviewers, capped only by
    ``final_review_max_reviewers``). Mathematically, ``ceil(units/divisor)``
    for ``divisor >= 1`` never exceeds ``units``, so the resulting count is
    always safe to feed into ``_contiguous_partition(units, count)``.
    """
    divisor = max(1, settings.final_review_fanout_divisor)
    cap = max(1, settings.final_review_max_reviewers)
    return min(cap, max(1, math.ceil(units / divisor)))


def _contiguous_partition(n_items: int, n_groups: int) -> list[tuple[int, int]]:
    n_groups = max(1, min(n_groups, n_items)) if n_items else max(1, n_groups)
    base, remainder = divmod(n_items, n_groups)
    ranges: list[tuple[int, int]] = []
    start = 0
    for i in range(n_groups):
        size = base + (1 if i < remainder else 0)
        end = start + size
        ranges.append((start, end))
        start = end
    return ranges


class WorkflowApplier:
    """Reshapes a base :class:`MachinePlan` into *preset*'s stage phases.

    ``apply()`` mutates *plan* in place and returns a :class:`WorkflowDecisions`
    summary. Idempotent: a second call on an already-shaped plan is a no-op
    guard (§5.1) that recomputes ``WorkflowDecisions`` from the current shape
    instead of re-deriving from *settings*.
    """

    def apply(
        self,
        plan: MachinePlan,
        preset: WorkflowPreset,
        settings: WorkflowSettings,
        *,
        fallback_gate: PlanGate | None = None,
    ) -> WorkflowDecisions:
        # §5.1 no-op guard keys on `plan.workflow == preset.name` (plus at
        # least one stamped `workflow_stage`) -- it does NOT check that
        # *this* `preset` object is the one that produced the current
        # shape, only that its NAME matches. Applying a DIFFERENT preset
        # (by name) to a plan already shaped by a first preset is out of
        # contract for v1 -- there is only one registered preset, so this
        # can't happen today, but it will need a real "re-shape under a new
        # preset" path (not this guard) once a second preset ships. No
        # behavior change; documenting the boundary.
        if plan.workflow == preset.name and any(
            step.workflow_stage for step in plan.all_steps
        ):
            return self._decisions_from_shaped_plan(plan, preset)
        return self._reshape(plan, preset, settings, fallback_gate)

    # -- no-op guard recompute (§5.1) ------------------------------------

    def _decisions_from_shaped_plan(
        self, plan: MachinePlan, preset: WorkflowPreset
    ) -> WorkflowDecisions:
        n_created = len(preset.stages)
        workflow_phases = plan.phases[:n_created]
        carryover_phases = plan.phases[n_created:]
        phase_by_name = {phase.name: phase for phase in workflow_phases}

        stage_entries: list[dict[str, Any]] = []
        implementation_units = 0
        final_review_reviewers = 0
        external_verifier = False

        for stage in preset.stages:
            phase = phase_by_name.get(stage.phase_name)
            if phase is None:
                continue
            agents = sorted({step.agent_name for step in phase.steps})
            # §5.5's model-stamp exception means "automation"/"task" steps
            # (e.g. the external-verifier step) are deliberately left at
            # their own default model, not the stage tier -- exclude them
            # so a heterogeneous phase still infers a single, deterministic
            # effective tier instead of an arbitrary set-iteration pick.
            tiered_models = {
                step.model
                for step in phase.steps
                if step.step_type not in _UNRETIERED_STEP_TYPES
            }
            models = tiered_models or {step.model for step in phase.steps}
            model = sorted(models)[0] if models else stage.model
            stage_entries.append(
                {
                    "stage_id": stage.stage_id,
                    "phase_name": phase.name,
                    "agents": agents,
                    "model": model,
                    "steps": len(phase.steps),
                }
            )
            if stage.harvests_implementation:
                implementation_units = _implementation_units(phase.steps)
            if stage.stage_id == "final_review":
                final_review_reviewers = len(phase.steps)
            if stage.stage_id == "implementation_verification":
                external_verifier = any(
                    step.step_type == "automation" for step in phase.steps
                )

        # §5.1 (amended): the no-op guard performs ZERO writes -- including
        # plan_diagnostics. Only the RETURNED WorkflowDecisions reflects the
        # recompute; the persisted plan_diagnostics["workflow"] record stays
        # exactly what the last real reshape wrote.
        return WorkflowDecisions(
            workflow=plan.workflow,
            stages=stage_entries,
            implementation_units=implementation_units,
            final_review_reviewers=final_review_reviewers,
            external_verifier=external_verifier,
            carried_over_phases=[phase.name for phase in carryover_phases],
        )

    # -- full reshape -----------------------------------------------------

    def _reshape(
        self,
        plan: MachinePlan,
        preset: WorkflowPreset,
        settings: WorkflowSettings,
        fallback_gate: PlanGate | None,
    ) -> WorkflowDecisions:
        base_phases = list(plan.phases)

        harvesting_stage = next(
            (s for s in preset.stages if s.harvests_implementation), None
        )
        if harvesting_stage is None:
            # Defense in depth: `get_workflow_preset()` only ever returns
            # registered presets, which `validate_preset()`
            # (core/workflow/presets.py) already guarantees have exactly
            # one harvesting stage at registration time -- this should be
            # unreachable for any preset that went through registration.
            raise ValueError(
                f"WorkflowPreset {preset.name!r} has no harvesting stage; "
                "this should have been caught at preset registration."
            )
        _, harvest_model = _effective_agent_model(harvesting_stage, settings)

        harvested = _harvest_steps(base_phases)
        if not harvested:
            harvested = [_synthesize_fallback_step(plan, harvest_model)]

        harvested_ids = {step.step_id for step in harvested}
        carryover = _carryover_phases(base_phases, harvested_ids)
        # §5.6 (amended): the gate/approval scan must skip carryover-eligible
        # phases -- otherwise the same PlanGate object could land on both
        # the Implementation phase and the preserved carryover phase
        # (aliased, would run twice), and a carryover phase's own approval
        # would double as Final Review sign-off. Identity comparison (not
        # `==`) since PlanPhase equality is field-based and a distinct
        # phase could coincidentally compare equal.
        carryover_object_ids = {id(p) for p in carryover}
        non_carryover_phases = [
            p for p in base_phases if id(p) not in carryover_object_ids
        ]

        base_gate = _first_moveable_gate(non_carryover_phases)
        gate = base_gate if base_gate is not None else fallback_gate
        approval_required = _any_approval_required(non_carryover_phases)

        impl_phase_id = list(preset.stages).index(harvesting_stage) + 1
        old_ids = [step.step_id for step in harvested]
        new_ids = [f"{impl_phase_id}.{i}" for i in range(1, len(harvested) + 1)]
        old_to_new = dict(zip(old_ids, new_ids))

        for step in harvested:
            step.depends_on = [old_to_new[d] for d in step.depends_on if d in old_to_new]
            step.workflow_stage = harvesting_stage.stage_id
            if step.step_type not in _UNRETIERED_STEP_TYPES:
                step.model = harvest_model
                if step.team:
                    _retier_team(step.team, harvest_model)

        spec_path = f".claude/team-context/executions/{plan.task_id}/spec.md"
        authoring_deliverable = (
            f"failing tests encoding the spec's behaviors for: {plan.task_summary}"
        )

        stage_phases: list[PlanPhase] = []
        stage_entries: list[dict[str, Any]] = []

        for phase_id, stage in enumerate(preset.stages, start=1):
            agent, model = _effective_agent_model(stage, settings)

            if stage.harvests_implementation:
                steps = harvested
                phase_gate = gate
                phase_approval = False
                phase_approval_desc = ""
            elif stage.stage_id == "spec":
                steps = [self._build_spec_step(plan, stage, agent, model, spec_path)]
                phase_gate = None
                phase_approval = False
                phase_approval_desc = ""
            elif stage.stage_id == "architecture":
                steps = [
                    self._build_architecture_step(plan, stage, agent, model, spec_path)
                ]
                phase_gate = None
                phase_approval = False
                phase_approval_desc = ""
            elif stage.stage_id == "test_authoring":
                steps = [
                    self._build_test_authoring_step(
                        plan, stage, agent, model, spec_path, authoring_deliverable
                    )
                ]
                phase_gate = None
                phase_approval = False
                phase_approval_desc = ""
            elif stage.stage_id == "test_verification":
                steps = [
                    self._build_test_verification_step(
                        plan, stage, agent, model, spec_path, authoring_deliverable
                    )
                ]
                phase_gate = None
                phase_approval = False
                phase_approval_desc = ""
            elif stage.stage_id == "implementation_verification":
                steps = self._build_implementation_verification_steps(
                    plan, stage, agent, model, spec_path, settings
                )
                phase_gate = None
                phase_approval = False
                phase_approval_desc = ""
            elif stage.stage_id == "final_review":
                steps = self._build_final_review_steps(
                    plan, stage, agent, model, harvested, settings
                )
                phase_gate = None
                phase_approval = approval_required
                phase_approval_desc = (
                    "Sign off on the reviewed slice." if approval_required else ""
                )
            else:
                # Unreachable for the shipped adversarial-tdd preset -- guards
                # future non-harvesting presets against a silently-wrong
                # final-review-shaped briefing for a stage_id no builder
                # exists for.
                raise ValueError(
                    f"WorkflowApplier: no created-step builder registered for "
                    f"non-harvesting stage_id {stage.stage_id!r}."
                )

            for i, step in enumerate(steps, start=1):
                step.step_id = f"{phase_id}.{i}"
            entry_agents = sorted({step.agent_name for step in steps})

            stage_phases.append(
                PlanPhase(
                    phase_id=phase_id,
                    name=stage.phase_name,
                    steps=steps,
                    gate=phase_gate,
                    approval_required=phase_approval,
                    approval_description=phase_approval_desc,
                )
            )
            stage_entries.append(
                {
                    "stage_id": stage.stage_id,
                    "phase_name": stage.phase_name,
                    "agents": entry_agents,
                    "model": model,
                    "steps": len(steps),
                }
            )

        final_review_reviewers = len(stage_phases[-1].steps)

        carryover_result: list[PlanPhase] = []
        next_phase_id = len(preset.stages) + 1
        for phase in carryover:
            # §5.7 (amended): re-key intra-phase depends_on, drop the rest
            # -- mirrors the harvested-step remap above. A verbatim old id
            # would otherwise silently point at a reshaped stage phase (a
            # mis-edge) or fail MachinePlan.from_dict's forward-ref check
            # at execute-start time. Build the old->new map BEFORE mutating
            # any step_id (same ordering hazard as the harvested remap).
            carryover_old_ids = [step.step_id for step in phase.steps]
            carryover_new_ids = [
                f"{next_phase_id}.{i}" for i in range(1, len(phase.steps) + 1)
            ]
            carryover_old_to_new = dict(zip(carryover_old_ids, carryover_new_ids))
            for step, new_id in zip(phase.steps, carryover_new_ids):
                step.depends_on = [
                    carryover_old_to_new[d]
                    for d in step.depends_on
                    if d in carryover_old_to_new
                ]
                step.workflow_stage = _CARRYOVER_STAGE_ID
                step.step_id = new_id
            carryover_result.append(
                PlanPhase(
                    phase_id=next_phase_id,
                    name=phase.name,
                    steps=list(phase.steps),
                    gate=phase.gate,
                    approval_required=phase.approval_required,
                    approval_description=phase.approval_description,
                    feedback_questions=list(phase.feedback_questions),
                    risk_level=phase.risk_level,
                )
            )
            next_phase_id += 1

        plan.phases = stage_phases + carryover_result
        plan.workflow = preset.name

        decisions = WorkflowDecisions(
            workflow=preset.name,
            stages=stage_entries,
            implementation_units=_implementation_units(harvested),
            final_review_reviewers=final_review_reviewers,
            external_verifier=bool(settings.external_command),
            carried_over_phases=[phase.name for phase in carryover],
        )
        plan.plan_diagnostics["workflow"] = decisions.to_dict()
        return decisions

    # -- created-step builders (§3) ---------------------------------------

    def _build_spec_step(
        self,
        plan: MachinePlan,
        stage: WorkflowStage,
        agent: str,
        model: str,
        spec_path: str,
    ) -> PlanStep:
        text = stage.briefing_template.format(task_summary=plan.task_summary)
        text += f" Write the spec to {spec_path}."
        text += _RESEARCH_SUPPORT_NOTE
        return PlanStep(
            step_id="_",
            agent_name=agent,
            task_description=text,
            model=model,
            step_type=stage.step_type,
            deliverables=[spec_path],
            workflow_stage=stage.stage_id,
        )

    def _build_architecture_step(
        self,
        plan: MachinePlan,
        stage: WorkflowStage,
        agent: str,
        model: str,
        spec_path: str,
    ) -> PlanStep:
        text = stage.briefing_template.format(task_summary=plan.task_summary)
        text += f" Read the spec at {spec_path} before designing."
        text += _RESEARCH_SUPPORT_NOTE
        return PlanStep(
            step_id="_",
            agent_name=agent,
            task_description=text,
            model=model,
            step_type=stage.step_type,
            context_files=[spec_path],
            workflow_stage=stage.stage_id,
        )

    def _build_test_authoring_step(
        self,
        plan: MachinePlan,
        stage: WorkflowStage,
        agent: str,
        model: str,
        spec_path: str,
        deliverable: str,
    ) -> PlanStep:
        text = stage.briefing_template.format(task_summary=plan.task_summary)
        text += _RESEARCH_SUPPORT_NOTE
        return PlanStep(
            step_id="_",
            agent_name=agent,
            task_description=text,
            model=model,
            step_type=stage.step_type,
            deliverables=[deliverable],
            context_files=[spec_path],
            workflow_stage=stage.stage_id,
        )

    def _build_test_verification_step(
        self,
        plan: MachinePlan,
        stage: WorkflowStage,
        agent: str,
        model: str,
        spec_path: str,
        authoring_deliverable: str,
    ) -> PlanStep:
        text = stage.briefing_template.format(task_summary=plan.task_summary)
        text += (
            " Locate the tests described in the test-authoring step's "
            f"deliverables: {authoring_deliverable}."
        )
        text += _RESEARCH_SUPPORT_NOTE
        return PlanStep(
            step_id="_",
            agent_name=agent,
            task_description=text,
            model=model,
            step_type=stage.step_type,
            context_files=[spec_path],
            workflow_stage=stage.stage_id,
        )

    def _build_implementation_verification_steps(
        self,
        plan: MachinePlan,
        stage: WorkflowStage,
        agent: str,
        model: str,
        spec_path: str,
        settings: WorkflowSettings,
    ) -> list[PlanStep]:
        text = stage.briefing_template.format(task_summary=plan.task_summary)
        text += _RESEARCH_SUPPORT_NOTE
        steps = [
            PlanStep(
                step_id="_",
                agent_name=agent,
                task_description=text,
                model=model,
                step_type=stage.step_type,
                context_files=[spec_path],
                workflow_stage=stage.stage_id,
            )
        ]
        if settings.external_command:
            steps.append(
                PlanStep(
                    step_id="_",
                    agent_name="task-runner",
                    task_description=(
                        f"Run the external verifier for: {plan.task_summary}."
                    ),
                    step_type="automation",
                    command=settings.external_command,
                    timeout_seconds=settings.external_timeout_seconds,
                    workflow_stage=stage.stage_id,
                )
            )
        return steps

    def _build_final_review_steps(
        self,
        plan: MachinePlan,
        stage: WorkflowStage,
        agent: str,
        model: str,
        harvested: list[PlanStep],
        settings: WorkflowSettings,
    ) -> list[PlanStep]:
        # §5.8 (amended): partition by DISPATCH UNIT, not by harvested step
        # count -- a single team step contributes one unit per member. The
        # reviewer-count formula is exact and is never clamped by
        # ``len(harvested)``; see `_reviewer_count`'s docstring for why the
        # resulting count always fits `_contiguous_partition`.
        units = _dispatch_units(harvested)
        n_units = len(units)
        reviewers = _reviewer_count(n_units, settings)
        ranges = _contiguous_partition(n_units, reviewers)
        base_text = stage.briefing_template.format(task_summary=plan.task_summary)
        base_text += _RESEARCH_SUPPORT_NOTE

        steps: list[PlanStep] = []
        for start, end in ranges:
            group = units[start:end]
            lines = [
                f"- {description} (writes: {', '.join(paths) if paths else 'n/a'})"
                for description, paths in group
            ]
            text = (
                base_text
                + " Review the following implementation steps:\n"
                + "\n".join(lines)
            )
            steps.append(
                PlanStep(
                    step_id="_",
                    agent_name=agent,
                    task_description=text,
                    model=model,
                    step_type=stage.step_type,
                    workflow_stage=stage.stage_id,
                )
            )
        return steps
