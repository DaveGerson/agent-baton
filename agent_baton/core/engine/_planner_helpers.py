"""Shared pure helpers for the IntelligentPlanner pipeline.

This module owns the stateless helper functions and constants that are consumed
by BOTH the planner's inline logic and the extracted analyzer/strategy modules.
It does NOT own any stateful planner behaviour, I/O, or LLM calls.

At Step 1.2 (this step), planner.py retains its own copies of these functions.
Step 1.4 will switch planner.py to import from here and delete the duplicates.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.models.knowledge import KnowledgeAttachment

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cross-concern agent signals — shared by CapabilityAnalyzer and DepthAnalyzer
# ---------------------------------------------------------------------------

_CROSS_CONCERN_SIGNALS: dict[str, list[str]] = {
    "frontend-engineer": [
        "ux", "ui", "navigate", "browser", "visual", "layout",
        "css", "component", "react", "frontend",
    ],
    "backend-engineer": [
        "api", "endpoint", "server", "database", "migration", "backend",
        "fix", "bug", "broken", "error", "remediate", "patch",
    ],
    "test-engineer": [
        "test suite", "e2e", "playwright", "coverage", "vitest",
        "jest", "unit test", "integration test",
    ],
    "code-reviewer": [
        "review", "code quality", "audit",
    ],
}

# Maps phase names (lower-cased) to human-readable action verbs for step descriptions.
_PHASE_VERBS: dict[str, str] = {
    "research": "Explore and document",
    "investigate": "Explore and document",
    "design": "Design the approach for",
    "implement": "Implement",
    "fix": "Fix",
    "draft": "Draft",
    "test": "Write tests to verify",
    "review": "Review the implementation of",
}

# Regex to detect concern markers at word boundaries.
# Recognized markers (must appear at start-of-string or after whitespace):
#   - ``F0.1`` / ``F1.2`` / ``f3.4`` — feature-id markers
#   - ``(1)`` / ``(2)`` — parenthesized integers
#   - ``1.`` / ``2.`` / ``1)`` — bare-integer-with-punctuation
_CONCERN_MARKER = re.compile(
    r"(?:^|(?<=\s))"                        # boundary: start or whitespace
    r"("                                    # group 1: the marker itself
    r"[A-Za-z]\d+\.\d+"                     # F0.1, f1.2, A2.3
    r"|\(\d+\)"                              # (1), (2)
    r"|\d+[.\)](?!\d)"                      # 1., 2), but not 1.5 (decimals)
    r")"
    r"\s+"                                  # required whitespace after marker
)

# Minimum distinct concerns needed to trigger the per-concern split.
_MIN_CONCERNS_FOR_SPLIT = 3

# Constraint-clause keywords that bound the deliverable list during
# concern-splitting.  When the planner sees one of these phrases, it stops
# consuming further markers as deliverables.  See bd-021d.
_CONCERN_CONSTRAINT_KEYWORDS = (
    "must not",
    "do not",
    "shall not",
    "should not",
    "regress",
    "non-goal",
    "non-goals",
)


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

def _parse_concerns(summary: str) -> list[tuple[str, str]]:
    """Parse distinct concerns from a multi-concern task description.

    Recognized markers (see :data:`_CONCERN_MARKER`):
      - Feature-id style: ``F0.1 Spec entity ... F0.2 Tenancy ...``
      - Parenthesized:    ``(1) ... (2) ...``
      - Bare-numbered:    ``1. ... 2. ...`` or ``1) ... 2) ...``

    Returns a list of ``(marker, text)`` pairs where ``marker`` is the
    concern label (e.g. ``"F0.1"``) and ``text`` is everything after the
    marker up to (but not including) the next marker.

    Empty list when fewer than :data:`_MIN_CONCERNS_FOR_SPLIT` concerns
    are detected — the caller treats this as "single concern, do not split".
    """
    # bd-021d: bound the deliverable list at the first constraint clause.
    lower = summary.lower()
    bound = len(summary)
    for kw in _CONCERN_CONSTRAINT_KEYWORDS:
        idx = lower.find(kw)
        if idx != -1 and idx < bound:
            bound = idx
    bounded_summary = summary[:bound]

    matches = list(_CONCERN_MARKER.finditer(bounded_summary))
    if len(matches) < _MIN_CONCERNS_FOR_SPLIT:
        return []

    concerns: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        marker = m.group(1).strip("().")
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(bounded_summary)
        text = bounded_summary[start:end].strip().rstrip(";,")
        if text:
            concerns.append((marker, text))

    return concerns if len(concerns) >= _MIN_CONCERNS_FOR_SPLIT else []


def _score_knowledge_for_concern(
    attachment: "KnowledgeAttachment",
    concern_text: str,
) -> int:
    """Return a domain-match score for *attachment* against *concern_text*.

    Scoring uses the same keyword lists as :data:`_CROSS_CONCERN_SIGNALS`:
    each keyword found in *concern_text* that also appears in the
    attachment's ``pack_name`` or ``document_name`` contributes +1.
    """
    text_lower = concern_text.lower()
    text_words = set(re.findall(r"\b\w+\b", text_lower))

    att_signal = " ".join(filter(None, [
        attachment.pack_name or "",
        attachment.document_name or "",
        attachment.path or "",
    ])).lower()

    score = 0
    for keywords in _CROSS_CONCERN_SIGNALS.values():
        for kw in keywords:
            if " " in kw:
                if kw in att_signal and kw in text_lower:
                    score += 1
            else:
                if kw in att_signal and kw in text_words:
                    score += 1
    return score


def _partition_knowledge(
    all_knowledge: list,
    concerns: list[tuple[str, str]],
) -> list[list]:
    """Partition *all_knowledge* across concern slots.

    For each attachment, compute a domain-match score against every concern
    text.  If only one concern scores > 0, assign the attachment exclusively
    to that concern.  Otherwise broadcast it to every concern (safer to
    over-share than to drop).

    Returns a list of per-concern knowledge lists, in the same order as
    *concerns*.
    """
    n = len(concerns)
    partitions: list[list] = [[] for _ in range(n)]

    for attachment in all_knowledge:
        scores = [
            _score_knowledge_for_concern(attachment, text)
            for _, text in concerns
        ]
        positive = [i for i, s in enumerate(scores) if s > 0]

        if len(positive) == 1:
            # Unambiguous domain match — assign only to that concern.
            partitions[positive[0]].append(attachment)
        else:
            # Ambiguous or cross-cutting — broadcast to all.
            for p in partitions:
                p.append(attachment)

    return partitions


def _expand_agents_for_concerns(
    agents: list[str],
    text: str,
) -> list[str]:
    """Expand agent roster based on cross-concern signals in the description.

    When the description mentions keywords associated with agents not in
    the current roster, those agents are added.
    """
    text_lower = text.lower()
    text_words = set(re.findall(r"\b\w+\b", text_lower))
    expanded = list(agents)

    for agent_base, keywords in _CROSS_CONCERN_SIGNALS.items():
        # Skip if this agent (or a flavored variant) is already present
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


def _split_implement_phase_by_concerns(
    phase: "PlanPhase",  # noqa: F821 — resolved at runtime
    concerns: list[tuple[str, str]],
    candidate_agents: list[str],
    task_summary: str,
    pick_agent_fn: "Callable[[str, list[str]], str]",  # noqa: F821
    step_type_fn: "Callable[[str, str, str], str]",    # noqa: F821
    knowledge_split_strategy: str = "smart",
) -> None:
    """Replace ``phase.steps`` with one parallel step per concern.

    This is the pure logic extracted from ``IntelligentPlanner._split_implement_phase_by_concerns``.
    The caller must supply ``pick_agent_fn`` and ``step_type_fn`` callables because
    those helpers depend on planner instance state (registry/router).

    Args:
        phase: The implement-type phase to split (mutated in place).
        concerns: List of ``(marker, text)`` pairs from :func:`_parse_concerns`.
        candidate_agents: Pool of agents to choose from per concern.
        task_summary: Original task summary (used for verb selection in fallback).
        pick_agent_fn: ``fn(concern_text, candidate_agents) -> agent_name``.
        step_type_fn: ``fn(agent_name, task_description, phase_name) -> step_type``.
        knowledge_split_strategy: ``"smart"`` (default) or ``"broadcast"``.
    """
    from agent_baton.models.execution import PlanStep

    all_knowledge: list = []
    seen_paths: set[str] = set()
    for s in phase.steps:
        for k in s.knowledge:
            key = k.path if k.path else id(k)
            if key not in seen_paths:
                all_knowledge.append(k)
                seen_paths.add(key)

    if knowledge_split_strategy == "smart":
        per_concern_knowledge = _partition_knowledge(all_knowledge, concerns)
    else:
        per_concern_knowledge = [list(all_knowledge) for _ in concerns]

    new_steps: list[PlanStep] = []
    for idx, ((marker, text), concern_knowledge) in enumerate(
        zip(concerns, per_concern_knowledge), start=1
    ):
        agent = pick_agent_fn(text, candidate_agents)
        verb = _PHASE_VERBS.get(phase.name.lower(), phase.name)
        desc = f"{verb} ({marker}): {text}"
        new_steps.append(
            PlanStep(
                step_id=f"{phase.phase_id}.{idx}",
                agent_name=agent,
                task_description=desc,
                step_type=step_type_fn(agent, desc, phase.name),
                knowledge=concern_knowledge,
            )
        )

    logger.info(
        "Split %s phase into %d parallel concern-steps "
        "(markers=%s, agents=%s, strategy=%s)",
        phase.name,
        len(new_steps),
        [c[0] for c in concerns],
        [s.agent_name for s in new_steps],
        knowledge_split_strategy,
    )
    phase.steps = new_steps


def _build_phases_for_names(
    phase_names: list[str],
    start_phase_id: int = 1,
) -> list["PlanPhase"]:  # noqa: F821
    """Build bare PlanPhase objects (no steps) for a list of names.

    This is the pure structural part of ``IntelligentPlanner._build_phases_for_names``.
    Agent assignment is a separate concern (handled by the planner's
    ``_assign_agents_to_phases`` method).

    Args:
        phase_names: Ordered list of phase name strings.
        start_phase_id: First phase_id to assign. Callers appending to an
            existing plan should pass ``max_existing_id + 1``.

    Returns:
        List of ``PlanPhase`` objects with empty steps lists.
    """
    from agent_baton.models.execution import PlanPhase

    return [
        PlanPhase(phase_id=idx, name=name, steps=[])
        for idx, name in enumerate(phase_names, start=start_phase_id)
    ]
