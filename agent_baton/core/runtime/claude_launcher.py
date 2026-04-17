"""ClaudeCodeLauncher -- production ``AgentLauncher`` that invokes the ``claude`` CLI.

This module implements the ``AgentLauncher`` protocol by launching ``claude``
as an async subprocess.  It is the only launcher used in production; all
other launchers are test mocks.

Security invariants (enforced on every call):

- **Environment whitelist**: The subprocess environment is built from an
  explicit whitelist of variable names; ``os.environ`` is never copied
  wholesale, preventing accidental secret leakage.
- **No shell interpolation**: The prompt is always a separate list element
  (never interpolated into a shell string), and ``create_subprocess_exec``
  is used exclusively (never ``create_subprocess_shell``).
- **Binary validation**: The ``claude`` binary path is resolved and validated
  at construction time, catching misconfiguration eagerly.
- **API key redaction**: Any Anthropic API key patterns in stderr output are
  replaced with ``sk-ant-***REDACTED***`` before being stored in results.

Retry behavior:

- Rate-limit responses (429 / "rate limit" in stderr) trigger exponential
  backoff retries up to ``max_retries`` attempts.
- All other failures are returned immediately without retry.

Typical usage::

    launcher = ClaudeCodeLauncher()
    result = await launcher.launch(
        agent_name="backend-engineer--python",
        model="sonnet",
        prompt="Your task is ...",
        step_id="1.1",
    )
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

from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.core.runtime.launcher import LaunchResult
from agent_baton.models.agent import AgentDefinition

logger = logging.getLogger(__name__)

# A5: Sensitive data patterns applied to both outcome and error text before storage.
# Keep as a module-level tuple so operators can extend it without touching launch logic.
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Anthropic API keys
    (re.compile(r"sk-ant-[A-Za-z0-9_-]+"), "sk-ant-***REDACTED***"),
    # GitHub personal access tokens (classic and fine-grained)
    (re.compile(r"ghp_[A-Za-z0-9_]+"), "ghp_***REDACTED***"),
    (re.compile(r"github_pat_[A-Za-z0-9_]+"), "github_pat_***REDACTED***"),
    # Slack bot/user tokens
    (re.compile(r"xoxb-[A-Za-z0-9_-]+"), "xoxb-***REDACTED***"),
    (re.compile(r"xoxp-[A-Za-z0-9_-]+"), "xoxp-***REDACTED***"),
    # Generic JSON password fields  (e.g. {"password": "hunter2"})
    (
        re.compile(r'"password"\s*:\s*"[^"]*"', re.IGNORECASE),
        '"password": "***REDACTED***"',
    ),
    # Generic JSON secret/token fields
    (
        re.compile(r'"(?:secret|token|api_key|apikey)"\s*:\s*"[^"]*"', re.IGNORECASE),
        r'"***REDACTED_KEY***": "***REDACTED***"',
    ),
)

# Keep the old name as an alias so any external callers do not break.
_API_KEY_RE = _REDACT_PATTERNS[0][0]


def _redact_sensitive(text: str) -> str:
    """Strip known sensitive patterns from agent output or error text.

    Applied to both ``LaunchResult.outcome`` and ``LaunchResult.error``
    before they are stored in step results, traces, and retrospectives
    (A5 — stdout redaction for sensitive data).

    Patterns covered: Anthropic API keys, GitHub PATs, Slack tokens,
    and generic JSON ``password``/``secret``/``token``/``api_key`` fields.
    """
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_stderr(text: str) -> str:
    """Backward-compatible alias for :func:`_redact_sensitive`.

    Retained so that call sites outside this module that reference
    ``_redact_stderr`` by name continue to work without modification.
    """
    return _redact_sensitive(text)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_MODEL_TIMEOUTS: dict[str, float] = {
    "opus": 900.0,
    "sonnet": 600.0,
    "haiku": 300.0,
}

_DEFAULT_ENV_PASSTHROUGH: list[str] = [
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "AWS_PROFILE",
    "AWS_REGION",
]


@dataclass
class ClaudeCodeConfig:
    """Configuration for ``ClaudeCodeLauncher``.

    All fields have sensible defaults so callers can do simply::

        launcher = ClaudeCodeLauncher(ClaudeCodeConfig(model_timeouts={"opus": 1200.0}))

    Serializable via ``to_dict()`` / ``from_dict()`` for persistence in
    daemon configuration files.
    """

    claude_path: str = "claude"
    """Path to the ``claude`` binary, or bare name for PATH lookup."""

    working_directory: Path | None = None
    """Working directory for subprocess calls.  ``None`` uses the current cwd."""

    default_timeout_seconds: float = 600.0
    """Timeout applied when no model-specific override is found."""

    model_timeouts: dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_MODEL_TIMEOUTS)
    )
    """Per-model timeout overrides (keyed by short name: ``opus``, ``sonnet``, …)."""

    max_retries: int = 3
    """Maximum number of retry attempts on rate-limit responses."""

    base_retry_delay: float = 5.0
    """Base delay (seconds) for exponential-backoff retries."""

    max_outcome_length: int = 4000
    """Maximum characters kept from the agent outcome string."""

    prompt_file_threshold: int = 131_072
    """Byte threshold above which the prompt is delivered via stdin rather
    than the ``-p`` flag (128 KB)."""

    env_passthrough: list[str] = field(
        default_factory=lambda: list(_DEFAULT_ENV_PASSTHROUGH)
    )
    """Environment variable names forwarded to the subprocess.  Only these
    variables (plus ``PATH`` and ``HOME``) are included in the child
    environment."""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict (JSON-safe)."""
        return {
            "claude_path": self.claude_path,
            "working_directory": self.working_directory.as_posix() if self.working_directory else None,
            "default_timeout_seconds": self.default_timeout_seconds,
            "model_timeouts": dict(self.model_timeouts),
            "max_retries": self.max_retries,
            "base_retry_delay": self.base_retry_delay,
            "max_outcome_length": self.max_outcome_length,
            "prompt_file_threshold": self.prompt_file_threshold,
            "env_passthrough": list(self.env_passthrough),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClaudeCodeConfig:
        """Deserialise from a plain dict."""
        wd = data.get("working_directory")
        return cls(
            claude_path=data.get("claude_path", "claude"),
            working_directory=Path(wd) if wd else None,
            default_timeout_seconds=float(data.get("default_timeout_seconds", 600.0)),
            model_timeouts=dict(data.get("model_timeouts", _DEFAULT_MODEL_TIMEOUTS)),
            max_retries=int(data.get("max_retries", 3)),
            base_retry_delay=float(data.get("base_retry_delay", 5.0)),
            max_outcome_length=int(data.get("max_outcome_length", 4000)),
            prompt_file_threshold=int(data.get("prompt_file_threshold", 131_072)),
            env_passthrough=list(data.get("env_passthrough", _DEFAULT_ENV_PASSTHROUGH)),
        )


# ---------------------------------------------------------------------------
# Launcher
# ---------------------------------------------------------------------------


class ClaudeCodeLauncher:
    """Production ``AgentLauncher`` that invokes the ``claude`` CLI.

    Validates the ``claude`` binary at construction time so misconfiguration
    is caught eagerly rather than at first launch.  Also checks for ``git``
    availability (non-fatal) to enable ``files_changed`` and ``commit_hash``
    population in launch results.

    Security invariants (enforced on every call):

    - Environment is built from an explicit whitelist -- never
      ``os.environ.copy()``.
    - Subprocess is started with ``asyncio.create_subprocess_exec`` -- never
      ``create_subprocess_shell``.
    - The prompt is always a separate list element -- never interpolated.

    Attributes:
        _config: Launcher configuration (timeouts, retries, paths).
        _registry: Optional agent registry for resolving agent definitions
            and injecting system prompts, permission modes, and tool lists.
        _claude_bin: Resolved absolute path to the ``claude`` binary.
        _git_bin: Resolved path to ``git``, or None if unavailable.
    """

    def __init__(
        self,
        config: ClaudeCodeConfig | None = None,
        registry: AgentRegistry | None = None,
    ) -> None:
        self._config = config or ClaudeCodeConfig()
        self._registry = registry

        # Validate claude binary.
        resolved = shutil.which(self._config.claude_path)
        if resolved is None:
            raise RuntimeError(
                f"claude binary not found: {self._config.claude_path!r}. "
                "Install Claude Code CLI (https://claude.ai/code) or set "
                "ClaudeCodeConfig.claude_path to the full binary path."
            )
        self._claude_bin: str = resolved
        logger.debug("ClaudeCodeLauncher: using claude binary at %s", self._claude_bin)

        # Optionally note whether git is available (non-fatal if missing).
        self._git_bin: str | None = shutil.which("git")
        if self._git_bin is None:
            logger.warning(
                "ClaudeCodeLauncher: git not found — files_changed and "
                "commit_hash will be empty in LaunchResult."
            )

        # Registry of active subprocesses for cleanup on shutdown.
        # Set operations are safe without locks because asyncio is single-threaded.
        self._active_processes: set[asyncio.subprocess.Process] = set()

    # ── Public API ───────────────────────────────────────────────────────────

    async def launch(
        self,
        agent_name: str,
        model: str,
        prompt: str,
        step_id: str = "",
        mcp_servers: list[str] | None = None,
    ) -> LaunchResult:
        """Launch a Claude Code agent and return its result.

        Implements the :class:`AgentLauncher` protocol.
        """
        start = time.monotonic()
        pre_commit = await self._git_rev_parse()

        agent: AgentDefinition | None = None
        if self._registry is not None:
            agent = self._registry.get(agent_name)
        cmd = self._build_command(model, agent, mcp_servers=mcp_servers)
        env = self._build_env()
        timeout = self._resolve_timeout(model)
        use_stdin = len(prompt.encode()) > self._config.prompt_file_threshold
        cwd = str(self._config.working_directory or Path.cwd())

        if use_stdin:
            # Large prompt — deliver via stdin; drop the -p flag from the command.
            pass
        else:
            # Normal prompt — append -p and the prompt as separate list elements.
            cmd = [*cmd, "-p", prompt]

        attempt = 0
        while True:
            attempt += 1
            result = await self._run_once(
                cmd=cmd,
                env=env,
                cwd=cwd,
                timeout=timeout,
                prompt_stdin=prompt.encode() if use_stdin else None,
                agent_name=agent_name,
                step_id=step_id,
                start=start,
            )

            if result.status == "failed" and self._is_rate_limit(result.error):
                if attempt <= self._config.max_retries:
                    delay = self._config.base_retry_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "Rate limit for step %s (attempt %d/%d) — retrying in %.1fs",
                        step_id,
                        attempt,
                        self._config.max_retries,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    start = time.monotonic()
                    continue

            break

        # Populate git fields if the agent committed anything.
        if result.status == "complete" and pre_commit:
            post_commit = await self._git_rev_parse()
            if post_commit and post_commit != pre_commit:
                result.commit_hash = post_commit
                result.files_changed = await self._git_diff_files(pre_commit, post_commit)

        return result

    # ── Private helpers ──────────────────────────────────────────────────────

    def _build_command(
        self,
        model: str,
        agent: AgentDefinition | None = None,
        mcp_servers: list[str] | None = None,
    ) -> list[str]:
        """Return the base ``claude`` command list (without the prompt).

        The prompt is appended separately as ``["-p", prompt]`` by the caller,
        ensuring it is never interpolated into a shell string.

        When *agent* is provided, agent-specific flags are injected:

        - ``--system-prompt`` when the agent has non-empty instructions.
        - ``--permission-mode`` when set to something other than ``"default"``.
        - ``--allowedTools`` when the agent declares a non-empty tool list.

        When *mcp_servers* is non-empty, ``--mcp-config`` is appended with
        the server names joined by commas.
        """
        cmd: list[str] = [
            self._claude_bin,
            "--print",
            "--model", model,
            "--output-format", "json",
        ]
        if agent is not None:
            if agent.instructions and agent.instructions.strip():
                cmd.extend(["--system-prompt", agent.instructions])
            if agent.permission_mode and agent.permission_mode != "default":
                cmd.extend(["--permission-mode", agent.permission_mode])
            if agent.tools:
                cmd.extend(["--allowedTools", ",".join(agent.tools)])
        if mcp_servers:
            cmd.extend(["--mcp-config", ",".join(mcp_servers)])
        return cmd

    def _build_env(self) -> dict[str, str]:
        """Return a fresh, whitelisted environment dict for the subprocess.

        SECURITY: this method NEVER starts from ``os.environ.copy()``.  It
        builds a new dict containing only the explicitly whitelisted variables
        plus ``PATH`` and ``HOME``.
        """
        env: dict[str, str] = {}

        # Always include PATH and HOME so the subprocess can find binaries and
        # resolve the home directory.
        for essential in ("PATH", "HOME"):
            val = os.environ.get(essential)
            if val is not None:
                env[essential] = val

        # Forward whitelisted API / cloud variables.
        for key in self._config.env_passthrough:
            val = os.environ.get(key)
            if val is not None:
                env[key] = val

        return env

    def _resolve_timeout(self, model: str) -> float:
        """Return the timeout for *model*, falling back to the default."""
        # Try exact match first, then case-insensitive substring.
        if model in self._config.model_timeouts:
            return self._config.model_timeouts[model]
        model_lower = model.lower()
        for key, value in self._config.model_timeouts.items():
            if key in model_lower:
                return value
        return self._config.default_timeout_seconds

    def _parse_output(
        self,
        stdout: bytes,
        stderr: bytes,
        exit_code: int,
        step_id: str,
        agent_name: str,
        elapsed: float,
    ) -> LaunchResult:
        """Parse subprocess output into a :class:`LaunchResult`.

        Attempts JSON parsing first; falls back to treating raw stdout as the
        outcome text.
        """
        stderr_text = stderr.decode(errors="replace").strip()
        stdout_text = stdout.decode(errors="replace").strip()

        # --- Attempt structured JSON parse -----------------------------------
        parsed: dict[str, Any] | None = None
        if stdout_text:
            try:
                parsed = json.loads(stdout_text)
            except json.JSONDecodeError:
                pass

        if parsed is not None:
            is_error: bool = bool(parsed.get("is_error", False))
            result_text: str = str(parsed.get("result", ""))
            # A5: redact sensitive patterns from outcome before storage.
            outcome = _redact_sensitive(result_text)[: self._config.max_outcome_length]

            # Token usage
            usage = parsed.get("usage", {}) or {}
            estimated_tokens = int(
                usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            )

            # Duration from JSON preferred; wall-clock fallback.
            duration_ms = parsed.get("duration_ms")
            if duration_ms is not None:
                duration_seconds = float(duration_ms) / 1000.0
            else:
                duration_seconds = elapsed

            if exit_code != 0 or is_error:
                # Include both stderr and result_text so rate-limit
                # detection works regardless of where the 429 appears.
                if is_error and stderr_text:
                    error = _redact_sensitive(f"{stderr_text}\n{result_text}")
                elif is_error:
                    error = _redact_sensitive(result_text)
                else:
                    error = _redact_sensitive(stderr_text) or f"exit code {exit_code}"
                return LaunchResult(
                    step_id=step_id,
                    agent_name=agent_name,
                    status="failed",
                    outcome=outcome,
                    duration_seconds=duration_seconds,
                    estimated_tokens=estimated_tokens,
                    error=error,
                )

            return LaunchResult(
                step_id=step_id,
                agent_name=agent_name,
                status="complete",
                outcome=outcome,
                duration_seconds=duration_seconds,
                estimated_tokens=estimated_tokens,
            )

        # --- Raw text fallback -----------------------------------------------
        # A5: redact sensitive patterns from raw stdout before storage.
        outcome = _redact_sensitive(stdout_text)[: self._config.max_outcome_length]

        # Estimate tokens from raw output length (1 token ≈ 4 chars).
        # stdout_text is used (not truncated outcome) to keep the estimate
        # representative of actual consumption; the cap is set by the OS
        # pipe buffer in practice.
        raw_estimated_tokens = max(1, len(stdout_text) // 4) if stdout_text else 0

        if exit_code != 0:
            return LaunchResult(
                step_id=step_id,
                agent_name=agent_name,
                status="failed",
                outcome=outcome,
                duration_seconds=elapsed,
                estimated_tokens=raw_estimated_tokens,
                error=_redact_sensitive(stderr_text) or f"exit code {exit_code}",
            )

        return LaunchResult(
            step_id=step_id,
            agent_name=agent_name,
            status="complete",
            outcome=outcome,
            duration_seconds=elapsed,
            estimated_tokens=raw_estimated_tokens,
        )

    def _is_rate_limit(self, stderr: str) -> bool:
        """Return ``True`` if *stderr* indicates a rate-limit response.

        Checks for "rate limit" (case-insensitive) or HTTP status code
        "429" anywhere in the error text.  When True, the caller retries
        with exponential backoff.
        """
        lower = stderr.lower()
        return "rate limit" in lower or "429" in lower

    async def _git_rev_parse(self) -> str:
        """Return the current HEAD commit hash, or ``""`` on failure."""
        if self._git_bin is None:
            return ""
        try:
            proc = await asyncio.create_subprocess_exec(
                self._git_bin, "rev-parse", "HEAD",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(self._config.working_directory or Path.cwd()),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            if proc.returncode == 0:
                return stdout.decode().strip()
        except (OSError, asyncio.TimeoutError):
            pass
        return ""

    async def _git_diff_files(self, from_commit: str, to_commit: str) -> list[str]:
        """Return files changed between *from_commit* and *to_commit*."""
        if self._git_bin is None or not from_commit or not to_commit:
            return []
        try:
            proc = await asyncio.create_subprocess_exec(
                self._git_bin, "diff", "--name-only", from_commit, to_commit,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(self._config.working_directory or Path.cwd()),
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            if proc.returncode == 0:
                return [f for f in stdout.decode().splitlines() if f]
        except (OSError, asyncio.TimeoutError):
            pass
        return []

    async def _run_once(
        self,
        *,
        cmd: list[str],
        env: dict[str, str],
        cwd: str,
        timeout: float,
        prompt_stdin: bytes | None,
        agent_name: str,
        step_id: str,
        start: float,
    ) -> LaunchResult:
        """Run the ``claude`` subprocess once and return a ``LaunchResult``.

        Handles three failure modes:

        - **OSError** at subprocess creation (binary not found, permission
          denied): returns a failed result immediately.
        - **TimeoutError** during communication: kills the process and
          returns a failed result with the timeout duration.
        - **Non-zero exit code** or ``is_error`` in JSON output: returns
          a failed result with redacted stderr.

        Args:
            cmd: Complete command list (claude binary + flags).
            env: Whitelisted environment variables for the subprocess.
            cwd: Working directory for the subprocess.
            timeout: Maximum seconds to wait for the subprocess.
            prompt_stdin: Prompt bytes for stdin delivery (when prompt
                exceeds the file threshold), or None for ``-p`` flag delivery.
            agent_name: Agent name for the result.
            step_id: Step ID for the result.
            start: ``time.monotonic()`` timestamp from launch start.

        Returns:
            A ``LaunchResult`` with status, outcome, and metadata.
        """
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
            elapsed = time.monotonic() - start
            return LaunchResult(
                step_id=step_id,
                agent_name=agent_name,
                status="failed",
                duration_seconds=elapsed,
                error=f"Failed to start claude subprocess: {exc}",
            )

        self._active_processes.add(process)
        try:
            if prompt_stdin is not None:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input=prompt_stdin),
                    timeout=timeout,
                )
            else:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
        except asyncio.TimeoutError:
            try:
                process.kill()
                await process.wait()
            except ProcessLookupError:
                pass
            elapsed = time.monotonic() - start
            return LaunchResult(
                step_id=step_id,
                agent_name=agent_name,
                status="failed",
                duration_seconds=elapsed,
                error=f"Agent timed out after {timeout:.0f}s",
            )
        finally:
            self._active_processes.discard(process)

        elapsed = time.monotonic() - start
        exit_code = process.returncode if process.returncode is not None else -1
        return self._parse_output(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            step_id=step_id,
            agent_name=agent_name,
            elapsed=elapsed,
        )

    async def cleanup(self) -> None:
        """Terminate all active subprocesses registered in ``_active_processes``.

        Called during graceful shutdown (e.g. SIGTERM) to ensure that child
        ``claude`` processes started with ``start_new_session=True`` are not
        orphaned.  For each process:

        1. Send ``SIGTERM`` via ``process.terminate()``.
        2. Wait up to 5 seconds for the process to exit.
        3. If still running, escalate to ``SIGKILL`` via ``process.kill()``.

        Safe to call multiple times or when the set is empty.
        """
        if not self._active_processes:
            return

        processes = list(self._active_processes)
        logger.info(
            "ClaudeCodeLauncher.cleanup(): terminating %d active subprocess(es)",
            len(processes),
        )
        for process in processes:
            try:
                process.terminate()
            except ProcessLookupError:
                # Process already exited between the snapshot and terminate().
                self._active_processes.discard(process)
                continue

            try:
                await asyncio.wait_for(process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "ClaudeCodeLauncher.cleanup(): PID %s did not exit after SIGTERM, sending SIGKILL",
                    process.pid,
                )
                try:
                    process.kill()
                    await process.wait()
                except ProcessLookupError:
                    pass
            finally:
                self._active_processes.discard(process)
