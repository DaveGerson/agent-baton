"""CLI entry point for the ``baton`` command.

This module implements the top-level CLI dispatcher for Agent Baton.  The
CLI is built on :mod:`argparse` and uses a plugin-based architecture:
every Python module under ``agent_baton.cli.commands`` (including nested
sub-packages) is auto-discovered at import time.  Each module must expose
a ``register(subparsers)`` function that returns an
:class:`~argparse.ArgumentParser` and a ``handler(args)`` function that
executes the command.

Command groups are organised into sub-packages that map to the six
functional domains of Agent Baton:

* **execution** -- Plan, execute, and manage orchestrated tasks
  (``baton plan``, ``baton execute``, ``baton daemon``, ``baton async``,
  ``baton decide``).
* **observe** -- Inspect execution artifacts, telemetry, and traces
  (``baton dashboard``, ``baton trace``, ``baton usage``, ``baton telemetry``,
  ``baton retro``, ``baton context-profile``, ``baton cleanup``,
  ``baton migrate-storage``, ``baton query``, ``baton context``).
* **govern** -- Policy enforcement, classification, and validation
  (``baton classify``, ``baton compliance``, ``baton policy``,
  ``baton escalations``, ``baton validate``, ``baton spec-check``,
  ``baton detect``).
* **improve** -- Scoring, patterns, evolution, and budget tuning
  (``baton scores``, ``baton evolve``, ``baton patterns``, ``baton budget``,
  ``baton changelog``, ``baton anomalies``, ``baton experiment``,
  ``baton improve``).
* **distribute** -- Packaging, publishing, and sharing
  (``baton package``, ``baton publish``, ``baton pull``,
  ``baton verify-package``, ``baton install``, ``baton transfer``).
* **agents** -- Agent discovery, routing, events, and incidents
  (``baton agents``, ``baton route``, ``baton events``, ``baton incident``).

Standalone commands live directly in ``cli/commands/`` outside the
sub-packages: ``baton pmo``, ``baton sync``, ``baton cquery``,
``baton source``, ``baton serve``.
"""
from __future__ import annotations

import argparse
import importlib
import pkgutil
import types

from agent_baton.cli import commands as commands_pkg

# Command groups for organized --help output
_COMMAND_GROUPS: dict[str, list[str]] = {
    "Core Workflow": ["plan", "execute", "status"],
    "Agents & Routing": ["agents", "route", "events", "incident"],
    "Observability": ["dashboard", "trace", "usage", "telemetry", "context-profile", "retro", "context"],
    "Governance": ["classify", "compliance", "policy", "escalations", "validate", "spec-check", "detect"],
    "Improvement": ["scores", "evolve", "patterns", "budget", "changelog", "experiment", "anomalies", "improve"],
    "Distribution": ["install", "uninstall", "package", "publish", "pull", "transfer", "verify-package"],
    "Storage & Sync": ["sync", "source", "cquery", "migrate-storage", "cleanup"],
    "Execution (Advanced)": ["daemon", "async", "decide"],
    "Portfolio": ["pmo", "serve"],
}


def discover_commands() -> dict[str, types.ModuleType]:
    """Auto-discover all command modules in ``cli/commands/`` and subdirectories.

    Walks the ``commands`` package tree two levels deep:

    1. Top-level modules (e.g. ``pmo_cmd.py``) are scanned directly.
    2. Sub-packages (e.g. ``execution/``, ``observe/``) are entered and each
       child module is scanned.

    Each qualifying module must expose:
      - ``register(subparsers) -> ArgumentParser``  -- registers the subcommand
      - ``handler(args)        -> None``             -- executes the command

    Returns:
        A mapping from module name to the imported module object.  The keys
        are the Python module names (e.g. ``"execute"``, ``"plan_cmd"``),
        *not* the subcommand strings.  The subcommand strings are extracted
        from the subparser ``prog`` attribute during registration in
        :func:`main`.
    """
    found: dict[str, types.ModuleType] = {}

    # Scan top-level (for any remaining ungrouped commands)
    for info in pkgutil.iter_modules(commands_pkg.__path__):
        if info.ispkg:
            # Scan subdirectory packages
            subpkg = importlib.import_module(f"agent_baton.cli.commands.{info.name}")
            for sub_info in pkgutil.iter_modules(subpkg.__path__):
                mod = importlib.import_module(
                    f"agent_baton.cli.commands.{info.name}.{sub_info.name}"
                )
                if hasattr(mod, "register") and hasattr(mod, "handler"):
                    found[sub_info.name] = mod
        else:
            mod = importlib.import_module(f"agent_baton.cli.commands.{info.name}")
            if hasattr(mod, "register") and hasattr(mod, "handler"):
                found[info.name] = mod
    return found


