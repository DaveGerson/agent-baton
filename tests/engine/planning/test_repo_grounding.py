"""Tests for ``planning.utils.repo_grounding`` — repository-grounded
decomposition for heavy-complexity tasks (Phase 6, step 6.1).

Covers:
1. ``gather_repo_findings`` is a clean no-op (all-empty, ``available=
   False``) when there is no repository to scan.
2. ``gather_repo_findings`` matches concrete files/tests/symbols from a
   synthetic repository against task-summary keywords.
3. ``ground_phases_in_repository`` populates concrete context_files /
   allowed_paths / deliverables / expected_outcome / task_description on
   a step, without overwriting explicit values already set.
4. Cross-phase ``depends_on`` wiring based on shared grounded evidence.
5. Deterministic fallback: no repository evidence -> no mutation, so the
   existing generic-template behavior is preserved unchanged.
"""
from __future__ import annotations

from pathlib import Path

from agent_baton.core.engine.planning.utils.repo_grounding import (
    RepoFindings,
    gather_repo_findings,
    ground_phases_in_repository,
)
from agent_baton.models.execution import PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# gather_repo_findings
# ---------------------------------------------------------------------------


class TestGatherRepoFindings:
    def test_no_project_root_is_unavailable(self) -> None:
        findings = gather_repo_findings(None, "Add reporting support")
        assert findings.available is False
        assert findings.matched_files == []
        assert findings.matched_symbols == []

    def test_nonexistent_project_root_is_unavailable(self, tmp_path: Path) -> None:
        findings = gather_repo_findings(tmp_path / "does-not-exist", "Add reporting")
        assert findings.available is False

    def test_matches_file_and_symbol_by_keyword_overlap(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "reporting.py").write_text(
            "def generate_report():\n    return 'report'\n",
            encoding="utf-8",
        )
        (app_dir / "unrelated.py").write_text("def noop():\n    pass\n", encoding="utf-8")

        findings = gather_repo_findings(
            tmp_path, "Add a generate_report endpoint to the reporting module"
        )
        assert findings.available is True
        assert any(p.endswith("reporting.py") for p in findings.matched_files)
        assert not any(p.endswith("unrelated.py") for p in findings.matched_files)
        assert ("app/reporting.py", "generate_report") in findings.matched_symbols

    def test_matches_test_files(self, tmp_path: Path) -> None:
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_reporting.py").write_text(
            "def test_generate_report():\n    pass\n", encoding="utf-8"
        )
        (tmp_path / "reporting.py").write_text(
            "def generate_report():\n    pass\n", encoding="utf-8"
        )

        findings = gather_repo_findings(tmp_path, "Fix the reporting generate_report bug")
        assert any("test_reporting.py" in p for p in findings.matched_tests)

    def test_extracted_path_confirmed_on_disk_is_matched(self, tmp_path: Path) -> None:
        (tmp_path / "widget.py").write_text("x = 1\n", encoding="utf-8")
        findings = gather_repo_findings(tmp_path, "Update widget.py to add a new field")
        assert "widget.py" in findings.matched_files

    def test_ignored_directories_are_skipped(self, tmp_path: Path) -> None:
        ignored = tmp_path / "node_modules"
        ignored.mkdir()
        (ignored / "widget.py").write_text("x = 1\n", encoding="utf-8")
        findings = gather_repo_findings(tmp_path, "Update the widget module")
        assert not any("node_modules" in p for p in findings.matched_files)


# ---------------------------------------------------------------------------
# ground_phases_in_repository
# ---------------------------------------------------------------------------


def _step(step_id: str, agent_name: str, task_description: str, step_type: str = "developing") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent_name,
        task_description=task_description,
        step_type=step_type,
    )


