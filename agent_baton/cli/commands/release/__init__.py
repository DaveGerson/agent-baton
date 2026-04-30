"""CLI command group: release-quality tooling.

Houses release-time CLIs: ``predict-conflicts`` (R3.7), ``release
readiness`` (R3.2), ``release profile`` (R3.8), release notes,
deploy-time gates, rollout summaries.
"""
from __future__ import annotations

import argparse


def get_or_create_release_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
    *,
    help_text: str = "Release management",
    dest: str = "release_subcommand",
) -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:  # type: ignore[type-arg]
    """Cooperative ``release`` parent parser.

    Multiple modules (release_cmd.py, release/profile_cmd.py,
    release/readiness_cmd.py) extend the ``release`` namespace with their
    own subcommands.  argparse refuses to register the same name twice,
    so we share a single parent parser by stashing the subparsers action
    on it (``_baton_release_sub``) and reusing whichever module gets
    discovered first.
    """
    existing = subparsers.choices.get("release") if subparsers.choices else None
    if existing is not None:
        sub = getattr(existing, "_baton_release_sub", None)
        if sub is None:
            for action in getattr(existing, "_actions", ()):
                if isinstance(action, argparse._SubParsersAction):  # type: ignore[attr-defined]
                    sub = action
                    break
            if sub is None:
                sub = existing.add_subparsers(dest=dest, metavar="SUBCOMMAND")
            existing._baton_release_sub = sub  # type: ignore[attr-defined]
        return existing, sub

    p = subparsers.add_parser("release", help=help_text)
    sub = p.add_subparsers(dest=dest, metavar="SUBCOMMAND")
    p._baton_release_sub = sub  # type: ignore[attr-defined]
    return p, sub
