"""Tests for agent_baton.core.engine.plan_reviewer.PlanReviewer."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agent_baton.core.engine.plan_reviewer import (
    PlanReviewer,
    _cluster_by_directory,
    _detect_coupling,
    _extract_file_paths,
    _humanize_directory,
)
from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plan(
    phases: list[PlanPhase] | None = None,
    task_summary: str = "Add new feature",
    complexity: str = "medium",
) -> MachinePlan:
    """Build a minimal MachinePlan for testing."""
    return MachinePlan(
        task_id="test-task-001",
        task_summary=task_summary,
        risk_level="LOW",
        budget_tier="standard",
        phases=phases or [],
        task_type="new-feature",
        complexity=complexity,
    )


def _make_step(
    step_id: str = "2.1",
    agent: str = "backend-engineer--python",
    desc: str = "Implement the feature",
    context_files: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent,
        task_description=desc,
        context_files=context_files or [],
    )


def _make_broad_plan() -> MachinePlan:
    """Create a plan with a single broad implementation step spanning many concerns."""
    step = _make_step(
        desc="Implement engine routing, dispatcher changes, worker automation, and CLI output",
        context_files=[
            "agent_baton/core/engine/executor.py",
            "agent_baton/core/engine/dispatcher.py",
            "agent_baton/core/runtime/worker.py",
            "agent_baton/cli/commands/execution/execute.py",
            "agent_baton/core/engine/planner.py",
            "agent_baton/models/execution.py",
        ],
    )
    return _make_plan(
        phases=[
            PlanPhase(phase_id=1, name="Design", steps=[
                _make_step("1.1", "architect", "Design the approach"),
            ]),
            PlanPhase(phase_id=2, name="Implement", steps=[step]),
            PlanPhase(phase_id=3, name="Test", steps=[
                _make_step("3.1", "test-engineer", "Write tests"),
            ]),
        ],
        task_summary=(
            "Add new step type to executor.py, dispatcher.py, worker.py, "
            "execute.py, planner.py, and execution.py"
        ),
    )


# ---------------------------------------------------------------------------
# Unit tests — file path extraction
# ---------------------------------------------------------------------------

class TestExtractFilePaths:
    def test_extracts_paths_with_slashes(self):
        text = "Fix agent_baton/core/engine/executor.py and agent_baton/models/execution.py"
        paths = _extract_file_paths(text)
        assert "agent_baton/core/engine/executor.py" in paths
        assert "agent_baton/models/execution.py" in paths

    def test_extracts_bare_filenames_with_extensions(self):
        text = "Update planner.py and classifier.py"
        paths = _extract_file_paths(text)
        assert "planner.py" in paths
        assert "classifier.py" in paths

    def test_ignores_non_paths(self):
        text = "This is a plain description with no file paths"
        paths = _extract_file_paths(text)
        assert len(paths) == 0

    def test_deduplicates(self):
        text = "Fix executor.py then test executor.py again"
        paths = _extract_file_paths(text)
        assert paths.count("executor.py") == 1


# ---------------------------------------------------------------------------
# Unit tests — directory clustering
# ---------------------------------------------------------------------------

class TestClusterByDirectory:
    def test_groups_by_immediate_parent(self):
        paths = [
            "agent_baton/core/engine/executor.py",
            "agent_baton/core/engine/planner.py",
            "agent_baton/models/execution.py",
            "agent_baton/cli/commands/execution/execute.py",
        ]
        clusters = _cluster_by_directory(paths)
        assert "engine" in clusters
        assert "models" in clusters
        assert "execution" in clusters
        assert len(clusters["engine"]) == 2

    def test_bare_filenames_go_to_root(self):
        paths = ["README.md", "setup.py"]
        clusters = _cluster_by_directory(paths)
        assert "root" in clusters
        assert len(clusters["root"]) == 2

    def test_mixed_depths(self):
        paths = [
            "agent_baton/core/engine/executor.py",
            "tests/test_executor.py",
            "README.md",
        ]
        clusters = _cluster_by_directory(paths)
        assert "engine" in clusters
        assert "tests" in clusters
        assert "root" in clusters


class TestHumanizeDirectory:
    def test_known_directories(self):
        assert "engine core" in _humanize_directory("engine")
        assert "CLI" in _humanize_directory("cli")
        assert "data models" in _humanize_directory("models")

    def test_unknown_directory_passes_through(self):
        assert _humanize_directory("utils") == "utils"


# ---------------------------------------------------------------------------
# Heuristic review tests
# ---------------------------------------------------------------------------

class TestHeuristicReview:
    def test_skips_light_complexity(self):
        reviewer = PlanReviewer()
        plan = _make_broad_plan()
        result = reviewer.review(plan, plan.task_summary, complexity="light")
        assert result.source == "skipped-light"
        assert result.splits_applied == 0

    def test_creates_team_for_coupled_concerns(self):
        """The core bug: a single step spanning coupled concerns should become a team."""
        reviewer = PlanReviewer()
        plan = _make_broad_plan()
        impl_phase = plan.phases[1]
        assert len(impl_phase.steps) == 1, "Precondition: single step"

        # Haiku unavailable — will use heuristic fallback
        with patch(
            "agent_baton.core.engine.plan_reviewer.PlanReviewer._try_haiku_review",
            return_value=None,
        ):
            result = reviewer.review(
                plan, plan.task_summary,
                file_paths=[
                    "agent_baton/core/engine/executor.py",
                    "agent_baton/core/engine/dispatcher.py",
                    "agent_baton/core/runtime/worker.py",
                    "agent_baton/cli/commands/execution/execute.py",
                    "agent_baton/core/engine/planner.py",
                    "agent_baton/models/execution.py",
                ],
                complexity="medium",
            )

        assert result.source == "heuristic"
        # Coupled concerns (engine+runtime+models+execution) → team
        assert result.teams_created >= 1
        # The step should now be a team step
        team_step = impl_phase.steps[0]
        assert team_step.agent_name == "team"
        assert len(team_step.team) >= 2
        # All team members should use the same agent
        member_agents = {m.agent_name for m in team_step.team}
        assert member_agents == {"backend-engineer--python"}
        # Team should have synthesis
        assert team_step.synthesis is not None

    def test_splits_independent_concerns_into_parallel_steps(self):
        """Truly independent concerns should produce parallel steps, not a team."""
        # Use files in unrelated directories that are NOT in _COUPLED_PAIRS
        step = _make_step(
            desc="Update docs, add tests, fix distribution scripts, and update PMO UI",
            context_files=[
                "docs/architecture.md",
                "docs/cli-reference.md",
                "tests/test_executor.py",
                "tests/test_planner.py",
                "agent_baton/core/distribute/packaging.py",
                "pmo-ui/src/components/Board.tsx",
            ],
        )
        plan = _make_plan(phases=[
            PlanPhase(phase_id=1, name="Implement", steps=[step]),
        ])
        reviewer = PlanReviewer()
        with patch(
            "agent_baton.core.engine.plan_reviewer.PlanReviewer._try_haiku_review",
            return_value=None,
        ):
            result = reviewer.review(
                plan,
                "Update docs, tests, distribution, and PMO UI",
                file_paths=[
                    "docs/architecture.md",
                    "docs/cli-reference.md",
                    "tests/test_executor.py",
                    "tests/test_planner.py",
                    "agent_baton/core/distribute/packaging.py",
                    "pmo-ui/src/components/Board.tsx",
                ],
                complexity="medium",
            )
        assert result.source == "heuristic"
        assert result.splits_applied >= 1
        assert result.teams_created == 0
        # Multiple parallel steps
        assert len(plan.phases[0].steps) > 1
        # All steps should be parallel (no depends_on)
        for s in plan.phases[0].steps:
            assert s.depends_on == []

    def test_does_not_split_narrow_step(self):
        """A step touching 2 files in the same directory should not split."""
        step = _make_step(
            context_files=[
                "agent_baton/core/engine/executor.py",
                "agent_baton/core/engine/planner.py",
            ],
        )
        plan = _make_plan(phases=[
            PlanPhase(phase_id=1, name="Implement", steps=[step]),
        ])
        reviewer = PlanReviewer()
        with patch(
            "agent_baton.core.engine.plan_reviewer.PlanReviewer._try_haiku_review",
            return_value=None,
        ):
            result = reviewer.review(plan, "Fix two engine files", complexity="medium")
        assert result.splits_applied == 0
        assert len(plan.phases[0].steps) == 1

    def test_does_not_split_design_phase(self):
        """Design phases should never be split, even if they touch many files."""
        step = _make_step(
            step_id="1.1",
            agent="architect",
            desc="Design approach for engine, CLI, models, runtime",
            context_files=[
                "agent_baton/core/engine/executor.py",
                "agent_baton/cli/commands/execution/execute.py",
                "agent_baton/models/execution.py",
                "agent_baton/core/runtime/worker.py",
            ],
        )
        plan = _make_plan(phases=[
            PlanPhase(phase_id=1, name="Design", steps=[step]),
        ])
        reviewer = PlanReviewer()
        with patch(
            "agent_baton.core.engine.plan_reviewer.PlanReviewer._try_haiku_review",
            return_value=None,
        ):
            result = reviewer.review(plan, "Design the approach", complexity="medium")
        assert result.splits_applied == 0

    def test_does_not_split_team_steps(self):
        """Team steps already have internal parallelism — don't split."""
        from agent_baton.models.execution import TeamMember

        step = _make_step(context_files=[
            "agent_baton/core/engine/executor.py",
            "agent_baton/core/engine/dispatcher.py",
            "agent_baton/core/runtime/worker.py",
            "agent_baton/cli/commands/execution/execute.py",
        ])
        step.team = [
            TeamMember(member_id="2.1.a", agent_name="backend-engineer--python",
                       role="lead", task_description="Engine work"),
            TeamMember(member_id="2.1.b", agent_name="backend-engineer--python",
                       role="implementer", task_description="CLI work"),
        ]
        plan = _make_plan(phases=[
            PlanPhase(phase_id=2, name="Implement", steps=[step]),
        ])
        reviewer = PlanReviewer()
        with patch(
            "agent_baton.core.engine.plan_reviewer.PlanReviewer._try_haiku_review",
            return_value=None,
        ):
            result = reviewer.review(
                plan, "Implement across engine and CLI",
                file_paths=[
                    "agent_baton/core/engine/executor.py",
                    "agent_baton/core/engine/dispatcher.py",
                    "agent_baton/core/runtime/worker.py",
                    "agent_baton/cli/commands/execution/execute.py",
                ],
                complexity="medium",
            )
        assert result.splits_applied == 0

    def test_does_not_split_multi_step_phase(self):
        """Phases that already have multiple steps don't need splitting."""
        plan = _make_plan(phases=[
            PlanPhase(phase_id=2, name="Implement", steps=[
                _make_step("2.1", desc="Implement engine changes"),
                _make_step("2.2", desc="Implement CLI changes"),
            ]),
        ])
        reviewer = PlanReviewer()
        with patch(
            "agent_baton.core.engine.plan_reviewer.PlanReviewer._try_haiku_review",
            return_value=None,
        ):
            result = reviewer.review(
                plan, "Implement across engine and CLI",
                file_paths=[
                    "agent_baton/core/engine/executor.py",
                    "agent_baton/cli/commands/execution/execute.py",
                    "agent_baton/models/execution.py",
                    "agent_baton/core/runtime/worker.py",
                ],
                complexity="medium",
            )
        assert result.splits_applied == 0


