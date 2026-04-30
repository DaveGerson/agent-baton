"""Baton execution test harness.

Programmatically drives a synthetic plan through the execution engine,
validating every state transition, action type, and result.  Usable as
a CLI command (``baton test-plan``) or a pytest fixture.
"""
from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.models.execution import (
    ActionType,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    TeamMember,
)


@dataclass
class HarnessResult:
    """Result of a test harness run."""

    passed: bool
    phases_executed: int
    steps_dispatched: int
    gates_checked: int
    action_types_seen: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        lines = [
            f"Harness: {status}",
            f"  Phases: {self.phases_executed}",
            f"  Steps dispatched: {self.steps_dispatched}",
            f"  Gates checked: {self.gates_checked}",
            f"  Action types: {', '.join(sorted(self.action_types_seen))}",
        ]
        if self.errors:
            lines.append(f"  Errors ({len(self.errors)}):")
            for e in self.errors:
                lines.append(f"    - {e}")
        if self.warnings:
            lines.append(f"  Warnings ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"    - {w}")
        return "\n".join(lines)


def build_synthetic_plan(task_id: str = "test-harness-run") -> MachinePlan:
    """Build a synthetic plan that exercises all execution engine features.

    The plan has 4 phases:

    1. Setup -- single step with dependency chain seed
    2. Implementation -- team step (lead + implementer) with parallel-safe sibling
    3. Validation -- automation step (command) + gate
    4. Review -- step with approval_required

    This exercises: DISPATCH, GATE, team steps, dependency chains,
    parallel-safe flags, automation steps, and approval workflows.
    """
    plan = MachinePlan(
        task_id=task_id,
        task_summary="Baton self-test harness run",
        risk_level="LOW",
        budget_tier="lean",
        execution_mode="phased",
        task_type="test",
        phases=[
            # Phase 1: Simple dispatch
            PlanPhase(
                phase_id=1,
                name="Setup",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="test-engineer",
                        task_description="Initialize test environment",
                        model="sonnet",
                        step_type="developing",
                        deliverables=["test-env-ready"],
                    ),
                ],
                gate=PlanGate(
                    gate_type="build",
                    command="echo 'build OK'",
                    description="Verify build passes",
                ),
            ),
            # Phase 2: Team step + parallel-safe sibling
            PlanPhase(
                phase_id=2,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="team",
                        task_description="Implement core feature",
                        model="sonnet",
                        step_type="developing",
                        depends_on=["1.1"],
                        team=[
                            TeamMember(
                                member_id="2.1.a",
                                agent_name="backend-engineer--python",
                                role="lead",
                                task_description="Build the core module",
                            ),
                            TeamMember(
                                member_id="2.1.b",
                                agent_name="test-engineer",
                                role="implementer",
                                task_description="Write tests for the core module",
                            ),
                        ],
                    ),
                    PlanStep(
                        step_id="2.2",
                        agent_name="frontend-engineer",
                        task_description="Build UI component",
                        model="sonnet",
                        step_type="developing",
                        depends_on=["1.1"],
                        parallel_safe=True,
                    ),
                ],
                gate=PlanGate(
                    gate_type="test",
                    command="echo 'tests OK'",
                    description="Run test suite",
                ),
            ),
            # Phase 3: Automation step
            PlanPhase(
                phase_id=3,
                name="Validation",
                steps=[
                    PlanStep(
                        step_id="3.1",
                        agent_name="task-runner",
                        task_description="Run validation checks",
                        model="sonnet",
                        step_type="automation",
                        command="echo 'validation passed'",
                        depends_on=["2.1", "2.2"],
                    ),
                ],
                gate=PlanGate(
                    gate_type="build",
                    command="echo 'validation OK'",
                    description="Verify validation passed",
                ),
            ),
            # Phase 4: Review with approval
            PlanPhase(
                phase_id=4,
                name="Review",
                steps=[
                    PlanStep(
                        step_id="4.1",
                        agent_name="code-reviewer",
                        task_description="Final review of all changes",
                        model="opus",
                        step_type="reviewing",
                        depends_on=["3.1"],
                    ),
                ],
                approval_required=True,
                approval_description="Review all changes before merge",
            ),
        ],
        detected_stack="python",
        complexity="medium",
        classification_source="test-harness",
    )
    plan.created_at = datetime.now(timezone.utc).isoformat()
    return plan


