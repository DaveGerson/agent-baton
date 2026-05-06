"""CostForecaster — token and dollar cost estimation for a MachinePlan.

Uses historical ``agent_usage`` rows from a SQLite ``BatonStore`` to derive
per-(agent, model) median token counts.  Falls back to hardcoded defaults
when no history exists for a given pair.

Pricing constants reflect Anthropic's public API pricing (2026):

    haiku:  $1 / 1M input,  $5 / 1M output
    sonnet: $3 / 1M input,  $15 / 1M output
    opus:   $15 / 1M input, $75 / 1M output

Confidence bands:
    low  = 0.75 * mid
    high = 1.25 * mid
"""
from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from agent_baton.models.cost_forecast import CostForecast

if TYPE_CHECKING:
    from agent_baton.models.execution import MachinePlan

# ── Defaults when no historical data is available ─────────────────────────────
_DEFAULT_TOKENS: dict[str, tuple[int, int]] = {
    # model → (input_tokens, output_tokens)
    "haiku": (2_000, 500),
    "sonnet": (8_000, 2_000),
    "opus": (20_000, 5_000),
}
_FALLBACK_TOKENS: tuple[int, int] = _DEFAULT_TOKENS["sonnet"]

# ── Pricing: USD per 1M tokens ────────────────────────────────────────────────
_PRICE_PER_1M: dict[str, tuple[float, float]] = {
    # model → (input_price, output_price)
    "haiku":  (1.0,  5.0),
    "sonnet": (3.0,  15.0),
    "opus":   (15.0, 75.0),
}
_FALLBACK_PRICE: tuple[float, float] = _PRICE_PER_1M["sonnet"]


def _model_key(model: str) -> str:
    """Normalise a model string to haiku / sonnet / opus."""
    m = model.lower()
    if "haiku" in m:
        return "haiku"
    if "opus" in m:
        return "opus"
    return "sonnet"


def _usd(input_tok: int, output_tok: int, price_in: float, price_out: float) -> float:
    return (input_tok * price_in + output_tok * price_out) / 1_000_000


class CostForecaster:
    """Estimate execution cost for a :class:`~agent_baton.models.execution.MachinePlan`.

    Args:
        store: An open ``sqlite3.Connection`` *or* a path-like to ``baton.db``.
               Pass ``None`` to force default-only forecasting (useful in tests).
        basis_window_days: How many trailing calendar days of ``agent_usage``
            history to include when computing token medians.
    """

    def __init__(
        self,
        store: sqlite3.Connection | str | None,
        *,
        basis_window_days: int = 14,
    ) -> None:
        self._conn: sqlite3.Connection | None
        if isinstance(store, str):
            self._conn = sqlite3.connect(store, timeout=5.0)
            self._conn.row_factory = sqlite3.Row
        else:
            self._conn = store
        self._basis_window_days = basis_window_days

    # ── Public API ─────────────────────────────────────────────────────────────

    def forecast(self, plan: MachinePlan) -> CostForecast:
        """Return a :class:`~agent_baton.models.cost_forecast.CostForecast` for *plan*.

        Args:
            plan: The ``MachinePlan`` to estimate costs for.

        Returns:
            A populated ``CostForecast`` with per-step breakdown and
            aggregate low/mid/high confidence bands.
        """
        history = self._load_history()
        sample_size = sum(len(v) for v in history.values())

        breakdown: list[dict] = []
        total_input = 0
        total_output = 0
        total_usd_mid = 0.0

        for step in plan.all_steps:
            agent = step.agent_name
            model_raw = getattr(step, "model", "sonnet") or "sonnet"
            mk = _model_key(model_raw)

            in_tok, out_tok = self._median_tokens(agent, mk, history)
            price_in, price_out = _PRICE_PER_1M.get(mk, _FALLBACK_PRICE)
            step_usd = _usd(in_tok, out_tok, price_in, price_out)

            breakdown.append(
                {
                    "agent_name": agent,
                    "model": mk,
                    "est_steps": 1,
                    "est_tokens": in_tok + out_tok,
                    "est_usd": round(step_usd, 6),
                }
            )
            total_input += in_tok
            total_output += out_tok
            total_usd_mid += step_usd

        # Aggregate per-agent breakdown (collapse duplicate agent+model rows)
        aggregated = self._aggregate_breakdown(breakdown)

        low = round(0.75 * total_usd_mid, 6)
        mid = round(total_usd_mid, 6)
        high = round(1.25 * total_usd_mid, 6)

        return CostForecast(
            plan_id=getattr(plan, "task_id", getattr(plan, "plan_id", "")),
            computed_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
            est_input_tokens=total_input,
            est_output_tokens=total_output,
            est_usd_low=low,
            est_usd_mid=mid,
            est_usd_high=high,
            basis_window_days=self._basis_window_days,
            sample_size=sample_size,
            breakdown=aggregated,
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _load_history(self) -> dict[tuple[str, str], list[int]]:
        """Return {(agent_name, model_key): [estimated_tokens, ...]} within window."""
        if self._conn is None:
            return {}

        cutoff = (
            datetime.now(tz=timezone.utc) - timedelta(days=self._basis_window_days)
        ).isoformat(timespec="seconds")

        try:
            rows = self._conn.execute(
                """
                SELECT au.agent_name, au.model, au.estimated_tokens
                FROM agent_usage au
                JOIN usage_records ur ON ur.task_id = au.task_id
                WHERE ur.timestamp >= ?
                """,
                (cutoff,),
            ).fetchall()
        except Exception:  # noqa: BLE001
            return {}

        result: dict[tuple[str, str], list[int]] = {}
        for row in rows:
            mk = _model_key(row[1] if isinstance(row, (list, tuple)) else row["model"])
            agent = row[0] if isinstance(row, (list, tuple)) else row["agent_name"]
            tokens = row[2] if isinstance(row, (list, tuple)) else row["estimated_tokens"]
            key = (agent, mk)
            result.setdefault(key, []).append(int(tokens))
        return result

    def _median_tokens(
        self,
        agent: str,
        model_key: str,
        history: dict[tuple[str, str], list[int]],
    ) -> tuple[int, int]:
        """Return (input_tokens, output_tokens) for agent+model.

        Splits a single ``estimated_tokens`` total by the canonical
        input/output ratio for the model tier.  When no history exists,
        returns the model's default values.
        """
        key = (agent, model_key)
        samples = history.get(key, [])
        if samples:
            median_total = int(statistics.median(samples))
            # split using canonical ratio for this model tier
            default_in, default_out = _DEFAULT_TOKENS.get(model_key, _FALLBACK_TOKENS)
            ratio = default_in / (default_in + default_out)
            in_tok = int(median_total * ratio)
            out_tok = median_total - in_tok
            return in_tok, out_tok

        return _DEFAULT_TOKENS.get(model_key, _FALLBACK_TOKENS)

    @staticmethod
    def _aggregate_breakdown(rows: list[dict]) -> list[dict]:
        """Collapse per-step rows into per-(agent, model) rows."""
        agg: dict[tuple[str, str], dict] = {}
        for row in rows:
            key = (row["agent_name"], row["model"])
            if key not in agg:
                agg[key] = {
                    "agent_name": row["agent_name"],
                    "model": row["model"],
                    "est_steps": 0,
                    "est_tokens": 0,
                    "est_usd": 0.0,
                }
            agg[key]["est_steps"] += row["est_steps"]
            agg[key]["est_tokens"] += row["est_tokens"]
            agg[key]["est_usd"] = round(agg[key]["est_usd"] + row["est_usd"], 6)
        return list(agg.values())
