"""Regression tests: the automation DISPATCH variant must not be a protocol dead-end.

Defect (MINOR): ``_print_action()`` printed ``Type: automation`` with no
``Agent:`` line *and no record instruction*, so an orchestrator driving the
loop by hand had a dispatched step it was never told how to close.  The fix is
purely **additive** — the pre-existing lines and their order are part of the
protocol surface and must not move.
"""
from __future__ import annotations

import pytest

from agent_baton.cli.commands.execution.execute import _print_action
from agent_baton.models.execution import ActionType, ExecutionAction


def _automation_action(step_id: str = "3.2") -> dict:
    return ExecutionAction(
        action_type=ActionType.DISPATCH,
        step_id=step_id,
        step_type="automation",
        command="./scripts/verify.sh",
        message="Run the external verifier.",
    ).to_dict()


def _agent_action() -> dict:
    return ExecutionAction(
        action_type=ActionType.DISPATCH,
        step_id="1.1",
        agent_name="backend-engineer--python",
        agent_model="fable",
        delegation_prompt="do the thing",
        message="Dispatch agent for step 1.1.",
    ).to_dict()


def test_automation_dispatch_emits_record_hint(capsys: pytest.CaptureFixture) -> None:
    _print_action(_automation_action())
    out = capsys.readouterr().out

    assert "When complete, record the result:" in out
    assert (
        'baton execute record --step 3.2 --agent automation --status complete '
        '--outcome "summary"' in out
    )
    assert (
        'baton execute record --step 3.2 --agent automation --status failed '
        '--error "what went wrong"' in out
    )


def test_record_hint_matches_the_agent_dispatch_form(
    capsys: pytest.CaptureFixture,
) -> None:
    """Same wording/flags as the agent variant — one shape for the parser."""
    _print_action(_agent_action())
    agent_out = capsys.readouterr().out
    _print_action(_automation_action())
    automation_out = capsys.readouterr().out

    for out, agent in ((agent_out, "backend-engineer--python"), (automation_out, "automation")):
        assert "When complete, record the result:" in out
        assert f"--agent {agent} --status complete" in out
        assert f"--agent {agent} --status failed" in out


def test_existing_automation_output_shape_is_unchanged(
    capsys: pytest.CaptureFixture,
) -> None:
    """The additive hint must not reshape the lines the protocol already had."""
    _print_action(_automation_action())
    lines = capsys.readouterr().out.splitlines()

    assert lines[0] == "ACTION: DISPATCH"
    assert lines[1] == "  Step:    3.2"
    assert lines[2] == "  Type:    automation"
    assert lines[3] == "  Command: ./scripts/verify.sh"
    assert lines[4] == "  Message: Run the external verifier."
    assert lines[5] == ""
    assert lines[6] == "--- Command ---"
    assert lines[7] == "./scripts/verify.sh"
    assert lines[8] == "--- End Command ---"
    # Everything after the command block is the new, additive tail.
    assert "When complete, record the result:" in lines[9:]
    # Still no Agent/Model lines on an automation dispatch.
    assert not any(line.startswith("  Agent:") for line in lines)
    assert not any(line.startswith("  Model:") for line in lines)


def test_reference_documents_the_automation_variant() -> None:
    """references/baton-engine.md is the agent-side contract for this shape."""
    from pathlib import Path

    import agent_baton

    repo_root = Path(agent_baton.__file__).resolve().parent.parent
    ref = repo_root / "references" / "baton-engine.md"
    if not ref.is_file():  # installed wheel without the repo layout
        pytest.skip("references/baton-engine.md not present in this layout")
    text = ref.read_text(encoding="utf-8")

    assert "DISPATCH — automation variant" in text
    assert "--agent automation" in text
    # fable is a real tier the planner emits; the tier list must name it.
    assert "`fable`, `opus`, `sonnet`, `haiku`" in text
