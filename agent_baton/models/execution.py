"""Execution engine models — machine-readable plans, state, and actions."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"


class PhaseStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    GATE_PENDING = "gate_pending"
    COMPLETE = "complete"
    FAILED = "failed"


class ActionType(Enum):
    """What the caller (Claude session) should do next."""
    DISPATCH = "dispatch"       # spawn a subagent with the given prompt
    GATE = "gate"               # run a QA gate check
    COMPLETE = "complete"       # execution is finished
    FAILED = "failed"           # execution cannot continue
    WAIT = "wait"               # parallel steps still running
    APPROVAL = "approval"       # pause for human review / approval


# ---------------------------------------------------------------------------
# Plan (machine-readable, JSON-serializable)
# ---------------------------------------------------------------------------

@dataclass
class TeamMember:
    """A member of a coordinated agent team within a step."""
    member_id: str                          # e.g. "1.1.a"
    agent_name: str
    role: str = "implementer"               # "lead", "implementer", "reviewer"
    task_description: str = ""
    model: str = "sonnet"
    depends_on: list[str] = field(default_factory=list)   # other member_ids
    deliverables: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "member_id": self.member_id,
            "agent_name": self.agent_name,
            "role": self.role,
            "task_description": self.task_description,
            "model": self.model,
            "depends_on": self.depends_on,
            "deliverables": self.deliverables,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TeamMember:
        return cls(
            member_id=data["member_id"],
            agent_name=data["agent_name"],
            role=data.get("role", "implementer"),
            task_description=data.get("task_description", ""),
            model=data.get("model", "sonnet"),
            depends_on=data.get("depends_on", []),
            deliverables=data.get("deliverables", []),
        )


@dataclass
class PlanStep:
    """A single agent assignment in a plan."""
    step_id: str                          # e.g. "1.1"
    agent_name: str
    task_description: str
    model: str = "sonnet"
    depends_on: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    blocked_paths: list[str] = field(default_factory=list)
    context_files: list[str] = field(default_factory=list)  # files agent should read
    team: list[TeamMember] = field(default_factory=list)    # non-empty = team step

    def to_dict(self) -> dict:
        d = {
            "step_id": self.step_id,
            "agent_name": self.agent_name,
            "task_description": self.task_description,
            "model": self.model,
            "depends_on": self.depends_on,
            "deliverables": self.deliverables,
            "allowed_paths": self.allowed_paths,
            "blocked_paths": self.blocked_paths,
            "context_files": self.context_files,
        }
        if self.team:
            d["team"] = [m.to_dict() for m in self.team]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> PlanStep:
        return cls(
            step_id=data["step_id"],
            agent_name=data["agent_name"],
            task_description=data.get("task_description", ""),
            model=data.get("model", "sonnet"),
            depends_on=data.get("depends_on", []),
            deliverables=data.get("deliverables", []),
            allowed_paths=data.get("allowed_paths", []),
            blocked_paths=data.get("blocked_paths", []),
            context_files=data.get("context_files", []),
            team=[TeamMember.from_dict(m) for m in data.get("team", [])],
        )


@dataclass
class PlanGate:
    """A QA gate between phases."""
    gate_type: str              # "build", "test", "lint", "spec", "review"
    command: str = ""           # bash command to run (e.g. "pytest")
    description: str = ""
    fail_on: list[str] = field(default_factory=list)  # criteria for failure

    def to_dict(self) -> dict:
        return {
            "gate_type": self.gate_type,
            "command": self.command,
            "description": self.description,
            "fail_on": self.fail_on,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlanGate:
        return cls(
            gate_type=data["gate_type"],
            command=data.get("command", ""),
            description=data.get("description", ""),
            fail_on=data.get("fail_on", []),
        )


@dataclass
class PlanPhase:
    """A phase in an execution plan."""
    phase_id: int
    name: str
    steps: list[PlanStep] = field(default_factory=list)
    gate: PlanGate | None = None
    approval_required: bool = False         # pause for human approval after steps complete
    approval_description: str = ""          # what the human should review

    def to_dict(self) -> dict:
        d: dict = {
            "phase_id": self.phase_id,
            "name": self.name,
            "steps": [s.to_dict() for s in self.steps],
        }
        if self.gate:
            d["gate"] = self.gate.to_dict()
        if self.approval_required:
            d["approval_required"] = self.approval_required
            d["approval_description"] = self.approval_description
        return d

    @classmethod
    def from_dict(cls, data: dict) -> PlanPhase:
        gate = PlanGate.from_dict(data["gate"]) if data.get("gate") else None
        return cls(
            phase_id=data["phase_id"],
            name=data["name"],
            steps=[PlanStep.from_dict(s) for s in data.get("steps", [])],
            gate=gate,
            approval_required=data.get("approval_required", False),
            approval_description=data.get("approval_description", ""),
        )


@dataclass
class MachinePlan:
    """Machine-readable execution plan — the contract between planner and executor."""
    task_id: str
    task_summary: str
    risk_level: str = "LOW"
    budget_tier: str = "standard"
    execution_mode: str = "phased"
    git_strategy: str = "commit-per-agent"
    phases: list[PlanPhase] = field(default_factory=list)
    shared_context: str = ""            # pre-built context for agents
    pattern_source: str | None = None   # pattern_id that influenced this plan
    created_at: str = ""
    task_type: str = ""                 # inferred task type (bug-fix, new-feature, etc.)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def all_steps(self) -> list[PlanStep]:
        return [s for p in self.phases for s in p.steps]

    @property
    def all_agents(self) -> list[str]:
        return [s.agent_name for s in self.all_steps]

    @property
    def total_steps(self) -> int:
        return len(self.all_steps)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_summary": self.task_summary,
            "risk_level": self.risk_level,
            "budget_tier": self.budget_tier,
            "execution_mode": self.execution_mode,
            "git_strategy": self.git_strategy,
            "phases": [p.to_dict() for p in self.phases],
            "shared_context": self.shared_context,
            "pattern_source": self.pattern_source,
            "created_at": self.created_at,
            "task_type": self.task_type,
        }

    @classmethod
    def from_dict(cls, data: dict) -> MachinePlan:
        return cls(
            task_id=data["task_id"],
            task_summary=data["task_summary"],
            risk_level=data.get("risk_level", "LOW"),
            budget_tier=data.get("budget_tier", "standard"),
            execution_mode=data.get("execution_mode", "phased"),
            git_strategy=data.get("git_strategy", "commit-per-agent"),
            phases=[PlanPhase.from_dict(p) for p in data.get("phases", [])],
            shared_context=data.get("shared_context", ""),
            pattern_source=data.get("pattern_source"),
            created_at=data.get("created_at", ""),
            task_type=data.get("task_type", ""),
        )

    def to_markdown(self) -> str:
        """Render as human-readable markdown (for plan.md)."""
        lines = [
            "# Execution Plan",
            "",
            f"**Task**: {self.task_summary}",
            f"**Task ID**: {self.task_id}",
            f"**Risk Level**: {self.risk_level}",
            f"**Budget Tier**: {self.budget_tier}",
            f"**Execution Mode**: {self.execution_mode}",
            f"**Git Strategy**: {self.git_strategy}",
            f"**Created**: {self.created_at}",
        ]
        if self.pattern_source:
            lines.append(f"**Pattern**: {self.pattern_source}")
        lines.append("")

        for phase in self.phases:
            approval_tag = " [APPROVAL REQUIRED]" if phase.approval_required else ""
            lines.append(f"## Phase {phase.phase_id}: {phase.name}{approval_tag}")
            lines.append("")
            if phase.approval_required and phase.approval_description:
                lines.append(f"> {phase.approval_description}")
                lines.append("")
            for step in phase.steps:
                if step.team:
                    lines.append(f"### Step {step.step_id}: Team")
                    lines.append(f"- **Task**: {step.task_description}")
                    lines.append(f"- **Members**:")
                    for member in step.team:
                        lines.append(f"  - {member.member_id}: {member.agent_name} ({member.role})")
                        if member.task_description:
                            lines.append(f"    {member.task_description}")
                else:
                    lines.append(f"### Step {step.step_id}: {step.agent_name}")
                    lines.append(f"- **Model**: {step.model}")
                    lines.append(f"- **Task**: {step.task_description}")
                if step.depends_on:
                    lines.append(f"- **Depends on**: {', '.join(step.depends_on)}")
                if step.deliverables:
                    lines.append(f"- **Deliverables**: {', '.join(step.deliverables)}")
                if step.allowed_paths:
                    lines.append(f"- **Writes to**: {', '.join(step.allowed_paths)}")
                if step.blocked_paths:
                    lines.append(f"- **Blocked from**: {', '.join(step.blocked_paths)}")
                lines.append("")

            if phase.gate:
                lines.append(f"### Gate: {phase.gate.gate_type}")
                if phase.gate.command:
                    lines.append(f"- **Command**: `{phase.gate.command}`")
                if phase.gate.description:
                    lines.append(f"- {phase.gate.description}")
                lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plan amendments (recorded modifications to the plan during execution)
# ---------------------------------------------------------------------------

@dataclass
class PlanAmendment:
    """A recorded modification to the plan during execution."""
    amendment_id: str
    trigger: str                    # "gate_feedback", "approval_feedback", "manual"
    trigger_phase_id: int
    description: str
    phases_added: list[int] = field(default_factory=list)   # phase_ids of new phases
    steps_added: list[str] = field(default_factory=list)    # step_ids of new steps
    created_at: str = ""
    feedback: str = ""              # reviewer/approver feedback that triggered this

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "amendment_id": self.amendment_id,
            "trigger": self.trigger,
            "trigger_phase_id": self.trigger_phase_id,
            "description": self.description,
            "phases_added": self.phases_added,
            "steps_added": self.steps_added,
            "created_at": self.created_at,
            "feedback": self.feedback,
        }

    @classmethod
    def from_dict(cls, data: dict) -> PlanAmendment:
        return cls(
            amendment_id=data.get("amendment_id", ""),
            trigger=data.get("trigger", "manual"),
            trigger_phase_id=data.get("trigger_phase_id", 0),
            description=data.get("description", ""),
            phases_added=data.get("phases_added", []),
            steps_added=data.get("steps_added", []),
            created_at=data.get("created_at", ""),
            feedback=data.get("feedback", ""),
        )


# ---------------------------------------------------------------------------
# Execution State (persisted between CLI calls)
# ---------------------------------------------------------------------------

@dataclass
class TeamStepResult:
    """Result of a single team member's work within a team step."""
    member_id: str
    agent_name: str
    status: str = "complete"        # complete, failed
    outcome: str = ""
    files_changed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "member_id": self.member_id,
            "agent_name": self.agent_name,
            "status": self.status,
            "outcome": self.outcome,
            "files_changed": self.files_changed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TeamStepResult:
        return cls(
            member_id=data.get("member_id", ""),
            agent_name=data.get("agent_name", ""),
            status=data.get("status", "complete"),
            outcome=data.get("outcome", ""),
            files_changed=data.get("files_changed", []),
        )


