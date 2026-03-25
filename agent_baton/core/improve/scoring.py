"""Performance scorer — computes per-agent scorecards from usage and retrospective data."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.observe.retrospective import RetrospectiveEngine

if TYPE_CHECKING:
    from agent_baton.core.storage.protocol import StorageBackend


@dataclass
class AgentScorecard:
    """Performance scorecard for a single agent."""
    agent_name: str
    times_used: int = 0
    first_pass_rate: float = 0.0  # % of uses with 0 retries
    retry_rate: float = 0.0       # avg retries per use
    gate_pass_rate: float | None = None  # % of gates passed
    total_estimated_tokens: int = 0
    avg_tokens_per_use: int = 0
    models_used: dict[str, int] = field(default_factory=dict)
    # Qualitative signals from retrospectives
    positive_mentions: int = 0
    negative_mentions: int = 0
    knowledge_gaps_cited: int = 0

    @property
    def health(self) -> str:
        """Simple health rating based on metrics."""
        if self.times_used == 0:
            return "unused"
        if self.first_pass_rate >= 0.8 and self.negative_mentions == 0:
            return "strong"
        if self.first_pass_rate >= 0.5:
            return "adequate"
        return "needs-improvement"

    def to_markdown(self) -> str:
        gate_str = f"{self.gate_pass_rate:.0%}" if self.gate_pass_rate is not None else "n/a"
        lines = [
            f"### {self.agent_name}",
            f"- **Health:** {self.health}",
            f"- **Uses:** {self.times_used}",
            f"- **First-pass rate:** {self.first_pass_rate:.0%}",
            f"- **Avg retries:** {self.retry_rate:.1f}",
            f"- **Gate pass rate:** {gate_str}",
            f"- **Avg tokens/use:** {self.avg_tokens_per_use:,}",
        ]
        if self.models_used:
            model_str = ", ".join(f"{m}({c})" for m, c in sorted(self.models_used.items()))
            lines.append(f"- **Models:** {model_str}")
        if self.positive_mentions or self.negative_mentions:
            lines.append(
                f"- **Retro mentions:** +{self.positive_mentions} / -{self.negative_mentions}"
            )
        if self.knowledge_gaps_cited:
            lines.append(f"- **Knowledge gaps cited:** {self.knowledge_gaps_cited}")
        lines.append("")
        return "\n".join(lines)


class PerformanceScorer:
    """Compute per-agent scorecards from usage logs and retrospective data.

    When *storage* is provided (a :class:`StorageBackend`), retrospective data
    is read from the storage backend rather than the filesystem.  This ensures
    ``baton scores`` returns current data in SQLite-mode projects where retros
    are written only to the database and not to the legacy filesystem path.

    Fall-back order: storage backend → filesystem (``retro_engine``).
    """

    def __init__(
        self,
        usage_logger: UsageLogger | None = None,
        retro_engine: RetrospectiveEngine | None = None,
        storage: StorageBackend | None = None,
    ) -> None:
        self._usage = usage_logger or UsageLogger()
        self._retro = retro_engine or RetrospectiveEngine()
        self._storage = storage

    def score_agent(self, agent_name: str) -> AgentScorecard:
        """Compute a scorecard for a single agent."""
        stats = self._usage.agent_stats(agent_name)

        times_used = stats["times_used"]
        if times_used == 0:
            return AgentScorecard(agent_name=agent_name)

        # First-pass rate: count uses with 0 retries
        records = self._usage.read_all()
        zero_retry_uses = 0
        total_tokens = 0
        for record in records:
            for agent in record.agents_used:
                if agent.name == agent_name:
                    if agent.retries == 0:
                        zero_retry_uses += 1
                    total_tokens += agent.estimated_tokens

        first_pass_rate = zero_retry_uses / times_used if times_used > 0 else 0.0
        avg_tokens = total_tokens // times_used if times_used > 0 else 0

        # Qualitative: scan retrospectives for agent mentions.
        # When a storage backend is configured, load retros from it so
        # SQLite-mode projects return current data (retros in that mode are
        # written only to the DB, not to the legacy filesystem path).
        positive = 0
        negative = 0
        gaps_cited = 0
        if self._storage is not None:
            try:
                task_ids = self._storage.list_retrospective_ids(limit=200)
            except Exception:
                task_ids = []
            for task_id in task_ids:
                try:
                    retro = self._storage.load_retrospective(task_id)
                except Exception:
                    continue
                if retro is None:
                    continue
                content = retro.to_markdown()
                in_worked = False
                in_didnt = False
                in_gaps = False
                for line in content.splitlines():
                    if line.startswith("## What Worked"):
                        in_worked, in_didnt, in_gaps = True, False, False
                    elif line.startswith("## What Didn't"):
                        in_worked, in_didnt, in_gaps = False, True, False
                    elif line.startswith("## Knowledge Gaps"):
                        in_worked, in_didnt, in_gaps = False, False, True
                    elif line.startswith("## "):
                        in_worked, in_didnt, in_gaps = False, False, False
                    if agent_name in line:
                        if in_worked:
                            positive += 1
                        elif in_didnt:
                            negative += 1
                        elif in_gaps:
                            gaps_cited += 1
        else:
            for retro_path in self._retro.list_retrospectives():
                try:
                    content = retro_path.read_text(encoding="utf-8")
                except OSError:
                    continue
                in_worked = False
                in_didnt = False
                in_gaps = False
                for line in content.splitlines():
                    if line.startswith("## What Worked"):
                        in_worked, in_didnt, in_gaps = True, False, False
                    elif line.startswith("## What Didn't"):
                        in_worked, in_didnt, in_gaps = False, True, False
                    elif line.startswith("## Knowledge Gaps"):
                        in_worked, in_didnt, in_gaps = False, False, True
                    elif line.startswith("## "):
                        in_worked, in_didnt, in_gaps = False, False, False
                    if agent_name in line:
                        if in_worked:
                            positive += 1
                        elif in_didnt:
                            negative += 1
                        elif in_gaps:
                            gaps_cited += 1

        return AgentScorecard(
            agent_name=agent_name,
            times_used=times_used,
            first_pass_rate=first_pass_rate,
            retry_rate=stats["avg_retries"],
            gate_pass_rate=stats["gate_pass_rate"],
            total_estimated_tokens=total_tokens,
            avg_tokens_per_use=avg_tokens,
            models_used=stats["models_used"],
            positive_mentions=positive,
            negative_mentions=negative,
            knowledge_gaps_cited=gaps_cited,
        )

    def score_all(self) -> list[AgentScorecard]:
        """Compute scorecards for all agents found in usage logs."""
        summary = self._usage.summary()
        agent_names = list(summary.get("agent_frequency", {}).keys())
        scorecards = [self.score_agent(name) for name in sorted(agent_names)]
        return [sc for sc in scorecards if sc.times_used > 0]

    def generate_report(self) -> str:
        """Generate a full markdown scorecard report."""
        scorecards = self.score_all()
        if not scorecards:
            return "# Agent Performance Scorecards\n\nNo usage data available.\n"

        lines = [
            "# Agent Performance Scorecards",
            "",
            f"Based on {sum(sc.times_used for sc in scorecards)} total agent uses.",
            "",
        ]

        # Group by health
        for health in ("strong", "adequate", "needs-improvement"):
            group = [sc for sc in scorecards if sc.health == health]
            if group:
                lines.append(f"## {health.replace('-', ' ').title()}")
                lines.append("")
                for sc in group:
                    lines.append(sc.to_markdown())

        return "\n".join(lines)

    def detect_trends(self, agent_name: str, window: int = 10) -> str:
        """Detect performance trend for an agent over the last *window* tasks.

        Uses a simple linear regression slope on the agent's first-pass rate
        (1 if zero retries, 0 otherwise) across the most recent *window*
        tasks where the agent participated.

        Returns:
            "improving" if slope > 0.02,
            "degrading" if slope < -0.02,
            "stable" otherwise.
        """
        records = self._usage.read_all()
        # Collect binary success values (1 = first pass, 0 = retry)
        # in chronological order for the given agent.
        successes: list[float] = []
        for rec in records:
            for agent in rec.agents_used:
                if agent.name == agent_name:
                    successes.append(1.0 if agent.retries == 0 else 0.0)

        if len(successes) < 3:
            return "stable"

        # Take the last `window` values
        recent = successes[-window:]

        # Simple linear regression: slope = (n*sum(x*y) - sum(x)*sum(y)) / (n*sum(x^2) - sum(x)^2)
        n = len(recent)
        sum_x = sum(range(n))
        sum_y = sum(recent)
        sum_xy = sum(i * y for i, y in enumerate(recent))
        sum_x2 = sum(i * i for i in range(n))

        denom = n * sum_x2 - sum_x * sum_x
        if denom == 0:
            return "stable"

        slope = (n * sum_xy - sum_x * sum_y) / denom

        if slope > 0.02:
            return "improving"
        if slope < -0.02:
            return "degrading"
        return "stable"

    def write_report(self, path: Path | None = None) -> Path:
        """Write the scorecard report to disk."""
        out_path = (path or Path(".claude/team-context/agent-scorecards.md")).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.generate_report(), encoding="utf-8")
        return out_path
