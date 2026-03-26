"""``baton trace`` -- list and inspect structured task execution traces.

Traces record the full lifecycle of an orchestrated task: every step
dispatch, gate result, and completion event with timestamps. This
command provides timeline and summary views for debugging and
performance analysis.

Delegates to:
    agent_baton.core.observe.trace.TraceRecorder
    agent_baton.core.observe.trace.TraceRenderer
"""
from __future__ import annotations

import argparse

from agent_baton.core.observe.trace import TraceRecorder, TraceRenderer


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser(
        "trace",
        help="List and inspect structured task execution traces",
    )

    # Positional: optional task_id for timeline view.
    p.add_argument(
        "task_id",
        nargs="?",
        metavar="TASK_ID",
        help="Show timeline for a specific task",
    )

    # Flags (mutually exclusive display modes).
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--last",
        action="store_true",
        help="Show timeline for the most recent task",
    )
    group.add_argument(
        "--summary",
        metavar="TASK_ID",
        dest="summary_task_id",
        help="Show compact summary for a specific task",
    )

    p.add_argument(
        "--count",
        type=int,
        default=10,
        metavar="N",
        help="Number of recent traces to list (default: 10)",
    )

    return p


def handler(args: argparse.Namespace) -> None:
    recorder = TraceRecorder()
    renderer = TraceRenderer()

    # ── baton trace --last ─────────────────────────────────────────────────
    if args.last:
        trace = recorder.get_last_trace()
        if trace is None:
            print("No traces found.")
            return
        print(renderer.render_timeline(trace))
        return

    # ── baton trace --summary TASK_ID ─────────────────────────────────────
    if args.summary_task_id:
        trace = recorder.load_trace(args.summary_task_id)
        if trace is None:
            print(f"No trace found for task '{args.summary_task_id}'.")
            return
        print(renderer.render_summary(trace))
        return

    # ── baton trace TASK_ID ───────────────────────────────────────────────
    if args.task_id:
        trace = recorder.load_trace(args.task_id)
        if trace is None:
            print(f"No trace found for task '{args.task_id}'.")
            return
        print(renderer.render_timeline(trace))
        return

    # ── baton trace [--count N]  (default: list recent) ───────────────────
    paths = recorder.list_traces(count=args.count)
    if not paths:
        print("No traces found.")
        return

    print(f"Recent traces ({len(paths)}):")
    for p in paths:
        # Load minimal info: just task_id, outcome, started_at.
        trace = recorder.load_trace(p.stem)
        if trace is None:
            print(f"  {p.stem}  (unreadable)")
            continue
        outcome = trace.outcome or "in-progress"
        started = trace.started_at
        event_count = len(trace.events)
        print(f"  {trace.task_id:<40}  {outcome:<15}  {started}  ({event_count} events)")
