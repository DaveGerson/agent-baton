"""Wave 5 — Integration + dogfood-isolation tests (bd-e208).

Fills documented gaps from the implementer's BEAD_DISCOVERY, plus
safety tests analogous to the Wave 1.3 dogfood suite.

All tests that touch git operations use a real git repository (tmp_git_repo
fixture) to catch actual integration bugs rather than mocking git.

Coverage:
  Gap 1: takeover paused-takeover state persists across engine restart
  Gap 2: resume_from_takeover re-runs gate and transitions to running
  Gap 3: append_coauthored_trailer writes Co-Authored-By to last commit

Note: Gap 4 (reset_dirty_index) and Gap 5 (_enqueue_selfheal) removed in
Phase D (007) — self-heal escalation ladder deleted.
Safety 8 (selfheal budget cap) removed in Phase D (007).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared fixtures — mirror the Wave 1.3 dogfood pattern
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a minimal real git repo with one initial commit.

    Layout:
        tmp_path/                  ← git repo root (project_root)
            .claude/team-context/  ← engine team context dir
    """
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"],
                   cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


@pytest.fixture
def team_context(tmp_git_repo: Path) -> Path:
    """Return (and create) .claude/team-context inside the git repo."""
    ctx = tmp_git_repo / ".claude" / "team-context"
    ctx.mkdir(parents=True, exist_ok=True)
    return ctx


def _minimal_plan(task_id: str = "task-test"):
    """Return a one-phase, one-step plan with no gate."""
    from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

    return MachinePlan(
        task_id=task_id,
        task_summary="Wave 5 integration test plan",
        risk_level="LOW",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Impl",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Implement foo",
                        model="sonnet",
                        step_type="implementation",
                    )
                ],
            )
        ],
    )


def _plan_with_gate(task_id: str = "task-gate", gate_command: str = "true"):
    """Return a one-phase plan with an automated gate."""
    from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep

    return MachinePlan(
        task_id=task_id,
        task_summary="Wave 5 integration test plan with gate",
        risk_level="LOW",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Impl",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Implement foo",
                        model="sonnet",
                        step_type="implementation",
                    )
                ],
                gate=PlanGate(gate_type="test", command=gate_command),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Gap 1 — paused-takeover state persists across engine restart
# ---------------------------------------------------------------------------


