"""Tests for bd-37a9: ClaudeCodeLauncher env injection on cwd_override.

Two test classes:
  - TestLauncherInjectsBatonEnvWhenCwdOverrideSet: when cwd_override is
    provided to launch(), the subprocess env must contain BATON_DB_PATH,
    BATON_TASK_ID (if set), and BATON_TEAM_CONTEXT_ROOT.
  - TestLauncherBatonEnvInDefaultPassthrough: the three BATON_* vars are
    in _DEFAULT_ENV_PASSTHROUGH so they propagate even without cwd_override.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_baton.core.runtime.claude_launcher import (
    ClaudeCodeConfig,
    ClaudeCodeLauncher,
    _DEFAULT_ENV_PASSTHROUGH,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a minimal project layout with .claude/team-context/baton.db."""
    (tmp_path / ".claude" / "team-context").mkdir(parents=True)
    (tmp_path / ".claude" / "team-context" / "baton.db").write_bytes(b"")
    return tmp_path


@pytest.fixture
def worktree_path(tmp_project: Path) -> Path:
    """Simulate an isolated worktree directory inside the project."""
    wt = tmp_project / ".claude" / "worktrees" / "task-1" / "1.1"
    wt.mkdir(parents=True)
    return wt


# ---------------------------------------------------------------------------
# TestLauncherBatonEnvInDefaultPassthrough
# ---------------------------------------------------------------------------


class TestLauncherBatonEnvInDefaultPassthrough:
    """bd-37a9: three BATON_* vars must be in _DEFAULT_ENV_PASSTHROUGH."""

    def test_baton_db_path_in_default_passthrough(self) -> None:
        assert "BATON_DB_PATH" in _DEFAULT_ENV_PASSTHROUGH, (
            "BATON_DB_PATH must be in _DEFAULT_ENV_PASSTHROUGH (bd-37a9)"
        )

    def test_baton_task_id_in_default_passthrough(self) -> None:
        assert "BATON_TASK_ID" in _DEFAULT_ENV_PASSTHROUGH, (
            "BATON_TASK_ID must be in _DEFAULT_ENV_PASSTHROUGH (bd-37a9)"
        )

    def test_baton_team_context_root_in_default_passthrough(self) -> None:
        assert "BATON_TEAM_CONTEXT_ROOT" in _DEFAULT_ENV_PASSTHROUGH, (
            "BATON_TEAM_CONTEXT_ROOT must be in _DEFAULT_ENV_PASSTHROUGH (bd-37a9)"
        )


# ---------------------------------------------------------------------------
# TestLauncherInjectsBatonEnvWhenCwdOverrideSet
# ---------------------------------------------------------------------------


