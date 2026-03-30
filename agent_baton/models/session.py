"""Session persistence models — multi-day execution tracking.

Sessions wrap ExecutionState with metadata for long-running workflows
that span hours or days.  They enable checkpoint/resume across daemon
restarts and track all participants (agents and humans).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SessionCheckpoint:
    """A snapshot point within a session for safe resumption.

    Attributes:
        checkpoint_id: Unique identifier for this checkpoint.
        phase_id: Phase index at checkpoint time.
        step_id: Last completed step ID.
        timestamp: ISO 8601 time of checkpoint creation.
        description: What state the execution was in.
    """

    checkpoint_id: str
    phase_id: int
    step_id: str = ""
    timestamp: str = ""
    description: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "checkpoint_id": self.checkpoint_id,
            "phase_id": self.phase_id,
            "step_id": self.step_id,
            "timestamp": self.timestamp,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SessionCheckpoint:
        return cls(
            checkpoint_id=data.get("checkpoint_id", ""),
            phase_id=data.get("phase_id", 0),
            step_id=data.get("step_id", ""),
            timestamp=data.get("timestamp", ""),
            description=data.get("description", ""),
        )


@dataclass
class SessionParticipant:
    """A participant (agent or human) in a session.

    Attributes:
        name: Agent name or human identifier.
        role: Participant role — "agent", "human", "approver", "contributor".
        joined_at: ISO 8601 time the participant first contributed.
        contributions: Count of contributions (step completions, decisions, etc).
    """

    name: str
    role: str = "agent"  # agent | human | approver | contributor
    joined_at: str = ""
    contributions: int = 0

    def __post_init__(self) -> None:
        if not self.joined_at:
            self.joined_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "role": self.role,
            "joined_at": self.joined_at,
            "contributions": self.contributions,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SessionParticipant:
        return cls(
            name=data.get("name", ""),
            role=data.get("role", "agent"),
            joined_at=data.get("joined_at", ""),
            contributions=data.get("contributions", 0),
        )


@dataclass
class SessionState:
    """Persistent session state wrapping an execution for multi-day work.

    Sessions add lifecycle metadata on top of ExecutionState:
    checkpoints for safe resume, participant tracking, and activity
    timestamps that span daemon restarts.

    Attributes:
        session_id: Unique session identifier (typically same as task_id).
        task_id: The ExecutionState.task_id this session wraps.
        created_at: ISO 8601 session creation time.
        last_activity: ISO 8601 time of most recent action.
        status: Session lifecycle — "active", "paused", "completed", "abandoned".
        participants: All agents and humans who contributed.
        checkpoints: Snapshot points for safe resumption.
        pause_reason: Why the session was paused, if applicable.
        metadata: Arbitrary session metadata (e.g. sprint, milestone).
    """

    session_id: str
    task_id: str
    created_at: str = ""
    last_activity: str = ""
    status: str = "active"  # active | paused | completed | abandoned
    participants: list[SessionParticipant] = field(default_factory=list)
    checkpoints: list[SessionCheckpoint] = field(default_factory=list)
    pause_reason: str = ""
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        if not self.last_activity:
            self.last_activity = self.created_at

    def touch(self) -> None:
        """Update last_activity to now."""
        self.last_activity = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")

    def add_participant(self, name: str, role: str = "agent") -> SessionParticipant:
        """Add a participant or increment their contribution count."""
        for p in self.participants:
            if p.name == name:
                p.contributions += 1
                return p
        participant = SessionParticipant(name=name, role=role)
        self.participants.append(participant)
        return participant

    def checkpoint(
        self,
        checkpoint_id: str,
        phase_id: int,
        step_id: str = "",
        description: str = "",
    ) -> SessionCheckpoint:
        """Create a new checkpoint at the current state."""
        cp = SessionCheckpoint(
            checkpoint_id=checkpoint_id,
            phase_id=phase_id,
            step_id=step_id,
            description=description,
        )
        self.checkpoints.append(cp)
        self.touch()
        return cp

    def pause(self, reason: str = "") -> None:
        """Pause the session."""
        self.status = "paused"
        self.pause_reason = reason
        self.touch()

    def resume(self) -> None:
        """Resume a paused session."""
        self.status = "active"
        self.pause_reason = ""
        self.touch()

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "task_id": self.task_id,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "status": self.status,
            "participants": [p.to_dict() for p in self.participants],
            "checkpoints": [c.to_dict() for c in self.checkpoints],
            "pause_reason": self.pause_reason,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SessionState:
        return cls(
            session_id=data.get("session_id", ""),
            task_id=data.get("task_id", ""),
            created_at=data.get("created_at", ""),
            last_activity=data.get("last_activity", ""),
            status=data.get("status", "active"),
            participants=[
                SessionParticipant.from_dict(p) for p in data.get("participants", [])
            ],
            checkpoints=[
                SessionCheckpoint.from_dict(c) for c in data.get("checkpoints", [])
            ],
            pause_reason=data.get("pause_reason", ""),
            metadata=data.get("metadata", {}),
        )
