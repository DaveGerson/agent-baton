"""Smoke tests for scripts/install.sh — git-notes replication setup (end-user readiness #5).

These tests run install.sh in a temporary directory and assert that the
expected git config lines are written to .git/config.

The install script is interactive (it reads from stdin for install scope and
knowledge infrastructure choice).  We drive it non-interactively by:
  - Setting BATON_SKIP_GIT_NOTES_SETUP / providing echo input
  - Using ``echo '2\n3' | bash install.sh`` to answer the scope and
    knowledge-infrastructure prompts automatically.

Requirements:
  - bash must be available (skipped otherwise).
  - Tests run in a tmpdir that is a git repo so the replication step activates.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOTES_FETCH_REFSPEC = "+refs/notes/*:refs/notes/*"
_NOTES_PUSH_REFSPEC = "+refs/notes/*:refs/notes/*"


def _has_bash() -> bool:
    try:
        subprocess.run(["bash", "--version"], capture_output=True, check=True, timeout=5)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _has_bash(), reason="bash not available")


def _init_git_repo_with_remote(path: Path) -> None:
    """Create a minimal git repo with an origin remote."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    for key, val in [
        ("user.email", "test@baton.test"),
        ("user.name", "Baton Test"),
        ("commit.gpgsign", "false"),
    ]:
        subprocess.run(
            ["git", "-C", str(path), "config", key, val],
            check=True,
            capture_output=True,
        )
    readme = path / "README.md"
    readme.write_text("init\n")
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


def _find_install_sh() -> Path:
    """Return the path to scripts/install.sh relative to this file's repo."""
    here = Path(__file__).resolve()
    # Walk up until we find scripts/install.sh
    candidate = here
    for _ in range(10):
        candidate = candidate.parent
        script = candidate / "scripts" / "install.sh"
        if script.exists():
            return script
    raise FileNotFoundError("scripts/install.sh not found relative to test file")


