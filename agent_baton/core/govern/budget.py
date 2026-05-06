"""Wave 5 — BudgetEnforcer: self-heal and speculation cost gating.

Provides in-memory ledger for Wave 5 self-heal and speculative pipelining
cost caps.  Persistence will be wired when full Wave 2.2 BudgetEngine lands.

Caps (from wave-5-design.md):
    Self-heal per-step:  $0.50  (selfheal.per_step_cap_usd)
    Self-heal per-task:  $5.00  (selfheal.per_task_cap_usd)
    Speculation daily:   $2.00  (speculate.daily_cap_usd)

Wave 6.2 Part B additions (immune system):
    Immune daily cap:    $5.00  (immune.daily_cap_usd)
    Anomaly window:      60 min — >30% of daily cap in this window → 1 h suspension.

End-user readiness #7 — Run-level token ceiling (BATON_RUN_TOKEN_CEILING):
    A single run-level kill-switch that caps the total USD spend across all
    subsystems (self-heal escalations, speculative pipelining, immune sweeps).
    Set via the ``BATON_RUN_TOKEN_CEILING`` environment variable (USD float).
    When unset on a HIGH-risk run, a warning is logged at engine start.
    Raises :class:`RunTokenCeilingExceeded` before any LLM call that would
    push cumulative spend past the ceiling.
    The cumulative counter is persisted in ``ExecutionState.run_cumulative_spend_usd``
    so resumed runs continue counting correctly.

Pricing baseline (April 2026 per design):
    Haiku:   $0.25 / $1.25 per M input/output tokens
    Sonnet:  $3.00 / $15.00 per M input/output tokens
    Opus:    $15.00 / $75.00 per M input/output tokens

When a cap is exceeded mid-escalation, the CURRENT attempt is NOT killed.
The NEXT dispatch call is refused and a BEAD_WARNING is filed.
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Callable

_log = logging.getLogger(__name__)

__all__ = ["BudgetEnforcer", "RunTokenCeilingExceeded"]


# ---------------------------------------------------------------------------
# RunTokenCeilingExceeded exception
# ---------------------------------------------------------------------------


class RunTokenCeilingExceeded(Exception):
    """Raised when a pending LLM call would push cumulative spend past the
    ``BATON_RUN_TOKEN_CEILING`` for this run.

    Attributes:
        current_spend_usd: Total USD spent so far in this run.
        ceiling_usd: The configured ceiling from ``BATON_RUN_TOKEN_CEILING``.
        estimated_call_usd: Cost of the refused call.
        intent: Short description of the refused call (e.g. "selfheal haiku-1").
    """

    def __init__(
        self,
        current_spend_usd: float,
        ceiling_usd: float,
        estimated_call_usd: float,
        intent: str,
    ) -> None:
        self.current_spend_usd = current_spend_usd
        self.ceiling_usd = ceiling_usd
        self.estimated_call_usd = estimated_call_usd
        self.intent = intent
        super().__init__(
            f"BATON_RUN_TOKEN_CEILING exceeded: intent={intent!r} "
            f"current_spend=${current_spend_usd:.4f} "
            f"estimated_call=${estimated_call_usd:.4f} "
            f"ceiling=${ceiling_usd:.4f} "
            f"(would reach ${current_spend_usd + estimated_call_usd:.4f})"
        )

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


def _read_run_token_ceiling() -> float | None:
    """Read ``BATON_RUN_TOKEN_CEILING`` from env on every call (never cached).

    Returns:
        The ceiling as a float (USD), or ``None`` when the env var is unset
        or empty.  Logs a warning when the value cannot be parsed as a float.
    """
    raw = os.environ.get("BATON_RUN_TOKEN_CEILING", "").strip()
    if not raw:
        return None
    try:
        val = float(raw)
        if val <= 0:
            _log.warning(
                "BATON_RUN_TOKEN_CEILING=%r is not positive — treating as unset", raw
            )
            return None
        return val
    except ValueError:
        _log.warning(
            "BATON_RUN_TOKEN_CEILING=%r cannot be parsed as float — treating as unset", raw
        )
        return None


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

    # ── Immune system defaults (Wave 6.2 Part B) ─────────────────────────────
    DEFAULT_IMMUNE_DAILY_CAP_USD: float = 5.00
    # Fraction of daily cap that triggers the anomaly burst rule.
    IMMUNE_ANOMALY_BURST_PCT: float = 0.30
    # Rolling window (minutes) for the anomaly burst detector.
    IMMUNE_ANOMALY_WINDOW_MIN: int = 60
    # Auto-fix headroom: refuse when less than this fraction of cap remains.
    IMMUNE_AUTOFIX_HEADROOM_PCT: float = 0.05

    def __init__(
        self,
        per_step_cap_usd: float = DEFAULT_PER_STEP_CAP_USD,
        per_task_cap_usd: float = DEFAULT_PER_TASK_CAP_USD,
        speculation_daily_cap_usd: float = DEFAULT_SPECULATION_DAILY_CAP_USD,
        immune_daily_cap_usd: float = DEFAULT_IMMUNE_DAILY_CAP_USD,
        bead_warning_fn: Callable[[str, str, str], None] | None = None,
        task_id: str = "",
        initial_run_spend_usd: float = 0.0,
    ) -> None:
        self._per_step_cap = per_step_cap_usd
        self._per_task_cap = per_task_cap_usd
        self._spec_daily_cap = speculation_daily_cap_usd
        self._immune_daily_cap = immune_daily_cap_usd
        self._bead_warning = bead_warning_fn
        self._task_id = task_id

        # Ledgers (in-memory).
        # self-heal: step_id → total USD spent
        self._selfheal_step_spend: dict[str, float] = defaultdict(float)
        # self-heal: task-level total
        self._selfheal_task_spend: float = 0.0
        # speculation: day-string (YYYY-MM-DD) → total USD spent
        self._spec_daily_spend: dict[str, float] = defaultdict(float)
        # immune: day-string (YYYY-MM-DD) → total USD spent
        self._immune_daily_spend: dict[str, float] = defaultdict(float)
        # immune anomaly detector: deque of (utc_datetime, cost_usd) tuples
        self._immune_burst_window: deque[tuple[datetime, float]] = deque()
        # UTC timestamp at which anomaly suspension ends (None = not suspended)
        self._immune_suspended_until: datetime | None = None

        # ── Predictive speculation state (Wave 6.2 Part C, bd-03b0) ──────────
        self._predict_daily_cap: float = self.DEFAULT_PREDICT_DAILY_CAP_USD
        self._predict_daily_spend: dict[str, float] = defaultdict(float)
        self._predict_outcomes: list[bool] = []
        self._predict_disabled_until: datetime | None = None

        # ── Run-level ceiling (end-user readiness #7) ─────────────────────────
        # Cumulative USD spent across ALL subsystems in this run.
        # Restored from ExecutionState when resuming a crashed run so the
        # counter is never reset mid-execution.
        self._run_cumulative_spend_usd: float = initial_run_spend_usd

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

        Updates both per-step and per-task ledgers, and the run-level
        cumulative counter.
        """
        cost = _cost_usd(tier, tokens_in, tokens_out)
        self._selfheal_step_spend[step_id] += cost
        self._selfheal_task_spend += cost
        self.add_run_spend(cost)
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
        self.add_run_spend(cost)
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
    # ── Immune system API (Wave 6.2 Part B) ──────────────────────────────────

    def allow_immune_sweep(self) -> tuple[bool, str]:
        """Return ``(True, "")`` when an immune sweep is within budget.

        Returns ``(False, reason)`` when:
        - The daily cap for immune is fully consumed.
        - An anomaly burst was detected and the suspension window is active.

        The hard-kill at 100% is enforced here; soft warns are logged only.
        """
        now = datetime.now(tz=timezone.utc)

        # ── Anomaly suspension check ──────────────────────────────────────
        if self._immune_suspended_until is not None:
            if now < self._immune_suspended_until:
                remaining = (self._immune_suspended_until - now).total_seconds()
                reason = (
                    f"immune-burst-suspension active for {remaining:.0f}s more"
                )
                _log.debug("BudgetEnforcer: %s", reason)
                return False, reason
            else:
                # Suspension expired — clear it.
                self._immune_suspended_until = None

        # ── Daily cap check ───────────────────────────────────────────────
        day = _today_str()
        spent = self._immune_daily_spend[day]
        if spent >= self._immune_daily_cap:
            reason = (
                f"immune daily cap exhausted "
                f"(cap={self._immune_daily_cap:.2f} spent={spent:.4f})"
            )
            _log.warning("BudgetEnforcer: %s", reason)
            return False, reason

        # ── Soft warnings ─────────────────────────────────────────────────
        pct = spent / self._immune_daily_cap if self._immune_daily_cap > 0 else 0.0
        if pct >= 0.80:
            _log.warning(
                "BudgetEnforcer: immune budget at %.0f%% (hard cap at 100%%)", pct * 100
            )
        elif pct >= 0.50:
            _log.info(
                "BudgetEnforcer: immune budget at %.0f%%", pct * 100
            )

        return True, ""

    def record_immune_spend(
        self,
        target_path: str,
        kind: str,
        tokens_in: int,
        tokens_out: int,
    ) -> float:
        """Record immune sweep token spend and check for anomaly burst.

        Applies the cost to the daily ledger and the 60-minute rolling window.
        If the rolling window exceeds 30% of the daily cap, a 1-hour
        suspension is triggered and a BEAD_WARNING is filed.

        Args:
            target_path: Path of the swept target (for log context).
            kind: Sweep kind (for log context).
            tokens_in: Input tokens consumed by the sweep.
            tokens_out: Output tokens produced by the sweep.

        Returns:
            The USD cost of this sweep.
        """
        cost = _cost_usd("haiku", tokens_in, tokens_out)
        day = _today_str()
        self._immune_daily_spend[day] += cost
        self.add_run_spend(cost)

        # ── Anomaly burst tracking ─────────────────────────────────────────
        now = datetime.now(tz=timezone.utc)
        self._immune_burst_window.append((now, cost))
        # Purge entries older than the 60-min window.
        cutoff = now - timedelta(minutes=self.IMMUNE_ANOMALY_WINDOW_MIN)
        while self._immune_burst_window and self._immune_burst_window[0][0] < cutoff:
            self._immune_burst_window.popleft()

        window_total = sum(c for _, c in self._immune_burst_window)
        burst_threshold = self._immune_daily_cap * self.IMMUNE_ANOMALY_BURST_PCT
        if (
            window_total >= burst_threshold
            and self._immune_suspended_until is None
        ):
            self._immune_suspended_until = now + timedelta(hours=1)
            msg = (
                f"BEAD_WARNING: immune-burst-detected "
                f"window_cost_usd={window_total:.4f} "
                f"threshold_usd={burst_threshold:.4f} "
                f"suspended_until={self._immune_suspended_until.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            )
            _log.warning(msg)
            self._file_bead_warning("immune-sweep", msg)

        _log.debug(
            "BudgetEnforcer: immune spend path=%s kind=%s cost_usd=%.4f "
            "(day_total=%.4f cap=%.2f)",
            target_path, kind, cost, self._immune_daily_spend[day], self._immune_daily_cap,
        )
        return cost

    def has_headroom_for_auto_fix(self) -> bool:
        """Return ``True`` when there is sufficient budget for an auto-fix dispatch.

        Refuses when less than 5% of the daily immune cap remains (approximately
        one Haiku micro-agent call of budget headroom is required).
        """
        day = _today_str()
        spent = self._immune_daily_spend[day]
        remaining = self._immune_daily_cap - spent
        headroom = self._immune_daily_cap * self.IMMUNE_AUTOFIX_HEADROOM_PCT
        return remaining >= headroom

    def daily_cap_exceeded(self) -> bool:
        """Return ``True`` when the immune daily cap has been fully consumed."""
        day = _today_str()
        return self._immune_daily_spend[day] >= self._immune_daily_cap

    def anomaly_burst_detected(self) -> bool:
        """Return ``True`` when an anomaly burst suspension is currently active."""
        if self._immune_suspended_until is None:
            return False
        return datetime.now(tz=timezone.utc) < self._immune_suspended_until

    def immune_daily_spend(self) -> float:
        """Return today's total immune sweep spend in USD."""
        return self._immune_daily_spend[_today_str()]

    # ── Predictive computation API (Wave 6.2 Part C, bd-03b0) ─────────────────

    # Default per-project per-day cap for predictive speculation ($2/day).
    DEFAULT_PREDICT_DAILY_CAP_USD: float = 2.00
    # Accept-rate window size (rolling) for auto-disable logic.
    PREDICT_ACCEPT_RATE_WINDOW: int = 50
    # Auto-disable threshold: disable when rolling accept-rate < this.
    PREDICT_ACCEPT_RATE_MIN: float = 0.20
    # Auto-disable duration (hours).
    PREDICT_AUTO_DISABLE_HOURS: int = 24

    def _ensure_predict_state(self) -> None:
        """No-op: predict state is now initialised eagerly in __init__."""

    def allow_speculation_predict(self) -> tuple[bool, str]:
        """Return ``(True, "")`` when predict speculation is within budget.

        Checks the per-project daily cap ($2/day by default) and the
        auto-disable state triggered by a rolling accept-rate < 20% over
        the last 50 speculations.

        Returns:
            ``(allowed, reason)`` — when *allowed* is False, *reason* explains
            why.  Both ``allow_speculation()`` (Wave 5.3) and this method
            gate predict dispatches; callers should use this method for
            Wave 6.2 Part C speculations.
        """
        self._ensure_predict_state()

        # Auto-disable check.
        now = datetime.now(tz=timezone.utc)
        if self._predict_disabled_until is not None:
            if now < self._predict_disabled_until:
                remaining = (self._predict_disabled_until - now).total_seconds()
                reason = (
                    f"predict auto-disabled (low accept-rate); "
                    f"re-enables in {remaining:.0f}s"
                )
                return False, reason
            else:
                self._predict_disabled_until = None

        # Daily cap check.
        day = _today_str()
        spent = self._predict_daily_spend[day]
        if spent >= self._predict_daily_cap:
            reason = (
                f"predict daily cap exhausted "
                f"(cap={self._predict_daily_cap:.2f} spent={spent:.4f})"
            )
            _log.warning("BudgetEnforcer: %s", reason)
            return False, reason

        return True, ""

    def record_speculation_spend_predict(
        self,
        spec_id: str,
        tokens_in: int,
        tokens_out: int,
    ) -> float:
        """Record predictive speculation spend and check for auto-disable.

        Updates the daily ledger for the ``predict`` subsystem (distinct
        from the Wave 5.3 speculation ledger).

        Args:
            spec_id: The speculation ID (for log context).
            tokens_in: Input tokens consumed.
            tokens_out: Output tokens produced.

        Returns:
            The USD cost of this speculation.
        """
        self._ensure_predict_state()
        cost = _cost_usd("haiku", tokens_in, tokens_out)
        day = _today_str()
        self._predict_daily_spend[day] += cost
        _log.debug(
            "BudgetEnforcer: predict spend spec=%s cost_usd=%.4f "
            "(day_total=%.4f cap=%.2f)",
            spec_id, cost, self._predict_daily_spend[day], self._predict_daily_cap,
        )
        return cost

    def record_predict_outcome(self, spec_id: str, accepted: bool) -> None:
        """Record whether a predictive speculation was accepted by the developer.

        Maintains a rolling window of the last ``PREDICT_ACCEPT_RATE_WINDOW``
        outcomes.  When the accept-rate drops below ``PREDICT_ACCEPT_RATE_MIN``
        after a full window, predict auto-disables for 24 hours and a
        BEAD_WARNING is filed.

        Args:
            spec_id: The speculation ID (for log context).
            accepted: True when the developer accepted the speculation.
        """
        self._ensure_predict_state()
        self._predict_outcomes.append(accepted)
        if len(self._predict_outcomes) > self.PREDICT_ACCEPT_RATE_WINDOW:
            self._predict_outcomes = self._predict_outcomes[
                -self.PREDICT_ACCEPT_RATE_WINDOW :
            ]

        # Only check auto-disable when we have a full window.
        if len(self._predict_outcomes) >= self.PREDICT_ACCEPT_RATE_WINDOW:
            rate = sum(self._predict_outcomes) / len(self._predict_outcomes)
            if rate < self.PREDICT_ACCEPT_RATE_MIN and self._predict_disabled_until is None:
                self._predict_disabled_until = datetime.now(tz=timezone.utc) + timedelta(
                    hours=self.PREDICT_AUTO_DISABLE_HOURS
                )
                msg = (
                    f"BEAD_WARNING: predict-auto-disabled "
                    f"accept_rate={rate:.2%} over last "
                    f"{self.PREDICT_ACCEPT_RATE_WINDOW} speculations "
                    f"< {self.PREDICT_ACCEPT_RATE_MIN:.0%} minimum. "
                    f"Disabled until {self._predict_disabled_until.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                )
                _log.warning(msg)
                self._file_bead_warning(spec_id, msg)

        _log.debug(
            "BudgetEnforcer: predict outcome spec=%s accepted=%s "
            "(window=%d rate=%.0f%%)",
            spec_id, accepted,
            len(self._predict_outcomes),
            sum(self._predict_outcomes) / len(self._predict_outcomes) * 100
            if self._predict_outcomes else 0,
        )

    def predict_daily_spend(self) -> float:
        """Return today's predictive speculation spend in USD."""
        self._ensure_predict_state()
        return self._predict_daily_spend[_today_str()]

    def predict_is_disabled(self) -> bool:
        """Return True when the predict auto-disable is currently active."""
        self._ensure_predict_state()
        if self._predict_disabled_until is None:
            return False
        return datetime.now(tz=timezone.utc) < self._predict_disabled_until

    # ── Run-level ceiling API (end-user readiness #7) ─────────────────────────

    def check_run_ceiling(
        self,
        estimated_call_usd: float,
        intent: str,
    ) -> None:
        """Raise :class:`RunTokenCeilingExceeded` if this call would exceed
        the run-level ceiling set via ``BATON_RUN_TOKEN_CEILING``.

        The ceiling is re-read from the environment on every call so that
        operators can adjust it without restarting the daemon.

        This method is a no-op when ``BATON_RUN_TOKEN_CEILING`` is unset.

        Args:
            estimated_call_usd: Estimated USD cost of the pending LLM call.
            intent: Human-readable description of the call (for the exception
                message and logs), e.g. ``"selfheal haiku-1"`` or
                ``"speculation haiku"``.

        Raises:
            RunTokenCeilingExceeded: When cumulative spend + estimated cost
                would exceed the configured ceiling.
        """
        ceiling = _read_run_token_ceiling()
        if ceiling is None:
            return  # no ceiling configured — unlimited

        projected = self._run_cumulative_spend_usd + estimated_call_usd
        if projected > ceiling:
            _log.error(
                "BudgetEnforcer: run ceiling exceeded — intent=%r "
                "current_spend=%.4f estimated_call=%.4f ceiling=%.4f",
                intent,
                self._run_cumulative_spend_usd,
                estimated_call_usd,
                ceiling,
            )
            raise RunTokenCeilingExceeded(
                current_spend_usd=self._run_cumulative_spend_usd,
                ceiling_usd=ceiling,
                estimated_call_usd=estimated_call_usd,
                intent=intent,
            )

    def add_run_spend(self, cost_usd: float) -> None:
        """Add *cost_usd* to the run-level cumulative counter.

        Called automatically by the per-subsystem record methods
        (``record_self_heal_spend``, ``record_speculation_spend``,
        ``record_immune_spend``).  May also be called directly by callers
        that track cost outside the standard subsystem methods.

        Args:
            cost_usd: USD amount to add to the cumulative counter.
        """
        self._run_cumulative_spend_usd += cost_usd
        _log.debug(
            "BudgetEnforcer: run cumulative spend += %.4f → %.4f",
            cost_usd,
            self._run_cumulative_spend_usd,
        )

    @property
    def run_cumulative_spend_usd(self) -> float:
        """Total USD spent in this run across all subsystems."""
        return self._run_cumulative_spend_usd

    def warn_if_ceiling_unset_for_high_risk(self, risk_level: str) -> None:
        """Log a warning when ``BATON_RUN_TOKEN_CEILING`` is unset on a HIGH
        or CRITICAL risk run.

        Called once at engine start.  Uses ``risk_level`` from the plan to
        determine whether the warning is warranted.

        Args:
            risk_level: Plan risk level string, e.g. ``"HIGH"`` or
                ``"CRITICAL"``.
        """
        if risk_level.upper() not in ("HIGH", "CRITICAL"):
            return
        if _read_run_token_ceiling() is None:
            _log.warning(
                "BATON_RUN_TOKEN_CEILING is unset for a %s-risk run. "
                "Self-heal escalation and immune sweeps have no run-level kill-switch. "
                "Set BATON_RUN_TOKEN_CEILING=<usd> to cap total spend for this run.",
                risk_level.upper(),
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
