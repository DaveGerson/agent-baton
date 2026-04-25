"""Escalation model — a question from an agent that needs user input."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass
class Escalation:
    """A question from an agent that requires human input before proceeding.

    Escalations are created when an agent encounters ambiguity it cannot
    resolve autonomously.  They are persisted to ``escalations.json`` and
    surfaced via ``baton status`` or the PMO dashboard.

    Attributes:
        agent_name: The agent that raised the escalation.
        question: The specific question needing a human answer.
        context: Background information to help the human decide.
        options: Suggested answer choices, if applicable.
        priority: ``"blocking"`` halts execution; ``"normal"`` is advisory.
        timestamp: ISO 8601 creation time (auto-populated if blank).
        resolved: Whether the human has answered.
        answer: The human's response, set when resolved.
        required_role: Role expected to handle this escalation
            (e.g. ``"tech-lead"``, ``"security-reviewer"``, ``"auditor"``).
            Empty string means anyone may handle it.
        timeout_minutes: Soft expiry for the escalation in minutes.
            ``0`` means no timeout. The escalation is considered ``expired``
            once ``timestamp + timeout_minutes`` has passed.
        escalate_to: Next-tier role to surface to in PMO when the timeout
            elapses. Empty string means stay at ``required_role``. Note:
            this is observation-only — no automatic paging or rerouting
            occurs; an operator must act on the surfaced expiry.
    """

    agent_name: str
    question: str
    context: str = ""
    options: list[str] = field(default_factory=list)
    priority: str = "normal"   # "blocking" or "normal"
    timestamp: str = ""        # ISO format; populated on first write if blank
    resolved: bool = False
    answer: str = ""           # filled in when resolved
    required_role: str = ""
    timeout_minutes: int = 0
    escalate_to: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(tz=timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Markdown rendering (legacy storage format)
    # ------------------------------------------------------------------

    def to_markdown(self) -> str:
        """Render the escalation as a markdown block."""
        status = "RESOLVED" if self.resolved else "PENDING"
        options_str = ", ".join(self.options) if self.options else ""
        lines = [
            f"### {self.timestamp} — {self.agent_name} — {status}",
            f"**Priority:** {self.priority}",
            f"**Question:** {self.question}",
            f"**Context:** {self.context}",
            f"**Options:** {options_str}",
            f"**Answer:** {self.answer}",
            f"**RequiredRole:** {self.required_role}",
            f"**TimeoutMinutes:** {self.timeout_minutes}",
            f"**EscalateTo:** {self.escalate_to}",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Dict round-trip
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-friendly dict."""
        return {
            "agent_name": self.agent_name,
            "question": self.question,
            "context": self.context,
            "options": list(self.options),
            "priority": self.priority,
            "timestamp": self.timestamp,
            "resolved": self.resolved,
            "answer": self.answer,
            "required_role": self.required_role,
            "timeout_minutes": self.timeout_minutes,
            "escalate_to": self.escalate_to,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Escalation":
        """Build an Escalation from a dict.

        Backwards-compatible: missing keys (including the new
        ``required_role``, ``timeout_minutes``, ``escalate_to`` fields)
        fall back to defaults so historical records still load.
        """
        return cls(
            agent_name=data.get("agent_name", ""),
            question=data.get("question", ""),
            context=data.get("context", ""),
            options=list(data.get("options", []) or []),
            priority=data.get("priority", "normal"),
            timestamp=data.get("timestamp", ""),
            resolved=bool(data.get("resolved", False)),
            answer=data.get("answer", ""),
            required_role=data.get("required_role", ""),
            timeout_minutes=int(data.get("timeout_minutes", 0) or 0),
            escalate_to=data.get("escalate_to", ""),
        )

    # ------------------------------------------------------------------
    # Timeout helpers (observation-only; no auto-paging)
    # ------------------------------------------------------------------

    def _created_at(self) -> datetime | None:
        """Parse ``timestamp`` into an aware datetime, or None on failure."""
        if not self.timestamp:
            return None
        try:
            dt = datetime.fromisoformat(self.timestamp)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    def _now(self, now: datetime | None) -> datetime:
        if now is None:
            return datetime.now(tz=timezone.utc)
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now

    def expired(self, now: datetime | None = None) -> bool:
        """Return True if the timeout has elapsed.

        ``timeout_minutes == 0`` means no timeout and never expires.
        """
        if self.timeout_minutes <= 0:
            return False
        created = self._created_at()
        if created is None:
            return False
        deadline = created + timedelta(minutes=self.timeout_minutes)
        return self._now(now) >= deadline

    def time_remaining(self, now: datetime | None = None) -> timedelta | None:
        """Time remaining until expiry.

        Returns ``None`` if there is no timeout configured (or the
        ``timestamp`` is unparseable). The returned ``timedelta`` may be
        negative if the escalation has already expired.
        """
        if self.timeout_minutes <= 0:
            return None
        created = self._created_at()
        if created is None:
            return None
        deadline = created + timedelta(minutes=self.timeout_minutes)
        return deadline - self._now(now)

    def next_role(self, now: datetime | None = None) -> str:
        """Role that should handle this escalation right now.

        Returns ``escalate_to`` if the timeout has elapsed and
        ``escalate_to`` is set; otherwise returns ``required_role``.
        Observation-only: callers (CLI, PMO) decide what to do with it.
        """
        if self.expired(now) and self.escalate_to:
            return self.escalate_to
        return self.required_role
