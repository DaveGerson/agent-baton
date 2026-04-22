"""Agent-facing team tools — the callable surface behind ``team_*`` tools.

These functions back five agent-visible tools (see
``references/team-messaging.md``):

- ``team_send_message``   — send a message to a team or specific member
- ``team_add_task``       — add a task to the caller's team board
- ``team_claim_task``     — claim an existing task
- ``team_complete_task``  — mark a claimed task done
- ``team_dispatch``       — LEAD-ONLY: carve out a sub-team on the fly

Each function validates against :class:`TeamRegistry` so that callers with
an invalid ``team_id``/``member_id`` get a clear error instead of a silent
misaddressing.  ``team_dispatch`` additionally enforces
``role == "lead"`` — non-lead members invoking it receive a ``ValueError``
with an explicit message.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.engine.executor import ExecutionEngine
    from agent_baton.core.engine.team_registry import TeamRegistry

_log = logging.getLogger(__name__)


class TeamToolError(Exception):
    """Raised when a team tool is called with invalid arguments."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_registry(engine: "ExecutionEngine") -> "TeamRegistry":
    reg = getattr(engine, "_team_registry", None)
    if reg is None:
        raise TeamToolError(
            "TeamRegistry is unavailable — team tools require a SQLite "
            "storage backend (schema v15)."
        )
    return reg


def _require_team(
    registry: "TeamRegistry", task_id: str, team_id: str,
) -> None:
    team = registry.get_team(task_id, team_id)
    if team is None:
        raise TeamToolError(
            f"Team {team_id!r} not found for task {task_id!r}."
        )


def _require_member(
    engine: "ExecutionEngine", task_id: str, member_id: str,
) -> None:
    """Raise TeamToolError when member_id is not referenced by any plan step.

    Accepts any member_id that appears (nested or flat) in any team step of
    the active plan.  This is deliberately permissive: the purpose is to
    catch obvious typos, not to enforce that the member is currently
    running.
    """
    state = engine._load_execution()  # type: ignore[attr-defined]
    if state is None:
        raise TeamToolError(f"No active execution for task {task_id!r}.")
    for phase in state.plan.phases:
        for step in phase.steps:
            if not step.team:
                continue
            for m in engine._flatten_team_members(step.team):  # type: ignore[attr-defined]
                if m.member_id == member_id:
                    return
    raise TeamToolError(
        f"Member {member_id!r} not found in the plan for task {task_id!r}."
    )


def _member_role(
    engine: "ExecutionEngine", task_id: str, member_id: str,
) -> str:
    """Return the role ('lead' | 'implementer' | 'reviewer' | '') of a member."""
    state = engine._load_execution()  # type: ignore[attr-defined]
    if state is None:
        return ""
    for phase in state.plan.phases:
        for step in phase.steps:
            if not step.team:
                continue
            for m in engine._flatten_team_members(step.team):  # type: ignore[attr-defined]
                if m.member_id == member_id:
                    return m.role
    return ""


# ---------------------------------------------------------------------------
# Messaging + task tools — open to every team member
# ---------------------------------------------------------------------------


def team_send_message(
    engine: "ExecutionEngine",
    *,
    task_id: str,
    from_team: str,
    from_member: str,
    to_team: str,
    to_member: str | None,
    subject: str,
    body: str,
) -> str:
    """Write a message bead addressed to a team or member.

    Returns the new bead_id.  Raises :class:`TeamToolError` if the
    sending team or recipient team is missing, or if the caller is not a
    registered member.
    """
    reg = _require_registry(engine)
    _require_team(reg, task_id, from_team)
    _require_team(reg, task_id, to_team)
    _require_member(engine, task_id, from_member)
    if to_member is not None:
        _require_member(engine, task_id, to_member)

    from agent_baton.core.engine.team_board import TeamBoard
    board = TeamBoard(engine._bead_store)  # type: ignore[attr-defined]
    return board.send_message(
        task_id=task_id,
        from_team=from_team, from_member=from_member,
        to_team=to_team, to_member=to_member,
        subject=subject, body=body,
    )


def team_add_task(
    engine: "ExecutionEngine",
    *,
    task_id: str,
    team_id: str,
    author_member_id: str,
    title: str,
    detail: str = "",
    parent_task_bead_id: str | None = None,
) -> str:
    """Append a task bead to the team's board.  Returns the new bead_id."""
    reg = _require_registry(engine)
    _require_team(reg, task_id, team_id)
    _require_member(engine, task_id, author_member_id)

    from agent_baton.core.engine.team_board import TeamBoard
    board = TeamBoard(engine._bead_store)  # type: ignore[attr-defined]
    return board.append_task(
        task_id=task_id, team_id=team_id,
        author_member_id=author_member_id,
        title=title, detail=detail,
        parent_task_bead_id=parent_task_bead_id,
    )


