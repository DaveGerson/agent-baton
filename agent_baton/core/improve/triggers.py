"""TriggerEvaluator -- determines when enough new data has accumulated to
warrant running the improvement analysis pipeline.

The trigger evaluator is the gatekeeper of the closed-loop improvement
cycle.  It prevents unnecessary analysis runs (which consume time and
create noise) by tracking how many new tasks have completed since the last
analysis.  It also provides anomaly detection to surface urgent issues
that should trigger immediate review.

Integration:

* :class:`~agent_baton.core.improve.loop.ImprovementLoop` calls
  :meth:`TriggerEvaluator.should_analyze` at the start of each cycle.  If
  the trigger is not met (and ``force=False``), the cycle is skipped.
* After a successful analysis, the loop calls :meth:`mark_analyzed` to
  record the watermark.
* Anomaly detection is always run during a cycle, even when triggered by
  ``force=True``, to ensure urgent issues are surfaced.

Anomaly types detected by :meth:`detect_anomalies`:

* **high_failure_rate** -- agent failure rate exceeds 30% (configurable).
* **high_gate_failure_rate** -- gate failure rate exceeds 20%.
* **budget_overrun** -- actual token usage deviates > 50% from expected
  tier midpoint.
* **retry_spike** -- average retries for an agent exceed 2.0.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.core.observe.usage import UsageLogger
from agent_baton.models.improvement import Anomaly, TriggerConfig
from agent_baton.models.usage import TaskUsageRecord

_DEFAULT_TEAM_CONTEXT = Path(".claude/team-context")
_TRIGGER_STATE_FILE = "improvement-trigger-state.json"


class TriggerEvaluator:
    """Check whether enough new data has accumulated since the last analysis.

    State is persisted to ``improvement-trigger-state.json`` in the
    team-context directory.  The state file contains a single watermark:
    the total task count at the time of the last analysis.

    Args:
        config: Trigger thresholds.  Defaults to :class:`TriggerConfig`
            with ``min_tasks_before_analysis=5``,
            ``analysis_interval_tasks=5``.
        team_context_root: Root directory for team context files.
    """

    def __init__(
        self,
        config: TriggerConfig | None = None,
        team_context_root: Path | None = None,
    ) -> None:
        self._config = config or TriggerConfig()
        self._root = (team_context_root or _DEFAULT_TEAM_CONTEXT).resolve()
        self._state_path = self._root / _TRIGGER_STATE_FILE
        self._log_path = self._root / "usage-log.jsonl"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_analyze(self) -> bool:
        """Return ``True`` if enough new data exists for a new improvement cycle.

        Two conditions must both be met:

        1. At least ``min_tasks_before_analysis`` total tasks must exist
           in the usage log (avoids analysis on tiny datasets).
        2. At least ``analysis_interval_tasks`` new tasks must have been
           recorded since the last analysis (avoids redundant re-analysis).

        Returns:
            ``True`` if both conditions are met and a new improvement
            cycle should be triggered.
        """
        records = self._read_records()
        total = len(records)

        if total < self._config.min_tasks_before_analysis:
            return False

        last_count = self._read_last_analyzed_count()
        new_tasks = total - last_count
        return new_tasks >= self._config.analysis_interval_tasks

    def mark_analyzed(self) -> None:
        """Record the current task count as the last-analyzed watermark."""
        records = self._read_records()
        self._write_state(len(records))

    def detect_anomalies(self) -> list[Anomaly]:
        """Scan all usage data for anomalies that warrant immediate attention.

        Runs four independent checks against the full usage log:

        1. **Per-agent failure rate**: for agents with >= 3 uses, flags
           those whose retry-based failure rate exceeds
           ``agent_failure_threshold`` (default 0.3).  Severity is
           ``"high"`` if > 50%, ``"medium"`` otherwise.

        2. **Retry spike**: for agents with >= 3 uses, flags those whose
           average retry count exceeds 2.0.

        3. **Gate failure rate**: across all tasks, flags if the overall
           gate failure rate exceeds ``gate_failure_threshold`` (default
           0.2).  Severity is ``"high"`` if > 40%.

        4. **Budget overrun**: per-task check comparing actual token usage
           to the tier midpoint implied by the task's risk level.  Flags
           deviations exceeding ``budget_deviation_threshold`` (default 0.5).

        Returns:
            List of :class:`~agent_baton.models.improvement.Anomaly` objects,
            possibly empty if no anomalies are detected.
        """
        records = self._read_records()
        if not records:
            return []

        anomalies: list[Anomaly] = []

        # --- Per-agent failure rate ---
        agent_uses: dict[str, int] = {}
        agent_failures: dict[str, int] = {}
        agent_retries: dict[str, list[int]] = {}

        for rec in records:
            for agent in rec.agents_used:
                agent_uses[agent.name] = agent_uses.get(agent.name, 0) + 1
                if agent.retries > 0:
                    agent_failures[agent.name] = agent_failures.get(agent.name, 0) + 1
                agent_retries.setdefault(agent.name, []).append(agent.retries)

        for name, uses in agent_uses.items():
            if uses < 3:
                continue  # Not enough data

            # Failure rate (any retry counts as a failure attempt)
            failures = agent_failures.get(name, 0)
            failure_rate = failures / uses
            if failure_rate > self._config.agent_failure_threshold:
                anomalies.append(Anomaly(
                    anomaly_type="high_failure_rate",
                    severity="high" if failure_rate > 0.5 else "medium",
                    agent_name=name,
                    metric="failure_rate",
                    current_value=round(failure_rate, 4),
                    threshold=self._config.agent_failure_threshold,
                    sample_size=uses,
                    evidence=[f"{failures}/{uses} tasks had retries"],
                ))

            # Retry spike
            retries = agent_retries.get(name, [])
            if retries:
                avg_retries = sum(retries) / len(retries)
                if avg_retries > 2.0:
                    anomalies.append(Anomaly(
                        anomaly_type="retry_spike",
                        severity="medium",
                        agent_name=name,
                        metric="avg_retries",
                        current_value=round(avg_retries, 4),
                        threshold=2.0,
                        sample_size=uses,
                        evidence=[f"avg {avg_retries:.1f} retries across {uses} uses"],
                    ))

        # --- Gate failure rate ---
        total_gates_passed = sum(r.gates_passed for r in records)
        total_gates_failed = sum(r.gates_failed for r in records)
        total_gates = total_gates_passed + total_gates_failed
        if total_gates > 0:
            gate_failure_rate = total_gates_failed / total_gates
            if gate_failure_rate > self._config.gate_failure_threshold:
                anomalies.append(Anomaly(
                    anomaly_type="high_gate_failure_rate",
                    severity="high" if gate_failure_rate > 0.4 else "medium",
                    metric="gate_failure_rate",
                    current_value=round(gate_failure_rate, 4),
                    threshold=self._config.gate_failure_threshold,
                    sample_size=total_gates,
                    evidence=[f"{total_gates_failed}/{total_gates} gates failed"],
                ))

        # --- Budget overrun ---
        # Compare actual tokens to tier midpoints to detect overruns
        _TIER_MIDPOINTS = {"lean": 25_000, "standard": 275_000, "full": 750_000}
        for rec in records:
            total_tokens = sum(a.estimated_tokens for a in rec.agents_used)
            # Use risk level as a rough proxy for expected tier
            expected_tier = "standard"  # default
            if rec.risk_level == "LOW":
                expected_tier = "lean"
            elif rec.risk_level == "HIGH":
                expected_tier = "full"

            midpoint = _TIER_MIDPOINTS.get(expected_tier, 275_000)
            if midpoint > 0:
                deviation = (total_tokens - midpoint) / midpoint
                if deviation > self._config.budget_deviation_threshold:
                    anomalies.append(Anomaly(
                        anomaly_type="budget_overrun",
                        severity="medium",
                        metric="token_deviation",
                        current_value=round(deviation, 4),
                        threshold=self._config.budget_deviation_threshold,
                        sample_size=1,
                        evidence=[
                            f"Task {rec.task_id}: {total_tokens:,} tokens vs "
                            f"{midpoint:,} expected ({expected_tier} tier)"
                        ],
                    ))

        return anomalies

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read_records(self) -> list[TaskUsageRecord]:
        logger = UsageLogger(self._log_path)
        return logger.read_all()

    def _read_last_analyzed_count(self) -> int:
        if not self._state_path.exists():
            return 0
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return int(data.get("last_analyzed_count", 0))
        except (json.JSONDecodeError, OSError):
            return 0

    def _write_state(self, count: int) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps({"last_analyzed_count": count}, indent=2) + "\n",
            encoding="utf-8",
        )
