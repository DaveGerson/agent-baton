"""Wave 6.2 Part C — handoff_to_pipeliner: Wave 6.2 → Wave 5.3 join point (bd-03b0).

When a developer accepts a speculative computation via ``baton predict accept``,
this module bridges the Wave 6.2 predictive dispatcher to the Wave 5.3
``SpeculativePipeliner.handoff()`` protocol.

Design from wave-5-design.md "Composition with Wave 6.2":
    baton predict accept <spec_id>
      ↓
    SpeculativePipeliner.handoff(
        worktree=<spec.worktree>,
        target_model="claude-sonnet",
        directive="finish, add tests, ensure gates pass",
    )
      ↓
    Standard step dispatch into speculation worktree
      ↓
    WorktreeManager.fold_back on success

The Sonnet/Opus pickup is billed against next-step budget, not the
speculation budget.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.predict.speculator import Speculation
    from agent_baton.core.engine.speculator import HandoffProtocol, SpeculativePipeliner

_log = logging.getLogger(__name__)

__all__ = ["handoff_to_pipeliner"]

# Default directive passed to the heavy-model pickup agent.
_DEFAULT_DIRECTIVE = "finish, add tests, ensure gates pass"


def handoff_to_pipeliner(
    spec: "Speculation",
    pipeliner: "SpeculativePipeliner",
    target_model: str = "claude-sonnet",
    directive: str = _DEFAULT_DIRECTIVE,
) -> "HandoffProtocol | None":
    """Bridge a Wave 6.2 ``Speculation`` to Wave 5.3 ``SpeculativePipeliner``.

    This is the join point described in the wave-5-design.md composition
    section.  The Haiku draft in ``spec.worktree_handle`` is treated as the
    scaffold that ``SpeculativePipeliner.build_handoff()`` normally produces
    from the ``speculative-drafter`` agent.

    The Sonnet/Opus pickup is dispatched via the pipeliner's standard path and
    billed against the next-step budget (not the speculation budget).

    Args:
        spec: The accepted ``Speculation`` from the predictive dispatcher.
        pipeliner: The Wave 5.3 ``SpeculativePipeliner`` instance wired into
            the execution engine.
        target_model: Model for the heavy-pickup agent.  Default
            ``"claude-sonnet"``.
        directive: Natural-language directive for the pickup agent.

    Returns:
        A ``HandoffProtocol`` describing the dispatch, or ``None`` when the
        pipeliner cannot construct the handoff (e.g., the worktree has
        uncommitted edits or is missing).
    """
    if spec.worktree_handle is None:
        _log.warning(
            "handoff_to_pipeliner: spec=%s has no worktree_handle — cannot hand off",
            spec.spec_id,
        )
        return None

    worktree_path = str(spec.worktree_handle.path)
    _log.info(
        "handoff_to_pipeliner: spec=%s worktree=%s target_model=%s",
        spec.spec_id, worktree_path, target_model,
    )

    # Synthesise the next-step description from the spec's directive.
    intent = spec.intent
    speculation_directive = intent.speculation_directive if intent else None
    description = directive
    if speculation_directive and isinstance(speculation_directive, dict):
        p = speculation_directive.get("prompt", "")
        if p:
            description = f"{directive}\n\nContext from classifier:\n{p}"

    # Delegate to Wave 5.3 SpeculativePipeliner.build_handoff().
    # We first register the spec's worktree as a SpeculationRecord in the
    # pipeliner, then call build_handoff to get the full handoff prompt.
    #
    # The pipeliner uses build_handoff() which checks for uncommitted edits
    # as a safety guard.
    synthetic_spec_id = spec.spec_id

    # Inject a SpeculationRecord into the pipeliner so build_handoff works.
    try:
        from agent_baton.core.engine.speculator import SpeculationRecord
        pipeliner_record = SpeculationRecord(
            spec_id=synthetic_spec_id,
            target_step_id=f"predict-accept-{spec.spec_id}",
            trigger="predict-accept",
            worktree_path=worktree_path,
            worktree_branch=spec.worktree_handle.branch,
            started_at=spec.started_at,
            status="running",
        )
        # Inject into pipeliner's in-memory index.
        pipeliner._speculations[synthetic_spec_id] = pipeliner_record  # type: ignore[attr-defined]
    except Exception as exc:
        _log.warning(
            "handoff_to_pipeliner: could not inject SpeculationRecord: %s — "
            "attempting direct handoff via worktree path",
            exc,
        )

    # Build the handoff protocol.
    handoff = pipeliner.build_handoff(
        synthetic_spec_id,
        target_agent_name="speculative-drafter",
        target_model=target_model,
        next_step_description=description,
    )

    if handoff is None:
        _log.warning(
            "handoff_to_pipeliner: pipeliner.build_handoff returned None "
            "for spec=%s (worktree may have uncommitted edits)",
            spec.spec_id,
        )
        return None

    _log.info(
        "handoff_to_pipeliner: handoff built for spec=%s → model=%s",
        spec.spec_id, target_model,
    )
    return handoff
