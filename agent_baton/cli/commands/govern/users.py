"""``baton govern users`` -- list, show, and assign engineering roles.

Subcommands:

* ``list``                       -- compact table of every known PMO user
* ``show <user>``                -- full identity record
* ``assign-role <user> <role>``  -- set the user's :class:`HumanRole`

The role taxonomy is defined in
:mod:`agent_baton.models.identity` (H3.1 / bd-0dea).  Assignments are
purely informational at this point: no execution path enforces gating
based on the assigned role.  G1.4 (Separation of Duties) will be the
first consumer.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.core.storage.user_store import UserStore
from agent_baton.models.identity import HumanRole, UserIdentity


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "users",
        help="List, show, or assign engineering roles to PMO users",
    )
    p.add_argument(
        "--db-path",
        metavar="PATH",
        default=None,
        help=(
            "Override path to central.db.  Defaults to ~/.baton/central.db. "
            "Mostly useful for tests and isolated environments."
        ),
    )

    sub = p.add_subparsers(dest="users_action", metavar="ACTION")

    sp_list = sub.add_parser("list", help="Show every known user as a table")
    sp_list.set_defaults(_action="list")

    sp_show = sub.add_parser("show", help="Print the full identity record for one user")
    sp_show.add_argument("user", metavar="USER_ID", help="User identifier")
    sp_show.set_defaults(_action="show")

    sp_assign = sub.add_parser(
        "assign-role",
        help="Set a user's engineering role (creates the row if missing)",
    )
    sp_assign.add_argument("user", metavar="USER_ID", help="User identifier")
    sp_assign.add_argument(
        "role",
        metavar="ROLE",
        help=(
            "One of: " + _valid_roles_help()
        ),
    )
    sp_assign.set_defaults(_action="assign-role")

    return p


def _valid_roles_help() -> str:
    """Return a human-readable list of the role enum values."""
    return ", ".join(
        m.value if m is not HumanRole.UNASSIGNED else "unassigned"
        for m in HumanRole
    )


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    db_path: Path | None = Path(args.db_path) if args.db_path else None
    action = getattr(args, "_action", None)

    if action is None:
        # Default to list when the operator runs ``baton users`` with no
        # subcommand -- mirrors the convention used by ``baton beads``.
        _do_list(db_path)
        return

    if action == "list":
        _do_list(db_path)
    elif action == "show":
        _do_show(db_path, args.user)
    elif action == "assign-role":
        _do_assign_role(db_path, args.user, args.role)
    else:  # pragma: no cover - argparse should prevent this
        print(f"Unknown action: {action}", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------


def _do_list(db_path: Path | None) -> None:
    """Print every user as a compact ``user_id | role | human_role | name`` table."""
    store = UserStore(db_path=db_path)
    users = store.list_all()

    if not users:
        print("No users registered.")
        return

    headers = ("USER_ID", "PMO_ROLE", "HUMAN_ROLE", "DISPLAY_NAME")
    rows = [
        (
            u.user_id,
            u.role,
            u.human_role.value or "(unassigned)",
            u.display_name,
        )
        for u in users
    ]
    _print_table(headers, rows)


def _do_show(db_path: Path | None, user_id: str) -> None:
    """Print the full identity record for *user_id*."""
    store = UserStore(db_path=db_path)
    identity = store.get(user_id)
    if identity is None:
        print(f"User {user_id!r} not found.", file=sys.stderr)
        sys.exit(1)
    _print_identity(identity)


def _do_assign_role(db_path: Path | None, user_id: str, role_str: str) -> None:
    """Assign *role_str* to *user_id* and print the resulting record.

    Exits with code 2 and a helpful message when *role_str* is not a
    valid :class:`HumanRole` value.
    """
    try:
        role = HumanRole.parse(role_str)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(f"Valid roles: {_valid_roles_help()}", file=sys.stderr)
        sys.exit(2)

    store = UserStore(db_path=db_path)
    identity = store.assign_role(user_id, role)
    label = role.value or "unassigned"
    print(f"User {user_id!r} -> human_role={label}")
    _print_identity(identity)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _print_identity(identity: UserIdentity) -> None:
    """Print the full identity record in a stable ``key: value`` layout."""
    label = identity.human_role.value or "(unassigned)"
    print(f"user_id      : {identity.user_id}")
    print(f"display_name : {identity.display_name}")
    print(f"email        : {identity.email}")
    print(f"role         : {identity.role}")
    print(f"human_role   : {label}")
    print(f"created_at   : {identity.created_at}")


def _print_table(
    headers: tuple[str, ...],
    rows: list[tuple[str, ...]],
) -> None:
    """Render *headers* + *rows* as an aligned plain-text table."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * w for w in widths)))
    for row in rows:
        print(fmt.format(*row))
