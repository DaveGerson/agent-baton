"""bd-ce64: ``baton beads`` must find the project's baton.db when invoked
from a worktree, sub-directory, or via an explicit ``BATON_DB_PATH``.

Validates :func:`agent_baton.cli.commands.bead_cmd._resolve_db_path`:

  1. ``BATON_DB_PATH`` env var wins (absolute and relative resolved to cwd).
  2. ``.claude/team-context/baton.db`` directly under cwd is preferred when
     present (legacy behaviour).
  3. When the cwd has no DB, the resolver walks parent directories and
     returns the first ``<parent>/.claude/team-context/baton.db`` found —
     this is the worktree-friendly path.
  4. Returns the legacy cwd-relative default when nothing is discoverable
     (so ``_get_or_create_bead_store`` can still create one).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_baton.cli.commands import bead_cmd


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BATON_DB_PATH", raising=False)


def _make_db(parent: Path) -> Path:
    db = parent / ".claude" / "team-context" / "baton.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.touch()
    return db.resolve()


class TestResolveDbPath:
    def test_env_var_override_absolute_wins(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Even with a cwd-local DB present, the env var must win.
        cwd_db = _make_db(tmp_path)
        env_db = tmp_path / "custom" / "elsewhere.db"
        env_db.parent.mkdir(parents=True, exist_ok=True)
        env_db.touch()

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("BATON_DB_PATH", str(env_db))

        resolved = bead_cmd._resolve_db_path()
        assert resolved == env_db.resolve()
        assert resolved != cwd_db

    def test_cwd_local_db_preferred_when_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        cwd_db = _make_db(tmp_path)
        monkeypatch.chdir(tmp_path)
        assert bead_cmd._resolve_db_path() == cwd_db

    def test_walks_up_to_parent_with_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Project layout:
        #   tmp_path/
        #       .claude/team-context/baton.db   ← the real DB
        #       worktrees/
        #           feature-x/                  ← we cd here, no local DB
        project_db = _make_db(tmp_path)
        nested = tmp_path / "worktrees" / "feature-x"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)

        resolved = bead_cmd._resolve_db_path()
        assert resolved == project_db, (
            f"expected upward walk to find {project_db}, got {resolved}"
        )

    def test_walks_through_multiple_levels(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        project_db = _make_db(tmp_path)
        deep = tmp_path / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        assert bead_cmd._resolve_db_path() == project_db

    def test_no_db_anywhere_returns_cwd_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        resolved = bead_cmd._resolve_db_path()
        # Must point at the conventional path under cwd so create-mode can
        # mkdir + open it.
        assert resolved.name == "baton.db"
        assert resolved.parent.name == "team-context"
        assert resolved.parent.parent.name == ".claude"
        assert not resolved.exists()


class TestGetBeadStoreFromWorktree:
    def test_get_bead_store_returns_store_when_walked_up(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sanity: the resolver hooks through to _get_bead_store."""
        from agent_baton.core.storage.connection import ConnectionManager
        from agent_baton.core.storage import schema as schema_mod

        # Create a real, schema-initialised DB under tmp_path.
        db_path = tmp_path / ".claude" / "team-context" / "baton.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # Initialise schema by opening via ConnectionManager.
        ConnectionManager(db_path).get_connection().close()

        # cd into a nested worktree directory with no local DB.
        nested = tmp_path / "worktrees" / "feature-y"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)

        store = bead_cmd._get_bead_store()
        assert store is not None, "BeadStore should be discoverable from worktree"

    def test_get_bead_store_returns_none_when_nothing_anywhere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        empty = tmp_path / "no-project"
        empty.mkdir()
        monkeypatch.chdir(empty)
        assert bead_cmd._get_bead_store() is None


class TestDeriveProjectRootDoesNotOvershoot:
    """bd-p6k: ``_derive_project_root``'s ``.claude/team-context/baton.db``
    fallback must only strip three path components when db_path actually
    matches that shape -- otherwise it must fall back to db_path's own
    parent, never an ancestor above the caller's directory. The sibling bug
    in ``bead_backend._derive_repo_root`` (see tests/test_bd_backend.py) is
    what actually corrupted tests/test_worktree_manager.py on CI, but this
    module has the identical fallback shape and must be guarded too.
    """

    def test_conventional_shape_unchanged(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".claude" / "team-context" / "baton.db"
        assert bead_cmd._derive_project_root(db_path) == tmp_path

    def test_shallow_db_path_does_not_overshoot(self, tmp_path: Path) -> None:
        shallow_db = tmp_path / "baton.db"
        root = bead_cmd._derive_project_root(shallow_db)
        assert root == tmp_path
        assert root != tmp_path.parent