def run_harness(*, dry_run: bool = True, verbose: bool = False) -> HarnessResult:
    """Drive a synthetic plan through the execution engine.

    Args:
        dry_run: If True, use dry-run mode (no actual agent dispatches).
        verbose: If True, print each action as it is processed.

    Returns:
        HarnessResult with pass/fail and diagnostics.
    """
    from agent_baton.core.engine.executor import ExecutionEngine

    result = HarnessResult(
        passed=True,
        phases_executed=0,
        steps_dispatched=0,
        gates_checked=0,
    )

    # Create a temporary context root -- must NOT touch the project's
    # .claude/team-context/.
    with tempfile.TemporaryDirectory(prefix="baton-harness-") as tmpdir:
        context_root = Path(tmpdir)
        task_id = f"harness-{int(time.time())}"

        plan = build_synthetic_plan(task_id=task_id)

        # Save plan so the engine can find it if needed.
        plan_path = context_root / "plan.json"
        plan_path.write_text(
            json.dumps(plan.to_dict(), indent=2), encoding="utf-8"
        )

        # Create engine -- no storage backend, no event bus, no
        # knowledge resolver.  Pure in-memory file-mode execution.
        engine = ExecutionEngine(
            team_context_root=context_root,
            task_id=task_id,
        )

        # Start execution
        try:
            action = engine.start(plan)
            result.action_types_seen.add(action.action_type.value)
            if verbose:
                print(f"  START -> {action.action_type.value}: {action.message}")
        except Exception as exc:
            result.passed = False
            result.errors.append(f"start() failed: {exc}")
            return result

        # Drive the loop (max 50 iterations as safety valve)
        for iteration in range(50):
            atype = action.action_type

            if atype == ActionType.DISPATCH:
                result.steps_dispatched += 1
                step_id = action.step_id
                agent = action.agent_name

                if verbose:
                    print(f"  DISPATCH {step_id} -> {agent}")

                # Determine if this is a team member dispatch by checking
                # the serialised dict (is_team_member flag).
                action_dict = action.to_dict()
                is_team_member = action_dict.get("is_team_member", False)

                # Simulate successful completion
                try:
                    if is_team_member:
                        parent_id = action_dict.get(
                            "parent_step_id",
                            ".".join(step_id.split(".")[:2]),
                        )
                        engine.record_team_member_result(
                            step_id=parent_id,
                            member_id=step_id,
                            agent_name=agent,
                            status="complete",
                            outcome=f"Harness: {agent} completed {step_id}",
                        )
                    else:
                        engine.record_step_result(
                            step_id=step_id,
                            agent_name=agent,
                            status="complete",
                            outcome=f"Harness: {agent} completed {step_id}",
                        )
                except Exception as exc:
                    result.warnings.append(
                        f"record failed for {step_id}: {exc}"
                    )

                # Also dispatch parallel actions if present
                for pa in action.parallel_actions:
                    result.steps_dispatched += 1
                    pa_step = pa.step_id
                    pa_agent = pa.agent_name
                    result.action_types_seen.add(pa.action_type.value)
                    if verbose:
                        print(
                            f"  DISPATCH (parallel) {pa_step} -> {pa_agent}"
                        )
                    pa_dict = pa.to_dict()
                    pa_is_team = pa_dict.get("is_team_member", False)
                    try:
                        if pa_is_team:
                            pa_parent = pa_dict.get(
                                "parent_step_id",
                                ".".join(pa_step.split(".")[:2]),
                            )
                            engine.record_team_member_result(
                                step_id=pa_parent,
                                member_id=pa_step,
                                agent_name=pa_agent,
                                status="complete",
                                outcome=f"Harness: {pa_agent} completed {pa_step}",
                            )
                        else:
                            engine.record_step_result(
                                step_id=pa_step,
                                agent_name=pa_agent,
                                status="complete",
                                outcome=f"Harness: {pa_agent} completed {pa_step}",
                            )
                    except Exception as exc:
                        result.warnings.append(
                            f"record failed for {pa_step}: {exc}"
                        )

            elif atype == ActionType.GATE:
                result.gates_checked += 1
                phase_id = action.phase_id
                if verbose:
                    print(f"  GATE phase {phase_id}")
                try:
                    engine.record_gate_result(
                        phase_id=phase_id,
                        passed=True,
                        output="Harness: gate passed",
                    )
                    result.phases_executed += 1
                except Exception as exc:
                    result.warnings.append(
                        f"gate record failed for phase {phase_id}: {exc}"
                    )

            elif atype == ActionType.APPROVAL:
                if verbose:
                    print(f"  APPROVAL phase {action.phase_id}")
                try:
                    engine.record_approval_result(
                        phase_id=action.phase_id,
                        result="approve",
                        feedback="Harness: auto-approved",
                    )
                except Exception as exc:
                    result.warnings.append(f"approval failed: {exc}")
                result.action_types_seen.add("approval")

            elif atype == ActionType.COMPLETE:
                if verbose:
                    print("  COMPLETE")
                result.action_types_seen.add("complete")
                break

            elif atype == ActionType.FAILED:
                result.passed = False
                result.errors.append(
                    f"Engine returned FAILED: {action.message}"
                )
                break

            elif atype == ActionType.WAIT:
                if verbose:
                    print("  WAIT (parallel steps pending)")
                result.action_types_seen.add("wait")

            else:
                result.action_types_seen.add(atype.value)
                if verbose:
                    print(f"  {atype.value}: {action.message}")

            # Get next action
            try:
                action = engine.next_action()
                result.action_types_seen.add(action.action_type.value)
            except Exception as exc:
                result.passed = False
                result.errors.append(
                    f"next_action() failed at iteration {iteration}: {exc}"
                )
                break
        else:
            result.passed = False
            result.errors.append("Safety limit reached (50 iterations)")

        # Validate expectations
        expected_types = {"dispatch", "gate", "complete"}
        missing = expected_types - result.action_types_seen
        if missing:
            result.warnings.append(
                f"Expected action types not seen: {missing}"
            )

        if result.steps_dispatched == 0:
            result.passed = False
            result.errors.append("No steps were dispatched")

        if result.gates_checked == 0:
            result.warnings.append("No gates were checked")

    return result
