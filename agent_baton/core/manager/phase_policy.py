"""``PhasePolicyApplier`` -- configurable phase/project policies (M6).

Spec: docs/internal/manager-mode-pmo-design.md, PRD §14.3 / §16 Milestone 6.

This is the **only** PMO component that mutates the plan graph produced by
``IntelligentPlanner.create_plan()`` -- it injects adversarial-review steps
per ``policies.phase_completion.adversarial_review`` /
``policies.project_completion.adversarial_review`` and (when the CLI did
not pin an explicit ``--gate-scope``) enforces ``gates.mode`` (bd-6dn):
``project_configured`` rescales existing phase gates to
``gates.gate_scope``, ``focused``/``full``/``smoke`` force that scope
directly (ignoring ``gates.gate_scope``), and ``off`` strips every phase
gate. ``gates.allow_smoke_fallback`` and ``gates.missing_gate_policy``
remain record-only (ADR-25). Everything else the PMO layer produces (charter,
scope map, blueprint, context bundles, ...) is a sidecar artifact that
never touches the ``MachinePlan`` itself.

``PhasePolicyApplier.apply()`` is a pure function of
``(plan, config, cli_gate_scope_explicit)``: no clock reads, no
filesystem/network IO, deterministic given its inputs. It is safe to call
more than once on the same plan -- re-applying injects no duplicate review
steps (idempotency is detected via the ``review-`` step_id prefix this
module always uses for its own injected steps).

Review-step **context bundles** (phase handoff + review rubric pack, PRD
§14.3's "review agent should receive...") are deliberately NOT built here.
``ManagerModePlanner`` (Wave 3) builds scope contracts and context bundles
over the *final* step list -- i.e. after this applier has run -- so
injected review steps get bundles like any other step. See
``docs/internal/manager-mode-pmo-plan.md`` Task 4's composition-order
docstring and Task 11's ``test_review_bundle_integration``.
"""
from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.engine.planning.utils.gates import default_gate
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

# All steps injected by this applier share this prefix -- it doubles as the
# idempotency marker (a phase that already has a ``review-``-prefixed step
# is treated as already reviewed and is never given a second one).
_REVIEW_PREFIX = "review-"

# `agent_baton/core/engine/planning/rules/step_types.py::AGENT_STEP_TYPE`
# maps agent names to step_type values "planning" / "reviewing" / "testing"
# / "task" -- "reviewing" exists in that rules module (Wave 2 review Q1
# corrected an earlier claim that it didn't). Injected review steps use it
# directly so `required_for_code_steps` knowledge packs (gated on
# non-review step types, Wave 3 composition) never attach to a review step.
_REVIEW_STEP_TYPE = "reviewing"

_RISK_RANK: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
_RISK_BASED_THRESHOLD_RANK = _RISK_RANK["MEDIUM"]


class PolicyDecisions(BaseModel):
    """Summary of the phase/project policies applied by one ``apply()`` call.

    A pure record of decisions taken (or deliberately not taken) -- it does
    not itself write anything to disk. Consumers (``ManagerReportBuilder``,
    the M9 execution hooks) read ``handoff_required`` to decide whether to
    write ``handoffs/phase-<n>-handoff.md``, and ``injected_review_steps`` /
    ``final_review_step`` to know which dispatched steps are reviews.
    """

    model_config = ConfigDict(extra="ignore")

    handoff_required: bool = True
    gates_mode: str = "project_configured"
    injected_review_steps: list[str] = Field(default_factory=list)
    final_review_step: str | None = None
    gate_scope_applied: str | None = None
    # Phase ids (stringified) whose gate was removed because
    # ``gates.mode == "off"`` (bd-6dn). Empty for every other mode and
    # whenever the CLI pinned an explicit ``--gate-scope``.
    gates_stripped: list[str] = Field(default_factory=list)


def _phase_review_step_id(phase_id: int) -> str:
    return f"{_REVIEW_PREFIX}{phase_id}"


def _final_review_step_id(phase_id: int) -> str:
    return f"{_REVIEW_PREFIX}{phase_id}-final"


def _has_review_step(phase: PlanPhase) -> bool:
    """True when *phase* already carries a step this applier injected."""
    return any(step.step_id.startswith(_REVIEW_PREFIX) for step in phase.steps)


def _phase_risk_rank(phase: PlanPhase, plan: MachinePlan) -> int:
    """Effective risk tier for *phase*: its own override, else the plan's."""
    level = (phase.risk_level or plan.risk_level or "LOW").upper()
    return _RISK_RANK.get(level, _RISK_RANK["LOW"])


def _should_inject_phase_review(policy: str, phase: PlanPhase, plan: MachinePlan) -> bool:
    if policy == "always":
        return True
    if policy == "risk_based":
        return _phase_risk_rank(phase, plan) >= _RISK_BASED_THRESHOLD_RANK
    return False  # "off" (and any unrecognized value -- fail closed, no injection)


def _build_review_step(
    *, step_id: str, agent_name: str, description: str, depends_on: str
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=description,
        depends_on=[depends_on],
        deliverables=["review verdict"],
        parallel_safe=False,
        step_type=_REVIEW_STEP_TYPE,
    )


