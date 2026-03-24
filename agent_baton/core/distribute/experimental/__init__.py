"""Experimental distribute modules — not yet validated with real usage data."""
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