# ---------------------------------------------------------------------------
# Haiku review path tests
# ---------------------------------------------------------------------------

class TestHaikuReview:
    def test_parses_valid_response(self):
        reviewer = PlanReviewer()
        raw = json.dumps({
            "splits": [{
                "phase_id": 2,
                "step_id": "2.1",
                "reason": "Step covers engine, CLI, and models",
                "groups": [
                    {"label": "engine core", "files": ["executor.py"],
                     "description_hint": "Implement engine routing"},
                    {"label": "CLI layer", "files": ["execute.py"],
                     "description_hint": "Implement CLI output changes"},
                    {"label": "data models", "files": ["execution.py"],
                     "description_hint": "Update data models"},
                ],
            }],
            "dependencies": [{
                "step_id": "2.3",
                "depends_on": "2.1",
                "reason": "CLI reads engine output",
            }],
            "warnings": ["Phase 2 has imbalanced scope"],
        })
        data = reviewer._parse_review_response(raw)
        assert len(data["splits"]) == 1
        assert len(data["splits"][0]["groups"]) == 3
        assert len(data["dependencies"]) == 1
        assert len(data["warnings"]) == 1

    def test_parses_markdown_wrapped_json(self):
        reviewer = PlanReviewer()
        raw = '```json\n{"splits": [], "dependencies": [], "warnings": []}\n```'
        data = reviewer._parse_review_response(raw)
        assert data["splits"] == []

    def test_rejects_invalid_json(self):
        reviewer = PlanReviewer()
        with pytest.raises(ValueError, match="invalid JSON"):
            reviewer._parse_review_response("not json at all")

    def test_applies_split_recommendations(self):
        """Haiku split recommendations should produce parallel steps."""
        reviewer = PlanReviewer()
        plan = _make_broad_plan()

        recommendations = {
            "splits": [{
                "phase_id": 2,
                "step_id": "2.1",
                "reason": "Too broad",
                "groups": [
                    {"label": "engine", "files": ["executor.py", "planner.py"],
                     "description_hint": "Implement engine changes"},
                    {"label": "runtime", "files": ["worker.py"],
                     "description_hint": "Implement runtime changes"},
                    {"label": "CLI", "files": ["execute.py"],
                     "description_hint": "Implement CLI changes"},
                ],
            }],
            "dependencies": [],
            "warnings": [],
        }

        result = reviewer._apply_recommendations(plan, recommendations)
        assert result.splits_applied == 1
        impl_phase = plan.phases[1]
        assert len(impl_phase.steps) == 3
        assert all(s.agent_name == "backend-engineer--python" for s in impl_phase.steps)

    def test_applies_dependency_recommendations(self):
        """Haiku dependency recommendations should add depends_on edges."""
        reviewer = PlanReviewer()
        plan = _make_plan(phases=[
            PlanPhase(phase_id=2, name="Implement", steps=[
                _make_step("2.1", desc="Engine changes"),
                _make_step("2.2", desc="CLI changes"),
            ]),
        ])

        recommendations = {
            "splits": [],
            "dependencies": [{
                "step_id": "2.2",
                "depends_on": "2.1",
                "reason": "CLI depends on engine",
            }],
            "warnings": [],
        }

        result = reviewer._apply_recommendations(plan, recommendations)
        assert result.dependencies_added == 1
        assert "2.1" in plan.phases[0].steps[1].depends_on

    def test_skips_invalid_step_ids(self):
        """Recommendations referencing non-existent steps should be ignored."""
        reviewer = PlanReviewer()
        plan = _make_broad_plan()

        recommendations = {
            "splits": [{
                "phase_id": 99,
                "step_id": "99.1",
                "reason": "Nonexistent",
                "groups": [
                    {"label": "a", "files": ["a.py"], "description_hint": "a"},
                    {"label": "b", "files": ["b.py"], "description_hint": "b"},
                ],
            }],
            "dependencies": [{
                "step_id": "99.1",
                "depends_on": "2.1",
                "reason": "Nonexistent",
            }],
            "warnings": [],
        }

        result = reviewer._apply_recommendations(plan, recommendations)
        assert result.splits_applied == 0
        assert result.dependencies_added == 0

    def test_haiku_fallback_to_heuristic(self):
        """When Haiku is unavailable, reviewer should fall back to heuristic."""
        reviewer = PlanReviewer()
        plan = _make_broad_plan()

        with patch(
            "agent_baton.core.engine.classifier._haiku_available",
            return_value=(False, "No API key"),
        ):
            result = reviewer.review(
                plan, plan.task_summary,
                file_paths=[
                    "agent_baton/core/engine/executor.py",
                    "agent_baton/core/engine/dispatcher.py",
                    "agent_baton/core/runtime/worker.py",
                    "agent_baton/cli/commands/execution/execute.py",
                    "agent_baton/core/engine/planner.py",
                    "agent_baton/models/execution.py",
                ],
                complexity="medium",
            )

        assert result.source == "heuristic"


