from enum import Enum

class RiskLevel(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

class TrustLevel(Enum):
    FULL_AUTONOMY = "Full Autonomy"
    SUPERVISED = "Supervised"
    RESTRICTED = "Restricted"
    PLAN_ONLY = "Plan Only"

class BudgetTier(Enum):
    LEAN = "Lean"        # 1-2 subagents
    STANDARD = "Standard" # 3-5 subagents
    FULL = "Full"        # 6-8 subagents

class ExecutionMode(Enum):
    PARALLEL = "Parallel Independent"
    SEQUENTIAL = "Sequential Pipeline"
    PHASED = "Phased Delivery"

class GateOutcome(Enum):
    PASS = "PASS"
    PASS_WITH_NOTES = "PASS WITH NOTES"
    FAIL = "FAIL"

class FailureClass(Enum):
    HARD = "Hard Failure"
    SCOPE_VIOLATION = "Scope Violation"
    QUALITY = "Quality Failure"
    PARTIAL = "Partial Success"

class GitStrategy(Enum):
    COMMIT_PER_AGENT = "Commit-per-agent"
    BRANCH_PER_AGENT = "Branch-per-agent"
    NONE = "None"

class AgentCategory(Enum):
    ENGINEERING = "Engineering"
    DATA = "Data & Analytics"
    DOMAIN = "Domain"
    REVIEW = "Review & Governance"
    META = "Meta"
