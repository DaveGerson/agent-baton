# Proposal 003: Closed-Loop Autonomous Learning & Self-Improvement

**Status**: Draft
**Author**: Architecture Review
**Date**: 2026-03-21
**Risk**: MEDIUM — extends existing experimental modules; no core breaking changes
**Estimated Scope**: ~1,800 LOC new, ~600 LOC modified across 12-15 files
**Depends On**: Proposal 001 (Event Bus) — soft dependency; works without it but better with it

---

## Problem Statement

Agent Baton has a complete **data collection pipeline** (usage logs,
traces, retrospectives, pattern learner, budget tuner, scoring, evolution
engine) but the loop is **open** — data flows in, insights are generated,
but nothing automatically acts on them. Every improvement requires a
human to:

1. Run `baton scores` and read the output
2. Run `baton patterns` and interpret recommendations
3. Run `baton budget` and manually adjust tier configurations
4. Run `baton evolve` and decide whether to apply proposals
5. Notice that an agent keeps failing and manually rewrite its prompt

In an autonomous development model, this manual review bottleneck means
the system **never improves on its own**. The pattern learner accumulates
data that nobody reads. The budget tuner recommends changes that nobody
applies. The evolution engine proposes prompt improvements that nobody
evaluates.

For autonomous agent development with async human interaction, the
learning system must:

- **Automatically detect** when agent performance degrades
- **Propose improvements** with supporting evidence
- **Apply safe improvements** autonomously (within guardrails)
- **Escalate risky improvements** to human review
- **Measure the impact** of applied improvements
- **Roll back** improvements that don't work

---

## Proposed Architecture

### Core Concept: The Improvement Loop

```
  ┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
  │  Execution   │────▶│  Observation  │────▶│  Analysis        │
  │  (engine)    │     │  (observe/)   │     │  (learn+improve) │
  └─────────────┘     └──────────────┘     └────────┬────────┘
        ▲                                           │
        │                                           ▼
  ┌─────┴─────────┐                        ┌────────────────────┐
  │  Application   │◀───────────────────────│  Decision          │
  │  (auto/human)  │                        │  (auto or escalate)│
  └───────────────┘                        └────────────────────┘
```

Each execution generates data. The analysis layer detects patterns and
proposes improvements. Safe improvements apply automatically. Risky ones
escalate to humans. Applied improvements are measured in subsequent
executions. The loop closes.

### Module Structure

```
agent_baton/core/improve/
├── __init__.py          # existing
├── scoring.py           # existing — enhanced
├── evolution.py         # existing — enhanced
├── vcs.py               # existing — unchanged
├── loop.py              # NEW — ImprovementLoop orchestrator
├── triggers.py          # NEW — when to analyze (thresholds, schedules)
├── proposals.py         # NEW — ImprovementProposal lifecycle
├── experiments.py       # NEW — A/B testing for prompt changes
└── rollback.py          # NEW — automatic rollback on regression

agent_baton/core/learn/
├── __init__.py          # existing
├── pattern_learner.py   # existing — enhanced
├── budget_tuner.py      # existing — enhanced
└── recommender.py       # NEW — unified recommendation engine
```

---

### Component 1: Improvement Triggers

