"""Team-board facade: messages and shared tasks over the Bead store.

The team board is a thin vocabulary layer on top of
:class:`~agent_baton.core.engine.bead_store.BeadStore`.  No new tables,
no schema change — addressing rides on the existing ``bead_tags`` index.

Tag conventions (indexed by ``bead_tags``):

- ``team=<team_id>``             — scopes a bead to a team.
- ``to_member=<member_id>``      — direct message to a specific member.
- ``to_team=<team_id>``          — broadcast message to every member of a team.
- ``from_member=<member_id>``    — author of a message or task.
- ``from_team=<team_id>``        — source team for cross-team messages.
- ``claimed_by=<member_id>``     — marks a task as claimed by a specific member.
- ``ack_of=<message_bead_id>``   — on a ``message_ack`` bead, the message it acknowledges.

Task status mapping:

- ``open``    — bead ``status=open`` with no ``claimed_by=X`` tag.
- ``claimed`` — bead ``status=open`` with a ``claimed_by=X`` tag.
- ``done``    — bead ``status=closed``.

Message re-delivery suppression: ``BeadSelector`` / ``open_tasks_for_team``
filter out ``message`` beads for which the recipient has written a
matching ``message_ack`` bead.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from agent_baton.models.bead import (
    Bead,
    _generate_bead_id,
)

if TYPE_CHECKING:
    from agent_baton.core.engine.bead_store import BeadStore

_log = logging.getLogger(__name__)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TeamBoard:
    """Facade over :class:`BeadStore` for team messages and shared tasks.

    Args:
        bead_store: Live :class:`~agent_baton.core.engine.bead_store.BeadStore`.
    """

    def __init__(self, bead_store: "BeadStore") -> None:
        self._store = bead_store

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def send_message(
        self,
        *,
        task_id: str,
        from_team: str,
        from_member: str,
        to_team: str,
        to_member: str | None,
        subject: str,
        body: str,
    ) -> str:
        """Write a ``message`` bead and return its ``bead_id``.

        When *to_member* is None the message is a broadcast to ``to_team``
        and every member of that team will see it on their next dispatch.
        Direct messages carry ``to_member=<id>`` in addition to the team
        scope so the broadcast filter still matches.

        Returns:
            The new bead_id, or an empty string on write failure.
        """
        now = _utcnow()
        tags: list[str] = [
            f"team={to_team}",
            f"to_team={to_team}",
            f"from_team={from_team}",
            f"from_member={from_member}",
        ]
        if to_member:
            tags.append(f"to_member={to_member}")
        content = f"{subject}\n\n{body}" if subject else body
        bead_count = self._count()
        bead_id = _generate_bead_id(task_id, "team-board", content, now, bead_count)
        bead = Bead(
            bead_id=bead_id,
            task_id=task_id,
            step_id="team-board",
            agent_name=from_member or "team",
            bead_type="message",
            content=content,
            scope="task",
            tags=tags,
            status="open",
            created_at=now,
            source="team-board",
        )
        return self._store.write(bead)

    def ack_message(
        self,
        *,
        task_id: str,
        message_bead_id: str,
        recipient_member_id: str,
    ) -> str:
        """Mark *message_bead_id* as read by *recipient_member_id*.

        Written as a ``message_ack`` bead with tag
        ``ack_of=<message_bead_id>`` and ``from_member=<recipient>``.
        :class:`BeadSelector` filters messages whose ack exists.
        """
        now = _utcnow()
        tags = [
            f"ack_of={message_bead_id}",
            f"from_member={recipient_member_id}",
        ]
        bead_count = self._count()
        content = f"ack:{message_bead_id}:{recipient_member_id}"
        bead_id = _generate_bead_id(task_id, "team-board", content, now, bead_count)
        bead = Bead(
            bead_id=bead_id,
            task_id=task_id,
            step_id="team-board",
            agent_name=recipient_member_id or "team",
            bead_type="message_ack",
            content=content,
            scope="task",
            tags=tags,
            status="closed",  # acks are terminal
            created_at=now,
            closed_at=now,
            source="team-board",
        )
        return self._store.write(bead)

    def unread_messages_for_member(
        self,
        *,
        task_id: str,
        team_id: str,
        member_id: str,
        limit: int = 100,
    ) -> list[Bead]:
        """Return open ``message`` beads addressed to *member_id* or *team_id*.

        Messages whose matching ``message_ack`` bead (by ``ack_of=<id>`` +
        ``from_member=<member_id>``) exists are suppressed.
        """
        messages = self._store.query(
            task_id=task_id, bead_type="message", limit=limit * 2,
        )
        acked_ids = self._acked_message_ids(task_id, member_id)
        out: list[Bead] = []
        for msg in messages:
            if msg.bead_id in acked_ids:
                continue
            tags = set(msg.tags)
            # Direct message to this member OR broadcast to this team.
            if (
                f"to_member={member_id}" in tags
                or (
                    f"to_team={team_id}" in tags
                    and not any(t.startswith("to_member=") for t in tags)
                )
            ):
                out.append(msg)
                if len(out) >= limit:
                    break
        return out

    # ------------------------------------------------------------------
    # Tasks
    # ------------------------------------------------------------------

    def append_task(
        self,
        *,
        task_id: str,
        team_id: str,
        author_member_id: str,
        title: str,
        detail: str = "",
        parent_task_bead_id: str | None = None,
    ) -> str:
        """Write an ``open`` ``task`` bead and return its ``bead_id``."""
        now = _utcnow()
        content = f"{title}\n\n{detail}" if detail else title
        tags = [
            f"team={team_id}",
            f"from_member={author_member_id}",
        ]
        if parent_task_bead_id:
            tags.append(f"parent_task={parent_task_bead_id}")
        bead_count = self._count()
        bead_id = _generate_bead_id(task_id, "team-board", content, now, bead_count)
        bead = Bead(
            bead_id=bead_id,
            task_id=task_id,
            step_id="team-board",
            agent_name=author_member_id or "team",
            bead_type="task",
            content=content,
            scope="task",
            tags=tags,
            status="open",
            created_at=now,
            source="team-board",
        )
        return self._store.write(bead)

    def claim_task(
        self,
        *,
        task_id: str,
        task_bead_id: str,
        member_id: str,
    ) -> None:
        """Add a ``claimed_by=<member_id>`` tag to a ``task`` bead.

        Uses the :class:`BeadStore.write` INSERT-OR-REPLACE semantics so
        the bead is updated in place while preserving ``created_at``.  If
        the bead is already claimed by a different member, the existing
        tag is replaced — last-writer-wins.
        """
        bead = self._store.read(task_bead_id)
        if bead is None or bead.bead_type != "task":
            _log.warning("claim_task: bead %s missing or not a task", task_bead_id)
            return
        # Strip any existing claimed_by tag before adding the new one.
        new_tags = [t for t in bead.tags if not t.startswith("claimed_by=")]
        new_tags.append(f"claimed_by={member_id}")
        bead.tags = new_tags
        self._store.write(bead)

    def complete_task(
        self,
        *,
        task_id: str,
        task_bead_id: str,
        outcome: str,
    ) -> None:
        """Close a ``task`` bead and attach *outcome* as its summary."""
        self._store.close(task_bead_id, outcome)

    def open_tasks_for_team(
        self,
        *,
        task_id: str,
        team_id: str,
        member_id: str | None = None,
        limit: int = 100,
    ) -> list[Bead]:
        """Return open ``task`` beads scoped to *team_id*.

        When *member_id* is provided, returns only tasks that are
        unclaimed OR claimed by this member (so the member sees their
        own work plus the common pool).  Other members' claimed tasks
        are filtered out.
        """
        tasks = self._store.query(
            task_id=task_id, bead_type="task", status="open",
            tags=[f"team={team_id}"], limit=limit,
        )
        if member_id is None:
            return tasks
        out: list[Bead] = []
        for t in tasks:
            claimed = [tag for tag in t.tags if tag.startswith("claimed_by=")]
            if not claimed:
                out.append(t)
            elif f"claimed_by={member_id}" in claimed:
                out.append(t)
        return out

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _count(self) -> int:
        """Return an approximate bead count for ID length selection.

        Uses a bounded query so pathological bead counts don't blow up
        ID generation.  Exact count isn't required — it's only used to
        pick hash length.
        """
        try:
            return len(self._store.query(limit=2000))
        except Exception:
            return 0

    def _acked_message_ids(
        self, task_id: str, member_id: str,
    ) -> set[str]:
        """Return message_bead_ids already acked by *member_id*."""
        acks = self._store.query(
            task_id=task_id,
            bead_type="message_ack",
            tags=[f"from_member={member_id}"],
            limit=1000,
        )
        out: set[str] = set()
        for ack in acks:
            for tag in ack.tags:
                if tag.startswith("ack_of="):
                    out.add(tag.split("=", 1)[1])
        return out
