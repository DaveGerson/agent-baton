"""Tests for agent_baton.core.storage.migration_backup."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_baton.core.storage.migration_backup import (
    backup_db,
    list_backups,
    restore_db,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(path: Path, version: int = 7) -> Path:
    """Create a minimal baton.db with a _schema_version row."""
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _schema_version (version INTEGER NOT NULL)"
    )
    conn.execute("INSERT INTO _schema_version VALUES (?)", (version,))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT)"
    )
    conn.execute("INSERT INTO items VALUES (1, 'alpha')")
    conn.execute("INSERT INTO items VALUES (2, 'beta')")
    conn.commit()
    conn.close()
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBackupDb:
    def test_backup_creates_valid_sqlite_file(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path / "baton.db", version=15)
        bak = backup_db(db)

        assert bak.exists(), "Backup file must be created"
        # Must be openable as a valid SQLite database
        conn = sqlite3.connect(str(bak))
        row = conn.execute("SELECT version FROM _schema_version").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == 15

    def test_backup_filename_contains_version_and_timestamp(
        self, tmp_path: Path
    ) -> None:
        db = _make_db(tmp_path / "baton.db", version=15)
        bak = backup_db(db)

        # Pattern: baton.db.bak-15-<timestamp>
        assert ".db.bak-15-" in bak.name
        # Timestamp portion: 22 chars like 20260425T120000123456Z (with microseconds)
        parts = bak.name.split("-")
        ts_part = parts[-1]  # last segment after final '-'
        assert len(ts_part) == 22, f"Unexpected timestamp format: {ts_part!r}"

    def test_backup_missing_db_returns_path_without_creating_file(
        self, tmp_path: Path
    ) -> None:
        db = tmp_path / "nonexistent.db"
        bak = backup_db(db)

        assert not bak.exists(), "No file should be created for a missing source"
        assert "nonexistent.db.bak-" in bak.name


class TestRestoreDb:
    def test_restore_round_trip_preserves_data(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path / "baton.db", version=15)
        bak = backup_db(db)

        # Corrupt the live db
        db.write_bytes(b"corrupted")

        restore_db(bak, db)

        conn = sqlite3.connect(str(db))
        rows = conn.execute("SELECT name FROM items ORDER BY id").fetchall()
        conn.close()
        assert [r[0] for r in rows] == ["alpha", "beta"]

    def test_restore_missing_backup_is_noop(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path / "baton.db", version=15)
        original_content = db.read_bytes()
        missing_bak = tmp_path / "baton.db.bak-99-99991231T000000Z"

        restore_db(missing_bak, db)  # must not raise

        assert db.read_bytes() == original_content, "DB must be untouched"


class TestListBackups:
    def test_list_returns_sorted_ascending(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path / "baton.db", version=15)

        bak1 = backup_db(db)
        bak2 = backup_db(db)
        bak3 = backup_db(db)

        backups = list_backups(db)
        assert len(backups) >= 3

        # Must be sorted ascending (oldest first = smallest timestamp string)
        names = [b.name for b in backups]
        assert names == sorted(names)

    def test_list_returns_empty_for_nonexistent_parent(
        self, tmp_path: Path
    ) -> None:
        ghost_db = tmp_path / "does_not_exist" / "baton.db"
        result = list_backups(ghost_db)
        assert result == []

    def test_list_ignores_unrelated_files(self, tmp_path: Path) -> None:
        db = _make_db(tmp_path / "baton.db", version=15)
        # Create a file that should not match the backup glob
        (tmp_path / "baton.db.journal").write_text("noise")
        (tmp_path / "other.db.bak-15-20260101T000000Z").write_text("noise")

        bak = backup_db(db)
        backups = list_backups(db)

        # Only the real backup for baton.db must appear
        assert bak in backups
        for b in backups:
            assert b.name.startswith("baton.db.bak-")
