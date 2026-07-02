"""``baton knowledge list|show|scan|audit|propose`` -- knowledge-pack lifecycle (M5).

Thin argparse layer over the business logic in
``agent_baton.core.manager.knowledge_plan``. Registers on the shared
``baton knowledge`` cooperative parser (see
``agent_baton/cli/commands/knowledge/__init__.py``) alongside the existing
``brief``/``effectiveness``/``harvest``/``stale``/``deprecate``/``retire``/
``sweep``/``usage``/``ranking``/``ab`` subcommands.

Per locked decision 2 (docs/internal/manager-mode-pmo-design.md): these
verbs read/write the existing ``knowledge.yaml`` manifest — there is no
separate ``pack.yaml``.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.cli.commands.knowledge import (
    dispatch,
    get_or_create_parser,
    register_handler,
)
from agent_baton.core.manager.knowledge_plan import (
    audit_packs,
    propose_from_gap_records,
    write_proposals,
    write_scan_report,
)
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p, sub = get_or_create_parser(subparsers)

    list_p = sub.add_parser(
        "list", help="List discovered knowledge packs with status/confidence/docs.",
    )
    list_p.add_argument("--root", type=Path, default=None, help="Project root (default: cwd)")

    show_p = sub.add_parser(
        "show", help="Show a single knowledge pack's summary, status, and freshness.",
    )
    show_p.add_argument("pack_name")
    show_p.add_argument("--root", type=Path, default=None, help="Project root (default: cwd)")

    scan_p = sub.add_parser(
        "scan",
        help="Discover knowledge packs and candidate docs; write knowledge-scan.json.",
    )
    scan_p.add_argument("--root", type=Path, default=None, help="Project root (default: cwd)")

    audit_p = sub.add_parser(
        "audit",
        help="Audit knowledge packs for invalid status, staleness, and missing metadata.",
    )
    audit_p.add_argument("--root", type=Path, default=None, help="Project root (default: cwd)")

    propose_p = sub.add_parser(
        "propose",
        help="Propose new knowledge packs from repeated knowledge-gap signals.",
    )
    propose_p.add_argument("--root", type=Path, default=None, help="Project root (default: cwd)")

    register_handler("list", _handle_list)
    register_handler("show", _handle_show)
    register_handler("scan", _handle_scan)
    register_handler("audit", _handle_audit)
    register_handler("propose", _handle_propose)

    return p


def handler(args: argparse.Namespace) -> None:
    """Module-level handler delegated to the shared knowledge dispatcher."""
    dispatch(args)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _resolve_root(args: argparse.Namespace) -> Path:
    root = getattr(args, "root", None)
    return Path(root).resolve() if root else Path.cwd().resolve()


def _build_registry(root: Path) -> KnowledgeRegistry:
    """Load global (``~/.claude/knowledge``) then project
    (``<root>/.claude/knowledge``) packs, project overriding global --
    mirrors ``KnowledgeRegistry.load_default_paths`` but honours ``--root``
    instead of always resolving the project dir from ``cwd``."""
    registry = KnowledgeRegistry()
    registry.load_directory(Path.home() / ".claude" / "knowledge")
    registry.load_directory(root / ".claude" / "knowledge", override=True)
    return registry


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def _handle_list(args: argparse.Namespace) -> None:
    root = _resolve_root(args)
    registry = _build_registry(root)
    packs = sorted(registry.all_packs.values(), key=lambda p: p.name)

    if not packs:
        print("No knowledge packs found.")
        return

    header = f"{'NAME':<30}{'STATUS':<12}{'CONFIDENCE':<12}{'DOCS':<6}{'TOKENS':<8}"
    print(header)
    for pack in packs:
        tokens = sum(d.token_estimate for d in pack.documents)
        flag = " (degraded)" if pack.name in registry.degraded_pack_names else ""
        print(
            f"{pack.name:<30}{pack.status:<12}{pack.confidence:<12}"
            f"{len(pack.documents):<6}{tokens:<8}{flag}"
        )


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def _handle_show(args: argparse.Namespace) -> None:
    root = _resolve_root(args)
    registry = _build_registry(root)
    pack = registry.get_pack(args.pack_name)
    if pack is None:
        print(f"error: knowledge pack not found: {args.pack_name}", file=sys.stderr)
        sys.exit(1)

    print(f"Pack: {pack.name}")
    print(f"  Status:        {pack.status}")
    print(f"  Confidence:    {pack.confidence}")
    print(f"  Description:   {pack.description or '(none)'}")
    print(f"  Source path:   {pack.source_path or '(unknown)'}")
    print(f"  Target agents: {', '.join(pack.target_agents) or '(none)'}")
    print(f"  Source files:  {', '.join(pack.source_files) or '(none)'}")
    print(f"  Last reviewed: {pack.last_reviewed or '(never)'}")
    stale_after = (
        f"{pack.stale_after_days} day(s)"
        if pack.stale_after_days is not None
        else "(inherits project default)"
    )
    print(f"  Stale after:   {stale_after}")
    print(f"  Documents ({len(pack.documents)}):")
    for doc in pack.documents:
        print(f"    - {doc.name} (~{doc.token_estimate} tokens)")


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def _handle_scan(args: argparse.Namespace) -> None:
    root = _resolve_root(args)
    registry = _build_registry(root)
    out_path = write_scan_report(root, registry)
    pack_count = len(registry.all_packs)
    print(f"Knowledge scan: {pack_count} pack(s) discovered.")
    print(f"Wrote {out_path}")


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


def _handle_audit(args: argparse.Namespace) -> None:
    from agent_baton.core.config.manager import ManagerConfig

    root = _resolve_root(args)
    registry = _build_registry(root)
    config = ManagerConfig.load(root)

    issues = audit_packs(registry, config, root=root)
    if not issues:
        print("Knowledge audit: no issues found.")
        return

    print(f"Knowledge audit: {len(issues)} issue(s) found.")
    for issue in issues:
        print(f"  [{issue.kind}] {issue.message}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


def _handle_propose(args: argparse.Namespace) -> None:
    root = _resolve_root(args)
    team_context_root = root / ".claude" / "team-context"

    proposals = propose_from_gap_records(team_context_root)
    if not proposals:
        print("No repeated knowledge gaps found — nothing to propose.")
        return

    written = write_proposals(team_context_root, proposals)
    print(f"Wrote {len(written)} knowledge-pack proposal(s):")
    for path in written:
        print(f"  - {path}")
