"""CLI command: ``baton config`` — inspect, validate, and scaffold ``baton.yaml``.

Subcommands
-----------
show       Print the discovered baton.yaml path and parsed contents.
validate   Validate a baton.yaml file (errors return non-zero).
init       Write a starter baton.yaml to the cwd with commented examples.

Every subcommand accepts ``--profile {project,manager}`` (default
``project``). ``project`` is the original, pre-manager-mode behavior:
:class:`~agent_baton.core.config.project_config.ProjectConfig`'s
``default_agents``/``default_gates``/... keys. ``manager`` operates on the
manager-mode PMO section of the SAME ``.claude/baton.yaml`` file (see
docs/internal/manager-mode-pmo-design.md) via
:class:`~agent_baton.core.config.manager.ManagerConfig` -- the two loaders
read disjoint top-level keys out of one shared file (each silently ignores
the other's keys; see ``ManagerConfig._validated``).

The ``project`` starter file mirrors
:mod:`agent_baton.core.config.project_config`'s field set so users
immediately see the supported knobs.  All fields are optional;
``baton.yaml`` is purely additive — its absence keeps planner behavior
identical to prior releases.
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

# Manager-mode profile template — verbatim copy of PRD §9.1's example
# .claude/baton.yaml (docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md).
# `baton config validate --profile manager` / `ManagerConfig.from_yaml` must
# accept this file cleanly; see tests/cli/test_config_cli.py.
_MANAGER_TEMPLATE = """\
version: 1

manager_mode:
  enabled_by_default: false
  project_size_default: medium
  manager_decision_threshold: medium
  assumptions_policy: record_and_continue
  ambiguity_policy: ask_when_high_impact

team:
  max_agents_by_complexity:
    light: 2
    medium: 5
    heavy: 8
  require_role_cards: true
  require_workstream_owners: true
  prefer_specialists_over_generalists: true
  allow_talent_builder: true
  default_roles:
    - architect
    - backend-engineer
    - test-engineer

scoping:
  require_scope_contracts: true
  require_allowed_paths: true
  allow_cross_scope_edits: manager_approval
  scope_expansion_policy: queue_for_manager
  out_of_scope_policy: block_or_escalate

context:
  default_step_token_budget: 12000
  max_knowledge_docs_per_step: 6
  include_prior_phase_handoff: true
  include_full_prior_outputs: false
  summarize_prior_outputs: true
  dedupe_knowledge_across_session: true
  context_bundle_format: json

knowledge_packs:
  discovery_paths:
    - .claude/knowledge
    - docs
    - .
  default_packs:
    - repo-architecture
    - coding-conventions
    - testing-strategy
  required_for_code_steps:
    - coding-conventions
    - testing-strategy
  stale_after_days: 90
  missing_pack_policy: propose

policies:
  phase_completion:
    adversarial_review: always
    handoff_required: true
    gates: project_configured
  project_completion:
    adversarial_review: always
    manager_report: required
    retrospective: required
  review_agents:
    adversarial_review: code-reviewer
    project_review: auditor

gates:
  mode: project_configured
  gate_scope: focused
  allow_smoke_fallback: true
  missing_gate_policy: warn_and_request_manager_decision

reporting:
  write_manager_brief: true
  write_manager_report: true
  decision_log: true
  include_raw_logs_by_default: false