```python
# triggers.py

@dataclass
class TriggerConfig:
    """Configurable thresholds for automatic analysis."""
    min_tasks_before_analysis: int = 10     # don't analyze too early
    analysis_interval_tasks: int = 5        # re-analyze every N tasks
    agent_failure_threshold: float = 0.3    # >30% failure rate triggers alert
    gate_failure_threshold: float = 0.2     # >20% gate failures triggers review
    budget_deviation_threshold: float = 0.5 # >50% over budget triggers tuning
    confidence_threshold: float = 0.7       # minimum confidence for auto-apply

class TriggerEvaluator:
    """Evaluates whether improvement analysis should run."""

    def __init__(self, config: TriggerConfig, usage_logger: UsageLogger):
        ...

    def should_analyze(self) -> bool:
        """Check if enough new data has accumulated since last analysis."""
        ...

    def detect_anomalies(self) -> list[Anomaly]:
        """Scan recent usage for concerning patterns."""
        return [
            a for a in [
                self._check_agent_failure_rates(),
                self._check_gate_failure_rates(),
                self._check_budget_overruns(),
                self._check_retry_spikes(),
                self._check_pattern_drift(),
            ]
            if a is not None
        ]

@dataclass
class Anomaly:
    anomaly_type: str        # "agent_degradation", "budget_overrun", etc.
    severity: str            # "info", "warning", "critical"
    agent_name: str | None
    metric: str              # "first_pass_rate", "gate_pass_rate", etc.
    current_value: float
    threshold: float
    sample_size: int
    evidence: str            # human-readable explanation
```

### Component 2: Unified Recommender

Consolidates PatternLearner, BudgetTuner, and PerformanceScorer into
a single analysis pass:

```python
# recommender.py

@dataclass
class Recommendation:
    rec_id: str                  # uuid4
    category: str                # "agent_prompt", "budget_tier", "routing",
                                 # "sequencing", "gate_config", "roster"
    target: str                  # agent name, task type, or "system"
    action: str                  # "modify_prompt", "adjust_budget", "add_agent",
                                 # "remove_agent", "change_model", "add_gate"
    description: str             # human-readable summary
    evidence: list[str]          # supporting data points
    confidence: float            # 0.0-1.0
    risk: str                    # "low", "medium", "high"
    auto_applicable: bool        # can this be applied without human review?
    proposed_change: dict        # machine-readable change spec
    rollback_spec: dict          # how to undo this change

class Recommender:
    """Unified analysis engine producing actionable recommendations."""

    def __init__(self, scorer: PerformanceScorer,
                 pattern_learner: PatternLearner,
                 budget_tuner: BudgetTuner,
                 evolution: PromptEvolutionEngine):
        ...

    def analyze(self) -> list[Recommendation]:
        """Run all analysis engines, deduplicate, rank by impact."""
        recs = []
        recs.extend(self._agent_performance_recs())
        recs.extend(self._budget_recs())
        recs.extend(self._routing_recs())
        recs.extend(self._sequencing_recs())
        recs.extend(self._prompt_evolution_recs())
        return self._deduplicate_and_rank(recs)

    def _agent_performance_recs(self) -> list[Recommendation]:
        """From PerformanceScorer: flag underperforming agents."""
        scorecards = self.scorer.score_all()
        recs = []
        for card in scorecards:
            if card.health == "needs-improvement":
                recs.append(Recommendation(
                    category="agent_prompt",
                    target=card.agent_name,
                    action="modify_prompt",
                    description=f"{card.agent_name} has {card.first_pass_rate:.0%} "
                                f"first-pass rate ({card.times_used} uses). "
                                f"Prompt improvement recommended.",
                    confidence=min(1.0, card.times_used / 20),
                    risk="medium",
                    auto_applicable=False,  # prompt changes need review
                    ...
                ))
        return recs

    def _budget_recs(self) -> list[Recommendation]:
        """From BudgetTuner: tier adjustments."""
        recommendations = self.budget_tuner.recommend()
        return [
            Recommendation(
                category="budget_tier",
                target=rec.task_type,
                action="adjust_budget",
                description=f"Adjust {rec.task_type} from {rec.current_tier} "
                            f"to {rec.recommended_tier}",
                confidence=rec.confidence,
                risk="low",
                auto_applicable=rec.confidence >= 0.8,  # high-confidence = auto
                ...
            )
            for rec in recommendations
        ]
```

### Component 3: Improvement Loop Orchestrator

