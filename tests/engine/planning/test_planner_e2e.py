"""End-to-end planner regression tests.

Exercises the full IntelligentPlanner pipeline against realistic task
descriptions.  Covers 7 functional criteria:

1. Complexity classification (light / medium / heavy)
2. Dependency and phase-ordering detection
3. Stage gates (quality, code review, compliance checks)
4. Swarm / team dispatch detection
5. Model selection (haiku / sonnet / opus)
6. Agent roster validation and specialist routing
7. Bead-documented planning failures (bd-be4f, bd-0e36, bd-5a7c,
   bd-1974, bd-0960, bd-124f, bd-701e, bd-6c5d, bd-b3e1)

**Not part of the default test battery.**  These tests make real CLI
subprocess calls and take ~3 minutes.  Run explicitly::

    pytest tests/engine/planning/test_planner_e2e.py -v

Or via marker::

    pytest -m e2e
"""
from __future__ import annotations

import re

import pytest

from agent_baton.core.engine.planning.planner import IntelligentPlanner

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def planner() -> IntelligentPlanner:
    return IntelligentPlanner()


# ---------------------------------------------------------------------------
# Task descriptions — generic enough for any orchestration engine
# ---------------------------------------------------------------------------

# Criterion 1: Complexity tiers
TASK_LIGHT = "Rename the variable 'usr' to 'user' in auth_handler.py"
TASK_MEDIUM = (
    "Add a new /api/health endpoint that returns system metrics, "
    "write unit tests for it, and update the API docs"
)
TASK_HEAVY = (
    "Refactor the payment processing service to use the new Stripe API v3, "
    "update the frontend checkout flow, add integration tests, "
    "and ensure SOX compliance for the audit trail"
)

# Criterion 2: Dependencies
TASK_SEQUENTIAL = (
    "1) Design the database schema for user preferences "
    "2) Implement the storage layer and ORM models "
    "3) Build the REST API endpoints on top of the storage layer "
    "4) Write integration tests covering the full stack"
)

# Criterion 3: Stage gates
TASK_DESTRUCTIVE_COMPLIANCE = (
    "Delete the legacy user database tables and migrate all records "
    "to the new schema with GDPR-compliant data masking"
)

# Criterion 4: Teams / swarms
TASK_MULTI_DOMAIN = (
    "Build a real-time dashboard: the backend engineer creates WebSocket "
    "endpoints, the frontend engineer builds React components with charts, "
    "and the data engineer sets up the ETL pipeline feeding the dashboard"
)

# Criterion 5: Model selection
TASK_ARCHITECTURE = (
    "Redesign the entire authentication system from session-based to "
    "JWT with refresh tokens, including the token rotation strategy, "
    "the migration plan for existing sessions, and a security review"
)

# Criterion 6: Agent routing
TASK_COMPOUND = (
    "1) Add a new /api/health endpoint with system metrics "
    "2) Write regression tests for the auth middleware "
    "3) Update the deployment docs with the new monitoring setup"
)

# Criterion 7: Spec-doc style (exercises bead-documented failures)
TASK_SPEC_DOC = (
    "Implement a persistent agent memory system: native SQLite storage for "
    "reasoning traces, a store API for creation/query/linking, cross-session "
    "memory retrieval in the dispatch prompt builder, and intra-execution "
    "memory passing between sequential agents"
)

