"""Default agent rosters and complexity caps."""
from __future__ import annotations

# Default agents by task type when no pattern is found.
DEFAULT_AGENTS: dict[str, list[str]] = {
    "new-feature": ["architect", "backend-engineer", "test-engineer", "code-reviewer"],
    "bug-fix": ["backend-engineer", "test-engineer"],
    "refactor": ["architect", "backend-engineer", "test-engineer", "code-reviewer"],
    "data-analysis": ["architect", "data-analyst"],
    "documentation": ["architect", "talent-builder", "code-reviewer"],
    "migration": ["architect", "backend-engineer", "test-engineer", "code-reviewer", "auditor"],
    "test": ["test-engineer"],
    "audit": ["architect", "code-reviewer"],
    "assessment": ["architect", "data-analyst", "code-reviewer"],
    # Fallback for unknown/generic tasks.
    "generic": ["architect", "backend-engineer", "test-engineer", "code-reviewer"],
}

# Maximum agents by complexity tier — caps roster bloat so a "light"
# task doesn't drag in five specialists.  Mirrors the table in
# ``classifier._MAX_AGENTS_BY_COMPLEXITY``.
MAX_AGENTS_BY_COMPLEXITY: dict[str, int] = {
    "light": 2,
    "medium": 4,
    "heavy": 6,
}

# Confidence floor for accepting a learned ``Pattern`` recommendation.
# Patterns below this confidence are ignored and the planner falls
# back to default agents/phases.
MIN_PATTERN_CONFIDENCE: float = 0.7

# Performance-scorer health labels treated as "low" — surfacing them
# triggers a routing warning on the plan.
LOW_HEALTH_RATINGS: frozenset[str] = frozenset({"needs-improvement"})


# Backward-compat aliases.
_DEFAULT_AGENTS = DEFAULT_AGENTS
_MAX_AGENTS_BY_COMPLEXITY = MAX_AGENTS_BY_COMPLEXITY
_MIN_PATTERN_CONFIDENCE = MIN_PATTERN_CONFIDENCE
_LOW_HEALTH_RATINGS = LOW_HEALTH_RATINGS
