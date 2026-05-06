"""Phase name templates and verbs."""
from __future__ import annotations

# Phase templates by task type — the default sequence of phase names
# the planner produces when no learned pattern overrides them.
PHASE_NAMES: dict[str, list[str]] = {
    "new-feature": ["Design", "Implement", "Test", "Review"],
    "bug-fix": ["Investigate", "Fix", "Test"],
    "refactor": ["Design", "Implement", "Test", "Review"],
    "data-analysis": ["Design", "Implement", "Review"],
    "documentation": ["Research", "Draft", "Review"],
    "migration": ["Design", "Implement", "Test", "Review"],
    "test": ["Implement", "Review"],
    "audit": ["Prepare", "Audit", "Synthesize", "Review"],
    "assessment": ["Research", "Assess", "Synthesize", "Review"],
    "generic": ["Investigate", "Implement", "Test", "Review"],
    "investigation": ["Investigate", "Hypothesize", "Fix", "Verify"],
}

# Used when the task type is unknown and no other source supplies a list.
DEFAULT_PHASE_NAMES: list[str] = ["Design", "Implement", "Test", "Review"]

# Compound-task decomposition: when the planner splits a "do A then do B"
# summary into multiple sub-task phases, this maps each sub-task's
# inferred type to the phase name to use.
SUBTASK_PHASE_NAMES: dict[str, str] = {
    "test": "Test",
    "bug-fix": "Fix",
    "new-feature": "Implement",
    "refactor": "Refactor",
    "migration": "Migrate",
    "data-analysis": "Analyze",
    "documentation": "Document",
    "audit": "Audit",
    "assessment": "Assess",
    "investigation": "Investigate",
}

# Phase name (lower-cased) → human-readable verb used in step descriptions.
PHASE_VERBS: dict[str, str] = {
    "research": "Explore and document",
    "investigate": "Explore and document",
    "design": "Design the approach for",
    "implement": "Implement",
    "fix": "Fix",
    "draft": "Draft",
    "test": "Write tests to verify",
    "review": "Review the implementation of",
    "prepare": "Prepare context and materials for",
    "audit": "Audit and evaluate",
    "assess": "Assess and evaluate",
    "synthesize": "Synthesize findings and produce recommendations for",
    "hypothesize": "Form and test hypotheses about",
    "verify": "Verify root-cause resolution of",
}


# Backward-compat aliases.
_PHASE_NAMES = PHASE_NAMES
_DEFAULT_PHASE_NAMES = DEFAULT_PHASE_NAMES
_SUBTASK_PHASE_NAMES = SUBTASK_PHASE_NAMES
_PHASE_VERBS = PHASE_VERBS
