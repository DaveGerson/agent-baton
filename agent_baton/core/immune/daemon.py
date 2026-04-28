"""Wave 6.2 Part B — ImmuneDaemon: long-lived sweep daemon (bd-be76).

Runs as ``baton daemon immune start``.  The daemon ticks at
``config.tick_interval_sec`` (default 300 s / 5 min), picks the next sweep
target from :class:`~agent_baton.core.immune.scheduler.SweepScheduler`,
dispatches via :class:`~agent_baton.core.immune.sweeper.Sweeper`, and routes
findings through :class:`~agent_baton.core.immune.triage.FindingTriage`.

The daemon is disabled by default.  It activates only when:
  - ``BATON_IMMUNE_ENABLED=1`` is set, OR
  - ``immune.enabled: true`` is in ``baton.yaml``.

All state (sweep queue) is persisted to SQLite so the daemon is resumable
after a crash.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.govern.budget import BudgetEnforcer
    from agent_baton.core.immune.scheduler import SweepScheduler
    from agent_baton.core.immune.sweeper import Sweeper
    from agent_baton.core.immune.triage import FindingTriage

_log = logging.getLogger(__name__)

__all__ = ["ImmuneConfig", "ImmuneDaemon"]


# ---------------------------------------------------------------------------
# ImmuneConfig
# ---------------------------------------------------------------------------


@dataclass
class ImmuneConfig:
    """Configuration for :class:`ImmuneDaemon`.

    Defaults mirror ``baton.yaml`` defaults from wave-6-2-design.md.

    Attributes:
        enabled: Master switch.  Must be ``True`` for the daemon to run.
        daily_cap_usd: Maximum USD spent on immune sweeps per UTC day.
        sweep_kinds: List of sweep kinds to activate.
        auto_fix: Whether to dispatch auto-fix agents for qualifying findings.
        auto_fix_threshold: Minimum confidence for auto-fix dispatch (0.0–1.0).
        tick_interval_sec: Seconds to sleep between sweep cycles.
    """

    enabled: bool = False
    daily_cap_usd: float = 5.00
    sweep_kinds: list[str] = field(default_factory=lambda: [
        "deprecated-api",
        "doc-drift",
        "stale-comment",
    ])
    auto_fix: bool = True
    auto_fix_threshold: float = 0.85
    tick_interval_sec: int = 300  # 5 min between sweeps

    @classmethod
    def from_env(cls) -> "ImmuneConfig":
        """Build an :class:`ImmuneConfig` from environment variables.

        Recognised env vars:

        ``BATON_IMMUNE_ENABLED``
            Set to ``1`` or ``true`` to enable.  Default: disabled.

        ``BATON_IMMUNE_DAILY_CAP_USD``
            Daily budget cap in USD.  Default: 5.00.

        ``BATON_IMMUNE_TICK_SEC``
            Tick interval in seconds.  Default: 300.
        """
        enabled = os.environ.get("BATON_IMMUNE_ENABLED", "0").lower() in ("1", "true", "yes")
        daily_cap = float(os.environ.get("BATON_IMMUNE_DAILY_CAP_USD", "5.00"))
        tick_sec = int(os.environ.get("BATON_IMMUNE_TICK_SEC", "300"))
        return cls(
            enabled=enabled,
            daily_cap_usd=daily_cap,
            tick_interval_sec=tick_sec,
        )


# ---------------------------------------------------------------------------
# ImmuneDaemon
# ---------------------------------------------------------------------------


class ImmuneDaemon:
    """Long-lived immune sweep daemon.

    Args:
        config: :class:`ImmuneConfig` controlling the daemon's behaviour.
        budget: :class:`~agent_baton.core.govern.budget.BudgetEnforcer` for
            cost gating.
        scheduler: :class:`~agent_baton.core.immune.scheduler.SweepScheduler`
            providing the next target.
        sweeper: :class:`~agent_baton.core.immune.sweeper.Sweeper` that
            dispatches the sweep agent.
        triage: :class:`~agent_baton.core.immune.triage.FindingTriage` that
            files beads and optionally triggers auto-fix.
    """

    def __init__(
        self,
        config: ImmuneConfig,
        budget: "BudgetEnforcer",
        scheduler: "SweepScheduler",
        sweeper: "Sweeper",
        triage: "FindingTriage",
    ) -> None:
        self.config = config
        self.budget = budget
        self.scheduler = scheduler
        self.sweeper = sweeper
        self.triage = triage

        self._shutdown: threading.Event = threading.Event()
        self._last_tick_at: datetime | None = None
        self._ticks_run: int = 0
        self._findings_count: int = 0

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Main tick loop.  Runs until :meth:`shutdown` is called."""
        _log.info(
            "ImmuneDaemon: starting (tick_interval=%ds daily_cap=%.2f)",
            self.config.tick_interval_sec,
            self.config.daily_cap_usd,
        )

        while not self._shutdown.is_set():
            self._last_tick_at = datetime.now(timezone.utc)
            self._ticks_run += 1

            # ── Budget gate ───────────────────────────────────────────────
            allowed, reason = self.budget.allow_immune_sweep()
            if not allowed:
                _log.info("ImmuneDaemon: budget gate blocked sweep (%s) — waiting", reason)
                self._sleep_until_budget_reset()
                continue

            # ── Pick next target ──────────────────────────────────────────
            target = self.scheduler.next_target()
            if target is None:
                _log.debug("ImmuneDaemon: no targets ready — sleeping")
                self._sleep(self.config.tick_interval_sec)
                continue

            # ── Skip kinds not in the config whitelist ────────────────────
            if target.kind not in self.config.sweep_kinds:
                # Advance the queue to avoid spinning on excluded kinds.
                # Sleep briefly so tests can observe and shut down.
                self.scheduler.mark_swept(target, found_issue=False)
                self._sleep(self.config.tick_interval_sec)
                continue

            # ── Sweep ─────────────────────────────────────────────────────
            _log.debug(
                "ImmuneDaemon: sweeping %s kind=%s", target.path, target.kind
            )
            try:
                finding = self.sweeper.sweep(target)
            except Exception as exc:
                _log.warning("ImmuneDaemon: sweeper raised unexpectedly: %s", exc)
                finding = None

            found_issue = finding is not None

            # ── Record token spend ────────────────────────────────────────
            # Estimate: 12K input (cached → effective ~1.2K) + 1K output Haiku.
            self.budget.record_immune_spend(
                target_path=str(target.path),
                kind=target.kind,
                tokens_in=12_000,
                tokens_out=1_000,
            )

            # ── Triage ────────────────────────────────────────────────────
            if finding is not None:
                self._findings_count += 1
                try:
                    self.triage.handle(finding)
                except Exception as exc:
                    _log.warning("ImmuneDaemon: triage raised unexpectedly: %s", exc)

            # ── Update queue ──────────────────────────────────────────────
            self.scheduler.mark_swept(target, found_issue=found_issue)

            self._sleep(self.config.tick_interval_sec)

        _log.info(
            "ImmuneDaemon: stopped (ticks=%d findings=%d)",
            self._ticks_run, self._findings_count,
        )

    def shutdown(self, *, drain: bool = True) -> None:  # noqa: ARG002 (drain reserved)
        """Signal the daemon to stop after the current tick completes.

        Args:
            drain: When ``True`` (default) the current in-flight sweep is
                allowed to finish before the process exits.  ``False`` makes
                the sleep interruptible immediately.
        """
        _log.info("ImmuneDaemon: shutdown requested")
        self._shutdown.set()

    # ------------------------------------------------------------------
    # Status helpers (used by CLI `baton daemon immune status`)
    # ------------------------------------------------------------------

    @property
    def last_tick_at(self) -> datetime | None:
        """UTC timestamp of the most recent tick, or ``None`` before first tick."""
        return self._last_tick_at

    @property
    def ticks_run(self) -> int:
        """Number of tick cycles completed so far."""
        return self._ticks_run

    @property
    def findings_count(self) -> int:
        """Cumulative number of findings filed since daemon start."""
        return self._findings_count

    # ------------------------------------------------------------------
    # Sleep helpers
    # ------------------------------------------------------------------

    def _sleep(self, seconds: int) -> None:
        """Sleep for *seconds*, waking early if shutdown is requested."""
        self._shutdown.wait(timeout=float(seconds))

    def _sleep_until_budget_reset(self) -> None:
        """Sleep until the next 00:00 UTC (daily budget reset)."""
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        tomorrow = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        wait_sec = (tomorrow - now).total_seconds()
        _log.info(
            "ImmuneDaemon: daily cap hit — sleeping %.0f s until 00:00 UTC", wait_sec
        )
        self._shutdown.wait(timeout=wait_sec)
