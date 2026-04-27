"""``baton knowledge`` -- inspect freshness, deprecate, retire knowledge.

Subcommands:
    stale        List active items that look stale (informational).
    deprecate    Flag an item as deprecated; schedules retirement after grace.
    retire       Manually retire an item immediately (skips grace window).
    sweep        Run auto_retire_expired() and report which ids were retired.
    usage        Show usage_count and last_used_at for a single item.

The ``sweep`` command is safe to schedule (e.g. ``baton knowledge sweep``
in a daily cron) -- it only retires items the operator has already
deprecated whose grace period has elapsed.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_baton.cli.commands.knowledge import (
    dispatch,
    get_or_create_parser,
    register_handler,
)
from agent_baton.core.knowledge.lifecycle import (
    DEFAULT_GRACE_DAYS,
    DEFAULT_MAX_USAGE,
    DEFAULT_STALE_DAYS,
    KnowledgeLifecycle,
)


def _resolve_db_path() -> Path:
    """Return the project's baton.db path (standard location)."""
    return Path(".claude/team-context/baton.db").resolve()


def _make_lifecycle() -> KnowledgeLifecycle:
    return KnowledgeLifecycle(_resolve_db_path())


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p, sub = get_or_create_parser(subparsers)

    # stale
    sp = sub.add_parser(
        "stale",
        help="List active knowledge items that look stale",
    )
    sp.add_argument(
        "--days", type=int, default=DEFAULT_STALE_DAYS,
        help=f"Days since last use threshold (default: {DEFAULT_STALE_DAYS})",
    )
    sp.add_argument(
        "--max-usage", type=int, default=DEFAULT_MAX_USAGE,
        dest="max_usage",
        help=f"Items with usage_count below this are eligible "
             f"(default: {DEFAULT_MAX_USAGE})",
    )

    # deprecate
    dp = sub.add_parser(
        "deprecate",
        help="Flag a knowledge item as deprecated",
    )
    dp.add_argument("knowledge_id", help='"<pack_name>/<doc_name>"')
    dp.add_argument(
        "--grace", type=int, default=DEFAULT_GRACE_DAYS,
        help=f"Grace period in days before auto-retirement "
             f"(default: {DEFAULT_GRACE_DAYS})",
    )
    dp.add_argument(
        "--reason", type=str, default=None,
        help="Optional human-readable reason recorded with the deprecation",
    )

    # retire
    rp = sub.add_parser(
        "retire",
        help="Retire a knowledge item immediately (skips grace window)",
    )
    rp.add_argument("knowledge_id", help='"<pack_name>/<doc_name>"')

    # sweep
    sub.add_parser(
        "sweep",
        help="Auto-retire deprecated items whose grace period has elapsed",
    )

    # usage
    up = sub.add_parser(
        "usage",
        help="Show usage_count and last_used_at for a single item",
    )
    up.add_argument("knowledge_id", help='"<pack_name>/<doc_name>"')

    register_handler("stale", _handle_stale)
    register_handler("deprecate", _handle_deprecate)
    register_handler("retire", _handle_retire)
    register_handler("sweep", _handle_sweep)
    register_handler("usage", _handle_usage)

    return p


def handler(args: argparse.Namespace) -> None:
    """Module-level handler delegated to the shared knowledge dispatcher."""
    dispatch(args)


# ---------------------------------------------------------------------------
# Per-subcommand handlers
# ---------------------------------------------------------------------------


def _handle_stale(args: argparse.Namespace) -> None:
    lc = _make_lifecycle()
    ids = lc.find_stale(stale_days=args.days, max_usage=args.max_usage)
    if not ids:
        print(
            f"No stale items "
            f"(threshold: {args.days}d / <{args.max_usage} uses)."
        )
        return
    print(
        f"Stale knowledge items "
        f"({len(ids)}; threshold: {args.days}d / <{args.max_usage} uses):"
    )
    for kid in ids:
        info = lc.compute_staleness(kid)
        days = info["days_since_use"]
        days_str = "never" if days < 0 else f"{days}d"
        print(
            f"  {kid:<60} usage={info['usage_count']:>4}  "
            f"last_used={days_str}"
        )


def _handle_deprecate(args: argparse.Namespace) -> None:
    lc = _make_lifecycle()
    lc.mark_deprecated(
        args.knowledge_id, grace_days=args.grace, reason=args.reason,
    )
    print(
        f"Deprecated {args.knowledge_id}; "
        f"will auto-retire after {args.grace} day(s)."
    )


def _handle_retire(args: argparse.Namespace) -> None:
    lc = _make_lifecycle()
    lc.retire(args.knowledge_id)
    print(f"Retired {args.knowledge_id}.")


def _handle_sweep(args: argparse.Namespace) -> None:
    lc = _make_lifecycle()
    retired = lc.auto_retire_expired()
    if not retired:
        print("Sweep complete: no items past grace period.")
        return
    print(f"Sweep complete: retired {len(retired)} item(s):")
    for kid in retired:
        print(f"  - {kid}")


def _handle_usage(args: argparse.Namespace) -> None:
    lc = _make_lifecycle()
    info = lc.compute_staleness(args.knowledge_id)
    if not info["lifecycle_state"]:
        print(f"{args.knowledge_id}: no usage recorded yet.")
        return
    days = info["days_since_use"]
    days_str = "never" if days < 0 else f"{days}d ago"
    print(f"{args.knowledge_id}")
    print(f"  state:        {info['lifecycle_state']}")
    print(f"  usage_count:  {info['usage_count']}")
    print(f"  last_used_at: {info['last_used_at'] or '(never)'} ({days_str})")
    print(f"  is_stale:     {info['is_stale']}")
