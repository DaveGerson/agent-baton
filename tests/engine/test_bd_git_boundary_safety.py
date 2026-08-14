"""``BdClient`` must never create a git repository somewhere destructive.

``_ensure_git_boundary`` exists to stop bd's ancestor-walking workspace
discovery from escaping upward, and it does that by running ``git init`` when
``repo_root`` has no ``.git``.  The escape it guards against is a contained
annoyance; the guard itself is a filesystem side effect on a path derived by
walking *up* the tree, which is how it can arrive at ``$HOME`` or ``/``.

These tests pin the refusals rather than the mechanism, so a future rewrite
that swaps ``git init`` for bd's own ``--db``/``BEADS_DIR`` scoping still has
to satisfy them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_baton.core.engine.bd_client import BdClient


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, check=True)


class TestGitBoundaryRefusals:
    """Paths ``_ensure_git_boundary`` must decline to initialise."""

    def test_home_directory_is_refused(self) -> None:
        reason = BdClient._git_boundary_refusal(Path.home())
        assert reason is not None
        assert "home" in reason.lower()

    def test_filesystem_root_is_refused(self) -> None:
        assert BdClient._git_boundary_refusal(Path("/")) is not None

    def test_top_level_directory_is_refused(self, tmp_path: Path) -> None:
        # A single-segment path such as /srv is never a project root.
        assert BdClient._git_boundary_refusal(Path("/tmp")) is not None

    def test_directory_inside_an_existing_repo_is_refused(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        (repo / "sub").mkdir(parents=True)
        _git(repo, "init", "-q")

        reason = BdClient._git_boundary_refusal(repo / "sub")

        assert reason is not None, (
            "initialising a nested repo punches a hole in the parent's index: "
            "`git add -A` then fails with 'does not have a commit checked out'"
        )
        assert "already inside" in reason

    def test_nonexistent_path_is_refused(self, tmp_path: Path) -> None:
        assert BdClient._git_boundary_refusal(tmp_path / "nope") is not None

    def test_ordinary_project_directory_is_allowed(self, tmp_path: Path) -> None:
        project = tmp_path / "workspace" / "project"
        project.mkdir(parents=True)
        assert BdClient._git_boundary_refusal(project) is None


class TestGitBoundaryDoesNotWriteWhereRefused:
    """The refusal must actually prevent the side effect, not merely report it."""

    def test_no_repo_is_created_inside_an_existing_repo(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        nested = repo / "sub"
        nested.mkdir(parents=True)
        _git(repo, "init", "-q")

        BdClient(repo_root=nested)._ensure_git_boundary()

        assert not (nested / ".git").exists(), (
            "a nested repository was created despite the refusal"
        )

    def test_parent_repo_can_still_stage_everything(self, tmp_path: Path) -> None:
        """The concrete breakage the nested-repo refusal exists to prevent."""
        repo = tmp_path / "repo"
        nested = repo / "sub"
        nested.mkdir(parents=True)
        _git(repo, "init", "-q")
        (nested / "file.txt").write_text("content\n")

        BdClient(repo_root=nested)._ensure_git_boundary()

        result = subprocess.run(
            ["git", "add", "-A"], cwd=str(repo), capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert "does not have a commit checked out" not in result.stderr

    def test_boundary_is_created_for_a_legitimate_project_root(
        self, tmp_path: Path
    ) -> None:
        project = tmp_path / "workspace" / "project"
        project.mkdir(parents=True)

        BdClient(repo_root=project)._ensure_git_boundary()

        assert (project / ".git").exists(), (
            "the guard must still do its job for ordinary project roots"
        )

    @pytest.mark.parametrize("target", [Path("/"), Path.home()])
    def test_dangerous_targets_are_never_initialised(self, target: Path) -> None:
        """Belt and braces: assert refusal without invoking the side effect."""
        assert BdClient._git_boundary_refusal(target) is not None
