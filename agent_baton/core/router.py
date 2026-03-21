"""Backward-compatible shim — canonical location: core/orchestration/router.py"""
from agent_baton.core.orchestration.router import (
    AgentRouter,
    StackProfile,
    PACKAGE_SIGNALS,
    FRAMEWORK_SIGNALS,
    FLAVOR_MAP,
)

__all__ = ["AgentRouter", "StackProfile", "PACKAGE_SIGNALS", "FRAMEWORK_SIGNALS", "FLAVOR_MAP"]