# ---------------------------------------------------------------------------
# Integration with planner
# ---------------------------------------------------------------------------

class TestPlannerIntegration:
    """Verify PlanReviewer is wired into IntelligentPlanner.create_plan()."""

    def test_planner_has_reviewer(self):
        """IntelligentPlanner should instantiate a PlanReviewer."""
        planner = IntelligentPlanner()
        assert hasattr(planner, "_plan_reviewer")
        assert isinstance(planner._plan_reviewer, PlanReviewer)

    def test_planner_has_review_result(self):
        """After create_plan, the planner should have a review result."""
        planner = IntelligentPlanner()
        planner.create_plan(
            "Fix a bug in executor.py",
            task_type="bug-fix",
            complexity="medium",
        )
        assert planner._last_review_result is not None

    def test_planner_skips_review_for_light(self):
        """Light complexity plans should skip review."""
        planner = IntelligentPlanner()
        planner.create_plan(
            "Fix a typo",
            task_type="bug-fix",
            complexity="light",
        )
        assert planner._last_review_result is not None
        assert planner._last_review_result.source == "skipped-light"

    def test_explain_plan_includes_review(self):
        """explain_plan() should include the Plan Review section."""
        planner = IntelligentPlanner()
        plan = planner.create_plan(
            "Fix a bug in executor.py",
            task_type="bug-fix",
            complexity="medium",
        )
        explanation = planner.explain_plan(plan)
        assert "## Plan Review" in explanation


