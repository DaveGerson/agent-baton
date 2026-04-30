"""CLI command: ``baton release`` -- manage delivery-target Releases (R3.1).

Subcommands
-----------
create   Register a new Release (id, name, optional date).
list     List Releases, optionally filtered by status.
show     Show a Release plus the plans tagged against it.
tag      Tag an existing plan (by ``task_id``) with a release.
untag    Clear a plan's release tag.
notes    Auto-generate release notes for a commit range or release (R3.3).

All subcommands are additive metadata only -- they never affect plan
execution or gating (R3.5 will introduce freeze-period gating later).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent_baton.models.release import RELEASE_STATUSES, Release


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = Path(".claude/team-context/baton.db")


def _get_release_store(create_if_missing: bool = False):
    """Return a ReleaseStore for the active project, or ``None``.

    When ``create_if_missing`` is True, the parent directory is created so
    a fresh project can register releases before any plan/execution exists.
    """
    from agent_baton.core.storage.release_store import ReleaseStore

    db = _DEFAULT_DB_PATH.resolve()
    if not db.exists():
        if not create_if_missing:
            return None
        db.parent.mkdir(parents=True, exist_ok=True)
    return ReleaseStore(db)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``release`` subcommand (cooperative parser)."""
    from agent_baton.cli.commands.release import get_or_create_release_parser
    p, sub = get_or_create_release_parser(
        subparsers,
        help_text="Manage Release entities (delivery targets) and tag plans against them",
    )

    # -- create --------------------------------------------------------------
    create_p = sub.add_parser("create", help="Register a new Release")
    create_p.add_argument(
        "--id",
        dest="release_id",
        metavar="ID",
        required=True,
        help="Release identifier (e.g. v2.5.0 or 2026-Q2-stability)",
    )
    create_p.add_argument(
        "--name",
        dest="name",
        metavar="NAME",
        default="",
        help="Human-friendly name (e.g. 'Q2 Stability Release')",
    )
    create_p.add_argument(
        "--date",
        dest="target_date",
        metavar="YYYY-MM-DD",
        default="",
        help="Target ship date (ISO 8601 date, optional)",
    )
    create_p.add_argument(
        "--status",
        dest="status",
        metavar="STATUS",
        default="planned",
        choices=list(RELEASE_STATUSES),
        help=f"Initial status (one of {', '.join(RELEASE_STATUSES)}; default: planned)",
    )
    create_p.add_argument(
        "--notes",
        dest="notes",
        metavar="TEXT",
        default="",
        help="Free-form notes (themes, scope summary, owners)",
    )

    # -- list ----------------------------------------------------------------
    list_p = sub.add_parser("list", help="List Releases")
    list_p.add_argument(
        "--status",
        dest="status",
        metavar="STATUS",
        default=None,
        choices=list(RELEASE_STATUSES),
        help=f"Filter by status ({', '.join(RELEASE_STATUSES)})",
    )

    # -- show ----------------------------------------------------------------
    show_p = sub.add_parser("show", help="Show a Release and its tagged plans")
    show_p.add_argument(
        "release_id",
        metavar="RELEASE_ID",
        help="Release identifier to show",
    )

    # -- tag -----------------------------------------------------------------
    tag_p = sub.add_parser("tag", help="Tag a plan with a release")
    tag_p.add_argument("plan_id", metavar="PLAN_ID", help="Plan task_id to tag")
    tag_p.add_argument(
        "release_id", metavar="RELEASE_ID", help="Release identifier to tag against"
    )

    # -- untag ---------------------------------------------------------------
    untag_p = sub.add_parser("untag", help="Clear a plan's release tag")
    untag_p.add_argument("plan_id", metavar="PLAN_ID", help="Plan task_id to untag")

    # -- update-status -------------------------------------------------------
    status_p = sub.add_parser(
        "update-status", help="Transition a release to a new lifecycle status"
    )
    status_p.add_argument(
        "release_id", metavar="RELEASE_ID", help="Release identifier"
    )
    status_p.add_argument(
        "new_status",
        metavar="STATUS",
        choices=list(RELEASE_STATUSES),
        help=f"New status ({', '.join(RELEASE_STATUSES)})",
    )

    # -- notes (R3.3) --------------------------------------------------------
    notes_p = sub.add_parser(
        "notes",
        help="Auto-generate release notes for a commit range or release",
    )
    notes_p.add_argument(
        "--release",
        dest="notes_release_id",
        default=None,
        help="Release entity ID (R3.1). Falls back to commit-only mode if unavailable.",
    )
    notes_p.add_argument(
        "--from",
        dest="from_ref",
        default=None,
        help="Git ref to start from (default: master)",
    )
    notes_p.add_argument(
        "--to",
        dest="to_ref",
        default=None,
        help="Git ref to end at (default: HEAD)",
    )
    notes_p.add_argument(
        "--format",
        dest="notes_format",
        choices=("markdown", "html", "json"),
        default="markdown",
        help="Output format (default: markdown)",
    )
    notes_p.add_argument(
        "--output",
        dest="notes_output",
        type=Path,
        default=None,
        help="Write output to PATH instead of stdout",
    )
    notes_p.add_argument(
        "--repo-root",
        dest="repo_root",
        type=Path,
        default=None,
        help="Repository root (default: current working directory)",
    )

    return p


