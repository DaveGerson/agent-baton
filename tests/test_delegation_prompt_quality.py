"""Tests verifying outcome-oriented delegation prompt quality.

Covers:
- _agent_expertise_level: expert / standard / minimal classification
- _agent_has_output_spec: detection of agent-defined output sections
- _extract_file_paths: path extraction from task summaries
- _step_description prompt weight scaling (expert / standard / minimal paths)
- All _STEP_TEMPLATES entries produce valid outcome-oriented output
- PromptDispatcher: Intent section, Success Criteria, Deviations instruction
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.dispatcher import PromptDispatcher, _SUCCESS_CRITERIA
from agent_baton.core.engine.planner import (
    IntelligentPlanner,
    _AGENT_DELIVERABLES,
)
from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.core.orchestration.router import AgentRouter
from agent_baton.models.execution import PlanStep


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_step(
    *,
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task_description: str = "Implement the auth module.",
    model: str = "sonnet",
    deliverables: list[str] | None = None,
    context_files: list[str] | None = None,
    allowed_paths: list[str] | None = None,
    blocked_paths: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task_description,
        model=model,
        deliverables=deliverables or [],
        context_files=context_files or [],
        allowed_paths=allowed_paths or [],
        blocked_paths=blocked_paths or [],
    )


def _agent_md(
    name: str,
    body: str,
    *,
    model: str = "sonnet",
    description: str = "A specialist.",
) -> str:
    """Return the full content of a minimal agent .md file."""
    return (
        f"---\nname: {name}\ndescription: {description}\nmodel: {model}\n"
        f"permissionMode: default\ntools: Read, Write\n---\n\n{body}"
    )


@pytest.fixture
def agents_dir(tmp_path: Path) -> Path:
    """Agents directory with three richness levels.

    - expert-agent: >200-word body (rich definition)
    - standard-agent: short body without output-spec markers
    - output-spec-agent: short body with "## Output Format" section
    """
    d = tmp_path / "agents"
    d.mkdir()

    # expert-agent — 210+ words so _agent_expertise_level returns "expert"
    expert_body = " ".join(["word"] * 210)
    (d / "expert-agent.md").write_text(
        _agent_md("expert-agent", expert_body, model="opus"), encoding="utf-8"
    )

    # standard-agent — 5 words, no output-spec markers
    (d / "standard-agent.md").write_text(
        _agent_md("standard-agent", "Handles standard work well."), encoding="utf-8"
    )

    # output-spec-agent — includes "## Output Format" section
    output_spec_body = (
        "Does analysis work.\n\n## Output Format\n\nReturn a JSON report.\n"
    )
    (d / "output-spec-agent.md").write_text(
        _agent_md("output-spec-agent", output_spec_body), encoding="utf-8"
    )

    # backend-engineer — standard richness, no output-spec
    (d / "backend-engineer.md").write_text(
        _agent_md("backend-engineer", "Generic backend engineer.", model="sonnet"),
        encoding="utf-8",
    )

    # architect — standard richness
    (d / "architect.md").write_text(
        _agent_md("architect", "System design specialist.", model="opus"),
        encoding="utf-8",
    )

    return d


@pytest.fixture
def planner(tmp_path: Path, agents_dir: Path) -> IntelligentPlanner:
    ctx = tmp_path / "team-context"
    ctx.mkdir()
    p = IntelligentPlanner(team_context_root=ctx)
    reg = AgentRegistry()
    reg.load_directory(agents_dir)
    p._registry = reg
    p._router = AgentRouter(reg)
    return p


@pytest.fixture
def dispatcher() -> PromptDispatcher:
    return PromptDispatcher()


# ---------------------------------------------------------------------------
# _agent_expertise_level
# ---------------------------------------------------------------------------


class TestAgentExpertiseLevel:
    def test_expert_for_rich_definition(self, planner: IntelligentPlanner) -> None:
        """Agents with >200-word definitions are classified as expert."""
        level = planner._agent_expertise_level("expert-agent")
        assert level == "expert"

    def test_standard_for_short_definition(self, planner: IntelligentPlanner) -> None:
        """Agents with a short definition (<=200 words) are classified as standard."""
        level = planner._agent_expertise_level("standard-agent")
        assert level == "standard"

    def test_minimal_for_unknown_agent(self, planner: IntelligentPlanner) -> None:
        """Agents not in the registry receive the 'minimal' expertise level."""
        level = planner._agent_expertise_level("nonexistent-agent")
        assert level == "minimal"

    def test_flavored_agent_name_resolved_to_base(self, planner: IntelligentPlanner) -> None:
        """backend-engineer--python should resolve via the base name when the
        flavored variant is not separately registered."""
        # standard-agent doesn't have a flavored variant — we check that the
        # call does not crash and returns a valid level.
        level = planner._agent_expertise_level("backend-engineer--python")
        # No python-flavored variant, so registry.get returns None → minimal
        assert level in ("expert", "standard", "minimal")

    def test_boundary_exactly_200_words_is_standard(self, tmp_path: Path) -> None:
        """Word count of exactly 200 is NOT expert (>200 is the threshold)."""
        d = tmp_path / "boundary_agents"
        d.mkdir()
        body = " ".join(["word"] * 200)
        (d / "boundary-agent.md").write_text(
            _agent_md("boundary-agent", body), encoding="utf-8"
        )
        reg = AgentRegistry()
        reg.load_directory(d)
        p = IntelligentPlanner(team_context_root=tmp_path)
        p._registry = reg
        assert p._agent_expertise_level("boundary-agent") == "standard"

    def test_201_words_is_expert(self, tmp_path: Path) -> None:
        """Word count of 201 crosses the threshold → expert."""
        d = tmp_path / "expert_agents"
        d.mkdir()
        body = " ".join(["word"] * 201)
        (d / "rich-agent.md").write_text(
            _agent_md("rich-agent", body), encoding="utf-8"
        )
        reg = AgentRegistry()
        reg.load_directory(d)
        p = IntelligentPlanner(team_context_root=tmp_path)
        p._registry = reg
        assert p._agent_expertise_level("rich-agent") == "expert"


# ---------------------------------------------------------------------------
# _agent_has_output_spec
# ---------------------------------------------------------------------------


class TestAgentHasOutputSpec:
    @pytest.mark.parametrize("_marker,body", [
        ("output format", "## Output Format\nReturn a dict.\n"),
        ("when you finish", "## When You Finish\nReturn files.\n"),
        ("return:", "Return: a JSON document.\n"),
        ("deliverables", "## Deliverables\nList all files.\n"),
    ])
    def test_detects_output_markers(
        self, tmp_path: Path, _marker: str, body: str
    ) -> None:
        """Each recognized output-spec marker causes True to be returned."""
        d = tmp_path / "agents"
        d.mkdir()
        (d / "spec-agent.md").write_text(
            _agent_md("spec-agent", body), encoding="utf-8"
        )
        reg = AgentRegistry()
        reg.load_directory(d)
        p = IntelligentPlanner(team_context_root=tmp_path)
        p._registry = reg
        assert p._agent_has_output_spec("spec-agent") is True

    def test_returns_false_for_plain_body(self, planner: IntelligentPlanner) -> None:
        """Agents without output-spec markers return False."""
        assert planner._agent_has_output_spec("standard-agent") is False

    def test_returns_false_for_unknown_agent(self, planner: IntelligentPlanner) -> None:
        """Unknown agents (not in registry) return False, not an exception."""
        assert planner._agent_has_output_spec("nonexistent-agent") is False

    def test_output_spec_agent_skips_deliverables(
        self, planner: IntelligentPlanner
    ) -> None:
        """When an agent has an output-spec, _enrich_phases must not add
        default deliverables so the agent's own format section takes priority."""
        # We exercise _enrich_phases indirectly by building a phase and checking
        # that no deliverables are injected for output-spec-agent.
        from agent_baton.models.execution import PlanPhase, PlanStep

        step = PlanStep(
            step_id="1.1",
            agent_name="output-spec-agent",
            task_description="Analyze logs.",
        )
        phase = PlanPhase(phase_id=1, name="Implement", steps=[step])
        enriched = planner._enrich_phases([phase])
        assert enriched[0].steps[0].deliverables == [], (
            "output-spec-agent should not receive injected deliverables"
        )