class TestLauncherInjectsBatonEnvWhenCwdOverrideSet:
    """bd-37a9: when cwd_override is set, the three BATON_* vars must be
    injected into the subprocess env even if they are not in os.environ."""

    def _run_launch_capture_env(
        self,
        *,
        tmp_project: Path,
        worktree_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        task_id: str = "task-test-bd37a9",
    ) -> dict[str, str]:
        """Run launcher.launch() with subprocess mocked; return env passed."""
        config = ClaudeCodeConfig(working_directory=tmp_project)
        launcher = ClaudeCodeLauncher(config)

        monkeypatch.setenv("BATON_TASK_ID", task_id)
        # Clear BATON_DB_PATH / BATON_TEAM_CONTEXT_ROOT so we exercise auto-resolution
        monkeypatch.delenv("BATON_DB_PATH", raising=False)
        monkeypatch.delenv("BATON_TEAM_CONTEXT_ROOT", raising=False)

        captured_env: dict[str, str] = {}

        async def _fake_run_once(**kwargs: object) -> "LaunchResult":  # type: ignore[name-defined]
            nonlocal captured_env
            captured_env = dict(kwargs.get("env") or {})
            from agent_baton.core.runtime.claude_launcher import LaunchResult
            return LaunchResult(
                status="complete",
                outcome="ok",
                agent_name="backend-engineer",
                step_id="1.1",
                duration_seconds=0.1,
            )

        with patch.object(launcher, "_run_once", side_effect=_fake_run_once), \
             patch.object(launcher, "_git_rev_parse", new=AsyncMock(return_value=None)):
            asyncio.run(
                launcher.launch(
                    agent_name="backend-engineer",
                    model="sonnet",
                    prompt="do something",
                    step_id="1.1",
                    cwd_override=str(worktree_path),
                )
            )

        return captured_env

    def test_baton_db_path_injected(
        self,
        tmp_project: Path,
        worktree_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = self._run_launch_capture_env(
            tmp_project=tmp_project,
            worktree_path=worktree_path,
            monkeypatch=monkeypatch,
        )
        assert "BATON_DB_PATH" in env, (
            "BATON_DB_PATH must be injected into the subprocess env when "
            "cwd_override is set (bd-37a9)"
        )
        # Must point to the parent's baton.db, not a worktree-local one
        db_path = Path(env["BATON_DB_PATH"])
        expected = tmp_project / ".claude" / "team-context" / "baton.db"
        assert db_path == expected, (
            f"BATON_DB_PATH must point to the parent baton.db at {expected}; "
            f"got {db_path}"
        )

    def test_baton_task_id_injected(
        self,
        tmp_project: Path,
        worktree_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = self._run_launch_capture_env(
            tmp_project=tmp_project,
            worktree_path=worktree_path,
            monkeypatch=monkeypatch,
            task_id="task-xyz-123",
        )
        assert env.get("BATON_TASK_ID") == "task-xyz-123", (
            "BATON_TASK_ID must be injected from the current process env (bd-37a9)"
        )

    def test_baton_team_context_root_injected(
        self,
        tmp_project: Path,
        worktree_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = self._run_launch_capture_env(
            tmp_project=tmp_project,
            worktree_path=worktree_path,
            monkeypatch=monkeypatch,
        )
        assert "BATON_TEAM_CONTEXT_ROOT" in env, (
            "BATON_TEAM_CONTEXT_ROOT must be injected when cwd_override is set (bd-37a9)"
        )
        expected_root = tmp_project / ".claude" / "team-context"
        actual_root = Path(env["BATON_TEAM_CONTEXT_ROOT"])
        assert actual_root == expected_root, (
            f"BATON_TEAM_CONTEXT_ROOT must point to {expected_root}; got {actual_root}"
        )

    def test_no_injection_without_cwd_override(
        self,
        tmp_project: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without cwd_override the override injection path is skipped.
        BATON_* vars are still forwarded if they happen to be in env, but the
        active injection logic must not fire."""
        config = ClaudeCodeConfig(working_directory=tmp_project)
        launcher = ClaudeCodeLauncher(config)

        # Do NOT set BATON_* env vars — so they won't appear in the env
        monkeypatch.delenv("BATON_TASK_ID", raising=False)
        monkeypatch.delenv("BATON_DB_PATH", raising=False)
        monkeypatch.delenv("BATON_TEAM_CONTEXT_ROOT", raising=False)

        captured_env: dict[str, str] = {}

        async def _fake_run_once(**kwargs: object) -> "LaunchResult":  # type: ignore[name-defined]
            nonlocal captured_env
            captured_env = dict(kwargs.get("env") or {})
            from agent_baton.core.runtime.claude_launcher import LaunchResult
            return LaunchResult(
                status="complete",
                outcome="ok",
                agent_name="backend-engineer",
                step_id="1.1",
                duration_seconds=0.1,
            )

        with patch.object(launcher, "_run_once", side_effect=_fake_run_once), \
             patch.object(launcher, "_git_rev_parse", new=AsyncMock(return_value=None)):
            asyncio.run(
                launcher.launch(
                    agent_name="backend-engineer",
                    model="sonnet",
                    prompt="do something",
                    step_id="1.1",
                    # No cwd_override
                )
            )

        # Without BATON_* in os.environ and without cwd_override, env is clean
        assert "BATON_DB_PATH" not in captured_env
        assert "BATON_TASK_ID" not in captured_env
        assert "BATON_TEAM_CONTEXT_ROOT" not in captured_env
