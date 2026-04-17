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

Configuration priority (highest to lowest):

1. Explicit ``config`` argument passed to the constructor.
2. ``trigger_config`` block inside ``learned-overrides.json``.
3. Environment variables: ``BATON_MIN_TASKS``, ``BATON_ANALYSIS_INTERVAL``.
4. Compiled-in defaults (``min_tasks_before_analysis=3``,
   ``analysis_interval_tasks=3``).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.core.observe.usage import UsageLogger
from agent_baton.models.improvement import Anomaly, TriggerConfig
from agent_baton.models.usage import TaskUsageRecord
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.storage.protocol import StorageBackend

_log = logging.getLogger(__name__)

_DEFAULT_TEAM_CONTEXT = Path(".claude/team-context")
_TRIGGER_STATE_FILE = "improvement-trigger-state.json"
_OVERRIDES_FILE = "learned-overrides.json"


class TriggerEvaluator:
    """Check whether enough new data has accumulated since the last analysis.

    State is persisted to ``improvement-trigger-state.json`` in the
    team-context directory.  The state file contains a single watermark:
    the total task count at the time of the last analysis.

    Configuration is resolved in priority order:

    1. Explicit *config* argument — highest precedence, used as-is.
    2. ``trigger_config`` block in ``learned-overrides.json`` — lets operators
       tune thresholds per-project without redeploying.
    3. ``BATON_MIN_TASKS`` / ``BATON_ANALYSIS_INTERVAL`` env vars.
    4. Compiled-in defaults (3 / 3).

    Args:
        config: Trigger thresholds.  When ``None``, the evaluator resolves
            configuration from ``learned-overrides.json`` and env vars.
        team_context_root: Root directory for team context files.
        storage: Optional :class:`StorageBackend`.  When provided,
            ``_read_records`` reads from ``storage.read_usage()`` instead
            of the JSONL flat file.  Falls back to JSONL on any exception.
        bead_store: Optional bead store.  When provided, ``should_analyze``
            checks for new beads created since the last analysis timestamp
            as a supplementary trigger signal (threshold: >= 3 beads).
        ledger: Optional learning-issue ledger.  When provided,
            ``should_analyze`` checks for open issues updated since the
            last analysis timestamp as a supplementary trigger signal
            (threshold: >= 1 issue).
    """

    def __init__(
        self,
        config: TriggerConfig | None = None,
        team_context_root: Path | None = None,
        storage: "StorageBackend | None" = None,
        bead_store=None,
        ledger=None,
    ) -> None:
        self._root = (team_context_root or _DEFAULT_TEAM_CONTEXT).resolve()
        self._state_path = self._root / _TRIGGER_STATE_FILE
        self._log_path = self._root / "usage-log.jsonl"
        self._storage = storage
        self._bead_store = bead_store
        self._ledger = ledger

        if config is not None:
            self._config = config
        else:
            # Start from env-var defaults, then let learned-overrides.json
            # override only the fields it explicitly sets.
            self._config = self._load_config_from_overrides(
                base=TriggerConfig.from_env()
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def should_analyze(self) -> bool:
        """Return ``True`` if enough new data exists for a new improvement cycle.

        Uses a composite signal from up to three data sources:

        1. **Usage records** (primary): at least ``min_tasks_before_analysis``
           total tasks must exist AND ``analysis_interval_tasks`` new tasks
           since the last analysis.
        2. **Learning issues** (supplementary): any open issue updated since
           the last analysis timestamp.
        3. **Beads** (supplementary): at least 3 beads created since the last
           analysis timestamp.

        Any single signal crossing its threshold triggers analysis, provided
        the minimum baseline data requirement (signal 1's total check) is met.

        Returns:
            ``True`` if any signal indicates new data worth analysing.
        """
        records = self._read_records()
        total = len(records)

        last_count = self._read_last_analyzed_count()

        # Auto-reset stale watermark (transition from JSONL to SQLite may
        # have left the watermark higher than the new source's count).
        if last_count > total:
            _log.info(
                "TriggerEvaluator: watermark (%d) > total records (%d), "
                "resetting to 0 (data source migration detected)",
                last_count, total,
            )
            self._write_state(0)
            last_count = 0

        # Baseline gate: need minimum data before any analysis.
        if total < self._config.min_tasks_before_analysis:
            return False

        # Signal 1: usage record count delta (primary).
        new_tasks = total - last_count
        if new_tasks >= self._config.analysis_interval_tasks:
            return True

        # Supplementary signals require a prior analysis timestamp.
        watermark_ts = self._read_last_analyzed_at()
        if watermark_ts is None:
            return False

        # Signal 2: learning issues updated since last analysis.
        if self._ledger is not None:
            try:
                open_issues = self._ledger.get_open_issues()
                new_issues = sum(
                    1 for i in open_issues if i.last_seen > watermark_ts
                )
                if new_issues >= 1:
                    _log.debug(
                        "Trigger: %d learning issue(s) updated since %s",
                        new_issues, watermark_ts,
                    )
                    return True
            except Exception as exc:
                _log.debug("Trigger: ledger query failed: %s", exc)

        # Signal 3: beads created since last analysis.
        if self._bead_store is not None:
            try:
                recent_beads = self._bead_store.query(limit=50)
                new_beads = sum(
                    1 for b in recent_beads if b.created_at > watermark_ts
                )
                if new_beads >= 3:
                    _log.debug(
                        "Trigger: %d bead(s) created since %s",
                        new_beads, watermark_ts,
                    )
                    return True
            except Exception as exc:
                _log.debug("Trigger: bead_store query failed: %s", exc)

        return False

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

    def reset_watermark(self) -> None:
        """Reset the last-analyzed watermark to zero.

        Forces :meth:`should_analyze` to consider all existing tasks as new
        on the next call.  Useful when the state file has drifted (e.g. a
        usage log was truncated) or after a fresh install.
        """
        self._write_state(0)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_config_from_overrides(self, base: TriggerConfig) -> TriggerConfig:
        """Overlay ``trigger_config`` from ``learned-overrides.json`` onto *base*.

        Only keys that are explicitly present in the overrides file are applied;
        absent keys leave *base* values intact.  Errors reading the file are
        silently ignored (best-effort, non-blocking).

        Args:
            base: Starting :class:`TriggerConfig` (from env vars or defaults).

        Returns:
            A new :class:`TriggerConfig` with any overrides applied.
        """
        overrides_path = self._root / _OVERRIDES_FILE
        if not overrides_path.exists():
            return base
        try:
            data = json.loads(overrides_path.read_text(encoding="utf-8"))
            tc_data: dict = data.get("trigger_config", {})
            if not tc_data:
                return base
            # Build a merged dict: start from base, overlay explicit keys.
            merged = base.to_dict()
            for key in (
                "min_tasks_before_analysis",
                "analysis_interval_tasks",
                "agent_failure_threshold",
                "gate_failure_threshold",
                "budget_deviation_threshold",
                "confidence_threshold",
            ):
                if key in tc_data:
                    merged[key] = tc_data[key]
            result = TriggerConfig.from_dict(merged)
            _log.debug(
                "TriggerEvaluator: loaded trigger_config from overrides "
                "(min_tasks=%d, interval=%d)",
                result.min_tasks_before_analysis,
                result.analysis_interval_tasks,
            )
            return result
        except Exception as exc:  # noqa: BLE001
            _log.debug("TriggerEvaluator: failed to load overrides (%s)", exc)
            return base

    def _read_records(self) -> list[TaskUsageRecord]:
        if self._storage is not None:
            try:
                return self._storage.read_usage()
            except Exception:
                _log.debug("storage.read_usage() failed, falling back to JSONL")
        return UsageLogger(self._log_path).read_all()

    def _read_last_analyzed_count(self) -> int:
        if not self._state_path.exists():
            return 0
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return int(data.get("last_analyzed_count", 0))
        except (json.JSONDecodeError, OSError):
            return 0

    def _read_last_analyzed_at(self) -> str | None:
        """Return the ISO timestamp of the last analysis, or ``None``."""
        if not self._state_path.exists():
            return None
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return data.get("last_analyzed_at")
        except (json.JSONDecodeError, OSError):
            return None

    def _write_state(self, count: int) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._state_path.write_text(
            json.dumps({
                "last_analyzed_count": count,
                "last_analyzed_at": now,
            }, indent=2) + "\n",
            encoding="utf-8",
        )
