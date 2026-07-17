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