"""

# Manager-mode config conventionally lives under .claude/, not the repo
# root (see ManagerConfig.find_config_file: `.claude/baton.yaml` is
# checked before `baton.yaml`).
_MANAGER_DEFAULT_RELATIVE_PATH = Path(".claude") / "baton.yaml"


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
    _add_profile_arg(show_p)

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
    _add_profile_arg(validate_p)

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
        help=(
            f"Output path (default: ./{CONFIG_FILENAME} for --profile project, "
            f"./{_MANAGER_DEFAULT_RELATIVE_PATH.as_posix()} for --profile manager)"
        ),
    )
    init_p.add_argument(
        "--force",
        dest="force",
        action="store_true",
        default=False,
        help="Overwrite an existing file at the target path",
    )
    _add_profile_arg(init_p)

    return p


def _add_profile_arg(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--profile",
        dest="profile",
        choices=["project", "manager"],
        default="project",
        help=(
            "Which config surface to operate on: 'project' (default) for "
            "ProjectConfig's default_agents/default_gates/... keys, or "
            "'manager' for the manager-mode PMO section (ManagerConfig -- "
            "see docs/internal/manager-mode-pmo-design.md). Both profiles "
            "read/write the same baton.yaml file; each ignores the other's keys."
        ),
    )


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
    if getattr(args, "profile", "project") == "manager":
        _handle_show_manager(args)
        return

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
    if getattr(args, "profile", "project") == "manager":
        _handle_validate_manager(args)
        return

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
    if getattr(args, "profile", "project") == "manager":
        _handle_init_manager(args)
        return

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


# ---------------------------------------------------------------------------
# Manager-mode profile handlers (W3) — operate on ManagerConfig instead of
# ProjectConfig. Imported lazily so a plain `baton config show/validate/init`
# (profile=project, the default) never imports agent_baton.core.config.manager.
# ---------------------------------------------------------------------------


def _handle_show_manager(args: argparse.Namespace) -> None:
    """Print the *effective* merged ManagerConfig (defaults < user config <
    project config) as YAML."""
    import yaml

    from agent_baton.core.config.manager import ManagerConfig

    start = Path(args.start_dir) if getattr(args, "start_dir", None) else None
    cfg = ManagerConfig.load(start)
    if cfg.source_path is None:
        print(
            f"No {CONFIG_FILENAME} found above "
            f"{(start or Path.cwd()).resolve()}. Effective config is built-in defaults."
        )
    else:
        print(f"Loaded {cfg.source_path}")
    for warning in cfg.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(yaml.safe_dump(cfg.to_dict(), sort_keys=False))


def _handle_validate_manager(args: argparse.Namespace) -> None:
    """Parse a manager-mode config; exit non-zero (and name the offending
    key/value) on failure."""
    from agent_baton.core.config.manager import ManagerConfig, ManagerConfigError

    if getattr(args, "path", None):
        path = Path(args.path)
        if not path.exists():
            print(f"error: {path} does not exist", file=sys.stderr)
            sys.exit(1)
        try:
            cfg = ManagerConfig.from_yaml(path)
        except ManagerConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(2)
    else:
        try:
            cfg = ManagerConfig.load()
        except ManagerConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(2)
        if cfg.source_path is None:
            print(
                f"error: no {CONFIG_FILENAME} discovered above {Path.cwd()}",
                file=sys.stderr,
            )
            sys.exit(1)

    for warning in cfg.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"OK: {cfg.source_path or '(empty)'} parses cleanly (manager profile)")


def _handle_init_manager(args: argparse.Namespace) -> None:
    """Write the manager-mode starter template (PRD §9.1) to
    ``.claude/baton.yaml`` (or ``--path``)."""
    from agent_baton.core.config.manager import ManagerConfig

    target = (
        Path(args.target_path)
        if getattr(args, "target_path", None)
        else Path.cwd() / _MANAGER_DEFAULT_RELATIVE_PATH
    )
    if target.exists() and not args.force:
        print(
            f"error: {target} already exists. Pass --force to overwrite.",
            file=sys.stderr,
        )
        sys.exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_MANAGER_TEMPLATE, encoding="utf-8")
    # Sanity-check the template we just wrote actually validates -- if this
    # raises, the constant itself is broken (a packaging bug, not a user
    # error), so let it propagate rather than leaving a silently-invalid
    # file on disk without any signal.
    ManagerConfig.from_yaml(target)
    print(f"Wrote manager-mode starter config to {target}")
