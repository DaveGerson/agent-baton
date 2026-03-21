"""baton install — install agents and references."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def _copy_file(src: Path, dst: Path, *, force: bool) -> bool:
    """Copy src to dst.  Returns True if the file was copied, False if skipped."""
    if dst.exists() and not force:
        print(f"  skip: '{dst}' exists (use --force to overwrite)")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _merge_settings(src_path: Path, dst_path: Path) -> bool:
    """Merge agent-baton hooks into existing settings.json, preserving user keys.

    Strategy: the source provides 'hooks'. The destination may have 'hooks'
    plus user-specific keys (permissions, mcpServers, env, etc.).
    We merge hook events additively — baton hooks are added/updated,
    user hooks for other events are preserved.
    """
    import json

    try:
        src_data = json.loads(src_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False

    if dst_path.exists():
        try:
            dst_data = json.loads(dst_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            dst_data = {}
    else:
        dst_data = {}

    # Merge hooks: for each hook event in source, replace in destination
    src_hooks = src_data.get("hooks", {})
    if src_hooks:
        dst_hooks = dst_data.setdefault("hooks", {})
        for event, entries in src_hooks.items():
            dst_hooks[event] = entries  # replace per-event (baton owns these)
        print(f"  merge: settings.json hooks ({len(src_hooks)} events)")

    # All other top-level keys in destination are preserved untouched
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(
        json.dumps(dst_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("install", help="Install agents and references")
    p.add_argument(
        "--scope",
        required=True,
        choices=["user", "project"],
        help="Install to user (~/.claude/) or project (.claude/) scope",
    )
    p.add_argument(
        "--source",
        default=".",
        help="Path to the agent-baton repo root (default: current directory)",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Overwrite ALL existing files without prompting",
    )
    p.add_argument(
        "--upgrade",
        action="store_true",
        help="Upgrade: overwrite agents + references but preserve settings, "
        "CLAUDE.md, knowledge packs, and team-context",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    """Non-interactive installer: copy agents, references, and templates.

    --upgrade mode: overwrites agents + references (they improve between
    versions), merges hooks into settings.json (preserving user keys),
    and preserves CLAUDE.md, knowledge/, and team-context/.
    """
    scope: str = args.scope
    source = Path(args.source).resolve()
    force: bool = args.force
    upgrade: bool = args.upgrade

    agents_src = source / "agents"
    refs_src = source / "references"
    claude_md_src = source / "templates" / "CLAUDE.md"
    settings_src = source / "templates" / "settings.json"

    if not agents_src.is_dir():
        print(
            f"error: agents/ directory not found under '{source}'. "
            "Pass the correct --source path."
        )
        sys.exit(1)

    if scope == "user":
        base = Path.home() / ".claude"
        claude_md_dst = base / "CLAUDE.md"
        settings_dst = base / "settings.json"
    else:
        base = Path.cwd() / ".claude"
        claude_md_dst = Path.cwd() / "CLAUDE.md"
        settings_dst = base / "settings.json"

    agent_target = base / "agents"
    ref_target = base / "references"
    team_ctx = base / "team-context"
    knowledge_dir = base / "knowledge"
    skills_dir = base / "skills"

    for d in (agent_target, ref_target, team_ctx, knowledge_dir, skills_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Agents + references: always overwrite on upgrade (these improve between versions)
    agent_force = force or upgrade
    ref_force = force or upgrade

    agent_count = 0
    for src_file in sorted(agents_src.glob("*.md")):
        dst_file = agent_target / src_file.name
        if _copy_file(src_file, dst_file, force=agent_force):
            agent_count += 1

    ref_count = 0
    if refs_src.is_dir():
        for src_file in sorted(refs_src.glob("*.md")):
            dst_file = ref_target / src_file.name
            if _copy_file(src_file, dst_file, force=ref_force):
                ref_count += 1

    # Settings.json: merge on upgrade (preserve user keys, update hooks),
    # copy on fresh install
    if settings_src.is_file():
        if upgrade:
            _merge_settings(settings_src, settings_dst)
        else:
            _copy_file(settings_src, settings_dst, force=force)

    # CLAUDE.md: only on fresh install or --force (user may have customized it)
    if not upgrade:
        if claude_md_src.is_file():
            _copy_file(claude_md_src, claude_md_dst, force=force)

    action = "Upgraded" if upgrade else "Installed"
    print(f"{action}: {agent_count} agents + {ref_count} references to {scope}")
