"""Tests for DX.7 Phase C — orphan command folding.

Covers:
- baton storage migrate subcommand registered
- baton install verify subcommand registered
- baton learn improve subcommand registered
- baton migrate-storage still works with deprecation warning
- baton verify-package still works with deprecation warning
- baton improve still works with deprecation warning
- _COMMAND_GROUPS no longer lists the three orphans
"""
from __future__ import annotations

import argparse
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli import main as cli_main
from agent_baton.cli.main import _COMMAND_GROUPS, _DEPRECATED_HELP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_dispatch() -> dict[str, object]:
    """Run discover_commands and register all modules, return the dispatch table."""
    parser = argparse.ArgumentParser(prog="baton")
    sub = parser.add_subparsers(dest="command")
    modules = cli_main.discover_commands()
    dispatch: dict[str, object] = {}
    for _mod_name, mod in modules.items():
        sp = mod.register(sub)
        subcommand = sp.prog.split(None, 1)[1] if " " in sp.prog else sp.prog
        dispatch[subcommand] = mod
    return dispatch


def _get_subcommand_names(parser: argparse.ArgumentParser) -> list[str]:
    """Return the names of subparsers registered on a parser."""
    names: list[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.extend(action.choices.keys())
    return names


# ---------------------------------------------------------------------------
# 1. New subcommands registered
# ---------------------------------------------------------------------------

def test_storage_migrate_subcommand_registered() -> None:
    """'baton storage migrate' subcommand must exist."""
    from agent_baton.cli.commands.observe import storage_cmd

    root = argparse.ArgumentParser(prog="baton")
    sub = root.add_subparsers(dest="command")
    storage_parser = storage_cmd.register(sub)

    assert storage_parser.prog == "baton storage", (
        f"Expected 'baton storage', got '{storage_parser.prog}'"
    )
    subnames = _get_subcommand_names(storage_parser)
    assert "migrate" in subnames, f"'migrate' not in storage subcommands: {subnames}"


def test_install_verify_subcommand_registered() -> None:
    """'baton install verify' subcommand must exist."""
    from agent_baton.cli.commands.distribute import install

    root = argparse.ArgumentParser(prog="baton")
    sub = root.add_subparsers(dest="command")
    install_parser = install.register(sub)

    assert install_parser.prog == "baton install", (
        f"Expected 'baton install', got '{install_parser.prog}'"
    )
    subnames = _get_subcommand_names(install_parser)
    assert "verify" in subnames, f"'verify' not in install subcommands: {subnames}"


def test_learn_improve_subcommand_registered() -> None:
    """'baton learn improve' subcommand must exist."""
    from agent_baton.cli.commands.improve import learn_cmd

    root = argparse.ArgumentParser(prog="baton")
    sub = root.add_subparsers(dest="command")
    learn_parser = learn_cmd.register(sub)

    assert learn_parser.prog == "baton learn", (
        f"Expected 'baton learn', got '{learn_parser.prog}'"
    )
    subnames = _get_subcommand_names(learn_parser)
    assert "improve" in subnames, f"'improve' not in learn subcommands: {subnames}"


# ---------------------------------------------------------------------------
# 2. Deprecated shims still work with warning
# ---------------------------------------------------------------------------

def test_old_migrate_storage_still_works_with_deprecation_warning() -> None:
    """baton migrate-storage must emit a WARN to stderr and not crash."""
    from agent_baton.cli.commands.observe import migrate_storage

    root = argparse.ArgumentParser(prog="baton")
    sub = root.add_subparsers(dest="command")
    p = migrate_storage.register(sub)

    assert p.prog == "baton migrate-storage", (
        f"Expected 'baton migrate-storage', got '{p.prog}'"
    )

    args = root.parse_args(["migrate-storage", "--dry-run"])

    # The handler should print a deprecation warning to stderr and delegate.
    # We mock _cmd_migrate to avoid needing real filesystem.
    stderr_capture = StringIO()
    with patch(
        "agent_baton.cli.commands.observe.storage_cmd._cmd_migrate"
    ) as mock_migrate, patch("sys.stderr", stderr_capture):
        migrate_storage.handler(args)
        mock_migrate.assert_called_once_with(args)

    warning = stderr_capture.getvalue()
    assert "deprecated" in warning.lower() or "WARN" in warning, (
        f"Expected deprecation warning on stderr, got: {warning!r}"
    )
    assert "baton storage migrate" in warning, (
        f"Expected new command name in warning, got: {warning!r}"
    )


def test_old_verify_package_still_works_with_deprecation_warning() -> None:
    """baton verify-package must emit a WARN to stderr and not crash."""
    from agent_baton.cli.commands.distribute import verify_package

    root = argparse.ArgumentParser(prog="baton")
    sub = root.add_subparsers(dest="command")
    p = verify_package.register(sub)

    assert p.prog == "baton verify-package", (
        f"Expected 'baton verify-package', got '{p.prog}'"
    )

    args = root.parse_args(["verify-package", "pkg.tar.gz"])

    stderr_capture = StringIO()
    with patch(
        "agent_baton.cli.commands.distribute.install._cmd_verify"
    ) as mock_verify, patch("sys.stderr", stderr_capture):
        verify_package.handler(args)
        mock_verify.assert_called_once_with(args)

    warning = stderr_capture.getvalue()
    assert "deprecated" in warning.lower() or "WARN" in warning, (
        f"Expected deprecation warning on stderr, got: {warning!r}"
    )
    assert "baton install verify" in warning, (
        f"Expected new command name in warning, got: {warning!r}"
    )


def test_old_improve_still_works_with_deprecation_warning() -> None:
    """baton improve must emit a WARN to stderr and not crash."""
    from agent_baton.cli.commands.improve import improve_cmd

    root = argparse.ArgumentParser(prog="baton")
    sub = root.add_subparsers(dest="command")
    p = improve_cmd.register(sub)

    assert p.prog == "baton improve", (
        f"Expected 'baton improve', got '{p.prog}'"
    )

    args = root.parse_args(["improve", "--report"])

    stderr_capture = StringIO()
    with patch(
        "agent_baton.cli.commands.improve.improve_cmd._improve_handler_impl"
    ) as mock_impl, patch("sys.stderr", stderr_capture):
        improve_cmd.handler(args)
        mock_impl.assert_called_once_with(args)

    warning = stderr_capture.getvalue()
    assert "deprecated" in warning.lower() or "WARN" in warning, (
        f"Expected deprecation warning on stderr, got: {warning!r}"
    )
    assert "baton learn improve" in warning, (
        f"Expected new command name in warning, got: {warning!r}"
    )


# ---------------------------------------------------------------------------
# 3. _COMMAND_GROUPS no longer lists the orphans
# ---------------------------------------------------------------------------

def test_command_groups_no_longer_lists_orphans() -> None:
    """The three retired top-level names must not appear in any command group."""
    orphans = {"migrate-storage", "verify-package", "improve"}
    all_grouped: set[str] = set()
    for cmds in _COMMAND_GROUPS.values():
        all_grouped.update(cmds)

    found = orphans & all_grouped
    assert not found, (
        f"Orphan commands still listed in _COMMAND_GROUPS: {found}. "
        "Remove them and add their parents (storage, learn) instead."
    )


def test_deprecated_help_dict_contains_three_migrations() -> None:
    """_DEPRECATED_HELP must document all three retired commands."""
    expected = {"migrate-storage", "verify-package", "improve"}
    missing = expected - set(_DEPRECATED_HELP.keys())
    assert not missing, (
        f"_DEPRECATED_HELP is missing entries for: {missing}"
    )


def test_storage_command_in_command_groups() -> None:
    """'storage' must appear in _COMMAND_GROUPS (Storage & Sync group)."""
    all_grouped: set[str] = set()
    for cmds in _COMMAND_GROUPS.values():
        all_grouped.update(cmds)
    assert "storage" in all_grouped, (
        "'storage' not found in any _COMMAND_GROUPS entry"
    )