# ---------------------------------------------------------------------------
# Coupling detection tests
# ---------------------------------------------------------------------------

class TestCouplingDetection:
    """Tests for _detect_coupling heuristic."""

    def test_coupled_engine_and_runtime(self):
        """Engine + runtime directories are known coupled pairs."""
        groups = {
            "engine": ["agent_baton/core/engine/executor.py"],
            "runtime": ["agent_baton/core/runtime/worker.py"],
            "models": ["agent_baton/models/execution.py"],
        }
        assert _detect_coupling(groups, "Wire new step type through engine and runtime")

    def test_coupled_engine_and_cli(self):
        """Engine + execution (CLI commands) are coupled."""
        groups = {
            "engine": ["agent_baton/core/engine/executor.py"],
            "execution": ["agent_baton/cli/commands/execution/execute.py"],
            "models": ["agent_baton/models/execution.py"],
        }
        assert _detect_coupling(groups, "Add new action type")

    def test_uncoupled_docs_and_tests(self):
        """Docs + tests + unrelated dirs are independent."""
        groups = {
            "docs": ["docs/architecture.md"],
            "tests": ["tests/test_executor.py"],
            "pmo-ui": ["pmo-ui/src/App.tsx"],
        }
        assert not _detect_coupling(groups, "Update docs and tests")

    def test_coupling_keywords_boost_signal(self):
        """Integration keywords increase coupling score."""
        # Groups that are NOT in _COUPLED_PAIRS but task description is coupling
        groups = {
            "observe": ["agent_baton/core/observe/trace.py"],
            "learn": ["agent_baton/core/learn/engine.py"],
            "improve": ["agent_baton/core/improve/scoring.py"],
        }
        # "wire" and "end-to-end" are coupling keywords — with shared parent
        # they should trigger coupling
        assert _detect_coupling(
            groups, "Wire end-to-end learning pipeline from observe to improve"
        )

    def test_uncoupled_no_keywords_no_pairs(self):
        """Completely unrelated directories with neutral description."""
        groups = {
            "distribute": ["agent_baton/core/distribute/packaging.py"],
            "pmo": ["pmo-ui/src/components/Board.tsx"],
            "scripts": ["scripts/install.sh"],
        }
        assert not _detect_coupling(groups, "Update packaging and PMO board")