```python
# loop.py

class ImprovementLoop:
    """Closed-loop improvement: analyze → propose → decide → apply → measure."""

    def __init__(self, recommender: Recommender,
                 trigger_eval: TriggerEvaluator,
                 experiment_manager: ExperimentManager,
                 decision_manager: DecisionManager | None = None,
                 config: ImprovementConfig = None):
        ...

    def run_cycle(self) -> ImprovementReport:
        """Execute one improvement cycle.

        Called after task completion or on schedule.
        """
        # 1. Check triggers
        if not self.trigger_eval.should_analyze():
            return ImprovementReport(skipped=True, reason="insufficient data")

        anomalies = self.trigger_eval.detect_anomalies()

        # 2. Generate recommendations
        recs = self.recommender.analyze()

        # 3. Classify each recommendation
        auto_apply = [r for r in recs if r.auto_applicable
                      and r.confidence >= self.config.auto_apply_threshold
                      and r.risk == "low"]
        needs_review = [r for r in recs if r not in auto_apply]

        # 4. Auto-apply safe recommendations
        applied = []
        for rec in auto_apply:
            experiment = self.experiment_manager.create(rec)
            self._apply(rec)
            applied.append((rec, experiment))

        # 5. Escalate risky recommendations to human
        escalated = []
        for rec in needs_review:
            if self.decision_manager:
                self.decision_manager.request(DecisionRequest(
                    decision_type="improvement_proposal",
                    summary=rec.description,
                    options=["approve", "reject", "defer"],
                    context_files=self._evidence_files(rec),
                ))
                escalated.append(rec)

        # 6. Return report
        return ImprovementReport(
            anomalies=anomalies,
            recommendations=recs,
            auto_applied=applied,
            escalated=escalated,
            timestamp=datetime.utcnow().isoformat(),
        )

    def _apply(self, rec: Recommendation) -> None:
        """Apply a recommendation with rollback backup."""
        if rec.category == "agent_prompt":
            self.vcs.backup(rec.target)
            # Apply prompt modification
            ...
        elif rec.category == "budget_tier":
            # Update budget configuration
            ...
        elif rec.category == "routing":
            # Update routing preferences
            ...
```

### Component 4: Experiment Tracking (A/B for Prompts)

```python
# experiments.py

@dataclass
class Experiment:
    experiment_id: str
    recommendation_id: str
    hypothesis: str              # "Improving X prompt will increase first-pass rate"
    metric: str                  # "first_pass_rate"
    baseline_value: float        # value before change
    target_value: float          # expected improvement
    agent_name: str
    started_at: str
    min_samples: int = 5         # minimum tasks before evaluating
    max_duration_days: int = 14  # auto-expire
    status: str                  # "active", "concluded", "rolled_back"
    samples: list[dict] = field(default_factory=list)
    result: str | None = None    # "improved", "degraded", "inconclusive"

class ExperimentManager:
    """Track experiments and evaluate their outcomes."""

    def __init__(self, storage_path: str):
        ...

    def create(self, rec: Recommendation) -> Experiment:
        """Create experiment from a recommendation."""
        baseline = self._get_baseline(rec.target, rec.category)
        return Experiment(
            experiment_id=str(uuid4()),
            recommendation_id=rec.rec_id,
            hypothesis=f"Applying '{rec.action}' to {rec.target} will improve "
                       f"{self._primary_metric(rec.category)}",
            metric=self._primary_metric(rec.category),
            baseline_value=baseline,
            target_value=baseline * 1.1,  # 10% improvement target
            agent_name=rec.target,
            started_at=datetime.utcnow().isoformat(),
        )

    def record_sample(self, experiment_id: str, outcome: dict) -> None:
        """Record a new data point for an active experiment."""
        ...

    def evaluate(self, experiment_id: str) -> str:
        """Evaluate experiment outcome.

        Returns: "improved", "degraded", "inconclusive"
        """
        exp = self.load(experiment_id)
        if len(exp.samples) < exp.min_samples:
            return "inconclusive"

        current_value = self._compute_metric(exp)

        # Statistical significance: improvement > 5% with p < 0.1
        # (relaxed threshold given small sample sizes in agent context)
        improvement = (current_value - exp.baseline_value) / max(exp.baseline_value, 0.01)

        if improvement > 0.05:
            return "improved"
        elif improvement < -0.05:
            return "degraded"
        return "inconclusive"

    def conclude(self, experiment_id: str) -> None:
        """Conclude experiment: keep change if improved, rollback if degraded."""
        result = self.evaluate(experiment_id)
        exp = self.load(experiment_id)
        exp.result = result
        exp.status = "concluded"

        if result == "degraded":
            self._trigger_rollback(exp)
            exp.status = "rolled_back"

        self.save(exp)
```

