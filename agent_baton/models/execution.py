"""Execution engine models — machine-readable plans, state, and actions.

This module defines the core data structures that flow through the
execution engine: the plan hierarchy (``MachinePlan`` > ``PlanPhase`` >
``PlanStep``), the persistent ``ExecutionState`` saved between CLI calls,
result records (``StepResult``, ``GateResult``, ``ApprovalResult``), and
the ``ExecutionAction`` instructions returned to the driving session.

These models are the contract between the planner, executor, and CLI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from agent_baton.models.knowledge import (
    KnowledgeAttachment,
    KnowledgeGapSignal,
    ResolvedDecision,
)
from agent_baton.models.parallel import ResourceLimits
from agent_baton.models.taxonomy import ForesightInsight

# Matches team-member IDs of the form N.N.x and nested forms N.N.x.y...
# (e.g. "1.1.a", "2.3.b", "1.1.a.b" for nested sub-team members).
# Used in ExecutionAction.to_dict() to set the is_team_member flag.
_TEAM_MEMBER_ID_RE = re.compile(r'^\d+\.\d+(?:\.[a-z]+)+$')


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StepStatus(Enum):
    """Lifecycle state of a single plan step."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"
    INTERRUPTED = "interrupted"
    INTERACTING = "interacting"              # agent responded, awaiting human input
    INTERACT_DISPATCHED = "interact_dispatched"  # human input given, agent re-dispatched


class PhaseStatus(Enum):
    """Lifecycle state of a plan phase."""

    PENDING = "pending"
    RUNNING = "running"
    GATE_PENDING = "gate_pending"
    COMPLETE = "complete"
    FAILED = "failed"


class ActionType(Enum):
    """What the caller (Claude session) should do next."""
    DISPATCH = "dispatch"               # spawn a subagent with the given prompt
    GATE = "gate"                       # run a QA gate check
    COMPLETE = "complete"               # execution is finished
    FAILED = "failed"                   # execution cannot continue
    WAIT = "wait"                       # parallel steps still running
    APPROVAL = "approval"               # pause for human review / approval
    FEEDBACK = "feedback"               # present multiple-choice questions, dispatch based on answers
    INTERACT = "interact"               # multi-turn interaction: agent responded, awaiting human input
    SWARM_DISPATCH = "swarm.dispatch"   # Wave 6.2 (bd-2b9f): trigger a SwarmDispatcher run
    CHECKPOINT = "checkpoint"            # save state + suggest fresh session to prevent context rot


# ---------------------------------------------------------------------------
# Result-type base class (Pydantic) — pre-Phase-1 prototype
# ---------------------------------------------------------------------------
#
# Single Pydantic base for every persisted execution result/decision record.
# Hosts the shared ``model_config`` and a default ``to_dict`` / ``from_dict``
# pair so that subclasses can drop the recurring boilerplate (and the
# ``cls.__dataclass_fields__`` introspection trap that breaks under Pydantic).
#
# Design rationale, scope, and the one-class-vs-two-level decision are
# documented in ``docs/internal/result-hierarchy-proposal.md``.  Mutation
# semantics (why ``frozen=False`` + ``validate_assignment=False`` are
# required) are documented in
# ``docs/internal/pydantic-migration-mutation-audit.md``.
#
# IMPORTANT: this base deliberately holds NO fields.  Promoting any single
# field (timestamp / phase_id / decision_source / actor) to the base would
# change the on-disk JSON shape of at least one subclass and break the
# Phase 0 byte-identical roundtrip tests.  Each subclass keeps its own
# field set verbatim.

class ExecutionRecord(BaseModel):
    """Common base for persisted execution result/decision records.

    Provides:
        - ``extra="ignore"`` — unknown keys in ``from_dict`` payloads are
          dropped silently (forward-compat for older / newer state files,
          and a structural replacement for the
          ``cls.__dataclass_fields__`` filter that ``GateResult`` and
          ``StepResult`` historically used).
        - Mutable instances — list / dict fields can be ``.append``-ed in
          place, matching the dataclass mutation semantics audited in
          ``docs/internal/pydantic-migration-mutation-audit.md``.
        - A default ``to_dict()`` / ``from_dict()`` pair that subclasses
          with no conditional emission (e.g. ``GateResult``) inherit
          unchanged.

    Subclasses override ``to_dict`` only when they must omit empty nested
    collections (e.g. ``StepResult.member_results``) or order keys for
    fixture stability.  Subclasses override ``from_dict`` only when they
    must re-hydrate nested objects whose types are not yet Pydantic
    models.

    Holds no fields — every subclass keeps the exact field set it had as
    a dataclass so the on-disk JSON shape stays byte-identical with the
    Phase 0 golden fixtures.
    """

    model_config = ConfigDict(
        extra="ignore",             # forward-compat; replaces __dataclass_fields__ filter
        validate_assignment=False,  # match dataclass mutation semantics (see audit Cat. 1-3)
        arbitrary_types_allowed=False,
    )

    def to_dict(self) -> dict[str, Any]:
        """Default serialisation. Override for conditional / ordered emission."""
        return self.model_dump(mode="python")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Default deserialisation. ``extra="ignore"`` drops unknown keys.

        Returns the concrete subclass type rather than ``ExecutionRecord``
        so Pyright accepts ``GateResult.from_dict(...)`` as ``GateResult``.
        Per result-hierarchy-proposal §8.5 (Pyright concern).
        """
        return cls(**data)


def _now_iso_seconds() -> str:
    """Auto-stamp factory used by Pydantic leaf records.

    Replaces the ``__post_init__`` empty-string-then-stamp idiom from the
    dataclass era.  The golden fixtures already carry concrete timestamps,
    so this factory only fires when a record is constructed without one.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Interaction history (multi-turn step conversations)
# ---------------------------------------------------------------------------

class InteractionTurn(ExecutionRecord):
    """A single turn in a multi-turn agent interaction.

    Recorded in :class:`StepResult.interaction_history` for interactive steps
    that use the INTERACT protocol.  Each turn is either an agent response or
    human input.

    Attributes:
        role: Either ``"agent"`` (step output) or ``"human"`` (orchestrator
            input via ``baton execute interact``).
        content: The text of this turn.
        timestamp: ISO 8601 timestamp, auto-stamped via ``default_factory``
            when not supplied at construction.
        turn_number: Sequential 1-based turn index within the interaction.
        source: Origin of a human-role turn.  One of:

            - ``"human"`` — typed by a person (CLI or PMO UI).
            - ``"auto-agent"`` — generated by Tier A daemon auto-resolution.
            - ``"webhook"`` — received via external webhook response.

            Agent-role turns always have source ``"agent"`` (same as role).
            Defaults to ``"human"`` so existing states deserialise correctly.
    """

    role: str                   # "agent" or "human"
    content: str = ""
    timestamp: str = Field(default_factory=_now_iso_seconds)
    turn_number: int = 0
    source: str = "human"       # "human" | "auto-agent" | "webhook"


# ---------------------------------------------------------------------------
# Plan (machine-readable, JSON-serializable)
# ---------------------------------------------------------------------------

