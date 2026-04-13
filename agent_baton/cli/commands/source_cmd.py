"""CLI command: ``baton source`` — manage external work-item source connections.

Subcommands
-----------
add         Register an external source (ADO, GitHub, Jira, Linear).
list        List all registered external sources.
sync        Pull work items from a source into central.db.
remove      Remove a registered external source.
map         Map an external item to a baton project/task.

Supported adapters: ADO, GitHub, Jira, Linear.  Each adapter module lives in
``agent_baton/core/storage/adapters/`` and self-registers via
``AdapterRegistry.register()`` on import.
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
        help="Manage external work-item source connections (ado, github, jira, linear)",
    )
    sub = p.add_subparsers(dest="subcommand")

    # baton source add ado --name NAME --org ORG --project PROJ --pat-env ENV
    p_add = sub.add_parser("add", help="Register an external source connection")
    p_add.add_argument(
        "source_type",
        metavar="TYPE",
        help="Source type: ado, github, jira, linear",
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

    _IMPLEMENTED_TYPES = {"ado", "github", "jira", "linear"}
    if args.source_type not in _IMPLEMENTED_TYPES:
        print(f"error: unknown source type '{args.source_type}'")
        print(f"supported types: {', '.join(sorted(_IMPLEMENTED_TYPES))}")
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
        store.execute(
            "INSERT OR REPLACE INTO external_sources "
            "(source_id, source_type, display_name, config, enabled) "
            "VALUES (?, ?, ?, ?, 1)",
            (source_id, args.source_type, args.name, json.dumps(config)),
        )
    except Exception as exc:
        msg = str(exc)
        if "UNIQUE constraint" in msg:
            print(f"error: source '{source_id}' is already registered.")
            print(f"  Update it by removing first: baton source remove {source_id}")
        else:
            print(f"error registering source: {exc}")
            print("  Check that central.db is writable and not locked.")
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


def _list(_args: argparse.Namespace) -> None:
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
        msg = str(exc)
        if "no such table" in msg:
            print("error: external_sources table not found in central.db.")
            print("  This may indicate an older database version. Run: baton migrate-storage")
        else:
            print(f"error reading external_sources: {exc}")
            print("  Check that central.db exists and is readable.")
        store.close()
        sys.exit(1)
    store.close()

    if not rows:
        print("No external sources registered.")
        print("Add one with: baton source add <type> --name NAME ...")
        print("Supported types: ado, github, jira, linear")
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
        msg = str(exc)
        if "no such table" in msg:
            print("error: external_sources table not found in central.db.")
            print("  Run: baton migrate-storage")
        else:
            print(f"error reading external_sources: {exc}")
            print("  Check that central.db exists and is readable.")
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

    # Load adapters — each import triggers self-registration side effects.
    AdapterRegistry = None  # type: ignore[assignment]
    try:
        from agent_baton.core.storage.adapters import AdapterRegistry
        import agent_baton.core.storage.adapters.ado  # noqa: F401  # type: ignore[import]
        import agent_baton.core.storage.adapters.github  # noqa: F401  # type: ignore[import]
        import agent_baton.core.storage.adapters.jira  # noqa: F401  # type: ignore[import]
        import agent_baton.core.storage.adapters.linear  # noqa: F401  # type: ignore[import]
    except ImportError:
        pass

    try:
        available = AdapterRegistry.available() if AdapterRegistry is not None else []
    except Exception:
        available = []

    store2 = CentralStore()
    for row in sources:
        source_id = row["source_id"]
        source_type = row["source_type"]
        if source_type not in available:
            print(f"  {source_id}: No adapter available for source type '{source_type}'.")
            continue

        adapter_cls = AdapterRegistry.get(source_type)
        if adapter_cls is None:
            print(f"  {source_id}: Adapter class missing for '{source_type}'.")
            continue

        # Load config from central.db
        config_rows = store2.query(
            "SELECT config FROM external_sources WHERE source_id = ?",
            (source_id,),
        )
        if not config_rows:
            print(f"  {source_id}: Source not found.")
            continue

        import json as _json
        raw_config = _json.loads(config_rows[0]["config"])
        # Normalise config keys to match each adapter's expectations.
        # The stored config uses generic CLI key names (org, project,
        # pat_env, url); adapters consume source-type-specific names.
        if source_type == "ado":
            config = {
                "organization": raw_config.get("org", ""),
                "project": raw_config.get("project", ""),
                "pat_env_var": raw_config.get("pat_env", "ADO_PAT"),
                "url": raw_config.get("url", ""),
            }
        elif source_type == "github":
            config = {
                "owner": raw_config.get("org", ""),
                "repo": raw_config.get("project", ""),
                "token_env_var": raw_config.get("pat_env", "GITHUB_TOKEN"),
            }
        elif source_type == "jira":
            config = {
                "url": raw_config.get("url", ""),
                "project": raw_config.get("project", ""),
                "email": raw_config.get("org", ""),  # --org used for email
                "token_env_var": raw_config.get("pat_env", "JIRA_API_TOKEN"),
            }
        elif source_type == "linear":
            config = {
                "team_key": raw_config.get("project", ""),
                "token_env_var": raw_config.get("pat_env", "LINEAR_API_KEY"),
            }
        else:
            config = dict(raw_config)

        adapter = adapter_cls()
        try:
            adapter.connect(config)
        except ValueError as exc:
            print(f"  {source_id}: Connection failed — {exc}")
            continue
        except Exception as exc:
            print(f"  {source_id}: Unexpected connection error — {exc}")
            continue

        try:
            items = adapter.fetch_items()
        except Exception as exc:
            print(f"  {source_id}: Fetch failed — {exc}")
            continue

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        import json as _json2

        persisted = 0
        for item in items:
            try:
                store2.execute(
                    "INSERT OR REPLACE INTO external_items "
                    "(source_id, external_id, item_type, title, description, "
                    "state, assigned_to, priority, parent_id, tags, url, "
                    "raw_data, fetched_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item.source_id,
                        item.external_id,
                        item.item_type,
                        item.title,
                        item.description,
                        item.state,
                        item.assigned_to,
                        str(item.priority),
                        item.parent_id,
                        _json2.dumps(item.tags or []),
                        item.url,
                        _json2.dumps(item.raw_data or {}),
                        now,
                        item.updated_at,
                    ),
                )
                persisted += 1
            except Exception:
                pass  # Don't abort the whole sync for one bad row

        # Update last_synced timestamp
        try:
            store2.execute(
                "UPDATE external_sources SET last_synced = ? WHERE source_id = ?",
                (now, source_id),
            )
        except Exception:
            pass

        print(f"  {source_id}: Synced {persisted} item(s).")

    store2.close()


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
        store.execute(
            "DELETE FROM external_sources WHERE source_id = ?",
            (args.source_id,),
        )
    except Exception as exc:
        msg = str(exc)
        if "no such table" in msg:
            print("error: external_sources table not found. Run: baton migrate-storage")
        else:
            print(f"error removing source: {exc}")
            print("  Check that central.db is writable and not locked.")
        store.close()
        sys.exit(1)
    store.close()

    print(f"Removed source: {args.source_id}")


def _map(args: argparse.Namespace) -> None:
    """Map an external work item to a baton project/task."""
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

        store.execute(
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
        msg = str(exc)
        if "UNIQUE constraint" in msg:
            print(f"error: mapping already exists for {args.source_id}/{args.external_id}")
            print("  The existing mapping has been replaced (INSERT OR REPLACE).")
        elif "no such table" in msg:
            print("error: external_mappings table not found. Run: baton migrate-storage")
        else:
            print(f"error writing mapping: {exc}")
            print("  Check that central.db is writable and not locked.")
        store.close()
        sys.exit(1)
    store.close()

    print(f"Mapped: {args.source_id}/{args.external_id} -> {args.project_id}/{args.task_id} ({args.mapping_type})")
