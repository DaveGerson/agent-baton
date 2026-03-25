"""baton uninstall — remove agent-baton files from project or user scope."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "uninstall",
        help="Remove agent-baton files (agents, references, team-context)",
    )
    p.add_argument(
        "--scope",
        required=True,
        choices=["project", "user"],
        help="Scope to uninstall from: project (.claude/) or user (~/.claude/)",
    )
    p.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    p.add_argument(
        "--keep-data",
        action="store_true",
        help="Keep execution data (team-context/) — only remove agents and references",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    if args.scope == "user":
        base = Path.home() / ".claude"
    else:
        base = Path(".claude")

    if not base.exists():
        print(f"Nothing to uninstall — {base} does not exist.")
        return

    # Determine what will be removed
    targets = []
    agents_dir = base / "agents"
    refs_dir = base / "references"
    team_ctx = base / "team-context"
    settings = base / "settings.json"

    if agents_dir.exists():
        agent_count = len(list(agents_dir.glob("*.md")))
        targets.append(("agents/", f"{agent_count} agent definitions", agents_dir))
    if refs_dir.exists():
        ref_count = len(list(refs_dir.glob("*.md")))
        targets.append(("references/", f"{ref_count} reference documents", refs_dir))
    if not args.keep_data and team_ctx.exists():
        targets.append(("team-context/", "execution state and logs", team_ctx))

    if not targets:
        print("Nothing to uninstall — no agent-baton directories found.")
        return

    # Show what will be removed
    print(f"Uninstalling from: {base.resolve()}")
    print()
    for name, desc, _ in targets:
        print(f"  Remove: {name} ({desc})")
    if settings.exists():
        print(f"  Note:   settings.json will NOT be removed (may contain user hooks/MCP config)")

    # Also mention CLAUDE.md
    claude_md = Path("CLAUDE.md") if args.scope == "project" else base / "CLAUDE.md"
    if claude_md.exists():
        print(f"  Note:   {claude_md} will NOT be removed (may contain user customizations)")
    print()

    if not args.yes:
        response = input("Proceed? [y/N] ").strip().lower()
        if response not in ("y", "yes"):
            print("Cancelled.")
            return

    # Remove
    for name, _, path in targets:
        try:
            shutil.rmtree(path)
            print(f"  Removed: {name}")
        except OSError as exc:
            print(f"  Failed to remove {name}: {exc}", file=sys.stderr)

    print()
    print("Uninstall complete.")
    if args.keep_data:
        print("  Execution data preserved in team-context/")
    print("  To reinstall: scripts/install.sh (or install.ps1)")
