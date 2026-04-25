"""UsageLogger -- append and read JSONL usage records.

The usage log is the primary quantitative data source for the learning
pipeline.  Every completed orchestrated task produces a single
:class:`~agent_baton.models.usage.TaskUsageRecord` that captures which
agents were used, how many tokens they consumed, how many retries they
needed, gate results, and the overall task outcome.

Downstream consumers:

* :class:`~agent_baton.core.learn.pattern_learner.PatternLearner` reads
  usage records to derive recurring orchestration patterns grouped by
  sequencing mode.
* :class:`~agent_baton.core.learn.budget_tuner.BudgetTuner` reads usage
  records to recommend budget-tier adjustments based on historical token
  consumption.
* :class:`~agent_baton.core.improve.scoring.PerformanceScorer` reads
  per-agent usage data to build agent scorecards.
* :class:`~agent_baton.core.observe.dashboard.DashboardGenerator`
  aggregates usage records into a human-readable Markdown dashboard.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord


class UsageLogger:
    """Append and read JSONL usage records.

    Each line in the log file is a single JSON object representing one
    TaskUsageRecord.  The file format is JSONL (newline-delimited JSON),
    not a JSON array, so records can be appended without loading the whole
    file into memory.
    """

    _DEFAULT_LOG_PATH = Path(".claude/team-context/usage-log.jsonl")

    def __init__(self, log_path: Path | None = None) -> None:
        self._log_path = (log_path or self._DEFAULT_LOG_PATH).resolve()

    @property
    def log_path(self) -> Path:
        return self._log_path

    # ── Write ──────────────────────────────────────────────────────────────

    def log(self, record: TaskUsageRecord) -> None:
        """Append a usage record as a JSON line to the log file.

        Creates the parent directory if it does not exist.  Each call
        appends exactly one line; the file is opened in append mode so
        concurrent writers from different sessions do not corrupt data.

        Tenancy fields on the record are populated from the active
        :class:`~agent_baton.core.runtime.tenancy_context.TenancyContext`
        when the caller has not supplied them explicitly.  This keeps
        legacy callers that construct ``TaskUsageRecord`` without
        identity information from emitting all-NULL tenancy rows.

        Args:
            record: The completed task's usage data to persist.
        """
        from agent_baton.core.runtime.tenancy_context import get_current_tenancy

        ctx = get_current_tenancy()
        if not record.org_id:
            record.org_id = ctx.org_id
        if not record.team_id:
            record.team_id = ctx.team_id
        if not record.user_id:
            record.user_id = ctx.user_id
        if not record.spec_author_id:
            record.spec_author_id = ctx.spec_author_id
        if not record.cost_center:
            record.cost_center = ctx.cost_center

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.to_dict(), separators=(",", ":"))
        with self._log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ── Read ───────────────────────────────────────────────────────────────

    def read_all(self) -> list[TaskUsageRecord]:
        """Read all usage records from the log file.

        Blank lines and malformed JSON lines are silently skipped.
        Returns an empty list if the file does not exist.
        """
        if not self._log_path.exists():
            return []

        records: list[TaskUsageRecord] = []
        with self._log_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    records.append(TaskUsageRecord.from_dict(data))
                except (json.JSONDecodeError, KeyError):
                    # Skip malformed lines gracefully
                    continue
        return records

    def read_recent(self, count: int = 10) -> list[TaskUsageRecord]:
        """Read the N most recent usage records."""
        all_records = self.read_all()
        return all_records[-count:] if count < len(all_records) else all_records

    # ── Aggregation ────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """Compute aggregate stats from all records.

        Returns a dict with:
            total_tasks, total_agents_used, total_estimated_tokens,
            avg_agents_per_task, avg_retries_per_task,
            outcome_counts, risk_level_counts, agent_frequency
        """
        records = self.read_all()
        total_tasks = len(records)

        if total_tasks == 0:
            return {
                "total_tasks": 0,
                "total_agents_used": 0,
                "total_estimated_tokens": 0,
                "avg_agents_per_task": 0.0,
                "avg_retries_per_task": 0.0,
                "outcome_counts": {},
                "risk_level_counts": {},
                "agent_frequency": {},
            }

        total_agents_used = 0
        total_estimated_tokens = 0
        total_retries = 0
        outcome_counts: dict[str, int] = {}
        risk_level_counts: dict[str, int] = {}
        agent_frequency: dict[str, int] = {}

        for record in records:
            total_agents_used += len(record.agents_used)

            if record.outcome:
                outcome_counts[record.outcome] = outcome_counts.get(record.outcome, 0) + 1

            risk_level_counts[record.risk_level] = (
                risk_level_counts.get(record.risk_level, 0) + 1
            )

            for agent in record.agents_used:
                total_estimated_tokens += agent.estimated_tokens
                total_retries += agent.retries
                agent_frequency[agent.name] = agent_frequency.get(agent.name, 0) + 1

        return {
            "total_tasks": total_tasks,
            "total_agents_used": total_agents_used,
            "total_estimated_tokens": total_estimated_tokens,
            "avg_agents_per_task": round(total_agents_used / total_tasks, 2),
            "avg_retries_per_task": round(total_retries / total_tasks, 2),
            "outcome_counts": outcome_counts,
            "risk_level_counts": risk_level_counts,
            "agent_frequency": agent_frequency,
        }

    def agent_stats(self, agent_name: str) -> dict:
        """Compute aggregate statistics for a specific agent across all tasks.

        Scans every :class:`~agent_baton.models.usage.TaskUsageRecord` and
        collects metrics for agent entries whose ``name`` matches
        *agent_name*.

        Args:
            agent_name: Exact agent name to filter by (case-sensitive).

        Returns:
            A dict with the following keys:

            * ``times_used`` -- total number of tasks the agent participated in.
            * ``total_retries`` -- sum of retries across all participations.
            * ``avg_retries`` -- mean retries per participation, rounded to 2
              decimal places.
            * ``gate_pass_rate`` -- fraction of gate results that are ``"PASS"``,
              or ``None`` if the agent never went through a gate.
            * ``models_used`` -- dict mapping model name to usage count.
        """
        records = self.read_all()

        times_used = 0
        total_retries = 0
        gate_passes = 0
        gate_total = 0
        models_used: dict[str, int] = {}

        for record in records:
            for agent in record.agents_used:
                if agent.name != agent_name:
                    continue
                times_used += 1
                total_retries += agent.retries
                models_used[agent.model] = models_used.get(agent.model, 0) + 1
                for result in agent.gate_results:
                    gate_total += 1
                    if result == "PASS":
                        gate_passes += 1

        gate_pass_rate = (gate_passes / gate_total) if gate_total > 0 else None
        avg_retries = (total_retries / times_used) if times_used > 0 else 0.0

        return {
            "times_used": times_used,
            "total_retries": total_retries,
            "avg_retries": round(avg_retries, 2),
            "gate_pass_rate": gate_pass_rate,
            "models_used": models_used,
        }
