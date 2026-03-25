"""``baton dashboard`` -- generate or display the usage dashboard.

Renders a markdown summary of orchestration activity including agent
usage counts, gate pass rates, and token consumption.  With ``--write``,
persists the dashboard to disk for later review.

Auto-detects the storage backend (SQLite or JSONL) so that executions
stored in ``baton.db`` are included alongside flat-file records.

Delegates to:
    :class:`~agent_baton.core.observe.dashboard.DashboardGenerator`
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from agent_baton.core.observe.dashboard import DashboardGenerator

_log = logging.getLogger(__name__)

_DEFAULT_CONTEXT_ROOT = Path(".claude/team-context")


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("dashboard", help="Generate usage dashboard")
    p.add_argument(
        "--write", action="store_true", help="Write dashboard to disk",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    context_root = _DEFAULT_CONTEXT_ROOT.resolve()

    # Auto-detect storage backend so SQLite-mode executions are included.
    storage = None
    try:
        from agent_baton.core.storage import detect_backend, get_project_storage
        if detect_backend(context_root) == "sqlite":
            storage = get_project_storage(context_root, backend="sqlite")
    except Exception as exc:
        _log.debug("Dashboard: storage backend unavailable, using JSONL only: %s", exc)

    gen = DashboardGenerator(storage=storage)

    if args.write:
        path = gen.write()
        print(f"Dashboard written to {path}")
        return

    print(gen.generate())
