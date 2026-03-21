"""Data models for the agent-baton registry — package index entries."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RegistryEntry:
    """A single published package entry in the registry index."""

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
    """Top-level index of all packages in a registry repo."""

    # Maps package name → list of RegistryEntry (one per version, sorted oldest→newest)
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