class TestGroundPhasesInRepository:
    def test_noop_when_findings_unavailable(self) -> None:
        step = _step("1.1", "backend-engineer", "Implement: widget support (as backend-engineer)")
        phases = [PlanPhase(phase_id=1, name="Implement", steps=[step])]
        ground_phases_in_repository(phases, "Add widget support", RepoFindings(available=False))
        # Deterministic fallback: nothing mutated.
        assert step.context_files == []
        assert step.allowed_paths == []
        assert step.deliverables == []
        assert step.expected_outcome == ""
        assert step.task_description == "Implement: widget support (as backend-engineer)"

    def test_grounds_step_with_concrete_evidence(self, tmp_path: Path) -> None:
        (tmp_path / "widget.py").write_text(
            "def render_widget():\n    pass\n", encoding="utf-8"
        )
        task_summary = "Add render_widget support to the widget module"
        findings = gather_repo_findings(tmp_path, task_summary)

        step = _step("1.1", "backend-engineer", "Implement: widget support")
        phases = [PlanPhase(phase_id=1, name="Implement", steps=[step])]
        ground_phases_in_repository(phases, task_summary, findings)

        assert "widget.py" in step.context_files
        assert step.allowed_paths, "allowed_paths should be derived from grounded evidence"
        assert step.deliverables, "deliverables should be concrete, not empty"
        assert any("widget.py" in d for d in step.deliverables)
        assert "Repository scope" in step.task_description
        assert "widget.py" in step.task_description
        assert step.expected_outcome != ""
        assert "widget.py" in step.expected_outcome or "render_widget" in step.expected_outcome

    def test_does_not_override_explicit_fields(self, tmp_path: Path) -> None:
        (tmp_path / "widget.py").write_text("def render_widget():\n    pass\n", encoding="utf-8")
        task_summary = "Add render_widget support to the widget module"
        findings = gather_repo_findings(tmp_path, task_summary)

        step = _step("1.1", "backend-engineer", "Implement: widget support")
        step.allowed_paths = ["explicit/area"]
        step.deliverables = ["Explicit deliverable"]
        step.expected_outcome = "Explicit outcome"
        phases = [PlanPhase(phase_id=1, name="Implement", steps=[step])]
        ground_phases_in_repository(phases, task_summary, findings)

        assert step.allowed_paths == ["explicit/area"]
        assert step.deliverables == ["Explicit deliverable"]
        assert step.expected_outcome == "Explicit outcome"

    def test_idempotent_grounding_suffix_not_duplicated(self, tmp_path: Path) -> None:
        (tmp_path / "widget.py").write_text("def render_widget():\n    pass\n", encoding="utf-8")
        task_summary = "Add render_widget support to the widget module"
        findings = gather_repo_findings(tmp_path, task_summary)

        step = _step("1.1", "backend-engineer", "Implement: widget support")
        phases = [PlanPhase(phase_id=1, name="Implement", steps=[step])]
        ground_phases_in_repository(phases, task_summary, findings)
        first_description = step.task_description
        ground_phases_in_repository(phases, task_summary, findings)
        assert step.task_description == first_description

    def test_cross_phase_dependency_wired_on_shared_file(self, tmp_path: Path) -> None:
        (tmp_path / "widget.py").write_text("def render_widget():\n    pass\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_widget.py").write_text(
            "def test_render_widget():\n    pass\n", encoding="utf-8"
        )
        task_summary = "Implement render_widget in the widget module and test it"
        findings = gather_repo_findings(tmp_path, task_summary)

        impl_step = _step("1.1", "backend-engineer", "Implement widget rendering")
        test_step = _step(
            "2.1", "test-engineer", "Verify widget rendering works", step_type="testing"
        )
        phases = [
            PlanPhase(phase_id=1, name="Implement", steps=[impl_step]),
            PlanPhase(phase_id=2, name="Test", steps=[test_step]),
        ]
        ground_phases_in_repository(phases, task_summary, findings)

        # Both steps grounded on widget.py -> the later phase's step must
        # depend on the earlier phase's step that first claimed it.
        assert set(impl_step.context_files) & set(test_step.context_files), (
            "test setup should make both steps share at least one grounded file"
        )
        assert "1.1" in test_step.depends_on
        assert "2.1" not in impl_step.depends_on


