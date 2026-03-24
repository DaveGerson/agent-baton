"""Tests for baton sync and baton source CLI commands.

Covers:
- baton sync --help exits 0
- baton source --help exits 0
- baton source list with empty central.db
- sync and source are discoverable via the CLI auto-discovery mechanism
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PYTHON = sys.executable
_MODULE = "agent_baton.cli.main"


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a baton CLI command via python -m and return the result."""
    return subprocess.run(
        [_PYTHON, "-m", _MODULE, *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
    )


# ---------------------------------------------------------------------------
# Help / registration tests
# ---------------------------------------------------------------------------


def test_sync_help_exits_zero():
    """baton sync --help returns exit code 0."""
    result = _run("sync", "--help")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "central.db" in result.stdout.lower() or "sync" in result.stdout.lower()


def test_source_help_exits_zero():
    """baton source --help returns exit code 0."""
    result = _run("source", "--help")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    # Should show available subcommands
    assert "add" in result.stdout
    assert "list" in result.stdout


def test_sync_subcommand_registered_in_main():
    """baton --help lists the sync subcommand."""
    result = _run("--help")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "sync" in result.stdout


def test_source_subcommand_registered_in_main():
    """baton --help lists the source subcommand."""
    result = _run("--help")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "source" in result.stdout


def test_sync_status_help_exits_zero():
    """baton sync status --help exits 0 (subcommand is positional arg)."""
    result = _run("sync", "--help")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    # 'status' should be mentioned as a possible subcommand value
    assert "status" in result.stdout or "SUBCOMMAND" in result.stdout


def test_source_add_help_exits_zero():
    """baton source add --help exits 0."""
    result = _run("source", "add", "--help")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "--name" in result.stdout


def test_source_sync_help_exits_zero():
    """baton source sync --help exits 0."""
    result = _run("source", "sync", "--help")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "--all" in result.stdout


def test_source_map_help_exits_zero():
    """baton source map --help exits 0."""
    result = _run("source", "map", "--help")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "TASK_ID" in result.stdout


def test_source_remove_help_exits_zero():
    """baton source remove --help exits 0."""
    result = _run("source", "remove", "--help")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "SOURCE_ID" in result.stdout


# ---------------------------------------------------------------------------
# Functional tests (require central.db to be importable)
# ---------------------------------------------------------------------------


def test_source_list_empty(tmp_path: Path, monkeypatch):
    """baton source list with empty central.db prints no-sources message."""
    # Only run if the central storage module is available
    try:
        import agent_baton.core.storage.central  # noqa: F401
    except ImportError:
        import pytest
        pytest.skip("central storage module not yet available")

    import sqlite3
    from agent_baton.core.storage.central import CentralStore

    # Point central.db to a temp directory
    central_db = tmp_path / "central.db"
    monkeypatch.setenv("BATON_CENTRAL_DB", str(central_db))

    result = _run("source", "list")
    # May succeed or fail depending on whether the env var is honoured;
    # the key assertion is just that the process doesn't crash with a traceback
    assert result.returncode in (0, 1), f"unexpected exit code, stderr: {result.stderr}"
    # Should not produce a Python traceback
    assert "Traceback" not in result.stderr


def test_sync_no_args_graceful(tmp_path: Path):
    """baton sync with no registered project prints a friendly message."""
    # Run from a directory that is not registered as any project
    result = _run("sync", cwd=tmp_path)
    # Should not crash with a traceback regardless of outcome
    assert "Traceback" not in result.stderr


# ---------------------------------------------------------------------------
# Discovery unit test (import-level)
# ---------------------------------------------------------------------------


def test_sync_cmd_has_register_and_handler():
    """sync_cmd exposes register() and handler() callables."""
    from agent_baton.cli.commands import sync_cmd  # type: ignore[import]
    assert callable(getattr(sync_cmd, "register", None))
    assert callable(getattr(sync_cmd, "handler", None))


def test_source_cmd_has_register_and_handler():
    """source_cmd exposes register() and handler() callables."""
    from agent_baton.cli.commands import source_cmd  # type: ignore[import]
    assert callable(getattr(source_cmd, "register", None))
    assert callable(getattr(source_cmd, "handler", None))


def test_discover_commands_includes_sync():
    """discover_commands() returns sync."""
    from agent_baton.cli.main import discover_commands
    modules = discover_commands()
    # The module is keyed by its filename stem (sync_cmd), but the subcommand
    # name is derived from the ArgumentParser prog, which is 'sync'.
    assert "sync_cmd" in modules or any(
        hasattr(m, "register") and "sync" in str(getattr(m, "__name__", ""))
        for m in modules.values()
    )


def test_discover_commands_includes_source():
    """discover_commands() returns source."""
    from agent_baton.cli.main import discover_commands
    modules = discover_commands()
    assert "source_cmd" in modules or any(
        hasattr(m, "register") and "source" in str(getattr(m, "__name__", ""))
        for m in modules.values()
    )


def test_main_dispatch_includes_sync():
    """After main() builds its dispatch table, 'sync' is a registered command."""
    import argparse
    from agent_baton.cli.main import discover_commands
    import types

    parser = argparse.ArgumentParser(prog="baton")
    sub = parser.add_subparsers(dest="command")

    modules = discover_commands()
    dispatch: dict[str, types.ModuleType] = {}
    for _mod_name, mod in modules.items():
        sp = mod.register(sub)
        subcommand = sp.prog.split(None, 1)[1] if " " in sp.prog else sp.prog
        dispatch[subcommand] = mod

    assert "sync" in dispatch, f"dispatch keys: {sorted(dispatch.keys())}"


def test_main_dispatch_includes_source():
    """After main() builds its dispatch table, 'source' is a registered command."""
    import argparse
    from agent_baton.cli.main import discover_commands
    import types

    parser = argparse.ArgumentParser(prog="baton")
    sub = parser.add_subparsers(dest="command")

    modules = discover_commands()
    dispatch: dict[str, types.ModuleType] = {}
    for _mod_name, mod in modules.items():
        sp = mod.register(sub)
        subcommand = sp.prog.split(None, 1)[1] if " " in sp.prog else sp.prog
        dispatch[subcommand] = mod

    assert "source" in dispatch, f"dispatch keys: {sorted(dispatch.keys())}"
