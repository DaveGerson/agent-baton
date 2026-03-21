"""CLI entry point for the baton command."""
from __future__ import annotations

import argparse
import importlib
import pkgutil
import types

from agent_baton.cli import commands as commands_pkg


def discover_commands() -> dict[str, types.ModuleType]:
    """Auto-discover all command modules in cli/commands/.

    Each module must expose:
      - register(subparsers) -> ArgumentParser  (registers the subcommand)
      - handler(args)        -> None             (executes the command)
    """
    found: dict[str, types.ModuleType] = {}
    for info in pkgutil.iter_modules(commands_pkg.__path__):
        mod = importlib.import_module(f"agent_baton.cli.commands.{info.name}")
        if hasattr(mod, "register") and hasattr(mod, "handler"):
            found[info.name] = mod
    return found


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="baton",
        description="Agent Baton — multi-agent orchestration tools",
    )
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

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return

    dispatch[args.command].handler(args)


if __name__ == "__main__":
    main()
