"""Data models for the closed-loop improvement system.

Covers recommendations, experiments, anomalies, trigger configuration,
improvement reports, and top-level improvement configuration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RecommendationCategory(Enum):
    AGENT_PROMPT = "agent_prompt"
    BUDGET_TIER = "budget_tier"
    ROUTING = "routing"
    SEQUENCING = "sequencing"
    GATE_CONFIG = "gate_config"
    ROSTER = "roster"


class RecommendationStatus(Enum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


class ExperimentStatus(Enum):
    RUNNING = "running"
    CONCLUDED = "concluded"
    ROLLED_BACK = "rolled_back"


class AnomalySeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------

@dataclass
class Recommendation:
    """A single improvement recommendation produced by the analysis pipeline."""

    rec_id: str
    category: str               # RecommendationCategory value
    target: str                 # e.g. agent name, task type, gate name
    action: str                 # short verb phrase: "downgrade budget", "adjust routing"
    description: str            # human-readable explanation
    evidence: list[str] = field(default_factory=list)   # supporting data references
    confidence: float = 0.0     # 0.0 - 1.0
    risk: str = "low"           # "low", "medium", "high"
    auto_applicable: bool = False
    proposed_change: dict = field(default_factory=dict)  # machine-readable change spec
    rollback_spec: dict = field(default_factory=dict)    # how to undo
    created_at: str = ""
    status: str = "proposed"    # RecommendationStatus value

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "rec_id": self.rec_id,
            "category": self.category,
            "target": self.target,
            "action": self.action,
            "description": self.description,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "risk": self.risk,
            "auto_applicable": self.auto_applicable,
            "proposed_change": self.proposed_change,
            "rollback_spec": self.rollback_spec,
            "created_at": self.created_at,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Recommendation:
        return cls(
            rec_id=data["rec_id"],
            category=data.get("category", ""),
            target=data.get("target", ""),
            action=data.get("action", ""),
            description=data.get("description", ""),
            evidence=data.get("evidence", []),
            confidence=float(data.get("confidence", 0.0)),
            risk=data.get("risk", "low"),
            auto_applicable=bool(data.get("auto_applicable", False)),
            proposed_change=data.get("proposed_change", {}),
            rollback_spec=data.get("rollback_spec", {}),
            created_at=data.get("created_at", ""),
            status=data.get("status", "proposed"),
        )


# ---------------------------------------------------------------------------
# Experiment
# ---------------------------------------------------------------------------

@dataclass
class Experiment:
    """Tracks the outcome of an applied recommendation over subsequent executions."""

    experiment_id: str
    recommendation_id: str
    hypothesis: str
    metric: str                     # e.g. "first_pass_rate", "gate_pass_rate"
    baseline_value: float = 0.0
    target_value: float = 0.0
    agent_name: str = ""
    started_at: str = ""
    min_samples: int = 5
    max_duration_days: int = 14
    status: str = "running"         # ExperimentStatus value
    samples: list[float] = field(default_factory=list)
    result: str = ""                # "improved", "degraded", "inconclusive", ""

    def __post_init__(self) -> None:
        if not self.started_at:
            self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "experiment_id": self.experiment_id,
            "recommendation_id": self.recommendation_id,
            "hypothesis": self.hypothesis,
            "metric": self.metric,
            "baseline_value": self.baseline_value,
            "target_value": self.target_value,
            "agent_name": self.agent_name,
            "started_at": self.started_at,
            "min_samples": self.min_samples,
            "max_duration_days": self.max_duration_days,
            "status": self.status,
            "samples": self.samples,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Experiment:
        return cls(
            experiment_id=data["experiment_id"],
            recommendation_id=data.get("recommendation_id", ""),
            hypothesis=data.get("hypothesis", ""),
            metric=data.get("metric", ""),
            baseline_value=float(data.get("baseline_value", 0.0)),
            target_value=float(data.get("target_value", 0.0)),
            agent_name=data.get("agent_name", ""),
            started_at=data.get("started_at", ""),
            min_samples=int(data.get("min_samples", 5)),
            max_duration_days=int(data.get("max_duration_days", 14)),
            status=data.get("status", "running"),
            samples=data.get("samples", []),
            result=data.get("result", ""),
        )


# ---------------------------------------------------------------------------
# Anomaly
# ---------------------------------------------------------------------------

@dataclass
class Anomaly:
    """A detected anomaly in agent or system behaviour."""

    anomaly_type: str           # e.g. "high_failure_rate", "budget_overrun", "retry_spike"
    severity: str               # AnomalySeverity value
    agent_name: str = ""
    metric: str = ""
    current_value: float = 0.0
    threshold: float = 0.0
    sample_size: int = 0
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "anomaly_type": self.anomaly_type,
            "severity": self.severity,
            "agent_name": self.agent_name,
            "metric": self.metric,
            "current_value": self.current_value,
            "threshold": self.threshold,
            "sample_size": self.sample_size,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Anomaly:
        return cls(
            anomaly_type=data.get("anomaly_type", ""),
            severity=data.get("severity", "low"),
            agent_name=data.get("agent_name", ""),
            metric=data.get("metric", ""),
            current_value=float(data.get("current_value", 0.0)),
            threshold=float(data.get("threshold", 0.0)),
            sample_size=int(data.get("sample_size", 0)),
            evidence=data.get("evidence", []),
        )


# ---------------------------------------------------------------------------
# TriggerConfig
# ---------------------------------------------------------------------------

@dataclass
class TriggerConfig:
    """Configuration for when to trigger improvement analysis."""

    min_tasks_before_analysis: int = 10
    analysis_interval_tasks: int = 5
    agent_failure_threshold: float = 0.3
    gate_failure_threshold: float = 0.2
    budget_deviation_threshold: float = 0.5
    confidence_threshold: float = 0.7

    def to_dict(self) -> dict:
        return {
            "min_tasks_before_analysis": self.min_tasks_before_analysis,
            "analysis_interval_tasks": self.analysis_interval_tasks,
            "agent_failure_threshold": self.agent_failure_threshold,
            "gate_failure_threshold": self.gate_failure_threshold,
            "budget_deviation_threshold": self.budget_deviation_threshold,
            "confidence_threshold": self.confidence_threshold,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TriggerConfig:
        return cls(
            min_tasks_before_analysis=int(data.get("min_tasks_before_analysis", 10)),
            analysis_interval_tasks=int(data.get("analysis_interval_tasks", 5)),
            agent_failure_threshold=float(data.get("agent_failure_threshold", 0.3)),
            gate_failure_threshold=float(data.get("gate_failure_threshold", 0.2)),
            budget_deviation_threshold=float(data.get("budget_deviation_threshold", 0.5)),
            confidence_threshold=float(data.get("confidence_threshold", 0.7)),
        )


# ---------------------------------------------------------------------------
# ImprovementReport
# ---------------------------------------------------------------------------

@dataclass
class ImprovementReport:
    """Summary of a single improvement cycle run."""

    report_id: str
    timestamp: str = ""
    skipped: bool = False
    reason: str = ""            # why skipped, if applicable
    anomalies: list[dict] = field(default_factory=list)     # list of Anomaly.to_dict()
    recommendations: list[dict] = field(default_factory=list)  # list of Recommendation.to_dict()
    auto_applied: list[str] = field(default_factory=list)   # rec_ids that were auto-applied
    escalated: list[str] = field(default_factory=list)      # rec_ids that were escalated
    active_experiments: list[str] = field(default_factory=list)  # experiment_ids

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "report_id": self.report_id,
            "timestamp": self.timestamp,
            "skipped": self.skipped,
            "reason": self.reason,
            "anomalies": self.anomalies,
            "recommendations": self.recommendations,
            "auto_applied": self.auto_applied,
            "escalated": self.escalated,
            "active_experiments": self.active_experiments,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ImprovementReport:
        return cls(
            report_id=data["report_id"],
            timestamp=data.get("timestamp", ""),
            skipped=bool(data.get("skipped", False)),
            reason=data.get("reason", ""),
            anomalies=data.get("anomalies", []),
            recommendations=data.get("recommendations", []),
            auto_applied=data.get("auto_applied", []),
            escalated=data.get("escalated", []),
            active_experiments=data.get("active_experiments", []),
        )


# ---------------------------------------------------------------------------
# ImprovementConfig
# ---------------------------------------------------------------------------

@dataclass
class ImprovementConfig:
    """Top-level configuration for the improvement loop."""

    auto_apply_threshold: float = 0.8
    paused: bool = False        # circuit breaker state

    def to_dict(self) -> dict:
        return {
            "auto_apply_threshold": self.auto_apply_threshold,
            "paused": self.paused,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ImprovementConfig:
        return cls(
            auto_apply_threshold=float(data.get("auto_apply_threshold", 0.8)),
            paused=bool(data.get("paused", False)),
        )