# ---------------------------------------------------------------------------
# Handler dispatch
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    cmd = getattr(args, "release_cmd", None)
    if cmd is None:
        print(
            "Usage: baton release <subcommand>  "
            "[create|list|show|tag|untag|update-status|notes]"
        )
        print("Run `baton release --help` for details.")
        return

    dispatch = {
        "create": _handle_create,
        "list": _handle_list,
        "show": _handle_show,
        "tag": _handle_tag,
        "untag": _handle_untag,
        "update-status": _handle_update_status,
        "notes": _handle_notes,
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
    store = _get_release_store(create_if_missing=True)
    if store is None:  # pragma: no cover -- create_if_missing forces non-None
        print("error: could not open release store", file=sys.stderr)
        sys.exit(1)
    release = Release(
        release_id=args.release_id,
        name=args.name,
        target_date=args.target_date,
        status=args.status,
        notes=args.notes,
    )
    store.create(release)
    date_part = f"  date={args.target_date}" if args.target_date else ""
    print(
        f"Created release {release.release_id} "
        f"({release.status}){date_part}: {release.name or '(no name)'}"
    )


def _handle_list(args: argparse.Namespace) -> None:
    store = _get_release_store()
    if store is None:
        print("No baton.db found in .claude/team-context/ -- no releases to list.")
        return
    releases = store.list(status=args.status)
    if not releases:
        if args.status:
            print(f"No releases with status={args.status}.")
        else:
            print("No releases registered.")
        return
    for r in releases:
        date_part = r.target_date or "----------"
        name_part = r.name or "(no name)"
        print(
            f"{r.release_id:30s}  [{r.status:9s}]  {date_part:12s}  {name_part}"
        )
    print(f"\n{len(releases)} release(s) shown.")


def _handle_show(args: argparse.Namespace) -> None:
    store = _get_release_store()
    if store is None:
        print(
            "No baton.db found in .claude/team-context/ -- no releases to show.",
            file=sys.stderr,
        )
        sys.exit(1)
    release = store.get(args.release_id)
    if release is None:
        print(f"Release not found: {args.release_id}", file=sys.stderr)
        sys.exit(1)
    print(f"Release:     {release.release_id}")
    print(f"Name:        {release.name or '(no name)'}")
    print(f"Status:      {release.status}")
    print(f"Target date: {release.target_date or '(none)'}")
    print(f"Created:     {release.created_at}")
    if release.notes:
        print(f"Notes:       {release.notes}")
    plans = store.list_plans_for_release(release.release_id)
    print()
    if not plans:
        print("No plans tagged against this release.")
        return
    print(f"Tagged plans ({len(plans)}):")
    for p in plans:
        print(
            f"  {p['task_id']:50s}  [{p['risk_level']:6s}]  "
            f"{p['task_summary'][:60]}"
        )


def _handle_tag(args: argparse.Namespace) -> None:
    store = _get_release_store()
    if store is None:
        print(
            "No baton.db found in .claude/team-context/ -- run `baton plan --save` first.",
            file=sys.stderr,
        )
        sys.exit(1)
    if store.get(args.release_id) is None:
        print(
            f"warning: release {args.release_id!r} does not exist; "
            "tagging anyway (create with `baton release create --id ...`).",
            file=sys.stderr,
        )
    ok = store.tag_plan(args.plan_id, args.release_id)
    if not ok:
        print(f"error: plan not found: {args.plan_id}", file=sys.stderr)
        sys.exit(1)
    print(f"Tagged plan {args.plan_id} -> release {args.release_id}")


def _handle_untag(args: argparse.Namespace) -> None:
    store = _get_release_store()
    if store is None:
        print(
            "No baton.db found in .claude/team-context/ -- nothing to untag.",
            file=sys.stderr,
        )
        sys.exit(1)
    ok = store.untag_plan(args.plan_id)
    if not ok:
        print(f"error: plan not found: {args.plan_id}", file=sys.stderr)
        sys.exit(1)
    print(f"Untagged plan {args.plan_id}")


def _handle_update_status(args: argparse.Namespace) -> None:
    store = _get_release_store()
    if store is None:
        print(
            "No baton.db found in .claude/team-context/.",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        ok = store.update_status(args.release_id, args.new_status)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    if not ok:
        print(f"error: release not found: {args.release_id}", file=sys.stderr)
        sys.exit(1)
    print(f"Release {args.release_id} -> status {args.new_status}")


def _handle_notes(args: argparse.Namespace) -> None:
    """Auto-generate release notes for a commit range or release (R3.3)."""
    from agent_baton.core.release.notes import ReleaseNotesBuilder

    builder = ReleaseNotesBuilder(repo_root=args.repo_root)
    notes = builder.build(
        release_id=args.notes_release_id,
        from_ref=args.from_ref,
        to_ref=args.to_ref,
    )

    if args.notes_format == "markdown":
        rendered = notes.to_markdown()
    elif args.notes_format == "html":
        rendered = notes.to_html()
    else:
        rendered = notes.to_json()

    if args.notes_output:
        args.notes_output.parent.mkdir(parents=True, exist_ok=True)
        args.notes_output.write_text(rendered, encoding="utf-8")
        print(f"Wrote release notes to {args.notes_output}")
    else:
        print(rendered)
