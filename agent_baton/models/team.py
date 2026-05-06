"""Team registry entity for multi-team orchestration.

A :class:`Team` is a stable identity for a coordinated group of agents that
spans the lifetime of a team step (or a nested sub-team carved out by a
lead).  Teams live in the ``teams`` table (added in schema v15) and are
referenced by ``team_id`` — not by leader_agent — so a single agent can
lead multiple concurrent teams without contention.

Nested teams: when a :class:`~agent_baton.models.execution.TeamMember` with
``role == "lead"`` carries a non-empty ``sub_team``, the engine registers a
child :class:`Team` whose ``parent_team_id`` points to the enclosing team.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Team:
    """Persistent identity for a coordinated group of agents.

    Attributes:
        team_id: Stable identifier, unique per ``task_id``.  Conventional
            form: ``"team-<step_id>"`` for top-level teams and
            ``"<step_id>::<member_id>"`` for nested sub-teams.
        task_id: Execution that owns this team.
        step_id: Step that created the team (for top-level teams) or the
            parent member_id (for nested sub-teams carved out by a lead).
        parent_team_id: ``team_id`` of the enclosing team when nested; empty
            string for top-level teams.
        leader_agent: Agent name of the member with ``role == "lead"``.
        leader_member_id: ``member_id`` of the lead.
        status: Lifecycle state — ``"active"`` | ``"complete"`` | ``"failed"``.
        created_at: ISO 8601 creation timestamp, auto-set in ``__post_init__``.
    """

    team_id: str
    task_id: str
    step_id: str
    leader_agent: str
    leader_member_id: str
    parent_team_id: str = ""
    status: str = "active"
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "team_id": self.team_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "parent_team_id": self.parent_team_id,
            "leader_agent": self.leader_agent,
            "leader_member_id": self.leader_member_id,
            "status": self.status,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Team:
        return cls(
            team_id=data["team_id"],
            task_id=data["task_id"],
            step_id=data.get("step_id", ""),
            leader_agent=data.get("leader_agent", ""),
            leader_member_id=data.get("leader_member_id", ""),
            parent_team_id=data.get("parent_team_id", ""),
            status=data.get("status", "active"),
            created_at=data.get("created_at", ""),
        )
