"""Plan Strategies for generating draft MachinePlans.

Ownership: ``HeuristicStrategy`` (canonical name, aliased as
``ZeroShotStrategy`` for stub compatibility — see BEAD_DECISION below)
generates plans using the keyword/Haiku-classifier path that the
``IntelligentPlanner`` used inline before the 005b decomposition.

``TemplateStrategy`` and ``RefinementStrategy`` are forward-port
placeholders; concrete implementations are deferred to Phase 1.5.

Constants ``_DEFAULT_AGENTS`` and ``_PHASE_NAMES`` are defined here
(byte-identical to planner.py) and re-exported by ``planner.py`` so
existing importers continue to work.  ``_TASK_TYPE_KEYWORDS`` lives
in ``planner.py``; it is not moved in this step.

BEAD_DECISION: class naming
  CHOSE: Keep ``ZeroShotStrategy`` as the class name and expose
  ``HeuristicStrategy = ZeroShotStrategy`` as the canonical alias.
  BECAUSE: The skeleton shipped ``ZeroShotStrategy`` and may have
  already been imported by downstream code or tests expecting that
  name.  Adding a forward alias is the zero-breakage path.

Per 005b-phase1-design.md §3 (Step 1.3).
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep, TeamMember

from agent_baton.core.engine._planner_helpers import (
    _CROSS_CONCERN_SIGNALS,
    _PHASE_VERBS,
    _expand_agents_for_concerns,
    _parse_concerns,
    _split_implement_phase_by_concerns,
)
from agent_baton.core.engine.analyzers import SubscalePlanError

if TYPE_CHECKING:
    from agent_baton.models.pattern import LearnedPattern

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level constants — copies byte-identical to planner.py originals.
# planner.py re-exports these; consumers that import from planner continue
# to work.  Step 1.4 will switch planner.py to import from here.
# ---------------------------------------------------------------------------

# Default agents by task type when no pattern is found.
_DEFAULT_AGENTS: dict[str, list[str]] = {
    "new-feature": ["architect", "backend-engineer", "test-engineer", "code-reviewer"],
    "bug-fix": ["backend-engineer", "test-engineer"],
    "refactor": ["architect", "backend-engineer", "test-engineer", "code-reviewer"],
    "data-analysis": ["architect", "data-analyst"],
    "documentation": ["architect", "talent-builder", "code-reviewer"],
    "migration": ["architect", "backend-engineer", "test-engineer", "code-reviewer", "auditor"],
    "test": ["test-engineer"],
    "audit": ["architect", "code-reviewer"],
    "assessment": ["architect", "data-analyst", "code-reviewer"],
    "investigation": ["architect", "backend-engineer", "test-engineer"],
    # E3 — fallback for unknown/generic tasks: default four-phase roster
    "generic": ["architect", "backend-engineer", "test-engineer", "code-reviewer"],
}

# Phase templates by task type
_PHASE_NAMES: dict[str, list[str]] = {
    "new-feature": ["Design", "Implement", "Test", "Review"],
    "bug-fix": ["Investigate", "Fix", "Test"],
    "refactor": ["Design", "Implement", "Test", "Review"],
    "data-analysis": ["Design", "Implement", "Review"],
    "documentation": ["Research", "Draft", "Review"],
    "migration": ["Design", "Implement", "Test", "Review"],
    "test": ["Implement", "Review"],
    "audit": ["Prepare", "Audit", "Synthesize", "Review"],
    "assessment": ["Research", "Assess", "Synthesize", "Review"],
    "investigation": ["Investigate", "Hypothesize", "Fix", "Verify"],
    # E3 — fallback phases for generic/unknown task types
    "generic": ["Investigate", "Implement", "Test", "Review"],
}

_DEFAULT_PHASE_NAMES: list[str] = ["Design", "Implement", "Test", "Review"]

# Minimum confidence required to follow a learned pattern
_MIN_PATTERN_CONFIDENCE = 0.7

# Compound task decomposition sub-task phase name mapping
_SUBTASK_PHASE_NAMES: dict[str, str] = {
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

# Regex to split numbered sub-tasks: (1), 1., or 1)
_SUBTASK_SPLIT = re.compile(
    r"(?:^|(?<=\s))(?:\((\d+)\)|(\d+)[.\)])\s+",
)

# Step type assignment — maps agent role to default step_type
_AGENT_STEP_TYPE: dict[str, str] = {
    "architect": "planning",
    "ai-systems-architect": "planning",
    "code-reviewer": "reviewing",
    "security-reviewer": "reviewing",
    "auditor": "reviewing",
    "test-engineer": "testing",
    "task-runner": "task",
}

# Keywords that flip test-engineer's step_type back to "developing"
_TEST_ENGINEER_DEVELOPING_KEYWORDS = ("create", "build", "scaffold")

# Phase names whose step_type must be "developing" regardless of the agent's default role.
_IMPLEMENT_PHASE_NAMES = {"implement", "fix", "draft", "build", "develop"}

# Agent step templates for rich descriptions
_STEP_TEMPLATES: dict[str, dict[str, str]] = {
    "architect": {
        "design": (
            "Produce a design for: {task} that the implementation team can build from without further clarification."
        ),
        "research": (
            "Assess feasibility and constraints for: {task}. Surface anything that would change the implementation approach."
        ),
        "review": (
            "Review: {task} for architectural fitness. Approve or flag structural issues."
        ),
    },
    "backend-engineer": {
        "implement": (
            "Implement: {task}. Deliver working, tested code."
        ),
        "fix": (
            "Fix: {task}. Include a regression test."
        ),
        "design": (
            "Design the backend approach for: {task}."
        ),
        "investigate": (
            "Investigate: {task}. Document root cause and reproduction steps."
        ),
    },
    "frontend-engineer": {
        "implement": (
            "Implement the UI for: {task}. Deliver working, accessible components."
        ),
        "design": (
            "Design the frontend approach for: {task}."
        ),
    },
    "test-engineer": {
        "test": (
            "Verify: {task}. Deliver tests that would catch regressions."
        ),
        "implement": (
            "Build test infrastructure for: {task}."
        ),
        "review": (
            "Review test coverage for: {task}. Flag gaps."
        ),
    },
    "code-reviewer": {
        "review": (
            "Review: {task}. Approve or flag issues blocking merge."
        ),
    },
    "security-reviewer": {
        "review": (
            "Security audit: {task}. Flag vulnerabilities and required fixes."
        ),
    },
    "devops-engineer": {
        "implement": (
            "Set up infrastructure for: {task}."
        ),
        "review": (
            "Review infrastructure for: {task}. Flag operational risks."
        ),
    },
    "data-engineer": {
        "design": (
            "Design the data layer for: {task}."
        ),
        "implement": (
            "Implement the data layer for: {task}."
        ),
    },
    "data-analyst": {
        "design": (
            "Plan the analysis for: {task}."
        ),
        "implement": (
            "Execute the analysis for: {task}. Deliver findings."
        ),
    },
    "data-scientist": {
        "design": (
            "Design the modeling approach for: {task}."
        ),
        "implement": (
            "Build and evaluate models for: {task}."
        ),
    },
    "auditor": {
        "review": (
            "Audit: {task}. Provide pass/fail with findings."
        ),
    },
    "visualization-expert": {
        "implement": (
            "Create visualizations for: {task}."
        ),
    },
    "subject-matter-expert": {
        "research": (
            "Provide domain context for: {task}."
        ),
        "review": (
            "Validate domain correctness of: {task}."
        ),
    },
}

# Default deliverables by agent base name
_AGENT_DELIVERABLES: dict[str, list[str]] = {
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

# Preferred agent roles per phase name — used for affinity-based assignment.
_PHASE_IDEAL_ROLES: dict[str, list[str]] = {
    "design": ["architect", "data-engineer", "data-analyst", "backend-engineer"],
    "research": ["architect", "subject-matter-expert", "data-analyst"],
    "investigate": ["backend-engineer", "frontend-engineer", "data-analyst"],
    "implement": ["backend-engineer", "frontend-engineer", "devops-engineer",
                   "data-engineer", "data-scientist", "visualization-expert"],
    "fix": ["backend-engineer", "frontend-engineer"],
    "draft": ["architect", "subject-matter-expert"],
    "test": ["test-engineer", "backend-engineer", "frontend-engineer"],
    "review": ["code-reviewer", "security-reviewer", "auditor", "architect"],
}

# Phase blocked roles
_PHASE_BLOCKED_ROLES: dict[str, set[str]] = {
    "implement": {"architect", "ai-systems-architect"},
    "fix": {"architect", "ai-systems-architect"},
    "draft": set(),
    "review": {
        "backend-engineer", "frontend-engineer", "devops-engineer",
        "data-engineer", "data-scientist", "data-analyst",
        "visualization-expert", "test-engineer",
    },
}

_IMPLEMENT_FALLBACK_AGENT = "backend-engineer"
_REVIEW_FALLBACK_AGENT = "code-reviewer"
_PHASE_FALLBACK_AGENT: dict[str, str] = {
    "implement": _IMPLEMENT_FALLBACK_AGENT,
    "fix": _IMPLEMENT_FALLBACK_AGENT,
    "review": _REVIEW_FALLBACK_AGENT,
}

# Fuzzy aliases for agent name detection in structured descriptions.
_AGENT_ALIASES: dict[str, str] = {
    "viz": "visualization-expert",
    "viz expert": "visualization-expert",
    "visualization": "visualization-expert",
    "sme": "subject-matter-expert",
    "subject matter expert": "subject-matter-expert",
    "backend": "backend-engineer",
    "frontend": "frontend-engineer",
    "devops": "devops-engineer",
    "security": "security-reviewer",
    "reviewer": "code-reviewer",
    "tester": "test-engineer",
    "data analyst": "data-analyst",
    "data engineer": "data-engineer",
    "data scientist": "data-scientist",
}


# ---------------------------------------------------------------------------
# Pure helpers (mirrored from planner.py — Step 1.4 will remove the copies)
# ---------------------------------------------------------------------------

def _step_type_for_agent(
    agent_name: str,
    task_description: str = "",
    phase_name: str | None = None,
) -> str:
    """Return the appropriate step_type for a given agent role.

    Mirrors ``IntelligentPlanner._step_type_for_agent`` (planner.py ~436).
    """
    base = agent_name.split("--")[0]
    step_type = _AGENT_STEP_TYPE.get(base, "developing")
    if base == "test-engineer" and step_type == "testing":
        lower_desc = task_description.lower()
        if any(kw in lower_desc for kw in _TEST_ENGINEER_DEVELOPING_KEYWORDS):
            step_type = "developing"
    if phase_name and phase_name.lower() in _IMPLEMENT_PHASE_NAMES:
        if base not in {"code-reviewer", "security-reviewer", "auditor"}:
            step_type = "developing"
    return step_type


# ---------------------------------------------------------------------------
# PlanContext dataclass
# BEAD_DECISION: placement
#   CHOSE: Define PlanContext in strategies.py (not _planner_helpers.py).
#   BECAUSE: PlanContext wraps strategy-execution context and carries
#   references to planner services (classifier, retro_engine, etc.).
#   It is not a pure stateless helper — it belongs with the consumers
#   that use it (strategies.py and, later, the planner pipeline).
#   _planner_helpers.py is reserved for pure stateless functions/constants.
# ---------------------------------------------------------------------------

@dataclass
class PlanContext:
    """Carries all inputs and service references needed by strategies and analyzers.

    Built once per ``create_plan`` call from the method's kwargs and the
    planner's injected services.  Strategies receive this as their second
    argument; analyzers receive it via ``**ctx.as_kwargs()``.

    Fields mirror ``IntelligentPlanner.create_plan`` keyword arguments plus
    references to injected planner services.  All service fields default to
    ``None`` so that lightweight tests can build a ``PlanContext`` without
    instantiating the full planner.
    """

    # ---- create_plan kwargs ------------------------------------------------
    task_summary: str = ""
    task_type: str | None = None
    complexity: str | None = None
    project_root: Path | None = None
    agents: list[str] | None = None
    phases: list[dict] | None = None
    explicit_knowledge_packs: list[str] | None = None
    explicit_knowledge_docs: list[str] | None = None
    intervention_level: str = "low"
    default_model: str | None = None
    gate_scope: str = "focused"

    # ---- planner services --------------------------------------------------
    classifier: Any = None            # DataClassifier | None
    task_classifier: Any = None       # TaskClassifier | None
    retro_engine: Any = None          # RetroEngine | None
    policy_engine: Any = None         # PolicyEngine | None
    knowledge_registry: Any = None    # KnowledgeRegistry | None
    project_config: Any = None        # ProjectConfig | None
    bead_store: Any = None            # BeadStore | None
    registry: Any = None              # AgentRegistry | None
    router: Any = None                # AgentRouter | None
    scorer: Any = None                # PerformanceScorer | None
    budget_tuner: Any = None          # BudgetTuner | None
    pattern_learner: Any = None       # PatternLearner | None

    def as_kwargs(self) -> dict[str, Any]:
        """Return a dict suitable for ``**kwargs`` expansion into analyzer.validate().

        All planner-pipeline fields are included so analyzers can pick
        what they need via keyword unpacking.
        """
        return {
            "task_summary": self.task_summary,
            "task_type": self.task_type,
            "complexity": self.complexity,
            "project_root": self.project_root,
            "agents": self.agents,
            "phases": self.phases,
            "explicit_knowledge_packs": self.explicit_knowledge_packs,
            "explicit_knowledge_docs": self.explicit_knowledge_docs,
            "intervention_level": self.intervention_level,
            "default_model": self.default_model,
            "gate_scope": self.gate_scope,
            "classifier": self.classifier,
            "task_classifier": self.task_classifier,
            "retro_engine": self.retro_engine,
            "policy_engine": self.policy_engine,
            "knowledge_registry": self.knowledge_registry,
            "project_config": self.project_config,
            "bead_store": self.bead_store,
            "registry": self.registry,
            "router": self.router,
            "scorer": self.scorer,
            "budget_tuner": self.budget_tuner,
            "pattern_learner": self.pattern_learner,
        }


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class PlanStrategy(Protocol):
    """Protocol for plan generation strategies."""

    def execute(self, task_summary: str, context: PlanContext) -> MachinePlan:
        """Generate a draft plan based on the objective and context."""
        ...

    def decompose(
        self,
        plan: MachinePlan,
        exc: SubscalePlanError,
        context: PlanContext,
    ) -> MachinePlan:
        """Decompose a subscale plan in response to a SubscalePlanError."""
        ...


# ---------------------------------------------------------------------------
# ZeroShotStrategy / HeuristicStrategy (the Phase-1 strategy)
# ---------------------------------------------------------------------------

class ZeroShotStrategy:
    """Heuristic plan generation using keyword/Haiku-classifier path.

    This is the path ``IntelligentPlanner.create_plan`` takes today when no
    explicit template or refinement is requested.  It wraps the existing
    keyword + Haiku-classifier flow; no new LLM prompts are introduced in
    Phase 1 (that is a Phase 1.5 concern).

    BEAD_DECISION: class naming
      CHOSE: Name the class ``ZeroShotStrategy`` and expose
      ``HeuristicStrategy = ZeroShotStrategy`` as the canonical alias.
      BECAUSE: The Phase 1 skeleton shipped ``ZeroShotStrategy``;
      renaming would break any existing consumers.  The alias is the
      zero-breakage forward path per 005b-phase1-design.md §3.1.

    Public surface (matches ``PlanStrategy`` protocol):
      - ``execute(task_summary, context) -> MachinePlan``
      - ``decompose(plan, exc, context) -> MachinePlan``
    """

    # ------------------------------------------------------------------
    # PlanStrategy.execute
    # ------------------------------------------------------------------

    def execute(self, task_summary: str, context: PlanContext) -> MachinePlan:
        """Generate a draft MachinePlan using heuristics and optional classifier.

        Mirrors the classification + phase-building pipeline that lives
        inside ``IntelligentPlanner.create_plan`` today (steps 1–9b + 12c).
        Knowledge resolution, gating, risk decoration, and depth analysis
        run *after* this method in the analyzer pipeline.

        Args:
            task_summary: Raw task description from the caller.
            context: Full plan context (kwargs + service references).

        Returns:
            A draft ``MachinePlan`` with phases populated.
        """
        from agent_baton.core.engine.classifier import (
            FallbackClassifier,
            _MAX_AGENTS_BY_COMPLEXITY,
            _score_task_type,
        )
        from agent_baton.core.orchestration.router import is_reviewer_agent

        # ------------------------------------------------------------------
        # Step 1: Task ID
        # ------------------------------------------------------------------
        task_id = self._generate_task_id(task_summary)

        # ------------------------------------------------------------------
        # Step 2: Detect stack (best effort)
        # ------------------------------------------------------------------
        stack_profile = None
        if context.project_root is not None and context.router is not None:
            try:
                stack_profile = context.router.detect_stack(context.project_root)
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Step 2b: Parse structured descriptions
        # ------------------------------------------------------------------
        parsed_phases, parsed_agents = self._parse_structured_description(
            task_summary, context
        )
        # Merge into context inputs (local copies — do not mutate context)
        phases = context.phases
        agents = context.agents
        if parsed_phases is not None:
            phases = parsed_phases
        if parsed_agents is not None and agents is None:
            agents = parsed_agents

        task_type = context.task_type
        complexity = context.complexity

        # ------------------------------------------------------------------
        # Step 3: Classify task type / complexity / agents / phases
        # ------------------------------------------------------------------
        # Ensure the classifier always receives a valid registry.  When
        # context.registry is None (e.g. in unit tests) we create a minimal
        # one so the classifier can enumerate registered agents.
        effective_registry = context.registry
        if effective_registry is None:
            try:
                from agent_baton.core.orchestration.registry import AgentRegistry
                _reg = AgentRegistry()
                _reg.load_default_paths()
                effective_registry = _reg
            except Exception:
                pass  # Stay None; classifier will degrade gracefully if possible

        classified_phases: list[str] | None = None
        _use_classifier_path = (
            task_type is None and agents is None and phases is None and complexity is None
        )
        _classifier_succeeded = False
        if _use_classifier_path:
            task_classifier = context.task_classifier or FallbackClassifier()
            try:
                task_cls = task_classifier.classify(
                    task_summary, effective_registry, context.project_root
                )
                inferred_type = task_cls.task_type
                inferred_complexity = task_cls.complexity
                resolved_agents = list(task_cls.agents)
                classified_phases = list(task_cls.phases)
                _classifier_succeeded = True
                logger.debug(
                    "Task classified: type=%s complexity=%s agents=%s phases=%s source=%s",
                    inferred_type,
                    inferred_complexity,
                    resolved_agents,
                    classified_phases,
                    task_cls.source,
                )
            except Exception:
                # Classifier failed (e.g. no registry available in CI) — fall
                # through to the keyword heuristic path.
                logger.debug(
                    "Task classifier failed for %r — falling back to keyword path",
                    task_summary,
                    exc_info=True,
                )

        if not _use_classifier_path or not _classifier_succeeded:
            inferred_type = task_type or self._infer_task_type(task_summary)
            inferred_complexity = complexity or "medium"
            classified_phases = None
            if agents is None:
                resolved_agents = list(_DEFAULT_AGENTS.get(inferred_type, []))
            else:
                resolved_agents = list(agents)
                _override_reviewers = [
                    a for a in resolved_agents if is_reviewer_agent(a)
                ]
                if _override_reviewers:
                    logger.warning(
                        "--agents override includes reviewer-class agent(s) %s; "
                        "they will be excluded from implement-phase team steps "
                        "(reviewers belong in review/gate phases only)",
                        _override_reviewers,
                    )
            logger.debug(
                "Task classification (override path): type=%s complexity=%s agents=%s",
                inferred_type,
                inferred_complexity,
                resolved_agents,
            )

        # ------------------------------------------------------------------
        # Step 4: Pattern lookup — only if classifier didn't provide agents
        # ------------------------------------------------------------------
        pattern: "LearnedPattern | None" = None
        if (
            not _classifier_succeeded
            and agents is None
            and phases is None
            and context.pattern_learner is not None
        ):
            try:
                stack_key = (
                    f"{stack_profile.language}/{stack_profile.framework}"
                    if stack_profile and stack_profile.framework
                    else (stack_profile.language if stack_profile else None)
                )
                candidates = context.pattern_learner.get_patterns_for_task(
                    inferred_type, stack=stack_key
                )
                for cand in candidates:
                    if cand.confidence >= _MIN_PATTERN_CONFIDENCE:
                        pattern = cand
                        resolved_agents = list(pattern.recommended_agents)
                        break
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Step 5b: Retrospective feedback (agent-level filtering)
        # ------------------------------------------------------------------
        if context.retro_engine is not None:
            try:
                retro_feedback = context.retro_engine.load_recent_feedback()
                if retro_feedback is not None and retro_feedback.has_feedback():
                    resolved_agents = self._apply_retro_feedback(
                        resolved_agents, retro_feedback
                    )
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Step 5c: Compound subtask decomposition
        # ------------------------------------------------------------------
        _subtask_data: list[dict] | None = None
        if phases is None:
            subtasks = self._parse_subtasks(task_summary)
            if len(subtasks) >= 2:
                _subtask_data = []
                _explicit_agents = list(agents) if agents is not None else None
                for sub_idx, sub_text in subtasks:
                    st_type = self._infer_task_type(sub_text)
                    if _explicit_agents is not None:
                        st_agents = list(_explicit_agents)
                    else:
                        st_agents = list(_DEFAULT_AGENTS.get(st_type, ["backend-engineer"]))
                        st_agents = list(_expand_agents_for_concerns(st_agents, sub_text))
                    _subtask_data.append({
                        "index": sub_idx,
                        "text": sub_text,
                        "task_type": st_type,
                        "agents": st_agents,
                    })
                union_agents: list[str] = []
                for st in _subtask_data:
                    for a in st["agents"]:
                        if a not in union_agents:
                            union_agents.append(a)
                resolved_agents = union_agents

        # ------------------------------------------------------------------
        # Step 5d + 5d-cap: Cross-concern expansion + complexity cap
        # ------------------------------------------------------------------
        if _subtask_data is None:
            resolved_agents = list(
                _expand_agents_for_concerns(resolved_agents, task_summary)
            )

        if agents is None:
            _agent_cap = _MAX_AGENTS_BY_COMPLEXITY.get(inferred_complexity, 5)
            _early_concerns = _parse_concerns(task_summary)
            if _early_concerns and len(_early_concerns) > _agent_cap:
                _agent_cap = len(_early_concerns)
                logger.debug(
                    "Concern-split detected (%d concerns) — raised agent cap "
                    "to %d to keep one specialist per concern (bd-076c).",
                    len(_early_concerns),
                    _agent_cap,
                )
            if len(resolved_agents) > _agent_cap:
                resolved_agents = resolved_agents[:_agent_cap]

        # ------------------------------------------------------------------
        # Step 5e: Pre-routing names for compound phase building
        # ------------------------------------------------------------------
        _pre_routing_agents = list(resolved_agents)

        # ------------------------------------------------------------------
        # Step 6: Route agents
        # ------------------------------------------------------------------
        resolved_agents = self._route_agents(resolved_agents, context)

        # Build route map (base name → routed name) for compound phases
        _agent_route_map = dict(zip(_pre_routing_agents, resolved_agents))
        logger.debug(
            "Agent routing complete: %s",
            _agent_route_map if _agent_route_map else resolved_agents,
        )

        # ------------------------------------------------------------------
        # Step 9: Build phases
        # ------------------------------------------------------------------
        if _subtask_data is not None:
            plan_phases = self._build_compound_phases(
                _subtask_data, _agent_route_map, context
            )
        elif phases is not None:
            plan_phases = self._phases_from_dicts(phases, resolved_agents, task_summary, context)
        elif classified_phases is not None:
            plan_phases = self._build_phases_for_names(
                classified_phases, resolved_agents, task_summary, context
            )
        elif pattern is not None:
            plan_phases = self._apply_pattern(pattern, inferred_type, task_summary)
            plan_phases = self._assign_agents_to_phases(
                plan_phases, resolved_agents, task_summary, context
            )
        elif complexity is not None:
            from agent_baton.core.engine.classifier import KeywordClassifier as _KC
            complexity_phases = _KC()._select_phases(inferred_type, inferred_complexity, _PHASE_NAMES)
            plan_phases = self._build_phases_for_names(
                complexity_phases, resolved_agents, task_summary, context
            )
        else:
            plan_phases = self._default_phases(inferred_type, resolved_agents, task_summary, context)

        logger.info(
            "Plan phases selected for task_id=%s: %s",
            task_id,
            [(p.name, [s.agent_name for s in p.steps]) for p in plan_phases],
        )

        # ------------------------------------------------------------------
        # Step 9b: Enrich phases
        # ------------------------------------------------------------------
        plan_phases = self._enrich_phases(plan_phases, task_summary=task_summary, context=context)

        # ------------------------------------------------------------------
        # Step 12c: Team consolidation
        # ------------------------------------------------------------------
        for phase in plan_phases:
            if self._is_team_phase(phase, task_summary):
                team_step = self._consolidate_team_step(phase)
                phase.steps = [team_step]

        # ------------------------------------------------------------------
        # Budget tier selection
        # ------------------------------------------------------------------
        budget_tier = self._select_budget_tier(
            inferred_type, len(resolved_agents), context
        )

        # ------------------------------------------------------------------
        # Assemble draft MachinePlan
        # ------------------------------------------------------------------
        plan = MachinePlan(
            task_id=task_id,
            task_summary=task_summary,
            phases=plan_phases,
            task_type=inferred_type,
            risk_level="LOW",          # RiskAnalyzer overwrites this
            git_strategy="commit-per-agent",  # RiskAnalyzer overwrites this
            budget_tier=budget_tier,
        )
        # bd-a379: annotate parallel_safe on intra-phase siblings with
        # disjoint allowed_paths so orchestrators can dispatch concurrently.
        annotate_parallel_safe(plan.phases)
        return plan

    # ------------------------------------------------------------------
    # PlanStrategy.decompose
    # ------------------------------------------------------------------

    def decompose(
        self,
        plan: MachinePlan,
        exc: SubscalePlanError,
        context: PlanContext,
    ) -> MachinePlan:
        """Decompose a subscale plan in response to DepthAnalyzer rejection.

        Per 005b-phase1-design.md §5.2:
          - "concern-density" → split the offending phase by concerns using
            ``_split_implement_phase_by_concerns`` from ``_planner_helpers``.
          - "conjunction" → split the offending step into two sequential
            steps with ids ``<phase_id>.1`` and ``<phase_id>.2``.
          - "multi-agent-affinity" → split by concern and promote into a
            team step via ``_consolidate_team_step``.

        Returns the mutated plan.  Never raises — on unknown reason codes
        the plan is returned unchanged with a warning logged.
        """
        # Locate the offending step and its phase
        offending_step: PlanStep | None = None
        offending_phase: PlanPhase | None = None
        for phase in plan.phases:
            for step in phase.steps:
                if step.step_id == exc.step_id:
                    offending_step = step
                    offending_phase = phase
                    break
            if offending_phase is not None:
                break

        if offending_step is None or offending_phase is None:
            logger.warning(
                "decompose: could not locate step %s in plan — returning plan unchanged",
                exc.step_id,
            )
            return plan

        if exc.reason == "concern-density":
            concerns = _parse_concerns(offending_step.task_description or "")
            if concerns:
                candidate_agents = [s.agent_name for s in offending_phase.steps]
                if not candidate_agents:
                    candidate_agents = ["backend-engineer"]
                _split_implement_phase_by_concerns(
                    phase=offending_phase,
                    concerns=concerns,
                    candidate_agents=candidate_agents,
                    task_summary=plan.task_summary,
                    pick_agent_fn=self._pick_agent_for_concern,
                    step_type_fn=_step_type_for_agent,
                )
            else:
                logger.warning(
                    "decompose[concern-density]: no concerns parsed from step %s — unchanged",
                    exc.step_id,
                )

        elif exc.reason == "conjunction":
            # Split the step into two sequential steps by the first matched verb pair.
            desc = offending_step.task_description or ""
            verb_pair = self._find_conjunction_split(desc)
            if verb_pair is not None:
                verb1, verb2 = verb_pair
                # Partition description: everything before " and <verb2>" goes to step 1
                pattern = re.compile(
                    rf"(?i)\s+and\s+{re.escape(verb2)}\b",
                )
                m = pattern.search(desc)
                if m:
                    part1 = desc[: m.start()].strip()
                    part2 = desc[m.start():].strip().lstrip("and ").strip()
                    # Capitalize second part if it starts with the verb
                    if part2.lower().startswith(verb2):
                        part2 = verb2.capitalize() + part2[len(verb2):]
                    step1 = PlanStep(
                        step_id=f"{offending_phase.phase_id}.1",
                        agent_name=offending_step.agent_name,
                        task_description=part1,
                        step_type=offending_step.step_type,
                        knowledge=list(offending_step.knowledge),
                        depends_on=list(offending_step.depends_on or []),
                    )
                    step2 = PlanStep(
                        step_id=f"{offending_phase.phase_id}.2",
                        agent_name=offending_step.agent_name,
                        task_description=part2,
                        step_type=offending_step.step_type,
                        knowledge=list(offending_step.knowledge),
                        depends_on=[step1.step_id],
                    )
                    # Replace the phase's steps list (preserving other steps)
                    new_steps: list[PlanStep] = []
                    for s in offending_phase.steps:
                        if s.step_id == exc.step_id:
                            new_steps.append(step1)
                            new_steps.append(step2)
                        else:
                            new_steps.append(s)
                    offending_phase.steps = new_steps
                else:
                    logger.warning(
                        "decompose[conjunction]: regex split failed for step %s — unchanged",
                        exc.step_id,
                    )
            else:
                logger.warning(
                    "decompose[conjunction]: no verb pair found in step %s — unchanged",
                    exc.step_id,
                )

        elif exc.reason == "multi-agent-affinity":
            # Split by concern first, then consolidate into a team step.
            concerns = _parse_concerns(offending_step.task_description or "")
            candidate_agents = [s.agent_name for s in offending_phase.steps]
            if not candidate_agents:
                candidate_agents = ["backend-engineer"]

            if concerns:
                _split_implement_phase_by_concerns(
                    phase=offending_phase,
                    concerns=concerns,
                    candidate_agents=candidate_agents,
                    task_summary=plan.task_summary,
                    pick_agent_fn=self._pick_agent_for_concern,
                    step_type_fn=_step_type_for_agent,
                )
            else:
                # No clean concern markers — just let the team step merge them.
                pass

            # Promote to team step regardless of whether concerns were found.
            if len(offending_phase.steps) >= 2:
                team_step = self._consolidate_team_step(offending_phase)
                offending_phase.steps = [team_step]

        else:
            logger.warning(
                "decompose: unknown reason code %r for step %s — returning plan unchanged",
                exc.reason,
                exc.step_id,
            )

        return plan

    # ------------------------------------------------------------------
    # Private helpers — task ID and type inference
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_task_id(summary: str) -> str:
        """Create a collision-free task ID.

        Format: ``YYYY-MM-DD-<slug>-<8-char-uuid>``
        The UUID suffix guarantees uniqueness even when two plans are
        created on the same day with identical summaries.

        Extracted verbatim from planner.py 2291–2304.
        """
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")
        slug = slug[:50]
        slug = slug.rstrip("-")
        uid = uuid.uuid4().hex[:8]
        base = f"{date_str}-{slug}" if slug else date_str
        return f"{base}-{uid}"

    @staticmethod
    def _infer_task_type(summary: str) -> str:
        """Infer task type from summary keywords.

        Extracted verbatim from planner.py 2306–2316.
        """
        from agent_baton.core.engine.classifier import _score_task_type
        # Import _TASK_TYPE_KEYWORDS from planner to avoid a second definition;
        # Step 1.4 will move the source of truth here.
        from agent_baton.core.engine.planner import _TASK_TYPE_KEYWORDS
        return _score_task_type(summary, _TASK_TYPE_KEYWORDS)

    # ------------------------------------------------------------------
    # Private helpers — structured description parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_structured_description(
        summary: str,
        context: PlanContext,
    ) -> tuple[list[dict] | None, list[str] | None]:
        """Detect and extract structured phase/agent information from a task summary.

        Extracted verbatim from planner.py 874–995.
        """
        registry = context.registry
        try:
            known_agents: set[str] = set(registry.names) if registry else set()
        except Exception:
            known_agents = set()

        def _detect_agents_in_text(text: str) -> list[str]:
            lower = text.lower()
            found: list[str] = []
            seen: set[str] = set()
            for name in sorted(known_agents, key=len, reverse=True):
                if name in lower and name not in seen:
                    found.append(name)
                    seen.add(name)
            for alias, canonical in sorted(
                _AGENT_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True
            ):
                if alias in lower and canonical not in seen:
                    found.append(canonical)
                    seen.add(canonical)
            return found

        # Pattern 1: "Phase N: ..." or "Step N: ..." labelled segments
        labelled_pattern = re.compile(
            r"(?:phase|step)\s*\d+\s*:",
            re.IGNORECASE,
        )
        labelled_matches = list(labelled_pattern.finditer(summary))
        if len(labelled_matches) >= 2:
            segments: list[str] = []
            for idx, m in enumerate(labelled_matches):
                start = m.start()
                end = (
                    labelled_matches[idx + 1].start()
                    if idx + 1 < len(labelled_matches)
                    else len(summary)
                )
                segments.append(summary[start:end].strip())

            phases_dicts: list[dict] = []
            all_agents: list[str] = []
            seen_agents: set[str] = set()
            for i, seg in enumerate(segments, start=1):
                agents_in_seg = _detect_agents_in_text(seg)
                phases_dicts.append({"name": f"Phase {i}", "agents": agents_in_seg})
                for a in agents_in_seg:
                    if a not in seen_agents:
                        all_agents.append(a)
                        seen_agents.add(a)

            if phases_dicts:
                return phases_dicts, all_agents or None

        # Pattern 2: numbered list "1. ... 2. ..."
        numbered_pattern = re.compile(r"(?:^|\s)(\d+)\.\s+(.+?)(?=\s+\d+\.|$)", re.DOTALL)
        numbered_matches = numbered_pattern.findall(summary)
        if len(numbered_matches) >= 2:
            phases_dicts = []
            all_agents = []
            seen_agents = set()
            for num, text in numbered_matches:
                agents_in_seg = _detect_agents_in_text(text)
                phases_dicts.append({"name": f"Phase {num}", "agents": agents_in_seg})
                for a in agents_in_seg:
                    if a not in seen_agents:
                        all_agents.append(a)
                        seen_agents.add(a)
            if phases_dicts:
                return phases_dicts, all_agents or None

        # Pattern 3: semicolon- or newline-separated clauses with agent hints
        delimiter_pattern = re.compile(r"[;\n]+")
        clauses = [c.strip() for c in delimiter_pattern.split(summary) if c.strip()]
        if len(clauses) >= 2:
            clause_agents: list[list[str]] = [_detect_agents_in_text(c) for c in clauses]
            clauses_with_agents = sum(1 for ca in clause_agents if ca)
            if clauses_with_agents >= 2:
                phases_dicts = []
                all_agents = []
                seen_agents = set()
                for i, (clause, agents_in_clause) in enumerate(
                    zip(clauses, clause_agents), start=1
                ):
                    phases_dicts.append({"name": f"Phase {i}", "agents": agents_in_clause})
                    for a in agents_in_clause:
                        if a not in seen_agents:
                            all_agents.append(a)
                            seen_agents.add(a)
                if phases_dicts:
                    return phases_dicts, all_agents or None

        return None, None

    # ------------------------------------------------------------------
    # Private helpers — compound task decomposition
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_subtasks(summary: str) -> list[tuple[int, str]]:
        """Parse numbered sub-tasks from a compound task description.

        Extracted verbatim from planner.py 2322–2339.
        """
        parts = _SUBTASK_SPLIT.split(summary)
        subtasks: list[tuple[int, str]] = []
        i = 1
        while i + 2 < len(parts):
            index = int(parts[i] or parts[i + 1])
            text = parts[i + 2].strip()
            if text:
                subtasks.append((index, text))
            i += 3
        return subtasks if len(subtasks) >= 2 else []

    @staticmethod
    def _pick_agent_for_concern(
        concern_text: str,
        candidate_agents: list[str],
    ) -> str:
        """Choose the best agent from ``candidate_agents`` for a concern.

        Extracted verbatim from planner.py 2386–2433.
        """
        from agent_baton.core.orchestration.router import is_reviewer_agent

        text_lower = concern_text.lower()
        text_words = set(re.findall(r"\b\w+\b", text_lower))

        _ARCHITECT_BASES = {"architect", "ai-systems-architect"}
        eligible = [
            a for a in candidate_agents
            if not is_reviewer_agent(a)
            and a.split("--")[0] not in _ARCHITECT_BASES
        ]
        if not eligible:
            eligible = [
                a for a in candidate_agents
                if a.split("--")[0] not in _ARCHITECT_BASES
            ]
        if not eligible:
            eligible = list(candidate_agents) or ["backend-engineer"]

        best_agent = eligible[0]
        best_score = -1
        for agent in eligible:
            base = agent.split("--")[0]
            keywords = _CROSS_CONCERN_SIGNALS.get(base, [])
            score = 0
            for kw in keywords:
                if " " in kw:
                    if kw in text_lower:
                        score += 1
                elif kw in text_words:
                    score += 1
            if score > best_score:
                best_score = score
                best_agent = agent
        return best_agent

    # ------------------------------------------------------------------
    # Private helpers — phase building
    # ------------------------------------------------------------------

    def _build_compound_phases(
        self,
        subtask_data: list[dict],
        agent_route_map: dict[str, str],
        context: PlanContext,
    ) -> list[PlanPhase]:
        """Build phases from compound sub-task data with routed agents.

        Extracted verbatim from planner.py 2613–2645.
        """
        phases: list[PlanPhase] = []
        for idx, st in enumerate(subtask_data, start=1):
            phase_name = _SUBTASK_PHASE_NAMES.get(st["task_type"], "Implement")

            steps: list[PlanStep] = []
            for step_idx, agent_base in enumerate(st["agents"], start=1):
                routed_name: str = agent_route_map.get(agent_base) or agent_base
                _desc = self._step_description(phase_name, routed_name, st["text"], context)
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

    def _enrich_phases(
        self,
        phases: list[PlanPhase],
        task_summary: str = "",
        context: PlanContext | None = None,
    ) -> list[PlanPhase]:
        """Post-process phases to add cross-phase context and default deliverables.

        Extracted verbatim from planner.py 2651–2700.
        """
        from agent_baton.core.engine.planner import _derive_expected_outcome

        for phase in phases:
            for step in phase.steps:
                # Cross-phase reference: tell agent what came before
                if phase.phase_id > 1:
                    prev = next(
                        (p for p in phases if p.phase_id == phase.phase_id - 1),
                        None,
                    )
                    if prev and prev.steps:
                        prev_agents = ", ".join(
                            s.agent_name for s in prev.steps
                        )
                        step.task_description += (
                            f" Build on the {prev.name.lower()} output"
                            f" from phase {prev.phase_id} ({prev_agents})."
                        )

                # Default deliverables
                if not step.deliverables:
                    base_agent = step.agent_name.split("--")[0]
                    defaults = _AGENT_DELIVERABLES.get(base_agent)
                    if defaults and not self._agent_has_output_spec(step.agent_name, context):
                        step.deliverables = list(defaults)

                # Wave 3.1 — derive expected_outcome
                if not step.expected_outcome:
                    step.expected_outcome = _derive_expected_outcome(
                        step, task_summary
                    )

        return phases

    def _default_phases(
        self,
        task_type: str,
        agents: list[str],
        task_summary: str = "",
        context: PlanContext | None = None,
    ) -> list[PlanPhase]:
        """Build the default PlanPhase list for a task type.

        Extracted verbatim from planner.py 2961–2969.
        """
        phase_names = _PHASE_NAMES.get(task_type, _DEFAULT_PHASE_NAMES)
        return self._build_phases_for_names(phase_names, agents, task_summary, context)

    @staticmethod
    def _apply_pattern(
        pattern: "LearnedPattern",
        task_type: str,
        task_summary: str = "",
    ) -> list[PlanPhase]:
        """Convert a LearnedPattern into empty PlanPhases.

        Extracted verbatim from planner.py 2971–2984.
        """
        phase_names = _PHASE_NAMES.get(task_type, _DEFAULT_PHASE_NAMES)
        phases: list[PlanPhase] = []
        for idx, name in enumerate(phase_names, start=1):
            phases.append(PlanPhase(phase_id=idx, name=name, steps=[]))
        return phases

    @classmethod
    def _is_blocked_for_phase(cls, agent_name: str, phase_name: str) -> bool:
        """Return True if *agent_name* must not be assigned to *phase_name*.

        Extracted verbatim from planner.py 3033–3040.
        """
        base = agent_name.split("--")[0]
        blocked = _PHASE_BLOCKED_ROLES.get(phase_name.lower(), set())
        return base in blocked

    def _assign_agents_to_phases(
        self,
        phases: list[PlanPhase],
        agents: list[str],
        task_summary: str = "",
        context: PlanContext | None = None,
    ) -> list[PlanPhase]:
        """Distribute agents across phases using affinity-based assignment.

        Extracted verbatim from planner.py 3042–3202.
        """
        if not agents:
            for phase in phases:
                if not phase.steps:
                    _desc = self._step_description(phase.name, "backend-engineer", task_summary, context)
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

        # Pass 1: assign agents to their ideal phases (greedy, first-match)
        for phase in list(remaining_phases):
            ideal_roles = _PHASE_IDEAL_ROLES.get(phase.name.lower(), [])
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

        # Pass 2: assign remaining agents to remaining phases round-robin
        for phase in list(remaining_phases):
            chosen: str | None = None
            skipped: list[str] = []
            while remaining_agents:
                candidate = remaining_agents.pop(0)
                if self._is_blocked_for_phase(candidate, phase.name):
                    skipped.append(candidate)
                    continue
                chosen = candidate
                break
            remaining_agents = skipped + remaining_agents
            if chosen is not None:
                assigned.append((phase, chosen))
                remaining_phases.remove(phase)

        # Pass 3: phases still unassigned — reuse the best-fit agent from pool
        for phase in remaining_phases:
            ideal_roles = _PHASE_IDEAL_ROLES.get(phase.name.lower(), [])
            best = None
            for role in ideal_roles:
                for agent in agents:
                    if agent.split("--")[0] == role and not self._is_blocked_for_phase(agent, phase.name):
                        best = agent
                        break
                if best:
                    break
            if best is None:
                for agent in agents:
                    if not self._is_blocked_for_phase(agent, phase.name):
                        best = agent
                        break
            if best is None:
                best = _PHASE_FALLBACK_AGENT.get(
                    phase.name.lower(), _IMPLEMENT_FALLBACK_AGENT
                )
            assigned.append((phase, best))

        # Pass 4: leftover agents — add to work phases only
        _WORK_PHASES = {"implement", "fix", "draft"}
        for agent in remaining_agents:
            base = agent.split("--")[0]
            best_phase = None
            for phase_name_key, roles in _PHASE_IDEAL_ROLES.items():
                if phase_name_key not in _WORK_PHASES:
                    continue
                if base in roles and not self._is_blocked_for_phase(agent, phase_name_key):
                    best_phase = next(
                        (p for p in phases if p.name.lower() == phase_name_key), None
                    )
                    if best_phase:
                        break
            if best_phase is None:
                for p in phases:
                    if p.name.lower() in _WORK_PHASES and not self._is_blocked_for_phase(agent, p.name):
                        best_phase = p
                        break
            if best_phase is None:
                continue
            assigned.append((best_phase, agent))

        # Build PlanStep objects from assignments
        for phase, agent in sorted(assigned, key=lambda x: x[0].phase_id):
            step_number = len(phase.steps) + 1
            step_id = f"{phase.phase_id}.{step_number}"
            _desc = self._step_description(phase.name, agent, task_summary, context)
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
                _desc = self._step_description(phase.name, agents[0], task_summary, context)
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

    def _build_phases_for_names(
        self,
        phase_names: list[str],
        agents: list[str],
        task_summary: str = "",
        context: PlanContext | None = None,
        start_phase_id: int = 1,
    ) -> list[PlanPhase]:
        """Build PlanPhase objects for a list of names, distributing agents.

        Extracted verbatim from planner.py 3204–3219.
        """
        phases: list[PlanPhase] = [
            PlanPhase(phase_id=idx, name=name, steps=[])
            for idx, name in enumerate(phase_names, start=start_phase_id)
        ]
        return self._assign_agents_to_phases(phases, agents, task_summary, context)

    def _phases_from_dicts(
        self,
        phase_dicts: list[dict],
        agents: list[str],
        task_summary: str = "",
        context: PlanContext | None = None,
    ) -> list[PlanPhase]:
        """Build PlanPhase objects from user-supplied dicts.

        Extracted verbatim from planner.py 3221–3265.
        """
        from agent_baton.models.execution import PlanGate

        phases: list[PlanPhase] = []
        for idx, d in enumerate(phase_dicts, start=1):
            name = d.get("name", f"Phase {idx}")
            phase_agents = d.get("agents", [])
            gate_dict = d.get("gate")
            gate: Any = None
            if gate_dict:
                gate = PlanGate(
                    gate_type=gate_dict.get("gate_type") or gate_dict.get("type", "build"),
                    command=gate_dict.get("command", ""),
                    description=gate_dict.get("description", ""),
                    fail_on=gate_dict.get("fail_on", []),
                )
            steps: list[PlanStep] = []
            for step_idx, agent in enumerate(phase_agents, start=1):
                _desc = self._step_description(name, agent, task_summary, context)
                steps.append(
                    PlanStep(
                        step_id=f"{idx}.{step_idx}",
                        agent_name=agent,
                        task_description=_desc,
                        step_type=_step_type_for_agent(agent, _desc, phase_name=name),
                    )
                )
            phases.append(PlanPhase(phase_id=idx, name=name, steps=steps, gate=gate))

        # If no phase-level agents were provided, distribute the resolved agents
        all_steps_empty = all(not p.steps for p in phases)
        if all_steps_empty and agents:
            return self._assign_agents_to_phases(phases, agents, task_summary, context)

        return phases

    @staticmethod
    def _is_team_phase(phase: PlanPhase, task_summary: str) -> bool:
        """Detect if a phase should use team dispatch.

        Extracted verbatim from planner.py 3416–3448.
        """
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

    @staticmethod
    def _consolidate_team_step(phase: PlanPhase) -> PlanStep:
        """Merge multiple steps in a phase into a single team step.

        Extracted verbatim from planner.py 3450–3528.
        """
        from agent_baton.core.orchestration.router import is_reviewer_agent

        is_implement_phase = phase.name.lower() in ("implement", "fix", "draft", "migrate")
        if is_implement_phase:
            kept_steps: list[PlanStep] = []
            dropped: list[str] = []
            for step in phase.steps:
                if is_reviewer_agent(step.agent_name):
                    dropped.append(step.agent_name)
                    continue
                kept_steps.append(step)
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
        for i, step in enumerate(source_steps):
            role = "lead" if i == 0 else "implementer"
            member_id = f"{phase.phase_id}.1.{chr(97 + i)}"
            members.append(TeamMember(
                member_id=member_id,
                agent_name=step.agent_name,
                role=role,
                task_description=step.task_description,
                model=step.model,
                deliverables=step.deliverables,
            ))
            all_deliverables.extend(step.deliverables)
            for k in step.knowledge:
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

    def _select_budget_tier(
        self,
        task_type: str,
        agent_count: int,
        context: PlanContext,
    ) -> str:
        """Select budget tier, preferring a BudgetTuner recommendation if available.

        Extracted verbatim from planner.py 3642–3661.
        """
        if context.budget_tuner is not None:
            try:
                recs = context.budget_tuner.load_recommendations()
                if recs:
                    for rec in recs:
                        if rec.task_type == task_type:
                            return rec.recommended_tier
            except Exception:
                pass

        # Heuristic fallback
        if agent_count <= 2:
            return "lean"
        if agent_count <= 5:
            return "standard"
        return "full"

    # ------------------------------------------------------------------
    # Private helpers — routing
    # ------------------------------------------------------------------

    @staticmethod
    def _route_agents(
        agents: list[str],
        context: PlanContext,
    ) -> list[str]:
        """Route base agent names to flavored variants where possible.

        Extracted verbatim from planner.py 3534–3560.
        """
        if not agents or context.router is None:
            return agents

        stack = None
        if context.project_root is not None:
            try:
                stack = context.router.detect_stack(context.project_root)
            except Exception:
                pass

        routed: list[str] = []
        for base in agents:
            try:
                resolved = context.router.route(base, stack=stack)
            except Exception:
                resolved = base
            routed.append(resolved)
        return routed

    @staticmethod
    def _apply_retro_feedback(
        agents: list[str],
        feedback: Any,
    ) -> list[str]:
        """Apply retrospective recommendations to the candidate agent list.

        Extracted verbatim from planner.py 3578–3636 (side-effect-free form;
        routing notes are not tracked here — the planner owns _last_routing_notes).
        """
        to_drop = set(feedback.agents_to_drop())

        try:
            from agent_baton.core.learn.overrides import LearnedOverrides
            _learned_drops = LearnedOverrides().get_agent_drops()
            to_drop.update(_learned_drops)
        except Exception:
            pass

        if to_drop:
            filtered = [
                a for a in agents
                if a.split("--")[0] not in to_drop and a not in to_drop
            ]
            if filtered:
                agents = filtered

        return agents

    # ------------------------------------------------------------------
    # Private helpers — step description / agent introspection
    # ------------------------------------------------------------------

    def _step_description(
        self,
        phase_name: str,
        agent_name: str,
        task_summary: str,
        context: PlanContext | None = None,
    ) -> str:
        """Generate a role-specific step description for an agent within a phase.

        Extracted verbatim from planner.py 2901–2959.
        """
        if not task_summary:
            return f"{phase_name} phase — {agent_name}"

        base_agent = agent_name.split("--")[0]
        phase_lower = phase_name.lower()
        expertise = self._agent_expertise_level(agent_name, context)

        if expertise == "expert":
            verb = _PHASE_VERBS.get(phase_lower, phase_name)
            return f"{verb}: {task_summary}."

        agent_templates = _STEP_TEMPLATES.get(base_agent, {})
        template = agent_templates.get(phase_lower)
        if template:
            description = template.format(task=task_summary)
            if expertise == "minimal":
                verb = _PHASE_VERBS.get(phase_lower, phase_name.lower())
                description += (
                    f" Apply sound {verb.lower().split(':')[0].strip()} practices"
                    f" and document your approach."
                )
            return description

        verb = _PHASE_VERBS.get(phase_lower, phase_name)
        base_desc = f"{verb}: {task_summary} (as {agent_name})"
        if expertise == "minimal":
            base_desc += " Document your approach and decisions."
        return base_desc

    @staticmethod
    def _agent_expertise_level(
        agent_name: str,
        context: PlanContext | None = None,
    ) -> str:
        """Assess agent expertise from definition richness.

        Extracted verbatim from planner.py 2842–2858.
        """
        registry = context.registry if context else None
        if registry is None:
            return "minimal"
        agent_def = registry.get(agent_name)
        if agent_def is None:
            return "minimal"
        word_count = len(agent_def.instructions.split())
        return "expert" if word_count > 200 else "standard"

    @staticmethod
    def _agent_has_output_spec(
        agent_name: str,
        context: PlanContext | None = None,
    ) -> bool:
        """Return True if the agent definition already specifies its output format.

        Extracted verbatim from planner.py 2860–2872.
        """
        registry = context.registry if context else None
        if registry is None:
            return False
        agent_def = registry.get(agent_name)
        if agent_def is None:
            return False
        instructions_lower = agent_def.instructions.lower()
        output_markers = ("output format", "when you finish", "return:", "deliverables")
        return any(marker in instructions_lower for marker in output_markers)

    # ------------------------------------------------------------------
    # Private helpers — decompose utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _find_conjunction_split(desc: str) -> tuple[str, str] | None:
        """Return the first (verb1, verb2) conjunction pair found in desc, or None."""
        desc_lower = desc.lower()
        verbs = sorted(_PHASE_VERBS.keys())
        for verb1 in verbs:
            for verb2 in verbs:
                if verb1 == verb2:
                    continue
                pattern = rf"\b{re.escape(verb1)}\s+and\s+{re.escape(verb2)}\b"
                if re.search(pattern, desc_lower):
                    return verb1, verb2
        return None


# ---------------------------------------------------------------------------
# Canonical alias — expose HeuristicStrategy as the preferred public name.
# ZeroShotStrategy is kept for backward compat.
# ---------------------------------------------------------------------------

HeuristicStrategy = ZeroShotStrategy


# ---------------------------------------------------------------------------
# bd-a379: Parallel-safe annotation pass
# ---------------------------------------------------------------------------

def annotate_parallel_safe(phases: list[PlanPhase]) -> None:
    """Annotate each step's ``parallel_safe`` flag in-place.

    A step is marked ``parallel_safe=True`` if and only if:

    1. It has at least one intra-phase sibling whose ``depends_on`` set is
       identical (same prerequisite steps — they can start at the same time).
    2. Every such sibling has an ``allowed_paths`` set that is **disjoint**
       from this step's ``allowed_paths`` (no shared write targets).
    3. Both this step and every sibling have a non-empty ``allowed_paths``
       list (empty paths → unknown scope → conservatively sequential).

    Phase boundaries are respected: only steps within the same
    ``PlanPhase`` are considered siblings.

    Args:
        phases: The list of ``PlanPhase`` objects from the assembled plan.
            Modified in-place; returns ``None``.
    """
    for phase in phases:
        steps = phase.steps
        for step in steps:
            step.parallel_safe = False
            if not step.allowed_paths:
                continue
            dep_set = frozenset(step.depends_on)
            siblings = [
                s for s in steps
                if s.step_id != step.step_id
                and frozenset(s.depends_on) == dep_set
            ]
            if not siblings:
                continue
            if any(not s.allowed_paths for s in siblings):
                continue
            my_paths = set(step.allowed_paths)
            if all(my_paths.isdisjoint(s.allowed_paths) for s in siblings):
                step.parallel_safe = True


# ---------------------------------------------------------------------------
# TemplateStrategy — forward-port placeholder (Phase 1.5)
# ---------------------------------------------------------------------------

class TemplateStrategy:
    """Forward-port placeholder. CLI's --from-template short-circuits before
    create_plan today; future Phase 1.5 will route through this strategy.

    Per 005b-phase1-design.md §3.2.
    """

    def execute(self, task_summary: str, context: PlanContext) -> MachinePlan:
        raise NotImplementedError(
            "TemplateStrategy: deferred to Phase 1.5; see proposals/005b §3.2"
        )

    def decompose(
        self,
        plan: MachinePlan,
        exc: SubscalePlanError,
        context: PlanContext,
    ) -> MachinePlan:
        raise NotImplementedError("TemplateStrategy.decompose: deferred")


# ---------------------------------------------------------------------------
# RefinementStrategy — forward-port placeholder (Phase 1.5)
# ---------------------------------------------------------------------------

class RefinementStrategy:
    """Amends an existing, partially executed plan based on feedback.

    Not implemented anywhere today. Closest is bead-hints application
    (_apply_bead_hints). Phase 1 leaves this as NotImplementedError.

    Per 005b-phase1-design.md §3.3.
    """

    def execute(self, task_summary: str, context: PlanContext) -> MachinePlan:
        raise NotImplementedError(
            "RefinementStrategy: deferred to Phase 1.5; see proposals/005b §3.3"
        )

    def decompose(
        self,
        plan: MachinePlan,
        exc: SubscalePlanError,
        context: PlanContext,
    ) -> MachinePlan:
        raise NotImplementedError("RefinementStrategy.decompose: deferred")
