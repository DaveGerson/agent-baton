"""Lightweight incident store for Agent Baton runtime incidents.

Records structured incident events (warning / error / critical) and
optionally forwards them to PagerDuty via :class:`PagerDutyNotifier`.

This module is distinct from the *incident response workflow* manager
in :mod:`agent_baton.core.distribute.experimental.incident`, which
handles phased P1-P4 runbooks. This module handles *live* runtime
incidents emitted by the execution engine.

Usage::

    from agent_baton.core.observe.incidents import IncidentStore
    from agent_baton.core.observe.pagerduty import PagerDutyNotifier

    store = IncidentStore(notifier=PagerDutyNotifier())
    store.record_incident(
        incident_id="INC-001",
        severity="error",
        summary="Step timeout exceeded",
        details={"step": "deploy", "elapsed_s": 300},
    )
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.observe.pagerduty import PagerDutyNotifier

logger = logging.getLogger(__name__)

_NOTIFY_SEVERITIES = frozenset({"warning", "error", "critical"})


class IncidentStore:
    """Record runtime incidents and optionally forward to PagerDuty.

    Args:
        notifier: Optional :class:`~agent_baton.core.observe.pagerduty.PagerDutyNotifier`.
            When provided, incidents with severity ``warning``, ``error``,
            or ``critical`` are forwarded. Notification failures are
            logged and never propagated.
    """

    def __init__(self, notifier: PagerDutyNotifier | None = None) -> None:
        self._notifier = notifier
        self._incidents: list[dict] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_incident(
        self,
        incident_id: str,
        severity: str,
        summary: str,
        details: dict | None = None,
    ) -> None:
        """Record an incident and optionally notify PagerDuty.

        Args:
            incident_id: Unique identifier for this incident.
            severity: One of ``critical``, ``error``, ``warning``, ``info``.
            summary: Short description of what happened.
            details: Optional structured context.
        """
        record = {
            "incident_id": incident_id,
            "severity": severity,
            "summary": summary,
            "details": details or {},
        }
        self._incidents.append(record)
        logger.info("Incident recorded: [%s] %s — %s", severity, incident_id, summary)

        if (
            self._notifier is not None
            and severity in _NOTIFY_SEVERITIES
        ):
            try:
                self._notifier.notify_incident(
                    incident_id=incident_id,
                    severity=severity,
                    summary=summary,
                    details=details,
                )
            except Exception as exc:
                logger.warning(
                    "PagerDuty notification failed for incident %s: %s",
                    incident_id,
                    exc,
                )

    def list_incidents(self) -> list[dict]:
        """Return all recorded incidents (most recent last)."""
        return list(self._incidents)
