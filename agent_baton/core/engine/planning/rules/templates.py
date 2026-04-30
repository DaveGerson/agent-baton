"""Step description templates and default deliverables by agent role."""
from __future__ import annotations

# Per-agent, per-phase step description templates.  ``{task}`` is filled
# with the task summary at format time.  Lookup is keyed by base agent
# name (no flavor suffix) and lower-cased phase name.
STEP_TEMPLATES: dict[str, dict[str, str]] = {
    "architect": {
        "design": (
            "Produce a design for: {task} that the implementation team can build "
            "from without further clarification."
        ),
        "research": (
            "Assess feasibility and constraints for: {task}. Surface anything "
            "that would change the implementation approach."
        ),
        "review": (
            "Review: {task} for architectural fitness. Approve or flag "
            "structural issues."
        ),
        "prepare": (
            "Prepare context and materials for: {task}. Gather relevant "
            "documentation, define evaluation criteria, and create a knowledge "
            "pack the audit agents can reference."
        ),
        "audit": (
            "Audit: {task}. Evaluate against quality, completeness, and "
            "fitness criteria. Document findings with severity and evidence."
        ),
        "assess": (
            "Assess: {task}. Evaluate current state, identify gaps, and "
            "grade against defined criteria."
        ),
        "synthesize": (
            "Synthesize findings from: {task}. Consolidate results into a "
            "prioritized set of recommendations with a phased roadmap."
        ),
    },
    "backend-engineer": {
        "implement": "Implement: {task}. Deliver working, tested code.",
        "fix": "Fix: {task}. Include a regression test.",
        "design": "Design the backend approach for: {task}.",
        "investigate": (
            "Investigate: {task}. Document root cause and reproduction steps."
        ),
    },
    "frontend-engineer": {
        "implement": (
            "Implement the UI for: {task}. Deliver working, accessible components."
        ),
        "design": "Design the frontend approach for: {task}.",
    },
    "test-engineer": {
        "test": "Verify: {task}. Deliver tests that would catch regressions.",
        "implement": "Build test infrastructure for: {task}.",
        "review": "Review test coverage for: {task}. Flag gaps.",
    },
    "code-reviewer": {
        "review": "Review: {task}. Approve or flag issues blocking merge.",
    },
    "security-reviewer": {
        "review": "Security audit: {task}. Flag vulnerabilities and required fixes.",
    },
    "devops-engineer": {
        "implement": "Set up infrastructure for: {task}.",
        "review": "Review infrastructure for: {task}. Flag operational risks.",
    },
    "data-engineer": {
        "design": "Design the data layer for: {task}.",
        "implement": "Implement the data layer for: {task}.",
    },
    "data-analyst": {
        "design": "Plan the analysis for: {task}.",
        "implement": "Execute the analysis for: {task}. Deliver findings.",
    },
    "data-scientist": {
        "design": "Design the modeling approach for: {task}.",
        "implement": "Build and evaluate models for: {task}.",
    },
    "auditor": {
        "review": "Audit: {task}. Provide pass/fail with findings.",
        "audit": "Audit: {task}. Evaluate against compliance and quality criteria. Document findings.",
        "assess": "Assess: {task}. Evaluate current state and identify compliance gaps.",
    },
    "visualization-expert": {
        "implement": "Create visualizations for: {task}.",
    },
    "subject-matter-expert": {
        "research": "Provide domain context for: {task}.",
        "review": "Validate domain correctness of: {task}.",
    },
}

# Default deliverables by agent base name — used when the planner has
# no learned pattern to draw from.
AGENT_DELIVERABLES: dict[str, list[str]] = {
    "architect": ["Design document"],
    "backend-engineer": ["Working implementation with tests"],
    "frontend-engineer": ["Working UI components with tests"],
    "test-engineer": ["Test suite"],
    "code-reviewer": ["Review verdict with findings"],
    "security-reviewer": ["Security audit report"],
    "devops-engineer": ["Infrastructure configuration"],
    "data-engineer": ["Schema and migrations"],
    "data-analyst": ["Analysis results"],
    "data-scientist": ["Model with evaluation results"],
    "auditor": ["Audit verdict"],
    "visualization-expert": ["Visualizations"],
    "subject-matter-expert": ["Domain context document"],
}


# Backward-compat aliases.
_STEP_TEMPLATES = STEP_TEMPLATES
_AGENT_DELIVERABLES = AGENT_DELIVERABLES
