"""``baton team`` -- ad-hoc team status (M7) plus the team runtime-contract
CLI (Phase 4 4.2).

Two independent surfaces share this module because they share the ``team``
top-level argparse group (one entry point per group, per ``cli/CLAUDE.md``):

- ``status`` / ``show`` -- manager-mode PMO team status and role cards. See
  docs/internal/manager-mode-pmo-plan.md Wave 2 / Task 10 and
  docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §8.3.
  ``status`` prints: team purpose, roles (with owned-workstream counts),
  current workstream ownership, completed handoffs, open knowledge gaps,
  open scope changes, and manager decisions needed -- the PRD §8.3 field
  list. ``show`` prints everything ``status`` does, plus each role's full
  role-card content (spec §14.2 template, via
  ``role_cards.render_role_card``).

- ``list`` / ``claim`` / ``update`` / ``send`` / ``read`` -- the callable
  boundary for the five canonical ``team_*`` tools specified in
  docs/internal/team-runtime-contract.md. This is the "structured Baton CLI
  surface" the contract chose over a local MCP server (§2.1-2.2): a
  dispatched team member (which already has the ``Bash`` tool) shells out to
  ``baton team <verb> --json ...``, which calls straight into the typed
  Python functions in ``agent_baton.core.engine.team_tools`` -- the same
  functions the in-process test suite (``tests/test_team_tools.py``)
  exercises directly, so the CLI is a thin, tested adapter, not a second
  implementation. ``team_dispatch`` is intentionally NOT exposed here (the
  contract scopes the CLI verb list to these five -- see doc §2.2/§9.1); a
  lead still has no callable path to stand up a sub-team mid-flight in this
  release.

Task-id resolution and context-root discovery for ``status``/``show``
mirror ``agent_baton.cli.commands.report_cmd`` (itself mirroring
``agent_baton.cli.commands.execution.handoff``'s local copies) -- kept
local rather than imported so this module has no dependency on
``report_cmd`` or the heavy ``execute`` module. The runtime-contract verbs
below reuse ``_resolve_task_id`` (task-id resolution is identical) but add
their own ``_resolve_runtime_context_root`` because they must resolve
``baton.db`` correctly from *inside an isolated worktree* -- see that
function's docstring.

CRITICAL (Wave 1 review, binding): workstream ownership is always read
from ``TeamBlueprint.workstream_assignments`` -- never
``Workstream.owner_role``. A role that owns zero workstreams (a
"displaced generalist") is listed under Roles only, never as a
workstream's owner.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from agent_baton.cli.errors import user_error
from agent_baton.core.config.manager import ManagerConfig, ManagerConfigError
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.engine.team_tools import (
    TeamAuthorizationError,
    TeamBackendUnavailableError,
    TeamConcurrencyError,
    TeamToolError,
    team_claim,
    team_list,
    team_read,
    team_send,
    team_update,
)
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.manager.reports import ManagerReportBuilder
from agent_baton.core.manager.role_cards import render_role_card
from agent_baton.core.storage import detect_backend, get_project_storage
from agent_baton.models.manager import KnowledgePlan, ScopeMap, TeamBlueprint

# ---------------------------------------------------------------------------
# Failure taxonomy -- docs/internal/team-runtime-contract.md §7.3.
# Exit codes are part of the CLI's public contract for scripted callers
# (a dispatched agent's Bash tool inspects the exit code to decide whether
# to retry).
# ---------------------------------------------------------------------------

EXIT_USAGE = 2                # unknown team_id/member_id, malformed args
EXIT_AUTHORIZATION = 3        # role not authorized for the requested tool
EXIT_CONCURRENCY_CONFLICT = 4  # optimistic-concurrency claim conflict
EXIT_BACKEND_UNAVAILABLE = 5  # TeamRegistry/bead store not configured


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``team`` top-level command and its subcommands."""
    p = subparsers.add_parser(
        "team",
        help="Ad-hoc team status and role cards for a manager-mode task",
    )
    p.add_argument(
        "--task-id", dest="task_id", default=None,
        help="Target a specific execution by task ID (defaults to the active task)",
    )

    sub = p.add_subparsers(dest="team_subcommand")

    p_status = sub.add_parser("status", help="Team purpose, roles, ownership, and open items")
    p_status.add_argument("--task-id", dest="task_id", default=None,
                           help="Target a specific execution by task ID")

    p_show = sub.add_parser("show", help="Everything 'status' shows, plus each role's full role card")
    p_show.add_argument("--task-id", dest="task_id", default=None,
                         help="Target a specific execution by task ID")

    # ── Team runtime-contract verbs (Phase 4 4.2) ────────────────────────
    # Common flags shared by list/claim/update/read (team_id + member_id +
    # task_id + --json); send addresses from/to teams explicitly instead
    # of a single --team-id, so it is wired separately below.

    def _add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--task-id", dest="task_id", default=None,
            help="Target task ID (defaults to $BATON_TASK_ID or the active task)",
        )
        sp.add_argument(
            "--team-id", dest="team_id", required=True,
            help="Team ID (top-level team steps use 'team-<step-id>')",
        )
        sp.add_argument(
            "--member-id", dest="member_id", default=None,
            help="Calling member's ID (defaults to $BATON_TEAM_MEMBER_ID)",
        )
        sp.add_argument(
            "--json", dest="json_output", action="store_true",
            help="Emit machine-readable JSON instead of a human-readable table",
        )

    p_list = sub.add_parser(
        "list", help="List the shared task board or child teams",
    )
    _add_common(p_list)
    p_list.add_argument(
        "--resource", choices=["tasks", "teams"], default="tasks",
        help="'tasks' (default): the shared task board. 'teams': child teams.",
    )
    p_list.add_argument(
        "--status", choices=["open", "claimed", "done"], default=None,
        help="Filter tasks by status (tasks resource only)",
    )
    p_list.add_argument("--limit", type=int, default=100)
    p_list.add_argument(
        "--all", dest="list_all", action="store_true",
        help=(
            "Force the unfiltered lead/observer-wide view (ignores "
            "--member-id and $BATON_TEAM_MEMBER_ID)"
        ),
    )

    p_claim = sub.add_parser("claim", help="Claim an open task on the board")
    _add_common(p_claim)
    p_claim.add_argument("--task-bead-id", dest="task_bead_id", required=True)
    p_claim.add_argument(
        "--allow-reassign", dest="allow_reassign", action="store_true",
        help="Bypass optimistic concurrency and take over another member's claim",
    )

    p_update = sub.add_parser(
        "update", help="Create a new task, or complete an existing one",
    )
    _add_common(p_update)
    p_update.add_argument(
        "--task-bead-id", dest="task_bead_id", default=None,
        help="Omit to create a new task; pass to transition an existing one",
    )
    p_update.add_argument("--title", dest="title", default=None, help="Required to create")
    p_update.add_argument("--detail", dest="detail", default="")
    p_update.add_argument(
        "--status", dest="status", choices=["complete"], default=None,
        help="Only 'complete' is a supported transition in this version",
    )
    p_update.add_argument("--outcome", dest="outcome", default="", help="Required to complete")
    p_update.add_argument(
        "--idempotency-key", dest="idempotency_key", default=None,
        help="Create mode only -- a retried call with the same key returns the original task",
    )
    p_update.add_argument("--parent-task-bead-id", dest="parent_task_bead_id", default=None)

    p_send = sub.add_parser("send", help="Send a mailbox message to a team or member")
    p_send.add_argument(
        "--task-id", dest="task_id", default=None,
        help="Target task ID (defaults to $BATON_TASK_ID or the active task)",
    )
    p_send.add_argument("--from-team", dest="from_team", required=True)
    p_send.add_argument(
        "--from-member", dest="from_member", default=None,
        help="Defaults to --member-id, then $BATON_TEAM_MEMBER_ID",
    )
    p_send.add_argument(
        "--member-id", dest="member_id", default=None,
        help="Alias for --from-member",
    )
    p_send.add_argument("--to-team", dest="to_team", required=True)
    p_send.add_argument(
        "--to-member", dest="to_member", default=None,
        help="Omit for a broadcast to the whole --to-team",
    )
    p_send.add_argument("--subject", dest="subject", required=True)
    p_send.add_argument("--body", dest="body", required=True)
    p_send.add_argument("--json", dest="json_output", action="store_true")

    p_read = sub.add_parser("read", help="Read (and by default ack) mailbox messages")
    _add_common(p_read)
    p_read.add_argument("--limit", type=int, default=100)
    p_read.add_argument(
        "--no-ack", dest="no_ack", action="store_true",
        help="Peek without acking -- messages remain unread for the next call/dispatch",
    )

    return p


