"""Project-level configuration for Agent Baton.

This package owns the optional ``baton.yaml`` workflow that lets a
project declare default agents, gates, isolation modes, and routing
rules so users don't have to repeat them on every ``baton plan`` call.

The config is **always optional and additive** — when no ``baton.yaml``
is present, behavior is unchanged from prior versions.
"""
from agent_baton.core.config.project_config import ProjectConfig

__all__ = ["ProjectConfig"]
