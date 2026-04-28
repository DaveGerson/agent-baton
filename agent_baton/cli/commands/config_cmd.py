"""CLI command: ``baton config`` — inspect, validate, and scaffold ``baton.yaml``.

Subcommands
-----------
show       Print the discovered baton.yaml path and parsed contents.
validate   Validate a baton.yaml file (errors return non-zero).
init       Write a starter baton.yaml to the cwd with commented examples.

The starter file mirrors :mod:`agent_baton.core.config.project_config`'s
field set so users immediately see the supported knobs.  All fields are
optional; ``baton.yaml`` is purely additive — its absence keeps planner
behavior identical to prior releases.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_baton.core.config import ProjectConfig
from agent_baton.core.config.project_config import CONFIG_FILENAME

# ---------------------------------------------------------------------------
# Starter template
# ---------------------------------------------------------------------------

_STARTER_TEMPLATE = """\
# Agent Baton project config — drop in repo root
# All fields optional. Missing fields use built-in defaults.
# Empty config = no behavioral change vs. earlier baton releases.

# Preferred agent per domain.  When a step has no explicit agent and
# its base name maps to a known domain, this value is substituted.
default_agents:
  backend: backend-engineer--python
  frontend: frontend-engineer--react
  test: test-engineer

# Gate types appended to every phase (pytest, lint, mypy, build, ...).
# Deduplicated against gates already on the phase.
default_gates:
  - pytest

# Risk level for tasks the classifier doesn't categorize.
# One of LOW | MEDIUM | HIGH.
default_risk_level: MEDIUM

# Default isolation policy applied to dispatched steps.
# Use "worktree" for parallel-friendly defaults; leave empty to disable.
default_isolation: worktree

# Ordered routing rules.  First match wins.
auto_route_rules:
  - path_glob: "tests/**"
    agent: test-engineer
  - path_glob: "docs/**"
    agent: documentation-architect

# Globs the planner adds to every step's blocked_paths.
excluded_paths:
  - "node_modules/**"
  - ".venv/**"
"""


# ---------------------------------------------------------------------------
# Registration (auto-discovery pattern: register() + handler())
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``config`` subcommand."""
    p = subparsers.add_parser(
        "config",
        help="Inspect, validate, or scaffold the project's baton.yaml config",
    )
    sub = p.add_subparsers(dest="config_cmd", metavar="SUBCOMMAND")

    # -- show ---------------------------------------------------------------
    show_p = sub.add_parser(
        "show",
        help="Print the discovered baton.yaml path and parsed contents",
    )
    show_p.add_argument(
        "--start-dir",
        dest="start_dir",
        metavar="DIR",
        default=None,
        help="Directory to start the upward search from (default: cwd)",
    )

    # -- validate -----------------------------------------------------------
    validate_p = sub.add_parser(
        "validate",
        help="Validate a baton.yaml file (errors return non-zero)",
    )
    validate_p.add_argument(
        "path",
        metavar="PATH",
        nargs="?",
        default=None,
        help=f"Path to a {CONFIG_FILENAME} file (defaults to discovery from cwd)",
    )

    # -- init ---------------------------------------------------------------
    init_p = sub.add_parser(
        "init",
        help=f"Write a starter {CONFIG_FILENAME} to the cwd",
    )
    init_p.add_argument(
        "--path",
        dest="target_path",
        metavar="PATH",
        default=None,
        help=f"Output path (default: ./{CONFIG_FILENAME})",
    )
    init_p.add_argument(
        "--force",
        dest="force",
        action="store_true",
        default=False,
        help="Overwrite an existing file at the target path",
    )

    return p


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    """Dispatch to the appropriate ``config`` subcommand handler."""
    cmd = getattr(args, "config_cmd", None)
    if cmd is None:
        print("Usage: baton config <subcommand>  [show|validate|init]")
        print("Run `baton config --help` for details.")
        return

    dispatch = {
        "show": _handle_show,
        "validate": _handle_validate,
        "init": _handle_init,
    }
    fn = dispatch.get(cmd)
    if fn is None:
        print(f"error: unknown config subcommand: {cmd}", file=sys.stderr)
        sys.exit(2)
    fn(args)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _handle_show(args: argparse.Namespace) -> None:
    """Print the discovered config (path + JSON-rendered fields)."""
    start = Path(args.start_dir) if args.start_dir else None
    cfg = ProjectConfig.load(start)
    if cfg.source_path is None:
        print(
            f"No {CONFIG_FILENAME} found above "
            f"{(start or Path.cwd()).resolve()}.  Empty defaults in effect."
        )
    else:
        print(f"Loaded {cfg.source_path}")
    print(json.dumps(cfg.to_dict(), indent=2, sort_keys=True))


def _handle_validate(args: argparse.Namespace) -> None:
    """Parse a config file; exit non-zero on failure with a readable message."""
    if args.path:
        path = Path(args.path)
        if not path.exists():
            print(f"error: {path} does not exist", file=sys.stderr)
            sys.exit(1)
        try:
            cfg = ProjectConfig.from_yaml(path)
        except Exception as exc:
            print(f"error: {path} is invalid — {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        cfg = ProjectConfig.load()
        if cfg.source_path is None:
            print(
                f"error: no {CONFIG_FILENAME} discovered above "
                f"{Path.cwd()}",
                file=sys.stderr,
            )
            sys.exit(1)
    print(f"OK: {cfg.source_path or '(empty)'} parses cleanly")


def _handle_init(args: argparse.Namespace) -> None:
    """Write the starter template to the cwd (or ``--path``)."""
    target = Path(args.target_path) if args.target_path else Path.cwd() / CONFIG_FILENAME
    if target.exists() and not args.force:
        print(
            f"error: {target} already exists. Pass --force to overwrite.",
            file=sys.stderr,
        )
        sys.exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_STARTER_TEMPLATE, encoding="utf-8")
    print(f"Wrote starter config to {target}")
