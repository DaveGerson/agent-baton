"""TriggerEvaluator — determines when enough new data has accumulated to
warrant running the improvement analysis pipeline.

Also provides ``detect_anomalies()`` to scan for agent failure rates, gate
failures, budget overruns, retry spikes, and pattern drift.
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
    """Check whether enough new data has accumulated since the last analysis."""

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
        """Return ``True`` if enough new tasks have been completed since the
        last analysis to warrant a new improvement cycle.

        Rules:
        - At least ``min_tasks_before_analysis`` total tasks must exist.
        - At least ``analysis_interval_tasks`` new tasks must have been
          recorded since the last analysis.
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
        """Scan recent usage data for anomalies.

        Checks:
        - Agent failure rate > agent_failure_threshold (30%)
        - Gate failure rate > gate_failure_threshold (20%)
        - Budget overrun > budget_deviation_threshold (50%)
        - Retry spikes (avg retries > 2.0)
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
