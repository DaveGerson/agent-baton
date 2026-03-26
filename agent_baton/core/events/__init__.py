"""Events sub-package -- event bus, domain events, persistence, and projections.

This package implements the event-driven backbone of the Agent Baton runtime.
It provides four layers:

1. **Domain events** (:mod:`events`) -- factory functions that produce
   typed :class:`Event` instances for step lifecycle, gates, approvals,
   human decisions, plan amendments, and team coordination.

2. **Event bus** (:class:`EventBus`) -- an in-process pub/sub bus with
   glob-style topic routing.  Handlers are invoked synchronously during
   ``publish()``.  No threads, no queues.

3. **Persistence** (:class:`EventPersistence`) -- append-only JSONL
   storage per task, enabling crash recovery and post-hoc analysis.
   Wired as a bus subscriber so events are durably stored as they flow.

4. **Projections** (:func:`project_task_view`) -- materialized views
   that fold an event stream into summary dataclasses (:class:`TaskView`,
   :class:`PhaseView`, :class:`StepView`) for dashboards and status queries.

The event system is intentionally simple: synchronous, file-backed, and
dependency-free.  It trades throughput for predictability and debuggability.
"""
from __future__ import annotations

from agent_baton.models.events import Event
from agent_baton.core.events.bus import EventBus
from agent_baton.core.events.persistence import EventPersistence
from agent_baton.core.events.projections import (
    TaskView,
    PhaseView,
    StepView,
    project_task_view,
)

__all__ = [
    "Event",
    "EventBus",
    "EventPersistence",
    "TaskView",
    "PhaseView",
    "StepView",
    "project_task_view",
]
