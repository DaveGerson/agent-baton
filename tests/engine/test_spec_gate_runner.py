"""Tests for SpecComplianceEvaluator and the spec gate branch in GateRunner.

Coverage:
- Heuristic path: high overlap → pass, low overlap → fail
- Heuristic path: empty output → fail, empty task_summary → fail (no crash)
- Stop-word filtering: stop-word-only output does not pass a real task
- Semantic path: invoked when ANTHROPIC_API_KEY is set (mocked client)
- Semantic path: falls back to heuristic when API client raises
- GateRunner spec branch: exit_code != 0 always fails; delegates to evaluator otherwise
- Existing gate callers (evaluate_output) still work for non-spec gate types
"""
from __future__ import annotations

import json
import os

import pytest

from agent_baton.core.engine.gates import (
    GateRunner,
    SpecComplianceEvaluator,
    _content_tokens,
)
from agent_baton.models.execution import GateResult, PlanGate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec_gate(command: str = "check-spec") -> PlanGate:
    return PlanGate(gate_type="spec", command=command, description="", fail_on=[])


def _make_evaluator(
    task_summary: str = "",
    phase_name: str = "Implement",
    step_descriptions: list[str] | None = None,
    haiku_caller=None,
) -> SpecComplianceEvaluator:
    return SpecComplianceEvaluator(
        task_summary=task_summary,
        phase_name=phase_name,
        step_descriptions=step_descriptions,
        haiku_caller=haiku_caller,
    )


# ---------------------------------------------------------------------------
# _content_tokens — unit tests for the tokeniser
# ---------------------------------------------------------------------------


class TestContentTokens:
    def test_removes_stop_words(self) -> None:
        tokens = _content_tokens("add a button to the form")
        assert "the" not in tokens
        assert "add" not in tokens  # len < 4
        assert "button" in tokens
        assert "form" in tokens

    def test_minimum_length_four(self) -> None:
        tokens = _content_tokens("fix bug add unit test")
        # "fix", "bug", "add" are all len < 4; "unit" and "test" are exactly 4
        assert "fix" not in tokens
        assert "bug" not in tokens
        assert "unit" in tokens
        assert "test" in tokens

    def test_strips_punctuation(self) -> None:
        tokens = _content_tokens("implement the button, update the form!")
        assert "button" in tokens
        assert "form" in tokens
        assert "button," not in tokens

    def test_lowercases(self) -> None:
        tokens = _content_tokens("Implement Button Form")
        assert "implement" in tokens
        assert "button" in tokens


# ---------------------------------------------------------------------------
# Heuristic path — SpecComplianceEvaluator._heuristic_evaluate
# ---------------------------------------------------------------------------


class TestHeuristicPath:
    def test_high_overlap_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Output containing most task-summary terms passes."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        evaluator = _make_evaluator(
            task_summary="implement login endpoint with authentication token validation"
        )
        output = (
            "Implemented the login endpoint. Added authentication token validation "
            "logic in the handler. The endpoint now returns a JWT."
        )
        passed, rationale = evaluator.evaluate(output)
        assert passed is True
        assert "[heuristic-fallback]" in rationale

    def test_low_overlap_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Output with little relation to the task summary fails."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        evaluator = _make_evaluator(
            task_summary="implement login endpoint with authentication token validation"
        )
        output = "Updated the README with installation instructions."
        passed, rationale = evaluator.evaluate(output)
        assert passed is False
        assert "[heuristic-fallback]" in rationale

    def test_empty_output_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty agent output always fails."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        evaluator = _make_evaluator(
            task_summary="implement login endpoint with authentication"
        )
        passed, rationale = evaluator.evaluate("")
        assert passed is False
        assert "empty" in rationale.lower()

    def test_whitespace_only_output_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Whitespace-only output is treated as empty."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        evaluator = _make_evaluator(task_summary="implement login endpoint")
        passed, _ = evaluator.evaluate("   \n\t  ")
        assert passed is False

    def test_empty_task_summary_fails_without_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No task_summary → deterministic fail, no exception raised."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        evaluator = _make_evaluator(task_summary="")
        passed, rationale = evaluator.evaluate("Some agent output here.")
        assert passed is False
        assert rationale  # must produce a non-empty reason

    def test_stop_word_only_output_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Output consisting only of stop words does not pass a real task summary."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        evaluator = _make_evaluator(
            task_summary="Add a button to the form to submit user data"
        )
        # "the the the the" → only stop words, no content tokens overlap
        output = "the the the the the the the the the the"
        passed, _ = evaluator.evaluate(output)
        # task_summary itself has content tokens: "button", "form", "submit",
        # "user", "data" — none appear in stop-word-only output.
        assert passed is False

    def test_rationale_contains_overlap_numbers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rationale should include overlap counts for debuggability."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        evaluator = _make_evaluator(
            task_summary="implement payment processing with stripe integration"
        )
        output = (
            "Implemented payment processing using the Stripe integration library."
        )
        passed, rationale = evaluator.evaluate(output)
        # Rationale must mention the threshold percentage
        assert "%" in rationale


