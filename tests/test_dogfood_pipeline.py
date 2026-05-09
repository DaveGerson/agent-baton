"""Dogfood Gate — Epic 2 prerequisite 3.0c.

Exercises the full observability pipeline end-to-end with real data:
  1. Write a usage log entry via UsageLogger
  2. Write a retrospective via RetrospectiveEngine
  3. Generate scorecards via PerformanceScorer; verify they reflect usage data
  4. Generate a dashboard via DashboardGenerator; verify it has content
  5. Verify CLI commands work against the generated data

Each test is independent (uses tmp_path) and tests one pipeline stage so that a
failure pinpoints exactly which stage broke.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests._subprocess_helpers import cli_subprocess_env

import pytest

from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.models.retrospective import (
    AgentOutcome,
    KnowledgeGap,
    RosterRecommendation,
    SequencingNote,
)
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.observe.dashboard import DashboardGenerator
from agent_baton.core.improve.scoring import PerformanceScorer


# ---------------------------------------------------------------------------
# Shared fixture: a realistic three-agent task record
# ---------------------------------------------------------------------------

@pytest.fixture()
def realistic_task() -> TaskUsageRecord:
    """A realistic orchestrated task record with three agents."""
    return TaskUsageRecord(
        task_id="dogfood-task-2026-03-20",
        timestamp="2026-03-20T09:00:00",
        agents_used=[
            AgentUsageRecord(
                name="architect",
                model="opus",
                steps=3,
                retries=0,
                gate_results=["PASS"],
                estimated_tokens=4500,
                duration_seconds=12.3,
            ),
            AgentUsageRecord(
                name="backend-engineer--python",
                model="sonnet",
                steps=7,
                retries=1,
                gate_results=["FAIL", "PASS"],
                estimated_tokens=12000,
                duration_seconds=38.7,
            ),
            AgentUsageRecord(
                name="test-engineer",
                model="sonnet",
                steps=4,
                retries=0,
                gate_results=["PASS"],
                estimated_tokens=6200,
                duration_seconds=21.1,
            ),
        ],
        total_agents=3,
        risk_level="MEDIUM",
        sequencing_mode="phased_delivery",
        gates_passed=3,
        gates_failed=1,
        outcome="SHIP WITH NOTES",
        notes="Backend needed one retry due to missing edge-case coverage.",
    )


# ---------------------------------------------------------------------------
# Step 1: UsageLogger — write and read back
# ---------------------------------------------------------------------------

class TestStep1UsageLogger:
    # DECISION: removed test_log_file_created_on_disk (file existence only) and
    # test_one_line_written_per_record (line count only). Both are subsets of
    # test_record_round_trips_correctly which already reads from the file, proving
    # it was created and contains exactly 1 parseable record.

    def test_record_round_trips_correctly(self, tmp_path: Path, realistic_task: TaskUsageRecord):
        log_file = tmp_path / "team-context" / "usage-log.jsonl"
        logger = UsageLogger(log_file)
        logger.log(realistic_task)
        records = logger.read_all()
        assert len(records) == 1
        restored = records[0]
        assert restored.task_id == realistic_task.task_id
        assert restored.outcome == realistic_task.outcome
        assert restored.risk_level == realistic_task.risk_level
        assert len(restored.agents_used) == 3

    def test_agent_names_preserved_after_roundtrip(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        log_file = tmp_path / "team-context" / "usage-log.jsonl"
        logger = UsageLogger(log_file)
        logger.log(realistic_task)
        restored = logger.read_all()[0]
        names = [a.name for a in restored.agents_used]
        assert "architect" in names
        assert "backend-engineer--python" in names
        assert "test-engineer" in names

    def test_summary_reflects_logged_record(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        log_file = tmp_path / "team-context" / "usage-log.jsonl"
        logger = UsageLogger(log_file)
        logger.log(realistic_task)
        summary = logger.summary()
        assert summary["total_tasks"] == 1
        assert summary["total_agents_used"] == 3
        assert summary["total_estimated_tokens"] == 4500 + 12000 + 6200
        assert "SHIP WITH NOTES" in summary["outcome_counts"]
        assert summary["outcome_counts"]["SHIP WITH NOTES"] == 1


# ---------------------------------------------------------------------------
# Step 2: RetrospectiveEngine — generate and save
# ---------------------------------------------------------------------------

class TestStep2RetrospectiveEngine:
    def test_retro_file_created_on_disk(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        retros_dir = tmp_path / "team-context" / "retrospectives"
        engine = RetrospectiveEngine(retros_dir)
        retro = engine.generate_from_usage(
            realistic_task,
            task_name="Dogfood Pipeline Verification",
            what_worked=[
                AgentOutcome(name="architect", worked_well="Clean design up front"),
                AgentOutcome(name="test-engineer", worked_well="Thorough test coverage"),
            ],
            what_didnt=[
                AgentOutcome(
                    name="backend-engineer--python",
                    issues="Missed edge case on first pass",
                    root_cause="Ambiguous spec",
                ),
            ],
            knowledge_gaps=[
                KnowledgeGap(
                    description="backend-engineer--python lacks async context manager patterns",
                    affected_agent="backend-engineer--python",
                    suggested_fix="create knowledge pack",
                )
            ],
            roster_recommendations=[
                RosterRecommendation(
                    action="improve",
                    target="backend-engineer--python",
                    reason="Add async patterns knowledge pack",
                )
            ],
            sequencing_notes=[
                SequencingNote(
                    phase="2",
                    observation="Gate caught real bug — retry was justified",
                    keep=True,
                )
            ],
        )
        path = engine.save(retro)
        assert path.exists(), "retrospective .md file was not created"
        assert path.suffix == ".md"
        assert "dogfood-task-2026-03-20" in path.name

    def test_retro_content_contains_metrics(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        retros_dir = tmp_path / "team-context" / "retrospectives"
        engine = RetrospectiveEngine(retros_dir)
        retro = engine.generate_from_usage(
            realistic_task, task_name="Dogfood Pipeline Verification"
        )
        engine.save(retro)
        content = engine.load(realistic_task.task_id)
        assert content is not None
        assert "Dogfood Pipeline Verification" in content
        # Metrics section
        assert "Agents: 3" in content
        assert "MEDIUM" in content
        # Token total: 4500 + 12000 + 6200 = 22700
        assert "22,700" in content

    def test_retro_content_contains_qualitative_sections(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        retros_dir = tmp_path / "team-context" / "retrospectives"
        engine = RetrospectiveEngine(retros_dir)
        retro = engine.generate_from_usage(
            realistic_task,
            task_name="Dogfood Pipeline Verification",
            what_worked=[AgentOutcome(name="architect", worked_well="Designed cleanly")],
            what_didnt=[
                AgentOutcome(
                    name="backend-engineer--python",
                    issues="Missed edge case",
                    root_cause="Ambiguous spec",
                )
            ],
            knowledge_gaps=[
                KnowledgeGap(description="Lacks async patterns", affected_agent="backend-engineer--python")
            ],
            roster_recommendations=[
                RosterRecommendation(action="improve", target="backend-engineer--python")
            ],
        )
        engine.save(retro)
        content = engine.load(realistic_task.task_id)
        assert "## What Worked" in content
        assert "architect" in content
        assert "## What Didn't" in content
        assert "backend-engineer--python" in content
        assert "## Knowledge Gaps Exposed" in content
        assert "## Roster Recommendations" in content

    def test_list_retrospectives_finds_saved_file(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        retros_dir = tmp_path / "team-context" / "retrospectives"
        engine = RetrospectiveEngine(retros_dir)
        retro = engine.generate_from_usage(realistic_task)
        engine.save(retro)
        listed = engine.list_retrospectives()
        assert len(listed) == 1


# ---------------------------------------------------------------------------
# Step 3: PerformanceScorer — scorecards reflect usage data
# ---------------------------------------------------------------------------

class TestStep3PerformanceScorer:
    def _setup(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ) -> tuple[UsageLogger, RetrospectiveEngine, PerformanceScorer]:
        log_file = tmp_path / "team-context" / "usage-log.jsonl"
        retros_dir = tmp_path / "team-context" / "retrospectives"
        logger = UsageLogger(log_file)
        logger.log(realistic_task)
        retro_engine = RetrospectiveEngine(retros_dir)
        retro = retro_engine.generate_from_usage(
            realistic_task,
            task_name="Dogfood",
            what_worked=[AgentOutcome(name="architect", worked_well="Clean design")],
            what_didnt=[
                AgentOutcome(name="backend-engineer--python", issues="Retry needed")
            ],
        )
        retro_engine.save(retro)
        scorer = PerformanceScorer(logger, retro_engine)
        return logger, retro_engine, scorer

    def test_all_scorecards_have_nonzero_times_used(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _, _, scorer = self._setup(tmp_path, realistic_task)
        for sc in scorer.score_all():
            assert sc.times_used > 0, f"{sc.agent_name} has times_used=0"

    # DECISION: parameterized test_architect_scorecard_has_correct_metrics +
    # test_backend_engineer_scorecard_reflects_retry + test_test_engineer_scorecard_is_nonzero
    # into one test. All three call score_agent() and check first_pass_rate,
    # retry_rate, total_estimated_tokens — identical structure, different values.
    @pytest.mark.parametrize("agent_name,expected_times_used,expected_first_pass_rate,expected_retry_rate,expected_tokens", [
        ("architect", 1, 1.0, 0.0, 4500),
        ("backend-engineer--python", 1, 0.0, 1.0, 12000),
        ("test-engineer", 1, 1.0, 0.0, 6200),
    ])
    def test_scorecard_metrics(
        self,
        tmp_path: Path,
        realistic_task: TaskUsageRecord,
        agent_name: str,
        expected_times_used: int,
        expected_first_pass_rate: float,
        expected_retry_rate: float,
        expected_tokens: int,
    ):
        _, _, scorer = self._setup(tmp_path, realistic_task)
        sc = scorer.score_agent(agent_name)
        assert sc.times_used == expected_times_used
        assert sc.first_pass_rate == expected_first_pass_rate
        assert sc.retry_rate == expected_retry_rate
        assert sc.total_estimated_tokens == expected_tokens

    def test_positive_mention_counted_for_architect(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _, _, scorer = self._setup(tmp_path, realistic_task)
        sc = scorer.score_agent("architect")
        assert sc.positive_mentions >= 1

    def test_negative_mention_counted_for_backend_engineer(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _, _, scorer = self._setup(tmp_path, realistic_task)
        sc = scorer.score_agent("backend-engineer--python")
        assert sc.negative_mentions >= 1

    def test_generate_report_contains_all_agent_names(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _, _, scorer = self._setup(tmp_path, realistic_task)
        report = scorer.generate_report()
        assert "architect" in report
        assert "backend-engineer--python" in report
        assert "test-engineer" in report

    def test_write_report_creates_file_on_disk(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _, _, scorer = self._setup(tmp_path, realistic_task)
        out_path = tmp_path / "team-context" / "agent-scorecards.md"
        result = scorer.write_report(out_path)
        assert result.exists(), "agent-scorecards.md was not created"
        content = result.read_text(encoding="utf-8")
        assert content.startswith("# Agent Performance Scorecards")

    # DECISION: parameterized test_gate_pass_rate_computed_for_architect +
    # test_gate_pass_rate_computed_for_backend_engineer into one test.
    @pytest.mark.parametrize("agent_name,expected_rate", [
        ("architect", 1.0),            # gate_results=["PASS"] → 100%
        ("backend-engineer--python", 0.5),  # gate_results=["FAIL","PASS"] → 50%
    ])
    def test_gate_pass_rate(
        self,
        tmp_path: Path,
        realistic_task: TaskUsageRecord,
        agent_name: str,
        expected_rate: float,
    ):
        _, _, scorer = self._setup(tmp_path, realistic_task)
        sc = scorer.score_agent(agent_name)
        assert sc.gate_pass_rate == pytest.approx(expected_rate)


# ---------------------------------------------------------------------------
# Step 4: DashboardGenerator — dashboard reflects usage data
# ---------------------------------------------------------------------------

class TestStep4DashboardGenerator:
    def _setup_logger(self, tmp_path: Path, realistic_task: TaskUsageRecord) -> UsageLogger:
        log_file = tmp_path / "team-context" / "usage-log.jsonl"
        logger = UsageLogger(log_file)
        logger.log(realistic_task)
        return logger

    # DECISION: consolidated the 8 single-assertion dashboard content tests
    # (test_dashboard_starts_with_header, test_dashboard_contains_task_count,
    # test_dashboard_contains_overview_section, test_dashboard_overview_reflects_token_total,
    # test_dashboard_contains_all_three_agents, test_dashboard_outcome_reflects_ship_with_notes,
    # test_dashboard_risk_level_present, test_dashboard_model_mix_contains_sonnet_and_opus,
    # test_dashboard_gate_pass_rate_is_75_percent) into 3 grouped tests.
    # Each group covers a logical slice of the dashboard content.
    # test_dashboard_write_content_matches_generate (pure roundtrip) removed as trivial.

    def test_dashboard_structure_and_overview(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        """Dashboard has correct header, task count, and Overview section."""
        logger = self._setup_logger(tmp_path, realistic_task)
        gen = DashboardGenerator(logger)
        dashboard = gen.generate()
        assert dashboard.startswith("# Usage Dashboard")
        assert "1 tasks tracked" in dashboard
        assert "## Overview" in dashboard

    def test_dashboard_numeric_data(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        """Dashboard includes correct token total (22,700) and gate pass rate (75%)."""
        logger = self._setup_logger(tmp_path, realistic_task)
        gen = DashboardGenerator(logger)
        dashboard = gen.generate()
        # 4500 + 12000 + 6200 = 22700
        assert "22,700" in dashboard
        # gates_passed=3, gates_failed=1 → 3/4 = 75%
        assert "75%" in dashboard

    def test_dashboard_content_reflects_task_data(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        """Dashboard contains all three agent names, outcome, risk level, and model names."""
        logger = self._setup_logger(tmp_path, realistic_task)
        gen = DashboardGenerator(logger)
        dashboard = gen.generate()
        assert "architect" in dashboard
        assert "backend-engineer--python" in dashboard
        assert "test-engineer" in dashboard
        assert "SHIP WITH NOTES" in dashboard
        assert "MEDIUM" in dashboard
        assert "sonnet" in dashboard
        assert "opus" in dashboard

    def test_dashboard_write_creates_file_on_disk(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        logger = self._setup_logger(tmp_path, realistic_task)
        gen = DashboardGenerator(logger)
        out_path = tmp_path / "team-context" / "usage-dashboard.md"
        result = gen.write(out_path)
        assert result.exists(), "usage-dashboard.md was not created"
        content = result.read_text(encoding="utf-8")
        assert "# Usage Dashboard" in content


# ---------------------------------------------------------------------------
# Step 5: CLI commands work against the generated data
# ---------------------------------------------------------------------------

def _run_cli(args: list[str], env_cwd: str) -> subprocess.CompletedProcess:
    """Run the baton CLI as a subprocess with a given CWD."""
    return subprocess.run(
        [sys.executable, "-m", "agent_baton.cli.main"] + args,
        capture_output=True,
        text=True,
        cwd=env_cwd,
        env=cli_subprocess_env(),
    )


def _seed_pipeline(tmp_path: Path, realistic_task: TaskUsageRecord) -> None:
    """Write usage log and retrospective to the default locations under tmp_path."""
    # CLI commands use hard-coded default paths relative to CWD:
    #   .claude/team-context/usage-log.jsonl
    #   .claude/team-context/retrospectives/
    log_file = tmp_path / ".claude" / "team-context" / "usage-log.jsonl"
    retros_dir = tmp_path / ".claude" / "team-context" / "retrospectives"

    logger = UsageLogger(log_file)
    logger.log(realistic_task)

    retro_engine = RetrospectiveEngine(retros_dir)
    retro = retro_engine.generate_from_usage(
        realistic_task,
        task_name="Dogfood Pipeline Verification",
        what_worked=[AgentOutcome(name="architect", worked_well="Designed cleanly")],
        what_didnt=[
            AgentOutcome(
                name="backend-engineer--python",
                issues="Needed one retry",
                root_cause="Ambiguous spec",
            )
        ],
        roster_recommendations=[
            RosterRecommendation(
                action="improve",
                target="backend-engineer--python",
                reason="Add async patterns knowledge",
            )
        ],
    )
    retro_engine.save(retro)


class TestStep5CLICommands:
    # DECISION: merged test_usage_summary_shows_task_count + test_usage_summary_shows_agent_names
    # + test_usage_recent_shows_task_id into 1 comprehensive usage command test. They all
    # call `baton usage` (or a minor variant) against the same seeded data. The task-id
    # check also implicitly proves the record was written and read back.
    def test_usage_command(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        """usage command reports task count, agent names, and recent task ids."""
        _seed_pipeline(tmp_path, realistic_task)

        # Basic summary
        result = _run_cli(["usage"], str(tmp_path))
        assert result.returncode == 0, f"CLI exited {result.returncode}: {result.stderr}"
        assert "1 task" in result.stdout
        assert "architect" in result.stdout

        # Recent
        result2 = _run_cli(["usage", "--recent", "5"], str(tmp_path))
        assert result2.returncode == 0
        assert "dogfood-task-2026-03-20" in result2.stdout

    def test_usage_agent_stats_for_architect(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _seed_pipeline(tmp_path, realistic_task)
        result = _run_cli(["usage", "--agent", "architect"], str(tmp_path))
        assert result.returncode == 0
        assert "architect" in result.stdout
        assert "Times used" in result.stdout

    def test_usage_agent_not_found_message(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _seed_pipeline(tmp_path, realistic_task)
        result = _run_cli(["usage", "--agent", "nonexistent-agent"], str(tmp_path))
        assert result.returncode == 0
        assert "No records found" in result.stdout

    def test_dashboard_generate_outputs_markdown(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _seed_pipeline(tmp_path, realistic_task)
        result = _run_cli(["dashboard"], str(tmp_path))
        assert result.returncode == 0, f"CLI exited {result.returncode}: {result.stderr}"
        assert "# Usage Dashboard" in result.stdout
        assert "architect" in result.stdout

    def test_dashboard_write_creates_file(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _seed_pipeline(tmp_path, realistic_task)
        result = _run_cli(["dashboard", "--write"], str(tmp_path))
        assert result.returncode == 0
        dashboard_path = (
            tmp_path / ".claude" / "team-context" / "usage-dashboard.md"
        )
        assert dashboard_path.exists(), "Dashboard file not written to disk"
        assert "# Usage Dashboard" in dashboard_path.read_text(encoding="utf-8")

    def test_scores_generate_shows_agent_scorecards(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _seed_pipeline(tmp_path, realistic_task)
        result = _run_cli(["scores"], str(tmp_path))
        assert result.returncode == 0, f"CLI exited {result.returncode}: {result.stderr}"
        assert "# Agent Performance Scorecards" in result.stdout
        assert "architect" in result.stdout

    def test_scores_specific_agent(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _seed_pipeline(tmp_path, realistic_task)
        result = _run_cli(["scores", "--agent", "architect"], str(tmp_path))
        assert result.returncode == 0
        assert "architect" in result.stdout
        assert "Uses" in result.stdout

    def test_scores_write_creates_file(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _seed_pipeline(tmp_path, realistic_task)
        result = _run_cli(["scores", "--write"], str(tmp_path))
        assert result.returncode == 0
        scorecards_path = (
            tmp_path / ".claude" / "team-context" / "agent-scorecards.md"
        )
        assert scorecards_path.exists(), "Scorecard report file not written to disk"

    def test_retro_list_shows_saved_task(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _seed_pipeline(tmp_path, realistic_task)
        result = _run_cli(["retro"], str(tmp_path))
        assert result.returncode == 0, f"CLI exited {result.returncode}: {result.stderr}"
        assert "dogfood-task-2026-03-20" in result.stdout

    def test_retro_task_id_shows_full_content(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _seed_pipeline(tmp_path, realistic_task)
        result = _run_cli(["retro", "--task-id", "dogfood-task-2026-03-20"], str(tmp_path))
        assert result.returncode == 0
        assert "# Retrospective: Dogfood Pipeline Verification" in result.stdout
        assert "architect" in result.stdout

    def test_retro_search_finds_keyword(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _seed_pipeline(tmp_path, realistic_task)
        result = _run_cli(["retro", "--search", "architect"], str(tmp_path))
        assert result.returncode == 0
        assert "dogfood-task-2026-03-20" in result.stdout

    def test_retro_recommendations_lists_improve_action(
        self, tmp_path: Path, realistic_task: TaskUsageRecord
    ):
        _seed_pipeline(tmp_path, realistic_task)
        result = _run_cli(["retro", "--recommendations"], str(tmp_path))
        assert result.returncode == 0
        assert "improve" in result.stdout
        assert "backend-engineer--python" in result.stdout


# ---------------------------------------------------------------------------
# Step 6: Full pipeline integrity — all artefacts present after one run
# ---------------------------------------------------------------------------

class TestStep6FullPipelineIntegrity:
    """Run the complete pipeline once and assert all expected artefacts exist."""

    def test_all_artefacts_present(self, tmp_path: Path, realistic_task: TaskUsageRecord):
        # Set up paths
        log_file = tmp_path / "team-context" / "usage-log.jsonl"
        retros_dir = tmp_path / "team-context" / "retrospectives"
        scorecards_path = tmp_path / "team-context" / "agent-scorecards.md"
        dashboard_path = tmp_path / "team-context" / "usage-dashboard.md"

        # 1. Log usage
        logger = UsageLogger(log_file)
        logger.log(realistic_task)
        assert log_file.exists(), "FAIL step 1: usage-log.jsonl not created"

        # 2. Write retrospective
        retro_engine = RetrospectiveEngine(retros_dir)
        retro = retro_engine.generate_from_usage(
            realistic_task,
            task_name="Full Integration Run",
            what_worked=[AgentOutcome(name="architect", worked_well="Clean design")],
        )
        retro_engine.save(retro)
        retro_files = list(retros_dir.glob("*.md"))
        assert len(retro_files) == 1, "FAIL step 2: retrospective .md not created"

        # 3. Generate scorecards
        scorer = PerformanceScorer(logger, retro_engine)
        scorer.write_report(scorecards_path)
        assert scorecards_path.exists(), "FAIL step 3: agent-scorecards.md not created"
        scorecards = scorer.score_all()
        assert len(scorecards) == 3, f"FAIL step 3: expected 3 scorecards, got {len(scorecards)}"
        assert all(sc.times_used > 0 for sc in scorecards), "FAIL step 3: scorecard has zero uses"

        # 4. Generate dashboard
        gen = DashboardGenerator(logger)
        gen.write(dashboard_path)
        assert dashboard_path.exists(), "FAIL step 4: usage-dashboard.md not created"
        dashboard_content = dashboard_path.read_text(encoding="utf-8")
        assert "# Usage Dashboard" in dashboard_content, "FAIL step 4: dashboard has no header"
        assert "architect" in dashboard_content, "FAIL step 4: dashboard missing agent data"

        # 5. Verify scorecard content is non-trivial
        scorecard_content = scorecards_path.read_text(encoding="utf-8")
        assert "architect" in scorecard_content
        assert "backend-engineer--python" in scorecard_content
        assert "test-engineer" in scorecard_content
