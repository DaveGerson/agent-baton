"""``bd``-backed bead store (ADR-13b — full replacement of the SQLite store).

:class:`BdBeadStore` implements the same public surface as the legacy
:class:`~agent_baton.core.engine.bead_store.BeadStore` (``write`` / ``read`` /
``query`` / ``ready`` / ``close`` / ``annotate`` / ``link``) but persists to
the real ``bd`` tool via :class:`~agent_baton.core.engine.bd_client.BdClient`.

This is the seam that lets the rest of the engine "run off beads" with minimal
call-site churn: anything holding a bead store keeps calling the same methods.

Phase status
------------
The seven core CRUD/relationship methods are implemented against ``bd``.  The
analytics helpers that the legacy store grew (``decay``, ``update_quality_score``,
``increment_retrieval_count``, conflict resolution) are intentionally light in
this phase — quality/retrieval counters live in the baton metadata blob and are
updated in place; bead decay maps onto ``bd``'s own compaction and is a no-op
here.  See ADR-13b for the staged migration of the remaining consumers.
"""
from __future__ import annotations

import logging

from agent_baton.core.engine.bd_client import BdClient, BdError
from agent_baton.core.engine.bd_mapping import (
    bd_issue_to_bead,
    bd_status_for,
    bead_labels,
    bead_to_create_kwargs,
)
from agent_baton.models.bead import Bead

_log = logging.getLogger(__name__)

# baton link_type -> bd dependency type.  bd's core dependency relation is
# "blocks"; the richer baton vocabulary is preserved in the bead's metadata
# blob, with a best-effort bd dependency created for graph-meaningful kinds.
_LINK_TO_BD_DEP = {
    "blocks": "blocks",
    "blocked_by": "blocks",
    "discovered_from": "discovered-from",
    "relates_to": "related",
    "validates": "related",
    "contradicts": "related",
    "extends": "related",
}


