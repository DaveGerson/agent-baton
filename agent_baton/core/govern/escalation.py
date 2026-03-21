"""EscalationManager — read/write the escalations.md file.

**Status: Experimental** — built and tested but not yet validated with real usage data.
"""
from __future__ import annotations

import re
from pathlib import Path

from agent_baton.models.escalation import Escalation


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(
    r"^###\s+(.+?)\s+—\s+(.+?)\s+—\s+(PENDING|RESOLVED)\s*$",
    re.MULTILINE,
)


def _parse_field(block: str, name: str) -> str:
    """Extract the value of a **Field:** line from a block."""
    pattern = re.compile(rf"^\*\*{re.escape(name)}:\*\*\s*(.*)", re.MULTILINE)
    m = pattern.search(block)
    return m.group(1).strip() if m else ""


def _parse_block(block: str) -> Escalation | None:
    """Parse a single escalation block.  Returns None if the block is empty."""
    block = block.strip()
    if not block:
        return None

    m = _HEADER_RE.search(block)
    if not m:
        return None

    timestamp = m.group(1).strip()
    agent_name = m.group(2).strip()
    resolved = m.group(3).strip() == "RESOLVED"

    priority = _parse_field(block, "Priority")
    question = _parse_field(block, "Question")
    context = _parse_field(block, "Context")
    options_raw = _parse_field(block, "Options")
    options = [o.strip() for o in options_raw.split(",") if o.strip()] if options_raw else []
    answer = _parse_field(block, "Answer")

    return Escalation(
        agent_name=agent_name,
        question=question,
        context=context,
        options=options,
        priority=priority,
        timestamp=timestamp,
        resolved=resolved,
        answer=answer,
    )


def _serialize_all(escalations: list[Escalation]) -> str:
    """Render all escalations to the full file content."""
    lines = ["# Escalations", ""]
    for esc in escalations:
        lines.append(esc.to_markdown())
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# EscalationManager
# ---------------------------------------------------------------------------

class EscalationManager:
    """Manage the escalation file at .claude/team-context/escalations.md."""

    def __init__(self, path: Path | None = None) -> None:
        self._path: Path = path or Path(".claude/team-context/escalations.md")

    @property
    def path(self) -> Path:
        return self._path

    # ── I/O helpers ────────────────────────────────────────────────────────

    def _ensure_parent(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read_all(self) -> list[Escalation]:
        """Parse the file and return all escalations, or [] if missing/empty."""
        if not self._path.exists():
            return []
        content = self._path.read_text(encoding="utf-8")
        # Split on the `---` separator used between blocks; strip the header.
        # The file starts with "# Escalations\n\n" before the first block.
        raw_blocks = content.split("\n---\n")
        result: list[Escalation] = []
        for raw in raw_blocks:
            esc = _parse_block(raw)
            if esc is not None:
                result.append(esc)
        return result

    def _write_all(self, escalations: list[Escalation]) -> None:
        self._ensure_parent()
        self._path.write_text(_serialize_all(escalations), encoding="utf-8")

    # ── Public API ──────────────────────────────────────────────────────────

    def add(self, escalation: Escalation) -> None:
        """Append an escalation to the file."""
        existing = self._read_all()
        existing.append(escalation)
        self._write_all(existing)

    def get_pending(self) -> list[Escalation]:
        """Return all unresolved escalations."""
        return [e for e in self._read_all() if not e.resolved]

    def get_all(self) -> list[Escalation]:
        """Return all escalations (resolved and unresolved)."""
        return self._read_all()

    def resolve(self, agent_name: str, answer: str) -> bool:
        """Resolve the oldest pending escalation from the given agent.

        Returns True if an escalation was found and resolved, False otherwise.
        """
        escalations = self._read_all()
        for esc in escalations:
            if esc.agent_name == agent_name and not esc.resolved:
                esc.resolved = True
                esc.answer = answer
                self._write_all(escalations)
                return True
        return False

    def resolve_all(self, answers: dict[str, str]) -> int:
        """Resolve multiple escalations by agent name.

        For each agent name key, resolves the oldest pending escalation for
        that agent with the corresponding answer value.

        Returns the count of successfully resolved escalations.
        """
        count = 0
        for agent_name, answer in answers.items():
            if self.resolve(agent_name, answer):
                count += 1
        return count

    def has_pending(self) -> bool:
        """Return True if there are any unresolved escalations."""
        return any(not e.resolved for e in self._read_all())

    def clear_resolved(self) -> None:
        """Remove all resolved escalations from the file."""
        remaining = [e for e in self._read_all() if not e.resolved]
        self._write_all(remaining)
