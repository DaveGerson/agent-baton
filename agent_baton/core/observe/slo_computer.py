"""SLI / SLO / error-budget computation (O1.5).

The computer is a *thin* read-side calculator:

* SLI numbers come from the existing observability tables -- ``step_results``
  (dispatch outcomes), ``gate_results`` (gate pass/fail), and the
  ``executions`` table (engine uptime checkpoints).  No new instrumentation
  is added.
* The error-budget formula is the standard SRE form -- remaining budget =
  ``(sli_value - target) / (1 - target)`` clipped to ``[0, 1]``.
* Burn detection compares the latest measurement against the previous one
  for the same SLO and emits an :class:`ErrorBudgetBurn` row when the
  observed budget consumption per hour exceeds a configurable threshold.

The computer is **observation only** -- it never raises gates, fails
phases, or asks the operator anything.  Its outputs feed the
``baton slo`` CLI and the Prometheus exposition (see
:mod:`agent_baton.core.observe.prometheus`).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_baton.core.storage.connection import ConnectionManager
from agent_baton.core.storage.schema import PROJECT_SCHEMA_DDL, SCHEMA_VERSION
from agent_baton.core.storage.slo_store import SLOStore
from agent_baton.models.slo import (
    ErrorBudgetBurn,
    SLODefinition,
    SLOMeasurement,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SLIResult:
    """Lightweight container for a raw SLI reading.

    Attributes:
        value: The observed ratio in ``[0.0, 1.0]``.  ``1.0`` when the
            sample is empty -- "no data" is treated as "not failing".
        sample_size: Number of underlying rows observed.
    """

    value: float
    sample_size: int


# ---------------------------------------------------------------------------
# SLOComputer
# ---------------------------------------------------------------------------


class SLOComputer:
    """Compute SLI values, SLO measurements, and error-budget burns.

    The computer reads directly from the per-project ``baton.db`` so it
    does not depend on the engine being live.  It uses its own
    :class:`ConnectionManager` (configured against the project DDL) so
    that the SLO tables exist before any read or write.

    Attributes:
        store: :class:`SLOStore` used for measurement / burn persistence.
        burn_threshold_per_hour: Burn rate above which a measurement
            triggers a new :class:`ErrorBudgetBurn` row.  Defaults to
            ``0.02`` -- 2% of the budget consumed per hour, the standard
            SRE "fast-burn" alerting threshold.
    """

    SUPPORTED_SLIS: tuple[str, ...] = (
        "dispatch_success_rate",
        "gate_pass_rate",
        "engine_uptime",
    )

    def __init__(
        self,
        db_path: Path,
        *,
        burn_threshold_per_hour: float = 0.02,
    ) -> None:
        self._conn_mgr = ConnectionManager(db_path)
        self._conn_mgr.configure_schema(PROJECT_SCHEMA_DDL, SCHEMA_VERSION)
        self.store = SLOStore(db_path)
        self.burn_threshold_per_hour = float(burn_threshold_per_hour)

    @property
    def db_path(self) -> Path:
        return self._conn_mgr.db_path

    def close(self) -> None:
        self._conn_mgr.close()
        self.store.close()

    # ------------------------------------------------------------------
    # Window helpers
    # ------------------------------------------------------------------

    def _window_bounds(self, window_days: int) -> tuple[str, str]:
        end = _now_utc()
        start = end - timedelta(days=int(window_days))
        return _iso(start), _iso(end)

    def _conn(self) -> sqlite3.Connection:
        return self._conn_mgr.get_connection()

    # ------------------------------------------------------------------
    # Raw SLI computations
    # ------------------------------------------------------------------

    def compute_dispatch_success_rate(self, window_days: int) -> SLIResult:
        """% of step results that ended in status ``complete``.

        Counts only terminal statuses (``complete`` or ``failed``).
        Skipped / pending steps are excluded so retries do not penalise
        the SLI.
        """
        start, _ = self._window_bounds(window_days)
        sql = """
        SELECT
            SUM(CASE WHEN status = 'complete' THEN 1 ELSE 0 END) AS ok,
            SUM(CASE WHEN status IN ('complete', 'failed') THEN 1 ELSE 0 END) AS total
        FROM step_results
        WHERE completed_at >= ?
        """
        row = self._conn().execute(sql, (start,)).fetchone()
        total = int((row["total"] if row else 0) or 0)
        ok = int((row["ok"] if row else 0) or 0)
        if total == 0:
            return SLIResult(value=1.0, sample_size=0)
        return SLIResult(value=ok / total, sample_size=total)

    def compute_gate_pass_rate(self, window_days: int) -> SLIResult:
        """% of gate results with ``passed = 1``."""
        start, _ = self._window_bounds(window_days)
        sql = """
        SELECT
            SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END) AS ok,
            COUNT(*) AS total
        FROM gate_results
        WHERE checked_at >= ?
        """
        row = self._conn().execute(sql, (start,)).fetchone()
        total = int((row["total"] if row else 0) or 0)
        ok = int((row["ok"] if row else 0) or 0)
        if total == 0:
            return SLIResult(value=1.0, sample_size=0)
        return SLIResult(value=ok / total, sample_size=total)

    def compute_engine_uptime(self, window_days: int) -> SLIResult:
        """% of execution rows whose status is not ``failed``.

        Each ``executions`` row is treated as one checkpoint.  Statuses
        like ``complete``, ``running``, and ``paused`` count as healthy;
        only ``failed`` counts against the SLI.
        """
        start, _ = self._window_bounds(window_days)
        sql = """
        SELECT
            SUM(CASE WHEN status != 'failed' THEN 1 ELSE 0 END) AS ok,
            COUNT(*) AS total
        FROM executions
        WHERE started_at >= ?
        """
        row = self._conn().execute(sql, (start,)).fetchone()
        total = int((row["total"] if row else 0) or 0)
        ok = int((row["ok"] if row else 0) or 0)
        if total == 0:
            return SLIResult(value=1.0, sample_size=0)
        return SLIResult(value=ok / total, sample_size=total)

    # ------------------------------------------------------------------
    # Dispatch table
    # ------------------------------------------------------------------

    def _compute_sli(self, sli_query: str, window_days: int) -> SLIResult:
        if sli_query == "dispatch_success_rate":
            return self.compute_dispatch_success_rate(window_days)
        if sli_query == "gate_pass_rate":
            return self.compute_gate_pass_rate(window_days)
        if sli_query == "engine_uptime":
            return self.compute_engine_uptime(window_days)
        raise ValueError(
            f"Unknown sli_query '{sli_query}'. "
            f"Supported: {', '.join(self.SUPPORTED_SLIS)}"
        )

    # ------------------------------------------------------------------
    # SLO measurement
    # ------------------------------------------------------------------

    def compute_measurement(self, slo_def: SLODefinition) -> SLOMeasurement:
        """Compute and return a fresh :class:`SLOMeasurement` (not persisted)."""
        sli = self._compute_sli(slo_def.sli_query, slo_def.window_days)
        budget_remaining = self.compute_error_budget_remaining(
            slo_def.target, sli.value
        )
        start, end = self._window_bounds(slo_def.window_days)
        return SLOMeasurement(
            slo_name=slo_def.name,
            window_start=start,
            window_end=end,
            sli_value=sli.value,
            target=slo_def.target,
            is_meeting=sli.value >= slo_def.target,
            error_budget_remaining_pct=budget_remaining,
            computed_at=end,
            sample_size=sli.sample_size,
        )

    @staticmethod
    def compute_error_budget_remaining(target: float, sli_value: float) -> float:
        """Standard SRE error-budget remaining formula.

        ``(sli_value - target) / (1 - target)`` clipped to ``[0, 1]``.

        Edge case: when ``target >= 1.0`` the formula is undefined --
        return ``1.0`` if the SLI is also ``>= 1.0`` (perfect), else
        ``0.0`` (any failure exhausts an unattainable budget).
        """
        if target >= 1.0:
            return 1.0 if sli_value >= 1.0 else 0.0
        raw = (sli_value - target) / (1.0 - target)
        if raw < 0.0:
            return 0.0
        if raw > 1.0:
            return 1.0
        return raw

    # ------------------------------------------------------------------
    # Burn detection + persistence
    # ------------------------------------------------------------------

    def compute_error_budget(
        self,
        slo_def: SLODefinition,
        measurement: SLOMeasurement,
        *,
        incident_id: str | None = None,
    ) -> ErrorBudgetBurn | None:
        """Detect a burn by comparing against the previous measurement.

        If the previous measurement for this SLO had more budget remaining
        than the current one, compute the burn rate per hour over the
        elapsed wall-clock time.  When the rate exceeds
        ``burn_threshold_per_hour``, return (and persist) an
        :class:`ErrorBudgetBurn` row.

        Returns ``None`` when no burn is detected (or there is no prior
        measurement to diff against).
        """
        previous = self._previous_measurement(slo_def.name, before_id=None)
        if previous is None:
            return None

        consumed = previous.error_budget_remaining_pct - measurement.error_budget_remaining_pct
        if consumed <= 0.0:
            return None

        hours = self._hours_between(previous.computed_at, measurement.computed_at)
        if hours <= 0.0:
            return None

        burn_rate = consumed / hours
        if burn_rate < self.burn_threshold_per_hour:
            return None

        burn = ErrorBudgetBurn(
            slo_name=slo_def.name,
            burn_rate=burn_rate,
            budget_consumed_pct=consumed,
            started_at=previous.computed_at,
            ended_at=measurement.computed_at,
            incident_id=incident_id,
        )
        self.store.insert_burn(burn)
        return burn

    # ------------------------------------------------------------------
    # Convenience -- measure + persist + maybe burn in one call
    # ------------------------------------------------------------------

    def measure_and_persist(
        self,
        slo_def: SLODefinition,
        *,
        incident_id: str | None = None,
    ) -> tuple[SLOMeasurement, ErrorBudgetBurn | None]:
        """Compute, persist, and (if applicable) record a burn.

        Returns the ``(measurement, burn)`` pair.  The burn is ``None``
        when none was triggered.
        """
        measurement = self.compute_measurement(slo_def)
        # Burn detection MUST observe the previous measurement before we
        # insert the new one, otherwise the previous lookup would return
        # the just-inserted row and report zero burn.
        burn = self.compute_error_budget(
            slo_def, measurement, incident_id=incident_id
        )
        self.store.insert_measurement(measurement)
        return measurement, burn

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _previous_measurement(
        self, slo_name: str, before_id: int | None
    ) -> SLOMeasurement | None:
        rows = self.store.list_measurements(slo_name=slo_name, limit=1)
        return rows[0] if rows else None

    @staticmethod
    def _hours_between(start_iso: str, end_iso: str) -> float:
        try:
            start = datetime.strptime(start_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            end = datetime.strptime(end_iso, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return 0.0
        delta = (end - start).total_seconds() / 3600.0
        return max(delta, 0.0)
