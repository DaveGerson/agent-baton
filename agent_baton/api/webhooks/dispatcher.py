"""WebhookDispatcher — outbound webhook delivery with retry and HMAC signing.

The dispatcher subscribes to the shared :class:`~agent_baton.core.events.bus.EventBus`
using the catch-all ``"*"`` pattern.  When an event arrives it checks the
:class:`~agent_baton.api.webhooks.registry.WebhookRegistry` for matching
subscriptions and schedules an async delivery task for each one.

Delivery features
-----------------
- **HMAC-SHA256 signing**: When a webhook has a ``secret``, the JSON payload
  is signed and the hex digest is sent in the ``X-Baton-Signature`` header.
- **Retry with exponential backoff**: Up to 3 attempts.  Wait times between
  retries are ``[5, 30, 300]`` seconds.
- **Auto-disable**: After ``_MAX_CONSECUTIVE_FAILURES`` (10) consecutive
  delivery failures the webhook is disabled in the registry.
- **Failure log**: Every failed delivery attempt is appended to a JSONL file
  (``webhook-failures.jsonl``).

Threading model
---------------
The :class:`~agent_baton.core.events.bus.EventBus` is **synchronous** — its
``publish()`` method calls handlers inline.  Webhook delivery is async because
it involves network I/O.  The handler therefore schedules each delivery as an
``asyncio.create_task()`` and returns immediately, keeping the bus non-blocking.

If no event loop is running at subscription time (e.g. during tests) the
dispatcher still registers correctly; any event published before an async loop
is available will be silently dropped (the ``asyncio.get_event_loop()`` call
returns a loop but ``create_task`` may not be available in all contexts — we
guard with a try/except).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from agent_baton.api.webhooks.payloads import format_generic, format_slack
from agent_baton.api.webhooks.registry import WebhookRegistry
from agent_baton.core.events.bus import EventBus
from agent_baton.models.events import Event

_log = logging.getLogger(__name__)

# Maximum consecutive failures before a webhook is auto-disabled.
_MAX_CONSECUTIVE_FAILURES = 10

# Retry delays in seconds between attempts (3 total attempts).
_RETRY_BACKOFFS: list[float] = [5.0, 30.0, 300.0]

# Per-request HTTP timeout in seconds.
_HTTP_TIMEOUT = 10.0

# Slack topic for specialised formatting.
_SLACK_TOPIC = "human.decision_needed"


class WebhookDispatcher:
    """Subscribes to EventBus, delivers matching events to registered webhooks.

    Args:
        registry: The :class:`WebhookRegistry` to query for matching webhooks.
        bus: The shared :class:`EventBus` to subscribe to.
        failures_path: Path to the JSONL failure log.  Defaults to
            ``webhook-failures.jsonl`` in the current directory.  Pass the
            team-context directory path to co-locate it with other runtime
            files.
    """

    def __init__(
        self,
        registry: WebhookRegistry,
        bus: EventBus,
        failures_path: Path | None = None,
    ) -> None:
        self._registry = registry
        self._bus = bus
        self._failures_path = failures_path or Path("webhook-failures.jsonl")
        self._sub_id: str | None = None

        # Subscribe to everything on the bus.
        self._sub_id = bus.subscribe("*", self._on_event)

    # ── Bus handler (synchronous) ─────────────────────────────────────────────

    def _on_event(self, event: Event) -> None:
        """Called synchronously by the EventBus for every published event.

        Schedules an async delivery task for each matching webhook without
        blocking the bus.
        """
        hooks = self._registry.match(event.topic)
        if not hooks:
            return

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # No event loop in this thread — cannot schedule async tasks.
            _log.debug(
                "WebhookDispatcher: no event loop available; skipping delivery "
                "for topic %s (%d hooks matched)",
                event.topic,
                len(hooks),
            )
            return

        for hook in hooks:
            if loop.is_running():
                loop.create_task(
                    self._deliver_with_retry(event, hook),
                    name=f"webhook-{hook['webhook_id']}-{event.event_id}",
                )
            else:
                _log.debug(
                    "WebhookDispatcher: event loop exists but is not running; "
                    "skipping delivery for hook %s",
                    hook.get("webhook_id"),
                )

    # ── Async delivery ────────────────────────────────────────────────────────

    async def _deliver_with_retry(self, event: Event, hook: dict) -> None:
        """Attempt delivery up to 3 times with exponential backoff.

        On exhausting all retries the webhook's ``consecutive_failures``
        counter is incremented.  If it reaches ``_MAX_CONSECUTIVE_FAILURES``
        the webhook is disabled.

        On a successful delivery ``consecutive_failures`` is reset to 0.
        """
        webhook_id: str = hook.get("webhook_id", "")
        max_attempts = len(_RETRY_BACKOFFS)

        for attempt in range(max_attempts):
            success = await self.deliver(event, hook)
            if success:
                # Reset failure counter on success.
                self._registry.update(webhook_id, consecutive_failures=0)
                return

            # Log the failed attempt.
            self._log_failure(event, hook, attempt=attempt + 1)

            if attempt < max_attempts - 1:
                backoff = _RETRY_BACKOFFS[attempt]
                _log.warning(
                    "Webhook %s delivery attempt %d/%d failed for event %s; "
                    "retrying in %.0fs.",
                    webhook_id,
                    attempt + 1,
                    max_attempts,
                    event.event_id,
                    backoff,
                )
                await asyncio.sleep(backoff)
            else:
                _log.error(
                    "Webhook %s exhausted all %d delivery attempts for event %s.",
                    webhook_id,
                    max_attempts,
                    event.event_id,
                )

        # All attempts failed — increment counter, possibly disable.
        current_failures: int = hook.get("consecutive_failures", 0) + 1
        update_fields: dict = {"consecutive_failures": current_failures}
        if current_failures >= _MAX_CONSECUTIVE_FAILURES:
            update_fields["enabled"] = False
            _log.error(
                "Webhook %s disabled after %d consecutive failures.",
                webhook_id,
                current_failures,
            )
        self._registry.update(webhook_id, **update_fields)

    async def deliver(self, event: Event, hook: dict) -> bool:
        """Deliver a single event to a webhook endpoint.

        Selects the appropriate payload formatter based on the hook URL
        (Slack-style payloads for ``slack.com`` endpoints and the
        ``human.decision_needed`` topic), signs the payload when a secret is
        present, and POSTs via :mod:`httpx`.

        Args:
            event: The event to deliver.
            hook: Webhook entry dict from the registry.

        Returns:
            ``True`` if the server responded with a 2xx status code.
        """
        try:
            import httpx
        except ImportError:
            _log.error(
                "httpx is not installed; webhook delivery is unavailable. "
                "Install it with: pip install httpx"
            )
            return False

        url: str = hook.get("url", "")
        secret: str | None = hook.get("secret")

        # Choose formatter.
        url_lower = url.lower()
        if event.topic == _SLACK_TOPIC or "slack.com" in url_lower or "hooks.slack" in url_lower:
            payload_dict = format_slack(event)
        else:
            payload_dict = format_generic(event)

        try:
            payload_bytes = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            _log.error("Failed to serialise webhook payload for event %s: %s", event.event_id, exc)
            return False

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "AgentBaton-Webhook/1.0",
            "X-Baton-Event": event.topic,
            "X-Baton-Event-Id": event.event_id,
        }

        if secret:
            headers["X-Baton-Signature"] = self._sign_payload(payload_bytes, secret)

        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                response = await client.post(url, content=payload_bytes, headers=headers)
                if response.is_success:
                    _log.debug(
                        "Webhook %s delivered event %s — HTTP %d.",
                        hook.get("webhook_id"),
                        event.event_id,
                        response.status_code,
                    )
                    return True
                _log.warning(
                    "Webhook %s received HTTP %d for event %s.",
                    hook.get("webhook_id"),
                    response.status_code,
                    event.event_id,
                )
                return False
        except httpx.TimeoutException:
            _log.warning(
                "Webhook %s timed out delivering event %s.",
                hook.get("webhook_id"),
                event.event_id,
            )
            return False
        except httpx.RequestError as exc:
            _log.warning(
                "Webhook %s request error delivering event %s: %s",
                hook.get("webhook_id"),
                event.event_id,
                exc,
            )
            return False

    def _sign_payload(self, payload_bytes: bytes, secret: str) -> str:
        """Compute HMAC-SHA256 signature for the ``X-Baton-Signature`` header.

        Args:
            payload_bytes: The raw JSON bytes to sign.
            secret: The webhook's shared secret.

        Returns:
            Hex-encoded HMAC-SHA256 digest string.
        """
        return hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256,
        ).hexdigest()

    # ── Failure logging ───────────────────────────────────────────────────────

    def _log_failure(self, event: Event, hook: dict, attempt: int) -> None:
        """Append a failure record to the JSONL failure log.

        The log entry captures the webhook ID, endpoint URL, event topic +
        ID, attempt number, and UTC timestamp.  Errors during logging are
        caught and emitted as warnings so they never interrupt delivery logic.
        """
        record = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            "webhook_id": hook.get("webhook_id"),
            "url": hook.get("url"),
            "event_id": event.event_id,
            "event_topic": event.topic,
            "task_id": event.task_id,
            "attempt": attempt,
        }
        try:
            self._failures_path.parent.mkdir(parents=True, exist_ok=True)
            with self._failures_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            _log.warning("Could not write to webhook failure log: %s", exc)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def unsubscribe(self) -> None:
        """Remove the bus subscription.  Call when shutting down the server."""
        if self._sub_id is not None:
            self._bus.unsubscribe(self._sub_id)
            self._sub_id = None
