"""Azure DevOps adapter — fetches Features, Bugs, Epics via REST API.

Authentication
--------------
The adapter reads a Personal Access Token (PAT) from the environment variable
named by ``config["pat_env_var"]`` (default ``ADO_PAT``).  The PAT must have
at least **Work Items (Read)** scope.  No credentials are persisted to disk.

REST API version
----------------
All requests use ``api-version=7.0``.  The WIQL endpoint is used for bulk
queries; the work-item batch endpoint is used to retrieve full field sets.

Type mapping
------------
ADO ``Work Item Type`` values are normalised to canonical baton item types:

=========================  ==============
ADO work item type         Baton item_type
=========================  ==============
Feature                    feature
Epic                       epic
Bug                        bug
User Story                 story
Task                       task
Issue                      bug
Test Case                  task
Product Backlog Item       story
Impediment                 bug
(anything else)            task
=========================  ==============

Usage::

    from agent_baton.core.storage.adapters.ado import AdoAdapter

    adapter = AdoAdapter()
    adapter.connect({
        "organization": "my-org",
        "project": "MyProject",
        "pat_env_var": "ADO_PAT",     # optional, default ADO_PAT
        "area_path": "MyProject\\\\Team",  # optional filter
    })
    items = adapter.fetch_items(item_types=["feature", "bug"])
    item  = adapter.fetch_item("12345")
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Any

from agent_baton.core.storage.adapters import (
    AdapterRegistry,
    ExternalItem,
)

_log = logging.getLogger(__name__)

# ADO REST API base URL pattern.
_ADO_BASE = "https://dev.azure.com/{org}/{project}/_apis"
_API_VERSION = "7.0"

# Canonical item-type mapping from ADO work item types.
_ITEM_TYPE_MAP: dict[str, str] = {
    "feature": "feature",
    "epic": "epic",
    "bug": "bug",
    "user story": "story",
    "task": "task",
    "issue": "bug",
    "test case": "task",
    "product backlog item": "story",
    "impediment": "bug",
}

# ADO fields to request in batch reads.
_FIELDS = [
    "System.Id",
    "System.Title",
    "System.Description",
    "System.WorkItemType",
    "System.State",
    "System.AssignedTo",
    "Microsoft.VSTS.Common.Priority",
    "System.Parent",
    "System.Tags",
    "System.ChangedDate",
]


class AdoAdapter:
    """Fetches work items from Azure DevOps using the REST API.

    Config keys (passed to :py:meth:`connect`):

    - ``organization`` (required): ADO organisation name.
    - ``project`` (required): ADO project name.
    - ``pat_env_var`` (optional, default ``ADO_PAT``): Name of the
      environment variable holding the Personal Access Token.
    - ``area_path`` (optional): Area path filter (e.g. ``"MyProject\\\\Team"``).
      If omitted, all work items in the project are fetched.
    """

    source_type = "ado"

    def __init__(self) -> None:
        self._org: str = ""
        self._project: str = ""
        self._pat: str = ""
        self._area_path: str = ""
        self._source_id: str = ""

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def connect(self, config: dict) -> None:
        """Validate ADO connection parameters.

        Args:
            config: Dict with ``organization``, ``project``, and optionally
                ``pat_env_var`` and ``area_path``.

        Raises:
            ValueError: If the organisation, project, or PAT is missing.
            ImportError: If the ``requests`` package is not installed.
        """
        self._ensure_requests()

        self._org = config.get("organization", "").strip()
        self._project = config.get("project", "").strip()
        if not self._org:
            raise ValueError("ADO adapter requires 'organization' in config.")
        if not self._project:
            raise ValueError("ADO adapter requires 'project' in config.")

        pat_var = config.get("pat_env_var", "ADO_PAT")
        self._pat = os.environ.get(pat_var, "")
        if not self._pat:
            raise ValueError(
                f"ADO PAT not found.  Set the '{pat_var}' environment variable "
                f"to a Personal Access Token with Work Items (Read) scope."
            )

        self._area_path = config.get("area_path", "")

        # Derive a source_id for ExternalItem population.
        self._source_id = f"ado-{self._org.lower()}-{self._project.lower()}"

    def fetch_items(
        self,
        item_types: list[str] | None = None,
        since: str | None = None,
    ) -> list[ExternalItem]:
        """Fetch work items via WIQL + batch read.

        Executes a WIQL query to get matching work item IDs, then fetches
        full field data in batches of 200 (the ADO API limit per request).

        Args:
            item_types: Optional list of baton item types to include.
                        Translated back to ADO work item types for the WIQL query.
            since:      ISO-8601 datetime; limits results to items changed
                        at or after this timestamp.

        Returns:
            List of ``ExternalItem`` instances.

        Raises:
            RuntimeError: If the WIQL or batch requests fail.
        """
        import requests as _req

        wiql = self._build_wiql(item_types, since)
        wiql_url = (
            f"https://dev.azure.com/{self._org}/{self._project}"
            f"/_apis/wit/wiql?api-version={_API_VERSION}"
        )
        headers = self._auth_headers()
        resp = _req.post(wiql_url, json={"query": wiql}, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(
                f"WIQL query failed: HTTP {resp.status_code} — {resp.text[:200]}"
            )

        work_item_refs = resp.json().get("workItems", [])
        if not work_item_refs:
            return []

        ids = [ref["id"] for ref in work_item_refs]
        items: list[ExternalItem] = []

        # Batch in chunks of 200 (ADO limit per batch call).
        for chunk_start in range(0, len(ids), 200):
            chunk = ids[chunk_start : chunk_start + 200]
            batch_items = self._fetch_batch(chunk, _req, headers)
            items.extend(batch_items)

        return items

    def fetch_item(self, external_id: str) -> ExternalItem | None:
        """Fetch a single work item by its ADO ID.

        Args:
            external_id: ADO work item ID (integer string, e.g. ``"12345"``).

        Returns:
            ``ExternalItem`` if found, ``None`` if not found.

        Raises:
            RuntimeError: On unexpected API errors (non-404).
        """
        import requests as _req

        url = (
            f"https://dev.azure.com/{self._org}/{self._project}"
            f"/_apis/wit/workitems/{external_id}"
            f"?fields={','.join(_FIELDS)}&api-version={_API_VERSION}"
        )
        headers = self._auth_headers()
        resp = _req.get(url, headers=headers, timeout=30)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RuntimeError(
                f"Fetch work item {external_id} failed: "
                f"HTTP {resp.status_code} — {resp.text[:200]}"
            )
        raw = resp.json()
        return self._normalise(raw)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_requests(self) -> None:
        """Raise ImportError with a friendly message if requests is missing."""
        try:
            import requests  # noqa: F401
        except ImportError:
            raise ImportError(
                "The 'requests' package is required for the ADO adapter.  "
                "Install it with: pip install requests"
            )

    def _auth_headers(self) -> dict[str, str]:
        """Return HTTP headers with Basic auth using the stored PAT."""
        token = base64.b64encode(f":{self._pat}".encode()).decode()
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _build_wiql(
        self,
        item_types: list[str] | None,
        since: str | None,
    ) -> str:
        """Construct a WIQL SELECT statement.

        Args:
            item_types: Baton item type filter (translated to ADO types).
            since:      ISO-8601 updated-since filter.

        Returns:
            WIQL query string.
        """
        clauses: list[str] = [
            f"[System.TeamProject] = '{self._project}'"
        ]

        if item_types:
            # Map baton types back to ADO work item type names.
            _reverse: dict[str, list[str]] = {}
            for ado_type, baton_type in _ITEM_TYPE_MAP.items():
                if baton_type in item_types:
                    # Capitalise first letter of each word (ADO convention).
                    _reverse.setdefault(baton_type, []).append(
                        ado_type.title()
                    )
            ado_types: list[str] = []
            for bt in item_types:
                ado_types.extend(_reverse.get(bt, []))
            if ado_types:
                quoted = ", ".join(f"'{t}'" for t in sorted(set(ado_types)))
                clauses.append(f"[System.WorkItemType] IN ({quoted})")

        if self._area_path:
            clauses.append(
                f"[System.AreaPath] UNDER '{self._area_path}'"
            )

        if since:
            clauses.append(f"[System.ChangedDate] >= '{since}'")

        where = " AND ".join(clauses)
        return (
            f"SELECT [System.Id] FROM WorkItems WHERE {where} "
            f"ORDER BY [System.ChangedDate] DESC"
        )

    def _fetch_batch(
        self,
        ids: list[int],
        requests_mod: Any,
        headers: dict[str, str],
    ) -> list[ExternalItem]:
        """Fetch a batch of work items by ID list.

        Args:
            ids: List of ADO work item IDs (at most 200).
            requests_mod: The imported ``requests`` module.
            headers: Auth headers.

        Returns:
            List of normalised ``ExternalItem`` instances.
        """
        id_list = ",".join(str(i) for i in ids)
        fields_param = ",".join(_FIELDS)
        url = (
            f"https://dev.azure.com/{self._org}/{self._project}"
            f"/_apis/wit/workitems"
            f"?ids={id_list}&fields={fields_param}&api-version={_API_VERSION}"
        )
        resp = requests_mod.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            _log.warning(
                "Batch fetch failed: HTTP %s — %s",
                resp.status_code,
                resp.text[:200],
            )
            return []

        raw_items = resp.json().get("value", [])
        return [self._normalise(r) for r in raw_items]

    def _normalise(self, raw: dict) -> ExternalItem:
        """Convert a raw ADO work item dict into an ``ExternalItem``.

        Args:
            raw: Full ADO REST API work item object (with ``fields`` key).

        Returns:
            Normalised ``ExternalItem``.
        """
        fields = raw.get("fields", {})
        item_id = str(raw.get("id", ""))
        ado_type = fields.get("System.WorkItemType", "")
        baton_type = _ITEM_TYPE_MAP.get(ado_type.lower(), "task")

        # AssignedTo may be a dict {"displayName": ...} or a plain string.
        assigned_raw = fields.get("System.AssignedTo", "")
        if isinstance(assigned_raw, dict):
            assigned_to = assigned_raw.get("displayName", "")
        else:
            assigned_to = str(assigned_raw)

        # Priority may be absent or None.
        priority_raw = fields.get("Microsoft.VSTS.Common.Priority")
        priority = int(priority_raw) if priority_raw is not None else 0

        # Parent is an integer ID or absent.
        parent_raw = fields.get("System.Parent")
        parent_id = str(parent_raw) if parent_raw is not None else ""

        # Tags are semicolon-separated.
        tags_raw = fields.get("System.Tags", "") or ""
        tags = [t.strip() for t in tags_raw.split(";") if t.strip()]

        # Build a web URL.
        url = (
            f"https://dev.azure.com/{self._org}/{self._project}"
            f"/_workitems/edit/{item_id}"
        )

        changed_date = fields.get("System.ChangedDate", "")

        return ExternalItem(
            source_id=self._source_id,
            external_id=item_id,
            item_type=baton_type,
            title=fields.get("System.Title", ""),
            description=fields.get("System.Description", "") or "",
            state=fields.get("System.State", ""),
            assigned_to=assigned_to,
            priority=priority,
            parent_id=parent_id,
            tags=tags,
            url=url,
            raw_data=raw,
            updated_at=changed_date,
        )


# Self-register on import so that any caller who does
# ``import agent_baton.core.storage.adapters.ado`` gains ADO support
# without an explicit registration step.
AdapterRegistry.register(AdoAdapter)
