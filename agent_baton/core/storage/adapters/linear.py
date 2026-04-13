"""Linear adapter — fetches issues via the Linear GraphQL API.

Authentication
--------------
The adapter reads an API key from the environment variable named by
``config["token_env_var"]`` (default ``LINEAR_API_KEY``).  The key must
have at least **read** scope on the target team's issues.  No credentials
are persisted to disk.

GraphQL API
-----------
All requests are sent to ``https://api.linear.app/graphql`` as HTTP POST
with a ``Bearer`` token.  The adapter queries issues filtered to the
configured team using Linear's ``IssueFilter`` input type.

Pagination
----------
Results are fetched using cursor-based pagination.  Each response returns
a ``pageInfo.endCursor`` value and a ``pageInfo.hasNextPage`` boolean.
The adapter passes ``after: "{cursor}"`` in subsequent requests until
``hasNextPage`` is false.

Type mapping
------------
Linear label names (case-insensitive) are mapped to canonical baton types:

======================  ==============
Label name contains     Baton item_type
======================  ==============
"bug"                   bug
"feature"               feature
(nothing matched)       task
======================  ==============

Usage::

    from agent_baton.core.storage.adapters.linear import LinearAdapter

    adapter = LinearAdapter()
    adapter.connect({
        "team_key": "ENG",
        "token_env_var": "LINEAR_API_KEY",   # optional, default LINEAR_API_KEY
    })
    items = adapter.fetch_items(item_types=["bug", "feature"])
    item  = adapter.fetch_item("LIN-123")
"""
from __future__ import annotations

import logging
import os
from typing import Any

from agent_baton.core.storage.adapters import (
    AdapterRegistry,
    ExternalItem,
)

_log = logging.getLogger(__name__)

_LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

# Number of issues to request per page (Linear default max is 250).
_PAGE_SIZE = 100

# GraphQL query template — fetches issues for a team with optional
# updatedAt filter and cursor-based pagination.
_ISSUES_QUERY = """
query FetchIssues($filter: IssueFilter, $first: Int, $after: String) {
  issues(filter: $filter, first: $first, after: $after, orderBy: updatedAt) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      identifier
      title
      description
      state {
        name
      }
      assignee {
        displayName
      }
      priority
      parent {
        identifier
      }
      labels {
        nodes {
          name
        }
      }
      url
      updatedAt
    }
  }
}
"""

# GraphQL query to fetch a single issue by identifier (e.g. "ENG-42").
_SINGLE_ISSUE_QUERY = """
query FetchIssue($identifier: String!) {
  issue(id: $identifier) {
    id
    identifier
    title
    description
    state {
      name
    }
    assignee {
      displayName
    }
    priority
    parent {
      identifier
    }
    labels {
      nodes {
        name
      }
    }
    url
    updatedAt
  }
}
"""


