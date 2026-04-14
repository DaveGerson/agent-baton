"""Foresight engine — proactive gap analysis for execution plans.

Scans a planned set of phases and steps to predict capability gaps,
missing prerequisites, edge cases, and tooling needs that the user
did not explicitly request but that are necessary for success.

When a gap is found, the engine inserts preparatory phases/steps into
the plan before the work that needs them.  For example:

- A data-quality agent that only has "update records" capability may
  need a "drop records" tool added to its toolkit before it encounters
  duplicates.
- A migration step that drops columns needs a rollback capability
  provisioned first.
- An API implementation step that exposes new endpoints needs schema
  validation scaffolded first.

The foresight engine runs as step 9.7 in ``IntelligentPlanner.create_plan()``,
after phases are built and enriched but before shared context is assembled.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from agent_baton.models.execution import PlanPhase, PlanStep
from agent_baton.models.taxonomy import ForesightInsight, StepIntent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Foresight rules — pattern-matching heuristics for gap detection
# ---------------------------------------------------------------------------

@dataclass
class ForesightRule:
    """A single foresight rule that detects a specific gap pattern.

    Attributes:
        rule_id: Unique identifier for this rule.
        name: Human-readable rule name.
        description: What gap this rule detects.
        trigger_keywords: Keywords in step descriptions that activate this rule.
        trigger_agents: Agent names that activate this rule.
        gap_category: Category of the detected gap.
        resolution_template: Description of the step to insert.
        resolution_agent: Agent to assign to the inserted step.
        resolution_intent: Step intent for the inserted step.
        resolution_phase_name: Name for the inserted phase.
        resolution_deliverables: Expected outputs from the resolution step.
        confidence: Confidence level for this rule's predictions.
    """

    rule_id: str
    name: str
    description: str
    trigger_keywords: list[str] = field(default_factory=list)
    trigger_agents: list[str] = field(default_factory=list)
    gap_category: str = "capability_gap"
    resolution_template: str = ""
    resolution_agent: str = "backend-engineer"
    resolution_intent: StepIntent = StepIntent.SCAFFOLD
    resolution_phase_name: str = "Prepare"
    resolution_deliverables: list[str] = field(default_factory=list)
    confidence: float = 0.8


# Built-in foresight rules
_BUILTIN_RULES: list[ForesightRule] = [
    # ── Capability gap: data operations need CRUD completeness ────────
    ForesightRule(
        rule_id="foresight-data-crud",
        name="Data CRUD completeness",
        description=(
            "Data quality or data processing steps that mention update/modify "
            "operations may need delete/drop capability for handling duplicates, "
            "stale records, or cleanup."
        ),
        trigger_keywords=[
            "data quality", "data processing", "update records",
            "clean data", "deduplicate", "deduplication", "data cleaning",
            "data validation", "record update", "data integrity",
        ],
        trigger_agents=["data-engineer", "data-analyst", "data-scientist"],
        gap_category="capability_gap",
        resolution_template=(
            "Provision data record management capabilities including "
            "drop/delete operations for handling duplicates, stale records, "
            "and data cleanup. Ensure the agent toolkit includes both "
            "constructive (create/update) and destructive (delete/drop) "
            "operations before data quality processing begins."
        ),
        resolution_agent="data-engineer",
        resolution_intent=StepIntent.PROVISION,
        resolution_phase_name="Prepare: Data Tooling",
        resolution_deliverables=["Data management toolkit with CRUD operations"],
        confidence=0.85,
    ),

    # ── Prerequisite: migrations need rollback capability ─────────────
    ForesightRule(
        rule_id="foresight-migration-rollback",
        name="Migration rollback safety",
        description=(
            "Migration steps that modify schema or move data need rollback "
            "capability provisioned before the migration runs."
        ),
        trigger_keywords=[
            "migrate", "migration", "schema change", "alter table",
            "drop column", "rename column", "data migration",
            "database migration", "move data",
        ],
        trigger_agents=["backend-engineer", "data-engineer"],
        gap_category="prerequisite",
        resolution_template=(
            "Set up migration rollback infrastructure: generate reversible "
            "migration scripts, create pre-migration snapshots or backups, "
            "and verify rollback procedures before the migration begins."
        ),
        resolution_agent="backend-engineer",
        resolution_intent=StepIntent.SCAFFOLD,
        resolution_phase_name="Prepare: Migration Safety",
        resolution_deliverables=["Rollback scripts", "Pre-migration backup procedure"],
        confidence=0.9,
    ),

    # ── Prerequisite: API changes need schema validation ──────────────
    ForesightRule(
        rule_id="foresight-api-schema",
        name="API schema validation",
        description=(
            "New or modified API endpoints need request/response schema "
            "validation scaffolded before implementation."
        ),
        trigger_keywords=[
            "api endpoint", "new endpoint", "rest api", "graphql",
            "api route", "http endpoint", "api implementation",
            "new api", "api changes",
        ],
        trigger_agents=["backend-engineer", "frontend-engineer"],
        gap_category="prerequisite",
        resolution_template=(
            "Define and validate API request/response schemas before "
            "implementation begins. Create schema definitions, validation "
            "rules, and example payloads that the implementation step "
            "can build against."
        ),
        resolution_agent="architect",
        resolution_intent=StepIntent.PRODUCE,
        resolution_phase_name="Prepare: API Schema",
        resolution_deliverables=["API schema definitions", "Validation rules"],
        confidence=0.8,
    ),

    # ── Edge case: destructive operations need safety checks ──────────
    ForesightRule(
        rule_id="foresight-destructive-safety",
        name="Destructive operation safety",
        description=(
            "Steps involving destructive operations (delete, drop, truncate, "
            "purge) need confirmation mechanisms and dry-run capability."
        ),
        trigger_keywords=[
            "delete", "drop", "truncate", "purge", "wipe",
            "remove all", "clear data", "reset database",
            "destroy", "clean up",
        ],
        trigger_agents=[],  # any agent
        gap_category="edge_case",
        resolution_template=(
            "Add safety mechanisms for destructive operations: implement "
            "dry-run mode, add confirmation prompts, create audit logging "
            "for destructive actions, and ensure idempotent execution."
        ),
        resolution_agent="backend-engineer",
        resolution_intent=StepIntent.SCAFFOLD,
        resolution_phase_name="Prepare: Safety Checks",
        resolution_deliverables=["Dry-run capability", "Audit logging for destructive ops"],
        confidence=0.85,
    ),

    # ── Tooling: infrastructure changes need environment setup ────────
    ForesightRule(
        rule_id="foresight-infra-env",
        name="Infrastructure environment preparation",
        description=(
            "Infrastructure or deployment changes need environment "
            "configuration validated before changes are applied."
        ),
        trigger_keywords=[
            "infrastructure", "deploy", "terraform", "docker",
            "ci/cd", "kubernetes", "helm", "cloudformation",
            "ansible", "container",
        ],
        trigger_agents=["devops-engineer"],
        gap_category="tooling",
        resolution_template=(
            "Validate and prepare the target environment configuration "
            "before applying infrastructure changes. Verify credentials, "
            "check resource quotas, and ensure deployment prerequisites "
            "are met."
        ),
        resolution_agent="devops-engineer",
        resolution_intent=StepIntent.CONFIGURE,
        resolution_phase_name="Prepare: Environment",
        resolution_deliverables=["Environment validation report", "Configuration checklist"],
        confidence=0.8,
    ),

    # ── Prerequisite: cross-domain integration needs contract ─────────
    ForesightRule(
        rule_id="foresight-integration-contract",
        name="Integration contract definition",
        description=(
            "Steps that integrate across domains (frontend+backend, "
            "service+service) need an interface contract defined first."
        ),
        trigger_keywords=[
            "integrate", "connect", "wire up", "hook up",
            "frontend and backend", "api and ui",
            "service integration", "event bus", "webhook",
        ],
        trigger_agents=["frontend-engineer", "backend-engineer"],
        gap_category="prerequisite",
        resolution_template=(
            "Define the integration contract between components before "
            "implementation begins. Document API shapes, event schemas, "
            "error handling conventions, and data flow diagrams."
        ),
        resolution_agent="architect",
        resolution_intent=StepIntent.PRODUCE,
        resolution_phase_name="Prepare: Integration Contract",
        resolution_deliverables=["Interface contract document", "Data flow diagram"],
        confidence=0.75,
    ),

    # ── Capability gap: test infrastructure may not exist ─────────────
    ForesightRule(
        rule_id="foresight-test-infra",
        name="Test infrastructure scaffolding",
        description=(
            "Test phases that reference integration or end-to-end testing "
            "may need test infrastructure (fixtures, mocks, test databases) "
            "set up before test authoring begins."
        ),
        trigger_keywords=[
            "integration test", "e2e test", "end-to-end",
            "test database", "test fixture", "mock service",
            "test environment", "test setup",
        ],
        trigger_agents=["test-engineer"],
        gap_category="capability_gap",
        resolution_template=(
            "Set up test infrastructure before test authoring: create "
            "fixtures, configure test databases, build mock services, "
            "and ensure the test runner environment is ready."
        ),
        resolution_agent="test-engineer",
        resolution_intent=StepIntent.SCAFFOLD,
        resolution_phase_name="Prepare: Test Infrastructure",
        resolution_deliverables=["Test fixtures", "Mock services", "Test environment config"],
        confidence=0.75,
    ),
]


# ---------------------------------------------------------------------------
# ForesightEngine
# ---------------------------------------------------------------------------

class ForesightEngine:
    """Analyzes a plan and inserts proactive preparatory steps.

    The engine scans step descriptions and agent assignments against a
    set of foresight rules.  When a rule matches, it inserts a
    preparatory phase before the phase that triggered the match.

    Rules are evaluated greedily — the first match per rule wins.
    Duplicate insertions (same rule matching multiple steps) are
    collapsed into a single preparatory phase.

    Usage::

        engine = ForesightEngine()
        phases, insights = engine.analyze(phases, task_summary, risk_level)
    """

    def __init__(
        self,
        rules: list[ForesightRule] | None = None,
        min_confidence: float = 0.7,
    ) -> None:
        self._rules = rules if rules is not None else list(_BUILTIN_RULES)
        self._min_confidence = min_confidence

    def analyze(
        self,
        phases: list[PlanPhase],
        task_summary: str,
        risk_level: str = "LOW",
        existing_agents: list[str] | None = None,
    ) -> tuple[list[PlanPhase], list[ForesightInsight]]:
        """Analyze a plan and return modified phases with foresight steps.

        Args:
            phases: The current plan phases (will not be mutated).
            task_summary: The overall task description.
            risk_level: Plan risk level — higher risk lowers confidence
                thresholds.
            existing_agents: Agents already in the plan for routing checks.

        Returns:
            Tuple of (modified phases, list of insights generated).
        """
        # Lower confidence threshold for higher-risk tasks
        threshold = self._min_confidence
        if risk_level in ("HIGH", "CRITICAL"):
            threshold = max(0.5, threshold - 0.15)
        elif risk_level == "MEDIUM":
            threshold = max(0.6, threshold - 0.05)

        insights: list[ForesightInsight] = []
        # Track which rules have already fired to avoid duplicates
        fired_rules: set[str] = set()
        # Track insertion points: maps phase_id -> list of prep phases to insert before it
        insertions: dict[int, list[tuple[ForesightRule, str]]] = {}

        combined_text = task_summary.lower()

        for phase in phases:
            for step in phase.steps:
                step_text = step.task_description.lower()
                combined_step_text = f"{combined_text} {step_text}"

                for rule in self._rules:
                    if rule.rule_id in fired_rules:
                        continue
                    if rule.confidence < threshold:
                        continue

                    if self._rule_matches(rule, combined_step_text, step.agent_name):
                        fired_rules.add(rule.rule_id)
                        if phase.phase_id not in insertions:
                            insertions[phase.phase_id] = []
                        insertions[phase.phase_id].append((rule, step.step_id))

        if not insertions:
            return phases, []

        # Build new phase list with insertions
        new_phases: list[PlanPhase] = []
        next_phase_id = 1

        for phase in phases:
            # Insert preparatory phases before this phase if needed
            if phase.phase_id in insertions:
                for rule, trigger_step_id in insertions[phase.phase_id]:
                    prep_step_id = f"{next_phase_id}.1"
                    prep_step = PlanStep(
                        step_id=prep_step_id,
                        agent_name=self._resolve_agent(
                            rule.resolution_agent, existing_agents
                        ),
                        task_description=rule.resolution_template,
                        deliverables=list(rule.resolution_deliverables),
                    )
                    prep_phase = PlanPhase(
                        phase_id=next_phase_id,
                        name=rule.resolution_phase_name,
                        steps=[prep_step],
                    )
                    new_phases.append(prep_phase)

                    insight = ForesightInsight(
                        category=rule.gap_category,
                        description=rule.description,
                        resolution=rule.resolution_template,
                        inserted_phase_name=rule.resolution_phase_name,
                        inserted_step_ids=[prep_step_id],
                        confidence=rule.confidence,
                        source_rule=rule.rule_id,
                    )
                    insights.append(insight)
                    next_phase_id += 1

            # Re-number the original phase
            phase.phase_id = next_phase_id
            # Re-number steps within the phase
            for i, step in enumerate(phase.steps, start=1):
                step.step_id = f"{next_phase_id}.{i}"
            new_phases.append(phase)
            next_phase_id += 1

        return new_phases, insights

    def _rule_matches(
        self,
        rule: ForesightRule,
        text: str,
        agent_name: str,
    ) -> bool:
        """Check if a foresight rule matches the given text and agent."""
        # Keyword match
        keyword_match = any(kw in text for kw in rule.trigger_keywords)
        if not keyword_match:
            return False

        # Agent match (empty trigger_agents means any agent matches)
        if rule.trigger_agents:
            base_agent = agent_name.split("--")[0]
            agent_match = base_agent in rule.trigger_agents
            if not agent_match:
                return False

        return True

    @staticmethod
    def _resolve_agent(
        preferred: str,
        existing_agents: list[str] | None,
    ) -> str:
        """Resolve the preferred agent to a routed variant if available.

        If the exact preferred agent is in the existing roster, use it.
        If a flavored variant exists (e.g., backend-engineer--python),
        use that instead.
        """
        if not existing_agents:
            return preferred
        # Exact match
        if preferred in existing_agents:
            return preferred
        # Flavored match
        for agent in existing_agents:
            if agent.split("--")[0] == preferred:
                return agent
        return preferred
