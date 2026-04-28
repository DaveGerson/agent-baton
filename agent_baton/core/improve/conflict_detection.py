"""ConflictDetector -- detect contradictions between improvement recommendations.

L2.4 (bd-362f): velocity-zero detection-only pass over a batch of
:class:`~agent_baton.models.improvement.Recommendation` instances.  Surfaces
clusters whose ``proposed_change`` touches overlapping config keys or pulls
in opposite directions, so the improvement loop does not auto-apply two
contradictory changes in the same cycle.

Three severity tiers
--------------------

* **HIGH -- direct contradiction**: ``r1.proposed_change.from`` equals
  ``r2.proposed_change.to`` *and* ``r1.proposed_change.to`` equals
  ``r2.proposed_change.from``.  i.e. the two recs would undo each other.
* **MEDIUM -- same-key disagreement**: identical ``category`` + ``target``
  but different ``proposed_change.to`` values.
* **LOW -- adjacent disagreement**: same ``category``, different ``target``,
  but the inferred *direction* is opposite (e.g. one downgrades a budget,
  one upgrades a budget).

Detection is a pure function over the rec list -- no IO, no logging side
effects -- so callers can run it before, during, or independently of
persistence.  Use :class:`~agent_baton.core.storage.conflict_store.ConflictStore`
to persist the resulting :class:`Conflict` objects.

Stdlib only.  Type-hinted.  Never raises -- malformed
``proposed_change`` payloads are skipped silently.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable

from agent_baton.models.improvement import Recommendation


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

# Severity ordering: when the same pair triggers multiple rules we keep the
# most-severe verdict only.
_SEVERITY_RANK = {SEVERITY_LOW: 0, SEVERITY_MEDIUM: 1, SEVERITY_HIGH: 2}

# Default temporal window for considering two recs as related.
DEFAULT_WINDOW_DAYS = 7

# Heuristic verb sets that imply a direction change for "adjacent" detection.
_UP_VERBS = frozenset({"upgrade", "raise", "increase", "boost", "expand", "extend"})
_DOWN_VERBS = frozenset({"downgrade", "lower", "decrease", "reduce", "shrink", "tighten"})


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Conflict:
    """A single detected conflict between two or more recommendations.

    Attributes:
        conflict_id: Unique identifier (``cf-<hex>`` by default).
        rec_ids: The recommendation ids involved in the conflict.
        reason: Human-readable explanation of *why* these recs conflict.
        severity: One of ``high``, ``medium``, ``low``.
        detected_at: ISO 8601 timestamp of detection.
        acknowledged_at: ISO 8601 timestamp set by
            ``baton improve conflicts acknowledge``; empty until reviewed.
            Acknowledgement is informational only -- it does NOT resolve
            the underlying recommendation conflict.
    """

    conflict_id: str = ""
    rec_ids: list[str] = field(default_factory=list)
    reason: str = ""
    severity: str = SEVERITY_LOW
    detected_at: str = field(default_factory=_utcnow_iso)
    acknowledged_at: str = ""

    def __post_init__(self) -> None:
        if not self.conflict_id:
            self.conflict_id = f"cf-{uuid.uuid4().hex[:12]}"

    def to_dict(self) -> dict:
        return {
            "conflict_id": self.conflict_id,
            "rec_ids": list(self.rec_ids),
            "reason": self.reason,
            "severity": self.severity,
            "detected_at": self.detected_at,
            "acknowledged_at": self.acknowledged_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Conflict:
        return cls(
            conflict_id=str(data.get("conflict_id", "")),
            rec_ids=list(data.get("rec_ids", [])),
            reason=str(data.get("reason", "")),
            severity=str(data.get("severity", SEVERITY_LOW)),
            detected_at=str(data.get("detected_at", "")),
            acknowledged_at=str(data.get("acknowledged_at", "")),
        )


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class ConflictDetector:
    """Pure function detector over a list of :class:`Recommendation`.

    The detector inspects every unordered pair ``(r1, r2)`` and emits a
    :class:`Conflict` when any of the three rules fires.  It never raises --
    invalid timestamps or malformed ``proposed_change`` payloads cause that
    specific pair to be skipped, never the whole batch.

    Args:
        window_days: Maximum gap (in days) between two recs for them to be
            considered related.  Defaults to :data:`DEFAULT_WINDOW_DAYS`.
    """

    def __init__(self, window_days: int = DEFAULT_WINDOW_DAYS) -> None:
        self._window = timedelta(days=int(window_days))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(self, recs: Iterable[Recommendation]) -> list[Conflict]:
        """Return all conflicts found amongst *recs*.

        Pairs that fire multiple rules are emitted once with the highest
        severity.  The returned list is sorted by ``(severity desc, rec_ids)``
        for deterministic CLI output.
        """
        rec_list = list(recs)
        conflicts: dict[tuple[str, str], Conflict] = {}

        for i in range(len(rec_list)):
            r1 = rec_list[i]
            for j in range(i + 1, len(rec_list)):
                r2 = rec_list[j]
                verdict = self._classify_pair(r1, r2)
                if verdict is None:
                    continue
                severity, reason = verdict
                key = tuple(sorted([r1.rec_id, r2.rec_id]))
                existing = conflicts.get(key)
                if existing is None or _SEVERITY_RANK[severity] > _SEVERITY_RANK[
                    existing.severity
                ]:
                    conflicts[key] = Conflict(
                        rec_ids=list(key),
                        reason=reason,
                        severity=severity,
                    )

        result = list(conflicts.values())
        result.sort(
            key=lambda c: (-_SEVERITY_RANK.get(c.severity, 0), c.rec_ids),
        )
        return result

    # ------------------------------------------------------------------
    # Filter helper for the auto-apply path
    # ------------------------------------------------------------------

    @staticmethod
    def filter_out_conflicting(
        applicable: Iterable[Recommendation],
        conflicts: Iterable[Conflict],
    ) -> list[Recommendation]:
        """Return the subset of *applicable* not present in any conflict.

        Used by the improvement loop to suppress auto-apply for recs that
        the detector flagged as in conflict with a peer.  Velocity-zero:
        the suppressed recs are still recorded; they just do not auto-apply.
        """
        flagged: set[str] = set()
        for c in conflicts:
            for rid in c.rec_ids:
                flagged.add(rid)
        return [r for r in applicable if r.rec_id not in flagged]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _classify_pair(
        self,
        r1: Recommendation,
        r2: Recommendation,
    ) -> tuple[str, str] | None:
        """Classify a single pair, returning ``(severity, reason)`` or None.

        Order of checks matters only insofar as ``detect()`` keeps the
        highest-severity verdict per pair -- but each rule fires
        independently here.
        """
        if not self._within_window(r1, r2):
            return None

        # Direct contradiction (HIGH) -- requires same target *and* both
        # proposed_change.from / .to fields and they swap.
        if r1.target and r1.target == r2.target:
            verdict = self._direct_contradiction(r1, r2)
            if verdict is not None:
                return verdict

            # Same-key disagreement (MEDIUM) -- same category + target,
            # different "to" values.
            if r1.category and r1.category == r2.category:
                verdict = self._same_key_disagreement(r1, r2)
                if verdict is not None:
                    return verdict

        # Adjacent disagreement (LOW) -- same category, different targets,
        # opposite direction inferred from the action verb.
        if (
            r1.category
            and r1.category == r2.category
            and r1.target != r2.target
        ):
            verdict = self._adjacent_disagreement(r1, r2)
            if verdict is not None:
                return verdict

        return None

    def _within_window(self, r1: Recommendation, r2: Recommendation) -> bool:
        t1 = _parse_iso(r1.created_at)
        t2 = _parse_iso(r2.created_at)
        if t1 is None or t2 is None:
            # If either timestamp is missing/garbage, we still consider them
            # related so the operator sees the conflict rather than silently
            # dropping it.  This is safer at velocity zero.
            return True
        return abs(t1 - t2) <= self._window

    def _direct_contradiction(
        self,
        r1: Recommendation,
        r2: Recommendation,
    ) -> tuple[str, str] | None:
        f1, t1 = _from_to(r1.proposed_change)
        f2, t2 = _from_to(r2.proposed_change)
        if f1 is None or t1 is None or f2 is None or t2 is None:
            return None
        if f1 == t2 and t1 == f2 and f1 != t1:
            reason = (
                f"direct contradiction on '{r1.target}': "
                f"{r1.rec_id} {f1!r} -> {t1!r} reverses "
                f"{r2.rec_id} {f2!r} -> {t2!r}"
            )
            return SEVERITY_HIGH, reason
        return None

    def _same_key_disagreement(
        self,
        r1: Recommendation,
        r2: Recommendation,
    ) -> tuple[str, str] | None:
        _, t1 = _from_to(r1.proposed_change)
        _, t2 = _from_to(r2.proposed_change)
        if t1 is None or t2 is None:
            return None
        if t1 == t2:
            return None
        reason = (
            f"same-key disagreement on '{r1.category}/{r1.target}': "
            f"{r1.rec_id} -> {t1!r} vs {r2.rec_id} -> {t2!r}"
        )
        return SEVERITY_MEDIUM, reason

    def _adjacent_disagreement(
        self,
        r1: Recommendation,
        r2: Recommendation,
    ) -> tuple[str, str] | None:
        d1 = _direction(r1)
        d2 = _direction(r2)
        if d1 is None or d2 is None:
            return None
        if (d1 == "up" and d2 == "down") or (d1 == "down" and d2 == "up"):
            reason = (
                f"adjacent disagreement in category '{r1.category}': "
                f"{r1.rec_id} ({r1.action!r} on {r1.target}) vs "
                f"{r2.rec_id} ({r2.action!r} on {r2.target})"
            )
            return SEVERITY_LOW, reason
        return None


# ---------------------------------------------------------------------------
# Free helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> datetime | None:
    """Best-effort ISO-8601 parser.  Accepts trailing ``Z`` and offsets."""
    if not value:
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _from_to(change: dict) -> tuple[object | None, object | None]:
    """Return ``(from, to)`` from a proposed_change dict, or ``(None, None)``.

    Tolerates either the literal keys ``from``/``to`` or the common
    ``before``/``after`` aliases used by older recommenders.
    """
    if not isinstance(change, dict):
        return None, None
    if "from" in change or "to" in change:
        return change.get("from"), change.get("to")
    if "before" in change or "after" in change:
        return change.get("before"), change.get("after")
    return None, None


def _direction(rec: Recommendation) -> str | None:
    """Infer ``"up"`` / ``"down"`` from the recommendation action verb."""
    action = (rec.action or "").lower()
    for verb in _UP_VERBS:
        if verb in action:
            return "up"
    for verb in _DOWN_VERBS:
        if verb in action:
            return "down"
    return None
