"""IntelligentPlanner — data-driven execution plan creation.

Creates MachinePlan objects informed by historical patterns (PatternLearner),
per-agent performance scores (PerformanceScorer), and budget recommendations
(BudgetTuner).  All data sources are optional; the planner degrades gracefully
to default heuristics when no historical data is available.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from agent_baton.core.engine.classifier import (
    FallbackClassifier,
    TaskClassification,
    TaskClassifier,
    _MAX_AGENTS_BY_COMPLEXITY,
    _score_task_type,
)
from agent_baton.core.govern.classifier import ClassificationResult, DataClassifier
from agent_baton.core.govern.policy import PolicyEngine, PolicySet, PolicyViolation
from agent_baton.core.improve.scoring import AgentScorecard, PerformanceScorer
from agent_baton.core.learn.budget_tuner import BudgetTuner
from agent_baton.core.learn.pattern_learner import PatternLearner
from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.core.orchestration.router import AgentRouter, StackProfile
from agent_baton.models.enums import GitStrategy, RiskLevel
from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep, TeamMember
from agent_baton.models.feedback import RetrospectiveFeedback
from agent_baton.models.pattern import LearnedPattern

if TYPE_CHECKING:
    from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Risk signal keywords → risk level
# ---------------------------------------------------------------------------

_RISK_SIGNALS: dict[str, RiskLevel] = {
    "production": RiskLevel.HIGH,
    "infrastructure": RiskLevel.HIGH,
    "docker": RiskLevel.HIGH,
    "ci/cd": RiskLevel.HIGH,
    "deploy": RiskLevel.HIGH,
    "terraform": RiskLevel.HIGH,
    "compliance": RiskLevel.HIGH,
    "regulated": RiskLevel.HIGH,
    "audit": RiskLevel.HIGH,
    "migration": RiskLevel.MEDIUM,
    "database": RiskLevel.MEDIUM,
    "schema": RiskLevel.MEDIUM,
    "bash": RiskLevel.MEDIUM,
    "security": RiskLevel.HIGH,
    "authentication": RiskLevel.HIGH,
    "secrets": RiskLevel.HIGH,
}

_RISK_ORDINAL: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def _select_git_strategy(risk: RiskLevel) -> GitStrategy:
    """Return the appropriate git strategy for a given risk level.

    HIGH and CRITICAL risk tasks use branch-per-agent isolation so each
    agent's work can be independently reverted.  MEDIUM and LOW risk tasks
    use the lighter commit-per-agent strategy on a single feature branch.
    """
    if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        return GitStrategy.BRANCH_PER_AGENT
    return GitStrategy.COMMIT_PER_AGENT


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Minimum confidence required to follow a learned pattern
_MIN_PATTERN_CONFIDENCE = 0.7

# Agent health ratings considered "low" — warn the caller
_LOW_HEALTH_RATINGS = {"needs-improvement"}

# Default agents by task type when no pattern is found
_DEFAULT_AGENTS: dict[str, list[str]] = {
    "new-feature": ["architect", "backend-engineer", "test-engineer", "code-reviewer"],
    "bug-fix": ["backend-engineer", "test-engineer"],
    "refactor": ["architect", "backend-engineer", "test-engineer", "code-reviewer"],
    "data-analysis": ["architect", "data-analyst"],
    "documentation": ["architect", "talent-builder", "code-reviewer"],
    "migration": ["architect", "backend-engineer", "test-engineer", "code-reviewer", "auditor"],
    "test": ["test-engineer"],
}

# Phase templates by task type
# Each entry is a list of (phase_name, agents_for_phase) pairs.
# The agent list entries are indices into the final agents list, or role names.
# We represent them as plain phase names; the step assignment is done dynamically.
_PHASE_NAMES: dict[str, list[str]] = {
    "new-feature": ["Design", "Implement", "Test", "Review"],
    "bug-fix": ["Investigate", "Fix", "Test"],
    "refactor": ["Design", "Implement", "Test", "Review"],
    "data-analysis": ["Design", "Implement", "Review"],
    "documentation": ["Research", "Draft", "Review"],
    "migration": ["Design", "Implement", "Test", "Review"],
    "test": ["Implement", "Review"],
}

_DEFAULT_PHASE_NAMES: list[str] = ["Design", "Implement", "Test", "Review"]


# ---------------------------------------------------------------------------
# Stack-aware gate commands — keyed by language
# ---------------------------------------------------------------------------

_STACK_GATE_COMMANDS: dict[str | None, dict[str, str]] = {
    "python": {"test": "pytest --cov", "build": "pytest"},
    "typescript": {"test": "npm test", "build": "npx tsc --noEmit"},
    "javascript": {"test": "npm test", "build": "npm test"},
    "go": {"test": "go test ./...", "build": "go build ./..."},
    "rust": {"test": "cargo test", "build": "cargo build"},
    "java": {"test": "mvn test", "build": "mvn compile"},
    "ruby": {"test": "bundle exec rake test", "build": "bundle exec rake"},
    "kotlin": {"test": "gradle test", "build": "gradle build"},
    "csharp": {"test": "dotnet test", "build": "dotnet build"},
}

# Fallback used when no stack is detected
_DEFAULT_GATE_COMMANDS: dict[str, str] = {"test": "pytest --cov", "build": "pytest"}


# ---------------------------------------------------------------------------
# Cross-concern agent signals — keywords that indicate an agent is needed
# beyond what the primary task_type would suggest.
# ---------------------------------------------------------------------------

_CROSS_CONCERN_SIGNALS: dict[str, list[str]] = {
    "frontend-engineer": [
        "ux", "ui", "navigate", "browser", "visual", "layout",
        "css", "component", "react", "frontend",
    ],
    "backend-engineer": [
        "api", "endpoint", "server", "database", "migration", "backend",
        "fix", "bug", "broken", "error", "remediate", "patch",
    ],
    "test-engineer": [
        "test suite", "e2e", "playwright", "coverage", "vitest",
        "jest", "unit test", "integration test",
    ],
    "code-reviewer": [
        "review", "code quality", "audit",
    ],
}


# ---------------------------------------------------------------------------
# Compound task decomposition — sub-task phase name mapping
# ---------------------------------------------------------------------------

_SUBTASK_PHASE_NAMES: dict[str, str] = {
    "test": "Test",
    "bug-fix": "Fix",
    "new-feature": "Implement",
    "refactor": "Refactor",
    "migration": "Migrate",
    "data-analysis": "Analyze",
    "documentation": "Document",
}

# Regex to split numbered sub-tasks: (1), 1., or 1)
_SUBTASK_SPLIT = re.compile(
    r"(?:^|(?<=\s))(?:\((\d+)\)|(\d+)[.\)])\s+",
)

# Maps phase names (lower-cased) to human-readable action verbs for step descriptions
_PHASE_VERBS: dict[str, str] = {
    "research": "Explore and document",
    "investigate": "Explore and document",
    "design": "Design the approach for",
    "implement": "Implement",
    "fix": "Fix",
    "draft": "Draft",
    "test": "Write tests to verify",
    "review": "Review the implementation of",
}

# Agent+phase-specific description templates. {task} is replaced with task_summary.
# Only common combinations need entries; _PHASE_VERBS handles the rest.
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

# Default deliverables by agent base name — used when step has no explicit deliverables
# and the agent definition does not already specify output format.
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

# Keyword sets for task type inference.
#
# Matching uses _score_task_type() which counts word-boundary keyword hits
# per type and picks the highest scorer.  Ties are broken by list order
# (earlier = higher priority).
#
# Ordering strategy: new-feature first (safest default when scores are
# tied), then specific intents, then domain-specific types, then test
# and documentation last (most prone to false positives from incidental
# keywords in feature descriptions).
#
# Each keyword should be distinctive enough that a word-boundary match
# is a genuine signal for the task type.  Avoid overly generic words
# (e.g. "error" alone is not a strong bug-fix signal — "error handling"
# is often a feature; "dashboard" alone is not analysis — it could be
# a UI feature).
_TASK_TYPE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("new-feature", ["add", "build", "create", "implement", "feature", "develop",
                      "introduce", "wire", "integrate", "extend"]),
    ("bug-fix", ["fix", "bug", "broken", "error", "crash", "traceback", "exception",
                  "patch", "regression", "fails", "failing"]),
    ("migration", ["migrate", "migration", "upgrade", "move"]),
    ("refactor", ["refactor", "clean up", "reorganize", "restructure", "rename",
                   "cleanup", "simplify", "decouple", "extract"]),
    ("data-analysis", ["analyze", "analyse", "analytics", "report", "dashboard",
                        "query", "insight", "metric", "kpi", "data exploration"]),
    ("test", ["test suite", "tests for", "testing", "test coverage", "e2e test",
              "unit test", "integration test", "playwright", "pytest"]),
    ("documentation", ["document", "documentation", "readme", "adr", "spec",
                        "wiki", "summarize", "write docs"]),
]


# ---------------------------------------------------------------------------
# Protocol for retrospective engine (avoids coupling to concrete class)
# ---------------------------------------------------------------------------

class RetroEngine(Protocol):
    """Structural type for any object that provides retrospective feedback.

    Decouples the planner from the concrete ``RetrospectiveEngine`` class
    so the planner can be tested without the full retrospective subsystem.
    The planner calls ``load_recent_feedback()`` during plan creation to
    apply closed-loop learning: dropping agents with poor track records
    and surfacing knowledge gaps from prior executions.
    """

    def load_recent_feedback(self, limit: int = ...) -> RetrospectiveFeedback: ...


# ---------------------------------------------------------------------------
# IntelligentPlanner
# ---------------------------------------------------------------------------

class IntelligentPlanner:
    """Creates execution plans informed by historical patterns, scores, and budgets.

    This replaces ad-hoc planning in the orchestrator prompt with data-driven
    decisions.  When no historical data exists the planner returns sensible
    defaults; as usage data accumulates the plans become progressively smarter.

    The planner consults five data sources (all optional, graceful degradation):

    1. ``PatternLearner`` -- learned patterns from prior executions.
    2. ``PerformanceScorer`` -- per-agent health ratings.
    3. ``BudgetTuner`` -- budget tier recommendations by task type.
    4. ``RetrospectiveEngine`` -- closed-loop feedback (drop/prefer agents).
    5. ``KnowledgeRegistry`` -- per-step knowledge attachment resolution.

    Usage::

        planner = IntelligentPlanner()
        plan = planner.create_plan("Add OAuth2 login to the API")
        print(planner.explain_plan(plan))

    Attributes:
        _pattern_learner: Finds high-confidence patterns matching the task
            type and stack to guide agent selection and phase templates.
        _scorer: Evaluates agent performance to warn about low-health agents.
        _budget_tuner: Recommends budget tiers based on task type history.
        _registry: Agent registry for resolving definitions and flavors.
        _router: Routes base agent names to stack-specific flavored variants.
        _classifier: Optional data classifier for sensitivity assessment.
        _policy_engine: Optional policy engine for guardrail validation.
        knowledge_registry: Optional knowledge registry for per-step
            knowledge resolution.  When None, the knowledge resolution
            step is skipped entirely.
    """

    def __init__(
        self,
        team_context_root: Path | None = None,
        classifier: DataClassifier | None = None,
        policy_engine: PolicyEngine | None = None,
        retro_engine: RetroEngine | None = None,
        knowledge_registry: KnowledgeRegistry | None = None,
        task_classifier: TaskClassifier | None = None,
        bead_store=None,  # BeadStore | None (F4 planning capture, F7 BeadAnalyzer)
    ) -> None:
        self._team_context_root = team_context_root
        self._pattern_learner = PatternLearner(team_context_root)
        self._scorer = PerformanceScorer()
        self._budget_tuner = BudgetTuner(team_context_root)
        registry = AgentRegistry()
        registry.load_default_paths()
        self._registry = registry
        self._router = AgentRouter(registry)

        # Optional governance subsystem — both are safe to leave as None
        self._classifier = classifier
        self._policy_engine = policy_engine

        # Optional retrospective engine — provides closed-loop learning feedback.
        self._retro_engine = retro_engine

        # Optional knowledge registry — enables per-step knowledge resolution.
        # When None, the knowledge resolution step is skipped entirely.
        self.knowledge_registry: KnowledgeRegistry | None = knowledge_registry

        # Optional bead store — enables F4 planning decision capture and
        # F7 BeadAnalyzer plan enrichment.
        # Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
        self._bead_store = bead_store

        # Populated during create_plan for use in explain_plan
        self._last_pattern_used: LearnedPattern | None = None
        self._last_score_warnings: list[str] = []
        self._last_routing_notes: list[str] = []
        self._last_retro_feedback: RetrospectiveFeedback | None = None
        self._last_classification: ClassificationResult | None = None
        self._last_policy_violations: list[PolicyViolation] = []

        # Task classifier — determines complexity and agent selection.
        # Uses FallbackClassifier by default (Haiku -> keyword).
        self._task_classifier: TaskClassifier = task_classifier or FallbackClassifier()
        self._last_task_classification: TaskClassification | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_plan(
        self,
        task_summary: str,
        *,
        task_type: str | None = None,
        complexity: str | None = None,
        project_root: Path | None = None,
        agents: list[str] | None = None,
        phases: list[dict] | None = None,
        explicit_knowledge_packs: list[str] | None = None,
        explicit_knowledge_docs: list[str] | None = None,
        intervention_level: str = "low",
        default_model: str | None = None,
    ) -> MachinePlan:
        """Create a complete, data-driven execution plan.

        Steps:
        1. Generate task_id from timestamp + summary slug.
        2. Detect project stack if project_root is given.
        3. Infer or use the provided task_type.
        4. Look for a high-confidence pattern that matches the task_type.
        5. Determine agents list — from explicit override, pattern, or defaults.
        6. Route base agent names to flavored variants.
        7. Classify task sensitivity (DataClassifier if available).
        8. Assess risk — combines classifier output with keyword/structural signals.
        9. Derive git strategy from risk level.
        10. Build phase list — from override, pattern, or defaults.
        11. Check PerformanceScorer; warn about low-scoring agents.
        12. Apply budget tier recommendation if one exists.
        13. Validate agent assignments against policy (PolicyEngine if available).
        14. Add QA gates between phases.
        15. Build shared_context string and return MachinePlan.

        Args:
            task_summary: One-line human description of the task.
            task_type: Override the auto-detected task type.
            project_root: Project directory for stack detection and agent routing.
            agents: Override the agent list; skips pattern/default agent selection.
            phases: Explicit phase definitions as dicts; if given, pattern/default
                    phase logic is skipped.  Each dict must have at minimum a
                    "name" key; optionally "agents" (list of str) and "gate" (dict).
            explicit_knowledge_packs: Pack names supplied via --knowledge-pack.
                    Stored on MachinePlan.explicit_knowledge_packs and used by the
                    knowledge resolver to attach docs globally to all steps.
            explicit_knowledge_docs: File paths supplied via --knowledge.
                    Stored on MachinePlan.explicit_knowledge_docs.
            intervention_level: How aggressively agents escalate knowledge gaps.
                    ``low`` (default) | ``medium`` | ``high``.

        Returns:
            A fully constructed MachinePlan.
        """
        # Reset per-call state
        self._last_pattern_used = None
        self._last_score_warnings = []
        self._last_routing_notes = []
        self._last_classification = None
        self._last_policy_violations = []
        self._last_retro_feedback = None
        self._last_task_classification = None

        # 1. Task ID
        task_id = self._generate_task_id(task_summary)

        # 2. Detect stack (best effort) — needed before agent resolution
        stack_profile = None
        if project_root is not None:
            try:
                stack_profile = self._router.detect_stack(project_root)
            except Exception:
                pass

        # 3. Classify — determines task_type, complexity, agents, and phases.
        # Explicit overrides take precedence over the classifier.
        # When complexity is explicitly provided, the caller is overriding
        # classification — use the keyword path so phases are scaled to
        # match the explicit complexity rather than the classifier's guess.
        classified_phases: list[str] | None = None
        if task_type is None and agents is None and phases is None and complexity is None:
            task_cls = self._task_classifier.classify(
                task_summary, self._registry, project_root
            )
            self._last_task_classification = task_cls
            inferred_type = task_cls.task_type
            inferred_complexity = task_cls.complexity
            resolved_agents = list(task_cls.agents)
            classified_phases = list(task_cls.phases)
        else:
            inferred_type = task_type or self._infer_task_type(task_summary)
            inferred_complexity = complexity or "medium"
            classified_phases = None  # let downstream logic handle phases
            # 5. Agent selection (legacy path for explicit overrides)
            if agents is None:
                resolved_agents = list(_DEFAULT_AGENTS.get(inferred_type, []))
            else:
                resolved_agents = list(agents)

        # 4. Pattern lookup — only if classifier didn't provide agents
        pattern: LearnedPattern | None = None
        if not self._last_task_classification and not agents and not phases:
            try:
                stack_key = (
                    f"{stack_profile.language}/{stack_profile.framework}"
                    if stack_profile and stack_profile.framework
                    else (stack_profile.language if stack_profile else None)
                )
                candidates = self._pattern_learner.get_patterns_for_task(
                    inferred_type, stack=stack_key
                )
                for cand in candidates:
                    if cand.confidence >= _MIN_PATTERN_CONFIDENCE:
                        pattern = cand
                        self._last_pattern_used = pattern
                        # Override agents from pattern
                        resolved_agents = list(pattern.recommended_agents)
                        break
            except Exception:
                pass

        # 4b. F7 — BeadAnalyzer: mine historical beads for plan structure hints.
        # Runs after pattern lookup so it can complement (not override) patterns.
        # Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
        _bead_hints: list = []
        if self._bead_store is not None:
            try:
                from agent_baton.core.learn.bead_analyzer import BeadAnalyzer
                _bead_hints = BeadAnalyzer().analyze(
                    self._bead_store, task_description=task_summary
                )
            except Exception:
                _bead_hints = []

        # 5b. Retrospective feedback — filter dropped agents and record gaps.
        # This is consulted before routing so the feedback applies to base names.
        # Violations are soft: dropped agents are removed but the plan is not
        # blocked; knowledge gaps are noted in shared_context only.
        retro_feedback: RetrospectiveFeedback | None = None
        if self._retro_engine is not None:
            try:
                retro_feedback = self._retro_engine.load_recent_feedback()
                self._last_retro_feedback = retro_feedback
            except Exception:
                pass

        if retro_feedback is not None and retro_feedback.has_feedback():
            resolved_agents = self._apply_retro_feedback(resolved_agents, retro_feedback)

        # 5c. Compound task decomposition — detect numbered sub-tasks and
        # build independent per-subtask agent rosters.  Only activates when
        # no explicit phases were provided and ≥2 numbered items are found.
        _subtask_data: list[dict] | None = None
        if phases is None:
            subtasks = self._parse_subtasks(task_summary)
            if len(subtasks) >= 2:
                _subtask_data = []
                for sub_idx, sub_text in subtasks:
                    st_type = self._infer_task_type(sub_text)
                    st_agents = list(_DEFAULT_AGENTS.get(st_type, ["backend-engineer"]))
                    st_agents = self._expand_agents_for_concerns(st_agents, sub_text)
                    _subtask_data.append({
                        "index": sub_idx,
                        "text": sub_text,
                        "task_type": st_type,
                        "agents": st_agents,
                    })
                # Override resolved_agents with the union of all sub-task agents
                union_agents: list[str] = []
                for st in _subtask_data:
                    for a in st["agents"]:
                        if a not in union_agents:
                            union_agents.append(a)
                resolved_agents = union_agents

        # 5d. Cross-concern agent expansion — when no compound decomposition
        # occurred, still expand the roster based on description keywords.
        if _subtask_data is None:
            resolved_agents = self._expand_agents_for_concerns(
                resolved_agents, task_summary,
            )

        # 5d-cap. Enforce complexity-tier agent cap so that cross-concern
        # expansion (or a generous classifier) cannot produce unbounded
        # rosters.  The cap matches HaikuClassifier's _MAX_AGENTS_BY_COMPLEXITY.
        # Only applies to automatically-resolved agents — explicit user-
        # provided agent lists are not capped.
        if agents is None:
            _agent_cap = _MAX_AGENTS_BY_COMPLEXITY.get(inferred_complexity, 5)
            if len(resolved_agents) > _agent_cap:
                resolved_agents = resolved_agents[:_agent_cap]

        # 5e. Store pre-routing names for compound phase building
        _pre_routing_agents = list(resolved_agents)

        # 6. Route agents
        resolved_agents = self._route_agents(resolved_agents, project_root)

        # 6a. Build route map (base name → routed name) for compound phases
        _agent_route_map = dict(zip(_pre_routing_agents, resolved_agents))

        # 6.5. Resolve knowledge attachments per step (KnowledgeRegistry if available).
        # This runs after routing so step.agent_name reflects the routed variant.
        # Phases and steps are not built yet at this point — knowledge resolution
        # happens after phase building (step 9). We defer it to a post-phase hook
        # at step 9.5 so it can iterate over actual PlanStep objects.
        # (The resolver reference is stored here for use at step 9.5 below.)
        _resolver = None
        if self.knowledge_registry is not None:
            from agent_baton.core.engine.knowledge_resolver import KnowledgeResolver
            _resolver = KnowledgeResolver(
                self.knowledge_registry,
                agent_registry=self._registry,
                rag_available=self._detect_rag(),
                step_token_budget=32_000,
                doc_token_cap=8_000,
            )

        # 7. Classify task sensitivity (DataClassifier if available)
        classification: ClassificationResult | None = None
        if self._classifier is not None:
            try:
                classification = self._classifier.classify(task_summary)
                self._last_classification = classification
            except Exception:
                pass

        # 8. Risk — combines DataClassifier output with keyword/structural signals.
        # The classifier's risk level is the floor; keyword/structural signals can
        # raise it further but cannot lower it below what the classifier detected.
        keyword_risk_level = self._assess_risk(task_summary, resolved_agents)
        if classification is not None:
            # Take the higher of the two assessments
            classifier_ordinal = _RISK_ORDINAL[classification.risk_level]
            keyword_ordinal = _RISK_ORDINAL[RiskLevel(keyword_risk_level)]
            if classifier_ordinal > keyword_ordinal:
                risk_level = classification.risk_level.value
            else:
                risk_level = keyword_risk_level
        else:
            risk_level = keyword_risk_level
        risk_level_enum = RiskLevel(risk_level)

        # 8b. Git strategy — derived from risk
        git_strategy = _select_git_strategy(risk_level_enum).value

        # 9. Build phases
        if _subtask_data is not None:
            # Compound task — each sub-task becomes its own phase
            plan_phases = self._build_compound_phases(
                _subtask_data, _agent_route_map,
            )
        elif phases is not None:
            plan_phases = self._phases_from_dicts(phases, resolved_agents, task_summary)
        elif classified_phases is not None:
            # Use classifier-provided phase names
            plan_phases = self._build_phases_for_names(
                classified_phases, resolved_agents, task_summary
            )
        elif pattern is not None:
            plan_phases = self._apply_pattern(pattern, inferred_type, task_summary)
            # Apply routed agent names to pattern-derived phases
            plan_phases = self._assign_agents_to_phases(plan_phases, resolved_agents, task_summary)
        elif complexity is not None:
            # Explicit complexity override — scale phases to match.
            # Use KeywordClassifier phase scaling so light/heavy produces
            # the right number of phases even in the legacy path.
            from agent_baton.core.engine.classifier import KeywordClassifier as _KC
            complexity_phases = _KC()._select_phases(inferred_type, inferred_complexity, _PHASE_NAMES)
            plan_phases = self._build_phases_for_names(complexity_phases, resolved_agents, task_summary)
        else:
            plan_phases = self._default_phases(inferred_type, resolved_agents, task_summary)

        # 9b. Enrich steps with cross-phase context and default deliverables
        plan_phases = self._enrich_phases(plan_phases)

        # 9.5. Resolve knowledge attachments for each step.
        # Runs after phase building so step.agent_name and task_description are final.
        # explicit_knowledge_packs/docs come from create_plan args (CLI --knowledge flags).
        if _resolver is not None:
            for phase in plan_phases:
                for step in phase.steps:
                    try:
                        step.knowledge = _resolver.resolve(
                            agent_name=step.agent_name,
                            task_description=step.task_description,
                            task_type=inferred_type,
                            risk_level=risk_level,
                            explicit_packs=explicit_knowledge_packs or [],
                            explicit_docs=explicit_knowledge_docs or [],
                        )
                    except Exception:
                        logger.debug(
                            "Knowledge resolution failed for step %s — skipping",
                            step.step_id,
                            exc_info=True,
                        )

        # 9.6. Gap-suggested attachments — query pattern learner for prior gaps
        # matching each step's agent + task type. Only runs when both resolver
        # and pattern learner are available.
        if _resolver is not None and self._pattern_learner is not None:
            for phase in plan_phases:
                for step in phase.steps:
                    try:
                        prior_gaps = self._pattern_learner.knowledge_gaps_for(
                            step.agent_name, inferred_type
                        )
                        for gap in prior_gaps:
                            matches = _resolver.resolve(
                                agent_name=step.agent_name,
                                task_description=gap.description,
                            )
                            existing_paths = {a.path for a in step.knowledge if a.path}
                            for match in matches:
                                if match.path and match.path in existing_paths:
                                    continue
                                match.source = "gap-suggested"
                                step.knowledge.append(match)
                                if match.path:
                                    existing_paths.add(match.path)
                    except Exception:
                        logger.debug(
                            "Gap-suggested resolution failed for step %s — skipping",
                            step.step_id,
                            exc_info=True,
                        )

        # 10. Score check — warn about low-health agents
        self._check_agent_scores(resolved_agents)

        # 11. Budget tier
        budget_tier = self._select_budget_tier(inferred_type, len(resolved_agents))

        # 11b. Policy validation — check agent assignments against active policy set.
        # Violations are recorded as warnings; they never hard-block plan creation.
        if self._policy_engine is not None:
            try:
                preset_name = self._classify_to_preset_key(classification)
                policy_set = self._policy_engine.load_preset(preset_name)
                if policy_set is not None:
                    self._last_policy_violations = self._validate_agents_against_policy(
                        resolved_agents, policy_set, plan_phases
                    )
                    # Enforce structural require_agent rules by injecting missing
                    # required agents into the plan's shared context as warnings.
                    # (We cannot silently add phases here — the user decides.)
            except Exception:
                pass

        # 12. Add QA gates (stack-aware)
        for phase in plan_phases:
            if phase.gate is None:
                phase.gate = self._default_gate(phase.name, stack=stack_profile)

        # 12b. Set approval gates on critical phases for HIGH+ risk
        if risk_level_enum in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            for phase in plan_phases:
                if phase.name.lower() in ("design", "research"):
                    phase.approval_required = True
                    phase.approval_description = (
                        f"Review {phase.name.lower()} output before "
                        f"implementation begins. Approve to continue, "
                        f"reject to stop, or approve-with-feedback to "
                        f"add remediation steps."
                    )

        # 12c. Consolidate multi-agent Implement/Fix phases into team steps
        for phase in plan_phases:
            if phase.name.lower() in ("implement", "fix") and len(phase.steps) >= 2:
                phase.steps = [self._consolidate_team_step(phase)]

        # 12d. Apply bead hints from BeadAnalyzer (F7).
        # Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
        if _bead_hints:
            plan_phases = self._apply_bead_hints(plan_phases, _bead_hints)

        # 13. Populate context_files — every agent should read CLAUDE.md
        for phase in plan_phases:
            for step in phase.steps:
                if not step.context_files:
                    step.context_files = ["CLAUDE.md"]

        # 13b. Model inheritance — inherit model preference from agent definition.
        # Priority: agent definition model > explicit default_model > "sonnet".
        for phase in plan_phases:
            for step in phase.steps:
                agent_def = self._registry.get(step.agent_name)
                if agent_def and agent_def.model:
                    step.model = agent_def.model
                elif default_model:
                    step.model = default_model
                # Also propagate to team members
                for member in step.team:
                    member_def = self._registry.get(member.agent_name)
                    if member_def and member_def.model:
                        member.model = member_def.model
                    elif default_model:
                        member.model = default_model

        # 13c. Context richness — extract file paths from task summary and append
        # to every step's context_files (deduplicated).
        extracted_paths = self._extract_file_paths(task_summary)
        if extracted_paths:
            for phase in plan_phases:
                for step in phase.steps:
                    existing = set(step.context_files)
                    for path in extracted_paths:
                        if path not in existing:
                            step.context_files.append(path)
                            existing.add(path)

        # 14. Shared context
        tmp_plan = MachinePlan(
            task_id=task_id,
            task_summary=task_summary,
            risk_level=risk_level,
            budget_tier=budget_tier,
            git_strategy=git_strategy,
            phases=plan_phases,
            pattern_source=pattern.pattern_id if pattern else None,
            task_type=inferred_type,
            explicit_knowledge_packs=list(explicit_knowledge_packs or []),
            explicit_knowledge_docs=list(explicit_knowledge_docs or []),
            intervention_level=intervention_level,
            complexity=inferred_complexity,
            classification_source=(
                self._last_task_classification.source
                if self._last_task_classification
                else "cli-override"
            ),
            detected_stack=(
                f"{stack_profile.language}/{stack_profile.framework}"
                if stack_profile and stack_profile.framework
                else (stack_profile.language if stack_profile else None)
            ),
        )
        # 16. Team cost estimation — look up historical cost data for team steps.
        self._last_team_cost_estimates: dict[str, int] = {}
        for phase in tmp_plan.phases:
            for step in phase.steps:
                if step.team and len(step.team) >= 2:
                    agents = [m.agent_name for m in step.team]
                    estimate = self._pattern_learner.get_team_cost_estimate(agents)
                    if estimate is not None:
                        self._last_team_cost_estimates[step.step_id] = estimate

        shared_context = self._build_shared_context(tmp_plan)
        tmp_plan.shared_context = shared_context

        # F4 — Planning Decision Capture: persist key planner decisions as beads.
        # Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
        if self._bead_store is not None:
            try:
                self._capture_planning_bead(
                    task_id=task_id,
                    content=(
                        f"Plan created for: {task_summary}. "
                        f"Type={inferred_type}, complexity={inferred_complexity}, "
                        f"risk={risk_level}, agents={resolved_agents}, "
                        f"phases={[p.name for p in plan_phases]}, "
                        f"budget_tier={budget_tier}, git_strategy={git_strategy}."
                    ),
                    tags=["planning", "plan-complete", inferred_type],
                )
            except Exception:
                pass

        return tmp_plan

    def explain_plan(self, plan: MachinePlan) -> str:
        """Return a human-readable explanation of why this plan was chosen.

        Includes pattern influence, score warnings, budget tier rationale, and
        routing decisions.

        Args:
            plan: A MachinePlan previously returned by create_plan.

        Returns:
            Multi-line markdown string.
        """
        lines: list[str] = [
            "# Plan Explanation",
            "",
            f"**Task**: {plan.task_summary}",
            f"**Task ID**: {plan.task_id}",
            f"**Risk Level**: {plan.risk_level}",
            f"**Budget Tier**: {plan.budget_tier}",
            f"**Git Strategy**: {plan.git_strategy}",
            "",
        ]

        # Pattern influence
        if plan.pattern_source:
            p = self._last_pattern_used
            if p is not None:
                lines += [
                    "## Pattern Influence",
                    "",
                    f"This plan was shaped by learned pattern **{p.pattern_id}** "
                    f"(confidence {p.confidence:.0%}, {p.sample_size} samples, "
                    f"{p.success_rate:.0%} success rate).",
                    f"Recommended template: *{p.recommended_template}*",
                    "",
                ]
            else:
                lines += [
                    "## Pattern Influence",
                    "",
                    f"Pattern **{plan.pattern_source}** was applied.",
                    "",
                ]
        else:
            lines += [
                "## Pattern Influence",
                "",
                "No pattern with sufficient confidence was found. "
                "Default phase templates were used.",
                "",
            ]

        # Score warnings
        if self._last_score_warnings:
            lines += ["## Score Warnings", ""]
            for w in self._last_score_warnings:
                lines.append(f"- {w}")
            lines.append("")
        else:
            lines += [
                "## Score Warnings",
                "",
                "No performance concerns identified.",
                "",
            ]

        # Routing decisions
        if self._last_routing_notes:
            lines += ["## Agent Routing", ""]
            for note in self._last_routing_notes:
                lines.append(f"- {note}")
            lines.append("")

        # Governance — classification
        if self._last_classification is not None:
            c = self._last_classification
            lines += ["## Data Classification", ""]
            lines.append(f"**Guardrail Preset:** {c.guardrail_preset}")
            lines.append(f"**Confidence:** {c.confidence}")
            if c.signals_found:
                lines.append(f"**Signals:** {', '.join(c.signals_found)}")
            if c.explanation:
                lines.append(f"**Explanation:** {c.explanation}")
            lines.append("")
        else:
            lines += [
                "## Data Classification",
                "",
                "No classifier configured. Risk assessed via keyword signals only.",
                "",
            ]

        # Task classification (complexity / agent selection)
        if self._last_task_classification is not None:
            tc = self._last_task_classification
            lines += [
                "## Task Classification",
                "",
                f"**Source:** {tc.source}",
                f"**Task Type:** {tc.task_type}",
                f"**Complexity:** {tc.complexity}",
                f"**Reasoning:** {tc.reasoning}",
                f"**Selected Agents:** {', '.join(tc.agents)}",
                f"**Selected Phases:** {', '.join(tc.phases)}",
                "",
            ]

        # Governance — policy violations
        if self._last_policy_violations:
            lines += ["## Policy Notes", ""]
            for v in self._last_policy_violations:
                severity_tag = "WARN" if v.rule.severity == "warn" else "POLICY"
                lines.append(f"- [{severity_tag}] **{v.rule.name}**: {v.details}")
            lines.append("")
        else:
            lines += [
                "## Policy Notes",
                "",
                "No policy violations detected.",
                "",
            ]

        # Team cost estimates
        if hasattr(self, '_last_team_cost_estimates') and self._last_team_cost_estimates:
            lines += ["## Team Cost Estimates", ""]
            for step_id, estimate in sorted(self._last_team_cost_estimates.items()):
                lines.append(f"- Step {step_id}: ~{estimate:,} tokens (historical average)")
            total_team = sum(self._last_team_cost_estimates.values())
            lines.append(f"- **Total team cost estimate:** ~{total_team:,} tokens")
            lines.append("")

        # Phase summary
        lines += ["## Phase Summary", ""]
        for phase in plan.phases:
            agent_names = [s.agent_name for s in phase.steps]
            gate_label = f" → gate: {phase.gate.gate_type}" if phase.gate else ""
            cost_label = ""
            for step in phase.steps:
                if hasattr(self, '_last_team_cost_estimates'):
                    est = self._last_team_cost_estimates.get(step.step_id)
                    if est:
                        cost_label = f" (~{est:,} tokens)"
            lines.append(
                f"- **Phase {phase.phase_id} — {phase.name}**: "
                f"{', '.join(agent_names) or '(no agents)'}{gate_label}{cost_label}"
            )
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers — bead capture and hint application (F4, F7)
    # ------------------------------------------------------------------

    def _capture_planning_bead(
        self,
        task_id: str,
        content: str,
        tags: list[str] | None = None,
    ) -> None:
        """Write a planning bead to the bead store.

        Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

        Called during ``create_plan()`` to capture key planning decisions as
        durable beads.  Silently no-ops when ``_bead_store`` is not set.

        Args:
            task_id: Task ID of the plan being created.
            content: The planning decision or observation to record.
            tags: Optional semantic tags for retrieval.
        """
        if self._bead_store is None:
            return
        try:
            from datetime import datetime, timezone
            from agent_baton.models.bead import Bead, _generate_bead_id
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                existing_count = len(
                    self._bead_store.query(task_id=task_id, limit=10000)
                )
            except Exception:
                existing_count = 0
            bead_id = _generate_bead_id(task_id, "planning", content, timestamp, existing_count)
            bead = Bead(
                bead_id=bead_id,
                task_id=task_id,
                step_id="planning",
                agent_name="planner",
                bead_type="planning",
                content=content,
                confidence="high",
                scope="task",
                tags=tags or ["planning"],
                status="open",
                created_at=timestamp,
                source="planning-capture",
            )
            self._bead_store.write(bead)
        except Exception as exc:
            logger.debug("_capture_planning_bead failed (non-fatal): %s", exc)

    def _apply_bead_hints(
        self,
        plan_phases: list,
        hints: list,
    ) -> list:
        """Apply :class:`~agent_baton.models.pattern.PlanStructureHint` objects to phases.

        Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

        Three hint types are handled:

        - ``add_context_file``: Append the hinted file to every step's
          ``context_files`` (deduplicated).
        - ``add_review_phase``: Insert a review phase before the first
          non-design, non-research phase (idempotent — skipped if a review
          phase already exists).
        - ``add_approval_gate``: Mark the first non-design phase as
          requiring human approval if it is not already gated.

        Args:
            plan_phases: The current list of :class:`~agent_baton.models.execution.PlanPhase`.
            hints: List of :class:`~agent_baton.models.pattern.PlanStructureHint`.

        Returns:
            Possibly-modified list of phases.
        """
        for hint in hints:
            try:
                if hint.hint_type == "add_context_file":
                    file_path = hint.metadata.get("file", "")
                    if file_path:
                        for phase in plan_phases:
                            for step in phase.steps:
                                if file_path not in step.context_files:
                                    step.context_files.append(file_path)

                elif hint.hint_type == "add_review_phase":
                    # Skip if a review phase already exists.
                    has_review = any(
                        p.name.lower() == "review" for p in plan_phases
                    )
                    if not has_review and plan_phases:
                        # Build a minimal review phase using the last agent.
                        last_agent = "code-reviewer"
                        if plan_phases[-1].steps:
                            last_agent = plan_phases[-1].steps[-1].agent_name
                        review_phase = self._build_phases_for_names(
                            ["Review"], [last_agent], "Review bead-flagged concerns"
                        )
                        plan_phases.extend(review_phase)

                elif hint.hint_type == "add_approval_gate":
                    # Add approval_required to the first non-design phase.
                    for phase in plan_phases:
                        if phase.name.lower() not in ("design", "research", "investigate"):
                            if not phase.approval_required:
                                phase.approval_required = True
                                phase.approval_description = (
                                    "Bead analysis detected decision reversals — "
                                    "review before proceeding. "
                                    "Approve to continue, reject to stop."
                                )
                            break
            except Exception as _hint_exc:
                logger.debug(
                    "_apply_bead_hints: hint %s failed (non-fatal): %s",
                    hint.hint_type, _hint_exc,
                )

        return plan_phases

    # ------------------------------------------------------------------
    # Private helpers — task ID and type inference
    # ------------------------------------------------------------------

    def _extract_file_paths(self, text: str) -> list[str]:
        """Extract file path candidates from task summary text.

        Scans for tokens that look like file paths — must contain a ``/``
        or end with a known code/config extension to reduce false positives.

        Returns:
            Deduplicated list of path-like strings found in *text*.
        """
        _CODE_EXTENSIONS = {
            ".py", ".ts", ".md", ".json", ".yaml", ".yml", ".toml",
            ".cfg", ".txt", ".html", ".css", ".js", ".jsx", ".tsx",
        }
        pattern = r'(?:^|[\s(])([a-zA-Z0-9_./-]+(?:\.[a-zA-Z0-9]+|/))'
        candidates = re.findall(pattern, text)
        seen: set[str] = set()
        result: list[str] = []
        for c in candidates:
            last_part = c.split("/")[-1]
            ext_match = "." in last_part and f".{last_part.rsplit('.', 1)[-1]}" in _CODE_EXTENSIONS
            if ("/" in c or ext_match) and c not in seen:
                seen.add(c)
                result.append(c)
        return result

    def _generate_task_id(self, summary: str) -> str:
        """Create a collision-free task ID.

        Format: ``YYYY-MM-DD-<slug>-<8-char-uuid>``
        The UUID suffix guarantees uniqueness even when two plans are
        created on the same day with identical summaries.
        """
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")
        slug = slug[:50]
        slug = slug.rstrip("-")
        uid = uuid.uuid4().hex[:8]
        base = f"{date_str}-{slug}" if slug else date_str
        return f"{base}-{uid}"

    def _infer_task_type(self, summary: str) -> str:
        """Infer task type from summary keywords.

        Returns one of: 'new-feature', 'bug-fix', 'refactor', 'data-analysis',
        'documentation', 'migration', 'test'.  Falls back to 'new-feature' when
        no keywords match.

        Delegates to :func:`_score_task_type` which uses word-boundary
        matching and multi-match scoring.
        """
        return _score_task_type(summary, _TASK_TYPE_KEYWORDS)

    # ------------------------------------------------------------------
    # Private helpers — compound task decomposition
    # ------------------------------------------------------------------

    def _parse_subtasks(self, summary: str) -> list[tuple[int, str]]:
        """Parse numbered sub-tasks from a compound task description.

        Detects patterns like ``(1) ...``, ``1. ...``, ``1) ...`` and returns
        a list of ``(index, text)`` pairs.  Returns empty list if fewer than
        2 sub-tasks are found.
        """
        parts = _SUBTASK_SPLIT.split(summary)
        # split() interleaves: [prefix, group1, group2, text, group1, group2, text, ...]
        subtasks: list[tuple[int, str]] = []
        i = 1
        while i + 2 < len(parts):
            index = int(parts[i] or parts[i + 1])
            text = parts[i + 2].strip()
            if text:
                subtasks.append((index, text))
            i += 3
        return subtasks if len(subtasks) >= 2 else []

    def _expand_agents_for_concerns(
        self,
        agents: list[str],
        text: str,
    ) -> list[str]:
        """Expand agent roster based on cross-concern signals in the description.

        When the description mentions keywords associated with agents not in
        the current roster, those agents are added.  This handles cases like
        ``--task-type test`` where the description also mentions "fix" and "UX".
        """
        text_lower = text.lower()
        text_words = set(re.findall(r"\b\w+\b", text_lower))
        expanded = list(agents)

        for agent_base, keywords in _CROSS_CONCERN_SIGNALS.items():
            # Skip if this agent (or a flavored variant) is already present
            if any(a.split("--")[0] == agent_base for a in expanded):
                continue
            for kw in keywords:
                # Multi-word keywords: use substring matching (specific enough)
                # Single-word keywords: use word-boundary matching to avoid
                # false positives like "ui" matching inside "suite".
                if " " in kw:
                    matched = kw in text_lower
                else:
                    matched = kw in text_words
                if matched:
                    expanded.append(agent_base)
                    break

        return expanded

    def _build_compound_phases(
        self,
        subtask_data: list[dict],
        agent_route_map: dict[str, str],
    ) -> list[PlanPhase]:
        """Build phases from compound sub-task data with routed agents.

        Each sub-task becomes its own phase with independently selected
        agents.  The *agent_route_map* translates base names to their
        stack-flavored variants (e.g. ``backend-engineer`` → ``backend-engineer--python``).
        """
        phases: list[PlanPhase] = []
        for idx, st in enumerate(subtask_data, start=1):
            phase_name = _SUBTASK_PHASE_NAMES.get(st["task_type"], "Implement")

            steps: list[PlanStep] = []
            for step_idx, agent_base in enumerate(st["agents"], start=1):
                routed_name: str = agent_route_map.get(agent_base) or agent_base
                steps.append(
                    PlanStep(
                        step_id=f"{idx}.{step_idx}",
                        agent_name=routed_name,
                        task_description=self._step_description(
                            phase_name, routed_name, st["text"],
                        ),
                    )
                )

            phases.append(PlanPhase(phase_id=idx, name=phase_name, steps=steps))

        return phases

    # ------------------------------------------------------------------
    # Private helpers — phase building
    # ------------------------------------------------------------------

    def _enrich_phases(self, phases: list[PlanPhase]) -> list[PlanPhase]:
        """Post-process phases to add cross-phase context and default deliverables.

        For each step:
        - If the step is in phase 2+, appends a reference to the preceding
          phase so the agent knows what to build on.
        - If the step has no explicit deliverables, populates them from
          ``_AGENT_DELIVERABLES`` based on the agent's base name.
        """
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

                # Default deliverables — skip if agent definition already specifies
                # output format (to avoid duplicating what the agent already knows).
                if not step.deliverables:
                    base_agent = step.agent_name.split("--")[0]
                    defaults = _AGENT_DELIVERABLES.get(base_agent)
                    if defaults and not self._agent_has_output_spec(step.agent_name):
                        step.deliverables = list(defaults)

        return phases

    def _agent_expertise_level(self, agent_name: str) -> str:
        """Assess agent expertise from definition richness.

        Consults the registry to determine how much guidance this agent needs
        in its delegation prompt.

        Returns:
            "expert"   — rich definition (>200 words); agent knows its craft,
                         use minimal task-only description.
            "standard" — has a definition; use the full outcome template.
            "minimal"  — no definition found; use template plus light hints.
        """
        agent_def = self._registry.get(agent_name)
        if agent_def is None:
            return "minimal"
        word_count = len(agent_def.instructions.split())
        return "expert" if word_count > 200 else "standard"

    def _agent_has_output_spec(self, agent_name: str) -> bool:
        """Return True if the agent definition already specifies its output format.

        Checks for common section headers/keywords that indicate the agent
        already knows what to produce.  When True, the planner skips adding
        ``_AGENT_DELIVERABLES`` defaults to avoid duplication.
        """
        agent_def = self._registry.get(agent_name)
        if agent_def is None:
            return False
        instructions_lower = agent_def.instructions.lower()
        output_markers = ("output format", "when you finish", "return:", "deliverables")
        return any(marker in instructions_lower for marker in output_markers)

    def _detect_rag(self) -> bool:
        """Return True if an MCP RAG server is registered in settings.json.

        Checks both the project-local ``.claude/settings.json`` and the global
        ``~/.claude/settings.json`` for MCP server entries whose name contains
        ``rag`` (case-insensitive). Returns False on any read or parse error.
        """
        settings_candidates = [
            Path(".claude/settings.json"),
            Path.home() / ".claude" / "settings.json",
        ]
        for settings_path in settings_candidates:
            if not settings_path.exists():
                continue
            try:
                data = json.loads(settings_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            # MCP servers may live under "mcpServers" (object keyed by name)
            # or "mcp" -> "servers" depending on the Claude version.
            mcp_servers = data.get("mcpServers", data.get("mcp", {}).get("servers", {}))
            if isinstance(mcp_servers, dict):
                for server_name in mcp_servers:
                    if "rag" in str(server_name).lower():
                        return True
        return False

    def _step_description(
        self, phase_name: str, agent_name: str, task_summary: str
    ) -> str:
        """Generate a role-specific step description for an agent within a phase.

        Uses ``_STEP_TEMPLATES`` for agent+phase combinations that have a
        dedicated template, falling back to ``_PHASE_VERBS`` for unknown
        combinations.  Prompt weight scales with agent expertise level:

        - **expert** agents (rich definitions, >200 words) receive just the
          outcome phrase — their definition already carries the methodology.
        - **standard** agents receive the full outcome template.
        - **minimal** agents (no definition) receive the template plus a brief
          method hint so they have enough guidance to proceed.

        Examples::

            _step_description("implement", "backend-engineer--python", "Add OAuth2 login")
            # -> "Implement: Add OAuth2 login. Deliver working, tested code."

            _step_description("design", "architect", "Add OAuth2 login")
            # -> "Produce a design for: Add OAuth2 login ..."

        Falls back to ``"<phase> phase — <agent>"`` when ``task_summary`` is empty.
        """
        if not task_summary:
            return f"{phase_name} phase — {agent_name}"

        base_agent = agent_name.split("--")[0]
        phase_lower = phase_name.lower()
        expertise = self._agent_expertise_level(agent_name)

        # Expert agents: minimal task-only description — their definition carries
        # the methodology.  Use verb + task rather than the full template to avoid
        # period-truncation issues when the task summary contains dots.
        if expertise == "expert":
            verb = _PHASE_VERBS.get(phase_lower, phase_name)
            return f"{verb}: {task_summary}."

        # Standard agents: full outcome template
        agent_templates = _STEP_TEMPLATES.get(base_agent, {})
        template = agent_templates.get(phase_lower)
        if template:
            description = template.format(task=task_summary)
            if expertise == "minimal":
                # Append a light method hint so agents without definitions have guidance
                verb = _PHASE_VERBS.get(phase_lower, phase_name.lower())
                description += (
                    f" Apply sound {verb.lower().split(':')[0].strip()} practices"
                    f" and document your approach."
                )
            return description

        # Fallback to generic verb + task
        verb = _PHASE_VERBS.get(phase_lower, phase_name)
        base_desc = f"{verb}: {task_summary} (as {agent_name})"
        if expertise == "minimal":
            base_desc += " Document your approach and decisions."
        return base_desc

    def _default_phases(self, task_type: str, agents: list[str], task_summary: str = "") -> list[PlanPhase]:
        """Build the default PlanPhase list for a task type.

        Phase names come from _PHASE_NAMES.  Agents are assigned to phases
        using affinity matching (see ``_assign_agents_to_phases``), with
        round-robin fallback for unmatched agents/phases.
        """
        phase_names = _PHASE_NAMES.get(task_type, _DEFAULT_PHASE_NAMES)
        return self._build_phases_for_names(phase_names, agents, task_summary)

    def _apply_pattern(self, pattern: LearnedPattern, task_type: str, task_summary: str = "") -> list[PlanPhase]:
        """Convert a LearnedPattern into PlanPhases.

        The pattern provides a template description and recommended agents but
        does not prescribe explicit phase names.  We infer phase names from the
        task_type default template and leave agent assignment to
        _assign_agents_to_phases.
        """
        phase_names = _PHASE_NAMES.get(task_type, _DEFAULT_PHASE_NAMES)
        # Build phases with empty steps; agents will be assigned by the caller
        phases: list[PlanPhase] = []
        for idx, name in enumerate(phase_names, start=1):
            phases.append(PlanPhase(phase_id=idx, name=name, steps=[]))
        return phases

    # Preferred agent roles per phase name — used for affinity-based assignment.
    # Each entry is a priority-ordered list: first match in the agent pool wins.
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

    def _assign_agents_to_phases(
        self, phases: list[PlanPhase], agents: list[str], task_summary: str = ""
    ) -> list[PlanPhase]:
        """Distribute agents across phases using affinity-based assignment.

        Assignment strategy:
        1. Match agents to phases where they are the ideal role (e.g. architect → Design).
        2. Assign remaining agents to remaining phases round-robin.
        3. For phases with no remaining agents, reuse the best-fit agent from the pool.
        4. Distribute any leftover agents to phases where they have affinity.
        5. Guarantee every phase has at least one step.
        """
        if not agents:
            for phase in phases:
                if not phase.steps:
                    phase.steps.append(
                        PlanStep(
                            step_id=f"{phase.phase_id}.1",
                            agent_name="backend-engineer",
                            task_description=self._step_description(
                                phase.name, "backend-engineer", task_summary
                            ),
                        )
                    )
            return phases

        assigned: list[tuple[PlanPhase, str]] = []
        remaining_agents = list(agents)
        remaining_phases = list(phases)

        # Pass 1: assign agents to their ideal phases (greedy, first-match)
        for phase in list(remaining_phases):
            ideal_roles = self._PHASE_IDEAL_ROLES.get(phase.name.lower(), [])
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
            if remaining_agents:
                agent = remaining_agents.pop(0)
                assigned.append((phase, agent))
                remaining_phases.remove(phase)

        # Pass 3: phases still unassigned — reuse the best-fit agent from pool
        for phase in remaining_phases:
            ideal_roles = self._PHASE_IDEAL_ROLES.get(phase.name.lower(), [])
            best = None
            for role in ideal_roles:
                for agent in agents:
                    if agent.split("--")[0] == role:
                        best = agent
                        break
                if best:
                    break
            if best is None:
                best = agents[0]
            assigned.append((phase, best))

        # Pass 4: leftover agents — add to work phases only.
        # Non-work phases (design, research, investigate, review, test) should
        # have at most one agent from Passes 1-3.  Leftover agents are placed
        # only into implementation-like phases (implement, fix, draft) to avoid
        # bloated plans where every agent gets a redundant design/review step.
        _WORK_PHASES = {"implement", "fix", "draft"}
        for agent in remaining_agents:
            base = agent.split("--")[0]
            best_phase = None
            for phase_name, roles in self._PHASE_IDEAL_ROLES.items():
                if phase_name not in _WORK_PHASES:
                    continue
                if base in roles:
                    best_phase = next(
                        (p for p in phases if p.name.lower() == phase_name), None
                    )
                    if best_phase:
                        break
            if best_phase is None:
                # Fall back to the first work phase, or first phase if none
                best_phase = next(
                    (p for p in phases if p.name.lower() in _WORK_PHASES),
                    phases[0],
                )
            assigned.append((best_phase, agent))

        # Build PlanStep objects from assignments
        for phase, agent in sorted(assigned, key=lambda x: x[0].phase_id):
            step_number = len(phase.steps) + 1
            step_id = f"{phase.phase_id}.{step_number}"
            phase.steps.append(
                PlanStep(
                    step_id=step_id,
                    agent_name=agent,
                    task_description=self._step_description(
                        phase.name, agent, task_summary
                    ),
                )
            )

        # Guarantee every phase has at least one step
        for phase in phases:
            if not phase.steps:
                phase.steps.append(
                    PlanStep(
                        step_id=f"{phase.phase_id}.1",
                        agent_name=agents[0],
                        task_description=self._step_description(
                            phase.name, agents[0], task_summary
                        ),
                    )
                )

        return phases

    def _build_phases_for_names(
        self, phase_names: list[str], agents: list[str], task_summary: str = ""
    ) -> list[PlanPhase]:
        """Build PlanPhase objects for a list of names, distributing agents."""
        phases: list[PlanPhase] = [
            PlanPhase(phase_id=idx, name=name, steps=[])
            for idx, name in enumerate(phase_names, start=1)
        ]
        return self._assign_agents_to_phases(phases, agents, task_summary)

    def _phases_from_dicts(
        self, phase_dicts: list[dict], agents: list[str], task_summary: str = ""
    ) -> list[PlanPhase]:
        """Build PlanPhase objects from user-supplied dicts.

        Each dict may have:
        - "name": str (required)
        - "agents": list[str] — per-phase agent override
        - "gate": dict — passed to PlanGate

        If "agents" is absent the resolved_agents list is split round-robin
        across phases.
        """
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
                steps.append(
                    PlanStep(
                        step_id=f"{idx}.{step_idx}",
                        agent_name=agent,
                        task_description=self._step_description(
                            name, agent, task_summary
                        ),
                    )
                )
            phases.append(PlanPhase(phase_id=idx, name=name, steps=steps, gate=gate))

        # If no phase-level agents were provided, distribute the resolved agents
        all_steps_empty = all(not p.steps for p in phases)
        if all_steps_empty and agents:
            return self._assign_agents_to_phases(phases, agents, task_summary)

        return phases

    # ------------------------------------------------------------------
    # Private helpers — gates
    # ------------------------------------------------------------------

    def _default_gate(
        self, phase_name: str, stack: StackProfile | None = None,
    ) -> PlanGate | None:
        """Return an appropriate QA gate for a phase name.

        Gate commands are matched to the detected project stack so that
        TypeScript projects get ``npm test`` instead of ``pytest``, etc.

        - 'Test' → test gate (language-appropriate test runner)
        - 'Investigate', 'Research', 'Review', 'Design' → no automated gate
        - All others (Implement, Fix, etc.) → build check (language-appropriate)
        """
        name_lower = phase_name.lower()
        if name_lower in ("investigate", "research", "review", "design"):
            # No automated gate — these phases don't produce code
            return None

        # Pick gate commands from detected stack, falling back to defaults
        language = stack.language if stack else None
        commands = _STACK_GATE_COMMANDS.get(language, _DEFAULT_GATE_COMMANDS)

        # Merge LearnedOverrides gate command corrections (best-effort).
        # Overrides take precedence over the stack-based defaults when present.
        if language:
            try:
                from agent_baton.core.learn.overrides import LearnedOverrides
                _gate_overrides = LearnedOverrides().get_gate_overrides()
                _lang_gates = _gate_overrides.get(language, {})
                if _lang_gates:
                    commands = dict(commands)  # copy so we don't mutate the module-level dict
                    commands.update(_lang_gates)
            except Exception:
                pass  # Never block planning on a learning failure

        if name_lower == "test":
            return PlanGate(
                gate_type="test",
                command=commands["test"],
                description="Run full test suite with coverage report.",
                fail_on=["test failure", "coverage below threshold"],
            )
        # All other code-producing phases (implement, fix, migrate, etc.)
        return PlanGate(
            gate_type="build",
            command=commands["build"],
            description="Run test suite to verify the implementation builds cleanly.",
            fail_on=["test failure", "import error"],
        )

    @staticmethod
    def _consolidate_team_step(phase: PlanPhase) -> PlanStep:
        """Merge multiple steps in a phase into a single team step.

        The first step's agent becomes the team lead; the rest become
        implementers.  The original step descriptions become member
        task descriptions.
        """
        members: list[TeamMember] = []
        all_deliverables: list[str] = []
        all_knowledge: list = []
        seen_knowledge_paths: set[str] = set()
        for i, step in enumerate(phase.steps):
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
            # Merge knowledge from constituent steps (deduplicated by path)
            for k in step.knowledge:
                key = k.path if k.path else id(k)
                if key not in seen_knowledge_paths:
                    all_knowledge.append(k)
                    seen_knowledge_paths.add(key)

        combined_desc = "; ".join(s.task_description for s in phase.steps)
        return PlanStep(
            step_id=f"{phase.phase_id}.1",
            agent_name="team",
            task_description=f"Team implementation: {combined_desc}",
            team=members,
            deliverables=all_deliverables,
            knowledge=all_knowledge,
        )

    # ------------------------------------------------------------------
    # Private helpers — routing and scoring
    # ------------------------------------------------------------------

    def _route_agents(self, agents: list[str], project_root: Path | None) -> list[str]:
        """Route base agent names to flavored variants where possible.

        Records routing notes for explain_plan.
        """
        if not agents:
            return agents

        stack = None
        if project_root is not None:
            try:
                stack = self._router.detect_stack(project_root)
            except Exception:
                pass

        routed: list[str] = []
        for base in agents:
            try:
                resolved = self._router.route(base, stack=stack)
            except Exception:
                resolved = base
            if resolved != base:
                self._last_routing_notes.append(
                    f"{base} -> {resolved} (stack-matched flavor)"
                )
            routed.append(resolved)
        return routed

    def _check_agent_scores(self, agents: list[str]) -> None:
        """Populate score warnings for any low-health agents."""
        for agent in agents:
            try:
                card: AgentScorecard = self._scorer.score_agent(
                    agent, bead_store=self._bead_store,
                )
            except Exception:
                continue
            if card.health in _LOW_HEALTH_RATINGS:
                self._last_score_warnings.append(
                    f"Agent '{agent}' has health '{card.health}' "
                    f"(first-pass rate {card.first_pass_rate:.0%}, "
                    f"{card.negative_mentions} negative mention(s))."
                )

    def _apply_retro_feedback(
        self,
        agents: list[str],
        feedback: RetrospectiveFeedback,
    ) -> list[str]:
        """Apply retrospective recommendations to the candidate agent list.

        Rules (soft — never hard-block the plan):
        - Agents whose base name appears in ``feedback.agents_to_drop()`` are
          removed from the list.  If removal would empty the list, the original
          list is returned unchanged to ensure the plan remains executable.
        - Agents recommended via ``feedback.agents_to_prefer()`` are not added
          automatically (the planner does not invent agents), but routing notes
          are recorded so ``explain_plan`` can surface them.

        Args:
            agents: The candidate agent list before routing.
            feedback: Aggregated retrospective feedback.

        Returns:
            Filtered agent list (same order, routing notes updated).
        """
        to_drop = set(feedback.agents_to_drop())

        # Merge learned agent drops from LearnedOverrides (best-effort).
        try:
            from agent_baton.core.learn.overrides import LearnedOverrides
            _learned_drops = LearnedOverrides().get_agent_drops()
            to_drop.update(_learned_drops)
        except Exception:
            pass  # Never block planning on a learning failure

        to_prefer = feedback.agents_to_prefer()

        if to_drop:
            filtered = [
                a for a in agents
                if a.split("--")[0] not in to_drop and a not in to_drop
            ]
            if filtered:
                for dropped in to_drop:
                    if any(
                        a.split("--")[0] == dropped or a == dropped
                        for a in agents
                    ):
                        self._last_routing_notes.append(
                            f"{dropped} removed (retrospective recommendation)"
                        )
                agents = filtered
            # else: would empty the list — silently keep the original

        if to_prefer:
            for preferred in sorted(to_prefer):
                self._last_routing_notes.append(
                    f"Retrospective recommends: {preferred} "
                    f"(not auto-added — add manually if desired)"
                )

        return agents

    # ------------------------------------------------------------------
    # Private helpers — budget
    # ------------------------------------------------------------------

    def _select_budget_tier(self, task_type: str, agent_count: int) -> str:
        """Select budget tier, preferring a BudgetTuner recommendation if available.

        Falls back to simple agent-count heuristic when no recommendation exists.
        """
        try:
            recs = self._budget_tuner.load_recommendations()
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
    # Private helpers — risk assessment
    # ------------------------------------------------------------------

    def _assess_risk(self, task_summary: str, agents: list[str]) -> str:
        """Assess risk level from task description and structural signals.

        Combines keyword matching (via _RISK_SIGNALS) with structural
        indicators drawn from the agent list:

        - Agent count: >5 agents raises score to at least MEDIUM.
        - Sensitive agent types (security-reviewer, auditor, devops-*): at
          least MEDIUM.
        - Destructive action verbs in the description: at least MEDIUM.
        - Read-only first-word indicators (review, analyze, inspect, …): caps
          score at LOW when no sensitive agents are present.  This prevents
          false positives such as "Review the production code" being flagged
          HIGH solely because of the word "production".

        Returns:
            One of "LOW", "MEDIUM", or "HIGH".
        """
        # ── Score-based accumulator ──────────────────────────────────────────
        # 0 = LOW, 1 = MEDIUM, 2 = HIGH
        score = 0

        # ── Keyword signals ────────────────────────────────────────────────────
        description_lower = task_summary.lower()
        keyword_risk = RiskLevel.LOW
        for keyword, level in _RISK_SIGNALS.items():
            if keyword in description_lower:
                if _RISK_ORDINAL[level] > _RISK_ORDINAL[keyword_risk]:
                    keyword_risk = level
        keyword_score = min(_RISK_ORDINAL.get(keyword_risk, 0), 2)
        score = max(score, keyword_score)

        # ── Structural signals ────────────────────────────────────────────────

        # Agent count: many agents = higher coordination risk
        if len(agents) > 5:
            score = max(score, 1)

        # Sensitive agent types involved
        _SENSITIVE_AGENTS = {"security-reviewer", "auditor", "devops-engineer"}
        if any(a in _SENSITIVE_AGENTS or a.startswith("devops") for a in agents):
            score = max(score, 1)

        # Destructive action verbs in description
        _DESTRUCTIVE_VERBS = {
            "delete", "remove", "drop", "destroy", "reset",
            "purge", "wipe", "truncate",
        }
        desc_words = set(task_summary.lower().split())
        if desc_words & _DESTRUCTIVE_VERBS:
            score = max(score, 1)

        # ── Read-only dampening ───────────────────────────────────────────────
        # When the first word of the description is a read-only indicator and no
        # sensitive agents are involved, cap the score at LOW.  This prevents
        # false positives like "Review the production code" being flagged HIGH
        # merely because the word "production" appears in the description.
        _READONLY_FIRST_WORDS = {
            "review", "analyze", "analyse", "investigate", "audit",
            "inspect", "check", "examine", "read", "list",
            "show", "report", "summarize",
        }
        desc_lower_words = task_summary.lower().split()
        first_word = desc_lower_words[0] if desc_lower_words else ""
        sensitive_agents_present = any(
            a in _SENSITIVE_AGENTS or a.startswith("devops") for a in agents
        )
        if first_word in _READONLY_FIRST_WORDS and not sensitive_agents_present:
            score = min(score, 0)

        _LEVELS = {0: "LOW", 1: "MEDIUM", 2: "HIGH"}
        return _LEVELS[score]

    # ------------------------------------------------------------------
    # Private helpers — governance
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_to_preset_key(classification: ClassificationResult | None) -> str:
        """Map a ClassificationResult's guardrail_preset string to a PolicyEngine key.

        The DataClassifier uses human-readable preset names; the PolicyEngine
        stores presets under short keys.  This function translates between them.

        Falls back to "standard_dev" when classification is absent.
        """
        if classification is None:
            return "standard_dev"
        name = classification.guardrail_preset
        mapping = {
            "Standard Development": "standard_dev",
            "Data Analysis": "data_analysis",
            "Infrastructure Changes": "infrastructure",
            "Regulated Data": "regulated",
            "Security-Sensitive": "security",
        }
        return mapping.get(name, "standard_dev")

    def _validate_agents_against_policy(
        self,
        agents: list[str],
        policy_set: PolicySet,
        plan_phases: list[PlanPhase],
    ) -> list[PolicyViolation]:
        """Check each agent's assignment against the active policy set.

        Evaluates path_block and tool_restrict rules for every agent/phase step.
        For require_agent rules, checks whether the required agent name is
        present anywhere in the resolved agent list.

        Returns a deduplicated list of PolicyViolation objects.  Violations are
        informational warnings — callers must not treat them as hard failures.
        """
        violations: list[PolicyViolation] = []
        seen: set[str] = set()  # deduplicate identical (agent, rule) pairs

        # Pass 1: per-step path_block and tool_restrict checks.
        # require_agent / require_gate are plan-level concerns handled in pass 2.
        for phase in plan_phases:
            for step in phase.steps:
                agent = step.agent_name
                # Use context_files as a proxy for paths this step touches
                paths = list(step.context_files or [])
                tools: list[str] = []  # tools not tracked at plan time

                if self._policy_engine is None:
                    continue
                step_violations = self._policy_engine.evaluate(
                    policy_set, agent, paths, tools
                )
                for v in step_violations:
                    # Skip require_agent / require_gate from per-step results —
                    # those are handled at the plan level in pass 2 below, which
                    # can correctly determine whether the agent is in the roster.
                    if v.rule.rule_type in ("require_agent", "require_gate"):
                        continue
                    key = f"{v.agent_name}:{v.rule.name}"
                    if key not in seen:
                        seen.add(key)
                        violations.append(v)

        # Pass 2: require_agent rules evaluated once at the plan level.
        # Checks whether the required agent name is present in the full roster.
        for rule in policy_set.rules:
            if rule.rule_type == "require_agent":
                required = rule.pattern
                # Match on full name OR base name (before "--" flavor separator)
                if not any(
                    a == required or a.split("--")[0] == required
                    for a in agents
                ):
                    key = f"plan:{rule.name}"
                    if key not in seen:
                        seen.add(key)
                        violations.append(
                            PolicyViolation(
                                agent_name="plan",
                                rule=rule,
                                details=(
                                    f"Required agent '{required}' is not in the plan roster. "
                                    "Consider adding it to satisfy this policy rule."
                                ),
                            )
                        )

        return violations

    # ------------------------------------------------------------------
    # Private helpers — shared context
    # ------------------------------------------------------------------

    def _build_shared_context(self, plan: MachinePlan) -> str:
        """Build the shared_context string embedded in the plan.

        This is the boilerplate every delegated agent should receive so it
        understands the overall mission and its role in the plan.

        When governance subsystems are active, classification results and
        policy warnings are appended so every agent is aware of applicable
        guardrails.
        """
        agent_list = ", ".join(dict.fromkeys(plan.all_agents))  # deduplicated, ordered
        lines: list[str] = [
            f"Task: {plan.task_summary}",
            f"Risk: {plan.risk_level} | Budget: {plan.budget_tier}",
        ]
        if agent_list:
            lines.append(f"Team: {agent_list}")

        # Governance — classification
        if self._last_classification is not None:
            lines.append(
                f"Guardrail Preset: {self._last_classification.guardrail_preset}"
            )
            if self._last_classification.signals_found:
                lines.append(
                    f"Sensitivity Signals: {', '.join(self._last_classification.signals_found)}"
                )

        # Governance — policy violations (warnings only, user decides)
        if self._last_policy_violations:
            warn_lines = []
            for v in self._last_policy_violations:
                severity_tag = "[WARN]" if v.rule.severity == "warn" else "[POLICY]"
                warn_lines.append(f"  {severity_tag} {v.details}")
            lines.append("Policy Notes:\n" + "\n".join(warn_lines))

        # Retrospective feedback — surface knowledge gaps so agents are aware
        if (
            self._last_retro_feedback is not None
            and self._last_retro_feedback.knowledge_gaps
        ):
            gap_lines = [
                f"  - {g.description}"
                + (f" (fix: {g.suggested_fix})" if g.suggested_fix else "")
                for g in self._last_retro_feedback.knowledge_gaps
            ]
            lines.append(
                "Knowledge Gaps (from recent retrospectives):\n" + "\n".join(gap_lines)
            )

        # Team cost estimates — budget awareness for agents
        if hasattr(self, '_last_team_cost_estimates') and self._last_team_cost_estimates:
            budget_thresholds = {"lean": 50_000, "standard": 500_000, "full": 2_000_000}
            budget_limit = budget_thresholds.get(plan.budget_tier, 500_000)
            total_team_cost = sum(self._last_team_cost_estimates.values())
            budget_pct = (total_team_cost / budget_limit * 100) if budget_limit > 0 else 0
            lines.append(
                f"Team Cost Estimate: ~{total_team_cost:,} tokens "
                f"({budget_pct:.0f}% of {plan.budget_tier} budget)"
            )

        return "\n".join(lines)
