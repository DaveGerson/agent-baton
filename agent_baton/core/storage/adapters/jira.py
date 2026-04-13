"""Jira adapter — fetches issues via the Jira REST API v2.

Authentication
--------------
The adapter uses HTTP Basic authentication with the Jira account email and
an API token.  The token is read from the environment variable named by
``config["token_env_var"]`` (default ``JIRA_API_TOKEN``).  No credentials
are persisted to disk.

REST API
--------
Issues are retrieved using the JQL search endpoint:
``POST {url}/rest/api/2/search``.  All issues in the configured project are
returned ordered by ``updated DESC``.  A ``since`` filter is expressed as
``AND updated >= "{since}"``.

Pagination
----------
Results are fetched in pages of 50 using Jira's ``startAt`` / ``maxResults``
pagination parameters.  The adapter pages until ``startAt + maxResults``
exceeds ``total``.

Type mapping
------------
Jira issue types are normalised to canonical baton item types:

=====================  ==============
Jira issue type        Baton item_type
=====================  ==============
Bug                    bug
Story                  story
Epic                   epic
Task                   task
Sub-task               task
(anything else)        feature
=====================  ==============

Usage::

    from agent_baton.core.storage.adapters.jira import JiraAdapter

    adapter = JiraAdapter()
    adapter.connect({
        "url": "https://my-org.atlassian.net",
        "project": "MYPROJ",
        "email": "user@example.com",
        "token_env_var": "JIRA_API_TOKEN",   # optional, default JIRA_API_TOKEN
    })
    items = adapter.fetch_items(item_types=["bug", "story"])
    item  = adapter.fetch_item("MYPROJ-123")
"""
from __future__ import annotations

import base64
import logging
import os
from urllib.parse import urlparse

from agent_baton.core.storage.adapters import (
    AdapterRegistry,
    ExternalItem,
)

_log = logging.getLogger(__name__)

# Number of issues to request per page.
_PAGE_SIZE = 50

# Canonical item-type mapping from Jira issue types.
_ITEM_TYPE_MAP: dict[str, str] = {
    "bug": "bug",
    "story": "story",
    "epic": "epic",
    "task": "task",
    "sub-task": "task",
    "subtask": "task",
}


