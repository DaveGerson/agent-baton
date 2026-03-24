"""Data models for knowledge delivery during plan execution."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class KnowledgeDocument:
    """A single knowledge document within a pack."""
    name: str
    description: str
    source_path: Path | None = None
    content: str = ""               # loaded on demand, not at index time
    tags: list[str] = field(default_factory=list)
    grounding: str = ""             # agent-facing context string
    priority: str = "normal"        # high | normal | low
    token_estimate: int = 0         # auto-computed by registry

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "source_path": str(self.source_path) if self.source_path else None,
            "content": self.content,
            "tags": self.tags,
            "grounding": self.grounding,
            "priority": self.priority,
            "token_estimate": self.token_estimate,
        }

    @classmethod
    def from_dict(cls, data: dict) -> KnowledgeDocument:
        raw_path = data.get("source_path")
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            source_path=Path(raw_path) if raw_path else None,
            content=data.get("content", ""),
            tags=data.get("tags", []),
            grounding=data.get("grounding", ""),
            priority=data.get("priority", "normal"),
            token_estimate=int(data.get("token_estimate", 0)),
        )


@dataclass
class KnowledgePack:
    """A curated collection of related knowledge documents."""
    name: str
    description: str
    source_path: Path | None = None
    tags: list[str] = field(default_factory=list)
    target_agents: list[str] = field(default_factory=list)
    default_delivery: str = "reference"   # inline | reference
    documents: list[KnowledgeDocument] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "source_path": str(self.source_path) if self.source_path else None,
            "tags": self.tags,
            "target_agents": self.target_agents,
            "default_delivery": self.default_delivery,
            "documents": [d.to_dict() for d in self.documents],
        }

    @classmethod
    def from_dict(cls, data: dict) -> KnowledgePack:
        raw_path = data.get("source_path")
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            source_path=Path(raw_path) if raw_path else None,
            tags=data.get("tags", []),
            target_agents=data.get("target_agents", []),
            default_delivery=data.get("default_delivery", "reference"),
            documents=[KnowledgeDocument.from_dict(d) for d in data.get("documents", [])],
        )


@dataclass
class KnowledgeAttachment:
    """A resolved knowledge item attached to a plan step."""
    source: str          # "explicit" | "agent-declared" | "planner-matched:tag"
                         # | "planner-matched:relevance" | "gap-suggested"
    pack_name: str | None    # None for standalone docs
    document_name: str
    path: str                # filesystem path
    delivery: str            # "inline" | "reference"
    retrieval: str = "file"  # "file" | "mcp-rag"
    grounding: str = ""      # agent-facing context string
    token_estimate: int = 0

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "pack_name": self.pack_name,
            "document_name": self.document_name,
            "path": self.path,
            "delivery": self.delivery,
            "retrieval": self.retrieval,
            "grounding": self.grounding,
            "token_estimate": self.token_estimate,
        }

    @classmethod
    def from_dict(cls, data: dict) -> KnowledgeAttachment:
        return cls(
            source=data["source"],
            pack_name=data.get("pack_name"),
            document_name=data["document_name"],
            path=data["path"],
            delivery=data["delivery"],
            retrieval=data.get("retrieval", "file"),
            grounding=data.get("grounding", ""),
            token_estimate=int(data.get("token_estimate", 0)),
        )


@dataclass
class KnowledgeGapSignal:
    """Parsed from agent output when they self-interrupt for knowledge."""
    description: str
    confidence: str      # none | low | partial
    gap_type: str        # factual | contextual
    step_id: str
    agent_name: str
    partial_outcome: str = ""   # work completed before the gap

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "confidence": self.confidence,
            "gap_type": self.gap_type,
            "step_id": self.step_id,
            "agent_name": self.agent_name,
            "partial_outcome": self.partial_outcome,
        }

    @classmethod
    def from_dict(cls, data: dict) -> KnowledgeGapSignal:
        return cls(
            description=data["description"],
            confidence=data.get("confidence", "low"),
            gap_type=data.get("gap_type", "factual"),
            step_id=data["step_id"],
            agent_name=data["agent_name"],
            partial_outcome=data.get("partial_outcome", ""),
        )


@dataclass
class KnowledgeGapRecord:
    """Persisted in retrospective data for the feedback loop."""
    description: str
    gap_type: str            # factual | contextual
    resolution: str          # auto-resolved | human-answered | best-effort | unresolved
    resolution_detail: str   # pack/doc that resolved it, or the human's answer
    agent_name: str
    task_summary: str
    task_type: str | None = None

    # ---------------------------------------------------------------------------
    # Backward-compatibility aliases for code that reads the old KnowledgeGap
    # schema (e.g. SQLite backend, migration layer).  These attrs are read-only
    # properties so duck-typing against KnowledgeGap still works without any
    # changes to core/ or storage/ code.
    # ---------------------------------------------------------------------------

    @property
    def affected_agent(self) -> str:
        """Compatibility alias for agent_name (old KnowledgeGap schema)."""
        return self.agent_name

    @property
    def suggested_fix(self) -> str:
        """Compatibility alias for resolution_detail (old KnowledgeGap schema)."""
        return self.resolution_detail

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "gap_type": self.gap_type,
            "resolution": self.resolution,
            "resolution_detail": self.resolution_detail,
            "agent_name": self.agent_name,
            "task_summary": self.task_summary,
            "task_type": self.task_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> KnowledgeGapRecord:
        return cls(
            description=data["description"],
            gap_type=data.get("gap_type", "factual"),
            resolution=data.get("resolution", "unresolved"),
            resolution_detail=data.get("resolution_detail", ""),
            agent_name=data.get("agent_name", ""),
            task_summary=data.get("task_summary", ""),
            task_type=data.get("task_type"),
        )


@dataclass
class ResolvedDecision:
    """A knowledge gap that has been answered — injected on re-dispatch as final."""
    gap_description: str
    resolution: str      # human answer or "auto-resolved via {pack_name}"
    step_id: str
    timestamp: str

    def to_dict(self) -> dict:
        return {
            "gap_description": self.gap_description,
            "resolution": self.resolution,
            "step_id": self.step_id,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ResolvedDecision:
        return cls(
            gap_description=data["gap_description"],
            resolution=data["resolution"],
            step_id=data["step_id"],
            timestamp=data.get("timestamp", ""),
        )
