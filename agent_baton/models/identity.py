"""User identity dataclass and human-role taxonomy (H3.1 / bd-0dea).

This module owns the in-memory representation of a PMO user, including
the engineering-seniority taxonomy introduced in H3.1.

Two distinct role concepts coexist on the ``users`` table and on the
:class:`UserIdentity` dataclass:

* ``role``        -- the *workflow* role used by the approval pipeline
  (creator, reviewer, approver, admin).  Pre-existing.

* ``human_role``  -- the *engineering-seniority* role used by future
  PMO views (H3.2) and the deferred Separation-of-Duties policy
  (G1.4).  Velocity-zero: no code path enforces gating based on this
  value yet.  An unassigned record loads as :attr:`HumanRole.UNASSIGNED`.

Backwards compatibility
-----------------------
Existing user rows that pre-date schema v16 do not have a
``human_role`` column value.  The migration adds the column with
``DEFAULT ''`` so those rows load as ``UNASSIGNED`` automatically.
``from_dict`` likewise tolerates dicts where the ``human_role`` key is
absent or set to ``None`` / ``""``.

Stable string values
--------------------
The string values of :class:`HumanRole` are part of the public surface:
they are persisted to SQLite and consumed by future SoD policy code.
Renaming a value is a breaking change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _utcnow_iso() -> str:
    """Return the current UTC time formatted as an ISO-8601 ``Z`` stamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


class HumanRole(str, Enum):
    """Engineering-seniority taxonomy for PMO users.

    Values are deliberately stable, lower-snake_case strings so they can
    round-trip through SQLite, JSON, env vars, and CLI args without any
    encoding gymnastics.

    The empty-string value (:attr:`UNASSIGNED`) is the canonical
    "no role assigned" sentinel.  It is the default for fresh records
    and for records that pre-date schema v16.
    """

    JUNIOR = "junior"
    SENIOR = "senior"
    TECH_LEAD = "tech_lead"
    ARCHITECT = "architect"
    ENGINEERING_MANAGER = "engineering_manager"
    QA = "qa"
    UNASSIGNED = ""

    @classmethod
    def parse(cls, value: str | None) -> HumanRole:
        """Parse a string value to a :class:`HumanRole`.

        Accepts the canonical lowercase value (``"tech_lead"``), as well
        as ``None``, the empty string, and human-friendly variants such
        as ``"Tech Lead"`` or ``"TECH-LEAD"``.  Raises :class:`ValueError`
        with a helpful message listing valid values otherwise.

        Args:
            value: A string (any case, hyphens or spaces tolerated) or
                ``None``.  ``None``, ``""``, and ``"unassigned"`` all
                map to :attr:`UNASSIGNED`.

        Returns:
            The matching :class:`HumanRole` member.

        Raises:
            ValueError: When *value* does not correspond to any known
                role.  The message lists all valid options.
        """
        if value is None:
            return cls.UNASSIGNED
        normalised = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalised in ("", "unassigned"):
            return cls.UNASSIGNED
        for member in cls:
            if member.value == normalised:
                return member
        valid = ", ".join(
            m.value if m is not cls.UNASSIGNED else "unassigned" for m in cls
        )
        raise ValueError(
            f"Unknown human role: {value!r}. Valid options are: {valid}."
        )


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class UserIdentity:
    """Canonical in-memory representation of a row in the ``users`` table.

    Attributes:
        user_id: Primary key.  Matches the value resolved by
            :class:`agent_baton.api.middleware.user_identity.UserIdentityMiddleware`
            (typically an email, bearer token, or ``"local-user"``).
        display_name: Human-readable name used by PMO surfaces.
        email: Optional contact email.
        role: PMO workflow role (creator, reviewer, approver, admin).
            Free-form string for backwards compatibility with
            pre-H3.1 callers; not validated by this dataclass.
        human_role: Engineering-seniority role (H3.1 / bd-0dea).
            Defaults to :attr:`HumanRole.UNASSIGNED`.
        created_at: ISO-8601 UTC timestamp.
    """

    user_id: str
    display_name: str = ""
    email: str = ""
    role: str = "creator"
    human_role: HumanRole = HumanRole.UNASSIGNED
    created_at: str = field(default_factory=_utcnow_iso)

    # ------------------------------------------------------------------
    # Round-trip
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe ``dict``.

        ``human_role`` is emitted as its string value (not the enum
        object) so the result is directly JSON-encodable.
        """
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "email": self.email,
            "role": self.role,
            "human_role": self.human_role.value,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserIdentity:
        """Construct from a ``dict`` produced by :meth:`to_dict` or by
        a SQLite ``Row.keys()`` mapping.

        Tolerates missing ``human_role`` keys (treated as
        :attr:`HumanRole.UNASSIGNED`) so that older serialised payloads
        and pre-v16 database rows load cleanly.
        """
        return cls(
            user_id=data["user_id"],
            display_name=data.get("display_name", "") or "",
            email=data.get("email", "") or "",
            role=data.get("role", "creator") or "creator",
            human_role=HumanRole.parse(data.get("human_role")),
            created_at=data.get("created_at") or _utcnow_iso(),
        )
