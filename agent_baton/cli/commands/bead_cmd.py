"""CLI command: ``baton beads`` — inspect and manage Bead memory.

Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

Subcommands
-----------
create   Create a bead manually (task-independent or task-scoped).
list     List beads with optional filters (--type, --status, --task, --tag).
show     Show a single bead as JSON.
ready    List open beads whose blocked_by dependencies are all satisfied.
close    Close a bead with a summary.
link     Add a typed link between two beads.
cleanup  Archive old closed beads (memory decay).
promote  Promote a bead to a persistent knowledge document.
graph        Show the dependency graph for a task's beads.
synthesize   Run the BeadSynthesizer manually (refresh bead_edges/clusters).
clusters     List bead clusters discovered by the synthesizer.

All subcommands degrade gracefully when the bead store is unavailable
(older schema, no baton.db in the current project).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = Path(".claude/team-context/baton.db")
_DB_REL = Path(".claude/team-context/baton.db")


def _resolve_db_path() -> Path:
    """Resolve the baton.db path with worktree-friendly discovery (bd-ce64).

    Resolution order:

    1. ``BATON_DB_PATH`` env var (absolute or relative to cwd) — explicit
       operator override; bypasses discovery entirely.
    2. Module-level ``_DEFAULT_DB_PATH`` if it has been monkey-patched
       to an absolute path that differs from the default relative one
       (preserves the legacy test ergonomics from ``tests/test_bead_cli``).
    3. ``.claude/team-context/baton.db`` directly under the cwd
       (legacy behaviour — preserved for backwards compatibility).
    4. Walk parent directories upward, returning the first
       ``<parent>/.claude/team-context/baton.db`` that exists.  This lets
       a subagent invoked from a worktree (or any nested cwd) reach the
       project-root baton.db instead of seeing an empty per-worktree DB.
    5. Fallback: the legacy cwd-relative path (so ``_get_or_create_*``
       can still create one in the conventional spot).
    """
    override = os.environ.get("BATON_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    # Honour test-time monkey-patches of _DEFAULT_DB_PATH.  When a caller
    # has swapped it for an absolute path (the pattern in
    # tests/test_bead_cli.py and others), use that path directly so we
    # never accidentally walk upward and pick up the developer's real
    # project DB.
    patched = _DEFAULT_DB_PATH
    if patched.is_absolute() and patched != Path("/").joinpath(_DB_REL):
        return patched.resolve()

    cwd_default = (Path.cwd() / _DB_REL).resolve()
    if cwd_default.exists():
        return cwd_default

    # Walk upward — stop at filesystem root or first match.
    current = Path.cwd().resolve()
    for parent in current.parents:
        candidate = parent / _DB_REL
        if candidate.exists():
            return candidate

    return cwd_default


def _get_bead_store():
    """Resolve the BeadStore for the current project.

    Returns a :class:`~agent_baton.core.engine.bead_store.BeadStore` instance
    or ``None`` when the database is not found.  See :func:`_resolve_db_path`
    for the discovery order (bd-ce64: walks upward so worktree subagents
    find the project-root baton.db).
    """
    from agent_baton.core.engine.bead_store import BeadStore

    db = _resolve_db_path()
    if not db.exists():
        return None
    return BeadStore(db)


def _get_or_create_bead_store():
    """Resolve the BeadStore, creating the parent directory and DB if needed.

    Unlike :func:`_get_bead_store`, this helper is used by the ``create``
    subcommand which must work even when no ``baton execute start`` has been
    run yet.  It ensures the ``.claude/team-context/`` directory exists before
    constructing the store so that a fresh project does not fail with
    ``FileNotFoundError``.

    Returns a :class:`~agent_baton.core.engine.bead_store.BeadStore` instance.
    """
    from agent_baton.core.engine.bead_store import BeadStore

    db = _resolve_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    return BeadStore(db)


def _get_active_task_id() -> str | None:
    """Return the active task ID from the baton.db active_task table, or None."""
    db = _resolve_db_path()
    if not db.exists():
        return None
    try:
        import sqlite3
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT task_id FROM active_task WHERE id = 1").fetchone()
        conn.close()
        return row["task_id"] if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``beads`` subcommand and its sub-subcommands."""
    p = subparsers.add_parser(
        "beads",
        help="Inspect and manage Bead memory (agent discoveries, decisions, warnings)",
    )
    sub = p.add_subparsers(dest="beads_cmd", metavar="SUBCOMMAND")

    # -- create --------------------------------------------------------------
    create_p = sub.add_parser("create", help="Create a bead manually")
    create_p.add_argument(
        "--type",
        dest="bead_type",
        metavar="TYPE",
        required=True,
        choices=["discovery", "decision", "warning", "outcome", "planning"],
        help="Bead type: discovery, decision, warning, outcome, planning",
    )
    create_p.add_argument(
        "--content", "--body",
        dest="content",
        metavar="TEXT",
        required=True,
        help="The bead text (insight, decision, or warning) (--body is accepted as an alias)",
    )
    create_p.add_argument(
        "--task-id",
        dest="task_id",
        metavar="TASK_ID",
        default=None,
        help="Task ID to scope this bead (defaults to $BATON_TASK_ID env var; "
             "omit for a project-scoped bead)",
    )
    create_p.add_argument(
        "--step-id",
        dest="step_id",
        metavar="STEP_ID",
        default="",
        help="Step ID within the execution (optional)",
    )
    create_p.add_argument(
        "--agent",
        dest="agent_name",
        metavar="AGENT",
        default="orchestrator",
        help="Agent name to record as the bead author (default: orchestrator)",
    )
    create_p.add_argument(
        "--tag",
        dest="tags",
        metavar="TAG",
        action="append",
        default=None,
        help="Semantic tag (repeatable)",
    )
    create_p.add_argument(
        "--file",
        dest="files",
        metavar="FILE",
        action="append",
        default=None,
        help="Affected file path (repeatable)",
    )
    create_p.add_argument(
        "--confidence",
        dest="confidence",
        metavar="LEVEL",
        choices=["none", "low", "partial", "medium", "high"],
        default="medium",
        help="Confidence level (default: medium)",
    )

    # -- list ----------------------------------------------------------------
    list_p = sub.add_parser("list", help="List beads with optional filters")
    list_p.add_argument(
        "--type",
        dest="bead_type",
        metavar="TYPE",
        default=None,
        help="Filter by type: discovery, decision, warning, outcome, planning",
    )
    list_p.add_argument(
        "--status",
        dest="status",
        metavar="STATUS",
        default=None,
        help="Filter by status: open, closed, archived",
    )
    list_p.add_argument(
        "--task",
        dest="task_id",
        metavar="TASK_ID",
        default=None,
        help="Filter by task ID",
    )
    list_p.add_argument(
        "--tag",
        dest="tag",
        metavar="TAG",
        default=None,
        help="Filter by tag (AND semantics when repeated)",
        action="append",
    )
    list_p.add_argument(
        "--limit",
        dest="limit",
        metavar="N",
        type=int,
        default=20,
        help="Maximum number of results (default: 20)",
    )

    # -- show ----------------------------------------------------------------
    show_p = sub.add_parser("show", help="Show a single bead as JSON")
    show_p.add_argument("bead_id", metavar="BEAD_ID", help="Bead ID (e.g. bd-a1b2)")

    # -- ready ---------------------------------------------------------------
    ready_p = sub.add_parser(
        "ready",
        help="List open beads whose blocked_by dependencies are satisfied",
    )
    ready_p.add_argument(
        "--task",
        dest="task_id",
        metavar="TASK_ID",
        default=None,
        help="Task ID to scope the query (defaults to active task)",
    )

    # -- close ---------------------------------------------------------------
    close_p = sub.add_parser("close", help="Close a bead with a summary")
    close_p.add_argument("bead_id", metavar="BEAD_ID", help="Bead ID to close")
    close_p.add_argument(
        "--summary", "--note", "--content",
        dest="summary",
        metavar="TEXT",
        default="",
        help="Compacted summary of the bead's outcome (aliases: --note, --content)",
    )

    # -- link ----------------------------------------------------------------
    link_p = sub.add_parser("link", help="Add a typed link between two beads")
    link_p.add_argument(
        "source_id", metavar="SOURCE_ID", help="Source bead ID"
    )
    link_group = link_p.add_mutually_exclusive_group(required=True)
    link_group.add_argument(
        "--relates-to",
        dest="relates_to",
        metavar="TARGET_ID",
        help="Add a relates_to link to TARGET_ID",
    )
    link_group.add_argument(
        "--contradicts",
        dest="contradicts",
        metavar="TARGET_ID",
        help="Add a contradicts link to TARGET_ID",
    )
    link_group.add_argument(
        "--extends",
        dest="extends",
        metavar="TARGET_ID",
        help="Add an extends link to TARGET_ID",
    )
    link_group.add_argument(
        "--blocks",
        dest="blocks",
        metavar="TARGET_ID",
        help="Add a blocks link to TARGET_ID",
    )
    link_group.add_argument(
        "--validates",
        dest="validates",
        metavar="TARGET_ID",
        help="Add a validates link to TARGET_ID",
    )

    # -- cleanup -------------------------------------------------------------
    cleanup_p = sub.add_parser(
        "cleanup",
        help="Archive old closed beads (memory decay, F6)",
    )
    cleanup_p.add_argument(
        "--ttl",
        dest="ttl_hours",
        metavar="HOURS",
        type=int,
        default=168,
        help="Archive beads closed more than HOURS ago (default: 168 = 7 days)",
    )
    cleanup_p.add_argument(
        "--task",
        dest="task_id",
        metavar="TASK_ID",
        default=None,
        help="Limit decay to beads from this task ID",
    )
    cleanup_p.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help="Show how many beads would be archived without modifying anything",
    )

    # -- promote -------------------------------------------------------------
    promote_p = sub.add_parser(
        "promote",
        help="Promote a bead to a persistent knowledge document (F9)",
    )
    promote_p.add_argument(
        "bead_id",
        metavar="BEAD_ID",
        help="Bead ID to promote (e.g. bd-a1b2)",
    )
    promote_p.add_argument(
        "--pack",
        dest="pack_name",
        metavar="PACK_NAME",
        required=True,
        help="Knowledge pack to add the document to (e.g. 'project-context')",
    )

    # -- graph ---------------------------------------------------------------
    graph_p = sub.add_parser(
        "graph",
        help="Show the dependency graph for a task's beads (F11)",
    )
    graph_p.add_argument(
        "--task",
        dest="task_id",
        metavar="TASK_ID",
        default=None,
        help="Task ID whose bead graph to display (defaults to active task)",
    )

    # -- synthesize ----------------------------------------------------------
    synth_p = sub.add_parser(
        "synthesize",
        help="Refresh inferred bead edges and clusters (Wave 2.1).",
    )
    synth_p.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text.",
    )

    # -- clusters ------------------------------------------------------------
    clusters_p = sub.add_parser(
        "clusters",
        help="List bead clusters discovered by the synthesizer.",
    )
    clusters_p.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text.",
    )

    # -- handoffs (Wave 3.2) -------------------------------------------------
    handoffs_p = sub.add_parser(
        "handoffs",
        help="List automated handoff documents synthesized for a task (Wave 3.2).",
    )
    handoffs_p.add_argument(
        "--task-id",
        dest="task_id",
        metavar="TASK_ID",
        default=None,
        help="Task ID whose handoff_beads to list (defaults to active task).",
    )
    handoffs_p.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text.",
    )

    return p


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    """Dispatch to the appropriate beads subcommand handler."""
    cmd = getattr(args, "beads_cmd", None)
    if cmd is None:
        print("Usage: baton beads <subcommand>  [create|list|show|ready|close|link|cleanup|promote|graph|synthesize|clusters|handoffs]")
        print("Run `baton beads --help` for details.")
        return

    dispatch = {
        "create": _handle_create,
        "list": _handle_list,
        "show": _handle_show,
        "ready": _handle_ready,
        "close": _handle_close,
        "link": _handle_link,
        "cleanup": _handle_cleanup,
        "promote": _handle_promote,
        "graph": _handle_graph,
        "synthesize": _handle_synthesize,
        "clusters": _handle_clusters,
        "handoffs": _handle_handoffs,
    }
    fn = dispatch.get(cmd)
    if fn is None:
        print(f"Unknown subcommand: {cmd}", file=sys.stderr)
        sys.exit(1)
    fn(args)


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------


