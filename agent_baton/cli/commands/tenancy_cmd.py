"""``baton tenancy`` — Tenancy & cost attribution hierarchy CLI (F0.2).

Subcommands
-----------
show            Show resolved tenancy context (identity.yaml + env)
set-team        Write team_id to ~/.baton/identity.yaml
set-org         Write org_id to ~/.baton/identity.yaml
migrate-existing  Backfill tenancy columns on pre-v16 usage rows
"""
from __future__ import annotations

import argparse
import json


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "tenancy",
        help="Manage tenancy & cost attribution hierarchy (F0.2)",
    )
    p.add_argument(
        "--db", metavar="PATH", default=None,
        help="Path to central.db (default: ~/.baton/central.db)",
    )
    sub = p.add_subparsers(dest="tenancy_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # show
    ps = sub.add_parser("show", help="Show resolved tenancy context")
    ps.add_argument("--json", dest="output_json", action="store_true")

    # set-team
    pt = sub.add_parser("set-team", help="Set team_id in identity.yaml")
    pt.add_argument("team_id", help="Team identifier")
    pt.add_argument("--org", default=None, help="Also set org_id")

    # set-org
    po = sub.add_parser("set-org", help="Set org_id in identity.yaml")
    po.add_argument("org_id", help="Org identifier")

    # migrate-existing
    pm = sub.add_parser(
        "migrate-existing",
        help="Backfill tenancy columns on pre-v16 usage rows",
    )
    pm.add_argument("--org", default="default", help="Org ID to apply")
    pm.add_argument("--team", default="default", help="Team ID to apply")

    return p


def handler(args: argparse.Namespace) -> None:
    from pathlib import Path
    from agent_baton.models.tenancy import (
        TenancyStore,
        resolve_tenancy_context,
    )

    db = getattr(args, "db", None)
    store = TenancyStore(db_path=Path(db) if db else None)
    cmd = args.tenancy_cmd

    if cmd == "show":
        ctx = resolve_tenancy_context()
        if getattr(args, "output_json", False):
            print(json.dumps(ctx.to_dict(), indent=2))
            return
        print(f"Tenancy context:")
        print(f"  org_id:      {ctx.org_id}")
        print(f"  team_id:     {ctx.team_id}")
        print(f"  user_id:     {ctx.user_id}")
        print(f"  cost_center: {ctx.cost_center or '(not set)'}")

    elif cmd == "set-team":
        path = TenancyStore.write_identity(
            team_id=args.team_id,
            org_id=getattr(args, "org", None),
        )
        print(f"Set team_id={args.team_id} in {path}")
        # Ensure the team exists in the DB
        store.create_team(args.team_id)

    elif cmd == "set-org":
        path = TenancyStore.write_identity(org_id=args.org_id)
        print(f"Set org_id={args.org_id} in {path}")
        store.create_org(args.org_id)

    elif cmd == "migrate-existing":
        updated = store.migrate_existing(org_id=args.org, team_id=args.team)
        print(f"Updated {updated} usage_records rows with org_id={args.org}, team_id={args.team}")
