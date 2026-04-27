"""Cost forecast model — estimated token and dollar cost for a plan.

``CostForecast`` is a pure data container produced by
:class:`~agent_baton.core.observe.cost_forecaster.CostForecaster`.
It provides a per-agent breakdown plus aggregate low/mid/high confidence
bands for the total spend.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CostForecast:
    """Estimated cost for executing a :class:`~agent_baton.models.execution.MachinePlan`.

    Attributes:
        plan_id: Identifier of the plan being forecasted.
        computed_at: ISO-8601 timestamp when the forecast was computed.
        est_input_tokens: Estimated total input tokens across all steps.
        est_output_tokens: Estimated total output tokens across all steps.
        est_usd_low: Lower-bound cost estimate (USD), 0.75 * mid.
        est_usd_mid: Mid-point cost estimate (USD).
        est_usd_high: Upper-bound cost estimate (USD), 1.25 * mid.
        basis_window_days: Number of historical days used for token medians.
        sample_size: Number of historical agent_usage rows consulted.
        breakdown: Per-agent detail rows::

            [{"agent_name": str, "model": str, "est_steps": int,
              "est_tokens": int, "est_usd": float}, ...]
    """

    plan_id: str
    computed_at: str
    est_input_tokens: int
    est_output_tokens: int
    est_usd_low: float
    est_usd_mid: float
    est_usd_high: float
    basis_window_days: int
    sample_size: int
    breakdown: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable dict of this forecast."""
        return {
            "plan_id": self.plan_id,
            "computed_at": self.computed_at,
            "est_input_tokens": self.est_input_tokens,
            "est_output_tokens": self.est_output_tokens,
            "est_usd_low": self.est_usd_low,
            "est_usd_mid": self.est_usd_mid,
            "est_usd_high": self.est_usd_high,
            "basis_window_days": self.basis_window_days,
            "sample_size": self.sample_size,
            "breakdown": list(self.breakdown),
        }