# ---------------------------------------------------------------------------
# Semantic path — SpecComplianceEvaluator._semantic_evaluate
# ---------------------------------------------------------------------------


class TestSemanticPath:
    def _compliant_response(self) -> str:
        return json.dumps(
            {"compliant": True, "rationale": "Output matches intent.", "deviations": []}
        )

    def _noncompliant_response(self) -> str:
        return json.dumps(
            {
                "compliant": False,
                "rationale": "Missing authentication step.",
                "deviations": ["authentication not implemented"],
            }
        )

    def test_semantic_path_invoked_when_api_key_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When ANTHROPIC_API_KEY is set, the mock caller is used."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-abc")
        calls: list[str] = []

        def mock_caller(prompt: str) -> str:
            calls.append(prompt)
            return self._compliant_response()

        evaluator = _make_evaluator(
            task_summary="implement login endpoint",
            haiku_caller=mock_caller,
        )
        passed, rationale = evaluator.evaluate("Implemented login endpoint with JWT.")
        assert passed is True
        assert len(calls) == 1
        assert "login endpoint" in calls[0]
        assert rationale == "Output matches intent."

    def test_semantic_path_returns_false_on_noncompliant(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Haiku returning compliant=false → gate fails."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-abc")

        evaluator = _make_evaluator(
            task_summary="implement login endpoint with authentication",
            haiku_caller=lambda _: self._noncompliant_response(),
        )
        passed, rationale = evaluator.evaluate("Only added a placeholder function.")
        assert passed is False
        assert "Missing authentication step." in rationale
        assert "authentication not implemented" in rationale

    def test_semantic_path_includes_deviations_in_rationale(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Deviations are appended to the rationale string."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-abc")
        response = json.dumps(
            {
                "compliant": False,
                "rationale": "Output incomplete.",
                "deviations": ["missing error handling", "no tests added"],
            }
        )
        evaluator = _make_evaluator(
            task_summary="implement endpoint with error handling and tests",
            haiku_caller=lambda _: response,
        )
        passed, rationale = evaluator.evaluate("Added stub.")
        assert "missing error handling" in rationale
        assert "no tests added" in rationale

    def test_semantic_path_falls_back_to_heuristic_on_api_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the API call raises, heuristic path is used and tagged."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-abc")

        def failing_caller(prompt: str) -> str:
            raise ConnectionError("API unreachable")

        evaluator = _make_evaluator(
            task_summary="implement login endpoint with authentication token validation",
            haiku_caller=failing_caller,
        )
        output = (
            "Implemented the login endpoint. Added authentication token validation "
            "logic in the handler."
        )
        passed, rationale = evaluator.evaluate(output)
        # Falls back to heuristic — result depends on overlap but must be tagged
        assert "[heuristic-fallback]" in rationale

    def test_semantic_path_handles_markdown_fenced_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Response wrapped in ```json fences is parsed correctly."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-abc")
        fenced = (
            "```json\n"
            + json.dumps({"compliant": True, "rationale": "Looks good.", "deviations": []})
            + "\n```"
        )
        evaluator = _make_evaluator(
            task_summary="implement login endpoint",
            haiku_caller=lambda _: fenced,
        )
        passed, rationale = evaluator.evaluate("Implemented login endpoint.")
        assert passed is True

    def test_semantic_path_not_invoked_without_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without ANTHROPIC_API_KEY the mock caller is never invoked."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        calls: list[str] = []

        def mock_caller(prompt: str) -> str:
            calls.append(prompt)
            return self._compliant_response()

        evaluator = _make_evaluator(
            task_summary="implement login endpoint",
            haiku_caller=mock_caller,
        )
        evaluator.evaluate("Some output.")
        assert len(calls) == 0


# ---------------------------------------------------------------------------
# GateRunner spec branch integration
# ---------------------------------------------------------------------------


