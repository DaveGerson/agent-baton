"""Tests for ``ExecutionEngine._process_pending_expansions`` manager-mode
publishing (Phase 6, 6.3).

Exercises the executor's scope-expansion phase-generation path directly
(mirrors ``tests/engine/test_executor_goal_wrap.py``'s style of calling
the private helper against a hand-built ``ExecutionState``) -- no LLM,
no network, no live ``bd``/``claude`` binaries involved.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.manager.rebuild import ManagerArtifactRebuildResult
from agent_baton.models.execution import ExecutionState, MachinePlan, PlanPhase, PlanStep


def _manager_plan(task_id: str = "t-scope-mm") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Add a reporting endpoint",
        manager_mode=True,
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[PlanStep(
                    step_id="1.1", agent_name="backend-engineer",
                    task_description="Implement the endpoint.",
                )],
            ),
        ],
    )


def _started_engine(tmp_path: Path, plan: MachinePlan) -> tuple[ExecutionEngine, ExecutionState]:
    engine = ExecutionEngine(team_context_root=tmp_path)
    engine.start(plan)
    state = engine._load_execution()
    assert state is not None
    return engine, state


class TestScopeExpansionManagerModePublish:
    def test_expansion_publishes_sidecars_for_new_phase(self, tmp_path: Path) -> None:
        plan = _manager_plan()
        engine, state = _started_engine(tmp_path, plan)
        state.pending_scope_expansions = [
            {"description": "Add RBAC middleware to auth module", "phase_id": 1},
        ]

        engine._process_pending_expansions(state)

        assert state.scope_expansions_applied == 1
        assert len(state.plan.phases) == 2
        new_phase = state.plan.phases[1]

        paths = ManagerArtifactPaths(tmp_path, state.task_id)
        assert paths.revision_manifest.is_file()
        for step in new_phase.steps:
            assert paths.scope_contract(step.step_id, ext="json").is_file()
            assert paths.context_bundle(step.step_id).is_file()

    def test_expansion_publish_failure_drops_expansion_without_crashing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        plan = _manager_plan()
        engine, state = _started_engine(tmp_path, plan)
        state.pending_scope_expansions = [
            {"description": "Add RBAC middleware to auth module", "phase_id": 1},
        ]

        monkeypatch.setattr(
            engine,
            "_publish_manager_artifacts",
            lambda _plan, *, trigger: ManagerArtifactRebuildResult(
                ok=False, errors=["forced failure for test"],
            ),
        )

        # Must not raise -- a rebuild failure drops this one expansion and
        # the phase-boundary transition continues.
        engine._process_pending_expansions(state)

        assert state.scope_expansions_applied == 0
        assert len(state.plan.phases) == 1
        assert state.pending_scope_expansions == []


def _plain_plan(
    task_id: str = "t-scope-plain",
    phases: list[PlanPhase] | None = None,
) -> MachinePlan:
    """A non-manager_mode plan -- the staleness regression below applies
    regardless of manager_mode; this fixture isolates that."""
    return MachinePlan(
        task_id=task_id,
        task_summary="Add a reporting endpoint",
        phases=phases if phases is not None else [
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[PlanStep(
                    step_id="1.1", agent_name="backend-engineer",
                    task_description="Implement the endpoint.",
                )],
            ),
        ],
    )


class TestScopeExpansionStateStaysLiveAfterAmend:
    """Regression: ``amend_plan()`` has no ``state`` parameter -- it
    reloads and persists its OWN copy internally. Before the fix,
    ``_process_pending_expansions``'s trailing ``_save_execution(state)``
    used the caller's now-stale pre-amendment ``state`` object and
    silently reverted every expansion this call had just applied. Not
    manager-mode-specific -- this must hold for a plain plan too."""

    def test_single_expansion_survives_the_trailing_save(self, tmp_path: Path) -> None:
        plan = _plain_plan()
        engine, state = _started_engine(tmp_path, plan)
        state.pending_scope_expansions = [
            {"description": "Add RBAC middleware to auth module", "phase_id": 1},
        ]

        engine._process_pending_expansions(state)

        # The caller's own `state` object reflects the amendment ...
        assert len(state.plan.phases) == 2
        assert len(state.amendments) == 1
        # ... and so does disk -- the trailing save must not have
        # clobbered it with a stale pre-amendment snapshot.
        reloaded = engine._load_execution()
        assert reloaded is not None
        assert len(reloaded.plan.phases) == 2
        assert len(reloaded.amendments) == 1
        assert reloaded.pending_scope_expansions == []

    def test_two_expansions_in_one_call_both_survive(self, tmp_path: Path) -> None:
        """Multiple pending expansions processed in the same call: each
        iteration's guardrail check and the next amend_plan() call must
        both see the previous iteration's amendment, not a stale plan.

        Uses a 4-step original plan so the step-count-ceiling guardrail
        (2x the original step count) comfortably allows both one-step
        expansion phases -- the point under test is state staleness, not
        guardrail thresholds.
        """
        plan = _plain_plan(
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Implement",
                    steps=[
                        PlanStep(step_id=f"1.{i}", agent_name="backend-engineer",
                                 task_description=f"step {i}")
                        for i in range(1, 5)
                    ],
                ),
            ],
        )
        engine, state = _started_engine(tmp_path, plan)
        state.pending_scope_expansions = [
            {"description": "Add RBAC middleware to auth module", "phase_id": 1},
            {"description": "Add audit logging to the auth flow", "phase_id": 1},
        ]

        engine._process_pending_expansions(state)

        assert state.scope_expansions_applied == 2
        assert len(state.plan.phases) == 3
        reloaded = engine._load_execution()
        assert reloaded is not None
        assert len(reloaded.plan.phases) == 3
        assert len(reloaded.amendments) == 2
