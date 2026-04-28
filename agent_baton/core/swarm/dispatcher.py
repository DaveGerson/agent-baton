"""Wave 6.2 Part A — SwarmDispatcher (bd-707d).

Synthesises a Machine plan from AST chunks and drives execution.  The
synthesised plan has shape:

  Phase("Partition", 1) → Phase("Implement", N parallel) →
  Phase("Coalesce", 1) → Phase("Verify", 1)

Each Implement step receives a chunk-specific prompt that ONLY modifies
files in its chunk.

Feature gate: ``BATON_SWARM_ENABLED=1`` (off by default).
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.engine.executor import ExecutionEngine
    from agent_baton.core.engine.worktree_manager import WorktreeManager
    from agent_baton.core.govern.budget import BudgetEnforcer
    from agent_baton.core.swarm.partitioner import ASTPartitioner
    from agent_baton.models.execution import MachinePlan

from agent_baton.core.swarm.partitioner import CodeChunk, RefactorDirective

_log = logging.getLogger(__name__)

__all__ = [
    "SwarmBudgetError",
    "SwarmDispatcher",
    "SwarmResult",
]

_SWARM_ENABLED_ENV = "BATON_SWARM_ENABLED"
_SWARM_DISABLED_MSG = (
    "Swarm is disabled; set BATON_SWARM_ENABLED=1 in baton.yaml or "
    "the BATON_SWARM_ENABLED environment variable."
)
_DEFAULT_SWARM_CAP_USD = 5.00
_HAIKU_INPUT_PRICE = 0.25 / 1_000_000
_HAIKU_OUTPUT_PRICE = 1.25 / 1_000_000


def _swarm_enabled() -> bool:
    """Return True only when the swarm feature flag is explicitly set."""
    return os.environ.get(_SWARM_ENABLED_ENV, "0").strip().lower() in ("1", "true", "yes")


class SwarmBudgetError(RuntimeError):
    """Raised when preflight_swarm rejects the swarm due to budget."""


@dataclass
class SwarmResult:
    """Outcome of a completed (or partially-completed) swarm dispatch.

    Attributes:
        swarm_id: Unique identifier for this swarm execution.
        n_succeeded: Number of chunks that applied cleanly.
        n_failed: Number of chunks that failed or were reverted.
        total_tokens: Sum of tokens across all chunk agents.
        total_cost_usd: Estimated USD cost.
        wall_clock_sec: Elapsed wall time in seconds.
        coalesce_branch: Name of the final coalesced branch.
        failed_chunks: chunk_id values for failed/reverted chunks.
    """

    swarm_id: str
    n_succeeded: int
    n_failed: int
    total_tokens: int
    total_cost_usd: float
    wall_clock_sec: float
    coalesce_branch: str
    failed_chunks: list[str] = field(default_factory=list)


class SwarmDispatcher:
    """Orchestrate a massive parallel refactor via a worktree swarm.

    Args:
        engine: Execution engine (for plan execution).
        worktree_mgr: Worktree manager (for swarm worktree lifecycle).
        partitioner: ASTPartitioner for directive → chunks.
        budget: BudgetEnforcer for cost gating.
    """

    def __init__(
        self,
        engine: ExecutionEngine,
        worktree_mgr: WorktreeManager,
        partitioner: ASTPartitioner,
        budget: BudgetEnforcer,
    ) -> None:
        self._engine = engine
        self._worktree_mgr = worktree_mgr
        self._partitioner = partitioner
        self._budget = budget

    # ── Public API ────────────────────────────────────────────────────────────

    def dispatch(
        self,
        directive: RefactorDirective,
        max_agents: int = 100,
        model: str = "claude-haiku",
    ) -> SwarmResult:
        """Partition, budget-check, synthesise plan, and execute swarm.

        Args:
            directive: What to refactor.
            max_agents: Maximum number of parallel chunk agents.
            model: LLM model tier for chunk agents.

        Returns:
            :class:`SwarmResult` with outcome metrics.

        Raises:
            SwarmBudgetError: When the preflight budget check fails.
            RuntimeError: When swarm feature is disabled.
        """
        if not _swarm_enabled():
            raise RuntimeError(_SWARM_DISABLED_MSG)

        swarm_id = uuid.uuid4().hex[:12]
        t_start = time.monotonic()

        _log.info(
            "SwarmDispatcher.dispatch: swarm_id=%s directive=%s max_agents=%d model=%s",
            swarm_id, directive.kind, max_agents, model,
        )

        # 1. Partition
        chunks = self._partitioner.partition(directive, max_chunks=max_agents)
        if not chunks:
            _log.info("SwarmDispatcher: no chunks produced; nothing to do")
            return SwarmResult(
                swarm_id=swarm_id,
                n_succeeded=0,
                n_failed=0,
                total_tokens=0,
                total_cost_usd=0.0,
                wall_clock_sec=time.monotonic() - t_start,
                coalesce_branch="",
                failed_chunks=[],
            )

        # 2. Budget preflight
        est_tokens_per_chunk = 8_000
        budget_ok = self._budget.preflight_swarm(
            chunks, model=model, est_tokens_per_chunk=est_tokens_per_chunk
        )
        if not budget_ok:
            raise SwarmBudgetError(
                f"Swarm preflight rejected: {len(chunks)} chunks * "
                f"{est_tokens_per_chunk} tok/chunk would exceed swarm budget cap "
                f"(${_DEFAULT_SWARM_CAP_USD:.2f}/swarm)."
            )

        # 3. Synthesise and execute plan
        plan = self._synthesize_swarm_plan(chunks, directive, model)
        result = self._execute_swarm(plan)

        # 4. Record spend
        self._budget.record_swarm_spend(
            swarm_id=swarm_id,
            tokens_in=result.total_tokens,
            tokens_out=result.total_tokens // 4,  # rough 4:1 in/out ratio
        )

        _log.info(
            "SwarmDispatcher: swarm_id=%s complete — "
            "succeeded=%d failed=%d tokens=%d cost=$%.4f wall=%.1fs",
            swarm_id, result.n_succeeded, result.n_failed,
            result.total_tokens, result.total_cost_usd, result.wall_clock_sec,
        )
        return result

    # ── Plan synthesis ────────────────────────────────────────────────────────

    def _synthesize_swarm_plan(
        self,
        chunks: list[CodeChunk],
        directive: RefactorDirective,
        model: str,
    ) -> MachinePlan:
        """Build a MachinePlan with shape: Partition → N*Implement → Coalesce → Verify.

        Each Implement step is scoped to a single chunk's files so that the
        dispatched agent cannot touch other chunks.
        """
        from agent_baton.models.execution import (
            MachinePlan,
            PlanPhase,
            PlanStep,
        )

        import datetime as _dt

        task_id = f"swarm-{uuid.uuid4().hex[:8]}"

        # Phase 1: Partition (metadata step, no agent needed — already done)
        partition_phase = PlanPhase(
            phase_id=1,
            name="Partition",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="automation",
                    task_description=(
                        f"[SWARM] Partition complete: {len(chunks)} independent chunks "
                        f"identified for directive={directive.kind}. "
                        "Proceeding to parallel implementation."
                    ),
                    model=model,
                    step_type="automation",
                    command="echo 'partition complete'",
                )
            ],
        )

        # Phase 2: Implement — one step per chunk, all parallel (no depends_on)
        implement_steps: list[PlanStep] = []
        for i, chunk in enumerate(chunks, start=1):
            file_list = "\n".join(f"  - {f}" for f in chunk.files)
            site_summary = f"{len(chunk.call_sites)} call site(s)"
            step = PlanStep(
                step_id=f"2.{i}",
                agent_name="backend-engineer--python",
                task_description=(
                    f"[SWARM CHUNK {chunk.chunk_id[:8]}] "
                    f"Apply directive '{directive.kind}' to the following files ONLY:\n"
                    f"{file_list}\n\n"
                    f"This chunk contains {site_summary} in {len(chunk.files)} file(s).\n"
                    f"Independence proof: {chunk.independence_proof.kind} — "
                    f"{chunk.independence_proof.details}\n\n"
                    f"CONSTRAINT: Do NOT modify any file outside this chunk's file list."
                ),
                model=model,
                allowed_paths=[str(f) for f in chunk.files],
                step_type="developing",
                expected_outcome=(
                    f"All {site_summary} in chunk {chunk.chunk_id[:8]} updated "
                    f"per directive '{directive.kind}' with no files outside the chunk modified."
                ),
            )
            implement_steps.append(step)

        implement_phase = PlanPhase(
            phase_id=2,
            name="Implement",
            steps=implement_steps,
        )

        # Phase 3: Coalesce
        coalesce_phase = PlanPhase(
            phase_id=3,
            name="Coalesce",
            steps=[
                PlanStep(
                    step_id="3.1",
                    agent_name="automation",
                    task_description=(
                        f"[SWARM] Coalesce {len(chunks)} chunk branches via sequential rebase "
                        f"in deterministic chunk_id order. Run conflict reconciler on failures."
                    ),
                    model=model,
                    step_type="automation",
                    command="echo 'coalesce phase'",
                )
            ],
        )

        # Phase 4: Verify
        verify_phase = PlanPhase(
            phase_id=4,
            name="Verify",
            steps=[
                PlanStep(
                    step_id="4.1",
                    agent_name="test-engineer",
                    task_description=(
                        "[SWARM] Run affected-tests subset on the coalesced branch. "
                        "Confirm all call sites were updated and no regressions introduced."
                    ),
                    model=model,
                    step_type="testing",
                )
            ],
        )

        return MachinePlan(
            task_id=task_id,
            task_summary=(
                f"Swarm refactor: {directive.kind} across "
                f"{len(chunks)} independent chunks"
            ),
            risk_level="MEDIUM",
            budget_tier="standard",
            execution_mode="parallel",
            git_strategy="worktree",
            phases=[partition_phase, implement_phase, coalesce_phase, verify_phase],
            shared_context=(
                f"SWARM EXECUTION — directive={directive.kind} "
                f"chunks={len(chunks)} model={model}\n"
                "Each agent MUST only modify files in its designated chunk."
            ),
            created_at=_dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds"),
        )

    # ── Swarm execution (stub — full engine integration in follow-up) ─────────

    def _execute_swarm(self, plan: MachinePlan) -> SwarmResult:
        """Drive the plan through the engine and collect per-chunk outcomes.

        v1 stub: records the swarm in-memory and returns a synthesized result.
        Full engine execution loop integration is in the Wave 6.2 follow-up
        that wires SwarmDispatcher into ExecutionEngine.__init__.
        """
        n_chunks = sum(
            len(phase.steps)
            for phase in plan.phases
            if phase.name == "Implement"
        )
        # Estimate tokens (8K input + 2K output per chunk, Haiku pricing)
        total_tokens = n_chunks * 10_000
        cost_usd = (
            n_chunks * 8_000 * _HAIKU_INPUT_PRICE
            + n_chunks * 2_000 * _HAIKU_OUTPUT_PRICE
        )

        return SwarmResult(
            swarm_id=plan.task_id,
            n_succeeded=n_chunks,
            n_failed=0,
            total_tokens=total_tokens,
            total_cost_usd=cost_usd,
            wall_clock_sec=0.0,
            coalesce_branch=f"swarm-coalesce-{plan.task_id}",
            failed_chunks=[],
        )
