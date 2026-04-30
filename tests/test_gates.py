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


@pytest.mark.parametrize("output,expected", [
    ("", False),
    ("All checks passed.\n1 file checked.", False),
    ("foo.py:3: warning: unused import", False),
    ("foo.py:10: error: unexpected indent", True),
    ("SyntaxError: invalid syntax at line 5", True),
    ("foo.py:1:1: [E001] Missing module docstring", True),
])
def test_has_lint_errors(output: str, expected: bool) -> None:
    assert _has_lint_errors(output) is expected


# ---------------------------------------------------------------------------
# describe_gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("gate_type,command,description,keywords", [
    ("test", "pytest --tb=short -q", "Custom description.", ["Custom description."]),
    ("build", "make build", "", ["compiles", "build"]),
    ("test", "pytest --tb=short -q", "", ["test"]),
    ("lint", "flake8 .", "", ["lint", "style", "error"]),
    ("spec", "openapi-spec-validator spec.yaml", "", None),
    ("review", "", "", ["review"]),
    ("custom-check", "./check.sh", "", None),
])
def test_describe_gate(
    runner: GateRunner,
    gate_type: str,
    command: str,
    description: str,
    keywords: list[str] | None,
) -> None:
    gate = _make_gate(gate_type=gate_type, command=command, description=description)
    desc = runner.describe_gate(gate)
    assert isinstance(desc, str)
    assert len(desc) > 0
    if description:
        # exact custom description returned verbatim
        assert desc == description
    elif keywords is not None:
        assert any(kw.lower() in desc.lower() for kw in keywords)


# ---------------------------------------------------------------------------
# build_gate_action — field propagation
# ---------------------------------------------------------------------------


def test_build_gate_action_type_is_gate(runner: GateRunner) -> None:
    action = runner.build_gate_action(_make_gate(), phase_id=1)
    assert action.action_type == ActionType.GATE


@pytest.mark.parametrize("phase_id,gate_type,command", [
    (3, "test", "pytest --tb=short -q"),
    (0, "lint", "flake8 ."),
    (1, "test", "pytest -q"),
])
def test_build_gate_action_fields_propagated(
    runner: GateRunner,
    phase_id: int,
    gate_type: str,
    command: str,
) -> None:
    gate = _make_gate(gate_type=gate_type, command=command)
    action = runner.build_gate_action(gate, phase_id=phase_id)
    assert action.phase_id == phase_id
    assert action.gate_type == gate_type
    assert action.gate_command == command


def test_build_gate_action_files_placeholder_substituted(runner: GateRunner) -> None:
    gate = _make_gate(command="python -m py_compile {files}")
    action = runner.build_gate_action(gate, phase_id=1, files_changed=["a.py", "b.py"])
    assert "{files}" not in action.gate_command
    assert "a.py" in action.gate_command
    assert "b.py" in action.gate_command


def test_build_gate_action_no_files_placeholder_kept(runner: GateRunner) -> None:
    gate = _make_gate(command="python -m py_compile {files}")
    action = runner.build_gate_action(gate, phase_id=1, files_changed=None)
    assert "{files}" in action.gate_command


def test_build_gate_action_message_is_non_empty(runner: GateRunner) -> None:
    action = runner.build_gate_action(_make_gate(), phase_id=2)
    assert isinstance(action.message, str)
    assert len(action.message) > 0


def test_build_gate_action_to_dict_includes_gate_fields(runner: GateRunner) -> None:
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