# ---------------------------------------------------------------------------
# Full-pipeline snapshot: create_plan() for a heavy task against a real,
# synthetic repository (Phase 6, 6.4). The unit-level tests above exercise
# gather_repo_findings/ground_phases_in_repository directly; this exercises
# the whole seven-stage pipeline (DecompositionStage -> ... -> assembly ->
# ValidationStage) the way `baton plan` actually calls it, and pins that the
# assembled plan's steps carry concrete, repository-grounded content -- no
# placeholder markers, no bare "(as <agent>)" template text left unfilled --
# for every step ValidationStage's shallow-decomposition check would flag.
# ---------------------------------------------------------------------------


class TestFullPipelineHeavyPlanGroundingSnapshot:
    @staticmethod
    def _build_repo(tmp_path: Path) -> None:
        # Basenames must overlap the task summary's keyword tokens
        # (gather_repo_findings matches on _basename_tokens(), not full
        # path) -- "report_service.py" tokenizes to {"report", "service"}.
        app_dir = tmp_path / "app" / "reporting"
        app_dir.mkdir(parents=True)
        (app_dir / "report_service.py").write_text(
            "def generate_report():\n    return 'report'\n",
            encoding="utf-8",
        )
        tests_dir = tmp_path / "tests" / "reporting"
        tests_dir.mkdir(parents=True)
        (tests_dir / "test_report_service.py").write_text(
            "def test_generate_report():\n    pass\n",
            encoding="utf-8",
        )

    @staticmethod
    def _planner(tmp_path: Path):
        from agent_baton.core.engine.planner import IntelligentPlanner
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "backend-engineer.md").write_text(
            "---\nname: backend-engineer\ndescription: backend specialist.\n"
            "model: sonnet\npermissionMode: default\ntools: Read, Write\n---\n",
            encoding="utf-8",
        )
        planner = IntelligentPlanner(team_context_root=tmp_path / "team-context")
        reg = AgentRegistry()
        reg.load_directory(agents_dir)
        planner._registry = reg
        planner._router = AgentRouter(reg)
        return planner

    def test_heavy_plan_is_grounded_not_generic(self, tmp_path: Path) -> None:
        self._build_repo(tmp_path)
        planner = self._planner(tmp_path)

        plan = planner.create_plan(
            "Add a generate_report endpoint to the reporting module, "
            "with tests",
            complexity="heavy",
            project_root=tmp_path,
            phases=[{"name": "Implement", "agents": ["backend-engineer"]}],
        )

        assert plan.complexity == "heavy"
        assert plan.phases, "heavy plan must have at least one phase"

        _PLACEHOLDER_MARKERS = ("tbd", "todo", "placeholder", "lorem ipsum")
        for phase in plan.phases:
            for step in phase.steps:
                haystack = (
                    step.task_description + " " + step.expected_outcome
                ).lower()
                for marker in _PLACEHOLDER_MARKERS:
                    assert marker not in haystack, (
                        f"step {step.step_id} carries placeholder marker "
                        f"{marker!r}: {haystack!r}"
                    )

        # At least one step must show concrete, repository-grounded
        # evidence -- the whole point of repo_grounding.py -- not just the
        # generic per-agent/per-phase template text.
        grounded_steps = [
            step
            for phase in plan.phases
            for step in phase.steps
            if any("report_service.py" in f for f in step.context_files)
        ]
        assert grounded_steps, (
            "expected at least one step grounded on app/reporting/report_service.py; "
            f"got phases: {[(p.name, [s.task_description for s in p.steps]) for p in plan.phases]}"
        )
        grounded = grounded_steps[0]
        assert grounded.allowed_paths, "grounded step must carry concrete allowed_paths"
        assert grounded.deliverables, "grounded step must carry concrete deliverables"
        assert "Repository scope" in grounded.task_description
        assert grounded.expected_outcome != ""