def team_claim_task(
    engine: "ExecutionEngine",
    *,
    task_id: str,
    task_bead_id: str,
    member_id: str,
) -> None:
    """Claim an open task for *member_id*."""
    _require_registry(engine)
    _require_member(engine, task_id, member_id)

    from agent_baton.core.engine.team_board import TeamBoard
    board = TeamBoard(engine._bead_store)  # type: ignore[attr-defined]
    board.claim_task(
        task_id=task_id, task_bead_id=task_bead_id, member_id=member_id,
    )


def team_complete_task(
    engine: "ExecutionEngine",
    *,
    task_id: str,
    task_bead_id: str,
    outcome: str,
) -> None:
    """Mark a task bead as done with *outcome* as its summary."""
    _require_registry(engine)

    from agent_baton.core.engine.team_board import TeamBoard
    board = TeamBoard(engine._bead_store)  # type: ignore[attr-defined]
    board.complete_task(
        task_id=task_id, task_bead_id=task_bead_id, outcome=outcome,
    )


# ---------------------------------------------------------------------------
# team_dispatch — LEAD-ONLY: register a sub-team on the fly
# ---------------------------------------------------------------------------


def team_dispatch(
    engine: "ExecutionEngine",
    *,
    task_id: str,
    parent_team_id: str,
    caller_member_id: str,
    members: "list[dict]",
    synthesis: "dict | None" = None,
) -> str:
    """Register a new sub-team under *caller_member_id*.

    Only invokable by members whose ``role == "lead"``.  Non-lead callers
    receive a :class:`TeamToolError` with a clear message.

    *members* is a list of dicts with keys matching ``TeamMember.from_dict``
    (at minimum ``member_id`` and ``agent_name``).  The caller's
    :class:`TeamMember` gains the new sub-team, the registry records a
    child team, and the engine's state is saved so the next call to
    ``next_actions()`` rebuilds with the new dispatchable members.

    Returns the new child ``team_id``.
    """
    reg = _require_registry(engine)
    _require_team(reg, task_id, parent_team_id)
    _require_member(engine, task_id, caller_member_id)

    caller_role = _member_role(engine, task_id, caller_member_id)
    if caller_role != "lead":
        raise TeamToolError(
            f"team_dispatch is available only to role='lead' members; "
            f"caller {caller_member_id!r} has role={caller_role!r}."
        )

    # Attach the sub_team to the lead's TeamMember.
    from agent_baton.models.execution import SynthesisSpec, TeamMember

    state = engine._load_execution()  # type: ignore[attr-defined]
    if state is None:
        raise TeamToolError(f"No active execution for task {task_id!r}.")

    caller_member = None
    parent_step = None
    for phase in state.plan.phases:
        for step in phase.steps:
            if not step.team:
                continue
            for m in engine._flatten_team_members(step.team):  # type: ignore[attr-defined]
                if m.member_id == caller_member_id:
                    caller_member = m
                    parent_step = step
                    break
            if caller_member is not None:
                break
        if caller_member is not None:
            break

    if caller_member is None or parent_step is None:
        raise TeamToolError(
            f"Unable to locate {caller_member_id!r} in plan for task {task_id!r}."
        )

    # Compose the new sub-team member list, generating member_ids under the
    # caller's own id when the input dict omits them.
    new_members: list[TeamMember] = []
    for idx, spec in enumerate(members):
        member_id = spec.get("member_id") or f"{caller_member_id}.{chr(97 + idx)}"
        new_members.append(TeamMember(
            member_id=member_id,
            agent_name=spec["agent_name"],
            role=spec.get("role", "implementer"),
            task_description=spec.get("task_description", ""),
            model=spec.get("model", "sonnet"),
            depends_on=list(spec.get("depends_on", [])),
            deliverables=list(spec.get("deliverables", [])),
        ))
    caller_member.sub_team.extend(new_members)

    if synthesis is not None:
        caller_member.synthesis = SynthesisSpec.from_dict(synthesis)
    elif caller_member.synthesis is None:
        caller_member.synthesis = SynthesisSpec()

    # Register the child team.
    child_team_id = f"{parent_step.step_id}::{caller_member_id}"
    reg.create_team(
        task_id=task_id,
        team_id=child_team_id,
        step_id=caller_member_id,
        leader_agent=caller_member.agent_name,
        leader_member_id=caller_member_id,
        parent_team_id=parent_team_id,
    )

    # Persist state so the next next_actions() call rebuilds the dispatch
    # wave with the new sub-team.
    engine._save_execution(state)  # type: ignore[attr-defined]
    return child_team_id
