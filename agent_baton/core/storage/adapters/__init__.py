"""External source adapter protocol and registry.

An *external source adapter* bridges a third-party work-tracking system
(Azure DevOps, Jira, GitHub, Linear) and Agent Baton's central.db.

Protocol
--------
- ``ExternalSourceAdapter`` — structural Protocol with ``source_type``,
  ``connect()``, ``fetch_items()``, and ``fetch_item()`` methods.
- ``ExternalItem`` — normalised dataclass for a single work item.
- ``AdapterRegistry`` — class-level registry; adapters self-register on import
  via ``AdapterRegistry.register(MyAdapter)``.

Usage::

    from agent_baton.core.storage.adapters import AdapterRegistry, ExternalItem
    import agent_baton.core.storage.adapters.ado  # triggers ADO registration

    cls = AdapterRegistry.get("ado")
    adapter = cls()
    adapter.connect({"organization": "myorg", "project": "myproj", "pat_env_var": "ADO_PAT"})
    items: list[ExternalItem] = adapter.fetch_items()
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class ExternalItem:
    """Normalised work item from any external source.

    Attributes:
        source_id:    Baton source ID (matches ``external_sources.source_id``).
        external_id:  Unique ID in the source system (e.g. ADO work item number).
        item_type:    Canonical type: ``feature`` | ``bug`` | ``epic`` | ``story`` | ``task``.
        title:        Short human-readable title.
        description:  Full description or body text.
        state:        Current workflow state string from the source system.
        assigned_to:  Display name of the current assignee, if any.
        priority:     Numeric priority (lower = higher priority; 0 = unset).
        parent_id:    ``external_id`` of the parent item, or empty string.
        tags:         List of tag/label strings.
        url:          Direct link to the item in the source system's web UI.
        raw_data:     Full JSON payload from the source API (for debugging / future fields).
        updated_at:   ISO-8601 timestamp of the last update in the source system.
    """

    source_id: str
    external_id: str
    item_type: str
    title: str
    description: str = ""
    state: str = ""
    assigned_to: str = ""
    priority: int = 0
    parent_id: str = ""
    tags: list[str] = field(default_factory=list)
    url: str = ""
    raw_data: dict | None = None
    updated_at: str = ""


@runtime_checkable
class ExternalSourceAdapter(Protocol):
    """Protocol for external work-tracking system adapters.

    Concrete adapters must set ``source_type`` as a class attribute and
    implement ``connect()``, ``fetch_items()``, and ``fetch_item()``.

    Adapters self-register by calling ``AdapterRegistry.register(cls)``
    at module level so that a plain ``import agent_baton.core.storage.adapters.ado``
    is sufficient to make the ADO adapter available.
    """

    source_type: str  # "ado" | "jira" | "github" | "linear"

    def connect(self, config: dict) -> None:
        """Validate and store connection credentials from *config*.

        Args:
            config: Mapping with source-specific keys such as ``organization``,
                ``project``, ``pat_env_var``, ``url``.

        Raises:
            ValueError: If required credentials are absent or invalid.
        """
        ...

    def fetch_items(
        self,
        item_types: list[str] | None = None,
        since: str | None = None,
    ) -> list[ExternalItem]:
        """Fetch work items from the external system.

        Args:
            item_types: Optional filter — only return items of these types.
                        ``None`` returns all supported types.
            since:      ISO-8601 datetime string; only return items updated
                        at or after this time.  ``None`` returns all items.

        Returns:
            List of ``ExternalItem`` instances, possibly empty.
        """
        ...

    def fetch_item(self, external_id: str) -> ExternalItem | None:
        """Fetch a single item by its external ID.

        Args:
            external_id: The item's ID in the source system.

        Returns:
            An ``ExternalItem`` if found, otherwise ``None``.
        """
        ...


class AdapterRegistry:
    """Class-level registry mapping source-type strings to adapter classes.

    Adapters register themselves at import time::

        # At the bottom of ado.py:
        AdapterRegistry.register(AdoAdapter)

    Callers discover available adapters via ``AdapterRegistry.available()``
    and instantiate them via ``AdapterRegistry.get(source_type)()``.
    """

    _adapters: dict[str, type] = {}

    @classmethod
    def register(cls, adapter_class: type) -> None:
        """Register an adapter class under its ``source_type``.

        Args:
            adapter_class: A class that satisfies ``ExternalSourceAdapter``.
        """
        cls._adapters[adapter_class.source_type] = adapter_class

    @classmethod
    def get(cls, source_type: str) -> type | None:
        """Return the adapter class for *source_type*, or ``None``.

        Args:
            source_type: One of ``"ado"``, ``"jira"``, ``"github"``, ``"linear"``.
        """
        return cls._adapters.get(source_type)

    @classmethod
    def available(cls) -> list[str]:
        """Return sorted list of registered source type strings."""
        return sorted(cls._adapters.keys())
