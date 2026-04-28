"""Tests for Wave 5 — Human-Agent Loop (bd-e208, bd-1483, bd-9839).

Covers:
- Part A: TakeoverRecord, TakeoverSession, TakeoverError hierarchy (bd-e208)
- Part B: EscalationTier, SelfHealAttempt, SelfHealEscalator (bd-1483)
- Part C: SpeculationRecord, SpeculativePipeliner (bd-9839)
- BudgetEnforcer (govern/budget.py)
- ExecutionState Wave 5 fields (to_dict / from_dict round-trip)
- Dispatcher prompt builders (build_self_heal_prompt, build_handoff_prompt)
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
# Part B — Self-Heal
# ---------------------------------------------------------------------------


class TestEscalationTier:
    def test_tier_values(self):
        from agent_baton.core.engine.selfheal import EscalationTier

        assert EscalationTier.HAIKU_1.value == "haiku-1"
        assert EscalationTier.OPUS.value == "opus"

    def test_all_tiers_have_models(self):
        from agent_baton.core.engine.selfheal import EscalationTier, _TIER_MODELS

        for tier in EscalationTier:
            assert tier in _TIER_MODELS, f"Missing model for tier {tier}"

    def test_all_tiers_have_agents(self):
        from agent_baton.core.engine.selfheal import EscalationTier, _TIER_AGENTS

        for tier in EscalationTier:
            assert tier in _TIER_AGENTS, f"Missing agent for tier {tier}"

    def test_input_caps_monotonically_increase(self):
        from agent_baton.core.engine.selfheal import (
            EscalationTier,
            _TIER_INPUT_CAPS,
            _TIER_ORDER,
        )

        caps = [_TIER_INPUT_CAPS[t] for t in _TIER_ORDER]
        # Haiku-1 == Haiku-2, Sonnet-1 == Sonnet-2, but Sonnet > Haiku, Opus > Sonnet
        assert caps[0] == caps[1]   # haiku-1 == haiku-2
        assert caps[2] == caps[3]   # sonnet-1 == sonnet-2
        assert caps[2] > caps[0]    # sonnet > haiku
        assert caps[4] > caps[2]    # opus > sonnet


class TestSelfHealAttempt:
    def test_to_dict_roundtrip(self):
        from agent_baton.core.engine.selfheal import SelfHealAttempt

        a = SelfHealAttempt(
            parent_step_id="1.3",
            tier="haiku-1",
            started_at="2026-04-28T10:00:00+00:00",
            ended_at="2026-04-28T10:01:00+00:00",
            status="gate-still-failing",
            tokens_in=1000,
            tokens_out=200,
            cost_usd=0.0005,
            commit_hash="deadbeef",
            gate_stderr_tail="FAIL: assertion error",
        )
        d = a.to_dict()
        a2 = SelfHealAttempt.from_dict(d)
        assert a2.tier == "haiku-1"
        assert a2.tokens_in == 1000
        assert a2.cost_usd == pytest.approx(0.0005)


class TestSelfHealEscalator:
    def _make_escalator(self, tmp_path):
        from agent_baton.core.engine.selfheal import SelfHealEscalator

        return SelfHealEscalator(
            step_id="1.3",
            gate_command="pytest tests/",
            worktree_path=tmp_path,
        )

    def test_next_tier_starts_at_haiku_1(self, tmp_path):
        from agent_baton.core.engine.selfheal import EscalationTier

        esc = self._make_escalator(tmp_path)
        assert esc.next_tier() == EscalationTier.HAIKU_1

    def test_next_tier_advances_after_haiku_1(self, tmp_path):
        from agent_baton.core.engine.selfheal import EscalationTier, SelfHealAttempt

        esc = self._make_escalator(tmp_path)
        esc.record_attempt(SelfHealAttempt(
            parent_step_id="1.3", tier="haiku-1",
            started_at="", ended_at="", status="gate-still-failing",
            tokens_in=100, tokens_out=20, cost_usd=0.0,
        ))
        assert esc.next_tier() == EscalationTier.HAIKU_2

    def test_next_tier_escalates_to_sonnet_after_both_haiku(self, tmp_path):
        from agent_baton.core.engine.selfheal import EscalationTier, SelfHealAttempt

        esc = self._make_escalator(tmp_path)
        for tier_val in ("haiku-1", "haiku-2"):
            esc.record_attempt(SelfHealAttempt(
                parent_step_id="1.3", tier=tier_val,
                started_at="", ended_at="", status="gate-still-failing",
                tokens_in=100, tokens_out=20, cost_usd=0.0,
            ))
        assert esc.next_tier() == EscalationTier.SONNET_1

    def test_next_tier_returns_none_when_exhausted(self, tmp_path):
        from agent_baton.core.engine.selfheal import SelfHealAttempt

        esc = self._make_escalator(tmp_path)
        for tier_val in ("haiku-1", "haiku-2", "sonnet-1", "sonnet-2", "opus"):
            esc.record_attempt(SelfHealAttempt(
                parent_step_id="1.3", tier=tier_val,
                started_at="", ended_at="", status="gate-still-failing",
                tokens_in=100, tokens_out=20, cost_usd=0.0,
            ))
        assert esc.next_tier() is None

    def test_eligible_for_gate_failed_status(self, tmp_path):
        esc = self._make_escalator(tmp_path)
        assert esc.eligible("gate_failed")

    def test_not_eligible_for_non_gate_failed(self, tmp_path):
        esc = self._make_escalator(tmp_path)
        assert not esc.eligible("running")
        assert not esc.eligible("failed")

    def test_build_attempt_context_haiku_includes_stderr(self, tmp_path):
        from agent_baton.core.engine.selfheal import EscalationTier

        esc = self._make_escalator(tmp_path)
        ctx = esc.build_attempt_context(
            EscalationTier.HAIKU_1,
            gate_stderr_tail="AssertionError: expected 1 got 2",
        )
        assert "AssertionError" in ctx

    def test_build_attempt_context_opus_includes_full_files(self, tmp_path):
        from agent_baton.core.engine.selfheal import EscalationTier

        esc = self._make_escalator(tmp_path)
        ctx = esc.build_attempt_context(
            EscalationTier.OPUS,
            full_file_contents={"mymodule.py": "def foo(): pass"},
            project_summary="A baton orchestration project.",
        )
        assert "mymodule.py" in ctx
        assert "baton" in ctx

    def test_worktree_dirty_detection(self, tmp_path):
        esc = self._make_escalator(tmp_path)
        # Non-git directory — should return False gracefully.
        assert not esc.worktree_is_dirty()

    def test_reset_dirty_index_non_git(self, tmp_path):
        esc = self._make_escalator(tmp_path)
        # Non-git directory — should return False without raising.
        result = esc.reset_dirty_index()
        assert result is False

    def test_prior_failed_patch_updated_after_record(self, tmp_path):
        from agent_baton.core.engine.selfheal import SelfHealAttempt

        esc = self._make_escalator(tmp_path)
        assert esc.prior_failed_patch() == ""
        esc.record_attempt(SelfHealAttempt(
            parent_step_id="1.3", tier="haiku-1",
            started_at="", ended_at="", status="gate-still-failing",
            tokens_in=100, tokens_out=20, cost_usd=0.0,
            commit_hash="",  # no commit → no diff
        ))
        assert esc.prior_failed_patch() == ""  # no commit hash → no diff fetched


# ---------------------------------------------------------------------------
# Part C — Speculation
# ---------------------------------------------------------------------------


class TestSpeculationRecord:
    def test_to_dict_roundtrip(self):
        from agent_baton.core.engine.speculator import SpeculationRecord

        r = SpeculationRecord(
            spec_id="abc-123",
            target_step_id="2.1",
            trigger="awaiting_human_approval",
            worktree_path="/tmp/spec/2.1",
            worktree_branch="worktree/spec/2.1",
            started_at="2026-04-28T10:00:00+00:00",
            status="running",
        )
        d = r.to_dict()
        r2 = SpeculationRecord.from_dict(d)
        assert r2.spec_id == "abc-123"
        assert r2.target_step_id == "2.1"
        assert r2.is_active()

    def test_accepted_not_active(self):
        from agent_baton.core.engine.speculator import SpeculationRecord

        r = SpeculationRecord(
            spec_id="abc-123",
            target_step_id="2.1",
            trigger="ci_running",
            worktree_path="",
            worktree_branch="",
            started_at="2026-04-28T10:00:00+00:00",
            status="accepted",
        )
        assert not r.is_active()


class TestSpeculationTrigger:
    def test_trigger_values(self):
        from agent_baton.core.engine.speculator import SpeculationTrigger

        assert SpeculationTrigger.HUMAN_APPROVAL_WAIT.value == "awaiting_human_approval"
        assert SpeculationTrigger.CI_RUNNING.value == "ci_running"


class TestSpeculativePipeliner:
    def _make_pipeliner(self, enabled=True):
        from agent_baton.core.engine.speculator import SpeculativePipeliner

        return SpeculativePipeliner(
            worktree_mgr=None,
            task_id="test-task",
            enabled=enabled,
        )

    def test_should_not_speculate_when_disabled(self):
        p = self._make_pipeliner(enabled=False)
        assert not p.should_speculate("awaiting_human_approval", "2.1")

    def test_should_speculate_on_human_approval_wait(self):
        p = self._make_pipeliner(enabled=True)
        assert p.should_speculate("awaiting_human_approval", "2.1")

    def test_should_speculate_on_ci_running(self):
        p = self._make_pipeliner(enabled=True)
        assert p.should_speculate("ci_running", "2.1")

    def test_should_not_speculate_on_interact(self):
        p = self._make_pipeliner(enabled=True)
        assert not p.should_speculate("interacting", "2.1")

    def test_should_not_speculate_when_next_step_is_none(self):
        p = self._make_pipeliner(enabled=True)
        assert not p.should_speculate("awaiting_human_approval", None)

    def test_should_not_speculate_when_duplicate_active(self):
        from agent_baton.core.engine.speculator import SpeculationRecord

        p = self._make_pipeliner(enabled=True)
        p._speculations["spec-1"] = SpeculationRecord(
            spec_id="spec-1",
            target_step_id="2.1",
            trigger="awaiting_human_approval",
            worktree_path="",
            worktree_branch="",
            started_at="2026-04-28T10:00:00+00:00",
            status="running",
        )
        p._creation_order.append("spec-1")
        # Should not start a second speculation for the same step.
        assert not p.should_speculate("awaiting_human_approval", "2.1")

    def test_accept_marks_accepted(self):
        from agent_baton.core.engine.speculator import SpeculationRecord

        p = self._make_pipeliner(enabled=True)
        p._speculations["spec-1"] = SpeculationRecord(
            spec_id="spec-1",
            target_step_id="2.1",
            trigger="awaiting_human_approval",
            worktree_path="",
            worktree_branch="",
            started_at="2026-04-28T10:00:00+00:00",
            status="running",
        )
        result = p.accept("spec-1")
        assert result is not None
        assert result.status == "accepted"
        assert result.accepted_at != ""

    def test_accept_unknown_spec_returns_none(self):
        p = self._make_pipeliner(enabled=True)
        assert p.accept("nonexistent") is None

    def test_reject_marks_rejected(self):
        from agent_baton.core.engine.speculator import SpeculationRecord

        p = self._make_pipeliner(enabled=True)
        p._speculations["spec-2"] = SpeculationRecord(
            spec_id="spec-2",
            target_step_id="2.2",
            trigger="ci_running",
            worktree_path="",
            worktree_branch="",
            started_at="2026-04-28T10:00:00+00:00",
            status="running",
        )
        result = p.reject("spec-2", reason="stale")
        assert result is not None
        assert result.status == "rejected"
        assert result.reject_reason == "stale"

    def test_list_active_filters_terminal(self):
        from agent_baton.core.engine.speculator import SpeculationRecord

        p = self._make_pipeliner(enabled=True)
        for sid, status in [("s1", "running"), ("s2", "accepted"), ("s3", "rejected")]:
            p._speculations[sid] = SpeculationRecord(
                spec_id=sid,
                target_step_id="x.1",
                trigger="ci_running",
                worktree_path="",
                worktree_branch="",
                started_at="2026-04-28T10:00:00+00:00",
                status=status,
            )
        active = p.list_active()
        assert len(active) == 1
        assert active[0].spec_id == "s1"

    def test_gc_stale_expires_by_ttl(self):
        from agent_baton.core.engine.speculator import SpeculationRecord

        p = self._make_pipeliner(enabled=True)
        p._spec_ttl = 0  # immediate expiry
        p._speculations["s1"] = SpeculationRecord(
            spec_id="s1",
            target_step_id="2.1",
            trigger="ci_running",
            worktree_path="",
            worktree_branch="",
            started_at="2000-01-01T00:00:00+00:00",  # ancient
            status="running",
        )
        reaped = p.gc_stale()
        assert "s1" in reaped
        assert p._speculations["s1"].status == "expired"

    def test_gc_stale_expires_by_dispatched_step(self):
        from agent_baton.core.engine.speculator import SpeculationRecord

        p = self._make_pipeliner(enabled=True)
        p._spec_ttl = 9999  # do not expire by TTL
        from datetime import datetime, timezone
        p._speculations["s2"] = SpeculationRecord(
            spec_id="s2",
            target_step_id="2.1",
            trigger="awaiting_human_approval",
            worktree_path="",
            worktree_branch="",
            started_at=datetime.now(tz=timezone.utc).isoformat(),
            status="running",
        )
        # Target step dispatched without handoff.
        reaped = p.gc_stale(dispatched_step_ids={"2.1"})
        assert "s2" in reaped

    def test_to_dict_from_state_roundtrip(self):
        from agent_baton.core.engine.speculator import SpeculationRecord, SpeculativePipeliner

        p = SpeculativePipeliner(task_id="t1", enabled=True)
        p._speculations["spec-x"] = SpeculationRecord(
            spec_id="spec-x",
            target_step_id="1.1",
            trigger="ci_running",
            worktree_path="",
            worktree_branch="",
            started_at="2026-04-28T10:00:00+00:00",
            status="pending",
        )
        d = p.to_dict()
        p2 = SpeculativePipeliner(task_id="t1", enabled=True)
        p2.load_from_state(d)
        assert "spec-x" in p2._speculations
        assert p2._speculations["spec-x"].target_step_id == "1.1"


# ---------------------------------------------------------------------------
# BudgetEnforcer
# ---------------------------------------------------------------------------


class TestBudgetEnforcer:
    def test_allow_self_heal_within_budget(self):
        from agent_baton.core.govern.budget import BudgetEnforcer

        b = BudgetEnforcer(per_step_cap_usd=1.0, per_task_cap_usd=10.0)
        assert b.allow_self_heal("step-1", "haiku-1")

    def test_deny_self_heal_when_step_cap_exceeded(self):
        from agent_baton.core.govern.budget import BudgetEnforcer

        b = BudgetEnforcer(per_step_cap_usd=0.001, per_task_cap_usd=10.0)
        b.record_self_heal_spend("step-1", "haiku-1", tokens_in=10000, tokens_out=2000)
        assert not b.allow_self_heal("step-1", "haiku-2")

    def test_deny_self_heal_when_task_cap_exceeded(self):
        from agent_baton.core.govern.budget import BudgetEnforcer

        b = BudgetEnforcer(per_step_cap_usd=100.0, per_task_cap_usd=0.001)
        b.record_self_heal_spend("step-1", "haiku-1", tokens_in=10000, tokens_out=2000)
        assert not b.allow_self_heal("step-2", "haiku-1")

    def test_record_spend_returns_cost(self):
        from agent_baton.core.govern.budget import BudgetEnforcer

        b = BudgetEnforcer()
        cost = b.record_self_heal_spend("step-1", "haiku-1", tokens_in=1_000_000, tokens_out=0)
        assert cost == pytest.approx(0.25, rel=0.01)  # Haiku input: $0.25/M

    def test_allow_speculation_within_daily_cap(self):
        from agent_baton.core.govern.budget import BudgetEnforcer

        b = BudgetEnforcer(speculation_daily_cap_usd=10.0)
        assert b.allow_speculation()

    def test_deny_speculation_when_daily_cap_exceeded(self):
        from agent_baton.core.govern.budget import BudgetEnforcer

        b = BudgetEnforcer(speculation_daily_cap_usd=0.0001)
        b.record_speculation_spend("spec-1", tokens_in=10000, tokens_out=2000)
        assert not b.allow_speculation()

    def test_self_heal_step_spend_accumulates(self):
        from agent_baton.core.govern.budget import BudgetEnforcer

        b = BudgetEnforcer()
        b.record_self_heal_spend("step-1", "haiku-1", 100, 20)
        b.record_self_heal_spend("step-1", "haiku-2", 100, 20)
        assert b.self_heal_step_spend("step-1") > 0

    def test_different_steps_have_separate_budgets(self):
        from agent_baton.core.govern.budget import BudgetEnforcer

        b = BudgetEnforcer(per_step_cap_usd=0.001)
        b.record_self_heal_spend("step-1", "haiku-1", 10000, 2000)
        # step-2 has its own cap — still within budget.
        assert b.allow_self_heal("step-2", "haiku-1")


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
        assert state.selfheal_attempts == []
        assert state.speculations == {}

    def test_wave5_fields_survive_to_dict_roundtrip(self):
        from agent_baton.models.execution import ExecutionState

        d = self._make_minimal_state_dict()
        d["takeover_records"] = [{"step_id": "1.1", "started_at": "2026-04-28T10:00:00+00:00",
                                   "started_by": "djiv", "reason": "test", "editor_or_shell": "vim",
                                   "pid": 0, "last_known_worktree_head": "abc", "resumed_at": "", "resolution": ""}]
        d["selfheal_attempts"] = [{"parent_step_id": "1.1", "tier": "haiku-1",
                                    "started_at": "2026-04-28T10:00:00+00:00", "ended_at": "2026-04-28T10:01:00+00:00",
                                    "status": "gate-still-failing", "tokens_in": 100, "tokens_out": 20,
                                    "cost_usd": 0.0, "commit_hash": "", "gate_stderr_tail": ""}]
        d["speculations"] = {"spec-1": {"spec_id": "spec-1", "target_step_id": "2.1",
                                         "trigger": "awaiting_human_approval",
                                         "worktree_path": "", "worktree_branch": "",
                                         "started_at": "2026-04-28T10:00:00+00:00",
                                         "status": "running", "accepted_at": "", "rejected_at": "",
                                         "reject_reason": "", "cost_usd": 0.0, "scaffold_files": []}}

        state = ExecutionState.from_dict(d)
        assert len(state.takeover_records) == 1
        assert len(state.selfheal_attempts) == 1
        assert "spec-1" in state.speculations

        # Round-trip via to_dict.
        out = state.to_dict()
        assert len(out["takeover_records"]) == 1
        assert len(out["selfheal_attempts"]) == 1
        assert "spec-1" in out["speculations"]

    def test_legacy_state_without_wave5_fields_loads_cleanly(self):
        from agent_baton.models.execution import ExecutionState

        # Legacy state has no Wave 5 keys — should default gracefully.
        d = self._make_minimal_state_dict()
        # Explicitly no takeover_records, selfheal_attempts, speculations keys.
        state = ExecutionState.from_dict(d)
        assert state.takeover_records == []
        assert state.selfheal_attempts == []
        assert state.speculations == {}


# ---------------------------------------------------------------------------
# Dispatcher prompt builders
# ---------------------------------------------------------------------------


class TestDispatcherPromptBuilders:
    def _dispatcher(self):
        from agent_baton.core.engine.dispatcher import PromptDispatcher

        return PromptDispatcher()

    def test_build_self_heal_prompt_haiku_1_includes_gate(self):
        d = self._dispatcher()
        prompt = d.build_self_heal_prompt(
            "haiku-1",
            {"gate_command": "pytest tests/", "stderr_tail": "FAILED test_foo.py", "diff": ""},
        )
        assert "pytest tests/" in prompt
        assert "FAILED test_foo.py" in prompt

    def test_build_self_heal_prompt_haiku_2_includes_do_not_repeat(self):
        d = self._dispatcher()
        prompt = d.build_self_heal_prompt(
            "haiku-2",
            {"gate_command": "make test", "stderr_tail": "error", "diff": ""},
            prior_failed_patch="--- a/foo.py\n+++ b/foo.py\n@@ -1,1 +1,1 @@\n-old\n+new",
        )
        assert "DO NOT REPEAT" in prompt
        assert "foo.py" in prompt

    def test_build_self_heal_prompt_opus_includes_root_cause_framing(self):
        d = self._dispatcher()
        prompt = d.build_self_heal_prompt(
            "opus",
            {
                "gate_command": "pytest",
                "stderr_tail": "structural bug",
                "full_file_contents": {"core.py": "def main(): pass"},
                "project_summary": "orchestration engine",
            },
        )
        assert "ROOT CAUSE" in prompt
        assert "core.py" in prompt

    def test_build_self_heal_prompt_sonnet_includes_file_windows(self):
        d = self._dispatcher()
        prompt = d.build_self_heal_prompt(
            "sonnet-1",
            {
                "gate_command": "mypy .",
                "stderr_tail": "type error",
                "file_windows": {"models.py": "class Foo: pass"},
            },
        )
        assert "models.py" in prompt

    def test_build_self_heal_prompt_haiku_1_no_do_not_repeat(self):
        d = self._dispatcher()
        prompt = d.build_self_heal_prompt(
            "haiku-1",
            {"gate_command": "pytest", "stderr_tail": "fail", "diff": ""},
            prior_failed_patch="some diff",
        )
        # Haiku-1 should NOT include the DO NOT REPEAT block.
        assert "DO NOT REPEAT" not in prompt
