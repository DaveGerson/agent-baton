"""Distribute sub-package — sharing, transfer, incident response, async dispatch."""
from __future__ import annotations

from agent_baton.core.distribute.sharing import PackageBuilder, PackageManifest
from agent_baton.core.distribute.transfer import ProjectTransfer, TransferManifest
from agent_baton.core.distribute.incident import IncidentManager, IncidentTemplate, IncidentPhase
from agent_baton.core.distribute.async_dispatch import AsyncDispatcher, AsyncTask
from agent_baton.core.distribute.packager import PackageVerifier, EnhancedManifest, PackageValidationResult
from agent_baton.core.distribute.registry_client import RegistryClient

__all__ = [
    "PackageBuilder",
    "PackageManifest",
    "ProjectTransfer",
    "TransferManifest",
    "IncidentManager",
    "IncidentTemplate",
    "IncidentPhase",
    "AsyncDispatcher",
    "AsyncTask",
    "PackageVerifier",
    "EnhancedManifest",
    "PackageValidationResult",
    "RegistryClient",
]
