"""``baton spec`` — first-class Spec entity CLI (F0.1).

Subcommands
-----------
create      Create a new spec (optionally from a template)
list        List specs with optional filters
show        Show a spec's full content
approve     Approve a draft/reviewed spec
link        Link a spec to a plan task ID
score       Record a scorecard on a spec
import      Import a spec from a JSON file
export      Export a spec to JSON
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_store(args: argparse.Namespace):
    from agent_baton.core.specs.store import SpecStore

    db = getattr(args, "db", None)
    return SpecStore(db_path=Path(db) if db else None)


def _print_spec_summary(spec) -> None:
    print(
        f"  [{spec.state:10s}] {spec.spec_id[:8]}  {spec.title or '(no title)'}"
        f"  author={spec.author_id}  updated={spec.updated_at[:10]}"
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser(
        "spec",
        help="Manage first-class Spec entities (F0.1)",
    )
    p.add_argument(
        "--db", metavar="PATH", default=None,
        help="Path to central.db (default: ~/.baton/central.db)",
    )
    sub = p.add_subparsers(dest="spec_cmd", metavar="SUBCOMMAND")
    sub.required = True

    # -- create --------------------------------------------------------------
    pc = sub.add_parser("create", help="Create a new spec")
    pc.add_argument("--title", required=True, help="Short spec title")
    pc.add_argument("--template", metavar="NAME", default="feature",
                    help="Template name (feature|bug-fix|refactor|migration|...)")
    pc.add_argument("--task-type", default="", help="Task type hint")
    pc.add_argument("--author", default="local-user", help="Author identity")
    pc.add_argument("--project", default="default", help="Project ID")
    pc.add_argument("--content-file", metavar="PATH", default=None,
                    help="Path to YAML file with spec body")

    # -- list ----------------------------------------------------------------
    pl = sub.add_parser("list", help="List specs")
    pl.add_argument("--state", default=None, help="Filter by state")
    pl.add_argument("--project", default=None, help="Filter by project")
    pl.add_argument("--author", default=None, help="Filter by author")
    pl.add_argument("--limit", type=int, default=20, help="Max results")
    pl.add_argument("--json", dest="output_json", action="store_true",
                    help="Machine-readable JSON output")

    # -- show ----------------------------------------------------------------
    ps = sub.add_parser("show", help="Show a spec in full")
    ps.add_argument("spec_id", help="Spec ID")
    ps.add_argument("--json", dest="output_json", action="store_true")

    # -- approve -------------------------------------------------------------
    pa = sub.add_parser("approve", help="Approve a spec")
    pa.add_argument("spec_id", help="Spec ID")
    pa.add_argument("--actor", default="local-user", help="Approver identity")

    # -- link ----------------------------------------------------------------
    pk = sub.add_parser("link", help="Link spec to a plan task ID")
    pk.add_argument("spec_id", help="Spec ID")
    pk.add_argument("task_id", help="Plan task ID to link")
    pk.add_argument("--project", default="default", help="Project context")

    # -- score ---------------------------------------------------------------
    psc = sub.add_parser("score", help="Record a scorecard on a spec")
    psc.add_argument("spec_id", help="Spec ID")
    psc.add_argument("--scorecard", required=True,
                     help="JSON dict of dimension→score, e.g. '{\"clarity\":0.9}'")

    # -- import --------------------------------------------------------------
    pi = sub.add_parser("import", help="Import a spec from a JSON file")
    pi.add_argument("file", help="Path to JSON export file")
    pi.add_argument("--overwrite", action="store_true",
                    help="Overwrite existing spec with same ID")

    # -- export --------------------------------------------------------------
    pe = sub.add_parser("export", help="Export a spec to JSON")
    pe.add_argument("spec_id", help="Spec ID")
    pe.add_argument("--out", metavar="PATH", default=None,
                    help="Output file (default: stdout)")

    return p


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(args: argparse.Namespace) -> None:
    store = _get_store(args)
    cmd = args.spec_cmd

    if cmd == "create":
        content = ""
        if args.content_file:
            content = Path(args.content_file).read_text(encoding="utf-8")
        elif args.template:
            tpl_path = (
                Path(__file__).parents[5] / "templates" / "specs"
                / f"{args.template}.yaml"
            )
            if tpl_path.exists():
                content = tpl_path.read_text(encoding="utf-8")
        spec = store.create(
            title=args.title,
            content=content,
            task_type=getattr(args, "task_type", ""),
            template_id=args.template,
            author_id=args.author,
            project_id=args.project,
        )
        print(f"Created spec {spec.spec_id}")
        _print_spec_summary(spec)

    elif cmd == "list":
        specs = store.list(
            project_id=getattr(args, "project", None),
            state=args.state,
            author_id=getattr(args, "author", None),
            limit=args.limit,
        )
        if getattr(args, "output_json", False):
            print(json.dumps([s.to_dict() for s in specs], indent=2))
            return
        if not specs:
            print("No specs found.")
            return
        print(f"Specs ({len(specs)}):")
        for s in specs:
            _print_spec_summary(s)

    elif cmd == "show":
        spec = store.get(args.spec_id)
        if spec is None:
            print(f"Spec not found: {args.spec_id}")
            sys.exit(1)
        if getattr(args, "output_json", False):
            print(json.dumps(spec.to_dict(), indent=2))
            return
        print(f"Spec: {spec.spec_id}")
        print(f"  Title:    {spec.title}")
        print(f"  State:    {spec.state}")
        print(f"  Author:   {spec.author_id}")
        print(f"  Template: {spec.template_id}")
        print(f"  Type:     {spec.task_type}")
        print(f"  Created:  {spec.created_at}")
        print(f"  Updated:  {spec.updated_at}")
        if spec.approved_at:
            print(f"  Approved: {spec.approved_at} by {spec.approved_by}")
        if spec.linked_plan_ids:
            print(f"  Plans:    {', '.join(spec.linked_plan_ids)}")
        if spec.content:
            print("\n--- Content ---")
            print(spec.content)

    elif cmd == "approve":
        spec = store.update_state(args.spec_id, "approved", actor=args.actor)
        print(f"Approved spec {spec.spec_id} by {args.actor}")

    elif cmd == "link":
        store.link_to_plan(args.spec_id, args.task_id, project_id=args.project)
        print(f"Linked spec {args.spec_id} to plan {args.task_id}")

    elif cmd == "score":
        try:
            scorecard = json.loads(args.scorecard)
        except json.JSONDecodeError as exc:
            print(f"Invalid scorecard JSON: {exc}")
            sys.exit(1)
        spec = store.score(args.spec_id, scorecard)
        print(f"Scored spec {spec.spec_id}: {spec.score_json}")

    elif cmd == "import":
        content = Path(args.file).read_text(encoding="utf-8")
        spec = store.import_json(content, overwrite=args.overwrite)
        print(f"Imported spec {spec.spec_id}")
        _print_spec_summary(spec)

    elif cmd == "export":
        json_str = store.export_json(args.spec_id)
        if args.out:
            Path(args.out).write_text(json_str, encoding="utf-8")
            print(f"Exported to {args.out}")
        else:
            print(json_str)
