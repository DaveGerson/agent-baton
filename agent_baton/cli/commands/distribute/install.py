"""``baton install`` -- install agents and references to user or project scope.

Non-interactive installer that copies agent definitions, reference
documents, and templates from the agent-baton source tree. Supports
--upgrade mode (overwrites agents/refs, merges settings.json) and
--verify for post-install health checks.
"""
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

    # Merge hooks: for each hook event in source, add entries that aren't
    # already present (dedup by "command" string). User hooks are preserved.
    src_hooks = src_data.get("hooks", {})
    if src_hooks:
        dst_hooks = dst_data.setdefault("hooks", {})
        for event, src_entries in src_hooks.items():
            existing = dst_hooks.get(event, [])
            existing_cmds = {
                e.get("command", "") for e in existing if isinstance(e, dict)
            }
            for entry in src_entries:
                cmd = entry.get("command", "") if isinstance(entry, dict) else ""
                if cmd not in existing_cmds:
                    existing.append(entry)
                    existing_cmds.add(cmd)
            dst_hooks[event] = existing
        print(f"  merge: settings.json hooks ({len(src_hooks)} events)")

    # All other top-level keys in destination are preserved untouched
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    dst_path.write_text(
        json.dumps(dst_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def _verify_install(base: Path, agents_dir: Path, refs_dir: Path, team_ctx: Path) -> None:
    """Post-install health check."""
    issues: list[str] = []

    # Check agents directory
    agent_files = list(agents_dir.glob("*.md"))
    if not agent_files:
        issues.append("No agent .md files found in " + str(agents_dir))
    else:
        # Verify each agent has valid YAML frontmatter
        from agent_baton.utils.frontmatter import parse_frontmatter
        for f in agent_files:
            try:
                meta, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
                if not meta:
                    issues.append(f"Agent {f.name}: missing YAML frontmatter")
            except Exception as e:
                issues.append(f"Agent {f.name}: parse error — {e}")

    # Check references directory
    ref_files = list(refs_dir.glob("*.md"))
    if not ref_files:
        issues.append("No reference .md files found in " + str(refs_dir))

    # Check team-context is writable
    try:
        test_file = team_ctx / ".verify-test"
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
    except OSError as e:
        issues.append(f"team-context directory not writable: {e}")

    # Check settings.json exists
    settings = base / "settings.json"
    if not settings.exists():
        issues.append("settings.json not found — hooks will not be active")

    if issues:
        print(f"\nVerification found {len(issues)} issue(s):")
        for issue in issues:
            print(f"  - {issue}")
    else:
        count_agents = len(agent_files)
        count_refs = len(ref_files)
        print(f"\nVerification passed: {count_agents} agents, {count_refs} references, team-context writable")


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("install", help="Install agents and references")
    sub = p.add_subparsers(dest="install_command", metavar="COMMAND")

    # ---- (default) install -------------------------------------------------
    # We keep all the install flags on the parent parser so that the existing
    # ``baton install --scope project`` invocation continues to work when no
    # subcommand is given.
    p.add_argument(
        "--scope",
        choices=["user", "project"],
        default=None,
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
    p.add_argument(
        "--verify",
        action="store_true",
        help="Run post-install verification: check agents load, references readable, dirs writable",
    )

    # ---- verify subcommand -------------------------------------------------
    verify_p = sub.add_parser(
        "verify",
        help="Validate a .tar.gz agent-baton package before distribution",
        description=(
            "Runs structural and content checks on a package archive: verifies "
            "the manifest, checks agent frontmatter, validates references, and "
            "optionally displays per-file SHA-256 checksums."
        ),
    )
    verify_p.add_argument(
        "archive",
        metavar="ARCHIVE",
        help="Path to the .tar.gz package to verify",
    )
    verify_p.add_argument(
        "--checksums",
        action="store_true",
        default=False,
        help="Display per-file SHA-256 checksums alongside validation results",
    )

    return p


def handler(args: argparse.Namespace) -> None:
    cmd = getattr(args, "install_command", None)
    if cmd == "verify":
        _cmd_verify(args)
        return
    _cmd_install(args)


def _cmd_install(args: argparse.Namespace) -> None:
    """Non-interactive installer: copy agents, references, and templates.

    --upgrade mode: overwrites agents + references (they improve between
    versions), merges hooks into settings.json (preserving user keys),
    and preserves CLAUDE.md, knowledge/, and team-context/.
    """
    scope: str | None = getattr(args, "scope", None)
    if scope is None:
        print("error: --scope is required (choices: user, project)", file=sys.stderr)
        sys.exit(1)

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

    if args.verify:
        _verify_install(base, agent_target, ref_target, team_ctx)


def _cmd_verify(args: argparse.Namespace) -> None:
    """Shared implementation for 'baton install verify' and the shim."""
    from pathlib import Path as _Path
    from agent_baton.core.distribute.packager import PackageVerifier

    archive_path = _Path(args.archive)
    verifier = PackageVerifier()

    result = verifier.validate_package(archive_path)

    status_label = "PASS" if result.valid else "FAIL"
    print(f"Package: {archive_path.name}  [{status_label}]")
    print(
        f"Contents: {result.agent_count} agent(s), "
        f"{result.reference_count} reference(s), "
        f"{result.knowledge_count} knowledge pack(s)"
    )

    if result.errors:
        print(f"\nErrors ({len(result.errors)}):")
        for err in result.errors:
            print(f"  [ERROR] {err}")

    if result.warnings:
        print(f"\nWarnings ({len(result.warnings)}):")
        for warn in result.warnings:
            print(f"  [WARN]  {warn}")

    if args.checksums and result.checksums:
        print(f"\nChecksums ({len(result.checksums)} files):")
        for member_name, digest in sorted(result.checksums.items()):
            print(f"  {digest}  {member_name}")

    if not result.errors and not result.warnings:
        print("\nAll checks passed.")

    if not result.valid:
        sys.exit(1)