# Plan-tier base.  Distinct from ExecutionRecord (which is reserved for
# result/decision records, per result-hierarchy-proposal §8.4).  Plan
# types — MachinePlan, PlanPhase, PlanStep, etc. — are LLM-input bound
# rather than persisted-result bound, so they get their own base class
# with the same forward-compat config.
class PlanModel(BaseModel):
    """Common base for plan-tier Pydantic models.

    Provides extra="ignore" forward-compat (newer agents may emit fields
    older planners do not know about) and the same validate_assignment=False
    mutation semantics the dataclass plan types had.  Each subclass keeps
    its own to_dict for conditional emission semantics — Pydantic's
    model_dump emits all fields unconditionally, which would change the
    on-disk JSON shape vs. the golden fixtures.
    """

    model_config = ConfigDict(
        extra="ignore",
        validate_assignment=False,
        arbitrary_types_allowed=True,  # for ResourceLimits / ForesightInsight dataclasses
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Default deserialisation. Subclasses override when nested
        dataclass types need explicit re-hydration."""
        return cls(**data)


class SynthesisSpec(PlanModel):
    """Configuration for how team member outputs are combined.

    Attached to a :class:`PlanStep` to control how the engine merges
    results when all team members complete.

    Attributes:
        strategy: How outputs are combined:
            - ``"concatenate"`` — join outcomes with ``"; "`` (default,
              backward-compatible).
            - ``"merge_files"`` — collect all ``files_changed`` and
              deduplicate; outcomes concatenated.
            - ``"agent_synthesis"`` — dispatch a synthesis agent to
              merge outputs into a coherent whole.
        synthesis_agent: Agent name for ``agent_synthesis`` strategy.
            Defaults to ``"code-reviewer"`` if unset.
        synthesis_prompt: Optional custom prompt template for the
            synthesis agent.  ``{member_outcomes}`` is replaced with
            the formatted member results.
        conflict_handling: How to handle detected conflicts between
            member outputs:
            - ``"auto_merge"`` — attempt automatic merge (default).
            - ``"escalate"`` — surface conflict to human via APPROVAL
              action with both positions.
            - ``"fail"`` — fail the team step if conflicts detected.
    """

    strategy: str = "concatenate"
    synthesis_agent: str = "code-reviewer"
    synthesis_prompt: str = ""
    conflict_handling: str = "auto_merge"

    def to_dict(self) -> dict:
        return self.model_dump(mode="python")


class TeamMember(PlanModel):
    """A member of a coordinated agent team within a step.

    Team steps allow multiple agents to collaborate on a single step,
    with intra-step dependency ordering.  Each member is dispatched
    individually and results are collected via ``TeamStepResult``.

    Attributes:
        member_id: Hierarchical ID (e.g. ``"1.1.a"``).
        agent_name: Name of the agent assigned to this role.
        role: Function within the team — ``"lead"``, ``"implementer"``,
            or ``"reviewer"``.
        task_description: What this team member should do.
        model: LLM model to use.
        depends_on: Other ``member_id`` values that must complete first.
        deliverables: Expected output artifacts.
        sub_team: Nested team the lead coordinates.  Non-empty only when
            ``role == "lead"``; enforced by :meth:`validate`.  The lead is
            still dispatched as a worker — its own outcome is merged with
            sub-team outcomes by the enclosing step's ``synthesis``.
        synthesis: How the lead's own outcome is merged with sub-team
            outcomes.  Meaningful only when ``sub_team`` is non-empty.
    """

    member_id: str
    agent_name: str
    role: str = "implementer"
    task_description: str = ""
    model: str = "sonnet"
    depends_on: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    sub_team: list[TeamMember] = Field(default_factory=list)
    synthesis: SynthesisSpec | None = None

    def validate(self) -> None:
        """Raise ``ValueError`` when the member carries a ``sub_team`` but is
        not a lead.  Sub-teams may only be attached to ``role == "lead"``."""
        if self.sub_team and self.role != "lead":
            raise ValueError(
                f"TeamMember {self.member_id!r} has a sub_team but role={self.role!r}; "
                "only role='lead' members may carry a sub_team."
            )

    def to_dict(self) -> dict:
        d: dict = {
            "member_id": self.member_id,
            "agent_name": self.agent_name,
            "role": self.role,
            "task_description": self.task_description,
            "model": self.model,
            "depends_on": list(self.depends_on),
            "deliverables": list(self.deliverables),
        }
        if self.sub_team:
            d["sub_team"] = [m.to_dict() for m in self.sub_team]
        if self.synthesis is not None:
            d["synthesis"] = self.synthesis.to_dict()
        return d


class PlanStep(PlanModel):
    """A single agent assignment within a plan phase.

    Steps are the atomic unit of work dispatched to agents.  They carry
    the task description, path constraints (sandbox), dependency ordering,
    and any knowledge attachments resolved during planning.

    Attributes:
        step_id: Hierarchical ID (e.g. ``"1.1"`` = phase 1, step 1).
        agent_name: Agent to dispatch for this step.
        task_description: What the agent should accomplish.
        model: LLM model to use for this dispatch.
        depends_on: ``step_id`` values that must complete before this step.
        deliverables: Expected output artifacts.
        allowed_paths: Filesystem paths the agent may write to (sandbox).
        blocked_paths: Filesystem paths the agent must not modify.
        context_files: Files the agent should read before starting.
        team: If non-empty, this is a team step with multiple members.
        knowledge: Knowledge documents attached by the planner.
        synthesis: How to merge team member outputs.  Only meaningful
            when ``team`` is non-empty.
        expected_outcome: One-sentence behavioral statement describing what
            should be observably true after this step completes.  Wave 3.1
            (Demo Statement) — used as the primary prompt anchor for
            ``code-reviewer`` and ``test-engineer`` to shift review from
            "no errors" to "behavioral correctness".  Empty string means
            no outcome was derived (preserves back-compat for older plans).
        parallel_safe: Annotated ``True`` by the planner (bd-a379) when this
            step shares its ``depends_on`` set with at least one sibling and
            every such sibling has a disjoint ``allowed_paths`` set.  Defaults
            to ``False`` (conservative — sequential) for any step the planner
            cannot prove is safe to run concurrently.  Orchestrators SHOULD
            dispatch concurrent worktree-isolated agents when this is ``True``.
    """

    step_id: str
    agent_name: str
    task_description: str
    model: str = "sonnet"
    depends_on: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    blocked_paths: list[str] = Field(default_factory=list)
    context_files: list[str] = Field(default_factory=list)
    team: list[TeamMember] = Field(default_factory=list)
    # KnowledgeAttachment lives in models/knowledge.py and is still a
    # dataclass; arbitrary_types_allowed on PlanModel lets us hold it.
    # The from_dict override below explicitly re-hydrates from dict.
    knowledge: list[Any] = Field(default_factory=list)
    synthesis: SynthesisSpec | None = None
    mcp_servers: list[str] = Field(default_factory=list)
    interactive: bool = False
    max_turns: int = 10
    step_type: str = "developing"
    command: str = ""
    expected_outcome: str = ""
    timeout_seconds: int = 0
    parallel_safe: bool = False
    max_estimated_minutes: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        # KnowledgeAttachment is still a dataclass (intentionally out of
        # scope for slice 11 to keep the diff bounded — see
        # migration-review-summary.md §5 slice 11 scope notes).  Use its
        # from_dict so the typed instance is constructed.
        from agent_baton.models.knowledge import KnowledgeAttachment

        data = dict(data)
        knowledge_in = data.pop("knowledge", [])
        knowledge_v = [
            (k if isinstance(k, KnowledgeAttachment)
             else KnowledgeAttachment.from_dict(k))
            for k in knowledge_in
        ]
        obj = cls(**data)
        obj.knowledge = knowledge_v
        return obj

    def to_dict(self) -> dict:
        d = {
            "step_id": self.step_id,
            "agent_name": self.agent_name,
            "task_description": self.task_description,
            "model": self.model,
            "depends_on": list(self.depends_on),
            "deliverables": list(self.deliverables),
            "allowed_paths": list(self.allowed_paths),
            "blocked_paths": list(self.blocked_paths),
            "context_files": list(self.context_files),
            "step_type": self.step_type,
        }
        if self.team:
            d["team"] = [m.to_dict() for m in self.team]
        if self.knowledge:
            d["knowledge"] = [k.to_dict() for k in self.knowledge]
        if self.synthesis is not None:
            d["synthesis"] = self.synthesis.to_dict()
        if self.mcp_servers:
            d["mcp_servers"] = list(self.mcp_servers)
        if self.interactive:
            d["interactive"] = self.interactive
            d["max_turns"] = self.max_turns
        if self.command:
            d["command"] = self.command
        if self.expected_outcome:
            d["expected_outcome"] = self.expected_outcome
        if self.timeout_seconds:
            d["timeout_seconds"] = self.timeout_seconds
        if self.parallel_safe:
            d["parallel_safe"] = self.parallel_safe
        if self.max_estimated_minutes:
            d["max_estimated_minutes"] = self.max_estimated_minutes
        return d


class PlanGate(PlanModel):
    """A QA gate that must pass before advancing to the next phase.

    Gates run automated checks (tests, linting, builds) or request
    manual review.  Gate failure triggers the ``GATE`` action in the
    execution loop, giving the orchestrator a chance to re-plan or
    request remediation.

    Attributes:
        gate_type: Category — ``"build"``, ``"test"``, ``"lint"``,
            ``"spec"``, or ``"review"``.
        command: Bash command to run (e.g. ``"pytest"``).
        description: Human-readable explanation of what the gate checks.
        fail_on: Criteria that constitute a failure.
    """

    gate_type: str
    command: str = ""
    description: str = ""
    fail_on: list[str] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        # Accept both "gate_type" (canonical) and "type" (common LLM variant).
        # The legacy variant predates the slice-11 conversion; older state
        # files and a few test fixtures still emit the alternate spelling.
        data = dict(data)
        if "gate_type" not in data and "type" in data:
            data["gate_type"] = data.pop("type")
        return cls(**data)

    def to_dict(self) -> dict:
        return self.model_dump(mode="python")


class PlanPhase(PlanModel):
    """A phase in an execution plan, containing steps and an optional gate.

    Phases group related steps and enforce a gate check or human approval
    before the execution engine advances to the next phase.  The planner
    creates phases based on logical work boundaries and risk thresholds.

    Attributes:
        phase_id: Sequential integer identifier.
        name: Human-readable phase name (e.g. ``"Implementation"``).
        steps: Ordered list of steps to execute in this phase.
        gate: Optional QA gate to run after all steps complete.
        approval_required: If ``True``, pause for human approval after
            steps complete (before the gate, if any).
        approval_description: What the human should review.
        risk_level: Optional per-phase risk override. When non-empty,
            ``ExecutionEngine._enforce_veto_before_advance`` uses this
            tier (LOW|MEDIUM|HIGH|CRITICAL) for VETO gating in place of
            the plan-level value. Lets a CRITICAL plan contain a single
            LOW phase that bypasses VETO blocks (bd-5bd9).
    """

    phase_id: int
    name: str
    steps: list[PlanStep] = Field(default_factory=list)
    gate: PlanGate | None = None
    approval_required: bool = False
    approval_description: str = ""
    # FeedbackQuestion is defined later in the file; the dict-typed field
    # is hydrated by the from_dict override below.  This forward reference
    # is unavoidable while FeedbackQuestion comes after the result types
    # in the file order.
    feedback_questions: list[Any] = Field(default_factory=list)
    risk_level: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        data = dict(data)
        gate_data = data.get("gate")
        if gate_data is not None and not isinstance(gate_data, PlanGate):
            data["gate"] = PlanGate.from_dict(gate_data)
        # FeedbackQuestion is still defined later in the file; resolve at
        # call time to avoid a forward-reference dance.
        steps_in = data.pop("steps", [])
        steps_v = [
            (s if isinstance(s, PlanStep) else PlanStep.from_dict(s))
            for s in steps_in
        ]
        fq_in = data.pop("feedback_questions", [])
        fq_v = [
            (q if isinstance(q, FeedbackQuestion) else FeedbackQuestion.from_dict(q))
            for q in fq_in
        ]
        obj = cls(**data)
        obj.steps = steps_v
        obj.feedback_questions = fq_v
        return obj

    def to_dict(self) -> dict:
        d: dict = {
            "phase_id": self.phase_id,
            "name": self.name,
            "steps": [s.to_dict() for s in self.steps],
        }
        if self.gate is not None:
            d["gate"] = self.gate.to_dict()
        if self.approval_required:
            d["approval_required"] = self.approval_required
            d["approval_description"] = self.approval_description
        if self.feedback_questions:
            d["feedback_questions"] = [q.to_dict() for q in self.feedback_questions]
        if self.risk_level:
            d["risk_level"] = self.risk_level
        return d


def _now_iso() -> str:
    """``MachinePlan.created_at`` factory.  Differs from ``_now_iso_seconds``
    in that it preserves microsecond precision (the dataclass version did
    not pass ``timespec="seconds"``)."""
    return datetime.now(timezone.utc).isoformat()


class MachinePlan(PlanModel):
    """Machine-readable execution plan — the contract between planner and executor.

    Created by ``IntelligentPlanner.create_plan()`` and persisted as
    ``plan.json``.  The ``ExecutionEngine`` reads this to drive the
    dispatch loop, and ``to_markdown()`` renders it as ``plan.md`` for
    human review.

    Attributes:
        task_id: Unique execution identifier.
        task_summary: Human-readable description of the task.
        risk_level: Classified risk tier (``"LOW"``, ``"MEDIUM"``,
            ``"HIGH"``, ``"CRITICAL"``).
        budget_tier: Token budget allocation (``"lean"``, ``"standard"``,
            ``"full"``).
        execution_mode: Step ordering strategy (``"phased"``,
            ``"parallel"``, ``"sequential"``).
        git_strategy: Version-control approach for agent commits.
        phases: Ordered list of execution phases.
        shared_context: Pre-built context string injected into all
            agent prompts.
        pattern_source: ``pattern_id`` of the learned pattern that
            influenced this plan, if any.
        created_at: ISO 8601 creation timestamp.
        task_type: Inferred task category (e.g. ``"feature"``,
            ``"bug-fix"``).
        explicit_knowledge_packs: Pack names from ``--knowledge-pack``
            CLI flag.
        explicit_knowledge_docs: Document paths from ``--knowledge``
            CLI flag.
        intervention_level: Human intervention frequency —
            ``"low"``, ``"medium"``, or ``"high"``.
        complexity: Plan complexity tier — ``"light"``, ``"medium"``,
            or ``"heavy"``.
        classification_source: How the classification was determined —
            ``"haiku"`` or ``"keyword-fallback"``.
    """

    task_id: str
    task_summary: str
    risk_level: str = "LOW"
    budget_tier: str = "standard"
    execution_mode: str = "phased"
    git_strategy: str = "commit-per-agent"
    phases: list[PlanPhase] = Field(default_factory=list)
    shared_context: str = ""
    pattern_source: str | None = None
    created_at: str = Field(default_factory=_now_iso)
    task_type: str | None = None
    explicit_knowledge_packs: list[str] = Field(default_factory=list)
    explicit_knowledge_docs: list[str] = Field(default_factory=list)
    intervention_level: str = "low"
    complexity: str = "medium"
    classification_source: str = "keyword-fallback"
    # ResourceLimits and ForesightInsight remain dataclasses (slice 11
    # scope — see migration-review-summary.md §5).  arbitrary_types_allowed
    # on PlanModel lets us hold them; from_dict explicitly re-hydrates
    # from dict shapes.
    resource_limits: Any = None
    detected_stack: str | None = None
    foresight_insights: list[Any] = Field(default_factory=list)
    depends_on_task: str | None = None
    classification_signals: str | None = None
    classification_confidence: float | None = None
    archetype: str = "phased"
    max_retry_phases: int = 0
    compliance_fail_closed: bool | None = None

    @model_validator(mode="after")
    def _validate_plan_graph_integrity(self) -> Self:
        """Hole-6: enforce plan-graph integrity invariants.

        Catches LLM-output-driven plan errors at construction time so
        downstream executor code never sees a structurally invalid plan.
        Raises ``ValueError`` with a descriptive message; Pydantic wraps
        it in ``ValidationError``.

        Invariants:

        1. **Step ID uniqueness across phases.**  Two phases sharing a
           ``step_id`` collide in ``ExecutionState.step_results`` and
           ``state.completed_step_ids`` — the executor would treat the
           second occurrence as already-complete.
        2. **Every step has a non-empty ``agent_name``.**  Empty agents
           cannot be dispatched.
        3. **Phase IDs are unique within a plan.**  Phase advance walks
           by ``current_phase`` index, but lookup-by-id (used by amend
           and resolver) collides on duplicates.
        4. **Step ``depends_on`` references resolve to a step that
           exists earlier in the plan.**  Forward references would deadlock
           the dispatcher.

        See migration-review-summary.md §3 (Hole-6 plan-graph integrity).
        """
        seen_step_ids: dict[str, int] = {}
        seen_phase_ids: set[int] = set()
        for phase in self.phases:
            if phase.phase_id in seen_phase_ids:
                raise ValueError(
                    f"Plan-graph invariant violation: phase_id "
                    f"{phase.phase_id} appears more than once."
                )
            seen_phase_ids.add(phase.phase_id)
            for step in phase.steps:
                if not step.agent_name:
                    raise ValueError(
                        f"Plan-graph invariant violation: step "
                        f"{step.step_id!r} has empty agent_name."
                    )
                prior_phase = seen_step_ids.get(step.step_id)
                if prior_phase is not None:
                    raise ValueError(
                        f"Plan-graph invariant violation: step_id "
                        f"{step.step_id!r} appears in phase {prior_phase} "
                        f"and again in phase {phase.phase_id}."
                    )
                seen_step_ids[step.step_id] = phase.phase_id
                for dep in step.depends_on:
                    if dep not in seen_step_ids:
                        raise ValueError(
                            f"Plan-graph invariant violation: step "
                            f"{step.step_id!r} depends on {dep!r}, which "
                            f"is not declared as an earlier step."
                        )
        return self

    @property
    def all_steps(self) -> list[PlanStep]:
        return [s for p in self.phases for s in p.steps]

    @property
    def all_agents(self) -> list[str]:
        return [s.agent_name for s in self.all_steps]

    @property
    def total_steps(self) -> int:
        return len(self.all_steps)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        # ResourceLimits and ForesightInsight live in other modules and
        # remain dataclasses for now — re-hydrate them explicitly here so
        # the Pydantic constructor receives typed values rather than
        # raw dicts.  PlanPhase is already Pydantic; let its from_dict
        # handle the nested re-hydration of PlanStep/PlanGate/etc.
        from agent_baton.models.parallel import ResourceLimits
        from agent_baton.models.taxonomy import ForesightInsight

        data = dict(data)
        rl_data = data.get("resource_limits")
        if rl_data is not None and not isinstance(rl_data, ResourceLimits):
            data["resource_limits"] = ResourceLimits.from_dict(rl_data)
        phases_in = data.pop("phases", [])
        phases_v = [
            (p if isinstance(p, PlanPhase) else PlanPhase.from_dict(p))
            for p in phases_in
        ]
        fi_in = data.pop("foresight_insights", [])
        fi_v = [
            (i if isinstance(i, ForesightInsight) else ForesightInsight.from_dict(i))
            for i in fi_in
        ]
        obj = cls(**data, phases=phases_v, foresight_insights=fi_v)
        return obj

    def to_dict(self) -> dict:
        d = {
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
            "explicit_knowledge_packs": list(self.explicit_knowledge_packs),
            "explicit_knowledge_docs": list(self.explicit_knowledge_docs),
            "intervention_level": self.intervention_level,
            "complexity": self.complexity,
            "classification_source": self.classification_source,
            "detected_stack": self.detected_stack,
            "foresight_insights": [i.to_dict() for i in self.foresight_insights],
            "depends_on_task": self.depends_on_task,
            "classification_signals": self.classification_signals,
            "classification_confidence": self.classification_confidence,
            "archetype": self.archetype,
            "max_retry_phases": self.max_retry_phases,
            "compliance_fail_closed": self.compliance_fail_closed,
        }
        if self.resource_limits is not None:
            d["resource_limits"] = self.resource_limits.to_dict()
        return d

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
            f"**Archetype**: {self.archetype}",
            f"**Created**: {self.created_at}",
        ]
        if self.pattern_source:
            lines.append(f"**Pattern**: {self.pattern_source}")
        if self.task_type:
            lines.append(f"**Task Type**: {self.task_type}")
        if self.intervention_level != "low":
            lines.append(f"**Intervention Level**: {self.intervention_level}")
        lines.append(f"**Complexity**: {self.complexity}")
        lines.append(f"**Classification Source**: {self.classification_source}")
        if self.depends_on_task:
            lines.append(f"**Depends On Task**: {self.depends_on_task}")
        if self.foresight_insights:
            lines.append(f"**Foresight Insights**: {len(self.foresight_insights)} proactive gap(s) addressed")
        if self.explicit_knowledge_packs:
            lines.append(f"**Explicit Knowledge Packs**: {', '.join(self.explicit_knowledge_packs)}")
        if self.explicit_knowledge_docs:
            lines.append(f"**Explicit Knowledge Docs**: {', '.join(self.explicit_knowledge_docs)}")
        if self.compliance_fail_closed is not None:
            lines.append(f"**Compliance Fail-Closed**: {self.compliance_fail_closed}")
        lines.append("")

        for phase in self.phases:
            feedback_tag = " [FEEDBACK GATE]" if phase.feedback_questions else ""
            approval_tag = " [APPROVAL REQUIRED]" if phase.approval_required else ""
            lines.append(f"## Phase {phase.phase_id}: {phase.name}{approval_tag}{feedback_tag}")
            lines.append("")
            if phase.approval_required and phase.approval_description:
                lines.append(f"> {phase.approval_description}")
                lines.append("")
            for step in phase.steps:
                _parallel_tag = " (parallel)" if step.parallel_safe else ""
                if step.team:
                    lines.append(f"### Step {step.step_id}: Team{_parallel_tag}")
                    lines.append(f"- **Task**: {step.task_description}")
                    lines.append(f"- **Members**:")
                    for member in step.team:
                        lines.append(f"  - {member.member_id}: {member.agent_name} ({member.role})")
                        if member.task_description:
                            lines.append(f"    {member.task_description}")
                else:
                    lines.append(f"### Step {step.step_id}: {step.agent_name}{_parallel_tag}")
                    lines.append(f"- **Model**: {step.model}")
                    lines.append(f"- **Task**: {step.task_description}")
                if step.expected_outcome:
                    lines.append(f"- **Expected outcome**: {step.expected_outcome}")
                if step.depends_on:
                    lines.append(f"- **Depends on**: {', '.join(step.depends_on)}")
                if step.deliverables:
                    lines.append(f"- **Deliverables**: {', '.join(step.deliverables)}")
                if step.allowed_paths:
                    lines.append(f"- **Writes to**: {', '.join(step.allowed_paths)}")
                if step.blocked_paths:
                    lines.append(f"- **Blocked from**: {', '.join(step.blocked_paths)}")
                if step.knowledge:
                    lines.append("- **Knowledge**:")
                    for att in step.knowledge:
                        pack_label = f" ({att.pack_name})" if att.pack_name else ""
                        lines.append(
                            f"  - {att.document_name}{pack_label}"
                            f" — {att.delivery} ({att.source})"
                        )
                lines.append("")

            if phase.gate:
                lines.append(f"### Gate: {phase.gate.gate_type}")
                if phase.gate.command:
                    lines.append(f"- **Command**: `{phase.gate.command}`")
                if phase.gate.description:
                    lines.append(f"- {phase.gate.description}")
                lines.append("")

            if phase.feedback_questions:
                lines.append("### Feedback Gate")
                lines.append("")
                for fq in phase.feedback_questions:
                    lines.append(f"**{fq.question_id}**: {fq.question}")
                    if fq.context:
                        lines.append(f"> {fq.context}")
                    for idx, opt in enumerate(fq.options):
                        agent = fq.option_agents[idx] if idx < len(fq.option_agents) else "?"
                        lines.append(f"  - [{idx}] {opt} → *{agent}*")
                    lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plan amendments (recorded modifications to the plan during execution)
# ---------------------------------------------------------------------------

class PlanAmendment(ExecutionRecord):
    """A recorded modification to the plan during execution.

    Amendments are created by ``baton execute amend`` when the plan
    needs to be adjusted mid-flight — for example, after a gate fails
    and remediation steps are added, or after human approval with
    feedback.

    Attributes:
        amendment_id: Unique identifier for this amendment.
        trigger: What caused the amendment — ``"gate_feedback"``,
            ``"approval_feedback"``, or ``"manual"``.
        trigger_phase_id: Phase that triggered the amendment.
        description: What was changed and why.
        phases_added: Phase IDs of newly inserted phases.
        steps_added: Step IDs of newly inserted steps.
        created_at: ISO 8601 timestamp; auto-stamped via
            ``default_factory`` when not supplied at construction.
        feedback: Reviewer or approver feedback that motivated this
            amendment.
    """

    amendment_id: str
    trigger: str                    # "gate_feedback", "approval_feedback", "manual"
    trigger_phase_id: int
    description: str
    phases_added: list[int] = Field(default_factory=list)   # phase_ids of new phases
    steps_added: list[str] = Field(default_factory=list)    # step_ids of new steps
    created_at: str = Field(default_factory=_now_iso_seconds)
    feedback: str = ""              # reviewer/approver feedback that triggered this
    metadata: dict[str, str] = Field(default_factory=dict)  # arbitrary key/value context


# ---------------------------------------------------------------------------
# Execution State (persisted between CLI calls)
# ---------------------------------------------------------------------------

class TeamStepResult(ExecutionRecord):
    """Result of a single team member's work within a team step.

    Collected individually per member and aggregated into the parent
    ``StepResult.member_results`` list.

    Attributes:
        member_id: Matches ``TeamMember.member_id``.
        agent_name: Agent that executed this member role.
        status: ``"complete"`` or ``"failed"``.
        outcome: Free-text summary of the member's work.
        files_changed: Files the member created or modified.
    """

    member_id: str
    agent_name: str
    status: str = "complete"        # complete, failed
    outcome: str = ""
    files_changed: list[str] = Field(default_factory=list)


class StepResult(ExecutionRecord):
    """Outcome of a single step execution.

    Recorded by ``baton execute record`` after each agent dispatch
    completes or fails.  Stored in ``ExecutionState.step_results``
    and used by the executor to determine the next action.

    Attributes:
        step_id: Matches ``PlanStep.step_id``.
        agent_name: Agent that executed this step.
        status: ``"complete"``, ``"failed"``, or ``"dispatched"``
            (in-progress).
        outcome: Free-text summary of what the agent accomplished.
        files_changed: Filesystem paths created or modified.
        commit_hash: Git commit SHA for this step's work.
        estimated_tokens: Estimated token consumption (real if session
            data available; char/4 heuristic otherwise).
        input_tokens: Sum of input_tokens across all assistant turns in
            the session window for this step.  0 when real data is
            unavailable (legacy or pre-session-scan steps).
        cache_read_tokens: Sum of cache_read_input_tokens for this step.
        cache_creation_tokens: Sum of cache_creation_input_tokens for
            this step.
        output_tokens: Sum of output_tokens for this step.
        model_id: Exact model string (e.g. ``"claude-opus-4-7"``) from
            the most common non-synthetic model observed in the session
            window.  Empty when real data is unavailable.
        session_id: Claude Code session UUID used to source the real
            token counts.  Set at ``mark_dispatched`` time from
            ``$CLAUDE_SESSION_ID``.  Empty when not available.
        step_started_at: ISO 8601 timestamp set when the step is
            dispatched.  Used as the lower bound when scanning the
            session JSONL for this step's token data.
        duration_seconds: Wall-clock execution time.
        retries: Number of retry attempts.
        error: Error message if the step failed.
        completed_at: ISO 8601 completion timestamp.
        member_results: Per-member results for team steps.
        deviations: Plan deviations reported by the agent during
            execution.
    """

    step_id: str
    agent_name: str
    status: str = "complete"        # complete, failed, dispatched
    outcome: str = ""               # free-text summary
    files_changed: list[str] = Field(default_factory=list)
    commit_hash: str = ""
    estimated_tokens: int = 0
    # Real per-step token fields (populated by session JSONL scanner).
    # All default to 0/"" so existing states deserialize without error.
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    model_id: str = ""
    session_id: str = ""
    step_started_at: str = ""
    duration_seconds: float = 0.0
    retries: int = 0
    error: str = ""
    completed_at: str = ""
    member_results: list[TeamStepResult] = Field(default_factory=list)
    deviations: list[str] = Field(default_factory=list)
    interaction_history: list[InteractionTurn] = Field(default_factory=list)
    step_type: str = "developing"   # echoed from PlanStep for analytics/queries
    updated_at: str = ""            # ISO 8601 UTC; set on every status mutation; used for bi-directional split-brain reconciliation
    outcome_spillover_path: str = ""  # relative path under execution dir to FULL outcome when truncated

    def to_dict(self) -> dict:
        # Override required to keep the empty-collection-omission semantics
        # of the dataclass-era to_dict — golden fixtures and call-site
        # snapshots assume member_results / interaction_history are absent
        # when empty.  Deviations is emitted unconditionally even when
        # empty (matches the historical hand-rolled output).
        d = {
            "step_id": self.step_id,
            "agent_name": self.agent_name,
            "status": self.status,
            "outcome": self.outcome,
            "files_changed": list(self.files_changed),
            "commit_hash": self.commit_hash,
            "estimated_tokens": self.estimated_tokens,
            "input_tokens": self.input_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "output_tokens": self.output_tokens,
            "model_id": self.model_id,
            "session_id": self.session_id,
            "step_started_at": self.step_started_at,
            "duration_seconds": self.duration_seconds,
            "retries": self.retries,
            "error": self.error,
            "completed_at": self.completed_at,
            "deviations": list(self.deviations),
            "step_type": self.step_type,
            "updated_at": self.updated_at,
            "outcome_spillover_path": self.outcome_spillover_path,
        }
        if self.member_results:
            d["member_results"] = [m.to_dict() for m in self.member_results]
        if self.interaction_history:
            d["interaction_history"] = [t.to_dict() for t in self.interaction_history]
        return d

    # from_dict inherited from ExecutionRecord — TeamStepResult and
    # InteractionTurn are now Pydantic models, so Pydantic auto-rehydrates
    # member_results and interaction_history from dict payloads via the
    # type annotations above.  The old __dataclass_fields__ filter is
    # replaced by the base class's extra="ignore".


class ApprovalResult(ExecutionRecord):
    """Outcome of a human approval checkpoint.

    Recorded by ``baton execute approve`` when a phase with
    ``approval_required=True`` reaches the approval gate.

    Attributes:
        phase_id: Phase that required approval.
        result: Decision — ``"approve"``, ``"reject"``, or
            ``"approve-with-feedback"`` (which inserts a remediation
            phase via plan amendment).
        feedback: Optional feedback from the reviewer.
        decided_at: ISO 8601 timestamp of the decision; auto-stamped
            via ``default_factory`` when not supplied at construction.
        decision_source: How this approval was decided — ``"human"``,
            ``"daemon_auto"``, ``"api"``, or ``"policy_auto"`` (A2).
        actor: Best-available identity of the approver —
            ``"$USER@$HOSTNAME"`` for CLI, ``"daemon"`` for auto (A2).
        rationale: Optional structured rationale for the decision (A2).
            Complements ``feedback`` with machine-readable context.
    """

    phase_id: int
    result: str                     # "approve", "reject", "approve-with-feedback"
    feedback: str = ""
    decided_at: str = Field(default_factory=_now_iso_seconds)
    decision_source: str = ""       # A2: human | daemon_auto | api | policy_auto
    actor: str = ""                 # A2: $USER@$HOSTNAME or "daemon"
    rationale: str = ""             # A2: structured rationale


class PendingApprovalRequest(ExecutionRecord):
    """Audit row for an approval that is currently pending.

    Stamped on ``ExecutionState.pending_approval_request`` when the engine
    emits an ``APPROVAL`` action, cleared when the approval is recorded.
    Used by the team-mode self-approval guard to compare the actor recording
    the decision against whoever requested it.

    Attributes:
        phase_id: Phase that requested the approval.
        requester: Best-available identity of the requester
            (``"$USER@$HOSTNAME"`` for CLI, ``"daemon"`` for auto).
        requested_at: ISO 8601 timestamp of when the approval was requested.
    """

    phase_id: int
    requester: str = ""
    requested_at: str = Field(default_factory=_now_iso_seconds)


class FeedbackQuestion(PlanModel):
    """A multiple-choice question presented during a feedback gate.

    Feedback gates present 1-4 focused questions to the user after
    initial planning and research phases complete.  Each answer maps
    to an agent and prompt that will be dispatched immediately,
    enabling high-throughput user steering of large changes.

    Attributes:
        question_id: Unique identifier within the feedback set.
        question: The question text shown to the user.
        context: Background explaining why this choice matters.
        options: Available choices (2-6 items).
        option_agents: Parallel list — agent name to dispatch for
            each option.
        option_prompts: Parallel list — delegation prompt template
            for each option.  ``{task}`` is replaced with the plan's
            ``task_summary`` at dispatch time.
    """

    question_id: str
    question: str
    context: str = ""
    options: list[str] = Field(default_factory=list)
    option_agents: list[str] = Field(default_factory=list)
    option_prompts: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return self.model_dump(mode="python")


class FeedbackResult(ExecutionRecord):
    """Outcome of a user's answer to a feedback question.

    When the user selects an option, the engine maps the choice to an
    agent and prompt, then amends the plan with a new dispatch step.

    Attributes:
        phase_id: Phase that presented the feedback gate.
        question_id: Which question was answered.
        chosen_option: The selected option text.
        chosen_index: Zero-based index into the options list.
        dispatched_step_id: Step ID created for the resulting dispatch.
        decided_at: ISO 8601 timestamp of the decision; auto-stamped via
            ``default_factory`` when not supplied at construction.
    """

    phase_id: int
    question_id: str
    chosen_option: str
    chosen_index: int
    dispatched_step_id: str = ""
    decided_at: str = Field(default_factory=_now_iso_seconds)


class GateResult(ExecutionRecord):
    """Outcome of a QA gate check.

    Recorded by ``baton execute gate`` after running the gate command
    and evaluating the result.

    Pre-Phase-1 prototype: this is the first result type promoted onto
    the :class:`ExecutionRecord` Pydantic base.  ``to_dict`` and
    ``from_dict`` are inherited; the historical ``cls.__dataclass_fields__``
    filter is replaced by the base class's ``extra="ignore"`` config.
    The on-disk JSON shape is unchanged — verified by
    ``tests/models/test_execution_roundtrip.py::TestGateResult`` against
    ``tests/models/golden_states/GateResult.json``.

    Attributes:
        phase_id: Phase whose gate was checked.
        gate_type: Gate category (matches ``PlanGate.gate_type``).
        passed: Whether the gate check succeeded.
        output: Command stdout/stderr or reviewer notes.
        checked_at: ISO 8601 timestamp of the check.
        command: The shell command that was executed (A6 traceability).
        exit_code: Subprocess exit code, or ``None`` for manual gates (A6).
        decision_source: How this gate was decided — ``"human"``,
            ``"daemon_auto"``, ``"api"``, or ``"policy_auto"`` (A2).
        actor: Best-available identity of who triggered this gate —
            ``"$USER@$HOSTNAME"`` for CLI, ``"daemon"`` for auto (A2).
    """

    phase_id: int
    gate_type: str
    passed: bool
    output: str = ""                # command output or reviewer notes
    checked_at: str = ""
    command: str = ""               # A6: the command that was run
    exit_code: int | None = None    # A6: subprocess exit code (None = manual)
    decision_source: str = ""       # A2: human | daemon_auto | api | policy_auto
    actor: str = ""                 # A2: $USER@$HOSTNAME or "daemon"

    # to_dict / from_dict inherited from ExecutionRecord — every field is
    # emitted unconditionally, matching the historical hand-rolled to_dict.


# ---------------------------------------------------------------------------
# Consolidation models (cherry-pick rebase of agent commits)
# ---------------------------------------------------------------------------

class FileAttribution(ExecutionRecord):
    """Per-file change attribution to a specific step.

    Populated by ``CommitConsolidator._diff_stats()`` after each
    cherry-pick and stored in ``ConsolidationResult.attributions``.

    Attributes:
        file_path: Repository-relative path of the changed file.
        step_id: Step that produced this change.
        agent_name: Agent that executed the step.
        insertions: Lines added.
        deletions: Lines removed.
    """

    file_path: str
    step_id: str
    agent_name: str
    insertions: int = 0
    deletions: int = 0


class ConsolidationResult(ExecutionRecord):
    """Outcome of rebasing agent commits onto the feature branch.

    Stored on ``ExecutionState.consolidation_result`` after
    ``CommitConsolidator.consolidate()`` completes (or partially
    completes on conflict).

    Attributes:
        status: Overall outcome — ``"success"``, ``"partial"``, or
            ``"conflict"``.
        rebased_commits: Ordered list of dicts with keys ``step_id``,
            ``agent_name``, ``original_hash``, ``new_hash`` — one entry
            per successfully cherry-picked commit.
        final_head: HEAD SHA after consolidation (empty on conflict).
        base_commit: HEAD SHA recorded before the first cherry-pick.
        files_changed: Deduplicated list of all repository-relative paths
            touched across all rebased commits.
        total_insertions: Sum of inserted lines across all rebased commits.
        total_deletions: Sum of deleted lines across all rebased commits.
        attributions: Per-file attribution records linking each change to a
            step and agent.
        conflict_files: Paths that triggered a merge conflict (non-empty
            only when ``status == "conflict"``).
        conflict_step_id: The step whose cherry-pick produced a conflict.
        skipped_steps: Step IDs whose commits had no hash recorded and were
            therefore skipped by the consolidator.
        started_at: ISO 8601 timestamp at consolidation start.
        completed_at: ISO 8601 timestamp at consolidation end.
        error: Exception message if an unexpected error terminated
            consolidation early.
    """

    status: str = "success"          # success | partial | conflict
    rebased_commits: list[dict] = Field(default_factory=list)
    final_head: str = ""
    base_commit: str = ""
    files_changed: list[str] = Field(default_factory=list)
    total_insertions: int = 0
    total_deletions: int = 0
    attributions: list[FileAttribution] = Field(default_factory=list)
    conflict_files: list[str] = Field(default_factory=list)
    conflict_step_id: str = ""
    skipped_steps: list[str] = Field(default_factory=list)
    started_at: str = ""
    completed_at: str = ""
    error: str = ""

    # to_dict / from_dict inherited from ExecutionRecord — every field is
    # emitted unconditionally (matching the legacy hand-rolled to_dict),
    # and FileAttribution rehydration is handled automatically via the
    # nested type annotation.


class ExecutionState(BaseModel):
    """Persistent state of a running execution, saved between CLI calls.

    Serialized as ``execution-state.json`` in the execution directory.
    Each ``baton execute`` subcommand reads, modifies, and writes this
    state back.  The executor uses it to determine the next action via
    ``ExecutionAction``.

    Attributes:
        task_id: Unique execution identifier.
        plan: The ``MachinePlan`` being executed (may be amended).
        current_phase: Index into ``plan.phases``.
        current_step_index: Index into the current phase's steps.
        status: Overall execution state — ``"running"``,
            ``"gate_pending"``, ``"approval_pending"``, ``"complete"``,
            or ``"failed"``.
        step_results: Results recorded so far.
        gate_results: Gate check outcomes.
        approval_results: Human approval outcomes.
        amendments: Modifications applied to the plan during execution.
        started_at: ISO 8601 execution start time.
        completed_at: ISO 8601 completion time, if finished.
        pending_gaps: Unresolved knowledge gap signals.
        resolved_decisions: Resolved gaps injected on re-dispatch.
        consolidation_result: Result of the commit consolidation pass run
            at execution completion.  ``None`` when consolidation has not
            yet been attempted or is not applicable.
    """

    # Pydantic config: extra="ignore" preserves the dataclass-era
    # forward-compat semantics (older state files with extra keys load
    # unchanged).  validate_assignment=False matches dataclass mutation:
    # callers do state.step_results.append(...) etc. and we don't want
    # Pydantic to revalidate the whole model on every assignment.
    # arbitrary_types_allowed=True keeps the still-dataclass nested
    # types (KnowledgeGapSignal, ResolvedDecision) buildable.
    model_config = ConfigDict(
        extra="ignore",
        validate_assignment=False,
        arbitrary_types_allowed=True,
    )

    task_id: str
    plan: MachinePlan
    current_phase: int = 0
    current_step_index: int = 0
    status: str = "running"
    step_results: list[StepResult] = Field(default_factory=list)
    gate_results: list[GateResult] = Field(default_factory=list)
    approval_results: list[ApprovalResult] = Field(default_factory=list)
    feedback_results: list[FeedbackResult] = Field(default_factory=list)
    amendments: list[PlanAmendment] = Field(default_factory=list)
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str = ""
    # KnowledgeGapSignal and ResolvedDecision live in models/knowledge.py
    # and remain dataclasses; arbitrary_types_allowed lets us hold them.
    pending_gaps: list[Any] = Field(default_factory=list)
    resolved_decisions: list[Any] = Field(default_factory=list)
    delivered_knowledge: dict[str, str] = Field(default_factory=dict)
    consolidation_result: ConsolidationResult | None = None
    force_override: bool = False
    override_justification: str = ""
    step_worktrees: dict[str, dict] = Field(default_factory=dict)
    steps_ran_in_place: dict[str, str] = Field(default_factory=dict)
    working_branch: str = ""
    working_branch_head: str = ""
    takeover_records: list[dict] = Field(default_factory=list)
    selfheal_attempts: list[dict] = Field(default_factory=list)
    speculations: dict[str, dict] = Field(default_factory=dict)
    run_cumulative_spend_usd: float = 0.0
    pending_scope_expansions: list[dict] = Field(default_factory=list)
    scope_expansions_applied: int = 0
    pending_approval_request: PendingApprovalRequest | None = None
    phase_retries: dict[str, int] = Field(default_factory=dict)

    # SQLite Phase C (slice 14): transient OCC version observed at load
    # time.  PrivateAttr so it does NOT appear in model_dump / to_dict —
    # the storage layer reads it directly via the underscore-prefixed
    # attribute when issuing the CAS UPDATE.  Default 0 matches "not
    # loaded from a row" — fresh ExecutionState constructions skip OCC.
    _loaded_version: int = PrivateAttr(default=0)

    @model_validator(mode="after")
    def _validate_invariants(self) -> Self:
        """I1/I2/I9 belt-and-suspenders for state loaded from disk.

        These mirror the transition methods' preconditions so any
        ExecutionState constructed from a legacy state file or a
        Pydantic ``model_validate`` call surfaces invariant violations
        immediately rather than at the next save.

        Per cross-proposal §2.3 (state loaded from disk with violating
        shape should raise loud).

        Invariants:

        * **I1**: ``status == "approval_pending"`` ⇔
          ``pending_approval_request is not None``.
        * **I2**: terminal ``status in {"complete","failed","cancelled"}``
          ⇒ ``completed_at != ""``.  Slice 12 closed every Python-side
          path; this validator catches state files that pre-date the
          fix being persisted with empty ``completed_at``.
        * **I9**: ``status == "paused-takeover"`` ⇒ at least one
          ``takeover_records`` entry has empty ``resumed_at``.
        """
        # I1
        is_approval_pending = self.status == "approval_pending"
        has_request = self.pending_approval_request is not None
        if is_approval_pending != has_request:
            raise ValueError(
                f"I1 invariant violation on task {self.task_id!r}: "
                f"status={self.status!r} but "
                f"pending_approval_request="
                f"{'set' if has_request else 'None'}.  Expected "
                f"approval_pending ⇔ pending_approval_request != None."
            )
        # I2 — only enforce on load (state files with empty completed_at
        # in terminal states pre-date the slice 12 funnel).  Auto-fill
        # rather than raise so existing state files stay loadable.
        if self.status in {"complete", "failed", "cancelled"} and not self.completed_at:
            self.completed_at = datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            )
        # I9
        if self.status == "paused-takeover":
            active = any(
                isinstance(r, dict) and not r.get("resumed_at")
                for r in self.takeover_records
            )
            if not active:
                raise ValueError(
                    f"I9 invariant violation on task {self.task_id!r}: "
                    f"status='paused-takeover' but no takeover_records "
                    f"entry has empty resumed_at."
                )
        return self

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

    @property
    def interrupted_step_ids(self) -> set[str]:
        return {r.step_id for r in self.step_results if r.status == "interrupted"}

    def get_step_result(self, step_id: str) -> StepResult | None:
        """Look up the result for a specific step.

        Args:
            step_id: The step ID to search for.

        Returns:
            The matching ``StepResult``, or ``None`` if the step has
            not been recorded yet.
        """
        for r in self.step_results:
            if r.step_id == step_id:
                return r
        return None

    # ── Hole-1-class transition methods ──────────────────────────────────────
    # Coupled-field mutations funnelled through methods so that the I1
    # invariant — ``status == "approval_pending"`` ⇔
    # ``pending_approval_request is not None`` — can't drift through an
    # early ``return`` between the status flip and the audit-row write.
    # See docs/internal/state-mutation-proposal.md §4.

    def transition_to_approval_pending(
        self,
        *,
        phase_id: int,
        requester: str,
        requested_at: str = "",
    ) -> None:
        """Atomically flip into the approval-pending blocked state.

        Sets ``status = "approval_pending"`` and stamps
        ``pending_approval_request`` so the I1 invariant cannot be observed
        torn by a concurrent save.

        Args:
            phase_id: Phase whose approval is being requested.
            requester: Identity (CLI actor / agent ID) that asked.
            requested_at: ISO 8601 timestamp; auto-stamped if empty.

        Raises:
            IllegalStateTransition: If current status is not one of
                ``running``, ``gate_pending``, ``approval_pending``
                (idempotent re-emit on resume).
        """
        from agent_baton.core.engine.errors import IllegalStateTransition

        allowed = {"running", "gate_pending", "approval_pending"}
        if self.status not in allowed:
            raise IllegalStateTransition(
                from_status=self.status,
                to_status="approval_pending",
                task_id=self.task_id,
                context="transition_to_approval_pending",
            )
        stamp = requested_at or datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        self.pending_approval_request = PendingApprovalRequest(
            phase_id=phase_id,
            requester=requester,
            requested_at=stamp,
        )
        self.status = "approval_pending"

    def clear_approval_pending(self) -> None:
        """Drop the pending-approval audit row.

        Used by ``record_approval_result`` after the approval has been
        recorded.  Does NOT flip status — the caller follows up with one
        of ``transition_to_running`` / ``transition_to_failed`` to move
        out of the blocked state.  This split exists because the
        approve-with-feedback path must save+reload between clearing the
        row and the final status flip.
        """
        self.pending_approval_request = None

    def transition_to_failed(
        self, *, reason: str = "", completed_at: str = "",
    ) -> None:
        """Flip status to ``"failed"`` and stamp ``completed_at`` atomically.

        Closes I2 in the failed direction.  Six of the fourteen historical
        ``state.status = "failed"`` sites did NOT set ``completed_at`` —
        retrospectives compute duration as ``completed_at - started_at``
        and skipped or showed ``Inf`` for those rows.  Funnelling through
        this method makes that impossible.

        Args:
            reason: Optional human-readable failure reason.  Currently
                stored only as a transient field for debugging; use the
                bead store for persistent failure narratives.
            completed_at: ISO 8601 timestamp; auto-stamped when blank.
        """
        stamp = completed_at or datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        # Allowed from any non-terminal status.  Terminal-to-terminal
        # is treated as a no-op so retried failure handlers don't double-
        # bump completed_at.
        if self.status in {"failed", "complete", "cancelled"}:
            return
        if self.status == "approval_pending":
            self.pending_approval_request = None
        self.completed_at = stamp
        self.status = "failed"
        # ``reason`` is intentionally not persisted — production callers
        # write the reason to a bead via BeadStore.create_bead.  Kept on
        # the signature so call sites can document it close to the call.
        _ = reason

    def transition_to_complete(self, *, completed_at: str = "") -> None:
        """Flip status to ``"complete"`` and stamp ``completed_at`` atomically.

        I2: terminal-with-timestamp.  No-op when already terminal so a
        retried completion path doesn't shift the recorded timestamp.
        """
        if self.status in {"complete", "failed", "cancelled"}:
            return
        stamp = completed_at or datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        self.completed_at = stamp
        self.status = "complete"

    def transition_to_cancelled(self, *, completed_at: str = "") -> None:
        """Flip status to ``"cancelled"`` and stamp ``completed_at`` atomically.

        Used by the CLI ``cancel`` verb and the REST stop-execution
        endpoint.  Same I2 contract as ``transition_to_complete``.
        """
        if self.status in {"complete", "failed", "cancelled"}:
            return
        stamp = completed_at or datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )
        self.completed_at = stamp
        self.status = "cancelled"

    def transition_to_paused_takeover(
        self, *, takeover_record: dict | None = None,
    ) -> None:
        """Flip status to ``"paused-takeover"`` with an active takeover row.

        I9: ``status == "paused-takeover"`` ⇒ at least one
        ``takeover_records`` entry has empty ``resumed_at``.  Funnelling
        the flip + record append through one method makes the invariant
        structurally impossible to break.

        Args:
            takeover_record: The dict to append to ``takeover_records``.
                When ``None``, an active row from existing records is
                expected; the method then merely flips status.  Resolver
                code at ``resolver.py:236-240`` walks the records list
                looking for an ``resumed_at == ""`` entry.
        """
        if takeover_record is not None:
            self.takeover_records.append(takeover_record)
        # I9 belt-and-suspenders: refuse to flip if no active record
        # exists, so the invariant cannot be observed broken.  The
        # active-row predicate matches resolver.py.
        active = any(
            isinstance(r, dict) and not r.get("resumed_at")
            for r in self.takeover_records
        )
        if not active:
            from agent_baton.core.engine.errors import IllegalStateTransition

            raise IllegalStateTransition(
                from_status=self.status,
                to_status="paused-takeover",
                task_id=self.task_id,
                context=(
                    "transition_to_paused_takeover requires at least one "
                    "active takeover record (resumed_at == '')"
                ),
            )
        self.status = "paused-takeover"

    def transition_to_running(
        self,
        *,
        from_status: Literal[
            "approval_pending",
            "feedback_pending",
            "gate_pending",
            "budget_exceeded",
            "paused-takeover",
            "pending",
            "running",
        ],
    ) -> None:
        """Flip status to ``"running"``, asserting the prior status.

        Also clears ``pending_approval_request`` if it is still set —
        leaving an audit row behind after exiting approval_pending would
        violate I1 in the reverse direction.

        Args:
            from_status: The status the caller observed before this
                transition.  The method asserts the actual current status
                matches.

        Raises:
            IllegalStateTransition: If ``self.status != from_status``.
        """
        from agent_baton.core.engine.errors import IllegalStateTransition

        _allowed_sources = {
            "approval_pending",
            "feedback_pending",
            "gate_pending",
            "budget_exceeded",
            "paused-takeover",
            "pending",
            "running",
        }
        if from_status not in _allowed_sources or self.status != from_status:
            raise IllegalStateTransition(
                from_status=self.status,
                to_status="running",
                task_id=self.task_id,
                context=f"transition_to_running(from_status={from_status!r})",
            )
        # I1 belt-and-suspenders: an inherited pending_approval_request
        # while running is the exact failure mode the funnel exists to
        # prevent.  Clear it on any exit out of approval_pending.
        if from_status == "approval_pending":
            self.pending_approval_request = None
        self.status = "running"

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
            "feedback_results": [f.to_dict() for f in self.feedback_results],
            "amendments": [a.to_dict() for a in self.amendments],
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "pending_gaps": [g.to_dict() for g in self.pending_gaps],
            "resolved_decisions": [d.to_dict() for d in self.resolved_decisions],
            "delivered_knowledge": dict(self.delivered_knowledge),
            "consolidation_result": (
                self.consolidation_result.to_dict()
                if self.consolidation_result is not None
                else None
            ),
            "force_override": self.force_override,
            "override_justification": self.override_justification,
            # Wave 1.3 (bd-86bf): worktree isolation state
            "step_worktrees": dict(getattr(self, "step_worktrees", {})),
            # steps that degraded to in-place execution after worktree failure
            "steps_ran_in_place": dict(getattr(self, "steps_ran_in_place", {})),
            "working_branch": getattr(self, "working_branch", ""),
            # Wave 5 (bd-e208, bd-1483, bd-9839): Human-Agent Loop state
            "takeover_records": list(getattr(self, "takeover_records", [])),
            "selfheal_attempts": list(getattr(self, "selfheal_attempts", [])),
            "speculations": dict(getattr(self, "speculations", {})),
            # SQLite Phase B: investigative-archetype phase retry counts.
            # Pulled out of the speculations bimodal scratchpad.
            "phase_retries": dict(getattr(self, "phase_retries", {})),
            # bd-def9: rebased tip SHA after most-recent fold_back()
            "working_branch_head": getattr(self, "working_branch_head", ""),
            # end-user readiness #7: run-level cumulative spend for ceiling tracking
            "run_cumulative_spend_usd": float(getattr(self, "run_cumulative_spend_usd", 0.0)),
            "pending_scope_expansions": list(getattr(self, "pending_scope_expansions", [])),
            "scope_expansions_applied": int(getattr(self, "scope_expansions_applied", 0)),
            # Hole 1 fix: approval-request audit row (None when no approval pending).
            "pending_approval_request": (
                self.pending_approval_request.to_dict()
                if getattr(self, "pending_approval_request", None) is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExecutionState:
        cr_data = data.get("consolidation_result")

        # SQLite Phase B: lift _phase_retries out of speculations.  Older
        # state files keyed phase retries inside the speculations dict
        # under the magic ``_phase_retries`` slot — the dict was bimodal.
        # Slice 6 separates the two; this shim makes legacy files load.
        speculations_in = dict(data.get("speculations", {}))
        legacy_retries = speculations_in.pop("_phase_retries", None)
        explicit_retries = data.get("phase_retries")
        if explicit_retries is not None:
            phase_retries = {str(k): int(v) for k, v in explicit_retries.items()}
        elif isinstance(legacy_retries, dict):
            phase_retries = {str(k): int(v) for k, v in legacy_retries.items()}
        else:
            phase_retries = {}

        return cls(
            task_id=data["task_id"],
            plan=MachinePlan.from_dict(data["plan"]),
            current_phase=data.get("current_phase", 0),
            current_step_index=data.get("current_step_index", 0),
            status=data.get("status", "running"),
            step_results=[StepResult.from_dict(r) for r in data.get("step_results", [])],
            gate_results=[GateResult.from_dict(g) for g in data.get("gate_results", [])],
            approval_results=[ApprovalResult.from_dict(a) for a in data.get("approval_results", [])],
            feedback_results=[FeedbackResult.from_dict(f) for f in data.get("feedback_results", [])],
            amendments=[PlanAmendment.from_dict(a) for a in data.get("amendments", [])],
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at", ""),
            pending_gaps=[KnowledgeGapSignal.from_dict(g) for g in data.get("pending_gaps", [])],
            resolved_decisions=[ResolvedDecision.from_dict(d) for d in data.get("resolved_decisions", [])],
            delivered_knowledge=dict(data.get("delivered_knowledge", {})),
            consolidation_result=ConsolidationResult.from_dict(cr_data) if cr_data is not None else None,
            force_override=bool(data.get("force_override", False)),
            override_justification=data.get("override_justification", ""),
            # Wave 1.3 (bd-86bf): worktree isolation — default to empty for legacy files
            step_worktrees=dict(data.get("step_worktrees", {})),
            steps_ran_in_place=dict(data.get("steps_ran_in_place", {})),
            working_branch=data.get("working_branch", ""),
            # Wave 5 (bd-e208, bd-1483, bd-9839): default to empty for legacy files
            takeover_records=list(data.get("takeover_records", [])),
            selfheal_attempts=list(data.get("selfheal_attempts", [])),
            # speculations no longer carries _phase_retries (split above);
            # use the post-shim copy that has it removed.
            speculations=speculations_in,
            phase_retries=phase_retries,
            # bd-def9: getattr guard for legacy state files that predate this field
            working_branch_head=data.get("working_branch_head", ""),
            # end-user readiness #7: default 0.0 for legacy state files
            run_cumulative_spend_usd=float(data.get("run_cumulative_spend_usd", 0.0)),
            pending_scope_expansions=list(data.get("pending_scope_expansions", [])),
            scope_expansions_applied=int(data.get("scope_expansions_applied", 0)),
            # Hole 1 fix: legacy state files have no approval-request audit row.
            # Accept both the new dataclass-shaped dict and the legacy raw dict
            # — same on-disk shape, but materialised as the typed model now.
            pending_approval_request=(
                PendingApprovalRequest.from_dict(data["pending_approval_request"])
                if data.get("pending_approval_request") is not None
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Execution Actions (returned by the engine to tell the caller what to do)
# ---------------------------------------------------------------------------

@dataclass
class ExecutionAction:
    """Instruction from the execution engine to the driving session.

    Returned by ``baton execute next`` to tell the orchestrator what
    to do.  The ``action_type`` determines which fields are populated.
    The CLI's ``_print_action()`` renders this as structured output
    that Claude reads to drive the orchestration loop.

    Attributes:
        action_type: What kind of action is required.
        message: Human-readable description of the action.
        agent_name: Agent to dispatch (``DISPATCH`` only).
        agent_model: Model for the dispatch (``DISPATCH`` only).
        delegation_prompt: Full prompt to send to the agent
            (``DISPATCH`` only).
        step_id: Plan step being dispatched (``DISPATCH`` only).
        path_enforcement: PreToolUse hook command for path sandboxing
            (``DISPATCH`` only).
        gate_type: Gate category (``GATE`` only).
        gate_command: Bash command to run (``GATE`` only).
        phase_id: Phase being gated or approved (``GATE`` / ``APPROVAL``).
        approval_context: Summary for the reviewer (``APPROVAL`` only).
        approval_options: Available approval choices (``APPROVAL`` only).
        summary: Final execution summary (``COMPLETE`` / ``FAILED``).
        parallel_actions: Batch of actions for parallel dispatch.
    """

    action_type: ActionType             # strongly-typed; serialises to str via to_dict()
    message: str = ""                   # human-readable description

    # For DISPATCH actions:
    agent_name: str = ""
    agent_model: str = ""
    delegation_prompt: str = ""
    step_id: str = ""
    step_type: str = ""             # echoed from PlanStep for caller routing
    command: str = ""               # for automation steps
    # Path enforcement hook command (for PreToolUse):
    path_enforcement: str = ""
    # MCP server names to pass through to this dispatch:
    mcp_servers: list[str] = field(default_factory=list)
    # Subagent isolation contract — set to "worktree" when the engine
    # emits this action as part of a parallel DISPATCH wave (>=2 actions).
    # The orchestrator MUST forward this onto the Agent invocation as
    # ``isolation="worktree"`` so concurrent agents land in separate git
    # worktrees and cannot contaminate the parent branch.  Empty string
    # means no isolation contract (singleton or sequential dispatch).
    isolation: str = ""

    # For GATE actions:
    gate_type: str = ""
    gate_command: str = ""
    phase_id: int = 0

    # For APPROVAL actions:
    approval_context: str = ""          # summary of phase output for reviewer
    approval_options: list[str] = field(default_factory=list)

    # For FEEDBACK actions:
    feedback_questions: list[FeedbackQuestion] = field(default_factory=list)
    feedback_context: str = ""          # summary of prior work for context

    # For COMPLETE/FAILED actions:
    summary: str = ""

    # For batch dispatch (parallel steps / team members):
    parallel_actions: list[ExecutionAction] = field(default_factory=list)

    # For INTERACT actions (multi-turn step conversation):
    interact_prompt: str = ""          # current agent output awaiting human response
    interact_step_id: str = ""         # step that is in interacting status
    interact_agent_name: str = ""      # agent involved in the interaction
    interact_turn: int = 0             # current turn number (1-based)
    interact_max_turns: int = 10       # maximum allowed turns for this step
    interactive: bool = False          # True on DISPATCH when the step is interactive

    # Wave 3.1 — behavioral demo statement echoed from PlanStep.expected_outcome
    # so the CLI and the orchestrator can surface it without re-reading plan.json.
    expected_outcome: str = ""         # DISPATCH only; empty when not derived
    # Wave 1.3 (bd-86bf): worktree isolation fields — empty string when no worktree.
    # Populated at mark_dispatched() time, not at next_actions() time.
    worktree_path: str = ""            # absolute path to isolated worktree; "" = no worktree
    worktree_branch: str = ""          # git branch inside the worktree

    def to_dict(self) -> dict[str, Any]:
        # action_type is serialised as a plain string so CLI / Claude output
        # is unaffected by the internal enum representation.
        d: dict[str, Any] = {"action_type": self.action_type.value, "message": self.message}
        if self.action_type == ActionType.DISPATCH:
            is_team_member = bool(_TEAM_MEMBER_ID_RE.match(self.step_id))
            d.update({
                "agent_name": self.agent_name,
                "agent_model": self.agent_model,
                "delegation_prompt": self.delegation_prompt,
                "step_id": self.step_id,
                "step_type": self.step_type,
                "path_enforcement": self.path_enforcement,
                "is_team_member": is_team_member,
                "interactive": self.interactive,
            })
            if is_team_member:
                # Provide the parent step ID so consumers can call team-record
                # without parsing the member ID themselves.
                d["parent_step_id"] = ".".join(self.step_id.split(".")[:2])
            if self.mcp_servers:
                d["mcp_servers"] = self.mcp_servers
            if self.interactive:
                d["interact_max_turns"] = self.interact_max_turns
            if self.command:
                d["command"] = self.command
            if self.isolation:
                d["isolation"] = self.isolation
            if self.expected_outcome:
                d["expected_outcome"] = self.expected_outcome
            # Wave 1.3 (bd-86bf): include worktree fields when populated
            if self.worktree_path:
                d["worktree_path"] = self.worktree_path
            if self.worktree_branch:
                d["worktree_branch"] = self.worktree_branch
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
        elif self.action_type == ActionType.FEEDBACK:
            d.update({
                "phase_id": self.phase_id,
                "feedback_questions": [q.to_dict() for q in self.feedback_questions],
                "feedback_context": self.feedback_context,
            })
        elif self.action_type == ActionType.INTERACT:
            d.update({
                "interact_prompt": self.interact_prompt,
                "interact_step_id": self.interact_step_id,
                "interact_agent_name": self.interact_agent_name,
                "interact_turn": self.interact_turn,
                "interact_max_turns": self.interact_max_turns,
            })
        elif self.action_type in (ActionType.COMPLETE, ActionType.FAILED):
            d["summary"] = self.summary
        if self.parallel_actions:
            d["parallel_actions"] = [a.to_dict() for a in self.parallel_actions]
        return d
