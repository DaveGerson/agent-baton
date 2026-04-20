"""Flag escalation protocol — structured obstacle markers emitted by agents.

Handles parsing DESIGN_CHOICE: and CONFLICT: flag blocks from agent outcome
text.  Flags are distinct from KNOWLEDGE_GAP: signals: a gap means "I am
missing information", while a flag means "I have information but need a
judgment call" (DESIGN_CHOICE) or "two outputs are incompatible" (CONFLICT).

Flag format agents output in their outcome text::

    DESIGN_CHOICE: JWT refresh tokens vs session cookies for auth persistence
    OPTION_A: JWT with refresh — stateless, better for API consumers
    OPTION_B: Session cookies — simpler, existing helper works
    CONFIDENCE: partial
    RECOMMENDATION: Option A based on API-first signals in codebase

    CONFLICT: API contract mismatch between backend and frontend
    PARTIES: backend-engineer--python (step 2.1), frontend-engineer--react (step 2.2)
    DESCRIPTION: Response field naming — backend uses snake_case, frontend expects camelCase
    CONFIDENCE: partial
    RECOMMENDATION: Backend adapts — add serialization layer

Specialist resolution markers::

    FLAG_RESOLVED: <decision text>   — Tier 1 resolved
    ESCALATE_TO_INTERACT:            — request Tier 2 agent-to-agent dialogue
    KNOWLEDGE_GAP:                   — can't resolve; escalate to Tier 3 (human)

See docs/superpowers/specs/2026-04-15-flag-escalation-system-design.md for
the full three-tier escalation specification.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

_DESIGN_CHOICE_PATTERN = re.compile(
    r"DESIGN_CHOICE:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
# Matches OPTION_A:, OPTION_B:, ... OPTION_Z: — collected in order.
_OPTION_PATTERN = re.compile(
    r"OPTION_([A-Z]):\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_CONFLICT_PATTERN = re.compile(
    r"CONFLICT:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_PARTIES_PATTERN = re.compile(
    r"PARTIES:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_DESCRIPTION_PATTERN = re.compile(
    r"DESCRIPTION:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_CONFIDENCE_PATTERN = re.compile(
    r"CONFIDENCE:\s*(none|low|partial)",
    re.IGNORECASE,
)
_RECOMMENDATION_PATTERN = re.compile(
    r"RECOMMENDATION:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_FLAG_RESOLVED_PATTERN = re.compile(
    r"FLAG_RESOLVED:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_ESCALATE_TO_INTERACT_PATTERN = re.compile(
    r"ESCALATE_TO_INTERACT:",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_CONFIDENCES = frozenset({"none", "low", "partial"})

# Routing table: flag_type -> specialist agent name.
# "domain-gap" and "security" are extensibility placeholders — no parsers emit
# these flag_type values yet, but the routing entries are defined now so the
# table pattern is visible for future work.
_FLAG_ROUTING: dict[str, str] = {
    "design-choice": "architect",
    "conflict": "architect",
    "domain-gap": "subject-matter-expert",
    "security": "security-reviewer",
}
_FLAG_ROUTING_DEFAULT = "architect"

# Truncation length for partial_outcome context in consultation descriptions.
_CONTEXT_EXCERPT_CHARS = 1500


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DesignFlag:
    """A parsed DESIGN_CHOICE: flag emitted by an agent.

    Represents a situation where the agent found multiple valid approaches
    and needs a judgment call on which to take.

    Attributes:
        flag_type: Always ``"design-choice"``.
        description: The choice being made (from the ``DESIGN_CHOICE:`` line).
        options: Ordered list of alternatives found (from ``OPTION_A:``,
            ``OPTION_B:``, etc.).  Empty when no option lines are present.
        confidence: Agent's self-reported confidence — ``"none"``,
            ``"low"``, or ``"partial"``.  Defaults to ``"low"``.
        recommendation: Agent's preferred option with reasoning.  Empty
            string when not provided.
        step_id: Step ID of the interrupted step.
        agent_name: Agent name of the interrupted step.
        partial_outcome: Full raw outcome text from the interrupted step,
            used to provide context in the consultation description.
    """

    flag_type: str = "design-choice"
    description: str = ""
    options: list[str] = field(default_factory=list)
    confidence: str = "low"
    recommendation: str = ""
    step_id: str = ""
    agent_name: str = ""
    partial_outcome: str = ""

    def to_consultation_description(self) -> str:
        """Format as a structured consultation brief for the specialist step.

        Produces markdown suitable for use as a consulting step's
        ``task_description``.

        Returns:
            Multi-section markdown string with choice, options, recommendation,
            and truncated context excerpt.
        """
        lines: list[str] = [
            "## Design Choice Requiring Resolution",
            "",
            f"**Step:** {self.step_id} ({self.agent_name})",
            f"**Choice:** {self.description}",
            "",
            "### Options",
        ]

        if self.options:
            for idx, option_text in enumerate(self.options):
                letter = chr(ord("A") + idx)
                lines.append(f"- **Option {letter}:** {option_text}")
        else:
            lines.append("- *(no options provided)*")

        lines += [
            "",
            "### Agent's Recommendation",
        ]
        if self.recommendation:
            lines.append(
                f"{self.recommendation} (confidence: {self.confidence})"
            )
        else:
            lines.append(f"*(none provided — confidence: {self.confidence})*")

        lines += [
            "",
            "### Relevant Context from Agent Output",
        ]
        excerpt = self.partial_outcome[-_CONTEXT_EXCERPT_CHARS:] if self.partial_outcome else ""
        if excerpt:
            # Blockquote each line of the excerpt.
            for excerpt_line in excerpt.splitlines():
                lines.append(f"> {excerpt_line}")
        else:
            lines.append("> *(no output available)*")

        return "\n".join(lines)


@dataclass
class ConflictFlag:
    """A parsed CONFLICT: flag emitted by an agent.

    Represents a situation where the agent detected an incompatibility with
    another agent's work — most commonly emitted by synthesis steps.

    Attributes:
        flag_type: Always ``"conflict"``.
        description: What's in conflict (from the ``CONFLICT:`` line).
        parties: List of agents/steps involved (parsed from the ``PARTIES:``
            line by splitting on commas).  Empty when not provided.
        conflict_detail: Specifics of the incompatibility (from the
            ``DESCRIPTION:`` line; named ``conflict_detail`` to avoid
            shadowing the top-level ``description``).
        confidence: Agent's self-reported confidence — ``"none"``,
            ``"low"``, or ``"partial"``.  Defaults to ``"low"``.
        recommendation: Agent's preferred resolution.  Empty when not
            provided.
        step_id: Step ID of the interrupted step.
        agent_name: Agent name of the interrupted step.
        partial_outcome: Full raw outcome text from the interrupted step,
            used to provide context in the consultation description.
    """

    flag_type: str = "conflict"
    description: str = ""
    parties: list[str] = field(default_factory=list)
    conflict_detail: str = ""    # from DESCRIPTION: line in output
    confidence: str = "low"
    recommendation: str = ""
    step_id: str = ""
    agent_name: str = ""
    partial_outcome: str = ""

    def to_consultation_description(self) -> str:
        """Format as a structured consultation brief for the specialist step.

        Produces markdown suitable for use as a consulting step's
        ``task_description``.

        Returns:
            Multi-section markdown string with conflict summary, parties,
            conflict detail, recommendation, and truncated context excerpt.
        """
        lines: list[str] = [
            "## Conflict Requiring Arbitration",
            "",
            f"**Step:** {self.step_id} ({self.agent_name})",
            f"**Conflict:** {self.description}",
            "",
            "### Parties",
        ]

        if self.parties:
            for party in self.parties:
                lines.append(f"- {party.strip()}")
        else:
            lines.append("- *(no parties identified)*")

        lines += [
            "",
            "### Conflict Detail",
        ]
        if self.conflict_detail:
            lines.append(self.conflict_detail)
        else:
            lines.append("*(no detail provided)*")

        lines += [
            "",
            "### Agent's Recommendation",
        ]
        if self.recommendation:
            lines.append(
                f"{self.recommendation} (confidence: {self.confidence})"
            )
        else:
            lines.append(f"*(none provided — confidence: {self.confidence})*")

        lines += [
            "",
            "### Relevant Context from Agent Output",
        ]
        excerpt = self.partial_outcome[-_CONTEXT_EXCERPT_CHARS:] if self.partial_outcome else ""
        if excerpt:
            for excerpt_line in excerpt.splitlines():
                lines.append(f"> {excerpt_line}")
        else:
            lines.append("> *(no output available)*")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Parser functions
# ---------------------------------------------------------------------------

def parse_design_flag(
    outcome: str,
    *,
    step_id: str = "",
    agent_name: str = "",
) -> DesignFlag | None:
    """Parse a DESIGN_CHOICE: block from agent outcome text.

    Looks for the structured ``DESIGN_CHOICE:`` / ``OPTION_[A-Z]:`` /
    ``CONFIDENCE:`` / ``RECOMMENDATION:`` block that agents output when they
    find multiple valid approaches.  Returns ``None`` if no
    ``DESIGN_CHOICE:`` line is present.

    The flag is considered partial if optional fields are missing — defaults
    are applied so the caller always gets a usable flag object.

    Args:
        outcome: Free-text agent outcome (the flag may appear anywhere).
        step_id: Step ID of the interrupted step (stored on the flag).
        agent_name: Agent name of the interrupted step (stored on the flag).

    Returns:
        :class:`DesignFlag` if a ``DESIGN_CHOICE:`` line is found,
        ``None`` otherwise.
    """
    choice_match = _DESIGN_CHOICE_PATTERN.search(outcome)
    if choice_match is None:
        return None

    description = choice_match.group(1).strip()

    # Collect OPTION_A:, OPTION_B:, ... in the order they appear.
    option_matches = _OPTION_PATTERN.findall(outcome)
    # findall returns [(letter, text), ...] — sort by letter to preserve order.
    option_matches_sorted = sorted(option_matches, key=lambda m: m[0].upper())
    options = [text.strip() for _letter, text in option_matches_sorted]

    confidence_match = _CONFIDENCE_PATTERN.search(outcome)
    if confidence_match:
        confidence = confidence_match.group(1).lower()
    else:
        confidence = "low"

    if confidence not in _VALID_CONFIDENCES:
        confidence = "low"

    recommendation_match = _RECOMMENDATION_PATTERN.search(outcome)
    recommendation = recommendation_match.group(1).strip() if recommendation_match else ""

    return DesignFlag(
        description=description,
        options=options,
        confidence=confidence,
        recommendation=recommendation,
        step_id=step_id,
        agent_name=agent_name,
        partial_outcome=outcome,
    )


def parse_conflict_flag(
    outcome: str,
    *,
    step_id: str = "",
    agent_name: str = "",
) -> ConflictFlag | None:
    """Parse a CONFLICT: block from agent outcome text.

    Looks for the structured ``CONFLICT:`` / ``PARTIES:`` /
    ``DESCRIPTION:`` / ``CONFIDENCE:`` / ``RECOMMENDATION:`` block.
    Returns ``None`` if no ``CONFLICT:`` line is present.

    The flag is considered partial if optional fields are missing — defaults
    are applied so the caller always gets a usable flag object.

    Args:
        outcome: Free-text agent outcome (the flag may appear anywhere).
        step_id: Step ID of the interrupted step (stored on the flag).
        agent_name: Agent name of the interrupted step (stored on the flag).

    Returns:
        :class:`ConflictFlag` if a ``CONFLICT:`` line is found,
        ``None`` otherwise.
    """
    conflict_match = _CONFLICT_PATTERN.search(outcome)
    if conflict_match is None:
        return None

    description = conflict_match.group(1).strip()

    parties_match = _PARTIES_PATTERN.search(outcome)
    if parties_match:
        raw_parties = parties_match.group(1).strip()
        parties = [p.strip() for p in raw_parties.split(",") if p.strip()]
    else:
        parties = []

    description_match = _DESCRIPTION_PATTERN.search(outcome)
    conflict_detail = description_match.group(1).strip() if description_match else ""

    confidence_match = _CONFIDENCE_PATTERN.search(outcome)
    if confidence_match:
        confidence = confidence_match.group(1).lower()
    else:
        confidence = "low"

    if confidence not in _VALID_CONFIDENCES:
        confidence = "low"

    recommendation_match = _RECOMMENDATION_PATTERN.search(outcome)
    recommendation = recommendation_match.group(1).strip() if recommendation_match else ""

    return ConflictFlag(
        description=description,
        parties=parties,
        conflict_detail=conflict_detail,
        confidence=confidence,
        recommendation=recommendation,
        step_id=step_id,
        agent_name=agent_name,
        partial_outcome=outcome,
    )


def parse_flag_resolution(outcome: str) -> str | None:
    """Extract the resolution decision from a FLAG_RESOLVED: line.

    Args:
        outcome: Specialist agent's output text.

    Returns:
        The decision text after ``FLAG_RESOLVED:`` if present, ``None``
        otherwise.
    """
    match = _FLAG_RESOLVED_PATTERN.search(outcome)
    if match is None:
        return None
    return match.group(1).strip()


def has_escalate_to_interact(outcome: str) -> bool:
    """Return True if the outcome contains an ESCALATE_TO_INTERACT: marker.

    Args:
        outcome: Specialist agent's output text.

    Returns:
        ``True`` when the ``ESCALATE_TO_INTERACT:`` marker is present.
    """
    return _ESCALATE_TO_INTERACT_PATTERN.search(outcome) is not None