### Component 5: Automatic Rollback

```python
# rollback.py

class RollbackManager:
    """Manages rollback of applied improvements that degrade performance."""

    def __init__(self, vcs: AgentVersionControl, bus: EventBus | None = None):
        ...

    def rollback(self, experiment: Experiment, reason: str) -> None:
        """Restore agent to pre-experiment state."""
        # 1. Restore from VCS backup
        self.vcs.restore(experiment.agent_name, experiment.started_at)

        # 2. Log rollback
        self._log_rollback(experiment, reason)

        # 3. Publish event (if bus available)
        if self.bus:
            self.bus.publish(Event(
                topic="improvement.rolled_back",
                payload={
                    "experiment_id": experiment.experiment_id,
                    "agent": experiment.agent_name,
                    "reason": reason,
                    "baseline_restored": True,
                },
            ))

    def _log_rollback(self, experiment: Experiment, reason: str) -> None:
        """Append to improvement history for future reference."""
        ...
```

---

## Data Model Changes

### New model: `agent_baton/models/improvement.py`

```python
@dataclass
class Recommendation:
    rec_id: str
    category: str
    target: str
    action: str
    description: str
    evidence: list[str]
    confidence: float
    risk: str
    auto_applicable: bool
    proposed_change: dict
    rollback_spec: dict
    created_at: str
    status: str              # "proposed", "applied", "rejected", "rolled_back"

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "Recommendation": ...

@dataclass
class Experiment:
    experiment_id: str
    recommendation_id: str
    hypothesis: str
    metric: str
    baseline_value: float
    target_value: float
    agent_name: str
    started_at: str
    min_samples: int
    max_duration_days: int
    status: str
    samples: list[dict]
    result: str | None

    def to_dict(self) -> dict: ...
    @classmethod
    def from_dict(cls, data: dict) -> "Experiment": ...

@dataclass
class ImprovementReport:
    report_id: str
    timestamp: str
    skipped: bool
    reason: str | None
    anomalies: list[Anomaly]
    recommendations: list[Recommendation]
    auto_applied: list[tuple[Recommendation, Experiment]]
    escalated: list[Recommendation]
    active_experiments: list[Experiment]

    def to_dict(self) -> dict: ...
    def to_markdown(self) -> str: ...
```

### Storage

```
.claude/team-context/
├── improvements/
│   ├── recommendations.jsonl    # all recommendations (append-only)
│   ├── experiments/
│   │   ├── <experiment_id>.json # per-experiment tracking
│   │   └── ...
│   ├── reports/
│   │   ├── <report_id>.json     # improvement cycle reports
│   │   └── ...
│   └── rollbacks.jsonl          # rollback audit trail
```

---

## Enhancement to Existing Modules

### PerformanceScorer (scoring.py)

Add trend detection:

```python
class PerformanceScorer:
    # Existing methods unchanged

    def detect_trends(self, agent_name: str, window: int = 10) -> Trend:
        """Detect performance trend over last N tasks.

        Returns: "improving", "stable", "degrading"
        """
        recent = self._recent_scores(agent_name, window)
        if len(recent) < 3:
            return "insufficient_data"
        slope = self._linear_regression_slope(recent)
        if slope > 0.02:
            return "improving"
        elif slope < -0.02:
            return "degrading"
        return "stable"
```

