"""Tests for backend detection and factory functions.

Covers:
- detect_backend() heuristics for all input scenarios
- get_project_storage() returns the correct backend type
- get_pmo_storage() returns PmoSqliteStore
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.storage import detect_backend, get_project_storage, get_pmo_storage
from agent_baton.core.storage.file_backend import FileStorage
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.core.storage.pmo_sqlite import PmoSqliteStore


# ---------------------------------------------------------------------------
# detect_backend()
# ---------------------------------------------------------------------------


class TestDetectBackend:
    def test_empty_dir_returns_sqlite(self, tmp_path: Path) -> None:
        """New projects with no files default to sqlite."""
        result = detect_backend(tmp_path)
        assert result == "sqlite"

    def test_baton_db_present_returns_sqlite(self, tmp_path: Path) -> None:
        """Presence of baton.db takes priority over everything."""
        (tmp_path / "baton.db").touch()
        assert detect_backend(tmp_path) == "sqlite"

    def test_execution_state_json_returns_file(self, tmp_path: Path) -> None:
        """Legacy execution-state.json signals file backend."""
        (tmp_path / "execution-state.json").write_text("{}", encoding="utf-8")
        assert detect_backend(tmp_path) == "file"

    def test_executions_dir_returns_file(self, tmp_path: Path) -> None:
        """Presence of executions/ directory signals legacy file backend."""
        (tmp_path / "executions").mkdir()
        assert detect_backend(tmp_path) == "file"

    def test_baton_db_wins_over_execution_state_json(self, tmp_path: Path) -> None:
        """When both baton.db and execution-state.json exist, sqlite wins."""
        (tmp_path / "baton.db").touch()
        (tmp_path / "execution-state.json").write_text("{}", encoding="utf-8")
        assert detect_backend(tmp_path) == "sqlite"

    def test_baton_db_wins_over_executions_dir(self, tmp_path: Path) -> None:
        """When both baton.db and executions/ exist, sqlite wins."""
        (tmp_path / "baton.db").touch()
        (tmp_path / "executions").mkdir()
        assert detect_backend(tmp_path) == "sqlite"

    def test_unrelated_files_return_sqlite(self, tmp_path: Path) -> None:
        """Unrecognised files don't trigger file backend."""
        (tmp_path / "plan.json").write_text("{}", encoding="utf-8")
        (tmp_path / "README.md").write_text("# Project", encoding="utf-8")
        assert detect_backend(tmp_path) == "sqlite"

    def test_team_context_subdirectory(self, tmp_path: Path) -> None:
        """Detection works correctly when given .claude/team-context/ as root."""
        ctx = tmp_path / ".claude" / "team-context"
        ctx.mkdir(parents=True)
        (ctx / "execution-state.json").write_text("{}", encoding="utf-8")
        assert detect_backend(ctx) == "file"

    def test_nonexistent_dir_returns_sqlite(self, tmp_path: Path) -> None:
        """A path that doesn't exist yet defaults to sqlite (new project)."""
        nonexistent = tmp_path / "brand-new-project"
        assert detect_backend(nonexistent) == "sqlite"


# ---------------------------------------------------------------------------
# get_project_storage() factory
# ---------------------------------------------------------------------------


