"""Planning taxonomy — semantic object model for execution plans.

Defines the universal vocabulary used to describe plans, phases, steps,
and their relationships.  Every concept that appears in a ``MachinePlan``
has a formal definition here, ensuring consistent interpretation by both
human operators and agentic consumers.

This module is the single source of truth for the planning taxonomy.
The ``PlanningTaxonomy`` singleton aggregates all controlled vocabularies
(enums, intent types, phase archetypes, step categories) into a
queryable, serializable object that can be rendered as documentation,
embedded into agent prompts, or served as structured data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Step Intent — why a step exists in a plan
# ---------------------------------------------------------------------------

class StepIntent(Enum):
    """Semantic intent of a plan step — the *why* behind the work.

    Every ``PlanStep`` carries an intent that describes its purpose in
    the plan.  Intent is orthogonal to agent selection — the same intent
    can be fulfilled by different agents depending on the domain.

    Intents fall into three families:

    **Core work** — steps that directly advance the task:
        PRODUCE, TRANSFORM, VALIDATE, INTEGRATE

    **Support** — steps that enable core work:
        SCAFFOLD, PROVISION, CONFIGURE, REMEDIATE

    **Governance** — steps that ensure quality and compliance:
        REVIEW, AUDIT, APPROVE, GATE

    **Foresight** — steps inserted proactively by the planner:
        FORESIGHT
    """

    # Core work
    PRODUCE = "produce"
    """Create new artifacts (code, docs, schemas, configs)."""

    TRANSFORM = "transform"
    """Modify existing artifacts (refactor, migrate, rename)."""

    VALIDATE = "validate"
    """Verify correctness (test, type-check, lint, spec-check)."""

    INTEGRATE = "integrate"
    """Connect components (wire APIs, hook events, compose services)."""

    # Support
    SCAFFOLD = "scaffold"
    """Set up project structure, boilerplate, or tooling prerequisites."""

    PROVISION = "provision"
    """Prepare runtime resources (databases, queues, infrastructure)."""

    CONFIGURE = "configure"
    """Adjust settings, feature flags, or environment variables."""

    REMEDIATE = "remediate"
    """Fix issues found by gates, reviews, or foresight analysis."""

    # Governance
    REVIEW = "review"
    """Human or agent review of deliverables."""

    AUDIT = "audit"
    """Compliance or security audit with pass/fail outcome."""

    APPROVE = "approve"
    """Human approval checkpoint."""

    GATE = "gate"
    """Automated quality gate (build, test, lint)."""

    # Foresight
    FORESIGHT = "foresight"
    """Proactive step inserted by foresight analysis to address
    predicted capability gaps, edge cases, or prerequisites."""


# ---------------------------------------------------------------------------
# Phase Archetype — canonical phase roles
# ---------------------------------------------------------------------------

class PhaseArchetype(Enum):
    """Canonical phase role in the plan lifecycle.

    Phase names in ``PlanPhase.name`` are free-form strings, but every
    phase maps to one of these archetypes for consistent routing and
    gate selection.
    """

    DISCOVERY = "discovery"
    """Understand the problem: research, investigate, analyze requirements."""

    DESIGN = "design"
    """Produce a design or architecture that guides implementation."""

    PREPARATION = "preparation"
    """Set up prerequisites: scaffold, provision, configure tooling.
    Foresight-inserted phases are typically this archetype."""

    IMPLEMENTATION = "implementation"
    """Build the solution: write code, create artifacts."""

    VERIFICATION = "verification"
    """Verify correctness: run tests, validate specs, check quality."""

    REVIEW = "review"
    """Human or agent review of completed work."""

    REMEDIATION = "remediation"
    """Fix issues found during verification or review."""


# ---------------------------------------------------------------------------
# Plan Element Category — what kind of plan object this is
# ---------------------------------------------------------------------------

class PlanElementKind(Enum):
    """Discriminator for the structural elements of a plan.

    Used in taxonomy queries and documentation to classify any object
    that participates in the plan hierarchy.
    """

    PLAN = "plan"
    """Top-level execution plan (``MachinePlan``)."""

    PHASE = "phase"
    """Logical grouping of steps (``PlanPhase``)."""

    STEP = "step"
    """Atomic unit of agent work (``PlanStep``)."""

    GATE = "gate"
    """QA checkpoint between phases (``PlanGate``)."""

    TEAM = "team"
    """Coordinated multi-agent step (``PlanStep`` with team members)."""

    MEMBER = "member"
    """Individual contributor within a team step (``TeamMember``)."""

    AMENDMENT = "amendment"
    """Runtime modification to the plan (``PlanAmendment``)."""

    FORESIGHT_INSIGHT = "foresight_insight"
    """A proactive insight generated by foresight analysis."""


# ---------------------------------------------------------------------------
# Foresight Insight — a proactive observation from foresight analysis
# ---------------------------------------------------------------------------

@dataclass
class ForesightInsight:
    """A proactive insight generated by foresight analysis.

    Foresight scans the planned work and predicts gaps, risks, or
    prerequisites that the user didn't explicitly request but that are
    necessary for success.

    Attributes:
        category: Classification of the insight.
        description: Human-readable explanation of the predicted need.
        resolution: What the foresight engine will do about it.
        inserted_phase_name: Name of the phase inserted to address this
            insight, if any.
        inserted_step_ids: Step IDs created for this insight.
        confidence: How confident the analysis is (0.0 - 1.0).
        source_rule: The foresight rule that triggered this insight.
    """

    category: str       # "capability_gap", "edge_case", "prerequisite", "tooling"
    description: str
    resolution: str
    inserted_phase_name: str = ""
    inserted_step_ids: list[str] = field(default_factory=list)
    confidence: float = 0.8
    source_rule: str = ""

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "description": self.description,
            "resolution": self.resolution,
            "inserted_phase_name": self.inserted_phase_name,
            "inserted_step_ids": list(self.inserted_step_ids),
            "confidence": self.confidence,
            "source_rule": self.source_rule,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ForesightInsight:
        return cls(
            category=data.get("category", ""),
            description=data.get("description", ""),
            resolution=data.get("resolution", ""),
            inserted_phase_name=data.get("inserted_phase_name", ""),
            inserted_step_ids=data.get("inserted_step_ids", []),
            confidence=data.get("confidence", 0.8),
            source_rule=data.get("source_rule", ""),
        )


# ---------------------------------------------------------------------------
# Phase-to-archetype mapping
# ---------------------------------------------------------------------------

_PHASE_NAME_TO_ARCHETYPE: dict[str, PhaseArchetype] = {
    "research": PhaseArchetype.DISCOVERY,
    "investigate": PhaseArchetype.DISCOVERY,
    "analyze": PhaseArchetype.DISCOVERY,
    "design": PhaseArchetype.DESIGN,
    "scaffold": PhaseArchetype.PREPARATION,
    "provision": PhaseArchetype.PREPARATION,
    "prepare": PhaseArchetype.PREPARATION,
    "configure": PhaseArchetype.PREPARATION,
    "implement": PhaseArchetype.IMPLEMENTATION,
    "fix": PhaseArchetype.IMPLEMENTATION,
    "draft": PhaseArchetype.IMPLEMENTATION,
    "build": PhaseArchetype.IMPLEMENTATION,
    "test": PhaseArchetype.VERIFICATION,
    "verify": PhaseArchetype.VERIFICATION,
    "validate": PhaseArchetype.VERIFICATION,
    "review": PhaseArchetype.REVIEW,
    "audit": PhaseArchetype.REVIEW,
    "remediate": PhaseArchetype.REMEDIATION,
    "hotfix": PhaseArchetype.REMEDIATION,
}


def phase_archetype(phase_name: str) -> PhaseArchetype:
    """Map a free-form phase name to its canonical archetype.

    Falls back to ``IMPLEMENTATION`` for unrecognized names.
    """
    return _PHASE_NAME_TO_ARCHETYPE.get(
        phase_name.lower(), PhaseArchetype.IMPLEMENTATION
    )


# ---------------------------------------------------------------------------
# Agent-to-intent affinity — which intents each agent naturally fulfils
# ---------------------------------------------------------------------------

AGENT_INTENT_AFFINITY: dict[str, list[StepIntent]] = {
    "architect": [StepIntent.PRODUCE, StepIntent.REVIEW, StepIntent.FORESIGHT],
    "backend-engineer": [StepIntent.PRODUCE, StepIntent.TRANSFORM, StepIntent.INTEGRATE, StepIntent.REMEDIATE],
    "frontend-engineer": [StepIntent.PRODUCE, StepIntent.TRANSFORM, StepIntent.INTEGRATE],
    "test-engineer": [StepIntent.VALIDATE, StepIntent.SCAFFOLD],
    "code-reviewer": [StepIntent.REVIEW],
    "security-reviewer": [StepIntent.AUDIT],
    "devops-engineer": [StepIntent.PROVISION, StepIntent.CONFIGURE, StepIntent.SCAFFOLD],
    "data-engineer": [StepIntent.PRODUCE, StepIntent.TRANSFORM, StepIntent.PROVISION],
    "data-analyst": [StepIntent.PRODUCE, StepIntent.VALIDATE],
    "data-scientist": [StepIntent.PRODUCE, StepIntent.VALIDATE],
    "auditor": [StepIntent.AUDIT],
    "visualization-expert": [StepIntent.PRODUCE],
    "subject-matter-expert": [StepIntent.REVIEW, StepIntent.FORESIGHT],
    "talent-builder": [StepIntent.SCAFFOLD],
}


# ---------------------------------------------------------------------------
# PlanningTaxonomy — aggregated queryable taxonomy
# ---------------------------------------------------------------------------

@dataclass
class TaxonomyTerm:
    """A single term in the planning taxonomy.

    Attributes:
        name: Machine-readable identifier (enum value or key).
        label: Human-readable display label.
        family: Grouping within the vocabulary (e.g. "core_work").
        definition: One-sentence definition.
        examples: Concrete examples of this term in use.
    """

    name: str
    label: str
    family: str
    definition: str
    examples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "family": self.family,
            "definition": self.definition,
            "examples": self.examples,
        }


class PlanningTaxonomy:
    """Aggregated, queryable taxonomy for the planning object model.

    Provides structured access to all controlled vocabularies used in
    plan creation and execution.  Designed for two audiences:

    1. **Agents** — embed taxonomy terms into delegation prompts so
       agents understand the semantic structure of their work.
    2. **End users** — render as documentation or serve as structured
       data to clarify what each plan element means.

    Usage::

        tax = PlanningTaxonomy()
        # All step intents
        for term in tax.step_intents():
            print(f"{term.label}: {term.definition}")

        # Full taxonomy as serializable dict
        data = tax.to_dict()

        # Markdown rendering
        md = tax.to_markdown()
    """

    def step_intents(self) -> list[TaxonomyTerm]:
        """Return taxonomy terms for all step intents."""
        _FAMILIES = {
            StepIntent.PRODUCE: "core_work",
            StepIntent.TRANSFORM: "core_work",
            StepIntent.VALIDATE: "core_work",
            StepIntent.INTEGRATE: "core_work",
            StepIntent.SCAFFOLD: "support",
            StepIntent.PROVISION: "support",
            StepIntent.CONFIGURE: "support",
            StepIntent.REMEDIATE: "support",
            StepIntent.REVIEW: "governance",
            StepIntent.AUDIT: "governance",
            StepIntent.APPROVE: "governance",
            StepIntent.GATE: "governance",
            StepIntent.FORESIGHT: "foresight",
        }
        _LABELS = {
            StepIntent.PRODUCE: "Produce",
            StepIntent.TRANSFORM: "Transform",
            StepIntent.VALIDATE: "Validate",
            StepIntent.INTEGRATE: "Integrate",
            StepIntent.SCAFFOLD: "Scaffold",
            StepIntent.PROVISION: "Provision",
            StepIntent.CONFIGURE: "Configure",
            StepIntent.REMEDIATE: "Remediate",
            StepIntent.REVIEW: "Review",
            StepIntent.AUDIT: "Audit",
            StepIntent.APPROVE: "Approve",
            StepIntent.GATE: "Gate",
            StepIntent.FORESIGHT: "Foresight",
        }
        _EXAMPLES: dict[StepIntent, list[str]] = {
            StepIntent.PRODUCE: ["Write new API endpoints", "Create database schema", "Author documentation"],
            StepIntent.TRANSFORM: ["Refactor module structure", "Migrate from REST to GraphQL"],
            StepIntent.VALIDATE: ["Run pytest suite", "Type-check with mypy", "Lint with ruff"],
            StepIntent.INTEGRATE: ["Wire frontend to new API", "Connect event bus subscribers"],
            StepIntent.SCAFFOLD: ["Create project boilerplate", "Set up test infrastructure"],
            StepIntent.PROVISION: ["Create database tables", "Set up message queue"],
            StepIntent.CONFIGURE: ["Update environment variables", "Adjust feature flags"],
            StepIntent.REMEDIATE: ["Fix failing tests after gate failure", "Address review findings"],
            StepIntent.REVIEW: ["Code review before merge", "Architecture review of design doc"],
            StepIntent.AUDIT: ["Security audit of auth flow", "Compliance check on data handling"],
            StepIntent.APPROVE: ["Human approval of design before implementation"],
            StepIntent.GATE: ["Run test gate between phases", "Lint gate on implementation"],
            StepIntent.FORESIGHT: [
                "Add drop-records tool before data quality agent runs",
                "Insert schema validation step before migration",
                "Add rollback capability before destructive operation",
            ],
        }

        terms = []
        for intent in StepIntent:
            doc = (intent.__doc__ or intent.name).strip().split("\n")[0]
            terms.append(TaxonomyTerm(
                name=intent.value,
                label=_LABELS.get(intent, intent.name.title()),
                family=_FAMILIES.get(intent, "other"),
                definition=doc,
                examples=_EXAMPLES.get(intent, []),
            ))
        return terms

    def phase_archetypes(self) -> list[TaxonomyTerm]:
        """Return taxonomy terms for all phase archetypes."""
        _EXAMPLES: dict[PhaseArchetype, list[str]] = {
            PhaseArchetype.DISCOVERY: ["Research phase", "Investigation phase"],
            PhaseArchetype.DESIGN: ["Architecture design phase", "API design phase"],
            PhaseArchetype.PREPARATION: ["Scaffold tooling phase", "Provision infrastructure phase"],
            PhaseArchetype.IMPLEMENTATION: ["Backend implementation", "Frontend build"],
            PhaseArchetype.VERIFICATION: ["Test phase", "Validation phase"],
            PhaseArchetype.REVIEW: ["Code review phase", "Security audit phase"],
            PhaseArchetype.REMEDIATION: ["Fix gate failures", "Address review findings"],
        }
        terms = []
        for arch in PhaseArchetype:
            doc = (arch.__doc__ or arch.name).strip().split("\n")[0]
            terms.append(TaxonomyTerm(
                name=arch.value,
                label=arch.name.title(),
                family="phase_archetype",
                definition=doc,
                examples=_EXAMPLES.get(arch, []),
            ))
        return terms

    def plan_elements(self) -> list[TaxonomyTerm]:
        """Return taxonomy terms for plan structural elements."""
        terms = []
        for kind in PlanElementKind:
            doc = (kind.__doc__ or kind.name).strip().split("\n")[0]
            terms.append(TaxonomyTerm(
                name=kind.value,
                label=kind.name.replace("_", " ").title(),
                family="plan_element",
                definition=doc,
            ))
        return terms

    def to_dict(self) -> dict:
        """Serialize the full taxonomy as a structured dict."""
        return {
            "step_intents": [t.to_dict() for t in self.step_intents()],
            "phase_archetypes": [t.to_dict() for t in self.phase_archetypes()],
            "plan_elements": [t.to_dict() for t in self.plan_elements()],
            "agent_intent_affinity": {
                agent: [i.value for i in intents]
                for agent, intents in AGENT_INTENT_AFFINITY.items()
            },
        }

    def to_markdown(self) -> str:
        """Render the taxonomy as human-readable markdown."""
        lines = [
            "# Planning Taxonomy",
            "",
            "Universal semantic object model for Agent Baton execution plans.",
            "",
        ]

        # Step Intents
        lines += ["## Step Intents", "", "Why a step exists in a plan.", ""]
        current_family = ""
        for term in self.step_intents():
            if term.family != current_family:
                current_family = term.family
                family_label = current_family.replace("_", " ").title()
                lines += [f"### {family_label}", ""]
            lines.append(f"**{term.label}** (`{term.name}`)")
            lines.append(f": {term.definition}")
            if term.examples:
                for ex in term.examples:
                    lines.append(f"  - {ex}")
            lines.append("")

        # Phase Archetypes
        lines += ["## Phase Archetypes", "", "Canonical roles in the plan lifecycle.", ""]
        for term in self.phase_archetypes():
            lines.append(f"**{term.label}** (`{term.name}`)")
            lines.append(f": {term.definition}")
            if term.examples:
                for ex in term.examples:
                    lines.append(f"  - {ex}")
            lines.append("")

        # Plan Elements
        lines += ["## Plan Elements", "", "Structural building blocks of a plan.", ""]
        for term in self.plan_elements():
            lines.append(f"**{term.label}** (`{term.name}`)")
            lines.append(f": {term.definition}")
            lines.append("")

        # Agent-Intent Affinity
        lines += [
            "## Agent-Intent Affinity",
            "",
            "Which intents each agent naturally fulfils.",
            "",
        ]
        for agent, intents in sorted(AGENT_INTENT_AFFINITY.items()):
            intent_labels = ", ".join(i.value for i in intents)
            lines.append(f"- **{agent}**: {intent_labels}")
        lines.append("")

        return "\n".join(lines)
