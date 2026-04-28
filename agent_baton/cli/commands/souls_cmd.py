"""CLI command: ``baton souls`` — manage persistent agent soul identities.

Wave 6.1 Part B (bd-d975).

Subcommands
-----------
mint    <role> <domain>               Operator-issued soul for a role+domain.
list    [--role ROLE]                  List active souls.
show    <soul_id>                      Show soul metadata + expertise.
retire  <soul_id> [--successor SUCC]  Mark soul retired (soft deprecation).
revoke  <soul_id> --confirm            Revocation flag (compromised key).

Souls live in ``~/.baton/central.db`` (cross-project).  Private keys at
``~/.config/baton/souls/<soul_id>.ed25519`` (mode 0600).

All subcommands degrade gracefully when central.db is unavailable.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_registry():
    """Construct a SoulRegistry using the default central.db path."""
    from agent_baton.core.engine.soul_registry import SoulRegistry
    return SoulRegistry()


def _print_soul(soul, *, verbose: bool = False) -> None:
    """Pretty-print a soul row."""
    status = "revoked" if soul.is_revoked else ("retired" if soul.retired_at else "active")
    privkey = str(soul.privkey_path) if soul.privkey_path else "(none on this machine)"
    local_key_exists = soul.privkey_path is not None and soul.privkey_path.exists()
    print(f"  soul_id      : {soul.soul_id}")
    print(f"  role         : {soul.role}")
    print(f"  status       : {status}")
    print(f"  created_at   : {soul.created_at}")
    if soul.retired_at:
        print(f"  retired_at   : {soul.retired_at}")
    if soul.parent_soul_id:
        print(f"  parent       : {soul.parent_soul_id}")
    if soul.origin_project:
        print(f"  origin       : {soul.origin_project}")
    print(f"  pubkey       : {soul.pubkey.hex()[:16]}...")
    print(f"  privkey_path : {privkey}  [local={'yes' if local_key_exists else 'NO'}]")
    if soul.notes:
        print(f"  notes        : {soul.notes}")
    if verbose:
        import base64
        print(f"  pubkey_full  : {base64.b64encode(soul.pubkey).decode()}")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def _cmd_mint(args: argparse.Namespace) -> None:
    """Mint a new soul for *role* + *domain*."""
    registry = _get_registry()
    project = getattr(args, "project", "") or ""
    soul = registry.mint(role=args.role, domain=args.domain, project=project)
    print(f"Minted soul: {soul.soul_id}")
    _print_soul(soul)
    print()
    print(
        f"BEAD_DISCOVERY: soul.minted soul_id={soul.soul_id} "
        f"role={args.role} domain={args.domain}"
    )


def _cmd_list(args: argparse.Namespace) -> None:
    """List active souls, optionally filtered by role."""
    registry = _get_registry()
    role_filter: str | None = getattr(args, "role", None)

    try:
        conn = registry._conn()
        if role_filter:
            rows = conn.execute(
                "SELECT * FROM agent_souls WHERE role = ? ORDER BY created_at",
                (role_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM agent_souls ORDER BY role, created_at"
            ).fetchall()
        conn.close()
    except Exception as exc:
        print(f"Error reading souls from central.db: {exc}", file=sys.stderr)
        return

    if not rows:
        print("No souls found." + (f" (role={role_filter})" if role_filter else ""))
        return

    print(f"{'SOUL_ID':<35} {'ROLE':<20} {'STATUS':<10} {'CREATED_AT'}")
    print("-" * 90)
    for row in rows:
        soul = registry._row_to_soul(row)
        status = "revoked" if soul.is_revoked else ("retired" if soul.retired_at else "active")
        print(f"{soul.soul_id:<35} {soul.role:<20} {status:<10} {soul.created_at}")


def _cmd_show(args: argparse.Namespace) -> None:
    """Show full details for a soul including expertise rows."""
    registry = _get_registry()
    soul = registry.get(args.soul_id)
    if soul is None:
        print(f"Soul not found: {args.soul_id}", file=sys.stderr)
        sys.exit(1)

    print(f"\nSoul: {soul.soul_id}")
    print("=" * 60)
    _print_soul(soul, verbose=True)

    expertise = registry.get_expertise(soul.soul_id)
    if expertise:
        print(f"\nExpertise ({len(expertise)} rows):")
        print(f"  {'SCOPE':<10} {'REF':<50} {'WEIGHT':<8} LAST_TOUCHED")
        print("  " + "-" * 85)
        for row in expertise[:20]:  # show top 20
            ref_short = row["ref"][-47:] if len(row["ref"]) > 47 else row["ref"]
            print(
                f"  {row['scope']:<10} {ref_short:<50} "
                f"{row['weight']:.4f}   {row['last_touched_at']}"
            )
        if len(expertise) > 20:
            print(f"  ... and {len(expertise) - 20} more rows")
    else:
        print("\nExpertise: (none — soul has not been dispatched yet)")


def _cmd_retire(args: argparse.Namespace) -> None:
    """Retire a soul, optionally recording a successor."""
    registry = _get_registry()
    soul = registry.get(args.soul_id)
    if soul is None:
        print(f"Soul not found: {args.soul_id}", file=sys.stderr)
        sys.exit(1)
    if soul.retired_at:
        print(f"Soul {args.soul_id} is already retired (at {soul.retired_at}).")
        return

    successor: str | None = getattr(args, "successor", None)
    registry.retire(args.soul_id, successor_id=successor)
    print(f"Retired soul: {args.soul_id}")
    if successor:
        print(f"  Successor recorded: {successor}")
    print(
        f"BEAD_DECISION: soul.retired soul_id={args.soul_id} successor={successor or 'none'}"
    )


def _cmd_revoke(args: argparse.Namespace) -> None:
    """Revoke a soul (compromised key).  Requires ``--confirm``."""
    if not getattr(args, "confirm", False):
        print(
            "ERROR: revoke is a destructive operation.  "
            "Pass --confirm to proceed.",
            file=sys.stderr,
        )
        sys.exit(1)

    registry = _get_registry()
    soul = registry.get(args.soul_id)
    if soul is None:
        print(f"Soul not found: {args.soul_id}", file=sys.stderr)
        sys.exit(1)
    if soul.is_revoked:
        print(f"Soul {args.soul_id} is already revoked.")
        return

    registry.revoke(args.soul_id)
    print(f"Revoked soul: {args.soul_id}")
    print(
        "BEAD_WARNING: soul-revoked all beads signed by this soul will "
        "produce signature-invalid warnings on read."
    )
    print(
        f"BEAD_DECISION: soul.revoked soul_id={args.soul_id} "
        f"NOTE: delete private key manually at "
        f"{registry._privkey_path(args.soul_id)}"
    )


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------

_SUBCOMMAND_TABLE = {
    "mint": _cmd_mint,
    "list": _cmd_list,
    "show": _cmd_show,
    "retire": _cmd_retire,
    "revoke": _cmd_revoke,
}


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    """Register ``baton souls`` and its subcommands."""
    p = subparsers.add_parser(
        "souls",
        help="Manage persistent agent soul identities (Wave 6.1 Part B, bd-d975).",
        description=(
            "Souls are cross-project cryptographic identities for agents.  "
            "They live in ~/.baton/central.db and enable expertise-based routing."
        ),
    )
    sub = p.add_subparsers(dest="souls_subcmd", metavar="SUBCMD")

    # -- mint --
    p_mint = sub.add_parser("mint", help="Mint a new operator-issued soul.")
    p_mint.add_argument("role", help="Agent role, e.g. 'code-reviewer'.")
    p_mint.add_argument("domain", help="Domain token, e.g. 'auth'.")
    p_mint.add_argument(
        "--project",
        default="",
        help="Origin project path (informational, defaults to cwd).",
    )

    # -- list --
    p_list = sub.add_parser("list", help="List active souls.")
    p_list.add_argument("--role", default=None, help="Filter by role.")

    # -- show --
    p_show = sub.add_parser("show", help="Show soul metadata + expertise.")
    p_show.add_argument("soul_id", help="The soul_id to inspect.")

    # -- retire --
    p_retire = sub.add_parser("retire", help="Mark a soul as retired.")
    p_retire.add_argument("soul_id", help="The soul_id to retire.")
    p_retire.add_argument(
        "--successor",
        default=None,
        metavar="SUCC",
        help="Optional successor soul_id.",
    )

    # -- revoke --
    p_revoke = sub.add_parser(
        "revoke",
        help="Revoke a soul (compromised key).  Requires --confirm.",
    )
    p_revoke.add_argument("soul_id", help="The soul_id to revoke.")
    p_revoke.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm the destructive revocation operation.",
    )

    return p


def handler(args: argparse.Namespace) -> None:
    """Dispatch to the appropriate ``baton souls`` subcommand."""
    subcmd = getattr(args, "souls_subcmd", None)
    if subcmd is None:
        print("Usage: baton souls <mint|list|show|retire|revoke>", file=sys.stderr)
        sys.exit(1)
    fn = _SUBCOMMAND_TABLE.get(subcmd)
    if fn is None:
        print(f"Unknown souls subcommand: {subcmd}", file=sys.stderr)
        sys.exit(1)
    fn(args)
