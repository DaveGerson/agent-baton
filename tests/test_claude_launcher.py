"""Tests for agent_baton.core.runtime.claude_launcher.

Strategy:
- asyncio.run(_run()) wrappers for every async test (matches project convention).
- No external mocking libraries.  Subprocess calls are intercepted by replacing
  asyncio.create_subprocess_exec with a FakeProcess factory.
- shutil.which is replaced via monkeypatch so the constructor does not require
  a real ``claude`` binary.
- git helpers (_git_rev_parse, _git_diff_files) are tested by controlling the
  sequence of subprocess calls; a call counter tracks which invocation is which.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import pytest

import agent_baton.core.runtime.claude_launcher as _mod
from agent_baton.core.runtime.claude_launcher import ClaudeCodeConfig, ClaudeCodeLauncher
from agent_baton.core.runtime.launcher import AgentLauncher, LaunchResult


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeProcess:
    """Fake asyncio.subprocess.Process returned by fake_exec."""

    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self._killed = False

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:  # noqa: A002
        return self.stdout, self.stderr

    def kill(self) -> None:
        self._killed = True

    async def wait(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------

def _patch_which(monkeypatch: pytest.MonkeyPatch, found: bool = True) -> None:
    """Replace shutil.which so the constructor resolves the binary."""
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/claude" if found else None)


def _patch_subprocess(monkeypatch: pytest.MonkeyPatch, process: FakeProcess) -> None:
    """Patch asyncio.create_subprocess_exec to return a single FakeProcess."""
    async def fake_exec(*args: Any, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


def _patch_subprocess_sequence(
    monkeypatch: pytest.MonkeyPatch,
    processes: list[FakeProcess],
) -> None:
    """Patch asyncio.create_subprocess_exec to return processes in order.

    Each call consumes the next FakeProcess.  Raises IndexError if the list
    is exhausted — which itself flags an unexpected extra subprocess call.
    """
    call_box: list[int] = [0]

    async def fake_exec(*args: Any, **kwargs: Any) -> FakeProcess:
        idx = call_box[0]
        call_box[0] += 1
        return processes[idx]

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


def _launcher(monkeypatch: pytest.MonkeyPatch, config: ClaudeCodeConfig | None = None) -> ClaudeCodeLauncher:
    """Construct a ClaudeCodeLauncher with the claude binary patched away."""
    _patch_which(monkeypatch)
    return ClaudeCodeLauncher(config)


# ---------------------------------------------------------------------------
# JSON output factories
# ---------------------------------------------------------------------------

def _ok_json(result: str = "Task complete", input_tokens: int = 100, output_tokens: int = 50, duration_ms: int = 1234) -> bytes:
    return json.dumps({
        "result": result,
        "is_error": False,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "duration_ms": duration_ms,
    }).encode()


def _error_json(result: str = "Something went wrong") -> bytes:
    return json.dumps({
        "result": result,
        "is_error": True,
        "usage": {},
        "duration_ms": 500,
    }).encode()


# ===========================================================================
# TestClaudeCodeConfig
# ===========================================================================

class TestClaudeCodeConfig:
    def test_default_values_are_sensible(self) -> None:
        cfg = ClaudeCodeConfig()
        assert cfg.claude_path == "claude"
        assert cfg.default_timeout_seconds == 600.0
        assert cfg.max_retries == 3
        assert cfg.base_retry_delay == 5.0
        assert cfg.max_outcome_length == 4000
        assert cfg.prompt_file_threshold == 131_072
        assert "ANTHROPIC_API_KEY" in cfg.env_passthrough
        assert "opus" in cfg.model_timeouts
        assert "sonnet" in cfg.model_timeouts
        assert "haiku" in cfg.model_timeouts
        assert cfg.working_directory is None

    def test_custom_values_override_defaults(self) -> None:
        cfg = ClaudeCodeConfig(
            claude_path="/opt/bin/claude",
            default_timeout_seconds=30.0,
            max_retries=1,
            base_retry_delay=1.0,
            max_outcome_length=500,
        )
        assert cfg.claude_path == "/opt/bin/claude"
        assert cfg.default_timeout_seconds == 30.0
        assert cfg.max_retries == 1
        assert cfg.base_retry_delay == 1.0
        assert cfg.max_outcome_length == 500

    def test_to_dict_from_dict_roundtrip(self) -> None:
        original = ClaudeCodeConfig(
            default_timeout_seconds=120.0,
            max_retries=5,
            base_retry_delay=2.5,
            max_outcome_length=1000,
            prompt_file_threshold=65536,
            model_timeouts={"opus": 800.0, "sonnet": 400.0},
            env_passthrough=["ANTHROPIC_API_KEY", "AWS_PROFILE"],
        )
        d = original.to_dict()
        restored = ClaudeCodeConfig.from_dict(d)
        assert restored.default_timeout_seconds == original.default_timeout_seconds
        assert restored.max_retries == original.max_retries
        assert restored.base_retry_delay == original.base_retry_delay
        assert restored.max_outcome_length == original.max_outcome_length
        assert restored.prompt_file_threshold == original.prompt_file_threshold
        assert restored.model_timeouts == original.model_timeouts
        assert restored.env_passthrough == original.env_passthrough

    def test_to_dict_working_directory_none(self) -> None:
        d = ClaudeCodeConfig().to_dict()
        assert d["working_directory"] is None

    def test_to_dict_working_directory_set(self, tmp_path) -> None:
        from pathlib import Path
        cfg = ClaudeCodeConfig(working_directory=tmp_path)
        d = cfg.to_dict()
        assert d["working_directory"] == str(tmp_path)
        restored = ClaudeCodeConfig.from_dict(d)
        assert restored.working_directory == Path(str(tmp_path))

    def test_from_dict_with_empty_dict_uses_defaults(self) -> None:
        cfg = ClaudeCodeConfig.from_dict({})
        assert cfg.claude_path == "claude"
        assert cfg.max_retries == 3


# ===========================================================================
# TestClaudeCodeLauncherConstruction
# ===========================================================================

class TestClaudeCodeLauncherConstruction:
    def test_constructor_succeeds_when_claude_binary_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_which(monkeypatch, found=True)
        launcher = ClaudeCodeLauncher()
        assert launcher is not None
        assert launcher._claude_bin == "/usr/bin/claude"

    def test_constructor_raises_when_claude_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_which(monkeypatch, found=False)
        with pytest.raises(RuntimeError, match="claude binary not found"):
            ClaudeCodeLauncher()

    def test_custom_config_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_which(monkeypatch, found=True)
        cfg = ClaudeCodeConfig(max_retries=1, default_timeout_seconds=10.0)
        launcher = ClaudeCodeLauncher(cfg)
        assert launcher._config.max_retries == 1
        assert launcher._config.default_timeout_seconds == 10.0

    def test_git_bin_absent_is_non_fatal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If git is not present the launcher still constructs without error."""
        monkeypatch.setattr(
            "shutil.which",
            lambda x: "/usr/bin/claude" if x == "claude" else None,
        )
        launcher = ClaudeCodeLauncher()
        assert launcher._git_bin is None


