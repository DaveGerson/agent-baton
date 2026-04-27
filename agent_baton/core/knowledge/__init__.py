"""Knowledge subsystem — DB-backed lifecycle metadata for filesystem packs.

Knowledge content (markdown documents inside ``.claude/knowledge/<pack>/``)
remains on disk and is loaded by ``agent_baton.core.orchestration.knowledge_registry``.
This package layers per-document lifecycle state on top of that filesystem
view: usage counts, last-used timestamps, deprecation flags, and retirement.

Public API:

    from agent_baton.core.knowledge import KnowledgeLifecycle
"""
from __future__ import annotations

from agent_baton.core.knowledge.lifecycle import KnowledgeLifecycle

__all__ = ["KnowledgeLifecycle"]
