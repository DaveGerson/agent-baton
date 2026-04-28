"""Tests for Wave 3.1 — Expected Outcome (Demo Statements).

Covers:
  - PlanStep.expected_outcome default + serialization round-trip.
  - Planner derivation of behavioral demo statements.
  - Dispatcher prompt section rendering.
  - plan.md rendering.

Resolves bd-6c5d.
"""
from __future__ import annotations

import pytest

from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.engine.planner import _derive_expected_outcome
from agent_baton.models.execution import (
    MachinePlan,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# PlanStep dataclass — field default + serialization back-compat
# ---------------------------------------------------------------------------


def test_step_dataclass_default_empty() -> None:
    """expected_outcome defaults to '' so existing call sites don't change."""
    step = PlanStep(
        step_id="1.1", agent_name="backend-engineer", task_description="x"
    )
    assert step.expected_outcome == ""


def test_step_to_dict_omits_empty() -> None:
    """When expected_outcome is empty it is omitted from to_dict.

    Preserves the existing JSON shape for plans created before Wave 3.1.
    """
    step = PlanStep(
        step_id="1.1", agent_name="backend-engineer", task_description="x"
    )
    d = step.to_dict()
    assert "expected_outcome" not in d


def test_step_to_dict_includes_when_set() -> None:
    """When expected_outcome is non-empty it round-trips through to_dict/from_dict."""
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer",
        task_description="x",
        expected_outcome="After this step, X is observably working.",
    )
    d = step.to_dict()
    assert d["expected_outcome"] == "After this step, X is observably working."

    revived = PlanStep.from_dict(d)
    assert revived.expected_outcome == step.expected_outcome


def test_step_from_dict_default_when_missing() -> None:
    """Older plan.json files without expected_outcome load with empty default."""
    legacy = {
        "step_id": "1.1",
        "agent_name": "backend-engineer",
        "task_description": "Add OAuth2 login.",
    }
    revived = PlanStep.from_dict(legacy)
    assert revived.expected_outcome == ""


# ---------------------------------------------------------------------------
# Planner derivation — _derive_expected_outcome
# ---------------------------------------------------------------------------


def test_planner_derives_outcome_for_implementation_step() -> None:
    """Implementation steps get an 'observably working' demo statement."""
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer--python",
        task_description="Implement: add OAuth2 login flow",
        step_type="developing",
    )
    outcome = _derive_expected_outcome(step, task_summary="add OAuth2 to checkout")
    assert outcome.startswith("After this step,")
    assert "OAuth2 login flow" in outcome
    assert "observably working" in outcome


def test_planner_derives_outcome_for_test_step() -> None:
    """Test-engineer / testing steps get a coverage-style demo statement."""
    step = PlanStep(
        step_id="2.1",
        agent_name="test-engineer",
        task_description="Write tests to verify: OAuth2 login flow",
        step_type="testing",
    )
    outcome = _derive_expected_outcome(step, task_summary="add OAuth2")
    assert outcome.startswith("After this step,")
    assert "automated test" in outcome
    assert "fails before" in outcome and "passes after" in outcome


def test_planner_returns_empty_for_blank_description() -> None:
    """No description means no derivable outcome — preserves back-compat."""
    step = PlanStep(
        step_id="1.1", agent_name="backend-engineer", task_description=""
    )
    assert _derive_expected_outcome(step, "") == ""


# ---------------------------------------------------------------------------
# Dispatcher rendering — Expected Outcome section in delegation prompt
# ---------------------------------------------------------------------------


@pytest.fixture
def dispatcher() -> PromptDispatcher:
    return PromptDispatcher()


def _make_step_with_outcome(outcome: str) -> PlanStep:
    return PlanStep(
        step_id="1.1",
        agent_name="backend-engineer--python",
        task_description="Implement the OAuth2 flow.",
        expected_outcome=outcome,
    )


def test_dispatcher_prepends_expected_outcome_section(
    dispatcher: PromptDispatcher,
) -> None:
    """When step has expected_outcome the prompt includes a marked section."""
    outcome = "After this step, OAuth2 login is observably working."
    step = _make_step_with_outcome(outcome)
    prompt = dispatcher.build_delegation_prompt(step)
    assert "## Expected Outcome" in prompt
    assert outcome in prompt
    task_idx = prompt.index("## Your Task")
    eo_idx = prompt.index("## Expected Outcome")
    assert eo_idx > task_idx


def test_dispatcher_no_section_when_empty(
    dispatcher: PromptDispatcher,
) -> None:
    """When expected_outcome is empty the section is omitted entirely."""
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer--python",
        task_description="Implement the OAuth2 flow.",
    )
    prompt = dispatcher.build_delegation_prompt(step)
    assert "## Expected Outcome" not in prompt


# ---------------------------------------------------------------------------
# plan.md rendering — MachinePlan.to_markdown
# ---------------------------------------------------------------------------


def test_plan_md_renders_expected_outcome() -> None:
    """to_markdown emits an '- **Expected outcome**: ...' line per step."""
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer",
        task_description="Add OAuth2.",
        expected_outcome="After this step, OAuth2 is implemented and observably working.",
    )
    phase = PlanPhase(phase_id=1, name="Implement", steps=[step])
    plan = MachinePlan(
        task_id="t1",
        task_summary="Add OAuth2 to checkout",
        phases=[phase],
    )
    md = plan.to_markdown()
    assert "**Expected outcome**" in md
    assert "OAuth2 is implemented and observably working" in md


def test_plan_md_omits_expected_outcome_when_empty() -> None:
    """Step without expected_outcome does not emit the marker line."""
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer",
        task_description="Add OAuth2.",
    )
    phase = PlanPhase(phase_id=1, name="Implement", steps=[step])
    plan = MachinePlan(
        task_id="t1",
        task_summary="Add OAuth2 to checkout",
        phases=[phase],
    )
    md = plan.to_markdown()
    assert "**Expected outcome**" not in md
