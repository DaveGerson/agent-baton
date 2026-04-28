"""Analyzer Pipeline for validating MachinePlans.

This module owns the four plan-validation analyzers that run after a draft
MachinePlan has been assembled by a PlanStrategy:

  - DependencyAnalyzer: DAG ordering and step-id integrity (warning-only).
  - RiskAnalyzer: Data classification, risk-merge, git-strategy derivation,
    and HIGH+ approval gating on Design/Research phases.
  - CapabilityAnalyzer: Agent routing, pattern application, retro feedback,
    score check, and policy validation.
  - DepthAnalyzer: Subscale plan rejection via conjunction, concern-density,
    and multi-agent affinity detection.

This module does NOT own: plan generation, phase building, prompt construction,
bead persistence, OTel span emission, or any I/O beyond logging.

At Step 1.2, the planner's inline logic is duplicated here. Step 1.4 will
wire the planner to delegate to these analyzers and remove the inline copies.

Per 005b-phase1-design.md §2.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_baton.models.execution import MachinePlan
from agent_baton.models.enums import RiskLevel

from agent_baton.core.engine._planner_helpers import (
    _CROSS_CONCERN_SIGNALS,
    _MIN_CONCERNS_FOR_SPLIT,
    _PHASE_VERBS,
    _parse_concerns,
    _expand_agents_for_concerns,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PlanValidationError(Exception):
    """Raised when an Analyzer rejects a draft MachinePlan."""


class SubscalePlanError(PlanValidationError):
    """Raised by DepthAnalyzer when a step combines too many distinct actions.

    Attributes:
        step_id: ID of the offending plan step.
        reason: Machine-readable reason code: "conjunction", "concern-density",
                or "multi-agent-affinity".
        hint: Human-readable description of why the step was rejected.
    """

    def __init__(self, step_id: str, reason: str, hint: str) -> None:
        super().__init__(f"Subscale step {step_id!r}: [{reason}] {hint}")
        self.step_id = step_id
        self.reason = reason
        self.hint = hint


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class Analyzer(Protocol):
    """Protocol for plan validators."""

    def validate(self, plan: MachinePlan, **kwargs: Any) -> MachinePlan:
        """Validate the plan. Returns the plan (possibly mutated) or raises."""
        ...


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RiskResult:
    """Output of RiskAnalyzer.validate()."""
    classification: Any  # ClassificationResult | None
    risk_level: str       # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    git_strategy: str     # "commit-per-agent" | "branch-per-agent"
    plan: MachinePlan


@dataclass
class CapabilityResult:
    """Output of CapabilityAnalyzer.validate()."""
    plan: MachinePlan
    pattern_used: Any = None              # LearnedPattern | None
    score_warnings: list[str] = field(default_factory=list)
    routing_notes: list[str] = field(default_factory=list)
    retro_feedback: Any = None            # RetrospectiveFeedback | None
    policy_violations: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared risk constants (mirrored from planner.py — see §6 Q9)
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


def _select_git_strategy_str(risk: RiskLevel) -> str:
    """Return git strategy string for the given risk level."""
    if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        return "branch-per-agent"
    return "commit-per-agent"


# ---------------------------------------------------------------------------
# DepthAnalyzer constants
# ---------------------------------------------------------------------------

# Phase verbs used for conjunction detection (word-boundary matching)
_DEPTH_PHASE_VERBS = set(_PHASE_VERBS.keys()) | {
    "research", "investigate", "design", "implement",
    "fix", "draft", "test", "review",
}

# Preferred agent roles per implement-type phase, used for multi-agent affinity check
_PHASE_IDEAL_ROLES: dict[str, list[str]] = {
    "implement": [
        "backend-engineer", "frontend-engineer", "devops-engineer",
        "data-engineer", "data-scientist", "visualization-expert",
    ],
}


# ---------------------------------------------------------------------------
# 1. DependencyAnalyzer
# ---------------------------------------------------------------------------

class DependencyAnalyzer:
    """Validates step DAG ordering and cyclic dependencies.

    Warning-only — never rejects a plan. This matches the existing skeleton
    spirit and aligns with 005b-phase1-design.md §2.1.
    """

    def validate(self, plan: MachinePlan, **kwargs: Any) -> MachinePlan:
        """Emit warnings for forward-references in step depends_on lists."""
        known_steps: set[str] = set()
        for phase in plan.phases:
            for step in phase.steps:
                for dep in (step.depends_on or []):
                    if dep not in known_steps:
                        logger.warning(
                            "Dependency '%s' for step '%s' not found before it in the plan.",
                            dep,
                            step.step_id,
                        )
                known_steps.add(step.step_id)
        return plan


# ---------------------------------------------------------------------------
# 2. RiskAnalyzer
# ---------------------------------------------------------------------------

class RiskAnalyzer:
    """Risk classification, approval gating, and git-strategy derivation.

    Owns:
      - DataClassifier call and risk-merge logic (planner.py 1322–1354).
      - Git-strategy derivation (1356–1357).
      - HIGH+ approval gating on Design/Research phases (1546–1557).
      - _assess_risk helper (3667–3738).

    Does NOT own: phase building, step creation, or bead signals.
    """

    def validate(
        self,
        plan: MachinePlan,
        *,
        classifier: Any = None,
        task_summary: str = "",
        agents: list[str] | None = None,
        **kwargs: Any,
    ) -> MachinePlan:
        """Apply risk classification and approval gating to *plan*.

        Mutates ``plan.risk_level``, ``plan.git_strategy``, and phase
        ``approval_required``/``approval_description`` for HIGH+ Design and
        Research phases.

        Returns the mutated plan.
        """
        resolved_agents = agents or []

        # Step 7: DataClassifier classification
        classification: Any = None
        if classifier is not None:
            try:
                classification = classifier.classify(task_summary)
            except Exception:
                pass

        # Step 8: Risk merge — keyword + classifier ordinal max
        keyword_risk_str = self._assess_risk(task_summary, resolved_agents)
        keyword_risk = RiskLevel(keyword_risk_str)

        if classification is not None:
            classifier_ordinal = _RISK_ORDINAL[classification.risk_level]
            keyword_ordinal = _RISK_ORDINAL[keyword_risk]
            if classifier_ordinal > keyword_ordinal:
                risk_level_enum = classification.risk_level
            else:
                risk_level_enum = keyword_risk
        else:
            risk_level_enum = keyword_risk

        risk_level_str = risk_level_enum.value

        logger.info(
            "RiskAnalyzer: risk=%s (keyword=%s classifier=%s) git_strategy=%s",
            risk_level_str,
            keyword_risk_str,
            classification.risk_level.value if classification else "n/a",
            _select_git_strategy_str(risk_level_enum),
        )

        # Step 8b: Git strategy
        git_strategy = _select_git_strategy_str(risk_level_enum)

        # Mutate plan
        plan.risk_level = risk_level_str
        plan.git_strategy = git_strategy

        # Step 12b: Approval gating on Design/Research at HIGH+ risk.
        # NOTE: The skeleton incorrectly used ("implement", "deploy", "execute").
        # The live planner (line 1549) gates on ("design", "research"). Fixed here.
        if risk_level_enum in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            for phase in plan.phases:
                if phase.name.lower() in ("design", "research"):
                    phase.approval_required = True
                    phase.approval_description = (
                        f"Review {phase.name.lower()} output before "
                        f"implementation begins. Approve to continue, "
                        f"reject to stop, or approve-with-feedback to "
                        f"add remediation steps."
                    )

        return plan

    @staticmethod
    def _assess_risk(task_summary: str, agents: list[str]) -> str:
        """Assess risk from task description and structural signals.

        Extracted verbatim from planner.py 3667–3738.

        Returns one of "LOW", "MEDIUM", or "HIGH".
        """
        score = 0

        description_lower = task_summary.lower()
        keyword_risk = RiskLevel.LOW
        for keyword, level in _RISK_SIGNALS.items():
            if keyword in description_lower:
                if _RISK_ORDINAL[level] > _RISK_ORDINAL[keyword_risk]:
                    keyword_risk = level
        keyword_score = min(_RISK_ORDINAL.get(keyword_risk, 0), 2)
        score = max(score, keyword_score)

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

        # Read-only dampening
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


# ---------------------------------------------------------------------------
# 3. CapabilityAnalyzer
# ---------------------------------------------------------------------------

class CapabilityAnalyzer:
    """Agent routing, pattern application, retro/policy filtering.

    Owns:
      - Pattern lookup and agent override (planner.py 1149–1169).
      - Retrospective drop/prefer (1184–1197, _apply_retro_feedback 3578–3636).
      - Cross-concern agent expansion (1235–1265, _expand_agents_for_concerns).
      - Agent routing (1270–1278, _route_agents 3534–3560).
      - Score check (1497–1498, _check_agent_scores 3562–3576).
      - Policy validation (1503–1517, _classify_to_preset_key 3744–3763,
        _validate_agents_against_policy 3765–3832).

    Returns a CapabilityResult exposing the _last_* fields that the planner
    must assign back onto the IntelligentPlanner instance after pipeline runs.

    Does NOT own: phase building, risk assessment, or knowledge resolution.
    """

    def __init__(
        self,
        registry: Any = None,
        router: Any = None,
        scorer: Any = None,
        bead_store: Any = None,
        policy_engine: Any = None,
    ) -> None:
        self._registry = registry
        self._router = router
        self._scorer = scorer
        self._bead_store = bead_store
        self._policy_engine = policy_engine

    def validate(self, plan: MachinePlan, **kwargs: Any) -> MachinePlan:
        """Route agents, apply retro feedback, check scores, validate policy.

        This is the structural entry point that matches the Analyzer protocol.
        Routing notes, score warnings, etc. are logged but NOT returned from
        this method (the protocol returns MachinePlan). Use the full
        ``validate_with_result`` method when you need the CapabilityResult.
        """
        stack = kwargs.get("stack")
        for phase in plan.phases:
            for step in phase.steps:
                if self._registry and not self._registry.get(step.agent_name):
                    logger.warning(
                        "CapabilityAnalyzer: agent '%s' not found in registry.",
                        step.agent_name,
                    )
                if self._router and stack:
                    try:
                        resolved = self._router.route(step.agent_name, stack=stack)
                        if resolved != step.agent_name:
                            logger.info(
                                "CapabilityAnalyzer routed %s -> %s",
                                step.agent_name,
                                resolved,
                            )
                            step.agent_name = resolved
                    except Exception:
                        pass
        return plan

    # ---- Agent routing helpers (extracted from planner.py 3534–3636) ------

    def route_agents(
        self,
        agents: list[str],
        project_root: Any = None,
        routing_notes: list[str] | None = None,
    ) -> list[str]:
        """Route base agent names to stack-flavored variants where possible.

        Extracted from planner.py _route_agents (3534–3560). Routing notes are
        appended to *routing_notes* list if provided.
        """
        if not agents:
            return agents

        notes = routing_notes if routing_notes is not None else []
        stack = None
        if project_root is not None and self._router is not None:
            try:
                stack = self._router.detect_stack(project_root)
            except Exception:
                pass

        routed: list[str] = []
        for base in agents:
            try:
                resolved = self._router.route(base, stack=stack) if self._router else base
            except Exception:
                resolved = base
            if resolved != base:
                notes.append(f"{base} -> {resolved} (stack-matched flavor)")
            routed.append(resolved)
        return routed

    def check_agent_scores(
        self,
        agents: list[str],
        score_warnings: list[str] | None = None,
    ) -> None:
        """Populate score warnings for any low-health agents.

        Extracted from planner.py _check_agent_scores (3562–3576).
        """
        _LOW_HEALTH_RATINGS = {"needs-improvement"}
        warnings = score_warnings if score_warnings is not None else []

        if self._scorer is None:
            return

        for agent in agents:
            try:
                card = self._scorer.score_agent(agent, bead_store=self._bead_store)
            except Exception:
                continue
            if card.health in _LOW_HEALTH_RATINGS:
                warnings.append(
                    f"Agent '{agent}' has health '{card.health}' "
                    f"(first-pass rate {card.first_pass_rate:.0%}, "
                    f"{card.negative_mentions} negative mention(s))."
                )

    def apply_retro_feedback(
        self,
        agents: list[str],
        feedback: Any,
        routing_notes: list[str] | None = None,
    ) -> list[str]:
        """Apply retrospective recommendations to the agent list.

        Extracted from planner.py _apply_retro_feedback (3578–3636). Soft rules:
        agents in feedback.agents_to_drop() are removed; agents in
        agents_to_prefer() generate routing notes but are not auto-added.
        """
        notes = routing_notes if routing_notes is not None else []
        to_drop = set(feedback.agents_to_drop())

        try:
            from agent_baton.core.learn.overrides import LearnedOverrides
            _learned_drops = LearnedOverrides().get_agent_drops()
            to_drop.update(_learned_drops)
        except Exception:
            pass

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
                        notes.append(f"{dropped} removed (retrospective recommendation)")
                agents = filtered

        if to_prefer:
            for preferred in sorted(to_prefer):
                notes.append(
                    f"Retrospective recommends: {preferred} "
                    f"(not auto-added — add manually if desired)"
                )

        return agents

    @staticmethod
    def classify_to_preset_key(classification: Any) -> str:
        """Map a ClassificationResult guardrail_preset to a PolicyEngine key.

        Extracted from planner.py _classify_to_preset_key (3744–3763).
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

    def validate_agents_against_policy(
        self,
        agents: list[str],
        policy_set: Any,
        plan_phases: list[Any],
    ) -> list[Any]:
        """Check agent assignments against the active policy set.

        Extracted from planner.py _validate_agents_against_policy (3765–3832).
        Returns a deduplicated list of PolicyViolation objects (informational only).
        """
        violations: list[Any] = []
        seen: set[str] = set()

        # Pass 1: per-step path_block and tool_restrict checks
        for phase in plan_phases:
            for step in phase.steps:
                agent = step.agent_name
                paths = list(step.context_files or [])
                tools: list[str] = []

                if self._policy_engine is None:
                    continue
                step_violations = self._policy_engine.evaluate(
                    policy_set, agent, paths, tools
                )
                for v in step_violations:
                    if v.rule.rule_type in ("require_agent", "require_gate"):
                        continue
                    key = f"{v.agent_name}:{v.rule.name}"
                    if key not in seen:
                        seen.add(key)
                        violations.append(v)

        # Pass 2: require_agent rules at plan level
        for rule in policy_set.rules:
            if rule.rule_type == "require_agent":
                required = rule.pattern
                if not any(
                    a == required or a.split("--")[0] == required
                    for a in agents
                ):
                    key = f"plan:{rule.name}"
                    if key not in seen:
                        seen.add(key)
                        from agent_baton.core.govern.policy import PolicyViolation
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


