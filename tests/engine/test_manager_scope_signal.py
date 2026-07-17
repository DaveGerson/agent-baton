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

    # -----------------------------------------------------------------
    # Threat model (Phase 3 "Make scope contracts authoritative", 3.3):
    # "unusual git status quoting". ``git status --porcelain`` (the
    # default, newline-delimited form) C-quotes any path containing a
    # space or other "unusual" byte, e.g. ``foo bar.py`` prints as
    # ``"foo bar.py"``. A parser that keeps those literal quote
    # characters as part of the path string produces a mangled path that
    # never matches a real scope-contract entry. -z/NUL-delimited output
    # avoids the whole quoting mechanism -- these tests pin that behavior
    # against a real git repo (not a mock), so a future regression back
    # to the newline-delimited form is caught immediately.
    # -----------------------------------------------------------------

    def test_untracked_filename_with_space_is_not_quote_mangled(self, tmp_path) -> None:
        repo, base_sha = _init_worktree_repo(tmp_path)
        (tmp_path / "app" / "new file.py").write_text("q = 1\n")
        changed = independent_worktree_diff({"path": repo, "base_sha": base_sha})
        assert "app/new file.py" in changed
        # The mangled, quote-wrapped form must never appear.
        assert not any(c.startswith('"') for c in changed)

    def test_tracked_modification_with_space_is_not_quote_mangled(self, tmp_path) -> None:
        repo, base_sha = _init_worktree_repo(tmp_path)
        target = tmp_path / "app" / "spaced name.py"
        target.write_text("q = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add spaced file"], cwd=repo, check=True)
        target.write_text("q = 2\n")  # uncommitted modification
        changed = independent_worktree_diff({"path": repo, "base_sha": base_sha})
        assert "app/spaced name.py" in changed
        assert not any(c.startswith('"') for c in changed)

    def test_rename_reports_only_new_path(self, tmp_path) -> None:
        repo, base_sha = _init_worktree_repo(tmp_path)
        subprocess.run(
            ["git", "mv", "app/a.py", "app/renamed.py"], cwd=repo, check=True
        )
        changed = independent_worktree_diff({"path": repo, "base_sha": base_sha})
        assert "app/renamed.py" in changed
        assert "app/a.py" not in changed
        # The old path must never leak through as its own bogus entry.
        assert len(changed) == 1

    def test_rename_with_space_reports_only_new_unmangled_path(self, tmp_path) -> None:
        repo, base_sha = _init_worktree_repo(tmp_path)
        subprocess.run(
            ["git", "mv", "app/a.py", "app/renamed with space.py"],
            cwd=repo, check=True,
        )
        changed = independent_worktree_diff({"path": repo, "base_sha": base_sha})
        assert "app/renamed with space.py" in changed
        assert not any(c.startswith('"') for c in changed)
        assert "app/a.py" not in changed

    def test_deletion_is_detected(self, tmp_path) -> None:
        repo, base_sha = _init_worktree_repo(tmp_path)
        (tmp_path / "app" / "a.py").unlink()
        changed = independent_worktree_diff({"path": repo, "base_sha": base_sha})
        assert "app/a.py" in changed

    def test_committed_deletion_is_detected(self, tmp_path) -> None:
        repo, base_sha = _init_worktree_repo(tmp_path)
        subprocess.run(["git", "rm", "-q", "app/a.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "remove a.py"], cwd=repo, check=True)
        changed = independent_worktree_diff({"path": repo, "base_sha": base_sha})
        assert "app/a.py" in changed

    def test_entirely_new_untracked_directory_reports_a_path_under_it(self, tmp_path) -> None:
        """git collapses a wholly-new untracked directory to a single
        ``dir/`` entry rather than listing every file inside it. The
        collapsed entry must still be usable evidence -- see
        ``paths_overlap``'s bidirectional containment, exercised together
        with this in ``TestDeriveScopeExpansionFromDiff``."""
        repo, base_sha = _init_worktree_repo(tmp_path)
        (tmp_path / "blocked").mkdir()
        (tmp_path / "blocked" / "secret.env").write_text("KEY=1\n")
        changed = independent_worktree_diff({"path": repo, "base_sha": base_sha})
        assert any(c.rstrip("/") == "blocked" for c in changed)


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

    # -----------------------------------------------------------------
    # Threat model (Phase 3 "Make scope contracts authoritative", 3.3)
    # -----------------------------------------------------------------

    def test_absolute_path_in_changed_files_fails_closed(self) -> None:
        """A changed-file entry can never legitimately be absolute -- git
        always reports repo-relative paths -- but a malformed/adversarial
        entry must still fail closed rather than silently normalize."""
        violations = derive_scope_expansion_from_diff(
            changed_files=["/etc/passwd"],
            allowed_paths=["app"],
            blocked_paths=[],
        )
        assert len(violations) == 1
        assert violations[0].path == "/etc/passwd"
        assert "could not be normalized" in violations[0].reason

    def test_blocked_over_allowed_precedence_even_when_nested_inside_allowed(self) -> None:
        """A path nested several levels inside an allowed area is still a
        violation when a blocked entry covers it -- blocked always wins,
        regardless of nesting depth relative to the allowed root."""
        violations = derive_scope_expansion_from_diff(
            changed_files=["app/a/b/c/secrets/key.pem"],
            allowed_paths=["app"],
            blocked_paths=["app/a/b/c/secrets"],
        )
        assert len(violations) == 1
        assert "blocked_paths" in violations[0].reason

    def test_blocklist_only_contract_blocked_path_change_is_a_violation(self) -> None:
        """A step whose contract is blocked_paths-only (no allowed_paths
        at all -- e.g. 'touch anything except these areas') must still
        catch a real, plain-ASCII change inside the blocked area."""
        violations = derive_scope_expansion_from_diff(
            changed_files=["secrets/key.pem"],
            allowed_paths=[],
            blocked_paths=["secrets"],
        )
        assert len(violations) == 1
        assert violations[0].path == "secrets/key.pem"

    def test_blocklist_only_contract_permits_unrelated_changes(self) -> None:
        """The flip side of the above: with no allowed_paths declared, a
        change OUTSIDE the blocked area is not a violation (a blocklist
        is a denylist, not an implicit allowlist of everything else being
        forbidden)."""
        violations = derive_scope_expansion_from_diff(
            changed_files=["app/service.py"],
            allowed_paths=[],
            blocked_paths=["secrets"],
        )
        assert violations == []

    def test_blocklist_only_contract_is_not_defeated_by_git_status_quoting(self) -> None:
        """Regression: ``independent_worktree_diff`` must hand this
        function the REAL path, not a git-quoted ``"secrets/key file.pem"``
        string (literal quote characters included). Before the -z fix,
        such a mangled path failed to match ``blocked_paths`` -- and with
        no ``allowed_paths`` to fall back on, the violation was silently
        dropped. This test exercises this function directly with the
        already-correct (unmangled) path -- the git-integration half of
        the regression lives in ``TestIndependentWorktreeDiff``."""
        violations = derive_scope_expansion_from_diff(
            changed_files=["secrets/key file.pem"],
            allowed_paths=[],
            blocked_paths=["secrets"],
        )
        assert len(violations) == 1
        assert violations[0].path == "secrets/key file.pem"

    def test_quote_mangled_path_would_have_bypassed_blocklist_only_contract(self) -> None:
        """Documents the exact shape of the pre-fix bypass: a literally
        quote-wrapped path (what a naive newline/space-delimited parse of
        ``git status --porcelain`` output would have produced for
        ``secrets/key file.pem``) matches neither ``blocked_paths`` nor
        (being absent) any ``allowed_paths`` -- so it is reported clean.
        This is why ``independent_worktree_diff`` must never hand this
        function a quote-mangled string; see the -z fix and
        ``TestIndependentWorktreeDiff``'s quoting regressions."""
        violations = derive_scope_expansion_from_diff(
            changed_files=['"secrets/key file.pem"'],
            allowed_paths=[],
            blocked_paths=["secrets"],
        )
        assert violations == []

    def test_generated_path_explicitly_blocked_is_still_a_violation(self) -> None:
        """The generated-file policy (``is_generated_path``) only ever
        excludes build/tooling output from *inferred* write-scope
        evidence upstream (ScopeMapBuilder); it must have no bearing at
        all on enforcement here -- an operator can explicitly block (or
        allow) a generated directory like any other path."""
        violations = derive_scope_expansion_from_diff(
            changed_files=["dist/bundle.js"],
            allowed_paths=["app"],
            blocked_paths=["dist"],
        )
        assert len(violations) == 1
        assert violations[0].path == "dist/bundle.js"

    def test_generated_path_explicitly_allowed_is_clean(self) -> None:
        violations = derive_scope_expansion_from_diff(
            changed_files=["dist/bundle.js"],
            allowed_paths=["dist"],
            blocked_paths=[],
        )
        assert violations == []

    def test_new_untracked_directory_collapse_still_flags_blocked_contents(self) -> None:
        """Pairs with ``TestIndependentWorktreeDiff.
        test_entirely_new_untracked_directory_reports_a_path_under_it``:
        git may report only the wholly-new parent directory (``blocked``)
        rather than every file inside it. ``paths_overlap``'s
        bidirectional containment (either side may be the more specific
        one) means the coarser directory entry still overlaps a
        finer-grained blocked_paths entry underneath it."""
        violations = derive_scope_expansion_from_diff(
            changed_files=["blocked"],
            allowed_paths=[],
            blocked_paths=["blocked/secrets/key.pem"],
        )
        assert len(violations) == 1
        assert violations[0].path == "blocked"

    def test_double_star_filename_does_not_glob_match_any_allowed_path(self) -> None:
        """Adversarial (phase 3 review): a changed file whose path is
        literally ``**`` (or ends in a ``**`` segment) must be treated as
        a concrete out-of-scope file, NOT glob-interpreted into matching
        every allowed path. Using bidirectional ``paths_overlap`` on the
        allow-list check let a root-level ``**`` entry match any allowed
        prefix -- an out-of-scope diff silently accepted."""
        violations = derive_scope_expansion_from_diff(
            changed_files=["**", "app/**"],
            allowed_paths=["app/reporting/service.py"],
            blocked_paths=[],
            step_id="s1",
        )
        flagged = {v.path for v in violations}
        assert "**" in flagged
        assert "app/**" in flagged

    def test_double_star_file_inside_an_allowed_dir_is_still_clean(self) -> None:
        """The fix must not over-correct: a file literally named ``**``
        that genuinely lives under an allowed directory is in-scope."""
        violations = derive_scope_expansion_from_diff(
            changed_files=["app/**"],
            allowed_paths=["app"],
            blocked_paths=[],
        )
        assert violations == []

    def test_collapsed_new_directory_not_swallowed_by_more_specific_allowed_file(
        self,
    ) -> None:
        """Adversarial (phase 3 review): git collapses a wholly-new
        untracked directory to a single ``newdir/`` entry. When the
        contract allows only a specific FILE inside it
        (``newdir/allowed.py``), the coarse ``newdir`` entry must NOT be
        accepted as in-scope -- an out-of-scope sibling (``newdir/evil.py``)
        created in the same new directory would ride in on it. The
        collapsed-directory ambiguity must fail closed (flagged)."""
        violations = derive_scope_expansion_from_diff(
            changed_files=["newdir/"],
            allowed_paths=["newdir/allowed.py"],
            blocked_paths=[],
        )
        assert len(violations) == 1
        assert violations[0].path == "newdir"

    def test_directory_allowed_still_admits_its_collapsed_form(self) -> None:
        """Counterpart to the above: when the whole directory is allowed
        (``newdir``), its collapsed ``newdir/`` diff entry is in-scope --
        no false positive."""
        violations = derive_scope_expansion_from_diff(
            changed_files=["newdir/"],
            allowed_paths=["newdir"],
            blocked_paths=[],
        )
        assert violations == []
