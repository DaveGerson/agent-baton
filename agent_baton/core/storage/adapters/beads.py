"""Beads adapter — reads an external `bd` project's `.beads/issues.jsonl`.

This is the interop seam contemplated by ADR-13 / ADR-13a: it lets Agent
Baton pull work items from a project that uses the external Beads tool
(github.com/gastownhall/beads, the `bd` CLI) **without** taking a Go-binary
or Dolt runtime dependency.

It reads the documented JSONL *interchange* file only:

    "`bd export` and `.beads/issues.jsonl` are issue-table exports.  They
    are useful for review, migration, and interoperability."

The Dolt database under `.beads/` remains the source of truth on the Beads
side; this adapter never touches it and never shells out to `bd`.  Keep the
JSONL fresh on the Beads side with `bd export` (or `bd`'s auto-export) before
syncing.

Read-only
---------
The ``ExternalSourceAdapter`` protocol is read-mostly.  This adapter
implements ``connect`` / ``fetch_items`` / ``fetch_item``; writing back to
``.beads/`` is intentionally out of scope.

Field mapping (tolerant — beads marks most fields ``omitempty``)::

    bd issue field        ExternalItem field   notes
    --------------------  -------------------  ------------------------------
    id                    external_id          e.g. "bd-a1b2"
    title                 title
    description / body /  description          first non-empty wins
      design
    status                state                open|in_progress|blocked|...
    issue_type            item_type            mapped to canonical baton type
    priority              priority             coerced to int (0 when absent)
    assignee              assigned_to
    parent / epic         parent_id            best-effort
    labels / tags         tags
    updated / updated_at   updated_at          ISO-8601 when present
    (whole object)        raw_data             preserved for debugging

Usage::

    adapter = BeadsAdapter()
    adapter.connect({"beads_dir": ".beads"})   # or {"path": "/repo/.beads"}
    items = adapter.fetch_items(item_types=["bug", "feature"])
    item  = adapter.fetch_item("bd-a1b2")
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from agent_baton.core.storage.adapters import (
    AdapterRegistry,
    ExternalItem,
)

_log = logging.getLogger(__name__)

# The interchange file the Beads tool exports, relative to the .beads dir.
_ISSUES_FILE = "issues.jsonl"

# Beads issue_type values → canonical baton item_type.  Anything not listed
# (chore, message, molecule, gate, agent, role, convoy, ...) falls through to
# "task" so unknown/agent-internal types don't break a sync.
_TYPE_MAP: dict[str, str] = {
    "bug": "bug",
    "feature": "feature",
    "enhancement": "feature",
    "epic": "epic",
    "story": "story",
    "task": "task",
}


class BeadsAdapter:
    """Reads issues from an external Beads project's `.beads/issues.jsonl`.

    Config keys (passed to :py:meth:`connect`):

    - ``beads_dir`` (optional, default ``.beads``): Path to the `.beads`
      directory, absolute or relative to the current working directory.
    - ``path`` (optional alias): Accepted as a synonym for ``beads_dir``;
      may point at the directory *or* directly at an ``issues.jsonl`` file.
    """

    source_type = "beads"

    def __init__(self) -> None:
        self._issues_path: Path | None = None
        self._source_id: str = ""

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    def connect(self, config: dict) -> None:
        """Resolve and validate the path to ``issues.jsonl``.

        Args:
            config: Dict with ``beads_dir`` (or ``path``).

        Raises:
            ValueError: If the resolved ``issues.jsonl`` file does not exist.
        """
        raw = (config.get("beads_dir") or config.get("path") or ".beads").strip()
        candidate = Path(raw).expanduser()

        # Accept either a directory (append issues.jsonl) or a direct file path.
        if candidate.name == _ISSUES_FILE:
            issues_path = candidate
        else:
            issues_path = candidate / _ISSUES_FILE

        if not issues_path.exists():
            raise ValueError(
                f"Beads interchange file not found: {issues_path}.  "
                f"Run `bd export` in the Beads project to produce "
                f"{_ISSUES_FILE}, or point --config beads_dir at the right "
                f"directory."
            )

        self._issues_path = issues_path
        # Prefer the caller-supplied source_id (the one registered in
        # central.db) so synced rows join back to the registered source.
        # Fall back to a stable, filesystem-derived id for standalone use.
        supplied = str(config.get("source_id") or "").strip()
        if supplied:
            self._source_id = supplied
        else:
            slug = str(issues_path.parent.resolve()).strip("/").replace("/", "-")
            self._source_id = f"beads-{slug}" if slug else "beads"

    def fetch_items(
        self,
        item_types: list[str] | None = None,
        since: str | None = None,
    ) -> list[ExternalItem]:
        """Read and normalise every issue from the JSONL file.

        Args:
            item_types: Optional list of canonical baton item types to keep.
            since:      ISO-8601 datetime; keep only items whose ``updated_at``
                        is greater-or-equal (lexicographic compare works for
                        zero-padded ISO-8601).

        Returns:
            List of ``ExternalItem`` instances (possibly empty).

        Raises:
            RuntimeError: If the file cannot be read.
        """
        if self._issues_path is None:
            raise RuntimeError("BeadsAdapter.connect() must be called first.")

        items: list[ExternalItem] = []
        try:
            with self._issues_path.open("r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError as exc:
                        # One malformed line should not abort the whole sync.
                        _log.warning(
                            "BeadsAdapter: skipping malformed line %d in %s: %s",
                            lineno,
                            self._issues_path,
                            exc,
                        )
                        continue
                    if not isinstance(raw, dict) or not raw.get("id"):
                        continue
                    item = self._normalise(raw)
                    if item_types is not None and item.item_type not in item_types:
                        continue
                    if since is not None and item.updated_at and item.updated_at < since:
                        continue
                    items.append(item)
        except OSError as exc:
            raise RuntimeError(
                f"Failed to read Beads file {self._issues_path}: {exc}"
            ) from exc

        return items

    def fetch_item(self, external_id: str) -> ExternalItem | None:
        """Return the issue with id *external_id*, or ``None`` if absent."""
        for item in self.fetch_items():
            if item.external_id == external_id:
                return item
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalise(self, raw: dict) -> ExternalItem:
        """Convert a raw beads issue dict into an ``ExternalItem``."""
        external_id = str(raw.get("id", ""))

        # Description: first non-empty of a few likely fields.
        description = (
            raw.get("description")
            or raw.get("body")
            or raw.get("design")
            or ""
        )

        bd_type = str(raw.get("issue_type", "task")).lower()
        item_type = _TYPE_MAP.get(bd_type, "task")

        # Tags: labels/tags, plus dependency ids for visibility (deps don't map
        # onto the flat ExternalItem model, so surface them as tags + raw_data).
        tags: list[str] = []
        for key in ("labels", "tags"):
            val = raw.get(key)
            if isinstance(val, list):
                tags.extend(str(t) for t in val if t)

        # Parent: explicit field, else a parent/epic-typed dependency target.
        parent_id = str(raw.get("parent") or raw.get("epic") or "")
        if not parent_id:
            parent_id = self._parent_from_deps(raw.get("dependencies"))

        return ExternalItem(
            source_id=self._source_id,
            external_id=external_id,
            item_type=item_type,
            title=str(raw.get("title", "")),
            description=str(description),
            state=str(raw.get("status", "open")),
            assigned_to=str(raw.get("assignee", "")),
            priority=self._coerce_priority(raw.get("priority")),
            parent_id=parent_id,
            tags=tags,
            url="",  # local file — no canonical web URL
            raw_data=raw,
            updated_at=str(raw.get("updated") or raw.get("updated_at") or ""),
        )

    @staticmethod
    def _coerce_priority(value: object) -> int:
        """Coerce a beads priority into an int (0 when unknown/absent).

        Accepts ints, numeric strings, and ``p0``/``P1`` style prefixes.
        """
        if value is None:
            return 0
        if isinstance(value, bool):  # guard: bool is an int subclass
            return 0
        if isinstance(value, int):
            return value
        text = str(value).strip().lower().lstrip("p")
        try:
            return int(text)
        except ValueError:
            return 0

    @staticmethod
    def _parent_from_deps(deps: object) -> str:
        """Best-effort: find a parent/epic dependency target id.

        Beads dependency entries vary in shape across versions; handle the
        common cases tolerantly and return "" when nothing parent-like is
        found.
        """
        if not isinstance(deps, list):
            return ""
        for dep in deps:
            if not isinstance(dep, dict):
                continue
            dtype = str(dep.get("type") or dep.get("dep_type") or "").lower()
            if "parent" in dtype or "epic" in dtype:
                target = dep.get("target") or dep.get("to") or dep.get("id")
                if target:
                    return str(target)
        return ""


# Self-register on import so that
# ``import agent_baton.core.storage.adapters.beads`` enables Beads interop.
AdapterRegistry.register(BeadsAdapter)
