"""IntelligentPlanner — data-driven execution plan creation.

Creates MachinePlan objects informed by historical patterns (PatternLearner),
per-agent performance scores (PerformanceScorer), and budget recommendations
(BudgetTuner).  All data sources are optional; the planner degrades gracefully
to default heuristics when no historical data is available.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.core.improve.scoring import AgentScorecard, PerformanceScorer
from agent_baton.core.learn.budget_tuner import BudgetTuner
from agent_baton.core.learn.pattern_learner import PatternLearner
from agent_baton.core.orchestration.plan import PlanBuilder
from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.core.orchestration.router import AgentRouter
from agent_baton.models.enums import RiskLevel
from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep
from agent_baton.models.pattern import LearnedPattern


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
    "documentation": [],
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

# Keyword sets for task type inference (checked in order)
_TASK_TYPE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("bug-fix", ["fix", "bug", "broken", "error", "crash", "traceback", "exception", "patch"]),
    ("migration", ["migrate", "migration", "upgrade", "move"]),
    ("refactor", ["refactor", "clean", "reorganize", "restructure", "rename", "cleanup"]),
    ("data-analysis", ["analyze", "analyse", "report", "dashboard", "query", "insight", "metric"]),
    ("documentation", ["doc", "docs", "readme", "spec", "adr", "document", "wiki"]),
    ("test", ["test", "tests", "testing", "coverage", "e2e", "unit", "integration"]),
    ("new-feature", ["add", "build", "create", "implement", "new", "feature", "develop"]),
]


# ---------------------------------------------------------------------------
# IntelligentPlanner
# ---------------------------------------------------------------------------

class IntelligentPlanner:
    """Creates execution plans informed by historical patterns, scores, and budgets.

    This replaces ad-hoc planning in the orchestrator prompt with data-driven
    decisions.  When no historical data exists the planner returns sensible
    defaults; as usage data accumulates the plans become progressively smarter.

    Usage::

        planner = IntelligentPlanner()
        plan = planner.create_plan("Add OAuth2 login to the API")
        print(planner.explain_plan(plan))
    """

    def __init__(self, team_context_root: Path | None = None) -> None:
        self._team_context_root = team_context_root
        self._pattern_learner = PatternLearner(team_context_root)
        self._scorer = PerformanceScorer()
        self._budget_tuner = BudgetTuner(team_context_root)
        self._plan_builder = PlanBuilder()
        registry = AgentRegistry()
        registry.load_default_paths()
        self._registry = registry
        self._router = AgentRouter(registry)

        # Populated during create_plan for use in explain_plan
        self._last_pattern_used: LearnedPattern | None = None
        self._last_score_warnings: list[str] = []
        self._last_routing_notes: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_plan(
        self,
        task_summary: str,
        *,
        task_type: str | None = None,
        project_root: Path | None = None,
        agents: list[str] | None = None,
        phases: list[dict] | None = None,
    ) -> MachinePlan:
        """Create a complete, data-driven execution plan.

        Steps:
        1. Generate task_id from timestamp + summary slug.
        2. Detect project stack if project_root is given.
        3. Infer or use the provided task_type.
        4. Look for a high-confidence pattern that matches the task_type.
        5. Determine agents list — from explicit override, pattern, or defaults.
        6. Route base agent names to flavored variants.
        7. Assess risk via _assess_risk (keywords + structural signals from agents).
        8. Derive git strategy from risk level.
        9. Build phase list — from override, pattern, or defaults.
        10. Check PerformanceScorer; warn about low-scoring agents.
        11. Apply budget tier recommendation if one exists.
        12. Add QA gates between phases.
        13. Build shared_context string and return MachinePlan.

        Args:
            task_summary: One-line human description of the task.
            task_type: Override the auto-detected task type.
            project_root: Project directory for stack detection and agent routing.
            agents: Override the agent list; skips pattern/default agent selection.
            phases: Explicit phase definitions as dicts; if given, pattern/default
                    phase logic is skipped.  Each dict must have at minimum a
                    "name" key; optionally "agents" (list of str) and "gate" (dict).

        Returns:
            A fully constructed MachinePlan.
        """
        # Reset per-call state
        self._last_pattern_used = None
        self._last_score_warnings = []
        self._last_routing_notes = []

        # 1. Task ID
        task_id = self._generate_task_id(task_summary)

        # 2. Detect stack (best effort) — needed before agent resolution
        stack_profile = None
        if project_root is not None:
            try:
                stack_profile = self._router.detect_stack(project_root)
            except Exception:
                pass

        # 3. Task type
        inferred_type = task_type or self._infer_task_type(task_summary)

        # 4. Pattern lookup
        pattern: LearnedPattern | None = None
        if not agents and not phases:
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
                        break
            except Exception:
                pass

        # 5. Determine agents list
        if agents is None:
            if pattern is not None:
                resolved_agents = list(pattern.recommended_agents)
            else:
                resolved_agents = list(_DEFAULT_AGENTS.get(inferred_type, []))
        else:
            resolved_agents = list(agents)

        # 6. Route agents
        resolved_agents = self._route_agents(resolved_agents, project_root)

        # 7. Risk — assessed after routing so structural signals use final agents
        risk_level = self._assess_risk(task_summary, resolved_agents)
        risk_level_enum = RiskLevel(risk_level)

        # 8. Git strategy — derived from risk
        from agent_baton.core.orchestration.plan import PlanBuilder as _PB
        git_strategy = _PB._select_git_strategy(risk_level_enum).value

        # 9. Build phases
        if phases is not None:
            plan_phases = self._phases_from_dicts(phases, resolved_agents)
        elif pattern is not None:
            plan_phases = self._apply_pattern(pattern, inferred_type)
            # Apply routed agent names to pattern-derived phases
            plan_phases = self._assign_agents_to_phases(plan_phases, resolved_agents)
        else:
            plan_phases = self._default_phases(inferred_type, resolved_agents)

        # 10. Score check — warn about low-health agents
        self._check_agent_scores(resolved_agents)

        # 11. Budget tier
        budget_tier = self._select_budget_tier(inferred_type, len(resolved_agents))

        # 12. Add QA gates
        for phase in plan_phases:
            if phase.gate is None:
                phase.gate = self._default_gate(phase.name)

        # 13. Shared context
        # Build a temporary plan to generate the context string
        # Build a temporary plan to generate the context string
        tmp_plan = MachinePlan(
            task_id=task_id,
            task_summary=task_summary,
            risk_level=risk_level,
            budget_tier=budget_tier,
            git_strategy=git_strategy,
            phases=plan_phases,
            pattern_source=pattern.pattern_id if pattern else None,
        )
        shared_context = self._build_shared_context(tmp_plan)
        tmp_plan.shared_context = shared_context

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

        # Phase summary
        lines += ["## Phase Summary", ""]
        for phase in plan.phases:
            agent_names = [s.agent_name for s in phase.steps]
            gate_label = f" → gate: {phase.gate.gate_type}" if phase.gate else ""
            lines.append(
                f"- **Phase {phase.phase_id} — {phase.name}**: "
                f"{', '.join(agent_names) or '(no agents)'}{gate_label}"
            )
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers — task ID and type inference
    # ------------------------------------------------------------------

    def _generate_task_id(self, summary: str) -> str:
        """Create a task ID like '2026-03-20-build-widget-api'."""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slug = re.sub(r"[^a-z0-9]+", "-", summary.lower()).strip("-")
        slug = slug[:50]  # keep IDs manageable
        slug = slug.rstrip("-")
        return f"{date_str}-{slug}" if slug else date_str

    def _infer_task_type(self, summary: str) -> str:
        """Infer task type from summary keywords.

        Returns one of: 'new-feature', 'bug-fix', 'refactor', 'data-analysis',
        'documentation', 'migration', 'test'.  Falls back to 'new-feature' when
        no keywords match.
        """
        lower = summary.lower()
        for task_type, keywords in _TASK_TYPE_KEYWORDS:
            for kw in keywords:
                if kw in lower:
                    return task_type
        return "new-feature"

    # ------------------------------------------------------------------
    # Private helpers — phase building
    # ------------------------------------------------------------------

    def _default_phases(self, task_type: str, agents: list[str]) -> list[PlanPhase]:
        """Build the default PlanPhase list for a task type.

        Phase names come from _PHASE_NAMES.  Agents are distributed round-robin
        across phases, ensuring every phase has at least one step when agents
        are available.  If the agent list is empty, one step with a generic
        placeholder name is created per phase.
        """
        phase_names = _PHASE_NAMES.get(task_type, _DEFAULT_PHASE_NAMES)
        return self._build_phases_for_names(phase_names, agents)

    def _apply_pattern(self, pattern: LearnedPattern, task_type: str) -> list[PlanPhase]:
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

    def _assign_agents_to_phases(
        self, phases: list[PlanPhase], agents: list[str]
    ) -> list[PlanPhase]:
        """Distribute agents across phases that have no steps yet."""
        if not agents:
            # Ensure every phase has at least one step even without real agents
            for idx, phase in enumerate(phases):
                if not phase.steps:
                    step_id = f"{phase.phase_id}.1"
                    phase.steps.append(
                        PlanStep(
                            step_id=step_id,
                            agent_name="backend-engineer",
                            task_description=f"{phase.name} phase work",
                        )
                    )
            return phases

        # Distribute agents across phases (round-robin)
        n_phases = len(phases)
        for agent_idx, agent in enumerate(agents):
            phase_idx = agent_idx % n_phases
            phase = phases[phase_idx]
            step_number = len(phase.steps) + 1
            step_id = f"{phase.phase_id}.{step_number}"
            phase.steps.append(
                PlanStep(
                    step_id=step_id,
                    agent_name=agent,
                    task_description=f"{phase.name} phase — {agent}",
                )
            )

        # Guarantee every phase has at least one step
        for phase in phases:
            if not phase.steps:
                phase.steps.append(
                    PlanStep(
                        step_id=f"{phase.phase_id}.1",
                        agent_name=agents[0],
                        task_description=f"{phase.name} phase work",
                    )
                )

        return phases

    def _build_phases_for_names(
        self, phase_names: list[str], agents: list[str]
    ) -> list[PlanPhase]:
        """Build PlanPhase objects for a list of names, distributing agents."""
        phases: list[PlanPhase] = [
            PlanPhase(phase_id=idx, name=name, steps=[])
            for idx, name in enumerate(phase_names, start=1)
        ]
        return self._assign_agents_to_phases(phases, agents)

    def _phases_from_dicts(
        self, phase_dicts: list[dict], agents: list[str]
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
                    gate_type=gate_dict.get("gate_type", "build"),
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
                        task_description=f"{name} phase — {agent}",
                    )
                )
            phases.append(PlanPhase(phase_id=idx, name=name, steps=steps, gate=gate))

        # If no phase-level agents were provided, distribute the resolved agents
        all_steps_empty = all(not p.steps for p in phases)
        if all_steps_empty and agents:
            return self._assign_agents_to_phases(phases, agents)

        return phases

    # ------------------------------------------------------------------
    # Private helpers — gates
    # ------------------------------------------------------------------

    def _default_gate(self, phase_name: str) -> PlanGate | None:
        """Return an appropriate QA gate for a phase name.

        - 'Implement' or 'Fix' → build check (pytest)
        - 'Test' → test gate (pytest with coverage)
        - 'Review' → no automated gate (human review)
        - All others → None
        """
        name_lower = phase_name.lower()
        if name_lower in ("implement", "fix"):
            return PlanGate(
                gate_type="build",
                command="pytest",
                description="Run test suite to verify the implementation builds cleanly.",
                fail_on=["test failure", "import error"],
            )
        if name_lower == "test":
            return PlanGate(
                gate_type="test",
                command="pytest --cov",
                description="Run full test suite with coverage report.",
                fail_on=["test failure", "coverage below threshold"],
            )
        # Review phases and everything else get no automated gate
        return None

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
                card: AgentScorecard = self._scorer.score_agent(agent)
            except Exception:
                continue
            if card.health in _LOW_HEALTH_RATINGS:
                self._last_score_warnings.append(
                    f"Agent '{agent}' has health '{card.health}' "
                    f"(first-pass rate {card.first_pass_rate:.0%}, "
                    f"{card.negative_mentions} negative mention(s))."
                )

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

        Combines keyword matching (delegated to PlanBuilder) with structural
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

        # ── Keyword signals (via PlanBuilder for consistency) ─────────────────
        risk_enum = self._plan_builder.assess_risk(task_summary)
        keyword_score = {
            RiskLevel.LOW: 0,
            RiskLevel.MEDIUM: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.CRITICAL: 2,
        }.get(risk_enum, 0)
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
    # Private helpers — shared context
    # ------------------------------------------------------------------

    def _build_shared_context(self, plan: MachinePlan) -> str:
        """Build the shared_context string embedded in the plan.

        This is the boilerplate every delegated agent should receive so it
        understands the overall mission and its role in the plan.
        """
        agent_list = ", ".join(dict.fromkeys(plan.all_agents))  # deduplicated, ordered
        lines: list[str] = [
            f"Task: {plan.task_summary}",
            f"Task ID: {plan.task_id}",
            f"Risk Level: {plan.risk_level}",
            f"Budget Tier: {plan.budget_tier}",
            f"Execution Mode: {plan.execution_mode}",
            f"Git Strategy: {plan.git_strategy}",
        ]
        if agent_list:
            lines.append(f"Team: {agent_list}")
        if plan.pattern_source:
            lines.append(f"Pattern: {plan.pattern_source}")
        lines.append("")
        lines.append(
            "Read `.claude/team-context/context.md` for shared project context."
        )
        return "\n".join(lines)