class JiraAdapter:
    """Fetches issues from a Jira project using the REST API v2.

    Config keys (passed to :py:meth:`connect`):

    - ``url`` (required): Base URL of the Jira instance
      (e.g. ``"https://my-org.atlassian.net"``).
    - ``project`` (required): Jira project key (e.g. ``"MYPROJ"``).
    - ``email`` (required): Jira account email address used for Basic auth.
    - ``token_env_var`` (optional, default ``JIRA_API_TOKEN``): Name of the
      environment variable holding the API token.
    """

    source_type = "jira"

    def __init__(self) -> None:
        self._base_url: str = ""
        self._project: str = ""
        self._email: str = ""
        self._token: str = ""
        self._source_id: str = ""

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def connect(self, config: dict) -> None:
        """Validate Jira connection parameters.

        Args:
            config: Dict with ``url``, ``project``, ``email``, and optionally
                ``token_env_var``.

        Raises:
            ValueError: If the URL, project, email, or token is missing.
            ImportError: If the ``requests`` package is not installed.
        """
        self._ensure_requests()

        self._base_url = config.get("url", "").strip().rstrip("/")
        self._project = config.get("project", "").strip()
        self._email = config.get("email", "").strip()

        if not self._base_url:
            raise ValueError("Jira adapter requires 'url' in config.")
        if not self._project:
            raise ValueError("Jira adapter requires 'project' in config.")
        if not self._email:
            raise ValueError("Jira adapter requires 'email' in config.")

        token_var = config.get("token_env_var", "JIRA_API_TOKEN")
        self._token = os.environ.get(token_var, "")
        if not self._token:
            raise ValueError(
                f"Jira API token not found.  Set the '{token_var}' environment variable "
                f"to an Atlassian API token (https://id.atlassian.com/manage/api-tokens)."
            )

        # Derive domain component for source_id (strip port, lowercase).
        parsed = urlparse(self._base_url)
        domain = (parsed.hostname or self._base_url).lower()

        self._source_id = f"jira-{domain}-{self._project.lower()}"

    def fetch_items(
        self,
        item_types: list[str] | None = None,
        since: str | None = None,
    ) -> list[ExternalItem]:
        """Fetch issues from the Jira project via JQL search.

        Pages through results _PAGE_SIZE at a time using ``startAt``
        pagination.

        Args:
            item_types: Optional list of baton item types to include.
                        Filtering happens after normalisation.
            since:      ISO-8601 datetime; limits results to issues updated
                        at or after this timestamp.

        Returns:
            List of ``ExternalItem`` instances.

        Raises:
            RuntimeError: If any API request fails.
        """
        import requests as _req

        jql = f"project = {self._project} ORDER BY updated DESC"
        if since:
            jql = f'project = {self._project} AND updated >= "{since}" ORDER BY updated DESC'

        headers = self._auth_headers()
        search_url = f"{self._base_url}/rest/api/2/search"
        items: list[ExternalItem] = []
        start_at = 0

        while True:
            payload = {
                "jql": jql,
                "startAt": start_at,
                "maxResults": _PAGE_SIZE,
                "fields": [
                    "summary",
                    "description",
                    "issuetype",
                    "status",
                    "assignee",
                    "priority",
                    "parent",
                    "subtasks",
                    "labels",
                    "updated",
                ],
            }
            resp = _req.post(search_url, json=payload, headers=headers, timeout=30)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Jira search failed: HTTP {resp.status_code} — {resp.text[:200]}"
                )

            data = resp.json()
            raw_issues = data.get("issues", [])
            total = data.get("total", 0)

            for raw in raw_issues:
                item = self._normalise(raw)
                if item_types is None or item.item_type in item_types:
                    items.append(item)

            start_at += len(raw_issues)
            if start_at >= total or not raw_issues:
                break

        return items

    def fetch_item(self, external_id: str) -> ExternalItem | None:
        """Fetch a single issue by its Jira issue key.

        Args:
            external_id: Jira issue key (e.g. ``"MYPROJ-123"``).

        Returns:
            ``ExternalItem`` if found, ``None`` if the issue does not exist.

        Raises:
            RuntimeError: On unexpected API errors (non-404).
        """
        import requests as _req

        url = f"{self._base_url}/rest/api/2/issue/{external_id}"
        headers = self._auth_headers()
        resp = _req.get(url, headers=headers, timeout=30)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RuntimeError(
                f"Fetch issue {external_id} failed: "
                f"HTTP {resp.status_code} — {resp.text[:200]}"
            )
        return self._normalise(resp.json())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_requests(self) -> None:
        """Raise ImportError with a friendly message if requests is missing."""
        try:
            import requests  # noqa: F401
        except ImportError:
            raise ImportError(
                "The 'requests' package is required for the Jira adapter.  "
                "Install it with: pip install requests"
            )

    def _auth_headers(self) -> dict[str, str]:
        """Return HTTP headers with Basic auth (email:token)."""
        token = base64.b64encode(
            f"{self._email}:{self._token}".encode()
        ).decode()
        return {
            "Authorization": f"Basic {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _normalise(self, raw: dict) -> ExternalItem:
        """Convert a raw Jira issue dict into an ``ExternalItem``.

        Args:
            raw: Full Jira REST API issue object (with ``fields`` key).

        Returns:
            Normalised ``ExternalItem``.
        """
        fields = raw.get("fields", {})
        issue_key = raw.get("key", "")

        issue_type_raw = fields.get("issuetype") or {}
        issue_type_name = issue_type_raw.get("name", "")
        baton_type = _ITEM_TYPE_MAP.get(issue_type_name.lower(), "feature")

        # Status may be a nested dict.
        status_raw = fields.get("status") or {}
        state = status_raw.get("name", "")

        # Assignee may be a dict with displayName or absent.
        assignee_raw = fields.get("assignee")
        if assignee_raw and isinstance(assignee_raw, dict):
            assigned_to = assignee_raw.get("displayName", "")
        else:
            assigned_to = ""

        # Priority may be a dict with name, or absent.
        priority_raw = fields.get("priority")
        if priority_raw and isinstance(priority_raw, dict):
            # Jira priority names: Highest=1, High=2, Medium=3, Low=4, Lowest=5
            _priority_name_map: dict[str, int] = {
                "highest": 1,
                "high": 2,
                "medium": 3,
                "low": 4,
                "lowest": 5,
            }
            priority = _priority_name_map.get(
                priority_raw.get("name", "").lower(), 0
            )
        else:
            priority = 0

        # Parent issue key — present for sub-tasks and issues with a parent.
        parent_raw = fields.get("parent")
        parent_id = parent_raw.get("key", "") if parent_raw else ""

        # Labels is a list of plain strings.
        tags: list[str] = fields.get("labels") or []

        url = f"{self._base_url}/browse/{issue_key}"
        updated_at = fields.get("updated", "")

        return ExternalItem(
            source_id=self._source_id,
            external_id=issue_key,
            item_type=baton_type,
            title=fields.get("summary", ""),
            description=fields.get("description", "") or "",
            state=state,
            assigned_to=assigned_to,
            priority=priority,
            parent_id=parent_id,
            tags=tags,
            url=url,
            raw_data=raw,
            updated_at=updated_at,
        )


# Self-register on import so that any caller who does
# ``import agent_baton.core.storage.adapters.jira`` gains Jira support
# without an explicit registration step.
AdapterRegistry.register(JiraAdapter)
