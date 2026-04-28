"""Wave 6.1 Part C — Executable Beads: AuditorGate quarantine flow (bd-81b9).

Every new executable bead is quarantined on creation.  An auditor agent
must approve it before it can be run.  Approval is recorded as a bead link
from an auditor-produced bead to the quarantined bead.

Flow:
    1. Caller writes an :class:`~agent_baton.models.bead.ExecutableBead`
       with ``status="quarantine"`` via
       :meth:`AuditorGate.quarantine`.
    2. Caller dispatches the auditor agent with bead context.
    3. Auditor agent writes an approval ``decision`` bead and calls
       :meth:`AuditorGate.approve` with ``auditor_bead_id``.
    4. ``approve`` verifies that the auditor bead is signed by an auditor
       soul (when ``BATON_SOULS_ENABLED=1``) and transitions the executable
       bead to ``status="open"``.
    5. :meth:`AuditorGate.is_approved` returns ``True`` for beads with
       ``status != 'quarantine'``.

Design note from feedback_no_human_compliance_gates.md: compliance reviews
must be automated (auditor agent), not human-gated.  This class never
pauses for human input — it only enforces the presence of a programmatic
approval bead.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.engine.bead_store import BeadStore
    from agent_baton.models.bead import ExecutableBead

_log = logging.getLogger(__name__)

_SOULS_ENABLED_ENV = "BATON_SOULS_ENABLED"
_AUDITOR_ROLES = frozenset({"auditor", "script-auditor"})


def _is_souls_enabled() -> bool:
    return os.environ.get(_SOULS_ENABLED_ENV, "0").strip() not in ("0", "false", "False", "")


class AuditorGate:
    """Quarantine and approval lifecycle for executable beads.

    Args:
        bead_store: The project's :class:`BeadStore` instance.
    """

    def __init__(self, bead_store: "BeadStore") -> None:
        self._store = bead_store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def quarantine(self, bead: "ExecutableBead") -> None:
        """Set *bead* status to ``"quarantine"`` and persist.

        This must be called immediately after an executable bead is first
        constructed and before it is dispatched for auditor review.

        Args:
            bead: The :class:`ExecutableBead` to quarantine.  Its
                ``bead_type`` must be ``"executable"``.

        Raises:
            ValueError: When *bead* is not an executable bead.
        """
        if bead.bead_type != "executable":
            raise ValueError(
                f"AuditorGate.quarantine: expected bead_type='executable', "
                f"got {bead.bead_type!r} for {bead.bead_id}"
            )
        bead.status = "quarantine"
        written = self._store.write(bead)
        if not written:
            _log.warning(
                "AuditorGate.quarantine: BeadStore.write failed for %s",
                bead.bead_id,
            )
        _log.info(
            "exec.bead.quarantine bead_id=%s reason=new-executable-bead",
            bead.bead_id,
        )

    def approve(self, bead_id: str, auditor_bead_id: str) -> None:
        """Transition *bead_id* from quarantine to open.

        Verifies that *auditor_bead_id* exists and (when souls enabled) is
        signed by an auditor-role soul.

        Args:
            bead_id: The ``bead_id`` of the quarantined executable bead.
            auditor_bead_id: The ``bead_id`` of the auditor's approval bead.

        Raises:
            ValueError: When the bead is not found, is not in quarantine, or
                the auditor bead fails soul verification.
        """
        # Resolve quarantined bead.
        bead = self._store.read(bead_id)
        if bead is None:
            raise ValueError(f"AuditorGate.approve: bead not found: {bead_id}")
        if bead.status != "quarantine":
            _log.info(
                "AuditorGate.approve: bead %s is already %s — no-op",
                bead_id, bead.status,
            )
            return

        # Resolve auditor bead.
        auditor_bead = self._store.read(auditor_bead_id)
        if auditor_bead is None:
            raise ValueError(
                f"AuditorGate.approve: auditor bead not found: {auditor_bead_id}"
            )

        # When souls are enabled, verify the auditor bead is signed by an
        # auditor-role soul.
        if _is_souls_enabled():
            self._verify_auditor_soul(auditor_bead)

        # Transition to open.
        bead.status = "open"
        from agent_baton.models.bead import BeadLink
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        bead.links.append(
            BeadLink(
                target_bead_id=auditor_bead_id,
                link_type="validates",
                created_at=now,
            )
        )
        written = self._store.write(bead)
        if not written:
            _log.warning(
                "AuditorGate.approve: BeadStore.write failed for %s",
                bead_id,
            )
        _log.info(
            "AuditorGate.approve: bead %s approved by auditor bead %s",
            bead_id, auditor_bead_id,
        )

    def is_approved(self, bead_id: str) -> bool:
        """Return ``True`` when *bead_id* is not in quarantine.

        A bead is considered approved (ready to run) once its status
        transitions away from ``"quarantine"``.

        Args:
            bead_id: The ``bead_id`` to check.

        Returns:
            ``True`` if the bead exists and has ``status != 'quarantine'``.
            ``False`` if the bead is not found or is quarantined.
        """
        bead = self._store.read(bead_id)
        if bead is None:
            return False
        return bead.status != "quarantine"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _verify_auditor_soul(self, auditor_bead: "ExecutableBead") -> None:
        """Verify that *auditor_bead* is signed by an auditor-role soul.

        Raises ``ValueError`` when the check fails.
        """
        signed_by = getattr(auditor_bead, "signed_by", "")
        if not signed_by:
            raise ValueError(
                f"AuditorGate: auditor bead {auditor_bead.bead_id} is unsigned. "
                "BATON_SOULS_ENABLED requires an auditor soul signature."
            )

        # Determine the role from the soul_id token (format: role_domain_suffix).
        # A soul signed by an auditor role has a soul_id that starts with
        # "auditor_" or the agent_name field on the bead is an auditor variant.
        soul_looks_like_auditor = (
            signed_by.startswith("auditor_")
            or auditor_bead.agent_name in _AUDITOR_ROLES
            or any(r in signed_by for r in ("auditor",))
        )
        if not soul_looks_like_auditor:
            raise ValueError(
                f"AuditorGate: auditor bead {auditor_bead.bead_id} is signed by "
                f"soul {signed_by!r} which does not appear to be an auditor soul. "
                "Only auditor-role souls may approve executable beads."
            )

        _log.debug(
            "AuditorGate: verified auditor soul %s for bead %s",
            signed_by, auditor_bead.bead_id,
        )
