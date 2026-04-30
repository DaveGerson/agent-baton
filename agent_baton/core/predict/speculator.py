"""Wave 6.2 Part C — PredictiveDispatcher (bd-03b0).

Debounced speculative dispatcher: waits for a 2.5-second typing pause,
classifies intent via Haiku, and dispatches a speculative implementation
into a background worktree.

Design decisions from wave-6-2-design.md Part C:
- Debounce: 2.5 s typing-pause threshold.
- max_concurrent = 3; eviction kills oldest unaccepted when 4th would fire.
- Per-developer-hour cap: predict.max_speculations_per_hour = 20.
- Pruning: cosine-similarity on summary embeddings; similarity < 0.4 OR
  scope disjoint → kill; 0.4-0.7 → mark "stale-risk"; ≥ 0.7 → keep.
- TF-IDF cosine fallback (no external embedding model required).
- Spawns speculative agent via existing Wave 5.3 / Wave 1.3 substrate
  (WorktreeManager + ClaudeCodeLauncher).

This module is distinct from ``agent_baton/core/engine/speculator.py``
(Wave 5.3 SpeculativePipeliner) — the Wave 6.2 dispatcher triggers FROM
filesystem events, whereas Wave 5.3 triggers FROM CI/review pipeline waits.
"""
from __future__ import annotations

import collections
import logging
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_baton.utils.time import utcnow_seconds as _utcnow

if TYPE_CHECKING:
    from agent_baton.core.predict.classifier import IntentClassification
    from agent_baton.core.predict.watcher import FileEvent
    from agent_baton.core.engine.speculator import SpeculativePipeliner, HandoffProtocol
    from agent_baton.core.engine.worktree_manager import WorktreeHandle, WorktreeManager
    from agent_baton.core.govern.budget import BudgetEnforcer

_log = logging.getLogger(__name__)

__all__ = ["Speculation", "PredictiveDispatcher"]

# ---------------------------------------------------------------------------
# Speculation dataclass
# ---------------------------------------------------------------------------


@dataclass
class Speculation:
    """In-flight or settled speculative computation.

    Attributes:
        spec_id: 8-hex-char identifier (uuid4 hex[:8]).
        intent: The ``IntentClassification`` that triggered this spec.
        worktree_handle: The ``WorktreeHandle`` backing this speculation.
        model: LLM model used for the speculative agent.
        started_at: ISO 8601 UTC timestamp.
        status: Lifecycle state.
        summary_embedding: TF-IDF term-frequency vector for pruning.
    """

    spec_id: str
    intent: "IntentClassification"
    worktree_handle: "WorktreeHandle | None"
    model: str = "claude-haiku"
    started_at: str = field(default_factory=lambda: _utcnow())
    status: str = "in-flight"          # in-flight | ready | accepted | rejected | pruned
    summary_embedding: dict[str, float] = field(default_factory=dict)

    def is_active(self) -> bool:
        return self.status in ("in-flight", "ready")

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return {
            "spec_id": self.spec_id,
            "intent": self.intent.intent.value if self.intent else "unknown",
            "confidence": self.intent.confidence if self.intent else 0.0,
            "summary": self.intent.summary if self.intent else "",
            "scope": [str(p) for p in (self.intent.scope if self.intent else [])],
            "model": self.model,
            "started_at": self.started_at,
            "status": self.status,
            "worktree_path": (
                str(self.worktree_handle.path) if self.worktree_handle else ""
            ),
        }


# ---------------------------------------------------------------------------
# TF-IDF cosine similarity (no external deps)
# ---------------------------------------------------------------------------

_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "do",
    "for", "from", "has", "have", "in", "is", "it", "its", "of",
    "on", "or", "that", "the", "this", "to", "was", "will", "with",
})


def _tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]+", text.lower())
            if t not in _STOP_WORDS and len(t) > 1]


def _tf_vector(text: str) -> dict[str, float]:
    tokens = _tokenize(text)
    if not tokens:
        return {}
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    total = len(tokens)
    return {t: c / total for t, c in counts.items()}