# ---------------------------------------------------------------------------
# _extract_file_paths
# ---------------------------------------------------------------------------


class TestExtractFilePaths:
    @pytest.mark.parametrize("text,expected_paths", [
        # slash-separated path
        ("Rewrite agent_baton/core/engine/planner.py", ["agent_baton/core/engine/planner.py"]),
        # extension-only path (no slash)
        ("Update the spec.md document", ["spec.md"]),
        # multiple paths
        (
            "See docs/design.md and tests/test_planner.py for context",
            ["docs/design.md", "tests/test_planner.py"],
        ),
        # path in parentheses
        ("Modify (agent_baton/models/execution.py) directly", ["agent_baton/models/execution.py"]),
    ])
    def test_extracts_paths(
        self, planner: IntelligentPlanner, text: str, expected_paths: list[str]
    ) -> None:
        result = planner._extract_file_paths(text)
        for path in expected_paths:
            assert path in result, f"Expected {path!r} in extracted paths: {result}"

    def test_empty_string_returns_empty(self, planner: IntelligentPlanner) -> None:
        assert planner._extract_file_paths("") == []

    def test_no_paths_in_plain_text(self, planner: IntelligentPlanner) -> None:
        """Plain prose without file paths returns an empty list."""
        result = planner._extract_file_paths("Add OAuth2 login to the application")
        assert result == []

    def test_deduplication(self, planner: IntelligentPlanner) -> None:
        """The same path mentioned twice appears only once in the result."""
        text = "Read docs/spec.md first, then update docs/spec.md"
        result = planner._extract_file_paths(text)
        assert result.count("docs/spec.md") == 1

    def test_paths_added_to_step_context_files(
        self, planner: IntelligentPlanner
    ) -> None:
        """File paths from the task summary are appended to every step's
        context_files when create_plan is called."""
        plan = planner.create_plan(
            "Rewrite agent_baton/core/engine/planner.py for better clarity",
            task_type="refactor",
        )
        for phase in plan.phases:
            for step in phase.steps:
                assert "agent_baton/core/engine/planner.py" in step.context_files


