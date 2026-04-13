"""Tests for agent_baton.core.improve.maintainer.MaintainerSpawner.

Strategy:
- Launcher is always a mock (AsyncMock or a simple coroutine factory).
- Filesystem interactions use tmp_path so nothing touches the real workspace.
- Tests verify:
    * Spawn criteria (skipped reports, empty report, with escalations/auto-applied)
    * Prompt construction (key context fields appear in the prompt)
    * Decision log read/write round-trip
    * ImprovementLoop integration (maintainer_spawner injected, called correctly)
    * Best-effort error swallowing (launcher raises, loop still returns report)
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_baton.core.improve.maintainer import MaintainerSpawner, _build_prompt
from agent_baton.core.improve.experiments import ExperimentManager
from agent_baton.core.improve.loop import ImprovementLoop
from agent_baton.core.improve.proposals import ProposalManager
from agent_baton.core.improve.rollback import RollbackManager
from agent_baton.core.improve.scoring import AgentScorecard, PerformanceScorer
from agent_baton.core.improve.triggers import TriggerEvaluator
from agent_baton.core.improve.vcs import AgentVersionControl
from agent_baton.core.learn.recommender import Recommender
from agent_baton.core.runtime.launcher import LaunchResult
from agent_baton.models.improvement import (
    ImprovementConfig,
    ImprovementReport,
    Recommendation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_report(
    *,
    skipped: bool = False,
    escalated: list[str] | None = None,
    auto_applied: list[str] | None = None,
    recommendations: list[dict] | None = None,
) -> ImprovementReport:
    return ImprovementReport(
        report_id="report-test01",
        skipped=skipped,
        escalated=escalated or [],
        auto_applied=auto_applied or [],
        recommendations=recommendations or [],
    )


def _make_recommendation(
    rec_id: str = "rec-abc123",
    category: str = "budget_tier",
) -> dict:
    return Recommendation(
        rec_id=rec_id,
        category=category,
        target="phased_delivery",
        action="downgrade budget",
        description="Safe downgrade",
        confidence=0.91,
        risk="low",
        auto_applicable=True,
    ).to_dict()


def _make_launcher(status: str = "complete", error: str = "") -> MagicMock:
    """Return a mock launcher whose launch() coroutine returns a LaunchResult."""
    launcher = MagicMock()
    result = LaunchResult(
        step_id="maintainer-report-test01",
        agent_name="system-maintainer",
        status=status,
        error=error,
    )
    launcher.launch = AsyncMock(return_value=result)
    return launcher


def _make_spawner(tmp_path: Path, launcher: MagicMock | None = None) -> MaintainerSpawner:
    improvements_dir = tmp_path / "improvements"
    overrides_path = tmp_path / "learned-overrides.json"
    return MaintainerSpawner(
        improvements_dir=improvements_dir,
        overrides_path=overrides_path,
        launcher=launcher or _make_launcher(),
    )


# ---------------------------------------------------------------------------
# _should_spawn criteria
# ---------------------------------------------------------------------------

class TestShouldSpawn:
    def test_skipped_report_does_not_spawn(self, tmp_path: Path):
        spawner = _make_spawner(tmp_path)
        report = _make_report(skipped=True, escalated=["rec-1"])
        assert spawner._should_spawn(report) is False

    def test_empty_report_does_not_spawn(self, tmp_path: Path):
        spawner = _make_spawner(tmp_path)
        report = _make_report()  # no escalated, no auto_applied
        assert spawner._should_spawn(report) is False

    def test_escalated_only_spawns(self, tmp_path: Path):
        spawner = _make_spawner(tmp_path)
        report = _make_report(escalated=["rec-1"])
        assert spawner._should_spawn(report) is True

    def test_auto_applied_only_spawns(self, tmp_path: Path):
        spawner = _make_spawner(tmp_path)
        report = _make_report(auto_applied=["rec-2"])
        assert spawner._should_spawn(report) is True

    def test_both_escalated_and_auto_applied_spawns(self, tmp_path: Path):
        spawner = _make_spawner(tmp_path)
        report = _make_report(escalated=["rec-1"], auto_applied=["rec-2"])
        assert spawner._should_spawn(report) is True


# ---------------------------------------------------------------------------
# _build_prompt content
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_prompt_contains_report_id(self, tmp_path: Path):
        report = _make_report(escalated=["rec-abc123"])
        prompt = _build_prompt(
            report=report,
            report_path=tmp_path / "reports" / "report-test01.json",
            overrides_path=tmp_path / "learned-overrides.json",
            recent_rollback_count=1,
        )
        assert "report-test01" in prompt

    def test_prompt_contains_overrides_path(self, tmp_path: Path):
        overrides_path = tmp_path / "learned-overrides.json"
        report = _make_report(escalated=["rec-abc123"])
        prompt = _build_prompt(
            report=report,
            report_path=tmp_path / "reports" / "report-test01.json",
            overrides_path=overrides_path,
            recent_rollback_count=0,
        )
        assert str(overrides_path) in prompt

    def test_prompt_contains_rollback_count(self, tmp_path: Path):
        report = _make_report(escalated=["rec-abc123"])
        prompt = _build_prompt(
            report=report,
            report_path=tmp_path / "reports" / "report-test01.json",
            overrides_path=tmp_path / "learned-overrides.json",
            recent_rollback_count=2,
        )
        assert "2" in prompt

    def test_prompt_embeds_escalated_rec_json(self, tmp_path: Path):
        rec = _make_recommendation("rec-esc", "budget_tier")
        report = _make_report(escalated=["rec-esc"], recommendations=[rec])
        prompt = _build_prompt(
            report=report,
            report_path=tmp_path / "reports" / "report-test01.json",
            overrides_path=tmp_path / "learned-overrides.json",
            recent_rollback_count=0,
        )
        assert "rec-esc" in prompt
        assert "budget_tier" in prompt

    def test_prompt_embeds_auto_applied_rec_json(self, tmp_path: Path):
        rec = _make_recommendation("rec-auto", "sequencing")
        report = _make_report(auto_applied=["rec-auto"], recommendations=[rec])
        prompt = _build_prompt(
            report=report,
            report_path=tmp_path / "reports" / "report-test01.json",
            overrides_path=tmp_path / "learned-overrides.json",
            recent_rollback_count=0,
        )
        assert "rec-auto" in prompt
        assert "sequencing" in prompt

    def test_prompt_includes_no_source_code_instruction(self, tmp_path: Path):
        report = _make_report(escalated=["rec-1"])
        prompt = _build_prompt(
            report=report,
            report_path=tmp_path / "reports" / "r.json",
            overrides_path=tmp_path / "learned-overrides.json",
            recent_rollback_count=0,
        )
        assert "Never modify source code" in prompt

    def test_prompt_includes_no_prompt_evolution_instruction(self, tmp_path: Path):
        report = _make_report(escalated=["rec-1"])
        prompt = _build_prompt(
            report=report,
            report_path=tmp_path / "reports" / "r.json",
            overrides_path=tmp_path / "learned-overrides.json",
            recent_rollback_count=0,
        )
        assert "agent_prompt" in prompt


# ---------------------------------------------------------------------------
# maybe_spawn_async — launcher interaction
# ---------------------------------------------------------------------------

class TestMaybeSpawnAsync:
    def test_spawns_when_escalated(self, tmp_path: Path):
        launcher = _make_launcher()
        spawner = _make_spawner(tmp_path, launcher)
        report = _make_report(escalated=["rec-1"])
        asyncio.run(spawner.maybe_spawn_async(report=report))
        launcher.launch.assert_called_once()

    def test_does_not_spawn_when_skipped(self, tmp_path: Path):
        launcher = _make_launcher()
        spawner = _make_spawner(tmp_path, launcher)
        report = _make_report(skipped=True, escalated=["rec-1"])
        asyncio.run(spawner.maybe_spawn_async(report=report))
        launcher.launch.assert_not_called()

    def test_does_not_spawn_when_empty(self, tmp_path: Path):
        launcher = _make_launcher()
        spawner = _make_spawner(tmp_path, launcher)
        report = _make_report()
        asyncio.run(spawner.maybe_spawn_async(report=report))
        launcher.launch.assert_not_called()

    def test_launch_called_with_correct_agent_name(self, tmp_path: Path):
        launcher = _make_launcher()
        spawner = _make_spawner(tmp_path, launcher)
        report = _make_report(escalated=["rec-1"])
        asyncio.run(spawner.maybe_spawn_async(report=report))
        call_kwargs = launcher.launch.call_args
        assert call_kwargs.kwargs["agent_name"] == "system-maintainer"

    def test_launch_called_with_sonnet_model(self, tmp_path: Path):
        launcher = _make_launcher()
        spawner = _make_spawner(tmp_path, launcher)
        report = _make_report(auto_applied=["rec-1"])
        asyncio.run(spawner.maybe_spawn_async(report=report))
        call_kwargs = launcher.launch.call_args
        assert call_kwargs.kwargs["model"] == "sonnet"

    def test_step_id_contains_report_id(self, tmp_path: Path):
        launcher = _make_launcher()
        spawner = _make_spawner(tmp_path, launcher)
        report = _make_report(escalated=["rec-1"])
        asyncio.run(spawner.maybe_spawn_async(report=report))
        call_kwargs = launcher.launch.call_args
        assert "report-test01" in call_kwargs.kwargs["step_id"]

    def test_launch_failure_does_not_raise(self, tmp_path: Path):
        launcher = MagicMock()
        launcher.launch = AsyncMock(side_effect=RuntimeError("subprocess exploded"))
        spawner = _make_spawner(tmp_path, launcher)
        report = _make_report(escalated=["rec-1"])
        # Must not raise
        asyncio.run(spawner.maybe_spawn_async(report=report))

    def test_failed_status_logged_but_no_raise(self, tmp_path: Path):
        launcher = _make_launcher(status="failed", error="timeout")
        spawner = _make_spawner(tmp_path, launcher)
        report = _make_report(escalated=["rec-1"])
        asyncio.run(spawner.maybe_spawn_async(report=report))
        # Completed without exception; launcher was still called
        launcher.launch.assert_called_once()


# ---------------------------------------------------------------------------
# maybe_spawn (sync wrapper)
# ---------------------------------------------------------------------------

class TestMaybeSpawnSync:
    def test_sync_wrapper_calls_launcher(self, tmp_path: Path):
        launcher = _make_launcher()
        spawner = _make_spawner(tmp_path, launcher)
        report = _make_report(escalated=["rec-1"])
        spawner.maybe_spawn(report=report)
        launcher.launch.assert_called_once()

    def test_sync_wrapper_swallows_errors(self, tmp_path: Path):
        # Simulate asyncio.run itself raising
        spawner = _make_spawner(tmp_path)
        report = _make_report(escalated=["rec-1"])
        with patch("asyncio.run", side_effect=Exception("event loop collision")):
            spawner.maybe_spawn(report=report)  # must not raise

    def test_none_launcher_skips_gracefully(self, tmp_path: Path):
        """When no launcher can be constructed, maybe_spawn returns without error."""
        improvements_dir = tmp_path / "improvements"
        overrides_path = tmp_path / "learned-overrides.json"
        spawner = MaintainerSpawner(
            improvements_dir=improvements_dir,
            overrides_path=overrides_path,
            launcher=None,
        )
        report = _make_report(escalated=["rec-1"])
        # ClaudeCodeLauncher construction will fail (no claude binary in test env).
        # maybe_spawn must return without raising.
        spawner.maybe_spawn(report=report)


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------

class TestDecisionLog:
    def test_log_decision_creates_file(self, tmp_path: Path):
        spawner = _make_spawner(tmp_path)
        spawner.log_decision(
            rec_id="rec-1",
            action="applied",
            reasoning="High confidence budget downgrade.",
            changes={"gate_commands": {"python": {"test": "pytest"}}},
            category="budget_tier",
            target="phased_delivery",
        )
        log_path = tmp_path / "improvements" / "maintainer-decisions.jsonl"
        assert log_path.exists()

    def test_log_decision_is_valid_json(self, tmp_path: Path):
        spawner = _make_spawner(tmp_path)
        spawner.log_decision(
            rec_id="rec-2",
            action="rejected",
            reasoning="Prompt changes require human review.",
            changes={},
            category="agent_prompt",
            target="architect",
        )
        entries = spawner.load_decisions()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["rec_id"] == "rec-2"
        assert entry["action"] == "rejected"
        assert entry["category"] == "agent_prompt"
        assert entry["target"] == "architect"
        assert entry["changes"] == {}

    def test_log_decision_appends(self, tmp_path: Path):
        spawner = _make_spawner(tmp_path)
        for i in range(3):
            spawner.log_decision(
                rec_id=f"rec-{i}",
                action="deferred",
                reasoning="Insufficient samples.",
                changes={},
            )
        entries = spawner.load_decisions()
        assert len(entries) == 3

    def test_log_decision_has_timestamp(self, tmp_path: Path):
        spawner = _make_spawner(tmp_path)
        spawner.log_decision(rec_id="rec-ts", action="applied", reasoning="ok")
        entries = spawner.load_decisions()
        assert "timestamp" in entries[0]
        assert entries[0]["timestamp"]  # non-empty

    def test_load_decisions_empty_when_no_log(self, tmp_path: Path):
        spawner = _make_spawner(tmp_path)
        assert spawner.load_decisions() == []

    def test_load_decisions_skips_malformed_lines(self, tmp_path: Path):
        improvements_dir = tmp_path / "improvements"
        improvements_dir.mkdir(parents=True)
        log_path = improvements_dir / "maintainer-decisions.jsonl"
        log_path.write_text(
            '{"rec_id": "good", "action": "applied", "reasoning": "ok", "changes": {}, "timestamp": "t", "category": "", "target": ""}\n'
            "not-valid-json\n"
            '{"rec_id": "also-good", "action": "deferred", "reasoning": "borderline", "changes": {}, "timestamp": "t", "category": "", "target": ""}\n',
            encoding="utf-8",
        )
        spawner = MaintainerSpawner(
            improvements_dir=improvements_dir,
            overrides_path=tmp_path / "overrides.json",
            launcher=_make_launcher(),
        )
        entries = spawner.load_decisions()
        assert len(entries) == 2
        assert entries[0]["rec_id"] == "good"
        assert entries[1]["rec_id"] == "also-good"


# ---------------------------------------------------------------------------
# ImprovementLoop integration
# ---------------------------------------------------------------------------

def _loop_with_spawner(
    tmp_path: Path,
    spawner: MaintainerSpawner,
    recommendations: list[Recommendation] | None = None,
) -> ImprovementLoop:
    improvements_dir = tmp_path / "improvements"

    triggers = MagicMock(spec=TriggerEvaluator)
    triggers.should_analyze.return_value = True
    triggers.detect_anomalies.return_value = []

    recommender = MagicMock(spec=Recommender)
    recommender.analyze.return_value = recommendations or []

    scorer = MagicMock(spec=PerformanceScorer)
    scorer.score_agent.return_value = AgentScorecard(
        agent_name="test", times_used=5, first_pass_rate=0.8
    )

    vcs = AgentVersionControl(tmp_path / "agents")
    rollbacks = RollbackManager(vcs=vcs, improvements_dir=improvements_dir)

    return ImprovementLoop(
        trigger_evaluator=triggers,
        recommender=recommender,
        proposal_manager=ProposalManager(improvements_dir),
        experiment_manager=ExperimentManager(improvements_dir),
        rollback_manager=rollbacks,
        scorer=scorer,
        config=ImprovementConfig(),
        improvements_dir=improvements_dir,
        maintainer_spawner=spawner,
    )


class TestImprovementLoopIntegration:
    def test_spawner_called_when_escalated_recommendation(self, tmp_path: Path):
        launcher = _make_launcher()
        spawner = _make_spawner(tmp_path, launcher)

        rec = Recommendation(
            rec_id="rec-prompt",
            category="agent_prompt",
            target="architect",
            action="evolve prompt",
            description="Review needed",
            confidence=0.75,
            risk="high",
            auto_applicable=False,
        )
        loop = _loop_with_spawner(tmp_path, spawner, recommendations=[rec])
        report = loop.run_cycle()

        assert "rec-prompt" in report.escalated
        launcher.launch.assert_called_once()

    def test_spawner_called_when_auto_applied(self, tmp_path: Path):
        launcher = _make_launcher()
        spawner = _make_spawner(tmp_path, launcher)

        rec = Recommendation(
            rec_id="rec-budget",
            category="budget_tier",
            target="phased_delivery",
            action="downgrade budget",
            description="Safe",
            confidence=0.92,
            risk="low",
            auto_applicable=True,
        )
        loop = _loop_with_spawner(tmp_path, spawner, recommendations=[rec])
        report = loop.run_cycle()

        assert "rec-budget" in report.auto_applied
        launcher.launch.assert_called_once()

    def test_spawner_not_called_when_no_recs(self, tmp_path: Path):
        launcher = _make_launcher()
        spawner = _make_spawner(tmp_path, launcher)
        loop = _loop_with_spawner(tmp_path, spawner, recommendations=[])
        loop.run_cycle()
        launcher.launch.assert_not_called()

    def test_spawner_failure_does_not_affect_report(self, tmp_path: Path):
        """A crashing spawner must not prevent run_cycle from returning the report."""
        bad_launcher = MagicMock()
        bad_launcher.launch = AsyncMock(side_effect=RuntimeError("crash"))
        spawner = _make_spawner(tmp_path, bad_launcher)

        rec = Recommendation(
            rec_id="rec-crash-test",
            category="agent_prompt",
            target="architect",
            action="evolve prompt",
            description="Review needed",
            confidence=0.75,
            risk="high",
            auto_applicable=False,
        )
        loop = _loop_with_spawner(tmp_path, spawner, recommendations=[rec])
        report = loop.run_cycle()

        # Report still returned correctly despite spawner crash
        assert report.report_id is not None
        assert report.skipped is False
        assert "rec-crash-test" in report.escalated

    def test_spawner_prompt_passed_to_launcher(self, tmp_path: Path):
        """The prompt delivered to the launcher must contain the report ID."""
        launcher = _make_launcher()
        spawner = _make_spawner(tmp_path, launcher)

        rec = Recommendation(
            rec_id="rec-prompt-check",
            category="agent_prompt",
            target="backend-engineer",
            action="evolve prompt",
            description="Needs review",
            confidence=0.7,
            risk="high",
            auto_applicable=False,
        )
        loop = _loop_with_spawner(tmp_path, spawner, recommendations=[rec])
        report = loop.run_cycle()

        call_kwargs = launcher.launch.call_args.kwargs
        assert report.report_id in call_kwargs["prompt"]

    def test_skipped_cycle_does_not_spawn(self, tmp_path: Path):
        launcher = _make_launcher()
        spawner = _make_spawner(tmp_path, launcher)

        improvements_dir = tmp_path / "improvements"
        triggers = MagicMock(spec=TriggerEvaluator)
        triggers.should_analyze.return_value = False  # triggers skip
        triggers.detect_anomalies.return_value = []
        recommender = MagicMock(spec=Recommender)
        scorer = MagicMock(spec=PerformanceScorer)
        vcs = AgentVersionControl(tmp_path / "agents")
        rollbacks = RollbackManager(vcs=vcs, improvements_dir=improvements_dir)

        loop = ImprovementLoop(
            trigger_evaluator=triggers,
            recommender=recommender,
            proposal_manager=ProposalManager(improvements_dir),
            experiment_manager=ExperimentManager(improvements_dir),
            rollback_manager=rollbacks,
            scorer=scorer,
            improvements_dir=improvements_dir,
            maintainer_spawner=spawner,
        )
        report = loop.run_cycle()
        assert report.skipped is True
        launcher.launch.assert_not_called()
