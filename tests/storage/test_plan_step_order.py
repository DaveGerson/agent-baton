"""Intra-phase step ordering must survive a SQLite round-trip (F023).

``plan_steps`` stores ``step_id`` as TEXT.  If the load path reconstructs
intra-phase order by sorting on that text column, planner-generated ids of
the form ``"<phase>.<n>"`` sort lexicographically — ``"1.10"`` lands before
``"1.2"`` — so any phase with ten or more steps comes back scrambled.

Two user-visible consequences, both exercised here:

* With the ordinary sequential dependency chain the planner emits
  (step ``1.N`` ``depends_on`` ``1.N-1``), the scrambled list violates
  ``MachinePlan._validate_plan_graph_integrity`` and the load raises
  ``ValidationError`` outright — the execution can never be resumed.
* Without dependencies the corruption is silent:
  ``ExecutionState.current_step_index`` indexes into ``phase.steps``, so a
  resumed run dispatches a different step than the engine believes it is on.

These tests assert observable behaviour (the order of the reloaded steps),
never the SQL text or schema shape, so any correct fix satisfies them.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import (
    ExecutionState,
    MachinePlan,
    PlanPhase,
    PlanStep,
)


@pytest.fixture()
def store(tmp_path: Path) -> SqliteStorage:
    """Isolated SqliteStorage backed by a temp-path DB."""
    return SqliteStorage(tmp_path / "baton.db")


def _plan_with_step_ids(
    step_ids: list[str],
    *,
    task_id: str,
    chain_dependencies: bool,
) -> MachinePlan:
    """One-phase plan whose single phase holds ``step_ids`` in that order.

    When ``chain_dependencies`` is set, each step declares the previous
    step_id in ``depends_on`` — the shape the planner emits for sequential
    work, and the shape that makes mis-ordering a hard load failure.
    """
    steps: list[PlanStep] = []
    for index, step_id in enumerate(step_ids):
        depends_on: list[str] = []
        if chain_dependencies and index > 0:
            depends_on = [step_ids[index - 1]]
        steps.append(
            PlanStep(
                step_id=step_id,
                agent_name="backend-engineer--python",
                task_description=f"Do the work for {step_id}",
                model="sonnet",
                depends_on=depends_on,
                deliverables=[f"agent_baton/step_{index}.py"],
                allowed_paths=["agent_baton/"],
            )
        )
    return MachinePlan(
        task_id=task_id,
        task_summary="Eleven-step phase",
        risk_level="MEDIUM",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[PlanPhase(phase_id=1, name="Implementation", steps=steps)],
        created_at="2026-01-01T00:00:00+00:00",
    )


# Planner-generated ids: "1.10" and "1.11" sort before "1.2" as text.
ELEVEN_STEP_IDS = [f"1.{n}" for n in range(1, 12)]


class TestPlanStepOrderRoundTrip:
    def test_ten_plus_step_phase_with_dependency_chain_reloads(
        self, store: SqliteStorage
    ) -> None:
        """A dependency-chained 11-step phase must reload, in order."""
        plan = _plan_with_step_ids(
            ELEVEN_STEP_IDS,
            task_id="task-f023-chained",
            chain_dependencies=True,
        )
        store.save_plan(plan)

        loaded = store.load_plan(plan.task_id)

        assert loaded is not None
        assert [s.step_id for s in loaded.phases[0].steps] == ELEVEN_STEP_IDS

    def test_ten_plus_step_phase_without_dependencies_keeps_order(
        self, store: SqliteStorage
    ) -> None:
        """Silent variant: no deps, so no validation error masks the scramble."""
        plan = _plan_with_step_ids(
            ELEVEN_STEP_IDS,
            task_id="task-f023-unchained",
            chain_dependencies=False,
        )
        store.save_plan(plan)

        loaded = store.load_plan(plan.task_id)

        assert loaded is not None
        assert [s.step_id for s in loaded.phases[0].steps] == ELEVEN_STEP_IDS

    def test_step_payloads_travel_with_their_positions(
        self, store: SqliteStorage
    ) -> None:
        """Order is per-step, not just the id list: payloads must line up."""
        plan = _plan_with_step_ids(
            ELEVEN_STEP_IDS,
            task_id="task-f023-payloads",
            chain_dependencies=False,
        )
        store.save_plan(plan)

        loaded = store.load_plan(plan.task_id)

        assert loaded is not None
        assert [
            (s.step_id, s.task_description, s.deliverables)
            for s in loaded.phases[0].steps
        ] == [
            (s.step_id, s.task_description, s.deliverables)
            for s in plan.phases[0].steps
        ]

    def test_non_lexicographic_step_ids_keep_insertion_order(
        self, store: SqliteStorage
    ) -> None:
        """Author-declared order wins over any alphabetical ordering."""
        declared = ["b", "a", "c"]
        plan = _plan_with_step_ids(
            declared,
            task_id="task-f023-alpha",
            chain_dependencies=False,
        )
        store.save_plan(plan)

        loaded = store.load_plan(plan.task_id)

        assert loaded is not None
        assert [s.step_id for s in loaded.phases[0].steps] == declared

    def test_order_survives_a_resave(self, store: SqliteStorage) -> None:
        """Upserting the same plan twice must not degrade step order."""
        plan = _plan_with_step_ids(
            ELEVEN_STEP_IDS,
            task_id="task-f023-resave",
            chain_dependencies=True,
        )
        store.save_plan(plan)
        store.save_plan(plan)

        loaded = store.load_plan(plan.task_id)

        assert loaded is not None
        assert [s.step_id for s in loaded.phases[0].steps] == ELEVEN_STEP_IDS


class TestExecutionStepOrderRoundTrip:
    def test_execution_reload_preserves_step_order(
        self, store: SqliteStorage
    ) -> None:
        """``load_execution`` is the resume path — it must round-trip too.

        ``current_step_index`` indexes into ``phase.steps``, so a scrambled
        reload dispatches the wrong step on resume.
        """
        plan = _plan_with_step_ids(
            ELEVEN_STEP_IDS,
            task_id="task-f023-exec",
            chain_dependencies=True,
        )
        state = ExecutionState(
            task_id=plan.task_id,
            plan=plan,
            current_phase=0,
            current_step_index=9,
            status="running",
            started_at="2026-01-01T00:00:00+00:00",
        )
        store.save_execution(state)

        loaded = store.load_execution(plan.task_id)

        assert loaded is not None
        assert [s.step_id for s in loaded.plan.phases[0].steps] == ELEVEN_STEP_IDS
        resumed_step = loaded.plan.phases[0].steps[loaded.current_step_index]
        assert resumed_step.step_id == "1.10"