class TestGateRunnerSpecBranch:
    def test_non_zero_exit_code_always_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """exit_code != 0 short-circuits before the evaluator is consulted."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        # Evaluator configured to pass if it were reached.
        evaluator = _make_evaluator(
            task_summary="implement login endpoint with authentication token",
            haiku_caller=lambda _: json.dumps(
                {"compliant": True, "rationale": "Fine.", "deviations": []}
            ),
        )
        runner = GateRunner(spec_evaluator=evaluator)
        gate = _spec_gate()
        result = runner.evaluate_output(gate, "Some valid output.", exit_code=1)
        assert result.passed is False
        assert result.gate_type == "spec"

    def test_zero_exit_code_delegates_to_evaluator(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """exit_code == 0 calls the evaluator and returns its verdict."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        evaluator = _make_evaluator(
            task_summary="implement login endpoint with authentication token validation"
        )
        runner = GateRunner(spec_evaluator=evaluator)
        gate = _spec_gate()
        output = (
            "Implemented the login endpoint. Authentication token validation "
            "is now enforced on every request."
        )
        result = runner.evaluate_output(gate, output, exit_code=0)
        assert isinstance(result, GateResult)
        assert result.gate_type == "spec"
        # High overlap → should pass
        assert result.passed is True

    def test_result_output_contains_rationale(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GateResult.output carries the evaluator rationale, not raw output."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        evaluator = _make_evaluator(
            task_summary="implement payment processing with stripe integration"
        )
        runner = GateRunner(spec_evaluator=evaluator)
        gate = _spec_gate()
        result = runner.evaluate_output(
            gate, "Updated README with installation instructions.", exit_code=0
        )
        assert "[heuristic-fallback]" in result.output

    def test_default_runner_no_task_summary_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default GateRunner() with no spec_evaluator configured fails non-empty output."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        runner = GateRunner()
        gate = _spec_gate()
        result = runner.evaluate_output(gate, "Some non-empty output.", exit_code=0)
        assert result.passed is False

    def test_semantic_evaluator_via_gate_runner(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GateRunner passes the mock Haiku caller through to the evaluator."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        calls: list[str] = []

        def mock_caller(prompt: str) -> str:
            calls.append(prompt)
            return json.dumps(
                {"compliant": True, "rationale": "Matches intent.", "deviations": []}
            )

        evaluator = SpecComplianceEvaluator(
            task_summary="implement login endpoint",
            phase_name="Implementation",
            haiku_caller=mock_caller,
        )
        runner = GateRunner(spec_evaluator=evaluator)
        result = runner.evaluate_output(
            _spec_gate(), "Implemented login endpoint with JWT.", exit_code=0
        )
        assert result.passed is True
        assert len(calls) == 1

    def test_api_failure_fallback_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """API failure → heuristic fallback, never raises from GateRunner."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

        def failing_caller(prompt: str) -> str:
            raise RuntimeError("network error")

        evaluator = SpecComplianceEvaluator(
            task_summary="implement authentication token validation endpoint",
            haiku_caller=failing_caller,
        )
        runner = GateRunner(spec_evaluator=evaluator)
        output = (
            "Implemented authentication token validation endpoint. "
            "Tokens are now validated on every request."
        )
        result = runner.evaluate_output(_spec_gate(), output, exit_code=0)
        assert isinstance(result, GateResult)
        assert "[heuristic-fallback]" in result.output


# ---------------------------------------------------------------------------
# Existing non-spec gate types still work (regression guard)
# ---------------------------------------------------------------------------


class TestNonSpecGatesUnchanged:
    @pytest.fixture
    def runner(self) -> GateRunner:
        return GateRunner()

    def test_test_gate_pass(self, runner: GateRunner) -> None:
        gate = PlanGate(gate_type="test", command="pytest", description="", fail_on=[])
        result = runner.evaluate_output(gate, "5 passed", exit_code=0)
        assert result.passed is True

    def test_test_gate_fail(self, runner: GateRunner) -> None:
        gate = PlanGate(gate_type="test", command="pytest", description="", fail_on=[])
        result = runner.evaluate_output(gate, "2 failed", exit_code=1)
        assert result.passed is False

    def test_build_gate_pass(self, runner: GateRunner) -> None:
        gate = PlanGate(gate_type="build", command="make", description="", fail_on=[])
        result = runner.evaluate_output(gate, "", exit_code=0)
        assert result.passed is True

    def test_lint_gate_warnings_pass(self, runner: GateRunner) -> None:
        gate = PlanGate(gate_type="lint", command="flake8", description="", fail_on=[])
        result = runner.evaluate_output(gate, "foo.py:3: warning: unused import", exit_code=0)
        assert result.passed is True

    def test_lint_gate_errors_fail(self, runner: GateRunner) -> None:
        gate = PlanGate(gate_type="lint", command="flake8", description="", fail_on=[])
        result = runner.evaluate_output(gate, "foo.py:10: error: bad syntax", exit_code=0)
        assert result.passed is False

    def test_review_gate_always_passes(self, runner: GateRunner) -> None:
        gate = PlanGate(gate_type="review", command="", description="", fail_on=[])
        result = runner.evaluate_output(gate, "FAIL: issues found", exit_code=2)
        assert result.passed is True
