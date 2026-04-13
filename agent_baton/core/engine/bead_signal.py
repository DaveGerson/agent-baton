"""Agent signal protocol for Bead memory extraction.

Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

Parses structured ``BEAD_DISCOVERY``, ``BEAD_DECISION``, and
``BEAD_WARNING`` signals from agent outcome text, converting them into
:class:`~agent_baton.models.bead.Bead` instances that are persisted by
:class:`~agent_baton.core.engine.bead_store.BeadStore`.

Signal format that agents output in their outcome text::

    BEAD_DISCOVERY: The auth module uses JWT with RS256, not HS256.

    BEAD_DECISION: Use SQLAlchemy 2.0 mapped_column style.
    CHOSE: mapped_column over Column
    BECAUSE: Matches project convention and enables better type inference.

    BEAD_WARNING: Test DB fixture uses hardcoded port 5433 — may conflict.

Following the same pattern as ``parse_knowledge_gap()`` in
``core/engine/knowledge_gap.py``:
- Malformed signals are silently dropped (never fatal).
- ``finditer()`` is used so a single outcome can contain multiple signals.
- All regex matches are case-insensitive.

See ``docs/superpowers/specs/2026-04-12-bead-memory-design.md`` —
"Signal Protocol" section for the full contract.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.models.bead import Bead

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signal patterns
# ---------------------------------------------------------------------------

# Matches a BEAD_DISCOVERY line.
_BEAD_DISCOVERY_PATTERN = re.compile(
    r"BEAD_DISCOVERY:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)

# Matches the opening line of a BEAD_DECISION block.
_BEAD_DECISION_PATTERN = re.compile(
    r"BEAD_DECISION:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)

# Sub-patterns for BEAD_DECISION CHOSE/BECAUSE fields.
# These are searched in the text *following* the BEAD_DECISION line.
_BEAD_CHOSE_PATTERN = re.compile(
    r"CHOSE:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_BEAD_BECAUSE_PATTERN = re.compile(
    r"BECAUSE:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)

# Matches a BEAD_WARNING line.
_BEAD_WARNING_PATTERN = re.compile(
    r"BEAD_WARNING:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_bead_signals(
    outcome: str,
    *,
    step_id: str = "",
    agent_name: str = "",
    task_id: str = "",
    bead_count: int = 0,
) -> "list[Bead]":  # noqa: F821
    """Parse all bead signals from agent outcome text.

    Scans *outcome* for ``BEAD_DISCOVERY``, ``BEAD_DECISION``, and
    ``BEAD_WARNING`` lines using ``finditer()`` so that multiple signals in
    a single outcome are all captured.  Returns an empty list when no signals
    are present or when the input is empty.

    Malformed signals (missing content after the colon, empty description,
    etc.) are silently skipped.  This function never raises.

    Args:
        outcome: Free-text agent outcome (may contain signals anywhere).
        step_id: Step ID of the completed step.
        agent_name: Agent that produced the outcome.
        task_id: Execution task identifier.
        bead_count: Current total number of beads in the project, used by
            :func:`~agent_baton.models.bead._generate_bead_id` for progressive
            ID length scaling.

    Returns:
        List of :class:`~agent_baton.models.bead.Bead` instances, one per
        recognised signal.  Order mirrors the signal appearance order in
        *outcome*.
    """
    if not outcome:
        return []

    try:
        return _extract_signals(outcome, step_id=step_id, agent_name=agent_name,
                                task_id=task_id, bead_count=bead_count)
    except Exception as exc:
        _log.warning("parse_bead_signals: unexpected error — %s", exc)
        return []


def _extract_signals(
    outcome: str,
    *,
    step_id: str,
    agent_name: str,
    task_id: str,
    bead_count: int,
) -> "list[Bead]":  # noqa: F821
    """Internal extraction logic — may raise; callers wrap in try/except."""
    from agent_baton.models.bead import Bead, _generate_bead_id

    beads: list[Bead] = []
    timestamp = _utcnow()

    # -- BEAD_DISCOVERY signals -------------------------------------------
    for match in _BEAD_DISCOVERY_PATTERN.finditer(outcome):
        description = match.group(1).strip()
        if not description:
            continue
        bead_id = _generate_bead_id(
            task_id, step_id, description, timestamp, bead_count + len(beads)
        )
        beads.append(
            Bead(
                bead_id=bead_id,
                task_id=task_id,
                step_id=step_id,
                agent_name=agent_name,
                bead_type="discovery",
                content=description,
                confidence="medium",
                scope="step",
                status="open",
                created_at=timestamp,
                source="agent-signal",
            )
        )

    # -- BEAD_DECISION signals --------------------------------------------
    for match in _BEAD_DECISION_PATTERN.finditer(outcome):
        decision_text = match.group(1).strip()
        if not decision_text:
            continue

        # Look for optional CHOSE / BECAUSE in the text that immediately
        # follows this BEAD_DECISION line (within the next 300 chars).
        search_window = outcome[match.start(): match.start() + 300]

        chose_match = _BEAD_CHOSE_PATTERN.search(search_window)
        because_match = _BEAD_BECAUSE_PATTERN.search(search_window)

        chose = chose_match.group(1).strip() if chose_match else ""
        because = because_match.group(1).strip() if because_match else ""

        # Build enriched content combining all three fields.
        parts = [decision_text]
        if chose:
            parts.append(f"CHOSE: {chose}")
        if because:
            parts.append(f"BECAUSE: {because}")
        content = " | ".join(parts)

        bead_id = _generate_bead_id(
            task_id, step_id, content, timestamp, bead_count + len(beads)
        )
        beads.append(
            Bead(
                bead_id=bead_id,
                task_id=task_id,
                step_id=step_id,
                agent_name=agent_name,
                bead_type="decision",
                content=content,
                confidence="high",
                scope="step",
                status="open",
                created_at=timestamp,
                source="agent-signal",
            )
        )

    # -- BEAD_WARNING signals ---------------------------------------------
    for match in _BEAD_WARNING_PATTERN.finditer(outcome):
        warning_text = match.group(1).strip()
        if not warning_text:
            continue
        bead_id = _generate_bead_id(
            task_id, step_id, warning_text, timestamp, bead_count + len(beads)
        )
        beads.append(
            Bead(
                bead_id=bead_id,
                task_id=task_id,
                step_id=step_id,
                agent_name=agent_name,
                bead_type="warning",
                content=warning_text,
                confidence="medium",
                scope="step",
                status="open",
                created_at=timestamp,
                source="agent-signal",
            )
        )

    return beads
