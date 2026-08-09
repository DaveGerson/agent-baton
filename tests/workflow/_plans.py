"""Base-plan builders shared by ``tests/workflow`` (and ``tests/cli``).

Every builder returns a *base* plan — the shape ``IntelligentPlanner.create_plan()``
hands to the workflow post-processor, i.e. **before** ``WorkflowApplier.apply()``
reshapes it. None of them set ``workflow`` / ``workflow_stage``; those fields are
the applier's output, never its input (except for the idempotency no-op guard,
which tests exercise by calling ``apply()`` twice rather than by hand-crafting a
pre-shaped plan).

Spec: docs/internal/adversarial-tdd-workflow-design.md §3 / §5.

Design notes for the builders:

- Harvested steps carry a **non-default** ``model`` (``"opus"`` / ``"haiku"``)
  so "re-tiered to sonnet" assertions cannot pass vacuously — ``PlanStep.model``
  already defaults to ``"sonnet"``.
- Step descriptions are unique and mutually non-substring so fan-out grouping
  assertions can attribute a briefing to exactly one source step.
"""
from __future__ import annotations

from typing import Any

from agent_baton.models.execution import (
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    SynthesisSpec,
    TeamMember,
)
from agent_baton.models.knowledge import KnowledgeAttachment

TASK_SUMMARY = "add rate limiting to the public API"


# ---------------------------------------------------------------------------
# Low-level constructors
# ---------------------------------------------------------------------------

def make_plan(phases: list[PlanPhase], **overrides: Any) -> MachinePlan:
    """Build a ``MachinePlan`` around *phases* with sane test defaults."""
    fields: dict[str, Any] = {
        "task_id": "task-workflow-fixture",
        "task_summary": TASK_SUMMARY,
        "risk_level": "MEDIUM",
        "detected_stack": "python",
        "archetype": "phased",
    }
    fields.update(overrides)
    return MachinePlan(phases=phases, **fields)


def dispatch_units(step: PlanStep) -> list[tuple[str, str, str]]:
    """Flatten *step* into the ``(agent_name, model, briefing)`` tuples that
    will actually be dispatched.

    A plain step is one unit; a team step is one unit per member, recursively
    through ``sub_team``. Used by assertions that must hold regardless of
    whether the applier realises a multi-reviewer fan-out as N sibling steps
    or as a single team step with N members (the spec pins the reviewer
    *count* and their briefings, not the container — see §5.8).
    """
    if not step.team:
        return [(step.agent_name, step.model, step.task_description)]

    units: list[tuple[str, str, str]] = []

    def _walk(members: list[TeamMember]) -> None:
        for member in members:
            units.append((member.agent_name, member.model, member.task_description))
            if member.sub_team:
                _walk(member.sub_team)

    _walk(step.team)
    return units


