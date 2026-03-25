"""``baton async`` -- dispatch and track asynchronous tasks.

Provides a lightweight task queue for fire-and-forget shell commands,
scripts, or manual tasks.  Tasks are persisted to disk and can be
checked later for completion status.

This is an experimental feature in ``core/distribute/experimental/``.

Delegates to:
    :class:`~agent_baton.core.distribute.experimental.async_dispatch.AsyncDispatcher`
"""
from __future__ import annotations

import argparse

from agent_baton.core.distribute.experimental.async_dispatch import AsyncDispatcher, AsyncTask


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = subparsers.add_parser("async", help="Dispatch and track asynchronous tasks")
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--pending", action="store_true", help="List only pending tasks",
    )
    group.add_argument(
        "--show", metavar="ID", help="Show a specific task's status",
    )
    group.add_argument(
        "--dispatch", metavar="COMMAND", help="Dispatch a new task",
    )
    p.add_argument(
        "--task-id", dest="task_id", metavar="ID", default=None,
        help="Task ID for --dispatch (auto-generated if omitted)",
    )
    p.add_argument(
        "--type", metavar="TYPE", default="shell",
        help="Dispatch type for --dispatch: shell, script, or manual (default: shell)",
    )
    return p


def handler(args: argparse.Namespace) -> None:
    dispatcher = AsyncDispatcher()

    if args.dispatch:
        task = AsyncTask(
            task_id=args.task_id or f"task-{args.dispatch[:20].replace(' ', '-')}",
            command=args.dispatch,
            dispatch_type=args.type or "shell",
        )
        path = dispatcher.dispatch(task)
        print(f"Task dispatched: {task.task_id} -> {path}")
        return

    if args.show:
        task = dispatcher.check_status(args.show)
        if task is None:
            print(f"No task found with ID '{args.show}'.")
            return
        print(f"Task ID:      {task.task_id}")
        print(f"Command:      {task.command}")
        print(f"Type:         {task.dispatch_type}")
        print(f"Status:       {task.status}")
        if task.dispatched_at:
            print(f"Dispatched:   {task.dispatched_at}")
        if task.completed_at:
            print(f"Completed:    {task.completed_at}")
        if task.exit_code is not None:
            print(f"Exit code:    {task.exit_code}")
        if task.result:
            print(f"Result:       {task.result}")
        return

    if args.pending:
        tasks = dispatcher.list_pending()
        if not tasks:
            print("No pending tasks.")
            return
        print(f"Pending tasks ({len(tasks)}):")
        for t in tasks:
            print(f"  {t.task_id:<30} {t.command}")
        return

    # Default: list all tasks
    tasks = dispatcher.list_tasks()
    if not tasks:
        print("No async tasks found.")
        return
    print(f"Async tasks ({len(tasks)}):")
    for t in tasks:
        print(f"  [{t.status:<12}] {t.task_id:<30} {t.command}")
