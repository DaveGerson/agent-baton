"""Data models for Beads-inspired structured memory.

Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

Beads capture discrete units of insight -- discoveries, decisions, warnings,
outcomes, and planning notes -- produced by agents during execution.  They
persist across steps and phases, enabling downstream agents to inherit
upstream context without re-reading raw output.

Unlike the original Beads project (which uses Dolt or JSONL), these models
are backed natively by Agent Baton's existing SQLite storage layer.  See
``core/engine/bead_store.py`` for persistence and
``docs/superpowers/specs/2026-04-12-bead-memory-design.md`` for the full
design rationale.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone


# Recognized bead_type values.
#
# The original agent-signal types (discovery, decision, warning, outcome,
# planning) are joined by three team-coordination types introduced for
# multi-team orchestration (schema v15):
#
# - ``task``         — a work item on the shared team board.  ``open`` with no
#                      ``claimed_by=X`` tag means unclaimed; a ``claimed_by=X``
#                      tag marks the claimer; status=closed means completed.
# - ``message``      — a one-shot communication from one member or team to
#                      another, delivered to the recipient's next dispatch.
# - ``message_ack``  — marks a ``message`` bead as read, suppressing
#                      re-delivery.  Keyed by tag ``ack_of=<message_bead_id>``
#                      plus ``from_member=<recipient_member_id>``.
#
# The addressing tags ride on the existing ``bead_tags`` index so no schema
# change is required on the beads table.
AGENT_SIGNAL_BEAD_TYPES: frozenset[str] = frozenset({
    "discovery", "decision", "warning", "outcome", "planning",
})
TEAM_BOARD_BEAD_TYPES: frozenset[str] = frozenset({
    "task", "message", "message_ack",
})
KNOWN_BEAD_TYPES: frozenset[str] = AGENT_SIGNAL_BEAD_TYPES | TEAM_BOARD_BEAD_TYPES


def is_known_bead_type(bead_type: str) -> bool:
    """Return True when *bead_type* is a recognized value.

    Callers that need strict validation (e.g. ``team_board`` wrappers) can
    check this before writing.  Unknown types are still accepted by
    :class:`BeadStore` — recognition here is advisory and serves as a
    catalog of well-known vocabulary.
    """
    return bead_type in KNOWN_BEAD_TYPES


def _generate_bead_id(
    task_id: str,
    step_id: str,
    content: str,
    timestamp: str,
    bead_count: int,
) -> str:
    """Generate a short hash ID using progressive scaling.

    Uses SHA-256 of ``task_id:step_id:content:timestamp`` truncated to
    a length that scales with the number of beads in the project:

    - < 500 beads:   4 hex chars  (~65k namespace)
    - < 1500 beads:  5 hex chars  (~1M namespace)
    - >= 1500 beads: 6 hex chars  (~16M namespace)

    Returns the ID with a ``bd-`` prefix for visual identification.

    Args:
        task_id: Execution task identifier.
        step_id: Step within the execution, or ``"planning"`` for planner beads.
        content: The bead content text (used as entropy source).
        timestamp: ISO 8601 creation timestamp.
        bead_count: Current total number of beads in the project, used to
            select the appropriate ID length.

    Returns:
        A short hash ID string, e.g. ``"bd-a1b2"``.
    """
    digest = hashlib.sha256(
        f"{task_id}:{step_id}:{content}:{timestamp}".encode()
    ).hexdigest()
    if bead_count >= 1500:
        length = 6
    elif bead_count >= 500:
        length = 5
    else:
        length = 4
    return f"bd-{digest[:length]}"


@dataclass
class BeadLink:
    """A typed dependency link between two beads.

    Inspired by Beads' typed dependency graph concept.  Edges carry
    semantic meaning so that downstream consumers can understand the
    relationship rather than just the fact that two beads are connected.

    Attributes:
        target_bead_id: The bead this link points to.
        link_type: Relationship kind -- ``"blocks"``, ``"blocked_by"``,
            ``"relates_to"``, ``"discovered_from"``, ``"validates"``,
            ``"contradicts"``, or ``"extends"``.
        created_at: ISO 8601 timestamp when the link was created.
    """

    target_bead_id: str
    link_type: str  # "blocks" | "blocked_by" | "relates_to" |
                    # "discovered_from" | "validates" | "contradicts" |
                    # "extends"
    created_at: str = ""

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON storage."""
        return {
            "target_bead_id": self.target_bead_id,
            "link_type": self.link_type,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BeadLink:
        """Deserialise from a plain dict.  Uses ``.get()`` with defaults
        for every field to guarantee backward compatibility with older
        schema versions."""
        return cls(
            target_bead_id=data["target_bead_id"],
            link_type=data.get("link_type", "relates_to"),
            created_at=data.get("created_at", ""),
        )


@dataclass
class Bead:
    """A discrete unit of structured memory produced during execution.

    Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).
    Unlike raw agent output, a Bead is a structured, queryable, typed record
    that persists across steps, phases, and even across executions when
    promoted to a knowledge document.

    Attributes:
        bead_id: Short hash ID (e.g. ``"bd-a1b2"``).
        task_id: Execution that produced this bead.
        step_id: Step within the execution, or ``"planning"`` for beads
            created during plan generation.
        agent_name: Agent that generated this bead.
        bead_type: Agent-signal types — ``"discovery"`` | ``"decision"``
            | ``"warning"`` | ``"outcome"`` | ``"planning"``.  Team-board
            types introduced in schema v15 — ``"task"`` | ``"message"``
            | ``"message_ack"``.  See :data:`KNOWN_BEAD_TYPES`.
        content: The actual insight, discovery, or decision text.
        confidence: ``"high"`` | ``"medium"`` | ``"low"``.
        scope: ``"step"`` | ``"phase"`` | ``"task"`` | ``"project"``.
        tags: Semantic tags for retrieval matching.
        affected_files: Files this bead is about.
        status: ``"open"`` | ``"closed"`` | ``"archived"``.
        created_at: ISO 8601 creation timestamp.
        closed_at: ISO 8601 close timestamp, empty if open.
        summary: Compacted description (populated on close or decay).
        links: Typed dependency links to other beads.
        source: ``"agent-signal"`` | ``"planning-capture"``
            | ``"retrospective"`` | ``"manual"``.
        token_estimate: Approximate token count for budget management.
    """

    bead_id: str
    task_id: str
    step_id: str
    agent_name: str
    bead_type: str
    content: str
    confidence: str = "medium"
    scope: str = "step"
    tags: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    status: str = "open"
    created_at: str = ""
    closed_at: str = ""
    summary: str = ""
    links: list[BeadLink] = field(default_factory=list)
    source: str = "agent-signal"
    token_estimate: int = 0
    quality_score: float = 0.0
    retrieval_count: int = 0
    # Gastown Part A fields (bd-2870).  All default to empty string so that
    # existing beads round-trip without change.
    schema_version: str = "gastown-1"
    anchor_commit: str = ""
    branch_at_create: str = ""

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON storage."""
        return {
            "bead_id": self.bead_id,
            "task_id": self.task_id,
            "step_id": self.step_id,
            "agent_name": self.agent_name,
            "bead_type": self.bead_type,
            "content": self.content,
            "confidence": self.confidence,
            "scope": self.scope,
            "tags": self.tags,
            "affected_files": self.affected_files,
            "status": self.status,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "summary": self.summary,
            "links": [lnk.to_dict() for lnk in self.links],
            "source": self.source,
            "token_estimate": self.token_estimate,
            "quality_score": self.quality_score,
            "retrieval_count": self.retrieval_count,
            # Gastown Part A fields (bd-2870)
            "schema_version": self.schema_version,
            "anchor_commit": self.anchor_commit,
            "branch_at_create": self.branch_at_create,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Bead:
        """Deserialise from a plain dict.  Uses ``.get()`` with defaults
        for every field to guarantee backward compatibility with older
        schema versions that may be missing some columns."""
        return cls(
            bead_id=data["bead_id"],
            task_id=data.get("task_id", ""),
            step_id=data.get("step_id", ""),
            agent_name=data.get("agent_name", ""),
            bead_type=data.get("bead_type", "discovery"),
            content=data.get("content", ""),
            confidence=data.get("confidence", "medium"),
            scope=data.get("scope", "step"),
            tags=data.get("tags", []),
            affected_files=data.get("affected_files", []),
            status=data.get("status", "open"),
            created_at=data.get("created_at", ""),
            closed_at=data.get("closed_at", ""),
            summary=data.get("summary", ""),
            links=[BeadLink.from_dict(d) for d in data.get("links", [])],
            source=data.get("source", "agent-signal"),
            token_estimate=int(data.get("token_estimate", 0)),
            quality_score=float(data.get("quality_score", 0.0)),
            retrieval_count=int(data.get("retrieval_count", 0)),
            # Gastown Part A fields (bd-2870) — use .get() for legacy load
            schema_version=data.get("schema_version", ""),
            anchor_commit=data.get("anchor_commit", ""),
            branch_at_create=data.get("branch_at_create", ""),
        )
