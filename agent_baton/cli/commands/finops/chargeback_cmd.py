"""``baton finops chargeback`` -- token / dollar attribution exports (O1.2).

Builds a CSV or JSON report of token + USD spend grouped by org, team,
project, user, or cost_center for a configurable time window.  Pure
read-side over the F0.2 ``usage_records`` + ``agent_usage`` tables --
no schema mutations.

Examples
--------
::

    baton finops chargeback
    baton finops chargeback --since 2026-01-01 --group-by team
    baton finops chargeback --format json --output spend.json
    baton finops chargeback --db ~/.baton/central.db --group-by project
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Argparse plumbing
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``baton finops`` command group + ``chargeback`` subcommand.

    Even though there is currently a single subcommand, we keep the
    ``finops`` group level so future read-side reports
    (``baton finops budget``, ``baton finops anomaly``) slot in cleanly.
    """
    p = subparsers.add_parser(
        "finops",
        help="FinOps cost reporting (chargeback / showback)",
        description="Read-side cost reporting over the F0.2 tenancy hierarchy.",
    )
    sub = p.add_subparsers(dest="finops_cmd", metavar="SUBCOMMAND")
    sub.required = True

    cb = sub.add_parser(
        "chargeback",
        help="Export token + USD spend attribution as CSV or JSON",
    )
    # ------------------------------------------------------------------
    # attribution-coverage subcommand
    # ------------------------------------------------------------------
    ac = sub.add_parser(
        "attribution-coverage",
        help="Report percentage of usage_records rows above the default attribution bucket",
    )
    ac.add_argument(
        "--output",
        choices=["table", "json"],
        default="table",
        dest="ac_output",
        help="Output format (default: table).",
    )
    ac.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help=(
            "SQLite database path.  Defaults to BATON_DB_PATH, then the "
            "project baton.db (.claude/team-context/baton.db, walking up), "
            "then ~/.baton/central.db."
        ),
    )

    # ------------------------------------------------------------------
    # chargeback args (continued)
    # ------------------------------------------------------------------
    cb.add_argument(
        "--since",
        metavar="DATE",
        default=None,
        help="ISO-8601 date or timestamp (default: 30 days ago).",
    )
    cb.add_argument(
        "--until",
        metavar="DATE",
        default=None,
        help="ISO-8601 date or timestamp (default: now).",
    )
    cb.add_argument(
        "--group-by",
        choices=["org", "team", "project", "user", "cost_center"],
        default="project",
        help="Aggregation scope (default: project).",
    )
    cb.add_argument(
        "--format",
        choices=["csv", "json"],
        default="csv",
        dest="output_format",
        help="Output format (default: csv).",
    )
    cb.add_argument(
        "--output",
        metavar="PATH",
        default=None,
        help="Write report to PATH instead of stdout.",
    )
    cb.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help=(
            "SQLite database path.  Defaults to BATON_DB_PATH, then the "
            "project baton.db (.claude/team-context/baton.db, walking up), "
            "then ~/.baton/central.db."
        ),
    )

    return p


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(args: argparse.Namespace) -> None:
    """Dispatch ``baton finops <subcommand>``."""
    sub = getattr(args, "finops_cmd", None)
    if sub == "chargeback":
        _handle_chargeback(args)
    elif sub == "attribution-coverage":
        _handle_attribution_coverage(args)
    else:  # pragma: no cover - argparse prevents this
        raise SystemExit(f"Unknown finops subcommand: {sub!r}")


def _handle_chargeback(args: argparse.Namespace) -> None:
    from agent_baton.core.observability.chargeback import ChargebackBuilder

    db_path = _resolve_db_path(getattr(args, "db", None))
    if db_path is None or not db_path.exists():
        sys.stderr.write(
            "ERROR: no baton.db / central.db found.  "
            "Set BATON_DB_PATH or pass --db PATH.\n"
        )
        raise SystemExit(2)

    builder = ChargebackBuilder(db_path=db_path)
    report = builder.build(
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        group_by=getattr(args, "group_by", "project"),
    )

    if args.output_format == "csv":
        rendered = report.to_csv()
    else:
        rendered = report.to_json()

    out = getattr(args, "output", None)
    if out:
        out_path = Path(out).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered, encoding="utf-8")
        sys.stderr.write(
            f"Wrote {len(report.rows)} rows to {out_path} "
            f"(group_by={report.group_by}, period={report.period_start} .. {report.period_end})\n"
        )
    else:
        sys.stdout.write(rendered)
        if rendered and not rendered.endswith("\n"):
            sys.stdout.write("\n")


def _handle_attribution_coverage(args: argparse.Namespace) -> None:
    from agent_baton.core.observability.attribution_coverage import CoverageScanner

    db_path = _resolve_db_path(getattr(args, "db", None))
    if db_path is None or not db_path.exists():
        sys.stderr.write(
            "ERROR: no baton.db / central.db found.  "
            "Set BATON_DB_PATH or pass --db PATH.\n"
        )
        raise SystemExit(2)

    scanner = CoverageScanner(db_path=db_path)
    report = scanner.scan()

    output_fmt = getattr(args, "ac_output", "table")
    if output_fmt == "json":
        rendered = report.to_json()
    else:
        rendered = report.to_table()

    sys.stdout.write(rendered)
    if rendered and not rendered.endswith("\n"):
        sys.stdout.write("\n")


# ---------------------------------------------------------------------------
# DB discovery
# ---------------------------------------------------------------------------

_DB_REL = Path(".claude/team-context/baton.db")
_CENTRAL_DB = Path.home() / ".baton" / "central.db"


def _resolve_db_path(override: str | None) -> Path | None:
    """Resolve the baton DB to query.

    Resolution order:
      1. ``--db PATH`` (CLI flag)
      2. ``BATON_DB_PATH`` env var
      3. ``.claude/team-context/baton.db`` in the current cwd
      4. Walk parents for the same path (worktree-friendly)
      5. ``~/.baton/central.db`` if it exists
    """
    if override:
        return Path(override).expanduser().resolve()

    env = os.environ.get("BATON_DB_PATH", "").strip()
    if env:
        return Path(env).expanduser().resolve()

    cwd_default = (Path.cwd() / _DB_REL).resolve()
    if cwd_default.exists():
        return cwd_default

    current = Path.cwd().resolve()
    for parent in current.parents:
        candidate = parent / _DB_REL
        if candidate.exists():
            return candidate

    if _CENTRAL_DB.exists():
        return _CENTRAL_DB

    return None
