"""Concern detection patterns and cross-concern signal table."""
from __future__ import annotations

import re

# Cross-concern signals: agent role → keywords that suggest the task
# touches that agent's domain.  Used by routing to expand the agent
# roster when the task summary mentions concerns from multiple domains.
CROSS_CONCERN_SIGNALS: dict[str, list[str]] = {
    "frontend-engineer": [
        "ux", "ui", "navigate", "browser", "visual", "layout",
        "css", "component", "react", "frontend", "checkout",
        "dashboard", "chart", "form", "page",
    ],
    "backend-engineer": [
        "api", "endpoint", "server", "database", "migration", "backend",
        "fix", "bug", "broken", "error", "remediate", "patch",
        "service", "handler", "middleware", "queue", "webhook",
    ],
    "test-engineer": [
        "test", "tests", "testing", "test suite", "e2e", "playwright",
        "coverage", "vitest", "jest", "unit test", "integration test",
        "regression", "verify", "validate",
    ],
    "code-reviewer": [
        "review", "code quality",
    ],
    "data-engineer": [
        "etl", "pipeline", "warehouse", "data lake", "spark",
        "airflow", "ingest", "transform", "batch",
    ],
    "data-scientist": [
        "model", "predict", "classify", "cluster", "train",
        "feature engineering", "experiment", "hypothesis",
    ],
    "architect": [
        "design", "architecture", "schema", "pattern", "redesign",
        "system design", "rearchitect", "overhaul",
    ],
    "auditor": [
        "compliance", "gdpr", "sox", "hipaa", "pci", "audit",
        "regulated", "regulation", "audit trail",
    ],
    "security-reviewer": [
        "security", "auth", "credential", "secret", "vulnerability",
        "cve", "owasp", "encryption", "token",
    ],
    "devops-engineer": [
        "deploy", "ci", "cd", "docker", "terraform", "kubernetes",
        "helm", "infrastructure", "pipeline", "staging", "production",
    ],
    "documentation-architect": [
        "documentation", "docs", "readme", "wiki", "guide",
        "tutorial", "reference",
    ],
}

# Regex for compound-task split: detects (1), 1., 1) markers that
# typically separate sequential sub-tasks ("(1) build foo (2) test bar").
SUBTASK_SPLIT = re.compile(
    r"(?:^|(?<=\s))(?:\((\d+)\)|(\d+)[.\)])\s+",
)

# Concern-marker regex.  Matches:
#   - Feature-id markers: F0.1, f1.2, A2.3
#   - Parenthesized integers: (1), (2)
#   - Bare integers with punctuation: 1., 2), but NOT decimals (1.5)
# Must be at start-of-string or after whitespace.
CONCERN_MARKER = re.compile(
    r"(?:^|(?<=\s))"
    r"("
    r"[A-Za-z]\d+\.\d+"
    r"|\(\d+\)"
    r"|\d+[.\)](?!\d)"
    r")"
    r"\s+"
)

# Minimum distinct concerns needed before the planner splits an
# implement-class phase into per-concern parallel steps.  Below this
# threshold the planner treats the summary as a single concern.
MIN_CONCERNS_FOR_SPLIT: int = 3

# Keywords that bound the concern list — anything after one of these
# phrases is treated as a constraint or non-goal, not a deliverable.
# bd-021d: prevents "Must not regress F0.3 ..." from being parsed as
# a phantom Implement step.
CONCERN_CONSTRAINT_KEYWORDS: tuple[str, ...] = (
    "must not",
    "do not",
    "shall not",
    "should not",
    "regress",
    "non-goal",
    "non-goals",
)

# ---------------------------------------------------------------------------
# Structured-spec detection — used by DecompositionStage to recognize when
# a task summary contains an explicit multi-phase spec, so the planner does
# not flatten it into one phase or explode it into one-plan-per-line (the
# plan-explosion incident).
# ---------------------------------------------------------------------------

# A phase header line in a structured spec.  Matches:
#   "Phase 1: Authentication"
#   "Phase 2 - Authorization"
#   "## Phase 3: Tenancy"
#   "Step 1: Set up the database"
PHASE_HEADER = re.compile(
    r"^(?:#+\s*)?"                          # optional markdown heading
    r"(?:phase|step|stage|milestone)\s+"   # section keyword
    r"(\d+(?:\.\d+)?)"                     # phase number (1, 1.1, 2)
    r"\s*[:\-—]\s*"                        # delimiter
    r"(.+?)$",                             # phase title
    re.IGNORECASE | re.MULTILINE,
)


# Backward-compat aliases.
_CROSS_CONCERN_SIGNALS = CROSS_CONCERN_SIGNALS
_SUBTASK_SPLIT = SUBTASK_SPLIT
_CONCERN_MARKER = CONCERN_MARKER
_MIN_CONCERNS_FOR_SPLIT = MIN_CONCERNS_FOR_SPLIT
_CONCERN_CONSTRAINT_KEYWORDS = CONCERN_CONSTRAINT_KEYWORDS
