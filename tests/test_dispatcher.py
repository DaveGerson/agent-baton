"""Tests for agent_baton.core.engine.dispatcher.PromptDispatcher."""
from __future__ import annotations

import pytest

from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.models.execution import ActionType, PlanGate, PlanStep


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dispatcher() -> PromptDispatcher:
    return PromptDispatcher()


def _make_step(
    *,
    step_id: str = "1.1",
    agent_name: str = "backend-engineer--python",
    task_description: str = "Implement the foo module.",
    model: str = "sonnet",
    deliverables: list[str] | None = None,
    allowed_paths: list[str] | None = None,
    blocked_paths: list[str] | None = None,
    context_files: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task_description,
        model=model,
        deliverables=deliverables or [],
        allowed_paths=allowed_paths or [],
        blocked_paths=blocked_paths or [],
        context_files=context_files or [],
    )


# ---------------------------------------------------------------------------
# build_delegation_prompt — full fields
# ---------------------------------------------------------------------------


class TestBuildDelegationPromptFullFields:
    def test_contains_agent_role(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(agent_name="backend-engineer--python")
        prompt = dispatcher.build_delegation_prompt(
            step,
            project_description="Agent Baton orchestration engine",
        )
        assert "backend-engineer--python" in prompt

    def test_contains_project_description(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(
            step,
            project_description="Agent Baton orchestration engine",
        )
        assert "Agent Baton orchestration engine" in prompt

    def test_contains_task_description(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(task_description="Build the registry module.")
        prompt = dispatcher.build_delegation_prompt(step)
        assert "Build the registry module." in prompt

    def test_contains_shared_context(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        ctx = "Stack: Python 3.11, pytest, PyYAML"
        prompt = dispatcher.build_delegation_prompt(step, shared_context=ctx)
        assert ctx in prompt
        assert "## Shared Context" in prompt

    def test_contains_deliverables(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(deliverables=["agent_baton/core/engine/foo.py", "tests/test_foo.py"])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "agent_baton/core/engine/foo.py" in prompt
        assert "tests/test_foo.py" in prompt
        assert "## Deliverables" in prompt

    def test_contains_context_files(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(context_files=["agent_baton/models/execution.py"])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "agent_baton/models/execution.py" in prompt
        assert "## Files to Read" in prompt

    def test_contains_boundaries_allowed(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(allowed_paths=["agent_baton/core/engine/"])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "agent_baton/core/engine/" in prompt
        assert "## Boundaries" in prompt

    def test_contains_boundaries_blocked(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(blocked_paths=["agent_baton/models/"])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "agent_baton/models/" in prompt
        assert "Do NOT write to" in prompt

    def test_contains_decision_logging_section(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert "## Decision Logging" in prompt
        assert "Decisions" in prompt

    def test_contains_handoff(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        handoff = "Previous agent wrote agent_baton/core/engine/foo.py with 3 classes."
        prompt = dispatcher.build_delegation_prompt(step, handoff_from=handoff)
        assert handoff in prompt
        assert "## Previous Step Output" in prompt

    def test_handoff_first_step_fallback(self, dispatcher: PromptDispatcher) -> None:
        """When handoff_from is empty the prompt says 'first step'."""
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, handoff_from="")
        assert "first step" in prompt.lower()


# ---------------------------------------------------------------------------
# build_delegation_prompt — minimal fields
# ---------------------------------------------------------------------------


class TestBuildDelegationPromptMinimalFields:
    def test_minimal_prompt_is_string(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_minimal_no_allowed_paths_says_any(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(allowed_paths=[])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "any" in prompt

    def test_minimal_no_blocked_paths_says_none(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(blocked_paths=[])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "none" in prompt

    def test_minimal_no_context_files_has_fallback(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(context_files=[])
        prompt = dispatcher.build_delegation_prompt(step)
        # Should have a fallback message, not a blank section
        assert "## Files to Read" in prompt

    def test_minimal_no_deliverables_has_fallback(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(deliverables=[])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "## Deliverables" in prompt

    def test_task_summary_used_when_no_project_description(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, task_summary="Build the engine")
        assert "Build the engine" in prompt

    def test_no_shared_context_has_placeholder(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, shared_context="")
        assert "## Shared Context" in prompt


# ---------------------------------------------------------------------------
# build_gate_prompt — automated vs review gates
# ---------------------------------------------------------------------------


class TestBuildGatePrompt:
    def test_automated_gate_returns_command(self, dispatcher: PromptDispatcher) -> None:
        gate = PlanGate(gate_type="test", command="pytest --tb=short -q")
        result = dispatcher.build_gate_prompt(gate)
        assert result == "pytest --tb=short -q"

    def test_automated_gate_substitutes_files(self, dispatcher: PromptDispatcher) -> None:
        gate = PlanGate(gate_type="build", command="python -m py_compile {files}")
        result = dispatcher.build_gate_prompt(
            gate, files_changed=["foo.py", "bar.py"]
        )
        assert "foo.py" in result
        assert "bar.py" in result
        assert "{files}" not in result

    def test_automated_gate_no_files_placeholder_unchanged(self, dispatcher: PromptDispatcher) -> None:
        gate = PlanGate(gate_type="test", command="pytest -q")
        result = dispatcher.build_gate_prompt(gate, files_changed=["some.py"])
        # No {files} placeholder — command should be returned as-is
        assert result == "pytest -q"

    def test_review_gate_returns_prompt_string(self, dispatcher: PromptDispatcher) -> None:
        gate = PlanGate(gate_type="review", command="", description="Check for security issues.")
        result = dispatcher.build_gate_prompt(gate)
        assert isinstance(result, str)
        assert "PASS" in result or "review" in result.lower()

    def test_review_gate_includes_description(self, dispatcher: PromptDispatcher) -> None:
        gate = PlanGate(gate_type="review", command="", description="Check for security issues.")
        result = dispatcher.build_gate_prompt(gate)
        assert "Check for security issues." in result

    def test_review_gate_includes_phase_name(self, dispatcher: PromptDispatcher) -> None:
        gate = PlanGate(gate_type="review", command="")
        result = dispatcher.build_gate_prompt(gate, phase_name="Phase 2: Implementation")
        assert "Phase 2: Implementation" in result

    def test_review_gate_lists_changed_files(self, dispatcher: PromptDispatcher) -> None:
        gate = PlanGate(gate_type="review", command="")
        result = dispatcher.build_gate_prompt(
            gate, files_changed=["src/foo.py", "src/bar.py"]
        )
        assert "src/foo.py" in result
        assert "src/bar.py" in result

    def test_review_gate_includes_fail_criteria(self, dispatcher: PromptDispatcher) -> None:
        gate = PlanGate(
            gate_type="review",
            command="",
            fail_on=["no type hints", "missing docstring"],
        )
        result = dispatcher.build_gate_prompt(gate)
        assert "no type hints" in result
        assert "missing docstring" in result


# ---------------------------------------------------------------------------
# build_action — returns correct ExecutionAction
# ---------------------------------------------------------------------------


class TestBuildAction:
    def test_action_type_is_dispatch(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        action = dispatcher.build_action(step)
        assert action.action_type == ActionType.DISPATCH.value

    def test_action_agent_name_matches_step(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(agent_name="architect")
        action = dispatcher.build_action(step)
        assert action.agent_name == "architect"

    def test_action_agent_model_matches_step(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(model="opus")
        action = dispatcher.build_action(step)
        assert action.agent_model == "opus"

    def test_action_step_id_matches(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(step_id="2.3")
        action = dispatcher.build_action(step)
        assert action.step_id == "2.3"

    def test_action_delegation_prompt_is_non_empty(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        action = dispatcher.build_action(step)
        assert isinstance(action.delegation_prompt, str)
        assert len(action.delegation_prompt) > 0

    def test_action_delegation_prompt_contains_task(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(task_description="Write the router tests.")
        action = dispatcher.build_action(step)
        assert "Write the router tests." in action.delegation_prompt

    def test_action_handoff_propagated_to_prompt(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        handoff = "Registry module written with 120 lines."
        action = dispatcher.build_action(step, handoff_from=handoff)
        assert handoff in action.delegation_prompt

    def test_action_message_is_non_empty(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        action = dispatcher.build_action(step)
        assert isinstance(action.message, str)
        assert len(action.message) > 0

    def test_action_to_dict_roundtrip(self, dispatcher: PromptDispatcher) -> None:
        """ExecutionAction.to_dict() should include all dispatch fields."""
        step = _make_step(step_id="3.1", agent_name="test-engineer", model="sonnet")
        action = dispatcher.build_action(step)
        d = action.to_dict()
        assert d["action_type"] == ActionType.DISPATCH.value
        assert d["agent_name"] == "test-engineer"
        assert d["step_id"] == "3.1"
        assert "delegation_prompt" in d