# ---------------------------------------------------------------------------
# handler
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    subcommand = getattr(args, "team_subcommand", None) or "status"

    if subcommand in _RUNTIME_HANDLERS:
        _RUNTIME_HANDLERS[subcommand](args)
        return

    if subcommand not in ("status", "show"):
        user_error(
            f"unknown team subcommand: {subcommand}",
            hint=(
                "Use 'baton team status', 'baton team show', or one of the "
                "runtime-contract verbs: list, claim, update, send, read."
            ),
        )
        return  # pragma: no cover -- user_error never returns

    context_root = _resolve_context_root()
    task_id = _resolve_task_id(getattr(args, "task_id", None), context_root)
    if not task_id:
        user_error(
            "no active manager-mode task found",
            hint="Pass --task-id, set BATON_TASK_ID, or run 'baton plan --manager-mode --save \"<task>\"' first.",
        )
        return  # pragma: no cover -- user_error never returns

    paths = ManagerArtifactPaths(context_root, task_id)
    blueprint = _read_json_model(paths.team_blueprint, TeamBlueprint)
    if blueprint is None:
        user_error(
            f"no team blueprint found for task {task_id!r}",
            hint="Run 'baton plan --manager-mode --save \"<task>\"' first.",
        )
        return  # pragma: no cover -- user_error never returns

    scope_map = _read_json_model(paths.scope_map, ScopeMap)
    knowledge_plan = _read_json_model(paths.knowledge_plan, KnowledgePlan)
    config = _load_config(context_root)

    print(f"Team: {blueprint.team_name} ({task_id})")
    print(f"Mission: {blueprint.mission or '(unspecified)'}")
    print()

    owned_counts: dict[str, int] = {}
    for role in blueprint.workstream_assignments.values():
        if role:
            owned_counts[role] = owned_counts.get(role, 0) + 1

    print("Roles:")
    for card in blueprint.roles:
        count = owned_counts.get(card.role, 0)
        descriptor = f"owns {count} workstream(s)" if count else "no workstream owned (support role)"
        print(f"  - {card.role}: {descriptor}")
    print()

    print("Workstream ownership:")
    if scope_map is not None and scope_map.workstreams:
        for ws in scope_map.workstreams:
            # Authoritative owner -- always workstream_assignments, never
            # Workstream.owner_role (see module docstring).
            owner = blueprint.workstream_assignments.get(ws.id, "(unassigned)")
            print(f"  - {ws.id} ({ws.name or '(unnamed)'}): {owner}")
    else:
        print("  (none recorded)")
    print()

    handoffs: list[str] = []
    if paths.handoffs_dir.is_dir():
        handoffs = sorted(p.name for p in paths.handoffs_dir.glob("phase-*-handoff.md"))
    print(f"Completed handoffs: {', '.join(handoffs) if handoffs else '(none)'}")

    knowledge_gaps: list[str] = []
    if knowledge_plan is not None:
        for missing in knowledge_plan.missing_packs:
            knowledge_gaps.append(f"missing pack: {missing.name} ({missing.reason})")
        for name in knowledge_plan.stale_packs:
            knowledge_gaps.append(f"stale pack: {name}")
    print(f"Open knowledge gaps: {', '.join(knowledge_gaps) if knowledge_gaps else '(none)'}")

    report_builder = ManagerReportBuilder(config, paths)
    decision_log = report_builder.read_decision_log()
    scope_changes = [e for e in decision_log if e.get("decision_type") == "scope_expansion"]
    open_scope_changes = [e for e in scope_changes if not e.get("resolved_at")]
    open_decisions = [e for e in decision_log if not e.get("resolved_at")]

    print(f"Open scope changes: {len(open_scope_changes)}")
    print(f"Manager decisions needed: {len(open_decisions)}")
    for entry in open_decisions:
        print(f"  - {entry.get('summary', '')} ({entry.get('decision_id', '')})")

    if subcommand == "show":
        print()
        print("Role cards:")
        for card in blueprint.roles:
            print()
            print(render_role_card(card).rstrip("\n"))


