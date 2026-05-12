"""Team mailbox — inter-teammate messaging substrate (A2.a).

Adopts Claude Code Agent Teams' mailbox model on top of baton's
existing ``TEAM_DISPATCH`` path so teammates can share context
without going through the lead. Independent of whether the team is
backed by the ``worktree`` execution backend (today) or the
experimental ``claude-teams`` backend (A1).

Storage: JSONL at ``.claude/team-context/mailbox/{team-id}.jsonl``,
one event per line, append-only. The mailbox is intentionally
single-writer-per-event-emit + multi-reader: each enqueue takes a brief
``filelock``-style sentinel via atomic rename to avoid interleaved
partial writes from concurrent dispatchers. JSONL keeps the file
diffable and grep-able for post-hoc audit (a regulated-domain
requirement) and matches the audit trail expectations called out in
``docs/internal/agent-teams-and-goal-design.md``.

Event taxonomy (matches Claude Code Agent Teams hooks):

* ``task_created`` — a task was added to the shared task list.
* ``task_completed`` — a task was marked done.
* ``task_failed`` — a task transitioned to failed.
* ``teammate_message`` — direct teammate-to-teammate message.
* ``teammate_idle`` — teammate finished its work and is awaiting input.
* ``plan_approval_requested`` — teammate seeks lead approval before
  implementation (A2.c).
* ``plan_approval_decided`` — lead approved or rejected (A2.c).

Retention: the mailbox is retained past team teardown (audit
requirement). A future GC sweep may trim entries older than a configured
window; the mailbox does not self-trim.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

_log = logging.getLogger(__name__)

MailboxEventType = Literal[
    "task_created",
    "task_completed",
    "task_failed",
    "teammate_message",
    "teammate_idle",
    "plan_approval_requested",
    "plan_approval_decided",
]


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class MailboxEvent:
    """A single mailbox event.

    Designed to be appendable as a single JSONL line. ``payload`` is
    free-form per ``event_type``; consumers should treat unknown keys
    as forward-compat extensions.

    Attributes:
        team_id: Stable identifier for the team (typically the task_id
            of the parent execution).
        event_id: Monotonic id within the mailbox (``"e1"``, ``"e2"``).
        event_type: One of ``MailboxEventType``.
        timestamp: ISO 8601 UTC stamp.
        from_member: Teammate id that emitted the event (or ``"lead"``
            / ``"engine"`` for system events).
        to_member: Recipient teammate id, ``"lead"`` for messages to the
            lead, or ``"*"`` for broadcasts. Ignored on non-message
            events.
        task_ref: Optional reference to the task this event concerns
            (step_id / member_id).
        subject: Short one-line summary; renders in the UI badge.
        body: Optional longer body text.
        payload: Extra event-type-specific fields.
    """

    team_id: str
    event_id: str
    event_type: MailboxEventType
    timestamp: str
    from_member: str
    to_member: str = ""
    task_ref: str = ""
    subject: str = ""
    body: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_jsonl_line(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MailboxEvent":
        return cls(
            team_id=data["team_id"],
            event_id=data["event_id"],
            event_type=data["event_type"],
            timestamp=data["timestamp"],
            from_member=data["from_member"],
            to_member=data.get("to_member", ""),
            task_ref=data.get("task_ref", ""),
            subject=data.get("subject", ""),
            body=data.get("body", ""),
            payload=dict(data.get("payload", {})),
        )


class TeamMailbox:
    """A per-team mailbox backed by an append-only JSONL file.

    All writes go through ``append()`` which performs an atomic
    write-then-rename of a sentinel + open-append so concurrent
    dispatchers cannot interleave a partial line. This is sufficient
    for the local-machine, single-host use case baton targets;
    cross-host coordination would require a real queue (out of scope
    for A2).

    The mailbox does NOT enforce auth — any caller with filesystem
    access can append. That mirrors the trust model of
    ``.claude/team-context/`` more broadly.
    """

    def __init__(self, team_context_root: Path, team_id: str) -> None:
        self._team_context_root = Path(team_context_root)
        self._team_id = team_id
        self._dir = self._team_context_root / "mailbox"
        self._path = self._dir / f"{team_id}.jsonl"

    # ---- File-level helpers ------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def team_id(self) -> str:
        return self._team_id

    def _ensure_dir(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)

    # ---- Writes ------------------------------------------------------------

    def append(
        self,
        event_type: MailboxEventType,
        *,
        from_member: str,
        to_member: str = "",
        task_ref: str = "",
        subject: str = "",
        body: str = "",
        payload: dict[str, Any] | None = None,
    ) -> MailboxEvent:
        """Append a new event to the mailbox.

        Concurrency: writes go to a temp file in the same directory and
        are then atomically appended via a short open(...; "a") in one
        ``os.fsync``-bounded call. The write itself is a single
        ``write(line + "\\n")`` so POSIX guarantees no torn lines on
        ext4/xfs/btrfs.
        """
        self._ensure_dir()
        existing = self._count_existing()
        event = MailboxEvent(
            team_id=self._team_id,
            event_id=f"e{existing + 1}",
            event_type=event_type,
            timestamp=_utcnow_iso(),
            from_member=from_member,
            to_member=to_member,
            task_ref=task_ref,
            subject=subject,
            body=body,
            payload=dict(payload or {}),
        )
        line = event.to_jsonl_line() + "\n"
        # Append in one syscall — short writes are guaranteed atomic on
        # local filesystems for line-sized payloads.
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(line)
        _log.debug(
            "mailbox[%s] +%s from=%s to=%s ref=%s",
            self._team_id, event.event_id,
            from_member, to_member or "-", task_ref or "-",
        )
        return event

    def _count_existing(self) -> int:
        if not self._path.exists():
            return 0
        # Linear scan is fine — these files are small relative to the
        # rest of team-context output and we only read on append.
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                return sum(1 for _ in fh)
        except OSError:
            return 0

    # ---- Reads -------------------------------------------------------------

    def read_all(self) -> list[MailboxEvent]:
        """Return every event in order."""
        if not self._path.exists():
            return []
        events: list[MailboxEvent] = []
        with open(self._path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(MailboxEvent.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError, ValueError) as exc:
                    _log.warning(
                        "mailbox[%s]: skipping malformed line: %s",
                        self._team_id, exc,
                    )
        return events

    def read_since(self, after_event_id: str | None) -> list[MailboxEvent]:
        """Return events strictly after *after_event_id*.

        Pass ``None`` to read from the beginning. Used by readers
        (orchestrator, PMO UI) to incrementally drain the mailbox.
        """
        events = self.read_all()
        if after_event_id is None:
            return events
        idx = next(
            (i for i, e in enumerate(events) if e.event_id == after_event_id),
            -1,
        )
        return events[idx + 1:] if idx >= 0 else events

    def read_for_member(self, member_id: str) -> list[MailboxEvent]:
        """Return events whose ``to_member`` is *member_id* or ``"*"``."""
        return [
            e for e in self.read_all()
            if e.to_member == member_id or e.to_member == "*"
        ]

    # ---- Lifecycle ---------------------------------------------------------

    def teardown_marker(self) -> Path:
        """Mark the mailbox as no longer active (team has been cleaned
        up). Returns the path to the marker file.

        We intentionally do NOT delete the mailbox: regulated workflows
        require post-hoc audit access. Future GC may trim, but never
        the lead at teardown.
        """
        self._ensure_dir()
        marker = self._dir / f"{self._team_id}.torn-down"
        marker.write_text(_utcnow_iso() + "\n", encoding="utf-8")
        return marker

    def is_torn_down(self) -> bool:
        return (self._dir / f"{self._team_id}.torn-down").exists()


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def open_mailbox(
    team_context_root: Path | str,
    team_id: str,
) -> TeamMailbox:
    """Create or open the mailbox for *team_id*."""
    return TeamMailbox(Path(team_context_root), team_id)


__all__ = [
    "MailboxEvent",
    "MailboxEventType",
    "TeamMailbox",
    "open_mailbox",
]
