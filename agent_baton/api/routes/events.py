"""SSE event streaming endpoint for the Agent Baton API.

GET /events/{task_id}
    Headers: Accept: text/event-stream
    Response: Server-Sent Events stream of Event objects

The EventBus is synchronous — handlers are invoked inline during publish().
The SSE endpoint is async and yields events over time.  The bridge between
the two is an ``asyncio.Queue``: the sync bus handler puts events onto the
queue; the async generator awaits them.

Event replay is performed first so that late-connecting clients receive the
full history before entering the live stream.  A 30-second keepalive comment
is sent when no event arrives within the timeout window, preventing proxies
and browsers from closing the connection silently.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from agent_baton.api.deps import get_bus
from agent_baton.core.events.bus import EventBus

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /events/{task_id}
# ---------------------------------------------------------------------------

@router.get(
    "/events/{task_id}",
    summary="Stream execution events over SSE",
    response_description="Server-Sent Event stream of Event objects.",
    # EventSourceResponse is not a JSON model; exclude from OpenAPI body schema.
    response_class=EventSourceResponse,
    tags=["events"],
)
async def stream_events(
    task_id: str,
    request: Request,
    bus: EventBus = Depends(get_bus),
) -> EventSourceResponse:
    """Open a Server-Sent Events stream for *task_id*.

    The stream begins with a replay of every event already stored in the bus
    for the requested task.  After the replay is exhausted, newly published
    events are forwarded in real time.  A ``keepalive`` comment is sent every
    30 seconds when the task produces no activity, so that load balancers and
    browser ``EventSource`` implementations do not close the connection.

    The subscription is cleaned up automatically when the client disconnects.

    Args:
        task_id: The task whose event stream to subscribe to.
        request: Injected by FastAPI; used to detect client disconnection.
        bus: The shared :class:`~agent_baton.core.events.bus.EventBus` instance.

    Returns:
        A streaming :class:`sse_starlette.sse.EventSourceResponse`.
    """

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        # Bridge: the synchronous bus calls this handler during publish().
        # put_nowait() is safe to call from a sync context because the event
        # loop is not awaiting inside put_nowait — the queue only schedules
        # an internal wakeup on the loop that is already running.
        def on_event(event) -> None:
            if event.task_id == task_id:
                queue.put_nowait(event)

        sub_id = bus.subscribe("*", on_event)

        try:
            # --- Replay existing events first --------------------------------
            # Fetch the snapshot before entering the live loop so we don't
            # race against events arriving between replay and subscribe.
            # (subscribe was already registered above, so any event published
            # after this point will also be in the queue.)
            existing = bus.replay(task_id=task_id)
            for event in existing:
                yield {
                    "event": event.topic,
                    "id": event.event_id,
                    "data": json.dumps(event.to_dict()),
                }

            # --- Live stream -------------------------------------------------
            while True:
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {
                        "event": event.topic,
                        "id": event.event_id,
                        "data": json.dumps(event.to_dict()),
                    }
                except asyncio.TimeoutError:
                    # Send a keepalive comment so the connection stays open.
                    yield {"comment": "keepalive"}

        finally:
            bus.unsubscribe(sub_id)

    return EventSourceResponse(event_generator())
