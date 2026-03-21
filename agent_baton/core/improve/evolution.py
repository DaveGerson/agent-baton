"""Prompt evolution engine — data-driven agent prompt improvement proposals.

**Status: Experimental** — built and tested but not yet validated with real usage data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agent_baton.core.improve.scoring import PerformanceScorer, AgentScorecard
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.improve.vcs import AgentVersionControl
from agent_baton.core.orchestration.registry import AgentRegistry


@dataclass
class EvolutionProposal:
    """A proposed change to an agent's prompt."""

    agent_name: str
    scorecard: AgentScorecard
    issues: list[str] = field(default_factory=list)       # problems identified
    suggestions: list[str] = field(default_factory=list)  # proposed changes
    priority: str = "normal"  # "high" if needs-improvement, "normal" if adequate
    timestamp: str = ""

    def to_markdown(self) -> str:
        """Render as a readable report."""
        lines = [
            f"# Evolution Proposal: {self.agent_name}",
            f"",
            f"**Priority:** {self.priority}",
            f"**Generated:** {self.timestamp or datetime.now().isoformat()}",
            f"**Health:** {self.scorecard.health}",
            f"**First-pass rate:** {self.scorecard.first_pass_rate:.0%}",
            f"**Retry rate:** {self.scorecard.retry_rate:.1f}",
            f"",
        ]
        if self.issues:
            lines.append("## Issues Identified")
            for issue in self.issues:
                lines.append(f"- {issue}")
            lines.append("")
        if self.suggestions:
            lines.append("## Suggested Changes")
            for i, suggestion in enumerate(self.suggestions, 1):
                lines.append(f"{i}. {suggestion}")
            lines.append("")
        lines.append("## Scorecard")
        lines.append(self.scorecard.to_markdown())
        return "\n".join(lines)


class PromptEvolutionEngine:
    """Analyze agent performance and propose prompt improvements.

    Workflow:
    1. analyze() — read scores + retrospectives, identify underperformers
    2. propose() — generate evolution proposals for specific agents
    3. apply() — backup via VCS, apply changes (or write to proposals dir)
    4. report() — generate summary of all proposals
    """

    def __init__(
        self,
        scorer: PerformanceScorer | None = None,
        retro_engine: RetrospectiveEngine | None = None,
        vcs: AgentVersionControl | None = None,
        registry: AgentRegistry | None = None,
        proposals_dir: Path | None = None,
    ) -> None:
        self._scorer = scorer or PerformanceScorer()
        self._retro = retro_engine or RetrospectiveEngine()
        self._vcs = vcs or AgentVersionControl()
        self._registry = registry or AgentRegistry()
        self._proposals_dir = proposals_dir or Path(".claude/team-context/evolution-proposals")

    def analyze(self) -> list[EvolutionProposal]:
        """Analyze all agents and generate proposals for those needing improvement.

        Returns proposals sorted by priority (high first).
        """
        scorecards = self._scorer.score_all()
        proposals = []

        for sc in scorecards:
            issues: list[str] = []
            suggestions: list[str] = []

            # Quantitative signals
            if sc.first_pass_rate < 0.5:
                issues.append(
                    f"Low first-pass rate ({sc.first_pass_rate:.0%}) — agent frequently needs retries"
                )
                suggestions.append("Add more specific instructions for common failure modes")
                suggestions.append("Include negative examples (what NOT to do)")
            elif sc.first_pass_rate < 0.8:
                issues.append(
                    f"Moderate first-pass rate ({sc.first_pass_rate:.0%}) — room for improvement"
                )
                suggestions.append("Review retry patterns in retrospectives for recurring issues")

            if sc.retry_rate > 1.0:
                issues.append(
                    f"High retry rate ({sc.retry_rate:.1f}) — suggests unclear instructions"
                )
                suggestions.append("Tighten acceptance criteria in the agent's output format section")

            if sc.gate_pass_rate is not None and sc.gate_pass_rate < 0.7:
                issues.append(
                    f"Low gate pass rate ({sc.gate_pass_rate:.0%}) — output quality issues"
                )
                suggestions.append("Add quality checklist to the agent's prompt")

            # Qualitative signals from retrospectives
            if sc.negative_mentions > 0:
                issues.append(f"{sc.negative_mentions} negative mention(s) in retrospectives")
                suggestions.append(
                    "Read retrospective 'What Didn't Work' entries for this agent and address specific failures"
                )

            if sc.knowledge_gaps_cited > 0:
                issues.append(f"{sc.knowledge_gaps_cited} knowledge gap(s) cited")
                suggestions.append("Create or update knowledge pack to fill cited gaps")
                suggestions.append(
                    "Add 'Before Starting' section pointing to relevant knowledge packs"
                )

            # Only generate proposals for agents with actual issues
            if issues:
                priority = "high" if sc.health == "needs-improvement" else "normal"
                proposals.append(
                    EvolutionProposal(
                        agent_name=sc.agent_name,
                        scorecard=sc,
                        issues=issues,
                        suggestions=suggestions,
                        priority=priority,
                        timestamp=datetime.now().isoformat(),
                    )
                )

        # Sort: high priority first, then by first_pass_rate ascending
        proposals.sort(
            key=lambda p: (0 if p.priority == "high" else 1, p.scorecard.first_pass_rate)
        )
        return proposals

    def propose_for_agent(self, agent_name: str) -> EvolutionProposal | None:
        """Generate an evolution proposal for a specific agent.

        Returns None if the agent has no usage data or no issues.
        """
        proposals = self.analyze()
        for p in proposals:
            if p.agent_name == agent_name:
                return p
        return None

    def save_proposals(self, proposals: list[EvolutionProposal]) -> list[Path]:
        """Write proposals to disk as markdown files."""
        self._proposals_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for proposal in proposals:
            safe_name = proposal.agent_name.replace("/", "-")
            path = self._proposals_dir / f"{safe_name}.md"
            path.write_text(proposal.to_markdown(), encoding="utf-8")
            paths.append(path)
        return paths

    def generate_report(self) -> str:
        """Generate a summary report of all evolution proposals."""
        proposals = self.analyze()
        if not proposals:
            return "# Prompt Evolution Report\n\nAll agents are performing well. No changes proposed.\n"

        lines = [
            "# Prompt Evolution Report",
            "",
            f"**Generated:** {datetime.now().isoformat()}",
            f"**Agents analyzed:** {len(self._scorer.score_all())}",
            f"**Proposals generated:** {len(proposals)}",
            "",
        ]

        high = [p for p in proposals if p.priority == "high"]
        normal = [p for p in proposals if p.priority == "normal"]

        if high:
            lines.append("## High Priority (needs-improvement)")
            lines.append("")
            for p in high:
                lines.append(f"### {p.agent_name}")
                for issue in p.issues:
                    lines.append(f"- {issue}")
                lines.append("")

        if normal:
            lines.append("## Normal Priority (adequate, could improve)")
            lines.append("")
            for p in normal:
                lines.append(f"### {p.agent_name}")
                for issue in p.issues:
                    lines.append(f"- {issue}")
                lines.append("")

        return "\n".join(lines)

    def write_report(self, path: Path | None = None) -> Path:
        """Write the evolution report to disk."""
        out_path = path or Path(".claude/team-context/evolution-report.md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.generate_report(), encoding="utf-8")
        return out_path
