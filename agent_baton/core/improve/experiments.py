"""ExperimentManager — creates experiments from applied recommendations,
records samples, and evaluates outcomes.

Constraints:
- Max 2 active experiments per agent.
- Min 5 samples before evaluating.
- Stores experiments at ``.claude/team-context/improvements/experiments/<id>.json``.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agent_baton.models.improvement import Experiment, Recommendation

_DEFAULT_DIR = Path(".claude/team-context/improvements")
_MAX_ACTIVE_PER_AGENT = 2
_MIN_SAMPLES = 5
_IMPROVEMENT_THRESHOLD = 0.05   # >5% gain = improved
_DEGRADATION_THRESHOLD = -0.05  # >5% loss = degraded


class ExperimentManager:
    """Track experiments that measure the impact of applied recommendations."""

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

        Returns ``None`` if the agent already has ``_MAX_ACTIVE_PER_AGENT``
        active experiments.
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
        """Record a new sample observation for an experiment.

        Returns the updated experiment, or ``None`` if not found.
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
        """Evaluate an experiment's outcome.

        Requires at least ``_MIN_SAMPLES`` samples.

        Returns:
            "improved" if >5% gain over baseline.
            "degraded" if >5% loss from baseline.
            "inconclusive" otherwise.
            "insufficient_data" if not enough samples.
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