class BdBeadStore:
    """A bead store backed by the external ``bd`` CLI.

    Args:
        client: A configured :class:`BdClient`.  The store calls ``init()``
            lazily on first write so a fresh project bootstraps automatically.
    """

    def __init__(self, client: BdClient) -> None:
        self._bd = client
        self._initialised = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_init(self) -> None:
        if not self._initialised:
            self._bd.init()
            self._initialised = True

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def write(self, bead: Bead) -> str:
        """Create or update *bead* in bd; returns the bead_id ("" on failure)."""
        try:
            self._ensure_init()
            existing = self._bd.show(bead.bead_id) if bead.bead_id else None
            if existing is None:
                kwargs = bead_to_create_kwargs(bead)
                created = self._bd.create(**kwargs)
                bead_id = str(created.get("id") or bead.bead_id)
                # Set non-open lifecycle state if the bead arrived closed/etc.
                if bead.status != "open":
                    self._bd.update(bead_id, status=bd_status_for(bead))
            else:
                bead_id = bead.bead_id
                self._bd.update(
                    bead_id,
                    status=bd_status_for(bead),
                    set_labels=bead_labels(bead),
                    metadata={"baton": bead.to_dict()},
                )
            # Best-effort dependency edges.
            for link in bead.links or []:
                dep_type = _LINK_TO_BD_DEP.get(link.link_type, "related")
                try:
                    self._bd.dep_add(bead_id, link.target_bead_id, dep_type)
                except BdError as exc:
                    _log.debug("BdBeadStore.link skipped (%s): %s", link.link_type, exc)
            return bead_id
        except BdError as exc:
            _log.warning("BdBeadStore.write failed for %s: %s", bead.bead_id, exc)
            return ""

    def close(self, bead_id: str, summary: str) -> None:
        """Close a bead and record *summary* as a note."""
        try:
            self._ensure_init()
            if summary:
                self._bd.note(bead_id, summary)
            self._bd.close(bead_id, reason=summary or "")
        except BdError as exc:
            _log.warning("BdBeadStore.close failed for %s: %s", bead_id, exc)

    def annotate(self, bead_id: str, note: str, agent_name: str | None = None) -> None:
        """Append *note* (optionally attributed) to a bead."""
        try:
            self._ensure_init()
            text = f"[{agent_name}] {note}" if agent_name else note
            self._bd.note(bead_id, text)
        except BdError as exc:
            _log.warning("BdBeadStore.annotate failed for %s: %s", bead_id, exc)

    def link(self, source_id: str, target_id: str, link_type: str) -> None:
        """Create a typed link (mapped to a bd dependency)."""
        try:
            self._ensure_init()
            self._bd.dep_add(source_id, target_id, _LINK_TO_BD_DEP.get(link_type, "related"))
        except BdError as exc:
            _log.warning("BdBeadStore.link failed %s->%s: %s", source_id, target_id, exc)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def read(self, bead_id: str) -> Bead | None:
        """Return a bead by id, or ``None``."""
        try:
            issue = self._bd.show(bead_id)
        except BdError as exc:
            _log.warning("BdBeadStore.read failed for %s: %s", bead_id, exc)
            return None
        return bd_issue_to_bead(issue) if issue else None

    def query(
        self,
        *,
        task_id: str | None = None,
        agent_name: str | None = None,
        bead_type: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
    ) -> list[Bead]:
        """Filtered search (AND semantics), newest first.

        Pushes the cheap facet filters down to ``bd`` via labels, then applies
        the remaining predicates in Python against the reconstructed beads so
        baton-specific fields (agent_name, confidence, …) filter correctly.
        """
        label_filters: list[str] = []
        if task_id is not None:
            label_filters.append(f"task:{task_id}")
        if bead_type is not None:
            label_filters.append(f"bead-type:{bead_type}")
        # Push the common ``open`` filter down to bd to avoid fetching every
        # issue on hot paths (synthesize/export). archived/quarantine have no
        # native bd status, so those stay Python-side below.
        bd_status = "open" if status == "open" else ""
        try:
            issues = self._bd.list(status=bd_status, labels=label_filters or None)
        except BdError as exc:
            _log.warning("BdBeadStore.query failed: %s", exc)
            return []

        beads = [bd_issue_to_bead(i) for i in issues]

        def _keep(b: Bead) -> bool:
            if task_id is not None and b.task_id != task_id:
                return False
            if agent_name is not None and b.agent_name != agent_name:
                return False
            if bead_type is not None and b.bead_type != bead_type:
                return False
            if status is not None and b.status != status:
                return False
            if tags and not set(tags).issubset(set(b.tags or [])):
                return False
            return True

        beads = [b for b in beads if _keep(b)]
        beads.sort(key=lambda b: b.created_at, reverse=True)
        return beads[:limit]

    def update_quality_score(self, bead_id: str, delta: float) -> None:
        """Adjust a bead's quality score (BEAD_FEEDBACK), persisted in metadata.

        Read-modify-write the baton metadata blob so feedback survives on the
        bd backend rather than silently no-opping.  Parity with
        ``BeadStore.update_quality_score``.
        """
        try:
            bead = self.read(bead_id)
            if bead is None:
                return
            bead.quality_score = float(bead.quality_score) + float(delta)
            self._bd.update(bead_id, metadata={"baton": bead.to_dict()})
        except BdError as exc:
            _log.warning("BdBeadStore.update_quality_score failed for %s: %s", bead_id, exc)

    def increment_retrieval_count(self, bead_id: str) -> None:
        """Increment a bead's retrieval counter, persisted in metadata.

        Parity with ``BeadStore.increment_retrieval_count``.
        """
        try:
            bead = self.read(bead_id)
            if bead is None:
                return
            bead.retrieval_count = int(bead.retrieval_count) + 1
            self._bd.update(bead_id, metadata={"baton": bead.to_dict()})
        except BdError as exc:
            _log.warning("BdBeadStore.increment_retrieval_count failed for %s: %s", bead_id, exc)

    def has_unresolved_conflicts(self, task_id: str) -> bool:
        """True if any open bead in *task_id* is tagged ``conflict:unresolved``.

        Parity with ``BeadStore.has_unresolved_conflicts`` (called on the
        executor's main flow), implemented via the store query surface.
        """
        return bool(
            self.query(task_id=task_id, status="open", tags=["conflict:unresolved"])
        )

    def resolve_conflict(self, bead_id: str) -> None:
        """Drop the ``conflict:unresolved`` tag from *bead_id* (re-writes labels)."""
        try:
            bead = self.read(bead_id)
            if bead is None:
                return
            if "conflict:unresolved" in (bead.tags or []):
                bead.tags = [t for t in bead.tags if t != "conflict:unresolved"]
                self.write(bead)
        except BdError as exc:
            _log.warning("BdBeadStore.resolve_conflict failed for %s: %s", bead_id, exc)

    def decay(self, max_age_days: int, task_id: str | None = None) -> int:
        """No-op under the bd backend (bd owns compaction/TTL).

        baton's SQLite-era decay archived old closed beads; on bd, lifecycle
        compaction is the tool's responsibility (``bd``'s ephemeral/TTL +
        Dolt GC).  Returns 0 explicitly so callers don't see an
        ``AttributeError`` and the no-op is intentional rather than silent.
        """
        _log.debug("BdBeadStore.decay is a no-op (bd owns compaction); returning 0")
        return 0

    def find_recent_approvals(self, tag: str, max_age_minutes: int = 5) -> list[Bead]:
        """Return open beads carrying *tag* created within *max_age_minutes*.

        Parity with ``BeadStore.find_recent_approvals`` (swarm sign-off gate).
        """
        from datetime import datetime, timedelta, timezone

        beads = self.query(status="open", tags=[tag], limit=200)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)
        out: list[Bead] = []
        for b in beads:
            try:
                created = datetime.fromisoformat((b.created_at or "").replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if created >= cutoff:
                out.append(b)
        out.sort(key=lambda b: b.created_at, reverse=True)
        return out

    def ready(self, task_id: str) -> list[Bead]:
        """Return ready (unblocked, open) beads, scoped to *task_id*."""
        try:
            issues = self._bd.ready()
        except BdError as exc:
            _log.warning("BdBeadStore.ready failed: %s", exc)
            return []
        beads = [bd_issue_to_bead(i) for i in issues]
        if task_id:
            beads = [b for b in beads if b.task_id == task_id or not b.task_id]
        return beads
