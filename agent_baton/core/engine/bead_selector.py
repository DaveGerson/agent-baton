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

Within each tier, beads are ranked by:

1. Scope preference: ``task``-scoped beads rank above ``phase`` and ``step``.
2. Type priority: ``decision`` and ``warning`` rank above ``discovery``,
   ``outcome``, and ``planning``.
3. Content similarity: keyword-overlap score between the bead content+tags
   and the current step description (TF-IDF-style term frequency).
4. Quality score as final tiebreaker (higher is better).

The entire selection must fit within *token_budget* and is capped at
*max_beads* regardless.
"""
from __future__ import annotations

import logging
import math
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.engine.bead_store import BeadStore
    from agent_baton.models.bead import Bead
    from agent_baton.models.execution import MachinePlan, PlanStep

_log = logging.getLogger(__name__)

# Token cost when token_estimate is missing (conservative estimate ~50 words).
_FALLBACK_TOKEN_ESTIMATE = 75

# Type priority within a tier — lower value = higher priority.
# decision and warning are most useful for downstream agents per spec C1.
_TYPE_PRIORITY: dict[str, int] = {
    "warning": 0,
    "decision": 1,
    "discovery": 2,
    "outcome": 3,
    "planning": 4,
}

# Scope preference — lower value = higher priority.
_SCOPE_PRIORITY: dict[str, int] = {
    "task": 0,
    "project": 1,
    "phase": 2,
    "step": 3,
}

# Stop-words to exclude from keyword extraction.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "by", "do",
        "for", "from", "has", "have", "in", "is", "it", "its", "of",
        "on", "or", "that", "the", "this", "to", "was", "will", "with",
    }
)


def _type_priority(bead: "Bead") -> int:
    return _TYPE_PRIORITY.get(bead.bead_type, 99)


def _scope_priority(bead: "Bead") -> int:
    return _SCOPE_PRIORITY.get(getattr(bead, "scope", "step"), 99)


def _quality_priority(bead: "Bead") -> float:
    """Return a sort key for quality_score — higher score = lower key value."""
    score = getattr(bead, "quality_score", 0.0)
    return -score  # negate so highest scores sort first


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase alpha tokens, excluding stop-words."""
    tokens = re.findall(r"[a-z]+", text.lower())
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 1]


def _term_frequencies(tokens: list[str]) -> dict[str, float]:
    """Return normalized term-frequency dict (TF) for a token list."""
    if not tokens:
        return {}
    counts: dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    total = len(tokens)
    return {t: c / total for t, c in counts.items()}


def _content_similarity(query_tf: dict[str, float], bead: "Bead") -> float:
    """Return a cosine-style overlap score between *query_tf* and bead content.

    The bead document is built from its content text plus all its tags
    (tags have double weight so targeted retrieval works).  Both document
    and query are represented as TF vectors; the score is the dot product
    of normalized vectors (cosine similarity approximation without IDF,
    which is expensive to maintain across a variable corpus).

    Returns a value in [0.0, 1.0].  Returns 0.0 when either side is empty.
    """
    if not query_tf:
        return 0.0

    content_text = getattr(bead, "content", "")
    tags: list[str] = getattr(bead, "tags", [])
    # Tags are weighted double — join them twice with content tokens.
    tag_text = " ".join(tags) + " " + " ".join(tags)
    bead_tokens = _tokenize(content_text + " " + tag_text)
    bead_tf = _term_frequencies(bead_tokens)

    if not bead_tf:
        return 0.0

    # Dot product of the two TF vectors.
    dot = sum(query_tf.get(t, 0.0) * bead_tf.get(t, 0.0) for t in query_tf)

    # Normalize by the product of L2 norms.
    query_norm = math.sqrt(sum(v * v for v in query_tf.values()))
    bead_norm = math.sqrt(sum(v * v for v in bead_tf.values()))
    if query_norm == 0 or bead_norm == 0:
        return 0.0
    return dot / (query_norm * bead_norm)


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
        max_beads: int = 10,
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

        # Gather all open beads for this task — pull from ALL completed phases.
        all_beads = bead_store.query(task_id=task_id, status="open", limit=500)
        if not all_beads:
            return []

        # Build query TF vector from the current step description + agent name.
        step_text = " ".join(
            filter(None, [
                getattr(current_step, "description", ""),
                getattr(current_step, "agent", ""),
                getattr(current_step, "agent_name", ""),
            ])
        )
        query_tokens = _tokenize(step_text)
        query_tf = _term_frequencies(query_tokens)

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

        # Sort within each tier using a composite key:
        #   1. scope priority  (task > project > phase > step)
        #   2. type priority   (decision/warning > discovery > outcome > planning)
        #   3. content similarity DESC (negated so higher similarity sorts first)
        #   4. quality score DESC
        for tier in tier_beads.values():
            tier.sort(
                key=lambda b: (
                    _scope_priority(b),
                    _type_priority(b),
                    -_content_similarity(query_tf, b),
                    _quality_priority(b),
                )
            )

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

    def select_for_team_member(
        self,
        bead_store: "BeadStore",
        current_step: "PlanStep",
        plan: "MachinePlan",
        *,
        team_id: str,
        member_id: str,
        token_budget: int = 4096,
        max_beads: int = 10,
    ) -> "list[Bead]":
        """Team-aware extension of :meth:`select`.

        Returns the union of:

        1. The standard :meth:`select` result (dependency-chain, same-phase,
           and cross-phase agent-signal beads).
        2. Open ``task`` beads scoped to *team_id* that are unclaimed or
           claimed by *member_id*.
        3. Unread ``message`` beads addressed to *member_id* or broadcast
           to *team_id* (not yet acked by *member_id*).

        The team-board beads are not budget-constrained by the same logic
        as the agent-signal beads — they always fit (they are bounded by
        how many messages and tasks the team has produced), but the
        *max_beads* cap still applies to the combined result.

        The existing :meth:`select` signature and call sites are
        unchanged.
        """
        try:
            base = self.select(
                bead_store, current_step, plan,
                token_budget=token_budget, max_beads=max_beads,
            )
        except Exception as exc:
            _log.debug("select_for_team_member: base select failed: %s", exc)
            base = []

        # Layer team-board beads on top of the base selection.
        try:
            from agent_baton.core.engine.team_board import TeamBoard
            board = TeamBoard(bead_store)
            messages = board.unread_messages_for_member(
                task_id=plan.task_id,
                team_id=team_id,
                member_id=member_id,
            )
            tasks = board.open_tasks_for_team(
                task_id=plan.task_id,
                team_id=team_id,
                member_id=member_id,
            )
        except Exception as exc:
            _log.debug(
                "select_for_team_member: team-board enrichment failed: %s",
                exc,
            )
            return base

        # Base select() pulls every open bead including team-board types;
        # strip those so the team-board filter (ack + claim) is the only
        # source of truth for message/task visibility.
        filtered_base = [
            b for b in base
            if b.bead_type not in ("message", "task", "message_ack")
        ]

        # Prepend team-board beads — they are the most recent/relevant
        # context for this specific member.  Base entries follow.
        seen: set[str] = set()
        result: list = []
        for bead in list(messages) + list(tasks) + list(filtered_base):
            if bead.bead_id in seen:
                continue
            seen.add(bead.bead_id)
            result.append(bead)
            if len(result) >= max_beads:
                break
        return result

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