# ---------------------------------------------------------------------------
# Helpers (mirror agent_baton.cli.commands.report_cmd -- kept local rather
# than imported so this module has no dependency on report_cmd)
# ---------------------------------------------------------------------------


def _resolve_context_root() -> Path:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            git_root = Path(result.stdout.strip())
            return (git_root / ".claude" / "team-context").resolve()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    cwd = Path.cwd()
    for ancestor in [cwd, *cwd.parents]:
        candidate = ancestor / ".claude" / "team-context"
        if candidate.is_dir():
            return candidate.resolve()
    return (cwd / ".claude" / "team-context").resolve()


def _resolve_task_id(explicit: str | None, context_root: Path) -> str | None:
    if explicit:
        return explicit
    env_id = os.environ.get("BATON_TASK_ID")
    if env_id:
        return env_id
    try:
        backend = detect_backend(context_root)
    except Exception:  # noqa: BLE001 - defensive
        backend = "file"
    if backend == "sqlite":
        try:
            storage = get_project_storage(context_root, backend="sqlite")
            tid = storage.get_active_task()
            if tid:
                return tid
        except Exception:  # noqa: BLE001 - defensive
            pass
    try:
        return StatePersistence.get_active_task_id(context_root)
    except Exception:  # noqa: BLE001 - defensive
        return None