### PatternLearner (pattern_learner.py)

Add recommendation generation:

```python
class PatternLearner:
    # Existing methods unchanged

    def recommend_sequencing(self, task_type: str) -> dict | None:
        """Recommend optimal sequencing based on historical success."""
        patterns = self.get_patterns_for_task(task_type)
        if not patterns:
            return None
        best = max(patterns, key=lambda p: p.confidence * p.success_rate)
        if best.confidence < 0.6:
            return None
        return {
            "execution_mode": best.sequencing_mode,
            "agent_sequence": best.agent_combination,
            "phase_template": best.phase_template,
            "confidence": best.confidence,
            "based_on_samples": best.sample_size,
        }
```

### BudgetTuner (budget_tuner.py)

Add auto-apply capability:

```python
class BudgetTuner:
    # Existing methods unchanged

    def auto_apply_recommendations(self, threshold: float = 0.8) -> list[dict]:
        """Apply high-confidence budget adjustments automatically.

        Only applies if confidence >= threshold and direction is "downgrade"
        (safe direction — never auto-upgrade to more expensive tier).
        """
        recs = self.recommend()
        applied = []
        for rec in recs:
            if (rec.confidence >= threshold
                    and rec.direction == "downgrade"
                    and rec.recommended_tier.value < rec.current_tier.value):
                self._apply_tier_change(rec)
                applied.append(rec)
        return applied
```

---

## Integration with Execution Engine

### Post-Execution Hook

After every `engine.complete()`, trigger an improvement check:

```python
# In ExecutionEngine.complete() or as EventBus subscriber

def on_execution_complete(event: Event) -> None:
    """Post-execution improvement trigger."""
    loop = ImprovementLoop(...)
    report = loop.run_cycle()

    if report.auto_applied:
        logger.info(f"Auto-applied {len(report.auto_applied)} improvements")

    if report.escalated:
        logger.info(f"Escalated {len(report.escalated)} improvements for review")

    # Persist report
    report_path = f".claude/team-context/improvements/reports/{report.report_id}.json"
    write_json(report_path, report.to_dict())
```

### Experiment Tracking in Usage Logger

```python
# In UsageLogger.log() — add experiment correlation

def log(self, record: TaskUsageRecord) -> None:
    # Existing logging unchanged
    super().log(record)

    # Feed active experiments
    for exp in self.experiment_manager.active():
        if record.agents_match(exp.agent_name):
            self.experiment_manager.record_sample(
                exp.experiment_id,
                {
                    "task_id": record.task_id,
                    "success": record.outcome == "success",
                    "metric_value": self._extract_metric(record, exp.metric),
                }
            )
            # Check if experiment has enough data to conclude
            if self.experiment_manager.can_evaluate(exp.experiment_id):
                self.experiment_manager.conclude(exp.experiment_id)
```

---

## Guardrails & Safety

### Auto-Apply Rules (Non-Negotiable)

1. **Only LOW-risk recommendations auto-apply.** Medium and high always
   escalate.
2. **Budget changes only auto-apply downward** (cheaper tier). Never
   auto-upgrade to more expensive execution.
3. **Prompt changes NEVER auto-apply.** Always escalate to human.
   (Prompts affect agent behavior in unpredictable ways.)
4. **Routing changes auto-apply only if confidence >= 0.9** and the
   change is additive (adding a flavor preference, not removing one).
5. **Sequencing changes auto-apply only if confidence >= 0.8** and
   historical success rate >= 0.9.
6. **Rollback is always automatic** — if an experiment shows degradation,
   restore immediately without waiting for human approval.
7. **Maximum 2 active experiments per agent** — prevent compounding
   changes that obscure causality.
8. **Minimum 5 samples before evaluating** — prevent premature conclusions.

### Human Escalation Triggers

The improvement loop escalates to human review when:

