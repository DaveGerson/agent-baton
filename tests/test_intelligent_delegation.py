"""Tests for Intelligent Delegation improvements (2026-03-24 spec).

Covers all four tiers:
  Tier 1 — Outcome-oriented _STEP_TEMPLATES and simplified _AGENT_DELIVERABLES
  Tier 2 — Agent-aware planning: expertise level, output spec detection,
            model inheritance, deliverables deduplication
  Tier 3 — Context richness: _extract_file_paths, Intent section,
            Success Criteria section, auto-extracted context files
  Tier 4 — Pushback protocol: _extract_deviations, StepResult.deviations
            serialization, Deviations section in delegation prompt,
            retrospective integration
"""
from __future__ import annotations

from pathlib import Path
import pytest

from agent_baton.core.engine.dispatcher import PromptDispatcher, _SUCCESS_CRITERIA
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.planner import (
    IntelligentPlanner,
    _AGENT_DELIVERABLES,
    _STEP_TEMPLATES,
)
from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.core.orchestration.router import AgentRouter
from agent_baton.models.execution import (
    MachinePlan,
    PlanPhase,
    PlanStep,
    StepResult,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_agent_file(
    agents_dir: Path,
    name: str,
    body: str,
    model: str = "sonnet",
) -> None:
    """Write a minimal agent .md file to *agents_dir*."""
    content = (
        f"---\nname: {name}\ndescription: A test agent.\n"
        f"model: {model}\npermissionMode: default\ntools: Read, Write\n---\n\n"
        f"{body}"
    )
    (agents_dir / f"{name}.md").write_text(content, encoding="utf-8")


def _rich_instructions(word_count: int) -> str:
    """Return a string of `word_count` filler words."""
    return " ".join(["word"] * word_count)


@pytest.fixture
def agents_dir(tmp_path: Path) -> Path:
    """Agents directory with a mix of short- and long-body definitions."""
    d = tmp_path / "agents"
    d.mkdir()

    # Short — ~10 words → "standard"
    _make_agent_file(d, "short-agent", "This agent does basic things.", model="sonnet")

    # Long — 250 words → "expert"
    _make_agent_file(d, "long-agent", _rich_instructions(250), model="opus")

    # Has output spec markers
    _make_agent_file(
        d,
        "output-spec-agent",
        "## When you finish\nReturn a JSON object with results.",
    )

    # Has deliverables marker
    _make_agent_file(
        d,
        "deliverables-agent",
        "## Deliverables\nProvide the completed implementation.",
    )

    # Standard agents used by planner
    for name, model in [
        ("architect", "opus"),
        ("backend-engineer", "sonnet"),
        ("test-engineer", "sonnet"),
        ("code-reviewer", "opus"),
        ("auditor", "opus"),
    ]:
        _make_agent_file(d, name, "A specialist agent.", model=model)

    return d


@pytest.fixture
def planner(tmp_path: Path, agents_dir: Path) -> IntelligentPlanner:
    """IntelligentPlanner wired to our controlled agents directory."""
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


def _make_step(
    *,
    step_id: str = "1.1",
    agent_name: str = "backend-engineer",
    task_description: str = "Implement the feature.",
    model: str = "sonnet",
    deliverables: list[str] | None = None,
    allowed_paths: list[str] | None = None,
    blocked_paths: list[str] | None = None,
    context_files: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task_description,
        model=model,
        deliverables=deliverables or [],
        allowed_paths=allowed_paths or [],
        blocked_paths=blocked_paths or [],
        context_files=context_files or [],
    )


# ---------------------------------------------------------------------------
# Tier 1 — Outcome-oriented templates
# ---------------------------------------------------------------------------


class TestStepTemplatesOutcomeOriented:
    """Verify _STEP_TEMPLATES entries describe outcomes, not methods."""

    # Phrases that prescribe methods — must NOT appear in any template
    _METHOD_PRESCRIPTIVE_PHRASES = [
        "define module boundaries",
        "define endpoints",
        "focus on api endpoints",
        "focus on api",
        "business logic, and data access",
        "wire up state management",
        "handle user interactions",
        "ensure accessibility",
        "responsive behavior",
        "check for owasp",
        "top 10 vulnerabilities",
        "input validation issues",
        "configure deployment",
        "configure ci/cd",
        "configure docker",
        "define schemas, migrations, indexes",
        "define metrics, data sources",
        "define features, model selection",
        "feature engineering, model training",
    ]

    def test_no_method_prescriptive_phrases_in_any_template(self) -> None:
        """Every entry in _STEP_TEMPLATES must be free of method-prescriptive text."""
        all_templates = [
            template
            for agent_templates in _STEP_TEMPLATES.values()
            for template in agent_templates.values()
        ]
        violations: list[str] = []
        for template in all_templates:
            lower = template.lower()
            for phrase in self._METHOD_PRESCRIPTIVE_PHRASES:
                if phrase in lower:
                    violations.append(f"Template '{template[:60]}...' contains '{phrase}'")
        assert not violations, "Found method-prescriptive phrases:\n" + "\n".join(violations)

    @pytest.mark.parametrize("agent,phase,expected_fragment", [
        ("architect", "design", "Produce a design for"),
        ("architect", "research", "Assess feasibility"),
        ("architect", "review", "architectural fitness"),
        ("backend-engineer", "implement", "Deliver working, tested code"),
        ("backend-engineer", "fix", "Include a regression test"),
        ("backend-engineer", "design", "Design the backend approach for"),
        ("backend-engineer", "investigate", "Document root cause"),
        ("frontend-engineer", "implement", "Deliver working, accessible components"),
        ("test-engineer", "test", "Deliver tests that would catch regressions"),
        ("test-engineer", "review", "Flag gaps"),
        ("code-reviewer", "review", "Approve or flag issues blocking merge"),
        ("security-reviewer", "review", "Flag vulnerabilities"),
        ("auditor", "review", "pass/fail"),
        ("subject-matter-expert", "research", "Provide domain context"),
        ("subject-matter-expert", "review", "Validate domain correctness"),
    ])
    def test_template_contains_expected_fragment(
        self, agent: str, phase: str, expected_fragment: str
    ) -> None:
        template = _STEP_TEMPLATES[agent][phase]
        assert expected_fragment.lower() in template.lower(), (
            f"Template for {agent}/{phase} missing '{expected_fragment}'.\n"
            f"Got: {template}"
        )

    def test_all_template_entries_have_task_placeholder(self) -> None:
        """Every template must contain {task} for substitution."""
        for agent, phases in _STEP_TEMPLATES.items():
            for phase, template in phases.items():
                assert "{task}" in template, (
                    f"Template for {agent}/{phase} is missing {{task}} placeholder."
                )


class TestAgentDeliverablesSimplified:
    """Verify _AGENT_DELIVERABLES are single concise strings, not verbose lists."""

    @pytest.mark.parametrize("agent,expected", [
        ("architect", "Design document"),
        ("backend-engineer", "Working implementation with tests"),
        ("frontend-engineer", "Working UI components with tests"),
        ("test-engineer", "Test suite"),
        ("code-reviewer", "Review verdict with findings"),
        ("security-reviewer", "Security audit report"),
        ("devops-engineer", "Infrastructure configuration"),
        ("data-engineer", "Schema and migrations"),
        ("data-analyst", "Analysis results"),
        ("data-scientist", "Model with evaluation results"),
        ("auditor", "Audit verdict"),
        ("visualization-expert", "Visualizations"),
        ("subject-matter-expert", "Domain context document"),
    ])
    def test_deliverable_value(self, agent: str, expected: str) -> None:
        assert _AGENT_DELIVERABLES[agent] == [expected], (
            f"Expected {agent!r} deliverable to be [{expected!r}], "
            f"got {_AGENT_DELIVERABLES[agent]}"
        )

    def test_no_deliverable_entry_is_multi_item_list(self) -> None:
        """No agent should have more than one default deliverable entry."""
        for agent, deliverables in _AGENT_DELIVERABLES.items():
            assert len(deliverables) == 1, (
                f"{agent} has {len(deliverables)} deliverables; expected exactly 1."
            )

    def test_no_legacy_verbose_phrases(self) -> None:
        """Old verbose phrases from the pre-redesign spec must not appear."""
        legacy_phrases = [
            "implementation source files",
            "tests for changed code",
            "ui component files",
            "test files with comprehensive coverage",
            "review summary with findings and approval status",
            "security audit report with findings and risk ratings",
            "infrastructure and deployment configuration files",
            "schema definitions and migration files",
            "analysis results and supporting queries",
            "model artifacts and evaluation results",
            "audit report with compliance findings",
            "visualization files or dashboard components",
            "domain requirements document",
            "design document with module boundaries and interfaces",
        ]
        all_values = [
            d.lower()
            for delivs in _AGENT_DELIVERABLES.values()
            for d in delivs
        ]
        for phrase in legacy_phrases:
            for value in all_values:
                assert phrase not in value, (
                    f"Legacy phrase '{phrase}' found in deliverables: '{value}'"
                )


# ---------------------------------------------------------------------------
# Tier 2 — Agent-aware planning
# ---------------------------------------------------------------------------


class TestAgentExpertiseLevel:
    """Tests for IntelligentPlanner._agent_expertise_level()."""

    def test_expert_for_agent_with_many_words(
        self, planner: IntelligentPlanner
    ) -> None:
        # long-agent has 250-word instructions
        assert planner._agent_expertise_level("long-agent") == "expert"

    def test_standard_for_agent_with_few_words(
        self, planner: IntelligentPlanner
    ) -> None:
        # short-agent has ~5 words
        assert planner._agent_expertise_level("short-agent") == "standard"

    def test_minimal_for_unknown_agent(self, planner: IntelligentPlanner) -> None:
        assert planner._agent_expertise_level("nonexistent-agent") == "minimal"

    def test_boundary_at_201_words_is_expert(
        self, planner: IntelligentPlanner, tmp_path: Path, agents_dir: Path
    ) -> None:
        """201 words is above the 200-word threshold → expert."""
        _make_agent_file(agents_dir, "boundary-agent", _rich_instructions(201))
        planner._registry.load_directory(agents_dir)
        assert planner._agent_expertise_level("boundary-agent") == "expert"

    def test_boundary_at_200_words_is_standard(
        self, planner: IntelligentPlanner, tmp_path: Path, agents_dir: Path
    ) -> None:
        """Exactly 200 words is NOT above threshold → standard."""
        _make_agent_file(agents_dir, "at-boundary-agent", _rich_instructions(200))
        planner._registry.load_directory(agents_dir)
        assert planner._agent_expertise_level("at-boundary-agent") == "standard"

    def test_expertise_independent_of_model_field(
        self, planner: IntelligentPlanner
    ) -> None:
        """Model preference should not affect expertise classification."""
        # long-agent has model=opus; expertise is derived from word count alone
        assert planner._agent_expertise_level("long-agent") == "expert"


class TestAgentHasOutputSpec:
    """Tests for IntelligentPlanner._agent_has_output_spec()."""

    def test_returns_true_for_when_you_finish_marker(
        self, planner: IntelligentPlanner
    ) -> None:
        assert planner._agent_has_output_spec("output-spec-agent") is True

    def test_returns_true_for_deliverables_marker(
        self, planner: IntelligentPlanner
    ) -> None:
        assert planner._agent_has_output_spec("deliverables-agent") is True

    def test_returns_false_for_plain_agent(
        self, planner: IntelligentPlanner
    ) -> None:
        assert planner._agent_has_output_spec("short-agent") is False

    def test_returns_false_for_unknown_agent(
        self, planner: IntelligentPlanner
    ) -> None:
        assert planner._agent_has_output_spec("no-such-agent") is False

    @pytest.mark.parametrize("marker", [
        "output format",
        "when you finish",
        "return:",
        "deliverables",
    ])
    def test_each_marker_triggers_true(
        self, planner: IntelligentPlanner, agents_dir: Path, marker: str
    ) -> None:
        agent_name = f"marker-{marker.replace(' ', '-').replace(':', '')}"
        _make_agent_file(
            agents_dir,
            agent_name,
            f"This agent provides results.\n\n## {marker.title()}\nSome content here.",
        )
        planner._registry.load_directory(agents_dir)
        assert planner._agent_has_output_spec(agent_name) is True

    def test_case_insensitive_marker_detection(
        self, planner: IntelligentPlanner, agents_dir: Path
    ) -> None:
        _make_agent_file(
            agents_dir,
            "uppercase-marker-agent",
            "## OUTPUT FORMAT\nProvide JSON.",
        )
        planner._registry.load_directory(agents_dir)
        assert planner._agent_has_output_spec("uppercase-marker-agent") is True


class TestModelInheritance:
    """Model preference from agent definition should propagate to plan steps."""

    def test_architect_inherits_opus_model(self, planner: IntelligentPlanner) -> None:
        plan = planner.create_plan(
            "Design the new module layout",
            agents=["architect"],
            task_type="documentation",
        )
        architect_steps = [
            s for p in plan.phases for s in p.steps
            if s.agent_name == "architect"
        ]
        assert architect_steps, "Expected at least one architect step"
        for step in architect_steps:
            assert step.model == "opus", (
                f"architect step should inherit model=opus, got {step.model!r}"
            )

    def test_backend_engineer_keeps_sonnet_default(
        self, planner: IntelligentPlanner
    ) -> None:
        plan = planner.create_plan(
            "Fix a bug in the auth module",
            agents=["backend-engineer"],
            task_type="bug-fix",
        )
        be_steps = [
            s for p in plan.phases for s in p.steps
            if s.agent_name == "backend-engineer"
        ]
        assert be_steps
        for step in be_steps:
            assert step.model == "sonnet"

    def test_unknown_agent_keeps_default_sonnet(
        self, planner: IntelligentPlanner
    ) -> None:
        """An agent not in registry should keep the default model."""
        plan = planner.create_plan(
            "Do some work",
            agents=["unknown-specialist"],
            task_type="new-feature",
        )
        all_steps = [s for p in plan.phases for s in p.steps]
        assert all_steps
        for step in all_steps:
            assert step.model == "sonnet"

    def test_all_plan_steps_have_model_set(
        self, planner: IntelligentPlanner
    ) -> None:
        """Every step in a plan should have a non-empty model."""
        plan = planner.create_plan(
            "Add user authentication",
            agents=["architect", "backend-engineer", "test-engineer", "code-reviewer"],
        )
        for phase in plan.phases:
            for step in phase.steps:
                assert step.model, f"Step {step.step_id} has empty model"


class TestDeliverablesDeduplication:
    """Agents with output specs should not get default _AGENT_DELIVERABLES."""

    def test_agent_with_output_spec_gets_no_default_deliverables(
        self, planner: IntelligentPlanner, agents_dir: Path
    ) -> None:
        """When agent definition has 'when you finish', default deliverables are skipped."""
        # Add output-spec-agent to the registry (already created in agents_dir fixture)
        plan = planner.create_plan(
            "Do something",
            agents=["output-spec-agent"],
            task_type="new-feature",
        )
        output_spec_steps = [
            s for p in plan.phases for s in p.steps
            if s.agent_name == "output-spec-agent"
        ]
        # Deliverables should NOT be auto-populated for this agent
        for step in output_spec_steps:
            assert step.deliverables == [], (
                f"output-spec-agent should have no default deliverables, "
                f"got {step.deliverables}"
            )

    def test_agent_without_output_spec_gets_default_deliverables(
        self, planner: IntelligentPlanner
    ) -> None:
        """Agents with no output spec should receive _AGENT_DELIVERABLES defaults."""
        plan = planner.create_plan(
            "Add user authentication",
            agents=["architect"],
            task_type="new-feature",
        )
        architect_steps = [
            s for p in plan.phases for s in p.steps
            if s.agent_name == "architect"
        ]
        # Phase-1 steps (no previous phase enrichment) should have deliverables set
        # at least one architect step should get defaults
        steps_with_deliverables = [s for s in architect_steps if s.deliverables]
        assert steps_with_deliverables, "At least one architect step should have deliverables"
        for step in steps_with_deliverables:
            assert step.deliverables == ["Design document"]


# ---------------------------------------------------------------------------
# Tier 3 — Context richness
# ---------------------------------------------------------------------------


class TestExtractFilePaths:
    """Tests for IntelligentPlanner._extract_file_paths()."""

    @pytest.mark.parametrize("text,expected_paths", [
        # Explicit paths with slashes
        ("Update agent_baton/core/engine/planner.py", ["agent_baton/core/engine/planner.py"]),
        ("See docs/spec.md for details", ["docs/spec.md"]),
        ("Edit tests/test_foo.py and src/bar.py", ["tests/test_foo.py", "src/bar.py"]),
        # Paths with trailing slash (directory-like)
        ("Work in agent_baton/models/", ["agent_baton/models/"]),
        # Known extensions without slash
        ("Change config.yaml and schema.json", ["config.yaml", "schema.json"]),
        # CLAUDE.md — single-component with known extension
        ("Read CLAUDE.md first", ["CLAUDE.md"]),
    ])
    def test_extracts_valid_paths(
        self, planner: IntelligentPlanner, text: str, expected_paths: list[str]
    ) -> None:
        result = planner._extract_file_paths(text)
        for path in expected_paths:
            assert path in result, (
                f"Expected {path!r} in extracted paths from {text!r}, got {result}"
            )

    @pytest.mark.parametrize("text,rejected", [
        # Plain version numbers — must not be extracted
        ("Upgrade to version 3.11 of Python", "3.11"),
        # Short plain words without known extension
        ("Fix the issue in auth", "auth"),
        # Numbers only
        ("Step 1.1 is complete", "1.1"),
    ])
    def test_rejects_non_paths(
        self, planner: IntelligentPlanner, text: str, rejected: str
    ) -> None:
        result = planner._extract_file_paths(text)
        assert rejected not in result, (
            f"Non-path {rejected!r} should not appear in extraction of {text!r}, "
            f"got {result}"
        )

    def test_deduplicates_repeated_paths(self, planner: IntelligentPlanner) -> None:
        text = "Edit agent_baton/models/execution.py. Also check agent_baton/models/execution.py."
        result = planner._extract_file_paths(text)
        assert result.count("agent_baton/models/execution.py") == 1

    def test_empty_text_returns_empty_list(self, planner: IntelligentPlanner) -> None:
        assert planner._extract_file_paths("") == []

    def test_no_file_like_tokens_returns_empty(self, planner: IntelligentPlanner) -> None:
        assert planner._extract_file_paths("Add a new feature today") == []

    def test_multiple_paths_all_extracted(self, planner: IntelligentPlanner) -> None:
        text = (
            "Modify agent_baton/core/engine/planner.py and "
            "agent_baton/core/engine/dispatcher.py to add the new feature. "
            "Also update tests/test_engine_planner.py."
        )
        result = planner._extract_file_paths(text)
        assert "agent_baton/core/engine/planner.py" in result
        assert "agent_baton/core/engine/dispatcher.py" in result
        assert "tests/test_engine_planner.py" in result


class TestContextFilesAutoExtraction:
    """Extracted file paths from task summary appear in plan step context_files."""

    def test_file_path_in_summary_added_to_context_files(
        self, planner: IntelligentPlanner
    ) -> None:
        plan = planner.create_plan(
            "Update agent_baton/core/engine/planner.py to fix the bug",
            agents=["backend-engineer"],
            task_type="bug-fix",
        )
        all_context_files = [
            f for p in plan.phases for s in p.steps for f in s.context_files
        ]
        assert "agent_baton/core/engine/planner.py" in all_context_files

    def test_extracted_paths_deduplicated_with_existing(
        self, planner: IntelligentPlanner
    ) -> None:
        """If a step already has CLAUDE.md and the summary mentions it, no duplicate."""
        plan = planner.create_plan(
            "Review CLAUDE.md for outdated conventions",
            agents=["architect"],
            task_type="documentation",
        )
        for phase in plan.phases:
            for step in phase.steps:
                # CLAUDE.md should appear at most once per step
                count = step.context_files.count("CLAUDE.md")
                assert count <= 1, (
                    f"CLAUDE.md appears {count} times in step {step.step_id}"
                )

    def test_no_paths_in_summary_leaves_only_claude_md(
        self, planner: IntelligentPlanner
    ) -> None:
        """When task summary has no file paths, steps get only CLAUDE.md."""
        plan = planner.create_plan(
            "Add a new feature",
            agents=["backend-engineer"],
            task_type="new-feature",
        )
        for phase in plan.phases:
            for step in phase.steps:
                assert "CLAUDE.md" in step.context_files


class TestDelegationPromptIntent:
    """The delegation prompt includes an ## Intent section."""

    def test_intent_section_present_when_task_summary_provided(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(
            step, task_summary="Fix the login bug in auth module"
        )
        assert "## Intent" in prompt

    def test_intent_section_contains_unmodified_summary(
        self, dispatcher: PromptDispatcher
    ) -> None:
        task_summary = "Fix the login bug in auth module"
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, task_summary=task_summary)
        assert task_summary in prompt

    def test_intent_section_absent_when_no_task_summary(
        self, dispatcher: PromptDispatcher
    ) -> None:
        """When task_summary is empty, the ## Intent section is omitted."""
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, task_summary="")
        assert "## Intent" not in prompt

    def test_intent_appears_before_your_task(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(
            step, task_summary="Build the new API"
        )
        intent_pos = prompt.index("## Intent")
        task_pos = prompt.index("## Your Task")
        assert intent_pos < task_pos, "Intent section should precede Your Task section"

    def test_intent_summary_not_wrapped_in_template_text(
        self, dispatcher: PromptDispatcher
    ) -> None:
        """The user's original words should appear verbatim, not inside a sentence."""
        original = "Refactor the payment module to reduce coupling"
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, task_summary=original)
        # The exact string must appear as-is, not buried in a sentence
        assert original in prompt


