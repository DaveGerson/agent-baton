"""Prompt evolution engine -- data-driven agent prompt improvement proposals.

The evolution engine is the most impactful -- and most carefully guarded --
component of the improvement layer.  It analyses agent scorecards to
identify underperformers and generates specific, actionable prompt
modification proposals.

Evolution strategy:

Proposals are generated based on a cascade of quantitative and qualitative
signals, each contributing specific suggestions:

1. **First-pass rate < 0.5** (``"needs-improvement"`` health):

   - "Add more specific instructions for common failure modes"
   - "Include negative examples (what NOT to do)"

2. **First-pass rate 0.5 -- 0.8** (``"adequate"`` health):

   - "Review retry patterns in retrospectives for recurring issues"

3. **Retry rate > 1.0**:

   - "Tighten acceptance criteria in the agent's output format section"

4. **Gate pass rate < 0.7**:

   - "Add quality checklist to the agent's prompt"

5. **Negative retrospective mentions**:

   - "Read retrospective 'What Didn't Work' entries and address failures"

6. **Knowledge gaps cited**:

   - "Create or update knowledge pack to fill cited gaps"
   - "Add 'Before Starting' section pointing to relevant knowledge packs"

Safety guardrails:

* Prompt changes are ALWAYS classified as ``risk="high"`` and
  ``auto_applicable=False`` in the recommendation pipeline.  They are never
  auto-applied -- always escalated to human review.
* :class:`~agent_baton.core.improve.vcs.AgentVersionControl` creates a
  timestamped backup before any modification.
* Rollback is automatic on detected degradation via the experiment system.

**Status: Experimental** -- built and tested but not yet validated with
real usage data.
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
    """A proposed change to an agent's prompt.

    Proposals capture the evidence (scorecard, identified issues) and the
    specific suggestions for how to modify the agent's definition file.
    They are designed for human review -- the operator decides which
    suggestions to implement.

    Attributes:
        agent_name: Name of the agent targeted for evolution.
        scorecard: The agent's current :class:`AgentScorecard` providing
            the quantitative basis for the proposal.
        issues: Human-readable descriptions of identified problems.
        suggestions: Specific, actionable prompt modifications.
        priority: ``"high"`` if the agent's health is
            ``"needs-improvement"``; ``"normal"`` otherwise.
        timestamp: ISO 8601 timestamp of when the proposal was generated.
    """

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
        self._proposals_dir = (proposals_dir or Path(".claude/team-context/evolution-proposals")).resolve()

    def analyze(self) -> list[EvolutionProposal]:
        """Analyse all agents and generate proposals for those needing improvement.

        Scores every agent via :class:`PerformanceScorer`, then applies the
        signal cascade (see module docstring) to identify issues and generate
        suggestions.  Only agents with at least one identified issue produce
        a proposal.

        Proposals are sorted by:

        1. Priority descending (``"high"`` before ``"normal"``).
        2. First-pass rate ascending (worst performers first).

        This ordering ensures the most impactful improvements are reviewed
        first.

        Returns:
            List of :class:`EvolutionProposal` objects, possibly empty if
            all agents are performing well.
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

        Runs the full analysis pipeline and returns only the proposal
        matching *agent_name*.  This is a convenience method for targeted
        review; for batch analysis use :meth:`analyze`.

        Args:
            agent_name: Exact agent name to generate a proposal for.

        Returns:
            An :class:`EvolutionProposal` if the agent has identified
            issues, or ``None`` if the agent has no usage data or is
            performing well.
        """
        proposals = self.analyze()
        for p in proposals:
            if p.agent_name == agent_name:
                return p
        return None

    def save_proposals(self, proposals: list[EvolutionProposal]) -> list[Path]:
        """Write proposals to disk as Markdown files.

        Each proposal is saved to
        ``<proposals_dir>/<agent_name>.md`` for human review.

        Args:
            proposals: The proposals to persist.

        Returns:
            List of absolute paths to the written files.
        """
        self._proposals_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for proposal in proposals:
            safe_name = proposal.agent_name.replace("/", "-")
            path = self._proposals_dir / f"{safe_name}.md"
            path.write_text(proposal.to_markdown(), encoding="utf-8")
            paths.append(path)
        return paths

    def generate_report(self) -> str:
        """Generate a summary Markdown report of all evolution proposals.

        Groups proposals by priority (high / normal) and lists each
        agent's identified issues.  The report is intended for the human
        operator to review before deciding which suggestions to implement.

        Returns:
            A complete Markdown document.  Returns a positive message if
            all agents are performing well and no proposals were generated.
        """
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
        out_path = (path or Path(".claude/team-context/evolution-report.md")).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.generate_report(), encoding="utf-8")
        return out_path
