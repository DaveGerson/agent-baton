"""``baton packs`` — Assurance Pack management commands.

Subcommands:
    init <name> [--dir DIR]       Scaffold a new pack directory from template.
    validate [<name>] [--dir DIR] Validate one or all packs; exit 2 on errors.
    list [--dir DIR]              List discovered packs with key metadata.

Exit codes
----------
0   Success / all packs valid.
1   Usage error (init: directory already exists, missing name).
2   Validation errors found (validate subcommand).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Scaffold skeleton — minimal VALID template with [YOUR_PACK_NAME] placeholders.
# Every field marked ILLUSTRATIVE TEMPLATE ONLY should be replaced by the
# org authoring the pack.  All JSON files include a "_comment" key noting this.
# ---------------------------------------------------------------------------

_SKELETON: dict[str, str] = {
    "pack.json": """\
{
  "_comment": "ILLUSTRATIVE TEMPLATE ONLY — replace all [YOUR_PACK_NAME] placeholders with real content.",
  "name": "[YOUR_PACK_NAME]",
  "version": "0.1.0",
  "description": "Describe what this pack governs (e.g. HIPAA PHI handling, OWASP secure coding).",
  "domain": "your-domain",
  "risk_level": "HIGH",
  "author": "your-org",
  "baton_min_version": ""
}
""",
    "policy.json": """\
{
  "_comment": "ILLUSTRATIVE TEMPLATE ONLY — not maintained compliance content.",
  "name": "pack:[YOUR_PACK_NAME]",
  "description": "Policy rules for [YOUR_PACK_NAME] domain governance.",
  "rules": [
    {
      "name": "require_subject_matter_expert",
      "description": "Subject-matter-expert must be in the plan for this domain.",
      "scope": "all",
      "rule_type": "require_agent",
      "pattern": "subject-matter-expert",
      "severity": "block"
    },
    {
      "name": "require_auditor",
      "description": "Auditor pre- and post-execution review is required.",
      "scope": "all",
      "rule_type": "require_agent",
      "pattern": "auditor",
      "severity": "block"
    }
  ]
}
""",
    "signals.json": """\
{
  "_comment": "ILLUSTRATIVE TEMPLATE ONLY — not maintained compliance content.",
  "pack": "[YOUR_PACK_NAME]",
  "keywords": {
    "regulated": ["[your-keyword-1]", "[your-keyword-2]"],
    "pii": ["[your-pii-keyword]"]
  },
  "path_patterns": ["[your/sensitive/path]"],
  "preset_name": "pack:[YOUR_PACK_NAME]",
  "risk_level": "HIGH"
}
""",
    "rubric.md": """\
<!-- ILLUSTRATIVE TEMPLATE ONLY — not maintained compliance content. -->
# [YOUR_PACK_NAME] Review Rubric

## Pre-execution checklist

- [ ] Subject-matter-expert has reviewed the plan.
- [ ] Auditor pre-execution review is complete.
- [ ] All sensitive data paths are identified.

## Post-execution checklist

- [ ] Auditor post-execution review is complete.
- [ ] Evidence artifacts are collected and stored.
""",
    "gates.json": """\
{
  "_comment": "ILLUSTRATIVE TEMPLATE ONLY — not maintained compliance content.",
  "pack": "[YOUR_PACK_NAME]",
  "gates": [
    {
      "id": "scan",
      "description": "Run domain-specific scan before committing changes.",
      "command": "echo '[YOUR_PACK_NAME] scan placeholder — replace with real command'",
      "on_match": "fail",
      "gate_type": "pre_commit",
      "pattern_ref": ""
    }
  ]
}
""",
    "evidence.json": """\
{
  "_comment": "ILLUSTRATIVE TEMPLATE ONLY — not maintained compliance content.",
  "pack": "[YOUR_PACK_NAME]",
  "required_artifacts": [
    {
      "id": "sme_review",
      "description": "Written sign-off from the subject-matter-expert agent."
    },
    {
      "id": "auditor_review",
      "description": "Auditor pre- and post-execution review report."
    }
  ]
}
""",
    "knowledge/overview.md": """\
<!-- ILLUSTRATIVE TEMPLATE ONLY — not maintained compliance content. -->
# [YOUR_PACK_NAME] Domain Overview

Replace this file with domain-specific reference material.

## Key concepts

- Concept 1: description
- Concept 2: description

## Resources

- Link to authoritative regulation or standard
""",
}

_NEXT_STEPS_HINT = """\
Next steps:
  1. Replace all [YOUR_PACK_NAME] placeholders with your pack's name.
  2. Edit policy.json rules to match your domain requirements.
  3. Edit signals.json keywords/path_patterns to trigger classification.
  4. Fill in rubric.md with domain-specific review criteria.
  5. Run: baton packs validate {name}
