"""Distribution sub-package for packaging, publishing, installing, and sharing
agent-baton configurations.

This package handles the full lifecycle of distributable agent-baton packages:
building ``.tar.gz`` archives from a project's ``.claude/`` directory,
verifying package integrity via SHA-256 checksums, publishing to and pulling
from a local registry, and transferring agents/knowledge/references between
projects.

Modules:
    sharing: Core ``PackageBuilder`` for creating and installing ``.tar.gz``
        archives containing agents, references, and knowledge packs.
    packager: Enhanced ``PackageVerifier`` with checksum validation,
        dependency tracking, and comprehensive package validation via
        ``AgentValidator``.
    registry_client: ``RegistryClient`` for publishing packages to a local
        registry directory and installing them into target projects.
    experimental/transfer: ``ProjectTransfer`` for direct cross-project
        file copying of agents, knowledge packs, and references.
    experimental/async_dispatch: ``AsyncDispatcher`` for tracking
        long-running tasks via on-disk JSON files.
    experimental/incident: ``IncidentManager`` for creating and managing
        phased incident response workflows from severity-based templates.
"""
from __future__ import annotations

from agent_baton.core.distribute.sharing import PackageBuilder, PackageManifest
from agent_baton.core.distribute.experimental.transfer import ProjectTransfer, TransferManifest
from agent_baton.core.distribute.experimental.incident import IncidentManager, IncidentTemplate, IncidentPhase
from agent_baton.core.distribute.experimental.async_dispatch import AsyncDispatcher, AsyncTask
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
