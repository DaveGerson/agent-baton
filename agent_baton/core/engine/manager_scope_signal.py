"""Manager-mode scope-expansion signal parsing (M9).

Agents dispatched under a manager-mode plan carry a scope contract (see
``agent_baton.core.manager.context_bundles``) whose ``Allowed Paths`` /
``Escalate If`` sections bound their work. When an agent needs to go
outside that contract it should not silently proceed -- it emits a
structured signal in its outcome text::

    SCOPE_EXPANSION: app/auth/session.py — session metadata needed

The engine parses these lines when recording the step result
(``ExecutionEngine.record_step_result``) and routes them per
``ManagerConfig.scoping.scope_expansion_policy`` (see
``agent_baton.core.config.manager.ScopingConfig``): ``allow_with_note``,
``queue_for_manager``, or ``block``.

This module is deliberately **distinct** from
``agent_baton.core.engine.scope_expansion`` (an unrelated, pre-existing
adaptive-replanning feature) and the ``SCOPE_EXPANSION: <description>``
free-text signal parsed by
``agent_baton.core.engine.bead_signal.parse_scope_expansions``. Both
signal formats share the ``SCOPE_EXPANSION:`` prefix and both parsers may
match the same outcome line (they are independent, best-effort consumers
of the same text) -- but only this module's stricter
``<path> — <reason>`` format participates in manager-mode scope-expansion
routing. See docs/internal/manager-mode-pmo-plan.md Task 13 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §13.2.

Design mirrors :mod:`agent_baton.core.engine.gate_addition` (the
canonical signal-parsing pattern for a dataclass + module-level regex +
parser function in this codebase).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum agent-declared scope-expansion signals to accept per step.
# Mirrors gate_addition.py's _MAX_ADDITIONS_PER_STEP so the two
# signal-parsing modules bound agent-declared input identically.
_MAX_SIGNALS_PER_STEP: int = 8

# ---------------------------------------------------------------------------
# Signal pattern
# ---------------------------------------------------------------------------

# Format: SCOPE_EXPANSION: <path> — <reason>  (em dash or hyphen separator,
# optional surrounding whitespace). Anchored per-line via re.MULTILINE so
# ``$`` matches end-of-line rather than end-of-string, and re.IGNORECASE so
# agents do not need to match exact prefix casing.
_SCOPE_EXPANSION_SIGNAL_RE = re.compile(
    r"^SCOPE_EXPANSION:\s*(?P<path>\S+)\s*[—-]\s*(?P<reason>.+)$",
    re.IGNORECASE | re.MULTILINE,
)

# ---------------------------------------------------------------------------
# Data shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScopeExpansionSignal:
    """A single ``<path> — <reason>`` scope-expansion request.

    Attributes:
        path: The file/path the agent needs to touch outside its scope
            contract's ``Allowed Paths``.
        reason: Why the agent believes the expansion is necessary.
        step_id: Step ID of the step that produced the signal. Defaults
            to ``""`` for callers that parse text without step context
            (e.g. unit tests); ``record_step_result`` always supplies it.
    """

    path: str
    reason: str
    step_id: str = ""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_scope_expansion_signals(
    text: str,
    *,
    step_id: str = "",
) -> list[ScopeExpansionSignal]:
    """Parse all ``SCOPE_EXPANSION: <path> — <reason>`` signals from *text*.

    Scans *text* for every matching line. Lines with an empty path or
    empty reason (after stripping whitespace) are ignored. Duplicate
    ``(path, reason)`` pairs are deduplicated, keeping only the first
    occurrence. The result is capped at :data:`_MAX_SIGNALS_PER_STEP`
    entries so a single verbose agent cannot overwhelm downstream routing.

    Args:
        text: Free-text agent outcome (may contain any number of
            ``SCOPE_EXPANSION:`` lines, anywhere in the text, mixed with
            the free-text ``SCOPE_EXPANSION: <description>`` format
            consumed by the unrelated adaptive-replanning pipeline -- a
            line missing the ``<path> — <reason>`` shape simply does not
            match this module's stricter pattern).
        step_id: Step ID to stamp onto every returned signal.

    Returns:
        Ordered, deduplicated list of :class:`ScopeExpansionSignal`
        objects -- at most :data:`_MAX_SIGNALS_PER_STEP` items. Empty
        list when no matching lines are present.
    """
    if not text:
        return []

    seen: set[tuple[str, str]] = set()
    signals: list[ScopeExpansionSignal] = []

    try:
        matches = list(_SCOPE_EXPANSION_SIGNAL_RE.finditer(text))
    except Exception as exc:  # noqa: BLE001 - defensive, mirrors gate_addition.py
        logger.debug("parse_scope_expansion_signals: regex scan failed: %s", exc)
        return []

    for match in matches:
        path = match.group("path").strip()
        reason = match.group("reason").strip()
        if not path or not reason:
            continue
        key = (path, reason)
        if key in seen:
            continue
        seen.add(key)
        signals.append(ScopeExpansionSignal(path=path, reason=reason, step_id=step_id))
        if len(signals) >= _MAX_SIGNALS_PER_STEP:
            break

    return signals