def _handle_create(args: argparse.Namespace) -> None:
    """Create a bead manually via the CLI.

    Project-scoped beads (no ``--task-id`` and no ``$BATON_TASK_ID``) are
    written with ``task_id=""`` which the store converts to NULL in the
    database, bypassing the executions FK constraint.  Task-scoped beads
    require a matching executions row only when foreign-key enforcement is
    active (it is OFF by default in SQLite unless explicitly enabled).
    """
    from datetime import datetime, timezone

    from agent_baton.models.bead import Bead

    # Resolve task_id: CLI flag > env var > project-scoped (empty string).
    task_id: str = args.task_id or os.environ.get("BATON_TASK_ID", "") or ""

    content: str = args.content
    bead_id: str = f"bd-{hashlib.sha256(content.encode()).hexdigest()[:4]}"

    # Determine scope based on whether we have a task_id.
    scope = "task" if task_id else "project"

    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    bead = Bead(
        bead_id=bead_id,
        task_id=task_id,
        step_id=args.step_id,
        agent_name=args.agent_name,
        bead_type=args.bead_type,
        content=content,
        confidence=args.confidence,
        scope=scope,
        tags=args.tags or [],
        affected_files=args.files or [],
        status="open",
        created_at=created_at,
        source="manual",
    )

    store = _get_or_create_bead_store()
    result = store.write(bead)
    if not result:
        print(
            f"error: failed to write bead — check that baton.db schema is up to date.",
            file=sys.stderr,
        )
        sys.exit(1)

    scope_label = f"task={task_id}" if task_id else "project-scoped"
    print(f"Created bead {bead_id} [{args.bead_type}] ({scope_label}).")


