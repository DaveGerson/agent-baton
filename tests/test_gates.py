"""Tests for agent_baton.core.engine.gates.GateRunner."""
from __future__ import annotations

import pytest

from agent_baton.core.engine.gates import GateRunner, _has_lint_errors
from agent_baton.models.execution import ActionType, GateResult, PlanGate


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> GateRunner:
    return GateRunner()


def _make_gate(
    gate_type: str = "test",
    command: str = "pytest --tb=short -q",
    description: str = "",
    fail_on: list[str] | None = None,
) -> PlanGate:
    return PlanGate(
        gate_type=gate_type,
        command=command,
        description=description,
        fail_on=fail_on or [],
    )


# ---------------------------------------------------------------------------
# Helper: _has_lint_errors
# ---------------------------------------------------------------------------


class TestHasLintErrors:
    def test_no_errors_in_empty_output(self) -> None:
        assert _has_lint_errors("") is False

    def test_no_errors_in_clean_output(self) -> None:
        assert _has_lint_errors("All checks passed.\n1 file checked.") is False

    def test_detects_error_colon(self) -> None:
        assert _has_lint_errors("foo.py:10: error: unexpected indent") is True

    def test_detects_syntax_error(self) -> None:
        assert _has_lint_errors("SyntaxError: invalid syntax at line 5") is True

    def test_does_not_flag_warning_only(self) -> None:
        assert _has_lint_errors("foo.py:3: warning: unused import") is False

    def test_detects_E_bracket_marker(self) -> None:
        assert _has_lint_errors("foo.py:1:1: [E001] Missing module docstring") is True


# ---------------------------------------------------------------------------
# describe_gate
# ---------------------------------------------------------------------------


class TestDescribeGate:
    def test_returns_custom_description_when_set(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="test", description="Custom description.")
        assert runner.describe_gate(gate) == "Custom description."

    def test_build_gate_description(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="build", description="")
        desc = runner.describe_gate(gate)
        assert "compiles" in desc.lower() or "build" in desc.lower()

    def test_test_gate_description(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="test", description="")
        desc = runner.describe_gate(gate)
        assert "test" in desc.lower()

    def test_lint_gate_description(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="lint", description="")
        desc = runner.describe_gate(gate)
        assert "lint" in desc.lower() or "style" in desc.lower() or "error" in desc.lower()

    def test_spec_gate_description(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="spec", description="")
        desc = runner.describe_gate(gate)
        assert isinstance(desc, str)
        assert len(desc) > 0

    def test_review_gate_description(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="review", command="", description="")
        desc = runner.describe_gate(gate)
        assert "review" in desc.lower()

    def test_unknown_gate_type_returns_string(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="custom-check", description="")
        desc = runner.describe_gate(gate)
        assert isinstance(desc, str)
        assert len(desc) > 0


# ---------------------------------------------------------------------------
# build_gate_action
# ---------------------------------------------------------------------------


class TestBuildGateAction:
    def test_action_type_is_gate(self, runner: GateRunner) -> None:
        gate = _make_gate()
        action = runner.build_gate_action(gate, phase_id=1)
        assert action.action_type == ActionType.GATE.value

    def test_phase_id_propagated(self, runner: GateRunner) -> None:
        gate = _make_gate()
        action = runner.build_gate_action(gate, phase_id=3)
        assert action.phase_id == 3

    def test_gate_type_propagated(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="lint")
        action = runner.build_gate_action(gate, phase_id=0)
        assert action.gate_type == "lint"

    def test_gate_command_propagated(self, runner: GateRunner) -> None:
        gate = _make_gate(command="pytest -q")
        action = runner.build_gate_action(gate, phase_id=1)
        assert action.gate_command == "pytest -q"

    def test_files_placeholder_substituted(self, runner: GateRunner) -> None:
        gate = _make_gate(command="python -m py_compile {files}")
        action = runner.build_gate_action(gate, phase_id=1, files_changed=["a.py", "b.py"])
        assert "{files}" not in action.gate_command
        assert "a.py" in action.gate_command
        assert "b.py" in action.gate_command

    def test_no_files_no_substitution(self, runner: GateRunner) -> None:
        gate = _make_gate(command="python -m py_compile {files}")
        action = runner.build_gate_action(gate, phase_id=1, files_changed=None)
        # No files passed — placeholder remains untouched
        assert "{files}" in action.gate_command

    def test_message_is_non_empty(self, runner: GateRunner) -> None:
        gate = _make_gate()
        action = runner.build_gate_action(gate, phase_id=2)
        assert isinstance(action.message, str)
        assert len(action.message) > 0

    def test_to_dict_includes_gate_fields(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="test", command="pytest")
        action = runner.build_gate_action(gate, phase_id=1)
        d = action.to_dict()
        assert d["action_type"] == ActionType.GATE.value
        assert d["gate_type"] == "test"
        assert d["phase_id"] == 1
        assert "gate_command" in d


# ---------------------------------------------------------------------------
# evaluate_output
# ---------------------------------------------------------------------------


