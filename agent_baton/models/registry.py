"""Data models for the agent-baton registry — package index entries."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RegistryEntry:
    """Metadata for a single published agent package in the registry.

    Each version of a package gets its own entry.  Entries are stored
    in a ``RegistryIndex`` and used by ``baton pull`` to resolve
    compatible packages for installation.

    Attributes:
        name: Package name (e.g. ``"data-science"``).
        version: Semantic version string.
        description: Short summary of the package contents.
        path: Relative path inside the registry repository
            (e.g. ``"packages/data-science"``).
        published_at: ISO 8601 publication timestamp.
        baton_version: Minimum compatible agent-baton version.
        agent_count: Number of agent definitions in the package.
        reference_count: Number of reference documents in the package.
    """

    name: str
    version: str
    description: str
    path: str               # relative path inside the registry repo (e.g. packages/data-science)
    published_at: str       # ISO 8601 timestamp
    baton_version: str      # minimum compatible baton version
    agent_count: int = 0
    reference_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "path": self.path,
            "published_at": self.published_at,
            "baton_version": self.baton_version,
            "agent_count": self.agent_count,
            "reference_count": self.reference_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> RegistryEntry:
        return cls(
            name=str(data.get("name", "")),
            version=str(data.get("version", "")),
            description=str(data.get("description", "")),
            path=str(data.get("path", "")),
            published_at=str(data.get("published_at", "")),
            baton_version=str(data.get("baton_version", "0.1.0")),
            agent_count=int(data.get("agent_count", 0)),  # type: ignore[arg-type]
            reference_count=int(data.get("reference_count", 0)),  # type: ignore[arg-type]
        )


@dataclass
class RegistryIndex:
    """Top-level index of all packages available in a registry repository.

    The index is fetched by ``baton pull`` to discover available packages
    and resolve version constraints.

    Attributes:
        packages: Mapping of package name to version-sorted list of
            ``RegistryEntry`` instances (oldest first).
        updated_at: ISO 8601 timestamp of the last index refresh.
    """

    # Maps package name -> list of RegistryEntry (one per version, sorted oldest->newest)
    packages: dict[str, list[RegistryEntry]] = field(default_factory=dict)
    updated_at: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "packages": {
                name: [entry.to_dict() for entry in entries]
                for name, entries in self.packages.items()
            },
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> RegistryIndex:
        raw_packages = data.get("packages", {})
        packages: dict[str, list[RegistryEntry]] = {}
        if isinstance(raw_packages, dict):
            for name, raw_entries in raw_packages.items():
                if isinstance(raw_entries, list):
                    packages[str(name)] = [
                        RegistryEntry.from_dict(e)  # type: ignore[arg-type]
                        for e in raw_entries
                        if isinstance(e, dict)
                    ]
        return cls(
            packages=packages,
            updated_at=str(data.get("updated_at", "")),
        )