def _handle_list(args: argparse.Namespace) -> None:
    store = _get_bead_store()
    if store is None:
        print("No baton.db found in .claude/team-context/ — no beads to list.")
        return

    tags = args.tag or None
    beads = store.query(
        task_id=args.task_id,
        bead_type=args.bead_type,
        status=args.status,
        tags=tags,
        limit=args.limit,
    )

    if not beads:
        print("No beads found matching the given filters.")
        return

    for bead in beads:
        tag_str = f"  tags=[{', '.join(bead.tags)}]" if bead.tags else ""
        files_str = (
            f"  files=[{', '.join(bead.affected_files)}]"
            if bead.affected_files
            else ""
        )
        print(
            f"{bead.bead_id}  [{bead.bead_type:9s}]  [{bead.status:8s}]"
            f"  {bead.agent_name:30s}  {bead.content[:60]!r}"
            f"{tag_str}{files_str}"
        )
    print(f"\n{len(beads)} bead(s) shown.")


def _handle_show(args: argparse.Namespace) -> None:
    store = _get_bead_store()
    if store is None:
        print("No baton.db found in .claude/team-context/ — no beads to show.")
        return

    bead = store.read(args.bead_id)
    if bead is None:
        print(f"Bead not found: {args.bead_id}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(bead.to_dict(), indent=2))


def _handle_ready(args: argparse.Namespace) -> None:
    store = _get_bead_store()
    if store is None:
        print("No baton.db found in .claude/team-context/ — no beads available.")
        return

    task_id = args.task_id or _get_active_task_id()
    if not task_id:
        print(
            "No active task found. Pass --task TASK_ID to specify one.",
            file=sys.stderr,
        )
        sys.exit(1)

    beads = store.ready(task_id)
    if not beads:
        print(f"No ready beads for task {task_id}.")
        return

    print(f"Ready beads for task {task_id}:")
    for bead in beads:
        print(
            f"  {bead.bead_id}  [{bead.bead_type:9s}]  "
            f"{bead.agent_name:30s}  {bead.content[:70]!r}"
        )
    print(f"\n{len(beads)} ready bead(s).")


def _handle_close(args: argparse.Namespace) -> None:
    store = _get_bead_store()
    if store is None:
        print("No baton.db found in .claude/team-context/.")
        return

    bead = store.read(args.bead_id)
    if bead is None:
        print(f"Bead not found: {args.bead_id}", file=sys.stderr)
        sys.exit(1)

    store.close(args.bead_id, args.summary)
    print(f"Closed bead {args.bead_id}.")


def _handle_link(args: argparse.Namespace) -> None:
    store = _get_bead_store()
    if store is None:
        print("No baton.db found in .claude/team-context/.")
        return

    # Determine link type and target from the mutually exclusive group.
    if args.relates_to:
        link_type, target_id = "relates_to", args.relates_to
    elif args.contradicts:
        link_type, target_id = "contradicts", args.contradicts
    elif args.extends:
        link_type, target_id = "extends", args.extends
    elif args.blocks:
        link_type, target_id = "blocks", args.blocks
    elif args.validates:
        link_type, target_id = "validates", args.validates
    else:
        print("No link type specified.", file=sys.stderr)
        sys.exit(1)

    # Verify both beads exist before creating the link.
    source = store.read(args.source_id)
    if source is None:
        print(f"Source bead not found: {args.source_id}", file=sys.stderr)
        sys.exit(1)
    target = store.read(target_id)
    if target is None:
        print(f"Target bead not found: {target_id}", file=sys.stderr)
        sys.exit(1)

    store.link(args.source_id, target_id, link_type)
    print(f"Linked {args.source_id} --[{link_type}]--> {target_id}.")


def _handle_cleanup(args: argparse.Namespace) -> None:
    """F6 — Memory Decay: archive old closed beads."""
    store = _get_bead_store()
    if store is None:
        print("No baton.db found in .claude/team-context/ — nothing to clean up.")
        return

    from agent_baton.core.engine.bead_decay import decay_beads

    count = decay_beads(
        store,
        ttl_hours=args.ttl_hours,
        task_id=args.task_id,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print(
            f"Dry run: {count} bead(s) would be archived "
            f"(TTL={args.ttl_hours}h, task={args.task_id or 'all'})."
        )
    else:
        print(
            f"Archived {count} bead(s) "
            f"(TTL={args.ttl_hours}h, task={args.task_id or 'all'})."
        )


def _handle_promote(args: argparse.Namespace) -> None:
    """F9 — Bead-to-Knowledge Promotion: write bead content as a knowledge doc."""
    store = _get_bead_store()
    if store is None:
        print("No baton.db found in .claude/team-context/.")
        return

    bead = store.read(args.bead_id)
    if bead is None:
        print(f"Bead not found: {args.bead_id}", file=sys.stderr)
        sys.exit(1)

    pack_name = args.pack_name
    # Resolve the knowledge pack directory.
    knowledge_dir = Path(".claude/knowledge") / pack_name
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    # Write bead content as a markdown document.
    safe_id = bead.bead_id.replace("bd-", "")
    doc_name = f"bead-{safe_id}-{bead.bead_type}.md"
    doc_path = knowledge_dir / doc_name

    doc_content = "\n".join([
        f"---",
        f"title: \"{bead.bead_type.capitalize()}: {bead.content[:60]}\"",
        f"source: bead-promotion",
        f"bead_id: {bead.bead_id}",
        f"bead_type: {bead.bead_type}",
        f"agent: {bead.agent_name}",
        f"created_at: {bead.created_at}",
        f"tags: [{', '.join(bead.tags)}]",
        f"---",
        f"",
        f"# {bead.bead_type.capitalize()} from {bead.agent_name}",
        f"",
        bead.content,
        f"",
    ])
    if bead.affected_files:
        doc_content += f"**Affected files:** {', '.join(bead.affected_files)}\n"

    try:
        doc_path.write_text(doc_content, encoding="utf-8")
    except OSError as exc:
        print(f"error: could not write knowledge document {doc_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    # Update pack.yaml index if it exists.
    pack_yaml = knowledge_dir / "pack.yaml"
    if pack_yaml.exists():
        try:
            text = pack_yaml.read_text(encoding="utf-8")
            if doc_name not in text:
                # Append a simple documents entry.
                with pack_yaml.open("a", encoding="utf-8") as f:
                    f.write(f"  - path: {doc_name}\n")
                    f.write(f"    description: \"Promoted from bead {bead.bead_id}\"\n")
        except Exception as exc:
            print(f"Warning: could not update pack.yaml: {exc}", file=sys.stderr)

    # Close the bead now that it has been promoted.
    store.close(bead.bead_id, summary=f"Promoted to knowledge pack '{pack_name}' as {doc_name}")
    print(f"Promoted bead {bead.bead_id} to {doc_path}.")
    print(f"Bead {bead.bead_id} marked as closed.")


def _handle_graph(args: argparse.Namespace) -> None:
    """F11 — Bead Dependency Graph: display link relationships for a task."""
    store = _get_bead_store()
    if store is None:
        print("No baton.db found in .claude/team-context/.")
        return

    task_id = args.task_id or _get_active_task_id()
    if not task_id:
        print(
            "No active task found. Pass --task TASK_ID to specify one.",
            file=sys.stderr,
        )
        sys.exit(1)

    beads = store.query(task_id=task_id, limit=500)
    if not beads:
        print(f"No beads found for task {task_id}.")
        return

    print(f"Bead graph for task {task_id} ({len(beads)} bead(s)):")
    print()

    bead_index = {b.bead_id: b for b in beads}

    for bead in beads:
        conflict_marker = ""
        try:
            has_conflict = any(
                t == "conflict:unresolved"
                for b2 in [bead]
                for t in (b2.tags or [])
            )
            if has_conflict:
                conflict_marker = " [CONFLICT]"
        except Exception:
            pass

        print(
            f"  {bead.bead_id} [{bead.bead_type:9s}] [{bead.status:8s}]"
            f"  {bead.agent_name}{conflict_marker}"
        )
        print(f"    {bead.content[:80]!r}")
        if bead.links:
            for lnk in bead.links:
                target = bead_index.get(lnk.target_bead_id)
                target_label = (
                    f"{target.bead_type}/{target.agent_name}"
                    if target else "external"
                )
                print(f"    --[{lnk.link_type}]--> {lnk.target_bead_id} ({target_label})")
        print()

    # Summary
    conflict_beads = [b for b in beads if "conflict:unresolved" in (b.tags or [])]
    if conflict_beads:
        print(f"WARNING: {len(conflict_beads)} unresolved conflict(s) detected.")
        print("Run `baton beads list --tag conflict:unresolved` to inspect.")
    else:
        print("No unresolved conflicts.")

    # Synthesizer-derived edges (Wave 2.1) — pulled directly from
    # bead_edges so we don't need to widen the BeadStore API surface.
    bead_id_set = {b.bead_id for b in beads}
    synth_rows = _query_bead_edges_for(bead_id_set)
    if synth_rows:
        print()
        print(f"Synthesized edges ({len(synth_rows)}):")
        for src, dst, etype, weight in synth_rows:
            print(f"  {src} --[{etype} w={weight:.2f}]--> {dst}")


def _query_bead_edges_for(bead_ids: set[str]) -> list[tuple[str, str, str, float]]:
    """Fetch bead_edges where both endpoints are in *bead_ids*.

    Defensive: returns ``[]`` when the table is missing or the query fails.
    """
    if not bead_ids:
        return []
    db = _DEFAULT_DB_PATH.resolve()
    if not db.exists():
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(str(db))
        # Detect table presence to stay graceful on older schemas.
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bead_edges'"
        ).fetchone()
        if not has_table:
            conn.close()
            return []
        placeholders = ",".join("?" * len(bead_ids))
        ids_list = list(bead_ids)
        rows = conn.execute(
            f"SELECT src_bead_id, dst_bead_id, edge_type, weight "
            f"FROM bead_edges "
            f"WHERE src_bead_id IN ({placeholders}) "
            f"AND dst_bead_id IN ({placeholders}) "
            f"ORDER BY edge_type, weight DESC, src_bead_id",
            ids_list + ids_list,
        ).fetchall()
        conn.close()
        return [(r[0], r[1], r[2], float(r[3])) for r in rows]
    except Exception:
        return []


def _handle_synthesize(args: argparse.Namespace) -> None:
    """Run the BeadSynthesizer manually and print counts."""
    store = _get_bead_store()
    if store is None:
        print("No baton.db found in .claude/team-context/.", file=sys.stderr)
        sys.exit(1)

    from agent_baton.core.intel.bead_synthesizer import BeadSynthesizer

    conn = store._conn()
    result = BeadSynthesizer().synthesize(conn)

    if getattr(args, "as_json", False):
        print(json.dumps(result.to_dict(), indent=2))
        return

    print("BeadSynthesizer completed.")
    print(f"  pairs_examined:    {result.pairs_examined}")
    print(f"  edges_added:       {result.edges_added}")
    print(f"  clusters_created:  {result.clusters_created}")
    print(f"  conflicts_flagged: {result.conflicts_flagged}")
    if result.errors:
        print(f"  errors:            {len(result.errors)}")
        for err in result.errors:
            print(f"    - {err}")


def _handle_clusters(args: argparse.Namespace) -> None:
    """List bead clusters discovered by the synthesizer."""
    db = _DEFAULT_DB_PATH.resolve()
    if not db.exists():
        print("No baton.db found in .claude/team-context/.", file=sys.stderr)
        sys.exit(1)

    try:
        import sqlite3
        conn = sqlite3.connect(str(db))
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bead_clusters'"
        ).fetchone()
        if not has_table:
            print("bead_clusters table not present (older schema).")
            conn.close()
            return
        rows = conn.execute(
            "SELECT cluster_id, label, bead_ids, created_at "
            "FROM bead_clusters ORDER BY created_at DESC, cluster_id"
        ).fetchall()
        conn.close()
    except Exception as exc:
        print(f"Failed to read bead_clusters: {exc}", file=sys.stderr)
        sys.exit(1)

    if not rows:
        if getattr(args, "as_json", False):
            print("[]")
        else:
            print("No bead clusters found. Run `baton beads synthesize` first.")
        return

    if getattr(args, "as_json", False):
        out = []
        for cluster_id, label, bead_ids_json, created_at in rows:
            try:
                members = json.loads(bead_ids_json)
            except Exception:
                members = []
            out.append({
                "cluster_id": cluster_id,
                "label": label,
                "bead_ids": members,
                "created_at": created_at,
            })
        print(json.dumps(out, indent=2))
        return

    print(f"Bead clusters ({len(rows)}):")
    for cluster_id, label, bead_ids_json, _created_at in rows:
        try:
            members = json.loads(bead_ids_json)
        except Exception:
            members = []
        print(f"  {cluster_id}  [{label}]  ({len(members)} bead(s))")
        for bid in members:
            print(f"    - {bid}")


def _handle_handoffs(args: argparse.Namespace) -> None:
    """List automated handoff documents synthesized for a task (Wave 3.2).

    Reads ``handoff_beads`` rows directly via sqlite3 — the table is
    populated by :class:`agent_baton.core.intel.handoff_synthesizer.
    HandoffSynthesizer` whenever the dispatcher hands off from one step
    to the next.  No-op when the table doesn't exist (older schema).
    """
    task_id: str | None = (
        getattr(args, "task_id", None)
        or os.environ.get("BATON_TASK_ID")
        or _get_active_task_id()
    )
    if not task_id:
        print(
            "No task ID provided and no active task found. "
            "Pass --task-id or set $BATON_TASK_ID.",
            file=sys.stderr,
        )
        sys.exit(1)

    db = _resolve_db_path()
    if not db.exists():
        print(f"No baton.db found at {db}.", file=sys.stderr)
        sys.exit(1)

    try:
        import sqlite3
        conn = sqlite3.connect(str(db))
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='handoff_beads'"
        ).fetchone()
        if not has_table:
            print("handoff_beads table not present (older schema).")
            conn.close()
            return
        rows = conn.execute(
            "SELECT handoff_id, from_step_id, to_step_id, created_at, content "
            "FROM handoff_beads WHERE task_id = ? "
            "ORDER BY created_at ASC, handoff_id ASC",
            (task_id,),
        ).fetchall()
        conn.close()
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to read handoff_beads: {exc}", file=sys.stderr)
        sys.exit(1)

    if getattr(args, "as_json", False):
        out = [
            {
                "handoff_id": h_id,
                "from_step_id": frm,
                "to_step_id": to,
                "created_at": ts,
                "content": content,
            }
            for (h_id, frm, to, ts, content) in rows
        ]
        print(json.dumps(out, indent=2))
        return

    if not rows:
        print(f"No handoff beads found for task {task_id}.")
        return

    print(f"Handoff beads for task {task_id} ({len(rows)}):")
    print(f"  {'HANDOFF_ID':<16} {'FROM → TO':<14} {'CREATED_AT':<22} CONTENT")
    for h_id, frm, to, ts, content in rows:
        snippet = (content or "").splitlines()[0][:60] if content else ""
        arrow = f"{frm or '-'} → {to or '-'}"
        print(f"  {h_id:<16} {arrow:<14} {ts:<22} {snippet}")
