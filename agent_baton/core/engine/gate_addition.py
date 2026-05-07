"""Declarative gate extension via GATE_ADDITION signals in agent outcomes.

Agents that finish a step and know they have introduced new quality checks
(e.g. added a security scan, a pre-commit hook, or a custom test command)
can request that those commands be included in the phase gate by emitting
one or more ``GATE_ADDITION:`` lines in their outcome text::

    GATE_ADDITION: npm audit --audit-level=high
    GATE_ADDITION: pre-commit run --all-files

The engine parses these lines when recording the step result, stores the
commands on the ``StepResult``, and appends them to the gate command at
gate-build time (chained with ``&&``, after any artifact-derived commands).

Design mirrors :mod:`agent_baton.core.engine.knowledge_gap` (the canonical
signal-parsing pattern in this codebase).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from agent_baton.core.engine._command_safety import (
    MAX_GATE_COMMAND_LENGTH,
    is_destructive,
    is_safe_gate_command,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum agent-declared gate additions to accept per step.  Mirrors
# ArtifactValidator._MAX_COMMANDS_PER_FILE so the two sources of extension
# commands are bounded identically.
_MAX_ADDITIONS_PER_STEP: int = 8

# ---------------------------------------------------------------------------
# Signal pattern
# ---------------------------------------------------------------------------

_GATE_ADDITION_RE = re.compile(
    # [^\S\n]* — horizontal whitespace only (never crosses a line boundary),
    # [^\n]+?  — one or more non-newline chars (command body; trailing spaces
    #            are stripped in the parser so pure-whitespace lines are
    #            discarded by the empty-command guard there).
    r"GATE_ADDITION:[^\S\n]*([^\n]+?)(?:\n|$)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GateAddition:
    """A single shell command declared by an agent for inclusion in the gate.

    Attributes:
        command: The shell command the agent wants appended to the gate.
        agent_name: Name of the agent that emitted the signal.
        step_id: Step ID of the step that produced the signal.
    """

    command: str
    agent_name: str
    step_id: str


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_gate_additions(
    outcome: str,
    *,
    step_id: str,
    agent_name: str,
) -> list[GateAddition]:
    """Parse all ``GATE_ADDITION:`` signals from agent outcome text.

    Scans *outcome* for every ``GATE_ADDITION: <command>`` line.  Lines with
    empty commands (after stripping whitespace) are ignored.  Duplicate
    command strings (case-sensitive) are deduplicated, keeping only the first
    occurrence.  The result is capped at :data:`_MAX_ADDITIONS_PER_STEP`
    entries so a single verbose agent cannot overwhelm the gate command.

    The match is case-insensitive on the ``GATE_ADDITION:`` prefix so agents
    do not need to match exact casing.

    Args:
        outcome: Free-text agent outcome (may contain any number of
            ``GATE_ADDITION:`` lines, anywhere in the text).
        step_id: Step ID of the step that produced *outcome*.  Stored on
            each returned :class:`GateAddition`.
        agent_name: Name of the agent that produced *outcome*.  Stored on
            each returned :class:`GateAddition`.

    Returns:
        Ordered, deduplicated list of :class:`GateAddition` objects — at
        most :data:`_MAX_ADDITIONS_PER_STEP` items.  Empty list when no
        ``GATE_ADDITION:`` lines are present or all commands are empty.
    """
    if not outcome:
        return []

    seen: set[str] = set()
    additions: list[GateAddition] = []

    for match in _GATE_ADDITION_RE.finditer(outcome):
        command = match.group(1).strip()
        if not command:
            continue
        # Defence in depth: reject commands that are too long, contain shell
        # metacharacters, or match known destructive patterns.
        if len(command) > MAX_GATE_COMMAND_LENGTH:
            logger.warning(
                "rejected GATE_ADDITION (too long, %d chars): %.80s",
                len(command),
                command,
            )
            continue
        if not is_safe_gate_command(command):
            logger.warning(
                "rejected GATE_ADDITION (shell metacharacter): %.80s",
                command,
            )
            continue
        if is_destructive(command):
            logger.warning(
                "rejected GATE_ADDITION (destructive pattern): %.80s",
                command,
            )
            continue
        if command in seen:
            continue
        seen.add(command)
        additions.append(
            GateAddition(
                command=command,
                agent_name=agent_name,
                step_id=step_id,
            )
        )
        if len(additions) >= _MAX_ADDITIONS_PER_STEP:
            break

    return additions
