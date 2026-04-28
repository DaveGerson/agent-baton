"""Release entity for plan/spec target-release tagging (R3.1).

A :class:`Release` is a lightweight tag that groups plans and specs together
under a single named delivery target — a SemVer release (``"v2.5.0"``), a
quarterly bucket (``"2026-Q2-stability"``), or any other string the team
agrees on.  The entity is intentionally minimal: it carries identity, a
human-friendly name, an optional target date, lifecycle status, and free-form
notes.

This is the foundational piece for the R3.x roadmap (R3.2 release notes,
R3.3 release dashboards, R3.4 release-burnup metrics, R3.5 freeze gating).
By itself it adds zero friction: tagging is opt-in metadata only and does
not affect plan execution or gating.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# Allowed lifecycle states for a release.  Stored as a TEXT column rather
# than a CHECK constraint so future workflow extensions don't require a
# schema migration.
RELEASE_STATUSES: tuple[str, ...] = ("planned", "active", "released", "cancelled")


def _utcnow() -> str:
    """Return ISO 8601 UTC timestamp at second precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Release:
    """A named delivery target that plans/specs can be tagged against.

    Attributes:
        release_id: Stable identifier (e.g. ``"v2.5.0"`` or
            ``"2026-Q2-stability"``).  Primary key in the ``releases`` table.
        name: Human-friendly label (e.g. ``"Q2 Stability Release"``).
        target_date: ISO 8601 date (``"YYYY-MM-DD"``) for the planned ship
            date, or empty string when no date is set.
        status: Lifecycle state — one of :data:`RELEASE_STATUSES`.  Defaults
            to ``"planned"``.
        notes: Free-form notes (themes, scope summary, owners, links).
        created_at: ISO 8601 UTC timestamp set on construction when empty.
    """

    release_id: str
    name: str = ""
    target_date: str = ""
    status: str = "planned"
    notes: str = ""
    created_at: str = field(default="")

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = _utcnow()

    def to_dict(self) -> dict:
        return {
            "release_id": self.release_id,
            "name": self.name,
            "target_date": self.target_date,
            "status": self.status,
            "notes": self.notes,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Release:
        return cls(
            release_id=data["release_id"],
            name=data.get("name", ""),
            target_date=data.get("target_date", ""),
            status=data.get("status", "planned"),
            notes=data.get("notes", ""),
            created_at=data.get("created_at", ""),
        )
