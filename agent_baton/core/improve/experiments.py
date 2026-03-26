"""ExperimentManager -- creates experiments from applied recommendations,
records samples, and evaluates outcomes.

Experiments are the validation mechanism for the improvement loop.  When a
recommendation is auto-applied, an experiment is created to track whether
the change actually improved the target metric.  The experiment collects
post-change metric samples and compares them to the pre-change baseline.

Evaluation methodology:

* Requires a minimum of 5 samples before evaluation (avoids premature
  conclusions from noisy early data).
* Computes ``change_pct = (avg_sample - baseline) / |baseline|``.
* **Improved**: change_pct > +5% (metric improved beyond noise threshold).
* **Degraded**: change_pct < -5% (metric worsened -- triggers auto-rollback).
* **Inconclusive**: change within +/-5% (not enough signal to decide).
* When baseline is 0, absolute thresholds are used instead.

Safety constraints:

* Maximum 2 active experiments per agent -- prevents compounding changes
  that make it impossible to attribute metric shifts.
* Degraded experiments are automatically rolled back by the
  :class:`~agent_baton.core.improve.loop.ImprovementLoop` without human
  approval.

Storage: ``<improvements_dir>/experiments/<experiment_id>.json``.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from agent_baton.models.improvement import Experiment, Recommendation

_DEFAULT_DIR = Path(".claude/team-context/improvements")
_MAX_ACTIVE_PER_AGENT = 2
_MIN_SAMPLES = 5
_IMPROVEMENT_THRESHOLD = 0.05   # >5% gain = improved
_DEGRADATION_THRESHOLD = -0.05  # >5% loss = degraded


class ExperimentManager:
    """Track experiments that measure the impact of applied recommendations.

    Manages the full experiment lifecycle: creation, sample recording,
    evaluation, conclusion, and rollback marking.  Experiments are persisted
    as individual JSON files for easy inspection and debugging.
    """

    def __init__(self, improvements_dir: Path | None = None) -> None:
        self._dir = (improvements_dir or _DEFAULT_DIR).resolve()
        self._experiments_dir = self._dir / "experiments"

    @property
    def experiments_dir(self) -> Path:
        return self._experiments_dir

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_experiment(
        self,
        recommendation: Recommendation,
        metric: str,
        baseline_value: float,
        target_value: float,
        agent_name: str = "",
    ) -> Experiment | None:
        """Create an experiment from an applied recommendation.

        The experiment's hypothesis is auto-generated from the
        recommendation's action and the baseline/target values.  The
        experiment starts in ``"running"`` status and awaits samples.

        Args:
            recommendation: The recommendation that was applied.
            metric: Name of the metric to track (e.g.
                ``"first_pass_rate"``, ``"avg_tokens_per_use"``).
            baseline_value: Pre-change metric value.
            target_value: Expected post-change metric value (typically
                baseline * 1.05 for a 5% improvement target).
            agent_name: Agent to track.  Falls back to
                ``recommendation.target`` if empty.

        Returns:
            The created :class:`~agent_baton.models.improvement.Experiment`,
            or ``None`` if the agent already has the maximum number of
            active experiments (2).
        """
        effective_agent = agent_name or recommendation.target

        # Enforce max 2 active experiments per agent
        active = self.active_for_agent(effective_agent)
        if len(active) >= _MAX_ACTIVE_PER_AGENT:
            return None

        experiment = Experiment(
            experiment_id=f"exp-{uuid.uuid4().hex[:8]}",
            recommendation_id=recommendation.rec_id,
            hypothesis=(
                f"Applying '{recommendation.action}' to {recommendation.target} "
                f"will improve {metric} from {baseline_value:.4f} to {target_value:.4f}"
            ),
            metric=metric,
            baseline_value=baseline_value,
            target_value=target_value,
            agent_name=effective_agent,
            status="running",
        )
        self._save(experiment)
        return experiment

    # ------------------------------------------------------------------
    # Record samples
    # ------------------------------------------------------------------

    def record_sample(self, experiment_id: str, value: float) -> Experiment | None:
        """Record a new sample observation for a running experiment.

        Samples are appended to the experiment's ``samples`` list and
        persisted immediately.  Only running experiments accept new samples;
        concluded or rolled-back experiments are ignored.

        Args:
            experiment_id: Identifier of the experiment to record against.
            value: The observed metric value for this sample.

        Returns:
            The updated :class:`~agent_baton.models.improvement.Experiment`,
            or ``None`` if the experiment was not found or is not running.
        """
        exp = self.get(experiment_id)
        if exp is None or exp.status != "running":
            return None

        exp.samples.append(value)
        self._save(exp)
        return exp

    # ------------------------------------------------------------------
    # Evaluate
    # ------------------------------------------------------------------

    def evaluate(self, experiment_id: str) -> str:
        """Evaluate an experiment's outcome against its baseline.

        Computes the average of all recorded samples and compares it to
        the baseline using the +/-5% threshold (see module docstring for
        the full methodology).  The experiment is concluded and persisted
        with the result.

        Args:
            experiment_id: Identifier of the experiment to evaluate.

        Returns:
            One of:

            * ``"improved"`` -- metric gained > 5% over baseline.
            * ``"degraded"`` -- metric dropped > 5% from baseline
              (triggers auto-rollback in the improvement loop).
            * ``"inconclusive"`` -- change within +/-5% noise band.
            * ``"insufficient_data"`` -- fewer than 5 samples recorded.
            * ``"not_found"`` -- no experiment with this ID exists.
        """
        exp = self.get(experiment_id)
        if exp is None:
            return "not_found"

        if len(exp.samples) < _MIN_SAMPLES:
            return "insufficient_data"

        avg_sample = sum(exp.samples) / len(exp.samples)

        if exp.baseline_value == 0:
            # Avoid division by zero — use absolute comparison
            if avg_sample > _IMPROVEMENT_THRESHOLD:
                result = "improved"
            elif avg_sample < _DEGRADATION_THRESHOLD:
                result = "degraded"
            else:
                result = "inconclusive"
        else:
            change_pct = (avg_sample - exp.baseline_value) / abs(exp.baseline_value)
            if change_pct > _IMPROVEMENT_THRESHOLD:
                result = "improved"
            elif change_pct < _DEGRADATION_THRESHOLD:
                result = "degraded"
            else:
                result = "inconclusive"

        exp.result = result
        exp.status = "concluded"
        self._save(exp)
        return result

    # ------------------------------------------------------------------
    # Conclude / rollback
    # ------------------------------------------------------------------

    def conclude(self, experiment_id: str, result: str) -> bool:
        """Manually conclude an experiment with a given result.

        Returns ``True`` if found and concluded.
        """
        exp = self.get(experiment_id)
        if exp is None:
            return False

        exp.result = result
        exp.status = "concluded"
        self._save(exp)
        return True

    def mark_rolled_back(self, experiment_id: str) -> bool:
        """Mark an experiment as rolled back.

        Returns ``True`` if found and updated.
        """
        exp = self.get(experiment_id)
        if exp is None:
            return False

        exp.status = "rolled_back"
        exp.result = "degraded"
        self._save(exp)
        return True

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get(self, experiment_id: str) -> Experiment | None:
        """Load an experiment by ID."""
        path = self._experiment_path(experiment_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Experiment.from_dict(data)
        except (json.JSONDecodeError, OSError, KeyError):
            return None

    def list_all(self) -> list[Experiment]:
        """Return all experiments, sorted by start time."""
        if not self._experiments_dir.is_dir():
            return []
        experiments: list[Experiment] = []
        for path in sorted(self._experiments_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                experiments.append(Experiment.from_dict(data))
            except (json.JSONDecodeError, OSError, KeyError):
                continue
        return experiments

    def active(self) -> list[Experiment]:
        """Return all running experiments."""
        return [e for e in self.list_all() if e.status == "running"]

    def active_for_agent(self, agent_name: str) -> list[Experiment]:
        """Return running experiments for a specific agent."""
        return [
            e for e in self.list_all()
            if e.status == "running" and e.agent_name == agent_name
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _experiment_path(self, experiment_id: str) -> Path:
        return self._experiments_dir / f"{experiment_id}.json"

    def _save(self, experiment: Experiment) -> None:
        self._experiments_dir.mkdir(parents=True, exist_ok=True)
        path = self._experiment_path(experiment.experiment_id)
        path.write_text(
            json.dumps(experiment.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
