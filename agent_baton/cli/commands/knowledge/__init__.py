"""CLI command group: ``baton knowledge``.

Multiple modules under this package register subcommands on the same
``baton knowledge`` parent parser.  Cooperation rules:

* Use either :func:`ensure_parent_parser` (returns the sub-action only)
  or :func:`get_or_create_parser` (returns the ``(parent, sub)`` tuple)
  instead of ``subparsers.add_parser`` so all modules share one parent
  and one ``knowledge_cmd`` sub-action.
* Each subcommand registers its handler with :func:`register_handler`.
* The shared :func:`dispatch` function routes ``args.knowledge_cmd`` to
  the registered handler.  Modules can either call ``dispatch(args)``
  directly from their own ``handler`` or rely on ``args._dispatch``
  injected by ``ensure_parent_parser``.

Without this convention, repeated ``add_parser("knowledge")`` calls
silently clobber each other and only one module's subcommands survive.
"""
from __future__ import annotations

import argparse
from typing import Callable

# Map of "knowledge_cmd" string -> handler callable(args).
_HANDLERS: dict[str, Callable[[argparse.Namespace], None]] = {}


def get_or_create_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:  # type: ignore[type-arg]
    """Return the ``(parent_parser, sub_action)`` for ``baton knowledge``.

    Reuses the existing parser when one is already registered so multiple
    modules can contribute subcommands without clobbering each other.
    The shared sub-action is stashed on the parent parser via the
    ``_baton_knowledge_sub`` attribute.
    """
    existing = subparsers.choices.get("knowledge") if subparsers.choices else None
    if existing is not None:
        sub = getattr(existing, "_baton_knowledge_sub", None)
        if sub is None:
            # Older modules (brief.py, harvest_cmd.py) call add_subparsers
            # directly on the parent without going through this helper.
            # Find their existing _SubParsersAction via argparse internals
            # rather than adding a second one (which raises
            # "cannot have multiple subparser arguments").
            sub = _find_existing_subparsers_action(existing)
            if sub is None:
                sub = existing.add_subparsers(
                    dest="knowledge_cmd", metavar="SUBCOMMAND",
                )
            existing._baton_knowledge_sub = sub  # type: ignore[attr-defined]
            existing.set_defaults(_dispatch=dispatch)
        return existing, sub

    p = subparsers.add_parser(
        "knowledge",
        help="Knowledge utilities (lifecycle, briefing, harvesting, etc.)",
    )
    sub = p.add_subparsers(dest="knowledge_cmd", metavar="SUBCOMMAND")
    p._baton_knowledge_sub = sub  # type: ignore[attr-defined]
    p.set_defaults(_dispatch=dispatch)
    return p, sub


def ensure_parent_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> argparse._SubParsersAction:  # type: ignore[type-arg]
    """Return the shared ``knowledge`` sub-action (creating it if needed).

    Compatibility helper for modules that only want the sub-action.
    """
    _, sub = get_or_create_parser(subparsers)
    return sub


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


def _find_existing_subparsers_action(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction | None:  # type: ignore[type-arg]
    """Return the parser's existing ``_SubParsersAction`` if one exists.

    argparse stores actions in the private ``_actions`` list. Only one
    ``_SubParsersAction`` is permitted per parser, so the first match (if
    any) is the one we want to reuse.
    """
    for action in getattr(parser, "_actions", ()):
        if isinstance(action, argparse._SubParsersAction):  # type: ignore[attr-defined]
            return action
    return None


# Backward-compat alias for HEAD's previous private name.
_dispatch = dispatch
