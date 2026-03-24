"""baton incident — manage incident response workflows."""
from __future__ import annotations

import argparse

from agent_baton.core.distribute.incident import IncidentManager


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("incident", help="Manage incident response workflows")
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--templates", action="store_true", help="Show all built-in incident templates",
    )
    group.add_argument(
        "--show", metavar="ID", help="Show a specific incident document",
    )
    p.add_argument(
        "--create", metavar="ID", default=None,
        help="Create an incident document with the given ID",
    )
    p.add_argument(
        "--severity", metavar="LEVEL", default=None,
        help="Severity level for --create (P1, P2, P3, P4)",
    )
    p.add_argument(
        "--desc", metavar="TEXT", default=None,
        help="Description for --create",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    manager = IncidentManager()

    if args.templates:
        for sev in ("P1", "P2", "P3", "P4"):
            tmpl = manager.get_template(sev)
            print(f"{sev}: {tmpl.name} ({len(tmpl.phases)} phases)")
            for i, phase in enumerate(tmpl.phases, start=1):
                print(f"  Phase {i}: {phase.name}")
        return

    if args.create and args.severity and args.desc:
        path = manager.create_incident(args.create, args.severity, args.desc)
        print(f"Incident created: {path}")
        return

    if args.show:
        content = manager.load_incident(args.show)
        if content is None:
            print(f"No incident found with ID '{args.show}'.")
            return
        print(content)
        return

    # Default: list incidents
    incidents = manager.list_incidents()
    if not incidents:
        print("No incidents found.")
        return
    print(f"Incidents ({len(incidents)}):")
    for path in incidents:
        print(f"  {path.stem}")
