"""Tests for phase-summary bead synthesis and chain rendering."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from agent_baton.core.intel.phase_summary import (
    PHASE_SUMMARY_MAX_CHARS,
    PHASE_SUMMARY_MAX_CHAIN,
    synthesize_phase_summary,
    collect_phase_summary_chain,
    render_phase_summary_section,
)
from agent_baton.models.bead import Bead


def _make_step_result(step_id: str, outcome: str = "", files: list[str] | None = None):
    return MagicMock(
        step_id=step_id,
        status="complete",
        outcome=outcome,
        files_changed=files or [],
    )


def _make_bead(bead_type: str, content: str, step_id: str = "1.1", status: str = "open"):
    return Bead(
        bead_id="bd-test",
        task_id="t-1",
        step_id=step_id,
        agent_name="architect",
        bead_type=bead_type,
        content=content,
        status=status,
    )


class TestSynthesizePhaseSummary:
    def test_basic_summary(self):
        results = [
            _make_step_result("1.1", "Implemented base models", ["agent_baton/models/foo.py"]),
            _make_step_result("1.2", "Added routes", ["agent_baton/api/routes.py"]),
        ]
        bead = synthesize_phase_summary(
            phase_id=1,
            phase_name="Foundation",
            step_results=results,
            decision_beads=[],
            warning_beads=[],
            task_id="t-1",
            bead_count=0,
        )
        assert bead.bead_type == "outcome"
        assert bead.scope == "phase"
        assert "phase-summary" in bead.tags
        assert "phase-1" in bead.tags
        assert "Phase 1: Foundation" in bead.content
        assert "agent_baton/models/foo.py" in bead.content
        assert "1.1:" in bead.content
        assert "1.2:" in bead.content

    def test_includes_decisions_and_warnings(self):
        results = [_make_step_result("1.1", "Done")]
        decisions = [_make_bead("decision", "Use SQLAlchemy 2.0")]
        warnings = [_make_bead("warning", "Port 5433 conflict")]
        bead = synthesize_phase_summary(
            phase_id=1,
            phase_name="Setup",
            step_results=results,
            decision_beads=decisions,
            warning_beads=warnings,
            task_id="t-1",
            bead_count=5,
        )
        assert "SQLAlchemy 2.0" in bead.content
        assert "Port 5433" in bead.content

    def test_content_capped(self):
        results = [
            _make_step_result(f"1.{i}", "A" * 200, [f"file{i}.py"])
            for i in range(30)
        ]
        bead = synthesize_phase_summary(
            phase_id=1,
            phase_name="BigPhase",
            step_results=results,
            decision_beads=[],
            warning_beads=[],
            task_id="t-1",
            bead_count=0,
        )
        assert len(bead.content) <= PHASE_SUMMARY_MAX_CHARS

    def test_affected_files_set(self):
        results = [
            _make_step_result("1.1", "Done", ["a.py", "b.py"]),
            _make_step_result("1.2", "Done", ["b.py", "c.py"]),
        ]
        bead = synthesize_phase_summary(
            phase_id=1,
            phase_name="Test",
            step_results=results,
            decision_beads=[],
            warning_beads=[],
            task_id="t-1",
            bead_count=0,
        )
        assert set(bead.affected_files) == {"a.py", "b.py", "c.py"}

    def test_empty_results(self):
        bead = synthesize_phase_summary(
            phase_id=1,
            phase_name="Empty",
            step_results=[],
            decision_beads=[],
            warning_beads=[],
            task_id="t-1",
            bead_count=0,
        )
        assert "Phase 1: Empty" in bead.content


class TestCollectPhaseSummaryChain:
    def _make_summary_bead(self, phase_id: int):
        return Bead(
            bead_id=f"bd-p{phase_id}",
            task_id="t-1",
            step_id=f"phase-{phase_id}",
            agent_name="engine",
            bead_type="outcome",
            content=f"Phase {phase_id}: Test",
            scope="phase",
            tags=["phase-summary", f"phase-{phase_id}"],
        )

    def test_returns_prior_phases(self):
        store = MagicMock()
        store.query.return_value = [
            self._make_summary_bead(1),
            self._make_summary_bead(2),
            self._make_summary_bead(3),
        ]
        chain = collect_phase_summary_chain(store, "t-1", current_phase_id=4)
        assert len(chain) == 3
        assert chain[0].tags == ["phase-summary", "phase-1"]

    def test_respects_max_chain(self):
        store = MagicMock()
        store.query.return_value = [
            self._make_summary_bead(i) for i in range(1, 6)
        ]
        chain = collect_phase_summary_chain(store, "t-1", current_phase_id=6, max_chain=3)
        assert len(chain) == 3
        assert "phase-3" in chain[0].tags
        assert "phase-5" in chain[2].tags

    def test_excludes_current_phase(self):
        store = MagicMock()
        store.query.return_value = [
            self._make_summary_bead(1),
            self._make_summary_bead(2),
        ]
        chain = collect_phase_summary_chain(store, "t-1", current_phase_id=2)
        assert len(chain) == 1
        assert "phase-1" in chain[0].tags

    def test_empty_store(self):
        store = MagicMock()
        store.query.return_value = []
        chain = collect_phase_summary_chain(store, "t-1", current_phase_id=1)
        assert chain == []

    def test_filters_non_summary_beads(self):
        store = MagicMock()
        non_summary = Bead(
            bead_id="bd-other",
            task_id="t-1",
            step_id="1.1",
            agent_name="architect",
            bead_type="outcome",
            content="step outcome",
            scope="step",
            tags=[],
        )
        store.query.return_value = [
            non_summary,
            self._make_summary_bead(1),
        ]
        chain = collect_phase_summary_chain(store, "t-1", current_phase_id=2)
        assert len(chain) == 1


class TestRenderPhaseSummarySection:
    def test_renders_chain(self):
        beads = [
            Bead(
                bead_id="bd-1",
                task_id="t-1",
                step_id="phase-1",
                agent_name="engine",
                bead_type="outcome",
                content="Phase 1: Foundation\nFiles: a.py, b.py\nSteps:\n  1.1: done",
                scope="phase",
                tags=["phase-summary", "phase-1"],
            ),
        ]
        section = render_phase_summary_section(beads)
        assert "## Prior Phase Context" in section
        assert "### Phase 1: Foundation" in section
        assert "a.py" in section

    def test_empty_chain(self):
        assert render_phase_summary_section([]) == ""