class TestDelegationPromptSuccessCriteria:
    """The delegation prompt includes ## Success Criteria for known task types."""

    @pytest.mark.parametrize("task_type,expected_fragment", [
        ("bug-fix", "regression test"),
        ("new-feature", "test coverage"),
        ("refactor", "Behavior is unchanged"),
        ("test", "no false positives"),
        ("documentation", "matches current code"),
        ("migration", "rollback capability"),
        ("data-analysis", "supporting evidence"),
    ])
    def test_success_criteria_matches_task_type(
        self,
        dispatcher: PromptDispatcher,
        task_type: str,
        expected_fragment: str,
    ) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, task_type=task_type)
        assert "**Success criteria:**" in prompt
        assert expected_fragment.lower() in prompt.lower(), (
            f"For task_type={task_type!r}, expected {expected_fragment!r} in:\n{prompt}"
        )

    def test_success_criteria_absent_for_unknown_task_type(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step, task_type="unknown-type")
        assert "## Success Criteria" not in prompt

    def test_success_criteria_absent_when_no_task_type(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert "## Success Criteria" not in prompt

    def test_success_criteria_dict_covers_all_canonical_task_types(self) -> None:
        """Every infer-able task type except 'test' (which maps to _SUCCESS_CRITERIA['test'])
        should have a success criteria entry."""
        expected_keys = {
            "bug-fix", "new-feature", "refactor", "test",
            "documentation", "migration", "data-analysis",
        }
        assert set(_SUCCESS_CRITERIA.keys()) == expected_keys, (
            f"_SUCCESS_CRITERIA keys mismatch. "
            f"Missing: {expected_keys - set(_SUCCESS_CRITERIA.keys())}, "
            f"Extra: {set(_SUCCESS_CRITERIA.keys()) - expected_keys}"
        )

    def test_success_criteria_section_appears_after_your_task(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(
            step, task_type="bug-fix", task_summary="Fix login"
        )
        task_pos = prompt.index("## Your Task")
        criteria_pos = prompt.index("**Success criteria:**")
        assert task_pos < criteria_pos


# ---------------------------------------------------------------------------
# Tier 4 — Pushback protocol
# ---------------------------------------------------------------------------


class TestExtractDeviations:
    """Tests for ExecutionEngine._extract_deviations() (static method)."""

    def test_single_deviation_section_extracted(self) -> None:
        outcome = (
            "## Summary\nDone the work.\n\n"
            "## Deviations\nI changed the approach because the API was different."
        )
        result = ExecutionEngine._extract_deviations(outcome)
        assert len(result) == 1
        assert "changed the approach" in result[0]

    def test_singular_deviation_heading_works(self) -> None:
        outcome = "## Deviation\nHad to use a different algorithm."
        result = ExecutionEngine._extract_deviations(outcome)
        assert len(result) == 1
        assert "different algorithm" in result[0]

    def test_multiple_deviation_sections_each_extracted(self) -> None:
        outcome = (
            "## Deviations\nFirst change: used REST instead of gRPC.\n\n"
            "## Implementation\nDone the work.\n\n"
            "## Deviations\nSecond change: skipped caching layer."
        )
        result = ExecutionEngine._extract_deviations(outcome)
        assert len(result) == 2
        assert any("REST" in d for d in result)
        assert any("caching" in d for d in result)

    def test_no_deviation_section_returns_empty_list(self) -> None:
        outcome = "## Summary\nCompleted the task.\n\n## Files Changed\nfoo.py"
        result = ExecutionEngine._extract_deviations(outcome)
        assert result == []

    def test_empty_outcome_returns_empty_list(self) -> None:
        assert ExecutionEngine._extract_deviations("") == []

    def test_deviation_section_with_no_content_excluded(self) -> None:
        """A Deviation heading followed immediately by another heading has no content."""
        outcome = "## Deviations\n## Next Section\nActual content."
        result = ExecutionEngine._extract_deviations(outcome)
        # Should not include an empty deviation
        assert result == []

    def test_deviation_captures_multiline_content(self) -> None:
        outcome = (
            "## Deviations\n"
            "Line one of deviation.\n"
            "Line two of deviation.\n"
            "Line three of deviation."
        )
        result = ExecutionEngine._extract_deviations(outcome)
        assert len(result) == 1
        assert "Line one" in result[0]
        assert "Line three" in result[0]

    def test_deviation_heading_level_1_works(self) -> None:
        outcome = "# Deviations\nUsed a queue instead of direct calls."
        result = ExecutionEngine._extract_deviations(outcome)
        assert len(result) == 1

    def test_deviation_heading_level_3_works(self) -> None:
        outcome = "### Deviations\nUsed mock instead of real implementation."
        result = ExecutionEngine._extract_deviations(outcome)
        assert len(result) == 1

    def test_heading_level_4_not_treated_as_deviation(self) -> None:
        """Level-4+ headings (####) are not recognized as Deviation sections."""
        outcome = "#### Deviations\nSome content."
        result = ExecutionEngine._extract_deviations(outcome)
        assert result == []

    def test_deviation_content_stripped_of_leading_trailing_whitespace(self) -> None:
        outcome = "## Deviations\n\n  Had to use approach B.  \n\n"
        result = ExecutionEngine._extract_deviations(outcome)
        assert len(result) == 1
        assert result[0] == result[0].strip()

    def test_deviation_stops_at_next_heading(self) -> None:
        outcome = (
            "## Deviations\nUsed queue instead.\n"
            "## Summary\nThis should not be in deviation."
        )
        result = ExecutionEngine._extract_deviations(outcome)
        assert len(result) == 1
        assert "Summary" not in result[0]
        assert "should not" not in result[0]


class TestStepResultDeviationsSerialization:
    """StepResult.deviations serializes and deserializes correctly."""

    def test_to_dict_includes_deviations(self) -> None:
        result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer",
            deviations=["Used approach B instead of A"],
        )
        d = result.to_dict()
        assert "deviations" in d
        assert d["deviations"] == ["Used approach B instead of A"]

    def test_from_dict_restores_deviations(self) -> None:
        data = {
            "step_id": "1.1",
            "agent_name": "backend-engineer",
            "status": "complete",
            "outcome": "Done",
            "files_changed": [],
            "commit_hash": "",
            "estimated_tokens": 0,
            "duration_seconds": 0.0,
            "retries": 0,
            "error": "",
            "completed_at": "",
            "deviations": ["Changed X to Y because Z"],
        }
        result = StepResult.from_dict(data)
        assert result.deviations == ["Changed X to Y because Z"]

    def test_serialization_roundtrip_preserves_deviations(self) -> None:
        original = StepResult(
            step_id="2.1",
            agent_name="architect",
            deviations=["Deviation A", "Deviation B"],
        )
        restored = StepResult.from_dict(original.to_dict())
        assert restored.deviations == ["Deviation A", "Deviation B"]

    def test_from_dict_backward_compat_missing_deviations_key(self) -> None:
        """Old execution-state.json files without 'deviations' must load cleanly."""
        data = {
            "step_id": "1.1",
            "agent_name": "backend-engineer",
            "status": "complete",
            "outcome": "Done",
            "files_changed": [],
            "commit_hash": "",
            "estimated_tokens": 0,
            "duration_seconds": 0.0,
            "retries": 0,
            "error": "",
            "completed_at": "",
            # 'deviations' key intentionally absent
        }
        result = StepResult.from_dict(data)
        assert result.deviations == []

    def test_default_deviations_is_empty_list(self) -> None:
        result = StepResult(step_id="1.1", agent_name="agent")
        assert result.deviations == []

    def test_to_dict_empty_deviations_included(self) -> None:
        result = StepResult(step_id="1.1", agent_name="agent", deviations=[])
        d = result.to_dict()
        assert "deviations" in d
        assert d["deviations"] == []

    def test_from_dict_multiple_deviations(self) -> None:
        data = {
            "step_id": "3.1",
            "agent_name": "test-engineer",
            "status": "complete",
            "outcome": "",
            "files_changed": [],
            "commit_hash": "",
            "estimated_tokens": 0,
            "duration_seconds": 0.0,
            "retries": 0,
            "error": "",
            "completed_at": "",
            "deviations": ["Note one", "Note two", "Note three"],
        }
        result = StepResult.from_dict(data)
        assert len(result.deviations) == 3


class TestDelegationPromptDeviationsSection:
    """Delegation prompt instructs agents to log deviations."""

    def test_deviations_instruction_present(self, dispatcher: PromptDispatcher) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert "**Deviations**" in prompt

    def test_decisions_instruction_present(
        self, dispatcher: PromptDispatcher
    ) -> None:
        step = _make_step()
        prompt = dispatcher.build_delegation_prompt(step)
        assert "**Decisions**" in prompt


class TestDeviationIntegration:
    """End-to-end: record_step_result extracts deviations from outcome text."""

    @pytest.fixture
    def engine(self, tmp_path: Path) -> ExecutionEngine:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        return ExecutionEngine(team_context_root=ctx)

    @pytest.fixture
    def plan_with_one_step(self) -> MachinePlan:
        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Fix the bug",
            model="sonnet",
        )
        phase = PlanPhase(phase_id=1, name="Fix", steps=[step])
        return MachinePlan(
            task_id="test-task-001",
            task_summary="Fix the bug",
            risk_level="LOW",
            phases=[phase],
        )

    def test_deviations_extracted_and_stored_on_step_result(
        self, engine: ExecutionEngine, plan_with_one_step: MachinePlan
    ) -> None:
        engine.start(plan_with_one_step)
        outcome = (
            "Fixed the bug.\n\n"
            "## Deviations\n"
            "Used a regex approach instead of the parser module."
        )
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome=outcome,
        )
        state = engine._load_execution()
        assert state is not None
        step_results = [r for r in state.step_results if r.step_id == "1.1" and r.status == "complete"]
        assert step_results, "Expected a complete step result for 1.1"
        result = step_results[-1]
        assert len(result.deviations) == 1
        assert "regex" in result.deviations[0]

    def test_outcome_without_deviations_has_empty_list(
        self, engine: ExecutionEngine, plan_with_one_step: MachinePlan
    ) -> None:
        engine.start(plan_with_one_step)
        engine.record_step_result(
            "1.1",
            "backend-engineer",
            status="complete",
            outcome="Fixed the bug cleanly with no surprises.",
        )
        state = engine._load_execution()
        assert state is not None
        step_results = [r for r in state.step_results if r.step_id == "1.1" and r.status == "complete"]
        result = step_results[-1]
        assert result.deviations == []