"""


# ---------------------------------------------------------------------------
# register / handler (auto-discovered by main.py)
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "packs",
        help="Manage assurance packs (.claude/packs/<name>/)",
    )
    sub = p.add_subparsers(dest="packs_cmd", metavar="COMMAND")

    # ── init ──────────────────────────────────────────────────────────────
    init_p = sub.add_parser("init", help="Scaffold a new pack directory")
    init_p.add_argument("name", metavar="NAME", help="Pack name (becomes the directory name)")
    init_p.add_argument(
        "--dir",
        metavar="DIR",
        default=None,
        help="Project root containing .claude/ (default: cwd)",
    )

    # ── validate ──────────────────────────────────────────────────────────
    val_p = sub.add_parser(
        "validate", help="Validate one or all packs; exit 2 on any error"
    )
    val_p.add_argument(
        "name",
        metavar="NAME",
        nargs="?",
        default=None,
        help="Pack name to validate (default: validate all)",
    )
    val_p.add_argument(
        "--dir",
        metavar="DIR",
        default=None,
        help="Project root containing .claude/ (default: cwd)",
    )

    # ── list ──────────────────────────────────────────────────────────────
    list_p = sub.add_parser("list", help="List discovered packs with metadata")
    list_p.add_argument(
        "--dir",
        metavar="DIR",
        default=None,
        help="Project root containing .claude/ (default: cwd)",
    )

    return p


def handler(args: argparse.Namespace) -> None:
    cmd = getattr(args, "packs_cmd", None)
    if cmd is None:
        print("Usage: baton packs <init|validate|list>", file=sys.stderr)
        sys.exit(1)
    if cmd == "init":
        _cmd_init(args)
    elif cmd == "validate":
        _cmd_validate(args)
    elif cmd == "list":
        _cmd_list(args)
    else:
        print(f"Unknown packs subcommand: {cmd}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def _resolve_packs_dir(args: argparse.Namespace) -> Path:
    root = Path(args.dir) if getattr(args, "dir", None) else Path.cwd()
    return root / ".claude" / "packs"


def _cmd_init(args: argparse.Namespace) -> None:
    """Scaffold a new pack directory."""
    name: str = args.name
    if not name:
        print("error: pack name is required", file=sys.stderr)
        sys.exit(1)

    packs_dir = _resolve_packs_dir(args)
    pack_path = packs_dir / name

    if pack_path.exists():
        print(
            f"error: pack directory already exists: {pack_path}",
            file=sys.stderr,
        )
        sys.exit(1)

    pack_path.mkdir(parents=True, exist_ok=False)
    (pack_path / "knowledge").mkdir(exist_ok=True)

    for rel_path, content in _SKELETON.items():
        # Substitute [YOUR_PACK_NAME] with the actual pack name.
        rendered = content.replace("[YOUR_PACK_NAME]", name)
        target = pack_path / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")

    print(f"Scaffolded pack '{name}' at {pack_path}")
    print(_NEXT_STEPS_HINT.format(name=name))


def _cmd_validate(args: argparse.Namespace) -> None:
    """Validate one or all packs; exit 2 on any error."""
    from agent_baton.core.govern.packs import validate_pack

    packs_dir = _resolve_packs_dir(args)
    name: str | None = getattr(args, "name", None)

    if name:
        targets = [packs_dir / name]
    else:
        if not packs_dir.is_dir():
            print(f"No packs directory found at {packs_dir}")
            return
        targets = sorted(p for p in packs_dir.iterdir() if p.is_dir())

    if not targets:
        print("No packs found.")
        return

    found_errors = False
    for pack_path in targets:
        if not pack_path.is_dir():
            print(f"[ERROR] {pack_path.name}: not a directory")
            found_errors = True
            continue
        errors = validate_pack(pack_path)
        if errors:
            found_errors = True
            for err in errors:
                print(str(err))
        else:
            print(f"[OK] {pack_path.name}")

    if found_errors:
        sys.exit(2)
    else:
        print("All packs valid.")


def _cmd_list(args: argparse.Namespace) -> None:
    """List discovered packs with key metadata."""
    from agent_baton.core.govern.packs import validate_pack

    packs_dir = _resolve_packs_dir(args)
    if not packs_dir.is_dir():
        print("No packs directory found.")
        return

    dirs = sorted(p for p in packs_dir.iterdir() if p.is_dir())
    if not dirs:
        print("No packs found.")
        return

    # Header
    print(f"{'NAME':<25} {'VERSION':<10} {'DOMAIN':<20} {'RISK':<10} {'DESCRIPTION'}")
    print("-" * 90)
    for pack_path in dirs:
        pack_json = pack_path / "pack.json"
        errors = validate_pack(pack_path)
        if not pack_json.exists() or errors:
            # Show [INVALID] row with whatever we can read.
            name = pack_path.name
            try:
                data = json.loads(pack_json.read_text(encoding="utf-8"))
                version = data.get("version", "?")
                domain = data.get("domain", "?")
                risk = data.get("risk_level", "?")
                desc = data.get("description", "?")
            except Exception:
                version = domain = risk = desc = "?"
            print(f"[INVALID] {name:<16} {version:<10} {domain:<20} {risk:<10} {desc}")
        else:
            try:
                data = json.loads(pack_json.read_text(encoding="utf-8"))
                name = data.get("name", pack_path.name)
                version = data.get("version", "?")
                domain = data.get("domain", "")
                risk = data.get("risk_level", "HIGH")
                desc = data.get("description", "")
                print(f"{name:<25} {version:<10} {domain:<20} {risk:<10} {desc}")
            except Exception:
                print(f"[INVALID] {pack_path.name}")
