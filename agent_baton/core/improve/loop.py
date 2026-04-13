"""ImprovementLoop -- the closed-loop orchestrator that drives improvement cycles.

This module is the top-level coordinator of the entire closed-loop learning
pipeline.  A single call to :meth:`ImprovementLoop.run_cycle` performs the
complete improvement cycle:

1. **Trigger check** -- :class:`TriggerEvaluator` decides if enough new
   data has accumulated since the last analysis.
2. **Anomaly detection** -- scans for high failure rates, retry spikes,
   gate failures, and budget overruns.
3. **Recommendation generation** -- :class:`Recommender` runs all analysis
   engines (budget tuner, pattern learner, scorer, evolution engine).
4. **Classification** -- each recommendation is classified as auto-apply
   or escalate based on guardrails (see :meth:`_should_auto_apply`).
5. **Application** -- safe recommendations are applied and tracked via
   :class:`ProposalManager`.
6. **Experiment creation** -- each applied recommendation spawns an
   :class:`~agent_baton.models.improvement.Experiment` to track impact.
7. **Experiment evaluation** -- running experiments with enough samples are
   evaluated; degraded experiments trigger automatic rollback.
8. **Report persistence** -- the cycle produces an
   :class:`~agent_baton.models.improvement.ImprovementReport` saved to
   ``improvements/reports/<id>.json``.

Safety mechanisms:

* **Circuit breaker** -- 3+ rollbacks in 7 days pauses all auto-apply
  (checked before the cycle runs).
* **Manual pause** -- ``config.paused`` flag skips the cycle.
* **Guardrails** -- prompt changes never auto-apply; budget upgrades never
  auto-apply; routing reductions never auto-apply.

Reports are stored at ``.claude/team-context/improvements/reports/<id>.json``.
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

_log = logging.getLogger(__name__)

from agent_baton.core.improve.experiments import ExperimentManager
from agent_baton.core.improve.proposals import ProposalManager
from agent_baton.core.improve.rollback import RollbackManager
from agent_baton.core.improve.scoring import PerformanceScorer
from agent_baton.core.improve.triggers import TriggerEvaluator
from agent_baton.core.learn.recommender import Recommender
from agent_baton.models.improvement import (
    Experiment,
    ImprovementConfig,
    ImprovementReport,
    Recommendation,
    TriggerConfig,
)

_DEFAULT_DIR = Path(".claude/team-context/improvements")


class ImprovementLoop:
    """Drive the closed-loop improvement cycle.

    Wires together all subsystems of the improvement pipeline:

    * :class:`TriggerEvaluator` -- decides when to run.
    * :class:`Recommender` -- produces recommendations from all engines.
    * :class:`ProposalManager` -- persists recommendation lifecycle.
    * :class:`ExperimentManager` -- tracks applied recommendation impact.
    * :class:`RollbackManager` -- handles rollbacks and circuit breaker.
    * :class:`PerformanceScorer` -- provides current metric values for
      experiment baselines.

    The loop is typically invoked by the ``baton`` CLI or at the end of an
    orchestrated task to continuously improve agent performance.
    """

    def __init__(
        self,
        trigger_evaluator: TriggerEvaluator | None = None,
        recommender: Recommender | None = None,
        proposal_manager: ProposalManager | None = None,
        experiment_manager: ExperimentManager | None = None,
        rollback_manager: RollbackManager | None = None,
        scorer: PerformanceScorer | None = None,
        config: ImprovementConfig | None = None,
        improvements_dir: Path | None = None,
        bead_store=None,
    ) -> None:
        self._dir = (improvements_dir or _DEFAULT_DIR).resolve()
        self._reports_dir = self._dir / "reports"

        self._triggers = trigger_evaluator or TriggerEvaluator()
        self._recommender = recommender or Recommender()
        self._proposals = proposal_manager or ProposalManager(self._dir)
        self._experiments = experiment_manager or ExperimentManager(self._dir)
        self._rollbacks = rollback_manager or RollbackManager(improvements_dir=self._dir)
        self._scorer = scorer or PerformanceScorer()
        self._config = config or ImprovementConfig()
        self._bead_store = bead_store  # F12: passed to scorer for bead quality metrics

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run_cycle(self, force: bool = False) -> ImprovementReport:
        """Run a complete improvement cycle.

        Executes the full pipeline: trigger check, anomaly detection,
        recommendation generation, classification, application, experiment
        creation, experiment evaluation, and report persistence.

        The cycle short-circuits early if:

        * The circuit breaker is tripped (3+ recent rollbacks).
        * ``config.paused`` is ``True``.
        * ``force=False`` and the trigger evaluator says not enough new
          data exists.

        Args:
            force: If ``True``, skip the trigger check and run the analysis
                regardless of how many new tasks have accumulated.

        Returns:
            An :class:`~agent_baton.models.improvement.ImprovementReport`
            summarising what was detected, recommended, applied, escalated,
            and experimented on.
        """
        report_id = f"report-{uuid.uuid4().hex[:8]}"

        # Check circuit breaker
        if self._config.paused or self._rollbacks.circuit_breaker_tripped():
            return self._skipped_report(
                report_id, "Auto-apply paused: circuit breaker tripped or manually paused"
            )

        # Check triggers
        if not force and not self._triggers.should_analyze():
            return self._skipped_report(
                report_id, "Not enough new data since last analysis"
            )

        # Detect anomalies
        anomalies = self._triggers.detect_anomalies()

        # Run learning engine analysis (best-effort, non-blocking).
        # Reads open LearningIssues, marks proposed candidates, feeds into
        # the recommendation pipeline via confidence scoring.
        try:
            from agent_baton.core.learn.engine import LearningEngine
            LearningEngine().analyze()
        except Exception as exc:
            _log.debug("Learning engine analysis skipped: %s", exc)

        # Generate recommendations
        recommendations = self._recommender.analyze()

        # Persist fresh patterns so the planner reads up-to-date data on the
        # next execution.  Best-effort: a failure here must never block the
        # execution completion flow.
        try:
            learner = getattr(self._recommender, "_learner", None)
            if learner is not None:
                learner.refresh()
                _log.debug("Pattern learner refreshed learned-patterns.json")
        except Exception as exc:  # noqa: BLE001
            _log.warning("Pattern learner refresh failed (non-fatal): %s", exc)

        # Persist budget recommendations so the planner reads up-to-date tiers
        # on the next execution.  Best-effort: same reasoning as above.
        try:
            tuner = getattr(self._recommender, "_tuner", None)
            if tuner is not None:
                tuner.save_recommendations()
                _log.debug("Budget tuner saved budget-recommendations.json")
        except Exception as exc:  # noqa: BLE001
            _log.warning("Budget tuner save_recommendations failed (non-fatal): %s", exc)

        # Persist all recommendations
        self._proposals.record_many(recommendations)

        # Classify and apply
        auto_applied: list[str] = []
        escalated: list[str] = []

        for rec in recommendations:
            if self._should_auto_apply(rec):
                self._apply_recommendation(rec)
                auto_applied.append(rec.rec_id)
            else:
                escalated.append(rec.rec_id)

        # Create experiments for auto-applied recommendations
        active_experiments: list[str] = []
        for rec_id in auto_applied:
            rec = self._proposals.get(rec_id)
            if rec is not None:
                exp = self._create_experiment_for(rec)
                if exp is not None:
                    active_experiments.append(exp.experiment_id)

        # Evaluate any running experiments that have enough data
        self._evaluate_running_experiments()

        # Mark analysis as done
        self._triggers.mark_analyzed()

        # Build and persist report
        report = ImprovementReport(
            report_id=report_id,
            anomalies=[a.to_dict() for a in anomalies],
            recommendations=[r.to_dict() for r in recommendations],
            auto_applied=auto_applied,
            escalated=escalated,
            active_experiments=active_experiments,
        )
        self._save_report(report)
        return report

    # ------------------------------------------------------------------
    # Experiment evaluation
    # ------------------------------------------------------------------

    def evaluate_experiments(self) -> list[tuple[str, str]]:
        """Evaluate all running experiments and auto-rollback degraded ones.

        For each running experiment with sufficient samples (>= 5), the
        experiment is evaluated against its baseline.  Degraded experiments
        (> 5% metric drop) are automatically rolled back via
        :class:`RollbackManager` without human approval.

        Returns:
            List of ``(experiment_id, result)`` tuples where result is one
            of ``"improved"``, ``"degraded"``, ``"inconclusive"``, or
            ``"insufficient_data"``.
        """
        return self._evaluate_running_experiments()

    # ------------------------------------------------------------------
    # Classification logic
    # ------------------------------------------------------------------

    def _should_auto_apply(self, rec: Recommendation) -> bool:
        """Determine if a recommendation should be auto-applied.

        Enforces a multi-layer guardrail system to prevent unsafe changes:

        1. Prompt changes (``category="agent_prompt"``) NEVER auto-apply.
        2. Must be marked ``auto_applicable=True`` by the
           :class:`Recommender` (which enforces category-specific rules).
        3. Must have ``risk="low"``.
        4. Must meet the ``auto_apply_threshold`` confidence from config.

        All other recommendations are escalated for human review.

        Args:
            rec: The recommendation to classify.

        Returns:
            ``True`` if all guardrail conditions are met and the
            recommendation can be safely applied without human review.
        """
        # Prompt changes never auto-apply
        if rec.category == "agent_prompt":
            return False

        # Must be marked as auto_applicable by the recommender
        if not rec.auto_applicable:
            return False

        # Must be low risk
        if rec.risk != "low":
            return False

        # Must meet confidence threshold
        if rec.confidence < self._config.auto_apply_threshold:
            return False

        return True

    def _apply_recommendation(self, rec: Recommendation) -> None:
        """Apply a recommendation and update its status."""
        rec.status = "applied"
        self._proposals.update_status(rec.rec_id, "applied")

    def _create_experiment_for(self, rec: Recommendation) -> Experiment | None:
        """Create an experiment to track the impact of an applied recommendation."""
        # Determine baseline metric based on category
        metric = self._metric_for_category(rec.category)
        baseline = self._current_metric_value(rec.target, metric)
        target = baseline * 1.05 if baseline > 0 else 0.05  # 5% improvement target

        return self._experiments.create_experiment(
            recommendation=rec,
            metric=metric,
            baseline_value=baseline,
            target_value=target,
            agent_name=rec.target,
        )

    def _evaluate_running_experiments(self) -> list[tuple[str, str]]:
        """Evaluate experiments and auto-rollback degraded ones."""
        results: list[tuple[str, str]] = []

        for exp in self._experiments.active():
            if len(exp.samples) < exp.min_samples:
                continue

            result = self._experiments.evaluate(exp.experiment_id)
            results.append((exp.experiment_id, result))

            # Auto-rollback on degradation (no human approval needed)
            if result == "degraded":
                rec = self._proposals.get(exp.recommendation_id)
                if rec is not None:
                    self._rollbacks.rollback(rec, f"Experiment {exp.experiment_id} degraded")
                    self._proposals.update_status(rec.rec_id, "rolled_back")
                    self._experiments.mark_rolled_back(exp.experiment_id)

        return results

    # ------------------------------------------------------------------
    # Metric helpers
    # ------------------------------------------------------------------

    def _metric_for_category(self, category: str) -> str:
        """Map recommendation category to the primary metric to track."""
        return {
            "budget_tier": "avg_tokens_per_use",
            "routing": "first_pass_rate",
            "sequencing": "gate_pass_rate",
            "gate_config": "gate_pass_rate",
            "roster": "first_pass_rate",
            "agent_prompt": "first_pass_rate",
        }.get(category, "first_pass_rate")

    def _current_metric_value(self, target: str, metric: str) -> float:
        """Read the current value of a metric for a target."""
        try:
            sc = self._scorer.score_agent(target, bead_store=self._bead_store)
            return float(getattr(sc, metric, 0.0) or 0.0)
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_report(self, report: ImprovementReport) -> Path:
        self._reports_dir.mkdir(parents=True, exist_ok=True)
        path = self._reports_dir / f"{report.report_id}.json"
        path.write_text(
            json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return path

    def _skipped_report(self, report_id: str, reason: str) -> ImprovementReport:
        report = ImprovementReport(
            report_id=report_id,
            skipped=True,
            reason=reason,
        )
        self._save_report(report)
        return report

    def load_reports(self) -> list[ImprovementReport]:
        """Load all improvement reports, sorted by timestamp."""
        if not self._reports_dir.is_dir():
            return []
        reports: list[ImprovementReport] = []
        for path in sorted(self._reports_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                reports.append(ImprovementReport.from_dict(data))
            except (json.JSONDecodeError, OSError, KeyError):
                continue
        return reports