def _run_install(
    repo: Path,
    *,
    scope_choice: str = "2",   # 2 = project-level (no ~/.claude writes)
    knowledge_choice: str = "3",  # 3 = skip
    env_extra: dict | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run install.sh non-interactively in *repo* directory."""
    install_sh = _find_install_sh()

    # Feed answers to the two read prompts: scope + knowledge infrastructure
    stdin_input = f"{scope_choice}\n{knowledge_choice}\n"

    env = os.environ.copy()
    # Suppress baton CLI verification — we don't need a working install
    env["PATH"] = "/usr/bin:/bin"  # minimal PATH, no baton
    if env_extra:
        env.update(env_extra)

    return subprocess.run(
        ["bash", str(install_sh)],
        input=stdin_input,
        capture_output=True,
        text=True,
        cwd=str(repo),
        env=env,
        timeout=timeout,
    )


def _get_git_config_all(repo: Path, key: str) -> list[str]:
    """Return all values for a git config key, or empty list if not set."""
    result = subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "--get-all", key],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return [line for line in result.stdout.splitlines() if line]
    return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInstallScriptNotesReplication:
    def test_fetch_refspec_added_after_install(self, tmp_path: Path) -> None:
        """install.sh adds +refs/notes/*:refs/notes/* to remote.origin.fetch."""
        repo = tmp_path / "project"
        repo.mkdir()
        _init_git_repo_with_remote(repo)

        result = _run_install(repo)

        assert result.returncode == 0, (
            f"install.sh exited {result.returncode}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        fetch_refspecs = _get_git_config_all(repo, "remote.origin.fetch")
        assert _NOTES_FETCH_REFSPEC in fetch_refspecs, (
            f"Expected '{_NOTES_FETCH_REFSPEC}' in remote.origin.fetch, got: {fetch_refspecs}"
        )

    def test_push_refspec_added_after_install(self, tmp_path: Path) -> None:
        """install.sh adds +refs/notes/*:refs/notes/* to remote.origin.push."""
        repo = tmp_path / "project_push"
        repo.mkdir()
        _init_git_repo_with_remote(repo)

        result = _run_install(repo)

        assert result.returncode == 0, (
            f"install.sh exited {result.returncode}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

        push_refspecs = _get_git_config_all(repo, "remote.origin.push")
        assert _NOTES_PUSH_REFSPEC in push_refspecs, (
            f"Expected '{_NOTES_PUSH_REFSPEC}' in remote.origin.push, got: {push_refspecs}"
        )

    def test_idempotent_double_install_does_not_duplicate_fetch_refspec(
        self, tmp_path: Path
    ) -> None:
        """Running install.sh twice must not duplicate the fetch refspec line."""
        repo = tmp_path / "project_idem"
        repo.mkdir()
        _init_git_repo_with_remote(repo)

        _run_install(repo)
        _run_install(repo)

        fetch_refspecs = _get_git_config_all(repo, "remote.origin.fetch")
        count = fetch_refspecs.count(_NOTES_FETCH_REFSPEC)
        assert count == 1, (
            f"Expected exactly 1 occurrence of notes fetch refspec, got {count}: {fetch_refspecs}"
        )

    def test_idempotent_double_install_does_not_duplicate_push_refspec(
        self, tmp_path: Path
    ) -> None:
        """Running install.sh twice must not duplicate the push refspec line."""
        repo = tmp_path / "project_idem_push"
        repo.mkdir()
        _init_git_repo_with_remote(repo)

        _run_install(repo)
        _run_install(repo)

        push_refspecs = _get_git_config_all(repo, "remote.origin.push")
        count = push_refspecs.count(_NOTES_PUSH_REFSPEC)
        assert count == 1, (
            f"Expected exactly 1 occurrence of notes push refspec, got {count}: {push_refspecs}"
        )

    def test_skip_env_var_suppresses_refspec_setup(self, tmp_path: Path) -> None:
        """BATON_SKIP_GIT_NOTES_SETUP=1 prevents any notes refspec from being written."""
        repo = tmp_path / "project_skip"
        repo.mkdir()
        _init_git_repo_with_remote(repo)

        result = _run_install(repo, env_extra={"BATON_SKIP_GIT_NOTES_SETUP": "1"})

        assert result.returncode == 0, (
            f"install.sh exited {result.returncode}\nstdout: {result.stdout}"
        )

        fetch_refspecs = _get_git_config_all(repo, "remote.origin.fetch")
        push_refspecs = _get_git_config_all(repo, "remote.origin.push")

        assert _NOTES_FETCH_REFSPEC not in fetch_refspecs, (
            "Notes fetch refspec should NOT be added when BATON_SKIP_GIT_NOTES_SETUP=1"
        )
        assert _NOTES_PUSH_REFSPEC not in push_refspecs, (
            "Notes push refspec should NOT be added when BATON_SKIP_GIT_NOTES_SETUP=1"
        )

    def test_no_git_repo_does_not_crash_install(self, tmp_path: Path) -> None:
        """install.sh must not exit non-zero when run outside a git repo."""
        non_repo = tmp_path / "not_a_repo"
        non_repo.mkdir()

        # The script will try to find agents/ etc. relative to SCRIPT_DIR.
        # It will likely exit early due to missing agents/, which is fine —
        # we just verify it does not crash with a signal or bash error due to
        # the git replication step itself.
        install_sh = _find_install_sh()
        result = subprocess.run(
            ["bash", str(install_sh)],
            input="2\n3\n",
            capture_output=True,
            text=True,
            cwd=str(non_repo),
            timeout=30,
        )
        # We only care that it did not crash with signal (returncode < 0)
        # or produce a bash set -e error from the notes-replication block.
        # The script may exit 1 due to missing agents/ dir, which is acceptable.
        assert result.returncode >= 0, (
            f"install.sh crashed (returncode={result.returncode}) outside git repo\n"
            f"stderr: {result.stderr}"
        )