@dataclass
class StepResult:
    """Outcome of a single step execution."""
    step_id: str
    agent_name: str
    status: str = "complete"        # complete, failed, dispatched
    outcome: str = ""               # free-text summary
    files_changed: list[str] = field(default_factory=list)
    commit_hash: str = ""
    estimated_tokens: int = 0
    duration_seconds: float = 0.0
    retries: int = 0
    error: str = ""
    completed_at: str = ""
    member_results: list[TeamStepResult] = field(default_factory=list)  # team step results

    def to_dict(self) -> dict:
        d = {
            "step_id": self.step_id,
            "agent_name": self.agent_name,
            "status": self.status,
            "outcome": self.outcome,
            "files_changed": self.files_changed,
            "commit_hash": self.commit_hash,
            "estimated_tokens": self.estimated_tokens,
            "duration_seconds": self.duration_seconds,
            "retries": self.retries,
            "error": self.error,
            "completed_at": self.completed_at,
        }
        if self.member_results:
            d["member_results"] = [m.to_dict() for m in self.member_results]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> StepResult:
        member_results = [
            TeamStepResult.from_dict(m) for m in data.pop("member_results", [])
        ]
        obj = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        obj.member_results = member_results
        return obj


@dataclass
class ApprovalResult:
    """Outcome of a human approval checkpoint."""
    phase_id: int
    result: str                     # "approve", "reject", "approve-with-feedback"
    feedback: str = ""
    decided_at: str = ""

    def __post_init__(self) -> None:
        if not self.decided_at:
            self.decided_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "phase_id": self.phase_id,
            "result": self.result,
            "feedback": self.feedback,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ApprovalResult:
        return cls(
            phase_id=data.get("phase_id", 0),
            result=data.get("result", "approve"),
            feedback=data.get("feedback", ""),
            decided_at=data.get("decided_at", ""),
        )


