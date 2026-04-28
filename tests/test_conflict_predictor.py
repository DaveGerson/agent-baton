"""Tests for R3.7 plan-time file conflict prediction."""
from __future__ import annotations

import pytest

from agent_baton.core.release.conflict_predictor import ConflictPredictor
from agent_baton.models.conflict_prediction import (
    CONFLICT_TYPES,
    FileConflict,
    PlanConflictReport,
)
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _step(
    step_id: str,
    *,
    agent: str = "implementer",
    allowed_paths: list[str] | None = None,
    context_files: list[str] | None = None,
    deliverables: list[str] | None = None,
    depends_on: list[str] | None = None,
) -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent,
        task_description=f"step {step_id}",
        allowed_paths=allowed_paths or [],
        context_files=context_files or [],
        deliverables=deliverables or [],
        depends_on=depends_on or [],
    )


def _plan(*phases: PlanPhase, task_id: str = "T-1") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="test plan",
        phases=list(phases),
    )


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

class TestFileConflictModel:
    def test_rejects_unknown_conflict_type(self) -> None:
        with pytest.raises(ValueError):
            FileConflict(
                step_a_id="1.1",
                step_b_id="1.2",
                file_path="x.py",
                conflict_type="bogus",
                confidence=0.5,
            )

    def test_rejects_out_of_range_confidence(self) -> None:
        with pytest.raises(ValueError):
            FileConflict(
                step_a_id="1.1",
                step_b_id="1.2",
                file_path="x.py",
                conflict_type="write_write",
                confidence=1.5,
            )

    def test_known_conflict_types(self) -> None:
        assert "write_write" in CONFLICT_TYPES
        assert "read_write" in CONFLICT_TYPES


class TestSerializationRoundtrip:
    def test_roundtrip_report_with_conflicts(self) -> None:
        report = PlanConflictReport(
            plan_id="T-1",
            total_steps_analyzed=4,
            parallel_groups_analyzed=1,
            conflicts=[
                FileConflict(
                    step_a_id="1.1",
                    step_b_id="1.2",
                    file_path="src/a.py",
                    conflict_type="write_write",
                    confidence=0.9,
                    reason="both write",
                ),
            ],
        )
        out = PlanConflictReport.from_dict(report.to_dict())
        assert out.plan_id == "T-1"
        assert out.total_steps_analyzed == 4
        assert out.parallel_groups_analyzed == 1
        assert len(out.conflicts) == 1
        assert out.conflicts[0].file_path == "src/a.py"
        assert out.conflicts[0].confidence == 0.9
        assert out.conflicts[0].reason == "both write"

    def test_roundtrip_empty_report(self) -> None:
        report = PlanConflictReport(plan_id="T-2")
        out = PlanConflictReport.from_dict(report.to_dict())
        assert out.plan_id == "T-2"
        assert out.conflicts == []
        assert out.has_conflicts is False


# ---------------------------------------------------------------------------
# Predictor — core detection
# ---------------------------------------------------------------------------

class TestWriteWriteDetection:
    def test_two_steps_write_same_file(self) -> None:
        phase = PlanPhase(
            phase_id=1,
            name="impl",
            steps=[
                _step("1.1", allowed_paths=["src/a.py"]),
                _step("1.2", allowed_paths=["src/a.py"]),
            ],
        )
        report = ConflictPredictor(_plan(phase)).predict()

        assert report.parallel_groups_analyzed == 1
        assert report.total_steps_analyzed == 2
        assert len(report.conflicts) == 1
        c = report.conflicts[0]
        assert c.conflict_type == "write_write"
        assert c.file_path == "src/a.py"
        assert c.confidence == pytest.approx(0.9)
        assert {c.step_a_id, c.step_b_id} == {"1.1", "1.2"}

    def test_deliverables_count_as_writes(self) -> None:
        phase = PlanPhase(
            phase_id=1,
            name="impl",
            steps=[
                _step("1.1", deliverables=["src/shared.py"]),
                _step("1.2", allowed_paths=["src/shared.py"]),
            ],
        )
        report = ConflictPredictor(_plan(phase)).predict()
        assert any(
            c.conflict_type == "write_write" and c.file_path == "src/shared.py"
            for c in report.conflicts
        )


class TestReadWriteDetection:
    def test_one_writes_other_reads(self) -> None:
        phase = PlanPhase(
            phase_id=1,
            name="impl",
            steps=[
                _step("1.1", allowed_paths=["src/a.py"]),
                _step("1.2", context_files=["src/a.py"], allowed_paths=["src/b.py"]),
            ],
        )
        report = ConflictPredictor(_plan(phase)).predict()
        rw = [c for c in report.conflicts if c.conflict_type == "read_write"]
        assert len(rw) == 1
        assert rw[0].file_path == "src/a.py"
        assert rw[0].confidence == pytest.approx(0.7)

    def test_does_not_double_count_when_both_read_only(self) -> None:
        # Both steps just *read* the same file; no write — no conflict.
        phase = PlanPhase(
            phase_id=1,
            name="impl",
            steps=[
                _step("1.1", context_files=["src/a.py"], allowed_paths=["src/x.py"]),
                _step("1.2", context_files=["src/a.py"], allowed_paths=["src/y.py"]),
            ],
        )
        report = ConflictPredictor(_plan(phase)).predict()
        assert report.conflicts == []


