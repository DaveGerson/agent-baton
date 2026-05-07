"""Gate runner -- determines what QA gate checks to run and evaluates results.

Gates are quality checkpoints inserted between execution phases.  Each gate
type has specific pass/fail semantics:

- **build** / **test**: pass when exit code is 0.
- **lint**: pass when exit code is 0 AND no error markers are found in
  output (warnings are tolerated).
- **spec**: semantic compliance check.  Dispatches the evaluation to a
  Claude Code subprocess via ``HeadlessClaude`` (``claude --print``) — no
  Anthropic API calls and no heuristic shortcut.  When the ``claude`` CLI
  is unavailable the gate fails closed with an actionable rationale rather
  than fabricating a verdict.
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

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from agent_baton.core.engine.artifact_validator import (
    ArtifactValidator,
    DerivedCommand,
)
from agent_baton.core.govern.spec_validator import SpecValidator
from agent_baton.models.execution import ActionType, ExecutionAction, GateResult, PlanGate

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


# ---------------------------------------------------------------------------
# Spec compliance evaluator (Claude Code subprocess, no API)
# ---------------------------------------------------------------------------

# Type for the injectable Claude caller.  Returns the raw text response.
ClaudeCaller = Callable[[str], str]


_SPEC_COMPLIANCE_PROMPT = """\
You are reviewing whether an agent's output fulfils the declared task intent
for one phase of a multi-phase orchestrated plan.

