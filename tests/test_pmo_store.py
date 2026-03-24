"""Tests for agent_baton.core.pmo.store.PmoStore."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.pmo.store import PmoStore
from agent_baton.models.pmo import PmoCard, PmoConfig, PmoProject, PmoSignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _store(tmp_path: Path) -> PmoStore:
    return PmoStore(
        config_path=tmp_path / "pmo-config.json",
        archive_path=tmp_path / "pmo-archive.jsonl",
    )


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
# Config load / save
# ---------------------------------------------------------------------------

class TestConfigLoadSave:
    def test_load_from_nonexistent_file_returns_empty_config(self, tmp_path: Path):
        store = _store(tmp_path)
        config = store.load_config()
        assert isinstance(config, PmoConfig)
        assert config.projects == []
        assert config.signals == []

    def test_save_then_load_roundtrip(self, tmp_path: Path):
        store = _store(tmp_path)
        config = PmoConfig(
            projects=[_project()],
            programs=["NDS"],
            signals=[_signal()],
            version="1",
        )
        store.save_config(config)
        loaded = store.load_config()
        assert len(loaded.projects) == 1
        assert loaded.projects[0].project_id == "nds"
        assert len(loaded.signals) == 1
        assert loaded.signals[0].signal_id == "sig-001"
        assert loaded.programs == ["NDS"]

    def test_save_creates_parent_directories(self, tmp_path: Path):
        nested_config = tmp_path / "deep" / "nested" / "pmo-config.json"
        store = PmoStore(config_path=nested_config, archive_path=tmp_path / "archive.jsonl")
        store.save_config(PmoConfig())
        assert nested_config.exists()

    def test_save_is_valid_json(self, tmp_path: Path):
        store = _store(tmp_path)
        store.save_config(PmoConfig(projects=[_project()]))
        data = json.loads(store.config_path.read_text(encoding="utf-8"))
        assert "projects" in data

    def test_load_corrupt_json_returns_empty_config(self, tmp_path: Path):
        store = _store(tmp_path)
        store.config_path.parent.mkdir(parents=True, exist_ok=True)
        store.config_path.write_text("not valid json {{{", encoding="utf-8")
        config = store.load_config()
        assert config.projects == []

    def test_save_uses_atomic_write_no_tmp_file_left(self, tmp_path: Path):
        store = _store(tmp_path)
        store.save_config(PmoConfig())
        assert not (tmp_path / "pmo-config.json.tmp").exists()


# ---------------------------------------------------------------------------
# Project registration
# ---------------------------------------------------------------------------

class TestRegisterProject:
    def test_register_project_appears_in_config(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project())
        config = store.load_config()
        assert any(p.project_id == "nds" for p in config.projects)

    def test_register_project_with_same_id_replaces_existing(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project(name="Original Name"))
        store.register_project(_project(name="Updated Name"))
        config = store.load_config()
        projects_with_id = [p for p in config.projects if p.project_id == "nds"]
        assert len(projects_with_id) == 1
        assert projects_with_id[0].name == "Updated Name"

    def test_register_project_sets_registered_at_when_empty(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project())
        p = store.get_project("nds")
        assert p is not None
        assert p.registered_at != ""

    def test_register_project_preserves_existing_registered_at(self, tmp_path: Path):
        store = _store(tmp_path)
        p = _project(registered_at="2026-01-01T00:00:00+00:00")
        store.register_project(p)
        loaded = store.get_project("nds")
        assert loaded.registered_at == "2026-01-01T00:00:00+00:00"

    def test_register_multiple_distinct_projects(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project(project_id="nds"))
        store.register_project(_project(project_id="atl", name="ATL", path="/atl", program="ATL"))
        config = store.load_config()
        ids = {p.project_id for p in config.projects}
        assert ids == {"nds", "atl"}


# ---------------------------------------------------------------------------
# Unregister project
# ---------------------------------------------------------------------------

class TestUnregisterProject:
    def test_unregister_existing_project_returns_true(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project())
        assert store.unregister_project("nds") is True

    def test_unregister_existing_project_removes_it(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project())
        store.unregister_project("nds")
        assert store.get_project("nds") is None

    def test_unregister_nonexistent_project_returns_false(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.unregister_project("no-such-project") is False

    def test_unregister_does_not_affect_other_projects(self, tmp_path: Path):
        store = _store(tmp_path)
        store.register_project(_project(project_id="nds"))
        store.register_project(_project(project_id="atl", name="ATL", path="/atl", program="ATL"))
        store.unregister_project("nds")
        assert store.get_project("atl") is not None

    def test_unregister_nonexistent_does_not_write_config(self, tmp_path: Path):
        store = _store(tmp_path)
        # Config file does not exist yet
        store.unregister_project("ghost")
        assert not store.config_path.exists()


# ---------------------------------------------------------------------------
# get_project
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

class TestAddSignal:
    def test_add_signal_appears_in_config(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal())
        config = store.load_config()
        assert any(s.signal_id == "sig-001" for s in config.signals)

    def test_add_signal_sets_created_at_when_empty(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal())
        config = store.load_config()
        assert config.signals[0].created_at != ""

    def test_add_signal_preserves_existing_created_at(self, tmp_path: Path):
        store = _store(tmp_path)
        s = _signal(created_at="2026-01-01T00:00:00+00:00")
        store.add_signal(s)
        config = store.load_config()
        assert config.signals[0].created_at == "2026-01-01T00:00:00+00:00"

    def test_add_multiple_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal(signal_id="s1"))
        store.add_signal(_signal(signal_id="s2", signal_type="blocker", title="B"))
        config = store.load_config()
        ids = {s.signal_id for s in config.signals}
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
        config = store.load_config()
        sig = next(s for s in config.signals if s.signal_id == "sig-001")
        assert sig.status == "resolved"

    def test_resolve_sets_resolved_at(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal())
        store.resolve_signal("sig-001")
        config = store.load_config()
        sig = next(s for s in config.signals if s.signal_id == "sig-001")
        assert sig.resolved_at != ""

    def test_resolve_nonexistent_signal_returns_false(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.resolve_signal("no-such-signal") is False

    def test_resolve_does_not_affect_other_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal(signal_id="s1"))
        store.add_signal(_signal(signal_id="s2", signal_type="blocker", title="B"))
        store.resolve_signal("s1")
        config = store.load_config()
        s2 = next(s for s in config.signals if s.signal_id == "s2")
        assert s2.status == "open"


class TestGetOpenSignals:
    def test_returns_open_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal(signal_id="open-1"))
        store.add_signal(_signal(signal_id="open-2", signal_type="blocker", title="B"))
        open_signals = store.get_open_signals()
        assert len(open_signals) == 2

    def test_excludes_resolved_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal(signal_id="open-1"))
        store.add_signal(_signal(signal_id="resolved-1", signal_type="blocker", title="B"))
        store.resolve_signal("resolved-1")
        open_signals = store.get_open_signals()
        ids = {s.signal_id for s in open_signals}
        assert "resolved-1" not in ids
        assert "open-1" in ids

    def test_includes_triaged_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        store.add_signal(_signal(signal_id="triaged-1", status="triaged"))
        open_signals = store.get_open_signals()
        ids = {s.signal_id for s in open_signals}
        assert "triaged-1" in ids

    def test_empty_when_no_signals(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.get_open_signals() == []


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

class TestArchiveCard:
    def test_archive_card_creates_file(self, tmp_path: Path):
        store = _store(tmp_path)
        store.archive_card(_card())
        assert store.archive_path.exists()

    def test_archive_card_appends_valid_json_line(self, tmp_path: Path):
        store = _store(tmp_path)
        store.archive_card(_card(card_id="task-001"))
        lines = store.archive_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["card_id"] == "task-001"

    def test_archive_multiple_cards_appends(self, tmp_path: Path):
        store = _store(tmp_path)
        store.archive_card(_card(card_id="t1"))
        store.archive_card(_card(card_id="t2"))
        store.archive_card(_card(card_id="t3"))
        lines = [l for l in store.archive_path.read_text(encoding="utf-8").strip().split("\n") if l]
        assert len(lines) == 3

    def test_archive_creates_parent_directories(self, tmp_path: Path):
        archive_path = tmp_path / "deep" / "archive.jsonl"
        store = PmoStore(config_path=tmp_path / "config.json", archive_path=archive_path)
        store.archive_card(_card())
        assert archive_path.exists()


class TestReadArchive:
    def test_read_archive_returns_pmo_cards(self, tmp_path: Path):
        store = _store(tmp_path)
        store.archive_card(_card(card_id="t1"))
        cards = store.read_archive()
        assert len(cards) == 1
        assert isinstance(cards[0], PmoCard)

    def test_read_archive_from_nonexistent_file_returns_empty_list(self, tmp_path: Path):
        store = _store(tmp_path)
        assert store.read_archive() == []

    def test_read_archive_respects_limit(self, tmp_path: Path):
        store = _store(tmp_path)
        for i in range(10):
            store.archive_card(_card(card_id=f"task-{i:03d}"))
        cards = store.read_archive(limit=3)
        assert len(cards) == 3

    def test_read_archive_returns_most_recent_when_limited(self, tmp_path: Path):
        store = _store(tmp_path)
        for i in range(5):
            store.archive_card(_card(card_id=f"task-{i:03d}"))
        cards = store.read_archive(limit=2)
        ids = [c.card_id for c in cards]
        assert "task-003" in ids
        assert "task-004" in ids

    def test_read_archive_skips_malformed_lines(self, tmp_path: Path):
        store = _store(tmp_path)
        store.archive_card(_card(card_id="good-card"))
        # Append a corrupt line
        with store.archive_path.open("a", encoding="utf-8") as f:
            f.write("not json at all\n")
        cards = store.read_archive()
        assert len(cards) == 1
        assert cards[0].card_id == "good-card"

    def test_read_archive_skips_blank_lines(self, tmp_path: Path):
        store = _store(tmp_path)
        store.archive_card(_card(card_id="t1"))
        with store.archive_path.open("a", encoding="utf-8") as f:
            f.write("\n\n")
        cards = store.read_archive()
        assert len(cards) == 1

    def test_read_archive_roundtrips_card_data(self, tmp_path: Path):
        store = _store(tmp_path)
        original = _card(
            card_id="roundtrip",
            project_id="nds",
            program="NDS",
            title="Test roundtrip",
            column="deployed",
            risk_level="HIGH",
            agents=["architect"],
            steps_completed=2,
            steps_total=2,
        )
        store.archive_card(original)
        loaded = store.read_archive()[0]
        assert loaded.card_id == original.card_id
        assert loaded.title == original.title
        assert loaded.agents == original.agents
        assert loaded.steps_completed == original.steps_completed
