"""Forward relay: select and rank beads to inject into delegation prompts.

Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

``BeadSelector`` picks the most relevant beads for an agent about to receive a
dispatch prompt.  Relevance is determined by three tiers:

1. **Dependency-chain** — beads produced by steps that the current step
   depends on (directly or transitively).  These are the highest-signal beads:
   they represent decisions and discoveries that the current step should
   definitely inherit.

2. **Same-phase** — beads produced by other steps in the same phase.  They
   share architectural context with the current step.

3. **Cross-phase** — beads from other phases.  Lower priority but still
   useful when the budget allows.

Within each tier, beads are ranked by type priority (warnings first, then
discoveries, then decisions) and by quality_score (tiebreaker, higher is better).

The entire selection must fit within *token_budget* and is capped at
*max_beads* regardless.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.engine.bead_store import BeadStore
    from agent_baton.models.bead import Bead
    from agent_baton.models.execution import MachinePlan, PlanStep

_log = logging.getLogger(__name__)

# Token cost when token_estimate is missing (conservative estimate ~50 words).
_FALLBACK_TOKEN_ESTIMATE = 75

# Type priority within a tier — lower value = higher priority.
_TYPE_PRIORITY: dict[str, int] = {
    "warning": 0,
    "discovery": 1,
    "decision": 2,
    "outcome": 3,
    "planning": 4,
}


def _type_priority(bead: "Bead") -> int:
    return _TYPE_PRIORITY.get(bead.bead_type, 99)


def _quality_priority(bead: "Bead") -> float:
    """Return a sort key for quality_score — higher score = lower key value."""
    score = getattr(bead, "quality_score", 0.0)
    return -score  # negate so highest scores sort first


class BeadSelector:
    """Select and rank beads for injection into a delegation prompt.

    Inspired by Steve Yegge's Beads agent memory system (beads-ai/beads-cli).

    The selector is stateless; call :meth:`select` with a live bead store and
    the plan context to get a ranked, budget-trimmed list.
    """

    def select(
        self,
        bead_store: "BeadStore",
        current_step: "PlanStep",
        plan: "MachinePlan",
        token_budget: int = 4096,
        max_beads: int = 5,
    ) -> "list[Bead]":
        """Return the top beads to include in the next delegation prompt.

        Args:
            bead_store: Live :class:`~agent_baton.core.engine.bead_store.BeadStore`.
            current_step: The step about to be dispatched.
            plan: The active :class:`~agent_baton.models.execution.MachinePlan`.
            token_budget: Maximum cumulative token estimate for all selected beads.
            max_beads: Hard cap on the number of beads returned.

        Returns:
            Ordered list of :class:`~agent_baton.models.bead.Bead` objects
            (highest priority first), within budget and cap constraints.
            Returns an empty list when bead_store is unavailable or no beads exist.
        """
        if bead_store is None:
            return []
        try:
            return self._select(bead_store, current_step, plan, token_budget, max_beads)
        except Exception as exc:
            _log.debug("BeadSelector.select failed (non-fatal): %s", exc)
            return []

    def _select(
        self,
        bead_store: "BeadStore",
        current_step: "PlanStep",
        plan: "MachinePlan",
        token_budget: int,
        max_beads: int,
    ) -> "list[Bead]":
        task_id = plan.task_id

        # Gather all open beads for this task.
        all_beads = bead_store.query(task_id=task_id, status="open", limit=500)
        if not all_beads:
            return []

        # Build a lookup: step_id -> bead list.
        by_step: dict[str, list["Bead"]] = {}
        for bead in all_beads:
            by_step.setdefault(bead.step_id, []).append(bead)

        # Compute tier membership for each bead.
        dep_chain_step_ids = self._dependency_chain(current_step, plan)
        same_phase_step_ids = self._same_phase_step_ids(current_step, plan)

        tier_beads: dict[int, list["Bead"]] = {0: [], 1: [], 2: []}
        for bead in all_beads:
            if bead.step_id in dep_chain_step_ids:
                tier_beads[0].append(bead)
            elif bead.step_id in same_phase_step_ids:
                tier_beads[1].append(bead)
            else:
                tier_beads[2].append(bead)

        # Sort within each tier: type priority ASC, quality DESC.
        for tier in tier_beads.values():
            tier.sort(key=lambda b: (_type_priority(b), _quality_priority(b)))

        # Merge tiers in priority order and apply budget/cap constraints.
        selected: list["Bead"] = []
        tokens_used = 0

        for tier_idx in (0, 1, 2):
            for bead in tier_beads[tier_idx]:
                if len(selected) >= max_beads:
                    break
                cost = bead.token_estimate or _FALLBACK_TOKEN_ESTIMATE
                if tokens_used + cost > token_budget:
                    continue
                selected.append(bead)
                tokens_used += cost
            if len(selected) >= max_beads:
                break

        return selected

    @staticmethod
    def _dependency_chain(
        current_step: "PlanStep", plan: "MachinePlan"
    ) -> frozenset[str]:
        """Return step_ids that the current step (directly or transitively) depends on."""
        all_steps = {
            s.step_id: s
            for phase in plan.phases
            for s in phase.steps
        }
        visited: set[str] = set()
        queue = list(current_step.depends_on or [])
        while queue:
            sid = queue.pop()
            if sid in visited:
                continue
            visited.add(sid)
            dep_step = all_steps.get(sid)
            if dep_step and dep_step.depends_on:
                queue.extend(dep_step.depends_on)
        return frozenset(visited)

    @staticmethod
    def _same_phase_step_ids(
        current_step: "PlanStep", plan: "MachinePlan"
    ) -> frozenset[str]:
        """Return step_ids in the same phase as the current step, excluding itself."""
        for phase in plan.phases:
            step_ids = {s.step_id for s in phase.steps}
            if current_step.step_id in step_ids:
                return frozenset(step_ids - {current_step.step_id})
        return frozenset()
