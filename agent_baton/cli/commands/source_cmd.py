"""CLI command: ``baton source`` — manage external work-item source connections.

Subcommands
-----------
add         Register an external source (ADO, Jira, GitHub, Linear).
list        List all registered external sources.
sync        Pull work items from a source into central.db.
remove      Remove a registered external source.
map         Map an external item to a baton project/task.

External source adapters are deferred. Sync operations print an informational
message until an adapter is implemented for the requested source type.
"""
from __future__ import annotations

import argparse
import sys


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``source`` subcommand group."""
    p = subparsers.add_parser(
        "source",
        help="Manage external work-item source connections (ADO, Jira, GitHub, Linear)",
    )
    sub = p.add_subparsers(dest="subcommand")

    # baton source add ado --name NAME --org ORG --project PROJ --pat-env ENV
    p_add = sub.add_parser("add", help="Register an external source connection")
    p_add.add_argument(
        "source_type",
        metavar="TYPE",
        help="Source type: ado, jira, github, linear",
    )
    p_add.add_argument(
        "--name",
        required=True,
        metavar="NAME",
        help="Display name for this source",
    )
    p_add.add_argument(
        "--org",
        default="",
        metavar="ORG",
        help="Organisation or account name (ADO/GitHub)",
    )
    p_add.add_argument(
        "--project",
        default="",
        dest="source_project",
        metavar="PROJECT",
        help="Project name within the source (ADO/Jira)",
    )
    p_add.add_argument(
        "--pat-env",
        default="",
        dest="pat_env",
        metavar="ENV_VAR",
        help="Name of environment variable holding the PAT/token",
    )
    p_add.add_argument(
        "--url",
        default="",
        metavar="URL",
        help="Base URL for self-hosted instances (Jira Server, GitHub Enterprise)",
    )

    # baton source list
    sub.add_parser("list", help="List all registered external sources")

    # baton source sync [SOURCE_ID] [--all]
    p_sync = sub.add_parser("sync", help="Pull work items from an external source")
    p_sync.add_argument(
        "source_id",
        nargs="?",
        default=None,
        metavar="SOURCE_ID",
        help="Source ID to sync (see 'baton source list')",
    )
    p_sync.add_argument(
        "--all",
        action="store_true",
        dest="sync_all",
        help="Sync all registered sources",
    )

    # baton source remove SOURCE_ID
    p_remove = sub.add_parser("remove", help="Remove a registered external source")
    p_remove.add_argument(
        "source_id",
        metavar="SOURCE_ID",
        help="Source ID to remove",
    )

    # baton source map SOURCE_ID EXTERNAL_ID PROJECT_ID TASK_ID [--type TYPE]
    p_map = sub.add_parser(
        "map",
        help="Map an external work item to a baton project/task",
    )
    p_map.add_argument("source_id", metavar="SOURCE_ID", help="Source ID")
    p_map.add_argument("external_id", metavar="EXTERNAL_ID", help="External item ID (e.g. ADO work item number)")
    p_map.add_argument("project_id", metavar="PROJECT_ID", help="Baton project ID")
    p_map.add_argument("task_id", metavar="TASK_ID", help="Baton task/execution ID")
    p_map.add_argument(
        "--type",
        default="implements",
        dest="mapping_type",
        choices=["implements", "blocks", "related"],
        help="Relationship type (default: implements)",
    )

    return p


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    if not hasattr(args, "subcommand") or args.subcommand is None:
        print("usage: baton source <subcommand>")
        print("subcommands: add, list, sync, remove, map")
        sys.exit(1)

    if args.subcommand == "add":
        _add(args)
    elif args.subcommand == "list":
        _list(args)
    elif args.subcommand == "sync":
        _sync(args)
    elif args.subcommand == "remove":
        _remove(args)
    elif args.subcommand == "map":
        _map(args)
    else:
        print(f"error: unknown source subcommand: {args.subcommand}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def _add(args: argparse.Namespace) -> None:
    """Register an external source in central.db."""
    import json

    _SUPPORTED_TYPES = {"ado", "jira", "github", "linear"}
    if args.source_type not in _SUPPORTED_TYPES:
        print(f"error: unknown source type '{args.source_type}'")
        print(f"supported types: {', '.join(sorted(_SUPPORTED_TYPES))}")
        sys.exit(1)

    # Build a stable source_id from type + org + project
    parts = [args.source_type]
    if args.org:
        parts.append(args.org.lower().replace(" ", "-"))
    if args.source_project:
        parts.append(args.source_project.lower().replace(" ", "-"))
    source_id = "-".join(parts)

    config = {
        "org": args.org,
        "project": args.source_project,
        "pat_env": args.pat_env,
        "url": args.url,
    }

    try:
        from agent_baton.core.storage.central import CentralStore
    except ImportError as exc:
        print(f"error: central storage module unavailable: {exc}")
        sys.exit(1)

    store = CentralStore()
    try:
        store.query(
            "INSERT OR REPLACE INTO external_sources "
            "(source_id, source_type, display_name, config, enabled) "
            "VALUES (?, ?, ?, ?, 1)",
            (source_id, args.source_type, args.name, json.dumps(config)),
        )
    except Exception as exc:
        print(f"error registering source: {exc}")
        store.close()
        sys.exit(1)
    store.close()

    print(f"Registered source: {source_id}")
    print(f"  Type:    {args.source_type}")
    print(f"  Name:    {args.name}")
    if args.org:
        print(f"  Org:     {args.org}")
    if args.source_project:
        print(f"  Project: {args.source_project}")
    if args.pat_env:
        print(f"  PAT env: {args.pat_env}")
    if args.url:
        print(f"  URL:     {args.url}")
    print()
    print(f"Sync with: baton source sync {source_id}")


def _list(args: argparse.Namespace) -> None:  # noqa: ARG001
    """List all registered external sources."""
    try:
        from agent_baton.core.storage.central import CentralStore
    except ImportError as exc:
        print(f"error: central storage module unavailable: {exc}")
        sys.exit(1)

    store = CentralStore()
    try:
        rows = store.query(
            "SELECT source_id, source_type, display_name, last_synced, enabled "
            "FROM external_sources ORDER BY source_type, source_id"
        )
    except Exception as exc:
        print(f"error reading external_sources: {exc}")
        store.close()
        sys.exit(1)
    store.close()

    if not rows:
        print("No external sources registered.")
        print("Add one with: baton source add ado --name NAME --org ORG --project PROJ --pat-env ENV_VAR")
        return

    print(f"External Sources ({len(rows)} registered)")
    print()
    for row in rows:
        enabled = "enabled" if row["enabled"] else "disabled"
        last_synced = row["last_synced"] or "(never)"
        print(f"  {row['source_id']:<30}  {row['source_type']:<8}  {row['display_name']:<24}  {enabled}  last: {last_synced}")


def _sync(args: argparse.Namespace) -> None:
    """Pull work items from an external source (adapter scaffold)."""
    if not args.sync_all and not args.source_id:
        print("error: specify a SOURCE_ID or use --all")
        sys.exit(1)

    try:
        from agent_baton.core.storage.central import CentralStore
    except ImportError as exc:
        print(f"error: central storage module unavailable: {exc}")
        sys.exit(1)

    store = CentralStore()
    try:
        if args.sync_all:
            sources = store.query(
                "SELECT source_id, source_type, display_name FROM external_sources WHERE enabled = 1"
            )
        else:
            sources = store.query(
                "SELECT source_id, source_type, display_name FROM external_sources WHERE source_id = ?",
                (args.source_id,),
            )
    except Exception as exc:
        print(f"error reading external_sources: {exc}")
        store.close()
        sys.exit(1)
    store.close()

    if not sources:
        if args.sync_all:
            print("No enabled external sources found.")
        else:
            print(f"error: source '{args.source_id}' not found.")
            print("Run 'baton source list' to see registered sources.")
        sys.exit(1)

    # Attempt to resolve adapters — currently deferred
    try:
        from agent_baton.core.storage.adapters import AdapterRegistry  # type: ignore[import]
        available = AdapterRegistry.available()
    except ImportError:
        available = []

    for row in sources:
        if row["source_type"] not in available:
            print(f"  {row['source_id']}: Adapter not available for source type '{row['source_type']}'.")
            print(f"    External adapters are not yet implemented. Check back in a future release.")
        else:
            # Future: instantiate adapter and call fetch_items()
            print(f"  {row['source_id']}: Adapter found but sync not yet wired. Check back soon.")


def _remove(args: argparse.Namespace) -> None:
    """Remove an external source from central.db."""
    try:
        from agent_baton.core.storage.central import CentralStore
    except ImportError as exc:
        print(f"error: central storage module unavailable: {exc}")
        sys.exit(1)

    store = CentralStore()
    try:
        existing = store.query(
            "SELECT source_id, display_name FROM external_sources WHERE source_id = ?",
            (args.source_id,),
        )
        if not existing:
            print(f"error: source '{args.source_id}' not found.")
            store.close()
            sys.exit(1)
        store.query(
            "DELETE FROM external_sources WHERE source_id = ?",
            (args.source_id,),
        )
    except Exception as exc:
        print(f"error removing source: {exc}")
        store.close()
        sys.exit(1)
    store.close()

    print(f"Removed source: {args.source_id}")


def _map(args: argparse.Namespace) -> None:
    """Map an external work item to a baton project/task."""
    import json
    from datetime import datetime, timezone

    try:
        from agent_baton.core.storage.central import CentralStore
    except ImportError as exc:
        print(f"error: central storage module unavailable: {exc}")
        sys.exit(1)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    store = CentralStore()
    try:
        # Verify the source exists
        sources = store.query(
            "SELECT source_id FROM external_sources WHERE source_id = ?",
            (args.source_id,),
        )
        if not sources:
            print(f"error: source '{args.source_id}' not found.")
            print("Run 'baton source list' to see registered sources.")
            store.close()
            sys.exit(1)

        store.query(
            "INSERT OR REPLACE INTO external_mappings "
            "(source_id, external_id, project_id, task_id, mapping_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                args.source_id,
                args.external_id,
                args.project_id,
                args.task_id,
                args.mapping_type,
                now,
            ),
        )
    except Exception as exc:
        print(f"error writing mapping: {exc}")
        store.close()
        sys.exit(1)
    store.close()

    print(f"Mapped: {args.source_id}/{args.external_id} -> {args.project_id}/{args.task_id} ({args.mapping_type})")
