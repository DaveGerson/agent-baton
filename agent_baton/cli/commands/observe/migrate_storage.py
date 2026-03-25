"""baton migrate-storage — migrate JSON/JSONL flat files to SQLite database."""
from __future__ import annotations

import argparse
from pathlib import Path


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "migrate-storage",
        help="Migrate JSON/JSONL flat files to SQLite database (baton.db)",
        description=(
            "Scans the team-context directory for existing JSON/JSONL files "
            "and imports them into baton.db. Safe to run multiple times — "
            "all inserts use INSERT OR IGNORE so duplicate records are skipped."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without writing to the database",
    )

    file_handling = p.add_mutually_exclusive_group()
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

    p.add_argument(
        "--team-context",
        default=".claude/team-context",
        metavar="PATH",
        help="Path to team-context directory (default: .claude/team-context)",
    )
    p.add_argument(
        "--verify",
        action="store_true",
        help="After migrating, compare source file counts against DB row counts",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    from agent_baton.core.storage.migrate import StorageMigrator

    context_root = Path(args.team_context).resolve()

    if not context_root.is_dir():
        print(f"Team-context directory not found: {context_root}")
        return

    migrator = StorageMigrator(context_root)

    # 1. Scan and print summary
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

    # 2. Migrate
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

    # 3. Verify (optional)
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _print_counts(counts: dict[str, int]) -> None:
    """Print a counts dict as an aligned two-column table."""
    if not counts:
        return
    col_w = max(len(k) for k in counts) + 2
    for key, value in sorted(counts.items()):
        if value > 0:
            print(f"  {key:<{col_w}} {value:>6}")
    # Also print zeros so the user sees what was scanned
    zero_keys = [k for k, v in counts.items() if v == 0]
    if zero_keys:
        for key in sorted(zero_keys):
            print(f"  {key:<{col_w}} {0:>6}  (none found)")