# ---------------------------------------------------------------------------
# Haiku team coordination tests
# ---------------------------------------------------------------------------

class TestHaikuTeamCoordination:
    """Tests for Haiku recommendations with coordination=team."""

    def test_applies_team_recommendation(self):
        """Haiku team recommendation should produce a team step."""
        reviewer = PlanReviewer()
        plan = _make_broad_plan()

        recommendations = {
            "splits": [{
                "phase_id": 2,
                "step_id": "2.1",
                "reason": "Coupled concerns need coordination",
                "coordination": "team",
                "groups": [
                    {"label": "engine", "files": ["executor.py"],
                     "description_hint": "Implement engine routing",
                     "depends_on_groups": []},
                    {"label": "runtime", "files": ["worker.py"],
                     "description_hint": "Implement runtime changes",
                     "depends_on_groups": ["engine"]},
                    {"label": "CLI", "files": ["execute.py"],
                     "description_hint": "Implement CLI output",
                     "depends_on_groups": ["engine"]},
                ],
            }],
            "dependencies": [],
            "warnings": [],
        }

        result = reviewer._apply_recommendations(plan, recommendations)
        assert result.teams_created == 1
        assert result.splits_applied == 0

        team_step = plan.phases[1].steps[0]
        assert team_step.agent_name == "team"
        assert len(team_step.team) == 3
        # All members should be the original agent type
        assert all(m.agent_name == "backend-engineer--python" for m in team_step.team)
        # First member is lead
        assert team_step.team[0].role == "lead"
        # Runtime and CLI members depend on engine
        engine_id = team_step.team[0].member_id
        assert engine_id in team_step.team[1].depends_on
        assert engine_id in team_step.team[2].depends_on
        # Team has synthesis
        assert team_step.synthesis is not None
        assert team_step.synthesis.strategy == "merge_files"

    def test_parallel_recommendation_still_works(self):
        """Haiku parallel recommendation should produce independent steps."""
        reviewer = PlanReviewer()
        plan = _make_broad_plan()

        recommendations = {
            "splits": [{
                "phase_id": 2,
                "step_id": "2.1",
                "reason": "Independent concerns",
                "coordination": "parallel",
                "groups": [
                    {"label": "docs", "files": ["docs/arch.md"],
                     "description_hint": "Update docs"},
                    {"label": "tests", "files": ["tests/test_x.py"],
                     "description_hint": "Add tests"},
                ],
            }],
            "dependencies": [],
            "warnings": [],
        }

        result = reviewer._apply_recommendations(plan, recommendations)
        assert result.splits_applied == 1
        assert result.teams_created == 0
        assert len(plan.phases[1].steps) == 2
        assert all(s.agent_name == "backend-engineer--python"
                    for s in plan.phases[1].steps)

    def test_defaults_to_parallel_when_coordination_missing(self):
        """When coordination field is absent, default to parallel."""
        reviewer = PlanReviewer()
        plan = _make_broad_plan()

        recommendations = {
            "splits": [{
                "phase_id": 2,
                "step_id": "2.1",
                "reason": "Broad step",
                "groups": [
                    {"label": "a", "files": ["a.py"],
                     "description_hint": "Do A"},
                    {"label": "b", "files": ["b.py"],
                     "description_hint": "Do B"},
                ],
            }],
            "dependencies": [],
            "warnings": [],
        }

        result = reviewer._apply_recommendations(plan, recommendations)
        assert result.splits_applied == 1
        assert result.teams_created == 0