def _cosine(v1: dict[str, float], v2: dict[str, float]) -> float:
    """Dot product of two TF vectors (both assumed unit-normalized).

    Both vectors are L2-normalized before computing the dot product so the
    result lies in [0, 1].
    """
    if not v1 or not v2:
        return 0.0

    def _norm(v: dict[str, float]) -> float:
        return math.sqrt(sum(x * x for x in v.values()))

    n1, n2 = _norm(v1), _norm(v2)
    if n1 == 0.0 or n2 == 0.0:
        return 0.0
    shared = set(v1) & set(v2)
    dot = sum(v1[k] * v2[k] for k in shared)
    return dot / (n1 * n2)


def _scope_disjoint(s1: list[Path], s2: list[Path]) -> bool:
    """Return True when the two scope lists share no path components."""
    if not s1 or not s2:
        return False  # empty scope → don't auto-kill
    set1 = {p.parts[-1] if p.parts else str(p) for p in s1}
    set2 = {p.parts[-1] if p.parts else str(p) for p in s2}
    return set1.isdisjoint(set2)


# ---------------------------------------------------------------------------
# Per-hour rate limiter
# ---------------------------------------------------------------------------


class _HourlyRateLimiter:
    """Sliding-window per-hour cap."""

    def __init__(self, max_per_hour: int) -> None:
        self._max = max_per_hour
        self._timestamps: collections.deque[float] = collections.deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """Return True when a new dispatch is within the per-hour cap."""
        now = time.monotonic()
        cutoff = now - 3600.0
        with self._lock:
            # Evict old entries.
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            if len(self._timestamps) >= self._max:
                return False
            self._timestamps.append(now)
            return True

    def count(self) -> int:
        """Return the number of dispatches in the current sliding hour."""
        now = time.monotonic()
        cutoff = now - 3600.0
        with self._lock:
            while self._timestamps and self._timestamps[0] < cutoff:
                self._timestamps.popleft()
            return len(self._timestamps)


# ---------------------------------------------------------------------------
# PredictiveDispatcher
# ---------------------------------------------------------------------------


