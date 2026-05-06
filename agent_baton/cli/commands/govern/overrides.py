"""``baton overrides`` — list / show / export governance override events.

G1.6 (bd-1a09).  Reads the ``governance_overrides`` SQL table written
by :class:`agent_baton.core.govern.override_log.OverrideLog`.

Subcommands
-----------
list      Recent overrides (newest first); ``--limit`` to bound the count.
show ID   Full single-record view including the justification text.
export    Bulk export ``--since DATE`` in ``--format csv|json``.

These commands are read-only.  Override rows are written by the CLI
helpers wired into ``baton execute gate --force`` etc. — there is no
manual ``record`` subcommand by design.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "overrides",
        help="Governance override + justification log (G1.6)",
    )
    sub = p.add_subparsers(dest="overrides_cmd", metavar="SUBCOMMAND")

    pls = sub.add_parser("list", help="List recent overrides (newest first)")
    pls.add_argument("--limit", type=int, default=20, metavar="N",
                     help="Maximum number of overrides to list (default 20)")

    psh = sub.add_parser("show", help="Show one override by ID")
    psh.add_argument("override_id", help="Override ID (UUID hex) to show")

    pex = sub.add_parser("export", help="Export overrides for compliance audit")
    pex.add_argument("--since", default=None, metavar="DATE",
                     help="ISO-8601 lower bound (inclusive); omitted = full log")
    pex.add_argument("--format", choices=["csv", "json"], default="json",
                     help="Output format (default: json)")
    return p


def handler(args: argparse.Namespace) -> None:
    cmd = getattr(args, "overrides_cmd", None) or "list"
    log = _open_log()

    if cmd == "list":
        rows = log.list_recent(limit=getattr(args, "limit", 20))
        _print_list(rows)
        return

    if cmd == "show":
        row = log.get(args.override_id)
        if row is None:
            print(f"No override found for id '{args.override_id}'.",
                  file=sys.stderr)
            sys.exit(1)
        print(json.dumps(row, indent=2, sort_keys=True))
        return

    if cmd == "export":
        rows = log.export_since(getattr(args, "since", None))
        if args.format == "csv":
            sys.stdout.write(_to_csv(rows))
        else:
            print(json.dumps(rows, indent=2, sort_keys=True))
        return


def _open_log():
    from agent_baton.cli._override_helper import _resolve_db_path
    from agent_baton.core.govern.override_log import OverrideLog

    return OverrideLog(db_path=_resolve_db_path())


def _print_list(rows: list[dict]) -> None:
    if not rows:
        print("No overrides recorded.")
        return
    print(f"Recent overrides ({len(rows)}):")
    for r in rows:
        print(
            f"  {r['created_at']}  {r['override_id'][:12]}  "
            f"{r['flag']:<14}  actor={r['actor']}  cmd={r['command']!r}"
        )


def _to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    fieldnames = [
        "override_id", "created_at", "actor", "flag",
        "command", "args_json", "justification", "chain_hash",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in fieldnames})
    return buf.getvalue()
