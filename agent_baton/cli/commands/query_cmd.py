"""CLI command: ``baton cquery`` — cross-project SQL queries against central.db.

This command targets ``~/.baton/central.db`` exclusively and provides:

- Shortcut names for the cross-project analytics views
- Ad-hoc SQL against any table in central.db
- Schema introspection (``--tables``, ``--table TABLE``)

Relationship to ``baton query``
--------------------------------
``baton query``  — queries the **local** ``baton.db`` for this project's
                   execution history, agent steps, tasks, and patterns.
                   Use it for per-project observability.

``baton cquery`` — queries the **central** ``~/.baton/central.db`` which
                   aggregates data across all projects.  Use it for
                   cross-project analytics and federated views.

Shortcuts
---------
agents      SELECT * FROM v_agent_reliability
costs       SELECT * FROM v_cost_by_task_type
gaps        SELECT * FROM v_recurring_knowledge_gaps
failures    SELECT * FROM v_project_failure_rate
mapping     SELECT * FROM v_external_plan_mapping

Examples
--------
baton cquery "SELECT * FROM v_agent_reliability"
baton cquery agents
baton cquery costs --format json
baton cquery --tables
baton cquery --table executions
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

from agent_baton.cli.formatting import print_table

# ---------------------------------------------------------------------------
# Shortcut → SQL view mapping
# ---------------------------------------------------------------------------

_SHORTCUTS: dict[str, str] = {
    "agents": "SELECT * FROM v_agent_reliability ORDER BY success_rate DESC",
    "costs": "SELECT * FROM v_cost_by_task_type ORDER BY total_tokens DESC",
    "gaps": "SELECT * FROM v_recurring_knowledge_gaps ORDER BY project_count DESC",
    "failures": "SELECT * FROM v_project_failure_rate ORDER BY failure_rate DESC",
    "mapping": "SELECT * FROM v_external_plan_mapping ORDER BY project_id, external_id",
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``cquery`` subcommand."""
    p = subparsers.add_parser(
        "cquery",
        help=(
            "Cross-project SQL queries against central.db (~/.baton/central.db).  "
            "For per-project queries use 'baton query'."
        ),
    )

    # Positional: either a shortcut keyword or a raw SQL string
    p.add_argument(
        "query",
        nargs="?",
        metavar="QUERY",
        help=(
            "SQL statement to execute, or a shortcut name.  "
            "Shortcuts: agents, costs, gaps, failures, mapping.  "
            "Example: baton cquery agents  |  baton cquery \"SELECT * FROM executions\""
        ),
    )

    # Output format
    p.add_argument(
        "--format",
        choices=["table", "json", "csv"],
        default="table",
        metavar="FORMAT",
        help="Output format: table (default), json, csv",
    )

    # Schema introspection
    p.add_argument(
        "--tables",
        action="store_true",
        help="List all tables (and views) in central.db",
    )
    p.add_argument(
        "--table",
        metavar="TABLE",
        dest="table_name",
        help="Describe a specific table: show column names and types",
    )

    # Override the default central.db path (useful for testing)
    p.add_argument(
        "--db",
        metavar="PATH",
        help="Override path to central.db (default: ~/.baton/central.db)",
    )

    return p


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    """Dispatch cquery subcommand."""
    try:
        from agent_baton.core.storage.central import CentralStore
    except ImportError as exc:
        _err(f"central storage module unavailable: {exc}")
        sys.exit(1)

    db_path = Path(args.db) if getattr(args, "db", None) else None
    store = CentralStore(db_path)

    try:
        _dispatch(args, store)
    except Exception as exc:  # noqa: BLE001
        _err(f"query failed: {exc}")
        sys.exit(1)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _dispatch(args: argparse.Namespace, store: object) -> None:
    fmt = getattr(args, "format", "table")

    # ── Schema: list all tables ────────────────────────────────────────────
    if getattr(args, "tables", False):
        _list_tables(store, fmt)
        return

    # ── Schema: describe one table ─────────────────────────────────────────
    if getattr(args, "table_name", None):
        _describe_table(store, args.table_name, fmt)
        return

    # ── Query argument (shortcut or raw SQL) ───────────────────────────────
    query_arg = getattr(args, "query", None)
    if query_arg is None:
        _print_help()
        return

    # Resolve shortcut
    sql = _SHORTCUTS.get(query_arg.lower(), query_arg)
    title = f"Shortcut: {query_arg.lower()}" if query_arg.lower() in _SHORTCUTS else "Query Results"

    try:
        rows = store.query(sql)  # type: ignore[attr-defined]
    except ValueError as exc:
        _err(str(exc))
        return

    if not rows:
        print("(no data)")
        return

    _render(rows, fmt, title=title)


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def _list_tables(store: object, fmt: str) -> None:
    """List all tables and views in central.db."""
    try:
        rows = store.query(  # type: ignore[attr-defined]
            "SELECT name, type FROM sqlite_master "
            "WHERE type IN ('table', 'view') "
            "ORDER BY type DESC, name ASC"
        )
    except Exception as exc:  # noqa: BLE001
        _err(f"could not list tables: {exc}")
        return

    if not rows:
        print("central.db appears to be empty.")
        return

    _render(rows, fmt, title="Tables and Views in central.db")


