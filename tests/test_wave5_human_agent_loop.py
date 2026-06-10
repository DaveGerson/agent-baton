"""Tests for Wave 5 — Human-Agent Loop (bd-e208).

Covers:
- Part A: TakeoverRecord, TakeoverSession, TakeoverError hierarchy (bd-e208)
- BudgetEnforcer (govern/budget.py) — immune system caps + run ceiling
- ExecutionState Wave 5 fields (to_dict / from_dict round-trip)
- Dispatcher prompt builders (build_gate_retry_prompt, build_handoff_prompt)

Note: Self-heal escalation ladder (Part B, bd-1483) removed in Phase D (007).
Speculative pipelining (Part C, bd-9839) removed in Phase B (007).
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Part A — Takeover
# ---------------------------------------------------------------------------


class TestTakeoverRecord:
    def test_to_dict_roundtrip(self):
        from agent_baton.core.engine.takeover import TakeoverRecord

        r = TakeoverRecord(
            step_id="1.3",
            started_at="2026-04-28T10:00:00+00:00",
            started_by="djiv",
            reason="gate failed",
            editor_or_shell="vim",
            pid=12345,
            last_known_worktree_head="abc123",
            resumed_at="",
            resolution="",
        )
        d = r.to_dict()
        r2 = TakeoverRecord.from_dict(d)
        assert r2.step_id == "1.3"
        assert r2.pid == 12345
        assert r2.is_active()

    def test_resolved_record_not_active(self):
        from agent_baton.core.engine.takeover import TakeoverRecord

        r = TakeoverRecord(
            step_id="1.3",
            started_at="2026-04-28T10:00:00+00:00",
            started_by="djiv",
            reason="test",
            editor_or_shell="vim",
            pid=0,
            last_known_worktree_head="abc123",
            resumed_at="2026-04-28T10:05:00+00:00",
            resolution="completed",
        )
        assert not r.is_active()


class TestTakeoverErrors:
    def test_error_hierarchy(self):
        from agent_baton.core.engine.takeover import (
            TakeoverError,
            TakeoverInvalidStateError,
            TakeoverWorktreeMissingError,
        )

        assert issubclass(TakeoverWorktreeMissingError, TakeoverError)
        assert issubclass(TakeoverInvalidStateError, TakeoverError)

    def test_missing_error_message(self):
        from agent_baton.core.engine.takeover import TakeoverWorktreeMissingError

        exc = TakeoverWorktreeMissingError("no worktree for step 1.3")
        assert "1.3" in str(exc)


class TestTakeoverSession:
    def test_validate_source_state_allowed(self):
        from agent_baton.core.engine.takeover import TakeoverSession

        session = TakeoverSession(worktree_mgr=None, task_id="test")
        # Should not raise for allowed states.
        for status in ("running", "gate_failed", "failed", "paused-takeover"):
            session.validate_source_state("1.1", status)

    def test_validate_source_state_forbidden_complete(self):
        from agent_baton.core.engine.takeover import (
            TakeoverInvalidStateError,
            TakeoverSession,
        )

        session = TakeoverSession(worktree_mgr=None, task_id="test")
        with pytest.raises(TakeoverInvalidStateError, match="complete"):
            session.validate_source_state("1.1", "complete")

    def test_validate_source_state_forbidden_dispatched(self):
        from agent_baton.core.engine.takeover import (
            TakeoverInvalidStateError,
            TakeoverSession,
        )

        session = TakeoverSession(worktree_mgr=None, task_id="test")
        with pytest.raises(TakeoverInvalidStateError, match="dispatched"):
            session.validate_source_state("1.1", "dispatched")

    def test_resolve_handle_no_worktree_mgr(self):
        from agent_baton.core.engine.takeover import (
            TakeoverSession,
            TakeoverWorktreeMissingError,
        )

        session = TakeoverSession(worktree_mgr=None, task_id="test")
        with pytest.raises(TakeoverWorktreeMissingError, match="disabled"):
            session.resolve_handle("1.1")

    def test_resolve_handle_no_retained_worktree(self):
        from agent_baton.core.engine.takeover import (
            TakeoverSession,
            TakeoverWorktreeMissingError,
        )

        mgr = MagicMock()
        mgr.handle_for.return_value = None
        session = TakeoverSession(worktree_mgr=mgr, task_id="test-task")
        with pytest.raises(TakeoverWorktreeMissingError, match="No retained worktree"):
            session.resolve_handle("1.1")

    def test_resolve_handle_returns_handle(self):
        from agent_baton.core.engine.takeover import TakeoverSession

        mock_handle = MagicMock()
        mgr = MagicMock()
        mgr.handle_for.return_value = mock_handle
        session = TakeoverSession(worktree_mgr=mgr, task_id="test-task")
        result = session.resolve_handle("1.1")
        assert result is mock_handle

    def test_resolve_editor_command_defaults_to_vim(self):
        from agent_baton.core.engine.takeover import TakeoverSession

        with patch.dict("os.environ", {}, clear=True):
            # Ensure EDITOR is not set.
            import os
            os.environ.pop("EDITOR", None)
            cmd = TakeoverSession.resolve_editor_command()
        assert cmd == "vim"

    def test_resolve_editor_command_uses_env_editor(self):
        from agent_baton.core.engine.takeover import TakeoverSession

        with patch.dict("os.environ", {"EDITOR": "nano"}):
            cmd = TakeoverSession.resolve_editor_command()
        assert cmd == "nano"

    def test_resolve_editor_command_shell_flag(self):
        from agent_baton.core.engine.takeover import TakeoverSession

        with patch.dict("os.environ", {"SHELL": "/bin/zsh"}):
            cmd = TakeoverSession.resolve_editor_command(use_shell=True)
        assert cmd == "/bin/zsh"

    def test_resolve_editor_command_override(self):
        from agent_baton.core.engine.takeover import TakeoverSession

        cmd = TakeoverSession.resolve_editor_command(editor_override="emacs -nw")
        assert cmd == "emacs -nw"

    def test_vscode_gets_dash_w(self):
        from agent_baton.core.engine.takeover import TakeoverSession

        with patch.dict("os.environ", {"EDITOR": "code"}):
            cmd = TakeoverSession.resolve_editor_command()
        assert "-w" in cmd

    def test_read_head_git_repo(self, tmp_path):
        from agent_baton.core.engine.takeover import TakeoverSession

        # Init a temporary git repo with one commit.
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), capture_output=True)
        (tmp_path / "file.txt").write_text("hello")
        subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), capture_output=True)

        head = TakeoverSession.read_head(tmp_path)
        assert len(head) == 40  # full SHA

    def test_read_head_nonexistent_path(self, tmp_path):
        from agent_baton.core.engine.takeover import TakeoverSession

        head = TakeoverSession.read_head(tmp_path / "does_not_exist")
        assert head == ""

    def test_compute_dev_commits_returns_empty_when_head_unchanged(self, tmp_path):
        from agent_baton.core.engine.takeover import TakeoverSession

        sha = "abc123" * 5 + "ab"  # 42 chars — doesn't matter, same == same
        result = TakeoverSession.compute_dev_commits(tmp_path, sha, sha)
        assert result == []

    def test_current_user_returns_string(self):
        from agent_baton.core.engine.takeover import TakeoverSession

        user = TakeoverSession.current_user()
        assert isinstance(user, str)
        assert len(user) > 0


# ---------------------------------------------------------------------------
# BudgetEnforcer
# ---------------------------------------------------------------------------


class TestBudgetEnforcer:
    def test_immune_daily_cap_defaults(self):
        from agent_baton.core.govern.budget import BudgetEnforcer

        b = BudgetEnforcer()
        assert b.DEFAULT_IMMUNE_DAILY_CAP_USD == 5.00

    def test_run_ceiling_add_spend(self):
        from agent_baton.core.govern.budget import BudgetEnforcer

        b = BudgetEnforcer()
        b.add_run_spend(0.01)
        assert b.run_cumulative_spend_usd == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# ExecutionState — Wave 5 fields round-trip
# ---------------------------------------------------------------------------


class TestExecutionStateWave5Fields:
    def _make_minimal_state_dict(self) -> dict:
        """Return the minimal dict required to construct an ExecutionState."""
        return {
            "task_id": "test-task",
            "plan": {
                "task_id": "test-task",
                "task_summary": "test",
                "phases": [],
                "risk_level": "LOW",
                "budget_tier": "lean",
                "engagement_level": "light",
            },
        }

    def test_wave5_fields_default_empty_on_from_dict(self):
        from agent_baton.models.execution import ExecutionState

        state = ExecutionState.from_dict(self._make_minimal_state_dict())
        assert state.takeover_records == []

    def test_wave5_fields_survive_to_dict_roundtrip(self):
        from agent_baton.models.execution import ExecutionState

        d = self._make_minimal_state_dict()
        d["takeover_records"] = [{"step_id": "1.1", "started_at": "2026-04-28T10:00:00+00:00",
                                   "started_by": "djiv", "reason": "test", "editor_or_shell": "vim",
                                   "pid": 0, "last_known_worktree_head": "abc", "resumed_at": "", "resolution": ""}]
        state = ExecutionState.from_dict(d)
        assert len(state.takeover_records) == 1

        # Round-trip via to_dict.
        out = state.to_dict()
        assert len(out["takeover_records"]) == 1

    def test_legacy_state_without_wave5_fields_loads_cleanly(self):
        from agent_baton.models.execution import ExecutionState

        # Legacy state has no Wave 5 keys — should default gracefully.
        d = self._make_minimal_state_dict()
        # Explicitly no takeover_records key — loads with empty default.
        state = ExecutionState.from_dict(d)
        assert state.takeover_records == []


# ---------------------------------------------------------------------------
# Dispatcher prompt builders
# ---------------------------------------------------------------------------


class TestDispatcherPromptBuilders:
    def _dispatcher(self):
        from agent_baton.core.engine.dispatcher import PromptDispatcher

        return PromptDispatcher()

    def test_build_gate_retry_prompt_includes_gate_output(self):
        prompt = self._dispatcher().build_gate_retry_prompt(
            original_prompt="Do the thing.",
            gate_output="FAILED: test_foo.py assertion failed",
            gate_command="pytest tests/",
        )
        assert "GATE OUTPUT (retry 1/1)" in prompt
        assert "FAILED: test_foo.py" in prompt
        assert "pytest tests/" in prompt
        assert "Do the thing." in prompt

    def test_build_gate_retry_prompt_no_command(self):
        prompt = self._dispatcher().build_gate_retry_prompt(
            original_prompt="Original task.",
            gate_output="error output",
        )
        assert "GATE OUTPUT (retry 1/1)" in prompt
        assert "error output" in prompt

    def test_build_gate_retry_prompt_empty_output(self):
        prompt = self._dispatcher().build_gate_retry_prompt(
            original_prompt="Do it.",
            gate_output="",
        )
        assert "(no output captured)" in prompt
