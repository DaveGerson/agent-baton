"""PmoStore -- legacy JSON file-based PMO configuration and archive.

This is the original file-based PMO persistence layer.  It has been
superseded by ``PmoSqliteStore`` (backed by ``pmo.db`` or ``central.db``)
for new installations, but remains available for backward compatibility.

Persistence paths:
    ``~/.baton/pmo-config.json`` -- project registry, programs, and signals.
        Written atomically via tmp+rename to prevent partial writes.
    ``~/.baton/pmo-archive.jsonl`` -- append-only log of completed
        execution cards.  Each line is a JSON-serialized ``PmoCard``.

The ``PmoSqliteStore`` in ``pmo_sqlite.py`` implements the same public
interface (``register_project``, ``unregister_project``, ``get_project``,
``add_signal``, ``resolve_signal``, ``get_open_signals``,
``archive_card``, ``read_archive``, ``load_config``, ``save_config``)
so callers can switch backends transparently.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.models.pmo import PmoCard, PmoConfig, PmoProject, PmoSignal

_DEFAULT_BATON_DIR = Path.home() / ".baton"
_CONFIG_FILENAME = "pmo-config.json"
_ARCHIVE_FILENAME = "pmo-archive.jsonl"


class PmoStore:
    """Read/write PMO configuration and completed-plan archive.

    This is the legacy file-based implementation.  It stores all state in
    two files: a JSON config file (projects, programs, signals) and a
    JSONL archive (completed cards).  Config writes are atomic (tmp file
    + rename).

    Attributes:
        _config_path: Path to ``pmo-config.json``.
        _archive_path: Path to ``pmo-archive.jsonl``.
    """

    def __init__(
        self,
        config_path: Path | None = None,
        archive_path: Path | None = None,
    ) -> None:
        baton_dir = _DEFAULT_BATON_DIR
        self._config_path = config_path or (baton_dir / _CONFIG_FILENAME)
        self._archive_path = archive_path or (baton_dir / _ARCHIVE_FILENAME)

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def archive_path(self) -> Path:
        return self._archive_path

    # ── Config (JSON, atomic write) ────────────────────────────────────────

    def load_config(self) -> PmoConfig:
        """Load PMO config from ``pmo-config.json``.

        Returns:
            A ``PmoConfig`` populated from the file, or an empty
            ``PmoConfig`` if the file does not exist or is malformed.
        """
        if not self._config_path.exists():
            return PmoConfig()
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
            return PmoConfig.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return PmoConfig()

    def save_config(self, config: PmoConfig) -> None:
        """Atomically write config to disk via tmp file + rename.

        Creates parent directories if needed, writes to a ``.json.tmp``
        file, then renames to the final path.  This prevents readers from
        seeing a partially-written file.

        Args:
            config: The PMO configuration to persist.
        """
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._config_path.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(config.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.rename(self._config_path)

    # ── Project registration ───────────────────────────────────────────────

    def register_project(self, project: PmoProject) -> None:
        """Add or update a project in the config.

        Removes any existing project with the same ``project_id``, sets
        ``registered_at`` if not already populated, appends the project,
        and atomically writes the config to disk.

        Args:
            project: The project to register.
        """
        config = self.load_config()
        config.projects = [
            p for p in config.projects if p.project_id != project.project_id
        ]
        if not project.registered_at:
            project.registered_at = datetime.now(timezone.utc).isoformat()
        config.projects.append(project)
        self.save_config(config)

    def unregister_project(self, project_id: str) -> bool:
        """Remove a project from the config by ID.

        Args:
            project_id: The identifier of the project to remove.

        Returns:
            ``True`` if a matching project was found and removed,
            ``False`` otherwise.
        """
        config = self.load_config()
        before = len(config.projects)
        config.projects = [
            p for p in config.projects if p.project_id != project_id
        ]
        if len(config.projects) < before:
            self.save_config(config)
            return True
        return False

    def get_project(self, project_id: str) -> PmoProject | None:
        """Look up a project by ID.

        Args:
            project_id: The identifier to search for.

        Returns:
            The matching ``PmoProject``, or ``None`` if not found.
        """
        config = self.load_config()
        for p in config.projects:
            if p.project_id == project_id:
                return p
        return None

    # ── Signals ────────────────────────────────────────────────────────────

    def add_signal(self, signal: PmoSignal) -> None:
        """Add a signal to the config and persist.

        Sets ``created_at`` to the current UTC timestamp if not already
        populated.  The signal is appended to the in-memory config and
        then atomically written to disk.

        Args:
            signal: The signal to add.
        """
        config = self.load_config()
        if not signal.created_at:
            signal.created_at = datetime.now(timezone.utc).isoformat()
        config.signals.append(signal)
        self.save_config(config)

    def resolve_signal(self, signal_id: str) -> bool:
        """Mark a signal as resolved and persist.

        Sets ``status = 'resolved'`` and ``resolved_at`` to the current
        UTC timestamp.

        Args:
            signal_id: The signal to resolve.

        Returns:
            ``True`` if the signal was found, ``False`` otherwise.
        """
        config = self.load_config()
        for s in config.signals:
            if s.signal_id == signal_id:
                s.status = "resolved"
                s.resolved_at = datetime.now(timezone.utc).isoformat()
                self.save_config(config)
                return True
        return False

    def get_signal(self, signal_id: str) -> PmoSignal | None:
        """Return a single signal by ID, or None if not found."""
        config = self.load_config()
        for s in config.signals:
            if s.signal_id == signal_id:
                return s
        return None

    def resolve_signals(self, signal_ids: list[str]) -> tuple[list[str], list[str]]:
        """Resolve multiple signals in a single config write.

        Marks each matching signal as ``"resolved"`` with ``resolved_at``
        set to the current UTC timestamp, then atomically writes the config
        once.  Unknown IDs are collected and returned so callers can report
        them to the client.

        Args:
            signal_ids: List of signal IDs to resolve.

        Returns:
            A ``(resolved, not_found)`` tuple where ``resolved`` contains
            the IDs that were successfully resolved and ``not_found``
            contains IDs that had no matching signal.
        """
        config = self.load_config()
        signal_map = {s.signal_id: s for s in config.signals}
        resolved: list[str] = []
        not_found: list[str] = []
        now = datetime.now(timezone.utc).isoformat()
        for sid in signal_ids:
            if sid in signal_map:
                signal_map[sid].status = "resolved"
                signal_map[sid].resolved_at = now
                resolved.append(sid)
            else:
                not_found.append(sid)
        if resolved:
            self.save_config(config)
        return resolved, not_found

    def get_open_signals(self) -> list[PmoSignal]:
        """Return all signals with status != resolved."""
        config = self.load_config()
        return [s for s in config.signals if s.status != "resolved"]

    # ── Archive (JSONL, append-only) ───────────────────────────────────────

    def archive_card(self, card: PmoCard) -> None:
        """Append a completed card to the JSONL archive.

        Each card is serialized as a single compact JSON line and appended
        to ``pmo-archive.jsonl``.  The archive is append-only -- cards are
        never updated or removed.

        Args:
            card: The completed execution card to archive.
        """
        self._archive_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(card.to_dict(), separators=(",", ":"))
        with self._archive_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read_archive(self, limit: int = 100) -> list[PmoCard]:
        """Read archived cards. Returns most recent `limit` entries."""
        if not self._archive_path.exists():
            return []
        cards: list[PmoCard] = []
        with self._archive_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    cards.append(PmoCard.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError):
                    continue
        return cards[-limit:]
