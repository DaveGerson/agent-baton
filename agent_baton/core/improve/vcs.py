"""Agent prompt version control -- backups and changelog tracking.

The VCS module provides the safety net for the improvement loop.  Before
any agent definition file is modified (whether by automated prompt
evolution or manual editing), a timestamped backup is created and a
changelog entry is written.

This enables:

* **Safe experimentation** -- applied recommendations can be reverted to
  the exact pre-change state.
* **Audit trail** -- the changelog records who changed what and why,
  supporting post-hoc review.
* **Rollback** --
  :class:`~agent_baton.core.improve.rollback.RollbackManager` uses
  :meth:`AgentVersionControl.restore_backup` to revert degraded
  experiments automatically.

Storage layout::

    agents/
      .backups/
        architect.20260324-120000.md
        backend-engineer.20260324-130000.md
      changelog.md
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class ChangelogEntry:
    """A single entry in the agent changelog.

    Attributes:
        timestamp: ISO 8601 UTC timestamp of the change.
        agent_name: Name of the agent whose definition was modified.
        action: Type of change: ``"created"``, ``"modified"``, or
            ``"archived"``.
        summary: Human-readable description of what changed and why.
        backup_path: Relative path to the ``.bak`` file if the agent was
            modified (empty string for creation events).
    """

    timestamp: str  # ISO format
    agent_name: str
    action: str  # "created", "modified", "archived"
    summary: str  # what changed and why
    backup_path: str = ""  # path to .bak file if modified

    def to_markdown(self) -> str:
        """Render the entry as a changelog.md section."""
        header = f"### {self.timestamp} — {self.agent_name} — {self.action}"
        lines: list[str] = [header, f"Summary: {self.summary}"]
        if self.backup_path:
            lines.append(f"Backup: {self.backup_path}")
        lines.append("")
        lines.append("---")
        lines.append("")
        return "\n".join(lines)


_CHANGELOG_HEADER = "# Agent Changelog\n\n"


class AgentVersionControl:
    """Track changes to agent definition files with backups and changelog.

    Provides three levels of API:

    * **Low-level**: :meth:`backup_agent`, :meth:`log_change`,
      :meth:`restore_backup` -- individual operations.
    * **High-level**: :meth:`track_modification`, :meth:`track_creation`
      -- combined backup + changelog in one call.
    * **Query**: :meth:`read_changelog`, :meth:`get_agent_history`,
      :meth:`list_backups` -- inspect the audit trail.
    """

    def __init__(self, agents_dir: Path | None = None) -> None:
        # Default to the canonical distributable agents/ directory relative to
        # the package root (three levels up from this file:
        # improve/ -> core/ -> agent_baton/ -> repo/).
        if agents_dir is None:
            agents_dir = Path(__file__).parent.parent.parent.parent / "agents"
        self._agents_dir = agents_dir

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def agents_dir(self) -> Path:
        return self._agents_dir

    @property
    def changelog_path(self) -> Path:
        return self._agents_dir / "changelog.md"

    @property
    def backup_dir(self) -> Path:
        return self._agents_dir / ".backups"

    # ------------------------------------------------------------------
    # Backup helpers
    # ------------------------------------------------------------------

    def backup_agent(self, agent_path: Path) -> Path:
        """Create a timestamped backup of an agent file before modifying it.

        The backup filename follows the pattern::

            <agents_dir>/.backups/<agent-name>.<YYYYMMDD-HHMMSS>.md

        If a backup with the same second-resolution timestamp already exists
        (rare, but possible in tests), a microsecond suffix is appended to
        guarantee uniqueness.

        Args:
            agent_path: Absolute path to the agent definition file to back up.

        Returns:
            Absolute path to the created backup file.
        """
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(tz=timezone.utc)
        ts = now.strftime("%Y%m%d-%H%M%S")
        backup_name = f"{agent_path.stem}.{ts}.md"
        backup_path = self.backup_dir / backup_name
        # If a backup with the same second already exists (rare but possible in
        # tests or rapid successive calls), append a microsecond suffix.
        if backup_path.exists():
            ts_micro = now.strftime("%Y%m%d-%H%M%S-%f")
            backup_name = f"{agent_path.stem}.{ts_micro}.md"
            backup_path = self.backup_dir / backup_name
        shutil.copy2(agent_path, backup_path)
        return backup_path

    # ------------------------------------------------------------------
    # Changelog I/O
    # ------------------------------------------------------------------

    def log_change(self, entry: ChangelogEntry) -> None:
        """Append a changelog entry to ``changelog.md``.

        Creates the file with a header if it does not yet exist.
        New entries are prepended directly after the header so that the
        most recent change appears first -- matching the convention of
        human-maintained changelogs.

        Args:
            entry: The changelog entry to persist.
        """
        new_block = entry.to_markdown()

        if not self.changelog_path.exists():
            self.changelog_path.parent.mkdir(parents=True, exist_ok=True)
            self.changelog_path.write_text(
                _CHANGELOG_HEADER + new_block, encoding="utf-8"
            )
            return

        existing = self.changelog_path.read_text(encoding="utf-8")
        # Insert after the header line(s); find the first '### ' entry or
        # append after the header if the file is otherwise empty.
        if _CHANGELOG_HEADER in existing:
            insert_at = existing.index(_CHANGELOG_HEADER) + len(_CHANGELOG_HEADER)
            updated = existing[:insert_at] + new_block + existing[insert_at:]
        else:
            # Malformed header — just prepend
            updated = _CHANGELOG_HEADER + new_block + existing

        self.changelog_path.write_text(updated, encoding="utf-8")

    def read_changelog(self) -> list[ChangelogEntry]:
        """Parse ``changelog.md`` and return entries, most recent first.

        Parsing is tolerant: fields missing from a block are silently
        defaulted to empty strings.  Each entry block starts with a
        ``### `` header line containing ``timestamp -- agent_name -- action``.

        Returns:
            List of :class:`ChangelogEntry` objects in reverse chronological
            order (newest first).  Returns an empty list if the changelog
            file does not exist.
        """
        if not self.changelog_path.exists():
            return []

        text = self.changelog_path.read_text(encoding="utf-8")
        entries: list[ChangelogEntry] = []

        # Each entry starts with '### '
        blocks = [b.strip() for b in text.split("### ") if b.strip()]
        for block in blocks:
            # Skip the file header line ("# Agent Changelog")
            if block.startswith("# "):
                continue

            lines = block.splitlines()
            if not lines:
                continue

            # First line: "<timestamp> — <agent_name> — <action>"
            header = lines[0]
            parts = [p.strip() for p in header.split("—")]
            timestamp = parts[0] if len(parts) > 0 else ""
            agent_name = parts[1] if len(parts) > 1 else ""
            action = parts[2] if len(parts) > 2 else ""

            summary = ""
            backup_path = ""
            for line in lines[1:]:
                if line.startswith("Summary:"):
                    summary = line[len("Summary:"):].strip()
                elif line.startswith("Backup:"):
                    backup_path = line[len("Backup:"):].strip()

            entries.append(
                ChangelogEntry(
                    timestamp=timestamp,
                    agent_name=agent_name,
                    action=action,
                    summary=summary,
                    backup_path=backup_path,
                )
            )

        return entries

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_agent_history(self, agent_name: str) -> list[ChangelogEntry]:
        """Return all changelog entries for a specific agent."""
        return [e for e in self.read_changelog() if e.agent_name == agent_name]

    def list_backups(self, agent_name: str | None = None) -> list[Path]:
        """List all backup files, optionally filtered by agent name.

        Returns paths sorted newest-first by filename (timestamp is embedded
        in the stem, so lexicographic order is chronological).
        """
        if not self.backup_dir.is_dir():
            return []

        paths = sorted(self.backup_dir.glob("*.md"), reverse=True)
        if agent_name is not None:
            paths = [p for p in paths if p.name.startswith(f"{agent_name}.")]
        return paths

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore_backup(self, backup_path: Path, target_path: Path) -> None:
        """Restore a backup file to the target path.

        A safety backup of the current file is created first so that the
        restore operation is itself reversible -- even a rollback can be
        rolled back.

        Args:
            backup_path: Path to the backup file to restore from.
            target_path: Path to the agent definition file to overwrite.
        """
        if target_path.exists():
            self.backup_agent(target_path)
        shutil.copy2(backup_path, target_path)

    # ------------------------------------------------------------------
    # High-level tracking API
    # ------------------------------------------------------------------

    def track_modification(self, agent_path: Path, summary: str) -> ChangelogEntry:
        """Back up the agent, log the change, and return the entry.

        This is the primary method other code calls *before* writing
        changes to an agent file.  It combines three operations atomically:

        1. Creates a timestamped backup via :meth:`backup_agent`.
        2. Writes a changelog entry via :meth:`log_change`.
        3. Returns the entry for the caller to inspect or forward.

        Args:
            agent_path: Absolute path to the agent definition file about
                to be modified.
            summary: Human-readable description of the change and its
                motivation.

        Returns:
            The created :class:`ChangelogEntry` with the backup path
            recorded.
        """
        backup_path = self.backup_agent(agent_path)
        ts = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        # Make the backup reference relative so it is portable across machines.
        try:
            rel_backup = backup_path.relative_to(self._agents_dir)
        except ValueError:
            rel_backup = backup_path  # type: ignore[assignment]

        entry = ChangelogEntry(
            timestamp=ts,
            agent_name=agent_path.stem,
            action="modified",
            summary=summary,
            backup_path=str(rel_backup),
        )
        self.log_change(entry)
        return entry

    def track_creation(self, agent_path: Path, summary: str) -> ChangelogEntry:
        """Log the creation of a new agent (no backup needed).

        Args:
            agent_path: Absolute path to the newly created agent definition.
            summary: Human-readable description of the new agent and its
                purpose.

        Returns:
            The created :class:`ChangelogEntry` with ``action="created"``.
        """
        ts = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        entry = ChangelogEntry(
            timestamp=ts,
            agent_name=agent_path.stem,
            action="created",
            summary=summary,
            backup_path="",
        )
        self.log_change(entry)
        return entry
