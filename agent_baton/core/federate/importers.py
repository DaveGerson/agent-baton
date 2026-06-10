"""External spec importers for the Spec Federation MVP (007 Phase I).

Provides:

- ``SpecImporter`` — protocol that importers must satisfy.
- ``GitHubIssuesImporter`` — fetches GitHub Issues via the REST API.
  Uses ``GITHUB_TOKEN`` env var for auth (optional; rate-limited when absent).
- ``AzureDevOpsImporter`` — fetches ADO work items via the REST API.
  Requires ``AZURE_DEVOPS_ORG``, ``AZURE_DEVOPS_PROJECT``, and
  ``AZURE_DEVOPS_PAT`` environment variables.  Raises ``NotImplementedError``
  with a helpful config message when any are missing.
- ``get_importer(source)`` — factory that returns the right importer.
"""
from __future__ import annotations

import base64
import os
from typing import Protocol, runtime_checkable

import httpx


# ---------------------------------------------------------------------------
# Shared result shape
# ---------------------------------------------------------------------------

class ImportedSpec:
    """A spec imported from an external source.

    Attributes:
        title: Issue/work-item title.
        body: Issue/work-item body/description.
        source_ref: URL or external ID to record.
    """

    __slots__ = ("title", "body", "source_ref")

    def __init__(self, title: str, body: str, source_ref: str) -> None:
        self.title = title
        self.body = body
        self.source_ref = source_ref


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class SpecImporter(Protocol):
    """Protocol for external spec importers."""

    def fetch(self, ref: str) -> ImportedSpec:
        """Fetch a single spec from the external source.

        Args:
            ref: Source-specific reference (e.g. issue number, URL, work item ID).

        Returns:
            An :class:`ImportedSpec` with title, body, and source_ref.

        Raises:
            NotImplementedError: When required configuration is absent.
            httpx.HTTPStatusError: When the remote API returns an error.
        """
        ...


# ---------------------------------------------------------------------------
# GitHub Issues importer
# ---------------------------------------------------------------------------

class GitHubIssuesImporter:
    """Fetch a GitHub issue as a spec draft.

    Args:
        owner: Repository owner (user or org).
        repo: Repository name.
        token: Personal access token.  Defaults to ``GITHUB_TOKEN`` env var.
            When absent, requests are unauthenticated (lower rate limit).
    """

    def __init__(
        self,
        owner: str,
        repo: str,
        token: str | None = None,
    ) -> None:
        self._owner = owner
        self._repo = repo
        self._token = token or os.environ.get("GITHUB_TOKEN", "")

    def fetch(self, ref: str) -> ImportedSpec:
        """Fetch a GitHub issue.

        Args:
            ref: Issue number (string or int) or full URL.

        Returns:
            An :class:`ImportedSpec` built from the issue's title and body.
        """
        issue_number = self._parse_ref(ref)
        url = f"https://api.github.com/repos/{self._owner}/{self._repo}/issues/{issue_number}"
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        resp = httpx.get(url, headers=headers, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()

        title = data.get("title", "")
        body = data.get("body", "") or ""
        html_url = data.get("html_url", url)
        return ImportedSpec(title=title, body=body, source_ref=html_url)

    def _parse_ref(self, ref: str) -> str:
        """Extract the issue number from a ref string or URL."""
        # Accept a bare number or a URL ending in /issues/<number>
        ref = ref.strip()
        if ref.isdigit():
            return ref
        # Try to extract from URL
        parts = ref.rstrip("/").split("/")
        for i, part in enumerate(parts):
            if part == "issues" and i + 1 < len(parts):
                return parts[i + 1]
        return ref


# ---------------------------------------------------------------------------
# Azure DevOps importer
# ---------------------------------------------------------------------------

_ADO_ORG_ENV = "AZURE_DEVOPS_ORG"
_ADO_PROJECT_ENV = "AZURE_DEVOPS_PROJECT"
_ADO_PAT_ENV = "AZURE_DEVOPS_PAT"


class AzureDevOpsImporter:
    """Fetch an Azure DevOps work item as a spec draft.

    Requires ``AZURE_DEVOPS_ORG``, ``AZURE_DEVOPS_PROJECT``, and
    ``AZURE_DEVOPS_PAT`` environment variables.

    Args:
        org: ADO organisation name.  Defaults to ``AZURE_DEVOPS_ORG``.
        project: ADO project name.  Defaults to ``AZURE_DEVOPS_PROJECT``.
        pat: Personal Access Token.  Defaults to ``AZURE_DEVOPS_PAT``.
            Requires "Work Items (Read)" scope.

    Raises:
        NotImplementedError: When any required env var is unset and no
            constructor override is provided.
    """

    def __init__(
        self,
        org: str | None = None,
        project: str | None = None,
        pat: str | None = None,
    ) -> None:
        self._org = org or os.environ.get(_ADO_ORG_ENV, "")
        self._project = project or os.environ.get(_ADO_PROJECT_ENV, "")
        self._pat = pat or os.environ.get(_ADO_PAT_ENV, "")

    def _check_config(self) -> None:
        missing = []
        if not self._org:
            missing.append(_ADO_ORG_ENV)
        if not self._project:
            missing.append(_ADO_PROJECT_ENV)
        if not self._pat:
            missing.append(_ADO_PAT_ENV)
        if missing:
            raise NotImplementedError(
                f"Azure DevOps importer requires the following environment variables "
                f"to be set: {', '.join(missing)}.  "
                f"The PAT must have 'Work Items (Read)' scope."
            )

    def fetch(self, ref: str) -> ImportedSpec:
        """Fetch an Azure DevOps work item.

        Args:
            ref: Work item ID (integer as string).

        Returns:
            An :class:`ImportedSpec` built from the work item's title and
            description.

        Raises:
            NotImplementedError: When required env vars are absent.
        """
        self._check_config()

        work_item_id = ref.strip().lstrip("#")
        url = (
            f"https://dev.azure.com/{self._org}/{self._project}"
            f"/_apis/wit/workitems/{work_item_id}"
            "?$expand=all&api-version=7.1"
        )
        token_b64 = base64.b64encode(f":{self._pat}".encode()).decode()
        headers = {"Authorization": f"Basic {token_b64}"}

        resp = httpx.get(url, headers=headers, timeout=15.0)
        resp.raise_for_status()
        data = resp.json()

        fields = data.get("fields", {})
        title = fields.get("System.Title", "")
        description = fields.get("System.Description", "") or ""
        item_url = (
            f"https://dev.azure.com/{self._org}/{self._project}"
            f"/_workitems/edit/{work_item_id}"
        )
        return ImportedSpec(title=title, body=description, source_ref=item_url)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_importer(
    source: str,
    *,
    owner: str = "",
    repo: str = "",
) -> SpecImporter:
    """Return the appropriate importer for *source*.

    Args:
        source: ``"github"`` or ``"ado"``.
        owner: GitHub repository owner (required for ``"github"``).
        repo: GitHub repository name (required for ``"github"``).

    Returns:
        A :class:`SpecImporter` instance.

    Raises:
        ValueError: When *source* is not recognised.
    """
    if source == "github":
        return GitHubIssuesImporter(owner=owner, repo=repo)
    if source == "ado":
        return AzureDevOpsImporter()
    raise ValueError(f"Unknown importer source: {source!r}")