class TestEvaluateOutput:
    # -- test gate --

    def test_test_gate_exit_0_passes(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="test")
        result = runner.evaluate_output(gate, "5 passed", exit_code=0)
        assert result.passed is True

    def test_test_gate_nonzero_exit_fails(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="test")
        result = runner.evaluate_output(gate, "2 failed, 3 passed", exit_code=1)
        assert result.passed is False

    # -- build gate --

    def test_build_gate_exit_0_passes(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="build")
        result = runner.evaluate_output(gate, "", exit_code=0)
        assert result.passed is True

    def test_build_gate_nonzero_exit_fails(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="build")
        result = runner.evaluate_output(gate, "SyntaxError: invalid syntax", exit_code=1)
        assert result.passed is False

    # -- lint gate --

    def test_lint_gate_clean_exit_0_passes(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="lint")
        result = runner.evaluate_output(gate, "All files clean.", exit_code=0)
        assert result.passed is True

    def test_lint_gate_warnings_only_passes(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="lint")
        result = runner.evaluate_output(gate, "foo.py:3: warning: unused import", exit_code=0)
        assert result.passed is True

    def test_lint_gate_errors_in_output_fails(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="lint")
        output = "foo.py:10: error: unexpected indent"
        result = runner.evaluate_output(gate, output, exit_code=0)
        assert result.passed is False

    def test_lint_gate_nonzero_exit_fails(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="lint")
        result = runner.evaluate_output(gate, "All clean.", exit_code=2)
        assert result.passed is False

    # -- spec gate --

    def test_spec_gate_exit_0_with_output_passes(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="spec")
        result = runner.evaluate_output(gate, "Spec validated OK.", exit_code=0)
        assert result.passed is True

    def test_spec_gate_nonzero_exit_fails(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="spec")
        result = runner.evaluate_output(gate, "Schema mismatch found.", exit_code=1)
        assert result.passed is False

    def test_spec_gate_empty_output_fails(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="spec")
        result = runner.evaluate_output(gate, "", exit_code=0)
        assert result.passed is False

    # -- review gate --

    def test_review_gate_always_passes_zero_exit(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="review", command="")
        result = runner.evaluate_output(gate, "Looks good overall.", exit_code=0)
        assert result.passed is True

    def test_review_gate_always_passes_nonzero_exit(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="review", command="")
        result = runner.evaluate_output(gate, "FAIL: missing docstrings.", exit_code=1)
        assert result.passed is True

    def test_review_gate_always_passes_negative_output(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="review", command="")
        result = runner.evaluate_output(gate, "Multiple critical issues found.", exit_code=2)
        assert result.passed is True

    # -- GateResult fields --

    def test_gate_result_output_field(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="test")
        result = runner.evaluate_output(gate, "3 passed", exit_code=0)
        assert result.output == "3 passed"

    def test_gate_result_gate_type_field(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="build")
        result = runner.evaluate_output(gate, "", exit_code=0)
        assert result.gate_type == "build"

    def test_gate_result_checked_at_is_iso(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="test")
        result = runner.evaluate_output(gate, "", exit_code=0)
        # Should be a non-empty ISO datetime string
        assert isinstance(result.checked_at, str)
        assert "T" in result.checked_at  # ISO format indicator

    def test_gate_result_is_gate_result_instance(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="test")
        result = runner.evaluate_output(gate, "", exit_code=0)
        assert isinstance(result, GateResult)

    def test_gate_result_to_dict_roundtrip(self, runner: GateRunner) -> None:
        gate = _make_gate(gate_type="test")
        result = runner.evaluate_output(gate, "5 passed", exit_code=0)
        d = result.to_dict()
        assert d["gate_type"] == "test"
        assert d["passed"] is True
        assert d["output"] == "5 passed"


# ---------------------------------------------------------------------------
# default_gates
# ---------------------------------------------------------------------------


class TestDefaultGates:
    def test_returns_dict(self, runner: GateRunner) -> None:
        gates = GateRunner.default_gates()
        assert isinstance(gates, dict)

    def test_contains_build(self, runner: GateRunner) -> None:
        gates = GateRunner.default_gates()
        assert "build" in gates

    def test_contains_test(self, runner: GateRunner) -> None:
        gates = GateRunner.default_gates()
        assert "test" in gates

    def test_contains_lint(self, runner: GateRunner) -> None:
        gates = GateRunner.default_gates()
        assert "lint" in gates

    def test_contains_review(self, runner: GateRunner) -> None:
        gates = GateRunner.default_gates()
        assert "review" in gates

    def test_all_values_are_plan_gate(self, runner: GateRunner) -> None:
        gates = GateRunner.default_gates()
        for name, gate in gates.items():
            assert isinstance(gate, PlanGate), f"{name} is not a PlanGate"

    def test_build_gate_has_command(self) -> None:
        gate = GateRunner.default_gates()["build"]
        assert gate.command != ""

    def test_test_gate_has_pytest_command(self) -> None:
        gate = GateRunner.default_gates()["test"]
        assert "pytest" in gate.command

    def test_lint_gate_type_is_lint(self) -> None:
        gate = GateRunner.default_gates()["lint"]
        assert gate.gate_type == "lint"

    def test_review_gate_has_no_command(self) -> None:
        gate = GateRunner.default_gates()["review"]
        assert gate.command == ""

    def test_review_gate_type_is_review(self) -> None:
        gate = GateRunner.default_gates()["review"]
        assert gate.gate_type == "review"

    def test_returns_fresh_dict_each_call(self) -> None:
        gates1 = GateRunner.default_gates()
        gates2 = GateRunner.default_gates()
        assert gates1 is not gates2
