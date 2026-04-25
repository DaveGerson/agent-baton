"""``baton improve conflicts`` -- list / show / acknowledge recommendation conflicts.

L2.4 (bd-362f): velocity-zero CLI surface for the conflict-detection
pipeline.  Reads from the ``improvement_conflicts`` table populated by
:class:`agent_baton.core.improve.conflict_detection.ConflictDetector` and
persisted via :class:`agent_baton.core.storage.conflict_store.ConflictStore`.

Subcommands:

* ``list [--status all|active|resolved]`` -- pending conflicts (default
  ``active``).
* ``show <conflict_id>`` -- full details of a single cluster, including the
  recommendation payloads when they are still on disk.
* ``acknowledge <conflict_id>`` -- flag as reviewed.  Does NOT auto-resolve
  the underlying recommendations.

Delegates to:
    agent_baton.core.storage.conflict_store.ConflictStore
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

_BATON_DB = "baton.db"


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "improve-conflicts",
        help="List / show / acknowledge improvement-recommendation conflicts (L2.4)",
    )
    sub = p.add_subparsers(dest="conflicts_action", required=False)

    p_list = sub.add_parser("list", help="List conflicts")
    p_list.add_argument(
        "--status",
        choices=("all", "active", "resolved"),
        default="active",
        help="Filter by status (default: active)",
    )
    p_list.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max rows to return (default: 100)",
    )

    p_show = sub.add_parser("show", help="Show full details of a single conflict")
    p_show.add_argument("conflict_id", help="Conflict identifier (cf-...)")

    p_ack = sub.add_parser(
        "acknowledge",
        help="Mark a conflict as reviewed (does NOT auto-resolve)",
    )
    p_ack.add_argument("conflict_id", help="Conflict identifier (cf-...)")

    return p


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    action = getattr(args, "conflicts_action", None) or "list"
    db_path = _resolve_db_path()
    store = _make_store(db_path)
    if store is None:
        print("Conflict store unavailable (baton.db not initialised).")
        return

    if action == "list":
        _cmd_list(store, status=args.status, limit=int(args.limit))
        return
    if action == "show":
        _cmd_show(store, args.conflict_id)
        return
    if action == "acknowledge":
        _cmd_acknowledge(store, args.conflict_id)
        return

    print(f"Unknown action: {action}")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _cmd_list(store, *, status: str, limit: int) -> None:  # type: ignore[no-untyped-def]
    rows = store.list(status=status, limit=limit)
    if not rows:
        print(f"No conflicts found (status={status}).")
        return
    print(f"Improvement Conflicts ({len(rows)}, status={status}):")
    print()
    print(f"  {'ID':<18} {'SEV':<6} {'ACK':<3} {'DETECTED':<22} REC_IDS")
    print("  " + "-" * 78)
    for c in rows:
        ack = "yes" if c.acknowledged_at else "no"
        rec_blob = ", ".join(c.rec_ids)
        print(
            f"  {c.conflict_id:<18} "
            f"{c.severity.upper():<6} "
            f"{ack:<3} "
            f"{c.detected_at:<22} "
            f"{rec_blob}"
        )


def _cmd_show(store, conflict_id: str) -> None:  # type: ignore[no-untyped-def]
    c = store.get(conflict_id)
    if c is None:
        print(f"Conflict not found: {conflict_id}")
        return
    print(f"Conflict: {c.conflict_id}")
    print(f"  Severity:     {c.severity.upper()}")
    print(f"  Detected at:  {c.detected_at}")
    print(
        f"  Acknowledged: {c.acknowledged_at or '(not yet reviewed)'}"
    )
    print(f"  Reason:       {c.reason}")
    print(f"  Rec ids:      {', '.join(c.rec_ids) or '(none)'}")

    # Best-effort recommendation payload lookup.  Skipped silently when the
    # JSONL log is absent so the command stays useful in minimal installs.
    payloads = _load_recommendations(c.rec_ids)
    if payloads:
        print()
        print("Recommendation payloads:")
        for rec in payloads:
            print(f"  - {rec.rec_id}")
            print(f"      category:   {rec.category}")
            print(f"      target:     {rec.target}")
            print(f"      action:     {rec.action}")
            print(f"      confidence: {rec.confidence:.3f}")
            print(f"      change:     {rec.proposed_change}")


def _cmd_acknowledge(store, conflict_id: str) -> None:  # type: ignore[no-untyped-def]
    ok = store.acknowledge(conflict_id)
    if ok:
        print(f"Acknowledged conflict {conflict_id}.")
        print("  (Underlying recommendations are unchanged.)")
    else:
        print(f"No conflict found with id {conflict_id}.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_db_path() -> Path:
    """Resolve the project-local ``baton.db`` path.

    Honours ``BATON_DB_PATH``, then walks ``cwd`` and ``cwd/.claude/...``
    in the same way as ``baton execute handoff``.
    """
    override = os.environ.get("BATON_DB_PATH")
    if override:
        return Path(override).resolve()
    cwd = Path.cwd().resolve()
    candidates = [
        cwd / _BATON_DB,
        cwd / ".claude" / _BATON_DB,
        cwd / ".claude" / "team-context" / ".." / _BATON_DB,
    ]
    for cand in candidates:
        if cand.exists():
            return cand.resolve()
    # Default to the canonical project-root location even if it does not yet
    # exist; the storage layer will create it on first write.
    return (cwd / _BATON_DB).resolve()


def _make_store(db_path: Path):  # type: ignore[no-untyped-def]
    try:
        from agent_baton.core.storage.conflict_store import ConflictStore

        return ConflictStore(db_path)
    except Exception:  # noqa: BLE001 - defensive
        return None


def _load_recommendations(rec_ids: list[str]):  # type: ignore[no-untyped-def]
    """Best-effort fetch of recommendation payloads from the JSONL log."""
    if not rec_ids:
        return []
    try:
        from agent_baton.core.improve.proposals import ProposalManager

        wanted = set(rec_ids)
        return [r for r in ProposalManager().load_all() if r.rec_id in wanted]
    except Exception:  # noqa: BLE001 - defensive
        return []
