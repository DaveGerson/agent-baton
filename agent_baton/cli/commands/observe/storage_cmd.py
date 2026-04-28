"""``baton storage`` -- storage management subcommands.

Subcommands:
    preflight   Pre-migration safety check (schema versions + backups).
    migrate     Migrate JSON/JSONL flat files to SQLite (baton.db).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "storage",
        help="Storage management subcommands",
        description=(
            "Storage management for Agent Baton: pre-migration safety checks "
            "and JSON/JSONL → SQLite migration."
        ),
    )
    sub = p.add_subparsers(dest="storage_command", metavar="SUBCOMMAND")
    sub.required = True

    # ---- preflight ---------------------------------------------------------
    pf = sub.add_parser(
        "preflight",
        help="Check schema versions and create backups before a migration",
    )
    pf.add_argument(
        "--context",
        default=".claude/team-context",
        metavar="PATH",
        help="Path to team-context directory (default: .claude/team-context)",
    )
    pf.add_argument(
        "--no-backup",
        action="store_true",
        help="Report only — skip backup creation",
    )

    # ---- migrate -----------------------------------------------------------
    migrate_p = sub.add_parser(
        "migrate",
        help="Migrate JSON/JSONL flat files to SQLite database (baton.db)",
        description=(
            "Scans the team-context directory for existing JSON/JSONL files "
            "and imports them into baton.db. Safe to run multiple times — "
            "all inserts use INSERT OR IGNORE so duplicate records are skipped."
        ),
    )
    migrate_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without writing to the database",
    )
    file_handling = migrate_p.add_mutually_exclusive_group()
    file_handling.add_argument(
        "--keep-files",
        action="store_true",
        default=True,
        help="Keep original files after migration (default)",
    )
    file_handling.add_argument(
        "--remove-files",
        action="store_true",
        default=False,
        help="Move original files to pre-sqlite-backup/ after successful import",
    )
    migrate_p.add_argument(
        "--team-context",
        default=".claude/team-context",
        metavar="PATH",
        help="Path to team-context directory (default: .claude/team-context)",
    )
    migrate_p.add_argument(
        "--verify",
        action="store_true",
        help="After migrating, compare source file counts against DB row counts",
    )

    return p


def handler(args: argparse.Namespace) -> None:
    cmd = getattr(args, "storage_command", None)
    if cmd == "preflight":
        _preflight(args)
    elif cmd == "migrate":
        _cmd_migrate(args)
    else:
        print(f"Unknown storage subcommand: {cmd}", file=sys.stderr)
        print("Subcommands: preflight, migrate", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Preflight implementation
# ---------------------------------------------------------------------------

def _preflight(args: argparse.Namespace) -> None:
    from agent_baton.core.storage.migration_backup import backup_db
    from agent_baton.core.storage.schema import SCHEMA_VERSION

    context_root = Path(args.context).resolve()
    baton_db = context_root / "baton.db"
    central_db = Path.home() / ".baton" / "central.db"

    print("baton storage preflight")
    print("=" * 60)
    print(f"Current engine SCHEMA_VERSION : {SCHEMA_VERSION}")
    print()

    errors: list[str] = []

    for label, db_path in [("baton.db", baton_db), ("central.db", central_db)]:
        print(f"[{label}]")
        print(f"  path      : {db_path}")

        if not db_path.exists():
            print("  status    : NOT FOUND (will be created on first use)")
            print()
            continue

        size_bytes = db_path.stat().st_size
        size_kb = size_bytes / 1024
        print(f"  size      : {size_kb:.1f} KB ({size_bytes} bytes)")

        mtime = db_path.stat().st_mtime
        mtime_dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
        print(f"  modified  : {mtime_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}")

        db_version = _read_schema_version(db_path)
        print(f"  db version: {db_version}")

        if int(db_version) > SCHEMA_VERSION:
            msg = (
                f"  WARNING: {label} is at v{db_version}, "
                f"engine expects v{SCHEMA_VERSION}. Downgrade risk."
            )
            print(msg)
            errors.append(msg)

        if not args.no_backup:
            try:
                bak = backup_db(db_path)
                print(f"  backup    : {bak}")
            except Exception as exc:
                msg = f"  ERROR: backup failed for {label}: {exc}"
                print(msg)
                errors.append(msg)

        print()

    if errors:
        print("Preflight completed with warnings/errors:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    else:
        print("Preflight OK — safe to proceed with migration.")
        sys.exit(0)


def _read_schema_version(db_path: Path) -> str:
    """Return the integer schema version stored in *db_path*, or '0'."""
    import sqlite3

    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            row = conn.execute(
                "SELECT version FROM _schema_version LIMIT 1"
            ).fetchone()
            return str(row[0]) if row else "0"
        except sqlite3.OperationalError:
            return "0"
        finally:
            conn.close()
    except Exception:
        return "0"


# ---------------------------------------------------------------------------
# Migrate implementation
# ---------------------------------------------------------------------------

def _cmd_migrate(args: argparse.Namespace) -> None:
    """Shared implementation called by both 'baton storage migrate' and the shim."""
    from agent_baton.core.storage.migrate import StorageMigrator

    context_root = Path(args.team_context).resolve()

    if not context_root.is_dir():
        print(f"Team-context directory not found: {context_root}")
        return

    migrator = StorageMigrator(context_root)

    scan_counts = migrator.scan()
    total_source = sum(scan_counts.values())

    print(f"Team-context: {context_root}")
    print()

    if total_source == 0:
        print("Nothing to migrate — no JSON/JSONL source files found.")
        return

    print("Source files found:")
    _print_counts(scan_counts)

    if args.dry_run:
        print()
        print("(dry run — no changes made)")
        return

    print()
    print("Migrating...")
    keep_files = not args.remove_files
    imported = migrator.migrate(dry_run=False, keep_files=keep_files)

    total_imported = sum(imported.values())
    print()
    print(f"Imported {total_imported} record(s):")
    _print_counts(imported)

    if not keep_files:
        print()
        print(f"Original files archived to: {context_root / 'pre-sqlite-backup'}/")

    if args.verify:
        print()
        print("Verifying migration...")
        verification = migrator.verify()
        all_match = True
        rows: list[tuple[str, int, int, str]] = []

        for category, (src, db) in sorted(verification.items()):
            status = "OK" if src == db else "MISMATCH"
            if status == "MISMATCH":
                all_match = False
            rows.append((category, src, db, status))

        from agent_baton.cli.formatting import print_table

        verify_rows = [
            {"category": category, "source": str(src), "db": str(db), "status": status}
            for category, src, db, status in rows
        ]
        print_table(
            verify_rows,
            columns=["category", "source", "db", "status"],
            headers={"category": "Category", "source": "Source", "db": "DB", "status": "Status"},
            alignments={"source": ">", "db": ">"},
            prefix="  ",
        )

        print()
        if all_match:
            print("All counts match — migration verified.")
        else:
            print("WARNING: count mismatches detected. Check logs for skipped records.")


def _print_counts(counts: dict[str, int]) -> None:
    if not counts:
        return
    col_w = max(len(k) for k in counts) + 2
    for key, value in sorted(counts.items()):
        if value > 0:
            print(f"  {key:<{col_w}} {value:>6}")
    zero_keys = [k for k, v in counts.items() if v == 0]
    if zero_keys:
        for key in sorted(zero_keys):
            print(f"  {key:<{col_w}} {0:>6}  (none found)")
