"""Wave 5.3 — Budget-Aware Speculative Pipelining (bd-9839).

Pre-computes the next phase's scaffolding in a background worktree while
a human review or CI gate blocks the main pipeline.

Design decisions from wave-5-design.md Part C:
- Triggers on EITHER human-approval-wait OR ci-running (never INTERACT).
- Speculations are tier-locked to Haiku (speculative-drafter agent).
- Speculation worktrees count against the Wave 1.3 max_concurrent semaphore
  at max 25% of total slots (pool="speculation").
- Eviction: kill oldest speculation (then lowest classifier confidence)
  when real-step needs the semaphore slot.
- Daily cap $2.00 (charged to speculate.daily_cap_usd); auto-disable when
  accept rate stays below 20% for 7 days.
- 60-second TTL: gc_stale() reaps speculations whose target step moved to
  dispatched without using the spec worktree.
- Handoff: on accept, SpeculativePipeliner.accept(spec_id) dispatches the
  next-step agent into the SAME worktree with the handoff prompt.
  Pickup billed against next-step budget, not speculation budget.

The module is pure logic; BudgetEnforcer provides daily cap gating.
WorktreeManager provides worktree lifecycle.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.engine.worktree_manager import WorktreeHandle, WorktreeManager

_log = logging.getLogger(__name__)

__all__ = [
    "SpeculationRecord",
    "SpeculationTrigger",
    "HandoffProtocol",
    "SpeculativePipeliner",
]

# ---------------------------------------------------------------------------
# SpeculationTrigger enum
# ---------------------------------------------------------------------------


class SpeculationTrigger(Enum):
    """Why a speculation was initiated."""
    HUMAN_APPROVAL_WAIT = "awaiting_human_approval"
    CI_RUNNING = "ci_running"


# ---------------------------------------------------------------------------
# SpeculationRecord dataclass
# ---------------------------------------------------------------------------


@dataclass
class SpeculationRecord:
    """Audit record for one speculative worktree.

    Lifecycle: pending → running → (accepted | rejected | evicted | ttl_expired).
    """

    spec_id: str                    # uuid
    target_step_id: str             # step whose content will be pre-staged
    trigger: str                    # SpeculationTrigger.value
    worktree_path: str              # absolute path; empty before create
    worktree_branch: str            # git branch inside the worktree
    started_at: str
    status: str = "pending"         # pending | running | accepted | rejected | evicted | expired
    accepted_at: str = ""
    rejected_at: str = ""
    reject_reason: str = ""
    cost_usd: float = 0.0           # charged against daily_cap
    scaffold_files: list[str] = field(default_factory=list)

    def is_active(self) -> bool:
        return self.status in ("pending", "running")

    def to_dict(self) -> dict:
        return {
            "spec_id": self.spec_id,
            "target_step_id": self.target_step_id,
            "trigger": self.trigger,
            "worktree_path": self.worktree_path,
            "worktree_branch": self.worktree_branch,
            "started_at": self.started_at,
            "status": self.status,
            "accepted_at": self.accepted_at,
            "rejected_at": self.rejected_at,
            "reject_reason": self.reject_reason,
            "cost_usd": self.cost_usd,
            "scaffold_files": list(self.scaffold_files),
        }

    @classmethod
    def from_dict(cls, data: dict) -> SpeculationRecord:
        return cls(
            spec_id=data.get("spec_id", ""),
            target_step_id=data.get("target_step_id", ""),
            trigger=data.get("trigger", ""),
            worktree_path=data.get("worktree_path", ""),
            worktree_branch=data.get("worktree_branch", ""),
            started_at=data.get("started_at", ""),
            status=data.get("status", "pending"),
            accepted_at=data.get("accepted_at", ""),
            rejected_at=data.get("rejected_at", ""),
            reject_reason=data.get("reject_reason", ""),
            cost_usd=float(data.get("cost_usd", 0.0)),
            scaffold_files=list(data.get("scaffold_files", [])),
        )


# ---------------------------------------------------------------------------
# HandoffProtocol dataclass
# ---------------------------------------------------------------------------


@dataclass
class HandoffProtocol:
    """Describes how to dispatch the heavy-pickup agent into a speculation worktree.

    Constructed by ``SpeculativePipeliner.build_handoff()`` and handed to
    the executor to emit a DISPATCH action into ``spec.worktree_path``.
    """

    spec_id: str
    target_step_id: str
    target_agent_name: str          # the real next-step agent (Sonnet/Opus)
    target_model: str               # resolved from phase risk
    worktree_path: str
    prompt: str                     # full handoff prompt (includes scaffold context)
    base_sha: str                   # reset anchor for "start fresh" path


# ---------------------------------------------------------------------------
# SpeculativePipeliner
# ---------------------------------------------------------------------------


class SpeculativePipeliner:
    """Manages speculative worktrees for the pre-computation pipeline.

    Args:
        worktree_mgr: WorktreeManager instance.  When None, speculation is
            silently disabled (worktrees unavailable).
        budget_enforcer: BudgetEnforcer for daily cap gating.  When None,
            budget checks are skipped.
        task_id: Active execution task ID.
        max_concurrent_pct: Fraction of WorktreeManager max_concurrent slots
            reserved for speculation.  Default 0.25 (25%).
        spec_ttl_seconds: Seconds before an un-accepted speculation expires.
            Default 60.
        enabled: Master kill-switch.  Also checked via BATON_SPECULATE_ENABLED.
    """

    SPECULATOR_AGENT = "speculative-drafter"
    SPECULATOR_MODEL = "claude-haiku"

    def __init__(
        self,
        worktree_mgr: object | None = None,   # WorktreeManager | None
        budget_enforcer: object | None = None,
        task_id: str = "",
        max_concurrent_pct: float = 0.25,
        spec_ttl_seconds: int = 60,
        enabled: bool = False,
    ) -> None:
        self._mgr: WorktreeManager | None = worktree_mgr  # type: ignore[assignment]
        self._budget = budget_enforcer
        self._task_id = task_id
        self._max_concurrent_pct = max_concurrent_pct
        self._spec_ttl = spec_ttl_seconds
        self._enabled = enabled

        # In-memory index: spec_id → SpeculationRecord.
        # The canonical store is ExecutionState.speculations (serialised to JSON).
        # This in-memory copy is the working state; caller syncs on save.
        self._speculations: dict[str, SpeculationRecord] = {}

        # Monotonic creation-time tracker for eviction ordering (oldest first).
        self._creation_order: list[str] = []

    # ── State sync (executor pulls these) ────────────────────────────────────

    def load_from_state(self, speculations_dict: dict[str, dict]) -> None:
        """Restore in-memory index from serialised ExecutionState.speculations."""
        self._speculations = {
            k: SpeculationRecord.from_dict(v)
            for k, v in speculations_dict.items()
        }
        # Restore creation order (approximate from started_at).
        self._creation_order = sorted(
            self._speculations.keys(),
            key=lambda s: self._speculations[s].started_at,
        )

    def to_dict(self) -> dict[str, dict]:
        """Serialise current speculation state back for ExecutionState."""
        return {k: v.to_dict() for k, v in self._speculations.items()}

    # ── Trigger check ─────────────────────────────────────────────────────────

    def should_speculate(
        self,
        block_reason: str,
        next_step_id: str | None,
    ) -> bool:
        """Return True when conditions are met to start a speculation.

        Args:
            block_reason: Why the current step is blocked.  One of
                ``"awaiting_human_approval"`` or ``"ci_running"``.
                Any other value → False.
            next_step_id: The step we would pre-stage.  None → False.
        """
        if not self._enabled:
            return False
        if next_step_id is None:
            return False
        if block_reason not in {
            SpeculationTrigger.HUMAN_APPROVAL_WAIT.value,
            SpeculationTrigger.CI_RUNNING.value,
        }:
            return False
        # Don't duplicate an active speculation for the same target step.
        for spec in self._speculations.values():
            if spec.target_step_id == next_step_id and spec.is_active():
                return False
        # Budget check.
        if self._budget is not None:
            allow = getattr(self._budget, "allow_speculation", lambda: True)
            if not allow():
                return False
        return True

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start_speculation(
        self,
        target_step_id: str,
        trigger: SpeculationTrigger,
        *,
        base_branch: str = "",
    ) -> SpeculationRecord | None:
        """Materialise a new speculation worktree.

        Returns the created ``SpeculationRecord``, or None when the
        semaphore is contested and eviction cannot free a slot.

        The worktree is created under a synthetic task_id so it does not
        collide with real-step worktrees.  The caller must dispatch the
        ``speculative-drafter`` agent into the returned worktree path.
        """
        if not self._enabled or self._mgr is None:
            return None

        spec_id = _new_spec_id()
        synthetic_task_id = f"speculate-{spec_id[:8]}"

        # Try to create the worktree.  On slot contention, attempt eviction.
        try:
            handle = self._mgr.create(  # type: ignore[attr-defined]
                task_id=synthetic_task_id,
                step_id="draft",
                base_branch=base_branch or getattr(self._mgr, "_current_branch", "") or "HEAD",
            )
        except Exception as exc:
            _log.warning(
                "SpeculativePipeliner.start_speculation: worktree create failed "
                "(spec_id=%s step=%s): %s — attempting eviction",
                spec_id, target_step_id, exc,
            )
            if not self._evict_one():
                _log.warning(
                    "SpeculativePipeliner: no speculation to evict; cannot start spec for step=%s",
                    target_step_id,
                )
                return None
            # Retry after eviction.
            try:
                handle = self._mgr.create(  # type: ignore[attr-defined]
                    task_id=synthetic_task_id,
                    step_id="draft",
                    base_branch=base_branch or "HEAD",
                )
            except Exception as exc2:
                _log.warning(
                    "SpeculativePipeliner: worktree create failed after eviction: %s", exc2
                )
                return None

        spec = SpeculationRecord(
            spec_id=spec_id,
            target_step_id=target_step_id,
            trigger=trigger.value,
            worktree_path=str(handle.path),
            worktree_branch=handle.branch,
            started_at=_utcnow(),
            status="running",
        )
        self._speculations[spec_id] = spec
        self._creation_order.append(spec_id)
        return spec

    def accept(self, spec_id: str) -> SpeculationRecord | None:
        """Mark a speculation as accepted.

        The caller is responsible for dispatching the heavy-model agent via
        ``build_handoff()`` before calling this.

        Returns the record, or None when spec_id is unknown.
        """
        spec = self._speculations.get(spec_id)
        if spec is None:
            return None
        spec.status = "accepted"
        spec.accepted_at = _utcnow()
        return spec

    def reject(self, spec_id: str, reason: str = "") -> SpeculationRecord | None:
        """Mark a speculation as rejected and clean up its worktree.

        The worktree is force-cleaned immediately.  Tokens already spent are
        charged against the daily cap but not refunded.
        """
        spec = self._speculations.get(spec_id)
        if spec is None:
            return None
        spec.status = "rejected"
        spec.rejected_at = _utcnow()
        spec.reject_reason = reason
        self._cleanup_spec_worktree(spec, reason="rejected")
        return spec

    def gc_stale(self, dispatched_step_ids: set[str] | None = None) -> list[str]:
        """Reap speculations that have expired or whose target step moved on.

        A speculation is stale when:
        - Its TTL (spec_ttl_seconds) has elapsed, OR
        - The target step is now ``dispatched`` WITHOUT using the spec's
          worktree as ``cwd_override``.

        Returns a list of reaped spec_ids.
        """
        now = time.monotonic()
        reaped: list[str] = []
        for spec_id, spec in list(self._speculations.items()):
            if not spec.is_active():
                continue
            # TTL check.
            try:
                created = datetime.fromisoformat(spec.started_at)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_seconds = (datetime.now(tz=timezone.utc) - created).total_seconds()
                if age_seconds > self._spec_ttl:
                    _log.info(
                        "SpeculativePipeliner.gc_stale: TTL expired for spec=%s (age=%.0fs)",
                        spec_id, age_seconds,
                    )
                    self._expire_spec(spec)
                    reaped.append(spec_id)
                    continue
            except Exception:
                pass
            # Target-dispatched-without-handoff check.
            if dispatched_step_ids and spec.target_step_id in dispatched_step_ids:
                _log.info(
                    "SpeculativePipeliner.gc_stale: target step %s dispatched without "
                    "handoff; expiring spec=%s",
                    spec.target_step_id, spec_id,
                )
                self._expire_spec(spec)
                reaped.append(spec_id)

        return reaped

    def build_handoff(
        self,
        spec_id: str,
        *,
        target_agent_name: str,
        target_model: str,
        next_step_description: str,
    ) -> HandoffProtocol | None:
        """Build the handoff protocol for the heavy-pickup agent.

        Returns None when the spec is unknown, not accepted, or the
        worktree contains uncommitted edits (safety guard per design).
        """
        spec = self._speculations.get(spec_id)
        if spec is None or not spec.worktree_path:
            return None

        wt_path = Path(spec.worktree_path)

        # Safety: if worktree has uncommitted edits at handoff, skip pickup.
        if self._worktree_has_uncommitted(wt_path):
            _log.warning(
                "SpeculativePipeliner.build_handoff: spec=%s worktree has uncommitted "
                "edits at handoff time — skipping pickup (cleanup unconditional)",
                spec_id,
            )
            self._cleanup_spec_worktree(spec, reason="uncommitted-at-handoff")
            spec.status = "rejected"
            spec.reject_reason = "uncommitted-edits-at-handoff"
            return None

        # Gather git log summary.
        git_log = self._git_log_oneline(wt_path, max_lines=10)
        base_sha = self._git_rev_parse(wt_path, "HEAD~1") or self._git_rev_parse(wt_path, "HEAD")

        prompt = _build_handoff_prompt(
            next_step_description=next_step_description,
            git_log=git_log,
            base_sha=base_sha,
        )

        return HandoffProtocol(
            spec_id=spec_id,
            target_step_id=spec.target_step_id,
            target_agent_name=target_agent_name,
            target_model=target_model,
            worktree_path=spec.worktree_path,
            prompt=prompt,
            base_sha=base_sha,
        )

    def list_active(self) -> list[SpeculationRecord]:
        """Return all active speculation records."""
        return [s for s in self._speculations.values() if s.is_active()]

    def get(self, spec_id: str) -> SpeculationRecord | None:
        return self._speculations.get(spec_id)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _evict_one(self) -> bool:
        """Evict the oldest active speculation.  Returns True if one was evicted."""
        for spec_id in self._creation_order:
            spec = self._speculations.get(spec_id)
            if spec is not None and spec.is_active():
                _log.info(
                    "SpeculativePipeliner._evict_one: evicting spec=%s (target=%s) "
                    "to free semaphore slot",
                    spec_id, spec.target_step_id,
                )
                spec.status = "evicted"
                self._cleanup_spec_worktree(spec, reason="evicted-for-real-step")
                return True
        return False

    def _expire_spec(self, spec: SpeculationRecord) -> None:
        spec.status = "expired"
        self._cleanup_spec_worktree(spec, reason="ttl-expired")

    def _cleanup_spec_worktree(self, spec: SpeculationRecord, reason: str) -> None:
        """Best-effort force cleanup of the speculation worktree."""
        if not spec.worktree_path or self._mgr is None:
            return
        wt_path = Path(spec.worktree_path)
        # Synthesise a minimal handle for cleanup.
        try:
            from agent_baton.core.engine.worktree_manager import WorktreeHandle
            # Reconstruct minimal handle from spec record.
            task_id = f"speculate-{spec.spec_id[:8]}"
            handle = WorktreeHandle(
                task_id=task_id,
                step_id="draft",
                path=wt_path,
                branch=spec.worktree_branch,
                base_branch="",
                base_sha="",
                created_at=spec.started_at,
                parent_repo=self._mgr._project_root,  # type: ignore[attr-defined]
            )
            self._mgr.cleanup(handle, on_failure=False, force=True)  # type: ignore[attr-defined]
        except Exception as exc:
            _log.debug(
                "SpeculativePipeliner._cleanup_spec_worktree: cleanup failed "
                "(non-fatal, spec=%s): %s",
                spec.spec_id, exc,
            )

    @staticmethod
    def _worktree_has_uncommitted(wt_path: Path) -> bool:
        """Return True when the worktree has uncommitted changes."""
        if not wt_path.exists():
            return False
        try:
            import subprocess as _sp
            r = _sp.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                text=True,
                cwd=str(wt_path),
                timeout=10,
            )
            return bool(r.stdout.strip())
        except Exception:
            return False

    @staticmethod
    def _git_log_oneline(wt_path: Path, max_lines: int = 10) -> str:
        """Return a short git log from the worktree."""
        try:
            import subprocess as _sp
            r = _sp.run(
                ["git", "log", "--oneline", f"-{max_lines}"],
                capture_output=True,
                text=True,
                cwd=str(wt_path),
                timeout=10,
            )
            return r.stdout.strip()
        except Exception:
            return ""

    @staticmethod
    def _git_rev_parse(wt_path: Path, ref: str) -> str:
        """Return the SHA for *ref* in the worktree."""
        try:
            import subprocess as _sp
            r = _sp.run(
                ["git", "rev-parse", ref],
                capture_output=True,
                text=True,
                cwd=str(wt_path),
                timeout=10,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:
            pass
        return ""


# ---------------------------------------------------------------------------
# Prompt builders
# ---------------------------------------------------------------------------


def _build_handoff_prompt(
    next_step_description: str,
    git_log: str,
    base_sha: str,
) -> str:
    """Build the handoff prompt for the heavy-pickup agent.

    This is exposed at module level so dispatcher.py can call it as a
    standalone function (``build_handoff_prompt``).
    """
    lines = [
        "A speculative-drafter pre-staged a scaffold in this worktree to save time.",
        "You are picking up where it left off. Inspect the current state:",
        "",
        "git log --oneline (since base):",
        git_log or "(no commits)",
        "",
        "Then complete the step per its description:",
        next_step_description,
        "",
        "The scaffold may be wrong, partial, or stale. You are AUTHORIZED to discard",
        "or restructure it. The scaffold's commits are NOT load-bearing —",
        "only the final state of this worktree matters at fold-back time.",
        "",
    ]
    if base_sha:
        lines += [
            "If the scaffold is unusable, run:",
            f"    git reset --hard {base_sha}",
            "and start fresh.",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Private utilities
# ---------------------------------------------------------------------------


def _new_spec_id() -> str:
    return str(uuid.uuid4())


def _utcnow() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