def main(argv: list[str] | None = None) -> None:
    """Parse CLI arguments and dispatch to the matching command handler.

    This is the main entry point invoked by the ``baton`` console script.
    It builds the argument parser, discovers and registers all command
    modules, resolves the subcommand, and calls its ``handler(args)``
    function.

    Args:
        argv: Explicit argument list for testing.  When ``None`` (the
            default), ``sys.argv[1:]`` is used.
    """
    from importlib.metadata import version, PackageNotFoundError
    try:
        _version = version("agent-baton")
    except PackageNotFoundError:
        _version = "dev"

    parser = argparse.ArgumentParser(
        prog="baton",
        description="Agent Baton — multi-agent orchestration tools",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version}")
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    sub = parser.add_subparsers(dest="command")

    # Discover and register all command modules.
    # Each module's register() returns the subparser it created; we collect
    # the command name from the subparser's prog suffix so the dispatch table
    # uses the subcommand string (e.g. "spec-check", "async") rather than the
    # module filename (e.g. "spec_check", "async_cmd").
    modules = discover_commands()
    dispatch: dict[str, types.ModuleType] = {}
    for _mod_name, mod in modules.items():
        sp = mod.register(sub)
        # sp.prog is "baton <subcommand>"; extract just the subcommand part.
        subcommand = sp.prog.split(None, 1)[1] if " " in sp.prog else sp.prog
        dispatch[subcommand] = mod

    # Build grouped help epilog
    lines = ["\nCommand groups:"]
    for group_name, cmd_names in _COMMAND_GROUPS.items():
        # Only include commands that actually exist
        available = [c for c in cmd_names if c in dispatch]
        if available:
            lines.append(f"\n  {group_name}:")
            lines.append(f"    {', '.join(available)}")

    # Any commands not in a group
    grouped = {c for cmds in _COMMAND_GROUPS.values() for c in cmds}
    ungrouped = sorted(set(dispatch.keys()) - grouped)
    if ungrouped:
        lines.append(f"\n  Other:")
        lines.append(f"    {', '.join(ungrouped)}")

    lines.append(f"\nQuick start:")
    lines.append(f"  1. baton plan \"task description\" --save --explain")
    lines.append(f"  2. baton execute start")
    lines.append(f"  3. baton execute next              # get next action")
    lines.append(f"     If DISPATCH: spawn agent, then:")
    lines.append(f"     baton execute record --step-id ID --agent NAME --status complete")
    lines.append(f"     If GATE: run test, then:")
    lines.append(f"     baton execute gate --phase-id ID --result pass")
    lines.append(f"  4. Repeat step 3 until ACTION: COMPLETE")
    lines.append(f"  5. baton execute complete")
    lines.append(f"")
    lines.append(f"Full walkthrough: docs/examples/first-run.md")
    lines.append(f"")

    parser.epilog = "\n".join(lines)
    parser.formatter_class = argparse.RawDescriptionHelpFormatter

    args = parser.parse_args(argv)

    if getattr(args, "no_color", False):
        from agent_baton.cli.colors import set_color_enabled
        set_color_enabled(False)

    if args.command is None:
        from pathlib import Path
        if not Path(".claude/agents").exists():
            print("Agent Baton is not installed in this project.\n")
            print("Quick start:")
            print("  baton install --scope project --source /path/to/agent-baton")
            print()
            print("Or run the install script from the agent-baton repo:")
            print("  scripts/install.sh")
            print()
        parser.print_help()
        return

    dispatch[args.command].handler(args)


if __name__ == "__main__":
    main()
