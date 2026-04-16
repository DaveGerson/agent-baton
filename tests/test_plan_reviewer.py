"""Tests for agent_baton.core.engine.plan_reviewer.PlanReviewer."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agent_baton.core.engine.plan_reviewer import (
    PlanReviewer,
    _cluster_by_directory,
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

    def test_splits_broad_single_step(self):
        """The core bug: a single step spanning 4+ files across 3+ dirs should split."""
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
        assert result.splits_applied >= 1
        # The implementation phase should now have multiple steps
        assert len(impl_phase.steps) > 1
        # All steps should use the same agent
        agents = {s.agent_name for s in impl_phase.steps}
        assert agents == {"backend-engineer--python"}
        # Steps should be parallel (no depends_on)
        for step in impl_phase.steps:
            assert step.depends_on == []

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
