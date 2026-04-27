"""Knowledge subsystem — effectiveness analytics + DB-backed lifecycle metadata.

Knowledge content (markdown documents inside ``.claude/knowledge/<pack>/``)
remains on disk and is loaded by ``agent_baton.core.orchestration.knowledge_registry``.
This package layers two complementary read/write surfaces on top of that
filesystem view:

* **Effectiveness analytics** (read-only): attachment counts, success
  rates, ROI per kilo-token, and stale-doc detection — see
  ``effectiveness.py``.
* **Lifecycle metadata** (read/write): per-document usage counts,
  last-used timestamps, deprecation flags, and retirement — see
  ``lifecycle.py`` (K2.3).

Public API:

    from agent_baton.core.knowledge import KnowledgeLifecycle
"""
from __future__ import annotations

from agent_baton.core.knowledge.lifecycle import KnowledgeLifecycle

__all__ = ["KnowledgeLifecycle"]
