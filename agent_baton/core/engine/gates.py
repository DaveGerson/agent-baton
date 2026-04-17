"""Gate runner -- determines what QA gate checks to run and evaluates results.

Gates are quality checkpoints inserted between execution phases.  Each gate
type has specific pass/fail semantics:

- **build** / **test**: pass when exit code is 0.
- **lint**: pass when exit code is 0 AND no error markers are found in
  output (warnings are tolerated).
- **spec**: delegates to ``SpecValidator`` for structural validation.
- **review**: advisory only -- always passes regardless of output.
- **ci**: triggers a CI provider workflow and polls for completion.
  Currently supports GitHub Actions only.  Requires ``GITHUB_TOKEN`` env var
  with ``checks:read`` and ``actions:write`` scopes.  Falls back to
  ``DecisionManager`` escalation when the token is absent or the network
  is unreachable.

The ``GateRunner`` is stateless; each method operates on its arguments
without side effects.  Gate evaluation results are recorded by the
``ExecutionEngine`` which handles state transitions on pass/fail.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_baton.core.govern.spec_validator import SpecValidator
from agent_baton.models.execution import ActionType, ExecutionAction, GateResult, PlanGate

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Default polling timeout for CI gates (seconds).
_CI_DEFAULT_TIMEOUT_SECONDS = 900  # 15 minutes
# Polling interval when waiting for a CI run to finish (seconds).
_CI_POLL_INTERVAL_SECONDS = 30


# Patterns that indicate lint errors (as opposed to warnings).
# Line prefixes / keywords used by common Python linters.
_LINT_ERROR_MARKERS = (
    ": error:",
    ":E:",
    " E ",
    "[E",
    "Error:",
    "ERROR",
    "SyntaxError",
    "error:",
)


def _has_lint_errors(output: str) -> bool:
    """Return True if *output* contains lint error markers (not just warnings).

    Scans line-by-line for patterns emitted by common Python linters
    (ruff, flake8, pylint, mypy, pyflakes).  Warnings without error
    markers do not trigger a failure, allowing lint gates to be used
    in ``warn`` mode without blocking progress.
    """
    for line in output.splitlines():
        for marker in _LINT_ERROR_MARKERS:
            if marker in line:
                return True
    return False


class GateRunner:
    """Runs QA gate checks between execution phases.

    Each public method is stateless and operates purely on its arguments.
    The class keeps no mutable state between calls.
    """

    def __init__(self) -> None:
        self._spec_validator = SpecValidator()

    # ------------------------------------------------------------------
    # describe_gate
    # ------------------------------------------------------------------

    def describe_gate(self, gate: PlanGate) -> str:
        """Return a human-readable description of what this gate checks.

        Args:
            gate: The gate to describe.

        Returns:
            A short description string suitable for display in logs or plans.
        """
        if gate.description:
            return gate.description

        descriptions: dict[str, str] = {
            "build": "Verify the codebase compiles without errors.",
            "test": "Run the automated test suite and require all tests to pass.",
            "lint": "Check code style; errors block progress, warnings are advisory.",
            "spec": "Validate agent output against the declared specification.",
            "review": "Advisory code review by the reviewer agent (never blocks).",
            "approval": "Human approval checkpoint — execution pauses for review.",
            "ci": "Trigger CI pipeline and wait for completion (GitHub Actions).",
        }
        return descriptions.get(gate.gate_type, f"Run '{gate.gate_type}' gate check.")

    # ------------------------------------------------------------------
    # build_gate_action
    # ------------------------------------------------------------------

    def build_gate_action(
        self,
        gate: PlanGate,
        phase_id: int,
        *,
        files_changed: list[str] | None = None,
    ) -> ExecutionAction:
        """Build an ExecutionAction with GATE type.

        Tells the caller what command to run or what to check.

        Args:
            gate: The gate to run.
            phase_id: Index of the phase that just completed.
            files_changed: Optional list of files changed in this phase.
                           Used to populate {files} placeholders in commands.

        Returns:
            An ExecutionAction with action_type=GATE.
        """
        command = gate.command or ""

        if command and files_changed:
            files_str = " ".join(files_changed)
            command = command.replace("{files}", files_str)

        description = self.describe_gate(gate)
        message = f"Gate '{gate.gate_type}' for phase {phase_id}: {description}"

        return ExecutionAction(
            action_type=ActionType.GATE,
            message=message,
            gate_type=gate.gate_type,
            gate_command=command,
            phase_id=phase_id,
        )

    # ------------------------------------------------------------------
    # evaluate_output
    # ------------------------------------------------------------------

    def evaluate_output(
        self,
        gate: PlanGate,
        command_output: str,
        exit_code: int = 0,
    ) -> GateResult:
        """Evaluate the output of a gate command.

        Rules:
        - 'test' and 'build' gates: passed = (exit_code == 0)
        - 'lint' gates: passed = (exit_code == 0 AND no error markers in output)
        - 'spec' gates: delegate to SpecValidator.run_gate with a trivial check
          on the output text (passes when output is non-empty and exit_code == 0)
        - 'review' gates: always pass (review is advisory)

        Args:
            gate: The gate that was run.
            command_output: stdout/stderr captured from the gate command (or
                            the reviewer agent's output for review gates).
            exit_code: Process exit code; 0 means success for build/test/lint.

        Returns:
            A populated GateResult.
        """
        checked_at = datetime.now(timezone.utc).isoformat()
        gate_type = gate.gate_type

        logger.debug("Evaluating gate: type=%s exit_code=%d", gate_type, exit_code)

        if gate_type == "review":
            # Advisory — always pass regardless of exit code or output content.
            logger.debug("Gate '%s': advisory — always pass", gate_type)
            return GateResult(
                phase_id=0,
                gate_type=gate_type,
                passed=True,
                output=command_output,
                checked_at=checked_at,
            )

        if gate_type in ("test", "build"):
            passed = exit_code == 0
            logger.info(
                "Gate '%s': %s (exit_code=%d)",
                gate_type,
                "PASS" if passed else "FAIL",
                exit_code,
            )
            return GateResult(
                phase_id=0,
                gate_type=gate_type,
                passed=passed,
                output=command_output,
                checked_at=checked_at,
            )

        if gate_type == "lint":
            # Warnings are acceptable; only hard errors block progress.
            has_errors = _has_lint_errors(command_output)
            passed = exit_code == 0 and not has_errors
            logger.info(
                "Gate 'lint': %s (exit_code=%d, error_markers=%s)",
                "PASS" if passed else "FAIL",
                exit_code,
                has_errors,
            )
            return GateResult(
                phase_id=0,
                gate_type=gate_type,
                passed=passed,
                output=command_output,
                checked_at=checked_at,
            )

        if gate_type == "spec":
            # Use SpecValidator.run_gate with a single structural check:
            # success when exit_code == 0 and output is non-empty.
            def _check_spec() -> tuple[bool, str]:
                if exit_code != 0:
                    return False, f"Spec command exited with code {exit_code}."
                if not command_output.strip():
                    return False, "Spec command produced no output."
                return True, "Spec check passed."

            result = self._spec_validator.run_gate([("spec output", _check_spec)])
            passed = result.passed
            logger.info("Gate 'spec': %s", "PASS" if passed else "FAIL")
            return GateResult(
                phase_id=0,
                gate_type=gate_type,
                passed=passed,
                output=command_output,
                checked_at=checked_at,
            )

        if gate_type == "ci":
            # CI gate: command_output is expected to carry pre-parsed CI output
            # (e.g. from ci_gate.run_ci_gate).  If it contains a recognised
            # pass marker the gate passes; if a fail marker is present it fails.
            # When the output is raw (called directly via evaluate_output with
            # subprocess output), fall back to exit_code semantics so the gate
            # is still useful without the full CI integration.
            passed = _parse_ci_output(command_output, exit_code)
            logger.info(
                "Gate 'ci': %s (exit_code=%d)",
                "PASS" if passed else "FAIL",
                exit_code,
            )
            return GateResult(
                phase_id=0,
                gate_type=gate_type,
                passed=passed,
                output=command_output,
                checked_at=checked_at,
            )

        # Unknown gate type — fall back to exit_code check.
        passed = exit_code == 0
        logger.warning(
            "Unknown gate type '%s' — falling back to exit_code check: %s",
            gate_type,
            "PASS" if passed else "FAIL",
        )
        return GateResult(
            phase_id=0,
            gate_type=gate_type,
            passed=passed,
            output=command_output,
            checked_at=checked_at,
        )

    # ------------------------------------------------------------------
    # default_gates
    # ------------------------------------------------------------------

    @staticmethod
    def default_gates() -> dict[str, PlanGate]:
        """Return the built-in gate definitions.

        Returns a fresh dict on every call; callers may mutate the values
        without affecting future calls.

        Returns:
            Mapping of gate name to PlanGate:
            - 'build': compile-check all files
            - 'test': run pytest
            - 'lint': compile-check (lightweight lint proxy)
            - 'review': advisory code review (no command)
        """
        return {
            "build": PlanGate(
                gate_type="build",
                command="python -m py_compile {files}",
                description="Verify the codebase compiles without errors.",
                fail_on=["compilation error", "SyntaxError"],
            ),
            "test": PlanGate(
                gate_type="test",
                command="pytest --tb=short -q",
                description="Run the automated test suite and require all tests to pass.",
                fail_on=["test failure", "error"],
            ),
            "lint": PlanGate(
                gate_type="lint",
                command="python -m py_compile {files}",
                description="Check code style; errors block progress, warnings are advisory.",
                fail_on=["lint error"],
            ),
            "review": PlanGate(
                gate_type="review",
                command="",
                description="Code review by reviewer agent",
                fail_on=[],
            ),
        }
