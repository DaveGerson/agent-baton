"""``baton release readiness`` — release health dashboard (R3.2).

Computes a ``ReleaseReadinessReport`` for a given release ID and renders it
as a human-readable Markdown table or raw JSON.

Usage::

    baton release readiness <release_id>
    baton release readiness <release_id> --since 14
    baton release readiness <release_id> --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# DB discovery (reused pattern from observe/export.py)
# ---------------------------------------------------------------------------

def _resolve_db_path() -> Path | None:
    """Walk upward from cwd looking for .claude/team-context/baton.db."""
    cwd = Path.cwd()
    for ancestor in [cwd, *cwd.parents]:
        candidate = ancestor / ".claude" / "team-context" / "baton.db"
        if candidate.exists():
            return candidate
    global_path = Path.home() / ".baton" / "baton.db"
    if global_path.exists():
        return global_path
    return None


# ---------------------------------------------------------------------------
# Status badge helper
# ---------------------------------------------------------------------------

_STATUS_BADGE: dict[str, str] = {
    "READY": "[READY]",
    "RISKY": "[RISKY]",
    "BLOCKED": "[BLOCKED]",
}


def _render_markdown(report: "ReleaseReadinessReport") -> str:  # type: ignore[name-defined]  # noqa: F821
    """Render a human-readable Markdown release report."""
    from agent_baton.models.release_readiness import ReleaseReadinessReport  # noqa: F401

    badge = _STATUS_BADGE.get(report.status, report.status)
    lines: list[str] = [
        f"# Release Readiness — {report.release_id}",
        "",
        f"Status: {badge}   Score: {report.score}/100",
        f"Computed at: {report.computed_at}",
        "",
        "## Signal breakdown",
        "",
        "| Signal | Count | Weight | Penalty |",
        "|--------|------:|-------:|--------:|",
    ]

    _weights = {
        "open_warnings": 5,
        "open_critical_beads": 15,
        "failed_gates_7d": 20,
        "incomplete_plans": 10,
        "slo_breaches_7d": 15,
        "escalations": 25,
    }
    _labels = {
        "open_warnings": "Open warnings",
        "open_critical_beads": "Open critical beads",
        "failed_gates_7d": "Failed gates (window)",
        "incomplete_plans": "Incomplete plans",
        "slo_breaches_7d": "SLO breaches (window)",
        "escalations": "Open escalations",
    }

    for field, weight in _weights.items():
        count = getattr(report, field)
        penalty = count * weight
        label = _labels[field]
        lines.append(f"| {label} | {count} | {weight} | {penalty} |")

    lines += [
        "",
        f"**Total penalty**: {100 - report.score}   **Score**: {report.score}/100",
        "",
    ]

    # Breakdown detail
    if report.breakdown:
        lines.append("## Top items")
        lines.append("")
        for category, items in report.breakdown.items():
            lines.append(f"### {category.replace('_', ' ').title()}")
            lines.append("")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "release",
        help="Release management — readiness dashboard and release health",
    )
    release_sub = p.add_subparsers(dest="release_subcommand")

    rr = release_sub.add_parser(
        "readiness",
        help="Compute a release readiness report for a given release ID",
    )
    rr.add_argument("release_id", help="Release identifier to evaluate")
    rr.add_argument(
        "--since",
        type=int,
        default=7,
        metavar="DAYS",
        help="Lookback window in days for time-bounded signals (default: 7)",
    )
    rr.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit report as JSON instead of Markdown",
    )
    rr.add_argument(
        "--db",
        type=Path,
        default=None,
        metavar="PATH",
        help="Explicit path to baton.db (auto-discovered if omitted)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    release_subcmd = getattr(args, "release_subcommand", None)
    if release_subcmd is None:
        print("Usage: baton release readiness <release_id> [--json] [--since DAYS]")
        sys.exit(1)

    if release_subcmd == "readiness":
        _handle_readiness(args)
    else:
        print(f"Unknown release subcommand: {release_subcmd}", file=sys.stderr)
        sys.exit(1)


def _handle_readiness(args: argparse.Namespace) -> None:
    from agent_baton.core.release.readiness import ReleaseReadinessChecker

    db_path: Path | None = getattr(args, "db", None) or _resolve_db_path()

    if db_path is None:
        print(
            "error: no baton.db found. Run from a project directory or pass --db.",
            file=sys.stderr,
        )
        sys.exit(1)

    checker = ReleaseReadinessChecker(db_path)
    report = checker.compute(args.release_id, since_days=args.since)

    if args.as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(_render_markdown(report))
