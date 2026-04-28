"""Tests for ``baton plan --dry-run``.

Covers:
- ``--dry-run`` + ``--save`` together -> error exit + helpful message.
- ``--dry-run`` produces a forecast block matching the schema
  (header line, columnar step table, gate block when gates exist,
  cost line, wall-clock line).
- ``--dry-run`` does NOT create or modify any files in
  ``.claude/team-context/``.
- Cost forecast accumulates: per-step token sum equals total; total
  cost matches MODEL_PRICING math.
- Forecast respects the model field on each step (the planner is
  responsible for honouring ``--model``; we just verify the rendered
  forecast reflects the per-step ``model``).
"""
from __future__ import annotations

import argparse
import contextlib
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution import plan_cmd
from agent_baton.core.engine.cost_estimator import MODEL_PRICING
from agent_baton.models.execution import (
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plan(task_id: str = "2026-04-25-dryrun-test-cafef00d") -> MachinePlan:
    """A two-phase plan with a mix of opus and sonnet, plus two gates."""
    return MachinePlan(
        task_id=task_id,
        task_summary="dry-run preview test",
        risk_level="MEDIUM",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Design",
                steps=[PlanStep(step_id="1.1", agent_name="architect",
                                task_description="Design the thing", model="opus")],
                gate=PlanGate(gate_type="build", command="echo ok",
                              description="Smoke check"),
            ),
            PlanPhase(
                phase_id=2,
                name="Implement",
                steps=[PlanStep(step_id="2.1", agent_name="backend-engineer--python",
                                task_description="Implement", model="sonnet")],
                gate=PlanGate(gate_type="test", command="pytest tests/foo/",
                              description="Run tests"),
            ),
        ],
        detected_stack="python",
        created_at="2026-04-25T00:00:00+00:00",
    )


def _make_args(
    *,
    dry_run: bool = False,
    save: bool = False,
    model: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        summary="do something",
        save=save,
        dry_run=dry_run,
        explain=False,
        json=False,
        verbose=False,
        import_path=None,
        template=False,
        task_type=None,
        agents=None,
        project=None,
        knowledge=[],
        knowledge_pack=[],
        intervention="low",
        model=model,
        complexity=None,
        save_as_template=None,
        from_template=None,
        skip_init=False,
    )


def _run_handler(
    args: argparse.Namespace,
    plan: MachinePlan,
    capsys: pytest.CaptureFixture,
) -> str:
    """Invoke handler with all heavy deps stubbed; return captured stdout."""
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = plan
    mock_planner.explain_plan.return_value = "Why this plan."

    patches = [
        patch("agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner",
              return_value=mock_planner),
        patch("agent_baton.cli.commands.execution.plan_cmd.KnowledgeRegistry",
              return_value=MagicMock()),
        patch("agent_baton.cli.commands.execution.plan_cmd.RetrospectiveEngine",
              return_value=MagicMock()),
        patch("agent_baton.cli.commands.execution.plan_cmd.DataClassifier",
              return_value=MagicMock()),
        patch("agent_baton.cli.commands.execution.plan_cmd.PolicyEngine",
              return_value=MagicMock()),
    ]

    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        plan_cmd.handler(args)

    return capsys.readouterr().out


# ---------------------------------------------------------------------------
# Mutually-exclusive flag handling
# ---------------------------------------------------------------------------

