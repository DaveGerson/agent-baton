"""EventBus — in-process publish/subscribe with glob-style topic routing.

The bus is the central nervous system of the async runtime.  Publishers emit
:class:`Event` objects with a topic string (``step.completed``,
``gate.required``, etc.) and subscribers register handlers for topic patterns.

Topic routing uses ``fnmatch``-style glob patterns:
    - ``step.*``         → matches ``step.completed``, ``step.failed``, …
    - ``human.*``        → matches ``human.decision_needed``, …
    - ``*``              → matches everything

The bus is **synchronous in-process**: handlers are called immediately during
``publish()``.  No threads, no queues.  This matches the file-based philosophy
and keeps deployment simple.  The optional :class:`EventPersistence` layer
handles file-backed durability separately.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from fnmatch import fnmatch
from typing import Callable

from agent_baton.models.events import Event


# Type alias for subscriber callbacks.
EventHandler = Callable[[Event], None]


class EventBus:
    """In-process event bus with glob-style topic routing."""

    def __init__(self) -> None:
        # subscription_id -> (pattern, handler)
        self._subscriptions: dict[str, tuple[str, EventHandler]] = {}
        # topic_pattern -> list of subscription_ids (for fast iteration)
        self._by_pattern: dict[str, list[str]] = defaultdict(list)
        # task_id -> monotonic sequence counter
        self._sequences: dict[str, int] = defaultdict(int)
        # Full ordered history (in-memory; persistence layer handles disk).
        self._history: list[Event] = []

    # ── Publish ─────────────────────────────────────────────────────────────

    def publish(self, event: Event) -> None:
        """Publish an event, invoking all matching subscribers synchronously.

        If the event's ``sequence`` is 0, the bus auto-assigns the next
        monotonic sequence number for the event's ``task_id``.
        """
        if event.sequence == 0:
            self._sequences[event.task_id] += 1
            event.sequence = self._sequences[event.task_id]

        self._history.append(event)

        for _sub_id, (pattern, handler) in list(self._subscriptions.items()):
            if fnmatch(event.topic, pattern):
                handler(event)

    # ── Subscribe / Unsubscribe ─────────────────────────────────────────────

    def subscribe(
        self,
        topic_pattern: str,
        handler: EventHandler,
    ) -> str:
        """Register a handler for events matching *topic_pattern*.

        Returns a subscription ID that can be passed to :meth:`unsubscribe`.
        """
        sub_id = uuid.uuid4().hex[:8]
        self._subscriptions[sub_id] = (topic_pattern, handler)
        self._by_pattern[topic_pattern].append(sub_id)
        return sub_id

    def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription.  No-op if the ID is unknown."""
        entry = self._subscriptions.pop(subscription_id, None)
        if entry is not None:
            pattern = entry[0]
            subs = self._by_pattern.get(pattern, [])
            if subscription_id in subs:
                subs.remove(subscription_id)

    # ── Replay / Query ──────────────────────────────────────────────────────

    def replay(
        self,
        task_id: str,
        from_seq: int = 0,
        topic_pattern: str | None = None,
    ) -> list[Event]:
        """Return events for *task_id* with sequence >= *from_seq*.

        Optionally filter by *topic_pattern* (glob).
        """
        results: list[Event] = []
        for event in self._history:
            if event.task_id != task_id:
                continue
            if event.sequence < from_seq:
                continue
            if topic_pattern and not fnmatch(event.topic, topic_pattern):
                continue
            results.append(event)
        return results

    def history(self, limit: int = 0) -> list[Event]:
        """Return all events in publish order, optionally limited to last *limit*."""
        if limit > 0:
            return list(self._history[-limit:])
        return list(self._history)

    @property
    def subscription_count(self) -> int:
        """Number of active subscriptions."""
        return len(self._subscriptions)

    def clear(self) -> None:
        """Clear all subscriptions and history.  Primarily for testing."""
        self._subscriptions.clear()
        self._by_pattern.clear()
        self._sequences.clear()
        self._history.clear()
