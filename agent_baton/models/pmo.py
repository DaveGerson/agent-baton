"""PMO data models — portfolio management overlay for orchestration plans."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

PMO_COLUMNS = [
    "queued",
    "planning",
    "executing",
    "awaiting_human",
    "validating",
    "deployed",
]

# Map ExecutionState.status → PmoCard.column
_STATUS_TO_COLUMN: dict[str, str] = {
    "running": "executing",
    "gate_pending": "validating",
    "approval_pending": "awaiting_human",
    "complete": "deployed",
    "failed": "executing",  # stays in executing with error flag
}


def status_to_column(execution_status: str | None) -> str:
    """Map an ExecutionState status string to a Kanban column."""
    if execution_status is None:
        return "queued"
    return _STATUS_TO_COLUMN.get(execution_status, "executing")


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

@dataclass
class PmoProject:
    """A project registered with the PMO."""
    project_id: str                             # slug, e.g. "nds"
    name: str
    path: str                                   # absolute filesystem path
    program: str                                # e.g. "RW", "PROJ2"
    color: str = ""
    description: str = ""
    registered_at: str = ""                     # ISO 8601
    # Reserved for future ADO integration
    ado_project: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PmoProject:
        return cls(
            project_id=data["project_id"],
            name=data["name"],
            path=data["path"],
            program=data["program"],
            color=data.get("color", ""),
            description=data.get("description", ""),
            registered_at=data.get("registered_at", ""),
            ado_project=data.get("ado_project", ""),
        )


# ---------------------------------------------------------------------------
# Kanban Card
# ---------------------------------------------------------------------------

@dataclass
class PmoCard:
    """A Kanban card representing a plan's lifecycle state."""
    card_id: str                                # task_id from MachinePlan
    project_id: str
    program: str
    title: str                                  # task_summary
    column: str                                 # one of PMO_COLUMNS
    risk_level: str = "LOW"
    priority: int = 0                           # 0=normal, 1=high, 2=critical
    agents: list[str] = field(default_factory=list)
    steps_completed: int = 0
    steps_total: int = 0
    gates_passed: int = 0
    current_phase: str = ""
    error: str = ""                             # set when status=failed
    created_at: str = ""                        # ISO 8601
    updated_at: str = ""
    # Reserved for future ADO integration
    external_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PmoCard:
        return cls(
            card_id=data["card_id"],
            project_id=data["project_id"],
            program=data["program"],
            title=data["title"],
            column=data["column"],
            risk_level=data.get("risk_level", "LOW"),
            priority=data.get("priority", 0),
            agents=data.get("agents", []),
            steps_completed=data.get("steps_completed", 0),
            steps_total=data.get("steps_total", 0),
            gates_passed=data.get("gates_passed", 0),
            current_phase=data.get("current_phase", ""),
            error=data.get("error", ""),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            external_id=data.get("external_id", ""),
        )


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------

@dataclass
class PmoSignal:
    """A signal (bug, escalation, blocker) in the Signals Bar."""
    signal_id: str
    signal_type: str                            # bug|escalation|blocker
    title: str
    description: str = ""
    source_project_id: str = ""
    severity: str = "medium"                    # low|medium|high|critical
    status: str = "open"                        # open|triaged|resolved
    created_at: str = ""                        # ISO 8601
    resolved_at: str = ""
    forge_task_id: str = ""                     # if this spawned a Forge plan

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PmoSignal:
        return cls(
            signal_id=data["signal_id"],
            signal_type=data["signal_type"],
            title=data["title"],
            description=data.get("description", ""),
            source_project_id=data.get("source_project_id", ""),
            severity=data.get("severity", "medium"),
            status=data.get("status", "open"),
            created_at=data.get("created_at", ""),
            resolved_at=data.get("resolved_at", ""),
            forge_task_id=data.get("forge_task_id", ""),
        )


# ---------------------------------------------------------------------------
# Program Health
# ---------------------------------------------------------------------------

@dataclass
class ProgramHealth:
    """Aggregate health metrics for a program."""
    program: str
    total_plans: int = 0
    active: int = 0
    completed: int = 0
    blocked: int = 0
    failed: int = 0
    completion_pct: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ProgramHealth:
        return cls(
            program=data["program"],
            total_plans=data.get("total_plans", 0),
            active=data.get("active", 0),
            completed=data.get("completed", 0),
            blocked=data.get("blocked", 0),
            failed=data.get("failed", 0),
            completion_pct=data.get("completion_pct", 0.0),
        )


# ---------------------------------------------------------------------------
# PMO Config (top-level persistent state)
# ---------------------------------------------------------------------------

@dataclass
class PmoConfig:
    """Global PMO configuration — persisted to ~/.baton/pmo-config.json."""
    projects: list[PmoProject] = field(default_factory=list)
    programs: list[str] = field(default_factory=list)
    signals: list[PmoSignal] = field(default_factory=list)
    version: str = "1"

    def to_dict(self) -> dict:
        return {
            "projects": [p.to_dict() for p in self.projects],
            "programs": self.programs,
            "signals": [s.to_dict() for s in self.signals],
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PmoConfig:
        return cls(
            projects=[
                PmoProject.from_dict(p) for p in data.get("projects", [])
            ],
            programs=data.get("programs", []),
            signals=[
                PmoSignal.from_dict(s) for s in data.get("signals", [])
            ],
            version=data.get("version", "1"),
        )


# ---------------------------------------------------------------------------
# Interview (Forge refinement)
# ---------------------------------------------------------------------------

@dataclass
class InterviewQuestion:
    """A structured question generated during Forge plan refinement."""
    id: str
    question: str
    context: str
    answer_type: str                        # "choice" or "text"
    choices: list[str] | None = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "question": self.question,
            "context": self.context,
            "answer_type": self.answer_type,
        }
        if self.choices is not None:
            d["choices"] = self.choices
        return d

    @classmethod
    def from_dict(cls, data: dict) -> InterviewQuestion:
        return cls(
            id=data["id"],
            question=data["question"],
            context=data.get("context", ""),
            answer_type=data.get("answer_type", "text"),
            choices=data.get("choices"),
        )


@dataclass
class InterviewAnswer:
    """User's answer to an interview question."""
    question_id: str
    answer: str

    def to_dict(self) -> dict:
        return {"question_id": self.question_id, "answer": self.answer}

    @classmethod
    def from_dict(cls, data: dict) -> InterviewAnswer:
        return cls(question_id=data["question_id"], answer=data["answer"])