class TestDryRunSaveMutuallyExclusive:
    def test_dry_run_and_save_together_exits_non_zero(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        args = _make_args(dry_run=True, save=True)
        with pytest.raises(SystemExit) as exc_info:
            plan_cmd.handler(args)
        assert exc_info.value.code != 0

    def test_dry_run_and_save_together_emits_helpful_message(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        args = _make_args(dry_run=True, save=True)
        with pytest.raises(SystemExit):
            plan_cmd.handler(args)
        captured = capsys.readouterr()
        # Error goes to stderr.
        assert "--dry-run" in captured.err
        assert "--save" in captured.err
        assert "mutually exclusive" in captured.err.lower()


# ---------------------------------------------------------------------------
# Forecast block schema
# ---------------------------------------------------------------------------

class TestForecastBlockSchema:
    def test_header_present(self, capsys: pytest.CaptureFixture) -> None:
        plan = _make_plan()
        out = _run_handler(_make_args(dry_run=True), plan, capsys)
        assert "=== Plan Preview (NOT saved) ===" in out

    def test_metadata_present(self, capsys: pytest.CaptureFixture) -> None:
        plan = _make_plan()
        out = _run_handler(_make_args(dry_run=True), plan, capsys)
        assert plan.task_id in out
        assert plan.risk_level in out
        assert plan.budget_tier in out
        assert plan.execution_mode in out

    def test_step_table_present(self, capsys: pytest.CaptureFixture) -> None:
        plan = _make_plan()
        out = _run_handler(_make_args(dry_run=True), plan, capsys)
        assert "Phase / Step" in out
        assert "Est. tokens" in out
        # both step agents appear
        assert "architect" in out
        assert "backend-engineer--python" in out

    def test_gates_block_present_when_gates_defined(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _make_plan()
        out = _run_handler(_make_args(dry_run=True), plan, capsys)
        assert "Gates that will block:" in out
        # Gate types appear
        assert "build" in out
        assert "test" in out

    def test_cost_and_wall_clock_lines_present(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _make_plan()
        out = _run_handler(_make_args(dry_run=True), plan, capsys)
        assert "Cost forecast:" in out
        assert "Wall-clock:" in out
        assert "tokens" in out
        assert "$" in out

    def test_invitation_to_save(self, capsys: pytest.CaptureFixture) -> None:
        plan = _make_plan()
        out = _run_handler(_make_args(dry_run=True), plan, capsys)
        assert "--save" in out


# ---------------------------------------------------------------------------
# No filesystem writes
# ---------------------------------------------------------------------------

class TestDryRunWritesNothing:
    def test_team_context_not_modified(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """``--dry-run`` must not create or touch ``.claude/team-context/``."""
        # Run from a clean temp working directory.
        monkeypatch.chdir(tmp_path)
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)
        # Capture mtime + listing before
        before_mtime = ctx_dir.stat().st_mtime_ns
        before_listing = sorted(p.name for p in ctx_dir.iterdir())

        plan = _make_plan()
        _run_handler(_make_args(dry_run=True), plan, capsys)

        after_mtime = ctx_dir.stat().st_mtime_ns
        after_listing = sorted(p.name for p in ctx_dir.iterdir())

        assert before_mtime == after_mtime, (
            ".claude/team-context/ mtime changed during --dry-run"
        )
        assert before_listing == after_listing, (
            "Files appeared in .claude/team-context/ during --dry-run"
        )

    def test_no_plan_files_created_anywhere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        plan = _make_plan()
        _run_handler(_make_args(dry_run=True), plan, capsys)

        # No plan.json / plan.md should exist anywhere under tmp_path.
        all_paths = list(tmp_path.rglob("plan.json")) + list(tmp_path.rglob("plan.md"))
        assert all_paths == [], f"Unexpected plan files written: {all_paths}"


# ---------------------------------------------------------------------------
# Cost math correctness
# ---------------------------------------------------------------------------

class TestCostMath:
    def test_per_step_tokens_sum_to_total(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _make_plan()
        out = _run_handler(_make_args(dry_run=True), plan, capsys)

        # Extract per-step token counts from the table rows. Lines look like:
        #   "1.x Design            architect             opus       8,000"
        # We grep all comma-formatted numbers from the table area and sum
        # the per-step values, then compare against the printed total.
        # Strategy: match the table rows by anchoring on a leading "<int>." or
        # a known agent prefix.
        # Simpler: use the cost_estimator directly to compute the canonical
        # total and verify it appears in the output.
        from agent_baton.core.engine.cost_estimator import forecast_plan
        forecast = forecast_plan(plan)
        # Per-step tokens from the forecast must sum to the total.
        assert sum(t for _, t in forecast.per_step_tokens) == forecast.total_tokens
        # The total must appear in the rendered output (with comma).
        assert f"~{forecast.total_tokens:,} tokens" in out

    def test_total_cost_matches_model_pricing(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _make_plan()
        out = _run_handler(_make_args(dry_run=True), plan, capsys)

        # architect (opus, 8_000) + backend-engineer (sonnet, 5_000)
        expected_cost = (
            8_000 * MODEL_PRICING["opus"]
            + 5_000 * MODEL_PRICING["sonnet"]
        ) / 1_000_000.0
        # 0.24 + 0.03 = 0.27
        assert expected_cost == pytest.approx(0.27, abs=1e-6)
        assert f"~${expected_cost:.2f}" in out

    def test_breakdown_includes_each_model_used(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _make_plan()
        out = _run_handler(_make_args(dry_run=True), plan, capsys)
        # The output contains a parenthesised "(opus 8000, sonnet 5000)" style breakdown.
        assert re.search(r"opus\s+8000", out), out
        assert re.search(r"sonnet\s+5000", out), out


# ---------------------------------------------------------------------------
# bd-1359 — team-augmented step coverage
# ---------------------------------------------------------------------------

class TestTeamAugmentedForecast:
    """Regression coverage for bd-1359.

    A plan with at least one team-augmented step must have each team
    member's cost rolled into both the rendered text forecast and the
    structured JSON forecast (bd-47b4 dry-run --json variant).
    """

    def _team_plan(self) -> MachinePlan:
        from agent_baton.models.execution import TeamMember
        return MachinePlan(
            task_id="2026-04-25-team-test-feed1234",
            task_summary="team-augmented dry-run test",
            risk_level="MEDIUM",
            budget_tier="standard",
            execution_mode="phased",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Build",
                    steps=[PlanStep(
                        step_id="1.1",
                        agent_name="architect",                # 8_000 baseline
                        task_description="lead",
                        model="opus",                          # 30/M
                        team=[
                            TeamMember(
                                member_id="1.1.a",
                                agent_name="backend-engineer", # 5_000 baseline
                                role="implementer",
                                model="sonnet",                # 6/M
                            ),
                            TeamMember(
                                member_id="1.1.b",
                                agent_name="test-engineer",    # 5_000 baseline
                                role="reviewer",
                                model="haiku",                 # 1.25/M
                            ),
                        ],
                    )],
                ),
            ],
            detected_stack="python",
        )

    def test_text_forecast_aggregates_team_member_tokens(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        plan = self._team_plan()
        out = _run_handler(_make_args(dry_run=True), plan, capsys)
        # Lead 8_000 + members 5_000 + 5_000 = 18_000 tokens.
        assert "~18,000 tokens" in out
        # Cost: opus 0.24 + sonnet 0.03 + haiku 0.00625 = 0.27625 -> "~$0.28"
        assert "~$0.28" in out
        # Breakdown shows each model family's tokens.
        assert "opus 8000" in out
        assert "sonnet 5000" in out
        assert "haiku 5000" in out

    def test_json_forecast_aggregates_team_member_costs(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        plan = self._team_plan()
        # bd-47b4: --dry-run --json emits the structured payload.
        args = _make_args(dry_run=True)
        args.json = True
        out = _run_handler(args, plan, capsys)
        # Strip the planner stderr noise — payload is the only stdout JSON.
        import json as _json
        payload = _json.loads(out)
        assert payload["cost_forecast"]["total_tokens"] == 18_000
        # 0.24 + 0.03 + 0.00625 = 0.27625
        assert payload["cost_forecast"]["total_cost_usd"] == pytest.approx(
            0.27625, abs=1e-4
        )
        breakdown = payload["cost_forecast"]["model_breakdown_tokens"]
        assert breakdown == {"opus": 8_000, "sonnet": 5_000, "haiku": 5_000}
        # bd-47b4: ±50% band exposed alongside the central estimate.
        band = payload["cost_forecast"]["estimate_band"]
        assert band["confidence"] == "±50%"
        assert band["low_usd"] == pytest.approx(0.27625 * 0.5, abs=1e-4)
        assert band["high_usd"] == pytest.approx(0.27625 * 1.5, abs=1e-4)


# ---------------------------------------------------------------------------
# bd-47b4 — ±50% disclaimer surfaced in text + JSON
# ---------------------------------------------------------------------------

class TestEstimateDisclaimer:
    def test_text_forecast_includes_disclaimer(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _make_plan()
        out = _run_handler(_make_args(dry_run=True), plan, capsys)
        # The disclaimer line and the inline range must both appear.
        assert "Estimate ±50%" in out
        assert "actual cost depends on model + retries" in out
        assert "range ~$" in out

    def test_json_forecast_includes_estimate_band(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        plan = _make_plan()
        args = _make_args(dry_run=True)
        args.json = True
        out = _run_handler(args, plan, capsys)
        import json as _json
        payload = _json.loads(out)
        assert "estimate_band" in payload["cost_forecast"]
        band = payload["cost_forecast"]["estimate_band"]
        assert band["confidence"] == "±50%"
        # Sanity: low < central < high.
        central = payload["cost_forecast"]["total_cost_usd"]
        assert band["low_usd"] <= central <= band["high_usd"]


# ---------------------------------------------------------------------------
# Per-step model honoured
# ---------------------------------------------------------------------------

class TestPerStepModelHonoured:
    def test_default_model_propagates_to_planner(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """``--model opus`` is forwarded to the planner; the resulting
        plan's per-step models drive the forecast.

        We verify both that the forecast reflects the per-step model
        we hand back from the (mocked) planner AND that the planner was
        called with default_model='opus'.
        """
        # Build a plan whose steps all use opus (mimicking what the
        # planner would emit when --model opus is supplied).
        opus_plan = MachinePlan(
            task_id="2026-04-25-opus-test-aaaabbbb",
            task_summary="opus default",
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Implement",
                    steps=[PlanStep(step_id="1.1",
                                    agent_name="backend-engineer",
                                    task_description="impl",
                                    model="opus")],
                ),
            ],
        )

        mock_planner = MagicMock()
        mock_planner.create_plan.return_value = opus_plan
        with patch(
            "agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner",
            return_value=mock_planner,
        ), patch(
            "agent_baton.cli.commands.execution.plan_cmd.KnowledgeRegistry",
            return_value=MagicMock(),
        ), patch(
            "agent_baton.cli.commands.execution.plan_cmd.RetrospectiveEngine",
            return_value=MagicMock(),
        ), patch(
            "agent_baton.cli.commands.execution.plan_cmd.DataClassifier",
            return_value=MagicMock(),
        ), patch(
            "agent_baton.cli.commands.execution.plan_cmd.PolicyEngine",
            return_value=MagicMock(),
        ):
            args = _make_args(dry_run=True, model="opus")
            plan_cmd.handler(args)

        # Planner received default_model='opus'.
        kwargs = mock_planner.create_plan.call_args.kwargs
        assert kwargs.get("default_model") == "opus"

        # Output reflects opus pricing for the single 5_000-token step.
        out = capsys.readouterr().out
        # 5_000 * 30 / 1_000_000 = 0.15
        assert "~$0.15" in out
        assert "opus" in out
