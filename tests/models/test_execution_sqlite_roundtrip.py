"""SQLite persistence roundtrip tests for ExecutionState and MachinePlan.

These tests verify that the SqliteStorage backend can save and reload
``ExecutionState`` and ``MachinePlan`` instances without losing any data
that the backend actually persists.

The SQLite backend is intentionally lossy for some fields that live only
in the JSON file backend (e.g. ``consolidation_result``, ``resource_limits``,
``foresight_insights``).  The tests compare only the fields that the backend
explicitly stores; see the inline notes for what is and is not persisted.

All tests use a temp-path-based SQLite DB; they never touch any real
``baton.db`` on the developer's machine.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import (
    ApprovalResult,
    ExecutionState,
    FeedbackResult,
    GateResult,
    InteractionTurn,
    MachinePlan,
    PlanAmendment,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
    TeamMember,
    TeamStepResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path: Path) -> SqliteStorage:
    """Isolated SqliteStorage instance backed by a temp-path DB."""
    return SqliteStorage(tmp_path / "baton_test.db")


def _minimal_plan(task_id: str = "task-sqlite-rt-001") -> MachinePlan:
    """A MachinePlan with only fields the SQLite backend persists."""
    step_1 = PlanStep(
        step_id="1.1",
        agent_name="architect",
        task_description="Design the schema.",
        model="opus",
        depends_on=[],
        deliverables=["docs/schema.md"],
        allowed_paths=["docs/"],
        blocked_paths=[],
        context_files=["CLAUDE.md"],
        step_type="planning",
    )
    step_2 = PlanStep(
        step_id="1.2",
        agent_name="backend-engineer--python",
        task_description="Implement the models.",
        model="sonnet",
        depends_on=["1.1"],
        deliverables=["agent_baton/models/new_model.py"],
        allowed_paths=["agent_baton/models/"],
        blocked_paths=[],
        context_files=["docs/schema.md"],
        step_type="developing",
    )
    gate_1 = PlanGate(
        gate_type="test",
        command="pytest tests/models/ -v",
        description="Model tests pass.",
        fail_on=["test failures"],
    )
    phase_1 = PlanPhase(
        phase_id=1,
        name="Design",
        steps=[step_1, step_2],
        gate=gate_1,
        approval_required=True,
        approval_description="Review before implementation.",
    )

    team_a = TeamMember(
        member_id="2.1.a",
        agent_name="backend-engineer--python",
        role="implementer",
        task_description="Implement routes.",
        model="sonnet",
        depends_on=[],
        deliverables=["agent_baton/api/routes/v2.py"],
    )
    team_b = TeamMember(
        member_id="2.1.b",
        agent_name="test-engineer",
        role="implementer",
        task_description="Write route tests.",
        model="sonnet",
        depends_on=["2.1.a"],
        deliverables=["tests/api/test_v2.py"],
    )
    step_2_1 = PlanStep(
        step_id="2.1",
        agent_name="backend-engineer--python",
        task_description="Implement the API routes as a team step.",
        model="sonnet",
        depends_on=["1.2"],
        deliverables=["agent_baton/api/routes/v2.py", "tests/api/test_v2.py"],
        allowed_paths=["agent_baton/api/routes/", "tests/api/"],
        blocked_paths=[],
        context_files=["docs/schema.md"],
        step_type="developing",
        team=[team_a, team_b],
    )
    gate_2 = PlanGate(
        gate_type="test",
        command="pytest tests/api/ -v",
        description="API tests pass.",
        fail_on=["test failures"],
    )
    phase_2 = PlanPhase(
        phase_id=2,
        name="Implementation",
        steps=[step_2_1],
        gate=gate_2,
        approval_required=False,
    )

    return MachinePlan(
        task_id=task_id,
        task_summary="SQLite roundtrip test plan.",
        risk_level="MEDIUM",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[phase_1, phase_2],
        shared_context="Shared context for the SQLite roundtrip test.",
        pattern_source="pattern-sqlite-test",
        created_at="2026-01-15T09:00:00+00:00",
        task_type="feature",
        explicit_knowledge_packs=["python-web"],
        explicit_knowledge_docs=["docs/schema.md"],
        intervention_level="medium",
        classification_source="haiku",
        classification_signals='{"keywords": ["api", "model"]}',
        archetype="phased",
        max_retry_phases=1,
    )


def _minimal_execution_state(plan: MachinePlan) -> ExecutionState:
    """An ExecutionState with fields the SQLite backend persists."""
    turn_1 = InteractionTurn(
        role="agent",
        content="I have a question about the schema.",
        timestamp="2026-01-15T09:10:00+00:00",
        turn_number=1,
        source="agent",
    )
    turn_2 = InteractionTurn(
        role="human",
        content="Use existing patterns as reference.",
        timestamp="2026-01-15T09:12:00+00:00",
        turn_number=2,
        source="human",
    )
    mr_a = TeamStepResult(
        member_id="2.1.a",
        agent_name="backend-engineer--python",
        status="complete",
        outcome="Implemented v2 routes.",
        files_changed=["agent_baton/api/routes/v2.py"],
    )
    mr_b = TeamStepResult(
        member_id="2.1.b",
        agent_name="test-engineer",
        status="complete",
        outcome="Wrote route tests.",
        files_changed=["tests/api/test_v2.py"],
    )
    sr_1 = StepResult(
        step_id="1.1",
        agent_name="architect",
        status="complete",
        outcome="Schema designed.",
        files_changed=["docs/schema.md"],
        commit_hash="aabbcc",
        estimated_tokens=5000,
        input_tokens=4000,
        cache_read_tokens=800,
        cache_creation_tokens=100,
        output_tokens=900,
        model_id="claude-opus-4-7",
        session_id="sess-001",
        step_started_at="2026-01-15T09:05:00+00:00",
        duration_seconds=300.0,
        retries=0,
        error="",
        completed_at="2026-01-15T09:10:00+00:00",
        member_results=[],
        deviations=["Added extra section on edge cases."],
        interaction_history=[turn_1, turn_2],
        step_type="planning",
        updated_at="2026-01-15T09:10:00+00:00",
        outcome_spillover_path="",
    )
    sr_2 = StepResult(
        step_id="2.1",
        agent_name="backend-engineer--python",
        status="complete",
        outcome="v2 routes implemented and tested.",
        files_changed=["agent_baton/api/routes/v2.py", "tests/api/test_v2.py"],
        commit_hash="ddeeff",
        estimated_tokens=12000,
        input_tokens=10000,
        cache_read_tokens=2000,
        cache_creation_tokens=300,
        output_tokens=2200,
        model_id="claude-sonnet-4-6",
        session_id="sess-002",
        step_started_at="2026-01-15T10:00:00+00:00",
        duration_seconds=600.0,
        retries=0,
        error="",
        completed_at="2026-01-15T10:10:00+00:00",
        member_results=[mr_a, mr_b],
        deviations=[],
        interaction_history=[],
        step_type="developing",
        updated_at="2026-01-15T10:10:00+00:00",
        outcome_spillover_path="",
    )

    gr = GateResult(
        phase_id=1,
        gate_type="test",
        passed=True,
        output="10 passed in 2.5s",
        checked_at="2026-01-15T09:30:00+00:00",
        command="pytest tests/models/ -v",
        exit_code=0,
        decision_source="human",
        actor="dev@local",
    )

    ar = ApprovalResult(
        phase_id=1,
        result="approve",
        feedback="Looks good.",
        decided_at="2026-01-15T09:45:00+00:00",
        decision_source="human",
        actor="dev@local",
        rationale="Design meets requirements.",
    )

    fr = FeedbackResult(
        phase_id=1,
        question_id="fq1",
        chosen_option="Keep pytest",
        chosen_index=0,
        dispatched_step_id="1.3",
        decided_at="2026-01-15T09:50:00+00:00",
    )

    amend = PlanAmendment(
        amendment_id="amend-sqlite-001",
        trigger="gate_feedback",
        trigger_phase_id=1,
        description="Added cleanup step after gate review.",
        phases_added=[],
        steps_added=["1.3"],
        created_at="2026-01-15T09:35:00+00:00",
        feedback="Please add cleanup.",
        metadata={},
    )

    return ExecutionState(
        task_id=plan.task_id,
        plan=plan,
        current_phase=1,
        current_step_index=0,
        status="running",
        step_results=[sr_1, sr_2],
        gate_results=[gr],
        approval_results=[ar],
        feedback_results=[fr],
        amendments=[amend],
        started_at="2026-01-15T09:00:00+00:00",
        completed_at="",
    )


# ---------------------------------------------------------------------------
# MachinePlan SQLite roundtrip
# ---------------------------------------------------------------------------

class TestMachinePlanSqliteRoundtrip:
    def test_plan_sqlite_roundtrip_field_parity(self, store: SqliteStorage) -> None:
        """save_plan → load_plan preserves all fields the backend stores.

        The SQLite backend reconstructs MachinePlan from normalised tables.
        Fields not stored by the backend (resource_limits, foresight_insights,
        compliance_fail_closed, etc.) are absent from the loaded plan —
        this test only compares the fields that the backend actually persists.
        """
        plan = _minimal_plan("task-plan-rt-001")
        store.save_plan(plan)
        loaded = store.load_plan("task-plan-rt-001")
        assert loaded is not None

        # Fields the SQLite backend definitely persists
        assert loaded.task_id == plan.task_id
        assert loaded.task_summary == plan.task_summary
        assert loaded.risk_level == plan.risk_level
        assert loaded.budget_tier == plan.budget_tier
        assert loaded.execution_mode == plan.execution_mode
        assert loaded.git_strategy == plan.git_strategy
        assert loaded.shared_context == plan.shared_context
        assert loaded.pattern_source == plan.pattern_source
        assert loaded.created_at == plan.created_at
        assert loaded.task_type == plan.task_type
        assert loaded.intervention_level == plan.intervention_level

    def test_plan_sqlite_phases_and_steps_preserved(self, store: SqliteStorage) -> None:
        """Phase and step hierarchy survives save_plan → load_plan."""
        plan = _minimal_plan("task-plan-rt-002")
        store.save_plan(plan)
        loaded = store.load_plan("task-plan-rt-002")
        assert loaded is not None

        assert len(loaded.phases) == len(plan.phases)
        for i, orig_phase in enumerate(plan.phases):
            loaded_phase = loaded.phases[i]
            assert loaded_phase.phase_id == orig_phase.phase_id
            assert loaded_phase.name == orig_phase.name
            assert loaded_phase.approval_required == orig_phase.approval_required
            assert len(loaded_phase.steps) == len(orig_phase.steps)

    def test_plan_sqlite_gate_preserved(self, store: SqliteStorage) -> None:
        """PlanGate data survives save_plan → load_plan."""
        plan = _minimal_plan("task-plan-rt-003")
        store.save_plan(plan)
        loaded = store.load_plan("task-plan-rt-003")
        assert loaded is not None

        phase_1 = loaded.phases[0]
        assert phase_1.gate is not None
        orig_gate = plan.phases[0].gate
        assert phase_1.gate.gate_type == orig_gate.gate_type
        assert phase_1.gate.command == orig_gate.command
        assert phase_1.gate.description == orig_gate.description
        assert phase_1.gate.fail_on == orig_gate.fail_on

    def test_plan_sqlite_team_members_preserved(self, store: SqliteStorage) -> None:
        """TeamMember data in a team step survives save_plan → load_plan."""
        plan = _minimal_plan("task-plan-rt-004")
        store.save_plan(plan)
        loaded = store.load_plan("task-plan-rt-004")
        assert loaded is not None

        # Phase 2, step 2.1 is a team step
        loaded_step = loaded.phases[1].steps[0]
        orig_step = plan.phases[1].steps[0]
        assert len(loaded_step.team) == len(orig_step.team)
        for i, orig_member in enumerate(orig_step.team):
            loaded_member = loaded_step.team[i]
            assert loaded_member.member_id == orig_member.member_id
            assert loaded_member.agent_name == orig_member.agent_name
            assert loaded_member.role == orig_member.role
            assert loaded_member.task_description == orig_member.task_description

    def test_plan_sqlite_load_returns_none_for_missing(self, store: SqliteStorage) -> None:
        """load_plan returns None for a task_id that was never saved."""
        result = store.load_plan("task-does-not-exist")
        assert result is None

    def test_plan_sqlite_upsert_is_idempotent(self, store: SqliteStorage) -> None:
        """Saving the same plan twice does not raise and produces a valid result."""
        plan = _minimal_plan("task-plan-rt-005")
        store.save_plan(plan)
        store.save_plan(plan)  # second save — upsert must not fail
        loaded = store.load_plan("task-plan-rt-005")
        assert loaded is not None
        assert loaded.task_id == plan.task_id


# ---------------------------------------------------------------------------
# ExecutionState SQLite roundtrip
# ---------------------------------------------------------------------------

class TestExecutionStateSqliteRoundtrip:
    def test_execution_sqlite_roundtrip_core_fields(self, store: SqliteStorage) -> None:
        """save_execution → load_execution preserves core ExecutionState fields.

        The SQLite backend stores a subset of fields.  Fields not persisted
        (consolidation_result, delivered_knowledge, step_worktrees, etc.)
        are not compared here — they are covered by the file-backend and
        roundtrip tests in test_execution_roundtrip.py.
        """
        plan = _minimal_plan("task-exec-rt-001")
        state = _minimal_execution_state(plan)
        store.save_execution(state)
        loaded = store.load_execution("task-exec-rt-001")
        assert loaded is not None

        assert loaded.task_id == state.task_id
        assert loaded.current_phase == state.current_phase
        assert loaded.current_step_index == state.current_step_index
        assert loaded.status == state.status
        assert loaded.started_at == state.started_at

    def test_execution_sqlite_step_results_roundtrip(self, store: SqliteStorage) -> None:
        """Step results survive save_execution → load_execution with field parity."""
        plan = _minimal_plan("task-exec-rt-002")
        state = _minimal_execution_state(plan)
        store.save_execution(state)
        loaded = store.load_execution("task-exec-rt-002")
        assert loaded is not None

        assert len(loaded.step_results) == len(state.step_results)
        for orig, loaded_sr in zip(state.step_results, loaded.step_results):
            assert loaded_sr.step_id == orig.step_id
            assert loaded_sr.agent_name == orig.agent_name
            assert loaded_sr.status == orig.status
            assert loaded_sr.outcome == orig.outcome
            assert loaded_sr.files_changed == orig.files_changed
            assert loaded_sr.commit_hash == orig.commit_hash
            assert loaded_sr.estimated_tokens == orig.estimated_tokens
            assert loaded_sr.duration_seconds == orig.duration_seconds
            assert loaded_sr.retries == orig.retries
            assert loaded_sr.error == orig.error
            assert loaded_sr.completed_at == orig.completed_at
            assert loaded_sr.deviations == orig.deviations
            assert loaded_sr.step_type == orig.step_type
            assert loaded_sr.input_tokens == orig.input_tokens
            assert loaded_sr.cache_read_tokens == orig.cache_read_tokens
            assert loaded_sr.cache_creation_tokens == orig.cache_creation_tokens
            assert loaded_sr.output_tokens == orig.output_tokens
            assert loaded_sr.model_id == orig.model_id
            assert loaded_sr.session_id == orig.session_id
            assert loaded_sr.step_started_at == orig.step_started_at

    def test_execution_sqlite_team_step_results_roundtrip(self, store: SqliteStorage) -> None:
        """TeamStepResult entries inside a step survive save → load."""
        plan = _minimal_plan("task-exec-rt-003")
        state = _minimal_execution_state(plan)
        store.save_execution(state)
        loaded = store.load_execution("task-exec-rt-003")
        assert loaded is not None

        # sr at index 1 (step 2.1) has two team member results
        orig_sr = state.step_results[1]
        loaded_sr = next(r for r in loaded.step_results if r.step_id == orig_sr.step_id)
        assert len(loaded_sr.member_results) == len(orig_sr.member_results)
        for orig_mr, loaded_mr in zip(orig_sr.member_results, loaded_sr.member_results):
            assert loaded_mr.member_id == orig_mr.member_id
            assert loaded_mr.agent_name == orig_mr.agent_name
            assert loaded_mr.status == orig_mr.status
            assert loaded_mr.outcome == orig_mr.outcome
            assert loaded_mr.files_changed == orig_mr.files_changed

    def test_execution_sqlite_interaction_history_roundtrip(self, store: SqliteStorage) -> None:
        """InteractionTurn entries in a step survive save_execution → load_execution."""
        plan = _minimal_plan("task-exec-rt-004")
        state = _minimal_execution_state(plan)
        store.save_execution(state)
        loaded = store.load_execution("task-exec-rt-004")
        assert loaded is not None

        # step 1.1 has two interaction turns
        orig_sr = state.step_results[0]
        loaded_sr = next(r for r in loaded.step_results if r.step_id == orig_sr.step_id)
        assert len(loaded_sr.interaction_history) == len(orig_sr.interaction_history)
        for orig_turn, loaded_turn in zip(
            orig_sr.interaction_history, loaded_sr.interaction_history
        ):
            assert loaded_turn.role == orig_turn.role
            assert loaded_turn.content == orig_turn.content
            assert loaded_turn.timestamp == orig_turn.timestamp
            assert loaded_turn.turn_number == orig_turn.turn_number
            assert loaded_turn.source == orig_turn.source

    def test_execution_sqlite_gate_results_roundtrip(self, store: SqliteStorage) -> None:
        """GateResult entries survive save_execution → load_execution."""
        plan = _minimal_plan("task-exec-rt-005")
        state = _minimal_execution_state(plan)
        store.save_execution(state)
        loaded = store.load_execution("task-exec-rt-005")
        assert loaded is not None

        assert len(loaded.gate_results) == len(state.gate_results)
        orig_gr = state.gate_results[0]
        loaded_gr = loaded.gate_results[0]
        assert loaded_gr.phase_id == orig_gr.phase_id
        assert loaded_gr.gate_type == orig_gr.gate_type
        assert loaded_gr.passed == orig_gr.passed
        assert loaded_gr.output == orig_gr.output
        assert loaded_gr.command == orig_gr.command
        assert loaded_gr.exit_code == orig_gr.exit_code
        assert loaded_gr.decision_source == orig_gr.decision_source
        assert loaded_gr.actor == orig_gr.actor

    def test_execution_sqlite_approval_results_roundtrip(self, store: SqliteStorage) -> None:
        """ApprovalResult entries survive save_execution → load_execution."""
        plan = _minimal_plan("task-exec-rt-006")
        state = _minimal_execution_state(plan)
        store.save_execution(state)
        loaded = store.load_execution("task-exec-rt-006")
        assert loaded is not None

        assert len(loaded.approval_results) == len(state.approval_results)
        orig_ar = state.approval_results[0]
        loaded_ar = loaded.approval_results[0]
        assert loaded_ar.phase_id == orig_ar.phase_id
        assert loaded_ar.result == orig_ar.result
        assert loaded_ar.feedback == orig_ar.feedback
        assert loaded_ar.decided_at == orig_ar.decided_at
        assert loaded_ar.decision_source == orig_ar.decision_source
        assert loaded_ar.actor == orig_ar.actor
        assert loaded_ar.rationale == orig_ar.rationale

    def test_execution_sqlite_feedback_results_roundtrip(self, store: SqliteStorage) -> None:
        """FeedbackResult entries survive save_execution → load_execution."""
        plan = _minimal_plan("task-exec-rt-007")
        state = _minimal_execution_state(plan)
        store.save_execution(state)
        loaded = store.load_execution("task-exec-rt-007")
        assert loaded is not None

        assert len(loaded.feedback_results) == len(state.feedback_results)
        orig_fr = state.feedback_results[0]
        loaded_fr = loaded.feedback_results[0]
        assert loaded_fr.phase_id == orig_fr.phase_id
        assert loaded_fr.question_id == orig_fr.question_id
        assert loaded_fr.chosen_option == orig_fr.chosen_option
        assert loaded_fr.chosen_index == orig_fr.chosen_index
        assert loaded_fr.dispatched_step_id == orig_fr.dispatched_step_id

    def test_execution_sqlite_amendments_roundtrip(self, store: SqliteStorage) -> None:
        """PlanAmendment entries survive save_execution → load_execution."""
        plan = _minimal_plan("task-exec-rt-008")
        state = _minimal_execution_state(plan)
        store.save_execution(state)
        loaded = store.load_execution("task-exec-rt-008")
        assert loaded is not None

        assert len(loaded.amendments) == len(state.amendments)
        orig_am = state.amendments[0]
        loaded_am = loaded.amendments[0]
        assert loaded_am.amendment_id == orig_am.amendment_id
        assert loaded_am.trigger == orig_am.trigger
        assert loaded_am.trigger_phase_id == orig_am.trigger_phase_id
        assert loaded_am.description == orig_am.description
        assert loaded_am.phases_added == orig_am.phases_added
        assert loaded_am.steps_added == orig_am.steps_added
        assert loaded_am.feedback == orig_am.feedback

    def test_execution_sqlite_plan_preserved(self, store: SqliteStorage) -> None:
        """The embedded MachinePlan survives save_execution → load_execution."""
        plan = _minimal_plan("task-exec-rt-009")
        state = _minimal_execution_state(plan)
        store.save_execution(state)
        loaded = store.load_execution("task-exec-rt-009")
        assert loaded is not None
        assert loaded.plan.task_id == plan.task_id
        assert len(loaded.plan.phases) == len(plan.phases)

    def test_execution_sqlite_load_returns_none_for_missing(
        self, store: SqliteStorage
    ) -> None:
        """load_execution returns None for a task_id that was never saved."""
        result = store.load_execution("task-does-not-exist")
        assert result is None

    def test_execution_sqlite_repeated_save_is_idempotent(
        self, store: SqliteStorage
    ) -> None:
        """Saving the same ExecutionState twice does not raise."""
        plan = _minimal_plan("task-exec-rt-010")
        state = _minimal_execution_state(plan)
        store.save_execution(state)
        store.save_execution(state)  # second save — must be safe
        loaded = store.load_execution("task-exec-rt-010")
        assert loaded is not None
        assert loaded.task_id == state.task_id

    def test_execution_sqlite_to_dict_stability(self, store: SqliteStorage) -> None:
        """loaded.to_dict() does not raise and returns a dict with expected keys.

        The SQLite backend omits some ExecutionState fields; the loaded object
        still must be able to call .to_dict() without errors.
        """
        plan = _minimal_plan("task-exec-rt-011")
        state = _minimal_execution_state(plan)
        store.save_execution(state)
        loaded = store.load_execution("task-exec-rt-011")
        assert loaded is not None

        d = loaded.to_dict()
        assert isinstance(d, dict)
        # Core structural keys must always be present
        for key in ("task_id", "plan", "status", "step_results",
                    "gate_results", "approval_results", "amendments"):
            assert key in d, f"Expected key '{key}' missing from to_dict() output"
