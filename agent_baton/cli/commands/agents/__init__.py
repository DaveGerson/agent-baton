"""CLI command group: agents.

Commands for discovering available agents, validating generated agent
definitions, routing roles to stack-specific agent flavors, querying the
domain event log, and managing incident response workflows.

``agents.py`` and ``doctor_cmd.py`` cooperatively share the single
``baton agents`` parser via the helpers below (mirrors the convention in
``agent_baton/cli/commands/knowledge/__init__.py``):

* Use :func:`ensure_parent_parser` (or :func:`get_or_create_parser`) instead
  of ``subparsers.add_parser("agents", ...)`` directly so multiple modules
  can register subcommands on the same parent without one silently
  clobbering the other in argparse's ``_name_parser_map``.
* Each subcommand registers its handler with :func:`register_handler`,
  keyed by ``args.agents_cmd`` (``None`` for the bare ``baton agents``
  listing default).
* Every cooperating module's own ``handler(args)`` should just forward to
  :func:`dispatch` -- that way it doesn't matter which module's `register()`
  call is used by ``main.py``'s auto-discovered dispatch table, since they
  all behave identically.
"""
from __future__ import annotations

import argparse
from typing import Callable

# Map of "agents_cmd" string (or None for the bare listing default) ->
# handler callable(args).
_HANDLERS: dict[str | None, Callable[[argparse.Namespace], None]] = {}


def get_or_create_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:  # type: ignore[type-arg]
    """Return the ``(parent_parser, sub_action)`` for ``baton agents``.

    Reuses the existing parser when one is already registered so multiple
    modules can contribute subcommands without clobbering each other.
    """
    existing = subparsers.choices.get("agents") if subparsers.choices else None
    if existing is not None:
        sub = getattr(existing, "_baton_agents_sub", None)
        if sub is None:
            sub = _find_existing_subparsers_action(existing)
            if sub is None:
                sub = existing.add_subparsers(
                    dest="agents_cmd", metavar="SUBCOMMAND",
                )
            existing._baton_agents_sub = sub  # type: ignore[attr-defined]
            existing.set_defaults(_dispatch=dispatch)
        return existing, sub

    p = subparsers.add_parser("agents", help="List and validate available agents")
    sub = p.add_subparsers(dest="agents_cmd", metavar="SUBCOMMAND")
    p._baton_agents_sub = sub  # type: ignore[attr-defined]
    p.set_defaults(_dispatch=dispatch)
    return p, sub


def ensure_parent_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> argparse._SubParsersAction:  # type: ignore[type-arg]
    """Return the shared ``agents`` sub-action (creating it if needed)."""
    _, sub = get_or_create_parser(subparsers)
    return sub


def register_handler(
    name: str | None, handler_fn: Callable[[argparse.Namespace], None],
) -> None:
    """Bind ``baton agents <name>`` to *handler_fn*.

    ``name=None`` binds the bare ``baton agents`` (no subcommand) default.
    """
    _HANDLERS[name] = handler_fn


def dispatch(args: argparse.Namespace) -> None:
    """Dispatch to the handler bound for ``args.agents_cmd``."""
    name = getattr(args, "agents_cmd", None)
    handler_fn = _HANDLERS.get(name)
    if handler_fn is None:
        print(f"error: unknown agents subcommand: {name}")
        return
    handler_fn(args)


def _find_existing_subparsers_action(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction | None:  # type: ignore[type-arg]
    """Return the parser's existing ``_SubParsersAction`` if one exists."""
    for action in getattr(parser, "_actions", ()):
        if isinstance(action, argparse._SubParsersAction):  # type: ignore[attr-defined]
            return action
    return None
