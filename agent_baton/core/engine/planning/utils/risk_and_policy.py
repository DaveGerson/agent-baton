"""Risk assessment and policy validation helpers.

Extracted from ``_legacy_planner.IntelligentPlanner``.  Every function
is stateless; services are passed explicitly.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.engine.planning.rules.risk_signals import (
    RISK_ORDINAL,
    RISK_SIGNALS,
)
from agent_baton.models.enums import GitStrategy, RiskLevel

if TYPE_CHECKING:
    from agent_baton.core.govern.classifier import ClassificationResult
    from agent_baton.core.govern.policy import PolicyEngine, PolicySet, PolicyViolation
    from agent_baton.core.learn.budget_tuner import BudgetTuner
    from agent_baton.models.execution import PlanPhase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_git_strategy(risk: RiskLevel) -> GitStrategy:
    """Return the appropriate git strategy for a given risk level."""
    if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
        return GitStrategy.BRANCH_PER_AGENT
    return GitStrategy.COMMIT_PER_AGENT


# Backward-compat alias used by stages/risk.py and the shim.
_select_git_strategy = select_git_strategy


def assess_risk(task_summary: str, agents: list[str]) -> str:
    """Assess risk level from task description and structural signals.

    Returns one of ``"LOW"``, ``"MEDIUM"``, or ``"HIGH"``.
    """
    score = 0

    description_lower = task_summary.lower()
    keyword_risk = RiskLevel.LOW
    for keyword, level in RISK_SIGNALS.items():
        if keyword in description_lower:
            if RISK_ORDINAL[level] > RISK_ORDINAL[keyword_risk]:
                keyword_risk = level
    keyword_score = min(RISK_ORDINAL.get(keyword_risk, 0), 2)
    score = max(score, keyword_score)

    if len(agents) > 5:
        score = max(score, 1)

    _SENSITIVE_AGENTS = {"security-reviewer", "auditor", "devops-engineer"}
    if any(a in _SENSITIVE_AGENTS or a.startswith("devops") for a in agents):
        score = max(score, 1)

    _DESTRUCTIVE_VERBS = {
        "delete", "remove", "drop", "destroy", "reset",
        "purge", "wipe", "truncate",
    }
    desc_words = set(task_summary.lower().split())
    if desc_words & _DESTRUCTIVE_VERBS:
        score = max(score, 1)

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


def classify_to_preset_key(classification: "ClassificationResult | None") -> str:
    """Map a ClassificationResult's guardrail_preset to a PolicyEngine key."""
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
    agents: list[str],
    policy_set: "PolicySet",
    plan_phases: "list[PlanPhase]",
    policy_engine: "PolicyEngine",
) -> "list[PolicyViolation]":
    """Check each agent's assignment against the active policy set.

    Returns a deduplicated list of PolicyViolation objects.
    """
    from agent_baton.core.govern.policy import PolicyViolation

    violations: list[PolicyViolation] = []
    seen: set[str] = set()

    for phase in plan_phases:
        for step in phase.steps:
            agent = step.agent_name
            paths = list(step.context_files or [])
            tools: list[str] = []

            step_violations = policy_engine.evaluate(
                policy_set, agent, paths, tools
            )
            for v in step_violations:
                if v.rule.rule_type in ("require_agent", "require_gate"):
                    continue
                key = f"{v.agent_name}:{v.rule.name}"
                if key not in seen:
                    seen.add(key)
                    violations.append(v)

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


def select_budget_tier(
    task_type: str,
    agent_count: int,
    budget_tuner: "BudgetTuner",
) -> str:
    """Select budget tier, preferring a BudgetTuner recommendation if available."""
    try:
        recs = budget_tuner.load_recommendations()
        if recs:
            for rec in recs:
                if rec.task_type == task_type:
                    return rec.recommended_tier
    except Exception:
        pass

    if agent_count <= 2:
        return "lean"
    if agent_count <= 5:
        return "standard"
    return "full"


def detect_rag() -> bool:
    """Return True if an MCP RAG server is registered in settings.json."""
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
        mcp_servers = data.get("mcpServers", data.get("mcp", {}).get("servers", {}))
        if isinstance(mcp_servers, dict):
            for server_name in mcp_servers:
                if "rag" in str(server_name).lower():
                    return True
    return False