# ---------------------------------------------------------------------------
# 4. DepthAnalyzer
# ---------------------------------------------------------------------------

class DepthAnalyzer:
    """Rejects subscale plans that combine too many distinct actions in one step.

    Per 005b-phase1-design.md §5.1–5.4:
      - Bypassed entirely when complexity == "light".
      - Detects conjunctions via word-boundary regex over _DEPTH_PHASE_VERBS.
      - Detects concern density via _parse_concerns (≥2 markers).
      - Detects multi-agent affinity when a single Implement step spans ≥2 of
        _PHASE_IDEAL_ROLES["implement"].

    Raises SubscalePlanError(step_id, reason, hint) on detection.
    Does NOT own: concern-splitting (that is the strategy's decompose() response).
    """

    def validate(
        self,
        plan: MachinePlan,
        *,
        complexity: str = "medium",
        **kwargs: Any,
    ) -> MachinePlan:
        """Validate plan depth. Raises SubscalePlanError on subscale detection.

        Args:
            plan: The draft plan to validate.
            complexity: Plan complexity tier. When "light", bypass entirely.
        """
        # §5.3 — light plans are explicitly subscale-exempt
        if complexity == "light":
            return plan

        for phase in plan.phases:
            for step in phase.steps:
                desc = step.task_description or ""

                # Check 1: conjunction signal (word-boundary regex)
                conjunction = self._detect_conjunction(desc)
                if conjunction is not None:
                    raise SubscalePlanError(
                        step_id=step.step_id,
                        reason="conjunction",
                        hint=(
                            f"Step description contains combined actions via "
                            f"'{conjunction}'. Split into two sequential steps."
                        ),
                    )

                # Check 2: concern density (≥2 distinct markers)
                concerns = _parse_concerns(desc)
                if len(concerns) >= _MIN_CONCERNS_FOR_SPLIT:
                    raise SubscalePlanError(
                        step_id=step.step_id,
                        reason="concern-density",
                        hint=(
                            f"Step description names {len(concerns)} distinct concerns "
                            f"({', '.join(c[0] for c in concerns)}). "
                            f"Split into one step per concern."
                        ),
                    )

                # Check 3: multi-agent affinity on Implement-class phases
                if phase.name.lower() in ("implement", "fix", "draft", "build"):
                    affinity_agents = self._detect_multi_agent_affinity(desc)
                    if affinity_agents:
                        raise SubscalePlanError(
                            step_id=step.step_id,
                            reason="multi-agent-affinity",
                            hint=(
                                f"Step spans multiple specialist roles "
                                f"({', '.join(affinity_agents)}). "
                                f"Promote to a team step or split by concern."
                            ),
                        )

        return plan

    @staticmethod
    def _detect_conjunction(desc: str) -> str | None:
        """Return the matched conjunction phrase, or None if not found.

        Uses word-boundary regex to avoid false positives from substrings
        (e.g. "audit and fix" inside "individual and fix" does not match
        "individual" as a phase verb).
        """
        desc_lower = desc.lower()
        verbs = sorted(_DEPTH_PHASE_VERBS)  # deterministic matching order

        for verb1 in verbs:
            for verb2 in verbs:
                if verb1 == verb2:
                    continue
                pattern = rf"\b{re.escape(verb1)}\s+and\s+{re.escape(verb2)}\b"
                if re.search(pattern, desc_lower):
                    return f"{verb1} and {verb2}"
        return None

    @staticmethod
    def _detect_multi_agent_affinity(desc: str) -> list[str]:
        """Return list of matched implement-phase ideal roles found in desc.

        Matches ≥2 roles from _PHASE_IDEAL_ROLES["implement"] via the
        _CROSS_CONCERN_SIGNALS keyword lists.
        """
        desc_lower = desc.lower()
        text_words = set(re.findall(r"\b\w+\b", desc_lower))
        ideal_roles = _PHASE_IDEAL_ROLES.get("implement", [])
        matched: list[str] = []

        for role in ideal_roles:
            keywords = _CROSS_CONCERN_SIGNALS.get(role, [])
            for kw in keywords:
                if " " in kw:
                    hit = kw in desc_lower
                else:
                    hit = kw in text_words
                if hit:
                    matched.append(role)
                    break  # one keyword match is enough to confirm the role

        return matched if len(matched) >= 2 else []
