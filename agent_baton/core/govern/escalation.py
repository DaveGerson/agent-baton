"""Escalation management -- read, write, and resolve human escalation requests.

When an agent encounters a decision that exceeds its authority or requires
domain expertise, it creates an ``Escalation`` record. The escalation flow
is:

1. **Agent raises an escalation** -- calls ``EscalationManager.add()`` with
   the question, context, and suggested options.
2. **Orchestrator detects pending escalations** -- calls ``has_pending()``
   or ``get_pending()`` during the execution loop.
3. **Human provides an answer** -- the orchestrator presents the question
   to the user and records the decision via ``resolve()``.
4. **Execution resumes** -- the agent reads the answer and proceeds.

Escalations are serialized as markdown blocks in a single file at
``.claude/team-context/escalations.md``. Each block has the format::

    ### <timestamp> -- <agent_name> -- PENDING|RESOLVED
    **Priority:** <priority>
    **Question:** <question>
    **Context:** <context>
    **Options:** <option1>, <option2>, ...
    **Answer:** <answer>

Blocks are separated by horizontal rules (``---``). Resolved escalations
remain in the file for audit purposes until ``clear_resolved()`` is called.

**Status: Experimental** -- built and tested but not yet validated with real
usage data.
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
    """Manage the escalation file at ``.claude/team-context/escalations.md``.

    Provides CRUD operations over the escalation file: adding new
    escalations, querying pending ones, resolving them with answers,
    and purging resolved entries. All operations re-read the file from
    disk to avoid stale state when multiple agents interact concurrently.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path: Path = (path or Path(".claude/team-context/escalations.md")).resolve()

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
        """Append a new escalation to the file.

        Creates the file and parent directories if they do not exist.

        Args:
            escalation: The ``Escalation`` to record. Its ``resolved``
                field should be ``False`` on creation.
        """
        existing = self._read_all()
        existing.append(escalation)
        self._write_all(existing)

    def get_pending(self) -> list[Escalation]:
        """Return all unresolved escalations.

        Returns:
            List of ``Escalation`` objects where ``resolved`` is ``False``,
            in file order (oldest first).
        """
        return [e for e in self._read_all() if not e.resolved]

    def get_all(self) -> list[Escalation]:
        """Return all escalations (resolved and unresolved)."""
        return self._read_all()

    def resolve(self, agent_name: str, answer: str) -> bool:
        """Resolve the oldest pending escalation from the given agent.

        Finds the first unresolved escalation whose ``agent_name`` matches,
        marks it as resolved, records the answer, and writes the updated
        file back to disk.

        Args:
            agent_name: Name of the agent whose escalation to resolve.
            answer: The human's decision or response text.

        Returns:
            ``True`` if a matching pending escalation was found and resolved,
            ``False`` if no pending escalation exists for that agent.
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
        that agent with the corresponding answer value. Each call to
        ``resolve()`` re-reads the file, so concurrent modifications are
        handled safely.

        Args:
            answers: Mapping of ``{agent_name: answer_text}``.

        Returns:
            The count of escalations that were successfully resolved.
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
