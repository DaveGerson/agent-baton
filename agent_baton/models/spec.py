"""Data model for the first-class Spec entity (F0.1).

A Spec is the authoritative design artifact that precedes a plan.  Plans are
execution blueprints; Specs are the *what* and *why* that justify a plan.
Each Spec has its own lifecycle (draft → reviewed → approved → executing →
completed → archived) and may be linked to one or more MachinePlan task IDs.

Schema backing: ``specs`` and ``spec_plan_links`` tables (v16 migration).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# Valid state transitions for a Spec.
SPEC_STATES: frozenset[str] = frozenset({
    "draft",
    "reviewed",
    "approved",
    "executing",
    "completed",
    "archived",
})


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_content(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


@dataclass
class Spec:
    """A first-class specification artifact.

    Attributes:
        spec_id: Unique identifier (UUID or slug).
        project_id: Project this spec belongs to (default ``"default"``).
        author_id: Identity of the spec author.
        task_type: Inferred task category (e.g. ``"feature"``, ``"bug-fix"``).
        template_id: Name of the YAML template used to create this spec.
        title: Short human-readable title.
        state: Lifecycle state.  One of ``SPEC_STATES``.
        content: Full YAML body of the spec.
        content_hash: SHA-256 of ``content`` for cheap deduplication.
        score_json: JSON-serialised multi-dimensional scorecard.
        created_at: ISO-8601 creation timestamp.
        updated_at: ISO-8601 last-update timestamp.
        approved_at: ISO-8601 approval timestamp (empty until approved).
        approved_by: Identity of the approver (empty until approved).
        linked_plan_ids: In-memory list of linked plan task IDs (not stored
            on the ``specs`` row; populated from ``spec_plan_links`` on load).
    """

    spec_id: str
    project_id: str = "default"
    author_id: str = "local-user"
    task_type: str = ""
    template_id: str = ""
    title: str = ""
    state: str = "draft"
    content: str = ""
    content_hash: str = ""
    score_json: str = "{}"
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    approved_at: str = ""
    approved_by: str = ""
    linked_plan_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.content_hash and self.content:
            self.content_hash = _hash_content(self.content)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec_id": self.spec_id,
            "project_id": self.project_id,
            "author_id": self.author_id,
            "task_type": self.task_type,
            "template_id": self.template_id,
            "title": self.title,
            "state": self.state,
            "content": self.content,
            "content_hash": self.content_hash,
            "score_json": self.score_json,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "approved_at": self.approved_at,
            "approved_by": self.approved_by,
            "linked_plan_ids": self.linked_plan_ids,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Spec:
        return cls(
            spec_id=data["spec_id"],
            project_id=data.get("project_id", "default"),
            author_id=data.get("author_id", "local-user"),
            task_type=data.get("task_type", ""),
            template_id=data.get("template_id", ""),
            title=data.get("title", ""),
            state=data.get("state", "draft"),
            content=data.get("content", ""),
            content_hash=data.get("content_hash", ""),
            score_json=data.get("score_json", "{}"),
            created_at=data.get("created_at", _now_iso()),
            updated_at=data.get("updated_at", _now_iso()),
            approved_at=data.get("approved_at", ""),
            approved_by=data.get("approved_by", ""),
            linked_plan_ids=data.get("linked_plan_ids", []),
        )

    def score(self) -> dict[str, Any]:
        """Return the parsed scorecard dict."""
        try:
            return json.loads(self.score_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    def update_content(self, new_content: str) -> None:
        """Replace content and refresh hash + updated_at."""
        self.content = new_content
        self.content_hash = _hash_content(new_content)
        self.updated_at = _now_iso()
