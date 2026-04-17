"""Tests for compact stdout output of ``baton plan --save``.

Covers:
- ``--save`` without ``--verbose``: emits <=5 lines, no plan.to_markdown() content
- ``--save --verbose``: emits the full plan markdown
- ``--save --explain``: writes explanation.md sidecar and prints pointer line
"""
from __future__ import annotations

import argparse
import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution import plan_cmd
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_plan(task_id: str = "2026-01-01-terse-test-aabb0011") -> MachinePlan:
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer--python",
        task_description="Implement the thing",
        model="sonnet",
        depends_on=[],
        deliverables=[],
        allowed_paths=[],
        blocked_paths=[],
        context_files=[],
    )
    phase = PlanPhase(
        phase_id=1,
        name="Implement",
        steps=[step],
        approval_required=False,
    )
    return MachinePlan(
        task_id=task_id,
        task_summary="Terse output test",
        risk_level="LOW",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[phase],
        shared_context="",
        pattern_source=None,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _make_args(
    *,
    summary: str = "do the thing",
    save: bool = True,
    explain: bool = False,
    json_flag: bool = False,
    verbose: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        summary=summary,
        save=save,
        explain=explain,
        json=json_flag,
        verbose=verbose,
        import_path=None,
        template=False,
        task_type=None,
        agents=None,
        project=None,
        knowledge=[],
        knowledge_pack=[],
        intervention="low",
        model=None,
        complexity=None,
    )


def _run_handler(
    args: argparse.Namespace,
    plan: MachinePlan,
    ctx_dir: Path,
    capsys: pytest.CaptureFixture,
) -> str:
    """Run handler() with all heavy deps mocked out; return captured stdout."""
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = plan
    mock_planner.explain_plan.return_value = "Explanation text here."

    patches = [
        patch("agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner", return_value=mock_planner),
        patch("agent_baton.cli.commands.execution.plan_cmd.KnowledgeRegistry", return_value=MagicMock()),
        patch("agent_baton.cli.commands.execution.plan_cmd.RetrospectiveEngine", return_value=MagicMock()),
        patch("agent_baton.cli.commands.execution.plan_cmd.DataClassifier", return_value=MagicMock()),
        patch("agent_baton.cli.commands.execution.plan_cmd.PolicyEngine", return_value=MagicMock()),
        patch("agent_baton.core.orchestration.context.ContextManager", return_value=MagicMock()),
        patch("agent_baton.cli.commands.execution.plan_cmd._persist_plan_to_db", MagicMock()),
        patch.object(
            Path,
            "resolve",
            lambda self: ctx_dir if str(self) == ".claude/team-context" else Path(str(self)),
        ),
    ]

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        plan_cmd.handler(args)

    return capsys.readouterr().out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSaveTerseOutput:
    """--save without --verbose must emit a compact summary only."""

    def test_line_count_at_most_five(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _make_minimal_plan()
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)
        args = _make_args(save=True, verbose=False)

        stdout = _run_handler(args, plan, ctx_dir, capsys)

        non_empty_lines = [ln for ln in stdout.splitlines() if ln.strip()]
        assert len(non_empty_lines) <= 5, (
            f"Expected <=5 non-empty lines, got {len(non_empty_lines)}:\n{stdout}"
        )

    def test_does_not_contain_plan_markdown(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _make_minimal_plan()
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)
        args = _make_args(save=True, verbose=False)

        stdout = _run_handler(args, plan, ctx_dir, capsys)

        markdown = plan.to_markdown()
        # The summary line includes task_id but not the full markdown body.
        # Check that at least one markdown-specific heading is absent.
        assert "## Phase" not in stdout, "Full markdown phase headings must not appear in terse output"
        assert markdown not in stdout, "Full to_markdown() output must not appear in terse output"

    def test_contains_plan_saved_pointer(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _make_minimal_plan()
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)
        args = _make_args(save=True, verbose=False)

        stdout = _run_handler(args, plan, ctx_dir, capsys)

        assert "Plan saved:" in stdout
        assert "Plan markdown:" in stdout

    def test_contains_task_metadata(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _make_minimal_plan()
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)
        args = _make_args(save=True, verbose=False)

        stdout = _run_handler(args, plan, ctx_dir, capsys)

        assert plan.task_id in stdout
        assert plan.risk_level in stdout
        assert plan.budget_tier in stdout

    def test_contains_next_hint(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _make_minimal_plan()
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)
        args = _make_args(save=True, verbose=False)

        stdout = _run_handler(args, plan, ctx_dir, capsys)

        assert "baton execute start" in stdout


class TestSaveVerboseOutput:
    """--save --verbose must emit the full plan markdown."""

    def test_full_markdown_present(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _make_minimal_plan()
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)
        args = _make_args(save=True, verbose=True)

        stdout = _run_handler(args, plan, ctx_dir, capsys)

        assert plan.to_markdown() in stdout

    def test_more_than_five_lines(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _make_minimal_plan()
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)
        args = _make_args(save=True, verbose=True)

        stdout = _run_handler(args, plan, ctx_dir, capsys)

        non_empty_lines = [ln for ln in stdout.splitlines() if ln.strip()]
        assert len(non_empty_lines) > 5


class TestSaveExplainOutput:
    """--save --explain must write explanation.md and print its path."""

    def test_explanation_file_written(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _make_minimal_plan()
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)
        args = _make_args(save=True, explain=True)

        _run_handler(args, plan, ctx_dir, capsys)

        explanation_file = ctx_dir / "explanation.md"
        assert explanation_file.exists(), "explanation.md must be written to ctx_dir"
        assert "Explanation text here." in explanation_file.read_text(encoding="utf-8")

    def test_stdout_contains_explanation_pointer(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _make_minimal_plan()
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)
        args = _make_args(save=True, explain=True)

        stdout = _run_handler(args, plan, ctx_dir, capsys)

        assert "Plan explanation:" in stdout

    def test_explain_output_is_compact(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """--save --explain should still be compact (no full markdown body)."""
        plan = _make_minimal_plan()
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)
        args = _make_args(save=True, explain=True)

        stdout = _run_handler(args, plan, ctx_dir, capsys)

        assert "## Phase" not in stdout


class TestNoSavePreservesMarkdown:
    """Without --save, stdout must still be the full plan markdown (unchanged behavior)."""

    def test_no_save_emits_markdown(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        plan = _make_minimal_plan()
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)
        args = _make_args(save=False, verbose=False)

        stdout = _run_handler(args, plan, ctx_dir, capsys)

        assert plan.to_markdown() in stdout
