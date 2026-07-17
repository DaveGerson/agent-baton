"""Tests for phase 4 4.2 (team runtime contract): ClaudeCodeLauncher must
inject ``BATON_TEAM_MEMBER_ID`` into every launched subprocess so a
dispatched team member's ``baton team <verb>`` calls can resolve their own
identity without needing to hand-transcribe it into every call (see
docs/internal/team-runtime-contract.md §9.1 and §2.2).

For a team member's dispatch, ``step_id`` IS the member's ``member_id`` by
construction (``ExecutionEngine._team_dispatch_action`` sets
``ExecutionAction.step_id=member.member_id`` for each flattened team
member) — so the launcher derives the env var directly from its per-call
``step_id`` argument rather than relying on a shared, mutable
``os.environ``, which would race across the concurrently-dispatched steps
in one ``StepScheduler.dispatch_batch`` wave.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agent_baton.core.runtime.claude_launcher import (
    ClaudeCodeConfig,
    ClaudeCodeLauncher,
    _DEFAULT_ENV_PASSTHROUGH,
)


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    (tmp_path / ".claude" / "team-context").mkdir(parents=True)
    (tmp_path / ".claude" / "team-context" / "baton.db").write_bytes(b"")
    return tmp_path


def _run_launch_capture_env(
    *,
    tmp_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    step_id: str,
    clear_env_first: bool = True,
) -> dict[str, str]:
    config = ClaudeCodeConfig(working_directory=tmp_project)
    launcher = ClaudeCodeLauncher(config)
    if clear_env_first:
        monkeypatch.delenv("BATON_TEAM_MEMBER_ID", raising=False)

    captured_env: dict[str, str] = {}

    async def _fake_run_once(**kwargs: object) -> "LaunchResult":  # type: ignore[name-defined]
        nonlocal captured_env
        captured_env = dict(kwargs.get("env") or {})
        from agent_baton.core.runtime.claude_launcher import LaunchResult
        return LaunchResult(
            status="complete", outcome="ok",
            agent_name="backend-engineer", step_id=step_id,
            duration_seconds=0.1,
        )

    with patch.object(launcher, "_run_once", side_effect=_fake_run_once), \
         patch.object(launcher, "_git_rev_parse", new=AsyncMock(return_value=None)):
        asyncio.run(
            launcher.launch(
                agent_name="backend-engineer",
                model="sonnet",
                prompt="do something",
                step_id=step_id,
            )
        )
    return captured_env


class TestBatonTeamMemberIdInDefaultPassthrough:
    def test_in_default_passthrough(self) -> None:
        assert "BATON_TEAM_MEMBER_ID" in _DEFAULT_ENV_PASSTHROUGH


class TestBatonTeamMemberIdInjection:
    def test_injected_from_step_id(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = _run_launch_capture_env(
            tmp_project=tmp_project, monkeypatch=monkeypatch, step_id="1.1.b",
        )
        assert env.get("BATON_TEAM_MEMBER_ID") == "1.1.b"

    def test_empty_step_id_does_not_inject(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        env = _run_launch_capture_env(
            tmp_project=tmp_project, monkeypatch=monkeypatch, step_id="",
        )
        assert "BATON_TEAM_MEMBER_ID" not in env

    def test_per_call_step_id_overrides_stale_parent_env(
        self, tmp_project: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Simulate a stale/wrong value already sitting in the parent
        # process's environment (e.g. left over from a previous dispatch
        # in the same process) — the per-call step_id must win, since it
        # is the only race-free source of truth across a concurrent
        # dispatch wave (see module docstring).
        monkeypatch.setenv("BATON_TEAM_MEMBER_ID", "stale-member-id")
        env = _run_launch_capture_env(
            tmp_project=tmp_project, monkeypatch=monkeypatch, step_id="1.1.a",
            clear_env_first=False,
        )
        assert env.get("BATON_TEAM_MEMBER_ID") == "1.1.a"