class TestTakeoverPausedStatePersistsAcrossEngineRestart:
    """start_takeover writes paused-takeover to disk; a new engine instance
    reads it back correctly."""

    def test_state_survives_engine_restart(
        self, team_context: Path, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BATON_WORKTREE_ENABLED", "1")
        monkeypatch.setenv("BATON_TAKEOVER_ENABLED", "1")
        from agent_baton.core.engine.executor import ExecutionEngine
        from agent_baton.core.engine.takeover import TakeoverRecord

        # First engine: start plan, dispatch step (creates worktree), then takeover.
        eng1 = ExecutionEngine(team_context_root=team_context)
        plan = _minimal_plan("task-takeover-persist")
        eng1.start(plan)
        eng1.mark_dispatched("1.1", "backend-engineer")

        # Force gate-failed so takeover is accepted as source state.
        state = eng1._load_execution()
        assert state is not None
        state.status = "gate_failed"
        eng1._save_execution(state)

        record = eng1.start_takeover(
            "1.1",
            reason="test-takeover-persist",
            editor_or_shell="vim",
            pid=99999,
        )
        assert record is not None

        # Verify the first engine wrote it to disk.
        state_after = eng1._load_execution()
        assert state_after is not None
        assert state_after.status == "paused-takeover"
        assert len(state_after.takeover_records) >= 1

        # Simulate engine restart with a fresh instance that has no in-memory state.
        eng2 = ExecutionEngine(team_context_root=team_context)
        state_restored = eng2._load_execution()
        assert state_restored is not None
        assert state_restored.status == "paused-takeover", (
            "status 'paused-takeover' must persist across engine restart"
        )
        assert len(state_restored.takeover_records) >= 1

        # The last record must match the step_id we took over.
        last_record_dict = state_restored.takeover_records[-1]
        assert last_record_dict.get("step_id") == "1.1"
        # is_active() must return True (resumed_at is empty).
        last_record = TakeoverRecord.from_dict(last_record_dict)
        assert last_record.is_active(), (
            "Takeover record must still be active after restart (not yet resumed)"
        )


# ---------------------------------------------------------------------------
# Gap 2 — resume_from_takeover re-runs gate and transitions to running
# ---------------------------------------------------------------------------


class TestResumeReronsGateAndPasses:
    """resume_from_takeover with rerun_gate=True: when the gate exits 0,
    the engine transitions paused-takeover → running and records gate pass."""

    def test_gate_passes_on_second_call_transitions_to_running(
        self, team_context: Path, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BATON_WORKTREE_ENABLED", "1")
        monkeypatch.setenv("BATON_TAKEOVER_ENABLED", "1")
        from agent_baton.core.engine.executor import ExecutionEngine

        eng = ExecutionEngine(team_context_root=team_context)
        plan = _plan_with_gate("task-resume-gate", gate_command="true")
        eng.start(plan)
        eng.mark_dispatched("1.1", "backend-engineer")

        # Force gate_failed → takeover
        state = eng._load_execution()
        assert state is not None
        state.status = "gate_failed"
        eng._save_execution(state)
        record = eng.start_takeover("1.1", reason="test-resume-gate", pid=0)
        assert record is not None

        state = eng._load_execution()
        assert state is not None
        assert state.status == "paused-takeover"

        # The worktree must have a new commit before resume will proceed.
        handle_dict = getattr(state, "step_worktrees", {}).get("1.1")
        assert handle_dict is not None, "Need a real worktree for resume test"
        wt_path = Path(handle_dict["path"])

        # Make a commit in the worktree so HEAD advances past last_known_head.
        (wt_path / "fix.py").write_text("# fix\n")
        subprocess.run(["git", "add", "fix.py"], cwd=wt_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "developer fix"],
            cwd=wt_path,
            capture_output=True,
        )

        # Capture calls to record_gate_result via spy on the engine instance.
        gate_result_calls = []
        original_record_gate = eng.record_gate_result

        def _spy_record_gate_result(**kwargs):
            gate_result_calls.append(kwargs)
            original_record_gate(**kwargs)

        # subprocess is imported locally inside resume_from_takeover as
        # `import subprocess as _sp`, so patching the stdlib module intercepts it.
        _real_run = subprocess.run

        def _gate_passes(cmd, *args, **kwargs):
            # Pass gate shell commands through with exit 0.
            if kwargs.get("shell") and isinstance(cmd, str) and "true" in cmd:
                result = MagicMock()
                result.returncode = 0
                result.stdout = "ok\n"
                result.stderr = ""
                return result
            return _real_run(cmd, *args, **kwargs)

        with patch.object(eng, "record_gate_result", side_effect=_spy_record_gate_result), \
             patch("subprocess.run", side_effect=_gate_passes):
            result = eng.resume_from_takeover("1.1", rerun_gate=True, abort=False)

        assert result is True, "resume_from_takeover must return True when gate passes"

        # Verify record_gate_result was called with passed=True and decision_source="takeover".
        assert len(gate_result_calls) >= 1
        assert gate_result_calls[-1]["passed"] is True
        assert gate_result_calls[-1]["decision_source"] == "takeover"

        # State must have exited paused-takeover.
        final_state = eng._load_execution()
        assert final_state is not None
        assert final_state.status != "paused-takeover", (
            "After gate passes, status must not remain paused-takeover"
        )


# ---------------------------------------------------------------------------
# Gap 3 — append_coauthored_trailer writes Co-Authored-By trailer
# ---------------------------------------------------------------------------


class TestTakeoverCommitAttributionTrailerAppended:
    """TakeoverSession.append_coauthored_trailer amends the last commit with
    a Co-Authored-By: agent-baton-<name> trailer."""

    def test_trailer_appears_in_git_log(self, tmp_git_repo: Path) -> None:
        from agent_baton.core.engine.takeover import TakeoverSession

        repo = tmp_git_repo
        # Create a commit in the repo for amending.
        (repo / "file.py").write_text("# code\n")
        subprocess.run(["git", "add", "file.py"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "agent work"],
            cwd=repo,
            capture_output=True,
            check=True,
        )

        result = TakeoverSession.append_coauthored_trailer(repo, "backend-engineer")
        assert result is True, "append_coauthored_trailer must return True on success"

        # Verify the trailer is present in the most recent commit message.
        log_result = subprocess.run(
            ["git", "log", "-1", "--format=%B"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        commit_msg = log_result.stdout
        assert "Co-Authored-By:" in commit_msg, (
            f"Co-Authored-By trailer not found in commit message:\n{commit_msg}"
        )
        assert "agent-baton-backend-engineer" in commit_msg, (
            f"Expected agent-baton-backend-engineer in trailer, got:\n{commit_msg}"
        )

    def test_trailer_format_uses_baton_local_email(self, tmp_git_repo: Path) -> None:
        from agent_baton.core.engine.takeover import TakeoverSession

        repo = tmp_git_repo
        (repo / "another.py").write_text("# another\n")
        subprocess.run(["git", "add", "another.py"], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "another agent work"],
            cwd=repo,
            capture_output=True,
            check=True,
        )

        TakeoverSession.append_coauthored_trailer(repo, "test-agent")

        log_result = subprocess.run(
            ["git", "log", "-1", "--format=%B"],
            cwd=repo,
            capture_output=True,
            text=True,
        )
        commit_msg = log_result.stdout
        assert "test-agent@baton.local" in commit_msg, (
            f"Expected email format <agent-name>@baton.local, got:\n{commit_msg}"
        )

    def test_trailer_fails_gracefully_on_no_commits(self, tmp_path: Path) -> None:
        """Non-git directory returns False without raising."""
        from agent_baton.core.engine.takeover import TakeoverSession

        result = TakeoverSession.append_coauthored_trailer(tmp_path, "some-agent")
        assert result is False



class TestEnqueueSelfhealRemovedPlaceholder:
    """Gap 4 (TestSelfhealDirtyIndexResetsBetweenAttempts) and
    Gap 5 (TestEnqueueSelfhealRecordsIntentAndValidatesPreconditions) were
    removed in Phase D (007) — the self-heal escalation ladder was deleted.
    This placeholder class documents the removal.

    NOTE: The following class body is intentionally left placeholder-only —
    the tests that previously existed here are gone.
    """
    # Gap 4 and Gap 5 deleted in Phase D (007).


# Gap 5 (TestEnqueueSelfhealRecordsIntentAndValidatesPreconditions) and
# Safety 8 (TestSelfhealEscalationRespectsPerStepBudgetCap) removed in
# Phase D (007) — the self-heal escalation ladder was deleted.
