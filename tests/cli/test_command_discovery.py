"""Regression tests for CLI command auto-discovery.

These guard against argparse subparser collisions: every module under
``cli/commands/`` that exposes ``register()`` must claim a unique
top-level subcommand name.
"""
from __future__ import annotations

import argparse

from agent_baton.cli.main import discover_commands


def _build_parser() -> tuple[argparse.ArgumentParser, dict[str, str]]:
    parser = argparse.ArgumentParser(prog="baton")
    sub = parser.add_subparsers(dest="command")
    name_to_module: dict[str, str] = {}
    for mod_name, mod in discover_commands().items():
        sp = mod.register(sub)
        subcommand = sp.prog.split(None, 1)[1] if " " in sp.prog else sp.prog
        # The same module may contribute to a cooperatively-shared parser
        # (e.g. release / release_cmd + release/profile_cmd); record the
        # first claimant only.
        name_to_module.setdefault(subcommand, mod_name)
    return parser, name_to_module


def test_discovery_does_not_collide() -> None:
    """Loading every CLI module must not raise argparse.ArgumentError."""
    parser, _ = _build_parser()
    # If we got here, no module raised on add_parser.
    assert parser is not None


def test_context_subcommands_are_disambiguated() -> None:
    """``context`` and ``agent-context`` are both registered (issue #2)."""
    _, names = _build_parser()
    assert "context" in names
    assert "agent-context" in names


def test_release_parser_owns_readiness_and_profile() -> None:
    """``baton release`` exposes both readiness and profile subcommands."""
    parser, _ = _build_parser()
    sub_actions = [
        a for a in parser._actions
        if isinstance(a, argparse._SubParsersAction)
    ]
    assert sub_actions, "expected a top-level subparsers action"
    release = sub_actions[0].choices.get("release")
    assert release is not None, "release parser missing"
    release_sub_actions = [
        a for a in release._actions
        if isinstance(a, argparse._SubParsersAction)
    ]
    assert release_sub_actions
    choices = release_sub_actions[0].choices
    for expected in ("create", "list", "show", "tag", "untag", "notes", "readiness", "profile"):
        assert expected in choices, f"missing release subcommand: {expected}"
