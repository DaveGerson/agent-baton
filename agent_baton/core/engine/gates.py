"""Gate runner -- determines what QA gate checks to run and evaluates results.

Gates are quality checkpoints inserted between execution phases.  Each gate
type has specific pass/fail semantics:

- **build** / **test**: pass when exit code is 0.
- **lint**: pass when exit code is 0 AND no error markers are found in
  output (warnings are tolerated).
- **spec**: semantic evaluation via ``SpecComplianceEvaluator`` — uses a
  Claude Haiku call when ``ANTHROPIC_API_KEY`` is set, otherwise falls back
  to keyword-overlap heuristics.  Replaces the former trivial structural check
  (exit_code + non-empty output) which conveyed no semantic signal.
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

import json
import logging
import os
import string
import time
from datetime import datetime, timezone
from typing import Protocol

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
# Spec compliance evaluator
# ---------------------------------------------------------------------------

# Model used for semantic spec compliance checks (fast + cheap).
_SPEC_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_SPEC_HAIKU_MAX_TOKENS = 500
_SPEC_HAIKU_TIMEOUT = 10.0

# Minimum fraction of task-summary content tokens that must appear in output.
_HEURISTIC_OVERLAP_THRESHOLD = 0.30

# Common English stop words — enough to filter noise without a dependency.
_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "it", "its", "be", "was",
    "are", "were", "been", "has", "have", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "shall", "not",
    "no", "nor", "so", "yet", "both", "either", "neither", "this", "that",
    "these", "those", "i", "we", "you", "he", "she", "they", "what",
    "which", "who", "whom", "if", "then", "than", "because", "while",
    "although", "though", "after", "before", "since", "until", "when",
    "where", "how", "all", "each", "every", "any", "some", "such", "own",
    "same", "just", "more", "also", "into", "about", "up", "out", "can",
    "new", "use", "used", "using", "based", "per", "via",
})


def _content_tokens(text: str) -> list[str]:
    """Extract lowercase content tokens: length >= 4, non-stop-word, alpha."""
    translator = str.maketrans("", "", string.punctuation)
    words = text.lower().translate(translator).split()
    return [w for w in words if len(w) >= 4 and w not in _STOP_WORDS]


class _HaikuCaller(Protocol):
    """Structural type for the Haiku API call — injectable for tests."""

    def __call__(self, prompt: str) -> str: ...


def _default_haiku_caller(prompt: str) -> str:
    """Call Claude Haiku via the Anthropic SDK (same pattern as classifier.py)."""
    import anthropic  # noqa: PLC0415 — optional dependency, imported lazily

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=_SPEC_HAIKU_MODEL,
        max_tokens=_SPEC_HAIKU_MAX_TOKENS,
        timeout=_SPEC_HAIKU_TIMEOUT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


_SPEC_COMPLIANCE_PROMPT = """\
You are a spec compliance evaluator for a software orchestration engine.

Task intent (what the plan declared):
{task_summary}

Phase: {phase_name}
Phase steps:
{step_descriptions}

Agent output to evaluate:
{agent_output}

Does the agent output fulfill the declared task intent for this phase?
Return JSON only (no markdown, no commentary):
{{"compliant": true|false, "rationale": "one sentence", "deviations": ["deviation1", ...]}}

