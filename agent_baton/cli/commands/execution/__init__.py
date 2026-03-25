"""CLI command group: execution.

Commands for creating plans, driving the execution engine loop, managing
daemon processes, dispatching async tasks, and handling human decision
requests.  This is the core group that the orchestrator agent interacts
with during task execution.

Commands:
    * ``baton plan`` -- Create an intelligent execution plan.
    * ``baton execute`` -- Drive the execution loop (start, next, record,
      gate, approve, complete, status, resume, list, switch).
    * ``baton status`` -- Show team-context file status.
    * ``baton daemon`` -- Background execution management.
    * ``baton async`` -- Dispatch and track asynchronous tasks.
    * ``baton decide`` -- Manage human decision requests.
"""
from __future__ import annotations
