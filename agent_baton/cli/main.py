"""CLI entry point for the ``baton`` command.

This module implements the top-level CLI dispatcher for Agent Baton. The
CLI is built on argparse and uses a plugin-based architecture: every
Python module under agent_baton.cli.commands (including nested sub-packages)
is auto-discovered at import time. Each module must expose register(subparsers)
and handler(args) functions.

Command groups are organised into sub-packages that map to six functional
domains: execution, observe, govern, improve, distribute, and agents.
Standalone commands (pmo, sync, cquery, source, serve) live directly
in cli/commands/.
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
    "Agents & Routing": ["agents", "route", "events", "incident", "agent-context"],
    "Observability": ["viz", "dashboard", "trace", "usage", "telemetry", "context-profile", "retro", "context", "export", "test-plan"],
    "Integrations": ["webhook"],
    "Governance": ["classify", "compliance", "policy", "escalations", "validate", "spec-check", "detect"],
    "Improvement": ["scores", "patterns", "budget", "changelog", "anomalies", "learn"],
    "Distribution": ["install", "uninstall", "package", "publish", "pull", "transfer"],
    "Storage & Sync": ["sync", "source", "cquery", "storage", "cleanup", "maintenance"],
    "Memory": ["beads", "souls"],
    "Execution (Advanced)": ["daemon", "async", "decide"],
    "Portfolio": ["pmo", "serve"],
    "Deprecated": ["evolve", "experiment"],
}

# Deprecated top-level commands — kept for backward compatibility.
# These still work but print a WARN to stderr on each invocation.
_DEPRECATED_HELP: dict[str, str] = {
    "migrate-storage": "use 'baton storage migrate' instead",
    "verify-package": "use 'baton install verify' instead",
    "improve": "use 'baton learn improve' instead",
    "evolve": "use 'baton learn run-cycle' instead",
    "experiment": "use 'baton learn run-cycle' instead",
}


def discover_commands() -> dict[str, types.ModuleType]:
    """Auto-discover all command modules in cli/commands/ and subdirectories.

    Each module must expose:
      - register(subparsers) -> ArgumentParser  (registers the subcommand)
      - handler(args)        -> None             (executes the command)
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
    # On Windows the default console encoding is often cp1252, which cannot
    # represent Unicode characters (em dashes, arrows, etc.) used throughout
    # the CLI output and logging.  Reconfigure stdout/stderr to UTF-8 with
    # replacement fallback so these characters are printed instead of raising
    # UnicodeEncodeError.
    #
    # On non-Windows platforms, terminals with non-UTF-8 LANG settings can
    # also fail.  The guard now runs on all platforms when the stream encoding
    # is not UTF-8.  When reconfigure is unavailable (piped/redirected output,
    # older Python builds), we set PYTHONIOENCODING as a last-resort fallback
    # and wrap the stream.
    import os
    import sys
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        encoding = getattr(stream, "encoding", None) or ""
        if encoding.lower().replace("-", "") == "utf8":
            continue
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
                continue
            except Exception:
                pass
        # Last-resort: set env var for any subprocesses and wrap the stream
        os.environ.setdefault("PYTHONIOENCODING", "utf-8:replace")
        try:
            import io
            wrapped = io.TextIOWrapper(
                stream.buffer, encoding="utf-8", errors="replace", line_buffering=stream.line_buffering,
            )
            setattr(sys, stream_name, wrapped)
        except Exception:
            pass

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
    lines = ["\nCommon workflows:"]
    lines.append(f"  1. baton plan \"task description\" --save --explain")
    lines.append(f"  2. baton execute start")
    lines.append(f"  3. baton execute next")
    lines.append(f"  4. Repeat step 3 until ACTION: COMPLETE")
    lines.append(f"  5. baton execute complete")
    lines.append(f"")
    lines.append(f"\nCommand groups:")
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

    # Emit deprecation banner to stderr *before* parse_args so it appears even
    # when --help is passed (argparse prints help then sys.exit(0), never
    # reaching the handler).
    import sys as _sys
    _argv = argv if argv is not None else _sys.argv[1:]
    if _argv and _argv[0] in _DEPRECATED_HELP:
        _cmd = _argv[0]
        print(
            f"DEPRECATED: 'baton {_cmd}' is deprecated — {_DEPRECATED_HELP[_cmd]}",
            file=_sys.stderr,
        )

    # For deprecated shim commands, use parse_known_args so that legacy scripts
    # passing old flags (e.g. `baton evolve --run`, `baton experiment --id foo`)
    # still reach the handler's deprecation message instead of getting an argparse
    # "unrecognized arguments" error (bd-ed80).
    if _argv and _argv[0] in _DEPRECATED_HELP:
        args, _unknown = parser.parse_known_args(argv)
    else:
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

    import os
    import traceback

    try:
        dispatch[args.command].handler(args)
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:
        if os.environ.get("BATON_DEBUG"):
            traceback.print_exc(file=sys.stderr)
        else:
            print(
                f"error: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            print(
                "  Run with BATON_DEBUG=1 for full traceback.",
                file=sys.stderr,
            )
        sys.exit(1)


if __name__ == "__main__":
    main()
