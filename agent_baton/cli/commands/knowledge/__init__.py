"""CLI command group: knowledge.

Multiple modules under this package register subcommands on the same
``baton knowledge`` parent parser.  Cooperation rules:

* Use :func:`get_or_create_parser` instead of ``subparsers.add_parser``
  so all modules share one parent and one ``knowledge_cmd`` sub-action.
* Each subcommand registers its handler with :func:`register_handler`.
  The shared :func:`dispatch` function (used by the auto-discovered
  ``register``/``handler`` pair below) routes ``args.knowledge_cmd`` to
  the right handler.

Without this convention, repeated ``add_parser("knowledge")`` calls
silently clobber each other and only one module's subcommands survive.
"""
from __future__ import annotations

import argparse
from typing import Callable

# Map of "knowledge_cmd" string -> handler callable(args).
_HANDLERS: dict[str, Callable[[argparse.Namespace], None]] = {}


def get_or_create_parser(
    subparsers: argparse._SubParsersAction,
) -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:
    """Return the (parent_parser, sub_action) for ``baton knowledge``.

    Reuses the existing parser when one is already registered so that
    multiple modules can contribute subcommands without clobbering each
    other.  The shared sub-action is stashed on the parent parser via the
    ``_baton_knowledge_sub`` attribute.
    """
    existing = subparsers.choices.get("knowledge") if subparsers.choices else None
    if existing is not None:
        sub = getattr(existing, "_baton_knowledge_sub", None)
        if sub is None:
            sub = existing.add_subparsers(
                dest="knowledge_cmd", metavar="SUBCOMMAND",
            )
            existing._baton_knowledge_sub = sub  # type: ignore[attr-defined]
        return existing, sub

    p = subparsers.add_parser(
        "knowledge",
        help="Knowledge utilities (lifecycle, briefing, harvesting, etc.)",
    )
    sub = p.add_subparsers(dest="knowledge_cmd", metavar="SUBCOMMAND")
    p._baton_knowledge_sub = sub  # type: ignore[attr-defined]
    return p, sub


def register_handler(
    name: str, handler_fn: Callable[[argparse.Namespace], None],
) -> None:
    """Bind ``baton knowledge <name>`` to *handler_fn*."""
    _HANDLERS[name] = handler_fn


def dispatch(args: argparse.Namespace) -> None:
    """Dispatch to the handler bound for ``args.knowledge_cmd``."""
    name = getattr(args, "knowledge_cmd", None)
    if name is None:
        print("Usage: baton knowledge SUBCOMMAND ...")
        print("Run 'baton knowledge --help' for the full list of subcommands.")
        return
    handler_fn = _HANDLERS.get(name)
    if handler_fn is None:
        print(f"error: unknown knowledge subcommand: {name}")
        return
    handler_fn(args)