class TestDeviationRetrospectiveIntegration:
    """Deviations in step results feed the retrospective as SequencingNotes."""

    @pytest.fixture
    def engine(self, tmp_path: Path) -> ExecutionEngine:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        return ExecutionEngine(team_context_root=ctx)

    def test_deviations_produce_sequencing_notes(
        self, engine: ExecutionEngine
    ) -> None:
        from agent_baton.models.execution import ExecutionState

        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Fix the bug",
            model="sonnet",
        )
        phase = PlanPhase(phase_id=1, name="Fix", steps=[step])
        plan = MachinePlan(
            task_id="retro-test-001",
            task_summary="Fix the bug",
            risk_level="LOW",
            phases=[phase],
        )

        # Build a fake state with a step result that has deviations
        step_result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            deviations=["Used REST instead of gRPC as originally planned."],
        )
        from agent_baton.models.execution import ExecutionState
        state = ExecutionState(
            task_id=plan.task_id,
            plan=plan,
            step_results=[step_result],
            gate_results=[],
        )

        retro_data = engine._build_retrospective_data(state)
        sequencing_notes = retro_data.get("sequencing_notes", [])

        # At least one note should reference the deviation
        deviation_notes = [
            n for n in sequencing_notes
            if "deviated" in getattr(n, "observation", "").lower()
        ]
        assert deviation_notes, (
            f"Expected sequencing notes from deviations, got: {sequencing_notes}"
        )
        assert any("backend-engineer" in n.observation for n in deviation_notes)
        assert any("REST" in n.observation for n in deviation_notes)

    def test_deviation_note_phase_is_labeled_deviation(
        self, engine: ExecutionEngine
    ) -> None:
        """SequencingNotes from deviations use phase='deviation'."""
        from agent_baton.models.execution import ExecutionState

        step = PlanStep(
            step_id="1.1",
            agent_name="architect",
            task_description="Design the system",
            model="opus",
        )
        phase = PlanPhase(phase_id=1, name="Design", steps=[step])
        plan = MachinePlan(
            task_id="retro-test-002",
            task_summary="Design the system",
            risk_level="LOW",
            phases=[phase],
        )
        step_result = StepResult(
            step_id="1.1",
            agent_name="architect",
            status="complete",
            deviations=["Changed from monolith to microservices."],
        )
        state = ExecutionState(
            task_id=plan.task_id,
            plan=plan,
            step_results=[step_result],
            gate_results=[],
        )

        retro_data = engine._build_retrospective_data(state)
        sequencing_notes = retro_data.get("sequencing_notes", [])
        deviation_notes = [
            n for n in sequencing_notes
            if getattr(n, "phase", "") == "deviation"
        ]
        assert deviation_notes, "Expected at least one note with phase='deviation'"

    def test_no_deviations_produces_no_extra_sequencing_notes(
        self, engine: ExecutionEngine
    ) -> None:
        """When no step has deviations, no deviation-phase notes are added."""
        from agent_baton.models.execution import ExecutionState

        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Implement the feature",
            model="sonnet",
        )
        phase = PlanPhase(phase_id=1, name="Implement", steps=[step])
        plan = MachinePlan(
            task_id="retro-test-003",
            task_summary="Implement the feature",
            risk_level="LOW",
            phases=[phase],
        )
        step_result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            deviations=[],
        )
        state = ExecutionState(
            task_id=plan.task_id,
            plan=plan,
            step_results=[step_result],
            gate_results=[],
        )

        retro_data = engine._build_retrospective_data(state)
        sequencing_notes = retro_data.get("sequencing_notes", [])
        deviation_notes = [
            n for n in sequencing_notes
            if getattr(n, "phase", "") == "deviation"
        ]
        assert deviation_notes == []


