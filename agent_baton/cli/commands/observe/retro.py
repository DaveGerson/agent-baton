"""``baton retro`` -- show retrospectives.

Retrospectives are auto-generated after each completed execution and
contain lessons learned, agent performance notes, and roster
recommendations.  This command lists, searches, and inspects them.

Display modes:
    * ``baton retro`` -- List recent retrospectives.
    * ``baton retro --task-id ID`` -- Show a specific retrospective.
    * ``baton retro --search KEYWORD`` -- Search by keyword.
    * ``baton retro --recommendations`` -- Extract roster recommendations
      (add/remove/modify agent) from all retrospectives.

Delegates to:
    :class:`~agent_baton.core.observe.retrospective.RetrospectiveEngine`
"""
from __future__ import annotations

import argparse

from agent_baton.core.observe.retrospective import RetrospectiveEngine


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("retro", help="Show retrospectives")
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--task-id", metavar="ID", help="Show a specific retrospective",
    )
    group.add_argument(
        "--search", metavar="KEYWORD", help="Search retrospectives by keyword",
    )
    group.add_argument(
        "--recommendations", action="store_true",
        help="Extract roster recommendations from all retrospectives",
    )
    p.add_argument(
        "--count", type=int, default=None, metavar="N",
        help="Number of recent retrospectives to list (default 10)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    engine = RetrospectiveEngine()

    if args.search:
        results = engine.search(args.search)
        if not results:
            print(f"No retrospectives matching '{args.search}'.")
            return
        print(f"Retrospectives matching '{args.search}':")
        for path in results:
            print(f"  {path.stem}")
        return

    if args.recommendations:
        recs = engine.extract_recommendations()
        if not recs:
            print("No roster recommendations found.")
            return
        print("Roster Recommendations (across all retrospectives):")
        for rec in recs:
            print(f"  [{rec.action}] {rec.target}")
            if rec.reason:
                print(f"    {rec.reason}")
        return

    if args.task_id:
        content = engine.load(args.task_id)
        if content is None:
            print(f"No retrospective found for task '{args.task_id}'.")
            return
        print(content)
        return

    # Default: list recent retrospectives
    recent = engine.list_recent(args.count or 10)
    if not recent:
        print("No retrospectives found.")
        return
    print(f"Recent retrospectives ({len(recent)}):")
    for path in recent:
        print(f"  {path.stem}")
