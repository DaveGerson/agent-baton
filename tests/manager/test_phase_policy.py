"""Tests for :mod:`agent_baton.core.manager.phase_policy` (M6 -- configurable
phase and project policies).

See docs/internal/manager-mode-pmo-plan.md Wave 2 / Task 9 and PRD §14.3 /
§16 Milestone 6.
"""
from __future__ import annotations

import json

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.manager.phase_policy import PhasePolicyApplier, PolicyDecisions
from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep


def _make_plan(
    *,
    task_id: str = "task-phase-policy-1",
    task_summary: str = "Add a reporting endpoint with tests and docs",
    risk_level: str = "MEDIUM",
    detected_stack: str | None = "python",
    phases: list[PlanPhase] | None = None,
) -> MachinePlan:
    if phases is None:
        phases = [
            PlanPhase(
                phase_id=1,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Build the reporting endpoint.",
                        deliverables=["reporting endpoint"],
                        allowed_paths=["app/reporting/service.py"],
                    ),
                    PlanStep(
                        step_id="1.2",
                        agent_name="backend-engineer",
                        task_description="Wire the endpoint into the router.",
                        deliverables=["router wiring"],
                        allowed_paths=["app/reporting/routes.py"],
                        depends_on=["1.1"],
                    ),
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Testing and docs",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="test-engineer",
                        task_description="Write tests for the reporting endpoint.",
                        deliverables=["test suite"],
                        allowed_paths=["tests/reporting"],
                        depends_on=["1.1"],
                    ),
                ],
            ),
        ]
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        risk_level=risk_level,
        task_type="feature",
        detected_stack=detected_stack,
        phases=phases,
    )


def _round_trip(plan: MachinePlan) -> MachinePlan:
    """Prove the mutated plan still satisfies the graph-integrity validator
    (unique step/phase ids, backward-only depends_on) and round-trips."""
    reloaded = MachinePlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert reloaded == plan
    return reloaded


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        existing = result.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            result[key] = _deep_merge(existing, value)
        else:
            result[key] = value
    return result


def _no_review_config(**overrides: dict) -> ManagerConfig:
    """Config with both review policies off, so a test can isolate exactly
    one policy dimension without incidental step injections."""
    base: dict = {
        "policies": {
            "phase_completion": {"adversarial_review": "off"},
            "project_completion": {"adversarial_review": "off"},
        }
    }
    return ManagerConfig.from_dict(_deep_merge(base, overrides))


def test_always_injects_review_after_each_phase() -> None:
    config = ManagerConfig.from_dict(
        {"policies": {"project_completion": {"adversarial_review": "off"}}}
    )
    assert config.policies.phase_completion.adversarial_review == "always"
    plan = _make_plan()

    decisions = PhasePolicyApplier(config).apply(plan, cli_gate_scope_explicit=True)

    phase1, phase2 = plan.phases
    assert [s.step_id for s in phase1.steps][-1] == "review-1"
    assert [s.step_id for s in phase2.steps][-1] == "review-2"

    review1 = phase1.steps[-1]
    assert review1.agent_name == config.policies.review_agents.adversarial_review
    assert review1.depends_on == ["1.2"]
    assert review1.deliverables == ["review verdict"]
    assert review1.parallel_safe is False

    review2 = phase2.steps[-1]
    assert review2.depends_on == ["2.1"]

    assert decisions.injected_review_steps == ["review-1", "review-2"]
    assert decisions.final_review_step is None

    _round_trip(plan)


def test_risk_based_injects_only_at_threshold() -> None:
    phases = [
        PlanPhase(
            phase_id=1,
            name="Implementation",
            risk_level="LOW",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Low-risk tweak.",
                    deliverables=["tweak"],
                    allowed_paths=["app/a.py"],
                ),
            ],
        ),
        PlanPhase(
            phase_id=2,
            name="Testing",
            risk_level="MEDIUM",
            steps=[
                PlanStep(
                    step_id="2.1",
                    agent_name="test-engineer",
                    task_description="Medium-risk change.",
                    deliverables=["tests"],
                    allowed_paths=["tests/b.py"],
                ),
            ],
        ),
    ]
    config = ManagerConfig.from_dict(
        {
            "policies": {
                "phase_completion": {"adversarial_review": "risk_based"},
                "project_completion": {"adversarial_review": "off"},
            }
        }
    )
    plan = _make_plan(phases=phases)

    decisions = PhasePolicyApplier(config).apply(plan, cli_gate_scope_explicit=True)

    phase1, phase2 = plan.phases
    assert [s.step_id for s in phase1.steps] == ["1.1"]
    assert [s.step_id for s in phase2.steps] == ["2.1", "review-2"]
    assert decisions.injected_review_steps == ["review-2"]

    _round_trip(plan)


