"""Render-ready data adapter for plan visualization.

``PlanSnapshot`` normalizes both ``MachinePlan`` (plan-only) and
``ExecutionState`` (plan + live results) into a single flat dataclass
that renderers can consume without touching engine internals.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Leaf snapshots
# ---------------------------------------------------------------------------

@dataclass
class MemberSnapshot:
    """Snapshot of a single team member within a team step."""

    member_id: str
    agent_name: str
    role: str           # "lead", "implementer", "reviewer"
    task_description: str
    status: str         # "pending", "complete", "failed", "dispatched"


@dataclass
class StepSnapshot:
    """Snapshot of a single plan step with optional execution results."""

    step_id: str
    agent_name: str
    task_description: str
    model: str
    step_type: str
    status: str         # "pending", "running", "complete", "failed", etc.
    depends_on: list[str]
    deliverables: list[str]
    parallel_safe: bool
    expected_outcome: str
    team: list[MemberSnapshot]
    # Execution results (zero/empty when not executed)
    outcome: str = ""
    error: str = ""
    tokens_used: int = 0        # input + output tokens
    duration_seconds: float = 0.0
    files_changed: list[str] = field(default_factory=list)


@dataclass
class GateSnapshot:
    """Snapshot of a phase gate."""

    gate_type: str
    command: str
    description: str
    status: str         # "pending", "passed", "failed"
    output: str = ""


@dataclass
class PhaseSnapshot:
    """Snapshot of a single execution phase."""

    phase_id: int
    name: str
    status: str         # "pending", "running", "gate_pending", "complete", "failed"
    risk_level: str
    approval_required: bool
    steps: list[StepSnapshot]
    gate: GateSnapshot | None


# ---------------------------------------------------------------------------
# Top-level snapshot
# ---------------------------------------------------------------------------

@dataclass
class PlanSnapshot:
    """Complete render-ready snapshot of a plan and its execution state."""

    # Identity
    task_id: str
    task_summary: str
    created_at: str
    # Classification
    risk_level: str
    budget_tier: str
    complexity: str
    task_type: str
    detected_stack: str       # joined with "/"
    execution_mode: str
    classification_source: str
    # Structure
    phases: list[PhaseSnapshot]
    total_steps: int
    total_agents: list[str]   # unique, ordered by first appearance
    # Execution progress
    execution_status: str     # "not_started", "running", "complete", "failed", etc.
    current_phase_index: int  # 0-based; -1 when not started
    steps_complete: int
    steps_failed: int
    steps_running: int
    started_at: str
    completed_at: str
    elapsed_seconds: float
    total_tokens: int
    total_cost_usd: float
    progress_pct: float       # 0.0 - 100.0
    amendment_count: int

    # ------------------------------------------------------------------
    # Factory: plan-only (no execution state)
    # ------------------------------------------------------------------

    @classmethod
    def from_plan(cls, plan: object) -> PlanSnapshot:
        """Build snapshot from a ``MachinePlan`` with no execution state."""
        from agent_baton.models.execution import MachinePlan

        assert isinstance(plan, MachinePlan)

        phases: list[PhaseSnapshot] = []
        agent_order: list[str] = []
        total_steps = 0

        for phase in plan.phases:
            step_snaps: list[StepSnapshot] = []
            for step in phase.steps:
                total_steps += 1
                team_snaps = [
                    MemberSnapshot(
                        member_id=m.member_id,
                        agent_name=m.agent_name,
                        role=m.role,
                        task_description=m.task_description,
                        status="pending",
                    )
                    for m in step.team
                ]
                step_snaps.append(
                    StepSnapshot(
                        step_id=step.step_id,
                        agent_name=step.agent_name,
                        task_description=step.task_description,
                        model=step.model,
                        step_type=step.step_type,
                        status="pending",
                        depends_on=list(step.depends_on),
                        deliverables=list(step.deliverables),
                        parallel_safe=step.parallel_safe,
                        expected_outcome=step.expected_outcome,
                        team=team_snaps,
                    )
                )
                # Collect unique agent names in first-appearance order
                if step.agent_name not in agent_order:
                    agent_order.append(step.agent_name)
                for m in step.team:
                    if m.agent_name not in agent_order:
                        agent_order.append(m.agent_name)

            gate_snap: GateSnapshot | None = None
            if phase.gate:
                gate_snap = GateSnapshot(
                    gate_type=phase.gate.gate_type,
                    command=phase.gate.command,
                    description=phase.gate.description,
                    status="pending",
                )

            phases.append(
                PhaseSnapshot(
                    phase_id=phase.phase_id,
                    name=phase.name,
                    status="pending",
                    risk_level=phase.risk_level or plan.risk_level,
                    approval_required=phase.approval_required,
                    steps=step_snaps,
                    gate=gate_snap,
                )
            )

        # detected_stack handling: field is str | None on MachinePlan
        raw_stack = plan.detected_stack
        if isinstance(raw_stack, list):
            detected_stack = "/".join(raw_stack) if raw_stack else "unknown"
        elif isinstance(raw_stack, str) and raw_stack:
            detected_stack = raw_stack
        else:
            detected_stack = "unknown"

        return cls(
            task_id=plan.task_id,
            task_summary=plan.task_summary,
            created_at=plan.created_at,
            risk_level=plan.risk_level,
            budget_tier=plan.budget_tier,
            complexity=getattr(plan, "complexity", ""),
            task_type=plan.task_type or "",
            detected_stack=detected_stack,
            execution_mode=plan.execution_mode,
            classification_source=getattr(plan, "classification_source", ""),
            phases=phases,
            total_steps=total_steps,
            total_agents=agent_order,
            execution_status="not_started",
            current_phase_index=-1,
            steps_complete=0,
            steps_failed=0,
            steps_running=0,
            started_at="",
            completed_at="",
            elapsed_seconds=0.0,
            total_tokens=0,
            total_cost_usd=0.0,
            progress_pct=0.0,
            amendment_count=0,
        )

    # ------------------------------------------------------------------
    # Factory: from execution state (plan + live results)
    # ------------------------------------------------------------------

    @classmethod
    def from_state(cls, state: object) -> PlanSnapshot:
        """Build snapshot from an ``ExecutionState`` (plan + live results)."""
        from agent_baton.models.execution import ExecutionState

        assert isinstance(state, ExecutionState)

        plan = state.plan

        # Build lookup dicts for fast access
        step_result_map: dict[str, object] = {}
        for r in state.step_results:
            step_result_map[r.step_id] = r

        gate_result_map: dict[int, object] = {}
        for g in state.gate_results:
            gate_result_map[g.phase_id] = g

        dispatched_ids = state.dispatched_step_ids
        completed_ids = state.completed_step_ids
        failed_ids = state.failed_step_ids

        phases: list[PhaseSnapshot] = []
        agent_order: list[str] = []
        total_steps = 0
        steps_complete = 0
        steps_failed = 0
        steps_running = 0
        total_tokens = 0

        for phase_idx, phase in enumerate(plan.phases):
            step_snaps: list[StepSnapshot] = []
            for step in phase.steps:
                total_steps += 1

                # Derive step status from execution state
                result = step_result_map.get(step.step_id)
                if result is not None:
                    step_status = result.status
                elif step.step_id in dispatched_ids:
                    step_status = "running"
                else:
                    step_status = "pending"

                # Count steps by status
                if step_status == "complete":
                    steps_complete += 1
                elif step_status == "failed":
                    steps_failed += 1
                elif step_status in ("running", "dispatched"):
                    steps_running += 1

                # Extract execution data from result
                outcome = ""
                error = ""
                tokens_used = 0
                duration_seconds = 0.0
                files_changed: list[str] = []

                if result is not None:
                    outcome = result.outcome
                    error = result.error
                    duration_seconds = result.duration_seconds
                    files_changed = list(result.files_changed)
                    # Token calculation: use real tokens when available,
                    # fall back to estimated_tokens
                    real_tokens = result.input_tokens + result.output_tokens
                    if real_tokens > 0:
                        tokens_used = real_tokens
                    else:
                        tokens_used = result.estimated_tokens
                    total_tokens += tokens_used

                # Team member snapshots
                team_snaps: list[MemberSnapshot] = []
                for m in step.team:
                    # Check if this member has a result
                    member_status = "pending"
                    if result is not None:
                        for mr in result.member_results:
                            if mr.member_id == m.member_id:
                                member_status = mr.status
                                break
                        else:
                            # If step has a result but no member result,
                            # inherit step status for non-pending
                            if step_status in ("complete", "failed"):
                                member_status = step_status
                    team_snaps.append(
                        MemberSnapshot(
                            member_id=m.member_id,
                            agent_name=m.agent_name,
                            role=m.role,
                            task_description=m.task_description,
                            status=member_status,
                        )
                    )

                step_snaps.append(
                    StepSnapshot(
                        step_id=step.step_id,
                        agent_name=step.agent_name,
                        task_description=step.task_description,
                        model=step.model,
                        step_type=step.step_type,
                        status=step_status,
                        depends_on=list(step.depends_on),
                        deliverables=list(step.deliverables),
                        parallel_safe=step.parallel_safe,
                        expected_outcome=step.expected_outcome,
                        team=team_snaps,
                        outcome=outcome,
                        error=error,
                        tokens_used=tokens_used,
                        duration_seconds=duration_seconds,
                        files_changed=files_changed,
                    )
                )
                # Collect unique agent names in first-appearance order
                if step.agent_name not in agent_order:
                    agent_order.append(step.agent_name)
                for m in step.team:
                    if m.agent_name not in agent_order:
                        agent_order.append(m.agent_name)

            # Derive phase status.
            # state.current_phase is a 0-based index into plan.phases,
            # so compare against phase_idx (not phase.phase_id which is 1-based).
            gate_result = gate_result_map.get(phase.phase_id)

            if phase_idx < state.current_phase:
                # Past phase -- check gate for failure
                if gate_result is not None and not gate_result.passed:
                    phase_status = "failed"
                else:
                    phase_status = "complete"
            elif phase_idx == state.current_phase:
                if state.status == "gate_pending":
                    phase_status = "gate_pending"
                else:
                    phase_status = "running"
            else:
                phase_status = "pending"

            # Gate snapshot
            gate_snap: GateSnapshot | None = None
            if phase.gate:
                if gate_result is not None:
                    gate_status = "passed" if gate_result.passed else "failed"
                    gate_output = gate_result.output
                else:
                    gate_status = "pending"
                    gate_output = ""
                gate_snap = GateSnapshot(
                    gate_type=phase.gate.gate_type,
                    command=phase.gate.command,
                    description=phase.gate.description,
                    status=gate_status,
                    output=gate_output,
                )

            phases.append(
                PhaseSnapshot(
                    phase_id=phase.phase_id,
                    name=phase.name,
                    status=phase_status,
                    risk_level=phase.risk_level or plan.risk_level,
                    approval_required=phase.approval_required,
                    steps=step_snaps,
                    gate=gate_snap,
                )
            )

        # detected_stack handling
        raw_stack = plan.detected_stack
        if isinstance(raw_stack, list):
            detected_stack = "/".join(raw_stack) if raw_stack else "unknown"
        elif isinstance(raw_stack, str) and raw_stack:
            detected_stack = raw_stack
        else:
            detected_stack = "unknown"

        # Elapsed seconds calculation
        elapsed_seconds = 0.0
        started_at = state.started_at
        completed_at = state.completed_at
        if started_at:
            try:
                start_dt = datetime.fromisoformat(started_at)
                if completed_at:
                    end_dt = datetime.fromisoformat(completed_at)
                else:
                    end_dt = datetime.now(timezone.utc)
                elapsed_seconds = max(0.0, (end_dt - start_dt).total_seconds())
            except (ValueError, TypeError):
                elapsed_seconds = 0.0

        # Progress percentage
        progress_pct = 0.0
        if total_steps > 0:
            progress_pct = (steps_complete / total_steps) * 100.0

        # Cost from state
        total_cost_usd = getattr(state, "run_cumulative_spend_usd", 0.0) or 0.0

        # Amendment count
        amendment_count = len(getattr(state, "amendments", []))

        return cls(
            task_id=plan.task_id,
            task_summary=plan.task_summary,
            created_at=plan.created_at,
            risk_level=plan.risk_level,
            budget_tier=plan.budget_tier,
            complexity=getattr(plan, "complexity", ""),
            task_type=plan.task_type or "",
            detected_stack=detected_stack,
            execution_mode=plan.execution_mode,
            classification_source=getattr(plan, "classification_source", ""),
            phases=phases,
            total_steps=total_steps,
            total_agents=agent_order,
            execution_status=state.status,
            current_phase_index=state.current_phase,
            steps_complete=steps_complete,
            steps_failed=steps_failed,
            steps_running=steps_running,
            started_at=started_at,
            completed_at=completed_at,
            elapsed_seconds=elapsed_seconds,
            total_tokens=total_tokens,
            total_cost_usd=total_cost_usd,
            progress_pct=progress_pct,
            amendment_count=amendment_count,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict for web renderer."""
        return asdict(self)
