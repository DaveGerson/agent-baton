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
        """Read all usage records, merging the JSONL log with SQLite (F098).

        ``ExecutionEngine`` may be built with a SQLite storage backend (the
        CLI path and — since the F098 fix — the daemon path via
        ``ExecutionContext.build``) or with none (legacy/file mode); either
        way it writes usage through exactly one sink, never both. A
        ``UsageLogger`` constructed against a bare JSONL path — as
        ``PerformanceScorer``, ``BudgetTuner``, and ``PatternLearner`` all do
        — would therefore see only half of a project's usage history unless
        it also reaches for the SQLite rows.

        When a ``baton.db`` already exists next to this log's directory, its
        ``usage_records`` are merged in, deduplicated by ``task_id`` with the
        SQLite copy taking precedence — the same merge
        :class:`~agent_baton.core.observe.dashboard.DashboardGenerator`
        already performs when an explicit storage backend is supplied.  When
        no ``baton.db`` is present (pure legacy/file-mode projects), behavior
        is unchanged: only the JSONL file is read, and no database is
        created as a read-side effect.

        Blank lines and malformed JSON lines are silently skipped.
        """
        jsonl_records = self._read_jsonl()
        storage_records = self._read_storage_records()
        if not storage_records:
            return jsonl_records

        # Deduplicate: storage wins; exclude task_ids already in storage.
        storage_task_ids = {r.task_id for r in storage_records}
        jsonl_only = [r for r in jsonl_records if r.task_id not in storage_task_ids]
        return storage_records + jsonl_only

    def _read_jsonl(self) -> list[TaskUsageRecord]:
        """Read usage records from the JSONL log file only.

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

    def _read_storage_records(self) -> list[TaskUsageRecord]:
        """Best-effort read of ``usage_records`` from a sibling ``baton.db``.

        Only attempted when a database file already exists next to this
        logger's directory — this method must never create one as a side
        effect of a read.  Any failure (corrupt DB, schema mismatch) is
        swallowed and treated as "no SQLite records", matching
        ``DashboardGenerator``'s fallback-to-JSONL-only behavior.
        """
        db_path = self._log_path.parent / "baton.db"
        if not db_path.exists():
            return []
        try:
            from agent_baton.core.storage import get_project_storage

            storage = get_project_storage(self._log_path.parent)
            return storage.read_usage()
        except Exception:
            return []

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
