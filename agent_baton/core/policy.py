"""Backward-compatible shim — canonical location: core/govern/policy.py"""
from agent_baton.core.govern.policy import (
    PolicyEngine,
    PolicySet,
    PolicyRule,
    PolicyViolation,
)

__all__ = ["PolicyEngine", "PolicySet", "PolicyRule", "PolicyViolation"]
