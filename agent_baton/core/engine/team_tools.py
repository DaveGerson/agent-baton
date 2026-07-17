"""Agent-facing team tools — the callable surface behind ``team_*`` tools.

Two generations of the same surface live here:

- **Legacy functions** — ``team_send_message``, ``team_add_task``,
  ``team_claim_task``, ``team_complete_task`` (see
  ``references/team-messaging.md``). Kept byte-for-byte behavior
  compatible; existing callers/tests are unaffected.
- **Canonical runtime-contract tools** — ``team_list``, ``team_claim``,
  ``team_update``, ``team_send``, ``team_read`` (plus lead-only
  ``team_dispatch``, shared across both generations). This is the
  five-tool surface named in
  ``docs/internal/team-runtime-contract.md``, the design doc that
  specifies the exposure mechanism (a structured Baton CLI invoked via
  the ``Bash`` tool — see the doc for why MCP was not chosen),
  authorization matrix, optimistic concurrency, idempotency, and
  failure-mode contract these functions implement. The canonical tools
  are a thin, additionally-authorized layer over the same
  :class:`TeamRegistry` / :class:`TeamBoard` stack; ``team_update``
  consolidates create (``team_add_task``) and complete
  (``team_complete_task``) into one create-or-transition call, and
  ``team_list``/``team_read`` are new (pull-based board/mailbox reads
  that did not previously exist as callable tools).

Each function validates against :class:`TeamRegistry` so that callers with
an invalid ``team_id``/``member_id`` get a clear error instead of a silent
misaddressing.  ``team_dispatch`` additionally enforces
``role == "lead"`` — non-lead members invoking it receive a
:class:`TeamToolError` with an explicit message. The canonical tools also
enforce the role -> tool authorization matrix (see
:func:`authorized_team_tools`) via :func:`authorize_team_tool`.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_baton.core.engine.executor import ExecutionEngine
    from agent_baton.core.engine.team_registry import TeamRegistry
    from agent_baton.models.bead import Bead

_log = logging.getLogger(__name__)


class TeamToolError(Exception):
    """Raised when a team tool is called with invalid arguments."""


class TeamAuthorizationError(TeamToolError):
    """Raised when a member's role is not authorized for the requested tool.

    Subclasses :class:`TeamToolError` so existing ``pytest.raises
    (TeamToolError, ...)`` assertions keep matching; callers that need to
    distinguish "bad input" from "not allowed" can catch this specifically.
    """


class TeamConcurrencyError(TeamToolError):
    """Raised when an optimistic-concurrency check fails.

    Wraps :class:`~agent_baton.core.engine.team_board.TeamBoardConflictError`
    at the tool boundary so callers of the canonical tools only need to
    catch :class:`TeamToolError` (or this subclass specifically) — the
    board-level exception type stays an internal implementation detail.
    """


class TeamBackendUnavailableError(TeamToolError):
    """Raised when the team backend itself is not usable — the
    :class:`TeamRegistry` never initialized (no SQLite storage / schema
    predates v15) or the bead store failed to construct (``bd`` binary
    missing).

    Distinct type (not just a message) so the CLI's exit-code mapping
    (docs/internal/team-runtime-contract.md §7.3, exit ``5``) can branch on
    the exception class rather than sniffing message text — message
    sniffing misclassified plain usage errors whose user-supplied
    ``team_id``/``member_id`` happened to contain the word "unavailable".
    Subclasses :class:`TeamToolError` so base-class handlers keep working.
    """


# ---------------------------------------------------------------------------
# Canonical tool names + role-based authorization matrix
# ---------------------------------------------------------------------------

#: The exact tool surface advertised by the team-runtime contract. A
#: dispatch prompt / CLI --help / MCP tool list must never advertise a name
#: outside this set, and must never advertise a name the calling member's
#: role is not authorized for (see :func:`authorized_team_tools`) — this is
#: the "advertised tools exactly match capabilities" invariant from
#: docs/internal/team-runtime-contract.md.
TEAM_TOOL_NAMES: frozenset[str] = frozenset({
    "team_list", "team_claim", "team_update", "team_send", "team_read",
    "team_dispatch",
})

# Role -> authorized tool names. Every known role gets the full board/
# mailbox surface (team_list/team_claim/team_update/team_send/team_read);
# only "lead" additionally gets team_dispatch. An unrecognized/custom role
# string falls back to the same permissive default as "implementer" —
# board/mailbox tools are intentionally open to any registered team member
# so a typo'd role never silently locks a member out of coordination; only
# the privileged team_dispatch tool is fail-closed by role.
_BOARD_AND_MAILBOX_TOOLS: frozenset[str] = frozenset({
    "team_list", "team_claim", "team_update", "team_send", "team_read",
})
_ROLE_TOOL_AUTHORIZATION: dict[str, frozenset[str]] = {
    "lead": _BOARD_AND_MAILBOX_TOOLS | frozenset({"team_dispatch"}),
    "implementer": _BOARD_AND_MAILBOX_TOOLS,
    "reviewer": _BOARD_AND_MAILBOX_TOOLS,
}


def authorized_team_tools(role: str) -> frozenset[str]:
    """Return the tool names *role* is authorized to call.

    Unknown/custom role strings get the same board/mailbox default as
    ``"implementer"`` — see the module-level note on
    ``_ROLE_TOOL_AUTHORIZATION`` for the fail-open rationale (only
    ``team_dispatch`` is role-gated).
    """
    return _ROLE_TOOL_AUTHORIZATION.get(role, _BOARD_AND_MAILBOX_TOOLS)


def advertised_team_tools_for_role(role: str) -> list[str]:
    """Return the sorted tool-name list a dispatch prompt should advertise
    for a member with *role*.

    This is the single source of truth future prompt/CLI-help/MCP-tool-list
    building code should call so the advertised surface can never drift
    from :data:`TEAM_TOOL_NAMES` / the authorization matrix — see
    docs/internal/team-runtime-contract.md §Advertised-tools invariant.
    """
    return sorted(authorized_team_tools(role))


def authorize_team_tool(
    engine: "ExecutionEngine",
    *,
    task_id: str,
    member_id: str,
    tool_name: str,
) -> None:
    """Raise :class:`TeamAuthorizationError` if *member_id* may not call
    *tool_name* given their current role.

    Callers must invoke :func:`_require_member` (or equivalent) first so an
    unregistered ``member_id`` fails with the clearer "member not found"
    error rather than being silently authorized under the permissive
    default role.
    """
    role = _member_role(engine, task_id, member_id)
    if tool_name not in authorized_team_tools(role):
        raise TeamAuthorizationError(
            f"Tool {tool_name!r} is not authorized for role={role!r} "
            f"(member {member_id!r})."
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_registry(engine: "ExecutionEngine") -> "TeamRegistry":
    reg = getattr(engine, "_team_registry", None)
    if reg is None:
        raise TeamBackendUnavailableError(
            "TeamRegistry is unavailable — team tools require a SQLite "
            "storage backend (schema v15)."
        )
    return reg


def _require_bead_store(engine: "ExecutionEngine"):
    """Return ``engine._bead_store``, raising a clean :class:`TeamToolError`
    when it is unavailable.

    Without this guard, an engine whose bead store failed to construct
    (e.g. the ``bd`` binary is missing — see ``ExecutionEngine.__init__``'s
    best-effort ``try/except`` around ``make_bead_store``) leaves
    ``engine._bead_store`` as ``None``.  Every canonical/legacy tool that
    talks to :class:`~agent_baton.core.engine.team_board.TeamBoard`
    constructs it as ``TeamBoard(engine._bead_store)`` — passing ``None``
    would raise an opaque ``AttributeError`` deep inside ``TeamBoard``
    instead of the documented, typed failure. This is exactly the
    "Underlying store unavailable" row of
    docs/internal/team-runtime-contract.md §7.3 (mapped to CLI exit code
    5, distinct from a plain usage error) — the CLI branches on the
    :class:`TeamBackendUnavailableError` type raised below.
    """
    store = getattr(engine, "_bead_store", None)
    if store is None:
        raise TeamBackendUnavailableError(
            "Team board bead store is unavailable — team tools require a "
            "configured bead backend (the 'bd' binary; see "
            "BATON_BD_BACKEND/BATON_BD_BIN)."
        )
    return store


def _audit(
    tool_name: str,
    *,
    task_id: str,
    member_id: str,
    outcome: str,
    detail: str = "",
) -> None:
    """Emit a structured, always-on audit log line for a canonical tool call.

    Independent of whether the call resulted in a bead write — an
    authorization failure never reaches a bead write but should still be
    observable (docs/internal/team-runtime-contract.md §7.1). This
    strengthens, but does not replace, the append-only bead trail every
    successful write already produces.
    """
    if detail:
        _log.info(
            "team_tool tool=%s task_id=%s member_id=%s outcome=%s detail=%s",
            tool_name, task_id, member_id, outcome, detail,
        )
    else:
        _log.info(
            "team_tool tool=%s task_id=%s member_id=%s outcome=%s",
            tool_name, task_id, member_id, outcome,
        )


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
    board = TeamBoard(_require_bead_store(engine))
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
    board = TeamBoard(_require_bead_store(engine))
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
    board = TeamBoard(_require_bead_store(engine))
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
    board = TeamBoard(_require_bead_store(engine))
    board.complete_task(
        task_id=task_id, task_bead_id=task_bead_id, outcome=outcome,
    )


# ---------------------------------------------------------------------------
# Payload shaping — canonical tools return plain dicts (JSON-safe: this is
# the shape a CLI --json flag or MCP tool-result would serialize).
# ---------------------------------------------------------------------------


def _task_status_label(bead: "Bead") -> str:
    if bead.status == "closed":
        return "done"
    if any(tag.startswith("claimed_by=") for tag in bead.tags):
        return "claimed"
    return "open"


def _tag_value(bead: "Bead", prefix: str) -> str:
    for tag in bead.tags:
        if tag.startswith(prefix):
            return tag.split("=", 1)[1]
    return ""


def _task_bead_to_dict(bead: "Bead") -> dict:
    title, _, detail = bead.content.partition("\n\n")
    return {
        "task_bead_id": bead.bead_id,
        "team_id": _tag_value(bead, "team="),
        "author_member_id": _tag_value(bead, "from_member="),
        "title": title,
        "detail": detail,
        "status": _task_status_label(bead),
        "claimed_by": _tag_value(bead, "claimed_by=") or None,
        "created_at": bead.created_at,
    }


def _message_bead_to_dict(bead: "Bead") -> dict:
    subject, _, body = bead.content.partition("\n\n")
    return {
        "message_bead_id": bead.bead_id,
        "from_team": _tag_value(bead, "from_team="),
        "from_member": _tag_value(bead, "from_member="),
        "to_team": _tag_value(bead, "to_team="),
        "to_member": _tag_value(bead, "to_member=") or None,
        "subject": subject,
        "body": body or subject,
        "created_at": bead.created_at,
    }


# ---------------------------------------------------------------------------
# Canonical runtime-contract tools: team_list, team_claim, team_update,
# team_send, team_read — see docs/internal/team-runtime-contract.md.
# ---------------------------------------------------------------------------


def team_list(
    engine: "ExecutionEngine",
    *,
    task_id: str,
    team_id: str,
    member_id: str | None = None,
    resource: str = "tasks",
    status: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """List board resources scoped to *team_id*.

    Args:
        resource: ``"tasks"`` (default) lists the shared task board —
            when *member_id* is given, unclaimed tasks plus tasks claimed
            by *member_id* (peers' claimed tasks are hidden, matching
            :meth:`TeamBoard.open_tasks_for_team`); *status* further
            filters to ``"open"``, ``"claimed"``, or ``"done"``.
            ``"teams"`` lists the child sub-teams registered under
            *team_id* (from :class:`TeamRegistry`); *status*/*member_id*
            are ignored in this mode.
        limit: Maximum rows returned.

    Returns:
        A list of JSON-safe dicts — task shape from :func:`_task_bead_to_dict`,
        team shape from :meth:`Team.to_dict`.

    Raises:
        TeamToolError: unknown *team_id*, unknown *member_id* (when
            given), or an unsupported *resource*/*status* value.
        TeamAuthorizationError: *member_id* given and its role is not
            authorized for ``team_list``.
    """
    try:
        reg = _require_registry(engine)
        _require_team(reg, task_id, team_id)
        if member_id is not None:
            _require_member(engine, task_id, member_id)
            authorize_team_tool(
                engine, task_id=task_id, member_id=member_id, tool_name="team_list",
            )

        if resource == "teams":
            result = [t.to_dict() for t in reg.child_teams(task_id, team_id)]
            _audit("team_list", task_id=task_id, member_id=member_id or "",
                   outcome="success", detail=f"resource=teams count={len(result)}")
            return result
        if resource != "tasks":
            raise TeamToolError(
                f"team_list: unsupported resource={resource!r}; "
                "expected 'tasks' or 'teams'."
            )
        if status not in (None, "open", "claimed", "done"):
            raise TeamToolError(
                f"team_list: unsupported status={status!r}; "
                "expected 'open', 'claimed', 'done', or None."
            )

        from agent_baton.core.engine.team_board import TeamBoard
        board = TeamBoard(_require_bead_store(engine))

        if status == "done":
            tasks = board.done_tasks_for_team(task_id=task_id, team_id=team_id, limit=limit)
        else:
            tasks = board.open_tasks_for_team(
                task_id=task_id, team_id=team_id, member_id=member_id, limit=limit,
            )
            if status == "open":
                tasks = [t for t in tasks if not any(
                    tag.startswith("claimed_by=") for tag in t.tags
                )]
            elif status == "claimed":
                tasks = [t for t in tasks if any(
                    tag.startswith("claimed_by=") for tag in t.tags
                )]
    except TeamToolError as exc:
        _audit("team_list", task_id=task_id, member_id=member_id or "",
               outcome="failed", detail=str(exc))
        raise
    result = [_task_bead_to_dict(t) for t in tasks]
    _audit("team_list", task_id=task_id, member_id=member_id or "",
           outcome="success", detail=f"resource=tasks count={len(result)}")
    return result


def team_claim(
    engine: "ExecutionEngine",
    *,
    task_id: str,
    team_id: str,
    task_bead_id: str,
    member_id: str,
    allow_reassign: bool = False,
) -> dict:
    """Claim an open task with optimistic concurrency (default: enforced).

    Unlike the legacy :func:`team_claim_task` (last-writer-wins), this
    raises :class:`TeamConcurrencyError` if another member already holds
    the claim — pass ``allow_reassign=True`` to force a reassignment
    (e.g. a lead taking over a stalled task).  Re-claiming your own
    existing claim is always a no-op success (idempotent retry after a
    timed-out response).

    Raises:
        TeamToolError: unknown *team_id*/*member_id*, or *task_bead_id*
            does not exist / is not a task bead.
        TeamAuthorizationError: role not authorized for ``team_claim``.
        TeamConcurrencyError: task already claimed by someone else and
            ``allow_reassign=False``.
    """
    try:
        reg = _require_registry(engine)
        _require_team(reg, task_id, team_id)
        _require_member(engine, task_id, member_id)
        authorize_team_tool(
            engine, task_id=task_id, member_id=member_id, tool_name="team_claim",
        )

        from agent_baton.core.engine.team_board import TeamBoard, TeamBoardConflictError
        board = TeamBoard(_require_bead_store(engine))
        try:
            board.claim_task(
                task_id=task_id, task_bead_id=task_bead_id, member_id=member_id,
                expected_status=None if allow_reassign else "open",
            )
        except TeamBoardConflictError as exc:
            raise TeamConcurrencyError(str(exc)) from exc
    except TeamToolError as exc:
        _audit("team_claim", task_id=task_id, member_id=member_id,
               outcome="failed", detail=str(exc))
        raise
    _audit("team_claim", task_id=task_id, member_id=member_id,
           outcome="success", detail=f"task_bead_id={task_bead_id}")
    return {"task_bead_id": task_bead_id, "claimed_by": member_id}


def team_update(
    engine: "ExecutionEngine",
    *,
    task_id: str,
    team_id: str,
    member_id: str,
    task_bead_id: str | None = None,
    title: str | None = None,
    detail: str = "",
    status: str | None = None,
    outcome: str = "",
    idempotency_key: str | None = None,
    parent_task_bead_id: str | None = None,
) -> dict:
    """Create or transition a task bead — consolidates
    :func:`team_add_task` + :func:`team_complete_task` into one
    create-or-update call.

    Two modes, selected by whether *task_bead_id* is given:

    - **Create** (``task_bead_id is None``): requires *title*. When
      *idempotency_key* is supplied, a retried call with the same key
      (scoped to *team_id*) returns the ORIGINAL bead_id instead of
      writing a duplicate task — see
      :meth:`TeamBoard.append_task`.
    - **Complete** (*task_bead_id* given, ``status="complete"``):
      requires *outcome*; closes the task.  Only this transition is
      supported in this contract version — any other *status* value
      raises :class:`TeamToolError` (see
      docs/internal/team-runtime-contract.md for the rationale: task
      "reopen"/"block" transitions are deferred to a follow-up step).

    Raises:
        TeamToolError: unknown *team_id*/*member_id*, missing *title* in
            create mode, or an unsupported transition in update mode.
        TeamAuthorizationError: role not authorized for ``team_update``.
    """
    try:
        reg = _require_registry(engine)
        _require_team(reg, task_id, team_id)
        _require_member(engine, task_id, member_id)
        authorize_team_tool(
            engine, task_id=task_id, member_id=member_id, tool_name="team_update",
        )

        from agent_baton.core.engine.team_board import TeamBoard
        board = TeamBoard(_require_bead_store(engine))

        if task_bead_id is None:
            if not title:
                raise TeamToolError(
                    "team_update: 'title' is required to create a task "
                    "(task_bead_id is None)."
                )
            new_id = board.append_task(
                task_id=task_id, team_id=team_id, author_member_id=member_id,
                title=title, detail=detail,
                parent_task_bead_id=parent_task_bead_id,
                idempotency_key=idempotency_key,
            )
            result = {"task_bead_id": new_id, "status": "open"}
        elif status == "complete":
            if not outcome:
                raise TeamToolError(
                    "team_update: 'outcome' is required to complete a task "
                    f"(task_bead_id={task_bead_id!r})."
                )
            board.complete_task(
                task_id=task_id, task_bead_id=task_bead_id, outcome=outcome,
            )
            result = {"task_bead_id": task_bead_id, "status": "done"}
        else:
            raise TeamToolError(
                f"team_update: unsupported transition (task_bead_id set, "
                f"status={status!r}); only status='complete' is supported when "
                "task_bead_id is given."
            )
    except TeamToolError as exc:
        _audit("team_update", task_id=task_id, member_id=member_id,
               outcome="failed", detail=str(exc))
        raise
    _audit("team_update", task_id=task_id, member_id=member_id,
           outcome="success", detail=f"task_bead_id={result['task_bead_id']} status={result['status']}")
    return result


def team_send(
    engine: "ExecutionEngine",
    *,
    task_id: str,
    from_team: str,
    from_member: str,
    to_team: str,
    to_member: str | None = None,
    subject: str,
    body: str,
) -> dict:
    """Canonical send tool — thin authorized wrapper over
    :func:`team_send_message`.

    Raises:
        TeamToolError: unknown *from_team*/*to_team*/*from_member*/
            *to_member*.
        TeamAuthorizationError: *from_member*'s role is not authorized
            for ``team_send``.
    """
    try:
        _require_registry(engine)
        _require_member(engine, task_id, from_member)
        authorize_team_tool(
            engine, task_id=task_id, member_id=from_member, tool_name="team_send",
        )
        bead_id = team_send_message(
            engine, task_id=task_id,
            from_team=from_team, from_member=from_member,
            to_team=to_team, to_member=to_member,
            subject=subject, body=body,
        )
    except TeamToolError as exc:
        _audit("team_send", task_id=task_id, member_id=from_member,
               outcome="failed", detail=str(exc))
        raise
    _audit("team_send", task_id=task_id, member_id=from_member,
           outcome="success", detail=f"message_bead_id={bead_id} to_team={to_team}")
    return {"message_bead_id": bead_id}


def team_read(
    engine: "ExecutionEngine",
    *,
    task_id: str,
    team_id: str,
    member_id: str,
    limit: int = 100,
    ack: bool = True,
) -> list[dict]:
    """Pull unread mailbox messages addressed to *member_id* or *team_id*.

    Complements the existing next-dispatch push delivery (see
    ``references/team-messaging.md``) with an explicit pull the agent can
    call mid-turn. By default (``ack=True``) each returned message is
    immediately acked so it is not re-delivered on the next dispatch or
    the next ``team_read`` call — pass ``ack=False`` to peek without
    consuming.

    Raises:
        TeamToolError: unknown *team_id*/*member_id*.
        TeamAuthorizationError: role not authorized for ``team_read``.
    """
    try:
        reg = _require_registry(engine)
        _require_team(reg, task_id, team_id)
        _require_member(engine, task_id, member_id)
        authorize_team_tool(
            engine, task_id=task_id, member_id=member_id, tool_name="team_read",
        )

        from agent_baton.core.engine.team_board import TeamBoard
        board = TeamBoard(_require_bead_store(engine))
        messages = board.unread_messages_for_member(
            task_id=task_id, team_id=team_id, member_id=member_id, limit=limit,
        )
        out = [_message_bead_to_dict(m) for m in messages]
        if ack:
            for m in messages:
                board.ack_message(
                    task_id=task_id, message_bead_id=m.bead_id,
                    recipient_member_id=member_id,
                )
    except TeamToolError as exc:
        _audit("team_read", task_id=task_id, member_id=member_id,
               outcome="failed", detail=str(exc))
        raise
    _audit("team_read", task_id=task_id, member_id=member_id,
           outcome="success", detail=f"count={len(out)} ack={ack}")
    return out


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
    try:
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
    except TeamToolError as exc:
        _audit("team_dispatch", task_id=task_id, member_id=caller_member_id,
               outcome="failed", detail=str(exc))
        raise
    _audit("team_dispatch", task_id=task_id, member_id=caller_member_id,
           outcome="success", detail=f"child_team_id={child_team_id}")
    return child_team_id
