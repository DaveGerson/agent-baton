"""``baton team status|show`` -- ad-hoc team status for a manager-mode task (M7).

See docs/internal/manager-mode-pmo-plan.md Wave 2 / Task 10 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §8.3.

``status`` prints: team purpose, roles (with owned-workstream counts),
current workstream ownership, completed handoffs, open knowledge gaps, open
scope changes, and manager decisions needed -- the PRD §8.3 field list.
``show`` prints everything ``status`` does, plus each role's full role-card
content (spec §14.2 template, via ``role_cards.render_role_card``).

Task-id resolution and context-root discovery mirror
``agent_baton.cli.commands.report_cmd`` (itself mirroring
``agent_baton.cli.commands.execution.handoff``'s local copies) -- kept
local rather than imported so this module has no dependency on
``report_cmd`` or the heavy ``execute`` module.

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
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.manager.reports import ManagerReportBuilder
from agent_baton.core.manager.role_cards import render_role_card
from agent_baton.core.storage import detect_backend, get_project_storage
from agent_baton.models.manager import KnowledgePlan, ScopeMap, TeamBlueprint


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

    return p


# ---------------------------------------------------------------------------
# handler
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    subcommand = getattr(args, "team_subcommand", None) or "status"
    if subcommand not in ("status", "show"):
        user_error(
            f"unknown team subcommand: {subcommand}",
            hint="Use 'baton team status' or 'baton team show'.",
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