def phase_by_name(plan: MachinePlan, name: str) -> PlanPhase:
    """Return the single phase named *name*; fail loudly when absent/ambiguous."""
    matches = [p for p in plan.phases if p.name == name]
    assert len(matches) == 1, (
        f"expected exactly one phase named {name!r}, "
        f"got {[p.name for p in plan.phases]}"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# Base-plan builders
# ---------------------------------------------------------------------------

def build_base_plan() -> MachinePlan:
    """A representative four-phase plan: Design / Implement / Test / Review.

    - ``Implement`` (archetype IMPLEMENTATION) is the only harvestable phase.
    - Its ``code-reviewer`` step is ``step_type="reviewing"`` and must be
      excluded from the harvest (§5.2).
    - ``2.1`` depends on a ``Design`` step that the reshape discards, so the
      dangling cross-phase dep must be dropped rather than raise (§3 "no
      cross-phase step deps").
    - The first gate in phase order is a *lint* gate; the test gate lives on
      a later phase, pinning "first **test/build** gate" (§5.6).
    - ``Review`` carries the approval that the OR-rule must preserve.
    """
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Design",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="architect",
                        task_description="Sketch the limiter design",
                        step_type="planning",
                        model="opus",
                    )
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Implement",
                gate=PlanGate(
                    gate_type="lint",
                    command="ruff check .",
                    description="Lint the changed files.",
                ),
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="backend-engineer",
                        task_description="Build the token-bucket limiter",
                        step_type="developing",
                        model="opus",
                        depends_on=["1.1"],
                        deliverables=["limiter module"],
                        allowed_paths=["app/api/limiter.py"],
                        blocked_paths=["migrations/"],
                        context_files=["docs/api.md"],
                        expected_outcome="Requests over the quota return 429.",
                        parallel_safe=True,
                        max_estimated_minutes=30,
                        mcp_servers=["postgres"],
                        interactive=True,
                        max_turns=4,
                        knowledge=[
                            KnowledgeAttachment(
                                source="planner-matched:tag",
                                pack_name="coding-conventions",
                                document_name="api-conventions",
                                path="docs/api-conventions.md",
                                delivery="reference",
                            )
                        ],
                    ),
                    PlanStep(
                        step_id="2.2",
                        agent_name="frontend-engineer",
                        task_description="Surface quota headers in the client",
                        step_type="developing",
                        model="haiku",
                        depends_on=["2.1"],
                        deliverables=["client wiring"],
                        allowed_paths=["web/src/limits.ts"],
                    ),
                    PlanStep(
                        step_id="2.3",
                        agent_name="code-reviewer",
                        task_description="Review the limiter diff",
                        step_type="reviewing",
                        model="sonnet",
                        depends_on=["2.1"],
                    ),
                ],
            ),
            PlanPhase(
                phase_id=3,
                name="Test",
                gate=PlanGate(
                    gate_type="test",
                    command="pytest tests/api",
                    description="Run the API test suite.",
                    fail_on=["test failure"],
                ),
                steps=[
                    PlanStep(
                        step_id="3.1",
                        agent_name="test-engineer",
                        task_description="Cover the limiter with tests",
                        step_type="testing",
                        model="sonnet",
                    )
                ],
            ),
            PlanPhase(
                phase_id=4,
                name="Review",
                approval_required=True,
                approval_description="Sign off on the rate limiter rollout.",
                steps=[
                    PlanStep(
                        step_id="4.1",
                        agent_name="code-reviewer",
                        task_description="Final read of the slice",
                        step_type="reviewing",
                        model="opus",
                    )
                ],
            ),
        ]
    )


def build_team_step_plan() -> MachinePlan:
    """Implement phase with a team step (nested ``sub_team``) plus a plain step.

    Fan-out units: ``max(1, len(team))`` per harvested step → 2 (team) + 1
    (plain) = 3 (§5.8). Member models are deliberately non-``sonnet`` so the
    recursive re-tier assertion is not vacuous (§5.5).
    """
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Coordinate the limiter build-out",
                        step_type="developing",
                        model="opus",
                        allowed_paths=["app/api/"],
                        synthesis=SynthesisSpec(strategy="concatenate"),
                        team=[
                            TeamMember(
                                member_id="1.1.a",
                                agent_name="backend-engineer",
                                role="lead",
                                task_description="Own the limiter core",
                                model="opus",
                                sub_team=[
                                    TeamMember(
                                        member_id="1.1.a.i",
                                        agent_name="data-engineer",
                                        role="implementer",
                                        task_description="Model the quota store",
                                        model="haiku",
                                    )
                                ],
                            ),
                            TeamMember(
                                member_id="1.1.b",
                                agent_name="frontend-engineer",
                                role="implementer",
                                task_description="Wire the quota banner",
                                model="haiku",
                            ),
                        ],
                    ),
                    PlanStep(
                        step_id="1.2",
                        agent_name="devops-engineer",
                        task_description="Publish the limiter config",
                        step_type="developing",
                        model="opus",
                        allowed_paths=["deploy/limits.yaml"],
                    ),
                ],
            )
        ]
    )


