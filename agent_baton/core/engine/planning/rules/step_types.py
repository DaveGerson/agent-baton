"""Agent → step_type mapping."""
from __future__ import annotations

# Agent base name → default step_type.  Unknown agents fall through to
# "developing".  ``planning.stages.routing._step_type_for_agent`` consumes
# this and may override based on phase context (bd-b3e1).
AGENT_STEP_TYPE: dict[str, str] = {
    "architect": "planning",
    "ai-systems-architect": "planning",
    "code-reviewer": "reviewing",
    "security-reviewer": "reviewing",
    "auditor": "reviewing",
    "test-engineer": "testing",
    "task-runner": "task",
}

# When test-engineer's task description contains one of these keywords,
# the step_type flips from "testing" to "developing" (the engineer is
# building test infrastructure rather than running tests).
TEST_ENGINEER_DEVELOPING_KEYWORDS: tuple[str, ...] = ("create", "build", "scaffold")


# Backward-compat aliases.
_AGENT_STEP_TYPE = AGENT_STEP_TYPE
_TEST_ENGINEER_DEVELOPING_KEYWORDS = TEST_ENGINEER_DEVELOPING_KEYWORDS
