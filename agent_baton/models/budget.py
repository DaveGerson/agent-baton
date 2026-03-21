"""Data models for budget tier recommendations."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BudgetRecommendation:
    """A recommendation to adjust a budget tier based on historical token usage.

    Attributes:
        task_type: Sequencing mode or inferred task type used as the group key.
        current_tier: The tier inferred from the median usage — "lean",
            "standard", or "full".
        recommended_tier: The tier recommended after applying the upgrade /
            downgrade rules — "lean", "standard", or "full".
        reason: Human-readable explanation of why the change is suggested.
        avg_tokens_used: Mean total tokens per task across all records in the
            group.
        median_tokens_used: Median total tokens — more robust than the average
            when outliers are present.
        p95_tokens_used: 95th-percentile total tokens, used to detect tasks
            that occasionally spike into a higher tier.
        sample_size: Number of TaskUsageRecords that contributed to the
            statistics.
        confidence: Score in [0.0, 1.0].  Formula: min(1.0, sample_size / 10).
        potential_savings: Estimated tokens saved per task for downgrade
            recommendations.  Zero for upgrade recommendations.
    """

    task_type: str
    current_tier: str
    recommended_tier: str
    reason: str
    avg_tokens_used: int
    median_tokens_used: int
    p95_tokens_used: int
    sample_size: int
    confidence: float
    potential_savings: int

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "task_type": self.task_type,
            "current_tier": self.current_tier,
            "recommended_tier": self.recommended_tier,
            "reason": self.reason,
            "avg_tokens_used": self.avg_tokens_used,
            "median_tokens_used": self.median_tokens_used,
            "p95_tokens_used": self.p95_tokens_used,
            "sample_size": self.sample_size,
            "confidence": self.confidence,
            "potential_savings": self.potential_savings,
        }

    @classmethod
    def from_dict(cls, data: dict) -> BudgetRecommendation:
        return cls(
            task_type=data["task_type"],
            current_tier=data["current_tier"],
            recommended_tier=data["recommended_tier"],
            reason=data.get("reason", ""),
            avg_tokens_used=int(data.get("avg_tokens_used", 0)),
            median_tokens_used=int(data.get("median_tokens_used", 0)),
            p95_tokens_used=int(data.get("p95_tokens_used", 0)),
            sample_size=int(data.get("sample_size", 0)),
            confidence=float(data.get("confidence", 0.0)),
            potential_savings=int(data.get("potential_savings", 0)),
        )
