"""``baton knowledge`` command group (K2.x).

Each module in this package registers its subcommand on the shared
``knowledge`` parent parser via the ``register_subcommand`` helper.  The
auto-discovery loop in ``cli/main.py`` only invokes ``register`` /
``handler`` on modules — packages are skipped — so the parent parser is
created on demand by the first sibling that needs it.

K2.7 (and other follow-ups) should follow the same pattern: import
``ensure_parent_parser`` from this package and add their subparser there.
"""
from __future__ import annotations

import argparse
from typing import Callable


# Module-level singletons — populated by the first sibling that calls
# ``ensure_parent_parser``.  argparse does not allow re-adding a parser
# with the same name, so we cache the parent and its sub-subparsers.
_PARENT_PARSER: argparse.ArgumentParser | None = None
_SUB: argparse._SubParsersAction | None = None  # type: ignore[type-arg]
_HANDLERS: dict[str, Callable[[argparse.Namespace], None]] = {}


def ensure_parent_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> argparse._SubParsersAction:  # type: ignore[type-arg]
    """Return the shared ``knowledge`` sub-subparsers, creating it if needed.

    Sibling modules call this from their own ``register`` function so the
    final dispatch path is ``baton knowledge <subcommand>``.
    """
    global _PARENT_PARSER, _SUB
    if _PARENT_PARSER is None:
        _PARENT_PARSER = subparsers.add_parser(
            "knowledge",
            help="Knowledge pack analytics and lifecycle commands",
        )
        _SUB = _PARENT_PARSER.add_subparsers(
            dest="knowledge_cmd", metavar="SUBCOMMAND"
        )
        _PARENT_PARSER.set_defaults(_dispatch=_dispatch)
    assert _SUB is not None
    return _SUB


def register_handler(
    name: str, fn: Callable[[argparse.Namespace], None]
) -> None:
    """Register a per-subcommand handler function."""
    _HANDLERS[name] = fn


def _dispatch(args: argparse.Namespace) -> None:
    sub = getattr(args, "knowledge_cmd", None)
    if sub is None:
        if _PARENT_PARSER is not None:
            _PARENT_PARSER.print_help()
        return
    handler = _HANDLERS.get(sub)
    if handler is None:
        raise SystemExit(f"Unknown knowledge subcommand: {sub}")
    handler(args)