class TestBroadAllowlistDetection:
    def test_broad_directory_allowlist_overlaps_specific_write(self) -> None:
        phase = PlanPhase(
            phase_id=1,
            name="impl",
            steps=[
                _step("1.1", allowed_paths=["src/"]),                # broad
                _step("1.2", allowed_paths=["src/feature.py"]),     # specific
            ],
        )
        report = ConflictPredictor(_plan(phase)).predict()
        ww = [c for c in report.conflicts if c.conflict_type == "write_write"]
        assert ww, "expected a broad/specific write_write conflict"
        # Confidence for the broad/specific case is 0.6, not 0.9.
        assert any(c.confidence == pytest.approx(0.6) for c in ww)

    def test_any_allowlist_marks_directory_overlap(self) -> None:
        phase = PlanPhase(
            phase_id=1,
            name="impl",
            steps=[
                _step("1.1", allowed_paths=["any"]),
                _step("1.2", allowed_paths=["src/feature.py"]),
            ],
        )
        report = ConflictPredictor(_plan(phase)).predict()
        assert any(c.conflict_type == "write_write" for c in report.conflicts)


# ---------------------------------------------------------------------------
# Predictor — happy paths and edge cases
# ---------------------------------------------------------------------------

class TestNoConflictHappyPath:
    def test_disjoint_paths_no_conflicts(self) -> None:
        phase = PlanPhase(
            phase_id=1,
            name="impl",
            steps=[
                _step("1.1", allowed_paths=["src/a.py"]),
                _step("1.2", allowed_paths=["src/b.py"]),
                _step("1.3", allowed_paths=["src/c.py"]),
            ],
        )
        report = ConflictPredictor(_plan(phase)).predict()
        assert report.conflicts == []
        assert report.parallel_groups_analyzed == 1
        assert report.total_steps_analyzed == 3


class TestMultiStepParallelGroup:
    def test_pairwise_detection_in_three_step_group(self) -> None:
        phase = PlanPhase(
            phase_id=1,
            name="impl",
            steps=[
                _step("1.1", allowed_paths=["src/a.py"]),
                _step("1.2", allowed_paths=["src/a.py"]),
                _step("1.3", allowed_paths=["src/a.py"]),
            ],
        )
        report = ConflictPredictor(_plan(phase)).predict()
        # C(3,2) = 3 pairs all writing the same file.
        assert len(report.conflicts) == 3
        pairs = {(c.step_a_id, c.step_b_id) for c in report.conflicts}
        assert pairs == {("1.1", "1.2"), ("1.1", "1.3"), ("1.2", "1.3")}


class TestNoParallelism:
    def test_sequential_phase_yields_zero_conflicts(self) -> None:
        # depends_on chain serializes the steps — no parallelism, no conflict.
        phase = PlanPhase(
            phase_id=1,
            name="impl",
            steps=[
                _step("1.1", allowed_paths=["src/a.py"]),
                _step("1.2", allowed_paths=["src/a.py"], depends_on=["1.1"]),
            ],
        )
        report = ConflictPredictor(_plan(phase)).predict()
        assert report.conflicts == []
        assert report.parallel_groups_analyzed == 0

    def test_single_step_phase_yields_zero_conflicts(self) -> None:
        phase = PlanPhase(
            phase_id=1,
            name="impl",
            steps=[_step("1.1", allowed_paths=["src/a.py"])],
        )
        report = ConflictPredictor(_plan(phase)).predict()
        assert report.conflicts == []
        assert report.parallel_groups_analyzed == 0
        assert report.total_steps_analyzed == 1


class TestCrossPhaseIsolation:
    def test_steps_in_different_phases_are_not_compared(self) -> None:
        p1 = PlanPhase(phase_id=1, name="a", steps=[_step("1.1", allowed_paths=["src/a.py"])])
        p2 = PlanPhase(phase_id=2, name="b", steps=[_step("2.1", allowed_paths=["src/a.py"])])
        report = ConflictPredictor(_plan(p1, p2)).predict()
        assert report.conflicts == []
        assert report.parallel_groups_analyzed == 0


# ---------------------------------------------------------------------------
# Summarize output
# ---------------------------------------------------------------------------

class TestSummarize:
    def test_summarize_empty_report(self) -> None:
        report = PlanConflictReport(plan_id="T-1")
        out = ConflictPredictor.summarize(report)
        assert "no" in out.lower() or "0" in out
        assert "T-1" in out

    def test_summarize_markdown_table(self) -> None:
        report = PlanConflictReport(
            plan_id="T-1",
            total_steps_analyzed=2,
            parallel_groups_analyzed=1,
            conflicts=[
                FileConflict(
                    step_a_id="1.1",
                    step_b_id="1.2",
                    file_path="src/a.py",
                    conflict_type="write_write",
                    confidence=0.9,
                    reason="both write src/a.py",
                ),
            ],
        )
        out = ConflictPredictor.summarize(report)
        # Markdown table headers + separator + a data row
        assert "|" in out
        assert "Step A" in out
        assert "Step B" in out
        assert "File" in out
        assert "Type" in out
        assert "Confidence" in out
        assert "1.1" in out
        assert "1.2" in out
        assert "src/a.py" in out
        assert "write_write" in out