# ===========================================================================
# TestClaudeCodeLauncherHappyPath
# ===========================================================================

class TestClaudeCodeLauncherHappyPath:
    def test_json_output_parsed_correctly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Structured JSON output → status=complete, tokens populated, duration from JSON."""
        _patch_subprocess(
            monkeypatch,
            FakeProcess(stdout=_ok_json(result="Done!", input_tokens=200, output_tokens=80, duration_ms=3000)),
        )
        launcher = _launcher(monkeypatch)
        # Suppress git rev-parse calls by removing git_bin
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "do something", "1.1")
            assert result.status == "complete"
            assert result.step_id == "1.1"
            assert result.agent_name == "backend"
            assert result.estimated_tokens == 280  # 200 + 80
            assert abs(result.duration_seconds - 3.0) < 0.01  # 3000ms / 1000
            assert "Done!" in result.outcome
            assert result.error == ""

        asyncio.run(_run())

    def test_raw_text_fallback_when_output_is_not_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-JSON stdout → status=complete, outcome contains the raw text."""
        raw = b"Task completed successfully with no structured output."
        _patch_subprocess(monkeypatch, FakeProcess(stdout=raw, returncode=0))
        launcher = _launcher(monkeypatch)
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "do something", "1.2")
            assert result.status == "complete"
            assert "Task completed successfully" in result.outcome
            assert result.estimated_tokens == 0  # raw path has no token data

        asyncio.run(_run())

    def test_git_changes_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When HEAD changes between pre and post launch, files_changed and commit_hash are set."""
        pre_commit_proc = FakeProcess(stdout=b"abc123\n", returncode=0)
        main_proc = FakeProcess(stdout=_ok_json(), returncode=0)
        post_commit_proc = FakeProcess(stdout=b"def456\n", returncode=0)
        diff_proc = FakeProcess(stdout=b"src/foo.py\nsrc/bar.py\n", returncode=0)

        _patch_subprocess_sequence(
            monkeypatch,
            [pre_commit_proc, main_proc, post_commit_proc, diff_proc],
        )
        launcher = _launcher(monkeypatch)
        # Ensure launcher thinks git is available
        launcher._git_bin = "/usr/bin/git"

        async def _run():
            result = await launcher.launch("backend", "sonnet", "add feature", "1.3")
            assert result.status == "complete"
            assert result.commit_hash == "def456"
            assert "src/foo.py" in result.files_changed
            assert "src/bar.py" in result.files_changed

        asyncio.run(_run())

    def test_no_git_changes_when_commit_same(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When pre and post commit are identical, files_changed and commit_hash stay empty."""
        same_hash = b"abc123\n"
        pre_commit_proc = FakeProcess(stdout=same_hash, returncode=0)
        main_proc = FakeProcess(stdout=_ok_json(), returncode=0)
        post_commit_proc = FakeProcess(stdout=same_hash, returncode=0)

        _patch_subprocess_sequence(
            monkeypatch,
            [pre_commit_proc, main_proc, post_commit_proc],
        )
        launcher = _launcher(monkeypatch)
        launcher._git_bin = "/usr/bin/git"

        async def _run():
            result = await launcher.launch("backend", "sonnet", "read only", "1.4")
            assert result.status == "complete"
            assert result.commit_hash == ""
            assert result.files_changed == []

        asyncio.run(_run())

    def test_duration_falls_back_to_wall_clock_when_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When JSON output has no duration_ms, duration_seconds uses wall-clock elapsed."""
        payload = json.dumps({
            "result": "ok",
            "is_error": False,
            "usage": {},
            # deliberately no duration_ms
        }).encode()
        _patch_subprocess(monkeypatch, FakeProcess(stdout=payload, returncode=0))
        launcher = _launcher(monkeypatch)
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "task", "1.5")
            assert result.status == "complete"
            # Wall-clock elapsed will be very small but non-negative
            assert result.duration_seconds >= 0.0

        asyncio.run(_run())


# ===========================================================================
# TestClaudeCodeLauncherFailures
# ===========================================================================

class TestClaudeCodeLauncherFailures:
    def test_nonzero_exit_code_returns_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Exit code 1 with stderr → status=failed, error populated from stderr."""
        proc = FakeProcess(stdout=b"", stderr=b"internal error", returncode=1)
        _patch_subprocess(monkeypatch, proc)
        launcher = _launcher(monkeypatch)
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "task", "2.1")
            assert result.status == "failed"
            assert result.error != ""

        asyncio.run(_run())

    def test_timeout_kills_process_and_returns_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When communicate() times out, the process is killed and status=failed."""
        killed_box: list[bool] = [False]

        class SlowProcess:
            returncode = None
            _killed = False

            async def communicate(self, input=None):  # noqa: A002
                # Simulate a process that never finishes within the timeout
                await asyncio.sleep(9999)
                return b"", b""

            def kill(self):
                killed_box[0] = True
                self.returncode = -9

            async def wait(self):
                pass

        async def fake_exec(*args: Any, **kwargs: Any):
            return SlowProcess()

        _patch_which(monkeypatch)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        cfg = ClaudeCodeConfig(
            max_retries=0,
            default_timeout_seconds=0.01,  # 10ms — will fire immediately
            model_timeouts={},
        )
        launcher = ClaudeCodeLauncher(cfg)
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "long task", "2.2")
            assert result.status == "failed"
            assert killed_box[0] is True
            error_lower = result.error.lower()
            assert "timed out" in error_lower or "timeout" in error_lower

        asyncio.run(_run())

    def test_malformed_json_falls_back_to_raw_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Partial/corrupt JSON stdout → raw text fallback, not an exception."""
        proc = FakeProcess(stdout=b"{not valid json!!!", returncode=0)
        _patch_subprocess(monkeypatch, proc)
        launcher = _launcher(monkeypatch)
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "task", "2.3")
            # Raw fallback path with exit code 0 → complete
            assert result.status == "complete"
            assert "{not valid json" in result.outcome

        asyncio.run(_run())

    def test_claude_authentication_error_returns_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Authentication failure reported in stderr → status=failed."""
        proc = FakeProcess(
            stdout=b"",
            stderr=b"Authentication failed: invalid API key",
            returncode=1,
        )
        _patch_subprocess(monkeypatch, proc)
        launcher = _launcher(monkeypatch)
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "task", "2.4")
            assert result.status == "failed"
            assert result.error != ""

        asyncio.run(_run())

    def test_json_is_error_true_returns_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """JSON payload with is_error=True → status=failed even if exit code is 0."""
        proc = FakeProcess(stdout=_error_json("API error"), returncode=0)
        _patch_subprocess(monkeypatch, proc)
        launcher = _launcher(monkeypatch)
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "task", "2.5")
            assert result.status == "failed"
            assert "API error" in result.error

        asyncio.run(_run())

    def test_subprocess_oserror_returns_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the subprocess itself fails to start (OSError), status=failed."""
        async def failing_exec(*args: Any, **kwargs: Any):
            raise OSError("No such file or directory")

        _patch_which(monkeypatch)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", failing_exec)
        launcher = ClaudeCodeLauncher()
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "task", "2.6")
            assert result.status == "failed"
            assert "Failed to start" in result.error

        asyncio.run(_run())

    def test_outcome_truncated_to_max_length(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """outcome is capped at max_outcome_length characters."""
        long_result = "x" * 8000
        payload = json.dumps({
            "result": long_result,
            "is_error": False,
            "usage": {},
            "duration_ms": 100,
        }).encode()
        proc = FakeProcess(stdout=payload, returncode=0)
        _patch_subprocess(monkeypatch, proc)
        cfg = ClaudeCodeConfig(max_outcome_length=500)
        launcher = _launcher(monkeypatch, cfg)
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "task", "2.7")
            assert len(result.outcome) <= 500

        asyncio.run(_run())


