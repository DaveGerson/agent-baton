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
# DECISION: Merged 8 separate field-presence tests into 1 parametrized test.
# Each tuple is (step_kwargs, prompt_kwargs, expected_substring).
# test_contains_shared_context kept separate because it checks TWO strings.
# test_contains_deliverables kept separate for same reason.
# test_contains_decision_logging_section kept separate (checks two strings).
# test_handoff_first_step_fallback kept separate (edge-case, not field presence).
# ---------------------------------------------------------------------------


class TestBuildDelegationPromptFullFields:
    @pytest.mark.parametrize("build_kwargs,expected", [
        # agent name
        ({"agent_name": "backend-engineer--python"}, "backend-engineer--python"),
        # project description
        ({}, "Agent Baton orchestration engine"),
        # task description
        ({"task_description": "Build the registry module."}, "Build the registry module."),
        # allowed path
        ({"allowed_paths": ["agent_baton/core/engine/"]}, "agent_baton/core/engine/"),
        # blocked path
        ({"blocked_paths": ["agent_baton/models/"]}, "agent_baton/models/"),
    ])
    def test_prompt_contains_field(
        self,
        dispatcher: PromptDispatcher,
        build_kwargs: dict,
        expected: str,
    ) -> None:
        step_kwargs: dict = {}
        for k in ("agent_name", "task_description", "allowed_paths", "blocked_paths"):
            if k in build_kwargs:
                step_kwargs[k] = build_kwargs.pop(k)
        step = _make_step(**step_kwargs)
        prompt = dispatcher.build_delegation_prompt(
            step, project_description="Agent Baton orchestration engine"
        )
        assert expected in prompt

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

    def test_contains_boundaries_blocked(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(blocked_paths=["agent_baton/models/"])
        prompt = dispatcher.build_delegation_prompt(step)
        assert "Do NOT write to" in prompt

    def test_contains_decision_logging_section(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert "**Decisions**" in prompt

    def test_contains_handoff(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        handoff = "Previous agent wrote agent_baton/core/engine/foo.py with 3 classes."
        prompt = dispatcher.build_delegation_prompt(step, handoff_from=handoff)
        assert handoff in prompt
        assert "## Previous Step Output" in prompt

    def test_handoff_omitted_when_empty(self, dispatcher: PromptDispatcher) -> None:
        """When handoff_from is empty the Previous Step Output section is omitted."""
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, handoff_from="")
        assert "## Previous Step Output" not in prompt


# ---------------------------------------------------------------------------
# build_delegation_prompt — minimal fields
# DECISION: Merged 5 fallback-content tests into 1 parametrized test.
# test_minimal_prompt_is_string removed: isinstance + len > 0 is trivial.
# test_task_summary_used and test_no_shared_context kept separate (distinct kwargs).
# ---------------------------------------------------------------------------


class TestBuildDelegationPromptMinimalFields:
    @pytest.mark.parametrize("step_kwargs,absent_section", [
        ({"allowed_paths": []}, "## Boundaries"),
        ({"blocked_paths": []}, "## Boundaries"),
        ({"context_files": []}, "## Files to Read"),
        ({"deliverables": []}, "## Deliverables"),
    ])
    def test_empty_sections_omitted(
        self,
        dispatcher: PromptDispatcher,
        step_kwargs: dict,
        absent_section: str,
    ) -> None:
        """Empty optional sections are omitted to keep prompts concise."""
        step = _make_step(**step_kwargs)
        prompt = dispatcher.build_delegation_prompt(step)
        assert absent_section not in prompt

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
# DECISION: Merged 8 action field-copy tests into 2 parametrized tests.
# test_action_type_is_dispatch, test_action_agent_name_matches_step,
# test_action_agent_model_matches_step, test_action_step_id_matches merged
# into test_action_scalar_fields. The delegation-prompt and path-enforcement
# tests stay separate as they involve richer assertion logic.
# ---------------------------------------------------------------------------


class TestBuildAction:
    @pytest.mark.parametrize("step_kwargs,field,expected", [
        ({}, "action_type", ActionType.DISPATCH),
        ({"agent_name": "architect"}, "agent_name", "architect"),
        ({"model": "opus"}, "agent_model", "opus"),
        ({"step_id": "2.3"}, "step_id", "2.3"),
    ])
    def test_action_scalar_fields(
        self,
        dispatcher: PromptDispatcher,
        step_kwargs: dict,
        field: str,
        expected: object,
    ) -> None:
        step = _make_step(**step_kwargs)
        action = dispatcher.build_action(step)
        assert getattr(action, field) == expected

    def test_action_delegation_prompt_contains_task(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step(task_description="Write the router tests.")
        action = dispatcher.build_action(step)
        assert isinstance(action.delegation_prompt, str)
        assert len(action.delegation_prompt) > 0
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

    @pytest.mark.parametrize("step_kwargs,expected_empty", [
        ({}, True),                                   # no restrictions → empty
        ({"allowed_paths": ["agent_baton/"]}, False), # restrictions → populated
    ])
    def test_action_path_enforcement(
        self,
        dispatcher: PromptDispatcher,
        step_kwargs: dict,
        expected_empty: bool,
    ) -> None:
        step = _make_step(**step_kwargs)
        action = dispatcher.build_action(step)
        if expected_empty:
            assert action.path_enforcement == ""
        else:
            assert action.path_enforcement != ""
            assert "BLOCKED" in action.path_enforcement

    def test_action_to_dict_includes_path_enforcement(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step(step_id="4.1", allowed_paths=["src/"])
        action = dispatcher.build_action(step)
        d = action.to_dict()
        assert "path_enforcement" in d
        assert d["path_enforcement"] != ""


# ---------------------------------------------------------------------------
# build_path_enforcement — mechanical path guard generation
# ---------------------------------------------------------------------------


class TestBuildPathEnforcement:
    """Tests for mechanical path enforcement generation."""

    def test_no_restrictions_returns_none(self) -> None:
        step = PlanStep(step_id="1.1", agent_name="backend", task_description="work")
        assert PromptDispatcher.build_path_enforcement(step) is None

    @pytest.mark.parametrize("allowed,blocked,check_str", [
        (["agent_baton/", "tests/"], [], "BLOCKED"),
        ([], [".env", "secrets/"], "BLOCKED"),
        (["src/"], ["src/secrets/"], "BLOCKED"),
    ])
    def test_path_enforcement_generates_guard(
        self, allowed: list[str], blocked: list[str], check_str: str
    ) -> None:
        step = PlanStep(
            step_id="1.1", agent_name="backend", task_description="work",
            allowed_paths=allowed,
            blocked_paths=blocked,
        )
        result = PromptDispatcher.build_path_enforcement(step)
        assert result is not None
        assert check_str in result

    def test_allowed_paths_dots_escaped_in_pattern(self) -> None:
        """Dots in path names are escaped so they match literally, not as regex wildcards."""
        step = PlanStep(
            step_id="2.1", agent_name="backend", task_description="work",
            allowed_paths=["agent_baton/models/execution.py"],
        )
        result = PromptDispatcher.build_path_enforcement(step)
        assert result is not None
        # The dot before 'py' should be escaped as '\.'
        assert "execution\\.py" in result

    def test_allowed_paths_wildcard_expanded_in_pattern(self) -> None:
        """An asterisk in a path entry becomes '.*' in the regex."""
        step = PlanStep(
            step_id="2.2", agent_name="backend", task_description="work",
            allowed_paths=["agent_baton/*.py"],
        )
        result = PromptDispatcher.build_path_enforcement(step)
        assert result is not None
        assert ".*\\.py" in result

    def test_step_id_embedded_in_guard_message(self) -> None:
        """The step_id appears in the BLOCKED error message for traceability."""
        step = PlanStep(
            step_id="3.5", agent_name="backend", task_description="work",
            blocked_paths=["secrets/"],
        )
        result = PromptDispatcher.build_path_enforcement(step)
        assert result is not None
        assert "3.5" in result

    def test_enforcement_in_dispatch_action(self, tmp_path: "Path") -> None:
        """ExecutionEngine includes path_enforcement in DISPATCH actions."""
        from pathlib import Path
        from agent_baton.core.engine.executor import ExecutionEngine
        from agent_baton.models.execution import MachinePlan, PlanPhase, ActionType

        plan = MachinePlan(
            task_id="test-enforce",
            task_summary="test",
            phases=[PlanPhase(phase_id=1, name="Build", steps=[
                PlanStep(
                    step_id="1.1", agent_name="backend",
                    task_description="work",
                    allowed_paths=["src/"],
                ),
            ])],
        )
        engine = ExecutionEngine(team_context_root=tmp_path)
        action = engine.start(plan)
        assert action.action_type == ActionType.DISPATCH
        assert action.path_enforcement != ""
        assert "BLOCKED" in action.path_enforcement
