"""Phase/step reference normalization after structural plan changes.

Several pipeline stages append new phases (``EnrichmentStage``'s
``_ensure_review_phase`` / ``_ensure_audit_phase`` / bead-hint
``add_review_phase``) — always at ``max(phase_id) + 1``, which preserves
canonical, gapless, sequential numbering by construction. Exactly one
mutator in the base pipeline does something more disruptive:
``ForesightEngine.analyze`` (``agent_baton.core.engine.foresight``) can
insert a preparatory phase *before* an existing one and renumber every
phase/step that follows it.

That matters because ``phase_builder.enrich_phases`` (step 9b of
``DecompositionStage``, which always runs *before* foresight) bakes the
*current* phase number of the preceding phase into a step's
``task_description`` — the "Build on the &lt;name&gt; output from phase
&lt;N&gt; (&lt;agents&gt;)." sentence. If foresight subsequently inserts a phase
ahead of the one that sentence refers to, the number baked into the text
goes stale: it still says "phase 2" even though the phase it was talking
about is now phase 3.

``normalize_phase_references`` is the single place that repairs this: it
diff-detects which phases/steps actually got renumbered (by comparing a
"before" snapshot — see :func:`snapshot_phase_state` — against the
current, already-canonical ``phase_id``/``step_id`` values) and rewrites
every ``depends_on`` edge and every baked "from phase N" reference through
that mapping. It is intentionally narrow about *which* text it rewrites
(see ``_FROM_PHASE_RE``) — this is our own generated wire-format, not an
attempt to parse or mutate director-authored prose, so a task summary
that happens to mention "phase 3" for its own reasons is never touched.

Usage — bracket any phase-restructuring call with a snapshot taken
immediately before it and a normalize call immediately after (this is
exactly what ``DecompositionStage._apply_foresight`` does around
``ForesightEngine.analyze``; any future restructuring call site — e.g. a
plan-amendment path — should follow the same two-line pattern)::

    pre_phase_ids, pre_step_ids = snapshot_phase_state(plan_phases)
    plan_phases = restructure(plan_phases)
    plan_phases = normalize_phase_references(
        plan_phases, pre_phase_ids=pre_phase_ids, pre_step_ids=pre_step_ids,
    )

Idempotent: calling it twice with no structural change between calls is a
no-op the second time (the snapshot would show no renumbering).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.models.execution import PlanPhase, PlanStep, TeamMember

__all__ = ["snapshot_phase_state", "normalize_phase_references"]

# The exact fragment ``phase_builder.enrich_phases`` bakes into
# ``task_description``: "... output from phase <N> (<agents>)." Only this
# narrow, self-authored pattern is rewritten.
_FROM_PHASE_RE = re.compile(r"(from phase )(\d+)( \()")


def snapshot_phase_state(
    plan_phases: "list[PlanPhase]",
) -> tuple[dict[int, int], dict[int, str]]:
    """Capture object-identity -> current (phase_id, step_id) before a
    restructuring mutation, for later diffing by
    :func:`normalize_phase_references`.

    Object identity (``id(...)``) is the correlation key because a
    restructuring pass like ``ForesightEngine.analyze`` mutates the
    surviving ``PlanPhase``/``PlanStep`` objects *in place* — their old
    ``phase_id``/``step_id`` values are otherwise gone the instant the
    restructuring assigns new ones.
    """
    pre_phase_ids: dict[int, int] = {id(phase): phase.phase_id for phase in plan_phases}
    pre_step_ids: dict[int, str] = {
        id(step): step.step_id for phase in plan_phases for step in phase.steps
    }
    return pre_phase_ids, pre_step_ids


def normalize_phase_references(
    plan_phases: "list[PlanPhase]",
    *,
    pre_phase_ids: dict[int, int] | None = None,
    pre_step_ids: dict[int, str] | None = None,
) -> "list[PlanPhase]":
    """Rewrite stale ``depends_on``/baked-text references after a
    restructuring mutation. Mutates *plan_phases* in place and returns it.

    *pre_phase_ids*/*pre_step_ids* should be the snapshot taken via
    :func:`snapshot_phase_state` immediately before the restructuring call
    that may have changed numbering. Without them (or when nothing
    actually changed), this is a no-op — there is nothing to diff against,
    so it cannot invent a mapping.
    """
    if not plan_phases:
        return plan_phases

    pre_phase_ids = pre_phase_ids or {}
    pre_step_ids = pre_step_ids or {}

    phase_id_map: dict[int, int] = {}
    step_id_map: dict[str, str] = {}

    for phase in plan_phases:
        old_phase_id = pre_phase_ids.get(id(phase))
        if old_phase_id is not None and old_phase_id != phase.phase_id:
            phase_id_map[old_phase_id] = phase.phase_id
        for step in phase.steps:
            old_step_id = pre_step_ids.get(id(step))
            if old_step_id is not None and old_step_id != step.step_id:
                step_id_map[old_step_id] = step.step_id

    if not phase_id_map and not step_id_map:
        return plan_phases

    for phase in plan_phases:
        for step in phase.steps:
            _normalize_step(step, phase_id_map, step_id_map)

    return plan_phases


def _normalize_step(
    step: "PlanStep",
    phase_id_map: dict[int, int],
    step_id_map: dict[str, str],
) -> None:
    if step.depends_on:
        step.depends_on = _remap_deps(step.depends_on, step_id_map)
    if step.task_description and phase_id_map:
        step.task_description = _rewrite_phase_text(step.task_description, phase_id_map)
    for member in getattr(step, "team", None) or []:
        _normalize_team_member(member, phase_id_map, step_id_map)


def _normalize_team_member(
    member: "TeamMember",
    phase_id_map: dict[int, int],
    step_id_map: dict[str, str],
) -> None:
    if getattr(member, "depends_on", None):
        member.depends_on = _remap_deps(member.depends_on, step_id_map)
    desc = getattr(member, "task_description", "")
    if desc and phase_id_map:
        member.task_description = _rewrite_phase_text(desc, phase_id_map)
    for nested in getattr(member, "sub_team", None) or []:
        _normalize_team_member(nested, phase_id_map, step_id_map)


def _remap_deps(deps: list[str], step_id_map: dict[str, str]) -> list[str]:
    out: list[str] = []
    for dep in deps:
        mapped = step_id_map.get(dep, dep)
        if mapped not in out:
            out.append(mapped)
    return out


def _rewrite_phase_text(text: str, phase_id_map: dict[int, int]) -> str:
    def _sub(match: "re.Match[str]") -> str:
        old_id = int(match.group(2))
        new_id = phase_id_map.get(old_id, old_id)
        return f"{match.group(1)}{new_id}{match.group(3)}"

    return _FROM_PHASE_RE.sub(_sub, text)
