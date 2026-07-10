"""Tests for :mod:`agent_baton.core.engine.manager_scope_signal` (M9).

See docs/internal/manager-mode-pmo-plan.md Task 13 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §13.2.
"""
from __future__ import annotations

import subprocess

import pytest

from agent_baton.core.engine.manager_scope_signal import (
    ScopeExpansionSignal,
    derive_scope_expansion_from_diff,
    independent_worktree_diff,
    parse_scope_expansion_signals,
)


def _init_worktree_repo(tmp_path) -> tuple[str, str]:
    """Create a tiny real git repo at *tmp_path*, return (path, base_sha)."""
    repo = str(tmp_path)
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text("x = 1\n")
    run("add", "-A")
    run("commit", "-q", "-m", "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo, base_sha


class TestParseScopeExpansionSignals:
    def test_single_signal_em_dash(self) -> None:
        outcome = (
            "Implemented the service layer.\n"
            "SCOPE_EXPANSION: app/auth/session.py — session metadata needed\n"
            "All tests pass."
        )
        signals = parse_scope_expansion_signals(outcome, step_id="2.1")
        assert signals == [
            ScopeExpansionSignal(
                path="app/auth/session.py",
                reason="session metadata needed",
                step_id="2.1",
            )
        ]

    def test_hyphen_separator_accepted(self) -> None:
        outcome = "SCOPE_EXPANSION: app/reporting/service.py - needs a new dependency"
        signals = parse_scope_expansion_signals(outcome)
        assert len(signals) == 1
        assert signals[0].path == "app/reporting/service.py"
        assert signals[0].reason == "needs a new dependency"

    def test_multiple_signals(self) -> None:
        outcome = (
            "SCOPE_EXPANSION: app/a.py — reason a\n"
            "SCOPE_EXPANSION: app/b.py — reason b\n"
        )
        signals = parse_scope_expansion_signals(outcome)
        assert [s.path for s in signals] == ["app/a.py", "app/b.py"]
        assert [s.reason for s in signals] == ["reason a", "reason b"]

    def test_case_insensitive_prefix(self) -> None:
        outcome = "scope_expansion: app/a.py — lowercase prefix"
        signals = parse_scope_expansion_signals(outcome)
        assert len(signals) == 1
        assert signals[0].path == "app/a.py"

    def test_no_signal(self) -> None:
        assert parse_scope_expansion_signals("Nothing to see here.") == []

    def test_empty_text(self) -> None:
        assert parse_scope_expansion_signals("") == []

    def test_free_text_format_does_not_match(self) -> None:
        """The unrelated adaptive-replanning free-text format
        (``SCOPE_EXPANSION: <description>``, no path/reason split) must
        NOT match this stricter parser -- the two signal formats are
        independent (see module docstring)."""
        outcome = "SCOPE_EXPANSION: Add RBAC middleware to auth module"
        assert parse_scope_expansion_signals(outcome) == []

    def test_empty_path_or_reason_ignored(self) -> None:
        outcome = "SCOPE_EXPANSION:  — reason with no path\n"
        assert parse_scope_expansion_signals(outcome) == []

    def test_deduplicates_identical_pairs(self) -> None:
        outcome = (
            "SCOPE_EXPANSION: app/a.py — dup reason\n"
            "SCOPE_EXPANSION: app/a.py — dup reason\n"
        )
        signals = parse_scope_expansion_signals(outcome)
        assert len(signals) == 1

    def test_caps_at_max_signals_per_step(self) -> None:
        outcome = "\n".join(
            f"SCOPE_EXPANSION: app/{i}.py — reason {i}" for i in range(20)
        )
        signals = parse_scope_expansion_signals(outcome)
        assert len(signals) == 8

    def test_step_id_defaults_empty(self) -> None:
        signals = parse_scope_expansion_signals("SCOPE_EXPANSION: a.py — r")
        assert signals[0].step_id == ""

    def test_reason_captures_rest_of_line_only(self) -> None:
        outcome = (
            "SCOPE_EXPANSION: app/a.py — first reason\n"
            "This next line is not part of the signal.\n"
        )
        signals = parse_scope_expansion_signals(outcome)
        assert signals[0].reason == "first reason"


# ---------------------------------------------------------------------------
# independent_worktree_diff (Phase 3 "Make scope contracts authoritative", 3.2)
# ---------------------------------------------------------------------------


class TestIndependentWorktreeDiff:
    def test_raises_on_missing_path_or_base_sha(self) -> None:
        with pytest.raises(ValueError):
            independent_worktree_diff({"path": "", "base_sha": "abc"})
        with pytest.raises(ValueError):
            independent_worktree_diff({"path": "/tmp/x", "base_sha": ""})
        with pytest.raises(ValueError):
            independent_worktree_diff(None)

    def test_raises_on_invalid_base_sha(self, tmp_path) -> None:
        repo, _base_sha = _init_worktree_repo(tmp_path)
        with pytest.raises(RuntimeError):
            independent_worktree_diff({"path": repo, "base_sha": "not-a-real-sha"})

    def test_detects_committed_change_beyond_base_sha(self, tmp_path) -> None:
        repo, base_sha = _init_worktree_repo(tmp_path)
        (tmp_path / "app" / "b.py").write_text("y = 2\n")
        (tmp_path / "secrets.env").write_text("KEY=1\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "add b + secrets"], cwd=repo, check=True
        )
        changed = independent_worktree_diff({"path": repo, "base_sha": base_sha})
        assert "app/b.py" in changed
        assert "secrets.env" in changed
        # Never touched the pre-existing file.
        assert "app/a.py" not in changed

    def test_detects_uncommitted_change(self, tmp_path) -> None:
        repo, base_sha = _init_worktree_repo(tmp_path)
        (tmp_path / "app" / "c.py").write_text("z = 3\n")
        changed = independent_worktree_diff({"path": repo, "base_sha": base_sha})
        assert "app/c.py" in changed

    def test_ignores_caller_reported_files_entirely(self, tmp_path) -> None:
        """The function takes no files_changed/commit_hash argument at all --
        it can only ever report what git itself says happened."""
        repo, base_sha = _init_worktree_repo(tmp_path)
        changed = independent_worktree_diff({"path": repo, "base_sha": base_sha})
        assert changed == []


# ---------------------------------------------------------------------------
# derive_scope_expansion_from_diff
# ---------------------------------------------------------------------------


class TestDeriveScopeExpansionFromDiff:
    def test_no_contract_means_no_violations(self) -> None:
        assert derive_scope_expansion_from_diff(
            changed_files=["app/a.py", "secrets.env"],
            allowed_paths=[],
            blocked_paths=[],
        ) == []

    def test_file_outside_allowed_paths_is_a_violation(self) -> None:
        violations = derive_scope_expansion_from_diff(
            changed_files=["app/a.py", "infra/deploy.yml"],
            allowed_paths=["app"],
            blocked_paths=[],
            step_id="2.1",
        )
        assert [v.path for v in violations] == ["infra/deploy.yml"]
        assert violations[0].step_id == "2.1"
        assert "diff-verified" in violations[0].reason
        assert "outside allowed_paths" in violations[0].reason

    def test_file_inside_allowed_paths_is_clean(self) -> None:
        violations = derive_scope_expansion_from_diff(
            changed_files=["app/reporting/service.py"],
            allowed_paths=["app"],
            blocked_paths=[],
        )
        assert violations == []

    def test_blocked_path_is_a_violation_even_if_also_allowed(self) -> None:
        violations = derive_scope_expansion_from_diff(
            changed_files=["app/secrets/key.pem"],
            allowed_paths=["app"],
            blocked_paths=["app/secrets"],
        )
        assert len(violations) == 1
        assert violations[0].path == "app/secrets/key.pem"
        assert "blocked_paths" in violations[0].reason

    def test_never_trusts_agent_markers_only_the_diff(self) -> None:
        """No SCOPE_EXPANSION marker is parsed here at all -- only the
        changed_files list, which the caller must supply from
        independent_worktree_diff, never from agent-reported text."""
        violations = derive_scope_expansion_from_diff(
            changed_files=["outside/area.py"],
            allowed_paths=["app"],
            blocked_paths=[],
        )
        assert len(violations) == 1

    def test_unnormalizable_path_fails_closed_as_violation(self) -> None:
        violations = derive_scope_expansion_from_diff(
            changed_files=["../escape.py"],
            allowed_paths=["app"],
            blocked_paths=[],
        )
        assert len(violations) == 1
        assert violations[0].path == "../escape.py"
        assert "could not be normalized" in violations[0].reason

    def test_deduplicates_repeated_changed_files(self) -> None:
        violations = derive_scope_expansion_from_diff(
            changed_files=["infra/x.yml", "infra/x.yml"],
            allowed_paths=["app"],
            blocked_paths=[],
        )
        assert len(violations) == 1