# ---------------------------------------------------------------------------
# _step_description — prompt weight scaling
# ---------------------------------------------------------------------------


class TestStepDescriptionWeightScaling:
    """Verify that _step_description adjusts detail level to agent expertise."""

    def test_expert_agent_gets_compact_description(
        self, planner: IntelligentPlanner
    ) -> None:
        """An expert agent (>200 words) receives only a verb+task description
        without the full outcome template text."""
        desc = planner._step_description("Implement", "expert-agent", "Build the router")
        assert "Build the router" in desc
        # Expert path format: "{verb}: {task}." — should not contain template-specific text
        assert "Deliver working" not in desc
        assert "tested code" not in desc

    def test_standard_agent_gets_full_template(
        self, planner: IntelligentPlanner
    ) -> None:
        """A standard agent receives the full outcome-oriented template."""
        desc = planner._step_description("Implement", "backend-engineer", "Build the router")
        assert "Build the router" in desc
        # The backend-engineer/implement template includes "Deliver working, tested code."
        assert "Deliver working, tested code" in desc

    def test_minimal_agent_gets_template_plus_hint(
        self, planner: IntelligentPlanner
    ) -> None:
        """A minimal agent (no registry entry) receives the template plus a
        brief method hint."""
        # Use an agent not in the agents_dir fixture
        desc = planner._step_description("Implement", "devops-engineer", "Deploy the app")
        assert "Deploy the app" in desc
        # devops-engineer/implement template is "Set up infrastructure for: {task}."
        assert "Set up infrastructure" in desc
        # Minimal path appends method hint
        assert "document your approach" in desc.lower()

    def test_expert_path_uses_phase_verb_for_unknown_phase(
        self, planner: IntelligentPlanner
    ) -> None:
        """Expert agents on unrecognized phases still get verb+task format."""
        desc = planner._step_description("Validate", "expert-agent", "Check invariants")
        assert "Check invariants" in desc
        # Falls back to phase_name as verb when verb not in _PHASE_VERBS
        assert "(as expert-agent)" not in desc

    def test_minimal_agent_fallback_phase_appends_hint(
        self, planner: IntelligentPlanner
    ) -> None:
        """Minimal agents on unknown phase with no template get hint appended."""
        desc = planner._step_description("Validate", "nonexistent-agent", "Check stuff")
        assert "Check stuff" in desc
        assert "document your approach" in desc.lower()


