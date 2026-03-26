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
    """In-process event bus with glob-style topic routing.

    The bus is the central nervous system of the execution runtime.
    Components publish :class:`Event` objects with topic strings (e.g.
    ``step.completed``, ``gate.required``) and subscribers register
    handlers for ``fnmatch``-style topic patterns.

    Design choices:
        - **Synchronous dispatch** -- handlers run inline during
          ``publish()``.  This keeps the execution deterministic and
          easy to debug (no concurrency surprises).
        - **In-memory history** -- the bus retains all published events
          for replay queries.  The :class:`EventPersistence` layer handles
          durable storage separately, typically wired as a subscriber.
        - **Monotonic sequencing** -- each task gets an auto-incrementing
          sequence counter.  When an event arrives with ``sequence == 0``,
          the bus assigns the next number, ensuring a total order per task.

    Attributes:
        _subscriptions: Map from subscription ID to ``(pattern, handler)``
            tuple.
        _by_pattern: Reverse index from topic pattern to subscription IDs,
            used internally for fast pattern-grouped operations.
        _sequences: Per-task monotonic sequence counters.
        _history: Ordered list of all published events (in-memory).
    """

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

        The event is appended to the in-memory history, and then every
        subscriber whose topic pattern matches the event's topic is
        called in registration order.  Handlers execute inline -- if a
        handler raises, the exception propagates to the publisher.

        If the event's ``sequence`` is 0 (the default from factory
        functions), the bus auto-assigns the next monotonic sequence
        number for the event's ``task_id``.

        Args:
            event: The event to publish.  Its ``sequence`` field may be
                mutated if it was 0.
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

        The pattern uses ``fnmatch``-style globbing:
            - ``"step.*"`` matches ``step.completed``, ``step.failed``, etc.
            - ``"*"`` matches all topics.
            - ``"gate.passed"`` matches only the exact topic.

        Args:
            topic_pattern: Glob pattern to match event topics against.
            handler: Callable that receives the :class:`Event` when a
                matching event is published.  Must not block for extended
                periods since all handlers execute synchronously.

        Returns:
            A subscription ID (8-character hex string) that can be passed
            to :meth:`unsubscribe` to remove this subscription.
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

        Used by the execution engine to rebuild state after a crash or
        to catch up a late-joining subscriber.  Events are returned in
        publish order.

        Args:
            task_id: The task whose events to replay.
            from_seq: Minimum sequence number (inclusive).  Use this to
                replay only events newer than the last processed one.
            topic_pattern: Optional glob pattern to filter by topic
                (e.g. ``"step.*"``).  When ``None``, all topics are
                included.

        Returns:
            List of matching events in publish order.
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
