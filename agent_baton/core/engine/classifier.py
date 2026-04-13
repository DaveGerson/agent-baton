"""Task classifier — determines type, complexity, and agent roster for a plan.

Provides a protocol (``TaskClassifier``) with two concrete implementations:

- ``HaikuClassifier`` — calls Claude Haiku for intelligent classification.
- ``KeywordClassifier`` — deterministic fallback using keyword heuristics
  and registry-aware agent scoring.

The planner consumes whichever implementation is available via the
``FallbackClassifier`` wrapper, which tries Haiku first and degrades
gracefully to keywords.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.models.enums import AgentCategory

logger = logging.getLogger(__name__)

_VALID_COMPLEXITIES = ("light", "medium", "heavy")

# Maximum agents per complexity tier — prevents bloated plans
_MAX_AGENTS_BY_COMPLEXITY: dict[str, int] = {
    "light": 1,
    "medium": 3,
    "heavy": 5,
}

_VALID_TASK_TYPES = (
    "new-feature", "bug-fix", "refactor", "data-analysis",
    "documentation", "migration", "test",
)

# ---------------------------------------------------------------------------
# TaskClassification dataclass
# ---------------------------------------------------------------------------

@dataclass
class TaskClassification:
    """Result of classifying a task for plan construction."""
    task_type: str
    complexity: str
    agents: list[str]
    phases: list[str]
    reasoning: str
    source: str

    def __post_init__(self) -> None:
        if self.complexity not in _VALID_COMPLEXITIES:
            raise ValueError(
                f"complexity must be one of {_VALID_COMPLEXITIES}, "
                f"got {self.complexity!r}"
            )
        if not self.agents:
            raise ValueError("agents must be non-empty")
        if not self.phases:
            raise ValueError("phases must be non-empty")


# ---------------------------------------------------------------------------
# TaskClassifier protocol
# ---------------------------------------------------------------------------

class TaskClassifier(Protocol):
    """Structural type for task classification."""
    def classify(
        self,
        summary: str,
        registry: AgentRegistry,
        project_root: Path | None = None,
    ) -> TaskClassification: ...


# ---------------------------------------------------------------------------
# Complexity / scope signals for KeywordClassifier
# ---------------------------------------------------------------------------

_LIGHT_QUANTIFIERS = re.compile(
    r"\b(?:one|two|three|[1-5])\s+(?:file|test|function|method|endpoint|component|field|line|class)s?\b",
    re.IGNORECASE,
)
_LIGHT_VERBS = re.compile(
    r"\b(?:move|rename|delete|remove|copy|update|change|swap|toggle)\b",
    re.IGNORECASE,
)
_HEAVY_SCOPE = re.compile(
    r"\b(?:entire|all|every|system-wide|across|throughout|whole|comprehensive)\b",
    re.IGNORECASE,
)
_HEAVY_ARCH = re.compile(
    r"\b(?:redesign|rearchitect|new\s+pattern|overhaul|rewrite|rebuild)\b",
    re.IGNORECASE,
)
_HEAVY_MULTI_DOMAIN = re.compile(
    r"\b(?:frontend\s+and\s+backend|api\s+and\s+ui|server\s+and\s+client|"
    r"across\s+(?:frontend|backend|api|ui|server|client))\b",
    re.IGNORECASE,
)

# Category-to-task-type affinity for registry scoring
_CATEGORY_AFFINITY: dict[str, set[str]] = {
    "data-analysis": {AgentCategory.DATA.value},
    "migration": {AgentCategory.ENGINEERING.value, AgentCategory.DATA.value},
    "new-feature": {AgentCategory.ENGINEERING.value},
    "bug-fix": {AgentCategory.ENGINEERING.value},
    "refactor": {AgentCategory.ENGINEERING.value},
    "documentation": {AgentCategory.ENGINEERING.value, AgentCategory.META.value},
    "test": {AgentCategory.ENGINEERING.value},
}

# Preferred "primary implementer" per task type for light complexity
_PRIMARY_IMPLEMENTER: dict[str, str] = {
    "new-feature": "backend-engineer",
    "bug-fix": "backend-engineer",
    "refactor": "backend-engineer",
    "migration": "backend-engineer",
    "data-analysis": "data-analyst",
    "documentation": "architect",
    "test": "test-engineer",
}


# ---------------------------------------------------------------------------
# Shared task-type inference — word-boundary scoring
# ---------------------------------------------------------------------------

def _score_task_type(
    summary: str,
    task_type_keywords: list[tuple[str, list[str]]],
) -> str:
    """Infer task type by scoring word-boundary keyword matches.

    Each task type is scored by how many of its keywords appear as whole
    words in *summary*.  The type with the most hits wins; ties are broken
    by list order (earlier = higher priority).  Returns ``"new-feature"``
    when no keyword matches at all.

    Word-boundary matching prevents false positives like "fix" matching
    inside "prefix" or "test" inside "latest".
    """
    lower = summary.lower()
    best_type = "new-feature"
    best_score = 0
    for task_type, keywords in task_type_keywords:
        score = 0
        for kw in keywords:
            # Multi-word keywords use substring matching (specific enough).
            # Single-word keywords require word boundaries.
            if " " in kw:
                if kw in lower:
                    score += 1
            else:
                if re.search(r"\b" + re.escape(kw) + r"\b", lower):
                    score += 1
        if score > best_score:
            best_score = score
            best_type = task_type
    return best_type


# ---------------------------------------------------------------------------
# KeywordClassifier
# ---------------------------------------------------------------------------

class KeywordClassifier:
    """Deterministic fallback classifier using keyword heuristics.

    Uses the existing task-type keyword matching from the planner
    combined with new complexity inference and registry-aware agent
    scoring.
    """

    def classify(
        self,
        summary: str,
        registry: AgentRegistry,
        project_root: Path | None = None,
    ) -> TaskClassification:
        # Late import to avoid circular imports: planner imports classifier,
        # classifier imports planner constants.
        from agent_baton.core.engine.planner import (
            _DEFAULT_AGENTS,
            _PHASE_NAMES,
            _TASK_TYPE_KEYWORDS,
        )
        task_type = self._infer_task_type(summary, _TASK_TYPE_KEYWORDS)
        complexity = self._infer_complexity(summary)
        agents = self._select_agents(
            summary, task_type, complexity, registry, _DEFAULT_AGENTS
        )
        phases = self._select_phases(task_type, complexity, _PHASE_NAMES)
        return TaskClassification(
            task_type=task_type,
            complexity=complexity,
            agents=agents,
            phases=phases,
            reasoning=f"Keyword classification: {task_type}/{complexity}",
            source="keyword-fallback",
        )

    @staticmethod
    def _infer_task_type(
        summary: str,
        task_type_keywords: list[tuple[str, list[str]]],
    ) -> str:
        return _score_task_type(summary, task_type_keywords)

    def _infer_complexity(self, summary: str) -> str:
        heavy_signals = 0
        light_signals = 0

        if _HEAVY_SCOPE.search(summary):
            heavy_signals += 1
        if _HEAVY_ARCH.search(summary):
            heavy_signals += 1
        if _HEAVY_MULTI_DOMAIN.search(summary):
            heavy_signals += 1

        if _LIGHT_QUANTIFIERS.search(summary):
            light_signals += 1
        if _LIGHT_VERBS.search(summary):
            light_signals += 1

        if heavy_signals >= 2:
            return "heavy"
        if heavy_signals >= 1 and light_signals == 0:
            return "heavy"
        if light_signals >= 1 and heavy_signals == 0:
            return "light"
        return "medium"

    def _select_agents(
        self,
        summary: str,
        task_type: str,
        complexity: str,
        registry: AgentRegistry,
        default_agents: dict[str, list[str]],
    ) -> list[str]:
        # Start with the default roster for this task type, but only keep
        # agents that are actually in the registry (or whose base name has
        # a registry entry).  Unregistered defaults are dead weight that
        # would consume cap slots without being routable.
        registry_names = set(registry.agents.keys())
        registry_bases = {n.split("--")[0] for n in registry_names}
        raw_defaults = default_agents.get(task_type, ["backend-engineer"])
        base_agents = [
            a for a in raw_defaults
            if a in registry_names or a in registry_bases
        ]
        # If every default was pruned (empty registry), keep the original
        # list so downstream still gets valid agent names.
        if not base_agents:
            base_agents = list(raw_defaults)

        # Score registered agents not in the default list by keyword
        # overlap with the task summary.  Require meaningful overlap:
        # category affinity alone (+2.0) is not enough — the agent must
        # also share ≥2 keywords with the summary to prove relevance.
        summary_words = set(summary.lower().split())
        scored_extras: list[tuple[float, str]] = []
        base_agent_bases = {a.split("--")[0] for a in base_agents}
        for name, agent_def in registry.agents.items():
            if name in base_agents:
                continue
            # Skip flavoured variants whose base is already present —
            # routing (step 6) picks the right flavour later.
            base_name = name.split("--")[0]
            if base_name in base_agent_bases:
                continue
            desc_words = set(agent_def.description.lower().split())
            overlap = len(summary_words & desc_words)
            category_match = (
                agent_def.category.value
                in _CATEGORY_AFFINITY.get(task_type, set())
            )
            # Require at least 2 keyword hits; category match is a bonus,
            # not a free pass.
            if overlap < 2:
                continue
            score = overlap + (2.0 if category_match else 0.0)
            scored_extras.append((score, name))

        scored_extras.sort(reverse=True)
        all_candidates = base_agents + [name for _, name in scored_extras]

        # Cap to complexity tier — matches HaikuClassifier behaviour
        max_agents = _MAX_AGENTS_BY_COMPLEXITY.get(complexity, 5)

        # Scale by complexity
        if complexity == "light":
            primary = _PRIMARY_IMPLEMENTER.get(task_type, "backend-engineer")
            # Prefer a matched extra agent over the default primary if
            # scored_extras has a better fit
            if scored_extras and scored_extras[0][0] > 3.0:
                return [scored_extras[0][1]]
            # Check if primary exists in all_candidates
            for agent in all_candidates:
                if agent.startswith(primary):
                    return [agent]
            return [all_candidates[0]] if all_candidates else ["backend-engineer"]
        elif complexity == "medium":
            # Drop review-only agents, keep implementers
            review_agents = {"code-reviewer", "auditor", "security-reviewer"}
            filtered = [a for a in all_candidates if a not in review_agents]
            return (filtered or all_candidates)[:max_agents]
        else:  # heavy
            return all_candidates[:max_agents]

    def _select_phases(
        self,
        task_type: str,
        complexity: str,
        phase_names: dict[str, list[str]],
    ) -> list[str]:
        full_phases = list(
            phase_names.get(task_type, ["Design", "Implement", "Test", "Review"])
        )
        if complexity == "light":
            return ["Implement"]
        elif complexity == "medium":
            return [p for p in full_phases if p != "Review"] or full_phases
        else:  # heavy
            return full_phases


# ---------------------------------------------------------------------------
# HaikuClassifier
# ---------------------------------------------------------------------------

_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_HAIKU_TIMEOUT = 5.0  # seconds
_HAIKU_MAX_TOKENS = 256

_CLASSIFIER_PROMPT_TEMPLATE = """\
You are a task classifier for a software orchestration engine.

