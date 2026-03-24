"""Tests for PMO migration from pmo.db to central.db.

Covers:
- _maybe_migrate_pmo(): copies PMO tables from pmo.db into central.db
- Idempotency: second call is a no-op (marker present)
- No-source path: pmo.db absent writes marker and returns False
- get_pmo_central_store(): returns PmoSqliteStore backed by central.db
- PmoScanner reads from central.db-backed store
- Backward compatibility: PmoSqliteStore pointed at central.db has all methods
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from agent_baton.core.storage.central import CentralStore, _maybe_migrate_pmo
from agent_baton.core.storage.pmo_sqlite import PmoSqliteStore
from agent_baton.core.storage import get_pmo_central_store
from agent_baton.models.pmo import PmoCard, PmoProject, PmoSignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pmo_db(path: Path) -> PmoSqliteStore:
    """Create a populated pmo.db at *path* and return the store."""
    store = PmoSqliteStore(path)
    store.register_project(
        PmoProject(
            project_id="nds",
            name="NDS Project",
            path="/srv/nds",
            program="NDS",
        )
    )
    store.register_project(
        PmoProject(
            project_id="atl",
            name="ATL Project",
            path="/srv/atl",
            program="ATL",
        )
    )
    store.add_program("NDS")
    store.add_program("ATL")
    store.add_signal(
        PmoSignal(
            signal_id="sig-001",
            signal_type="bug",
            title="Login broken",
            severity="high",
        )
    )
    store.archive_card(
        PmoCard(
            card_id="task-001",
            project_id="nds",
            program="NDS",
            title="Build login",
            column="deployed",
        )
    )
    store.close()
    return store


def _central_store(tmp_path: Path) -> CentralStore:
    return CentralStore(tmp_path / "central.db")


# ---------------------------------------------------------------------------
# _maybe_migrate_pmo
# ---------------------------------------------------------------------------


class TestMaybeMigratePmo:
    def test_migrates_projects(self, tmp_path: Path) -> None:
        pmo_db = tmp_path / "pmo.db"
        central_db = tmp_path / "central.db"
        marker = tmp_path / ".pmo-migrated"
        _make_pmo_db(pmo_db)

        result = _maybe_migrate_pmo(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=marker,
        )

        assert result is True
        store = CentralStore(central_db)
        projects = store.query("SELECT project_id FROM projects ORDER BY project_id")
        store.close()
        ids = [r["project_id"] for r in projects]
        assert "nds" in ids
        assert "atl" in ids

    def test_migrates_signals(self, tmp_path: Path) -> None:
        pmo_db = tmp_path / "pmo.db"
        central_db = tmp_path / "central.db"
        marker = tmp_path / ".pmo-migrated"
        _make_pmo_db(pmo_db)

        _maybe_migrate_pmo(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=marker,
        )

        store = CentralStore(central_db)
        signals = store.query("SELECT signal_id FROM signals")
        store.close()
        assert any(r["signal_id"] == "sig-001" for r in signals)

    def test_migrates_archived_cards(self, tmp_path: Path) -> None:
        pmo_db = tmp_path / "pmo.db"
        central_db = tmp_path / "central.db"
        marker = tmp_path / ".pmo-migrated"
        _make_pmo_db(pmo_db)

        _maybe_migrate_pmo(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=marker,
        )

        store = CentralStore(central_db)
        cards = store.query("SELECT card_id FROM archived_cards")
        store.close()
        assert any(r["card_id"] == "task-001" for r in cards)

    def test_migrates_programs(self, tmp_path: Path) -> None:
        pmo_db = tmp_path / "pmo.db"
        central_db = tmp_path / "central.db"
        marker = tmp_path / ".pmo-migrated"
        _make_pmo_db(pmo_db)

        _maybe_migrate_pmo(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=marker,
        )

        store = CentralStore(central_db)
        programs = store.query("SELECT name FROM programs ORDER BY name")
        store.close()
        names = [r["name"] for r in programs]
        assert "NDS" in names
        assert "ATL" in names

    def test_writes_marker_file(self, tmp_path: Path) -> None:
        pmo_db = tmp_path / "pmo.db"
        central_db = tmp_path / "central.db"
        marker = tmp_path / ".pmo-migrated"
        _make_pmo_db(pmo_db)

        _maybe_migrate_pmo(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=marker,
        )

        assert marker.exists()

    def test_idempotent_second_call(self, tmp_path: Path) -> None:
        """Second call returns False without touching the database."""
        pmo_db = tmp_path / "pmo.db"
        central_db = tmp_path / "central.db"
        marker = tmp_path / ".pmo-migrated"
        _make_pmo_db(pmo_db)

        first = _maybe_migrate_pmo(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=marker,
        )
        second = _maybe_migrate_pmo(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=marker,
        )

        assert first is True
        assert second is False

    def test_no_source_db_returns_false(self, tmp_path: Path) -> None:
        """When pmo.db is absent, migration returns False and writes marker."""
        central_db = tmp_path / "central.db"
        marker = tmp_path / ".pmo-migrated"

        result = _maybe_migrate_pmo(
            central_db_path=central_db,
            pmo_db_path=tmp_path / "nonexistent.db",
            marker_path=marker,
        )

        assert result is False
        assert marker.exists()

    def test_no_source_subsequent_call_still_false(self, tmp_path: Path) -> None:
        """Repeated calls with no pmo.db all return False."""
        central_db = tmp_path / "central.db"
        marker = tmp_path / ".pmo-migrated"

        _maybe_migrate_pmo(
            central_db_path=central_db,
            pmo_db_path=tmp_path / "nonexistent.db",
            marker_path=marker,
        )
        result = _maybe_migrate_pmo(
            central_db_path=central_db,
            pmo_db_path=tmp_path / "nonexistent.db",
            marker_path=marker,
        )

        assert result is False

    def test_insert_or_replace_is_idempotent(self, tmp_path: Path) -> None:
        """Running migration with the same source data twice does not duplicate rows."""
        pmo_db = tmp_path / "pmo.db"
        central_db = tmp_path / "central.db"
        # No marker — simulate re-running migration manually (without marker).
        _make_pmo_db(pmo_db)

        # First migration.
        _maybe_migrate_pmo(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=tmp_path / ".marker1",
        )
        # Second migration (different marker to bypass idempotency guard).
        _maybe_migrate_pmo(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=tmp_path / ".marker2",
        )

        store = CentralStore(central_db)
        projects = store.query("SELECT project_id FROM projects")
        store.close()
        # Two source rows — INSERT OR REPLACE means no duplicates.
        project_ids = [r["project_id"] for r in projects]
        assert len(project_ids) == len(set(project_ids))


# ---------------------------------------------------------------------------
# get_pmo_central_store
# ---------------------------------------------------------------------------


class TestGetPmoCentralStore:
    def test_returns_pmo_sqlite_store(self, tmp_path: Path) -> None:
        central_db = tmp_path / "central.db"
        store = get_pmo_central_store(
            central_db_path=central_db,
            pmo_db_path=tmp_path / "nonexistent.db",
            marker_path=tmp_path / ".marker",
        )
        assert isinstance(store, PmoSqliteStore)
        store.close()

    def test_store_is_writable(self, tmp_path: Path) -> None:
        central_db = tmp_path / "central.db"
        store = get_pmo_central_store(
            central_db_path=central_db,
            pmo_db_path=tmp_path / "nonexistent.db",
            marker_path=tmp_path / ".marker",
        )
        project = PmoProject(
            project_id="p1",
            name="Project One",
            path="/srv/p1",
            program="PROG",
        )
        store.register_project(project)
        retrieved = store.get_project("p1")
        store.close()
        assert retrieved is not None
        assert retrieved.name == "Project One"

    def test_auto_migrates_on_first_call(self, tmp_path: Path) -> None:
        """get_pmo_central_store migrates pmo.db data into central.db."""
        pmo_db = tmp_path / "pmo.db"
        central_db = tmp_path / "central.db"
        marker = tmp_path / ".marker"
        _make_pmo_db(pmo_db)

        store = get_pmo_central_store(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=marker,
        )
        projects = store.list_projects()
        store.close()

        project_ids = [p.project_id for p in projects]
        assert "nds" in project_ids
        assert "atl" in project_ids

    def test_signals_available_after_migration(self, tmp_path: Path) -> None:
        pmo_db = tmp_path / "pmo.db"
        central_db = tmp_path / "central.db"
        marker = tmp_path / ".marker"
        _make_pmo_db(pmo_db)

        store = get_pmo_central_store(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=marker,
        )
        signals = store.get_open_signals()
        store.close()

        signal_ids = [s.signal_id for s in signals]
        assert "sig-001" in signal_ids

    def test_second_call_skips_migration(self, tmp_path: Path) -> None:
        """Marker is written on first call; second call must not re-run migration."""
        pmo_db = tmp_path / "pmo.db"
        central_db = tmp_path / "central.db"
        marker = tmp_path / ".marker"
        _make_pmo_db(pmo_db)

        store1 = get_pmo_central_store(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=marker,
        )
        store1.close()

        # Delete pmo.db to prove the second call does not need it.
        pmo_db.unlink()

        store2 = get_pmo_central_store(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=marker,
        )
        projects = store2.list_projects()
        store2.close()

        # Data is still there from the first migration.
        assert len(projects) >= 2


# ---------------------------------------------------------------------------
# PmoSqliteStore backed by central.db — interface compatibility
# ---------------------------------------------------------------------------


class TestPmoSqliteStoreCentralDbCompat:
    """Verify that PmoSqliteStore works correctly when pointed at central.db.

    These tests use get_pmo_central_store() to obtain the store and exercise
    the full CRUD interface to catch any schema mismatch between PMO_SCHEMA_DDL
    and CENTRAL_SCHEMA_DDL.
    """

    def _store(self, tmp_path: Path) -> PmoSqliteStore:
        return get_pmo_central_store(
            central_db_path=tmp_path / "central.db",
            pmo_db_path=tmp_path / "nonexistent.db",
            marker_path=tmp_path / ".marker",
        )

    def test_register_and_get_project(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        project = PmoProject(
            project_id="test-proj",
            name="Test Project",
            path="/srv/test",
            program="TEST",
        )
        store.register_project(project)
        result = store.get_project("test-proj")
        store.close()
        assert result is not None
        assert result.project_id == "test-proj"
        assert result.name == "Test Project"

    def test_list_projects(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        for i in range(3):
            store.register_project(
                PmoProject(
                    project_id=f"proj-{i}",
                    name=f"Project {i}",
                    path=f"/srv/proj-{i}",
                    program="MULTI",
                )
            )
        projects = store.list_projects()
        store.close()
        assert len(projects) == 3

    def test_unregister_project(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        store.register_project(
            PmoProject(
                project_id="to-remove",
                name="Remove Me",
                path="/srv/remove",
                program="RM",
            )
        )
        removed = store.unregister_project("to-remove")
        result = store.get_project("to-remove")
        store.close()
        assert removed is True
        assert result is None

    def test_add_and_list_programs(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        store.add_program("ALPHA")
        store.add_program("BETA")
        programs = store.list_programs()
        store.close()
        assert "ALPHA" in programs
        assert "BETA" in programs

    def test_add_and_resolve_signal(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        store.add_signal(
            PmoSignal(
                signal_id="sig-central-001",
                signal_type="bug",
                title="Central DB bug",
            )
        )
        open_before = store.get_open_signals()
        resolved = store.resolve_signal("sig-central-001")
        open_after = store.get_open_signals()
        store.close()
        assert any(s.signal_id == "sig-central-001" for s in open_before)
        assert resolved is True
        assert not any(s.signal_id == "sig-central-001" for s in open_after)

    def test_archive_and_read_card(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        card = PmoCard(
            card_id="archived-001",
            project_id="proj-x",
            program="X",
            title="Archived task",
            column="deployed",
        )
        store.archive_card(card)
        archive = store.read_archive()
        store.close()
        assert any(c.card_id == "archived-001" for c in archive)

    def test_load_config_compatibility(self, tmp_path: Path) -> None:
        """load_config() returns a PmoConfig from central.db tables."""
        from agent_baton.models.pmo import PmoConfig

        store = self._store(tmp_path)
        store.register_project(
            PmoProject(
                project_id="cfg-proj",
                name="Config Test",
                path="/srv/cfg",
                program="CFG",
            )
        )
        store.add_program("CFG")
        config = store.load_config()
        store.close()
        assert isinstance(config, PmoConfig)
        assert any(p.project_id == "cfg-proj" for p in config.projects)

    def test_record_and_read_metric(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        store.record_metric("TEST", "velocity", 42.5)
        metrics = store.read_metrics("velocity")
        store.close()
        assert len(metrics) == 1
        assert metrics[0]["metric_value"] == pytest.approx(42.5)

    def test_forge_session_lifecycle(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        store.create_forge_session("sess-001", "proj-a", "Build feature")
        sessions_before = store.list_forge_sessions(status="active")
        store.complete_forge_session("sess-001", "task-xyz")
        sessions_after = store.list_forge_sessions(status="completed")
        store.close()
        assert any(s["session_id"] == "sess-001" for s in sessions_before)
        assert any(s["session_id"] == "sess-001" for s in sessions_after)


# ---------------------------------------------------------------------------
# PmoScanner backward-compat with central.db-backed store
# ---------------------------------------------------------------------------


class TestPmoScannerWithCentralStore:
    """PmoScanner must work when its store is backed by central.db."""

    def test_scan_all_returns_list(self, tmp_path: Path) -> None:
        from agent_baton.core.pmo.scanner import PmoScanner

        store = get_pmo_central_store(
            central_db_path=tmp_path / "central.db",
            pmo_db_path=tmp_path / "nonexistent.db",
            marker_path=tmp_path / ".marker",
        )
        # No projects registered — scan_all should return an empty list, not raise.
        scanner = PmoScanner(store)
        cards = scanner.scan_all()
        store.close()
        assert isinstance(cards, list)

    def test_program_health_returns_dict(self, tmp_path: Path) -> None:
        from agent_baton.core.pmo.scanner import PmoScanner

        store = get_pmo_central_store(
            central_db_path=tmp_path / "central.db",
            pmo_db_path=tmp_path / "nonexistent.db",
            marker_path=tmp_path / ".marker",
        )
        store.add_program("PROG")
        scanner = PmoScanner(store)
        health = scanner.program_health()
        store.close()
        assert isinstance(health, dict)

    def test_archived_cards_appear_in_scan_all(self, tmp_path: Path) -> None:
        from agent_baton.core.pmo.scanner import PmoScanner

        pmo_db = tmp_path / "pmo.db"
        central_db = tmp_path / "central.db"
        marker = tmp_path / ".marker"
        _make_pmo_db(pmo_db)

        store = get_pmo_central_store(
            central_db_path=central_db,
            pmo_db_path=pmo_db,
            marker_path=marker,
        )
        scanner = PmoScanner(store)
        cards = scanner.scan_all()
        store.close()

        card_ids = [c.card_id for c in cards]
        assert "task-001" in card_ids