def _describe_table(store: object, table_name: str, fmt: str) -> None:
    """Show column definitions for a specific table."""
    try:
        # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
        rows = store.query(  # type: ignore[attr-defined]
            f"PRAGMA table_info({table_name})"
        )
    except Exception as exc:  # noqa: BLE001
        _err(f"could not describe table '{table_name}': {exc}")
        return

    if not rows:
        _err(f"table '{table_name}' not found in central.db")
        return

    # Project to a friendlier subset
    display_rows = [
        {
            "column": r["name"],
            "type": r["type"],
            "not_null": "YES" if r["notnull"] else "no",
            "pk": "YES" if r["pk"] else "",
            "default": r["dflt_value"] or "",
        }
        for r in rows
    ]
    _render(display_rows, fmt, title=f"Columns: {table_name}")


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _render(rows: list[dict], fmt: str, title: str = "") -> None:
    if fmt == "json":
        _render_json(rows)
    elif fmt == "csv":
        _render_csv(rows, title)
    else:
        _render_table(rows, title)


def _render_table(rows: list[dict], title: str = "") -> None:
    if not rows:
        print("(no data)")
        return

    if title:
        print(title)

    columns = list(rows[0].keys())
    # Normalize all values to strings for print_table
    str_rows = [{col: str(row.get(col, "") or "") for col in columns} for row in rows]
    # Use column keys as headers (lower-case keys become upper-case display labels
    # via print_table's default behaviour, matching the original output)
    print_table(str_rows, columns=columns)

    print(f"\n{len(rows)} row(s)")


def _render_json(rows: list[dict]) -> None:
    print(json.dumps(rows, indent=2, default=str))


def _render_csv(rows: list[dict], _title: str = "") -> None:
    if not rows:
        return
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    print(buf.getvalue(), end="")


# ---------------------------------------------------------------------------
# Help and error utilities
# ---------------------------------------------------------------------------


def _print_help() -> None:
    print("Usage: baton cquery QUERY [--format FORMAT] [--tables] [--table TABLE]")
    print()
    print("Targets ~/.baton/central.db (cross-project analytics).")
    print("For per-project queries (local baton.db) use: baton query")
    print()
    print("Shortcuts:")
    for name, sql in _SHORTCUTS.items():
        truncated = sql[:60] + "..." if len(sql) > 60 else sql
        print(f"  {name:<12}  {truncated}")
    print()
    print("Examples:")
    print('  baton cquery agents')
    print('  baton cquery costs --format json')
    print('  baton cquery "SELECT * FROM executions LIMIT 10"')
    print('  baton cquery --tables')
    print('  baton cquery --table executions')


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
