"""LearnedOverrides — read/write auto-applied corrections.

Persists corrections to ``.claude/team-context/learned-overrides.json``.
Consumers load overrides at call time so that any applied fix is picked up
without requiring a restart.

File layout::

    {
      "flavor_map": {
        "python/react": {"backend-engineer": "python", "frontend-engineer": "react"}
      },
      "gate_commands": {
        "typescript": {"test": "vitest run", "build": "npx tsc --noEmit"}
      },
      "agent_drops": ["visualization-expert"],
      "classifier_adjustments": {
        "min_keyword_overlap": 3
      },
      "version": 2,
      "last_updated": "2026-04-13T12:00:00Z"
    }
"""
from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
from pathlib import Path

from agent_baton.utils.time import utcnow_iso as _utcnow

_log = logging.getLogger(__name__)

_DEFAULT_PATH = Path(".claude/team-context/learned-overrides.json")

_EMPTY_OVERRIDES: dict = {
    "flavor_map": {},
    "gate_commands": {},
    "agent_drops": [],
    "classifier_adjustments": {},
    "version": 1,
    "last_updated": "",
}


class LearnedOverrides:
    """Read/write interface for the learned-overrides.json file.

    All mutations write atomically: data is written to a temp file in the
    same directory then renamed so readers never see partial writes.

    Args:
        overrides_path: Path to the JSON file.  Defaults to
            ``.claude/team-context/learned-overrides.json`` relative to cwd.
    """

    def __init__(self, overrides_path: Path | None = None) -> None:
        self._path = (overrides_path or _DEFAULT_PATH).resolve()

    # ------------------------------------------------------------------
    # Core I/O
    # ------------------------------------------------------------------

    def load(self) -> dict:
        """Load the overrides file.  Returns empty defaults on any error."""
        try:
            if self._path.exists():
                raw = self._path.read_text(encoding="utf-8")
                data = json.loads(raw)
                # Ensure all expected top-level keys are present.
                # Use deepcopy so callers cannot accidentally mutate the
                # module-level _EMPTY_OVERRIDES sentinel through nested dicts.
                merged = copy.deepcopy(_EMPTY_OVERRIDES)
                merged.update(data)
                return merged
        except Exception as exc:
            _log.debug("LearnedOverrides.load failed (%s): %s", self._path, exc)
        return copy.deepcopy(_EMPTY_OVERRIDES)

    def save(self, data: dict) -> None:
        """Write *data* atomically to the overrides file."""
        data["last_updated"] = _utcnow()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, indent=2, ensure_ascii=False)
        # Atomic write via temp file in same directory
        fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, prefix=".overrides-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_flavor_override(self, stack_key: str, agent_base: str, flavor: str) -> None:
        """Write a FLAVOR_MAP override for *agent_base* under *stack_key*.

        Args:
            stack_key: Composite key such as ``"python/react"``.
            agent_base: Base agent name, e.g. ``"backend-engineer"``.
            flavor: Flavor suffix, e.g. ``"python"``.
        """
        data = self.load()
        flavor_map: dict = data.setdefault("flavor_map", {})
        stack_entry: dict = flavor_map.setdefault(stack_key, {})
        stack_entry[agent_base] = flavor
        data["version"] = data.get("version", 1) + 1
        self.save(data)

    def add_gate_override(self, language: str, gate_type: str, command: str) -> None:
        """Write a gate command override for *language* / *gate_type*.

        Args:
            language: Stack language key, e.g. ``"typescript"``.
            gate_type: Gate category, e.g. ``"test"`` or ``"build"``.
            command: Shell command to use, e.g. ``"vitest run"``.
        """
        data = self.load()
        gate_commands: dict = data.setdefault("gate_commands", {})
        lang_entry: dict = gate_commands.setdefault(language, {})
        lang_entry[gate_type] = command
        data["version"] = data.get("version", 1) + 1
        self.save(data)

    def add_agent_drop(self, agent_name: str) -> None:
        """Add *agent_name* to the persistent drop list.

        Idempotent — adding an agent already in the list is a no-op.

        Args:
            agent_name: Fully-qualified agent name or base name.
        """
        data = self.load()
        drops: list[str] = data.setdefault("agent_drops", [])
        if agent_name not in drops:
            drops.append(agent_name)
            data["version"] = data.get("version", 1) + 1
            self.save(data)

    def remove_override(self, issue_id: str) -> bool:
        """Remove an override by its associated issue_id.

        In practice this is used for rollback: the ``issue_id`` is stored
        in the override value (when possible) or the caller provides context.
        For the current implementation, ``issue_id`` is treated as a hint
        and is logged; actual removal requires direct key manipulation.

        Returns:
            True if any data was modified.
        """
        # The current JSON format doesn't index by issue_id directly.
        # This method is a hook for future rollback integration.
        _log.info(
            "remove_override called for issue %s — override removal requires"
            " manual key deletion from %s",
            issue_id,
            self._path,
        )
        return False

    # ------------------------------------------------------------------
    # Read accessors
    # ------------------------------------------------------------------

    def get_flavor_overrides(self) -> dict[str, dict[str, str]]:
        """Return the flavor_map override dict, keyed by stack_key."""
        return self.load().get("flavor_map", {})

    def get_gate_overrides(self) -> dict[str, dict[str, str]]:
        """Return gate command overrides, keyed by language then gate_type."""
        return self.load().get("gate_commands", {})

    def get_agent_drops(self) -> list[str]:
        """Return the list of agent names that should be dropped from plans."""
        return list(self.load().get("agent_drops", []))

    def get_classifier_adjustments(self) -> dict:
        """Return classifier threshold adjustments."""
        return dict(self.load().get("classifier_adjustments", {}))
