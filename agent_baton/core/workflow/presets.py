"""Named delivery-workflow presets — spec: docs/internal/adversarial-tdd-workflow-design.md §3/§4.

Presets are **code-defined data** (frozen dataclasses + a registry), not JSON
templates: the shaped plan's implementation phase depends on the base plan
``IntelligentPlanner.create_plan()`` produced for the specific task, which a
static plan JSON cannot express (see decision #3 in the design doc). This
module only defines *what a preset says should happen*; the reshape itself
lives in :mod:`agent_baton.core.workflow.applier`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowStage:
    """One row of a workflow preset's stage table (design doc §3).

    Attributes:
        stage_id: Stable identifier (e.g. ``"spec"``) — used for
            ``WorkflowSettings.stages`` overrides and stamped onto
            ``PlanStep.workflow_stage``.
        phase_name: The ``PlanPhase.name`` the applier creates for this stage.
        agent_name: Default agent for applier-created steps. Ignored for the
            harvesting stage — its agents come from the base plan.
        model: Default model tier (``haiku`` | ``sonnet`` | ``opus`` |
            ``fable``).
        step_type: Stamped on applier-created steps only; never on harvested
            steps (which keep their own ``step_type``).
        briefing_template: ``str.format``-style template with a single
            placeholder, ``{task_summary}``.
        harvests_implementation: ``True`` for exactly one stage per preset —
            the stage whose phase is populated by flattening the base plan's
            harvested implementation-like steps rather than by a
            preset-authored briefing.
    """

    stage_id: str
    phase_name: str
    agent_name: str
    model: str
    step_type: str
    briefing_template: str
    harvests_implementation: bool = False


@dataclass(frozen=True)
class WorkflowPreset:
    """A named, ordered sequence of :class:`WorkflowStage` rows."""

    name: str
    description: str
    stages: tuple[WorkflowStage, ...]


class UnknownWorkflowError(RuntimeError):
    """Raised by :func:`get_workflow_preset` for an unregistered name.

    ``core/`` cannot import ``cli/errors.BatonError`` (dependency-arrow rule,
    see ``core/CLAUDE.md``), so this is plain ``RuntimeError``-derived —
    engine-errors style. The message names the bad value and lists every
    registered preset name so the CLI can render it directly without
    re-deriving the list.
    """

    def __init__(self, name: str, available: list[str]) -> None:
        self.name = name
        self.available = list(available)
        super().__init__(
            f"Unknown workflow {name!r}. Available workflows: "
            f"{', '.join(available)}."
        )


ADVERSARIAL_TDD = WorkflowPreset(
    name="adversarial-tdd",
    description=(
        "Staged, model-tiered, adversarially verified TDD pipeline: "
        "brainstorm & spec, architecture, test authoring, test "
        "verification, implementation, implementation verification, "
        "and final review."
    ),
    stages=(
        WorkflowStage(
            stage_id="spec",
            phase_name="Brainstorm & Spec",
            agent_name="architect",
            model="fable",
            step_type="planning",
            briefing_template=(
                "Frame the capability for: {task_summary}. Capture the "
                "intended behaviors, non-goals, and acceptance criteria."
            ),
        ),
        WorkflowStage(
            stage_id="architecture",
            phase_name="Architecture",
            agent_name="architect",
            model="fable",
            step_type="planning",
            briefing_template=(
                "Design the architecture for: {task_summary}. Produce "
                "architecture notes and a file-boundary map for implementers."
            ),
        ),
        WorkflowStage(
            stage_id="test_authoring",
            phase_name="Test Authoring",
            agent_name="test-engineer",
            model="opus",
            step_type="testing",
            briefing_template=(
                "Write failing tests that encode the spec's behaviors for: "
                "{task_summary}. Do not write implementation code."
            ),
        ),
        WorkflowStage(
            stage_id="test_verification",
            phase_name="Test Verification",
            agent_name="test-adequacy-reviewer",
            model="opus",
            step_type="reviewing",
            briefing_template=(
                "Verify that the authored tests pin the intended behaviors "
                "for: {task_summary}. Flag any test that is missing, "
                "vacuous, tautological, or testing implementation details."
            ),
        ),
        WorkflowStage(
            stage_id="implementation",
            phase_name="Implementation",
            agent_name="",
            model="sonnet",
            step_type="developing",
            briefing_template="Implement: {task_summary}.",
            harvests_implementation=True,
        ),
        WorkflowStage(
            stage_id="implementation_verification",
            phase_name="Implementation Verification",
            agent_name="code-reviewer",
            model="opus",
            step_type="reviewing",
            briefing_template=(
                "Verify the implementation against the spec for: "
                "{task_summary}, beyond simply confirming that tests pass."
            ),
        ),
        WorkflowStage(
            stage_id="final_review",
            phase_name="Final Review",
            agent_name="code-reviewer",
            model="fable",
            step_type="reviewing",
            briefing_template="Review the whole slice for: {task_summary}.",
        ),
    ),
)

_REGISTRY: dict[str, WorkflowPreset] = {ADVERSARIAL_TDD.name: ADVERSARIAL_TDD}


def get_workflow_preset(name: str) -> WorkflowPreset:
    """Look up a registered preset by name.

    Raises:
        UnknownWorkflowError: *name* is not registered. The message lists
            every registered preset name.
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownWorkflowError(
            name, [p.name for p in _REGISTRY.values()]
        ) from None


def list_workflow_presets() -> list[WorkflowPreset]:
    """Return every registered preset, in registration order."""
    return list(_REGISTRY.values())
