"""Wave 5 — BudgetEnforcer: self-heal and speculation cost gating.

Provides in-memory ledger for Wave 5 self-heal and speculative pipelining
cost caps.  Persistence will be wired when full Wave 2.2 BudgetEngine lands.

Caps (from wave-5-design.md):
    Self-heal per-step:  $0.50  (selfheal.per_step_cap_usd)
    Self-heal per-task:  $5.00  (selfheal.per_task_cap_usd)
    Speculation daily:   $2.00  (speculate.daily_cap_usd)

Pricing baseline (April 2026 per design):
    Haiku:   $0.25 / $1.25 per M input/output tokens
    Sonnet:  $3.00 / $15.00 per M input/output tokens
    Opus:    $15.00 / $75.00 per M input/output tokens

When a cap is exceeded mid-escalation, the CURRENT attempt is NOT killed.
The NEXT dispatch call is refused and a BEAD_WARNING is filed.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

_log = logging.getLogger(__name__)

__all__ = ["BudgetEnforcer"]

# ---------------------------------------------------------------------------
# Per-tier pricing (USD per 1M tokens)
# ---------------------------------------------------------------------------

_PRICING_INPUT: dict[str, float] = {
    "haiku-1": 0.25 / 1_000_000,
    "haiku-2": 0.25 / 1_000_000,
    "sonnet-1": 3.00 / 1_000_000,
    "sonnet-2": 3.00 / 1_000_000,
    "opus": 15.00 / 1_000_000,
    "haiku": 0.25 / 1_000_000,
    "sonnet": 3.00 / 1_000_000,
    "speculation": 0.25 / 1_000_000,  # speculation drafter uses Haiku
}

_PRICING_OUTPUT: dict[str, float] = {
    "haiku-1": 1.25 / 1_000_000,
    "haiku-2": 1.25 / 1_000_000,
    "sonnet-1": 15.00 / 1_000_000,
    "sonnet-2": 15.00 / 1_000_000,
    "opus": 75.00 / 1_000_000,
    "haiku": 1.25 / 1_000_000,
    "sonnet": 15.00 / 1_000_000,
    "speculation": 1.25 / 1_000_000,
}


def _cost_usd(tier: str, tokens_in: int, tokens_out: int) -> float:
    """Compute USD cost for *tier* given token counts."""
    tier_key = tier.lower()
    price_in = _PRICING_INPUT.get(tier_key, _PRICING_INPUT["haiku"])
    price_out = _PRICING_OUTPUT.get(tier_key, _PRICING_OUTPUT["haiku"])
    return tokens_in * price_in + tokens_out * price_out


# ---------------------------------------------------------------------------
# BudgetEnforcer
# ---------------------------------------------------------------------------


class BudgetEnforcer:
    """In-memory cost enforcer for Wave 5 self-heal and speculation.

    All state is held in-memory for v1.  Full Wave 2.2 will add SQLite
    persistence, daily-reset logic, and cross-execution aggregation.

    Args:
        per_step_cap_usd: Maximum USD spend on self-heal per step.
        per_task_cap_usd: Maximum USD spend on self-heal for the whole task.
        speculation_daily_cap_usd: Maximum USD/day for all speculations
            in this project.
        bead_warning_fn: Optional callable to file a BEAD_WARNING.  Signature:
            ``(task_id: str, step_id: str, content: str) -> None``.
        task_id: Active execution task_id (used for bead filing).
    """

    # Public defaults matching wave-5-design.md
    DEFAULT_PER_STEP_CAP_USD: float = 0.50
    DEFAULT_PER_TASK_CAP_USD: float = 5.00
    DEFAULT_SPECULATION_DAILY_CAP_USD: float = 2.00

    def __init__(
        self,
        per_step_cap_usd: float = DEFAULT_PER_STEP_CAP_USD,
        per_task_cap_usd: float = DEFAULT_PER_TASK_CAP_USD,
        speculation_daily_cap_usd: float = DEFAULT_SPECULATION_DAILY_CAP_USD,
        bead_warning_fn: Callable[[str, str, str], None] | None = None,
        task_id: str = "",
    ) -> None:
        self._per_step_cap = per_step_cap_usd
        self._per_task_cap = per_task_cap_usd
        self._spec_daily_cap = speculation_daily_cap_usd
        self._bead_warning = bead_warning_fn
        self._task_id = task_id

        # Ledgers (in-memory).
        # self-heal: step_id → total USD spent
        self._selfheal_step_spend: dict[str, float] = defaultdict(float)
        # self-heal: task-level total
        self._selfheal_task_spend: float = 0.0
        # speculation: day-string (YYYY-MM-DD) → total USD spent
        self._spec_daily_spend: dict[str, float] = defaultdict(float)

    # ── Self-heal API ─────────────────────────────────────────────────────────

    def allow_self_heal(self, step_id: str, tier: str) -> bool:
        """Return True when a self-heal dispatch is within budget.

        Checks both per-step and per-task caps.  Logs a warning and files
        a BEAD_WARNING when either cap would be exceeded.
        """
        step_spent = self._selfheal_step_spend[step_id]
        if step_spent >= self._per_step_cap:
            msg = (
                f"BEAD_WARNING: selfheal-budget-exhausted "
                f"step={step_id} cap_usd={self._per_step_cap:.2f} "
                f"spent_usd={step_spent:.4f} refused_tier={tier}"
            )
            _log.warning(msg)
            self._file_bead_warning(step_id, msg)
            return False

        if self._selfheal_task_spend >= self._per_task_cap:
            msg = (
                f"BEAD_WARNING: selfheal-budget-exhausted "
                f"step={step_id} task-level cap_usd={self._per_task_cap:.2f} "
                f"spent_usd={self._selfheal_task_spend:.4f} refused_tier={tier}"
            )
            _log.warning(msg)
            self._file_bead_warning(step_id, msg)
            return False

        return True

    def record_self_heal_spend(
        self,
        step_id: str,
        tier: str,
        tokens_in: int,
        tokens_out: int,
    ) -> float:
        """Record self-heal spend for *step_id* and return the cost.

        Updates both per-step and per-task ledgers.
        """
        cost = _cost_usd(tier, tokens_in, tokens_out)
        self._selfheal_step_spend[step_id] += cost
        self._selfheal_task_spend += cost
        _log.debug(
            "BudgetEnforcer: self-heal spend step=%s tier=%s tokens_in=%d "
            "tokens_out=%d cost_usd=%.4f (step_total=%.4f task_total=%.4f)",
            step_id, tier, tokens_in, tokens_out, cost,
            self._selfheal_step_spend[step_id], self._selfheal_task_spend,
        )
        return cost

    def self_heal_step_spend(self, step_id: str) -> float:
        """Return total USD spent on self-heal for *step_id*."""
        return self._selfheal_step_spend[step_id]

    def self_heal_task_spend(self) -> float:
        """Return total USD spent on self-heal for the whole task."""
        return self._selfheal_task_spend

    # ── Speculation API ───────────────────────────────────────────────────────

    def allow_speculation(self) -> bool:
        """Return True when a new speculation is within the daily budget."""
        day = _today_str()
        spent = self._spec_daily_spend[day]
        if spent >= self._spec_daily_cap:
            _log.warning(
                "BudgetEnforcer: speculation daily cap exhausted "
                "(cap=%.2f spent=%.4f day=%s)",
                self._spec_daily_cap, spent, day,
            )
            return False
        return True

    def record_speculation_spend(
        self,
        spec_id: str,
        tokens_in: int,
        tokens_out: int,
    ) -> float:
        """Record speculation spend against the daily cap.  Returns cost."""
        cost = _cost_usd("speculation", tokens_in, tokens_out)
        day = _today_str()
        self._spec_daily_spend[day] += cost
        _log.debug(
            "BudgetEnforcer: speculation spend spec=%s cost_usd=%.4f "
            "(daily_total=%.4f cap=%.2f)",
            spec_id, cost, self._spec_daily_spend[day], self._spec_daily_cap,
        )
        return cost

    def speculation_daily_spend(self) -> float:
        """Return today's speculation spend."""
        return self._spec_daily_spend[_today_str()]

    # ── Swarm API (Wave 6.2 Part A, bd-707d) ─────────────────────────────────

    # Default per-swarm cap: $5.00 (matches immune daily cap)
    DEFAULT_SWARM_CAP_USD: float = 5.00

    def preflight_swarm(
        self,
        chunks: list,
        model: str,
        est_tokens_per_chunk: int,
    ) -> bool:
        """Return True when the swarm fits within the per-swarm budget cap.

        Args:
            chunks: List of :class:`~agent_baton.core.swarm.partitioner.CodeChunk`
                objects (uses ``len(chunks)`` only — no direct import to avoid
                circular dependency).
            model: LLM model tier (used to select pricing).
            est_tokens_per_chunk: Estimated tokens per chunk agent invocation.

        Returns:
            True if the estimated cost is within cap; False otherwise.
        """
        n_chunks = len(chunks)
        # Estimate: est_tokens_per_chunk input + 25% output
        tokens_in = n_chunks * est_tokens_per_chunk
        tokens_out = n_chunks * (est_tokens_per_chunk // 4)
        estimated_cost = _cost_usd(model, tokens_in, tokens_out)

        cap = self.DEFAULT_SWARM_CAP_USD
        if estimated_cost > cap:
            msg = (
                f"BEAD_WARNING: swarm-budget-preflight-rejected "
                f"n_chunks={n_chunks} model={model} "
                f"est_tokens_per_chunk={est_tokens_per_chunk} "
                f"estimated_cost_usd={estimated_cost:.4f} cap_usd={cap:.2f}"
            )
            _log.warning(msg)
            self._file_bead_warning("swarm-preflight", msg)
            return False

        _log.debug(
            "BudgetEnforcer.preflight_swarm: OK n_chunks=%d model=%s "
            "est_cost_usd=%.4f cap_usd=%.2f",
            n_chunks, model, estimated_cost, cap,
        )
        return True

    def record_swarm_spend(
        self,
        swarm_id: str,
        tokens_in: int,
        tokens_out: int,
    ) -> None:
        """Record actual swarm spend against the task-level ledger.

        Args:
            swarm_id: Unique identifier for the swarm execution.
            tokens_in: Total input tokens consumed by the swarm.
            tokens_out: Total output tokens produced by the swarm.
        """
        cost = _cost_usd("haiku", tokens_in, tokens_out)
        # Record against the task-level self-heal ledger as a unified cost sink.
        # Full Wave 2.2 persistence will add a dedicated swarm ledger.
        self._selfheal_task_spend += cost
        _log.info(
            "BudgetEnforcer.record_swarm_spend: swarm_id=%s tokens_in=%d "
            "tokens_out=%d cost_usd=%.4f (task_total=%.4f)",
            swarm_id, tokens_in, tokens_out, cost, self._selfheal_task_spend,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _file_bead_warning(self, step_id: str, content: str) -> None:
        if self._bead_warning is None:
            return
        try:
            self._bead_warning(self._task_id, step_id, content)
        except Exception as exc:
            _log.debug("BudgetEnforcer: bead_warning_fn failed (non-fatal): %s", exc)


def _today_str() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