def test_off_injects_nothing() -> None:
    config = _no_review_config()
    plan = _make_plan()
    before = plan.to_dict()

    decisions = PhasePolicyApplier(config).apply(plan, cli_gate_scope_explicit=True)

    assert plan.to_dict() == before
    assert decisions.injected_review_steps == []
    assert decisions.final_review_step is None


def test_project_completion_always_adds_final_review() -> None:
    config = ManagerConfig.from_dict(
        {"policies": {"phase_completion": {"adversarial_review": "off"}}}
    )
    assert config.policies.project_completion.adversarial_review == "always"
    plan = _make_plan()

    decisions = PhasePolicyApplier(config).apply(plan, cli_gate_scope_explicit=True)

    phase1, phase2 = plan.phases
    assert [s.step_id for s in phase1.steps] == ["1.1", "1.2"]
    assert [s.step_id for s in phase2.steps] == ["2.1", "review-2-final"]

    final_step = phase2.steps[-1]
    assert final_step.agent_name == config.policies.review_agents.project_review
    assert final_step.depends_on == ["2.1"]
    assert final_step.deliverables == ["review verdict"]
    assert final_step.parallel_safe is False

    assert decisions.final_review_step == "review-2-final"
    assert decisions.injected_review_steps == []

    _round_trip(plan)


def test_handoff_required_recorded_not_mutating() -> None:
    config = _no_review_config(policies={"phase_completion": {"handoff_required": True}})
    assert config.policies.phase_completion.handoff_required is True
    plan = _make_plan()
    before = plan.to_dict()

    decisions = PhasePolicyApplier(config).apply(plan, cli_gate_scope_explicit=True)

    assert decisions == PolicyDecisions(
        handoff_required=True,
        gates_mode=config.gates.mode,
        injected_review_steps=[],
        final_review_step=None,
    )
    assert plan.to_dict() == before


def test_gate_scope_respects_explicit_cli() -> None:
    def _plan_with_gate() -> MachinePlan:
        return _make_plan(
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Implementation",
                    steps=[
                        PlanStep(
                            step_id="1.1",
                            agent_name="backend-engineer",
                            task_description="Do the work.",
                            deliverables=["work"],
                            allowed_paths=["app/a.py"],
                        ),
                    ],
                    gate=PlanGate(
                        gate_type="build",
                        command="echo original-gate",
                        description="original description",
                        fail_on=["original failure"],
                    ),
                ),
            ]
        )

    config = ManagerConfig.from_dict(
        {
            "policies": {
                "phase_completion": {"adversarial_review": "off"},
                "project_completion": {"adversarial_review": "off"},
            },
            "gates": {"mode": "project_configured", "gate_scope": "smoke"},
        }
    )
    assert config.gates.mode == "project_configured"

    explicit_plan = _plan_with_gate()
    PhasePolicyApplier(config).apply(explicit_plan, cli_gate_scope_explicit=True)
    gate = explicit_plan.phases[0].gate
    assert gate.command == "echo original-gate"
    assert gate.description == "original description"
    assert gate.fail_on == ["original failure"]

    implicit_plan = _plan_with_gate()
    decisions = PhasePolicyApplier(config).apply(
        implicit_plan, cli_gate_scope_explicit=False
    )
    gate = implicit_plan.phases[0].gate
    assert gate.command == 'python -c "import agent_baton; print(\'ok\')"'
    assert gate.description == (
        "Import smoke check — fast sanity that the package imports cleanly."
    )
    assert gate.fail_on == ["import error"]
    assert decisions.gates_mode == "project_configured"

    _round_trip(implicit_plan)


