"""Tests for ``SpecComplianceEvaluator`` and the spec branch of ``GateRunner``.

The evaluator dispatches semantic spec checks to a Claude Code subprocess
(``claude --print`` via ``HeadlessClaude``).  Tests inject a fake
``claude_caller`` so they never touch the real CLI or any network.

Coverage:
- Caller returns a compliant verdict       → gate passes
- Caller returns a non-compliant verdict   → gate fails
- Caller returns deviations                → rationale includes them
- Caller wraps JSON in markdown fences     → still parsed
- Caller returns empty (CLI unavailable)   → fail-closed with actionable msg
- Caller raises                            → fail-closed with the exception
- Caller returns unparseable text          → fail-closed with the snippet
- No task_summary attached                 → skip with explicit "wire it" msg
- GateRunner spec branch: exit_code != 0   → always fails, no caller invoked
- GateRunner spec branch: clean exit       → delegates to evaluator
- Default caller path: no API key required (smoke check that
  ``_default_claude_caller`` resolves and gracefully reports unavailability
  when the ``claude`` binary is absent — never raises).
"""
from __future__ import annotations

import pytest

from agent_baton.core.engine.gates import (
    GateRunner,
    SpecComplianceEvaluator,
    _default_claude_caller,
)
from agent_baton.models.execution import PlanGate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec_gate(command: str = "echo ok") -> PlanGate:
    return PlanGate(gate_type="spec", command=command, description="", fail_on=[])


def _make_evaluator(
    *,
    task_summary: str = "Add a login button to the homepage",
    phase_name: str = "Implement",
    step_descriptions: list[str] | None = None,
    claude_caller=None,
) -> SpecComplianceEvaluator:
    return SpecComplianceEvaluator(
        task_summary=task_summary,
        phase_name=phase_name,
        step_descriptions=step_descriptions or ["Add LoginButton component"],
        claude_caller=claude_caller,
    )


# ---------------------------------------------------------------------------
# Evaluator: happy / sad parses
# ---------------------------------------------------------------------------


class TestSpecComplianceEvaluator:
    def test_compliant_verdict_passes(self) -> None:
        ev = _make_evaluator(
            claude_caller=lambda _: '{"compliant": true, "rationale": "Button added.", "deviations": []}'
        )
        passed, rationale = ev.evaluate("LoginButton.tsx +12 lines")
        assert passed is True
        assert "Button added" in rationale

    def test_noncompliant_verdict_fails(self) -> None:
        ev = _make_evaluator(
            claude_caller=lambda _: '{"compliant": false, "rationale": "No button found.", "deviations": ["LoginButton not exported"]}'
        )
        passed, rationale = ev.evaluate("Header.tsx unrelated edits")
        assert passed is False
        assert "No button found" in rationale
        assert "LoginButton not exported" in rationale

    def test_markdown_fenced_json_is_parsed(self) -> None:
        ev = _make_evaluator(
            claude_caller=lambda _: '```json\n{"compliant": true, "rationale": "ok", "deviations": []}\n```'
        )
        passed, rationale = ev.evaluate("any output")
        assert passed is True
        assert rationale == "ok"

    def test_extracts_json_object_from_chatty_response(self) -> None:
        # Some models prefix/suffix a verdict with conversational filler;
        # the evaluator should still find the JSON object.
        ev = _make_evaluator(
            claude_caller=lambda _: 'Sure, here is the verdict: {"compliant": false, "rationale": "scope drift"} — hope this helps.'
        )
        passed, rationale = ev.evaluate("any output")
        assert passed is False
        assert "scope drift" in rationale

    def test_empty_caller_response_fails_closed(self) -> None:
        # Empty string signals "claude CLI unavailable" — must NOT default-pass.
        ev = _make_evaluator(claude_caller=lambda _: "")
        passed, rationale = ev.evaluate("any output")
        assert passed is False
        assert "claude CLI unavailable" in rationale or "claude" in rationale.lower()

    def test_caller_exception_fails_closed_with_reason(self) -> None:
        def raising(_: str) -> str:
            raise RuntimeError("subprocess timeout")

        ev = _make_evaluator(claude_caller=raising)
        passed, rationale = ev.evaluate("any output")
        assert passed is False
        assert "subprocess timeout" in rationale

    def test_unparseable_response_fails_closed(self) -> None:
        ev = _make_evaluator(claude_caller=lambda _: "lol no JSON here")
        passed, rationale = ev.evaluate("any output")
        assert passed is False
        assert "unparseable" in rationale.lower()

    def test_missing_task_summary_skips_with_actionable_msg(self) -> None:
        # No plan context attached: don't pretend to evaluate.
        ev = SpecComplianceEvaluator(claude_caller=lambda _: '{"compliant": true}')
        passed, rationale = ev.evaluate("any output")
        assert passed is False
        assert "task_summary" in rationale.lower()

    def test_default_caller_resolves_without_api_key(self) -> None:
        # Smoke: the default caller must not require ANTHROPIC_API_KEY and
        # must not raise when ``claude`` is absent — it should return ""
        # so the evaluator fails closed with an actionable message.
        out = _default_claude_caller("noop prompt")
        assert isinstance(out, str)


