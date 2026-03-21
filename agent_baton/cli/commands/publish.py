"""baton publish — publish a package archive to a local registry directory."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.core.distribute.registry_client import RegistryClient


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "publish",
        help="Publish a package archive to a local registry, or initialise a new registry",
    )

    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "archive",
        nargs="?",
        metavar="ARCHIVE",
        help="Path to the .tar.gz archive to publish",
    )
    mode.add_argument(
        "--init",
        metavar="PATH",
        dest="init_path",
        help="Initialise a new empty registry at PATH",
    )

    p.add_argument(
        "--registry",
        metavar="PATH",
        dest="registry",
        help="Path to the local registry directory (required when publishing)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    client = RegistryClient()

    # --- baton publish --init PATH -------------------------------------------
    if args.init_path:
        registry_path = Path(args.init_path)
        client.init_registry(registry_path)
        print(f"Registry initialised at: {registry_path}")
        return

    # --- baton publish ARCHIVE --registry PATH --------------------------------
    if not args.archive:
        print("error: supply an ARCHIVE path or use --init PATH")
        sys.exit(1)

    if not args.registry:
        print("error: --registry PATH is required when publishing an archive")
        sys.exit(1)

    archive_path = Path(args.archive)
    registry_path = Path(args.registry)

    if not archive_path.is_file():
        print(f"error: archive not found: {archive_path}")
        sys.exit(1)

    if not registry_path.is_dir():
        print(f"error: registry directory not found: {registry_path}")
        print("       Run 'baton publish --init PATH' to create it first.")
        sys.exit(1)

    try:
        entry = client.publish(archive_path, registry_path)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"error: {exc}")
        sys.exit(1)

    print(f"Published: {entry.name} @ {entry.version}")
    print(f"  Registry: {registry_path}")
    print(f"  Path:     {entry.path}")
    print(f"  Agents:   {entry.agent_count}")
    print(f"  Refs:     {entry.reference_count}")