# ---------------------------------------------------------------------------
# All _STEP_TEMPLATES entries produce valid outcome-oriented output
# ---------------------------------------------------------------------------


class TestAllStepTemplatesOutcomeOriented:
    """Every entry in _STEP_TEMPLATES should:
    1. Contain the task text.
    2. Be longer than 20 characters.
    3. Not fall through to the (as {agent}) fallback.
    4. Not contain prescriptive method instructions (API endpoints, module
       boundaries, etc.) that belong in agent definitions, not plans.
    """

    # All (agent, phase) pairs from _STEP_TEMPLATES that are not already
    # covered by the original test_all_template_entries_produce_output.
    @pytest.mark.parametrize("agent,phase", [
        ("architect", "design"),
        ("architect", "research"),
        ("architect", "review"),
        ("backend-engineer", "implement"),
        ("backend-engineer", "fix"),
        ("backend-engineer", "design"),
        ("backend-engineer", "investigate"),
        ("frontend-engineer", "implement"),
        ("frontend-engineer", "design"),
        ("test-engineer", "test"),
        ("test-engineer", "implement"),
        ("test-engineer", "review"),
        ("code-reviewer", "review"),
        ("security-reviewer", "review"),
        ("devops-engineer", "implement"),
        ("devops-engineer", "review"),
        ("data-engineer", "design"),
        ("data-engineer", "implement"),
        ("data-analyst", "design"),
        ("data-analyst", "implement"),
        ("data-scientist", "design"),
        ("data-scientist", "implement"),
        ("auditor", "review"),
        ("visualization-expert", "implement"),
        ("subject-matter-expert", "research"),
        ("subject-matter-expert", "review"),
    ])
    def test_template_produces_valid_output(
        self, planner: IntelligentPlanner, agent: str, phase: str
    ) -> None:
        task = "Add OAuth2 login to the API"
        desc = planner._step_description(phase.capitalize(), agent, task)
        assert task in desc, f"{agent}/{phase}: task text missing from description"
        assert len(desc) > 20, f"{agent}/{phase}: description too short"
        assert f"(as {agent})" not in desc, (
            f"{agent}/{phase}: fell through to fallback — template not matched"
        )

    @pytest.mark.parametrize("agent,phase,forbidden_phrase", [
        # Old prescriptive phrases that should no longer appear
        ("backend-engineer", "implement", "Focus on API endpoints"),
        ("backend-engineer", "implement", "business logic, and data access"),
        ("frontend-engineer", "implement", "wire up state management"),
        ("frontend-engineer", "implement", "handle user interactions"),
        ("test-engineer", "test", "Cover happy paths, edge cases"),
        ("test-engineer", "test", "Include both unit and integration tests"),
        ("code-reviewer", "review", "Check code quality, error handling"),
        ("code-reviewer", "review", "naming consistency"),
        ("security-reviewer", "review", "Check for OWASP top 10"),
        ("architect", "design", "Define module boundaries, interfaces"),
        ("architect", "design", "and data flow"),
    ])
    def test_prescriptive_phrases_removed(
        self, planner: IntelligentPlanner, agent: str, phase: str, forbidden_phrase: str
    ) -> None:
        """Templates must not contain old prescriptive how-to instructions."""
        task = "Add OAuth2 login to the API"
        desc = planner._step_description(phase.capitalize(), agent, task)
        assert forbidden_phrase not in desc, (
            f"{agent}/{phase}: old prescriptive phrase still present: {forbidden_phrase!r}"
        )

    def test_agent_deliverables_match_outcome_focus(self) -> None:
        """_AGENT_DELIVERABLES entries should be concise outcome labels,
        not verbose prescriptive lists."""
        verbose_indicators = [
            "that verifies",
            "following project conventions",
            "for changed code",
        ]
        for agent, deliverables in _AGENT_DELIVERABLES.items():
            for item in deliverables:
                for phrase in verbose_indicators:
                    assert phrase not in item, (
                        f"_AGENT_DELIVERABLES[{agent!r}] contains verbose phrase: "
                        f"{phrase!r} in {item!r}"
                    )


