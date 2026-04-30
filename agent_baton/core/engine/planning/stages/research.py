"""ResearchStage — optional pre-roster discovery pass.

Runs between ClassificationStage and RosterStage.  When a task has broad
scope (e.g. "audit all components") the planner cannot know how many
independent work surfaces exist, so it collapses everything into a single
phase.  ResearchStage queries HeadlessClaude to enumerate those surfaces
first, storing them in ``draft.research_concerns`` so that downstream
stages (Decomposition in particular) can parallelise across them.

Skip conditions (returns draft unchanged):
- HeadlessClaude CLI is unavailable
- Task complexity is "light"
- Task type is one that doesn't benefit from discovery (bug-fix, test)
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.services import PlannerServices

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Task types where research adds no value — they are already well-scoped.
_SKIP_TASK_TYPES: frozenset[str] = frozenset({"bug-fix", "test"})

# Task types (and keyword fragments) that signal broad-scope tasks where
# research can discover parallelisable work surfaces.
_RESEARCH_TASK_TYPES: frozenset[str] = frozenset({
    "audit", "assessment", "new-feature", "refactor", "migration",
    "data-analysis", "generic",
})

_BROAD_SCOPE_KEYWORDS: tuple[str, ...] = (
    "all components",
    "all modules",
    "all subsystems",
    "all services",
    "entire",
    "comprehensive",
    "full audit",
    "full review",
    "full assessment",
    "system-wide",
    "across the",
    "each component",
    "each module",
    "each service",
)

_RESEARCH_SYSTEM_PROMPT = (
    "You are a codebase analyst embedded in an AI orchestration engine. "
    "Your job is to identify independent work surfaces so the planner can "
    "parallelise work across them. Return ONLY valid JSON — no prose, no "
    "markdown fences."
)

_RESEARCH_PROMPT_TEMPLATE = """\
You are helping an orchestration engine plan work across a codebase.

Task: {task_summary}

Project root: {project_root}

Your job: identify the distinct, independent components/domains/subsystems
that the task applies to.  These will become parallel work streams.

Rules:
1. Explore the project structure under the project root to find real modules,
   packages, services, or domain boundaries.
2. Each item must be independently actionable (another agent can work on it
   without blocking on the others).
3. Aim for 3-8 items.  If the task is genuinely single-domain, return 1.
4. Each marker must be a short string (e.g. "1", "2", "A") used as a label.

Return a JSON array — nothing else:
[
  {{"marker": "1", "text": "Brief description of this work surface"}},
  {{"marker": "2", "text": "Brief description of this work surface"}},
  ...
]

If you cannot identify distinct parallelisable surfaces, return an empty
array: []
"""


class ResearchStage:
    """Stage 1.5: optional codebase discovery to enumerate work surfaces.

    Inserted between ClassificationStage and RosterStage.  Writes
    ``draft.research_concerns`` (list of (marker, text) tuples) and
    ``draft.research_context`` (free-text summary of findings).
    """

    name = "research"

    def run(self, draft: PlanDraft, services: PlannerServices) -> PlanDraft:
        if not self._should_run(draft):
            logger.debug(
                "ResearchStage: skipping (type=%s, complexity=%s)",
                draft.inferred_type,
                draft.inferred_complexity,
            )
            return draft

        hc = self._get_headless()
        if hc is None:
            logger.debug("ResearchStage: skipping — HeadlessClaude unavailable")
            return draft

        project_root_str = str(draft.project_root) if draft.project_root else "."
        prompt = _RESEARCH_PROMPT_TEMPLATE.format(
            task_summary=draft.task_summary,
            project_root=project_root_str,
        )

        try:
            result = hc.run_sync(
                prompt,
                model="sonnet",
                system_prompt=_RESEARCH_SYSTEM_PROMPT,
            )
        except Exception as exc:
            logger.debug("ResearchStage: HeadlessClaude call failed: %s", exc)
            return draft

        if not result.success:
            logger.debug(
                "ResearchStage: HeadlessClaude returned failure: %s", result.error
            )
            return draft

        concerns = self._parse_response(result.output)
        if not concerns:
            logger.debug("ResearchStage: no concerns returned — leaving draft unchanged")
            return draft

        draft.research_concerns = concerns
        draft.research_context = (
            f"ResearchStage discovered {len(concerns)} independent work surface(s): "
            + ", ".join(text for _, text in concerns)
        )
        logger.info(
            "ResearchStage: discovered %d work surface(s): %s",
            len(concerns),
            [text for _, text in concerns],
        )
        return draft

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _should_run(self, draft: PlanDraft) -> bool:
        """Return True when research is likely to improve plan quality."""
        # Never run for light tasks — they're already simple enough.
        if draft.inferred_complexity == "light":
            return False

        # Never run for task types that are already well-scoped.
        if draft.inferred_type in _SKIP_TASK_TYPES:
            return False

        # Always run when the task explicitly has broad scope.
        lower = draft.task_summary.lower()
        if any(kw in lower for kw in _BROAD_SCOPE_KEYWORDS):
            return True

        # Run for audit/assessment tasks regardless of phrasing — their
        # whole point is to span the codebase.
        if draft.inferred_type in ("audit", "assessment"):
            return True

        return False

    def _get_headless(self) -> Any:
        """Instantiate HeadlessClaude (Sonnet) or return None if unavailable."""
        try:
            from agent_baton.core.runtime.headless import HeadlessClaude, HeadlessConfig
        except Exception:
            return None

        hc = HeadlessClaude(HeadlessConfig(model="sonnet", timeout_seconds=120.0))
        if not hc.is_available:
            return None
        return hc

    @staticmethod
    def _parse_response(raw: str) -> list[tuple[str, str]]:
        """Parse the JSON array from the LLM response.

        Returns a list of (marker, text) tuples, or empty list on failure.
        """
        text = raw.strip()
        # Strip markdown code fences if present.
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [ln for ln in lines if not ln.strip().startswith("```")]
            text = "\n".join(lines).strip()

        # Try a clean parse first.
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Fall back to extracting the array from somewhere in the output.
            start = text.find("[")
            end = text.rfind("]")
            if start < 0 or end <= start:
                logger.debug("ResearchStage: could not find JSON array in output")
                return []
            try:
                data = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                logger.debug("ResearchStage: JSON parse failed on extracted array")
                return []

        if not isinstance(data, list):
            return []

        concerns: list[tuple[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            marker = str(item.get("marker", "")).strip()
            text_val = str(item.get("text", "")).strip()
            if marker and text_val:
                concerns.append((marker, text_val))

        return concerns
