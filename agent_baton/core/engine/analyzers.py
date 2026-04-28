"""Analyzer Pipeline for validating MachinePlans."""
from __future__ import annotations

import logging
from typing import Any, Protocol

from agent_baton.models.execution import MachinePlan
from agent_baton.core.orchestration.router import StackProfile

logger = logging.getLogger(__name__)

class PlanValidationError(Exception):
    """Raised when an Analyzer rejects a draft MachinePlan."""
    pass

class SubscalePlanError(PlanValidationError):
    """Raised by DepthAnalyzer when a step combines too many distinct actions."""
    pass

class Analyzer(Protocol):
    """Protocol for plan validators."""
    def validate(self, plan: MachinePlan, **kwargs: Any) -> MachinePlan:
        """Validate the plan. Returns the plan (possibly mutated) or raises PlanValidationError."""
        ...

class DependencyAnalyzer:
    """Validates step DAG ordering and cyclic dependencies."""
    def validate(self, plan: MachinePlan, **kwargs: Any) -> MachinePlan:
        known_steps = set()
        for phase in plan.phases:
            for step in phase.steps:
                for dep in step.depends_on:
                    if dep not in known_steps:
                        logger.warning(
                            "Dependency '%s' for step '%s' not found before it in the plan.",
                            dep, step.step_id
                        )
                known_steps.add(step.step_id)
        return plan

class RiskAnalyzer:
    """Flags steps requiring human approval."""
    def validate(self, plan: MachinePlan, **kwargs: Any) -> MachinePlan:
        if plan.risk_level in ("HIGH", "CRITICAL"):
            for phase in plan.phases:
                if phase.name.lower() in ("implement", "deploy", "execute"):
                    phase.approval_required = True
                    if not phase.approval_description:
                        phase.approval_description = f"Approval required for {plan.risk_level} risk phase"
        return plan

class CapabilityAnalyzer:
    """Matches steps to available agents."""
    def __init__(self, registry: Any = None, router: Any = None):
        self.registry = registry
        self.router = router

    def validate(self, plan: MachinePlan, **kwargs: Any) -> MachinePlan:
        stack = kwargs.get("stack")
        for phase in plan.phases:
            for step in phase.steps:
                if self.registry and not self.registry.get(step.agent_name):
                    logger.warning("Agent '%s' not found in registry.", step.agent_name)
                
                if self.router and stack:
                    resolved = self.router.route(step.agent_name, stack=stack)
                    if resolved != step.agent_name:
                        logger.info("CapabilityAnalyzer routed %s -> %s", step.agent_name, resolved)
                        step.agent_name = resolved
        return plan

class DepthAnalyzer:
    """Rejects subscale plans.
    
    If a step contains multiple distinct actions, it fails the plan and forces 
    the Strategy to recursively decompose it into a deeper, substance-level DAG.
    """
    
    SUBSCALE_CONJUNCTIONS = [
        "research and write",
        "audit and fix",
        "analyze and implement",
        "design and build",
        "investigate and solve"
    ]

    def validate(self, plan: MachinePlan, **kwargs: Any) -> MachinePlan:
        for phase in plan.phases:
            for step in phase.steps:
                desc_lower = step.task_description.lower()
                for conjunction in self.SUBSCALE_CONJUNCTIONS:
                    if conjunction in desc_lower:
                        raise SubscalePlanError(
                            f"Subscale plan detected in step {step.step_id}. "
                            f"Description contains combined actions: '{conjunction}'. "
                            f"Must be decomposed into a deeper DAG."
                        )
        return plan
