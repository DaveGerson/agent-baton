"""GitHub adapter — fetches Issues and Pull Requests via REST API v3.

Authentication
--------------
The adapter reads a Personal Access Token from the environment variable
named by ``config["token_env_var"]`` (default ``GITHUB_TOKEN``).  The
token must have at least **repo** (or **public_repo**) scope.  No
credentials are persisted to disk.

REST API
--------
All requests target ``https://api.github.com/repos/{owner}/{repo}/issues``.
Pull requests are included because GitHub's issues endpoint returns both
(they share a number sequence).  Items with a ``pull_request`` key in the
raw payload are excluded from the results — only plain issues are returned.

Pagination
----------
Results are fetched 100 at a time using the ``per_page=100`` parameter.
The adapter follows ``Link: <url>; rel="next"`` headers until no next
page is present.

Type mapping
------------
Label names are inspected (case-insensitive) to assign a canonical type:

======================  ==============
Label text contains     Baton item_type
======================  ==============
"bug"                   bug
"feature" or            feature
"enhancement"
"epic"                  epic
(nothing matched)       task
======================  ==============

The first matching rule wins.  If multiple labels match different rules,
precedence follows the order above.

Usage::

    from agent_baton.core.storage.adapters.github import GitHubAdapter

    adapter = GitHubAdapter()
    adapter.connect({
        "owner": "my-org",
        "repo": "my-repo",
        "token_env_var": "GITHUB_TOKEN",   # optional, default GITHUB_TOKEN
    })
    items = adapter.fetch_items(item_types=["bug", "feature"])
    item  = adapter.fetch_item("42")
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import parse_qs, urlparse

from agent_baton.core.storage.adapters import (
    AdapterRegistry,
    ExternalItem,
)

_log = logging.getLogger(__name__)

# GitHub REST API base URL.
_GH_API_BASE = "https://api.github.com"


class GitHubAdapter:
    """Fetches issues from a GitHub repository using the REST API v3.

    Config keys (passed to :py:meth:`connect`):

    - ``owner`` (required): GitHub organisation or user name.
    - ``repo`` (required): Repository name.
    - ``token_env_var`` (optional, default ``GITHUB_TOKEN``): Name of the
      environment variable holding the Personal Access Token.
    """

    source_type = "github"

    def __init__(self) -> None:
        self._owner: str = ""
        self._repo: str = ""
        self._token: str = ""
        self._source_id: str = ""

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def connect(self, config: dict) -> None:
        """Validate GitHub connection parameters.

        Args:
            config: Dict with ``owner``, ``repo``, and optionally
                ``token_env_var``.

        Raises:
            ValueError: If the owner, repo, or token is missing.
            ImportError: If the ``requests`` package is not installed.
        """
        self._ensure_requests()

        self._owner = config.get("owner", "").strip()
        self._repo = config.get("repo", "").strip()
        if not self._owner:
            raise ValueError("GitHub adapter requires 'owner' in config.")
        if not self._repo:
            raise ValueError("GitHub adapter requires 'repo' in config.")

        token_var = config.get("token_env_var", "GITHUB_TOKEN")
        self._token = os.environ.get(token_var, "")
        if not self._token:
            raise ValueError(
                f"GitHub token not found.  Set the '{token_var}' environment variable "
                f"to a Personal Access Token with repo (or public_repo) scope."
            )

        self._source_id = f"github-{self._owner.lower()}-{self._repo.lower()}"

    def fetch_items(
        self,
        item_types: list[str] | None = None,
        since: str | None = None,
    ) -> list[ExternalItem]:
        """Fetch issues from the GitHub repository.

        Pages through the issues endpoint 100 items at a time, following
        ``Link`` headers until no next page is present.  Pull requests
        (which share the same endpoint) are silently skipped.

        Args:
            item_types: Optional list of baton item types to include.
                        Filtering happens after normalisation because GitHub
                        does not support type filtering natively.
            since:      ISO-8601 datetime; limits results to issues updated
                        at or after this timestamp (maps to ``?since=``).

        Returns:
            List of ``ExternalItem`` instances.

        Raises:
            RuntimeError: If any API request fails.
        """
        import requests as _req

        params: dict[str, Any] = {
            "state": "all",
            "per_page": 100,
        }
        if since:
            params["since"] = since

        url: str | None = (
            f"{_GH_API_BASE}/repos/{self._owner}/{self._repo}/issues"
        )
        headers = self._auth_headers()
        items: list[ExternalItem] = []

        while url is not None:
            resp = _req.get(url, headers=headers, params=params, timeout=30)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"GitHub issues fetch failed: HTTP {resp.status_code} — {resp.text[:200]}"
                )

            raw_issues = resp.json()
            for raw in raw_issues:
                # Skip pull requests — they appear on the issues endpoint but
                # are not work items in the traditional sense.
                if "pull_request" in raw:
                    continue
                item = self._normalise(raw)
                if item_types is None or item.item_type in item_types:
                    items.append(item)

            # Follow pagination via Link header.
            url = self._next_page_url(resp)
            # Clear params on subsequent requests — the next URL already
            # encodes them.
            params = {}

        return items

    def fetch_item(self, external_id: str) -> ExternalItem | None:
        """Fetch a single issue by its issue number.

        Args:
            external_id: GitHub issue number as a string (e.g. ``"42"``).

        Returns:
            ``ExternalItem`` if found, ``None`` if the issue does not exist.

        Raises:
            RuntimeError: On unexpected API errors (non-404).
        """
        import requests as _req

        url = (
            f"{_GH_API_BASE}/repos/{self._owner}/{self._repo}"
            f"/issues/{external_id}"
        )
        headers = self._auth_headers()
        resp = _req.get(url, headers=headers, timeout=30)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise RuntimeError(
                f"Fetch issue {external_id} failed: "
                f"HTTP {resp.status_code} — {resp.text[:200]}"
            )
        raw = resp.json()
        if "pull_request" in raw:
            # The caller asked for an issue number that is actually a PR.
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
                "The 'requests' package is required for the GitHub adapter.  "
                "Install it with: pip install requests"
            )

    def _auth_headers(self) -> dict[str, str]:
        """Return HTTP headers with Bearer token auth."""
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _item_type_from_labels(self, labels: list[dict]) -> str:
        """Derive the canonical baton item type from a list of GitHub label objects.

        Precedence: bug > feature/enhancement > epic > task.

        Args:
            labels: List of label dicts from the GitHub API, each with a
                ``"name"`` key.

        Returns:
            One of ``"bug"``, ``"feature"``, ``"epic"``, or ``"task"``.
        """
        names = [lbl.get("name", "").lower() for lbl in labels]
        for name in names:
            if "bug" in name:
                return "bug"
        for name in names:
            if "feature" in name or "enhancement" in name:
                return "feature"
        for name in names:
            if "epic" in name:
                return "epic"
        return "task"

    def _next_page_url(self, resp: Any) -> str | None:
        """Parse the ``Link`` response header and return the next-page URL.

        Args:
            resp: A ``requests.Response`` object.

        Returns:
            URL string for the next page, or ``None`` if this is the last page.
        """
        link_header = resp.headers.get("Link", "")
        if not link_header:
            return None
        for part in link_header.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                # Extract URL between < and >
                try:
                    return part.split(";")[0].strip().lstrip("<").rstrip(">")
                except IndexError:
                    return None
        return None

    def _normalise(self, raw: dict) -> ExternalItem:
        """Convert a raw GitHub issue dict into an ``ExternalItem``.

        Args:
            raw: Full GitHub REST API issue object.

        Returns:
            Normalised ``ExternalItem``.
        """
        issue_number = str(raw.get("number", ""))
        labels: list[dict] = raw.get("labels", []) or []
        item_type = self._item_type_from_labels(labels)
        tag_names = [lbl.get("name", "") for lbl in labels if lbl.get("name")]

        # Assignee may be a single object or absent.
        assignee_raw = raw.get("assignee")
        if assignee_raw and isinstance(assignee_raw, dict):
            assigned_to = assignee_raw.get("login", "")
        else:
            assigned_to = ""

        # Milestone can serve as a loose parent reference.
        milestone = raw.get("milestone")
        parent_id = str(milestone["number"]) if milestone else ""

        url = f"https://github.com/{self._owner}/{self._repo}/issues/{issue_number}"

        return ExternalItem(
            source_id=self._source_id,
            external_id=issue_number,
            item_type=item_type,
            title=raw.get("title", ""),
            description=raw.get("body", "") or "",
            state=raw.get("state", ""),
            assigned_to=assigned_to,
            priority=0,  # GitHub issues have no built-in numeric priority
            parent_id=parent_id,
            tags=tag_names,
            url=url,
            raw_data=raw,
            updated_at=raw.get("updated_at", ""),
        )


# Self-register on import so that any caller who does
# ``import agent_baton.core.storage.adapters.github`` gains GitHub support
# without an explicit registration step.
AdapterRegistry.register(GitHubAdapter)