# ---------------------------------------------------------------------------
# MachinePlan.task_type field
# ---------------------------------------------------------------------------


class TestMachinePlanTaskType:
    """MachinePlan.task_type is set correctly and survives serialization."""

    def test_create_plan_stores_inferred_task_type(
        self, planner: IntelligentPlanner
    ) -> None:
        plan = planner.create_plan("Fix the login bug")
        assert plan.task_type == "bug-fix"

    def test_create_plan_stores_overridden_task_type(
        self, planner: IntelligentPlanner
    ) -> None:
        plan = planner.create_plan("Do something", task_type="migration")
        assert plan.task_type == "migration"

    def test_task_type_serialization_roundtrip(self) -> None:
        plan = MachinePlan(
            task_id="t-001",
            task_summary="Fix the bug",
            risk_level="LOW",
            phases=[],
            task_type="bug-fix",
        )
        restored = MachinePlan.from_dict(plan.to_dict())
        assert restored.task_type == "bug-fix"

    def test_task_type_backward_compat_missing_key(self) -> None:
        """Old plan dicts without 'task_type' should load as None (not empty string).

        Phase 1 changed the field type from str to str | None to distinguish
        'not inferred yet' from an empty string. Old plan.json files that
        lack the key deserialize to None.
        """
        data = {
            "task_id": "t-old",
            "task_summary": "Old plan",
            "risk_level": "LOW",
            "budget_tier": "standard",
            "git_strategy": "commit-per-agent",
            "phases": [],
            "shared_context": "",
            "pattern_source": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            # 'task_type' intentionally absent
        }
        plan = MachinePlan.from_dict(data)
        assert plan.task_type is None


