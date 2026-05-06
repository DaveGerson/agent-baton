"""Tests for DX.7 Phase B — CLI consolidation in-tree changes.

Covers:
- _DEPRECATED_HELP constant contents
- Deprecation banner printed to stderr for deprecated commands
- Non-deprecated commands do NOT trigger the banner
- Help epilog ordering: "Common workflows:" before "Command groups:"
- First group in _COMMAND_GROUPS is "Core Workflow"
- Backward-compat: every dispatch command accepts --help without error
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from agent_baton.cli.main import _COMMAND_GROUPS, _DEPRECATED_HELP, main


# ---------------------------------------------------------------------------
# 1. _DEPRECATED_HELP contents
# ---------------------------------------------------------------------------

def test_deprecated_help_contains_evolve() -> None:
    assert "evolve" in _DEPRECATED_HELP
    assert _DEPRECATED_HELP["evolve"]


def test_deprecated_help_contains_experiment() -> None:
    assert "experiment" in _DEPRECATED_HELP
    assert _DEPRECATED_HELP["experiment"]


# ---------------------------------------------------------------------------
# 2. Deprecation banner printed to stderr for deprecated commands
# ---------------------------------------------------------------------------

def test_evolve_help_prints_deprecation_banner_to_stderr() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agent_baton.cli.main", "evolve", "--help"],
        capture_output=True,
        text=True,
    )
    assert "DEPRECATED" in result.stderr


# ---------------------------------------------------------------------------
# 3. Non-deprecated commands do NOT trigger the banner
# ---------------------------------------------------------------------------

def test_learn_help_no_deprecation_banner() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agent_baton.cli.main", "learn", "--help"],
        capture_output=True,
        text=True,
    )
    assert "DEPRECATED" not in result.stderr


# ---------------------------------------------------------------------------
# 4. Help epilog: "Common workflows:" appears before "Command groups:"
# ---------------------------------------------------------------------------

def test_epilog_common_workflows_before_command_groups() -> None:
    # Capture --help output (goes to stdout for argparse)
    result = subprocess.run(
        [sys.executable, "-m", "agent_baton.cli.main", "--help"],
        capture_output=True,
        text=True,
    )
    output = result.stdout
    assert "Common workflows:" in output, "Epilog must contain 'Common workflows:'"
    assert "Command groups:" in output, "Epilog must contain 'Command groups:'"
    assert output.index("Common workflows:") < output.index("Command groups:")


# ---------------------------------------------------------------------------
# 5. First group in _COMMAND_GROUPS is "Core Workflow"
# ---------------------------------------------------------------------------

def test_first_command_group_is_core_workflow() -> None:
    first_key = next(iter(_COMMAND_GROUPS))
    assert first_key == "Core Workflow"


# ---------------------------------------------------------------------------
# 6. Backward-compat: every command in dispatch parses --help without error
# ---------------------------------------------------------------------------

def _all_dispatch_commands() -> list[str]:
    """Collect all registered subcommand names by running baton --help."""
    from agent_baton.cli.main import discover_commands
    import types

    # We need the actual dispatch table, which is built inside main().
    # Re-use discover_commands() and replicate the prog-extraction logic.
    import argparse
    import pkgutil
    import importlib
    from agent_baton.cli import commands as commands_pkg

    modules = discover_commands()

    tmp_parser = argparse.ArgumentParser(prog="baton")
    sub = tmp_parser.add_subparsers(dest="command")
    names: list[str] = []
    for _mod_name, mod in modules.items():
        sp = mod.register(sub)
        subcommand = sp.prog.split(None, 1)[1] if " " in sp.prog else sp.prog
        names.append(subcommand)
    return names


@pytest.mark.parametrize("cmd", _all_dispatch_commands())
def test_command_help_no_error(cmd: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agent_baton.cli.main", cmd, "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"baton {cmd} --help exited {result.returncode}:\n{result.stderr}"
    )