def _read_json_model(path: Path, model_cls: type):
    if not path.is_file():
        return None
    try:
        return model_cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, ValueError, KeyError):
        return None


def _load_config(context_root: Path) -> ManagerConfig:
    project_root = context_root.parent.parent
    try:
        return ManagerConfig.load(project_root)
    except ManagerConfigError:
        return ManagerConfig()


# ---------------------------------------------------------------------------
# Team runtime-contract verbs (Phase 4 4.2) -- list / claim / update / send /
# read. See docs/internal/team-runtime-contract.md for the full design;
# these handlers are a thin argparse-to-team_tools adapter plus the
# resolution/output/error-mapping conventions the doc specifies (§2.2, §7.3).
# ---------------------------------------------------------------------------


def _resolve_runtime_context_root() -> Path:
    """Resolve ``.claude/team-context/`` for the runtime-contract verbs.

    Unlike :func:`_resolve_context_root` (used by ``status``/``show``,
    which read plain JSON artifacts off disk), these verbs must open the
    project's ``baton.db`` -- and a dispatched team member's ``Bash`` tool
    often runs from *inside an isolated git worktree*. In that case
    ``git rev-parse --show-toplevel`` resolves to the WORKTREE root, not
    the parent project root, so a plain git-walk would silently target a
    nonexistent (or wrong) worktree-local ``baton.db``. ``BATON_DB_PATH``
    / ``BATON_TEAM_CONTEXT_ROOT`` are exactly the pointers
    ``ClaudeCodeLauncher._inject_parent_state_env`` sets on every
    worktree-isolated subprocess for this reason (bd-37a9) -- honor them
    FIRST, before falling back to the git/cwd walk used by the legacy
    status/show verbs (and by a human running ``baton team list`` directly
    from the project root, where the env vars are typically unset).
    """
    root_env = os.environ.get("BATON_TEAM_CONTEXT_ROOT", "").strip()
    if root_env:
        return Path(root_env)
    db_env = os.environ.get("BATON_DB_PATH", "").strip()
    if db_env:
        return Path(db_env).parent
    return _resolve_context_root()


def _resolve_member_id(explicit: str | None) -> str | None:
    """Resolution order: ``--member-id`` flag, then ``$BATON_TEAM_MEMBER_ID``.

    Matches docs/internal/team-runtime-contract.md §2.2's stated order so a
    dispatched member's prompt never has to hand-transcribe its own ID into
    every call (``ClaudeCodeLauncher.launch`` sets the env var from the
    dispatch's own ``step_id`` -- see ``claude_launcher.py``).
    """
    if explicit:
        return explicit
    env_val = os.environ.get("BATON_TEAM_MEMBER_ID", "").strip()
    return env_val or None


def _require_task_id(explicit: str | None, context_root: Path) -> str:
    task_id = _resolve_task_id(explicit, context_root)
    if not task_id:
        user_error(
            "no active task found",
            hint="Pass --task-id, or set $BATON_TASK_ID.",
            exit_code=EXIT_USAGE,
        )
    return task_id  # pragma: no cover -- user_error above never returns on failure


def _build_runtime_engine(task_id: str, context_root: Path) -> ExecutionEngine:
    storage = get_project_storage(context_root)
    return ExecutionEngine(
        team_context_root=context_root, task_id=task_id, storage=storage,
    )


def _call_team_tool(fn, **kwargs):
    """Invoke a canonical team_tools function, mapping its typed exceptions
    onto the CLI exit-code taxonomy (docs/internal/team-runtime-contract.md
    §7.3). Never returns on failure -- ``user_error`` exits the process.
    """
    try:
        return fn(**kwargs)
    except TeamConcurrencyError as exc:
        user_error(
            str(exc), exit_code=EXIT_CONCURRENCY_CONFLICT,
            hint="Re-run 'baton team list' for the current claim state, then retry.",
        )
    except TeamAuthorizationError as exc:
        user_error(str(exc), exit_code=EXIT_AUTHORIZATION)
    except TeamBackendUnavailableError as exc:
        # Typed, not message-sniffed: a usage error whose user-supplied
        # team_id/member_id merely contains the word "unavailable" must NOT
        # be misclassified as "environment broken, stop retrying" (exit 5).
        user_error(str(exc), exit_code=EXIT_BACKEND_UNAVAILABLE)
    except TeamToolError as exc:
        user_error(str(exc), exit_code=EXIT_USAGE)
    raise AssertionError("unreachable")  # pragma: no cover -- user_error never returns


