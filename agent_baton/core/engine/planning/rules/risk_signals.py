"""Risk-signal keyword tables.

The planner combines (a) keyword presence in the task summary, (b)
agent roster heuristics, and (c) destructive-verb detection to settle
on a final ``RiskLevel``.  This module owns only the keyword table and
ordinal mapping; the combining logic lives in
``planning.stages.classification``.
"""
from __future__ import annotations

from agent_baton.models.enums import RiskLevel

# Keyword → minimum risk floor.  When the task summary contains the key
# (case-insensitive substring match), the plan's risk is at least this
# level.  Multiple matches use the maximum.
RISK_SIGNALS: dict[str, RiskLevel] = {
    "production": RiskLevel.HIGH,
    "infrastructure": RiskLevel.HIGH,
    "docker": RiskLevel.HIGH,
    "ci/cd": RiskLevel.HIGH,
    "deploy": RiskLevel.HIGH,
    "terraform": RiskLevel.HIGH,
    "compliance": RiskLevel.HIGH,
    "regulated": RiskLevel.HIGH,
    "audit": RiskLevel.MEDIUM,
    "migration": RiskLevel.MEDIUM,
    "database": RiskLevel.MEDIUM,
    "schema": RiskLevel.MEDIUM,
    "bash": RiskLevel.MEDIUM,
    "security": RiskLevel.HIGH,
    "authentication": RiskLevel.HIGH,
    "secrets": RiskLevel.HIGH,
}

# Numeric ordering for risk level comparisons.  Higher = more risky.
RISK_ORDINAL: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

# First-word "read-only" verbs that dampen the risk score one level when
# no sensitive agents (security-reviewer, auditor, devops-*) are on the
# roster.  Catches "review the production deploy script" → LOW.
READ_ONLY_FIRST_VERBS: frozenset[str] = frozenset({
    "review", "analyze", "analyse", "audit", "inspect", "investigate",
    "evaluate", "assess", "summarize", "summarise", "describe",
    "document", "explain", "compare",
})

# Destructive verbs that bump risk to at least MEDIUM regardless of
# other signals.  These describe actions whose effects are hard to undo.
DESTRUCTIVE_VERBS: frozenset[str] = frozenset({
    "delete", "drop", "purge", "wipe", "destroy", "remove",
    "truncate", "reset", "rollback",
})

# Agent roles that, when present on a task, raise the risk floor to
# at least MEDIUM (these agents only get pulled in for sensitive work).
SENSITIVE_AGENT_PREFIXES: tuple[str, ...] = (
    "security-reviewer",
    "auditor",
    "devops-",
)


# Backward-compat aliases for legacy code that imports the underscored names.
_RISK_SIGNALS = RISK_SIGNALS
_RISK_ORDINAL = RISK_ORDINAL