class TestGetProjectStorage:
    def test_empty_dir_returns_sqlite_storage(self, tmp_path: Path) -> None:
        """Factory returns SqliteStorage for an empty directory."""
        backend = get_project_storage(tmp_path)
        assert isinstance(backend, SqliteStorage)
        backend.close()

    def test_baton_db_returns_sqlite_storage(self, tmp_path: Path) -> None:
        """Factory returns SqliteStorage when baton.db already exists."""
        (tmp_path / "baton.db").touch()
        backend = get_project_storage(tmp_path)
        assert isinstance(backend, SqliteStorage)
        backend.close()

    def test_legacy_files_returns_sqlite_storage(self, tmp_path: Path) -> None:
        """Slice 15: factory returns SqliteStorage even with legacy files.

        ``detect_backend`` still reports 'file' so the migrate command
        can find legacy projects, but ``get_project_storage`` no longer
        instantiates FileStorage.  Operators run ``baton storage migrate``
        to import legacy execution-state.json into SQLite.
        """
        (tmp_path / "execution-state.json").write_text("{}", encoding="utf-8")
        backend = get_project_storage(tmp_path)
        assert isinstance(backend, SqliteStorage)
        backend.close()

    def test_executions_dir_returns_sqlite_storage(self, tmp_path: Path) -> None:
        """Slice 15: factory returns SqliteStorage even with executions/ dir."""
        (tmp_path / "executions").mkdir()
        backend = get_project_storage(tmp_path)
        assert isinstance(backend, SqliteStorage)
        backend.close()

    def test_force_sqlite_overrides_detection(self, tmp_path: Path) -> None:
        """Explicit backend='sqlite' overrides detection even with legacy files."""
        (tmp_path / "execution-state.json").write_text("{}", encoding="utf-8")
        backend = get_project_storage(tmp_path, backend="sqlite")
        assert isinstance(backend, SqliteStorage)
        backend.close()

    def test_force_file_warns_and_falls_back_to_sqlite(
        self, tmp_path: Path,
    ) -> None:
        """Slice 15: backend='file' emits a deprecation warning and
        returns SqliteStorage anyway.  Pinning legacy callers to file
        storage is no longer supported."""
        import warnings as _warnings
        (tmp_path / "baton.db").touch()
        with _warnings.catch_warnings(record=True) as caught:
            _warnings.simplefilter("always")
            backend = get_project_storage(tmp_path, backend="file")
        assert isinstance(backend, SqliteStorage)
        assert any(
            issubclass(w.category, DeprecationWarning)
            and "backend='file' is no longer supported" in str(w.message)
            for w in caught
        )
        backend.close()

    def test_sqlite_db_placed_in_context_root(self, tmp_path: Path) -> None:
        """SqliteStorage db is placed at context_root/baton.db."""
        backend = get_project_storage(tmp_path)
        assert isinstance(backend, SqliteStorage)
        assert backend.db_path == tmp_path / "baton.db"
        backend.close()

    def test_file_storage_context_root_property(self, tmp_path: Path) -> None:
        """FileStorage still exposes context_root when constructed directly.

        Slice 15 removed FileStorage from the factory but it remains
        importable for the export helper (dump_state_to_json) and any
        legacy code that constructs it directly.
        """
        import warnings as _warnings
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore", DeprecationWarning)
            backend = FileStorage(tmp_path)
        assert backend.context_root == tmp_path
        backend.close()

    def test_sqlite_backend_is_functional(self, tmp_path: Path) -> None:
        """The SqliteStorage returned by the factory can write and read data."""
        backend = get_project_storage(tmp_path)
        assert isinstance(backend, SqliteStorage)
        backend.set_active_task("task-factory-test")
        assert backend.get_active_task() == "task-factory-test"
        backend.close()


# ---------------------------------------------------------------------------
# get_pmo_storage() factory
# ---------------------------------------------------------------------------


class TestGetPmoStorage:
    def test_returns_pmo_sqlite_store(self, tmp_path: Path) -> None:
        """get_pmo_storage() always returns a PmoSqliteStore."""
        store = get_pmo_storage(tmp_path / "pmo.db")
        assert isinstance(store, PmoSqliteStore)
        store.close()

    def test_pmo_db_placed_at_given_path(self, tmp_path: Path) -> None:
        """The PMO store uses the exact path provided."""
        db_path = tmp_path / "custom" / "pmo.db"
        store = get_pmo_storage(db_path)
        assert store.db_path == db_path
        store.close()

    def test_pmo_store_is_functional(self, tmp_path: Path) -> None:
        """The store returned by the factory is immediately usable."""
        store = get_pmo_storage(tmp_path / "pmo.db")
        store.add_program("TEST")
        assert "TEST" in store.list_programs()
        store.close()