def test_gate_scope_focused_is_a_noop() -> None:
    """F1(1): the default config (``gates.gate_scope == "focused"``, not
    explicit on the CLI) must NOT rescope planner-built gates at all -- the
    planner already produced focused gates with strictly better
    information (real changed-path test scoping) than this applier has.
    Regenerating with ``changed_paths=None`` would silently discard the
    planner's scoped ``pytest tests/test_decisions.py``-style command in
    favor of a smoke fallback."""
    plan = _make_plan(
        phases=[
            PlanPhase(
                phase_id=1,
                name="Testing and docs",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="test-engineer",
                        task_description="Write tests for the decisions module.",
                        deliverables=["tests"],
                        allowed_paths=["tests/test_decisions.py"],
                    ),
                ],
                gate=PlanGate(
                    gate_type="test",
                    command="pytest tests/test_decisions.py",
                    description="Run focused test suite (scoped to 1 file(s)) with coverage report.",
                    fail_on=["test failure", "coverage below threshold"],
                ),
            ),
        ]
    )
    config = ManagerConfig.from_dict(
        {
            "policies": {
                "phase_completion": {"adversarial_review": "off"},
                "project_completion": {"adversarial_review": "off"},
            },
            # gates.mode / gates.gate_scope both left at their defaults:
            # "project_configured" / "focused".
        }
    )
    assert config.gates.mode == "project_configured"
    assert config.gates.gate_scope == "focused"

    decisions = PhasePolicyApplier(config).apply(plan, cli_gate_scope_explicit=False)

    gate = plan.phases[0].gate
    assert gate.command == "pytest tests/test_decisions.py"
    assert gate.description == (
        "Run focused test suite (scoped to 1 file(s)) with coverage report."
    )
    assert gate.fail_on == ["test failure", "coverage below threshold"]
    assert decisions.gate_scope_applied is None

    _round_trip(plan)


def test_gate_scope_full_threads_detected_stack_into_default_gate() -> None:
    """F1(2): a non-focused ``gate_scope`` must thread ``plan.detected_stack``
    into ``default_gate`` via a minimal stack shim, so a TypeScript plan
    gets the JS/TS full-gate command (``npm test``) rather than a
    python-flavored fallback."""
    plan = _make_plan(
        detected_stack="typescript",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Test",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="test-engineer",
                        task_description="Write tests for the widget.",
                        deliverables=["tests"],
                        allowed_paths=["src/widget.test.ts"],
                    ),
                ],
                gate=PlanGate(
                    gate_type="test",
                    command="pytest tests/test_widget.py",
                    description="original description",
                    fail_on=["original failure"],
                ),
            ),
        ]
    )
    config = ManagerConfig.from_dict(
        {
            "policies": {
                "phase_completion": {"adversarial_review": "off"},
                "project_completion": {"adversarial_review": "off"},
            },
            "gates": {"mode": "project_configured", "gate_scope": "full"},
        }
    )

    decisions = PhasePolicyApplier(config).apply(plan, cli_gate_scope_explicit=False)

    gate = plan.phases[0].gate
    assert gate.command == "npm test"
    assert "pytest" not in gate.command
    assert decisions.gate_scope_applied == "full"

    _round_trip(plan)


def test_injected_review_steps_use_reviewing_step_type() -> None:
    """F1b: injected review steps (both phase reviews and the final
    project review) use ``step_type="reviewing"`` -- confirmed present in
    ``planning/rules/step_types.py::AGENT_STEP_TYPE`` -- not
    ``"developing"``. This keeps `required_for_code_steps` knowledge packs
    (Wave 3 composition, gated on non-review step types) from attaching to
    review steps."""
    config = ManagerConfig.from_dict({})
    plan = _make_plan()

    PhasePolicyApplier(config).apply(plan, cli_gate_scope_explicit=True)

    phase1, phase2 = plan.phases
    review1 = next(s for s in phase1.steps if s.step_id == "review-1")
    review2 = next(s for s in phase2.steps if s.step_id == "review-2")
    final_review = next(s for s in phase2.steps if s.step_id == "review-2-final")

    assert review1.step_type == "reviewing"
    assert review2.step_type == "reviewing"
    assert final_review.step_type == "reviewing"

    _round_trip(plan)


def test_idempotent() -> None:
    config = ManagerConfig.from_dict(
        {"gates": {"mode": "off"}}
    )
    plan = _make_plan()

    decisions1 = PhasePolicyApplier(config).apply(plan, cli_gate_scope_explicit=True)
    phase1, phase2 = plan.phases
    assert [s.step_id for s in phase1.steps] == ["1.1", "1.2", "review-1"]
    assert [s.step_id for s in phase2.steps] == ["2.1", "review-2", "review-2-final"]
    assert decisions1.injected_review_steps == ["review-1", "review-2"]
    assert decisions1.final_review_step == "review-2-final"

    _round_trip(plan)

    steps_after_first = [
        [s.step_id for s in phase.steps] for phase in plan.phases
    ]

    decisions2 = PhasePolicyApplier(config).apply(plan, cli_gate_scope_explicit=True)

    steps_after_second = [
        [s.step_id for s in phase.steps] for phase in plan.phases
    ]
    assert steps_after_second == steps_after_first
    assert decisions2.injected_review_steps == []
    # Final review step still exists (from the first apply) even though
    # nothing new was injected the second time.
    assert decisions2.final_review_step == "review-2-final"

    _round_trip(plan)
