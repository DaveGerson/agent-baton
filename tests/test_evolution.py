"""Tests for agent_baton.core.evolution — PromptEvolutionEngine and EvolutionProposal."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord
from agent_baton.models.retrospective import (
    AgentOutcome,
    KnowledgeGap,
    Retrospective,
)
from agent_baton.core.usage import UsageLogger
from agent_baton.core.retrospective import RetrospectiveEngine
from agent_baton.core.scoring import AgentScorecard, PerformanceScorer
from agent_baton.core.evolution import EvolutionProposal, PromptEvolutionEngine


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _agent(
    name: str,
    retries: int = 0,
    gate_results: list[str] | None = None,
    model: str = "sonnet",
    tokens: int = 1000,
) -> AgentUsageRecord:
    return AgentUsageRecord(
        name=name,
        model=model,
        steps=1,
        retries=retries,
        gate_results=gate_results if gate_results is not None else [],
        estimated_tokens=tokens,
        duration_seconds=1.0,
    )


def _task(
    task_id: str,
    agents: list[AgentUsageRecord],
    timestamp: str = "2026-03-01T10:00:00",
    risk_level: str = "LOW",
    outcome: str = "SHIP",
    gates_passed: int = 0,
    gates_failed: int = 0,
) -> TaskUsageRecord:
    return TaskUsageRecord(
        task_id=task_id,
        timestamp=timestamp,
        agents_used=agents,
        total_agents=len(agents),
        risk_level=risk_level,
        sequencing_mode="phased_delivery",
        gates_passed=gates_passed,
        gates_failed=gates_failed,
        outcome=outcome,
        notes="",
    )


def _setup_engine(tmp_path: Path) -> tuple[UsageLogger, RetrospectiveEngine, PromptEvolutionEngine]:
    """Return a (logger, retro_engine, evolution_engine) triple wired to tmp_path."""
    log_file = tmp_path / "usage.jsonl"
    retros_dir = tmp_path / "retros"
    proposals_dir = tmp_path / "proposals"

    logger = UsageLogger(log_file)
    retro_engine = RetrospectiveEngine(retros_dir)
    scorer = PerformanceScorer(logger, retro_engine)
    engine = PromptEvolutionEngine(
        scorer=scorer,
        retro_engine=retro_engine,
        proposals_dir=proposals_dir,
    )
    return logger, retro_engine, engine


# ---------------------------------------------------------------------------
# EvolutionProposal.to_markdown
# ---------------------------------------------------------------------------

class TestEvolutionProposalToMarkdown:
    def test_starts_with_h1(self) -> None:
        sc = AgentScorecard(agent_name="arch", times_used=3, first_pass_rate=0.33)
        proposal = EvolutionProposal(
            agent_name="arch",
            scorecard=sc,
            issues=["Low first-pass rate"],
            suggestions=["Add examples"],
            priority="high",
            timestamp="2026-03-20T10:00:00",
        )
        md = proposal.to_markdown()
        assert md.startswith("# Evolution Proposal: arch")

    def test_contains_priority(self) -> None:
        sc = AgentScorecard(agent_name="be", times_used=2, first_pass_rate=0.5)
        proposal = EvolutionProposal(
            agent_name="be",
            scorecard=sc,
            priority="normal",
        )
        md = proposal.to_markdown()
        assert "**Priority:** normal" in md

    def test_contains_health(self) -> None:
        sc = AgentScorecard(agent_name="be", times_used=2, first_pass_rate=0.5)
        proposal = EvolutionProposal(agent_name="be", scorecard=sc)
        md = proposal.to_markdown()
        assert "**Health:**" in md

    def test_contains_first_pass_rate(self) -> None:
        sc = AgentScorecard(agent_name="be", times_used=2, first_pass_rate=0.5)
        proposal = EvolutionProposal(agent_name="be", scorecard=sc)
        md = proposal.to_markdown()
        assert "**First-pass rate:** 50%" in md

    def test_issues_section_rendered(self) -> None:
        sc = AgentScorecard(agent_name="arch", times_used=3, first_pass_rate=0.33)
        proposal = EvolutionProposal(
            agent_name="arch",
            scorecard=sc,
            issues=["Issue one", "Issue two"],
        )
        md = proposal.to_markdown()
        assert "## Issues Identified" in md
        assert "- Issue one" in md
        assert "- Issue two" in md

    def test_suggestions_section_rendered(self) -> None:
        sc = AgentScorecard(agent_name="arch", times_used=3, first_pass_rate=0.33)
        proposal = EvolutionProposal(
            agent_name="arch",
            scorecard=sc,
            suggestions=["Do this", "Then that"],
        )
        md = proposal.to_markdown()
        assert "## Suggested Changes" in md
        assert "1. Do this" in md
        assert "2. Then that" in md

    def test_scorecard_section_rendered(self) -> None:
        sc = AgentScorecard(agent_name="arch", times_used=3, first_pass_rate=0.33)
        proposal = EvolutionProposal(agent_name="arch", scorecard=sc)
        md = proposal.to_markdown()
        assert "## Scorecard" in md

    def test_timestamp_used_when_provided(self) -> None:
        sc = AgentScorecard(agent_name="arch", times_used=1, first_pass_rate=0.5)
        ts = "2026-03-20T00:00:00"
        proposal = EvolutionProposal(agent_name="arch", scorecard=sc, timestamp=ts)
        md = proposal.to_markdown()
        assert ts in md

    def test_no_issues_section_when_empty(self) -> None:
        sc = AgentScorecard(agent_name="arch", times_used=1, first_pass_rate=0.9)
        proposal = EvolutionProposal(agent_name="arch", scorecard=sc, issues=[])
        md = proposal.to_markdown()
        assert "## Issues Identified" not in md

    def test_no_suggestions_section_when_empty(self) -> None:
        sc = AgentScorecard(agent_name="arch", times_used=1, first_pass_rate=0.9)
        proposal = EvolutionProposal(agent_name="arch", scorecard=sc, suggestions=[])
        md = proposal.to_markdown()
        assert "## Suggested Changes" not in md


# ---------------------------------------------------------------------------
# PromptEvolutionEngine.analyze — base cases
# ---------------------------------------------------------------------------

class TestAnalyzeBaseCases:
    def test_returns_empty_list_when_no_usage_data(self, tmp_path: Path) -> None:
        _, _, engine = _setup_engine(tmp_path)
        assert engine.analyze() == []

    def test_strong_agent_produces_no_proposal(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        # Three uses, zero retries => first_pass_rate=1.0 => "strong"
        logger.log(_task("t1", [_agent("arch", retries=0)]))
        logger.log(_task("t2", [_agent("arch", retries=0)]))
        logger.log(_task("t3", [_agent("arch", retries=0)]))
        proposals = engine.analyze()
        names = [p.agent_name for p in proposals]
        assert "arch" not in names


# ---------------------------------------------------------------------------
# PromptEvolutionEngine.analyze — low first_pass_rate signals
# ---------------------------------------------------------------------------

class TestAnalyzeLowFirstPassRate:
    def test_first_pass_rate_below_0_5_generates_proposal(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        # 1 out of 3 zero-retry => first_pass_rate=0.33 < 0.5
        logger.log(_task("t1", [_agent("arch", retries=0)]))
        logger.log(_task("t2", [_agent("arch", retries=2)]))
        logger.log(_task("t3", [_agent("arch", retries=1)]))
        proposals = engine.analyze()
        names = [p.agent_name for p in proposals]
        assert "arch" in names

    def test_low_first_pass_rate_issue_text(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("slow", retries=0)]))
        logger.log(_task("t2", [_agent("slow", retries=3)]))
        logger.log(_task("t3", [_agent("slow", retries=3)]))
        proposals = engine.analyze()
        proposal = next(p for p in proposals if p.agent_name == "slow")
        assert any("first-pass rate" in issue.lower() for issue in proposal.issues)

    def test_low_first_pass_rate_suggests_negative_examples(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("slow", retries=0)]))
        logger.log(_task("t2", [_agent("slow", retries=3)]))
        logger.log(_task("t3", [_agent("slow", retries=3)]))
        proposals = engine.analyze()
        proposal = next(p for p in proposals if p.agent_name == "slow")
        combined = " ".join(proposal.suggestions).lower()
        assert "negative examples" in combined or "failure modes" in combined

    def test_moderate_first_pass_rate_between_0_5_and_0_8(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        # 3 out of 5 zero-retry => first_pass_rate=0.6 (adequate band)
        for i in range(3):
            logger.log(_task(f"t{i}", [_agent("mod", retries=0)]))
        for i in range(3, 5):
            logger.log(_task(f"t{i}", [_agent("mod", retries=1)]))
        proposals = engine.analyze()
        names = [p.agent_name for p in proposals]
        assert "mod" in names

    def test_first_pass_rate_at_or_above_0_8_no_proposal(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        # 4 out of 5 zero-retry => 0.8 => "strong" (no negatives) => no proposal
        for i in range(4):
            logger.log(_task(f"t{i}", [_agent("good", retries=0)]))
        logger.log(_task("t4", [_agent("good", retries=1)]))
        proposals = engine.analyze()
        names = [p.agent_name for p in proposals]
        assert "good" not in names


# ---------------------------------------------------------------------------
# PromptEvolutionEngine.analyze — high retry_rate signal
# ---------------------------------------------------------------------------

class TestAnalyzeHighRetryRate:
    def test_retry_rate_above_1_generates_issue(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        # retries=2 each use => avg=2.0 > 1.0
        logger.log(_task("t1", [_agent("chatty", retries=2)]))
        logger.log(_task("t2", [_agent("chatty", retries=2)]))
        proposals = engine.analyze()
        proposal = next((p for p in proposals if p.agent_name == "chatty"), None)
        assert proposal is not None
        assert any("retry rate" in issue.lower() for issue in proposal.issues)

    def test_retry_rate_suggestion_mentions_acceptance_criteria(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("chatty", retries=2)]))
        logger.log(_task("t2", [_agent("chatty", retries=2)]))
        proposals = engine.analyze()
        proposal = next(p for p in proposals if p.agent_name == "chatty")
        combined = " ".join(proposal.suggestions).lower()
        assert "acceptance criteria" in combined


# ---------------------------------------------------------------------------
# PromptEvolutionEngine.analyze — gate pass rate signal
# ---------------------------------------------------------------------------

class TestAnalyzeLowGatePassRate:
    def test_low_gate_pass_rate_generates_issue(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        # 1 PASS, 2 FAIL => gate_pass_rate=0.33 < 0.7
        logger.log(_task("t1", [_agent("gated", gate_results=["PASS", "FAIL", "FAIL"])]))
        proposals = engine.analyze()
        proposal = next((p for p in proposals if p.agent_name == "gated"), None)
        assert proposal is not None
        assert any("gate pass rate" in issue.lower() for issue in proposal.issues)

    def test_high_gate_pass_rate_no_gate_issue(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        # 3 PASS, 1 FAIL => 0.75 > 0.7, and first_pass_rate=1.0 => strong → no proposal
        logger.log(_task("t1", [_agent("gated", retries=0, gate_results=["PASS", "PASS", "PASS", "FAIL"])]))
        proposals = engine.analyze()
        names = [p.agent_name for p in proposals]
        assert "gated" not in names


# ---------------------------------------------------------------------------
# PromptEvolutionEngine.analyze — retrospective qualitative signals
# ---------------------------------------------------------------------------

class TestAnalyzeRetroSignals:
    def test_negative_mentions_generate_issue(self, tmp_path: Path) -> None:
        logger, retro_engine, engine = _setup_engine(tmp_path)
        # Give the agent a strong quantitative score so only retro signal matters
        logger.log(_task("t1", [_agent("alpha", retries=0)]))
        logger.log(_task("t2", [_agent("alpha", retries=0)]))
        retro = Retrospective(
            task_id="t1",
            task_name="T",
            timestamp="2026-03-01",
            what_didnt=[AgentOutcome(name="alpha", issues="Missed edge case")],
        )
        retro_engine.save(retro)
        proposals = engine.analyze()
        proposal = next((p for p in proposals if p.agent_name == "alpha"), None)
        assert proposal is not None
        assert any("negative mention" in issue.lower() for issue in proposal.issues)

    def test_knowledge_gaps_generate_issue(self, tmp_path: Path) -> None:
        logger, retro_engine, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("beta", retries=0)]))
        logger.log(_task("t2", [_agent("beta", retries=0)]))
        retro = Retrospective(
            task_id="t1",
            task_name="T",
            timestamp="2026-03-01",
            knowledge_gaps=[KnowledgeGap(description="beta lacks Redis knowledge", affected_agent="beta")],
        )
        retro_engine.save(retro)
        proposals = engine.analyze()
        proposal = next((p for p in proposals if p.agent_name == "beta"), None)
        assert proposal is not None
        assert any("knowledge gap" in issue.lower() for issue in proposal.issues)

    def test_knowledge_gap_suggestion_mentions_knowledge_pack(self, tmp_path: Path) -> None:
        logger, retro_engine, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("beta", retries=0)]))
        retro = Retrospective(
            task_id="t1",
            task_name="T",
            timestamp="2026-03-01",
            knowledge_gaps=[KnowledgeGap(description="beta lacks Redis knowledge", affected_agent="beta")],
        )
        retro_engine.save(retro)
        proposals = engine.analyze()
        proposal = next(p for p in proposals if p.agent_name == "beta")
        combined = " ".join(proposal.suggestions).lower()
        assert "knowledge pack" in combined


# ---------------------------------------------------------------------------
# PromptEvolutionEngine.analyze — priority and sorting
# ---------------------------------------------------------------------------

class TestAnalyzePriorityAndSorting:
    def test_needs_improvement_health_gives_high_priority(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        # first_pass_rate < 0.5 => "needs-improvement" => priority="high"
        logger.log(_task("t1", [_agent("poor", retries=0)]))
        logger.log(_task("t2", [_agent("poor", retries=5)]))
        logger.log(_task("t3", [_agent("poor", retries=5)]))
        proposals = engine.analyze()
        proposal = next(p for p in proposals if p.agent_name == "poor")
        assert proposal.priority == "high"

    def test_adequate_health_gives_normal_priority(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        # first_pass_rate = 0.6 => "adequate" => priority="normal"
        for i in range(3):
            logger.log(_task(f"t{i}", [_agent("mid", retries=0)]))
        for i in range(3, 5):
            logger.log(_task(f"t{i}", [_agent("mid", retries=1)]))
        proposals = engine.analyze()
        proposal = next(p for p in proposals if p.agent_name == "mid")
        assert proposal.priority == "normal"

    def test_high_priority_sorted_before_normal(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        # "good-ish": first_pass_rate=0.6 -> adequate -> normal
        for i in range(3):
            logger.log(_task(f"a{i}", [_agent("good-ish", retries=0)]))
        for i in range(3, 5):
            logger.log(_task(f"a{i}", [_agent("good-ish", retries=1)]))
        # "bad": first_pass_rate=0.33 -> needs-improvement -> high
        logger.log(_task("b1", [_agent("bad", retries=0)]))
        logger.log(_task("b2", [_agent("bad", retries=3)]))
        logger.log(_task("b3", [_agent("bad", retries=3)]))
        proposals = engine.analyze()
        priorities = [p.priority for p in proposals]
        # All "high" entries must appear before "normal" entries
        seen_normal = False
        for pri in priorities:
            if pri == "normal":
                seen_normal = True
            if seen_normal:
                assert pri == "normal", "A 'high' priority appeared after a 'normal' one"


# ---------------------------------------------------------------------------
# PromptEvolutionEngine.propose_for_agent
# ---------------------------------------------------------------------------

class TestProposeForAgent:
    def test_returns_none_for_unknown_agent(self, tmp_path: Path) -> None:
        _, _, engine = _setup_engine(tmp_path)
        assert engine.propose_for_agent("ghost") is None

    def test_returns_none_for_well_performing_agent(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("star", retries=0)]))
        logger.log(_task("t2", [_agent("star", retries=0)]))
        assert engine.propose_for_agent("star") is None

    def test_returns_proposal_for_underperforming_agent(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("sluggish", retries=0)]))
        logger.log(_task("t2", [_agent("sluggish", retries=3)]))
        logger.log(_task("t3", [_agent("sluggish", retries=3)]))
        proposal = engine.propose_for_agent("sluggish")
        assert proposal is not None
        assert proposal.agent_name == "sluggish"

    def test_returned_proposal_has_issues(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("sluggish", retries=0)]))
        logger.log(_task("t2", [_agent("sluggish", retries=3)]))
        logger.log(_task("t3", [_agent("sluggish", retries=3)]))
        proposal = engine.propose_for_agent("sluggish")
        assert proposal is not None
        assert len(proposal.issues) > 0


# ---------------------------------------------------------------------------
# PromptEvolutionEngine.save_proposals
# ---------------------------------------------------------------------------

class TestSaveProposals:
    def test_writes_files_to_proposals_dir(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("arch", retries=0)]))
        logger.log(_task("t2", [_agent("arch", retries=3)]))
        logger.log(_task("t3", [_agent("arch", retries=3)]))
        proposals = engine.analyze()
        paths = engine.save_proposals(proposals)
        assert len(paths) > 0
        for path in paths:
            assert path.exists()

    def test_file_named_after_agent(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("myagent", retries=0)]))
        logger.log(_task("t2", [_agent("myagent", retries=3)]))
        logger.log(_task("t3", [_agent("myagent", retries=3)]))
        proposals = engine.analyze()
        paths = engine.save_proposals(proposals)
        file_names = [p.name for p in paths]
        assert "myagent.md" in file_names

    def test_file_contains_agent_name_in_content(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("myagent", retries=0)]))
        logger.log(_task("t2", [_agent("myagent", retries=3)]))
        logger.log(_task("t3", [_agent("myagent", retries=3)]))
        proposals = engine.analyze()
        paths = engine.save_proposals(proposals)
        target = next(p for p in paths if p.name == "myagent.md")
        content = target.read_text(encoding="utf-8")
        assert "myagent" in content

    def test_creates_proposals_dir_if_missing(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        proposals_dir = tmp_path / "proposals"
        assert not proposals_dir.exists()
        logger.log(_task("t1", [_agent("arch", retries=0)]))
        logger.log(_task("t2", [_agent("arch", retries=3)]))
        proposals = engine.analyze()
        engine.save_proposals(proposals)
        assert proposals_dir.exists()

    def test_agent_name_with_slash_sanitised_in_filename(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("org/agent", retries=0)]))
        logger.log(_task("t2", [_agent("org/agent", retries=4)]))
        logger.log(_task("t3", [_agent("org/agent", retries=4)]))
        proposals = engine.analyze()
        paths = engine.save_proposals(proposals)
        assert any("org-agent.md" == p.name for p in paths)

    def test_empty_proposals_saves_no_files(self, tmp_path: Path) -> None:
        _, _, engine = _setup_engine(tmp_path)
        paths = engine.save_proposals([])
        assert paths == []


# ---------------------------------------------------------------------------
# PromptEvolutionEngine.generate_report
# ---------------------------------------------------------------------------

class TestGenerateReport:
    def test_all_well_message_when_no_proposals(self, tmp_path: Path) -> None:
        _, _, engine = _setup_engine(tmp_path)
        report = engine.generate_report()
        assert "All agents are performing well" in report

    def test_starts_with_h1(self, tmp_path: Path) -> None:
        _, _, engine = _setup_engine(tmp_path)
        assert engine.generate_report().startswith("# Prompt Evolution Report")

    def test_report_includes_agent_name_when_issues_exist(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("baddie", retries=0)]))
        logger.log(_task("t2", [_agent("baddie", retries=3)]))
        logger.log(_task("t3", [_agent("baddie", retries=3)]))
        report = engine.generate_report()
        assert "baddie" in report

    def test_report_includes_issues_for_agents(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("baddie", retries=0)]))
        logger.log(_task("t2", [_agent("baddie", retries=3)]))
        logger.log(_task("t3", [_agent("baddie", retries=3)]))
        report = engine.generate_report()
        assert "first-pass rate" in report.lower() or "retry rate" in report.lower()

    def test_report_contains_high_priority_section(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        # first_pass_rate < 0.5 => needs-improvement => "High Priority"
        logger.log(_task("t1", [_agent("baddie", retries=0)]))
        logger.log(_task("t2", [_agent("baddie", retries=5)]))
        logger.log(_task("t3", [_agent("baddie", retries=5)]))
        report = engine.generate_report()
        assert "High Priority" in report

    def test_report_contains_normal_priority_section(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        # first_pass_rate = 0.6 => adequate => "Normal Priority"
        for i in range(3):
            logger.log(_task(f"t{i}", [_agent("mid", retries=0)]))
        for i in range(3, 5):
            logger.log(_task(f"t{i}", [_agent("mid", retries=1)]))
        report = engine.generate_report()
        assert "Normal Priority" in report

    def test_report_proposals_count(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        logger.log(_task("t1", [_agent("baddie", retries=0)]))
        logger.log(_task("t2", [_agent("baddie", retries=3)]))
        logger.log(_task("t3", [_agent("baddie", retries=3)]))
        report = engine.generate_report()
        # Report uses bold markdown: **Proposals generated:** 1
        assert "Proposals generated:** 1" in report

    def test_report_agents_analyzed_count(self, tmp_path: Path) -> None:
        logger, _, engine = _setup_engine(tmp_path)
        # Use two agents that both have issues so the report is not the
        # "all performing well" early-return branch.
        for i in range(3):
            logger.log(_task(f"t{i}", [_agent("a1", retries=3), _agent("a2", retries=3)]))
        report = engine.generate_report()
        # Report uses bold markdown: **Agents analyzed:** 2
        assert "Agents analyzed:** 2" in report


# ---------------------------------------------------------------------------
# PromptEvolutionEngine.write_report
# ---------------------------------------------------------------------------

class TestWriteReport:
    def test_creates_file_on_disk(self, tmp_path: Path) -> None:
        _, _, engine = _setup_engine(tmp_path)
        out_path = tmp_path / "evo-report.md"
        result = engine.write_report(out_path)
        assert result.exists()

    def test_returns_the_output_path(self, tmp_path: Path) -> None:
        _, _, engine = _setup_engine(tmp_path)
        out_path = tmp_path / "evo-report.md"
        result = engine.write_report(out_path)
        assert result == out_path

    def test_file_content_is_markdown(self, tmp_path: Path) -> None:
        _, _, engine = _setup_engine(tmp_path)
        out_path = tmp_path / "evo-report.md"
        engine.write_report(out_path)
        content = out_path.read_text(encoding="utf-8")
        assert content.startswith("# Prompt Evolution Report")

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        _, _, engine = _setup_engine(tmp_path)
        out_path = tmp_path / "reports" / "subdir" / "evo-report.md"
        engine.write_report(out_path)
        assert out_path.exists()