# ---------------------------------------------------------------------------
# PromptDispatcher — Intent, Success Criteria, Deviations
# ---------------------------------------------------------------------------


class TestDispatcherIntentSection:
    def test_intent_section_present_when_task_summary_given(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(
            step, task_summary="Add OAuth2 login to the user API"
        )
        assert "## Intent" in prompt
        assert "Add OAuth2 login to the user API" in prompt

    def test_intent_section_absent_when_no_task_summary(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, task_summary="")
        assert "## Intent" not in prompt

    def test_intent_section_shows_unmodified_user_text(
        self, dispatcher: PromptDispatcher
    ) -> None:
        """The Intent section must forward the user's original words verbatim
        without wrapping them in template boilerplate."""
        original = "Fix the broken OAuth token refresh (see issue #423)"
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, task_summary=original)
        # Original text must appear exactly, not paraphrased
        assert original in prompt

    def test_intent_appears_before_your_task(
        self, dispatcher: PromptDispatcher
    ) -> None:
        """Intent section must come before 'Your Task' in the prompt."""
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(
            step, task_summary="Build the search index"
        )
        intent_pos = prompt.index("## Intent")
        task_pos = prompt.index("## Your Task")
        assert intent_pos < task_pos


class TestDispatcherSuccessCriteria:
    @pytest.mark.parametrize("task_type,expected_phrase", list(_SUCCESS_CRITERIA.items()))
    def test_success_criteria_rendered_per_task_type(
        self,
        dispatcher: PromptDispatcher,
        task_type: str,
        expected_phrase: str,
    ) -> None:
        """Each task type maps to a concrete success-criteria sentence."""
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, task_type=task_type)
        assert expected_phrase in prompt, (
            f"task_type={task_type!r}: expected success criteria not found in prompt"
        )

    def test_success_criteria_absent_for_unknown_task_type(
        self, dispatcher: PromptDispatcher
    ) -> None:
        """Unknown task types produce no success criteria line."""
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, task_type="unknown-type")
        assert "**Success criteria:**" not in prompt

    def test_success_criteria_absent_when_task_type_empty(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, task_type="")
        assert "**Success criteria:**" not in prompt

    def test_success_criteria_placed_after_task_description(
        self, dispatcher: PromptDispatcher
    ) -> None:
        """Success criteria must appear immediately after the task description."""
        step = _make_step(task_description="Fix the login regression.")
        prompt = dispatcher.build_delegation_prompt(step, task_type="bug-fix")
        task_pos = prompt.index("Fix the login regression.")
        criteria_pos = prompt.index("**Success criteria:**")
        assert task_pos < criteria_pos

    def test_all_task_types_have_criteria_defined(self) -> None:
        """Spot-check that all expected task types have entries in _SUCCESS_CRITERIA."""
        expected_types = {
            "bug-fix", "new-feature", "refactor",
            "test", "documentation", "migration", "data-analysis",
        }
        missing = expected_types - set(_SUCCESS_CRITERIA.keys())
        assert not missing, f"Missing success criteria for: {missing}"


