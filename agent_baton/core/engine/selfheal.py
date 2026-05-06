"""Wave 5.2 — Self-Healing Micro-Agents with Escalation (bd-1483).

Implements the ``Haiku → Sonnet → Opus`` escalation ladder that automatically
attempts to repair a failing gate without human intervention.

Design decisions from wave-5-design.md Part B:
- 2 Haiku attempts (varied prompt on attempt 2)
- 2 Sonnet attempts (varied prompt on attempt 2)
- 1 Opus attempt
- Context expansion is MONOTONIC: each tier sees everything the previous
  tier saw, plus more.  Tier input caps: 4K / 16K / 64K tokens.
- Per-step budget: $0.50.  Per-task budget: $5.00.
- When budget is exhausted mid-escalation, refuse the next dispatch
  (cannot kill an in-flight subprocess).
- Dirty index between attempts: engine calls reset_dirty_index() before
  each next-tier attempt.
- Auditor gating on HIGH/CRITICAL phases only (auto-merge for LOW/MEDIUM).
- Run-level ceiling (end-user readiness #7): when BATON_RUN_TOKEN_CEILING
  is set and the ceiling trips, the escalation cycle aborts cleanly,
  marks the gate failure as final, and emits a clear log line.

All configuration is read from env-vars or baton.yaml (via flags.py).
The class itself is pure logic; BudgetEnforcer provides cost gating.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from agent_baton.utils.time import utcnow_seconds

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)

__all__ = [
    "EscalationTier",
    "SelfHealAttempt",
    "SelfHealResult",
    "SelfHealEscalator",
]

# ---------------------------------------------------------------------------
# EscalationTier enum
# ---------------------------------------------------------------------------


class EscalationTier(Enum):
    """Ordered escalation tiers for self-heal micro-agents.

    Values are the string keys used in ``SelfHealAttempt.tier``
    and in CLI output.
    """
    HAIKU_1 = "haiku-1"
    HAIKU_2 = "haiku-2"
    SONNET_1 = "sonnet-1"
    SONNET_2 = "sonnet-2"
    OPUS = "opus"


# Ordered sequence for escalation traversal.
_TIER_ORDER: list[EscalationTier] = [
    EscalationTier.HAIKU_1,
    EscalationTier.HAIKU_2,
    EscalationTier.SONNET_1,
    EscalationTier.SONNET_2,
    EscalationTier.OPUS,
]

# Model IDs per tier (resolved at dispatch time).
_TIER_MODELS: dict[EscalationTier, str] = {
    EscalationTier.HAIKU_1: "claude-haiku",
    EscalationTier.HAIKU_2: "claude-haiku",
    EscalationTier.SONNET_1: "claude-sonnet",
    EscalationTier.SONNET_2: "claude-sonnet",
    EscalationTier.OPUS: "claude-opus",
}

# Agent name per tier (matches agents/ directory).
_TIER_AGENTS: dict[EscalationTier, str] = {
    EscalationTier.HAIKU_1: "self-heal-haiku",
    EscalationTier.HAIKU_2: "self-heal-haiku",
    EscalationTier.SONNET_1: "self-heal-sonnet",
    EscalationTier.SONNET_2: "self-heal-sonnet",
    EscalationTier.OPUS: "self-heal-opus",
}

# Token input caps per tier (in tokens, not chars).
_TIER_INPUT_CAPS: dict[EscalationTier, int] = {
    EscalationTier.HAIKU_1: 4_000,
    EscalationTier.HAIKU_2: 4_000,
    EscalationTier.SONNET_1: 16_000,
    EscalationTier.SONNET_2: 16_000,
    EscalationTier.OPUS: 64_000,
}

# Token output caps per tier.
_TIER_OUTPUT_CAPS: dict[EscalationTier, int] = {
    EscalationTier.HAIKU_1: 1_000,
    EscalationTier.HAIKU_2: 1_000,
    EscalationTier.SONNET_1: 4_000,
    EscalationTier.SONNET_2: 4_000,
    EscalationTier.OPUS: 8_000,
}


# ---------------------------------------------------------------------------
# SelfHealAttempt / SelfHealResult dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SelfHealAttempt:
    """Audit record for a single self-heal micro-agent attempt.

    Stored in ``ExecutionState.selfheal_attempts`` and in the
    ``selfheal_attempts`` SQLite table (added in this wave).

    Status values:
        'success'            -- gate now passes.
        'gate-still-failing' -- agent ran OK but gate still fails.
        'agent-error'        -- launcher or agent raised an exception.
        'budget-skip'        -- BudgetEnforcer refused this tier.
        'ceiling-abort'      -- BATON_RUN_TOKEN_CEILING tripped; escalation
                                halted and gate failure is marked final.
    """

    parent_step_id: str
    tier: str                       # EscalationTier.value
    started_at: str
    ended_at: str
    status: str                     # see docstring
    tokens_in: int
    tokens_out: int
    cost_usd: float
    commit_hash: str = ""
    gate_stderr_tail: str = ""

    def to_dict(self) -> dict:
        return {
            "parent_step_id": self.parent_step_id,
            "tier": self.tier,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "commit_hash": self.commit_hash,
            "gate_stderr_tail": self.gate_stderr_tail,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SelfHealAttempt:
        return cls(
            parent_step_id=data.get("parent_step_id", ""),
            tier=data.get("tier", ""),
            started_at=data.get("started_at", ""),
            ended_at=data.get("ended_at", ""),
            status=data.get("status", ""),
            tokens_in=int(data.get("tokens_in", 0)),
            tokens_out=int(data.get("tokens_out", 0)),
            cost_usd=float(data.get("cost_usd", 0.0)),
            commit_hash=data.get("commit_hash", ""),
            gate_stderr_tail=data.get("gate_stderr_tail", ""),
        )


@dataclass
class SelfHealResult:
    """Aggregate result after the full self-heal escalation cycle completes.

    Returned by ``SelfHealEscalator.run_cycle()`` so the engine can decide
    whether to proceed or fall through to the standard failure path.
    """

    fixed: bool                     # True -> gate now passes; proceed with fold-back
    final_tier: str                 # EscalationTier.value of the last attempt
    total_attempts: int
    total_cost_usd: float
    commit_hash: str = ""           # winning commit (when fixed=True)
    failure_reason: str = ""        # summary when fixed=False


# ---------------------------------------------------------------------------
# SelfHealEscalator
# ---------------------------------------------------------------------------


class SelfHealEscalator:
    """Manages the Haiku -> Sonnet -> Opus escalation ladder for self-heal.

    Args:
        step_id: The step whose gate failed.
        gate_command: The shell command to re-run as the gate check.
        worktree_path: Path to the retained failed worktree.
        budget_enforcer: ``BudgetEnforcer`` instance for cost gating.
            When None, budget checks are skipped (testing / budget-disabled).
        max_tier: Maximum tier to escalate to.  Defaults to OPUS.
        haiku_attempts: Number of Haiku attempts.  Defaults to 2.
        sonnet_attempts: Number of Sonnet attempts.  Defaults to 2.
    """

    # Expose caps as class constants for easy reference.
    INPUT_CAPS = _TIER_INPUT_CAPS
    OUTPUT_CAPS = _TIER_OUTPUT_CAPS
    TIER_ORDER = _TIER_ORDER
    TIER_MODELS = _TIER_MODELS
    TIER_AGENTS = _TIER_AGENTS

    def __init__(
        self,
        step_id: str,
        gate_command: str,
        worktree_path: Path,
        *,
        budget_enforcer: object | None = None,   # BudgetEnforcer | None
        max_tier: EscalationTier = EscalationTier.OPUS,
        haiku_attempts: int = 2,
        sonnet_attempts: int = 2,
    ) -> None:
        self._step_id = step_id
        self._gate_command = gate_command
        self._worktree_path = worktree_path
        self._budget = budget_enforcer
        self._max_tier = max_tier
        self._haiku_attempts = haiku_attempts
        self._sonnet_attempts = sonnet_attempts

        # Accumulated context grows monotonically across tiers.
        self._accumulated_context: list[str] = []
        # Recorded attempts (completed + skipped).
        self._attempts: list[SelfHealAttempt] = []
        # Cache of prior attempt diff for prompt variation.
        self._prior_failed_patch: str = ""

    @property
    def attempts(self) -> list[SelfHealAttempt]:
        return list(self._attempts)

    # ── Public API ────────────────────────────────────────────────────────────

    def eligible(self, current_status: str) -> bool:
        """Return True when the step is eligible for a self-heal cycle.

        Eligibility: gate_failed status, no prior exhausted cycle.
        """
        if current_status != "gate_failed":
            return False
        # Allow new cycle if there are no prior SUCCESSFUL attempts.
        exhausted = sum(
            1 for a in self._attempts
            if a.status in ("gate-still-failing", "agent-error")
        )
        # Already exhausted all tiers?
        return exhausted < len(self._active_tiers())

    def _active_tiers(self) -> list[EscalationTier]:
        """Return tiers up to max_tier, filtered by attempt counts."""
        active: list[EscalationTier] = []
        haiku_used = 0
        sonnet_used = 0
        for tier in _TIER_ORDER:
            if tier == EscalationTier.OPUS and self._max_tier == EscalationTier.OPUS:
                active.append(tier)
                break
            if tier in (EscalationTier.HAIKU_1, EscalationTier.HAIKU_2):
                if haiku_used < self._haiku_attempts:
                    active.append(tier)
                    haiku_used += 1
            elif tier in (EscalationTier.SONNET_1, EscalationTier.SONNET_2):
                if sonnet_used < self._sonnet_attempts:
                    active.append(tier)
                    sonnet_used += 1
            if tier == self._max_tier:
                break
        return active

    def next_tier(self) -> EscalationTier | None:
        """Return the next tier to attempt, or None if the ladder is exhausted."""
        attempted = {a.tier for a in self._attempts if a.status != "budget-skip"}
        for tier in self._active_tiers():
            if tier.value not in attempted:
                return tier
        return None

    def next_tier_with_ceiling_check(self) -> EscalationTier | None:
        """Return the next tier to attempt after verifying the run-level ceiling.

        Identical to :meth:`next_tier` but also calls
        ``budget_enforcer.check_run_ceiling()`` with the estimated cost of
        the next tier before returning it.  When the ceiling would be
        exceeded, records a ``ceiling-abort`` attempt, logs a clear message,
        and returns ``None`` so the caller treats the gate failure as final.

        Returns:
            The next :class:`EscalationTier`, or ``None`` when either the
            ladder is exhausted or the run-level ceiling would be exceeded.
        """
        tier = self.next_tier()
        if tier is None:
            return None

        if self._budget is None:
            return tier

        # Estimate cost for the pending tier.
        tokens_in = _TIER_INPUT_CAPS.get(tier, 4_000)
        tokens_out = _TIER_OUTPUT_CAPS.get(tier, 1_000)

        try:
            from agent_baton.core.govern.budget import RunTokenCeilingExceeded, _cost_usd
            estimated = _cost_usd(tier.value, tokens_in, tokens_out)
            check_fn = getattr(self._budget, "check_run_ceiling", None)
            if check_fn is not None:
                check_fn(estimated, f"selfheal {tier.value}")
        except Exception as exc:
            from agent_baton.core.govern.budget import RunTokenCeilingExceeded
            if isinstance(exc, RunTokenCeilingExceeded):
                _log.error(
                    "SelfHealEscalator: run ceiling tripped — aborting escalation "
                    "for step=%s tier=%s. Gate failure is FINAL. %s",
                    self._step_id,
                    tier.value,
                    exc,
                )
                # Record a ceiling-abort attempt so callers and audit trails
                # know why escalation stopped.
                now = self._utcnow()
                abort_attempt = SelfHealAttempt(
                    parent_step_id=self._step_id,
                    tier=tier.value,
                    started_at=now,
                    ended_at=now,
                    status="ceiling-abort",
                    tokens_in=0,
                    tokens_out=0,
                    cost_usd=0.0,
                    gate_stderr_tail=str(exc),
                )
                self._attempts.append(abort_attempt)
                return None
            # Re-raise unexpected exceptions — they indicate a bug in
            # check_run_ceiling, not a ceiling trip.
            raise

        return tier

    def build_attempt_context(
        self,
        tier: EscalationTier,
        *,
        gate_stderr_tail: str = "",
        touched_files: list[str] | None = None,
        bead_summaries: list[str] | None = None,
        full_file_contents: dict[str, str] | None = None,
        project_summary: str = "",
    ) -> str:
        """Build the context block for *tier* using monotonic accumulation.

        Each tier receives a superset of the previous tier's context.

        T1/T2 (Haiku): diff + last 50 lines stderr + 1-line directive.
        T3/T4 (Sonnet): T1 + 10-line windows per touched file + beads (<=5).
        T5 (Opus): T2 + full file contents + project summary.

        Context is accumulated in ``self._accumulated_context`` so the
        caller does not need to pass prior tiers' data again.
        """
        # Always include gate stderr tail.
        if gate_stderr_tail and gate_stderr_tail not in self._accumulated_context:
            self._accumulated_context.append(
                f"GATE OUTPUT (last 50 lines):\n{gate_stderr_tail}"
            )

        # Haiku tiers: diff + stderr.  Already in accumulated.
        # Sonnet tiers: add file context windows + beads.
        if tier in (EscalationTier.SONNET_1, EscalationTier.SONNET_2, EscalationTier.OPUS):
            if touched_files:
                for fpath in (touched_files or [])[:10]:
                    snippet = self._get_file_snippet(fpath, lines=10)
                    entry = f"FILE CONTEXT ({fpath}, 10-line window):\n{snippet}"
                    if entry not in self._accumulated_context:
                        self._accumulated_context.append(entry)
            if bead_summaries:
                for bead in (bead_summaries or [])[:5]:
                    entry = f"BEAD: {bead}"
                    if entry not in self._accumulated_context:
                        self._accumulated_context.append(entry)

        # Opus tier: add full file contents + project summary.
        if tier == EscalationTier.OPUS:
            if full_file_contents:
                for fpath, content in (full_file_contents or {}).items():
                    entry = f"FULL FILE ({fpath}):\n{content}"
                    if entry not in self._accumulated_context:
                        self._accumulated_context.append(entry)
            if project_summary:
                entry = f"PROJECT SUMMARY:\n{project_summary}"
                if entry not in self._accumulated_context:
                    self._accumulated_context.append(entry)

        return "\n\n".join(self._accumulated_context)

    def record_attempt(self, attempt: SelfHealAttempt) -> None:
        """Record a completed attempt.  Updates prior failed patch cache."""
        self._attempts.append(attempt)
        if attempt.status == "gate-still-failing" and attempt.commit_hash:
            # Cache the diff for "DO NOT REPEAT" variation on next attempt.
            self._prior_failed_patch = self._get_diff_for_commit(attempt.commit_hash)

    def prior_failed_patch(self) -> str:
        """Return the diff from the last failed attempt (empty if none)."""
        return self._prior_failed_patch

    # ── Worktree helpers ──────────────────────────────────────────────────────

    def reset_dirty_index(self) -> bool:
        """Run ``git reset --hard HEAD`` in the worktree.

        Called between tier attempts when the prior agent left a dirty index.
        Returns True on success.
        """
        try:
            r = subprocess.run(
                ["git", "reset", "--hard", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(self._worktree_path),
                timeout=30,
            )
            if r.returncode == 0:
                _log.info(
                    "SelfHealEscalator: reset dirty index at %s", self._worktree_path
                )
                return True
            _log.warning(
                "SelfHealEscalator: git reset --hard failed (exit %d): %s",
                r.returncode, r.stderr.strip(),
            )
        except Exception as exc:
            _log.warning("SelfHealEscalator: reset_dirty_index error: %s", exc)
        return False

    def worktree_is_dirty(self) -> bool:
        """Return True when the worktree has uncommitted changes."""
        try:
            r = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                cwd=str(self._worktree_path),
                timeout=10,
            )
            return bool(r.stdout.strip())
        except Exception:
            return False

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_file_snippet(self, file_path: str, lines: int = 10) -> str:
        """Return up to *lines* lines from *file_path* in the worktree."""
        full = self._worktree_path / file_path
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
            all_lines = text.splitlines()
            return "\n".join(all_lines[:lines])
        except Exception:
            return "(unreadable)"

    def _get_diff_for_commit(self, commit_hash: str) -> str:
        """Return the diff introduced by *commit_hash*."""
        try:
            r = subprocess.run(
                ["git", "diff", f"{commit_hash}~1", commit_hash],
                capture_output=True,
                text=True,
                cwd=str(self._worktree_path),
                timeout=20,
            )
            return r.stdout[:8000]  # cap to avoid token blowout
        except Exception:
            return ""

    @staticmethod
    def _utcnow() -> str:
        return utcnow_seconds()