@dataclass
class GateResult:
    """Outcome of a QA gate check."""
    phase_id: int
    gate_type: str
    passed: bool
    output: str = ""                # command output or reviewer notes
    checked_at: str = ""

    def to_dict(self) -> dict:
        return {
            "phase_id": self.phase_id,
            "gate_type": self.gate_type,
            "passed": self.passed,
            "output": self.output,
            "checked_at": self.checked_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> GateResult:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ExecutionState:
    """Persistent state of a running execution — saved to disk between CLI calls."""
    task_id: str
    plan: MachinePlan
    current_phase: int = 0              # index into plan.phases
    current_step_index: int = 0         # index into current phase's steps
    status: str = "running"             # running, gate_pending, approval_pending, complete, failed
    step_results: list[StepResult] = field(default_factory=list)
    gate_results: list[GateResult] = field(default_factory=list)
    approval_results: list[ApprovalResult] = field(default_factory=list)
    amendments: list[PlanAmendment] = field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = datetime.now(timezone.utc).isoformat()

    @property
    def current_phase_obj(self) -> PlanPhase | None:
        if 0 <= self.current_phase < len(self.plan.phases):
            return self.plan.phases[self.current_phase]
        return None

    @property
    def completed_step_ids(self) -> set[str]:
        return {r.step_id for r in self.step_results if r.status == "complete"}

    @property
    def failed_step_ids(self) -> set[str]:
        return {r.step_id for r in self.step_results if r.status == "failed"}

    @property
    def dispatched_step_ids(self) -> set[str]:
        return {r.step_id for r in self.step_results if r.status == "dispatched"}

    def get_step_result(self, step_id: str) -> StepResult | None:
        for r in self.step_results:
            if r.step_id == step_id:
                return r
        return None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "plan": self.plan.to_dict(),
            "current_phase": self.current_phase,
            "current_step_index": self.current_step_index,
            "status": self.status,
            "step_results": [r.to_dict() for r in self.step_results],
            "gate_results": [g.to_dict() for g in self.gate_results],
            "approval_results": [a.to_dict() for a in self.approval_results],
            "amendments": [a.to_dict() for a in self.amendments],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExecutionState:
        return cls(
            task_id=data["task_id"],
            plan=MachinePlan.from_dict(data["plan"]),
            current_phase=data.get("current_phase", 0),
            current_step_index=data.get("current_step_index", 0),
            status=data.get("status", "running"),
            step_results=[StepResult.from_dict(r) for r in data.get("step_results", [])],
            gate_results=[GateResult.from_dict(g) for g in data.get("gate_results", [])],
            approval_results=[ApprovalResult.from_dict(a) for a in data.get("approval_results", [])],
            amendments=[PlanAmendment.from_dict(a) for a in data.get("amendments", [])],
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
        )


# ---------------------------------------------------------------------------
# Execution Actions (returned by the engine to tell the caller what to do)
# ---------------------------------------------------------------------------

@dataclass
class ExecutionAction:
    """Instruction from the engine to the driving session."""
    action_type: ActionType             # strongly-typed; serialises to str via to_dict()
    message: str = ""                   # human-readable description

    # For DISPATCH actions:
    agent_name: str = ""
    agent_model: str = ""
    delegation_prompt: str = ""
    step_id: str = ""
    # Path enforcement hook command (for PreToolUse):
    path_enforcement: str = ""

    # For GATE actions:
    gate_type: str = ""
    gate_command: str = ""
    phase_id: int = 0

    # For APPROVAL actions:
    approval_context: str = ""          # summary of phase output for reviewer
    approval_options: list[str] = field(default_factory=list)

    # For COMPLETE/FAILED actions:
    summary: str = ""

    # For batch dispatch (parallel steps / team members):
    parallel_actions: list[ExecutionAction] = field(default_factory=list)

    def to_dict(self) -> dict:
        # action_type is serialised as a plain string so CLI / Claude output
        # is unaffected by the internal enum representation.
        d = {"action_type": self.action_type.value, "message": self.message}
        if self.action_type == ActionType.DISPATCH:
            d.update({
                "agent_name": self.agent_name,
                "agent_model": self.agent_model,
                "delegation_prompt": self.delegation_prompt,
                "step_id": self.step_id,
                "path_enforcement": self.path_enforcement,
            })
        elif self.action_type == ActionType.GATE:
            d.update({
                "gate_type": self.gate_type,
                "gate_command": self.gate_command,
                "phase_id": self.phase_id,
            })
        elif self.action_type == ActionType.APPROVAL:
            d.update({
                "phase_id": self.phase_id,
                "approval_context": self.approval_context,
                "approval_options": self.approval_options,
            })
        elif self.action_type in (ActionType.COMPLETE, ActionType.FAILED):
            d["summary"] = self.summary
        if self.parallel_actions:
            d["parallel_actions"] = [a.to_dict() for a in self.parallel_actions]
        return d
