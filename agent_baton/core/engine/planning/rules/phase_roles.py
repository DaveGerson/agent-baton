"""Phase ↔ agent role affinity tables."""
from __future__ import annotations

# Preferred agent roles per phase, in priority order.  The 4-pass
# affinity assignment in ``planning.stages.routing`` walks these to
# match agents to phases where they are the natural fit (e.g. architect
# → Design).  Phases not listed here have no preference.
PHASE_IDEAL_ROLES: dict[str, list[str]] = {
    "design": ["architect", "data-engineer", "data-analyst", "backend-engineer"],
    "research": ["architect", "subject-matter-expert", "data-analyst"],
    "investigate": ["backend-engineer", "frontend-engineer", "data-analyst"],
    "implement": [
        "backend-engineer", "frontend-engineer", "devops-engineer",
        "data-engineer", "data-scientist", "visualization-expert",
    ],
    "fix": ["backend-engineer", "frontend-engineer"],
    "draft": ["architect", "subject-matter-expert"],
    "test": ["test-engineer", "backend-engineer", "frontend-engineer"],
    "review": ["code-reviewer", "security-reviewer", "auditor", "architect"],
}

# Phase → set of agent roles that must NOT land on this phase.
# bd-0e36 (architects on Implement) and bd-1974 (implementers on Review)
# both surfaced from violations of this table; keeping it as data here
# makes the constraints visible and overridable per project.
PHASE_BLOCKED_ROLES: dict[str, set[str]] = {
    "implement": {"architect", "ai-systems-architect"},
    "fix": {"architect", "ai-systems-architect"},
    "draft": set(),
    "review": {
        "backend-engineer", "frontend-engineer", "devops-engineer",
        "data-engineer", "data-scientist", "data-analyst",
        "visualization-expert", "test-engineer",
    },
}

# Hard-coded fallback when no agent on the roster fits a phase.  Rather
# than synthesizing an architect on an Implement phase (the bug bd-0e36
# patched), the planner reaches for these.
IMPLEMENT_FALLBACK_AGENT: str = "backend-engineer"
REVIEW_FALLBACK_AGENT: str = "code-reviewer"
PHASE_FALLBACK_AGENT: dict[str, str] = {
    "implement": IMPLEMENT_FALLBACK_AGENT,
    "fix": IMPLEMENT_FALLBACK_AGENT,
    "review": REVIEW_FALLBACK_AGENT,
}

# Phase names whose step_type must be "developing" regardless of the
# agent's default role.  Read by ``planning.stages.routing`` to fix
# bd-b3e1 (architect on Implement → wrong step_type).
IMPLEMENT_PHASE_NAMES: frozenset[str] = frozenset({
    "implement", "fix", "draft", "build", "develop",
})


# Backward-compat aliases.
_PHASE_IDEAL_ROLES = PHASE_IDEAL_ROLES
_PHASE_BLOCKED_ROLES = PHASE_BLOCKED_ROLES
_IMPLEMENT_FALLBACK_AGENT = IMPLEMENT_FALLBACK_AGENT
_REVIEW_FALLBACK_AGENT = REVIEW_FALLBACK_AGENT
_PHASE_FALLBACK_AGENT = PHASE_FALLBACK_AGENT
_IMPLEMENT_PHASE_NAMES = IMPLEMENT_PHASE_NAMES
