"""Role-card Markdown renderer.

Renders a :class:`~agent_baton.models.manager.RoleCard` (built by
:class:`agent_baton.core.manager.team_blueprint.TeamBlueprintBuilder`) as
Markdown following the spec §14.2 role-card template exactly: the six
``## `` section headers, in order (Mission, Owns, Does Not Own, Required
Knowledge Packs, Context Budget, Escalation Triggers, Handoff
Requirements). Per-role content is necessarily data-driven from the card's
fields rather than the spec's illustrative literal example text -- see
design decision #4 in ``team_blueprint``'s module docstring.
"""
from __future__ import annotations

from agent_baton.models.manager import RoleCard


def render_role_card(card: RoleCard) -> str:
    """Render *card* as Markdown per spec §14.2.

    List fields render as ``- item`` bullets; an empty list renders a
    single ``- (none)`` placeholder so no section is ever left blank.
    ``default_context_budget`` renders as ``"<N,NNN> tokens"`` (thousands
    separator), matching the spec example literally (``"12,000 tokens"``).
    """
    lines: list[str] = [f"# Role Card: {card.role}", ""]

    lines.append("## Mission")
    lines.append(card.mission or "(unspecified)")
    lines.append("")

    lines.append("## Owns")
    lines.extend(_bullets(card.owns))
    lines.append("")

    lines.append("## Does Not Own")
    lines.extend(_bullets(card.does_not_own))
    lines.append("")

    lines.append("## Required Knowledge Packs")
    lines.extend(_bullets(card.required_knowledge_packs))
    lines.append("")

    lines.append("## Context Budget")
    lines.append(f"{card.default_context_budget:,} tokens")
    lines.append("")

    lines.append("## Escalation Triggers")
    lines.extend(_bullets(card.escalation_triggers))
    lines.append("")

    lines.append("## Handoff Requirements")
    lines.extend(_bullets(card.expected_handoffs))
    lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def _bullets(items: list[str]) -> list[str]:
    if not items:
        return ["- (none)"]
    return [f"- {item}" for item in items]