def build_harvest_predicate_plan() -> MachinePlan:
    """Pins the harvest predicate and its *name normalization* (§5.2).

    Positives — every one of these must be harvested:

    - ``Fix`` / ``Build`` → IMPLEMENTATION.
    - ``Prepare`` → PREPARATION, ``Remediate`` → REMEDIATION (the other two
      archetypes the predicate accepts).
    - ``Implement: API Layer`` → colon-stripped to ``implement``.
    - ``Backend Implementation`` → last-word keyed to ``implementation``,
      which ``ValidationStage._phase_key`` aliases to ``implement``.

    The last two are the reason the predicate must *normalize* rather than do
    an explicit full-name lookup against ``_PHASE_NAME_TO_ARCHETYPE`` — a
    naive ``table.get(phase.name.lower())`` misses both.

    Negatives:

    - ``Security Review`` → last-word key ``review`` → REVIEW, even though it
      contains a ``developing`` step.
    - ``Widget Frobnication`` → unrecognized → not harvested (the
      ``phase_archetype`` fallback to IMPLEMENTATION must not be used).
    - The ``planning`` step inside ``Implement: API Layer`` (step-type
      exclusion applies *within* an implement-like phase).

    ``Build``'s step also depends on ``Fix``'s step — a cross-base-phase
    dependency between two *harvested* steps, which must be re-keyed (not
    dropped) once both land in the flattened Implementation phase.
    """
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Security Review",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="security-reviewer",
                        task_description="Threat-model the limiter",
                        step_type="reviewing",
                        model="opus",
                    ),
                    PlanStep(
                        step_id="1.2",
                        agent_name="backend-engineer",
                        task_description="Patch the header leak found in triage",
                        step_type="developing",
                        model="opus",
                    ),
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Fix",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="backend-engineer",
                        task_description="Repair the quota reset arithmetic",
                        step_type="developing",
                        model="opus",
                    )
                ],
            ),
            PlanPhase(
                phase_id=3,
                name="Build",
                steps=[
                    PlanStep(
                        step_id="3.1",
                        agent_name="devops-engineer",
                        task_description="Compile the release artifact",
                        step_type="developing",
                        model="haiku",
                        depends_on=["2.1"],
                    )
                ],
            ),
            PlanPhase(
                phase_id=4,
                name="Widget Frobnication",
                steps=[
                    PlanStep(
                        step_id="4.1",
                        agent_name="backend-engineer",
                        task_description="Frobnicate the widget registry",
                        step_type="developing",
                        model="opus",
                    )
                ],
            ),
            PlanPhase(
                phase_id=5,
                name="Implement: API Layer",
                steps=[
                    PlanStep(
                        step_id="5.1",
                        agent_name="architect",
                        task_description="Draft the middleware interface",
                        step_type="planning",
                        model="opus",
                    ),
                    PlanStep(
                        step_id="5.2",
                        agent_name="backend-engineer",
                        task_description="Add the throttle middleware to the API layer",
                        step_type="developing",
                        model="opus",
                    ),
                ],
            ),
            PlanPhase(
                phase_id=6,
                name="Backend Implementation",
                steps=[
                    PlanStep(
                        step_id="6.1",
                        agent_name="backend-engineer",
                        task_description="Wire the quota store into the backend",
                        step_type="developing",
                        model="opus",
                    )
                ],
            ),
            PlanPhase(
                phase_id=7,
                name="Prepare",
                steps=[
                    PlanStep(
                        step_id="7.1",
                        agent_name="devops-engineer",
                        task_description="Provision the redis quota cache",
                        step_type="developing",
                        model="haiku",
                    )
                ],
            ),
            PlanPhase(
                phase_id=8,
                name="Remediate",
                steps=[
                    PlanStep(
                        step_id="8.1",
                        agent_name="backend-engineer",
                        task_description="Roll back the broken quota migration",
                        step_type="developing",
                        model="opus",
                    )
                ],
            ),
        ]
    )


def build_developing_fallback_plan() -> MachinePlan:
    """No phase name maps explicitly to an implement-like archetype, but a
    ``developing`` step exists → harvest fallback (a) (§5.2)."""
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Discovery",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="general-purpose",
                        task_description="Chart the current throttling behaviour",
                        step_type="task",
                        model="haiku",
                    )
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Rollout",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="devops-engineer",
                        task_description="Ship the limiter behind a flag",
                        step_type="developing",
                        model="opus",
                        allowed_paths=["deploy/"],
                    ),
                    PlanStep(
                        step_id="2.2",
                        agent_name="code-reviewer",
                        task_description="Check the rollout runbook",
                        step_type="reviewing",
                        model="opus",
                    ),
                ],
            ),
        ]
    )


def build_no_implementation_plan() -> MachinePlan:
    """No implement-like phase and no ``developing`` step anywhere → the
    applier must synthesize a fallback implementation step (§5.2 fallback b)."""
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Design",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="architect",
                        task_description="Write the limiter RFC",
                        step_type="planning",
                        model="opus",
                    )
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Review",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="code-reviewer",
                        task_description="Review the RFC",
                        step_type="reviewing",
                        model="opus",
                    )
                ],
            ),
        ]
    )


