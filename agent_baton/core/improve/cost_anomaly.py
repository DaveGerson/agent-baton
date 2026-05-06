"""Statistical cost-anomaly detection (O1.3, bd-91c7).

Replaces the heuristic budget-overrun threshold with a statistical
detector using z-score and IQR fences, computed per ``(agent_name,
model)`` pair over a rolling window.

Velocity-zero: this module is **detection only**.  It surfaces
anomalies via the CLI and the existing ``improvement_reports`` flow.
It never blocks execution and never auto-applies any change.

Algorithm
---------
For each ``(agent_name, model)`` pair seen in ``step_results``:

* Compute ``mean`` and ``stdev`` of ``tokens_per_step`` (sum of
  ``input_tokens`` + ``output_tokens``) over the most recent
  ``window_days`` days.
* Compute the inter-quartile range ``IQR = Q3 - Q1``.
* For every step in that window, flag it if either condition holds:

  * ``z = (value - mean) / stdev`` is greater than ``3.0``
    (very unusual under a Gaussian assumption).
  * ``value > Q3 + 3 * IQR`` (outside an aggressive Tukey fence).

* Severity buckets follow the z-score:

  * ``low``    --  3 < z <= 4
  * ``medium`` --  4 < z <= 6
  * ``high``   --  z > 6  (or IQR-only flags with no usable z-score)

Notes
-----
* Stdlib only: uses :mod:`statistics` (mean, stdev, quantiles).
* Pure detection -- no execution path is gated on the result.
* Per-pair: anomalies in ``agent A / model X`` never pollute the
  baseline computed for ``agent B / model Y``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

_log = logging.getLogger(__name__)


_Z_LOW = 3.0
_Z_MEDIUM = 4.0
_Z_HIGH = 6.0
_IQR_FENCE_FACTOR = 3.0
# When iqr_factor exceeds this, the value is far outside the bulk of the
# distribution -- promote to ``high`` regardless of z-score.
_IQR_HIGH_FACTOR = 5.0
# Minimum number of samples required in the window for a pair before we
# attempt statistical detection.  With fewer samples the variance is
# too noisy and we'd produce false positives.
_MIN_SAMPLES = 5


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CostAnomaly:
    """A statistically-flagged cost outlier for one step."""

    step_id: str
    agent: str
    model: str
    observed_tokens: int
    baseline_mean: float
    baseline_stdev: float
    z_score: float
    iqr_factor: float
    severity: str  # "low" | "medium" | "high"
    task_id: str = ""
    completed_at: str = ""

    # Severity ordering used for sorting "desc" in the CLI.
    _SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}

    @property
    def severity_rank(self) -> int:
        return self._SEVERITY_RANK.get(self.severity, 0)

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "task_id": self.task_id,
            "agent": self.agent,
            "model": self.model,
            "observed_tokens": self.observed_tokens,
            "baseline_mean": self.baseline_mean,
            "baseline_stdev": self.baseline_stdev,
            "z_score": self.z_score,
            "iqr_factor": self.iqr_factor,
            "severity": self.severity,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CostAnomaly:
        return cls(
            step_id=str(data.get("step_id", "")),
            task_id=str(data.get("task_id", "")),
            agent=str(data.get("agent", "")),
            model=str(data.get("model", "")),
            observed_tokens=int(data.get("observed_tokens", 0)),
            baseline_mean=float(data.get("baseline_mean", 0.0)),
            baseline_stdev=float(data.get("baseline_stdev", 0.0)),
            z_score=float(data.get("z_score", 0.0)),
            iqr_factor=float(data.get("iqr_factor", 0.0)),
            severity=str(data.get("severity", "low")),
            completed_at=str(data.get("completed_at", "")),
        )

    def to_anomaly_dict(self) -> dict:
        """Serialize as a generic ``Anomaly`` entry for ``ImprovementReport``.

        The shape matches :class:`agent_baton.models.improvement.Anomaly` so
        the report consumer needs no special-casing.
        """
        return {
            "anomaly_type": "cost_anomaly",
            "severity": self.severity,
            "agent_name": self.agent,
            "metric": "tokens_per_step",
            "current_value": float(self.observed_tokens),
            "threshold": float(self.baseline_mean),
            "sample_size": 1,
            "evidence": [
                f"step_id={self.step_id}",
                f"model={self.model}",
                f"z={self.z_score:.2f}",
                f"iqr_factor={self.iqr_factor:.2f}",
            ],
        }


# ---------------------------------------------------------------------------
# Step record (internal)
# ---------------------------------------------------------------------------


@dataclass
class _StepRecord:
    task_id: str
    step_id: str
    agent_name: str
    model: str
    tokens: int
    completed_at: str


# ---------------------------------------------------------------------------
# Acknowledgement store
# ---------------------------------------------------------------------------


_ACK_FILENAME = "cost_anomaly_acks.json"


class _AckStore:
    """Persistent set of acknowledged step IDs.

    Stored as a JSON file so it survives across cycles without requiring
    a schema migration.  Keyed by ``(step_id, task_id)`` to avoid
    collisions across tasks that re-use the same step name.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @staticmethod
    def _key(step_id: str, task_id: str) -> str:
        return f"{task_id}::{step_id}"

    def load(self) -> set[str]:
        if not self._path.is_file():
            return set()
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return set(data.get("acked", []))
        except (json.JSONDecodeError, OSError):
            return set()

    def add_many(self, anomalies: Iterable[CostAnomaly]) -> int:
        existing = self.load()
        before = len(existing)
        for a in anomalies:
            existing.add(self._key(a.step_id, a.task_id))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"acked": sorted(existing)}, indent=2) + "\n",
            encoding="utf-8",
        )
        return len(existing) - before

    def is_acked(self, anomaly: CostAnomaly) -> bool:
        return self._key(anomaly.step_id, anomaly.task_id) in self.load()


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class CostAnomalyDetector:
    """Detect statistical cost outliers in ``step_results``.

    The detector reads from a SQLite ``step_results`` table that contains
    the columns ``task_id, step_id, agent_name, model_id, input_tokens,
    output_tokens, completed_at``.

    Args:
        db_path: Absolute path to the project ``baton.db``.  When ``None``
            the detector works on an injected in-memory record set (mainly
            used by tests).
        records: Optional pre-loaded list of :class:`_StepRecord`.
            When supplied, ``db_path`` is ignored.  Useful for tests.
        ack_store_path: Where to persist acknowledged step IDs.  Default
            is ``<db_dir>/cost_anomaly_acks.json``.
    """

    def __init__(
        self,
        db_path: Path | None = None,
        records: list[_StepRecord] | None = None,
        ack_store_path: Path | None = None,
    ) -> None:
        self._db_path = Path(db_path) if db_path else None
        self._injected_records = records
        if ack_store_path is not None:
            self._ack_path = Path(ack_store_path)
        elif self._db_path is not None:
            self._ack_path = self._db_path.parent / _ACK_FILENAME
        else:
            self._ack_path = Path.cwd() / _ACK_FILENAME
        self._acks = _AckStore(self._ack_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(
        self,
        window_days: int = 30,
        include_acked: bool = False,
    ) -> list[CostAnomaly]:
        """Detect anomalies over the most recent ``window_days``.

        Args:
            window_days: Look-back window in days.  Steps with
                ``completed_at`` older than this are excluded.
            include_acked: If ``True``, anomalies the user has already
                acknowledged are still returned (useful for audit views
                and tests).

        Returns:
            A list of :class:`CostAnomaly`, sorted by severity descending
            then by ``z_score`` descending.
        """
        records = self._load_records(window_days)
        anomalies: list[CostAnomaly] = []

        # Bucket records by (agent, model).
        buckets: dict[tuple[str, str], list[_StepRecord]] = {}
        for r in records:
            buckets.setdefault((r.agent_name, r.model), []).append(r)

        for (agent, model), bucket in buckets.items():
            anomalies.extend(self._detect_for_pair(agent, model, bucket))

        if not include_acked:
            acked = self._acks.load()
            anomalies = [
                a for a in anomalies
                if _AckStore._key(a.step_id, a.task_id) not in acked
            ]

        anomalies.sort(
            key=lambda a: (a.severity_rank, a.z_score, a.iqr_factor),
            reverse=True,
        )
        return anomalies

    def acknowledge(self, anomalies: Iterable[CostAnomaly]) -> int:
        """Mark the given anomalies as acknowledged.

        Returns:
            Number of *newly* added acknowledgements.
        """
        return self._acks.add_many(anomalies)

    # ------------------------------------------------------------------
    # Statistical core
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_for_pair(
        agent: str,
        model: str,
        records: list[_StepRecord],
    ) -> list[CostAnomaly]:
        if len(records) < _MIN_SAMPLES:
            return []

        values = [r.tokens for r in records]
        mean = statistics.mean(values)
        try:
            stdev = statistics.stdev(values)
        except statistics.StatisticsError:
            stdev = 0.0

        # IQR fence -- requires at least 4 samples for ``quantiles(n=4)``.
        try:
            q1, _q2, q3 = statistics.quantiles(values, n=4)
            iqr = q3 - q1
            iqr_fence = q3 + _IQR_FENCE_FACTOR * iqr
        except statistics.StatisticsError:
            iqr = 0.0
            iqr_fence = float("inf")

        anomalies: list[CostAnomaly] = []
        for r in records:
            z = 0.0
            if stdev > 0:
                z = (r.tokens - mean) / stdev

            iqr_factor = 0.0
            if iqr > 0:
                iqr_factor = (r.tokens - q3) / iqr

            flagged_z = z > _Z_LOW
            flagged_iqr = iqr > 0 and r.tokens > iqr_fence

            if not (flagged_z or flagged_iqr):
                continue

            anomalies.append(
                CostAnomaly(
                    step_id=r.step_id,
                    task_id=r.task_id,
                    agent=agent,
                    model=model,
                    observed_tokens=r.tokens,
                    baseline_mean=mean,
                    baseline_stdev=stdev,
                    z_score=z,
                    iqr_factor=iqr_factor,
                    severity=_classify_severity(
                        z, iqr_factor, flagged_iqr, flagged_z
                    ),
                    completed_at=r.completed_at,
                )
            )
        return anomalies

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def _load_records(self, window_days: int) -> list[_StepRecord]:
        if self._injected_records is not None:
            return list(self._injected_records)

        if self._db_path is None or not self._db_path.is_file():
            return []

        cutoff = (
            datetime.now(timezone.utc) - timedelta(days=window_days)
        ).isoformat()

        try:
            conn = sqlite3.connect(str(self._db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.execute(
                """
                SELECT task_id, step_id, agent_name,
                       COALESCE(model_id, '') AS model_id,
                       COALESCE(input_tokens, 0) AS input_tokens,
                       COALESCE(output_tokens, 0) AS output_tokens,
                       COALESCE(completed_at, '') AS completed_at
                FROM step_results
                WHERE completed_at >= ?
                """,
                (cutoff,),
            )
            records: list[_StepRecord] = []
            for row in cur:
                tokens = int(row["input_tokens"]) + int(row["output_tokens"])
                if tokens <= 0:
                    continue
                model = row["model_id"] or "unknown"
                records.append(
                    _StepRecord(
                        task_id=row["task_id"],
                        step_id=row["step_id"],
                        agent_name=row["agent_name"],
                        model=model,
                        tokens=tokens,
                        completed_at=row["completed_at"],
                    )
                )
            return records
        except sqlite3.DatabaseError as exc:
            _log.debug("CostAnomalyDetector: db read failed -- %s", exc)
            return []
        finally:
            try:
                conn.close()  # type: ignore[union-attr]
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _classify_severity(
    z: float,
    iqr_factor: float,
    flagged_iqr: bool,
    flagged_z: bool,
) -> str:
    """Map z-score and IQR-factor to a severity bucket.

    Rules (in priority order):

    * IQR-only flag (``flagged_iqr=True, flagged_z=False``) -- ``high``.
      The z-score was suppressed by the outlier inflating stdev.
    * Very large IQR factor (> ``_IQR_HIGH_FACTOR``) -- ``high``.
      Even when z is in the medium range, an IQR factor that large means
      the value sits far outside the bulk of the distribution.
    * z > ``_Z_HIGH``                                -- ``high``.
    * z > ``_Z_MEDIUM``                              -- ``medium``.
    * otherwise                                      -- ``low``.
    """
    if not flagged_z and flagged_iqr:
        return "high"
    if iqr_factor > _IQR_HIGH_FACTOR:
        return "high"
    if z > _Z_HIGH:
        return "high"
    if z > _Z_MEDIUM:
        return "medium"
    return "low"