@pytest.mark.parametrize("gate_type,command,output,exit_code,expected_pass", [
    # test gate
    ("test", "pytest --tb=short -q", "5 passed", 0, True),
    ("test", "pytest --tb=short -q", "2 failed, 3 passed", 1, False),
    # build gate
    ("build", "make build", "", 0, True),
    ("build", "make build", "SyntaxError: invalid syntax", 1, False),
    # lint gate — clean
    ("lint", "flake8 .", "All files clean.", 0, True),
    # lint gate — warnings only still pass
    ("lint", "flake8 .", "foo.py:3: warning: unused import", 0, True),
    # lint gate — error in output fails even with exit 0
    ("lint", "flake8 .", "foo.py:10: error: unexpected indent", 0, False),
    # lint gate — non-zero exit fails
    ("lint", "flake8 .", "All clean.", 2, False),
    # spec gate behaviour moved to tests/engine/test_spec_gate_runner.py —
    # it is now a semantic check dispatched to a Claude Code subprocess,
    # not a structural exit_code check, so parametrized integration rows
    # here would either require a real CLI or a heavy fixture.
    # review gate — always passes regardless of exit code or output
    ("review", "", "Looks good overall.", 0, True),
    ("review", "", "FAIL: missing docstrings.", 1, True),
    ("review", "", "Multiple critical issues found.", 2, True),
])
def test_evaluate_output(
    runner: GateRunner,
    gate_type: str,
    command: str,
    output: str,
    exit_code: int,
    expected_pass: bool,
) -> None:
    gate = _make_gate(gate_type=gate_type, command=command)
    result = runner.evaluate_output(gate, output, exit_code=exit_code)
    assert result.passed is expected_pass


@pytest.mark.parametrize("gate_type,output,exit_code,check", [
    ("test", "3 passed", 0, lambda r: r.output == "3 passed"),
    ("build", "", 0, lambda r: r.gate_type == "build"),
    ("test", "", 0, lambda r: isinstance(r.checked_at, str) and "T" in r.checked_at),
    ("test", "", 0, lambda r: isinstance(r, GateResult)),
])
def test_gate_result_fields(
    runner: GateRunner,
    gate_type: str,
    output: str,
    exit_code: int,
    check,
) -> None:
    gate = _make_gate(gate_type=gate_type)
    result = runner.evaluate_output(gate, output, exit_code=exit_code)
    assert check(result)


def test_gate_result_to_dict_roundtrip(runner: GateRunner) -> None:
    gate = _make_gate(gate_type="test")
    result = runner.evaluate_output(gate, "5 passed", exit_code=0)
    d = result.to_dict()
    assert d["gate_type"] == "test"
    assert d["passed"] is True
    assert d["output"] == "5 passed"


# ---------------------------------------------------------------------------
# default_gates
# ---------------------------------------------------------------------------


def test_default_gates_returns_dict() -> None:
    assert isinstance(GateRunner.default_gates(), dict)


def test_default_gates_returns_fresh_dict_each_call() -> None:
    assert GateRunner.default_gates() is not GateRunner.default_gates()


@pytest.mark.parametrize("key", ["build", "test", "lint", "review"])
def test_default_gates_contains_key(key: str) -> None:
    assert key in GateRunner.default_gates()


def test_default_gates_all_values_are_plan_gate() -> None:
    for name, gate in GateRunner.default_gates().items():
        assert isinstance(gate, PlanGate), f"{name} is not a PlanGate"


@pytest.mark.parametrize("key,attr,check", [
    ("build", "command", lambda v: v != ""),
    ("test", "command", lambda v: "pytest" in v),
    ("lint", "gate_type", lambda v: v == "lint"),
    ("review", "command", lambda v: v == ""),
    ("review", "gate_type", lambda v: v == "review"),
])
def test_default_gate_attributes(key: str, attr: str, check) -> None:
    gate = GateRunner.default_gates()[key]
    assert check(getattr(gate, attr))


# ---------------------------------------------------------------------------
# PlanGate.from_dict — "type" fallback (LLM compatibility)
# ---------------------------------------------------------------------------


def test_plan_gate_from_dict_accepts_type_as_fallback() -> None:
    """PlanGate.from_dict should accept 'type' when 'gate_type' is absent."""
    data = {"type": "test", "command": "pytest"}
    gate = PlanGate.from_dict(data)
    assert gate.gate_type == "test"
    assert gate.command == "pytest"


def test_plan_gate_from_dict_prefers_gate_type_over_type() -> None:
    """When both 'gate_type' and 'type' are present, 'gate_type' wins."""
    data = {"gate_type": "build", "type": "test", "command": "make"}
    gate = PlanGate.from_dict(data)
    assert gate.gate_type == "build"


def test_plan_gate_from_dict_canonical_gate_type_still_works() -> None:
    """The canonical 'gate_type' key continues to work."""
    data = {"gate_type": "lint", "command": "ruff check ."}
    gate = PlanGate.from_dict(data)
    assert gate.gate_type == "lint"
