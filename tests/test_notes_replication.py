"""Tests for git-notes replication configuration helpers (end-user readiness #5).

Covers:
- verify_notes_replication_configured detects missing fetch refspec
- verify_notes_replication_configured detects present fetch refspec
- maybe_warn_replication emits BEAD_WARNING when replication is unconfigured
- maybe_warn_replication is silent when replication is configured
- maybe_warn_replication fires at most once per process session
- BATON_SKIP_GIT_NOTES_SETUP=1 suppresses the warning
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

import agent_baton.core.engine.notes_replication as notes_replication_module
from agent_baton.core.engine.notes_replication import (
    _NOTES_FETCH_REFSPEC,
    maybe_warn_replication,
    verify_notes_replication_configured,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOTES_REFSPEC = "+refs/notes/*:refs/notes/*"


def _init_git_repo(path: Path, *, with_remote: bool = False) -> None:
    """Initialise a minimal git repo with one commit."""
    env_extras = [
        ("user.email", "test@baton.test"),
        ("user.name", "Baton Test"),
        ("commit.gpgsign", "false"),
    ]
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    for key, val in env_extras:
        subprocess.run(
            ["git", "-C", str(path), "config", key, val],
            check=True,
            capture_output=True,
        )
    readme = path / "README.md"
    readme.write_text("baton test\n")
    subprocess.run(
        ["git", "-C", str(path), "add", "README.md"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    if with_remote:
        # Add a fake origin so remote.origin.fetch lines exist in config
        subprocess.run(
            [
                "git",
                "-C",
                str(path),
                "remote",
                "add",
                "origin",
                "https://example.com/repo.git",
            ],
            check=True,
            capture_output=True,
        )


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    return repo


@pytest.fixture()
def git_repo_with_remote(tmp_path: Path) -> Path:
    repo = tmp_path / "repo_remote"
    repo.mkdir()
    _init_git_repo(repo, with_remote=True)
    return repo


# ---------------------------------------------------------------------------
# verify_notes_replication_configured
# ---------------------------------------------------------------------------


class TestVerifyNotesReplicationConfigured:
    def test_detects_missing_refspec(self, git_repo_with_remote: Path) -> None:
        """Returns False when +refs/notes/*:refs/notes/* is not in fetch config."""
        result = verify_notes_replication_configured(git_repo_with_remote)
        assert result is False

    def test_detects_present_refspec(self, git_repo_with_remote: Path) -> None:
        """Returns True after the wildcard notes refspec is added."""
        subprocess.run(
            [
                "git",
                "-C",
                str(git_repo_with_remote),
                "config",
                "--add",
                "remote.origin.fetch",
                _NOTES_REFSPEC,
            ],
            check=True,
            capture_output=True,
        )
        result = verify_notes_replication_configured(git_repo_with_remote)
        assert result is True

    def test_returns_false_for_non_git_dir(self, tmp_path: Path) -> None:
        """Returns False (no raise) when path is not a git repo."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()
        result = verify_notes_replication_configured(non_repo)
        assert result is False

    def test_partial_refspec_does_not_match(self, git_repo_with_remote: Path) -> None:
        """A narrower refspec (e.g. single ref) must not satisfy the check."""
        subprocess.run(
            [
                "git",
                "-C",
                str(git_repo_with_remote),
                "config",
                "--add",
                "remote.origin.fetch",
                "+refs/notes/baton-beads:refs/notes/baton-beads",
            ],
            check=True,
            capture_output=True,
        )
        # The narrow refspec is NOT the wildcard — should still return False
        result = verify_notes_replication_configured(git_repo_with_remote)
        assert result is False

    def test_idempotent_double_add(self, git_repo_with_remote: Path) -> None:
        """Adding the refspec twice and checking still returns True."""
        for _ in range(2):
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(git_repo_with_remote),
                    "config",
                    "--add",
                    "remote.origin.fetch",
                    _NOTES_REFSPEC,
                ],
                check=True,
                capture_output=True,
            )
        result = verify_notes_replication_configured(git_repo_with_remote)
        assert result is True


# ---------------------------------------------------------------------------
# maybe_warn_replication — per-session warning behaviour
# ---------------------------------------------------------------------------


class TestMaybeWarnReplication:
    """The per-process warning flag must be reset before each test."""

    @pytest.fixture(autouse=True)
    def reset_warning_flag(self) -> None:
        """Reset the module-level flag so each test starts clean."""
        notes_replication_module._replication_warning_emitted = False
        yield
        notes_replication_module._replication_warning_emitted = False

    def test_emits_warning_when_unconfigured(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A BEAD_WARNING is logged on the first call when replication is not set up."""
        with caplog.at_level(
            logging.WARNING, logger="agent_baton.core.engine.notes_replication"
        ):
            maybe_warn_replication(git_repo)

        warning_msgs = [r.message for r in caplog.records if "BEAD_WARNING" in r.message]
        assert warning_msgs, "expected at least one BEAD_WARNING log line"
        assert "replication not configured" in warning_msgs[0]

    def test_warning_emitted_only_once_per_session(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The BEAD_WARNING fires at most once per process session."""
        with caplog.at_level(
            logging.WARNING, logger="agent_baton.core.engine.notes_replication"
        ):
            maybe_warn_replication(git_repo)
            maybe_warn_replication(git_repo)

        warning_count = sum(
            1
            for r in caplog.records
            if "BEAD_WARNING" in r.message and "replication" in r.message
        )
        assert warning_count == 1, f"expected 1 replication warning, got {warning_count}"

    def test_silent_when_configured(
        self, git_repo_with_remote: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No BEAD_WARNING is logged when the fetch refspec is already present."""
        # Pre-configure the refspec
        subprocess.run(
            [
                "git",
                "-C",
                str(git_repo_with_remote),
                "config",
                "--add",
                "remote.origin.fetch",
                _NOTES_REFSPEC,
            ],
            check=True,
            capture_output=True,
        )

        with caplog.at_level(
            logging.WARNING, logger="agent_baton.core.engine.notes_replication"
        ):
            maybe_warn_replication(git_repo_with_remote)

        replication_warnings = [
            r for r in caplog.records if "replication not configured" in r.message
        ]
        assert replication_warnings == [], (
            "expected no replication warning when refspec is configured"
        )

    def test_silent_when_opt_out_env_var_set(
        self,
        git_repo: Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """BATON_SKIP_GIT_NOTES_SETUP=1 suppresses the replication warning."""
        monkeypatch.setenv("BATON_SKIP_GIT_NOTES_SETUP", "1")

        with caplog.at_level(
            logging.WARNING, logger="agent_baton.core.engine.notes_replication"
        ):
            maybe_warn_replication(git_repo)

        replication_warnings = [
            r for r in caplog.records if "replication not configured" in r.message
        ]
        assert replication_warnings == [], (
            "expected no replication warning when BATON_SKIP_GIT_NOTES_SETUP=1"
        )
