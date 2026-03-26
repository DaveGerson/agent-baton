"""Experimental distribution modules -- not yet validated with real usage data.

These modules provide capabilities that are built and tested but have not
been exercised in production workflows. They may change or be removed in
future versions.

Modules:
    transfer: Cross-project file transfer of agents, knowledge packs, and
        references between ``.claude/`` directories.
    async_dispatch: On-disk task tracking for long-running asynchronous
        operations.
    incident: Phased incident response workflow management with
        severity-based templates (P1--P4).
"""
from __future__ import annotations

from agent_baton.core.distribute.experimental.incident import (
    IncidentManager,
    IncidentTemplate,
    IncidentPhase,
)
from agent_baton.core.distribute.experimental.async_dispatch import (
    AsyncDispatcher,
    AsyncTask,
)
from agent_baton.core.distribute.experimental.transfer import (
    ProjectTransfer,
    TransferManifest,
)

__all__ = [
    "IncidentManager",
    "IncidentTemplate",
    "IncidentPhase",
    "AsyncDispatcher",
    "AsyncTask",
    "ProjectTransfer",
    "TransferManifest",
]
