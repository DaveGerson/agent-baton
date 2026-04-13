"""Runtime knowledge acquisition protocol.

Handles parsing KNOWLEDGE_GAP signals from agent outcomes and determining
the appropriate escalation action based on the risk/intervention matrix.

Signal format agents output in their outcome text::

    KNOWLEDGE_GAP: Need context on SOX audit trail requirements
    CONFIDENCE: none
    TYPE: contextual

See docs/superpowers/specs/2026-03-24-knowledge-delivery-design.md —
"Runtime Knowledge Acquisition Protocol" section for the full spec.
"""
from __future__ import annotations

import logging
import re

from agent_baton.models.knowledge import KnowledgeGapSignal

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Signal parsing
# ---------------------------------------------------------------------------

# Matches the first KNOWLEDGE_GAP line in agent output.
_GAP_PATTERN = re.compile(
    r"KNOWLEDGE_GAP:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
# Matches CONFIDENCE: none | low | partial
_CONFIDENCE_PATTERN = re.compile(
    r"CONFIDENCE:\s*(none|low|partial)",
    re.IGNORECASE,
)
# Matches TYPE: factual | contextual
_TYPE_PATTERN = re.compile(
    r"TYPE:\s*(factual|contextual)",
    re.IGNORECASE,
)

_VALID_CONFIDENCES = frozenset({"none", "low", "partial"})
_VALID_GAP_TYPES = frozenset({"factual", "contextual"})


def parse_knowledge_gap(
    outcome: str,
    *,
    step_id: str = "",
    agent_name: str = "",
) -> KnowledgeGapSignal | None:
    """Parse a KNOWLEDGE_GAP signal from agent outcome text.

    Looks for the structured KNOWLEDGE_GAP / CONFIDENCE / TYPE block that
    agents output when they self-interrupt.  Returns ``None`` if no
    ``KNOWLEDGE_GAP:`` line is present.

    The signal is considered partial if CONFIDENCE or TYPE are missing or
    invalid — defaults are applied so the caller always gets a usable signal.

    Args:
        outcome: Free-text agent outcome (may contain the signal anywhere).
        step_id: Step ID of the interrupted step (used to populate the signal).
        agent_name: Agent name of the interrupted step.

    Returns:
        ``KnowledgeGapSignal`` if a ``KNOWLEDGE_GAP:`` line is found,
        ``None`` otherwise.
    """
    gap_match = _GAP_PATTERN.search(outcome)
    if gap_match is None:
        return None

    description = gap_match.group(1).strip()

    confidence_match = _CONFIDENCE_PATTERN.search(outcome)
    if confidence_match:
        confidence = confidence_match.group(1).lower()
    else:
        # Default: low confidence when agent didn't specify
        confidence = "low"

    type_match = _TYPE_PATTERN.search(outcome)
    if type_match:
        gap_type = type_match.group(1).lower()
    else:
        # Default: treat unknown type as factual (more resolvable automatically)
        gap_type = "factual"

    # Guard against invalid values (malformed agent output)
    if confidence not in _VALID_CONFIDENCES:
        confidence = "low"
    if gap_type not in _VALID_GAP_TYPES:
        gap_type = "factual"

    return KnowledgeGapSignal(
        description=description,
        confidence=confidence,
        gap_type=gap_type,
        step_id=step_id,
        agent_name=agent_name,
        partial_outcome=outcome,
    )


# ---------------------------------------------------------------------------
# Escalation matrix
# ---------------------------------------------------------------------------

# Risk level normalisation — callers may pass "LOW", "MEDIUM", etc.
_HIGH_RISK_LEVELS = frozenset({"medium", "high", "critical"})
_LOW_RISK_LEVEL = "low"

# Intervention level normalisation
_LOW_INTERVENTION = "low"
_ELEVATED_INTERVENTION = frozenset({"medium", "high"})


def determine_escalation(
    signal: KnowledgeGapSignal,
    risk_level: str,
    intervention_level: str,
    resolution_found: bool,
    bead_store=None,  # BeadStore | None
) -> str:
    """Apply the escalation matrix and return the recommended action.

    Matrix (from spec):

    =========  ===========  ========================  ==============
    Gap type   Resolution   Risk × Intervention       Action
    =========  ===========  ========================  ==============
    factual    match found  any                       auto-resolve
    factual    bead match   any                       auto-resolve  (F8)
    factual    no match     LOW + low intervention    best-effort
    factual    no match     LOW + medium/high         queue-for-gate
    factual    no match     MEDIUM+ any               queue-for-gate
    contextual —            any                       queue-for-gate
    =========  ===========  ========================  ==============

    F8 — Knowledge Gap Auto-Resolution from Beads:
    Before applying the matrix, search the bead store for high-confidence
    ``discovery`` beads whose content keywords overlap with the gap
    description (>= 2 matching keywords).  When found, treat as
    ``resolution_found=True`` (auto-resolve without human escalation).

    Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

    Args:
        signal: The parsed KnowledgeGapSignal.
        risk_level: Plan risk level string (case-insensitive).
            E.g. "LOW", "MEDIUM", "HIGH", "CRITICAL".
        intervention_level: Plan intervention setting (case-insensitive).
            E.g. "low", "medium", "high".
        resolution_found: Whether the resolver found matching knowledge.
        bead_store: Optional
            :class:`~agent_baton.core.engine.bead_store.BeadStore`.
            When provided, discovery beads are searched for gap auto-resolution
            before the escalation matrix is applied.

    Returns:
        One of ``"auto-resolve"``, ``"best-effort"``, or
        ``"queue-for-gate"``.
    """
    normalised_risk = risk_level.lower()
    normalised_intervention = intervention_level.lower()

    # Contextual gaps always go to a human gate — agents can't self-resolve
    # contextual knowledge (business context, org decisions, etc.).
    if signal.gap_type == "contextual":
        return "queue-for-gate"

    # Factual gap with a registry/RAG match — auto-resolve regardless of risk.
    if resolution_found:
        return "auto-resolve"

    # F8 — Bead auto-resolution: check discovery beads for a matching answer
    # before escalating.  Only for factual gaps (contextual already returned).
    if bead_store is not None and not resolution_found:
        try:
            bead_resolved = _resolve_from_beads(signal.description, bead_store)
            if bead_resolved:
                logger.debug(
                    "Knowledge gap auto-resolved from bead store: %r",
                    signal.description,
                )
                return "auto-resolve"
        except Exception as _bead_exc:
            logger.debug(
                "Bead gap resolution failed (non-fatal): %s", _bead_exc
            )

    # Factual gap, no match — escalate based on risk × intervention.
    if normalised_risk in _HIGH_RISK_LEVELS:
        return "queue-for-gate"

    # LOW risk path — shift by intervention level.
    if normalised_intervention in _ELEVATED_INTERVENTION:
        return "queue-for-gate"

    # LOW risk + low intervention + no match — best-effort (log and continue).
    return "best-effort"


def _resolve_from_beads(description: str, bead_store) -> bool:
    """Return True if a high-confidence discovery bead covers *description*.

    Computes keyword overlap between *description* and the content of each
    ``discovery`` bead with ``confidence = "high"``.  A match requires at
    least 2 overlapping content keywords (stop-words excluded).

    Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

    Args:
        description: The knowledge gap description text.
        bead_store: A live :class:`~agent_baton.core.engine.bead_store.BeadStore`.

    Returns:
        ``True`` when a matching high-confidence discovery bead is found.
    """
    _STOP_WORDS = frozenset({
        "the", "a", "an", "is", "it", "in", "on", "at", "to", "for",
        "of", "and", "or", "with", "by", "from", "that", "this", "are",
        "be", "has", "have", "was", "were", "not", "no", "as", "its",
    })

    def _keywords(text: str) -> frozenset:
        words = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]{2,}\b", text.lower())
        return frozenset(w for w in words if w not in _STOP_WORDS)

    gap_keywords = _keywords(description)
    if len(gap_keywords) < 2:
        return False

    candidates = bead_store.query(bead_type="discovery", limit=200)
    for bead in candidates:
        if getattr(bead, "confidence", "medium") != "high":
            continue
        bead_keywords = _keywords(bead.content)
        overlap = gap_keywords & bead_keywords
        if len(overlap) >= 2:
            return True

    return False
