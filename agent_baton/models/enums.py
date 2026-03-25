"""Shared enumerations for the orchestration engine.

These enums define the controlled vocabularies used throughout the plan,
execution, and observation layers.  They appear in persisted state
(``execution-state.json``, usage logs, retrospectives) and in CLI output,
so their string values are part of the public API.
"""

from enum import Enum


class RiskLevel(Enum):
    """Risk classification assigned to a task by the DataClassifier.

    Determines the trust level, gate strictness, and human-intervention
    thresholds applied during execution.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TrustLevel(Enum):
    """Autonomy level granted to agents during execution.

    Derived from risk level — higher risk reduces autonomy and increases
    the frequency of human checkpoints.
    """

    FULL_AUTONOMY = "Full Autonomy"
    SUPERVISED = "Supervised"
    RESTRICTED = "Restricted"
    PLAN_ONLY = "Plan Only"


class BudgetTier(Enum):
    """Token and agent budget allocated to a plan.

    The planner selects a tier based on task complexity.  The budget
    tuner may recommend tier changes via ``BudgetRecommendation``.
    """

    LEAN = "Lean"        # 1-2 subagents
    STANDARD = "Standard" # 3-5 subagents
    FULL = "Full"        # 6-8 subagents


class ExecutionMode(Enum):
    """Strategy for ordering steps within a plan.

    Chosen by the planner based on inter-step dependencies and the
    nature of the task.
    """

    PARALLEL = "Parallel Independent"
    SEQUENTIAL = "Sequential Pipeline"
    PHASED = "Phased Delivery"


class GateOutcome(Enum):
    """Result of a QA gate check between execution phases."""

    PASS = "PASS"
    PASS_WITH_NOTES = "PASS WITH NOTES"
    FAIL = "FAIL"


class FailureClass(Enum):
    """Classification of how a step or agent failed.

    Used in mission log entries and retrospectives to distinguish
    recoverable quality issues from hard failures.
    """

    HARD = "Hard Failure"
    SCOPE_VIOLATION = "Scope Violation"
    QUALITY = "Quality Failure"
    PARTIAL = "Partial Success"


class GitStrategy(Enum):
    """Version-control strategy applied during plan execution.

    Determines how the orchestrator commits agent work — either one
    commit per agent dispatch or one branch per agent.
    """

    COMMIT_PER_AGENT = "Commit-per-agent"
    BRANCH_PER_AGENT = "Branch-per-agent"
    NONE = "None"


class AgentCategory(Enum):
    """Functional grouping of agents for routing and reporting.

    Assigned by ``AgentDefinition.category`` based on the agent's
    base name and used by the router and dashboard.
    """

    ENGINEERING = "Engineering"
    DATA = "Data & Analytics"
    DOMAIN = "Domain"
    REVIEW = "Review & Governance"
    META = "Meta"
