"""PagerDuty Events API v2 notifier for Agent Baton incidents.

Sends alert events to PagerDuty using the Events API v2
(``https://events.pagerduty.com/v2/enqueue``). The notifier is
**opt-in**: if no routing key is configured it silently returns
``None`` and never raises.

Usage::

    notifier = PagerDutyNotifier()          # reads BATON_PAGERDUTY_KEY
    dedup_key = notifier.notify_incident(
        incident_id="INC-001",
        severity="critical",
        summary="Database unreachable",
        details={"host": "db-primary"},
    )

Inject a *client* object for testing::

    class FakeClient:
        def urlopen(self, req, timeout): ...

    notifier = PagerDutyNotifier(routing_key="test-key", client=FakeClient())
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_VALID_SEVERITIES = frozenset({"critical", "error", "warning", "info"})
_ENQUEUE_URL = "https://events.pagerduty.com/v2/enqueue"
_TIMEOUT = 5


@runtime_checkable
class _HttpClient(Protocol):
    """Minimal HTTP client interface (duck-typed for testability)."""

    def urlopen(self, req: urllib.request.Request, timeout: int) -> object:
        ...


class PagerDutyNotifier:
    """Send alert events to PagerDuty Events API v2.

    Args:
        routing_key: PagerDuty integration routing key. If ``None``, the
            value of the ``BATON_PAGERDUTY_KEY`` environment variable is
            used. When neither is set the notifier is disabled.
        client: Optional HTTP client override for testing. Must implement
            ``urlopen(req, timeout)``. Defaults to ``urllib.request``.
    """

    def __init__(
        self,
        routing_key: str | None = None,
        *,
        client: _HttpClient | None = None,
    ) -> None:
        self._routing_key: str | None = routing_key or os.environ.get(
            "BATON_PAGERDUTY_KEY"
        )
        self._client = client  # None = use urllib.request directly

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify_incident(
        self,
        incident_id: str,
        severity: str,
        summary: str,
        details: dict | None = None,
    ) -> str | None:
        """Send a trigger event to PagerDuty.

        Args:
            incident_id: Unique identifier used as the PagerDuty
                ``dedup_key`` (alerts with the same key are grouped).
            severity: Must be one of ``critical``, ``error``,
                ``warning``, or ``info``. Raises ``ValueError`` for
                any other value.
            summary: Short human-readable description of the incident.
            details: Optional extra context attached to the payload.

        Returns:
            The ``dedup_key`` echoed back by PagerDuty, or ``None`` when
            the notifier is disabled or a network error occurs.

        Raises:
            ValueError: If *severity* is not one of the four valid values.
        """
        if severity not in _VALID_SEVERITIES:
            raise ValueError(
                f"Invalid severity {severity!r}. "
                f"Must be one of: {', '.join(sorted(_VALID_SEVERITIES))}"
            )

        if not self._routing_key:
            logger.debug(
                "PagerDuty notifier disabled: no routing key configured "
                "(set BATON_PAGERDUTY_KEY or pass routing_key=)."
            )
            return None

        payload = {
            "routing_key": self._routing_key,
            "event_action": "trigger",
            "dedup_key": incident_id,
            "payload": {
                "summary": summary,
                "severity": severity,
                "source": "agent-baton",
                "custom_details": details or {},
            },
        }

        try:
            return self._post(payload)
        except Exception as exc:
            logger.warning("PagerDuty notify failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post(self, payload: dict) -> str | None:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            _ENQUEUE_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        if self._client is not None:
            response = self._client.urlopen(req, _TIMEOUT)
        else:
            response = urllib.request.urlopen(req, timeout=_TIMEOUT)  # noqa: S310

        raw = getattr(response, "read", lambda: b"")()
        if isinstance(raw, bytes):
            raw = raw.decode(errors="replace")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            data = {}

        return data.get("dedup_key") or data.get("message") or None
