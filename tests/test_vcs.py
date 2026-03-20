"""Tests for agent_baton.core.vcs.AgentVersionControl."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.vcs import AgentVersionControl, ChangelogEntry


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


# ---------------------------------------------------------------------------
# backup_agent
# ---------------------------------------------------------------------------

class TestBackupAgent:
    def test_creates_backup_file_in_backups_dir(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "arch")
        backup = vcs.backup_agent(agent_file)
        assert backup.exists()
        assert backup.parent == vcs.backup_dir

    def test_backup_filename_contains_agent_stem(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "backend-engineer")
        backup = vcs.backup_agent(agent_file)
        assert backup.name.startswith("backend-engineer.")

    def test_backup_filename_has_md_extension(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "arch")
        backup = vcs.backup_agent(agent_file)
        assert backup.suffix == ".md"

    def test_backup_contains_original_content(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        original_content = "# Original agent\nSome content here.\n"
        agent_file = _make_agent_file(agents_dir, "arch", original_content)
        backup = vcs.backup_agent(agent_file)
        assert backup.read_text(encoding="utf-8") == original_content

    def test_backup_dir_is_inside_agents_dir(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        assert vcs.backup_dir == agents_dir / ".backups"

    def test_backup_creates_backups_dir_if_missing(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        assert not vcs.backup_dir.exists()
        _make_agent_file(agents_dir, "x")
        vcs.backup_agent(agents_dir / "x.md")
        assert vcs.backup_dir.is_dir()

    def test_two_backups_of_same_agent_both_exist(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "arch")
        b1 = vcs.backup_agent(agent_file)
        b2 = vcs.backup_agent(agent_file)
        # They may share a name if within the same second (handled by microsecond suffix)
        assert b1.exists()
        assert b2.exists()


# ---------------------------------------------------------------------------
# log_change / changelog_path
# ---------------------------------------------------------------------------

class TestLogChange:
    def test_creates_changelog_md_when_missing(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        assert not vcs.changelog_path.exists()
        vcs.log_change(_entry())
        assert vcs.changelog_path.exists()

    def test_changelog_path_is_agents_dir_slash_changelog(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        assert vcs.changelog_path == agents_dir / "changelog.md"

    def test_changelog_starts_with_header(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry())
        content = vcs.changelog_path.read_text(encoding="utf-8")
        assert content.startswith("# Agent Changelog")

    def test_log_change_appends_entry_content(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(agent_name="arch", summary="Initial create"))
        content = vcs.changelog_path.read_text(encoding="utf-8")
        assert "arch" in content
        assert "Initial create" in content

    def test_second_entry_prepended_after_header(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(agent_name="agent-a", timestamp="2026-01-01T00:00:00"))
        vcs.log_change(_entry(agent_name="agent-b", timestamp="2026-02-01T00:00:00"))
        content = vcs.changelog_path.read_text(encoding="utf-8")
        # agent-b is more recent and should appear first in the content
        idx_a = content.index("agent-a")
        idx_b = content.index("agent-b")
        assert idx_b < idx_a

    def test_backup_path_present_in_changelog_when_provided(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(backup_path=".backups/arch.20260101-120000.md"))
        content = vcs.changelog_path.read_text(encoding="utf-8")
        assert ".backups/arch.20260101-120000.md" in content

    def test_backup_path_absent_when_empty(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(backup_path=""))
        content = vcs.changelog_path.read_text(encoding="utf-8")
        assert "Backup:" not in content


# ---------------------------------------------------------------------------
# read_changelog
# ---------------------------------------------------------------------------

class TestReadChangelog:
    def test_returns_empty_when_no_file(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        assert vcs.read_changelog() == []

    def test_returns_one_entry_after_one_log(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(agent_name="arch", action="created",
                              summary="New agent"))
        entries = vcs.read_changelog()
        assert len(entries) == 1

    def test_parsed_entry_fields(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(agent_name="arch", action="modified",
                              summary="Improved prompt",
                              timestamp="2026-03-01T10:00:00"))
        entry = vcs.read_changelog()[0]
        assert entry.agent_name == "arch"
        assert entry.action == "modified"
        assert entry.summary == "Improved prompt"
        assert "2026-03-01T10:00:00" in entry.timestamp

    def test_multiple_entries_all_parsed(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(agent_name="a"))
        vcs.log_change(_entry(agent_name="b"))
        vcs.log_change(_entry(agent_name="c"))
        assert len(vcs.read_changelog()) == 3

    def test_backup_path_parsed_correctly(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(backup_path=".backups/arch.20260101-120000.md"))
        entry = vcs.read_changelog()[0]
        assert ".backups/arch.20260101-120000.md" in entry.backup_path


# ---------------------------------------------------------------------------
# get_agent_history
# ---------------------------------------------------------------------------

class TestGetAgentHistory:
    def test_filters_by_agent_name(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(agent_name="arch"))
        vcs.log_change(_entry(agent_name="backend"))
        vcs.log_change(_entry(agent_name="arch"))
        history = vcs.get_agent_history("arch")
        assert len(history) == 2
        assert all(e.agent_name == "arch" for e in history)

    def test_returns_empty_for_unknown_agent(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        vcs.log_change(_entry(agent_name="arch"))
        assert vcs.get_agent_history("nonexistent") == []

    def test_returns_empty_when_no_changelog(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        assert vcs.get_agent_history("arch") == []


# ---------------------------------------------------------------------------
# list_backups
# ---------------------------------------------------------------------------

class TestListBackups:
    def test_returns_empty_when_no_backups_dir(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        assert vcs.list_backups() == []

    def test_lists_all_backup_files(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        for name in ("agent-a", "agent-b", "agent-c"):
            _make_agent_file(agents_dir, name)
            vcs.backup_agent(agents_dir / f"{name}.md")
        backups = vcs.list_backups()
        assert len(backups) == 3

    def test_filters_by_agent_name(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        _make_agent_file(agents_dir, "arch")
        _make_agent_file(agents_dir, "backend")
        vcs.backup_agent(agents_dir / "arch.md")
        vcs.backup_agent(agents_dir / "backend.md")
        backups = vcs.list_backups(agent_name="arch")
        assert len(backups) == 1
        assert all(p.name.startswith("arch.") for p in backups)

    def test_returns_empty_filter_for_unknown_agent(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        _make_agent_file(agents_dir, "arch")
        vcs.backup_agent(agents_dir / "arch.md")
        assert vcs.list_backups(agent_name="ghost") == []

    def test_backups_sorted_newest_first(self, tmp_path: Path):
        """File names include timestamps so lexicographic reverse is newest-first."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        # Manually create backup files with predictable timestamps
        vcs.backup_dir.mkdir(parents=True, exist_ok=True)
        (vcs.backup_dir / "arch.20260101-000000.md").write_text("old")
        (vcs.backup_dir / "arch.20260202-000000.md").write_text("mid")
        (vcs.backup_dir / "arch.20260303-000000.md").write_text("new")
        backups = vcs.list_backups(agent_name="arch")
        names = [p.name for p in backups]
        assert names[0] == "arch.20260303-000000.md"
        assert names[-1] == "arch.20260101-000000.md"


