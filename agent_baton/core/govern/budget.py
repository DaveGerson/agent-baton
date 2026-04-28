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

Pricing baseline (April 2026 per design):
    Haiku:   $0.25 / $1.25 per M input/output tokens
    Sonnet:  $3.00 / $15.00 per M input/output tokens
    Opus:    $15.00 / $75.00 per M input/output tokens

When a cap is exceeded mid-escalation, the CURRENT attempt is NOT killed.
The NEXT dispatch call is refused and a BEAD_WARNING is filed.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
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
