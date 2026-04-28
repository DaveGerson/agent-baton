"""Tests for the post-dispatch isolation verifier (bd-edbf).

Covers both the verifier core (DispatchVerifier) and the two CLI
subcommands that expose it (`baton execute verify-dispatch`,
`baton execute audit-isolation`).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from agent_baton.core.audit.dispatch_verifier import (
    AuditReport,
    DispatchVerifier,
    VerificationResult,
    _path_matches_any,
)
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Fixtures: lightweight git repo + plan/state factories
# ---------------------------------------------------------------------------


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a small git repo with one commit so we can test resolution."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        check=True,
    )
    (tmp_path / "agent_baton").mkdir()
    (tmp_path / "agent_baton" / "core").mkdir()
    (tmp_path / "agent_baton" / "core" / "audit").mkdir()
    (tmp_path / "agent_baton" / "core" / "audit" / "marker.py").write_text("# x\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_marker.py").write_text("# t\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-q", "-m", "seed"], check=True
    )
    return tmp_path


def _step(step_id: str = "1.1", allowed_paths=None) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name="backend-engineer",
        task_description="t",
        allowed_paths=list(allowed_paths or []),
    )


def _result(
    step_id: str = "1.1",
    files=None,
    commit_hash: str = "",
) -> StepResult:
    return StepResult(
        step_id=step_id,
        agent_name="backend-engineer",
        files_changed=list(files or []),
        commit_hash=commit_hash,
    )


def _state_with(steps: list[PlanStep], results: list[StepResult]) -> ExecutionState:
    plan = MachinePlan(
        task_id="task-test",
        task_summary="t",
        risk_level="LOW",
        budget_tier="lean",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[PlanPhase(phase_id=1, name="P1", steps=steps)],
    )
    return ExecutionState(task_id="task-test", plan=plan, step_results=results)


# ---------------------------------------------------------------------------
# Verifier core tests
# ---------------------------------------------------------------------------


class TestPathMatching:
    def test_exact_file_match(self):
        assert _path_matches_any("foo/bar.py", ["foo/bar.py"]) is True

    def test_dir_prefix_match(self):
        assert _path_matches_any("agent_baton/core/audit/x.py", ["agent_baton/core/audit/"]) is True
        assert _path_matches_any("agent_baton/core/audit/x.py", ["agent_baton/core/audit"]) is True

    def test_glob_match(self):
        assert _path_matches_any("tests/foo.py", ["tests/*.py"]) is True
        # Segment-aware: * does not cross /
        assert _path_matches_any("tests/sub/foo.py", ["tests/*.py"]) is False

    def test_outside_scope(self):
        assert _path_matches_any("docs/x.md", ["agent_baton/core/audit/"]) is False

    def test_multiple_paths_ored(self):
        paths = ["agent_baton/core/audit/", "tests/"]
        assert _path_matches_any("agent_baton/core/audit/x.py", paths) is True
        assert _path_matches_any("tests/test_x.py", paths) is True
        assert _path_matches_any("docs/x.md", paths) is False

    def test_normalizes_leading_dotslash(self):
        assert _path_matches_any("./agent_baton/core/audit/x.py", ["agent_baton/core/audit/"]) is True

    def test_empty_allowed_paths_no_match(self):
        assert _path_matches_any("foo.py", []) is False


class TestVerifyStep:
    def test_step_within_scope_passes(self, git_repo: Path):
        step = _step(allowed_paths=["agent_baton/core/audit/"])
        result = _result(files=["agent_baton/core/audit/x.py"])
        v = DispatchVerifier().verify_step(step, result, git_repo)
        assert v.passed is True
        assert v.files_outside_scope == []
        assert v.violations == []
        assert v.inconclusive is False

    def test_step_with_file_outside_scope_fails(self, git_repo: Path):
        step = _step(allowed_paths=["agent_baton/core/audit/"])
        result = _result(files=[
            "agent_baton/core/audit/x.py",
            "agent_baton/core/engine/executor.py",
        ])
        v = DispatchVerifier().verify_step(step, result, git_repo)
        assert v.passed is False
        assert "agent_baton/core/engine/executor.py" in v.files_outside_scope
        assert any("outside allowed_paths" in m for m in v.violations)

    def test_step_with_no_files_changed_inconclusive(self, git_repo: Path):
        step = _step(allowed_paths=["agent_baton/core/audit/"])
        # No files_changed, no commit_hash → inconclusive
        result = _result(files=[], commit_hash="")
        v = DispatchVerifier().verify_step(step, result, git_repo)
        assert v.inconclusive is True
        assert v.passed is True  # inconclusive is NOT a failure
        assert v.violations == []

    def test_branch_mismatch_detected(self, git_repo: Path):
        # Bogus commit hash that does not resolve in the repo
        step = _step(allowed_paths=["agent_baton/core/audit/"])
        result = _result(
            files=["agent_baton/core/audit/x.py"],
            commit_hash="0123456789abcdef0123456789abcdef01234567",
        )
        v = DispatchVerifier().verify_step(step, result, git_repo)
        assert v.branch_mismatch is True
        assert v.passed is False
        assert any("does not resolve" in m for m in v.violations)

    def test_valid_commit_hash_passes_branch_check(self, git_repo: Path):
        # Resolve real HEAD sha
        sha = subprocess.run(
            ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        step = _step(allowed_paths=["agent_baton/core/audit/"])
        result = _result(
            files=["agent_baton/core/audit/x.py"],
            commit_hash=sha,
        )
        v = DispatchVerifier().verify_step(step, result, git_repo)
        assert v.branch_mismatch is False
        assert v.passed is True

    def test_no_allowed_paths_no_violation(self, git_repo: Path):
        # When no sandbox is declared, files_changed cannot be "outside scope".
        step = _step(allowed_paths=[])
        result = _result(files=["anywhere/at/all.py"])
        v = DispatchVerifier().verify_step(step, result, git_repo)
        assert v.passed is True
        assert v.files_outside_scope == []

    def test_git_diff_fallback_when_files_changed_empty(self, git_repo: Path):
        # Modify a file and commit it; record only commit_hash, no files_changed
        target = git_repo / "agent_baton" / "core" / "audit" / "marker.py"
        target.write_text("# updated\n")
        subprocess.run(["git", "-C", str(git_repo), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-q", "-m", "audit-fixture"],
            check=True,
        )
        sha = subprocess.run(
            ["git", "-C", str(git_repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        step = _step(allowed_paths=["agent_baton/core/audit/"])
        result = _result(files=[], commit_hash=sha)
        v = DispatchVerifier().verify_step(step, result, git_repo)
        # Fallback retrieved files_changed via git diff-tree; the file is in scope
        assert v.passed is True
        assert v.inconclusive is False


# ---------------------------------------------------------------------------
# Audit-task aggregation tests
# ---------------------------------------------------------------------------


class TestAuditTask:
    def test_audit_returns_zero_when_all_pass(self, git_repo: Path):
        steps = [
            _step("1.1", allowed_paths=["agent_baton/core/audit/"]),
            _step("1.2", allowed_paths=["tests/"]),
        ]
        results = [
            _result("1.1", files=["agent_baton/core/audit/x.py"]),
            _result("1.2", files=["tests/test_x.py"]),
        ]
        state = _state_with(steps, results)
        report = DispatchVerifier().audit_task(state, git_repo)
        assert report.total_steps == 2
        assert report.compliant_count == 2
        assert report.violation_count == 0
        assert report.has_violations is False

    def test_audit_aggregates_violations(self, git_repo: Path):
        steps = [
            _step("1.1", allowed_paths=["agent_baton/core/audit/"]),
            _step("1.2", allowed_paths=["tests/"]),
        ]
        results = [
            _result("1.1", files=["agent_baton/core/audit/x.py"]),         # OK
            _result("1.2", files=["docs/architecture.md"]),                 # VIOLATION
        ]
        state = _state_with(steps, results)
        report = DispatchVerifier().audit_task(state, git_repo)
        assert report.total_steps == 2
        assert report.compliant_count == 1
        assert report.violation_count == 1
        assert report.has_violations is True
        # Per-step rows preserved
        per_step = {r.step_id: r for r in report.results}
        assert per_step["1.1"].passed is True
        assert per_step["1.2"].passed is False

    def test_audit_returns_nonzero_when_any_fail(self, git_repo: Path):
        steps = [
            _step("1.1", allowed_paths=["agent_baton/core/audit/"]),
        ]
        results = [
            _result("1.1", files=["docs/x.md"]),  # outside scope
        ]
        state = _state_with(steps, results)
        report = DispatchVerifier().audit_task(state, git_repo)
        assert report.has_violations is True

    def test_audit_skips_steps_without_matching_plan(self, git_repo: Path):
        # A result for a step that no longer exists in the plan is ignored
        steps = [_step("1.1", allowed_paths=["agent_baton/core/audit/"])]
        results = [
            _result("1.1", files=["agent_baton/core/audit/x.py"]),
            _result("9.9", files=["whatever.py"]),  # orphan
        ]
        state = _state_with(steps, results)
        report = DispatchVerifier().audit_task(state, git_repo)
        assert report.total_steps == 1   # orphan was skipped
        assert report.compliant_count == 1


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------


def _build_engine_for(state: ExecutionState):
    """Build a fake engine that returns the given state from _load_execution()."""
    class _FakeEngine:
        def __init__(self, st):
            self._st = st
        def _load_execution(self):
            return self._st
    return _FakeEngine(state)


class TestCLI:
    def test_cli_verify_dispatch_outputs_pass(
        self, git_repo: Path, capsys, monkeypatch
    ):
        from agent_baton.cli.commands.execution.execute import (
            _handle_verify_dispatch,
        )
        steps = [_step("1.1", allowed_paths=["agent_baton/core/audit/"])]
        results = [_result("1.1", files=["agent_baton/core/audit/x.py"])]
        state = _state_with(steps, results)
        engine = _build_engine_for(state)
        # context_root is .claude/team-context/ — repo root is two levels up
        ctx = git_repo / ".claude" / "team-context"
        ctx.mkdir(parents=True)

        ns = argparse.Namespace(
            verify_step_id="1.1", output="text", task_id=None,
        )
        _handle_verify_dispatch(ns, engine, ctx)
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "1.1" in out

    def test_cli_verify_dispatch_fail_exits_nonzero(
        self, git_repo: Path, capsys
    ):
        from agent_baton.cli.commands.execution.execute import (
            _handle_verify_dispatch,
        )
        steps = [_step("1.1", allowed_paths=["agent_baton/core/audit/"])]
        results = [_result("1.1", files=["docs/x.md"])]
        state = _state_with(steps, results)
        engine = _build_engine_for(state)
        ctx = git_repo / ".claude" / "team-context"
        ctx.mkdir(parents=True)

        ns = argparse.Namespace(
            verify_step_id="1.1", output="text", task_id=None,
        )
        with pytest.raises(SystemExit) as ei:
            _handle_verify_dispatch(ns, engine, ctx)
        assert ei.value.code == 1
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "outside allowed_paths" in out

    def test_cli_audit_isolation_text_format(
        self, git_repo: Path, capsys
    ):
        from agent_baton.cli.commands.execution.execute import (
            _handle_audit_isolation,
        )
        steps = [
            _step("1.1", allowed_paths=["agent_baton/core/audit/"]),
            _step("1.2", allowed_paths=["tests/"]),
        ]
        results = [
            _result("1.1", files=["agent_baton/core/audit/x.py"]),
            _result("1.2", files=["tests/test_x.py"]),
        ]
        state = _state_with(steps, results)
        engine = _build_engine_for(state)
        ctx = git_repo / ".claude" / "team-context"
        ctx.mkdir(parents=True)

        ns = argparse.Namespace(output="text", task_id=None)
        _handle_audit_isolation(ns, engine, ctx)
        out = capsys.readouterr().out
        assert "Isolation audit" in out
        assert "task-test" in out
        assert "Steps inspected: 2" in out
        assert "Compliant:       2" in out
        assert "Violations:      0" in out

    def test_cli_audit_isolation_json_format(
        self, git_repo: Path, capsys
    ):
        from agent_baton.cli.commands.execution.execute import (
            _handle_audit_isolation,
        )
        steps = [
            _step("1.1", allowed_paths=["agent_baton/core/audit/"]),
            _step("1.2", allowed_paths=["tests/"]),
        ]
        results = [
            _result("1.1", files=["agent_baton/core/audit/x.py"]),  # OK
            _result("1.2", files=["docs/wrong.md"]),                 # FAIL
        ]
        state = _state_with(steps, results)
        engine = _build_engine_for(state)
        ctx = git_repo / ".claude" / "team-context"
        ctx.mkdir(parents=True)

        ns = argparse.Namespace(output="json", task_id=None)
        with pytest.raises(SystemExit) as ei:
            _handle_audit_isolation(ns, engine, ctx)
        assert ei.value.code == 1
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["task_id"] == "task-test"
        assert payload["total_steps"] == 2
        assert payload["compliant_count"] == 1
        assert payload["violation_count"] == 1
        assert any(r["step_id"] == "1.2" and r["passed"] is False
                   for r in payload["results"])

    def test_cli_audit_isolation_zero_exit_on_clean(
        self, git_repo: Path, capsys
    ):
        from agent_baton.cli.commands.execution.execute import (
            _handle_audit_isolation,
        )
        steps = [_step("1.1", allowed_paths=["agent_baton/core/audit/"])]
        results = [_result("1.1", files=["agent_baton/core/audit/x.py"])]
        state = _state_with(steps, results)
        engine = _build_engine_for(state)
        ctx = git_repo / ".claude" / "team-context"
        ctx.mkdir(parents=True)

        ns = argparse.Namespace(output="text", task_id=None)
        # Should NOT raise SystemExit on a clean audit
        _handle_audit_isolation(ns, engine, ctx)
