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
) -> str:
    """Apply the escalation matrix and return the recommended action.

    Matrix (from spec):

    =========  ===========  ========================  ==============
    Gap type   Resolution   Risk × Intervention       Action
    =========  ===========  ========================  ==============
    factual    match found  any                       auto-resolve
    factual    no match     LOW + low intervention    best-effort
    factual    no match     LOW + medium/high         queue-for-gate
    factual    no match     MEDIUM+ any               queue-for-gate
    contextual —            any                       queue-for-gate
    =========  ===========  ========================  ==============

    Args:
        signal: The parsed KnowledgeGapSignal.
        risk_level: Plan risk level string (case-insensitive).
            E.g. "LOW", "MEDIUM", "HIGH", "CRITICAL".
        intervention_level: Plan intervention setting (case-insensitive).
            E.g. "low", "medium", "high".
        resolution_found: Whether the resolver found matching knowledge.

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

    # Factual gap, no match — escalate based on risk × intervention.
    if normalised_risk in _HIGH_RISK_LEVELS:
        return "queue-for-gate"

    # LOW risk path — shift by intervention level.
    if normalised_intervention in _ELEVATED_INTERVENTION:
        return "queue-for-gate"

    # LOW risk + low intervention + no match — best-effort (log and continue).
    return "best-effort"
