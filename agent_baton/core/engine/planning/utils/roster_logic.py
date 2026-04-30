"""Agent roster helpers — routing, scoring, concern expansion.

Extracted from ``_legacy_planner.IntelligentPlanner``.  Every function
is stateless; services are passed explicitly.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.orchestration.router import is_reviewer_agent
from agent_baton.core.engine.planning.rules.concerns import CROSS_CONCERN_SIGNALS

if TYPE_CHECKING:
    from agent_baton.core.orchestration.registry import AgentRegistry
    from agent_baton.core.orchestration.router import AgentRouter
    from agent_baton.core.improve.scoring import PerformanceScorer
    from agent_baton.models.feedback import RetrospectiveFeedback

logger = logging.getLogger(__name__)

_LOW_HEALTH_RATINGS = {"needs-improvement"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def expand_agents_for_concerns(agents: list[str], text: str) -> list[str]:
    """Expand agent roster based on cross-concern signals in the description."""
    text_lower = text.lower()
    text_words = set(re.findall(r"\b\w+\b", text_lower))
    expanded = list(agents)

    for agent_base, keywords in CROSS_CONCERN_SIGNALS.items():
        if any(a.split("--")[0] == agent_base for a in expanded):
            continue
        for kw in keywords:
            if " " in kw:
                matched = kw in text_lower
            else:
                matched = kw in text_words
            if matched:
                expanded.append(agent_base)
                break

    return expanded


def pick_agent_for_concern(
    concern_text: str,
    candidate_agents: list[str],
) -> str:
    """Choose the best agent from *candidate_agents* for a concern."""
    text_lower = concern_text.lower()
    text_words = set(re.findall(r"\b\w+\b", text_lower))

    _ARCHITECT_BASES = {"architect", "ai-systems-architect"}
    eligible = [
        a for a in candidate_agents
        if not is_reviewer_agent(a)
        and a.split("--")[0] not in _ARCHITECT_BASES
    ]
    if not eligible:
        eligible = [
            a for a in candidate_agents
            if a.split("--")[0] not in _ARCHITECT_BASES
        ]
    if not eligible:
        eligible = list(candidate_agents) or ["backend-engineer"]

    best_agent = eligible[0]
    best_score = -1
    for agent in eligible:
        base = agent.split("--")[0]
        keywords = CROSS_CONCERN_SIGNALS.get(base, [])
        score = 0
        for kw in keywords:
            if " " in kw:
                if kw in text_lower:
                    score += 1
            elif kw in text_words:
                score += 1
        if score > best_score:
            best_score = score
            best_agent = agent
    return best_agent


def apply_retro_feedback(
    agents: list[str],
    feedback: "RetrospectiveFeedback",
    routing_notes: list[str],
) -> list[str]:
    """Apply retrospective recommendations to the candidate agent list.

    Mutates *routing_notes* in place.  Returns the filtered agent list.
    """
    to_drop = set(feedback.agents_to_drop())

    try:
        from agent_baton.core.learn.overrides import LearnedOverrides
        _learned_drops = LearnedOverrides().get_agent_drops()
        to_drop.update(_learned_drops)
    except Exception:
        pass

    to_prefer = feedback.agents_to_prefer()

    if to_drop:
        filtered = [
            a for a in agents
            if a.split("--")[0] not in to_drop and a not in to_drop
        ]
        if filtered:
            for dropped in to_drop:
                if any(
                    a.split("--")[0] == dropped or a == dropped
                    for a in agents
                ):
                    routing_notes.append(
                        f"{dropped} removed (retrospective recommendation)"
                    )
            agents = filtered

    if to_prefer:
        for preferred in sorted(to_prefer):
            routing_notes.append(
                f"Retrospective recommends: {preferred} "
                f"(not auto-added — add manually if desired)"
            )

    return agents


def route_agents(
    agents: list[str],
    project_root: Path | None,
    router: "AgentRouter",
    routing_notes: list[str],
) -> list[str]:
    """Route base agent names to flavored variants where possible.

    Mutates *routing_notes* in place.
    """
    if not agents:
        return agents

    stack = None
    if project_root is not None:
        try:
            stack = router.detect_stack(project_root)
        except Exception:
            pass

    routed: list[str] = []
    for base in agents:
        try:
            resolved = router.route(base, stack=stack)
        except Exception:
            resolved = base
        if resolved != base:
            routing_notes.append(
                f"{base} -> {resolved} (stack-matched flavor)"
            )
        routed.append(resolved)
    return routed


def check_agent_scores(
    agents: list[str],
    scorer: "PerformanceScorer",
    bead_store: object | None = None,
) -> list[str]:
    """Return score warnings for any low-health agents."""
    warnings: list[str] = []
    for agent in agents:
        try:
            card = scorer.score_agent(agent, bead_store=bead_store)
        except Exception:
            continue
        if card.health in _LOW_HEALTH_RATINGS:
            warnings.append(
                f"Agent '{agent}' has health '{card.health}' "
                f"(first-pass rate {card.first_pass_rate:.0%}, "
                f"{card.negative_mentions} negative mention(s))."
            )
    return warnings


def agent_expertise_level(agent_name: str, registry: "AgentRegistry") -> str:
    """Assess agent expertise from definition richness."""
    agent_def = registry.get(agent_name)
    if agent_def is None:
        return "minimal"
    word_count = len(agent_def.instructions.split())
    return "expert" if word_count > 200 else "standard"


def agent_has_output_spec(agent_name: str, registry: "AgentRegistry") -> bool:
    """Return True if the agent definition already specifies its output format."""
    agent_def = registry.get(agent_name)
    if agent_def is None:
        return False
    instructions_lower = agent_def.instructions.lower()
    output_markers = ("output format", "when you finish", "return:", "deliverables")
    return any(marker in instructions_lower for marker in output_markers)
