"""``baton transfer`` -- transfer agents, knowledge, and references between projects.

Supports bidirectional transfer of distributable assets between
project directories. The --discover mode analyses agent performance
data to identify high-performing assets worth sharing.

Delegates to:
    agent_baton.core.distribute.experimental.transfer.ProjectTransfer
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.core.distribute.experimental.transfer import ProjectTransfer, TransferManifest


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "transfer", help="Transfer agents/knowledge/references between projects"
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--discover",
        action="store_true",
        help="Show what is available to transfer from this project",
    )
    mode.add_argument(
        "--export",
        metavar="TARGET",
        help="Export items to a target project root",
    )
    mode.add_argument(
        "--import",
        dest="import_from",
        metavar="SOURCE",
        help="Import items from another project root into this one",
    )
    p.add_argument(
        "--project",
        default=None,
        metavar="ROOT",
        help="Source project root (default: current directory)",
    )
    p.add_argument(
        "--agents",
        metavar="NAMES",
        help="Comma-separated agent names (without .md) or filenames",
    )
    p.add_argument(
        "--knowledge",
        metavar="PACKS",
        help="Comma-separated knowledge pack directory names",
    )
    p.add_argument(
        "--references",
        metavar="NAMES",
        help="Comma-separated reference filenames",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Transfer all discoverable items",
    )
    p.add_argument(
        "--min-score",
        dest="min_score",
        type=float,
        default=0.0,
        metavar="RATE",
        help="Minimum first-pass rate for --discover (0.0–1.0)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files at the destination",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    project_root = Path(args.project) if args.project else None
    transfer = ProjectTransfer(source_root=project_root)

    if args.discover:
        min_score: float = args.min_score or 0.0
        manifest = transfer.discover_transferable(min_score=min_score)
        print(manifest.to_markdown())
        return

    if args.export:
        target = Path(args.export)
        agent_names: list[str] = []
        if args.agents:
            raw = args.agents
            agent_names = [
                (a if a.endswith(".md") else f"{a}.md")
                for a in raw.split(",")
                if a.strip()
            ]
        knowledge_packs: list[str] = []
        if args.knowledge:
            knowledge_packs = [k.strip() for k in args.knowledge.split(",") if k.strip()]
        ref_names: list[str] = []
        if args.references:
            ref_names = [
                (r if r.endswith(".md") else f"{r}.md")
                for r in args.references.split(",")
                if r.strip()
            ]

        if args.all:
            manifest = transfer.discover_transferable()
        else:
            manifest = TransferManifest(
                agents=agent_names,
                knowledge_packs=knowledge_packs,
                references=ref_names,
            )

        counts = transfer.export_to(target, manifest, force=args.force)
        print(
            f"Exported to '{target}': "
            f"{counts['agents']} agents, "
            f"{counts['knowledge']} knowledge files, "
            f"{counts['references']} references"
        )
        return

    if args.import_from:
        source = Path(args.import_from)
        agent_names = []
        if args.agents:
            raw = args.agents
            agent_names = [
                (a if a.endswith(".md") else f"{a}.md")
                for a in raw.split(",")
                if a.strip()
            ]
        knowledge_packs = []
        if args.knowledge:
            knowledge_packs = [k.strip() for k in args.knowledge.split(",") if k.strip()]
        ref_names = []
        if args.references:
            ref_names = [
                (r if r.endswith(".md") else f"{r}.md")
                for r in args.references.split(",")
                if r.strip()
            ]

        other = ProjectTransfer(source_root=source)
        if args.all:
            manifest = other.discover_transferable()
        else:
            manifest = TransferManifest(
                agents=agent_names,
                knowledge_packs=knowledge_packs,
                references=ref_names,
            )

        counts = transfer.import_from(source, manifest, force=args.force)
        print(
            f"Imported from '{source}': "
            f"{counts['agents']} agents, "
            f"{counts['knowledge']} knowledge files, "
            f"{counts['references']} references"
        )
        return

    print("error: supply --discover, --export PATH, or --import PATH")
    sys.exit(1)