# ---------------------------------------------------------------------------
# track_modification
# ---------------------------------------------------------------------------

class TestTrackModification:
    def test_creates_backup(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "arch")
        vcs.track_modification(agent_file, "Rewrote system prompt")
        assert len(vcs.list_backups(agent_name="arch")) == 1

    def test_logs_change_with_modified_action(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "arch")
        vcs.track_modification(agent_file, "Updated tools list")
        entries = vcs.read_changelog()
        assert len(entries) == 1
        assert entries[0].action == "modified"
        assert entries[0].summary == "Updated tools list"

    def test_returns_changelog_entry(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "arch")
        result = vcs.track_modification(agent_file, "Some change")
        assert isinstance(result, ChangelogEntry)

    def test_entry_has_relative_backup_path(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "arch")
        entry = vcs.track_modification(agent_file, "Change")
        # The backup path should be relative (e.g., ".backups/arch.YYYYMMDD-HHMMSS.md")
        assert not entry.backup_path.startswith("/")

    def test_entry_agent_name_matches_stem(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "backend-engineer")
        entry = vcs.track_modification(agent_file, "Change")
        assert entry.agent_name == "backend-engineer"


# ---------------------------------------------------------------------------
# track_creation
# ---------------------------------------------------------------------------

class TestTrackCreation:
    def test_logs_creation_action(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "new-agent")
        entry = vcs.track_creation(agent_file, "Created new specialist")
        assert entry.action == "created"

    def test_no_backup_created(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "new-agent")
        vcs.track_creation(agent_file, "Created")
        assert vcs.list_backups() == []

    def test_changelog_entry_has_empty_backup_path(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "new-agent")
        entry = vcs.track_creation(agent_file, "Created")
        assert entry.backup_path == ""

    def test_returns_changelog_entry(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "x")
        result = vcs.track_creation(agent_file, "summary")
        assert isinstance(result, ChangelogEntry)

    def test_summary_in_changelog(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "new-agent")
        vcs.track_creation(agent_file, "Brand new agent for finance tasks")
        entries = vcs.read_changelog()
        assert entries[0].summary == "Brand new agent for finance tasks"


# ---------------------------------------------------------------------------
# restore_backup
# ---------------------------------------------------------------------------

class TestRestoreBackup:
    def test_restores_backup_content_to_target(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "arch", "# Version 1\n")
        backup = vcs.backup_agent(agent_file)
        # Now overwrite the original
        agent_file.write_text("# Version 2\n", encoding="utf-8")
        vcs.restore_backup(backup, agent_file)
        assert agent_file.read_text(encoding="utf-8") == "# Version 1\n"

    def test_restore_creates_safety_backup_of_current(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        agent_file = _make_agent_file(agents_dir, "arch", "# v1\n")
        backup = vcs.backup_agent(agent_file)
        agent_file.write_text("# v2\n", encoding="utf-8")
        # Before restore: 1 backup
        assert len(vcs.list_backups(agent_name="arch")) == 1
        vcs.restore_backup(backup, agent_file)
        # After restore: 2 backups (original + safety backup of v2)
        assert len(vcs.list_backups(agent_name="arch")) == 2

    def test_restore_works_when_target_does_not_exist(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        vcs = AgentVersionControl(agents_dir)
        # Create a backup file manually
        vcs.backup_dir.mkdir(parents=True, exist_ok=True)
        backup_file = vcs.backup_dir / "arch.20260101-000000.md"
        backup_file.write_text("# Restored\n", encoding="utf-8")
        target = agents_dir / "arch.md"
        assert not target.exists()
        vcs.restore_backup(backup_file, target)
        assert target.read_text(encoding="utf-8") == "# Restored\n"
