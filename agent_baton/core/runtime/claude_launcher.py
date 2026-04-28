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
from agent_baton.core.runtime._redaction import (
    _REDACT_PATTERNS,
    redact_sensitive as _redact_sensitive,
)
from agent_baton.core.runtime.launcher import LaunchResult
from agent_baton.models.agent import AgentDefinition

logger = logging.getLogger(__name__)

# Keep the old name as an alias so any external callers do not break.
_API_KEY_RE = _REDACT_PATTERNS[0][0]

# ---------------------------------------------------------------------------
# Prompt-cache optimisation flag detection
# ---------------------------------------------------------------------------

_EXCLUDE_FLAG = "--exclude-dynamic-system-prompt-sections"
_exclude_flag_supported: bool | None = None  # None = not yet probed


def _supports_exclude_flag() -> bool:
    """Return True if the installed ``claude`` CLI supports
    ``--exclude-dynamic-system-prompt-sections``.

    The result is cached after the first call so we only probe once per
    process lifetime.  The probe runs ``claude --print --help`` and checks
    whether the flag name appears in the output; failure modes (binary not
    found, timeout, non-zero exit) are treated as "not supported" so they
    never block a dispatch.
    """
    global _exclude_flag_supported
    if _exclude_flag_supported is not None:
        return _exclude_flag_supported

    claude_bin = shutil.which("claude")
    if claude_bin is None:
        _exclude_flag_supported = False
        return False

    try:
        import subprocess  # noqa: PLC0415 — stdlib, intentional lazy import
        result = subprocess.run(
            [claude_bin, "--print", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        combined = result.stdout + result.stderr
        _exclude_flag_supported = _EXCLUDE_FLAG in combined
    except Exception:  # noqa: BLE001
        _exclude_flag_supported = False

    logger.debug(
        "ClaudeCodeLauncher: %s is %s",
        _EXCLUDE_FLAG,
        "supported" if _exclude_flag_supported else "NOT supported",
    )
    return _exclude_flag_supported


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
    """Maximum characters kept from the agent outcome *inline* string.

    Outputs longer than ``max_outcome_length - 200`` are truncated; the
    full text is preserved on disk under ``outcome_spillover_dir_relative``
    and referenced via :attr:`LaunchResult.outcome_spillover_path`.
    """

    outcome_spillover_dir_relative: str = "outcome-spillover"
    """Subdirectory (relative to the per-task execution dir) where full
    outcome text is written when truncation occurs.  Created lazily."""

    execution_dir: Path | None = None
    """Per-task execution directory used as the parent for the spillover
    subdirectory.  When ``None`` the launcher falls back to
    ``$BATON_TEAM_CONTEXT_ROOT/executions/$BATON_TASK_ID`` (or the
    canonical ``.claude/team-context/executions/<task_id>`` path) at the
    time of writing."""

    prompt_file_threshold: int = 131_072
    """Byte threshold above which the prompt is delivered via stdin rather
    than the ``-p`` flag (128 KB)."""

    env_passthrough: list[str] = field(
        default_factory=lambda: list(_DEFAULT_ENV_PASSTHROUGH)
    )
    """Environment variable names forwarded to the subprocess.  Only these
    variables (plus ``PATH`` and ``HOME``) are included in the child
    environment."""

    bead_db_path: Path | None = None
    """Optional path to the project ``baton.db``.  When set and an agent
    outcome is silently truncated by ``max_outcome_length``, a ``warning``
    bead tagged ``outcome-truncated`` is filed so the operator can see the
    data loss.  When ``None`` the bead is skipped (warning is still logged)."""

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
            "outcome_spillover_dir_relative": self.outcome_spillover_dir_relative,
            "execution_dir": self.execution_dir.as_posix() if self.execution_dir else None,
            "prompt_file_threshold": self.prompt_file_threshold,
            "env_passthrough": list(self.env_passthrough),
            "bead_db_path": self.bead_db_path.as_posix() if self.bead_db_path else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClaudeCodeConfig:
        """Deserialise from a plain dict."""
        wd = data.get("working_directory")
        ed = data.get("execution_dir")
        bdp = data.get("bead_db_path")
        return cls(
            claude_path=data.get("claude_path", "claude"),
            working_directory=Path(wd) if wd else None,
            default_timeout_seconds=float(data.get("default_timeout_seconds", 600.0)),
            model_timeouts=dict(data.get("model_timeouts", _DEFAULT_MODEL_TIMEOUTS)),
            max_retries=int(data.get("max_retries", 3)),
            base_retry_delay=float(data.get("base_retry_delay", 5.0)),
            max_outcome_length=int(data.get("max_outcome_length", 4000)),
            outcome_spillover_dir_relative=str(
                data.get("outcome_spillover_dir_relative", "outcome-spillover")
            ),
            execution_dir=Path(ed) if ed else None,
            prompt_file_threshold=int(data.get("prompt_file_threshold", 131_072)),
            env_passthrough=list(data.get("env_passthrough", _DEFAULT_ENV_PASSTHROUGH)),
            bead_db_path=Path(bdp) if bdp else None,
        )


# ---------------------------------------------------------------------------
# Outcome spillover
# ---------------------------------------------------------------------------

# Headroom reserved at the end of the inline outcome for the spillover
# breadcrumb.  When raw outcome length exceeds (max_outcome_length -
# _SPILLOVER_BREADCRUMB_HEADROOM), spillover-on-truncate is triggered.
_SPILLOVER_BREADCRUMB_HEADROOM: int = 200


def _resolve_execution_dir(config: ClaudeCodeConfig) -> Path | None:
    """Return the per-task execution directory used as the spillover root.

    Resolution order:
    1. ``config.execution_dir`` if explicitly set.
    2. ``$BATON_TASK_ID`` + ``$BATON_TEAM_CONTEXT_ROOT`` (or working_directory
       / ``.claude/team-context``) — canonical layout
       ``<root>/executions/<task_id>``.
    3. ``None`` if no task id is available (caller should skip spillover).
    """
    if config.execution_dir is not None:
        return config.execution_dir
    task_id = os.environ.get("BATON_TASK_ID", "").strip()
    if not task_id:
        return None
    root_env = os.environ.get("BATON_TEAM_CONTEXT_ROOT", "").strip()
    if root_env:
        root = Path(root_env)
    else:
        base = config.working_directory or Path.cwd()
        root = base / ".claude" / "team-context"
    return root / "executions" / task_id


def _write_outcome_spillover(
    *,
    full_text: str,
    step_id: str,
    config: ClaudeCodeConfig,
) -> tuple[str, str] | None:
    """Persist the FULL untruncated outcome to disk.

    Returns a ``(relative_path, breadcrumb_outcome)`` tuple on success, where
    ``relative_path`` is the spillover file path relative to the execution
    dir and ``breadcrumb_outcome`` is the inline string that should replace
    ``outcome`` in the LaunchResult.  Returns ``None`` when the execution
    directory cannot be resolved or the write fails (caller falls back to
    legacy truncation).
    """
    exec_dir = _resolve_execution_dir(config)
    if exec_dir is None:
        return None

    spillover_dir = exec_dir / config.outcome_spillover_dir_relative
    try:
        spillover_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # noqa: BLE001
        logger.warning(
            "Could not create spillover dir %s: %s — falling back to truncation.",
            spillover_dir,
            exc,
        )
        return None

    safe_step_id = re.sub(r"[^A-Za-z0-9._-]", "_", step_id) or "unknown"
    timestamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    fname = f"step-{safe_step_id}-{timestamp}.md"
    target = spillover_dir / fname

    try:
        target.write_text(full_text, encoding="utf-8")
    except OSError as exc:  # noqa: BLE001
        logger.warning(
            "Could not write spillover file %s: %s — falling back to truncation.",
            target,
            exc,
        )
        return None

    rel_path = f"{config.outcome_spillover_dir_relative}/{fname}"
    head_chars = max(0, config.max_outcome_length - _SPILLOVER_BREADCRUMB_HEADROOM)
    head = full_text[:head_chars]
    breadcrumb = (
        f"[TRUNCATED — full output: {rel_path} "
        f"({len(full_text.encode('utf-8'))} bytes total)]\n\n"
        f"--- First {head_chars} chars ---\n"
        f"{head}"
    )
    return rel_path, breadcrumb


def _truncate_or_spillover(
    *,
    raw_text: str,
    step_id: str,
    config: ClaudeCodeConfig,
) -> tuple[str, str]:
    """Apply the inline cap; if exceeded, write spillover and return the
    breadcrumb outcome.

    Returns ``(outcome_string, spillover_relative_path)``.  ``spillover_relative_path``
    is empty when no spillover was needed (or could not be written).
    """
    inline_cap = config.max_outcome_length
    threshold = inline_cap - _SPILLOVER_BREADCRUMB_HEADROOM
    if len(raw_text) <= threshold:
        # Below the headroom-adjusted threshold: legacy behavior, no spillover.
        return raw_text[:inline_cap], ""

    spilled = _write_outcome_spillover(
        full_text=raw_text, step_id=step_id, config=config
    )
    if spilled is None:
        # Best-effort fallback: legacy hard truncation.
        return raw_text[:inline_cap], ""
    rel_path, breadcrumb = spilled
    return breadcrumb, rel_path


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
        has_system_prompt = (
            agent is not None
            and bool(agent.instructions and agent.instructions.strip())
        )
        cmd: list[str] = [
            self._claude_bin,
            "--print",
            "--model", model,
            "--output-format", "json",
        ]
        # Improve cross-dispatch prompt-cache reuse by moving per-machine
        # sections (cwd, env info, git status) out of the system prompt.
        # The flag is documented as a no-op when --system-prompt is supplied,
        # so we only add it when no custom system prompt will be injected.
        if not has_system_prompt and _supports_exclude_flag():
            cmd.append(_EXCLUDE_FLAG)
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

    def _warn_truncation(
        self,
        agent_name: str,
        step_id: str,
        bytes_attempted: int,
        bytes_written: int,
    ) -> None:
        """Log a WARNING and file a ``warning`` bead when outcome is truncated.

        Called whenever the raw outcome text exceeds ``max_outcome_length``.
        The truncated outcome is still returned by the caller — this method
        only makes the data loss *visible*.

        Best-effort: any exception during bead filing is caught and logged at
        DEBUG level so a BeadStore failure never cascades into a launcher
        failure.

        Args:
            agent_name: Name of the agent whose outcome was truncated.
            step_id: Step identifier within the execution.
            bytes_attempted: Length of the full (pre-truncation) outcome text.
            bytes_written: Length of the truncated outcome text that was kept.
        """
        logger.warning(
            "Outcome truncated for agent=%r step=%r: attempted=%d chars, kept=%d chars "
            "(max_outcome_length=%d). Data loss is silent without this warning.",
            agent_name,
            step_id,
            bytes_attempted,
            bytes_written,
            self._config.max_outcome_length,
        )

        if self._config.bead_db_path is None:
            return

        try:
            from datetime import datetime, timezone

            from agent_baton.core.engine.bead_store import BeadStore
            from agent_baton.models.bead import Bead, _generate_bead_id  # type: ignore[attr-defined]

            content = (
                f"Outcome truncated for agent={agent_name!r} step={step_id!r}. "
                f"Attempted {bytes_attempted} chars, kept {bytes_written} chars "
                f"(limit={self._config.max_outcome_length})."
            )
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            bead_id = _generate_bead_id(
                task_id="",
                step_id=step_id,
                content=content,
                timestamp=ts,
                bead_count=0,
            )
            bead = Bead(
                bead_id=bead_id,
                task_id="",
                step_id=step_id,
                agent_name=agent_name,
                bead_type="warning",
                content=content,
                confidence="high",
                scope="step",
                tags=["outcome-truncated"],
                source="agent-signal",
                created_at=ts,
            )
            store = BeadStore(self._config.bead_db_path)
            store.write(bead)
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "ClaudeCodeLauncher._warn_truncation: bead write failed (non-fatal): %s", exc
            )

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
            redacted = _redact_sensitive(result_text)
            outcome, spillover_path = _truncate_or_spillover(
                raw_text=redacted, step_id=step_id, config=self._config
            )
            # bd-e78c: if truncation happened without spillover (write failed
            # or spillover disabled), file a warning bead so the loss is visible.
            if len(redacted) > self._config.max_outcome_length and not spillover_path:
                self._warn_truncation(
                    agent_name=agent_name,
                    step_id=step_id,
                    bytes_attempted=len(redacted),
                    bytes_written=len(outcome),
                )

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
                    outcome_spillover_path=spillover_path,
                )

            return LaunchResult(
                step_id=step_id,
                agent_name=agent_name,
                status="complete",
                outcome=outcome,
                duration_seconds=duration_seconds,
                estimated_tokens=estimated_tokens,
                outcome_spillover_path=spillover_path,
            )

        # --- Raw text fallback -----------------------------------------------
        # A5: redact sensitive patterns from raw stdout before storage.
        redacted_raw = _redact_sensitive(stdout_text)
        outcome, spillover_path = _truncate_or_spillover(
            raw_text=redacted_raw, step_id=step_id, config=self._config
        )
        # bd-e78c: visible warning when truncation happened without spillover.
        if len(redacted_raw) > self._config.max_outcome_length and not spillover_path:
            self._warn_truncation(
                agent_name=agent_name,
                step_id=step_id,
                bytes_attempted=len(redacted_raw),
                bytes_written=len(outcome),
            )

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
                outcome_spillover_path=spillover_path,
            )

        return LaunchResult(
            step_id=step_id,
            agent_name=agent_name,
            status="complete",
            outcome=outcome,
            duration_seconds=elapsed,
            estimated_tokens=raw_estimated_tokens,
            outcome_spillover_path=spillover_path,
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
