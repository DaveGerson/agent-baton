"""Golden fixture generator for execution model roundtrip tests.

This script is a one-shot helper — run it manually to regenerate the JSON
fixtures in ``tests/models/golden_states/``.  The test suite does NOT run
this automatically; it only reads the committed output.

Usage (from the repo root):
    python tests/models/_generate_golden.py

Determinism contract: all timestamps are explicit ISO 8601 strings.
No ``datetime.now()`` calls appear here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the package is importable when run from repo root.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from agent_baton.models.execution import (
    ApprovalResult,
    ConsolidationResult,
    ExecutionState,
    FeedbackQuestion,
    FeedbackResult,
    FileAttribution,
    GateResult,
    InteractionTurn,
    MachinePlan,
    PlanAmendment,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
    SynthesisSpec,
    TeamMember,
    TeamStepResult,
)
from agent_baton.models.knowledge import KnowledgeAttachment, KnowledgeGapSignal, ResolvedDecision
from agent_baton.models.parallel import ResourceLimits
from agent_baton.models.taxonomy import ForesightInsight

OUT_DIR = Path(__file__).parent / "golden_states"
OUT_DIR.mkdir(exist_ok=True)


def _write(name: str, data: dict) -> None:
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  wrote {path.name}")


# ---------------------------------------------------------------------------
# Leaf types — independent fixtures
# ---------------------------------------------------------------------------

def gen_interaction_turn() -> None:
    obj = InteractionTurn(
        role="agent",
        content="I have completed the initial analysis of the codebase.",
        timestamp="2026-01-15T10:00:00+00:00",
        turn_number=1,
        source="agent",
    )
    _write("InteractionTurn", obj.to_dict())


def gen_synthesis_spec() -> None:
    obj = SynthesisSpec(
        strategy="agent_synthesis",
        synthesis_agent="code-reviewer",
        synthesis_prompt="Merge the following member outputs: {member_outcomes}",
        conflict_handling="escalate",
    )
    _write("SynthesisSpec", obj.to_dict())


def gen_team_member() -> None:
    synthesis = SynthesisSpec(
        strategy="merge_files",
        synthesis_agent="code-reviewer",
        synthesis_prompt="Combine outputs focusing on file coverage: {member_outcomes}",
        conflict_handling="auto_merge",
    )
    obj = TeamMember(
        member_id="1.1.a",
        agent_name="backend-engineer--python",
        role="lead",
        task_description="Design and implement the storage layer.",
        model="opus",
        depends_on=[],
        deliverables=["agent_baton/core/storage/new_store.py"],
        sub_team=[
            TeamMember(
                member_id="1.1.a.b",
                agent_name="test-engineer",
                role="implementer",
                task_description="Write unit tests for the storage layer.",
                model="sonnet",
                depends_on=["1.1.a"],
                deliverables=["tests/storage/test_new_store.py"],
            )
        ],
        synthesis=synthesis,
    )
    _write("TeamMember", obj.to_dict())


def gen_plan_gate() -> None:
    obj = PlanGate(
        gate_type="test",
        command="pytest tests/storage/ -v --tb=short",
        description="All storage tests must pass.",
        fail_on=["test failures", "import errors"],
    )
    _write("PlanGate", obj.to_dict())


def gen_feedback_question() -> None:
    obj = FeedbackQuestion(
        question_id="q1",
        question="Which testing framework should be used for the new feature?",
        context="The team currently uses pytest but has been considering switching to unittest.",
        options=["Keep pytest", "Switch to unittest", "Use both in parallel"],
        option_agents=["test-engineer", "test-engineer", "architect"],
        option_prompts=[
            "Maintain pytest suite for {task}",
            "Migrate tests to unittest for {task}",
            "Create dual-framework harness for {task}",
        ],
    )
    _write("FeedbackQuestion", obj.to_dict())


def gen_plan_step() -> None:
    synthesis = SynthesisSpec(
        strategy="concatenate",
        synthesis_agent="code-reviewer",
        synthesis_prompt="",
        conflict_handling="auto_merge",
    )
    team_member_a = TeamMember(
        member_id="1.2.a",
        agent_name="backend-engineer--python",
        role="implementer",
        task_description="Implement the new API endpoint.",
        model="sonnet",
        depends_on=[],
        deliverables=["agent_baton/api/routes/new_endpoint.py"],
    )
    team_member_b = TeamMember(
        member_id="1.2.b",
        agent_name="test-engineer",
        role="implementer",
        task_description="Write integration tests for the new endpoint.",
        model="sonnet",
        depends_on=["1.2.a"],
        deliverables=["tests/api/test_new_endpoint.py"],
    )
    ka = KnowledgeAttachment(
        source="planner-matched:relevance",
        pack_name="python-web",
        document_name="fastapi-patterns.md",
        path="/path/to/fastapi-patterns.md",
        delivery="inline",
        retrieval="file",
        grounding="Use these FastAPI patterns for the implementation.",
        token_estimate=500,
    )
    obj = PlanStep(
        step_id="1.2",
        agent_name="backend-engineer--python",
        task_description="Add the new /api/v1/items endpoint with full CRUD support.",
        model="sonnet",
        depends_on=["1.1"],
        deliverables=["agent_baton/api/routes/items.py", "tests/api/test_items.py"],
        allowed_paths=["agent_baton/api/routes/", "tests/api/"],
        blocked_paths=["agent_baton/core/"],
        context_files=["agent_baton/api/CLAUDE.md", "agent_baton/models/execution.py"],
        team=[team_member_a, team_member_b],
        knowledge=[ka],
        synthesis=synthesis,
        mcp_servers=["filesystem"],
        interactive=False,
        max_turns=10,
        step_type="developing",
        command="",
        expected_outcome="The /api/v1/items endpoint returns 200 for GET and 201 for POST with valid payloads.",
        timeout_seconds=3600,
        parallel_safe=True,
        max_estimated_minutes=45,
    )
    _write("PlanStep", obj.to_dict())


def gen_plan_phase() -> None:
    gate = PlanGate(
        gate_type="test",
        command="pytest tests/ -v",
        description="Full test suite must pass.",
        fail_on=["any test failure"],
    )
    fq = FeedbackQuestion(
        question_id="fq1",
        question="Should we add a caching layer?",
        context="Phase 1 profiling shows 40% of requests are repeated queries.",
        options=["Yes, add Redis cache", "No, optimize queries instead"],
        option_agents=["backend-engineer--python", "backend-engineer--python"],
        option_prompts=[
            "Add Redis caching for {task}",
            "Optimize database queries for {task}",
        ],
    )
    step1 = PlanStep(
        step_id="1.1",
        agent_name="architect",
        task_description="Design the new data model schema.",
        model="opus",
        depends_on=[],
        deliverables=["docs/data-model.md"],
        allowed_paths=["docs/"],
        blocked_paths=[],
        context_files=["agent_baton/models/CLAUDE.md"],
        step_type="planning",
        expected_outcome="A clear data model schema document is produced.",
    )
    step2 = PlanStep(
        step_id="1.2",
        agent_name="backend-engineer--python",
        task_description="Implement the new data model classes.",
        model="sonnet",
        depends_on=["1.1"],
        deliverables=["agent_baton/models/new_model.py", "tests/models/test_new_model.py"],
        allowed_paths=["agent_baton/models/", "tests/models/"],
        blocked_paths=[],
        context_files=["docs/data-model.md"],
        step_type="developing",
    )
    obj = PlanPhase(
        phase_id=1,
        name="Design and Implementation",
        steps=[step1, step2],
        gate=gate,
        approval_required=True,
        approval_description="Review the data model design before proceeding to phase 2.",
        feedback_questions=[fq],
        risk_level="MEDIUM",
    )
    _write("PlanPhase", obj.to_dict())


def gen_plan_amendment() -> None:
    obj = PlanAmendment(
        amendment_id="amend-001",
        trigger="gate_feedback",
        trigger_phase_id=1,
        description="Added remediation step after test gate failure: fix import error in storage module.",
        phases_added=[],
        steps_added=["2.1"],
        created_at="2026-01-15T12:30:00+00:00",
        feedback="Tests failed due to missing import. Added a remediation step.",
        metadata={"gate_exit_code": "1", "gate_command": "pytest tests/storage/"},
    )
    _write("PlanAmendment", obj.to_dict())


def gen_team_step_result() -> None:
    obj = TeamStepResult(
        member_id="1.1.a",
        agent_name="backend-engineer--python",
        status="complete",
        outcome="Implemented the new storage backend with full CRUD support.",
        files_changed=[
            "agent_baton/core/storage/new_store.py",
            "tests/storage/test_new_store.py",
        ],
    )
    _write("TeamStepResult", obj.to_dict())


def gen_step_result() -> None:
    turn1 = InteractionTurn(
        role="agent",
        content="I have analyzed the requirements and have a question about the schema.",
        timestamp="2026-01-15T10:05:00+00:00",
        turn_number=1,
        source="agent",
    )
    turn2 = InteractionTurn(
        role="human",
        content="The schema should follow the existing pattern in models/execution.py.",
        timestamp="2026-01-15T10:07:00+00:00",
        turn_number=2,
        source="human",
    )
    mr1 = TeamStepResult(
        member_id="1.1.a",
        agent_name="backend-engineer--python",
        status="complete",
        outcome="Implemented new storage backend.",
        files_changed=["agent_baton/core/storage/new_store.py"],
    )
    mr2 = TeamStepResult(
        member_id="1.1.b",
        agent_name="test-engineer",
        status="complete",
        outcome="Wrote unit tests for storage backend.",
        files_changed=["tests/storage/test_new_store.py"],
    )
    obj = StepResult(
        step_id="1.1",
        agent_name="backend-engineer--python",
        status="complete",
        outcome="Implemented the new storage module with all required CRUD operations and tests.",
        files_changed=[
            "agent_baton/core/storage/new_store.py",
            "tests/storage/test_new_store.py",
        ],
        commit_hash="abc123def456",
        estimated_tokens=15000,
        input_tokens=12000,
        cache_read_tokens=3000,
        cache_creation_tokens=500,
        output_tokens=2500,
        model_id="claude-sonnet-4-6",
        session_id="sess-abc123",
        step_started_at="2026-01-15T10:00:00+00:00",
        duration_seconds=187.5,
        retries=0,
        error="",
        completed_at="2026-01-15T10:03:07+00:00",
        member_results=[mr1, mr2],
        deviations=["Added extra helper function not in the plan for better testability."],
        interaction_history=[turn1, turn2],
        step_type="developing",
        updated_at="2026-01-15T10:03:07+00:00",
        outcome_spillover_path="",
    )
    _write("StepResult", obj.to_dict())


def gen_approval_result() -> None:
    obj = ApprovalResult(
        phase_id=1,
        result="approve-with-feedback",
        feedback="Approved, but please add docstrings to all public methods before phase 2.",
        decided_at="2026-01-15T11:00:00+00:00",
        decision_source="human",
        actor="jdoe@workstation.local",
        rationale="Code is functionally correct; documentation gap is minor and can be remediated inline.",
    )
    _write("ApprovalResult", obj.to_dict())


def gen_gate_result() -> None:
    obj = GateResult(
        phase_id=1,
        gate_type="test",
        passed=True,
        output="========================= 42 passed in 3.14s =========================",
        checked_at="2026-01-15T10:45:00+00:00",
        command="pytest tests/storage/ -v --tb=short",
        exit_code=0,
        decision_source="human",
        actor="jdoe@workstation.local",
    )
    _write("GateResult", obj.to_dict())


def gen_feedback_result() -> None:
    obj = FeedbackResult(
        phase_id=1,
        question_id="q1",
        chosen_option="Keep pytest",
        chosen_index=0,
        dispatched_step_id="2.1",
        decided_at="2026-01-15T11:30:00+00:00",
    )
    _write("FeedbackResult", obj.to_dict())


def gen_file_attribution() -> None:
    obj = FileAttribution(
        file_path="agent_baton/core/storage/new_store.py",
        step_id="1.1",
        agent_name="backend-engineer--python",
        insertions=142,
        deletions=3,
    )
    _write("FileAttribution", obj.to_dict())


def gen_consolidation_result() -> None:
    fa1 = FileAttribution(
        file_path="agent_baton/core/storage/new_store.py",
        step_id="1.1",
        agent_name="backend-engineer--python",
        insertions=142,
        deletions=3,
    )
    fa2 = FileAttribution(
        file_path="tests/storage/test_new_store.py",
        step_id="1.1",
        agent_name="test-engineer",
        insertions=88,
        deletions=0,
    )
    obj = ConsolidationResult(
        status="success",
        rebased_commits=[
            {
                "step_id": "1.1",
                "agent_name": "backend-engineer--python",
                "original_hash": "deadbeef",
                "new_hash": "cafebabe",
            },
            {
                "step_id": "1.2",
                "agent_name": "code-reviewer",
                "original_hash": "fee1dead",
                "new_hash": "c0ffee00",
            },
        ],
        final_head="c0ffee00cafe1234",
        base_commit="0000000000000000",
        files_changed=[
            "agent_baton/core/storage/new_store.py",
            "tests/storage/test_new_store.py",
        ],
        total_insertions=230,
        total_deletions=3,
        attributions=[fa1, fa2],
        conflict_files=[],
        conflict_step_id="",
        skipped_steps=[],
        started_at="2026-01-15T15:00:00+00:00",
        completed_at="2026-01-15T15:01:30+00:00",
        error="",
    )
    _write("ConsolidationResult", obj.to_dict())


def gen_machine_plan() -> None:
    """MachinePlan with 2 phases, each with multiple steps and gates."""
    # Phase 1 steps
    step_1_1 = PlanStep(
        step_id="1.1",
        agent_name="architect",
        task_description="Design the new API schema and data models.",
        model="opus",
        depends_on=[],
        deliverables=["docs/api-schema.md", "docs/data-model.md"],
        allowed_paths=["docs/"],
        blocked_paths=[],
        context_files=["agent_baton/models/CLAUDE.md"],
        step_type="planning",
        expected_outcome="API schema document with all endpoint contracts defined.",
        parallel_safe=False,
    )
    step_1_2 = PlanStep(
        step_id="1.2",
        agent_name="backend-engineer--python",
        task_description="Implement the Pydantic models as defined in the schema.",
        model="sonnet",
        depends_on=["1.1"],
        deliverables=["agent_baton/models/new_models.py"],
        allowed_paths=["agent_baton/models/"],
        blocked_paths=["agent_baton/core/"],
        context_files=["docs/data-model.md"],
        step_type="developing",
        expected_outcome="All Pydantic models defined with full type hints and docstrings.",
        timeout_seconds=3600,
        parallel_safe=False,
    )
    gate_1 = PlanGate(
        gate_type="test",
        command="pytest tests/models/ -v --tb=short",
        description="Model tests must pass before proceeding.",
        fail_on=["test failures", "import errors"],
    )
    fq_1 = FeedbackQuestion(
        question_id="fq1",
        question="Should model validation be strict or permissive?",
        context="Strict validation catches bugs early but may break legacy clients.",
        options=["Strict validation (recommended)", "Permissive with warnings"],
        option_agents=["backend-engineer--python", "architect"],
        option_prompts=[
            "Apply strict Pydantic validation for {task}",
            "Add permissive validation with deprecation warnings for {task}",
        ],
    )
    phase_1 = PlanPhase(
        phase_id=1,
        name="Design and Model Definition",
        steps=[step_1_1, step_1_2],
        gate=gate_1,
        approval_required=True,
        approval_description="Review the API schema and data model designs.",
        feedback_questions=[fq_1],
        risk_level="LOW",
    )

    # Phase 2 steps
    ka = KnowledgeAttachment(
        source="agent-declared",
        pack_name="python-web",
        document_name="fastapi-dependency-injection.md",
        path="/path/to/fastapi-dependency-injection.md",
        delivery="inline",
        retrieval="file",
        grounding="Apply these DI patterns throughout the implementation.",
        token_estimate=350,
    )
    step_2_1 = PlanStep(
        step_id="2.1",
        agent_name="backend-engineer--python",
        task_description="Implement the API routes using the new models.",
        model="sonnet",
        depends_on=["1.2"],
        deliverables=["agent_baton/api/routes/v2.py"],
        allowed_paths=["agent_baton/api/routes/"],
        blocked_paths=["agent_baton/models/"],
        context_files=["docs/api-schema.md", "agent_baton/api/CLAUDE.md"],
        knowledge=[ka],
        step_type="developing",
        expected_outcome="All API endpoints return correct status codes and response shapes.",
        timeout_seconds=7200,
        parallel_safe=True,
    )
    step_2_2 = PlanStep(
        step_id="2.2",
        agent_name="test-engineer",
        task_description="Write integration tests for the new API routes.",
        model="sonnet",
        depends_on=["1.2"],
        deliverables=["tests/api/test_v2_routes.py"],
        allowed_paths=["tests/api/"],
        blocked_paths=["agent_baton/"],
        context_files=["docs/api-schema.md"],
        step_type="testing",
        expected_outcome="Integration tests cover all endpoints with happy-path and error cases.",
        parallel_safe=True,
    )
    gate_2 = PlanGate(
        gate_type="test",
        command="pytest tests/api/ -v --tb=long",
        description="All API integration tests must pass.",
        fail_on=["test failures", "HTTP 500 responses"],
    )
    phase_2 = PlanPhase(
        phase_id=2,
        name="API Implementation and Testing",
        steps=[step_2_1, step_2_2],
        gate=gate_2,
        approval_required=False,
        approval_description="",
        feedback_questions=[],
        risk_level="MEDIUM",
    )

    rl = ResourceLimits(
        max_concurrent_executions=2,
        max_concurrent_agents=4,
        max_tokens_per_minute=0,
        max_concurrent_per_project=1,
    )
    fi = ForesightInsight(
        category="prerequisite",
        description="The new models will need migration support for existing database rows.",
        resolution="Add a migration step in phase 2 for existing baton.db files.",
        inserted_phase_name="",
        inserted_step_ids=["2.1"],
        confidence=0.9,
        source_rule="migration-prerequisite",
    )

    obj = MachinePlan(
        task_id="2026-01-15-add-api-v2-abc12345",
        task_summary="Add API v2 endpoints with new Pydantic models.",
        risk_level="MEDIUM",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[phase_1, phase_2],
        shared_context="Context: This project uses FastAPI + SQLAlchemy 2.0. All models are Pydantic v2.",
        pattern_source="pattern-api-feature-001",
        created_at="2026-01-15T09:00:00+00:00",
        task_type="feature",
        explicit_knowledge_packs=["python-web"],
        explicit_knowledge_docs=["docs/api-schema.md"],
        intervention_level="medium",
        complexity="medium",
        classification_source="haiku",
        resource_limits=rl,
        detected_stack="python/fastapi",
        foresight_insights=[fi],
        depends_on_task=None,
        classification_signals='{"keywords": ["api", "endpoint", "model"]}',
        archetype="phased",
        max_retry_phases=2,
    )
    _write("MachinePlan", obj.to_dict())


def gen_execution_state() -> None:
    """ExecutionState with step results, approval, amendment, and consolidation result."""
    # Build a representative plan
    step_1_1 = PlanStep(
        step_id="1.1",
        agent_name="architect",
        task_description="Design the new API schema.",
        model="opus",
        depends_on=[],
        deliverables=["docs/api-schema.md"],
        allowed_paths=["docs/"],
        blocked_paths=[],
        context_files=[],
        step_type="planning",
    )
    step_1_2 = PlanStep(
        step_id="1.2",
        agent_name="backend-engineer--python",
        task_description="Implement the new models.",
        model="sonnet",
        depends_on=["1.1"],
        deliverables=["agent_baton/models/new_models.py"],
        allowed_paths=["agent_baton/models/"],
        blocked_paths=[],
        context_files=["docs/api-schema.md"],
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
        steps=[step_1_1, step_1_2],
        gate=gate_1,
        approval_required=True,
        approval_description="Review the design before implementation.",
    )
    step_2_1 = PlanStep(
        step_id="2.1",
        agent_name="backend-engineer--python",
        task_description="Implement the API routes.",
        model="sonnet",
        depends_on=["1.2"],
        deliverables=["agent_baton/api/routes/v2.py"],
        allowed_paths=["agent_baton/api/routes/"],
        blocked_paths=[],
        context_files=["docs/api-schema.md"],
        step_type="developing",
    )
    phase_2 = PlanPhase(
        phase_id=2,
        name="Implementation",
        steps=[step_2_1],
        approval_required=False,
    )
    plan = MachinePlan(
        task_id="2026-01-15-exec-state-golden-abc",
        task_summary="Execution state golden fixture.",
        risk_level="MEDIUM",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[phase_1, phase_2],
        shared_context="Test execution state fixture.",
        created_at="2026-01-15T09:00:00+00:00",
        task_type="feature",
        archetype="phased",
    )

    # Step results (1 step result with interaction history)
    turn1 = InteractionTurn(
        role="agent",
        content="I have a question about the schema design approach.",
        timestamp="2026-01-15T09:15:00+00:00",
        turn_number=1,
        source="agent",
    )
    turn2 = InteractionTurn(
        role="human",
        content="Use the existing patterns from models/execution.py as reference.",
        timestamp="2026-01-15T09:17:00+00:00",
        turn_number=2,
        source="human",
    )
    sr1 = StepResult(
        step_id="1.1",
        agent_name="architect",
        status="complete",
        outcome="Designed the API schema with 5 endpoints and 3 data models.",
        files_changed=["docs/api-schema.md"],
        commit_hash="aabbccdd",
        estimated_tokens=8000,
        input_tokens=6500,
        cache_read_tokens=1500,
        cache_creation_tokens=200,
        output_tokens=1800,
        model_id="claude-opus-4-7",
        session_id="sess-design-001",
        step_started_at="2026-01-15T09:10:00+00:00",
        duration_seconds=420.0,
        retries=0,
        error="",
        completed_at="2026-01-15T09:17:00+00:00",
        member_results=[],
        deviations=[],
        interaction_history=[turn1, turn2],
        step_type="planning",
        updated_at="2026-01-15T09:17:00+00:00",
        outcome_spillover_path="",
    )

    # Approval result
    ar1 = ApprovalResult(
        phase_id=1,
        result="approve",
        feedback="Looks good, proceed to implementation.",
        decided_at="2026-01-15T10:30:00+00:00",
        decision_source="human",
        actor="eng@devbox.local",
        rationale="Schema covers all requirements from the spec.",
    )

    # Gate result
    gr1 = GateResult(
        phase_id=1,
        gate_type="test",
        passed=True,
        output="5 passed in 1.23s",
        checked_at="2026-01-15T10:20:00+00:00",
        command="pytest tests/models/ -v",
        exit_code=0,
        decision_source="human",
        actor="eng@devbox.local",
    )

    # Feedback result
    fr1 = FeedbackResult(
        phase_id=1,
        question_id="fq1",
        chosen_option="Keep pytest",
        chosen_index=0,
        dispatched_step_id="1.3",
        decided_at="2026-01-15T10:32:00+00:00",
    )

    # Amendment
    amend1 = PlanAmendment(
        amendment_id="amend-golden-001",
        trigger="approval_feedback",
        trigger_phase_id=1,
        description="Added a documentation step after phase 1 approval.",
        phases_added=[],
        steps_added=["1.3"],
        created_at="2026-01-15T10:35:00+00:00",
        feedback="Add inline docstrings before moving to phase 2.",
        metadata={"source": "approval", "reviewer": "eng@devbox.local"},
    )

    # Knowledge gap (uses the actual KnowledgeGapSignal fields)
    gap = KnowledgeGapSignal(
        description="Unclear how to handle backward compatibility for the new models.",
        confidence="low",
        gap_type="contextual",
        step_id="1.1",
        agent_name="architect",
        partial_outcome="Drafted schema up to the point of the compatibility question.",
    )

    # Resolved decision (uses the actual ResolvedDecision fields)
    rd = ResolvedDecision(
        gap_description="Unclear how to handle backward compatibility for the new models.",
        resolution="Use migration validators in from_dict to handle old field names.",
        step_id="1.2",
        timestamp="2026-01-15T10:00:00+00:00",
    )

    # Consolidation result
    fa = FileAttribution(
        file_path="docs/api-schema.md",
        step_id="1.1",
        agent_name="architect",
        insertions=55,
        deletions=0,
    )
    cr = ConsolidationResult(
        status="success",
        rebased_commits=[
            {
                "step_id": "1.1",
                "agent_name": "architect",
                "original_hash": "aabbccdd",
                "new_hash": "11223344",
            }
        ],
        final_head="11223344aabbccdd",
        base_commit="00000000ffffffff",
        files_changed=["docs/api-schema.md"],
        total_insertions=55,
        total_deletions=0,
        attributions=[fa],
        conflict_files=[],
        conflict_step_id="",
        skipped_steps=[],
        started_at="2026-01-15T10:40:00+00:00",
        completed_at="2026-01-15T10:41:00+00:00",
        error="",
    )

    obj = ExecutionState(
        task_id="2026-01-15-exec-state-golden-abc",
        plan=plan,
        current_phase=1,
        current_step_index=0,
        status="approval_pending",
        step_results=[sr1],
        gate_results=[gr1],
        approval_results=[ar1],
        feedback_results=[fr1],
        amendments=[amend1],
        started_at="2026-01-15T09:00:00+00:00",
        completed_at="",
        pending_gaps=[gap],
        resolved_decisions=[rd],
        delivered_knowledge={"fastapi-di.md::python-web": "1.1"},
        consolidation_result=cr,
        force_override=False,
        override_justification="",
        step_worktrees={"1.1": {"path": "/tmp/worktrees/wt-1.1", "branch": "wt/task/1.1"}},
        working_branch="feature/add-api-v2",
        working_branch_head="11223344aabbccdd",
        takeover_records=[],
        selfheal_attempts=[],
        speculations={},
        run_cumulative_spend_usd=0.042,
        pending_scope_expansions=[],
        scope_expansions_applied=0,
    )
    _write("ExecutionState", obj.to_dict())


if __name__ == "__main__":
    print("Generating golden JSON fixtures...")
    gen_interaction_turn()
    gen_synthesis_spec()
    gen_team_member()
    gen_plan_gate()
    gen_feedback_question()
    gen_plan_step()
    gen_plan_phase()
    gen_plan_amendment()
    gen_team_step_result()
    gen_step_result()
    gen_approval_result()
    gen_gate_result()
    gen_feedback_result()
    gen_file_attribution()
    gen_consolidation_result()
    gen_machine_plan()
    gen_execution_state()
    print(f"Done. {len(list(OUT_DIR.iterdir()))} fixtures written to {OUT_DIR}")
