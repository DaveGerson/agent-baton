"""Tests for agent_baton.core.storage.pmo_sqlite.PmoSqliteStore."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.storage.pmo_sqlite import PmoSqliteStore
from agent_baton.models.pmo import PmoCard, PmoConfig, PmoProject, PmoSignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(tmp_path: Path) -> PmoSqliteStore:
    return PmoSqliteStore(tmp_path / "pmo.db")


def _project(**kwargs) -> PmoProject:
    defaults = dict(
        project_id="nds",
        name="NDS Project",
        path="/srv/nds",
        program="NDS",
    )
    defaults.update(kwargs)
    return PmoProject(**defaults)


def _signal(**kwargs) -> PmoSignal:
    defaults = dict(
        signal_id="sig-001",
        signal_type="bug",
        title="Login fails",
    )
    defaults.update(kwargs)
    return PmoSignal(**defaults)


def _card(**kwargs) -> PmoCard:
    defaults = dict(
        card_id="task-001",
        project_id="nds",
        program="NDS",
        title="Build the thing",
        column="deployed",
    )
    defaults.update(kwargs)
    return PmoCard(**defaults)


# ---------------------------------------------------------------------------
# DB creation
# ---------------------------------------------------------------------------

class TestDbCreation:
    def test_db_file_created_on_first_access(self, tmp_path: Path):
        store = _store(tmp_path)
        store.list_projects()          # triggers schema init
        assert (tmp_path / "pmo.db").exists()

    def test_db_path_property(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.db_path == tmp_path / "pmo.db"

    def test_parent_directories_created(self, tmp_path: Path):
        deep_db = tmp_path / "deep" / "nested" / "pmo.db"
        store = PmoSqliteStore(deep_db)
        store.list_projects()
        assert deep_db.exists()


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class TestRegisterProject:
    def test_register_project_appears_in_list(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project())
        projects = store.list_projects()
        assert any(p.project_id == "nds" for p in projects)

    def test_register_same_id_replaces_existing(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project(name="Original"))
        store.register_project(_project(name="Updated"))
        projects = store.list_projects()
        nds_list = [p for p in projects if p.project_id == "nds"]
        assert len(nds_list) == 1
        assert nds_list[0].name == "Updated"

    def test_register_sets_registered_at_when_empty(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project())
        p = store.get_project("nds")
        assert p is not None
        assert p.registered_at != ""

    def test_register_preserves_existing_registered_at(self, tmp_path: Path):
        store = _store(tmp_path)
        p = _project(registered_at="2026-01-01T00:00:00+00:00")
        store.register_project(p)
        loaded = store.get_project("nds")
        assert loaded.registered_at == "2026-01-01T00:00:00+00:00"

    def test_register_multiple_distinct_projects(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project(project_id="nds"))
        store.register_project(_project(project_id="atl", name="ATL", path="/atl", program="ATL"))
        ids = {p.project_id for p in store.list_projects()}
        assert ids == {"nds", "atl"}

    def test_list_projects_empty_initially(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.list_projects() == []


class TestUnregisterProject:
    def test_unregister_existing_returns_true(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project())
        assert store.unregister_project("nds") is True

    def test_unregister_existing_removes_it(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project())
        store.unregister_project("nds")
        assert store.get_project("nds") is None

    def test_unregister_nonexistent_returns_false(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.unregister_project("no-such-project") is False

    def test_unregister_does_not_affect_other_projects(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project(project_id="nds"))
        store.register_project(_project(project_id="atl", name="ATL", path="/atl", program="ATL"))
        store.unregister_project("nds")
        assert store.get_project("atl") is not None


class TestGetProject:
    def test_get_project_returns_correct_project(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project(project_id="nds", name="NDS"))
        store.register_project(_project(project_id="atl", name="ATL", path="/atl", program="ATL"))
        p = store.get_project("atl")
        assert p is not None
        assert p.name == "ATL"

    def test_get_project_returns_none_for_missing_id(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project())
        assert store.get_project("no-such-id") is None

    def test_get_project_from_empty_store_returns_none(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.get_project("nds") is None

    def test_get_project_roundtrips_all_fields(self, tmp_path: Path):
        store = _store(tmp_path)
        original = _project(
            color="blue",
            description="A test project",
            registered_at="2026-03-01T12:00:00+00:00",
            ado_project="ADO-123",
        )
        store.register_project(original)
        loaded = store.get_project("nds")
        assert loaded.color == "blue"
        assert loaded.description == "A test project"
        assert loaded.ado_project == "ADO-123"


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------

class TestPrograms:
    def test_add_program_appears_in_list(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_program("NDS")
        assert "NDS" in store.list_programs()

    def test_add_program_is_idempotent(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_program("NDS")
        store.add_program("NDS")
        assert store.list_programs().count("NDS") == 1

    def test_list_programs_returns_sorted(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_program("ZZZ")
        store.add_program("AAA")
        store.add_program("MMM")
        assert store.list_programs() == ["AAA", "MMM", "ZZZ"]

    def test_list_programs_empty_initially(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.list_programs() == []


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

class TestAddSignal:
    def test_add_signal_appears_in_open_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal())
        signals = store.get_open_signals()
        assert any(s.signal_id == "sig-001" for s in signals)

    def test_add_signal_sets_created_at_when_empty(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal())
        s = store.get_signal("sig-001")
        assert s is not None
        assert s.created_at != ""

    def test_add_signal_preserves_existing_created_at(self, tmp_path: Path):
        store = _store(tmp_path)
        sig = _signal(created_at="2026-01-01T00:00:00+00:00")
        store.add_signal(sig)
        loaded = store.get_signal("sig-001")
        assert loaded.created_at == "2026-01-01T00:00:00+00:00"

    def test_add_multiple_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal(signal_id="s1"))
        store.add_signal(_signal(signal_id="s2", signal_type="blocker", title="Blocker"))
        ids = {s.signal_id for s in store.get_open_signals()}
        assert ids == {"s1", "s2"}


class TestResolveSignal:
    def test_resolve_existing_signal_returns_true(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal())
        assert store.resolve_signal("sig-001") is True

    def test_resolve_sets_status_to_resolved(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal())
        store.resolve_signal("sig-001")
        sig = store.get_signal("sig-001")
        assert sig.status == "resolved"

    def test_resolve_sets_resolved_at(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal())
        store.resolve_signal("sig-001")
        sig = store.get_signal("sig-001")
        assert sig.resolved_at != ""

    def test_resolve_nonexistent_signal_returns_false(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.resolve_signal("no-such-signal") is False

    def test_resolve_does_not_affect_other_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal(signal_id="s1"))
        store.add_signal(_signal(signal_id="s2", signal_type="blocker", title="B"))
        store.resolve_signal("s1")
        s2 = store.get_signal("s2")
        assert s2.status == "open"


class TestGetOpenSignals:
    def test_returns_only_open_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal(signal_id="open-1"))
        store.add_signal(_signal(signal_id="resolved-1", signal_type="bug", title="X"))
        store.resolve_signal("resolved-1")
        ids = {s.signal_id for s in store.get_open_signals()}
        assert "open-1" in ids
        assert "resolved-1" not in ids

    def test_includes_triaged_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal(signal_id="triaged-1", status="triaged"))
        ids = {s.signal_id for s in store.get_open_signals()}
        assert "triaged-1" in ids

    def test_empty_when_no_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.get_open_signals() == []


class TestGetSignal:
    def test_get_signal_returns_correct_signal(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal(signal_id="s1", title="First"))
        store.add_signal(_signal(signal_id="s2", title="Second", signal_type="blocker"))
        s = store.get_signal("s2")
        assert s is not None
        assert s.title == "Second"

    def test_get_signal_returns_none_for_missing(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.get_signal("no-such") is None

    def test_get_signal_roundtrips_all_fields(self, tmp_path: Path):
        store = _store(tmp_path)
        original = _signal(
            severity="critical",
            source_project_id="nds",
            forge_task_id="forge-42",
        )
        store.add_signal(original)
        loaded = store.get_signal("sig-001")
        assert loaded.severity == "critical"
        assert loaded.source_project_id == "nds"
        assert loaded.forge_task_id == "forge-42"


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

class TestArchiveCard:
    def test_archive_card_is_retrievable(self, tmp_path: Path):
        store = _store(tmp_path)
        store.archive_card(_card())
        cards = store.read_archive()
        assert len(cards) == 1
        assert cards[0].card_id == "task-001"

    def test_archive_multiple_cards(self, tmp_path: Path):
        store = _store(tmp_path)
        for i in range(3):
            store.archive_card(_card(card_id=f"t{i}"))
        assert len(store.read_archive()) == 3

    def test_archive_replaces_on_same_card_id(self, tmp_path: Path):
        store = _store(tmp_path)
        store.archive_card(_card(title="First"))
        store.archive_card(_card(title="Second"))
        cards = store.read_archive()
        assert len(cards) == 1
        assert cards[0].title == "Second"

    def test_archive_card_roundtrips_agents_list(self, tmp_path: Path):
        store = _store(tmp_path)
        store.archive_card(_card(agents=["architect", "backend-engineer"]))
        loaded = store.read_archive()[0]
        assert loaded.agents == ["architect", "backend-engineer"]


class TestReadArchive:
    def test_read_archive_returns_pmo_card_objects(self, tmp_path: Path):
        store = _store(tmp_path)
        store.archive_card(_card())
        cards = store.read_archive()
        assert isinstance(cards[0], PmoCard)

    def test_read_archive_empty_initially(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.read_archive() == []

    def test_read_archive_respects_limit(self, tmp_path: Path):
        store = _store(tmp_path)
        for i in range(10):
            store.archive_card(_card(card_id=f"t{i:03d}"))
        assert len(store.read_archive(limit=3)) == 3

    def test_read_archive_returns_most_recent_when_limited(self, tmp_path: Path):
        store = _store(tmp_path)
        for i in range(5):
            store.archive_card(_card(card_id=f"t{i:03d}"))
        cards = store.read_archive(limit=2)
        ids = {c.card_id for c in cards}
        assert "t003" in ids
        assert "t004" in ids

    def test_read_archive_roundtrips_full_card(self, tmp_path: Path):
        store = _store(tmp_path)
        original = _card(
            risk_level="HIGH",
            priority=2,
            agents=["architect"],
            steps_completed=3,
            steps_total=5,
            gates_passed=2,
            current_phase="phase-2",
            error="",
            external_id="EXT-99",
        )
        store.archive_card(original)
        loaded = store.read_archive()[0]
        assert loaded.risk_level == "HIGH"
        assert loaded.priority == 2
        assert loaded.agents == ["architect"]
        assert loaded.steps_completed == 3
        assert loaded.steps_total == 5
        assert loaded.gates_passed == 2
        assert loaded.external_id == "EXT-99"


# ---------------------------------------------------------------------------
# Forge sessions
# ---------------------------------------------------------------------------

class TestForgeSessions:
    def test_create_forge_session_appears_in_list(self, tmp_path: Path):
        store = _store(tmp_path)
        store.create_forge_session("sess-1", "nds", "Build feature X")
        sessions = store.list_forge_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "sess-1"
        assert sessions[0]["status"] == "active"

    def test_complete_forge_session_updates_status(self, tmp_path: Path):
        store = _store(tmp_path)
        store.create_forge_session("sess-1", "nds", "Task title")
        store.complete_forge_session("sess-1", "task-abc")
        sessions = store.list_forge_sessions()
        assert sessions[0]["status"] == "completed"
        assert sessions[0]["task_id"] == "task-abc"
        assert sessions[0]["completed_at"] is not None

    def test_list_forge_sessions_filtered_by_status(self, tmp_path: Path):
        store = _store(tmp_path)
        store.create_forge_session("s1", "nds", "Active")
        store.create_forge_session("s2", "nds", "Also active")
        store.complete_forge_session("s1", "t-1")
        active = store.list_forge_sessions(status="active")
        assert len(active) == 1
        assert active[0]["session_id"] == "s2"

    def test_list_forge_sessions_empty_initially(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.list_forge_sessions() == []


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_record_and_read_metric(self, tmp_path: Path):
        store = _store(tmp_path)
        store.record_metric("NDS", "completion_pct", 75.0)
        rows = store.read_metrics("completion_pct")
        assert len(rows) == 1
        assert rows[0]["metric_value"] == 75.0
        assert rows[0]["program"] == "NDS"

    def test_read_metrics_respects_limit(self, tmp_path: Path):
        store = _store(tmp_path)
        for i in range(10):
            store.record_metric("NDS", "throughput", float(i))
        rows = store.read_metrics("throughput", limit=5)
        assert len(rows) == 5

    def test_read_metrics_returns_only_named_metric(self, tmp_path: Path):
        store = _store(tmp_path)
        store.record_metric("NDS", "alpha", 1.0)
        store.record_metric("NDS", "beta", 2.0)
        rows = store.read_metrics("alpha")
        assert all(r["metric_name"] == "alpha" for r in rows)

    def test_read_metrics_empty_for_unknown_name(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.read_metrics("no-such-metric") == []


# ---------------------------------------------------------------------------
# PmoConfig compatibility shim
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_load_config_returns_pmo_config(self, tmp_path: Path):
        store = _store(tmp_path)
        config = store.load_config()
        assert isinstance(config, PmoConfig)

    def test_load_config_empty_when_db_is_fresh(self, tmp_path: Path):
        store = _store(tmp_path)
        config = store.load_config()
        assert config.projects == []
        assert config.programs == []
        assert config.signals == []

    def test_load_config_reflects_registered_data(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project())
        store.add_program("NDS")
        store.add_signal(_signal())
        config = store.load_config()
        assert len(config.projects) == 1
        assert "NDS" in config.programs
        assert len(config.signals) == 1


class TestSaveConfig:
    def test_save_config_writes_projects(self, tmp_path: Path):
        store = _store(tmp_path)
        config = PmoConfig(
            projects=[_project()],
            programs=["NDS"],
            signals=[_signal()],
        )
        store.save_config(config)
        assert store.get_project("nds") is not None

    def test_save_config_writes_programs(self, tmp_path: Path):
        store = _store(tmp_path)
        store.save_config(PmoConfig(programs=["ALPHA", "BETA"]))
        programs = store.list_programs()
        assert "ALPHA" in programs
        assert "BETA" in programs

    def test_save_config_writes_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        store.save_config(PmoConfig(signals=[_signal()]))
        assert store.get_signal("sig-001") is not None

    def test_save_config_is_idempotent(self, tmp_path: Path):
        store = _store(tmp_path)
        config = PmoConfig(projects=[_project()], programs=["NDS"])
        store.save_config(config)
        store.save_config(config)
        assert len(store.list_projects()) == 1

    def test_save_then_load_config_roundtrip(self, tmp_path: Path):
        store = _store(tmp_path)
        config = PmoConfig(
            projects=[_project()],
            programs=["NDS"],
            signals=[_signal()],
        )
        store.save_config(config)
        loaded = store.load_config()
        assert loaded.projects[0].project_id == "nds"
        assert "NDS" in loaded.programs
        assert loaded.signals[0].signal_id == "sig-001"