def _format_row(row: dict) -> str:
    return " | ".join(f"{k}={v}" for k, v in row.items())


def _print_team_result(result, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    if isinstance(result, list):
        if not result:
            print("(none)")
            return
        for row in result:
            print(_format_row(row))
        return
    print(_format_row(result))


def _handle_team_list(args: argparse.Namespace) -> None:
    context_root = _resolve_runtime_context_root()
    task_id = _require_task_id(getattr(args, "task_id", None), context_root)
    engine = _build_runtime_engine(task_id, context_root)
    member_id = (
        None if getattr(args, "list_all", False)
        else _resolve_member_id(getattr(args, "member_id", None))
    )
    result = _call_team_tool(
        team_list,
        engine=engine, task_id=task_id, team_id=args.team_id,
        member_id=member_id, resource=args.resource,
        status=args.status, limit=args.limit,
    )
    _print_team_result(result, json_output=args.json_output)


def _handle_team_claim(args: argparse.Namespace) -> None:
    context_root = _resolve_runtime_context_root()
    task_id = _require_task_id(getattr(args, "task_id", None), context_root)
    engine = _build_runtime_engine(task_id, context_root)
    member_id = _resolve_member_id(getattr(args, "member_id", None))
    if not member_id:
        user_error(
            "member_id is required",
            hint="Pass --member-id, or set $BATON_TEAM_MEMBER_ID.",
            exit_code=EXIT_USAGE,
        )
    result = _call_team_tool(
        team_claim,
        engine=engine, task_id=task_id, team_id=args.team_id,
        task_bead_id=args.task_bead_id, member_id=member_id,
        allow_reassign=args.allow_reassign,
    )
    _print_team_result(result, json_output=args.json_output)


def _handle_team_update(args: argparse.Namespace) -> None:
    context_root = _resolve_runtime_context_root()
    task_id = _require_task_id(getattr(args, "task_id", None), context_root)
    engine = _build_runtime_engine(task_id, context_root)
    member_id = _resolve_member_id(getattr(args, "member_id", None))
    if not member_id:
        user_error(
            "member_id is required",
            hint="Pass --member-id, or set $BATON_TEAM_MEMBER_ID.",
            exit_code=EXIT_USAGE,
        )
    result = _call_team_tool(
        team_update,
        engine=engine, task_id=task_id, team_id=args.team_id, member_id=member_id,
        task_bead_id=args.task_bead_id, title=args.title, detail=args.detail,
        status=args.status, outcome=args.outcome,
        idempotency_key=args.idempotency_key,
        parent_task_bead_id=args.parent_task_bead_id,
    )
    _print_team_result(result, json_output=args.json_output)


def _handle_team_send(args: argparse.Namespace) -> None:
    context_root = _resolve_runtime_context_root()
    task_id = _require_task_id(getattr(args, "task_id", None), context_root)
    engine = _build_runtime_engine(task_id, context_root)
    from_member = _resolve_member_id(
        getattr(args, "from_member", None) or getattr(args, "member_id", None)
    )
    if not from_member:
        user_error(
            "from_member is required",
            hint="Pass --from-member (or --member-id), or set $BATON_TEAM_MEMBER_ID.",
            exit_code=EXIT_USAGE,
        )
    result = _call_team_tool(
        team_send,
        engine=engine, task_id=task_id,
        from_team=args.from_team, from_member=from_member,
        to_team=args.to_team, to_member=args.to_member,
        subject=args.subject, body=args.body,
    )
    _print_team_result(result, json_output=args.json_output)


def _handle_team_read(args: argparse.Namespace) -> None:
    context_root = _resolve_runtime_context_root()
    task_id = _require_task_id(getattr(args, "task_id", None), context_root)
    engine = _build_runtime_engine(task_id, context_root)
    member_id = _resolve_member_id(getattr(args, "member_id", None))
    if not member_id:
        user_error(
            "member_id is required",
            hint="Pass --member-id, or set $BATON_TEAM_MEMBER_ID.",
            exit_code=EXIT_USAGE,
        )
    result = _call_team_tool(
        team_read,
        engine=engine, task_id=task_id, team_id=args.team_id, member_id=member_id,
        limit=args.limit, ack=not args.no_ack,
    )
    _print_team_result(result, json_output=args.json_output)


_RUNTIME_HANDLERS = {
    "list": _handle_team_list,
    "claim": _handle_team_claim,
    "update": _handle_team_update,
    "send": _handle_team_send,
    "read": _handle_team_read,
}