Rules:
- "compliant" must be a boolean.
- "deviations" must be a list (empty when compliant is true).
- Be lenient on style; strict on functional gaps."""


class SpecComplianceEvaluator:
    """Evaluate whether agent output fulfills the plan's declared intent.

    Two evaluation paths:
    1. Semantic (preferred): calls Claude Haiku when ANTHROPIC_API_KEY is set.
    2. Heuristic fallback: keyword-overlap against task_summary content tokens.

    The evaluator is injectable — pass a ``haiku_caller`` to replace the real
    API call in tests.

    Args:
        task_summary: The plan's top-level task description.
        phase_name: Name of the phase whose gate is being evaluated.
        step_descriptions: Step task_description strings for context.
        haiku_caller: Optional override for the Haiku API call (test seam).
    """

    def __init__(
        self,
        task_summary: str = "",
        phase_name: str = "",
        step_descriptions: list[str] | None = None,
        haiku_caller: _HaikuCaller | None = None,
    ) -> None:
        self._task_summary = task_summary
        self._phase_name = phase_name
        self._step_descriptions = step_descriptions or []
        self._haiku_caller: _HaikuCaller = haiku_caller or _default_haiku_caller

    def evaluate(self, command_output: str) -> tuple[bool, str]:
        """Evaluate output against intent; return (passed, rationale).

        Never raises — both error paths return (False, reason) so the gate
        always produces an actionable result.
        """
        if not self._task_summary.strip():
            # Defensive: no plan context available — heuristic would be noise.
            return False, "No task_summary available; spec gate cannot evaluate compliance."

        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if api_key:
            try:
                return self._semantic_evaluate(command_output)
            except Exception as exc:  # noqa: BLE001
                # API call failed — fall through to heuristic.
                logger.warning("SpecComplianceEvaluator: semantic path failed (%s), using heuristic", exc)

        return self._heuristic_evaluate(command_output)

    def _semantic_evaluate(self, command_output: str) -> tuple[bool, str]:
        steps_text = "\n".join(
            f"- {desc}" for desc in self._step_descriptions if desc
        ) or "(no step descriptions)"

        prompt = _SPEC_COMPLIANCE_PROMPT.format(
            task_summary=self._task_summary,
            phase_name=self._phase_name or "(unknown)",
            step_descriptions=steps_text,
            agent_output=command_output[:4000],  # cap to avoid huge prompts
        )

        raw = self._haiku_caller(prompt)

        # Strip markdown fences if the model adds them despite instructions.
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(l for l in lines if not l.strip().startswith("```"))

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to extract JSON substring.
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                data = json.loads(cleaned[start: end + 1])
            else:
                raise ValueError(f"Haiku returned unparseable JSON: {cleaned[:200]!r}")

        compliant: bool = bool(data.get("compliant", False))
        rationale: str = str(data.get("rationale", ""))
        deviations: list[str] = data.get("deviations", [])
        if deviations:
            rationale += " Deviations: " + "; ".join(str(d) for d in deviations)
        return compliant, rationale

    def _heuristic_evaluate(self, command_output: str) -> tuple[bool, str]:
        if not command_output.strip():
            return False, "[heuristic-fallback] Output is empty; cannot verify spec compliance."

        summary_tokens = set(_content_tokens(self._task_summary))
        if not summary_tokens:
            # Summary has no meaningful tokens — nothing to compare against.
            return False, "[heuristic-fallback] task_summary yields no content tokens."

        output_tokens = set(_content_tokens(command_output))
        overlap = summary_tokens & output_tokens
        ratio = len(overlap) / len(summary_tokens)
        passed = ratio >= _HEURISTIC_OVERLAP_THRESHOLD
        rationale = (
            f"[heuristic-fallback] Keyword overlap {len(overlap)}/{len(summary_tokens)} "
            f"({ratio:.0%}); threshold {_HEURISTIC_OVERLAP_THRESHOLD:.0%}. "
            f"{'PASS' if passed else 'FAIL'}."
        )
        return passed, rationale


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
        # SpecComplianceEvaluator replaces the old trivial structural check.
        # Callers can inject one pre-configured with plan context; defaults to
        # an empty evaluator which will use heuristic-fallback conservatively.
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
        - 'spec' gates: semantic evaluation via SpecComplianceEvaluator — Haiku
          API when ANTHROPIC_API_KEY is set, heuristic keyword-overlap otherwise
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
            # Semantic evaluation via SpecComplianceEvaluator (replaces the
            # old trivial exit_code+non-empty check which had no semantic value).
            # A non-zero exit_code is treated as an automatic failure so that
            # a broken spec command still blocks progress.
            if exit_code != 0:
                passed = False
                rationale = f"Spec command exited with code {exit_code}."
            else:
                passed, rationale = self._spec_evaluator.evaluate(command_output)
            logger.info(
                "Gate 'spec': %s — %s",
                "PASS" if passed else "FAIL",
                rationale,
            )
            return GateResult(
                phase_id=0,
                gate_type=gate_type,
                passed=passed,
                output=rationale,
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
