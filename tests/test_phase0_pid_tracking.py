"""Regression tests for Fix 0.2 — PID tracking and cleanup in ClaudeCodeLauncher.

Verifies:
  1. _active_processes is a set, initialised empty on construction.
  2. cleanup() method exists and is callable.
  3. cleanup() calls terminate() on each active process, then kill() on timeout.
  4. cleanup() is a no-op when the set is empty.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ClaudeCodeLauncher validates the claude binary at construction time, so we
# must patch shutil.which to avoid a RuntimeError in tests that don't have
# claude installed.
_LAUNCHER_MOD = "agent_baton.core.runtime.claude_launcher"


def _make_launcher():
    """Construct a ClaudeCodeLauncher with the claude binary patched out."""
    with patch(f"{_LAUNCHER_MOD}.shutil.which", return_value="/usr/bin/claude"):
        from agent_baton.core.runtime.claude_launcher import ClaudeCodeLauncher
        return ClaudeCodeLauncher()


class TestActiveProcessesRegistry:
    def test_active_processes_set_exists(self) -> None:
        """_active_processes must be a set attribute on the launcher instance."""
        launcher = _make_launcher()
        assert hasattr(launcher, "_active_processes"), (
            "_active_processes set must exist on ClaudeCodeLauncher (Fix 0.2)"
        )

    def test_active_processes_is_a_set(self) -> None:
        """_active_processes must be a set (not list or dict)."""
        launcher = _make_launcher()
        assert isinstance(launcher._active_processes, set), (
            "_active_processes must be of type set"
        )

    def test_active_processes_empty_at_init(self) -> None:
        """_active_processes must be empty when the launcher is first constructed."""
        launcher = _make_launcher()
        assert len(launcher._active_processes) == 0, (
            "_active_processes must be empty at construction time"
        )


class TestCleanupMethod:
    def test_cleanup_method_exists(self) -> None:
        """cleanup() must exist as a method on ClaudeCodeLauncher."""
        launcher = _make_launcher()
        assert hasattr(launcher, "cleanup"), (
            "cleanup() method must exist on ClaudeCodeLauncher (Fix 0.2)"
        )
        assert callable(launcher.cleanup), "cleanup must be callable"

    def test_cleanup_is_noop_when_empty(self) -> None:
        """cleanup() with no active processes must not raise."""
        launcher = _make_launcher()
        asyncio.run(launcher.cleanup())  # should complete without error

    def test_cleanup_calls_terminate_on_active_processes(self) -> None:
        """cleanup() must call terminate() on each tracked process."""
        launcher = _make_launcher()

        # Create a mock process that exits quickly after terminate().
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.terminate = MagicMock()
        # wait() returns immediately (process exited)
        mock_proc.wait = AsyncMock(return_value=0)

        launcher._active_processes.add(mock_proc)

        asyncio.run(launcher.cleanup())

        mock_proc.terminate.assert_called_once(), (
            "cleanup() must call terminate() on active processes"
        )

    def test_cleanup_kills_process_that_does_not_exit_after_terminate(self) -> None:
        """cleanup() must send SIGKILL when a process does not exit after SIGTERM."""
        launcher = _make_launcher()

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()

        call_count = 0

        async def _wait_that_times_out():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call (after terminate) times out.
                raise asyncio.TimeoutError
            # Second call (after kill) completes immediately.
            return 0

        mock_proc.wait = _wait_that_times_out

        launcher._active_processes.add(mock_proc)

        # Patch asyncio.wait_for so we control the timeout behaviour.
        original_wait_for = asyncio.wait_for

        async def _patched_wait_for(coro, timeout):
            # Delegate to the mock's wait() directly without a real timeout.
            return await coro

        with patch("asyncio.wait_for", side_effect=_patched_wait_for):
            asyncio.run(launcher.cleanup())

        mock_proc.kill.assert_called_once(), (
            "cleanup() must call kill() when terminate() doesn't stop the process"
        )

    def test_cleanup_removes_process_from_active_set(self) -> None:
        """After cleanup(), _active_processes must be empty."""
        launcher = _make_launcher()

        mock_proc = MagicMock()
        mock_proc.pid = 11111
        mock_proc.terminate = MagicMock()
        mock_proc.wait = AsyncMock(return_value=0)

        launcher._active_processes.add(mock_proc)
        asyncio.run(launcher.cleanup())

        assert len(launcher._active_processes) == 0, (
            "cleanup() must remove processes from _active_processes after termination"
        )

    def test_cleanup_handles_already_exited_process(self) -> None:
        """cleanup() must handle ProcessLookupError from terminate() gracefully."""
        launcher = _make_launcher()

        mock_proc = MagicMock()
        mock_proc.pid = 22222
        mock_proc.terminate = MagicMock(side_effect=ProcessLookupError)
        mock_proc.wait = AsyncMock(return_value=0)

        launcher._active_processes.add(mock_proc)

        # Should not raise even when the process is already gone.
        asyncio.run(launcher.cleanup())
