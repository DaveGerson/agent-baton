"""Performance scorer -- computes per-agent scorecards from usage and retrospective data.

The scorer bridges observational data and improvement actions.  It reads
quantitative metrics from :class:`~agent_baton.core.observe.usage.UsageLogger`
(retries, tokens, gate results) and qualitative signals from
:class:`~agent_baton.core.observe.retrospective.RetrospectiveEngine`
(positive/negative mentions, knowledge gaps cited) to produce an
:class:`AgentScorecard` for each agent.

Scoring methodology:

* **first_pass_rate** -- fraction of agent uses with zero retries.  This is
  the primary quality metric: higher means the agent produces acceptable
  output on the first attempt.
* **retry_rate** -- average retries per use.  High retry rates indicate
  unclear instructions or mismatched expectations.
* **gate_pass_rate** -- fraction of gate checks the agent's output passed.
  Low rates indicate systematic output quality issues.
* **health** -- categorical rating derived from the above:

  - ``"strong"`` -- first_pass_rate >= 0.8 AND zero negative mentions.
  - ``"adequate"`` -- first_pass_rate >= 0.5.
  - ``"needs-improvement"`` -- first_pass_rate < 0.5.
  - ``"unused"`` -- no usage data.

* **trend detection** -- :meth:`PerformanceScorer.detect_trends` applies
  simple linear regression over the last *N* tasks to classify the agent's
  trajectory as ``"improving"``, ``"degrading"``, or ``"stable"``.

Downstream consumers:

* :class:`~agent_baton.core.learn.recommender.Recommender` uses
  ``needs-improvement`` scorecards to generate routing recommendations.
* The ``learning-analyst`` agent (dispatched via ``baton learn run-cycle``)
  reads scorecards plus retrospectives to produce prompt-improvement
  proposals -- L2.1 (bd-362f) retired the in-process ``PromptEvolutionEngine``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.models.retrospective import TeamCompositionRecord

if TYPE_CHECKING:
    from agent_baton.core.storage.protocol import StorageBackend


@dataclass
class AgentScorecard:
    """Performance scorecard for a single agent.

    Combines quantitative metrics from usage logs with qualitative signals
    from retrospective analysis into a single assessment.

    Scoring thresholds:

    * ``first_pass_rate >= 0.8`` with no negative mentions = ``"strong"``.
    * ``first_pass_rate >= 0.5`` = ``"adequate"``.
    * ``first_pass_rate < 0.5`` = ``"needs-improvement"`` -- this agent
      is a candidate for prompt evolution or routing weight reduction.

    Attributes:
        agent_name: The agent being scored.
        times_used: Total number of task participations.
        first_pass_rate: Fraction of uses with zero retries (0.0 -- 1.0).
            Higher is better; 1.0 means the agent never needed a retry.
        retry_rate: Average retries per use.  Lower is better.
        gate_pass_rate: Fraction of gate checks passed, or ``None`` if the
            agent never went through a gate.
        total_estimated_tokens: Cumulative token consumption.
        avg_tokens_per_use: Mean tokens per participation.
        models_used: Model name to usage count mapping.
        positive_mentions: Count of lines mentioning this agent in
            "What Worked" retrospective sections.
        negative_mentions: Count of lines mentioning this agent in
            "What Didn't Work" retrospective sections.
        knowledge_gaps_cited: Count of knowledge gap entries citing this
            agent in retrospective data.
    """
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
    # Bead quality metrics (F12, populated when bead_store is available)
    avg_bead_quality: float = 0.0
    bead_count: int = 0

    @property
    def health(self) -> str:
        """Categorical health rating derived from quantitative metrics.

        Returns:
            One of ``"unused"``, ``"strong"``, ``"adequate"``, or
            ``"needs-improvement"``.  See class docstring for threshold
            definitions.
        """
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


@dataclass
class TeamScorecard:
    """Performance scorecard for a team composition.

    Aggregates effectiveness metrics across all retrospectives where
    a specific team composition was used.

    Attributes:
        agents: Canonical sorted list of agent names in the team.
        times_used: Number of times this composition appeared.
        success_rate: Fraction of usages with outcome ``"success"``.
        avg_token_cost: Mean estimated token cost per team step.
        task_types: Task types where this team was deployed.
        health: Categorical rating derived from success_rate.
    """

    agents: list[str]
    times_used: int = 0
    success_rate: float = 0.0
    avg_token_cost: int = 0
    task_types: list[str] = field(default_factory=list)

    @property
    def health(self) -> str:
        if self.times_used == 0:
            return "unused"
        if self.success_rate >= 0.8:
            return "strong"
        if self.success_rate >= 0.5:
            return "adequate"
        return "needs-improvement"

    def to_markdown(self) -> str:
        agents_str = " + ".join(self.agents)
        lines = [
            f"### {agents_str}",
            f"- **Health:** {self.health}",
            f"- **Uses:** {self.times_used}",
            f"- **Success rate:** {self.success_rate:.0%}",
            f"- **Avg tokens/use:** {self.avg_token_cost:,}",
        ]
        if self.task_types:
            lines.append(f"- **Task types:** {', '.join(self.task_types)}")
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

    def score_agent(self, agent_name: str, *, bead_store=None) -> AgentScorecard:
        """Compute a full scorecard for a single agent.

        Data sources:

        1. **Usage log** -- provides ``times_used``, ``first_pass_rate``,
           ``retry_rate``, ``gate_pass_rate``, ``total_estimated_tokens``,
           ``avg_tokens_per_use``, and ``models_used``.
        2. **Retrospectives** (from storage backend or filesystem) --
           provides ``positive_mentions``, ``negative_mentions``, and
           ``knowledge_gaps_cited`` by scanning Markdown sections.

        When a ``StorageBackend`` is configured, retrospective data is read
        from SQLite (which may be the only location in SQLite-mode projects).
        Otherwise, filesystem-based retrospective Markdown files are scanned.

        Args:
            agent_name: Exact agent name to score (case-sensitive).

        Returns:
            A populated :class:`AgentScorecard`.  Returns a scorecard with
            ``times_used=0`` if the agent has no usage history.
        """
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

        # Bead quality metrics (F12): average quality_score of beads this
        # agent produced, and total bead count.  Only populated when a
        # bead_store is provided — gracefully defaults to 0 otherwise.
        avg_bead_quality = 0.0
        bead_count = 0
        if bead_store is not None:
            try:
                agent_beads = bead_store.query(agent_name=agent_name, limit=500)
                bead_count = len(agent_beads)
                if agent_beads:
                    scores = [b.quality_score for b in agent_beads
                              if b.quality_score != 0.0]
                    avg_bead_quality = (
                        sum(scores) / len(scores) if scores else 0.0
                    )
            except Exception:
                pass

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
            avg_bead_quality=avg_bead_quality,
            bead_count=bead_count,
        )

    def score_all(self) -> list[AgentScorecard]:
        """Compute scorecards for all agents found in usage logs.

        Discovers agent names from the usage summary's ``agent_frequency``
        dict and scores each one.  Only agents with ``times_used > 0`` are
        included in the result.

        Returns:
            List of scorecards sorted alphabetically by agent name,
            excluding unused agents.
        """
        summary = self._usage.summary()
        agent_names = list(summary.get("agent_frequency", {}).keys())
        scorecards = [self.score_agent(name) for name in sorted(agent_names)]
        return [sc for sc in scorecards if sc.times_used > 0]

    def generate_report(self) -> str:
        """Generate a full Markdown scorecard report.

        Groups agents by health category (strong, adequate,
        needs-improvement) and renders each scorecard's Markdown
        representation.

        Returns:
            A complete Markdown document.  Returns a placeholder message
            if no usage data is available.
        """
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

        Algorithm:
            Collects a binary success vector (1.0 = zero retries, 0.0 =
            had retries) for each task where the agent participated, takes
            the last *window* values, and computes the OLS linear regression
            slope.  The slope represents the per-task rate of change in
            first-pass success probability.

        Thresholds:
            * ``slope > 0.02`` -- ``"improving"`` (success rate rising by
              more than 2 percentage points per task).
            * ``slope < -0.02`` -- ``"degrading"`` (success rate falling).
            * Otherwise -- ``"stable"``.

        A minimum of 3 data points is required; with fewer, the result is
        always ``"stable"`` since the trend is not statistically meaningful.

        Args:
            agent_name: Exact agent name to analyse.
            window: Number of most-recent participations to consider.

        Returns:
            One of ``"improving"``, ``"degrading"``, or ``"stable"``.
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

    # ── Team-level scoring ─────────────────────────────────────────────────

    def score_teams(self) -> list[TeamScorecard]:
        """Compute scorecards for all team compositions found in retrospectives.

        Aggregates :class:`TeamCompositionRecord` entries across all
        retrospectives to identify recurring team compositions and
        their effectiveness.

        Returns:
            List of team scorecards sorted by times_used (descending).
        """
        compositions = self._collect_team_compositions()

        # Group by canonical agent combo (sorted tuple).
        groups: dict[tuple[str, ...], list[TeamCompositionRecord]] = {}
        for comp in compositions:
            key = tuple(sorted(comp.agents))
            groups.setdefault(key, []).append(comp)

        scorecards: list[TeamScorecard] = []
        for combo, records in groups.items():
            successes = [r for r in records if r.outcome == "success"]
            success_rate = len(successes) / len(records) if records else 0.0

            token_source = [r for r in records if r.token_cost > 0]
            avg_tokens = (
                sum(r.token_cost for r in token_source) // len(token_source)
                if token_source
                else 0
            )

            task_types = sorted({
                r.task_type for r in records
                if r.task_type
            })

            scorecards.append(TeamScorecard(
                agents=list(combo),
                times_used=len(records),
                success_rate=round(success_rate, 4),
                avg_token_cost=avg_tokens,
                task_types=task_types,
            ))

        scorecards.sort(key=lambda s: s.times_used, reverse=True)
        return scorecards

    def generate_team_report(self) -> str:
        """Generate a Markdown team composition effectiveness report.

        Groups team compositions by health rating and renders each
        team scorecard.

        Returns:
            A complete Markdown document.
        """
        scorecards = self.score_teams()
        if not scorecards:
            return (
                "# Team Composition Scorecards\n\n"
                "No team composition data available.\n"
            )

        lines = [
            "# Team Composition Scorecards",
            "",
            f"Based on {sum(sc.times_used for sc in scorecards)} total team steps.",
            "",
        ]

        for health in ("strong", "adequate", "needs-improvement"):
            group = [sc for sc in scorecards if sc.health == health]
            if group:
                lines.append(f"## {health.replace('-', ' ').title()}")
                lines.append("")
                for sc in group:
                    lines.append(sc.to_markdown())

        return "\n".join(lines)

    def _collect_team_compositions(self) -> list[TeamCompositionRecord]:
        """Collect all team composition records from retrospectives.

        Reads retrospective JSON sidecars via the ``RetrospectiveEngine``.
        Each sidecar may contain a ``team_compositions`` list (added in
        Phase 1 of the daemon mode spec).  Older sidecars without the
        field are silently skipped.
        """
        import json
        from agent_baton.models.retrospective import Retrospective

        compositions: list[TeamCompositionRecord] = []

        for retro_path in self._retro.list_retrospectives():
            json_path = retro_path.with_suffix(".json")
            if not json_path.exists():
                continue
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
                retro = Retrospective.from_dict(data)
                compositions.extend(retro.team_compositions)
            except (json.JSONDecodeError, KeyError, TypeError, OSError):
                continue

        return compositions
