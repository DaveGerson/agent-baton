"""``baton package`` -- create, inspect, or install agent-baton package archives.

Packages bundle agents, references, and knowledge packs into a
``.tar.gz`` archive that can be shared, published to a registry,
or installed into another project.

Modes:
    * ``--name NAME`` -- Build a new package from the project source.
    * ``--info ARCHIVE`` -- Show the manifest of an existing package.
    * ``--install ARCHIVE`` -- Install a package to user or project scope.

Delegates to:
    :class:`~agent_baton.core.distribute.sharing.PackageBuilder`
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.core.distribute.sharing import PackageBuilder


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("package", help="Create or install agent-baton packages")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--name",
        metavar="NAME",
        help="Create a package archive with this name",
    )
    mode.add_argument(
        "--info",
        metavar="ARCHIVE",
        help="Show manifest of an existing .tar.gz package",
    )
    mode.add_argument(
        "--install",
        metavar="ARCHIVE",
        help="Install an agent-baton package",
    )
    p.add_argument(
        "--version",
        default="1.0.0",
        help="Package version (default: 1.0.0)",
    )
    p.add_argument(
        "--description",
        default="",
        help="Package description",
    )
    p.add_argument(
        "--include-knowledge",
        dest="include_knowledge",
        action="store_true",
        help="Include knowledge packs in the package",
    )
    p.add_argument(
        "--no-agents",
        action="store_true",
        help="Exclude agents from the package",
    )
    p.add_argument(
        "--no-references",
        action="store_true",
        help="Exclude references from the package",
    )
    p.add_argument(
        "--output-dir",
        dest="output_dir",
        default=None,
        metavar="DIR",
        help="Directory to write the archive to (default: current directory)",
    )
    p.add_argument(
        "--scope",
        choices=["user", "project"],
        default="project",
        help="Install scope: user (~/.claude/) or project (.claude/) — used with --install",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files when installing",
    )
    p.add_argument(
        "--project",
        default=None,
        metavar="ROOT",
        help="Source project root (default: current directory)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    project_root = Path(args.project) if args.project else None
    builder = PackageBuilder(source_root=project_root)

    if args.name:
        output_dir = Path(args.output_dir) if args.output_dir else None
        archive = builder.build(
            name=args.name,
            version=args.version or "1.0.0",
            description=args.description or "",
            include_agents=not args.no_agents,
            include_references=not args.no_references,
            include_knowledge=args.include_knowledge,
            output_dir=output_dir,
        )
        print(f"Package created: {archive}")
        return

    if args.info:
        archive_path = Path(args.info)
        manifest = builder.read_manifest(archive_path)
        if manifest is None:
            print(f"error: could not read manifest from '{archive_path}'")
            sys.exit(1)
        print(f"Name:        {manifest.name}")
        print(f"Version:     {manifest.version}")
        print(f"Description: {manifest.description}")
        print(f"Created:     {manifest.created_at}")
        print(f"Baton ver:   {manifest.baton_version}")
        print(f"Agents ({len(manifest.agents)}):     {', '.join(manifest.agents) or '(none)'}")
        print(f"References ({len(manifest.references)}): {', '.join(manifest.references) or '(none)'}")
        print(f"Knowledge packs ({len(manifest.knowledge_packs)}): {', '.join(manifest.knowledge_packs) or '(none)'}")
        return

    if args.install:
        archive_path = Path(args.install)
        scope = args.scope or "project"
        counts = builder.install_package(archive_path, scope=scope, force=args.force)
        print(
            f"Installed to '{scope}': "
            f"{counts['agents']} agents, "
            f"{counts['references']} references, "
            f"{counts['knowledge']} knowledge files"
        )
        return

    print("error: supply --name NAME, --info ARCHIVE, or --install ARCHIVE")
    sys.exit(1)