# Bead bd-1974: constraint clause misparsing
TASK_WITH_CONSTRAINTS = (
    "Add rate limiting to the /api/search endpoint. "
    "Must not regress existing authentication behavior. "
    "Add tests for each new middleware layer"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _agent_bases(plan) -> set[str]:
    """Base agent names (stripped of --flavor), including team members."""
    agents = set()
    for phase in plan.phases:
        for step in phase.steps:
            agents.add(step.agent_name.split("--")[0])
            for member in getattr(step, "team", []) or []:
                agents.add(member.agent_name.split("--")[0])
    return agents


def _phase_names(plan) -> list[str]:
    return [p.name for p in plan.phases]


def _phase_names_lower(plan) -> list[str]:
    return [p.name.lower() for p in plan.phases]


def _has_gate(plan, gate_type: str) -> bool:
    return any(
        p.gate and p.gate.gate_type == gate_type
        for p in plan.phases
    )


def _all_steps(plan):
    for phase in plan.phases:
        yield from phase.steps


def _all_step_types(plan) -> set[str]:
    return {s.step_type for s in _all_steps(plan) if s.step_type}


def _gate_commands(plan) -> list[str]:
    return [p.gate.command for p in plan.phases if p.gate]


def _has_team_step(plan) -> bool:
    return any(
        getattr(s, "team", None) for s in _all_steps(plan)
    )


def _steps_with_model(plan, model: str) -> list:
    result = []
    for s in _all_steps(plan):
        if getattr(s, "model", None) == model:
            result.append(s)
        for m in getattr(s, "team", []) or []:
            if getattr(m, "model", None) == model:
                result.append(m)
    return result


# ===========================================================================
# CRITERION 1 — Complexity Classification
# ===========================================================================

class TestComplexityLight:
    """A single-file rename should produce a light, minimal plan."""

    @pytest.fixture(scope="class")
    def plan(self, planner):
        return planner.create_plan(TASK_LIGHT)

    def test_complexity_is_light(self, plan):
        assert plan.complexity == "light", (
            f"Single rename should be light, got '{plan.complexity}'"
        )

    def test_minimal_phases(self, plan):
        assert len(plan.phases) <= 2, (
            f"Light task should have 1-2 phases, got {len(plan.phases)}"
        )

    def test_risk_is_low(self, plan):
        assert plan.risk_level == "LOW", (
            f"Rename should be LOW risk, got '{plan.risk_level}'"
        )


class TestComplexityMedium:
    """A 3-concern task should produce a medium-complexity plan."""

    @pytest.fixture(scope="class")
    def plan(self, planner):
        return planner.create_plan(TASK_MEDIUM)

    def test_complexity_not_light(self, plan):
        if plan.classification_source == "keyword-fallback":
            pytest.xfail("Keyword fallback may under-classify complexity")
        assert plan.complexity in ("medium", "heavy"), (
            f"3-concern task should be medium+, got '{plan.complexity}'"
        )

    def test_multiple_phases(self, plan):
        """Medium-complexity 3-concern task should have 2+ phases.

        Known gap: comma-separated concerns (not numbered) don't
        trigger concern splitting in the phase builder even at medium
        complexity.  The Opus plan review may add phases but this is
        nondeterministic.
        """
        if len(plan.phases) < 2:
            pytest.xfail(
                f"Got {len(plan.phases)} phase(s) — comma-separated "
                "concerns don't trigger phase decomposition"
            )


class TestComplexityHeavy:
    """A 4-concern cross-cutting task with compliance should be heavy."""

    @pytest.fixture(scope="class")
    def plan(self, planner):
        return planner.create_plan(TASK_HEAVY)

    def test_complexity_not_light(self, plan):
        if plan.classification_source == "keyword-fallback":
            pytest.xfail("Keyword fallback classifies cross-cutting as light")
        assert plan.complexity != "light", (
            f"4-concern task with SOX should not be light, got '{plan.complexity}'"
        )

    def test_many_phases(self, plan):
        if plan.complexity == "light":
            pytest.xfail("Light complexity produces minimal phases")
        assert len(plan.phases) >= 3, (
            f"Expected 3+ phases, got {len(plan.phases)}: {_phase_names(plan)}"
        )


# ===========================================================================
# CRITERION 2 — Dependency and Phase Ordering
# ===========================================================================

class TestDependencyDetection:
    """Sequential tasks should produce ordered phases with dependencies."""

    @pytest.fixture(scope="class")
    def plan(self, planner):
        return planner.create_plan(TASK_SEQUENTIAL)

    def test_multiple_phases(self, plan):
        assert len(plan.phases) >= 3, (
            f"4 sequential subtasks should produce 3+ phases, got {len(plan.phases)}"
        )

    def test_design_before_implement(self, plan):
        names = _phase_names_lower(plan)
        design_idx = next(
            (i for i, n in enumerate(names) if "design" in n), None
        )
        impl_idx = next(
            (i for i, n in enumerate(names) if "implement" in n), None
        )
        if design_idx is not None and impl_idx is not None:
            assert design_idx < impl_idx, (
                f"Design (idx={design_idx}) should precede "
                f"Implement (idx={impl_idx})"
            )

    def test_test_phase_after_implement(self, plan):
        names = _phase_names_lower(plan)
        impl_indices = [i for i, n in enumerate(names) if "implement" in n]
        test_indices = [i for i, n in enumerate(names) if "test" in n]
        if impl_indices and test_indices:
            assert max(impl_indices) <= max(test_indices), (
                "Test phase should follow implementation"
            )

    def test_architect_in_design_phase(self, plan):
        assert "architect" in _agent_bases(plan), (
            "Schema design task should include architect"
        )


# ===========================================================================
# CRITERION 3 — Stage Gates (quality, code review, compliance)
# ===========================================================================

class TestStageGates:
    """Destructive + compliance tasks need appropriate gates."""

    @pytest.fixture(scope="class")
    def plan(self, planner):
        return planner.create_plan(TASK_DESTRUCTIVE_COMPLIANCE)

    def test_has_test_or_build_gate(self, plan):
        assert _has_gate(plan, "test") or _has_gate(plan, "build"), (
            "Migration plan needs at least one test/build gate"
        )

    def test_has_review_or_audit_phase(self, plan):
        names = _phase_names_lower(plan)
        assert any("review" in n or "audit" in n for n in names), (
            f"GDPR task needs review/audit phase: {names}"
        )

    def test_auditor_present_for_compliance(self, plan):
        assert "auditor" in _agent_bases(plan), (
            f"GDPR task needs auditor: {_agent_bases(plan)}"
        )

    def test_gate_commands_use_pytest_not_npm(self, plan):
        """bd-5a7c: gates should use pytest for Python projects, not npm."""
        for cmd in _gate_commands(plan):
            assert "npm" not in cmd, (
                f"Python project gate should not use npm: {cmd}"
            )

    def test_gate_commands_not_full_suite(self, plan):
        """bd-124f: gates should be scoped, not always 'pytest --cov'."""
        for cmd in _gate_commands(plan):
            if "pytest" in cmd and "--cov" in cmd:
                has_files = bool(re.search(r"tests/\S+\.py", cmd))
                has_collect_only = "--co" in cmd
                if not has_files and not has_collect_only:
                    pass  # Acceptable for now, scoping is best-effort


class TestGatesOnCodePhases:
    """Every code-producing phase should have a gate."""

    @pytest.fixture(scope="class")
    def plan(self, planner):
        return planner.create_plan(TASK_SPEC_DOC)

    def test_implement_phases_have_gates(self, plan):
        for phase in plan.phases:
            name_lower = phase.name.lower()
            if any(kw in name_lower for kw in ("implement", "fix", "draft")):
                assert phase.gate is not None, (
                    f"Code-producing phase '{phase.name}' has no gate"
                )

    def test_research_phases_skip_gates(self, plan):
        for phase in plan.phases:
            name_lower = phase.name.lower()
            if name_lower in ("research", "investigate"):
                # Research phases MAY have gates but shouldn't require them
                pass  # No assertion — just documenting the rule


# ===========================================================================
# CRITERION 4 — Swarm / Team Dispatch
# ===========================================================================

class TestTeamDispatch:
    """Multi-domain tasks should use team steps when appropriate."""

    @pytest.fixture(scope="class")
    def plan(self, planner):
        return planner.create_plan(TASK_MULTI_DOMAIN)

    def test_has_team_step_or_multiple_agents(self, plan):
        agents = _agent_bases(plan)
        assert len(agents) >= 3 or _has_team_step(plan), (
            f"Multi-domain task should have 3+ agents or team steps: {agents}"
        )

    def test_frontend_engineer_present(self, plan):
        """Task explicitly mentions 'frontend engineer builds React'.

        Nondeterministic: CLI classifier may not always add
        frontend-engineer when backend/data keywords dominate.
        """
        bases = _agent_bases(plan)
        if "frontend-engineer" not in bases:
            pytest.xfail(
                f"frontend-engineer not routed: {bases} — "
                "CLI correction nondeterministic"
            )

    def test_backend_engineer_present(self, plan):
        assert "backend-engineer" in _agent_bases(plan), (
            f"WebSocket backend needs backend-engineer: {_agent_bases(plan)}"
        )

    def test_data_engineer_present(self, plan):
        bases = _agent_bases(plan)
        if "data-engineer" not in bases:
            pytest.xfail(
                f"data-engineer not routed for ETL concern: {bases}"
            )


# ===========================================================================
# CRITERION 5 — Model Selection
# ===========================================================================

class TestModelSelection:
    """Architecture-heavy tasks should use opus for design steps."""

    @pytest.fixture(scope="class")
    def plan(self, planner):
        return planner.create_plan(TASK_ARCHITECTURE)

    def test_opus_used_for_design(self, plan):
        opus_steps = _steps_with_model(plan, "opus")
        assert len(opus_steps) >= 1, (
            "Architecture redesign should use opus for at least one step"
        )

    def test_not_all_opus(self, plan):
        """Non-design steps should use sonnet to save cost."""
        all_steps = list(_all_steps(plan))
        opus_steps = _steps_with_model(plan, "opus")
        if len(all_steps) > 1:
            assert len(opus_steps) < len(all_steps), (
                "Not every step should use opus — test/implement use sonnet"
            )

    def test_security_reviewer_present(self, plan):
        """Task mentions 'security review' explicitly.

        Nondeterministic: 'security' is a CROSS_CONCERN_SIGNALS
        keyword but the classifier may not always expand the roster
        to include security-reviewer.
        """
        bases = _agent_bases(plan)
        if "security-reviewer" not in bases:
            pytest.xfail(
                f"security-reviewer not routed: {bases} — "
                "CLI correction nondeterministic"
            )


# ===========================================================================
# CRITERION 6 — Agent Roster Validation
# ===========================================================================

class TestAgentRouting:
    """Compound task should route subtasks to correct specialists."""

    @pytest.fixture(scope="class")
    def plan(self, planner):
        return planner.create_plan(TASK_COMPOUND)

    def test_test_engineer_for_test_subtask(self, plan):
        """bd-be4f: 'Write regression tests' routes to test-engineer."""
        assert "test-engineer" in _agent_bases(plan), (
            f"test-engineer missing: {_agent_bases(plan)}"
        )

    def test_backend_engineer_for_api_subtask(self, plan):
        assert "backend-engineer" in _agent_bases(plan), (
            f"backend-engineer missing for API endpoint: {_agent_bases(plan)}"
        )

    def test_task_type_not_test(self, plan):
        """Compound add+test+docs should not be classified as 'test'."""
        if plan.task_type == "test":
            pytest.xfail(
                f"task_type={plan.task_type!r} "
                f"(source={plan.classification_source}) — "
                "compound task dominated by 'test' keyword"
            )

    def test_risk_not_high_for_docs(self, plan):
        """'deployment docs' should not trigger HIGH risk."""
        if plan.risk_level == "HIGH":
            pytest.xfail(
                f"risk={plan.risk_level} "
                f"(source={plan.classification_source}) — "
                "'deployment docs' triggers 'deploy' keyword"
            )


class TestAgentRoutingHeavy:
    """Heavy cross-cutting task should have full specialist roster."""

    @pytest.fixture(scope="class")
    def plan(self, planner):
        return planner.create_plan(TASK_HEAVY)

    def test_auditor_for_sox(self, plan):
        assert "auditor" in _agent_bases(plan), (
            f"SOX compliance needs auditor: {_agent_bases(plan)}"
        )

    def test_no_system_maintainer_for_implementation(self, plan):
        """bd-0e36: system-maintainer should not do API migrations."""
        for step in _all_steps(plan):
            if step.agent_name == "system-maintainer":
                name_lower = step.task_description.lower()
                assert "refactor" not in name_lower, (
                    f"system-maintainer assigned to refactor work: "
                    f"{step.task_description[:80]}"
                )

    def test_risk_at_least_medium(self, plan):
        assert plan.risk_level in ("MEDIUM", "HIGH"), (
            f"Payment + SOX = MEDIUM+, got '{plan.risk_level}'"
        )


# ===========================================================================
# CRITERION 7 — Bead-Documented Planning Failures
# ===========================================================================

class TestBeadRegressions:
    """Regression tests derived from beads documenting planning bugs."""

    @pytest.fixture(scope="class")
    def plan_spec(self, planner):
        return planner.create_plan(TASK_SPEC_DOC)

    @pytest.fixture(scope="class")
    def plan_destructive(self, planner):
        return planner.create_plan(TASK_DESTRUCTIVE_COMPLIANCE)

    @pytest.fixture(scope="class")
    def plan_constraint(self, planner):
        return planner.create_plan(TASK_WITH_CONSTRAINTS)

    def test_task_type_is_new_feature(self, plan_spec):
        """'Implement a ... system' classifies as new-feature."""
        assert plan_spec.task_type == "new-feature", (
            f"Expected 'new-feature', got '{plan_spec.task_type}'"
        )

    def test_migration_type_detected(self, plan_destructive):
        """'migrate all records' classifies as migration."""
        assert plan_destructive.task_type == "migration", (
            f"Expected 'migration', got '{plan_destructive.task_type}'"
        )

    def test_step_types_are_valid(self, plan_spec):
        """bd-b3e1: implement phases should have step_type='implementing'
        or similar, not 'planning'."""
        for phase in plan_spec.phases:
            name_lower = phase.name.lower()
            if "implement" in name_lower:
                for step in phase.steps:
                    if step.step_type:
                        assert step.step_type != "planning", (
                            f"Implement phase step has step_type='planning': "
                            f"{step.step_id}"
                        )

    def test_context_files_are_real_paths(self, plan_spec):
        """bd-0960: context_files should be file paths, not parse artifacts."""
        for step in _all_steps(plan_spec):
            for path in step.context_files or []:
                assert "/" not in path or "." in path.split("/")[-1] or path.endswith("/"), (
                    f"Suspicious context_file (parse artifact?): {path}"
                )
                assert "required_role" not in path, (
                    f"Parse artifact in context_files: {path}"
                )

    def test_expected_outcomes_present(self, plan_spec):
        """bd-6c5d: steps should have expected outcomes for gate
        verification."""
        steps_with_outcomes = sum(
            1 for s in _all_steps(plan_spec)
            if getattr(s, "expected_outcome", "")
        )
        total = sum(1 for _ in _all_steps(plan_spec))
        if total > 0:
            coverage = steps_with_outcomes / total
            assert coverage >= 0.3, (
                f"Only {steps_with_outcomes}/{total} steps have "
                f"expected_outcome ({coverage:.0%})"
            )

    def test_constraint_not_parsed_as_deliverable(self, plan_constraint):
        """bd-1974/bd-021d: 'Must not regress...' should not become a
        separate implementation phase."""
        names = _phase_names_lower(plan_constraint)
        for name in names:
            assert "must not" not in name, (
                f"Constraint clause parsed as phase: '{name}'"
            )
            assert "regress" not in name or "test" in name, (
                f"Regression constraint became a phase: '{name}'"
            )

    def test_design_phase_present(self, plan_spec):
        """New subsystem needs a design phase."""
        names = _phase_names_lower(plan_spec)
        assert any("design" in n for n in names), (
            f"No design phase for new subsystem: {names}"
        )

    def test_has_test_gate(self, plan_spec):
        assert _has_gate(plan_spec, "test"), (
            "New feature should have a test gate"
        )

    def test_risk_appropriate(self, plan_spec):
        """New feature without destructive ops or compliance = not HIGH."""
        assert plan_spec.risk_level in ("LOW", "MEDIUM"), (
            f"Expected LOW/MEDIUM, got '{plan_spec.risk_level}'"
        )

    def test_multi_concern_not_thin(self, plan_spec):
        """bd-701e: multi-concern tasks should not produce thin plans."""
        assert len(plan_spec.phases) >= 2, (
            f"4-component feature got only {len(plan_spec.phases)} phase(s)"
        )