class LinearAdapter:
    """Fetches issues from a Linear team using the GraphQL API.

    Config keys (passed to :py:meth:`connect`):

    - ``team_key`` (required): Linear team key (e.g. ``"ENG"``).
    - ``token_env_var`` (optional, default ``LINEAR_API_KEY``): Name of the
      environment variable holding the API key.
    """

    source_type = "linear"

    def __init__(self) -> None:
        self._team_key: str = ""
        self._token: str = ""
        self._source_id: str = ""

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def connect(self, config: dict) -> None:
        """Validate Linear connection parameters.

        Args:
            config: Dict with ``team_key`` and optionally ``token_env_var``.

        Raises:
            ValueError: If the team key or token is missing.
            ImportError: If the ``requests`` package is not installed.
        """
        self._ensure_requests()

        self._team_key = config.get("team_key", "").strip()
        if not self._team_key:
            raise ValueError("Linear adapter requires 'team_key' in config.")

        token_var = config.get("token_env_var", "LINEAR_API_KEY")
        self._token = os.environ.get(token_var, "")
        if not self._token:
            raise ValueError(
                f"Linear API key not found.  Set the '{token_var}' environment variable "
                f"to a Linear API key (https://linear.app/settings/api)."
            )

        self._source_id = f"linear-{self._team_key.lower()}"

    def fetch_items(
        self,
        item_types: list[str] | None = None,
        since: str | None = None,
    ) -> list[ExternalItem]:
        """Fetch issues from the Linear team via GraphQL.

        Pages through all issues using cursor-based pagination until
        ``hasNextPage`` is false.

        Args:
            item_types: Optional list of baton item types to include.
                        Filtering happens after normalisation because Linear
                        does not expose type directly — types are inferred
                        from labels.
            since:      ISO-8601 datetime; limits results to issues with
                        ``updatedAt >= since``.

        Returns:
            List of ``ExternalItem`` instances.

        Raises:
            RuntimeError: If any GraphQL request fails.
        """
        issue_filter: dict[str, Any] = {
            "team": {"key": {"eq": self._team_key}},
        }
        if since:
            issue_filter["updatedAt"] = {"gte": since}

        headers = self._auth_headers()
        items: list[ExternalItem] = []
        cursor: str | None = None

        while True:
            variables: dict[str, Any] = {
                "filter": issue_filter,
                "first": _PAGE_SIZE,
            }
            if cursor:
                variables["after"] = cursor

            data = self._graphql(headers, _ISSUES_QUERY, variables)
            issues_payload = data.get("issues", {})
            nodes = issues_payload.get("nodes", [])
            page_info = issues_payload.get("pageInfo", {})

            for raw in nodes:
                item = self._normalise(raw)
                if item_types is None or item.item_type in item_types:
                    items.append(item)

            has_next = page_info.get("hasNextPage", False)
            if not has_next or not nodes:
                break
            cursor = page_info.get("endCursor")

        return items

    def fetch_item(self, external_id: str) -> ExternalItem | None:
        """Fetch a single issue by its Linear identifier.

        Args:
            external_id: Linear issue identifier (e.g. ``"ENG-42"``).

        Returns:
            ``ExternalItem`` if found, ``None`` if the issue does not exist.

        Raises:
            RuntimeError: On unexpected API errors.
        """
        headers = self._auth_headers()
        try:
            data = self._graphql(
                headers,
                _SINGLE_ISSUE_QUERY,
                {"identifier": external_id},
            )
        except RuntimeError:
            return None

        raw = data.get("issue")
        if raw is None:
            return None
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
                "The 'requests' package is required for the Linear adapter.  "
                "Install it with: pip install requests"
            )

    def _auth_headers(self) -> dict[str, str]:
        """Return HTTP headers with Bearer token auth."""
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _graphql(
        self,
        headers: dict[str, str],
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a GraphQL request and return the ``data`` payload.

        Args:
            headers: Auth headers.
            query:   GraphQL query string.
            variables: Query variables dict.

        Returns:
            The ``data`` dict from the GraphQL response.

        Raises:
            RuntimeError: If the HTTP request fails or the response contains
                top-level ``errors``.
        """
        import requests as _req

        payload = {"query": query, "variables": variables}
        resp = _req.post(
            _LINEAR_GRAPHQL_URL,
            json=payload,
            headers=headers,
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Linear GraphQL request failed: HTTP {resp.status_code} — {resp.text[:200]}"
            )

        body = resp.json()
        if "errors" in body:
            first_error = body["errors"][0].get("message", "unknown error")
            raise RuntimeError(f"Linear GraphQL error: {first_error}")

        return body.get("data", {})

    def _item_type_from_labels(self, label_nodes: list[dict]) -> str:
        """Derive the canonical baton item type from a list of Linear label dicts.

        Precedence: bug > feature > task.

        Args:
            label_nodes: List of label dicts, each with a ``"name"`` key.

        Returns:
            One of ``"bug"``, ``"feature"``, or ``"task"``.
        """
        names = [lbl.get("name", "").lower() for lbl in label_nodes]
        for name in names:
            if "bug" in name:
                return "bug"
        for name in names:
            if "feature" in name:
                return "feature"
        return "task"

    def _normalise(self, raw: dict) -> ExternalItem:
        """Convert a raw Linear issue node into an ``ExternalItem``.

        Args:
            raw: A single issue node from the Linear GraphQL response.

        Returns:
            Normalised ``ExternalItem``.
        """
        identifier = raw.get("identifier", raw.get("id", ""))

        labels_payload = raw.get("labels") or {}
        label_nodes: list[dict] = labels_payload.get("nodes", [])
        item_type = self._item_type_from_labels(label_nodes)
        tag_names = [lbl.get("name", "") for lbl in label_nodes if lbl.get("name")]

        state_raw = raw.get("state") or {}
        state = state_raw.get("name", "")

        assignee_raw = raw.get("assignee")
        if assignee_raw and isinstance(assignee_raw, dict):
            assigned_to = assignee_raw.get("displayName", "")
        else:
            assigned_to = ""

        # Linear priority: 0=No priority, 1=Urgent, 2=High, 3=Medium, 4=Low.
        priority_raw = raw.get("priority")
        priority = int(priority_raw) if priority_raw is not None else 0

        parent_raw = raw.get("parent")
        parent_id = parent_raw.get("identifier", "") if parent_raw else ""

        # URL comes directly from the API response.
        url = raw.get("url", "")

        return ExternalItem(
            source_id=self._source_id,
            external_id=identifier,
            item_type=item_type,
            title=raw.get("title", ""),
            description=raw.get("description", "") or "",
            state=state,
            assigned_to=assigned_to,
            priority=priority,
            parent_id=parent_id,
            tags=tag_names,
            url=url,
            raw_data=raw,
            updated_at=raw.get("updatedAt", ""),
        )


# Self-register on import so that any caller who does
# ``import agent_baton.core.storage.adapters.linear`` gains Linear support
# without an explicit registration step.
AdapterRegistry.register(LinearAdapter)