def build_audit_carryover_plan() -> MachinePlan:
    """Regulated-domain shape: an ``Audit`` phase with an ``auditor`` step,
    its own approval and gate. Must survive the reshape verbatim, appended
    after Final Review (§3 "Audit carryover", §5.7)."""
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Implement",
                gate=PlanGate(
                    gate_type="test",
                    command="pytest tests/billing",
                    description="Run the billing suite.",
                ),
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Add the retention-window enforcement",
                        step_type="developing",
                        model="opus",
                        allowed_paths=["app/billing/retention.py"],
                    )
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Audit",
                approval_required=True,
                approval_description="Compliance officer sign-off required.",
                gate=PlanGate(
                    gate_type="review",
                    command="baton evidence verify",
                    description="Verify the evidence bundle.",
                    fail_on=["missing evidence"],
                ),
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="auditor",
                        task_description="Confirm the retention controls are auditable",
                        step_type="reviewing",
                        model="opus",
                        deliverables=["audit memo"],
                    )
                ],
            ),
        ],
        risk_level="CRITICAL",
    )


def build_audit_carryover_with_dependencies_plan() -> MachinePlan:
    """Audit carryover phase whose steps carry BOTH a cross-phase dependency
    on a harvested step (must be DROPPED) and an intra-phase dependency
    (must be RE-KEYED) -- pins the amended §5.7 depends_on rule.

    ``2.1`` (``compliance-analyst``, not itself an auditor) depends on the
    harvested ``Implement`` step -- that edge must be dropped, not left
    pointing at the discarded original id. ``2.2`` (``auditor`` -- the step
    that actually qualifies the phase for carryover) depends on ``2.1`` --
    an intra-phase edge that must survive, re-keyed to the renumbered id.
    """
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Add the retention-window enforcement",
                        step_type="developing",
                        model="opus",
                    )
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Audit",
                approval_required=True,
                approval_description="Compliance officer sign-off required.",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="compliance-analyst",
                        task_description="Gather the retention evidence",
                        step_type="task",
                        model="opus",
                        depends_on=["1.1"],
                    ),
                    PlanStep(
                        step_id="2.2",
                        agent_name="auditor",
                        task_description="Confirm the retention controls are auditable",
                        step_type="reviewing",
                        model="opus",
                        depends_on=["2.1"],
                        deliverables=["audit memo"],
                    ),
                ],
            ),
        ]
    )


def build_auditor_in_implement_phase_plan() -> MachinePlan:
    """An ``auditor`` step living inside a phase that IS harvested.

    §5.7 scopes carryover to *non-harvested* base phases, so this plan must
    produce no carryover at all — the auditor step is a ``reviewing`` step and
    is simply discarded with the rest of the phase's non-harvestable work.
    """
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Add the retention-window enforcement",
                        step_type="developing",
                        model="opus",
                    ),
                    PlanStep(
                        step_id="1.2",
                        agent_name="auditor",
                        task_description="Spot-check the retention controls inline",
                        step_type="reviewing",
                        model="opus",
                    ),
                ],
            )
        ]
    )


def build_build_gate_plan() -> MachinePlan:
    """The only test/build gate in the plan is a ``build`` gate (§5.6).

    The earlier phase carries a ``lint`` gate, so an implementation that
    looked for ``gate_type == "test"`` alone would wrongly pick nothing (and
    fall through to ``fallback_gate``).
    """
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Implement",
                gate=PlanGate(
                    gate_type="lint",
                    command="ruff check .",
                    description="Lint the changed files.",
                ),
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Add the limiter middleware",
                        step_type="developing",
                        model="opus",
                    )
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Package",
                gate=PlanGate(
                    gate_type="build",
                    command="python -m build",
                    description="Build the wheel.",
                    fail_on=["build failure"],
                ),
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="devops-engineer",
                        task_description="Cut the release wheel",
                        step_type="task",
                        model="haiku",
                    )
                ],
            ),
        ]
    )


