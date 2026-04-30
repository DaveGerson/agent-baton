"""Tests for ResearchStage — the optional pre-roster discovery pass."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.engine.planning.draft import PlanDraft
from agent_baton.core.engine.planning.stages.research import ResearchStage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_draft(
    task_summary: str = "Audit all components of the system",
    inferred_type: str = "audit",
    inferred_complexity: str = "medium",
    project_root: Path | None = None,
    agents: list[str] | None = None,
    phases: list[dict] | None = None,
) -> PlanDraft:
    draft = PlanDraft.from_inputs(
        task_summary,
        agents=agents,
        phases=phases,
        project_root=project_root,
    )
    draft.inferred_type = inferred_type
    draft.inferred_complexity = inferred_complexity
    return draft


def _make_services() -> Any:
    """Minimal services stub — ResearchStage doesn't use services."""
    return MagicMock()


def _headless_returning(concerns: list[dict]) -> MagicMock:
    """Return a HeadlessClaude mock whose run_sync yields the given JSON array."""
    hc = MagicMock()
    hc.is_available = True
    result = MagicMock()
    result.success = True
    result.output = json.dumps(concerns)
    hc.run_sync.return_value = result
    return hc


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

class TestResearchStageSkipConditions:
    def test_skips_for_light_complexity(self) -> None:
        stage = ResearchStage()
        draft = _make_draft(inferred_complexity="light")
        out = stage.run(draft, _make_services())
        assert out.research_concerns is None
        assert out.research_context is None

    def test_skips_for_bug_fix_type(self) -> None:
        stage = ResearchStage()
        draft = _make_draft(
            task_summary="Fix the login bug",
            inferred_type="bug-fix",
            inferred_complexity="medium",
        )
        out = stage.run(draft, _make_services())
        assert out.research_concerns is None

    def test_skips_for_test_type(self) -> None:
        stage = ResearchStage()
        draft = _make_draft(
            task_summary="Write tests for the auth module",
            inferred_type="test",
            inferred_complexity="medium",
        )
        out = stage.run(draft, _make_services())
        assert out.research_concerns is None

    def test_skips_when_headless_unavailable(self) -> None:
        stage = ResearchStage()
        # audit task qualifies for research but CLI is unavailable
        draft = _make_draft(
            task_summary="Audit all components of the system",
            inferred_type="audit",
        )
        with patch.object(stage, "_get_headless", return_value=None):
            out = stage.run(draft, _make_services())
        assert out.research_concerns is None
        assert out.research_context is None

    def test_skips_when_headless_import_fails(self) -> None:
        """_get_headless returns None when HeadlessClaude cannot be imported."""
        stage = ResearchStage()
        with patch(
            "agent_baton.core.engine.planning.stages.research.ResearchStage._get_headless",
            return_value=None,
        ):
            draft = _make_draft(inferred_type="audit")
            out = stage.run(draft, _make_services())
        assert out.research_concerns is None

    def test_narrows_scope_task_without_broad_keyword_skips(self) -> None:
        """A refactor task without broad-scope keywords should not trigger research."""
        stage = ResearchStage()
        draft = _make_draft(
            task_summary="Refactor the payment service",
            inferred_type="refactor",
            inferred_complexity="medium",
        )
        with patch.object(stage, "_get_headless", return_value=None) as mock_hc:
            out = stage.run(draft, _make_services())
        # _get_headless is never called because _should_run returns False
        mock_hc.assert_not_called()
        assert out.research_concerns is None


# ---------------------------------------------------------------------------
# Run conditions
# ---------------------------------------------------------------------------

class TestResearchStageRunConditions:
    def test_runs_for_audit_type(self) -> None:
        stage = ResearchStage()
        draft = _make_draft(
            task_summary="Audit all components of the system",
            inferred_type="audit",
        )
        hc = _headless_returning([
            {"marker": "1", "text": "Auth domain"},
            {"marker": "2", "text": "Payment domain"},
        ])
        with patch.object(stage, "_get_headless", return_value=hc):
            out = stage.run(draft, _make_services())
        assert out.research_concerns == [("1", "Auth domain"), ("2", "Payment domain")]

    def test_runs_when_broad_scope_keyword_present(self) -> None:
        stage = ResearchStage()
        draft = _make_draft(
            task_summary="Review all modules for security issues",
            inferred_type="refactor",
            inferred_complexity="medium",
        )
        hc = _headless_returning([{"marker": "A", "text": "Security scan"}])
        with patch.object(stage, "_get_headless", return_value=hc):
            out = stage.run(draft, _make_services())
        assert out.research_concerns == [("A", "Security scan")]