Task summary (the plan's top-level intent):
{task_summary}

Phase: {phase_name}
Phase steps:
{step_descriptions}

Agent output to evaluate (truncated to 4000 chars if longer):
{agent_output}

Evaluate strictly on functional fulfilment.  Be lenient on style.  Be strict
on missing functionality, wrong scope, or contradictions with the intent.

Return ONLY a single JSON object on one line, no markdown fences, no commentary:
{{"compliant": true|false, "rationale": "one sentence", "deviations": ["...", ...]}}
"""


def _default_claude_caller(prompt: str) -> str:
    """Default caller: dispatch via ``HeadlessClaude`` (``claude --print``).

    Uses the user's authenticated Claude Code session — no API key required.
    Returns "" when the ``claude`` CLI is not installed; the evaluator
    interprets empty output as "tool unavailable" and fails the gate
    closed with an actionable message.
    """
    from agent_baton.core.runtime.headless import HeadlessClaude  # noqa: PLC0415
    hc = HeadlessClaude()
    if not hc.is_available:
        return ""
    result = asyncio.run(hc.run(prompt))
    if not result.success:
        logger.warning("SpecComplianceEvaluator: claude --print failed: %s", result.error)
        return ""
    return result.output or ""


@dataclass
class SpecComplianceEvaluator:
    """Evaluate whether agent output fulfils the plan's declared intent.

    Dispatches the evaluation to a Claude Code subprocess via
    ``HeadlessClaude``.  All agentic evaluation routes through the user's
    Claude Code session — there are no direct Anthropic API calls and no
    heuristic shortcut.

    The ``claude_caller`` seam exists so tests can inject deterministic
    responses; in production the default uses ``claude --print``.

    Args:
        task_summary: The plan's top-level task description.
        phase_name: Name of the phase whose gate is being evaluated.
        step_descriptions: Step task_description strings for context.
        claude_caller: Optional override for the subprocess call (test seam).

    Behaviour:
        ``evaluate(output)`` returns ``(passed, rationale)``.  When the
        caller returns an empty string (CLI unavailable or call failed)
        the gate fails closed with a message that surfaces the cause.
    """

    task_summary: str = ""
    phase_name: str = ""
    step_descriptions: list[str] | None = None
    claude_caller: ClaudeCaller | None = None

    def evaluate(self, command_output: str) -> tuple[bool, str]:
        if not self.task_summary.strip():
            # No plan context attached — gate cannot make a real judgement.
            return False, (
                "Spec gate skipped: no task_summary attached to evaluator. "
                "Wire the plan's task_summary through GateRunner."
            )

        steps = self.step_descriptions or []
        step_text = "\n".join(f"- {d}" for d in steps if d) or "(no step descriptions)"
        prompt = _SPEC_COMPLIANCE_PROMPT.format(
            task_summary=self.task_summary,
            phase_name=self.phase_name or "(unknown)",
            step_descriptions=step_text,
            agent_output=(command_output or "")[:4000],
        )

        caller = self.claude_caller or _default_claude_caller
        try:
            raw = caller(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SpecComplianceEvaluator: claude_caller raised: %s", exc)
            return False, f"Spec gate error: claude_caller raised ({exc!s})."

        if not raw or not raw.strip():
            return False, (
                "Spec gate cannot evaluate: claude CLI unavailable or returned "
                "empty output.  Install the Claude Code CLI and re-run."
            )

        return self._parse_verdict(raw)

    @staticmethod
    def _parse_verdict(raw: str) -> tuple[bool, str]:
        # Tolerate models that wrap JSON in markdown fences.
        text = raw.strip()
        if text.startswith("```"):
            text = "\n".join(
                ln for ln in text.splitlines() if not ln.strip().startswith("```")
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    return False, f"Spec gate: unparseable verdict ({text[:160]!r})."
            else:
                return False, f"Spec gate: unparseable verdict ({text[:160]!r})."

        compliant = bool(data.get("compliant", False))
        rationale = str(data.get("rationale", "")).strip() or (
            "PASS" if compliant else "FAIL"
        )
        deviations = data.get("deviations") or []
        if deviations:
            rationale = f"{rationale} Deviations: " + "; ".join(
                str(d) for d in deviations
            )
        return compliant, rationale


class GateRunner:
    """Runs QA gate checks between execution phases.

    Each public method is stateless and operates purely on its arguments.
    The class keeps no mutable state between calls.
    """

    def __init__(
        self,
        spec_evaluator: SpecComplianceEvaluator | None = None,
    ) -> None:
        self._spec_validator = SpecValidator()
        self._spec_evaluator = spec_evaluator or SpecComplianceEvaluator()

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
        project_root: Path | str | None = None,
        artifact_validator: ArtifactValidator | None = None,
    ) -> ExecutionAction:
        """Build an ExecutionAction with GATE type.

        Tells the caller what command to run or what to check.

        When ``files_changed`` includes new runnable artifacts (CI
        workflows, npm scripts, Playwright configs), the
        :class:`ArtifactValidator` derives extra shell commands and
        appends them to ``gate.command`` chained with ``&&``.  This
        closes the "trusted-but-unverified" gap where a phase could
        pass even though the artifacts the agent just created were
        broken on arrival.

        Args:
            gate: The gate to run.
            phase_id: Index of the phase that just completed.
            files_changed: Optional list of files changed in this phase.
                           Used to populate {files} placeholders in
                           commands and to drive artifact validation.
            project_root: Filesystem root for resolving ``files_changed``
                           when the validator needs to read file contents
                           (workflow YAML, package.json scripts).  When
                           ``None``, only path-based derivations fire.
            artifact_validator: Inject a pre-built validator (test
                           seam).  When ``None`` and ``files_changed``
                           is non-empty, one is constructed from
                           ``project_root``.

        Returns:
            An ExecutionAction with action_type=GATE.  ``gate_command``
            carries the planned command followed by any derived
            commands.  ``message`` enumerates derived commands so the
            orchestrator and audit trail surface what was added.
        """
        command = gate.command or ""

        if command and files_changed:
            files_str = " ".join(files_changed)
            command = command.replace("{files}", files_str)

        derived: list[DerivedCommand] = []
        if files_changed:
            validator = artifact_validator or ArtifactValidator(project_root)
            derived = validator.derive_commands(files_changed)

        if derived:
            extension = " && ".join(d.command for d in derived)
            command = f"{command} && {extension}" if command else extension

        description = self.describe_gate(gate)
        message = f"Gate '{gate.gate_type}' for phase {phase_id}: {description}"
        if derived:
            extras = "; ".join(f"{d.command} ({d.rationale})" for d in derived)
            message = f"{message} [+artifact checks: {extras}]"

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
            # Spec gate: dispatch semantic compliance evaluation to a Claude
            # Code subprocess.  exit_code != 0 still hard-fails (the agent's
            # gate command itself broke); on a clean exit we ask the
            # evaluator whether the output fulfils the declared intent.
            if exit_code != 0:
                return GateResult(
                    phase_id=0,
                    gate_type=gate_type,
                    passed=False,
                    output=f"Spec command exited with code {exit_code}.\n{command_output}",
                    checked_at=checked_at,
                )
            passed, rationale = self._spec_evaluator.evaluate(command_output)
            logger.info("Gate 'spec': %s — %s", "PASS" if passed else "FAIL", rationale)
            return GateResult(
                phase_id=0,
                gate_type=gate_type,
                passed=passed,
                output=f"{rationale}\n---\n{command_output}",
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
            - 'ci': GitHub Actions workflow dispatch + poll
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
            "ci": PlanGate(
                gate_type="ci",
                command="",
                description="Trigger CI pipeline and wait for completion (GitHub Actions).",
                fail_on=["failure", "cancelled"],
            ),
        }


# ---------------------------------------------------------------------------
# Dry-run gate runner
# ---------------------------------------------------------------------------


class DryRunGateRunner:
    """Gate runner used by the dry-run testing harness.

    Returns a passing :class:`GateResult` for every gate without invoking the
    underlying command, and records what *would* have run for the dry-run
    report.  The runner is intentionally minimal: it exposes the same
    ``evaluate_output`` entry-point as :class:`GateRunner` so callers in
    the dry-run loop can swap in this implementation transparently.

    Attributes:
        gates_run: List of dicts ``{gate_type, command, phase_id}``
            recording every gate the harness encountered, in order.
    """

    def __init__(self) -> None:
        self.gates_run: list[dict] = []

    def evaluate_output(
        self,
        gate: PlanGate,
        command_output: str = "",
        exit_code: int = 0,
        *,
        phase_id: int = 0,
    ) -> GateResult:
        """Always return a passing GateResult and record the would-be command."""
        self.gates_run.append(
            {
                "gate_type": gate.gate_type,
                "command": gate.command,
                "phase_id": phase_id,
            }
        )
        checked_at = datetime.now(timezone.utc).isoformat()
        return GateResult(
            phase_id=phase_id,
            gate_type=gate.gate_type,
            passed=True,
            output=f"[dry-run] would have run: {gate.command or '(no command)'}",
            checked_at=checked_at,
        )


# ---------------------------------------------------------------------------
# CI gate helpers
# ---------------------------------------------------------------------------

# Markers in CI output text that indicate a completed-passing run.
_CI_PASS_MARKERS = ("conclusion: success", "status: completed", "PASS", "All checks passed")
# Markers that indicate a completed-failing run.
_CI_FAIL_MARKERS = ("conclusion: failure", "conclusion: cancelled", "FAIL", "checks failed")


def _parse_ci_output(output: str, exit_code: int) -> bool:
    """Determine CI gate pass/fail from captured output and exit code.

    Checks for well-known pass/fail marker strings in *output* before
    falling back to *exit_code*.  This allows both the full GitHub Actions
    integration (which injects structured markers) and a simple shell
    command (which communicates purely via exit code) to work through the
    same code path.

    Args:
        output: Captured stdout/stderr from the CI gate command or the
            GitHub Actions poller in ``ci_gate.py``.
        exit_code: Process exit code; 0 means success when no markers
            are found.

    Returns:
        ``True`` when the CI run passed, ``False`` otherwise.
    """
    lower = output.lower()
    for marker in _CI_PASS_MARKERS:
        if marker.lower() in lower:
            return True
    for marker in _CI_FAIL_MARKERS:
        if marker.lower() in lower:
            return False
    # No structured markers found — fall back to exit code.
    return exit_code == 0


def run_github_actions_gate(
    workflow_name: str,
    *,
    repo: str = "",
    ref: str = "HEAD",
    timeout_seconds: int = _CI_DEFAULT_TIMEOUT_SECONDS,
    poll_interval: int = _CI_POLL_INTERVAL_SECONDS,
) -> GateResult:
    """Dispatch a GitHub Actions workflow and poll until completion.

    Requires the ``gh`` CLI to be installed and authenticated, or a valid
    ``GITHUB_TOKEN`` environment variable.  When the token is absent or the
    network is unreachable the gate returns a ``passed=False`` result with
    an ``escalate`` marker so the caller can route to ``DecisionManager``.

    Gate configuration keys (from ``PlanGate.command``)::

        {"gate_type": "ci", "command": "workflow_name", "ci_provider": "github"}

    Args:
        workflow_name: The GitHub Actions workflow file name or ID (e.g.
            ``"ci.yml"``).
        repo: ``"owner/repo"`` string.  When empty, inferred from ``gh``
            context.
        ref: Branch or SHA to run the workflow on (default: ``"HEAD"``).
        timeout_seconds: Maximum seconds to wait before timing out.
        poll_interval: Seconds between status polls.

    Returns:
        A :class:`GateResult` with ``gate_type="ci"``.  The ``output``
        field contains the CI run URL on success, or an error description
        with ``"[escalate]"`` prefix when the token is missing.
    """
    checked_at = datetime.now(timezone.utc).isoformat()

    github_token = os.environ.get("GITHUB_TOKEN", "")
    if not github_token:
        msg = (
            "[escalate] GITHUB_TOKEN not set. "
            "Cannot dispatch GitHub Actions workflow without credentials. "
            "Use 'baton execute approve' to manually approve this gate."
        )
        logger.warning("CI gate: %s", msg)
        return GateResult(
            phase_id=0,
            gate_type="ci",
            passed=False,
            output=msg,
            checked_at=checked_at,
        )

    try:
        import subprocess  # noqa: PLC0415

        # Build gh workflow run command.
        cmd = ["gh", "workflow", "run", workflow_name]
        if repo:
            cmd += ["--repo", repo]
        if ref and ref != "HEAD":
            cmd += ["--ref", ref]

        logger.info("CI gate: dispatching workflow '%s'", workflow_name)
        dispatch_result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "GITHUB_TOKEN": github_token},
        )
        if dispatch_result.returncode != 0:
            err = dispatch_result.stderr.strip() or dispatch_result.stdout.strip()
            return GateResult(
                phase_id=0,
                gate_type="ci",
                passed=False,
                output=f"Workflow dispatch failed: {err}",
                checked_at=checked_at,
            )

        # Poll for completion.
        deadline = time.monotonic() + timeout_seconds
        run_url = ""
        while time.monotonic() < deadline:
            time.sleep(poll_interval)

            list_cmd = ["gh", "run", "list", "--workflow", workflow_name, "--limit", "1", "--json", "status,conclusion,url"]
            if repo:
                list_cmd += ["--repo", repo]

            poll_result = subprocess.run(
                list_cmd,
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "GITHUB_TOKEN": github_token},
            )
            if poll_result.returncode != 0:
                logger.debug("CI gate poll error: %s", poll_result.stderr.strip())
                continue

            import json  # noqa: PLC0415
            try:
                runs = json.loads(poll_result.stdout)
            except json.JSONDecodeError:
                continue

            if not runs:
                continue

            latest = runs[0]
            status = latest.get("status", "")
            conclusion = latest.get("conclusion", "")
            run_url = latest.get("url", "")

            logger.debug("CI gate poll: status=%s conclusion=%s", status, conclusion)

            if status == "completed":
                passed = conclusion == "success"
                marker = "conclusion: success" if passed else f"conclusion: {conclusion}"
                output = f"{marker}\nRun URL: {run_url}"
                logger.info(
                    "CI gate: completed — conclusion=%s passed=%s url=%s",
                    conclusion, passed, run_url,
                )
                return GateResult(
                    phase_id=0,
                    gate_type="ci",
                    passed=passed,
                    output=output,
                    checked_at=datetime.now(timezone.utc).isoformat(),
                )

        # Timed out.
        timeout_msg = (
            f"[escalate] CI gate timed out after {timeout_seconds}s "
            f"waiting for workflow '{workflow_name}' to complete."
            + (f"\nRun URL: {run_url}" if run_url else "")
        )
        logger.warning("CI gate: %s", timeout_msg)
        return GateResult(
            phase_id=0,
            gate_type="ci",
            passed=False,
            output=timeout_msg,
            checked_at=datetime.now(timezone.utc).isoformat(),
        )

    except FileNotFoundError:
        msg = (
            "[escalate] 'gh' CLI not found. "
            "Install the GitHub CLI or use 'baton execute approve' to manually approve this gate."
        )
        logger.warning("CI gate: %s", msg)
        return GateResult(
            phase_id=0,
            gate_type="ci",
            passed=False,
            output=msg,
            checked_at=checked_at,
        )
    except Exception as exc:  # noqa: BLE001
        msg = f"[escalate] CI gate error: {exc}"
        logger.warning("CI gate: %s", msg)
        return GateResult(
            phase_id=0,
            gate_type="ci",
            passed=False,
            output=msg,
            checked_at=checked_at,
        )
