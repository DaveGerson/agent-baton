"""Tests for agent_baton.core.engine.gate_addition."""
from __future__ import annotations

import pytest

from agent_baton.core.engine.gate_addition import (
    GateAddition,
    _MAX_ADDITIONS_PER_STEP,
    parse_gate_additions,
)


# ---------------------------------------------------------------------------
# parse_gate_additions — basic cases
# ---------------------------------------------------------------------------


def test_no_signal_returns_empty() -> None:
    assert parse_gate_additions("No special signals here.", step_id="1.1", agent_name="a") == []


def test_empty_string_returns_empty() -> None:
    assert parse_gate_additions("", step_id="1.1", agent_name="a") == []


def test_single_signal_parsed() -> None:
    outcome = "GATE_ADDITION: npm audit --audit-level=high\nSome other text."
    result = parse_gate_additions(outcome, step_id="1.1", agent_name="security-agent")
    assert len(result) == 1
    assert result[0].command == "npm audit --audit-level=high"
    assert result[0].agent_name == "security-agent"
    assert result[0].step_id == "1.1"


def test_multiple_signals_parsed() -> None:
    outcome = (
        "GATE_ADDITION: npm audit --audit-level=high\n"
        "GATE_ADDITION: pre-commit run --all-files\n"
    )
    result = parse_gate_additions(outcome, step_id="2.3", agent_name="dev")
    assert len(result) == 2
    commands = [r.command for r in result]
    assert "npm audit --audit-level=high" in commands
    assert "pre-commit run --all-files" in commands


def test_duplicate_commands_deduped() -> None:
    outcome = (
        "GATE_ADDITION: npm audit --audit-level=high\n"
        "GATE_ADDITION: npm audit --audit-level=high\n"
        "GATE_ADDITION: pre-commit run --all-files\n"
    )
    result = parse_gate_additions(outcome, step_id="1.1", agent_name="a")
    commands = [r.command for r in result]
    assert len(commands) == 2
    assert commands.count("npm audit --audit-level=high") == 1


def test_case_insensitive_prefix() -> None:
    """gate_addition: prefix matching is case-insensitive."""
    outcome = "gate_addition: my-check\nGATE_ADDITION: other-check\n"
    result = parse_gate_additions(outcome, step_id="1.1", agent_name="a")
    commands = [r.command for r in result]
    assert "my-check" in commands
    assert "other-check" in commands


def test_cap_at_max_additions() -> None:
    """Never return more than _MAX_ADDITIONS_PER_STEP items."""
    lines = "\n".join(
        f"GATE_ADDITION: cmd-{i}" for i in range(_MAX_ADDITIONS_PER_STEP + 5)
    )
    result = parse_gate_additions(lines, step_id="1.1", agent_name="a")
    assert len(result) == _MAX_ADDITIONS_PER_STEP


def test_empty_command_after_colon_ignored() -> None:
    """GATE_ADDITION: <whitespace-only> lines are silently dropped."""
    outcome = "GATE_ADDITION:    \nGATE_ADDITION: real-cmd\n"
    result = parse_gate_additions(outcome, step_id="1.1", agent_name="a")
    assert len(result) == 1
    assert result[0].command == "real-cmd"


def test_malformed_lines_without_command_ignored() -> None:
    """Lines that are just 'GATE_ADDITION:' with nothing after are dropped."""
    outcome = "GATE_ADDITION:\nGATE_ADDITION: valid-cmd"
    result = parse_gate_additions(outcome, step_id="1.1", agent_name="a")
    assert len(result) == 1
    assert result[0].command == "valid-cmd"


def test_result_is_frozen_dataclass() -> None:
    outcome = "GATE_ADDITION: pytest -x"
    result = parse_gate_additions(outcome, step_id="1.1", agent_name="a")
    assert len(result) == 1
    item = result[0]
    assert isinstance(item, GateAddition)
    with pytest.raises((AttributeError, TypeError)):
        item.command = "mutated"  # type: ignore[misc]


def test_command_whitespace_stripped() -> None:
    """Leading/trailing whitespace on the command value is stripped."""
    outcome = "GATE_ADDITION:   make test   \n"
    result = parse_gate_additions(outcome, step_id="1.1", agent_name="a")
    assert len(result) == 1
    assert result[0].command == "make test"


def test_preserves_insertion_order_before_dedup() -> None:
    """First-seen command wins; order matches appearance in outcome."""
    outcome = (
        "GATE_ADDITION: cmd-c\n"
        "GATE_ADDITION: cmd-a\n"
        "GATE_ADDITION: cmd-b\n"
        "GATE_ADDITION: cmd-a\n"  # duplicate of cmd-a
    )
    result = parse_gate_additions(outcome, step_id="1.1", agent_name="a")
    commands = [r.command for r in result]
    assert commands == ["cmd-c", "cmd-a", "cmd-b"]


def test_signal_embedded_in_verbose_outcome() -> None:
    """Signal anywhere in verbose agent text is detected."""
    outcome = (
        "I implemented the security scanning step.\n"
        "Here is what I did:\n"
        " 1. Added npm audit config\n"
        " 2. Added pre-commit hooks\n\n"
        "GATE_ADDITION: npm audit --audit-level=high\n"
        "GATE_ADDITION: pre-commit run --all-files\n\n"
        "The changes are in package.json and .pre-commit-config.yaml."
    )
    result = parse_gate_additions(outcome, step_id="3.2", agent_name="security-agent")
    commands = [r.command for r in result]
    assert "npm audit --audit-level=high" in commands
    assert "pre-commit run --all-files" in commands