def _apply_gate_scope(plan: MachinePlan, gate_scope: str) -> str | None:
    """Rescale every phase's existing gate to *gate_scope*, in place.

    Returns the scope actually applied (``gate_scope`` when a rescale ran,
    ``None`` when this was a no-op) -- see :attr:`PolicyDecisions.gate_scope_applied`.

    ``gate_scope == "focused"`` is a no-op (Wave 2 review F1): the planner
    already produced focused gates from strictly better information than
    this applier has (real changed-path test scoping via
    ``_test_files_for_changes``, not just a phase name). Regenerating a
    "focused" gate here with ``changed_paths=None`` can only ever produce
    the smoke-fallback command, silently discarding the planner's
    scoped-test command -- so for "focused" this function does nothing.

    For "full"/"smoke", reuses ``planning/utils/gates.default_gate`` --
    the same function the planner itself calls at plan-creation time -- so
    the regenerated command/description/fail_on shape matches what the
    planner would have produced for this ``gate_scope``. ``default_gate``
    reads only ``stack.language`` (confirmed against
    ``planning/utils/gates.py``), so a minimal ``SimpleNamespace`` shim
    carries ``plan.detected_stack`` through without needing a real
    ``StackProfile`` or project root (this applier must stay IO-free).
    ``plan.detected_stack`` may be a combined ``"language/framework"``
    string (see ``planning/stages/assembly.py``); only the language segment
    is meaningful to ``default_gate``'s stack-command lookup, so it is
    split off here. Changed-path test scoping is still NOT replicated
    (``changed_paths=None`` unconditionally; ``default_gate`` never touches
    the filesystem when ``changed_paths`` is falsy, so this stays pure).

    - Only ``command`` / ``description`` / ``fail_on`` are overwritten;
      ``gate_type`` (and whether a phase has a gate at all) is left as the
      planner decided it, so a phase's gate is rescoped in place instead of
      being replaced by whatever type ``default_gate`` would infer purely
      from the phase name.
    - A phase with no gate stays gate-less; a phase whose gate type
      ``default_gate`` can't regenerate purely from its name (e.g. its name
      matches the "skip" list such as "review"/"design") is left untouched
      rather than having its gate deleted.
    """
    if gate_scope == "focused":
        return None

    stack = (
        SimpleNamespace(language=plan.detected_stack.split("/", 1)[0])
        if plan.detected_stack
        else None
    )

    for phase in plan.phases:
        gate = phase.gate
        if gate is None:
            continue
        regenerated = default_gate(
            phase.name,
            stack=stack,
            changed_paths=None,
            gate_scope=gate_scope,
            project_root=None,
        )
        if regenerated is None:
            continue
        gate.command = regenerated.command
        gate.description = regenerated.description
        gate.fail_on = list(regenerated.fail_on)
    return gate_scope


class PhasePolicyApplier:
    """Applies ``policies.*`` and ``gates.*`` from :class:`ManagerConfig`
    onto an already-built :class:`MachinePlan`.
    """

    def __init__(self, config: ManagerConfig) -> None:
        self._config = config

    def apply(self, plan: MachinePlan, *, cli_gate_scope_explicit: bool) -> PolicyDecisions:
        config = self._config
        injected: list[str] = []

        phase_policy = config.policies.phase_completion.adversarial_review
        for phase in plan.phases:
            if not phase.steps or _has_review_step(phase):
                continue
            if not _should_inject_phase_review(phase_policy, phase, plan):
                continue
            last_step_id = phase.steps[-1].step_id
            step_id = _phase_review_step_id(phase.phase_id)
            phase.steps.append(
                _build_review_step(
                    step_id=step_id,
                    agent_name=config.policies.review_agents.adversarial_review,
                    description=(
                        f"Adversarial review of phase '{phase.name}': verify "
                        "deliverables against the project charter and phase "
                        "handoff; veto with reasons or approve."
                    ),
                    depends_on=last_step_id,
                )
            )
            injected.append(step_id)

        final_review_step: str | None = None
        if plan.phases and plan.phases[-1].steps:
            last_phase = plan.phases[-1]
            if config.policies.project_completion.adversarial_review == "always":
                final_id = _final_review_step_id(last_phase.phase_id)
                already_present = any(s.step_id == final_id for s in last_phase.steps)
                if not already_present:
                    last_step_id = last_phase.steps[-1].step_id
                    last_phase.steps.append(
                        _build_review_step(
                            step_id=final_id,
                            agent_name=config.policies.review_agents.project_review,
                            description=(
                                "Final adversarial review of the project: verify "
                                "all workstream deliverables against the project "
                                "charter; veto with reasons or approve."
                            ),
                            depends_on=last_step_id,
                        )
                    )
                final_review_step = final_id

        # gates.mode enforcement (bd-6dn). An explicit CLI --gate-scope
        # always wins: when the operator pinned a scope on the command
        # line, this applier never touches gates -- no rescope, no strip.
        gates_mode = config.gates.mode
        gate_scope_applied: str | None = None
        gates_stripped: list[str] = []
        if not cli_gate_scope_explicit:
            if gates_mode == "project_configured":
                gate_scope_applied = _apply_gate_scope(plan, config.gates.gate_scope)
            elif gates_mode in ("focused", "full", "smoke"):
                # The mode names a scope directly: force it exactly as if
                # it were the project-configured gate_scope. Same fidelity
                # rules as above -- "focused" is a no-op inside
                # _apply_gate_scope (planner gates win), "full"/"smoke"
                # rescope via default_gate with detected_stack threaded.
                gate_scope_applied = _apply_gate_scope(plan, gates_mode)
            elif gates_mode == "off":
                # Strip every phase gate (PlanPhase.gate is Optional, so
                # the plan stays round-trip valid). Record which phases
                # actually lost a gate so `baton team`/`baton report` can
                # surface the decision.
                for phase in plan.phases:
                    if phase.gate is not None:
                        phase.gate = None
                        gates_stripped.append(str(phase.phase_id))

        return PolicyDecisions(
            handoff_required=config.policies.phase_completion.handoff_required,
            gates_mode=gates_mode,
            injected_review_steps=injected,
            final_review_step=final_review_step,
            gate_scope_applied=gate_scope_applied,
            gates_stripped=gates_stripped,
        )