- Any recommendation with `risk == "medium"` or `risk == "high"`
- Any prompt modification (regardless of confidence)
- Any roster change (add/remove agent)
- Any gate configuration change
- An experiment concludes as "inconclusive" after max duration
- An anomaly with `severity == "critical"` is detected
- More than 3 rollbacks in 7 days (system instability signal)

---

## CLI Integration

```bash
# Existing commands enhanced
baton scores --trends              # show performance trends per agent
baton patterns --recommendations   # show sequencing recommendations
baton budget --auto-apply          # apply high-confidence budget changes

# New commands
baton improve                      # run one improvement cycle manually
baton improve --report             # show latest improvement report
baton improve --experiments        # list active experiments
baton improve --history            # show improvement history

baton experiment list              # list all experiments
baton experiment show EXP_ID       # show experiment details + samples
baton experiment conclude EXP_ID   # manually conclude an experiment
baton experiment rollback EXP_ID   # manually rollback an experiment

baton anomalies                    # show detected anomalies
baton anomalies --watch            # continuous monitoring mode
```

---

## Migration Strategy

### Phase 1: Triggers & Recommender (Week 1-2)
1. Implement `triggers.py` (TriggerEvaluator, TriggerConfig, Anomaly)
2. Implement `recommender.py` (unified recommendation from existing engines)
3. Implement `proposals.py` (Recommendation lifecycle)
4. Wire into existing scoring, pattern_learner, budget_tuner
5. Tests: 70+ unit tests
6. CLI: `baton improve`, `baton anomalies`

### Phase 2: Experiment Tracking (Week 3-4)
1. Implement `experiments.py` (ExperimentManager)
2. Implement `rollback.py` (RollbackManager)
3. Wire ExperimentManager into UsageLogger for sample collection
4. Tests: 50+ unit tests for experiment lifecycle
5. CLI: `baton experiment *`

### Phase 3: Improvement Loop (Week 5-6)
1. Implement `loop.py` (ImprovementLoop orchestrator)
2. Wire post-execution trigger (EventBus subscriber or direct call)
3. Implement auto-apply logic with guardrails
4. Implement human escalation (via DecisionManager if available,
   file-based fallback otherwise)
5. Tests: 40+ integration tests for full loop cycles

### Phase 4: Measurement & Dashboard (Week 7-8)
1. Enhance DashboardGenerator with improvement metrics
2. Add improvement history to retrospectives
3. Add trend visualization to `baton scores`
4. Integration test: multi-cycle improvement showing measurable change
5. Documentation: improvement loop reference doc

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Auto-applied changes degrade performance | Experiment tracking + automatic rollback. Only LOW-risk auto-applies. |
| Small sample sizes lead to false conclusions | Minimum 5 samples. Relaxed p-value (0.1) acknowledges small-N reality. |
| Compounding experiments obscure causality | Max 2 active experiments per agent. Sequential experimentation preferred. |
| Improvement loop overhead costs tokens | Loop runs post-execution, not during. Pure Python analysis on local data — no LLM calls. |
| Human escalation queue grows unbounded | Recommendations expire after 30 days. Auto-defer on expiry with log entry. |
| Rollback cascade (rollback causes failure causes rollback) | Circuit breaker: if 3+ rollbacks in 7 days, pause all auto-apply and escalate to human. |

---

## Success Criteria

1. After 10 task executions, the system produces at least 3 actionable
   recommendations with confidence > 0.7.
2. A budget downgrade recommendation (confidence > 0.8) auto-applies
   without human intervention and subsequent tasks execute successfully
   at the lower tier.
3. A prompt improvement escalated to human review includes specific
   evidence (failure rates, sample sizes, comparison to baseline).
4. An experiment that degrades performance (first-pass rate drops > 5%)
   automatically rolls back within 2 task executions.
5. The improvement dashboard shows trend lines for agent performance,
   active experiments, and applied/rolled-back changes over time.
6. Zero prompt changes are auto-applied (guardrail enforced).
