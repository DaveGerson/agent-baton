"""Phase construction, enrichment, and team consolidation.

Extracted from ``_legacy_planner.IntelligentPlanner``.  Every function
is stateless; the ``AgentRegistry`` is passed explicitly where needed.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from agent_baton.core.orchestration.router import is_reviewer_agent
from agent_baton.core.engine.planning.rules.concerns import CROSS_CONCERN_SIGNALS
from agent_baton.core.engine.planning.rules.phase_roles import (
    IMPLEMENT_PHASE_NAMES,
    PHASE_BLOCKED_ROLES,
    PHASE_IDEAL_ROLES,
)
from agent_baton.core.engine.planning.rules.step_types import (
    AGENT_STEP_TYPE,
    TEST_ENGINEER_DEVELOPING_KEYWORDS,
)
from agent_baton.core.engine.planning.rules.phase_templates import (
    DEFAULT_PHASE_NAMES,
    PHASE_NAMES,
    PHASE_VERBS,
    SUBTASK_PHASE_NAMES,
)
from agent_baton.core.engine.planning.rules.templates import (
    AGENT_DELIVERABLES,
    STEP_TEMPLATES,
)
from agent_baton.core.engine.planning.utils.roster_logic import (
    agent_expertise_level,
    agent_has_output_spec,
    pick_agent_for_concern,
)
from agent_baton.models.execution import PlanPhase, PlanStep, TeamMember

if TYPE_CHECKING:
    from agent_baton.core.orchestration.registry import AgentRegistry
    from agent_baton.models.pattern import LearnedPattern

logger = logging.getLogger(__name__)

_IMPLEMENT_FALLBACK_AGENT = "backend-engineer"
_REVIEW_FALLBACK_AGENT = "code-reviewer"

_PHASE_FALLBACK_AGENT: dict[str, str] = {
    "implement": _IMPLEMENT_FALLBACK_AGENT,
    "fix": _IMPLEMENT_FALLBACK_AGENT,
    "review": _REVIEW_FALLBACK_AGENT,
}


def _step_type_for_agent(
    agent_name: str,
    task_description: str = "",
    phase_name: str | None = None,
) -> str:
    """Return the appropriate step_type for a given agent role."""
    base = agent_name.split("--")[0]
    step_type = AGENT_STEP_TYPE.get(base, "developing")
    if base == "test-engineer" and step_type == "testing":
        lower_desc = task_description.lower()
        if any(kw in lower_desc for kw in TEST_ENGINEER_DEVELOPING_KEYWORDS):
            step_type = "developing"
    if phase_name and phase_name.lower() in IMPLEMENT_PHASE_NAMES:
        if base not in {"code-reviewer", "security-reviewer", "auditor"}:
            step_type = "developing"
    return step_type


def _derive_expected_outcome(step: "PlanStep", task_summary: str = "") -> str:
    """Derive a 1-sentence behavioral demo statement for a step."""
    desc = (step.task_description or "").strip()
    if not desc:
        return ""

    base_agent = (step.agent_name or "").split("--")[0]
    step_type = (step.step_type or "").lower()

    cleaned = desc
    for sep in (": ", " — ", " - "):
        if sep in cleaned and cleaned.index(sep) < 60:
            cleaned = cleaned.split(sep, 1)[1].strip()
            break

    for marker in (" Build on the ", " Apply sound ", " Document your approach"):
        if marker in cleaned:
            cleaned = cleaned.split(marker, 1)[0].strip()

    snippet = cleaned[:140].rstrip(" .,;:")
    if not snippet:
        return ""

    if step_type in {"testing", "test"} or base_agent == "test-engineer":
        outcome = (
            f"After this step, the behavior in '{snippet}' is covered by an "
            f"automated test that fails before the fix and passes after."
        )
    elif step_type == "reviewing" or base_agent in {"code-reviewer", "security-reviewer", "auditor"}:
        outcome = (
            f"After this step, '{snippet}' has a documented review verdict "
            f"with any blocking issues called out."
        )
    elif step_type == "planning" or base_agent in {"architect", "subject-matter-expert"}:
        outcome = (
            f"After this step, '{snippet}' has a concrete approach the "
            f"implementation team can build from without further clarification."
        )
    else:
        outcome = (
            f"After this step, '{snippet}' is implemented and observably "
            f"working in the running system."
        )

    if len(outcome) > 240:
        outcome = outcome[:237] + "..."
    return outcome


# ---------------------------------------------------------------------------
# Concern scoping — extract agent-relevant clause from task summary
# ---------------------------------------------------------------------------

_CLAUSE_SPLIT = re.compile(r"[,;]\s+(?:and\s+)?|(?:\s+and\s+)")
_NUMBERED_SPLIT = re.compile(r"(?:^|\s)\d+[.)]\s+")


def _scope_summary_for_agent(task_summary: str, agent_name: str) -> str:
    """Extract the clause from *task_summary* most relevant to *agent_name*.

    Splits the summary on comma/semicolon/and boundaries OR numbered
    subtask markers, scores each clause against the agent's
    CROSS_CONCERN_SIGNALS keywords, and returns the best-matching
    clause.  Falls back to the full summary when no clause scores
    above zero or the summary is short.

    bd-f712: fixes the bug where every step got the identical full
    task description pasted in.
    """
    if len(task_summary) < 80:
        return task_summary

    clauses = [c.strip() for c in _CLAUSE_SPLIT.split(task_summary) if c.strip()]
    if len(clauses) < 2:
        parts = _NUMBERED_SPLIT.split(task_summary)
        clauses = [c.strip() for c in parts if c.strip()]
    if len(clauses) < 2:
        return task_summary

    base = agent_name.split("--")[0]
    keywords = CROSS_CONCERN_SIGNALS.get(base, [])
    if not keywords:
        return task_summary

    best_clause = ""
    best_score = 0
    summary_lower_words = set(re.findall(r"\b\w+\b", task_summary.lower()))

    for clause in clauses:
        clause_lower = clause.lower()
        clause_words = set(re.findall(r"\b\w+\b", clause_lower))
        score = 0
        for kw in keywords:
            if " " in kw:
                if kw in clause_lower:
                    score += 2
            elif kw in clause_words:
                score += 1
        if score > best_score:
            best_score = score
            best_clause = clause

    if best_score > 0 and best_clause:
        return best_clause

    return task_summary


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def step_description(
    phase_name: str,
    agent_name: str,
    task_summary: str,
    registry: "AgentRegistry",
) -> str:
    """Generate a role-specific step description for an agent within a phase."""
    if not task_summary:
        return f"{phase_name} phase — {agent_name}"

    scoped = _scope_summary_for_agent(task_summary, agent_name)
    base_agent = agent_name.split("--")[0]
    phase_lower = phase_name.lower()
    expertise = agent_expertise_level(agent_name, registry)

    if expertise == "expert":
        verb = PHASE_VERBS.get(phase_lower, phase_name)
        return f"{verb}: {scoped}."

    agent_templates = STEP_TEMPLATES.get(base_agent, {})
    template = agent_templates.get(phase_lower)
    if template:
        description = template.format(task=scoped)
        if expertise == "minimal":
            verb = PHASE_VERBS.get(phase_lower, phase_name.lower())
            description += (
                f" Apply sound {verb.lower().split(':')[0].strip()} practices"
                f" and document your approach."
            )
        return description

    verb = PHASE_VERBS.get(phase_lower, phase_name)
    base_desc = f"{verb}: {scoped} (as {agent_name})"
    if expertise == "minimal":
        base_desc += " Document your approach and decisions."
    return base_desc


def _is_blocked_for_phase(agent_name: str, phase_name: str) -> bool:
    """Return True if *agent_name* must not be assigned to *phase_name*."""
    base = agent_name.split("--")[0]
    blocked = PHASE_BLOCKED_ROLES.get(phase_name.lower(), set())
    return base in blocked


def assign_agents_to_phases(
    phases: list[PlanPhase],
    agents: list[str],
    task_summary: str,
    registry: "AgentRegistry",
) -> list[PlanPhase]:
    """Distribute agents across phases using affinity-based assignment."""
    if not agents:
        for phase in phases:
            if not phase.steps:
                _desc = step_description(phase.name, "backend-engineer", task_summary, registry)
                phase.steps.append(
                    PlanStep(
                        step_id=f"{phase.phase_id}.1",
                        agent_name="backend-engineer",
                        task_description=_desc,
                        step_type=_step_type_for_agent(
                            "backend-engineer", _desc, phase_name=phase.name
                        ),
                    )
                )
        return phases

    assigned: list[tuple[PlanPhase, str]] = []
    remaining_agents = list(agents)
    remaining_phases = list(phases)

    # Pass 1: assign agents to their ideal phases
    for phase in list(remaining_phases):
        ideal_roles = PHASE_IDEAL_ROLES.get(phase.name.lower(), [])
        matched = False
        for role in ideal_roles:
            for agent in remaining_agents:
                if agent.split("--")[0] == role:
                    assigned.append((phase, agent))
                    remaining_agents.remove(agent)
                    remaining_phases.remove(phase)
                    matched = True
                    break
            if matched:
                break

    # Pass 2: round-robin remaining agents to remaining phases
    for phase in list(remaining_phases):
        chosen: str | None = None
        skipped: list[str] = []
        while remaining_agents:
            candidate = remaining_agents.pop(0)
            if _is_blocked_for_phase(candidate, phase.name):
                skipped.append(candidate)
                continue
            chosen = candidate
            break
        remaining_agents = skipped + remaining_agents
        if chosen is not None:
            assigned.append((phase, chosen))
            remaining_phases.remove(phase)

    # Pass 3: unassigned phases — reuse best-fit from pool
    for phase in remaining_phases:
        ideal_roles = PHASE_IDEAL_ROLES.get(phase.name.lower(), [])
        best = None
        for role in ideal_roles:
            for agent in agents:
                if agent.split("--")[0] == role and not _is_blocked_for_phase(agent, phase.name):
                    best = agent
                    break
            if best:
                break
        if best is None:
            for agent in agents:
                if not _is_blocked_for_phase(agent, phase.name):
                    best = agent
                    break
        if best is None:
            best = _PHASE_FALLBACK_AGENT.get(
                phase.name.lower(), _IMPLEMENT_FALLBACK_AGENT
            )
        assigned.append((phase, best))

    # Pass 4: leftover agents into work phases only
    _WORK_PHASES = {"implement", "fix", "draft"}
    for agent in remaining_agents:
        base = agent.split("--")[0]
        best_phase = None
        for phase_name_key, roles in PHASE_IDEAL_ROLES.items():
            if phase_name_key not in _WORK_PHASES:
                continue
            if base in roles and not _is_blocked_for_phase(agent, phase_name_key):
                best_phase = next(
                    (p for p in phases if p.name.lower() == phase_name_key), None
                )
                if best_phase:
                    break
        if best_phase is None:
            for p in phases:
                if p.name.lower() in _WORK_PHASES and not _is_blocked_for_phase(agent, p.name):
                    best_phase = p
                    break
        if best_phase is None:
            continue
        assigned.append((best_phase, agent))

    # Build PlanStep objects
    for phase, agent in sorted(assigned, key=lambda x: x[0].phase_id):
        step_number = len(phase.steps) + 1
        step_id = f"{phase.phase_id}.{step_number}"
        _desc = step_description(phase.name, agent, task_summary, registry)
        phase.steps.append(
            PlanStep(
                step_id=step_id,
                agent_name=agent,
                task_description=_desc,
                step_type=_step_type_for_agent(agent, _desc, phase_name=phase.name),
            )
        )

    # Guarantee every phase has at least one step
    for phase in phases:
        if not phase.steps:
            _desc = step_description(phase.name, agents[0], task_summary, registry)
            phase.steps.append(
                PlanStep(
                    step_id=f"{phase.phase_id}.1",
                    agent_name=agents[0],
                    task_description=_desc,
                    step_type=_step_type_for_agent(
                        agents[0], _desc, phase_name=phase.name
                    ),
                )
            )

    return phases


def build_phases_for_names(
    phase_names: list[str],
    agents: list[str],
    task_summary: str,
    registry: "AgentRegistry",
    start_phase_id: int = 1,
) -> list[PlanPhase]:
    """Build PlanPhase objects for a list of names, distributing agents."""
    phases: list[PlanPhase] = [
        PlanPhase(phase_id=idx, name=name, steps=[])
        for idx, name in enumerate(phase_names, start=start_phase_id)
    ]
    return assign_agents_to_phases(phases, agents, task_summary, registry)


def phases_from_dicts(
    phase_dicts: list[dict],
    agents: list[str],
    task_summary: str,
    registry: "AgentRegistry",
) -> list[PlanPhase]:
    """Build PlanPhase objects from user-supplied dicts."""
    from agent_baton.models.execution import PlanGate

    phases: list[PlanPhase] = []
    for idx, d in enumerate(phase_dicts, start=1):
        name = d.get("name", f"Phase {idx}")
        phase_agents = d.get("agents", [])
        gate_dict = d.get("gate")
        gate: PlanGate | None = None
        if gate_dict:
            gate = PlanGate(
                gate_type=gate_dict.get("gate_type") or gate_dict.get("type", "build"),
                command=gate_dict.get("command", ""),
                description=gate_dict.get("description", ""),
                fail_on=gate_dict.get("fail_on", []),
            )
        steps: list[PlanStep] = []
        for step_idx, agent in enumerate(phase_agents, start=1):
            _desc = step_description(name, agent, task_summary, registry)
            steps.append(
                PlanStep(
                    step_id=f"{idx}.{step_idx}",
                    agent_name=agent,
                    task_description=_desc,
                    step_type=_step_type_for_agent(agent, _desc, phase_name=name),
                )
            )
        phases.append(PlanPhase(phase_id=idx, name=name, steps=steps, gate=gate))

    all_steps_empty = all(not p.steps for p in phases)
    if all_steps_empty and agents:
        return assign_agents_to_phases(phases, agents, task_summary, registry)

    return phases


def build_compound_phases(
    subtask_data: list[dict],
    agent_route_map: dict[str, str],
    registry: "AgentRegistry",
) -> list[PlanPhase]:
    """Build phases from compound sub-task data with routed agents."""
    phases: list[PlanPhase] = []
    for idx, st in enumerate(subtask_data, start=1):
        phase_name = SUBTASK_PHASE_NAMES.get(st["task_type"], "Implement")

        steps: list[PlanStep] = []
        for step_idx, agent_base in enumerate(st["agents"], start=1):
            routed_name: str = agent_route_map.get(agent_base) or agent_base
            _desc = step_description(phase_name, routed_name, st["text"], registry)
            steps.append(
                PlanStep(
                    step_id=f"{idx}.{step_idx}",
                    agent_name=routed_name,
                    task_description=_desc,
                    step_type=_step_type_for_agent(
                        routed_name, _desc, phase_name=phase_name
                    ),
                )
            )

        phases.append(PlanPhase(phase_id=idx, name=phase_name, steps=steps))

    return phases


def apply_pattern(
    pattern: "LearnedPattern",
    task_type: str,
    task_summary: str = "",
) -> list[PlanPhase]:
    """Convert a LearnedPattern into PlanPhases (empty steps).

    Uses the pattern's ``recommended_template`` to derive phase names
    when it contains a parseable sequence (e.g. ``"Design → Implement → Review"``
    or ``"Design, Implement, Review"``).  Falls back to the task type's
    default template when the pattern's template is unparseable.
    """
    parsed_names: list[str] | None = None
    template = (pattern.recommended_template or "").strip()
    if template:
        for sep in (" → ", " -> ", ", ", "; "):
            parts = [p.strip() for p in template.split(sep) if p.strip()]
            if len(parts) >= 2:
                parsed_names = parts
                break

    phase_names = parsed_names or PHASE_NAMES.get(task_type, DEFAULT_PHASE_NAMES)
    phases: list[PlanPhase] = []
    for idx, name in enumerate(phase_names, start=1):
        phases.append(PlanPhase(phase_id=idx, name=name, steps=[]))
    return phases


def default_phases(
    task_type: str,
    agents: list[str],
    task_summary: str,
    registry: "AgentRegistry",
) -> list[PlanPhase]:
    """Build the default PlanPhase list for a task type."""
    phase_names = PHASE_NAMES.get(task_type, DEFAULT_PHASE_NAMES)
    return build_phases_for_names(phase_names, agents, task_summary, registry)


def enrich_phases(
    phases: list[PlanPhase],
    task_summary: str,
    registry: "AgentRegistry",
) -> list[PlanPhase]:
    """Post-process phases to add cross-phase context and default deliverables."""
    for phase in phases:
        for stp in phase.steps:
            if phase.phase_id > 1:
                prev = next(
                    (p for p in phases if p.phase_id == phase.phase_id - 1),
                    None,
                )
                if prev and prev.steps:
                    prev_agents = ", ".join(
                        s.agent_name for s in prev.steps
                    )
                    stp.task_description += (
                        f" Build on the {prev.name.lower()} output"
                        f" from phase {prev.phase_id} ({prev_agents})."
                    )

            if not stp.deliverables:
                base_agent = stp.agent_name.split("--")[0]
                defaults = AGENT_DELIVERABLES.get(base_agent)
                if defaults and not agent_has_output_spec(stp.agent_name, registry):
                    stp.deliverables = list(defaults)

            if not stp.expected_outcome:
                stp.expected_outcome = _derive_expected_outcome(
                    stp, task_summary
                )

    return phases


def score_knowledge_for_concern(
    attachment: object,
    concern_text: str,
) -> int:
    """Return a domain-match score for *attachment* against *concern_text*."""
    text_lower = concern_text.lower()
    text_words = set(re.findall(r"\b\w+\b", text_lower))

    att_signal = " ".join(filter(None, [
        getattr(attachment, 'pack_name', '') or "",
        getattr(attachment, 'document_name', '') or "",
        getattr(attachment, 'path', '') or "",
    ])).lower()

    score = 0
    for keywords in CROSS_CONCERN_SIGNALS.values():
        for kw in keywords:
            if " " in kw:
                if kw in att_signal and kw in text_lower:
                    score += 1
            else:
                if kw in att_signal and kw in text_words:
                    score += 1
    return score


def partition_knowledge(
    all_knowledge: list,
    concerns: list[tuple[str, str]],
) -> list[list]:
    """Partition *all_knowledge* across concern slots."""
    n = len(concerns)
    partitions: list[list] = [[] for _ in range(n)]

    for attachment in all_knowledge:
        scores = [
            score_knowledge_for_concern(attachment, text)
            for _, text in concerns
        ]
        positive = [i for i, s in enumerate(scores) if s > 0]

        if len(positive) == 1:
            partitions[positive[0]].append(attachment)
        else:
            for p in partitions:
                p.append(attachment)

    return partitions


def split_implement_phase_by_concerns(
    phase: PlanPhase,
    concerns: list[tuple[str, str]],
    candidate_agents: list[str],
    task_summary: str,
    knowledge_split_strategy: str = "smart",
) -> None:
    """Replace ``phase.steps`` with one parallel step per concern.  Mutates in place."""
    all_knowledge: list = []
    seen_paths: set[str] = set()
    for s in phase.steps:
        for k in s.knowledge:
            key = k.path if k.path else id(k)
            if key not in seen_paths:
                all_knowledge.append(k)
                seen_paths.add(key)

    if knowledge_split_strategy == "smart":
        per_concern_knowledge = partition_knowledge(all_knowledge, concerns)
    else:
        per_concern_knowledge = [list(all_knowledge) for _ in concerns]

    new_steps: list[PlanStep] = []
    for idx, ((marker, text), concern_knowledge) in enumerate(
        zip(concerns, per_concern_knowledge), start=1
    ):
        agent = pick_agent_for_concern(text, candidate_agents)
        verb = PHASE_VERBS.get(phase.name.lower(), phase.name)
        desc = f"{verb} ({marker}): {text}"
        new_steps.append(
            PlanStep(
                step_id=f"{phase.phase_id}.{idx}",
                agent_name=agent,
                task_description=desc,
                step_type=_step_type_for_agent(agent, desc, phase_name=phase.name),
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


def is_team_phase(phase: PlanPhase, task_summary: str) -> bool:
    """Detect if a phase should use team dispatch."""
    if phase.name.lower() in ("implement", "fix") and len(phase.steps) >= 2:
        return True
    if len(phase.steps) >= 2:
        lower_summary = task_summary.lower()
        team_signals = [
            "pair", "joint", "together", "adversarial", "paired", "team",
            "collaborate", "combined", "dual",
        ]
        if any(signal in lower_summary for signal in team_signals):
            return True
    return False


def consolidate_team_step(phase: PlanPhase) -> PlanStep:
    """Merge multiple steps in a phase into a single team step."""
    is_implement_phase = phase.name.lower() in ("implement", "fix", "draft", "migrate")
    if is_implement_phase:
        kept_steps: list[PlanStep] = []
        dropped: list[str] = []
        for stp in phase.steps:
            if is_reviewer_agent(stp.agent_name):
                dropped.append(stp.agent_name)
                continue
            kept_steps.append(stp)
        if dropped:
            logger.warning(
                "Filtered reviewer agent(s) %s from %s phase team-step "
                "(reviewers belong in review/gate phases, not as implementers)",
                dropped,
                phase.name,
            )
        if kept_steps:
            source_steps = kept_steps
        else:
            logger.warning(
                "All members of %s phase were reviewer agents; "
                "keeping original list to preserve executability",
                phase.name,
            )
            source_steps = phase.steps
    else:
        source_steps = phase.steps

    members: list[TeamMember] = []
    all_deliverables: list[str] = []
    all_knowledge: list = []
    seen_knowledge_paths: set[str] = set()
    for i, stp in enumerate(source_steps):
        role = "lead" if i == 0 else "implementer"
        member_id = f"{phase.phase_id}.1.{chr(97 + i)}"
        members.append(TeamMember(
            member_id=member_id,
            agent_name=stp.agent_name,
            role=role,
            task_description=stp.task_description,
            model=stp.model,
            deliverables=stp.deliverables,
        ))
        all_deliverables.extend(stp.deliverables)
        for k in stp.knowledge:
            key = k.path if k.path else id(k)
            if key not in seen_knowledge_paths:
                all_knowledge.append(k)
                seen_knowledge_paths.add(key)

    combined_desc = "; ".join(s.task_description for s in source_steps)
    return PlanStep(
        step_id=f"{phase.phase_id}.1",
        agent_name="team",
        task_description=f"Team implementation: {combined_desc}",
        team=members,
        deliverables=all_deliverables,
        knowledge=all_knowledge,
    )
