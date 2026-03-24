"""Retrospective engine — generates and manages task retrospectives."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from agent_baton.models.retrospective import (
    AgentOutcome,
    KnowledgeGap,
    Retrospective,
    RosterRecommendation,
    SequencingNote,
)
from agent_baton.models.usage import TaskUsageRecord


class RetrospectiveEngine:
    """Generate structured retrospectives and store them on disk.

    Retrospectives are written to .claude/team-context/retrospectives/[task_id].md
    and provide qualitative signal about what went well, what failed, and
    what the system should learn for next time.
    """

    def __init__(self, retrospectives_dir: Path | None = None) -> None:
        self._dir = (retrospectives_dir or Path(".claude/team-context/retrospectives")).resolve()

    @property
    def dir(self) -> Path:
        return self._dir

    def generate_from_usage(
        self,
        usage: TaskUsageRecord,
        task_name: str = "",
        what_worked: list[AgentOutcome] | None = None,
        what_didnt: list[AgentOutcome] | None = None,
        knowledge_gaps: list[KnowledgeGap] | None = None,
        roster_recommendations: list[RosterRecommendation] | None = None,
        sequencing_notes: list[SequencingNote] | None = None,
    ) -> Retrospective:
        """Generate a retrospective from a usage record plus qualitative input.

        The usage record provides metrics (agent count, retries, gates, tokens).
        The qualitative fields (what_worked, what_didnt, etc.) are provided by
        the orchestrator based on its observations during the task.
        """
        total_tokens = sum(a.estimated_tokens for a in usage.agents_used)
        total_retries = sum(a.retries for a in usage.agents_used)

        return Retrospective(
            task_id=usage.task_id,
            task_name=task_name or usage.task_id,
            timestamp=usage.timestamp,
            agent_count=len(usage.agents_used),
            retry_count=total_retries,
            gates_passed=usage.gates_passed,
            gates_failed=usage.gates_failed,
            risk_level=usage.risk_level,
            estimated_tokens=total_tokens,
            what_worked=what_worked or [],
            what_didnt=what_didnt or [],
            knowledge_gaps=knowledge_gaps or [],
            roster_recommendations=roster_recommendations or [],
            sequencing_notes=sequencing_notes or [],
        )

    def save(self, retro: Retrospective) -> Path:
        """Write a retrospective to disk as markdown.

        Returns the path to the written file.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        # Sanitize task_id for filename
        safe_id = retro.task_id.replace("/", "-").replace(" ", "-")
        path = self._dir / f"{safe_id}.md"
        path.write_text(retro.to_markdown(), encoding="utf-8")
        return path

    def load(self, task_id: str) -> str | None:
        """Read a retrospective by task ID. Returns markdown content or None."""
        safe_id = task_id.replace("/", "-").replace(" ", "-")
        path = self._dir / f"{safe_id}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def list_retrospectives(self) -> list[Path]:
        """List all retrospective files, sorted by name (most recent last)."""
        if not self._dir.is_dir():
            return []
        return sorted(self._dir.glob("*.md"))

    def list_recent(self, count: int = 5) -> list[Path]:
        """Return the N most recent retrospective files."""
        all_retros = self.list_retrospectives()
        return all_retros[-count:]

    def search(self, keyword: str) -> list[Path]:
        """Find retrospectives containing a keyword (case-insensitive)."""
        results = []
        keyword_lower = keyword.lower()
        for path in self.list_retrospectives():
            try:
                content = path.read_text(encoding="utf-8")
                if keyword_lower in content.lower():
                    results.append(path)
            except OSError:
                continue
        return results

    def extract_recommendations(self) -> list[RosterRecommendation]:
        """Extract all roster recommendations across all retrospectives.

        Useful for the talent-builder and orchestrator to see patterns
        in what agents/knowledge packs are repeatedly recommended.
        """
        recommendations: list[RosterRecommendation] = []
        for path in self.list_retrospectives():
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            # Parse recommendations from markdown
            in_roster_section = False
            for line in content.splitlines():
                if line.startswith("## Roster Recommendations"):
                    in_roster_section = True
                    continue
                if line.startswith("## ") and in_roster_section:
                    break
                if in_roster_section and line.startswith("- **"):
                    # Parse: - **Create:** target-name
                    try:
                        action_end = line.index(":**")
                        action = line[4:action_end].lower()
                        target = line[action_end + 3:].strip()
                        recommendations.append(
                            RosterRecommendation(action=action, target=target)
                        )
                    except (ValueError, IndexError):
                        continue
        return recommendations