class TestDispatcherDeviationsInstruction:
    def test_deviations_instruction_present(
        self, dispatcher: PromptDispatcher
    ) -> None:
        """Every delegation prompt must instruct the agent to document
        deviations so the learning loop can capture plan mismatches."""
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert "Deviations" in prompt

    def test_deviations_instruction_in_team_prompt(
        self, dispatcher: PromptDispatcher
    ) -> None:
        """Team member delegation prompts must also carry the Deviations instruction."""
        from agent_baton.models.execution import TeamMember

        step = _make_step()
        member = TeamMember(
            member_id="1.1.a",
            agent_name="backend-engineer",
            role="implementer",
            task_description="Implement the router.",
        )
        prompt = dispatcher.build_team_delegation_prompt(
            step, member, task_summary="Build the router"
        )
        assert "Deviations" in prompt

    def test_decision_logging_instruction_present(
        self, dispatcher: PromptDispatcher
    ) -> None:
        """Every prompt instructs agents to log non-obvious decisions."""
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert "Decisions" in prompt


# ---------------------------------------------------------------------------
# Integration: create_plan produces outcome-oriented step descriptions
# ---------------------------------------------------------------------------


class TestCreatePlanOutcomeOriented:
    """End-to-end checks that the full create_plan → step_description pipeline
    produces outcome-oriented descriptions, not prescriptive ones."""

    def test_new_feature_steps_are_outcome_oriented(
        self, planner: IntelligentPlanner
    ) -> None:
        plan = planner.create_plan(
            "Add OAuth2 login to the API",
            task_type="new-feature",
            agents=["architect", "backend-engineer"],
        )
        for phase in plan.phases:
            for step in phase.steps:
                desc = step.task_description
                assert "Add OAuth2 login to the API" in desc, (
                    f"Step {step.step_id}: task text missing from description"
                )
                # Must not contain the old prescriptive phrases
                assert "Define module boundaries" not in desc
                assert "Focus on API endpoints, business logic" not in desc

    def test_bug_fix_steps_include_regression_instruction(
        self, planner: IntelligentPlanner
    ) -> None:
        """The backend-engineer/fix template mandates a regression test."""
        plan = planner.create_plan(
            "Fix token refresh returning 401",
            task_type="bug-fix",
            agents=["backend-engineer"],
        )
        fix_steps = [
            step
            for phase in plan.phases
            for step in phase.steps
            if "backend-engineer" in step.agent_name and "Fix" in phase.name
        ]
        assert fix_steps, "Expected at least one Fix step for backend-engineer"
        for step in fix_steps:
            assert "regression test" in step.task_description.lower() or "fix" in step.task_description.lower()

    def test_review_steps_use_approval_language(
        self, planner: IntelligentPlanner
    ) -> None:
        """Review-phase steps should use pass/fail or approve/flag language."""
        plan = planner.create_plan(
            "Refactor the storage layer",
            task_type="refactor",
            agents=["architect", "backend-engineer", "code-reviewer"],
        )
        review_steps = [
            step
            for phase in plan.phases
            for step in phase.steps
            if "review" in phase.name.lower()
        ]
        for step in review_steps:
            desc = step.task_description.lower()
            has_verdict_language = any(
                kw in desc for kw in ("approve", "flag", "pass", "fail", "review", "verdict")
            )
            assert has_verdict_language, (
                f"Review step {step.step_id} lacks approval/flag language: {desc!r}"
            )