# ---------------------------------------------------------------------------
# Routing mismatch detection in retrospective builder
# ---------------------------------------------------------------------------


class TestRoutingMismatchRetrospective:
    """Executor detects agent flavor / stack mismatches and emits roster recs."""

    @pytest.fixture
    def engine(self, tmp_path: Path) -> ExecutionEngine:
        ctx = tmp_path / "team-context"
        ctx.mkdir()
        return ExecutionEngine(team_context_root=ctx)

    def test_node_agent_on_python_stack_generates_prefer_rec(
        self, engine: ExecutionEngine
    ) -> None:
        from agent_baton.models.execution import ExecutionState

        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--node",
            task_description="Fix the API",
        )
        plan = MachinePlan(
            task_id="routing-test-001",
            task_summary="Fix the API",
            risk_level="LOW",
            phases=[PlanPhase(phase_id=1, name="Fix", steps=[step])],
            detected_stack="python",
        )
        step_result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer--node",
            status="complete",
        )
        state = ExecutionState(
            task_id=plan.task_id,
            plan=plan,
            step_results=[step_result],
            gate_results=[],
        )

        retro_data = engine._build_retrospective_data(state)
        roster_recs = retro_data.get("roster_recommendations", [])
        prefer_recs = [
            r for r in roster_recs
            if r.action == "prefer" and "backend-engineer--python" in r.target
        ]
        assert prefer_recs, (
            f"Expected 'prefer backend-engineer--python' recommendation, "
            f"got: {roster_recs}"
        )

    def test_correct_flavor_generates_no_mismatch_rec(
        self, engine: ExecutionEngine
    ) -> None:
        from agent_baton.models.execution import ExecutionState

        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--python",
            task_description="Fix the API",
        )
        plan = MachinePlan(
            task_id="routing-test-002",
            task_summary="Fix the API",
            risk_level="LOW",
            phases=[PlanPhase(phase_id=1, name="Fix", steps=[step])],
            detected_stack="python",
        )
        step_result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer--python",
            status="complete",
        )
        state = ExecutionState(
            task_id=plan.task_id,
            plan=plan,
            step_results=[step_result],
            gate_results=[],
        )

        retro_data = engine._build_retrospective_data(state)
        roster_recs = retro_data.get("roster_recommendations", [])
        mismatch_recs = [r for r in roster_recs if "mismatch" in r.reason.lower()]
        assert not mismatch_recs, (
            f"No mismatch expected for correct flavor, got: {mismatch_recs}"
        )

    def test_no_detected_stack_skips_mismatch_check(
        self, engine: ExecutionEngine
    ) -> None:
        from agent_baton.models.execution import ExecutionState

        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer--node",
            task_description="Fix the API",
        )
        plan = MachinePlan(
            task_id="routing-test-003",
            task_summary="Fix the API",
            risk_level="LOW",
            phases=[PlanPhase(phase_id=1, name="Fix", steps=[step])],
            # No detected_stack
        )
        step_result = StepResult(
            step_id="1.1",
            agent_name="backend-engineer--node",
            status="complete",
        )
        state = ExecutionState(
            task_id=plan.task_id,
            plan=plan,
            step_results=[step_result],
            gate_results=[],
        )

        retro_data = engine._build_retrospective_data(state)
        roster_recs = retro_data.get("roster_recommendations", [])
        mismatch_recs = [r for r in roster_recs if "mismatch" in r.reason.lower()]
        assert not mismatch_recs
