"""HeadlessClaude — synchronous subprocess wrapper for ``claude --print``.

Provides plan generation and arbitrary prompt execution via the Claude Code
CLI without requiring an active Claude Code session.  Used by:

- **ForgeSession** — to generate real LLM-quality plans instead of the
  rule-based IntelligentPlanner templates.
- **``baton execute run``** — to dispatch agents autonomously in a CLI-only
  execution loop (no Claude Code session needed).
- **PMO execute endpoint** — to launch headless execution from the UI.

Security: inherits the same environment-whitelist and no-shell-interpolation
invariants as :class:`~agent_baton.core.runtime.claude_launcher.ClaudeCodeLauncher`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_baton.models.execution import MachinePlan
from agent_baton.core.runtime.claude_launcher import (
    _EXCLUDE_FLAG,
    _supports_exclude_flag,
)

logger = logging.getLogger(__name__)

_API_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_-]+")

_DEFAULT_ENV_PASSTHROUGH: list[str] = [
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "AWS_PROFILE",
    "AWS_REGION",
]


@dataclass
class HeadlessResult:
    """Result of a headless Claude invocation."""

    success: bool
    output: str = ""
    error: str = ""
    duration_seconds: float = 0.0
    raw_json: dict[str, Any] | None = None


@dataclass
class HeadlessConfig:
    """Configuration for :class:`HeadlessClaude`."""

    claude_path: str = "claude"
    model: str = "sonnet"
    timeout_seconds: float = 120.0
    max_retries: int = 2
    base_retry_delay: float = 5.0
    working_directory: Path | None = None
    env_passthrough: list[str] = field(
        default_factory=lambda: list(_DEFAULT_ENV_PASSTHROUGH)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "claude_path": self.claude_path,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "base_retry_delay": self.base_retry_delay,
            "working_directory": self.working_directory.as_posix() if self.working_directory else None,
            "env_passthrough": list(self.env_passthrough),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HeadlessConfig:
        wd = data.get("working_directory")
        return cls(
            claude_path=data.get("claude_path", "claude"),
            model=data.get("model", "sonnet"),
            timeout_seconds=float(data.get("timeout_seconds", 120.0)),
            max_retries=int(data.get("max_retries", 2)),
            base_retry_delay=float(data.get("base_retry_delay", 5.0)),
            working_directory=Path(wd) if wd else None,
            env_passthrough=list(data.get("env_passthrough", _DEFAULT_ENV_PASSTHROUGH)),
        )


class HeadlessClaude:
    """Synchronous-style wrapper around ``claude --print`` for plan generation.

    Validates the ``claude`` binary eagerly at construction.  Falls back
    gracefully when the CLI is not installed (``is_available`` returns False).

    Usage::

        hc = HeadlessClaude()
        if hc.is_available:
            result = await hc.run("Generate a plan for: add login page")
            if result.success:
                print(result.output)
    """

    def __init__(self, config: HeadlessConfig | None = None) -> None:
        self._config = config or HeadlessConfig()
        resolved = shutil.which(self._config.claude_path)
        self._claude_bin: str | None = resolved
        if resolved is None:
            logger.info(
                "HeadlessClaude: claude binary not found at %r — headless mode unavailable",
                self._config.claude_path,
            )

    @property
    def is_available(self) -> bool:
        """True if the claude CLI binary was found on PATH."""
        return self._claude_bin is not None

    async def run(self, prompt: str, *, model: str | None = None) -> HeadlessResult:
        """Execute a prompt via ``claude --print`` and return the result.

        Args:
            prompt: The full prompt text to send.
            model: Override the default model for this call.

        Returns:
            A :class:`HeadlessResult` with success/failure, output text,
            and optional parsed JSON.
        """
        if self._claude_bin is None:
            return HeadlessResult(
                success=False,
                error="claude CLI not available",
            )

        effective_model = model or self._config.model
        cmd = [
            self._claude_bin,
            "--print",
            "--model", effective_model,
            "--output-format", "json",
        ]
        # Improve prompt-cache reuse by moving per-machine dynamic sections
        # out of the system prompt.  HeadlessClaude never injects a custom
        # --system-prompt, so the flag is always applicable when supported.
        if _supports_exclude_flag():
            cmd.append(_EXCLUDE_FLAG)

        use_stdin = len(prompt.encode()) > 131_072
        if not use_stdin:
            cmd.extend(["-p", prompt])

        env = self._build_env()
        cwd = str(self._config.working_directory or Path.cwd())

        attempt = 0
        while True:
            attempt += 1
            result = await self._run_once(
                cmd=cmd,
                env=env,
                cwd=cwd,
                prompt_stdin=prompt.encode() if use_stdin else None,
            )

            if not result.success and self._is_rate_limit(result.error):
                if attempt <= self._config.max_retries:
                    delay = self._config.base_retry_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "HeadlessClaude rate limit (attempt %d/%d) — retrying in %.1fs",
                        attempt, self._config.max_retries, delay,
                    )
                    await asyncio.sleep(delay)
                    continue

            return result

    async def generate_plan(
        self,
        description: str,
        *,
        project_id: str = "",
        project_path: str = "",
        task_type: str | None = None,
        priority: int = 0,
        agents_available: list[str] | None = None,
        refinement_context: str = "",
    ) -> MachinePlan | None:
        """Generate a MachinePlan via the Claude CLI.

        Constructs a plan-generation prompt, sends it to Claude, and
        parses the JSON response into a :class:`MachinePlan`.

        Returns:
            A ``MachinePlan`` on success, or ``None`` if generation fails.
        """
        prompt = self._build_plan_prompt(
            description=description,
            project_id=project_id,
            project_path=project_path,
            task_type=task_type,
            priority=priority,
            agents_available=agents_available,
            refinement_context=refinement_context,
        )

        result = await self.run(prompt)
        if not result.success:
            logger.error("HeadlessClaude plan generation failed: %s", result.error)
            return None

        return self._parse_plan_output(result.output)

    # -- Private helpers ------------------------------------------------------

    def _build_env(self) -> dict[str, str]:
        """Build a whitelisted environment for the subprocess."""
        env: dict[str, str] = {}
        for essential in ("PATH", "HOME"):
            val = os.environ.get(essential)
            if val is not None:
                env[essential] = val
        for key in self._config.env_passthrough:
            val = os.environ.get(key)
            if val is not None:
                env[key] = val
        return env

    async def _run_once(
        self,
        *,
        cmd: list[str],
        env: dict[str, str],
        cwd: str,
        prompt_stdin: bytes | None,
    ) -> HeadlessResult:
        """Run the subprocess once and return a HeadlessResult."""
        start = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if prompt_stdin is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            return HeadlessResult(
                success=False,
                error=f"Failed to start claude subprocess: {exc}",
                duration_seconds=time.monotonic() - start,
            )

        try:
            if prompt_stdin is not None:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input=prompt_stdin),
                    timeout=self._config.timeout_seconds,
                )
            else:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self._config.timeout_seconds,
                )
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass
            return HeadlessResult(
                success=False,
                error=f"Timed out after {self._config.timeout_seconds:.0f}s",
                duration_seconds=time.monotonic() - start,
            )

        elapsed = time.monotonic() - start
        stderr_text = _API_KEY_RE.sub("sk-ant-***REDACTED***", stderr.decode(errors="replace").strip())
        stdout_text = stdout.decode(errors="replace").strip()
        exit_code = process.returncode if process.returncode is not None else -1

        # Try JSON parse (claude --output-format json)
        parsed: dict[str, Any] | None = None
        if stdout_text:
            try:
                parsed = json.loads(stdout_text)
            except json.JSONDecodeError:
                pass

        if parsed is not None:
            is_error = bool(parsed.get("is_error", False))
            result_text = str(parsed.get("result", ""))

            if exit_code != 0 or is_error:
                return HeadlessResult(
                    success=False,
                    output=result_text,
                    error=stderr_text or result_text or f"exit code {exit_code}",
                    duration_seconds=elapsed,
                    raw_json=parsed,
                )

            return HeadlessResult(
                success=True,
                output=result_text,
                duration_seconds=elapsed,
                raw_json=parsed,
            )

        # Raw text fallback
        if exit_code != 0:
            return HeadlessResult(
                success=False,
                output=stdout_text,
                error=stderr_text or f"exit code {exit_code}",
                duration_seconds=elapsed,
            )

        return HeadlessResult(
            success=True,
            output=stdout_text,
            duration_seconds=elapsed,
        )

    @staticmethod
    def _is_rate_limit(error: str) -> bool:
        lower = error.lower()
        return "rate limit" in lower or "429" in lower

    @staticmethod
    def _build_plan_prompt(
        description: str,
        *,
        project_id: str = "",
        project_path: str = "",
        task_type: str | None = None,
        priority: int = 0,
        agents_available: list[str] | None = None,
        refinement_context: str = "",
    ) -> str:
        """Build the plan-generation prompt for Claude."""
        priority_label = {2: "CRITICAL", 1: "HIGH", 0: "NORMAL"}.get(priority, "NORMAL")
        agents_str = ", ".join(agents_available) if agents_available else "auto-detect from task"

        parts = [
            "You are an execution planner for the Agent Baton orchestration system.",
            "Generate a machine-readable execution plan as a JSON object.",
            "",
            "## Task Description",
            description,
            "",
            "## Constraints",
            f"- Priority: {priority_label}",
        ]
        if task_type:
            parts.append(f"- Task type: {task_type}")
        if project_id:
            parts.append(f"- Project ID: {project_id}")
        if project_path:
            parts.append(f"- Project path: {project_path}")
        parts.append(f"- Available agents: {agents_str}")

        if refinement_context:
            parts.extend(["", "## Refinement Context (from user interview)", refinement_context])

        parts.extend([
            "",
            "## Output Format",
            "Return ONLY a JSON object matching this schema (no markdown fences, no commentary):",
            "",
            """{
  "task_id": "<slug derived from task summary, max 60 chars>",
  "task_summary": "<the task description>",
  "risk_level": "LOW|MEDIUM|HIGH|CRITICAL",
  "budget_tier": "lean|standard|thorough",
  "execution_mode": "phased",
  "git_strategy": "commit-per-agent",
  "task_type": "<feature|bugfix|refactor|analysis|migration|test>",
  "complexity": "light|medium|heavy",
  "phases": [
    {
      "phase_id": 1,
      "name": "<phase name>",
      "steps": [
        {
          "step_id": "1.1",
          "agent_name": "<agent name from available agents>",
          "task_description": "<what this agent should do>",
          "model": "sonnet|opus|haiku"
        }
      ],
      "gate": {
        "gate_type": "test|build|review",
        "command": "<shell command to run, e.g. pytest>",
        "description": "<what the gate checks>"
      }
    }
  ]
}""",
            "",
            "## Planning Rules",
            "- Use the MINIMUM number of agents and phases needed. Simple tasks get 1-2 phases.",
            "- Only assign agents that are relevant to the work. Do NOT assign every available agent.",
            "- Every code-producing phase MUST have a test gate (pytest).",
            "- Research/investigate/review phases do NOT need gates.",
            "- Use 'opus' model only for complex architectural decisions. Default to 'sonnet'.",
            "- task_id should be a URL-safe slug: lowercase, hyphens, max 60 chars.",
            "- step_id format: '<phase_id>.<step_number>' (e.g. '1.1', '2.1', '2.2').",
        ])

        return "\n".join(parts)

    @staticmethod
    def _parse_plan_output(output: str) -> MachinePlan | None:
        """Parse Claude's output into a MachinePlan.

        Handles both raw JSON and markdown-fenced JSON blocks.
        """
        text = output.strip()

        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            # Remove first line (```json or ```) and last line (```)
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object in the output
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    logger.error("HeadlessClaude: could not parse plan JSON from output")
                    return None
            else:
                logger.error("HeadlessClaude: no JSON object found in output")
                return None

        try:
            return MachinePlan.from_dict(data)
        except (KeyError, TypeError, ValueError) as exc:
            logger.error("HeadlessClaude: invalid plan structure: %s", exc)
            return None
