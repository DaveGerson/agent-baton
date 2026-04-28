"""Wave 6.2 Part A — Conflict reconciler for swarm coalesce conflicts (bd-707d).

When a chunk's rebase fails during coalescing, the reconciler:
  1. Collects conflicting hunks + 50 lines of context.
  2. Dispatches a Haiku 'swarm-reconciler' agent with the conflict context.
  3. Verifies the diff passes affected-tests subset.
  4. On failure: reverts the chunk (no escalation to Sonnet) + files a bead.

Design decision (from spec): On reconciler failure → revert chunk only + bead.
DO NOT escalate to Sonnet.  Rest of swarm continues.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.swarm.dispatcher import SwarmDispatcher
    from agent_baton.core.swarm.partitioner import ReconcileResult

_log = logging.getLogger(__name__)

__all__ = ["ConflictReconciler"]


@dataclass
class _ConflictContext:
    """Collected context for a reconcile attempt."""

    chunk_id: str
    intent_a: str
    intent_b: str
    conflict_files: list[Path]
    conflict_diff: str = ""


class ConflictReconciler:
    """Dispatch a Haiku 'swarm-reconciler' agent to resolve rebase conflicts.

    When reconciliation fails (or the reconciler's diff fails affected tests),
    the conflicting chunk is reverted and a BEAD_WARNING is filed.  The rest
    of the swarm is NOT affected.

    Args:
        dispatcher: SwarmDispatcher (for filing beads and Haiku dispatch).
    """

    def __init__(self, dispatcher: SwarmDispatcher) -> None:
        self._dispatcher = dispatcher

    # ── Public API ────────────────────────────────────────────────────────────

    def reconcile(
        self,
        conflicting_chunk_id: str,
        intent_a: str,
        intent_b: str,
        conflict_files: list[Path],
    ) -> ReconcileResult:
        """Attempt to reconcile a swarm conflict.

        Dispatches a Haiku reconciler agent with conflict context.  If the
        reconciler fails or produces a diff that breaks affected tests, the
        chunk is reverted and a bead is filed.

        Args:
            conflicting_chunk_id: chunk_id of the conflicting chunk.
            intent_a: What the first overlapping chunk intended to do.
            intent_b: What the conflicting chunk intended to do.
            conflict_files: Files with merge conflicts.

        Returns:
            :class:`ReconcileResult` with success + resolved diff.
        """
        from agent_baton.core.swarm.partitioner import ReconcileResult

        ctx = _ConflictContext(
            chunk_id=conflicting_chunk_id,
            intent_a=intent_a,
            intent_b=intent_b,
            conflict_files=conflict_files,
        )

        _log.info(
            "ConflictReconciler: attempting reconcile for chunk %s (%d conflict file(s))",
            conflicting_chunk_id[:8],
            len(conflict_files),
        )

        try:
            result = self._dispatch_reconciler_agent(ctx)
        except Exception as exc:
            _log.warning(
                "ConflictReconciler: reconciler agent failed for chunk %s: %s — "
                "reverting chunk and filing bead",
                conflicting_chunk_id[:8], exc,
            )
            self._file_bead_warning(conflicting_chunk_id, str(exc))
            return ReconcileResult(
                success=False,
                resolved_diff="",
                error=f"Reconciler agent raised: {exc}",
            )

        if not result.success:
            _log.warning(
                "ConflictReconciler: reconcile failed for chunk %s — "
                "reverting chunk; rest of swarm continues",
                conflicting_chunk_id[:8],
            )
            self._file_bead_warning(conflicting_chunk_id, result.error)

        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _dispatch_reconciler_agent(
        self, ctx: _ConflictContext
    ) -> ReconcileResult:
        """Build the reconciler prompt and dispatch the Haiku agent.

        v1 stub: returns a failure result (real Haiku dispatch wired in
        Wave 6.2 follow-up when ClaudeCodeLauncher.cwd_override is available).
        """
        from agent_baton.core.swarm.partitioner import ReconcileResult

        file_list = "\n".join(f"  - {f}" for f in ctx.conflict_files)
        _prompt = (
            f"[SWARM CONFLICT RECONCILER] chunk={ctx.chunk_id[:8]}\n\n"
            f"Two chunks of an AST migration conflicted.\n\n"
            f"Chunk A intent: {ctx.intent_a}\n"
            f"Chunk B intent: {ctx.intent_b}\n\n"
            f"Conflict files:\n{file_list}\n\n"
            "Produce a unified diff that satisfies both intents.  "
            "Output ONLY the diff — nothing else.\n"
            "If you cannot reconcile without ambiguity, output:\n"
            "RECONCILE_BLOCKED: <one-line reason>"
        )

        _log.debug(
            "ConflictReconciler: reconciler prompt built for chunk %s "
            "(agent dispatch pending Wave 6.2 follow-up)",
            ctx.chunk_id[:8],
        )

        # v1: return failure so the caller reverts and files a bead.
        # Real Haiku dispatch to be wired once ClaudeCodeLauncher gains
        # cwd_override support.
        return ReconcileResult(
            success=False,
            resolved_diff="",
            error=(
                "swarm-reconciler Haiku dispatch not yet wired (Wave 6.2 follow-up). "
                "Chunk reverted; rest of swarm continues."
            ),
        )

    def _file_bead_warning(self, chunk_id: str, reason: str) -> None:
        """File a BEAD_WARNING for a failed reconcile (best-effort)."""
        content = (
            f"BEAD_WARNING: swarm-conflict chunk={chunk_id[:8]} "
            f"reconciler=failed reason={reason[:200]}"
        )
        _log.warning("ConflictReconciler: %s", content)

        # Best-effort: attempt to file via bead_store if accessible
        try:
            engine = self._dispatcher._engine  # type: ignore[attr-defined]
            if hasattr(engine, "_bead_store") and engine._bead_store is not None:
                from agent_baton.models.bead import Bead, _generate_bead_id
                from agent_baton.core.engine.worktree_manager import _utcnow

                ts = _utcnow()
                bead_id = _generate_bead_id(
                    task_id="swarm",
                    step_id=chunk_id[:8],
                    content=content,
                    ts=ts,
                    existing_count=0,
                )
                bead = Bead(
                    bead_id=bead_id,
                    task_id="swarm",
                    step_id=chunk_id[:8],
                    agent_name="swarm-reconciler",
                    bead_type="warning",
                    content=content,
                    confidence="high",
                    scope="step",
                    created_at=ts,
                    source="agent-signal",
                )
                engine._bead_store.write(bead)
        except Exception as exc:
            _log.debug(
                "ConflictReconciler: bead filing failed (non-fatal): %s", exc
            )