# ---------------------------------------------------------------------------
# GateRunner: spec branch routing
# ---------------------------------------------------------------------------


class TestGateRunnerSpecBranch:
    def test_nonzero_exit_code_fails_without_invoking_caller(self) -> None:
        # Caller raises if invoked — proves we short-circuit on exit_code.
        def must_not_be_called(_: str) -> str:
            raise AssertionError("evaluator should not be called when exit_code != 0")

        ev = SpecComplianceEvaluator(
            task_summary="Add a button",
            claude_caller=must_not_be_called,
        )
        runner = GateRunner(spec_evaluator=ev)
        result = runner.evaluate_output(
            _spec_gate(),
            command_output="...failure trace...",
            exit_code=2,
        )
        assert result.passed is False
        assert "exited with code 2" in result.output

    def test_clean_exit_delegates_to_evaluator_pass(self) -> None:
        ev = _make_evaluator(
            claude_caller=lambda _: '{"compliant": true, "rationale": "ok"}'
        )
        runner = GateRunner(spec_evaluator=ev)
        result = runner.evaluate_output(
            _spec_gate(),
            command_output="LoginButton.tsx +12",
            exit_code=0,
        )
        assert result.passed is True
        assert "ok" in result.output

    def test_clean_exit_delegates_to_evaluator_fail(self) -> None:
        ev = _make_evaluator(
            claude_caller=lambda _: '{"compliant": false, "rationale": "scope drift"}'
        )
        runner = GateRunner(spec_evaluator=ev)
        result = runner.evaluate_output(
            _spec_gate(),
            command_output="Header.tsx unrelated edits",
            exit_code=0,
        )
        assert result.passed is False
        assert "scope drift" in result.output

    def test_default_runner_has_default_evaluator(self) -> None:
        # GateRunner() must construct a default evaluator without errors.
        runner = GateRunner()
        # Default evaluator has no task_summary → skip path returns False
        # with an actionable message.
        result = runner.evaluate_output(_spec_gate(), command_output="x", exit_code=0)
        assert result.passed is False
        assert "task_summary" in result.output.lower()


# ---------------------------------------------------------------------------
# Other gate types: unaffected by the spec changes
# ---------------------------------------------------------------------------


class TestOtherGatesUnchanged:
    @pytest.mark.parametrize(
        "gate_type,exit_code,output,expected",
        [
            ("build", 0, "ok", True),
            ("build", 1, "err", False),
            ("test", 0, "1 passed", True),
            ("test", 1, "1 failed", False),
            ("lint", 0, "no issues", True),
            ("review", 0, "anything", True),  # advisory; always passes
        ],
    )
    def test_non_spec_gates(self, gate_type, exit_code, output, expected) -> None:
        runner = GateRunner()
        gate = PlanGate(gate_type=gate_type, command="x", description="", fail_on=[])
        result = runner.evaluate_output(gate, command_output=output, exit_code=exit_code)
        assert result.passed is expected
