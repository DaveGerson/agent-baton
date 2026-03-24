"""baton pull — install a package from a local registry directory."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.core.distribute.registry_client import RegistryClient


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "pull",
        help="Install a package from a local registry directory",
    )

    p.add_argument(
        "name",
        nargs="?",
        metavar="NAME",
        help="Name of the package to install",
    )
    p.add_argument(
        "--registry",
        metavar="PATH",
        dest="registry",
        required=True,
        help="Path to the local registry directory",
    )
    p.add_argument(
        "--version",
        metavar="VERSION",
        default=None,
        help="Specific version to install (default: latest)",
    )
    p.add_argument(
        "--scope",
        choices=["user", "project"],
        default="project",
        help="Install scope: project (.claude/) or user (~/.claude/) — default: project",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files",
    )
    p.add_argument(
        "--list",
        action="store_true",
        dest="list_packages",
        help="List all available packages in the registry",
    )
    p.add_argument(
        "--search",
        metavar="QUERY",
        dest="search_query",
        default=None,
        help="Search packages by name substring",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    client = RegistryClient()
    registry_path = Path(args.registry)

    if not registry_path.is_dir():
        print(f"error: registry directory not found: {registry_path}")
        sys.exit(1)

    # --- baton pull --list --registry PATH -----------------------------------
    if args.list_packages:
        entries = client.list_packages(registry_path)
        if not entries:
            print("Registry is empty.")
            return
        print(f"{'Name':<30} {'Version':<12} {'Agents':>6} {'Refs':>5}  Description")
        print("-" * 72)
        for entry in sorted(entries, key=lambda e: e.name):
            desc = entry.description[:28] + ".." if len(entry.description) > 30 else entry.description
            print(
                f"{entry.name:<30} {entry.version:<12} "
                f"{entry.agent_count:>6} {entry.reference_count:>5}  {desc}"
            )
        return

    # --- baton pull --search QUERY --registry PATH ---------------------------
    if args.search_query is not None:
        results = client.search(registry_path, args.search_query)
        if not results:
            print(f"No packages found matching '{args.search_query}'.")
            return
        print(f"Search results for '{args.search_query}':")
        for entry in results:
            print(f"  {entry.name}  ({entry.version})  — {entry.description}")
        return

    # --- baton pull NAME --registry PATH [--version V] -----------------------
    if not args.name:
        print("error: supply a package NAME, or use --list / --search")
        sys.exit(1)

    try:
        counts = client.pull(
            registry_path=registry_path,
            package_name=args.name,
            version=args.version,
            install_scope=args.scope,
            force=args.force,
        )
    except KeyError as exc:
        print(f"error: {exc}")
        sys.exit(1)
    except ValueError as exc:
        print(f"error: {exc}")
        sys.exit(1)

    version_label = args.version or "latest"
    print(
        f"Installed '{args.name}' ({version_label}) to '{args.scope}': "
        f"{counts['agents']} agents, "
        f"{counts['references']} references, "
        f"{counts['knowledge']} knowledge files"
    )
