"""``baton decide`` -- manage human decision requests.

During daemon execution, agents may encounter situations requiring
human judgment (e.g. architecture choices, scope decisions).  These
are persisted as decision requests that this command lists, inspects,
and resolves.

Default action (no flags): list pending decisions.

Delegates to:
    :class:`~agent_baton.core.runtime.decisions.DecisionManager`
"""
from __future__ import annotations

import argparse

from agent_baton.core.runtime.decisions import DecisionManager


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    p = subparsers.add_parser("decide", help="Manage human decision requests")
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--list",
        dest="list_pending",
        action="store_true",
        help="List pending decision requests (default action)",
    )
    group.add_argument(
        "--all",
        dest="list_all",
        action="store_true",
        help="List all decision requests regardless of status",
    )
    group.add_argument(
        "--show",
        metavar="ID",
        help="Show full details of a single decision request",
    )
    group.add_argument(
        "--resolve",
        metavar="ID",
        help="Resolve a pending decision request",
    )
    p.add_argument(
        "--option",
        metavar="OPTION",
        help="Chosen option when using --resolve",
    )
    p.add_argument(
        "--rationale",
        metavar="TEXT",
        default=None,
        help="Optional rationale for the decision",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    mgr = DecisionManager()

    if args.resolve:
        if not args.option:
            print("Error: --option is required when using --resolve")
            return
        ok = mgr.resolve(args.resolve, args.option, rationale=args.rationale)
        if ok:
            print(f"Decision {args.resolve} resolved: {args.option}")
        else:
            print(f"No pending decision found with ID '{args.resolve}'.")
        return

    if args.show:
        req = mgr.get(args.show)
        if req is None:
            print(f"No decision request found with ID '{args.show}'.")
            return
        print(f"Request ID:    {req.request_id}")
        print(f"Task:          {req.task_id}")
        print(f"Type:          {req.decision_type}")
        print(f"Status:        {req.status}")
        print(f"Created:       {req.created_at}")
        print(f"Summary:       {req.summary}")
        print(f"Options:       {', '.join(req.options)}")
        if req.context_files:
            print(f"Context files: {', '.join(req.context_files)}")
        if req.deadline:
            print(f"Deadline:      {req.deadline}")
        return

    if args.list_all:
        reqs = mgr.list_all()
        if not reqs:
            print("No decision requests found.")
            return
        print(f"All decisions ({len(reqs)}):")
        for r in reqs:
            print(
                f"  [{r.status:<10}] {r.request_id:<14} "
                f"{r.decision_type:<20} {r.summary[:50]}"
            )
        return

    # Default: list pending.
    reqs = mgr.pending()
    if not reqs:
        print("No pending decisions.")
        return
    print(f"Pending decisions ({len(reqs)}):")
    for r in reqs:
        print(f"  {r.request_id:<14} {r.decision_type:<20} {r.summary[:50]}")
        print(f"    Options: {', '.join(r.options)}")
