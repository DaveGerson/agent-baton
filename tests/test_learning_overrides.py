"""Tests for agent_baton.core.learn.overrides — LearnedOverrides."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.learn.overrides import LearnedOverrides, _EMPTY_OVERRIDES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def overrides_path(tmp_path: Path) -> Path:
    return tmp_path / "learned-overrides.json"


@pytest.fixture
def overrides(overrides_path: Path) -> LearnedOverrides:
    return LearnedOverrides(overrides_path)


# ---------------------------------------------------------------------------
# load — missing / empty / corrupt file
# ---------------------------------------------------------------------------


class TestLoad:
    def test_returns_empty_defaults_when_file_missing(self, overrides: LearnedOverrides):
        data = overrides.load()
        assert data["flavor_map"] == {}
        assert data["gate_commands"] == {}
        assert data["agent_drops"] == []
        assert data["classifier_adjustments"] == {}

    def test_returns_version_1_when_file_missing(self, overrides: LearnedOverrides):
        data = overrides.load()
        assert data["version"] == 1

    def test_merges_missing_keys_from_defaults(self, overrides_path: Path, overrides: LearnedOverrides):
        """A file that only has some keys must be merged with defaults."""
        overrides_path.parent.mkdir(parents=True, exist_ok=True)
        overrides_path.write_text(json.dumps({"version": 3, "flavor_map": {"k": {}}}), encoding="utf-8")
        data = overrides.load()
        assert data["version"] == 3
        assert data["flavor_map"] == {"k": {}}
        # Missing keys come from defaults
        assert data["agent_drops"] == []
        assert data["gate_commands"] == {}

    def test_corrupted_json_returns_empty_defaults(self, overrides_path: Path, overrides: LearnedOverrides):
        overrides_path.parent.mkdir(parents=True, exist_ok=True)
        overrides_path.write_text("{{ NOT VALID JSON {{", encoding="utf-8")
        data = overrides.load()
        assert data == dict(_EMPTY_OVERRIDES)

    def test_load_full_file(self, overrides_path: Path, overrides: LearnedOverrides):
        content = {
            "flavor_map": {"python/react": {"backend-engineer": "python"}},
            "gate_commands": {"typescript": {"test": "vitest run"}},
            "agent_drops": ["visualization-expert"],
            "classifier_adjustments": {"min_keyword_overlap": 3},
            "version": 5,
            "last_updated": "2026-04-13T12:00:00Z",
        }
        overrides_path.parent.mkdir(parents=True, exist_ok=True)
        overrides_path.write_text(json.dumps(content), encoding="utf-8")
        data = overrides.load()
        assert data["version"] == 5
        assert data["agent_drops"] == ["visualization-expert"]
        assert data["gate_commands"]["typescript"]["test"] == "vitest run"


# ---------------------------------------------------------------------------
# save — atomic write
# ---------------------------------------------------------------------------


class TestSave:
    def test_creates_parent_directories(self, tmp_path: Path):
        deep_path = tmp_path / "a" / "b" / "c" / "overrides.json"
        ovr = LearnedOverrides(deep_path)
        ovr.save({"flavor_map": {}, "gate_commands": {}, "agent_drops": []})
        assert deep_path.exists()

    def test_sets_last_updated_on_save(self, overrides: LearnedOverrides):
        data = overrides.load()
        overrides.save(data)
        saved = json.loads(overrides._path.read_text(encoding="utf-8"))
        assert saved["last_updated"] != ""

    def test_saved_file_is_valid_json(self, overrides: LearnedOverrides):
        overrides.add_agent_drop("some-agent")
        raw = overrides._path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert "agent_drops" in parsed

    def test_save_load_roundtrip(self, overrides: LearnedOverrides):
        data = overrides.load()
        data["flavor_map"]["python"] = {"backend-engineer": "python"}
        overrides.save(data)
        loaded = overrides.load()
        assert loaded["flavor_map"]["python"] == {"backend-engineer": "python"}


# ---------------------------------------------------------------------------
# add_flavor_override / get_flavor_overrides
# ---------------------------------------------------------------------------


class TestFlavorOverrides:
    def test_add_and_get_roundtrip(self, overrides: LearnedOverrides):
        overrides.add_flavor_override("python/react", "backend-engineer", "python")
        result = overrides.get_flavor_overrides()
        assert result["python/react"]["backend-engineer"] == "python"

    def test_multiple_agents_same_stack(self, overrides: LearnedOverrides):
        overrides.add_flavor_override("python/react", "backend-engineer", "python")
        overrides.add_flavor_override("python/react", "frontend-engineer", "react")
        result = overrides.get_flavor_overrides()
        assert result["python/react"]["backend-engineer"] == "python"
        assert result["python/react"]["frontend-engineer"] == "react"

    def test_multiple_stacks_independent(self, overrides: LearnedOverrides):
        overrides.add_flavor_override("python", "backend-engineer", "python")
        overrides.add_flavor_override("typescript", "backend-engineer", "node")
        result = overrides.get_flavor_overrides()
        assert result["python"]["backend-engineer"] == "python"
        assert result["typescript"]["backend-engineer"] == "node"

    def test_overwrite_existing_flavor(self, overrides: LearnedOverrides):
        overrides.add_flavor_override("python", "backend-engineer", "old-flavor")
        overrides.add_flavor_override("python", "backend-engineer", "python")
        result = overrides.get_flavor_overrides()
        assert result["python"]["backend-engineer"] == "python"

    def test_increments_version(self, overrides: LearnedOverrides):
        overrides.add_flavor_override("python", "backend-engineer", "python")
        data = overrides.load()
        assert data["version"] == 2

    def test_get_flavor_overrides_empty_when_none(self, overrides: LearnedOverrides):
        assert overrides.get_flavor_overrides() == {}


# ---------------------------------------------------------------------------
# add_gate_override / get_gate_overrides
# ---------------------------------------------------------------------------


class TestGateOverrides:
    def test_add_and_get_roundtrip(self, overrides: LearnedOverrides):
        overrides.add_gate_override("typescript", "test", "vitest run")
        result = overrides.get_gate_overrides()
        assert result["typescript"]["test"] == "vitest run"

    def test_multiple_gate_types_same_language(self, overrides: LearnedOverrides):
        overrides.add_gate_override("typescript", "test", "vitest run")
        overrides.add_gate_override("typescript", "build", "npx tsc --noEmit")
        result = overrides.get_gate_overrides()
        assert result["typescript"]["test"] == "vitest run"
        assert result["typescript"]["build"] == "npx tsc --noEmit"

    def test_multiple_languages_independent(self, overrides: LearnedOverrides):
        overrides.add_gate_override("typescript", "test", "vitest run")
        overrides.add_gate_override("python", "test", "pytest")
        result = overrides.get_gate_overrides()
        assert result["typescript"]["test"] == "vitest run"
        assert result["python"]["test"] == "pytest"

    def test_overwrite_existing_gate(self, overrides: LearnedOverrides):
        overrides.add_gate_override("typescript", "test", "jest")
        overrides.add_gate_override("typescript", "test", "vitest run")
        result = overrides.get_gate_overrides()
        assert result["typescript"]["test"] == "vitest run"

    def test_increments_version(self, overrides: LearnedOverrides):
        overrides.add_gate_override("typescript", "test", "vitest run")
        data = overrides.load()
        assert data["version"] == 2

    def test_get_gate_overrides_empty_when_none(self, overrides: LearnedOverrides):
        assert overrides.get_gate_overrides() == {}


# ---------------------------------------------------------------------------
# add_agent_drop / get_agent_drops
# ---------------------------------------------------------------------------


class TestAgentDrops:
    def test_add_and_get_roundtrip(self, overrides: LearnedOverrides):
        overrides.add_agent_drop("visualization-expert")
        drops = overrides.get_agent_drops()
        assert "visualization-expert" in drops

    def test_multiple_drops(self, overrides: LearnedOverrides):
        overrides.add_agent_drop("visualization-expert")
        overrides.add_agent_drop("data-scientist")
        drops = overrides.get_agent_drops()
        assert "visualization-expert" in drops
        assert "data-scientist" in drops

    def test_idempotent_add(self, overrides: LearnedOverrides):
        overrides.add_agent_drop("visualization-expert")
        overrides.add_agent_drop("visualization-expert")
        drops = overrides.get_agent_drops()
        assert drops.count("visualization-expert") == 1

    def test_idempotent_does_not_increment_version(self, overrides: LearnedOverrides):
        overrides.add_agent_drop("visualization-expert")
        v1 = overrides.load()["version"]
        overrides.add_agent_drop("visualization-expert")
        v2 = overrides.load()["version"]
        assert v2 == v1

    def test_increments_version_on_first_add(self, overrides: LearnedOverrides):
        overrides.add_agent_drop("visualization-expert")
        data = overrides.load()
        assert data["version"] == 2

    def test_get_agent_drops_empty_when_none(self, overrides: LearnedOverrides):
        assert overrides.get_agent_drops() == []

    def test_returns_copy_not_reference(self, overrides: LearnedOverrides):
        overrides.add_agent_drop("agent-x")
        drops = overrides.get_agent_drops()
        drops.append("injected")
        # The stored drops should not be modified
        assert "injected" not in overrides.get_agent_drops()


# ---------------------------------------------------------------------------
# Atomic write / corruption safety
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_no_tmp_file_left_after_save(self, overrides: LearnedOverrides):
        overrides.add_agent_drop("agent-x")
        tmp_files = list(overrides._path.parent.glob(".overrides-*.tmp"))
        assert tmp_files == []

    def test_existing_file_replaced_not_appended(self, overrides: LearnedOverrides):
        overrides.add_agent_drop("agent-x")
        overrides.add_agent_drop("agent-y")
        drops = overrides.get_agent_drops()
        # Should contain both but exactly once each
        assert drops.count("agent-x") == 1
        assert drops.count("agent-y") == 1
