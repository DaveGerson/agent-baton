"""Tests for :mod:`agent_baton.core.workflow.presets`.

Pins the preset registry contract (§4) and the ``adversarial-tdd`` stage
table (§3) of docs/internal/adversarial-tdd-workflow-design.md.

The stage table is the user-visible promise of the feature: acceptance
criterion #1 says a saved plan's per-step models must read
``fable/fable/opus/opus/sonnet/opus/fable``, which is exactly the tier column
asserted here.
"""
from __future__ import annotations

import dataclasses

import pytest

from agent_baton.core.workflow.presets import (
    ADVERSARIAL_TDD,
    InvalidWorkflowPresetError,
    UnknownWorkflowError,
    WorkflowPreset,
    WorkflowStage,
    get_workflow_preset,
    list_workflow_presets,
    validate_preset,
)

# (stage_id, phase_name, agent_name, model, step_type) — spec §3, in order.
# The harvesting stage (#5) is absent from this table on purpose: its agents
# come from the base plan and its ``step_type`` is preserved from the
# harvested steps, so the preset's own values for those two fields are
# unused. Its id/name/tier are asserted separately below.
_CREATED_STAGES: list[tuple[str, str, str, str, str]] = [
    ("spec", "Brainstorm & Spec", "architect", "fable", "planning"),
    ("architecture", "Architecture", "architect", "fable", "planning"),
    ("test_authoring", "Test Authoring", "test-engineer", "opus", "testing"),
    (
        "test_verification",
        "Test Verification",
        "test-adequacy-reviewer",
        "opus",
        "reviewing",
    ),
    (
        "implementation_verification",
        "Implementation Verification",
        "code-reviewer",
        "opus",
        "reviewing",
    ),
    ("final_review", "Final Review", "code-reviewer", "fable", "reviewing"),
]

_STAGE_ORDER = [
    "spec",
    "architecture",
    "test_authoring",
    "test_verification",
    "implementation",
    "implementation_verification",
    "final_review",
]

_TIER_ORDER = ["fable", "fable", "opus", "opus", "sonnet", "opus", "fable"]


def _stage(stage_id: str) -> WorkflowStage:
    matches = [s for s in ADVERSARIAL_TDD.stages if s.stage_id == stage_id]
    assert len(matches) == 1, f"expected exactly one {stage_id!r} stage"
    return matches[0]


# ---------------------------------------------------------------------------
# Registry lookup
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_get_returns_the_adversarial_tdd_preset(self) -> None:
        assert get_workflow_preset("adversarial-tdd") == ADVERSARIAL_TDD

    def test_preset_name_is_the_cli_spelling(self) -> None:
        assert ADVERSARIAL_TDD.name == "adversarial-tdd"

    def test_preset_has_a_description(self) -> None:
        assert ADVERSARIAL_TDD.description.strip()

    def test_list_includes_adversarial_tdd(self) -> None:
        names = [p.name for p in list_workflow_presets()]
        assert "adversarial-tdd" in names

    def test_list_returns_preset_objects(self) -> None:
        assert all(isinstance(p, WorkflowPreset) for p in list_workflow_presets())

    def test_list_names_are_unique(self) -> None:
        names = [p.name for p in list_workflow_presets()]
        assert len(names) == len(set(names))

    def test_every_registered_preset_is_retrievable_by_name(self) -> None:
        for preset in list_workflow_presets():
            assert get_workflow_preset(preset.name) == preset


# ---------------------------------------------------------------------------
# Unknown-name error
# ---------------------------------------------------------------------------

class TestUnknownWorkflowError:
    def test_is_a_runtime_error(self) -> None:
        # §2 decision #11: engine-errors style — core/ cannot import
        # cli/errors.BatonError, so the base is RuntimeError.
        assert issubclass(UnknownWorkflowError, RuntimeError)

    def test_unknown_name_raises(self) -> None:
        with pytest.raises(UnknownWorkflowError):
            get_workflow_preset("no-such-workflow")

    def test_message_names_the_bad_value_and_lists_available_presets(self) -> None:
        with pytest.raises(UnknownWorkflowError) as exc_info:
            get_workflow_preset("no-such-workflow")

        message = str(exc_info.value)
        assert "no-such-workflow" in message
        for preset in list_workflow_presets():
            assert preset.name in message


# ---------------------------------------------------------------------------
# adversarial-tdd stage table (spec §3)
# ---------------------------------------------------------------------------

