"""CLI command: ``baton sync`` — federated sync from project baton.db to central.db.

Subcommands
-----------
(default)   Sync the current project (auto-detect from cwd).
status      Show sync watermarks for all projects.

Flags
-----
--all           Sync all registered projects.
--project ID    Sync a specific project by ID.
--rebuild       Full rebuild (delete + re-sync).
"""
from __future__ import annotations

import argparse
import sys


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``sync`` subcommand."""
    p = subparsers.add_parser(
        "sync",
        help="Sync project data to central.db",
    )
    p.add_argument(
        "subcommand",
        nargs="?",
        default=None,
        metavar="SUBCOMMAND",
        help="Optional subcommand: status",
    )
    p.add_argument(
        "--all",
        action="store_true",
        dest="sync_all",
        help="Sync all registered projects",
    )
    p.add_argument(
        "--project",
        metavar="ID",
        default=None,
        help="Sync a specific project by ID",
    )
    p.add_argument(
        "--rebuild",
        action="store_true",
        help="Full rebuild (delete all central rows then re-sync)",
    )
    return p


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    if args.subcommand == "status":
        _status(args)
    elif args.sync_all:
        _sync_all(args)
    elif args.project:
        _sync_project(args)
    else:
        _sync_current(args)


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def _status(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Show sync watermarks from central.db."""
    try:
        from agent_baton.core.storage.central import CentralStore
    except ImportError as exc:
        print(f"error: central storage module unavailable: {exc}")
        sys.exit(1)

    store = CentralStore()
    try:
        watermarks = store.query(
            "SELECT project_id, table_name, last_rowid, last_synced "
            "FROM sync_watermarks "
            "ORDER BY project_id, table_name"
        )
    except Exception as exc:
        print(f"error reading sync_watermarks: {exc}")
        sys.exit(1)
    finally:
        store.close()

    if not watermarks:
        print("No sync watermarks found.")
        print("Run 'baton sync' to sync the current project first.")
        return

    print(f"Sync Watermarks ({len(watermarks)} entries)")
    print()

    # Group by project_id for display
    current_project = None
    for row in watermarks:
        if row["project_id"] != current_project:
            current_project = row["project_id"]
            print(f"  Project: {current_project}")
        last_synced = row["last_synced"] or "(never)"
        print(f"    {row['table_name']:<30}  rowid={row['last_rowid']:<8}  {last_synced}")


def _sync_all(args: argparse.Namespace) -> None:
    """Sync all registered projects to central.db."""
    try:
        from agent_baton.core.storage.sync import SyncEngine
    except ImportError as exc:
        print(f"error: sync module unavailable: {exc}")
        sys.exit(1)

    engine = SyncEngine()

    if args.rebuild:
        # Rebuild requires iterating projects and calling rebuild() on each
        try:
            from agent_baton.core.storage.central import CentralStore
            store = CentralStore()
            projects = store.query("SELECT project_id, path FROM projects ORDER BY project_id")
            store.close()
        except Exception as exc:
            print(f"error reading projects from central.db: {exc}")
            sys.exit(1)

        if not projects:
            print("No projects registered in central.db.")
            print("Register with 'baton pmo add' first.")
            return

        print(f"Rebuilding {len(projects)} project(s)...")
        failed = 0
        for row in projects:
            from pathlib import Path
            db_path = Path(row["path"]) / ".claude" / "team-context" / "baton.db"
            result = engine.rebuild(row["project_id"], db_path)
            status = "OK" if result.success else "FAILED"
            print(f"  {result.project_id}: {result.rows_synced} rows ({status})")
            if not result.success:
                failed += 1
                for err in result.errors:
                    print(f"    error: {err}")
        if failed:
            sys.exit(1)
    else:
        results = engine.push_all()
        if not results:
            print("No projects registered in central.db.")
            print("Register with 'baton pmo add' first.")
            return
        failed = 0
        for r in results:
            status = "OK" if r.success else "FAILED"
            print(f"  {r.project_id}: {r.rows_synced} rows ({status})")
            if not r.success:
                failed += 1
                for err in r.errors:
                    print(f"    error: {err}")
        if failed:
            sys.exit(1)


def _sync_project(args: argparse.Namespace) -> None:
    """Sync a single project by ID."""
    from pathlib import Path

    try:
        from agent_baton.core.storage.sync import SyncEngine
        from agent_baton.core.storage.central import CentralStore
    except ImportError as exc:
        print(f"error: sync module unavailable: {exc}")
        sys.exit(1)

    # Look up the project path from central.db
    store = CentralStore()
    try:
        rows = store.query(
            "SELECT path FROM projects WHERE project_id = ?",
            (args.project,),
        )
    except Exception as exc:
        print(f"error reading project from central.db: {exc}")
        store.close()
        sys.exit(1)
    store.close()

    if not rows:
        print(f"error: project '{args.project}' not found in central.db.")
        print("Register with 'baton pmo add' first.")
        sys.exit(1)

    project_path = Path(rows[0]["path"])
    db_path = project_path / ".claude" / "team-context" / "baton.db"

    engine = SyncEngine()
    if args.rebuild:
        result = engine.rebuild(args.project, db_path)
    else:
        result = engine.push(args.project, db_path)

    status = "OK" if result.success else "FAILED"
    print(f"Synced {result.project_id}: {result.rows_synced} rows ({status})")
    if not result.success:
        for err in result.errors:
            print(f"  error: {err}")
        sys.exit(1)


def _sync_current(args: argparse.Namespace) -> None:
    """Sync the current project (auto-detect from cwd)."""
    try:
        from agent_baton.core.storage.sync import SyncEngine, auto_sync_current_project
    except ImportError as exc:
        print(f"error: sync module unavailable: {exc}")
        sys.exit(1)

    if args.rebuild:
        # For rebuild, we need the project_id and db_path — can't use the simple helper
        from pathlib import Path
        import os
        cwd = Path(os.getcwd()).resolve()

        try:
            from agent_baton.core.storage.central import CentralStore
            store = CentralStore()
            rows = store.query("SELECT project_id, path FROM projects ORDER BY project_id")
            store.close()
        except Exception as exc:
            print(f"error reading projects from central.db: {exc}")
            sys.exit(1)

        matched = None
        for row in rows:
            try:
                proj_path = Path(row["path"]).resolve()
                if str(cwd).startswith(str(proj_path)):
                    matched = row
                    break
            except Exception:
                continue

        if matched is None:
            print("Could not detect current project. Register with 'baton pmo add' first.")
            sys.exit(1)

        db_path = Path(matched["path"]) / ".claude" / "team-context" / "baton.db"
        engine = SyncEngine()
        result = engine.rebuild(matched["project_id"], db_path)
        status = "OK" if result.success else "FAILED"
        print(f"Rebuilt {result.project_id}: {result.rows_synced} rows ({status})")
        if not result.success:
            for err in result.errors:
                print(f"  error: {err}")
            sys.exit(1)
    else:
        result = auto_sync_current_project()
        if result is None:
            print("Could not detect current project. Register with 'baton pmo add' first.")
            return
        if result.rows_synced > 0:
            print(f"Synced {result.rows_synced} rows to central.db")
        else:
            print("Already up to date.")
        if not result.success:
            for err in result.errors:
                print(f"  error: {err}")
            sys.exit(1)
