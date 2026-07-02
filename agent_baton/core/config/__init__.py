"""Project-level configuration for Agent Baton.

This package owns the optional ``baton.yaml`` workflow that lets a
project declare default agents, gates, isolation modes, and routing
rules so users don't have to repeat them on every ``baton plan`` call.

The config is **always optional and additive** — when no ``baton.yaml``
is present, behavior is unchanged from prior versions.
"""
from agent_baton.core.config.project_config import ProjectConfig
from agent_baton.core.config.pricing import (
    ModelPrice,
    PRICING,
    get_pricing,
    blended,
    normalise_family,
)
from agent_baton.core.config.manager import ManagerConfig, ManagerConfigError

__all__ = [
    "ProjectConfig",
    "ModelPrice",
    "PRICING",
    "get_pricing",
    "blended",
    "normalise_family",
    "ManagerConfig",
    "ManagerConfigError",
]