class TestAdversarialTddStageTable:
    def test_stage_ids_in_order(self) -> None:
        assert [s.stage_id for s in ADVERSARIAL_TDD.stages] == _STAGE_ORDER

    def test_model_tiers_in_order(self) -> None:
        assert [s.model for s in ADVERSARIAL_TDD.stages] == _TIER_ORDER

    def test_phase_names_in_order(self) -> None:
        assert [s.phase_name for s in ADVERSARIAL_TDD.stages] == [
            "Brainstorm & Spec",
            "Architecture",
            "Test Authoring",
            "Test Verification",
            "Implementation",
            "Implementation Verification",
            "Final Review",
        ]

    @pytest.mark.parametrize(
        ("stage_id", "phase_name", "agent_name", "model", "step_type"),
        _CREATED_STAGES,
        ids=[row[0] for row in _CREATED_STAGES],
    )
    def test_created_stage_row(
        self,
        stage_id: str,
        phase_name: str,
        agent_name: str,
        model: str,
        step_type: str,
    ) -> None:
        stage = _stage(stage_id)
        assert stage.phase_name == phase_name
        assert stage.agent_name == agent_name
        assert stage.model == model
        assert stage.step_type == step_type

    def test_implementation_stage_is_sonnet_tier(self) -> None:
        assert _stage("implementation").model == "sonnet"
        assert _stage("implementation").phase_name == "Implementation"

    def test_exactly_one_harvesting_stage(self) -> None:
        harvesting = [s for s in ADVERSARIAL_TDD.stages if s.harvests_implementation]
        assert [s.stage_id for s in harvesting] == ["implementation"]

    def test_non_harvesting_stages_default_the_flag_off(self) -> None:
        for stage in ADVERSARIAL_TDD.stages:
            if stage.stage_id != "implementation":
                assert stage.harvests_implementation is False

    def test_last_stage_is_a_review_phase(self) -> None:
        # §3 stage 7 (amended): "the plan's last NON-CARRYOVER phase must be
        # named '…Review'" — audit-carryover phases may follow it, so this
        # invariant lives on the preset, not on the applied plan.
        assert ADVERSARIAL_TDD.stages[-1].phase_name.endswith("Review")

    def test_tiers_are_known_model_families(self) -> None:
        for stage in ADVERSARIAL_TDD.stages:
            assert stage.model in {"haiku", "sonnet", "opus", "fable"}

    def test_every_preset_has_exactly_one_harvesting_stage(self) -> None:
        for preset in list_workflow_presets():
            harvesting = [s for s in preset.stages if s.harvests_implementation]
            assert len(harvesting) == 1, f"{preset.name} must harvest exactly once"

    def test_stage_ids_unique_within_preset(self) -> None:
        for preset in list_workflow_presets():
            ids = [s.stage_id for s in preset.stages]
            assert len(ids) == len(set(ids)), f"{preset.name} has duplicate stage ids"


# ---------------------------------------------------------------------------
# Briefing templates
# ---------------------------------------------------------------------------

class TestBriefingTemplates:
    def test_every_stage_has_a_briefing(self) -> None:
        for stage in ADVERSARIAL_TDD.stages:
            assert stage.briefing_template.strip(), stage.stage_id

    def test_briefings_format_with_task_summary_only(self) -> None:
        # §4: "str.format-style; placeholder: {task_summary}". Any other
        # placeholder would raise KeyError at apply() time.
        for stage in ADVERSARIAL_TDD.stages:
            rendered = stage.briefing_template.format(task_summary="ship the thing")
            assert "{" not in rendered.replace("{{", "").replace("}}", ""), (
                f"{stage.stage_id} briefing has an unexpanded placeholder"
            )


# ---------------------------------------------------------------------------
# Immutability (§2 decision #3: frozen dataclasses)
# ---------------------------------------------------------------------------

class TestPresetsAreImmutable:
    def test_preset_is_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            ADVERSARIAL_TDD.name = "mutated"  # type: ignore[misc]

    def test_stage_is_frozen(self) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            ADVERSARIAL_TDD.stages[0].model = "haiku"  # type: ignore[misc]

    def test_stages_is_a_tuple(self) -> None:
        assert isinstance(ADVERSARIAL_TDD.stages, tuple)


# ---------------------------------------------------------------------------
# Structural invariants at registration time (§4 note)
# ---------------------------------------------------------------------------

def _make_stage(stage_id: str, *, harvests: bool = False, phase_name: str = "X") -> WorkflowStage:
    return WorkflowStage(
        stage_id=stage_id,
        phase_name=phase_name,
        agent_name="architect",
        model="sonnet",
        step_type="developing",
        briefing_template="Do the thing for: {task_summary}.",
        harvests_implementation=harvests,
    )


class TestPresetStructuralInvariants:
    def test_shipped_preset_is_already_valid(self) -> None:
        # validate_preset() must be a no-op (return the same object) for
        # the preset this module actually registers.
        assert validate_preset(ADVERSARIAL_TDD) is ADVERSARIAL_TDD

    def test_empty_stages_is_rejected(self) -> None:
        preset = WorkflowPreset(name="broken", description="", stages=())
        with pytest.raises(InvalidWorkflowPresetError) as exc_info:
            validate_preset(preset)
        assert "broken" in str(exc_info.value)

    def test_zero_harvesting_stages_is_rejected(self) -> None:
        preset = WorkflowPreset(
            name="broken",
            description="",
            stages=(
                _make_stage("a"),
                _make_stage("b", phase_name="Review"),
            ),
        )
        with pytest.raises(InvalidWorkflowPresetError) as exc_info:
            validate_preset(preset)
        assert "exactly one harvesting stage" in str(exc_info.value)

    def test_two_harvesting_stages_is_rejected(self) -> None:
        preset = WorkflowPreset(
            name="broken",
            description="",
            stages=(
                _make_stage("a", harvests=True),
                _make_stage("b", harvests=True, phase_name="Review"),
            ),
        )
        with pytest.raises(InvalidWorkflowPresetError) as exc_info:
            validate_preset(preset)
        assert "exactly one harvesting stage" in str(exc_info.value)

    def test_harvesting_stage_last_is_rejected(self) -> None:
        preset = WorkflowPreset(
            name="broken",
            description="",
            stages=(
                _make_stage("a", phase_name="Review"),
                _make_stage("b", harvests=True),
            ),
        )
        with pytest.raises(InvalidWorkflowPresetError) as exc_info:
            validate_preset(preset)
        assert "fan-out final-review stage" in str(exc_info.value)

    def test_last_stage_not_named_review_is_rejected(self) -> None:
        preset = WorkflowPreset(
            name="broken",
            description="",
            stages=(
                _make_stage("a", harvests=True),
                _make_stage("b", phase_name="Wrap-up"),
            ),
        )
        with pytest.raises(InvalidWorkflowPresetError) as exc_info:
            validate_preset(preset)
        message = str(exc_info.value)
        assert "Wrap-up" in message
        assert "Review" in message

    def test_error_is_a_runtime_error(self) -> None:
        assert issubclass(InvalidWorkflowPresetError, RuntimeError)
