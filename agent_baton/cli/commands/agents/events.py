"""``baton events`` -- query the event log for a task.

The event log records domain events (step dispatched, step completed,
gate passed, etc.) as an append-only sequence per task.  This command
provides raw event listing, projected summary views, and JSON export.

Display modes:
    * ``baton events`` -- List all task IDs with event logs.
    * ``baton events --task TASK_ID`` -- Show events for a specific task.
    * ``baton events --task TASK_ID --summary`` -- Projected summary view
      with phase/step/gate rollups.
    * ``baton events --task TASK_ID --json`` -- JSON export.
    * ``baton events --task TASK_ID --topic 'step.*'`` -- Filter by topic.
    * ``baton events --list-tasks`` -- List task IDs with event counts.

Delegates to:
    :class:`~agent_baton.core.events.persistence.EventPersistence`
    :func:`~agent_baton.core.events.projections.project_task_view`
"""
from __future__ import annotations

import argparse
import json

from agent_baton.core.events.persistence import EventPersistence
from agent_baton.core.events.projections import project_task_view


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("events", help="Query the event log for a task")
    p.add_argument(
        "--task", metavar="TASK_ID", default=None,
        help="Task ID to query events for",
    )
    p.add_argument(
        "--topic", metavar="PATTERN", default=None,
        help="Filter events by topic pattern (glob, e.g. 'step.*')",
    )
    p.add_argument(
        "--last", metavar="N", type=int, default=0,
        help="Show only the last N events",
    )
    p.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Output events as JSON",
    )
    p.add_argument(
        "--summary", action="store_true",
        help="Show a projected summary view instead of raw events",
    )
    p.add_argument(
        "--list-tasks", dest="list_tasks", action="store_true",
        help="List all task IDs that have event logs",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    persistence = EventPersistence()

    if args.list_tasks:
        task_ids = persistence.list_task_ids()
        if not task_ids:
            print("No event logs found.")
            return
        print(f"Tasks with event logs ({len(task_ids)}):")
        for tid in task_ids:
            count = persistence.event_count(tid)
            print(f"  {tid} ({count} events)")
        return

    if not args.task:
        # Default: list tasks if no task specified
        task_ids = persistence.list_task_ids()
        if not task_ids:
            print("No event logs found. Use --task TASK_ID to query events.")
            return
        print(f"Tasks with event logs ({len(task_ids)}):")
        for tid in task_ids:
            count = persistence.event_count(tid)
            print(f"  {tid} ({count} events)")
        print("\nUse --task TASK_ID to see events for a specific task.")
        return

    events = persistence.read(
        task_id=args.task,
        topic_pattern=args.topic,
    )

    if not events:
        print(f"No events found for task '{args.task}'.")
        return

    if args.last > 0:
        events = events[-args.last:]

    if args.summary:
        view = project_task_view(events, task_id=args.task)
        print(f"Task: {view.task_id}")
        print(f"Status: {view.status}")
        if view.started_at:
            print(f"Started: {view.started_at}")
        if view.completed_at:
            print(f"Completed: {view.completed_at}")
        print(f"Steps: {view.steps_completed} completed, "
              f"{view.steps_dispatched} dispatched, "
              f"{view.steps_failed} failed")
        print(f"Gates: {view.gates_passed} passed, {view.gates_failed} failed")
        if view.pending_decisions:
            print(f"Pending decisions: {', '.join(view.pending_decisions)}")
        if view.phases:
            print(f"\nPhases ({len(view.phases)}):")
            for phase_id in sorted(view.phases):
                phase = view.phases[phase_id]
                step_count = len(phase.steps)
                print(f"  Phase {phase_id}: {phase.phase_name or '(unnamed)'} "
                      f"[{phase.status}] ({step_count} steps)")
        return

    if args.as_json:
        output = [e.to_dict() for e in events]
        print(json.dumps(output, indent=2, ensure_ascii=False))
        return

    # Default: human-readable table
    print(f"Events for task '{args.task}' ({len(events)}):\n")
    for event in events:
        topic_col = f"{event.topic:<28}"
        seq_col = f"seq={event.sequence:<4}"
        ts_col = event.timestamp
        payload_keys = ", ".join(f"{k}={_truncate(v)}" for k, v in event.payload.items())
        print(f"  [{seq_col}] {ts_col}  {topic_col} {payload_keys}")


def _truncate(value: object, max_len: int = 40) -> str:
    """Truncate a value for display."""
    s = str(value)
    if len(s) > max_len:
        return s[:max_len - 3] + "..."
    return s
