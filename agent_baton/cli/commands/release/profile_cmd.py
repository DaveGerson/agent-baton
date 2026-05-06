"""``baton release profile`` — deployment profile management (R3.8).

Subcommands::

    baton release profile list
    baton release profile create --name X --env staging \\
        --gate test --gate lint --slo dispatch_success_rate \\
        --allow-risk LOW,MEDIUM [--description "..."]
    baton release profile attach <release_id> <profile_id>
    baton release profile check <release_id>

Uses the cooperative parser pattern: the ``release`` parent parser is
shared with other modules (e.g. readiness_cmd.py) via
``subparsers.choices.get("release")``.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


# ---------------------------------------------------------------------------
# DB discovery
# ---------------------------------------------------------------------------

def _resolve_db_path() -> Path | None:
    cwd = Path.cwd()
    for ancestor in [cwd, *cwd.parents]:
        candidate = ancestor / ".claude" / "team-context" / "baton.db"
        if candidate.exists():
            return candidate
    global_path = Path.home() / ".baton" / "baton.db"
    if global_path.exists():
        return global_path
    return None


def _open_conn(db_path: Path) -> "sqlite3.Connection":  # type: ignore[name-defined]  # noqa: F821
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Cooperative parser helpers
# ---------------------------------------------------------------------------

def _get_or_create_release_parser(
    subparsers: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:  # type: ignore[type-arg]
    """Return (release_parser, release_sub) — reuse existing parser if present."""
    existing = subparsers.choices.get("release") if subparsers.choices else None
    if existing is not None:
        sub = getattr(existing, "_baton_release_sub", None)
        if sub is None:
            for action in getattr(existing, "_actions", ()):
                if isinstance(action, argparse._SubParsersAction):  # type: ignore[attr-defined]
                    sub = action
                    break
            if sub is None:
                sub = existing.add_subparsers(dest="release_subcommand", metavar="SUBCOMMAND")
            existing._baton_release_sub = sub  # type: ignore[attr-defined]
        return existing, sub

    p = subparsers.add_parser(
        "release",
        help="Release management — profiles, readiness, notes",
    )
    sub = p.add_subparsers(dest="release_subcommand", metavar="SUBCOMMAND")
    p._baton_release_sub = sub  # type: ignore[attr-defined]
    return p, sub


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    release_p, release_sub = _get_or_create_release_parser(subparsers)

    profile_p = release_sub.add_parser(
        "profile",
        help="Manage deployment profiles (R3.8)",
    )
    profile_sub = profile_p.add_subparsers(dest="profile_subcommand", metavar="ACTION")

    # -- list ----------------------------------------------------------------
    profile_sub.add_parser("list", help="List all deployment profiles")

    # -- create --------------------------------------------------------------
    create_p = profile_sub.add_parser("create", help="Create a new deployment profile")
    create_p.add_argument("--name", required=True, help="Human-readable profile name")
    create_p.add_argument("--env", required=True, dest="environment", help="Target environment (dev/staging/prod)")
    create_p.add_argument(
        "--gate",
        dest="gates",
        action="append",
        default=[],
        metavar="GATE_TYPE",
        help="Required gate type (repeatable)",
    )
    create_p.add_argument(
        "--slo",
        dest="slos",
        action="append",
        default=[],
        metavar="SLO_NAME",
        help="Target SLO name (repeatable)",
    )
    create_p.add_argument(
        "--allow-risk",
        dest="allow_risk",
        default="LOW,MEDIUM",
        metavar="LEVELS",
        help="Comma-separated allowed risk levels (default: LOW,MEDIUM)",
    )
    create_p.add_argument("--description", default="", help="Optional description")
    create_p.add_argument("--db", type=Path, default=None, metavar="PATH", help="Path to baton.db")

    # -- attach --------------------------------------------------------------
    attach_p = profile_sub.add_parser("attach", help="Attach a profile to a release")
    attach_p.add_argument("release_id", help="Release identifier")
    attach_p.add_argument("profile_id", help="Profile identifier to attach")
    attach_p.add_argument("--db", type=Path, default=None, metavar="PATH", help="Path to baton.db")

    # -- check ---------------------------------------------------------------
    check_p = profile_sub.add_parser("check", help="Check a release against its profile")
    check_p.add_argument("release_id", help="Release identifier to check")
    check_p.add_argument("--db", type=Path, default=None, metavar="PATH", help="Path to baton.db")

    release_p.set_defaults(_profile_handler=handler)
    return release_p


def handler(args: argparse.Namespace) -> None:
    release_subcmd = getattr(args, "release_subcommand", None)
    if release_subcmd != "profile":
        # Another handler owns non-profile subcommands (e.g. readiness_cmd).
        _delegate_non_profile(args)
        return
    _dispatch_profile(args)


def _delegate_non_profile(args: argparse.Namespace) -> None:
    """Pass through to any previously registered release handler."""
    alt = getattr(args, "_profile_handler", None)
    if alt and alt is not handler:
        alt(args)
    else:
        print("Usage: baton release profile ACTION ...")
        print("Run 'baton release profile --help' for available actions.")


def _dispatch_profile(args: argparse.Namespace) -> None:
    action = getattr(args, "profile_subcommand", None)
    if action is None:
        print("Usage: baton release profile ACTION ...")
        print("Run 'baton release profile --help' for available actions.")
        return
    dispatch = {
        "list": _cmd_list,
        "create": _cmd_create,
        "attach": _cmd_attach,
        "check": _cmd_check,
    }
    fn = dispatch.get(action)
    if fn is None:
        print(f"error: unknown profile action: {action}", file=sys.stderr)
        sys.exit(1)
    fn(args)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------

def _cmd_list(args: argparse.Namespace) -> None:
    from agent_baton.core.storage.deployment_profile_store import DeploymentProfileStore

    db_path = getattr(args, "db", None) or _resolve_db_path()
    if db_path is None:
        print("error: no baton.db found. Pass --db or run from a project directory.", file=sys.stderr)
        sys.exit(1)

    conn = _open_conn(db_path)
    store = DeploymentProfileStore(conn)
    profiles = store.list_all()

    if not profiles:
        print("No deployment profiles found.")
        return

    col_w = [10, 16, 10, 14, 12, 30]
    header = f"{'PROFILE_ID':<{col_w[0]}}  {'NAME':<{col_w[1]}}  {'ENV':<{col_w[2]}}  {'GATES':<{col_w[3]}}  {'RISK':<{col_w[4]}}  DESCRIPTION"
    print(header)
    print("-" * (sum(col_w) + len(col_w) * 2))
    for p in profiles:
        gates = ",".join(p.required_gates) or "-"
        risk = ",".join(p.allowed_risk_levels) or "-"
        print(
            f"{p.profile_id:<{col_w[0]}}  {p.name:<{col_w[1]}}  {p.environment:<{col_w[2]}}  "
            f"{gates:<{col_w[3]}}  {risk:<{col_w[4]}}  {p.description}"
        )


def _cmd_create(args: argparse.Namespace) -> None:
    from agent_baton.core.storage.deployment_profile_store import DeploymentProfileStore
    from agent_baton.models.deployment_profile import DeploymentProfile

    db_path = getattr(args, "db", None) or _resolve_db_path()
    if db_path is None:
        print("error: no baton.db found. Pass --db or run from a project directory.", file=sys.stderr)
        sys.exit(1)

    allow_risk = [r.strip().upper() for r in args.allow_risk.split(",") if r.strip()]
    profile_id = f"dp-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    profile = DeploymentProfile(
        profile_id=profile_id,
        name=args.name,
        environment=args.environment,
        required_gates=args.gates,
        target_slos=args.slos,
        allowed_risk_levels=allow_risk,
        description=args.description,
        created_at=now,
    )

    conn = _open_conn(db_path)
    store = DeploymentProfileStore(conn)
    store.save(profile)
    print(f"Created deployment profile: {profile_id}")
    print(f"  name={profile.name}  env={profile.environment}  gates={profile.required_gates}")


def _cmd_attach(args: argparse.Namespace) -> None:
    from agent_baton.core.storage.deployment_profile_store import DeploymentProfileStore

    db_path = getattr(args, "db", None) or _resolve_db_path()
    if db_path is None:
        print("error: no baton.db found. Pass --db or run from a project directory.", file=sys.stderr)
        sys.exit(1)

    conn = _open_conn(db_path)
    store = DeploymentProfileStore(conn)

    if store.get(args.profile_id) is None:
        print(f"error: profile '{args.profile_id}' not found.", file=sys.stderr)
        sys.exit(1)

    store.attach_to_release(args.release_id, args.profile_id)
    print(f"Attached profile '{args.profile_id}' to release '{args.release_id}'.")


def _cmd_check(args: argparse.Namespace) -> None:
    from agent_baton.core.storage.deployment_profile_store import DeploymentProfileStore
    from agent_baton.core.release.profile_checker import ProfileChecker

    db_path = getattr(args, "db", None) or _resolve_db_path()
    if db_path is None:
        print("error: no baton.db found. Pass --db or run from a project directory.", file=sys.stderr)
        sys.exit(1)

    conn = _open_conn(db_path)
    store = DeploymentProfileStore(conn)
    checker = ProfileChecker(store)
    warnings = checker.check(args.release_id)

    total = sum(len(v) for v in warnings.values())
    print(f"# Profile check — release: {args.release_id}\n")

    if total == 0:
        print("All profile requirements satisfied.")
        return

    if warnings["missing_gates"]:
        print("## Missing gates")
        for g in warnings["missing_gates"]:
            print(f"  - {g}")
        print()

    if warnings["untracked_slos"]:
        print("## Untracked SLOs")
        for s in warnings["untracked_slos"]:
            print(f"  - {s}")
        print()

    if warnings["risk_violations"]:
        print("## Risk violations")
        for plan_id in warnings["risk_violations"]:
            print(f"  - plan {plan_id}")
        print()

    print(f"{total} warning(s) found.")