# ---------------------------------------------------------------------------
# Concern population
# ---------------------------------------------------------------------------

class TestResearchStagePopulatesConcerns:
    def test_populates_research_concerns_with_three_items(self) -> None:
        stage = ResearchStage()
        draft = _make_draft(inferred_type="audit")
        raw = [
            {"marker": "1", "text": "Core engine subsystem"},
            {"marker": "2", "text": "CLI interface"},
            {"marker": "3", "text": "REST API layer"},
        ]
        hc = _headless_returning(raw)
        with patch.object(stage, "_get_headless", return_value=hc):
            out = stage.run(draft, _make_services())

        assert out.research_concerns is not None
        assert len(out.research_concerns) == 3
        assert out.research_concerns[0] == ("1", "Core engine subsystem")
        assert out.research_concerns[1] == ("2", "CLI interface")
        assert out.research_concerns[2] == ("3", "REST API layer")

    def test_populates_research_context_string(self) -> None:
        stage = ResearchStage()
        draft = _make_draft(inferred_type="audit")
        hc = _headless_returning([
            {"marker": "1", "text": "Domain A"},
            {"marker": "2", "text": "Domain B"},
        ])
        with patch.object(stage, "_get_headless", return_value=hc):
            out = stage.run(draft, _make_services())

        assert out.research_context is not None
        assert "2" in out.research_context  # mentions count
        assert "Domain A" in out.research_context
        assert "Domain B" in out.research_context

    def test_empty_llm_response_leaves_draft_unchanged(self) -> None:
        stage = ResearchStage()
        draft = _make_draft(inferred_type="audit")
        hc = _headless_returning([])  # LLM returns empty array
        with patch.object(stage, "_get_headless", return_value=hc):
            out = stage.run(draft, _make_services())
        assert out.research_concerns is None
        assert out.research_context is None

    def test_failed_llm_call_leaves_draft_unchanged(self) -> None:
        stage = ResearchStage()
        draft = _make_draft(inferred_type="audit")
        hc = MagicMock()
        hc.is_available = True
        result = MagicMock()
        result.success = False
        result.error = "timeout"
        hc.run_sync.return_value = result
        with patch.object(stage, "_get_headless", return_value=hc):
            out = stage.run(draft, _make_services())
        assert out.research_concerns is None

    def test_malformed_json_leaves_draft_unchanged(self) -> None:
        stage = ResearchStage()
        draft = _make_draft(inferred_type="audit")
        hc = MagicMock()
        hc.is_available = True
        result = MagicMock()
        result.success = True
        result.output = "This is not JSON at all."
        hc.run_sync.return_value = result
        with patch.object(stage, "_get_headless", return_value=hc):
            out = stage.run(draft, _make_services())
        assert out.research_concerns is None

    def test_json_in_markdown_fences_is_parsed(self) -> None:
        stage = ResearchStage()
        fenced = (
            "```json\n"
            '[{"marker": "1", "text": "Auth"}, {"marker": "2", "text": "Billing"}]\n'
            "```"
        )
        result = ResearchStage._parse_response(fenced)
        assert result == [("1", "Auth"), ("2", "Billing")]


# ---------------------------------------------------------------------------
# End-to-end pipeline smoke (ResearchStage is a no-op when CLI absent)
# ---------------------------------------------------------------------------

class TestResearchStageInPipeline:
    def test_pipeline_still_produces_plan_with_research_stage_added(self) -> None:
        """Regression: adding ResearchStage must not break the pipeline."""
        from agent_baton.core.engine.planning.planner import IntelligentPlanner

        planner = IntelligentPlanner()
        plan = planner.create_plan("Add a hello-world endpoint")
        assert plan.task_id
        assert plan.phases

    def test_pipeline_stage_order_includes_research(self) -> None:
        from agent_baton.core.engine.planning.planner import _build_default_pipeline

        pipeline = _build_default_pipeline()
        names = [s.name for s in pipeline.stages]
        assert "research" in names
        # research must come after classification and before roster
        assert names.index("research") == names.index("classification") + 1
        assert names.index("research") < names.index("roster")

    def test_research_stage_implements_protocol(self) -> None:
        stage = ResearchStage()
        assert isinstance(stage.name, str) and stage.name == "research"
        assert callable(getattr(stage, "run", None))
