"""CLI command: ``baton webhook`` — manage outbound webhook subscriptions.

Thin wrappers around :class:`~agent_baton.api.webhooks.registry.WebhookRegistry`
that read and write ``webhooks.json`` directly — no API server required.

Subcommands
-----------
add     Register a new webhook subscription.
list    List all registered webhooks.
remove  Remove a webhook by ID.

Usage
-----
    baton webhook add --url https://example.com/hook --events "gate.*,step.completed"
    baton webhook add --url https://example.com/hook --events "*" --secret s3cr3t
    baton webhook list
    baton webhook remove --id <webhook_id>

Topic patterns use glob-style matching (fnmatch):
    ``*``               matches every event
    ``gate.*``          matches gate.required, gate.passed, gate.failed
    ``step.completed``  exact match
    ``plan.*``          matches plan.created, plan.started, etc.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``webhook`` subcommand group."""
    p = subparsers.add_parser(
        "webhook",
        help="Manage outbound webhook subscriptions (no API server required)",
    )
    sub = p.add_subparsers(dest="subcommand")

    # baton webhook add --url URL --events PATTERNS [--secret SECRET]
    p_add = sub.add_parser(
        "add",
        help="Register a new webhook subscription",
    )
    p_add.add_argument(
        "--url",
        required=True,
        metavar="URL",
        help="HTTPS endpoint to deliver events to",
    )
    p_add.add_argument(
        "--events",
        required=True,
        metavar="PATTERNS",
        help=(
            "Comma-separated list of event topic patterns, e.g. "
            "\"gate.*,step.completed\" or \"*\" for all events"
        ),
    )
    p_add.add_argument(
        "--secret",
        default=None,
        metavar="SECRET",
        help=(
            "Optional HMAC-SHA256 signing secret.  "
            "When set, every delivery includes an X-Baton-Signature header."
        ),
    )

    # baton webhook list
    sub.add_parser(
        "list",
        help="List all registered webhook subscriptions",
    )

    # baton webhook remove --id ID
    p_remove = sub.add_parser(
        "remove",
        help="Remove a webhook subscription by ID",
    )
    p_remove.add_argument(
        "--id",
        required=True,
        dest="webhook_id",
        metavar="ID",
        help="Webhook ID to remove (from 'baton webhook list')",
    )

    return p


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    if not hasattr(args, "subcommand") or args.subcommand is None:
        print("usage: baton webhook <subcommand>")
        print("subcommands: add, list, remove")
        sys.exit(1)

    if args.subcommand == "add":
        _add(args)
    elif args.subcommand == "list":
        _list(args)
    elif args.subcommand == "remove":
        _remove(args)
    else:
        print(f"error: unknown webhook subcommand: {args.subcommand}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def _resolve_webhooks_path() -> Path:
    """Locate webhooks.json by searching from cwd up to the project root.

    Walks ancestor directories looking for ``.claude/team-context/``.
    Falls back to ``~/.baton/`` when no project context is found.

    Returns:
        Path to ``webhooks.json`` (may not exist yet — registry creates it).
    """
    cwd = Path.cwd()
    for ancestor in [cwd, *cwd.parents]:
        ctx = ancestor / ".claude" / "team-context"
        if ctx.is_dir():
            return ctx / "webhooks.json"
    # Global fallback
    return Path.home() / ".baton" / "webhooks.json"


def _add(args: argparse.Namespace) -> None:
    """Register a new webhook subscription."""
    from agent_baton.api.webhooks.registry import WebhookRegistry

    # Parse comma-separated event patterns, strip whitespace.
    events = [e.strip() for e in args.events.split(",") if e.strip()]
    if not events:
        print("error: --events must contain at least one pattern", file=sys.stderr)
        sys.exit(1)

    webhooks_path = _resolve_webhooks_path()
    registry = WebhookRegistry(webhooks_path)
    entry = registry.register(url=args.url, events=events, secret=args.secret)

    print(f"Registered webhook: {entry['webhook_id']}")
    print(f"  URL:    {entry['url']}")
    print(f"  Events: {', '.join(entry['events'])}")
    if args.secret:
        print("  Secret: (set)")
    print(f"  File:   {webhooks_path}")


def _list(args: argparse.Namespace) -> None:  # noqa: ARG001
    """List all registered webhook subscriptions."""
    from agent_baton.api.webhooks.registry import WebhookRegistry

    webhooks_path = _resolve_webhooks_path()
    registry = WebhookRegistry(webhooks_path)
    entries = registry.list_all()

    if not entries:
        print("No webhooks registered.")
        print(f"Run: baton webhook add --url URL --events PATTERNS")
        return

    print(f"Webhooks ({len(entries)} registered)  [{webhooks_path}]")
    print()
    for entry in entries:
        enabled = "enabled" if entry.get("enabled", True) else "DISABLED"
        failures = entry.get("consecutive_failures", 0)
        secret_flag = " [signed]" if entry.get("secret") else ""
        events_str = ", ".join(entry.get("events", []))
        print(f"  {entry['webhook_id']}  {enabled}{secret_flag}")
        print(f"    url:     {entry['url']}")
        print(f"    events:  {events_str}")
        print(f"    created: {entry.get('created', '')}")
        if failures:
            print(f"    consecutive_failures: {failures}")
        print()


def _remove(args: argparse.Namespace) -> None:
    """Remove a webhook subscription by ID."""
    from agent_baton.api.webhooks.registry import WebhookRegistry

    webhooks_path = _resolve_webhooks_path()
    registry = WebhookRegistry(webhooks_path)
    deleted = registry.delete(args.webhook_id)

    if deleted:
        print(f"Removed webhook: {args.webhook_id}")
    else:
        print(f"error: webhook '{args.webhook_id}' not found", file=sys.stderr)
        sys.exit(1)
