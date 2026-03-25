"""Orchestration sub-package -- agent discovery, routing, and context management.

This package is responsible for the "who does the work" layer of Agent Baton.
It answers three questions at planning and dispatch time:

1. **What agents are available?** -- :class:`AgentRegistry` loads agent
   definitions from markdown files on disk (project-local and global).

2. **Which agent variant should handle a task?** -- :class:`AgentRouter`
   detects the project's technology stack and maps generic role names
   (e.g. ``backend-engineer``) to the best-fit flavor
   (e.g. ``backend-engineer--python``).

3. **What does the agent need to know?** -- :class:`KnowledgeRegistry`
   indexes knowledge packs and resolves them to agents or tasks using
   tag matching and TF-IDF relevance search.

:class:`ContextManager` manages the shared ``team-context/`` directory
tree that agents read from and write to during execution, including
plans, shared context documents, mission logs, and codebase profiles.
"""
from __future__ import annotations

from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.core.orchestration.router import AgentRouter
from agent_baton.core.orchestration.context import ContextManager
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry

__all__ = [
    "AgentRegistry",
    "AgentRouter",
    "ContextManager",
    "KnowledgeRegistry",
]