# ===========================================================================
# TestClaudeCodeLauncherRetry
# ===========================================================================

class TestClaudeCodeLauncherRetry:
    def test_rate_limit_retries_and_eventually_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First call returns rate-limit error; second call succeeds."""
        rate_limit_proc = FakeProcess(
            stdout=b"",
            stderr=b"rate limit exceeded (429)",
            returncode=1,
        )
        success_proc = FakeProcess(stdout=_ok_json(result="retry worked"), returncode=0)

        _patch_subprocess_sequence(monkeypatch, [rate_limit_proc, success_proc])

        # Disable actual sleep so the test is fast
        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        cfg = ClaudeCodeConfig(max_retries=2, base_retry_delay=0.0)
        launcher = _launcher(monkeypatch, cfg)
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "task", "3.1")
            assert result.status == "complete"
            assert "retry worked" in result.outcome
            assert len(sleep_calls) == 1  # exactly one retry sleep

        asyncio.run(_run())

    def test_rate_limit_exhausted_after_max_retries_returns_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All attempts return rate-limit; after max_retries exhausted → failed."""
        # max_retries=2 means we try once initially + 2 retries = 3 total attempts
        procs = [
            FakeProcess(stdout=b"", stderr=b"rate limit exceeded (429)", returncode=1)
            for _ in range(4)  # more than enough
        ]
        _patch_subprocess_sequence(monkeypatch, procs)

        async def fake_sleep(delay: float) -> None:
            pass

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        cfg = ClaudeCodeConfig(max_retries=2, base_retry_delay=0.0)
        launcher = _launcher(monkeypatch, cfg)
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "task", "3.2")
            assert result.status == "failed"
            error_lower = result.error.lower()
            assert "rate limit" in error_lower or "429" in error_lower

        asyncio.run(_run())

    def test_retry_delay_uses_exponential_backoff(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Retry delays follow base * 2^(attempt-1) pattern."""
        procs = [
            FakeProcess(stdout=b"", stderr=b"rate limit exceeded (429)", returncode=1),
            FakeProcess(stdout=b"", stderr=b"rate limit exceeded (429)", returncode=1),
            FakeProcess(stdout=_ok_json(), returncode=0),
        ]
        _patch_subprocess_sequence(monkeypatch, procs)

        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        cfg = ClaudeCodeConfig(max_retries=3, base_retry_delay=4.0)
        launcher = _launcher(monkeypatch, cfg)
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "task", "3.3")
            assert result.status == "complete"
            assert len(sleep_calls) == 2
            # First retry delay: 4.0 * 2^0 = 4.0
            assert sleep_calls[0] == pytest.approx(4.0)
            # Second retry delay: 4.0 * 2^1 = 8.0
            assert sleep_calls[1] == pytest.approx(8.0)

        asyncio.run(_run())


# ===========================================================================
# TestClaudeCodeLauncherSecurity
# ===========================================================================

class TestClaudeCodeLauncherSecurity:
    def test_env_filtering_only_whitelisted_vars_forwarded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_build_env must never pass through non-whitelisted environment variables."""
        # Inject a sensitive variable that must NOT appear in the child env
        monkeypatch.setenv("SECRET_DB_PASSWORD", "hunter2")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("HOME", "/home/testuser")

        launcher = _launcher(monkeypatch)
        env = launcher._build_env()

        # Whitelisted key must be present
        assert env.get("ANTHROPIC_API_KEY") == "sk-test"
        # HOME is always forwarded
        assert "HOME" in env

        # Non-whitelisted key must be absent
        assert "SECRET_DB_PASSWORD" not in env

        # The result must be a fresh dict — not the same object as os.environ
        assert env is not os.environ

    def test_env_filtering_does_not_copy_os_environ(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The child env dict must contain only PATH, HOME, and whitelisted keys."""
        # Inject several arbitrary env vars
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "some_test")
        monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
        monkeypatch.setenv("PYTHONPATH", "/some/path")

        launcher = _launcher(monkeypatch, ClaudeCodeConfig(env_passthrough=[]))
        env = launcher._build_env()

        # With empty passthrough, only PATH and HOME should appear
        unexpected = set(env.keys()) - {"PATH", "HOME"}
        assert unexpected == set(), f"Unexpected env vars leaked: {unexpected}"

    def test_shell_metacharacters_passed_verbatim_not_interpreted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AUDITOR REQUIREMENT: prompt containing $(), backticks, pipes, and
        semicolons must appear as a literal string in the subprocess args — no
        shell interpolation.

        This verifies that create_subprocess_exec is used (not create_subprocess_shell)
        and that the prompt is a separate list element, never f-string interpolated.
        """
        dangerous_prompt = "$(whoami); echo `id` | cat; rm -rf /tmp/foo; echo $HOME"

        captured_args: list[tuple] = []

        async def capturing_exec(*args: Any, **kwargs: Any) -> FakeProcess:
            captured_args.append(args)
            return FakeProcess(stdout=_ok_json(), returncode=0)

        _patch_which(monkeypatch)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", capturing_exec)

        launcher = ClaudeCodeLauncher()
        launcher._git_bin = None

        async def _run():
            await launcher.launch("backend", "sonnet", dangerous_prompt, "sec.1")

        asyncio.run(_run())

        assert len(captured_args) == 1, "Expected exactly one subprocess call"
        call_argv = captured_args[0]  # tuple of positional args to create_subprocess_exec

        # The dangerous prompt must appear as a single, unmodified element
        assert dangerous_prompt in call_argv, (
            f"Dangerous prompt not found verbatim in subprocess args.\n"
            f"Args were: {call_argv}"
        )

        # Verify the prompt is preceded by the -p flag (not interpolated elsewhere)
        argv_list = list(call_argv)
        p_idx = argv_list.index("-p")
        assert argv_list[p_idx + 1] == dangerous_prompt, (
            "Prompt must be the element immediately after '-p', not interpolated"
        )

    def test_prompt_via_stdin_for_large_payloads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Prompts exceeding prompt_file_threshold must NOT appear in argv at all."""
        captured_args: list[tuple] = []

        async def capturing_exec(*args: Any, **kwargs: Any) -> FakeProcess:
            captured_args.append(args)
            return FakeProcess(stdout=_ok_json(), returncode=0)

        _patch_which(monkeypatch)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", capturing_exec)

        cfg = ClaudeCodeConfig(prompt_file_threshold=10)  # very low threshold
        launcher = ClaudeCodeLauncher(cfg)
        launcher._git_bin = None

        large_prompt = "x" * 200  # exceeds the 10-byte threshold

        async def _run():
            result = await launcher.launch("backend", "sonnet", large_prompt, "sec.2")
            assert result.status == "complete"

        asyncio.run(_run())

        assert len(captured_args) == 1
        call_argv = captured_args[0]
        # Large prompt must NOT be in argv (delivered via stdin instead)
        assert large_prompt not in call_argv
        assert "-p" not in call_argv


# ===========================================================================
# TestClaudeCodeLauncherProtocol
# ===========================================================================

class TestClaudeCodeLauncherProtocol:
    def test_satisfies_agent_launcher_protocol(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ClaudeCodeLauncher must structurally satisfy the AgentLauncher protocol."""
        launcher = _launcher(monkeypatch)

        # Structural duck-type check: the protocol requires a `launch` async method.
        assert hasattr(launcher, "launch"), "ClaudeCodeLauncher must have a 'launch' method"
        assert asyncio.iscoroutinefunction(launcher.launch), (
            "'launch' must be a coroutine function"
        )

        # Runtime isinstance check via the Protocol.
        # This works because AgentLauncher uses structural subtyping (Protocol).
        import inspect
        sig = inspect.signature(launcher.launch)
        params = list(sig.parameters.keys())
        # Protocol requires: agent_name, model, prompt, step_id (with default)
        assert "agent_name" in params
        assert "model" in params
        assert "prompt" in params
        assert "step_id" in params

    def test_launch_return_type_is_launch_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """launch() must return a LaunchResult instance."""
        _patch_subprocess(monkeypatch, FakeProcess(stdout=_ok_json(), returncode=0))
        launcher = _launcher(monkeypatch)
        launcher._git_bin = None

        async def _run():
            result = await launcher.launch("backend", "sonnet", "task", "proto.1")
            assert isinstance(result, LaunchResult)

        asyncio.run(_run())


# ===========================================================================
# TestClaudeCodeLauncherResolveTimeout
# ===========================================================================

class TestClaudeCodeLauncherResolveTimeout:
    def test_exact_model_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = ClaudeCodeConfig(model_timeouts={"opus": 900.0, "sonnet": 600.0, "haiku": 300.0})
        launcher = _launcher(monkeypatch, cfg)
        assert launcher._resolve_timeout("opus") == 900.0
        assert launcher._resolve_timeout("sonnet") == 600.0
        assert launcher._resolve_timeout("haiku") == 300.0

    def test_substring_model_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = ClaudeCodeConfig(model_timeouts={"sonnet": 600.0})
        launcher = _launcher(monkeypatch, cfg)
        # "claude-sonnet-4" contains "sonnet"
        assert launcher._resolve_timeout("claude-sonnet-4") == 600.0

    def test_unknown_model_uses_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = ClaudeCodeConfig(
            model_timeouts={"opus": 900.0},
            default_timeout_seconds=42.0,
        )
        launcher = _launcher(monkeypatch, cfg)
        assert launcher._resolve_timeout("unknown-model") == 42.0