Task: "{summary}"

Available agents:
{agent_list}

Classify this task and return JSON only (no markdown, no commentary):
{{
  "task_type": one of ["new-feature", "bug-fix", "refactor", "migration", \
"data-analysis", "documentation", "test"],
  "complexity": one of ["light", "medium", "heavy"],
  "agents": [ordered list of agent names needed, from available list only],
  "phases": [ordered list of phase names, e.g. ["Implement"] or \
["Design", "Implement", "Test", "Review"]],
  "reasoning": "one sentence explaining the classification"
}}

Complexity guide:
- light: 1-3 files, single domain, simple mechanical action, no \
architectural decisions. Exactly 1 agent, 1 phase.
- medium: 3-6 files, may cross domains, moderate effort, some design \
needed. 2-3 agents (MAX 3), 2-3 phases.
- heavy: 6+ files, multi-domain, new patterns, high risk, needs review \
gates. 3-5 agents (MAX 5), 3-4 phases.

Rules:
- Select ONLY agents from the available list above.
- Fewer agents is better. Only add agents that have distinct work to do.
- NEVER exceed the agent count limit for the complexity tier.
- For light tasks, use exactly 1 implementer agent. No design or review.
- Include review/audit agents only for heavy complexity.
- Each agent should have a DIFFERENT job. Do not assign multiple agents \
to the same phase unless they do genuinely different work.
- Phase count should match complexity."""


def _call_haiku(prompt: str) -> str:
    """Call Claude Haiku via the Anthropic SDK.

    Separated into a module-level function for easy mocking in tests.
    Raises ImportError if the SDK is not installed, or any API error.
    """
    import anthropic  # optional dependency

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    response = client.messages.create(
        model=_HAIKU_MODEL,
        max_tokens=_HAIKU_MAX_TOKENS,
        timeout=_HAIKU_TIMEOUT,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


class HaikuClassifier:
    """Classify tasks using Claude Haiku via the Anthropic SDK.

    Builds a structured prompt listing all registered agents and asks
    Haiku to return a JSON classification. Validates the response
    against the registry (rejects agents not in the registry).
    """

    def classify(
        self,
        summary: str,
        registry: AgentRegistry,
        project_root: Path | None = None,
    ) -> TaskClassification:
        prompt = self._build_prompt(summary, registry)
        raw_response = _call_haiku(prompt)
        return self._parse_response(raw_response, registry)

    def _build_prompt(self, summary: str, registry: AgentRegistry) -> str:
        agent_lines: list[str] = []
        for name, agent_def in sorted(registry.agents.items()):
            cat = agent_def.category.value
            agent_lines.append(f"- {name}: {agent_def.description} [category: {cat}]")
        agent_list = "\n".join(agent_lines)
        # Escape braces in summary/agent_list to prevent KeyError from .format()
        safe_summary = summary.replace("{", "{{").replace("}", "}}")
        safe_agent_list = agent_list.replace("{", "{{").replace("}", "}}")
        return _CLASSIFIER_PROMPT_TEMPLATE.format(
            summary=safe_summary,
            agent_list=safe_agent_list,
        )

    def _parse_response(
        self, raw: str, registry: AgentRegistry
    ) -> TaskClassification:
        # Strip markdown code fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            raise ValueError(f"Haiku returned invalid JSON: {e}") from e

        # Validate and filter agents against registry
        valid_names = set(registry.agents.keys())
        filtered_agents = [a for a in data.get("agents", []) if a in valid_names]
        if not filtered_agents:
            raise ValueError(
                f"Haiku returned no valid agents. "
                f"Raw agents: {data.get('agents', [])}, "
                f"valid: {sorted(valid_names)}"
            )

        # Validate complexity
        complexity = data.get("complexity", "medium")
        if complexity not in _VALID_COMPLEXITIES:
            complexity = "medium"

        # Validate task_type
        task_type = data.get("task_type", "new-feature")
        if task_type not in _VALID_TASK_TYPES:
            task_type = "new-feature"

        phases = data.get("phases", ["Implement"])
        if not phases:
            phases = ["Implement"]

        # Cap agents to prevent bloated plans — respect complexity tier limits
        max_agents = _MAX_AGENTS_BY_COMPLEXITY.get(complexity, 5)
        filtered_agents = filtered_agents[:max_agents]

        return TaskClassification(
            task_type=task_type,
            complexity=complexity,
            agents=filtered_agents,
            phases=phases,
            reasoning=data.get("reasoning", "Haiku classification"),
            source="haiku",
        )


# ---------------------------------------------------------------------------
# FallbackClassifier
# ---------------------------------------------------------------------------

class FallbackClassifier:
    """Try HaikuClassifier; on any failure, fall back to KeywordClassifier.

    Checks for Haiku availability (SDK import, API key) at call time,
    not at construction time, so the planner works regardless of when
    the API key becomes available.
    """

    def __init__(self) -> None:
        self._haiku = HaikuClassifier()
        self._keyword = KeywordClassifier()

    def classify(
        self,
        summary: str,
        registry: AgentRegistry,
        project_root: Path | None = None,
    ) -> TaskClassification:
        try:
            return self._haiku.classify(summary, registry, project_root)
        except Exception:
            logger.debug(
                "Haiku classification failed — falling back to keyword classifier",
                exc_info=True,
            )
            return self._keyword.classify(summary, registry, project_root)
