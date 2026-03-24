"""Tests for agent_baton.core.vcs.AgentVersionControl."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.improve.vcs import AgentVersionControl, ChangelogEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent_file(agents_dir: Path, name: str = "my-agent",
                     content: str = "# Agent content\n") -> Path:
    path = agents_dir / f"{name}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _entry(
    agent_name: str = "my-agent",
    action: str = "modified",
    summary: str = "Test change",
    backup_path: str = "",
    timestamp: str = "2026-03-01T10:00:00",
) -> ChangelogEntry:
    return ChangelogEntry(
        timestamp=timestamp,
        agent_name=agent_name,
        action=action,
        summary=summary,
        backup_path=backup_path,
    )


@pytest.fixture
def agents_dir(tmp_path: Path) -> Path:
    d = tmp_path / "agents"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# backup_agent
# ---------------------------------------------------------------------------

class TestBackupAgent:
    def test_backup_file_properties(self, agents_dir: Path):
        vcs = AgentVersionControl(agents_dir)
        original_content = "# Original agent\nSome content here.\n"
        agent_file = _make_agent_file(agents_dir, "backend-engineer", original_content)
        backup = vcs.backup_agent(agent_file)

        assert backup.exists()
        assert backup.parent == vcs.backup_dir
        assert backup.name.startswith("backend-engineer.")
        assert backup.suffix == ".md"
        assert backup.read_text(encoding="utf-8") == original_content

    def test_backup_dir_location_and_creation(self, agents_dir: Path):
        vcs = AgentVersionControl(agents_dir)
        assert vcs.backup_dir == agents_dir / ".backups"
        assert not vcs.backup_dir.exists()
        _make_agent_file(agents_dir, "x")
        vcs.backup_agent(agents_dir / "x.md")
        assert vcs.backup_dir.is_dir()

    def test_two_backups_of_same_agent_both_exist(self, agents_dir: Path):
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "arch")
        b1 = vcs.backup_agent(agent_file)
        b2 = vcs.backup_agent(agent_file)
        assert b1.exists()
        assert b2.exists()


# ---------------------------------------------------------------------------
# log_change / changelog_path
# ---------------------------------------------------------------------------

class TestLogChange:
    def test_changelog_path_and_initial_structure(self, agents_dir: Path):
        vcs = AgentVersionControl(agents_dir)
        assert vcs.changelog_path == agents_dir / "changelog.md"
        assert not vcs.changelog_path.exists()

        vcs.log_change(_entry(agent_name="arch", summary="Initial create"))
        content = vcs.changelog_path.read_text(encoding="utf-8")
        assert content.startswith("# Agent Changelog")
        assert "arch" in content
        assert "Initial create" in content

    def test_second_entry_prepended_after_header(self, agents_dir: Path):
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(agent_name="agent-a", timestamp="2026-01-01T00:00:00"))
        vcs.log_change(_entry(agent_name="agent-b", timestamp="2026-02-01T00:00:00"))
        content = vcs.changelog_path.read_text(encoding="utf-8")
        assert content.index("agent-b") < content.index("agent-a")

    @pytest.mark.parametrize("backup_path,should_appear", [
        (".backups/arch.20260101-120000.md", True),
        ("", False),
    ])
    def test_backup_path_in_changelog(self, agents_dir: Path, backup_path, should_appear):
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(backup_path=backup_path))
        content = vcs.changelog_path.read_text(encoding="utf-8")
        if should_appear:
            assert backup_path in content
        else:
            assert "Backup:" not in content


# ---------------------------------------------------------------------------
# read_changelog
# ---------------------------------------------------------------------------

class TestReadChangelog:
    def test_returns_empty_when_no_file(self, agents_dir: Path):
        assert AgentVersionControl(agents_dir).read_changelog() == []

    def test_roundtrip_multiple_entries(self, agents_dir: Path):
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(agent_name="arch", action="modified",
                              summary="Improved prompt",
                              timestamp="2026-03-01T10:00:00"))
        vcs.log_change(_entry(agent_name="b"))
        vcs.log_change(_entry(agent_name="c"))
        entries = vcs.read_changelog()
        assert len(entries) == 3

        # Verify parsed fields of the most recently written entry (last read = first parsed)
        arch_entry = next(e for e in entries if e.agent_name == "arch")
        assert arch_entry.action == "modified"
        assert arch_entry.summary == "Improved prompt"
        assert "2026-03-01T10:00:00" in arch_entry.timestamp

    def test_backup_path_parsed_correctly(self, agents_dir: Path):
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(backup_path=".backups/arch.20260101-120000.md"))
        entry = vcs.read_changelog()[0]
        assert ".backups/arch.20260101-120000.md" in entry.backup_path


# ---------------------------------------------------------------------------
# get_agent_history
# ---------------------------------------------------------------------------

class TestGetAgentHistory:
    def test_filters_by_agent_name(self, agents_dir: Path):
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(agent_name="arch"))
        vcs.log_change(_entry(agent_name="backend"))
        vcs.log_change(_entry(agent_name="arch"))
        history = vcs.get_agent_history("arch")
        assert len(history) == 2
        assert all(e.agent_name == "arch" for e in history)

    @pytest.mark.parametrize("setup,agent,expected_len", [
        (lambda v: v.log_change(_entry(agent_name="arch")), "nonexistent", 0),
        (lambda v: None, "arch", 0),  # no changelog at all
    ])
    def test_returns_empty_for_unknown_or_no_changelog(
        self, agents_dir: Path, setup, agent, expected_len
    ):
        vcs = AgentVersionControl(agents_dir)
        setup(vcs)
        assert len(vcs.get_agent_history(agent)) == expected_len


# ---------------------------------------------------------------------------
# list_backups
# ---------------------------------------------------------------------------

class TestListBackups:
    def test_returns_empty_when_no_backups_dir(self, agents_dir: Path):
        assert AgentVersionControl(agents_dir).list_backups() == []

    def test_lists_all_backup_files(self, agents_dir: Path):
        vcs = AgentVersionControl(agents_dir)
        for name in ("agent-a", "agent-b", "agent-c"):
            _make_agent_file(agents_dir, name)
            vcs.backup_agent(agents_dir / f"{name}.md")
        assert len(vcs.list_backups()) == 3

    @pytest.mark.parametrize("filter_name,expected_count,check_fn", [
        ("arch", 1, lambda ps: all(p.name.startswith("arch.") for p in ps)),
        ("ghost", 0, lambda ps: True),
    ])
    def test_filter_by_agent_name(
        self, agents_dir: Path, filter_name, expected_count, check_fn
    ):
        vcs = AgentVersionControl(agents_dir)
        _make_agent_file(agents_dir, "arch")
        _make_agent_file(agents_dir, "backend")
        vcs.backup_agent(agents_dir / "arch.md")
        vcs.backup_agent(agents_dir / "backend.md")
        backups = vcs.list_backups(agent_name=filter_name)
        assert len(backups) == expected_count
        assert check_fn(backups)

    def test_backups_sorted_newest_first(self, agents_dir: Path):
        """File names include timestamps so lexicographic reverse is newest-first."""
        vcs = AgentVersionControl(agents_dir)
        vcs.backup_dir.mkdir(parents=True, exist_ok=True)
        (vcs.backup_dir / "arch.20260101-000000.md").write_text("old")
        (vcs.backup_dir / "arch.20260202-000000.md").write_text("mid")
        (vcs.backup_dir / "arch.20260303-000000.md").write_text("new")
        backups = vcs.list_backups(agent_name="arch")
        names = [p.name for p in backups]
        assert names[0] == "arch.20260303-000000.md"
        assert names[-1] == "arch.20260101-000000.md"


# ---------------------------------------------------------------------------
# track_modification and track_creation (parameterized by action type)
# ---------------------------------------------------------------------------

class TestTrackActions:
    @pytest.mark.parametrize("action_type,method,expected_action,has_backup", [
        ("modification", "track_modification", "modified", True),
        ("creation", "track_creation", "created", False),
    ])
    def test_track_action_core_behavior(
        self, agents_dir: Path, action_type, method, expected_action, has_backup
    ):
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "arch")
        summary = f"Test {action_type}"
        entry = getattr(vcs, method)(agent_file, summary)

        assert isinstance(entry, ChangelogEntry)
        assert entry.action == expected_action
        assert entry.agent_name == "arch"
        assert entry.summary == summary

        entries = vcs.read_changelog()
        assert len(entries) == 1
        assert entries[0].action == expected_action

        backups = vcs.list_backups()
        if has_backup:
            assert len(backups) == 1
            assert not entry.backup_path.startswith("/")
        else:
            assert backups == []
            assert entry.backup_path == ""

    def test_track_modification_agent_name_matches_stem(self, agents_dir: Path):
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "backend-engineer")
        entry = vcs.track_modification(agent_file, "Change")
        assert entry.agent_name == "backend-engineer"


# ---------------------------------------------------------------------------
# restore_backup
# ---------------------------------------------------------------------------

class TestRestoreBackup:
    def test_restores_backup_content_to_target(self, agents_dir: Path):
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "arch", "# Version 1\n")
        backup = vcs.backup_agent(agent_file)
        agent_file.write_text("# Version 2\n", encoding="utf-8")
        vcs.restore_backup(backup, agent_file)
        assert agent_file.read_text(encoding="utf-8") == "# Version 1\n"

    def test_restore_creates_safety_backup_of_current(self, agents_dir: Path):
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "arch", "# v1\n")
        backup = vcs.backup_agent(agent_file)
        agent_file.write_text("# v2\n", encoding="utf-8")
        assert len(vcs.list_backups(agent_name="arch")) == 1
        vcs.restore_backup(backup, agent_file)
        assert len(vcs.list_backups(agent_name="arch")) == 2

    def test_restore_works_when_target_does_not_exist(self, agents_dir: Path):
        vcs = AgentVersionControl(agents_dir)
        vcs.backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = vcs.backup_dir / "arch.20260101-000000.md"
        backup_file.write_text("# Restored\n", encoding="utf-8")
        target = agents_dir / "arch.md"
        assert not target.exists()
        vcs.restore_backup(backup_file, target)
        assert target.read_text(encoding="utf-8") == "# Restored\n"
