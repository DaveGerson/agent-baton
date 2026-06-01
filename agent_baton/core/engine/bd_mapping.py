"""Lossless mapping between baton :class:`Bead` objects and ``bd`` issues.

ADR-13b. baton beads carry fields with no native ``bd`` column
(``agent_name``, ``step_id``, ``confidence``, ``scope``, ``source``,
``bead_type`` vocabulary beyond bd's built-ins, soul signatures, executable
metadata, …).  We preserve them losslessly by stashing the full
``bead.to_dict()`` under the ``baton`` key of bd's free-form ``metadata``
object, while *also* populating bd-native fields (title, description, type,
status, labels) so bd's own querying, ``ready`` computation, and the
``.beads/issues.jsonl`` interchange remain meaningful.

On read we prefer the ``baton`` metadata blob for full fidelity and overlay
bd's authoritative ``status``/``closed_at``; when it is absent (e.g. an issue
created directly with ``bd`` outside baton) we fall back to a best-effort map
of bd-native fields so externally-authored issues are still visible as beads.
"""
from __future__ import annotations

from agent_baton.core.engine.bd_client import BD_BUILTIN_TYPES
from agent_baton.models.bead import Bead

# Metadata key under which the full baton bead dict is stored.
_BATON_META_KEY = "baton"

# baton status -> bd status.  bd has no "archived"/"quarantine"; we map them to
# the closest bd state and tag the original via a label so it round-trips.
_STATUS_TO_BD = {
    "open": "open",
    "closed": "closed",
    "archived": "closed",
    "quarantine": "blocked",
}
_BD_TO_STATUS = {
    "open": "open",
    "in_progress": "open",
    "blocked": "open",
    "deferred": "open",
    "pinned": "open",
    "hooked": "open",
    "closed": "closed",
}


def _title_for(bead: Bead) -> str:
    """Derive a concise bd title from the bead content (first line, trimmed)."""
    first = (bead.content or bead.summary or bead.bead_type or "bead").strip().splitlines()
    line = first[0] if first else "bead"
    return line[:120] if len(line) > 120 else line


def _priority_for(bead: Bead) -> int:
    """Map a bead to a bd priority (0=highest…4). Warnings rank higher."""
    if bead.bead_type == "warning":
        return 1
    if bead.bead_type in ("decision", "outcome"):
        return 2
    return 3


def _issue_type_for(bead: Bead) -> str:
    """Use the bead_type as a bd issue_type when bd knows it, else ``task``."""
    return bead.bead_type if bead.bead_type in BD_BUILTIN_TYPES else "task"


def bead_labels(bead: Bead) -> list[str]:
    """Build the queryable bd label set for a bead.

    Combines the bead's own tags with synthetic facets so ``bd list --label``
    can filter by baton concepts (type, scope, source, task).
    """
    labels: list[str] = list(bead.tags or [])
    labels.append(f"bead-type:{bead.bead_type}")
    if bead.scope:
        labels.append(f"scope:{bead.scope}")
    if bead.source:
        labels.append(f"source:{bead.source}")
    if bead.task_id:
        labels.append(f"task:{bead.task_id}")
    if bead.status in ("archived", "quarantine"):
        labels.append(f"baton-status:{bead.status}")
    # De-dup while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for lbl in labels:
        if lbl and lbl not in seen:
            seen.add(lbl)
            out.append(lbl)
    return out


def bead_to_create_kwargs(bead: Bead) -> dict:
    """Return kwargs for :meth:`BdClient.create` representing *bead*."""
    return {
        "title": _title_for(bead),
        "issue_type": _issue_type_for(bead),
        "priority": _priority_for(bead),
        "description": bead.content or "",
        "design": bead.summary or "",
        "labels": bead_labels(bead),
        "metadata": {_BATON_META_KEY: bead.to_dict()},
        "bead_id": bead.bead_id,
    }


def bd_status_for(bead: Bead) -> str:
    """Map a baton bead status to the bd status to set on update/close."""
    return _STATUS_TO_BD.get(bead.status, "open")


def bd_issue_to_bead(issue: dict) -> Bead:
    """Reconstruct a :class:`Bead` from a ``bd`` issue dict.

    Prefers the embedded ``metadata.baton`` blob for full fidelity; overlays
    bd's authoritative ``status``/``closed_at``.  Falls back to a best-effort
    field map for issues authored directly in bd (no baton blob).
    """
    metadata = issue.get("metadata") or {}
    baton_blob = metadata.get(_BATON_META_KEY) if isinstance(metadata, dict) else None

    if isinstance(baton_blob, dict) and baton_blob.get("bead_id"):
        bead = Bead.from_dict(baton_blob)
        # bd is authoritative for lifecycle state.
        bd_status = str(issue.get("status", "") or "")
        if bd_status:
            # Preserve baton's archived/quarantine distinction if recorded.
            baton_status = _baton_status_from_labels(issue)
            bead.status = baton_status or _BD_TO_STATUS.get(bd_status, bead.status)
        if issue.get("closed_at"):
            bead.closed_at = str(issue["closed_at"])
        return bead

    # --- Fallback: externally-authored bd issue, no baton blob. ---
    return Bead(
        bead_id=str(issue.get("id", "")),
        task_id=_label_value(issue, "task:") or "",
        step_id="",
        agent_name=str(issue.get("owner", "") or ""),
        bead_type=_label_value(issue, "bead-type:") or str(issue.get("issue_type", "discovery")),
        content=str(issue.get("description") or issue.get("title") or ""),
        confidence="medium",
        scope=_label_value(issue, "scope:") or "task",
        tags=_plain_labels(issue),
        affected_files=[],
        status=_BD_TO_STATUS.get(str(issue.get("status", "open")), "open"),
        created_at=str(issue.get("created_at", "") or ""),
        closed_at=str(issue.get("closed_at", "") or ""),
        summary=str(issue.get("design", "") or ""),
        source="bd-external",
    )


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------


def _label_value(issue: dict, prefix: str) -> str:
    for lbl in issue.get("labels", []) or []:
        if isinstance(lbl, str) and lbl.startswith(prefix):
            return lbl[len(prefix):]
    return ""


def _baton_status_from_labels(issue: dict) -> str:
    return _label_value(issue, "baton-status:")


def _plain_labels(issue: dict) -> list[str]:
    """Return labels that are not synthetic baton facets."""
    facets = ("bead-type:", "scope:", "source:", "task:", "baton-status:")
    return [
        lbl
        for lbl in issue.get("labels", []) or []
        if isinstance(lbl, str) and not lbl.startswith(facets)
    ]