def build_automation_step_plan() -> MachinePlan:
    """Implement phase containing ``automation`` and ``task`` steps alongside a
    ``developing`` step. All three are harvested; only the ``developing`` one
    is re-tiered (§5.5 model-stamp exception)."""
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Add the limiter middleware",
                        step_type="developing",
                        model="opus",
                    ),
                    PlanStep(
                        step_id="1.2",
                        agent_name="task-runner",
                        task_description="Apply the quota migration",
                        step_type="automation",
                        model="haiku",
                        command="make migrate",
                        timeout_seconds=120,
                        depends_on=["1.1"],
                    ),
                    PlanStep(
                        step_id="1.3",
                        agent_name="general-purpose",
                        task_description="Regenerate the API changelog",
                        step_type="task",
                        model="haiku",
                    ),
                ],
            )
        ]
    )


def build_units_plan(units: int) -> MachinePlan:
    """An Implement phase with *units* distinct plain steps (one unit each).

    Descriptions are zero-padded so no description is a substring of another —
    fan-out grouping assertions attribute a briefing to exactly one step.
    """
    steps = [
        PlanStep(
            step_id=f"1.{i + 1}",
            agent_name="backend-engineer",
            task_description=f"implement unit-{i + 1:02d} of the rate limiter",
            step_type="developing",
            model="opus",
            allowed_paths=[f"app/api/unit_{i + 1:02d}.py"],
        )
        for i in range(units)
    ]
    return make_plan([PlanPhase(phase_id=1, name="Implement", steps=steps)])


def build_gateless_plan() -> MachinePlan:
    """Implement-only plan with no gate and no approval anywhere — exercises
    the ``fallback_gate`` path and the ``approval_required`` OR-rule's
    False case (§5.6)."""
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Add the limiter middleware",
                        step_type="developing",
                        model="opus",
                    )
                ],
            )
        ]
    )


def build_large_team_plan() -> MachinePlan:
    """A single harvested step whose team has 8 members and no plain steps.

    Pins the amended §5.8: fan-out is by DISPATCH UNIT, never clamped by the
    harvested *step* count — one step with 8 team members must still yield
    ``ceil(8 / final_review_fanout_divisor)`` reviewers, not 1 (the step
    count). Member descriptions are unique/non-substring so partition
    assertions can attribute a reviewer briefing to exactly its member
    subset.
    """
    members = [
        TeamMember(
            member_id=f"1.1.{chr(ord('a') + i)}",
            agent_name="backend-engineer",
            role="implementer",
            task_description=f"own team-unit-{i + 1:02d} of the rate limiter",
            model="opus",
        )
        for i in range(8)
    ]
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Coordinate the limiter build-out",
                        step_type="developing",
                        model="opus",
                        allowed_paths=["app/api/limiter.py"],
                        synthesis=SynthesisSpec(strategy="concatenate"),
                        team=members,
                    )
                ],
            )
        ]
    )


def build_qualifying_phase_without_harvestable_steps_plan() -> MachinePlan:
    """§5.2(a) amended: a qualifying phase ("Implementation") whose only step
    is a reviewing step (so nothing is harvested from it), plus a
    non-qualifying "Design" phase carrying a developing step.

    Fallback (a) must fire because harvesting the qualifying phase(s) alone
    yielded *zero* steps — not because no phase qualified — and it must
    harvest the "Design" phase's developing step rather than synthesizing a
    fallback step.
    """
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Design",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Prototype the limiter approach",
                        step_type="developing",
                        model="opus",
                    )
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="code-reviewer",
                        task_description="Review the limiter approach",
                        step_type="reviewing",
                        model="opus",
                    )
                ],
            ),
        ]
    )


def build_audit_carryover_only_gate_plan() -> MachinePlan:
    """The ONLY test/build-type gate in the base plan sits on the Audit
    carryover phase -- §5.6 (amended): the gate scan must skip
    carryover-eligible phases, so Implementation must fall through to
    ``fallback_gate`` rather than aliasing the Audit phase's own gate
    object (which would then run twice -- once per phase, both pointing at
    the same ``PlanGate`` instance).
    """
    return make_plan(
        [
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Add the retention-window enforcement",
                        step_type="developing",
                        model="opus",
                    )
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Audit",
                approval_required=True,
                approval_description="Compliance officer sign-off required.",
                gate=PlanGate(
                    gate_type="test",
                    command="baton evidence verify",
                    description="Verify the evidence bundle.",
                ),
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="auditor",
                        task_description="Confirm the retention controls are auditable",
                        step_type="reviewing",
                        model="opus",
                    )
                ],
            ),
        ]
    )