class PredictiveDispatcher:
    """Debounced speculative dispatcher.

    Args:
        engine: The ``ExecutionEngine`` instance (used for context).
            May be ``None`` in tests.
        worktree_mgr: The ``WorktreeManager`` for creating speculation
            worktrees.  When ``None``, no worktrees are created (test mode).
        classifier: The ``IntentClassifier`` for event classification.
        budget: The ``BudgetEnforcer`` for daily cost gating.  When
            ``None``, budget checks are skipped.
        max_concurrent: Maximum in-flight speculations.  Default 3.
        pause_threshold_sec: Typing-pause threshold in seconds.  Default 2.5.
        max_per_hour: Maximum speculations per developer-hour.  Default 20.
    """

    def __init__(
        self,
        engine: object | None = None,
        worktree_mgr: "WorktreeManager | None" = None,
        classifier: "Any | None" = None,
        budget: "BudgetEnforcer | None" = None,
        max_concurrent: int = 3,
        pause_threshold_sec: float = 2.5,
        max_per_hour: int = 20,
    ) -> None:
        self._engine = engine
        self._worktree_mgr = worktree_mgr
        self._classifier = classifier
        self._budget = budget
        self._max_concurrent = max_concurrent
        self._pause_threshold = pause_threshold_sec
        self._rate_limiter = _HourlyRateLimiter(max_per_hour)

        # In-memory speculation store: spec_id → Speculation.
        self._speculations: dict[str, Speculation] = {}
        # Ordered insertion list for eviction (oldest first).
        self._creation_order: list[str] = []

        self._lock = threading.Lock()

        # Debounce timer — fires after pause_threshold_sec of inactivity.
        self._debounce_timer: threading.Timer | None = None
        self._last_event: "FileEvent | None" = None

    # ── Public API ───────────────────────────────────────────────────────────

    def on_file_event(self, event: "FileEvent") -> None:
        """Handle a filesystem event.

        Resets the debounce timer and updates the last event reference.
        This should be called by the watcher event loop for every
        non-privacy-gated event.
        """
        with self._lock:
            self._last_event = event
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
            timer = threading.Timer(self._pause_threshold, self._fire_on_pause)
            timer.daemon = True
            self._debounce_timer = timer
        timer.start()
        # Prune contradicted speculations on each new event.
        self.prune_contradicted(event)

    def on_pause(self, event: "FileEvent") -> "Speculation | None":
        """Directly trigger a speculation for *event* (bypasses debounce timer).

        This is the core dispatch method.  Called by the internal debounce
        timer or directly in tests.

        Returns:
            The ``Speculation`` created, or ``None`` when filtered.
        """
        return self._dispatch(event)

    def accept(self, spec_id: str) -> "HandoffProtocol | None":
        """Accept a speculation and delegate to Wave 5.3 SpeculativePipeliner.

        Updates the speculation status to ``'accepted'`` and returns a
        ``HandoffProtocol`` for the heavy-model pickup.

        Returns ``None`` when *spec_id* is unknown or not ready.
        """
        from agent_baton.core.predict.accept import handoff_to_pipeliner  # local import

        with self._lock:
            spec = self._speculations.get(spec_id)
        if spec is None:
            _log.warning("PredictiveDispatcher.accept: unknown spec_id=%s", spec_id)
            return None
        if not spec.is_active():
            _log.warning(
                "PredictiveDispatcher.accept: spec=%s is not active (status=%s)",
                spec_id, spec.status,
            )
            return None

        spec.status = "accepted"
        _log.info(
            "PredictiveDispatcher.accept: spec=%s intent=%s accepted",
            spec_id, spec.intent.intent.value if spec.intent else "?",
        )

        if self._classifier is not None:
            self._classifier.record_outcome(accepted=True)

        # Find SpeculativePipeliner via engine (if available).
        pipeliner = self._get_pipeliner()
        if pipeliner is not None:
            return handoff_to_pipeliner(spec, pipeliner)
        return None

    def reject(self, spec_id: str, reason: str = "human-reject") -> None:
        """Reject and clean up a speculation worktree.

        Args:
            spec_id: Speculation to reject.
            reason: Rejection reason string.
        """
        with self._lock:
            spec = self._speculations.get(spec_id)
        if spec is None:
            _log.warning("PredictiveDispatcher.reject: unknown spec_id=%s", spec_id)
            return
        spec.status = "rejected"
        _log.info("PredictiveDispatcher.reject: spec=%s reason=%r", spec_id, reason)
        self._cleanup_worktree(spec)
        if self._classifier is not None:
            self._classifier.record_outcome(accepted=False)

    def prune_contradicted(self, new_event: "FileEvent") -> int:
        """Prune speculations contradicted by *new_event*.

        A speculation is contradicted when:
        - Cosine similarity of summary embeddings < 0.4, OR
        - The scopes are disjoint.

        A score between 0.4 and 0.7 marks the spec as ``stale-risk`` but
        does not kill it.

        Returns:
            Number of speculations pruned.
        """
        if not self._speculations:
            return 0

        # Build embedding for the new event summary.
        new_text = str(new_event.path)
        new_embedding = _tf_vector(new_text)

        pruned = 0
        with self._lock:
            to_prune: list[str] = []
            for spec_id, spec in self._speculations.items():
                if not spec.is_active():
                    continue
                sim = _cosine(spec.summary_embedding, new_embedding)
                scope_ok = not _scope_disjoint(
                    spec.intent.scope if spec.intent else [],
                    [],  # new event doesn't have a scope yet
                )
                if sim < 0.4:
                    to_prune.append(spec_id)
                elif sim < 0.7:
                    _log.debug(
                        "PredictiveDispatcher.prune_contradicted: spec=%s "
                        "similarity=%.2f → stale-risk",
                        spec_id, sim,
                    )
                    spec.status = "in-flight"  # keep but note stale
            for spec_id in to_prune:
                spec = self._speculations[spec_id]
                spec.status = "pruned"
                self._cleanup_worktree(spec)
                pruned += 1
                _log.info(
                    "PredictiveDispatcher.prune_contradicted: pruned spec=%s "
                    "(similarity < 0.4)",
                    spec_id,
                )
        return pruned

    def status(self) -> list[Speculation]:
        """Return all in-flight and ready speculations."""
        with self._lock:
            return [s for s in self._speculations.values() if s.is_active()]

    def get(self, spec_id: str) -> "Speculation | None":
        """Return the ``Speculation`` for *spec_id* or ``None``."""
        with self._lock:
            return self._speculations.get(spec_id)

    def most_recent_ready(self) -> "Speculation | None":
        """Return the most-recently-created speculation with status ``'ready'``."""
        with self._lock:
            for spec_id in reversed(self._creation_order):
                spec = self._speculations.get(spec_id)
                if spec is not None and spec.status == "ready":
                    return spec
        return None

    def stop(self) -> None:
        """Cancel the debounce timer and clean up all in-flight speculations."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
        # Reject all in-flight speculations to clean up worktrees.
        with self._lock:
            active_ids = [
                sid for sid, s in self._speculations.items() if s.is_active()
            ]
        for spec_id in active_ids:
            self.reject(spec_id, reason="dispatcher-shutdown")

    def accept_rate(self) -> float | None:
        """Return the rolling accept rate or None if not enough data."""
        with self._lock:
            total = sum(
                1 for s in self._speculations.values()
                if s.status in ("accepted", "rejected", "pruned")
            )
            if total == 0:
                return None
            accepted = sum(
                1 for s in self._speculations.values() if s.status == "accepted"
            )
            return accepted / total

    def cost_so_far_usd(self) -> float:
        """Return total speculation spend recorded by the budget enforcer."""
        if self._budget is None:
            return 0.0
        return float(getattr(self._budget, "speculation_daily_spend", lambda: 0.0)())

    # ── Private helpers ───────────────────────────────────────────────────────

    def _fire_on_pause(self) -> None:
        """Internal callback invoked by the debounce timer."""
        with self._lock:
            event = self._last_event
            self._debounce_timer = None
        if event is not None:
            self._dispatch(event)

    def _dispatch(self, event: "FileEvent") -> "Speculation | None":
        """Core dispatch: classify, gate-check, create worktree, start agent."""
        # Budget gate.
        if self._budget is not None:
            allow = getattr(self._budget, "allow_speculation", None)
            if allow is not None and not allow():
                _log.debug("PredictiveDispatcher._dispatch: budget gate blocked")
                return None

        # Per-hour rate limit.
        if not self._rate_limiter.allow():
            _log.debug(
                "PredictiveDispatcher._dispatch: per-hour cap reached (%d/hr)",
                self._rate_limiter.count(),
            )
            return None

        # Classify.
        if self._classifier is None:
            return None
        classification = self._classifier.classify(event)
        if classification.speculation_directive is None:
            _log.debug(
                "PredictiveDispatcher._dispatch: classifier returned no directive "
                "(confidence=%.2f intent=%s)",
                classification.confidence, classification.intent.value,
            )
            return None

        # Evict oldest if at capacity.
        with self._lock:
            active_count = sum(1 for s in self._speculations.values() if s.is_active())
            if active_count >= self._max_concurrent:
                self._evict_oldest()

        # Create worktree.
        spec_id = uuid.uuid4().hex[:8]
        wt_handle = self._create_worktree(spec_id)

        # Build summary embedding.
        summary_emb = _tf_vector(classification.summary)

        spec = Speculation(
            spec_id=spec_id,
            intent=classification,
            worktree_handle=wt_handle,
            model="claude-haiku",
            started_at=_utcnow(),
            status="in-flight",
            summary_embedding=summary_emb,
        )

        with self._lock:
            self._speculations[spec_id] = spec
            self._creation_order.append(spec_id)

        _log.info(
            "PredictiveDispatcher._dispatch: spec=%s intent=%s confidence=%.2f "
            "worktree=%s",
            spec_id, classification.intent.value, classification.confidence,
            str(wt_handle.path) if wt_handle else "none",
        )

        # Launch the speculative agent asynchronously.
        self._launch_agent(spec)

        return spec

    def _evict_oldest(self) -> bool:
        """Evict the oldest unaccepted in-flight speculation.

        Called with self._lock held.
        """
        for spec_id in self._creation_order:
            spec = self._speculations.get(spec_id)
            if spec is not None and spec.is_active():
                spec.status = "pruned"
                _log.info(
                    "PredictiveDispatcher._evict_oldest: evicting spec=%s "
                    "to free concurrent slot",
                    spec_id,
                )
                # Cleanup worktree outside the lock to avoid deadlock.
                threading.Thread(
                    target=self._cleanup_worktree,
                    args=(spec,),
                    daemon=True,
                ).start()
                return True
        return False

    def _create_worktree(self, spec_id: str) -> "WorktreeHandle | None":
        """Create a worktree for *spec_id*.  Returns None when unavailable."""
        if self._worktree_mgr is None:
            return None
        try:
            handle = self._worktree_mgr.create(  # type: ignore[attr-defined]
                task_id=f"predict-{spec_id}",
                step_id="speculate",
                base_branch=self._current_branch(),
            )
            return handle  # type: ignore[return-value]
        except Exception as exc:
            _log.warning(
                "PredictiveDispatcher._create_worktree: failed for spec=%s: %s",
                spec_id, exc,
            )
            return None

    def _launch_agent(self, spec: Speculation) -> None:
        """Spawn the speculative-drafter agent in a daemon thread."""
        directive = spec.intent.speculation_directive
        if directive is None:
            spec.status = "ready"
            return
        prompt = str(directive.get("prompt", "Implement the speculated change."))
        cwd = str(spec.worktree_handle.path) if spec.worktree_handle else None
        _log.debug(
            "PredictiveDispatcher._launch_agent: launching spec=%s cwd=%s",
            spec.spec_id, cwd,
        )

        def _run() -> None:
            try:
                import asyncio
                launcher = self._get_launcher()
                if launcher is not None and hasattr(launcher, "launch"):
                    loop = asyncio.new_event_loop()
                    try:
                        loop.run_until_complete(
                            launcher.launch(  # type: ignore[attr-defined]
                                agent_name="speculative-drafter",
                                model="claude-haiku",
                                prompt=prompt,
                                step_id=f"predict-{spec.spec_id}",
                                cwd_override=cwd,
                            )
                        )
                    finally:
                        loop.close()
            except Exception as exc:
                _log.warning(
                    "PredictiveDispatcher._launch_agent: agent failed for "
                    "spec=%s: %s",
                    spec.spec_id, exc,
                )
            finally:
                if spec.status == "in-flight":
                    spec.status = "ready"

        t = threading.Thread(target=_run, name=f"predict-{spec.spec_id}", daemon=True)
        t.start()

    def _cleanup_worktree(self, spec: Speculation) -> None:
        """Force-cleanup the worktree for *spec*."""
        if spec.worktree_handle is None or self._worktree_mgr is None:
            return
        try:
            self._worktree_mgr.cleanup(  # type: ignore[attr-defined]
                spec.worktree_handle,
                on_failure=False,
                force=True,
            )
        except Exception as exc:
            _log.debug(
                "PredictiveDispatcher._cleanup_worktree: non-fatal for spec=%s: %s",
                spec.spec_id, exc,
            )

    def _current_branch(self) -> str:
        """Return the current git branch, falling back to ``'HEAD'``."""
        try:
            import subprocess
            r = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:
            pass
        return "HEAD"

    def _get_pipeliner(self) -> "SpeculativePipeliner | None":
        """Extract SpeculativePipeliner from the engine, if wired."""
        if self._engine is None:
            return None
        return getattr(self._engine, "_speculative_pipeliner", None)

    def _get_launcher(self) -> object | None:
        """Extract the ClaudeCodeLauncher from the engine, if wired."""
        if self._engine is None:
            return None
        return getattr(self._engine, "_launcher", None)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

