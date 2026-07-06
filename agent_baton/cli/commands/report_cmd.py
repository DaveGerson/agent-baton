"""``baton report`` -- manager-mode status report for a task (M7).

See docs/internal/manager-mode-pmo-plan.md Wave 2 / Task 10 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §8.5 / §15.2.

Resolves the target task via the same ladder ``baton execute`` uses
(explicit ``--task-id`` -> ``BATON_TASK_ID`` env var -> SQLite active-task
row -> ``active-task-id.txt`` file marker -- see
``agent_baton.cli.commands.execution.execute.handler`` and the near-identical
local copy in ``agent_baton.cli.commands.execution.handoff``), loads the
manager-mode sidecar artifacts written by ``ManagerModePlanner`` under
``executions/<task_id>/``, and renders (or rebuilds) the manager report.

Kept free of any import of ``agent_baton.cli.commands.execution.execute``
itself -- that module pulls in the full ``ExecutionEngine`` just for a path
lookup and a task-id ladder; this module (like ``handoff.py``) keeps its own
small local copies instead.

Degrades gracefully when execution hasn't started yet (PRD: "manager brief
after planning, report after execution"): a task that has only been planned
(``baton plan --manager-mode --save``) still produces a report from the
scope-map/team-blueprint/knowledge-plan sidecars and the saved ``plan.json``
-- ``execution-state.json`` is optional.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from agent_baton.cli.errors import user_error
from agent_baton.core.config.manager import ManagerConfig, ManagerConfigError
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.manager.artifacts import ManagerArtifacts
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.manager.reports import ManagerReportBuilder
from agent_baton.core.storage import detect_backend, get_project_storage
from agent_baton.models.execution import MachinePlan
from agent_baton.models.manager import KnowledgePlan, ScopeMap, TeamBlueprint


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:  # type: ignore[type-arg]
    """Register the ``report`` top-level command."""
    p = subparsers.add_parser(
        "report",
        help="Manager-mode status report for a task (brief + execution progress)",
    )
    p.add_argument(
        "--task-id", dest="task_id", default=None,
        help="Target a specific execution by task ID (defaults to the active task)",
    )
    p.add_argument(
        "--json", dest="json_output", action="store_true", default=False,
        help="Emit machine-readable JSON instead of Markdown",
    )
    return p


# ---------------------------------------------------------------------------
# handler
# ---------------------------------------------------------------------------


def handler(args: argparse.Namespace) -> None:
    context_root = _resolve_context_root()
    task_id = _resolve_task_id(getattr(args, "task_id", None), context_root)

    if not task_id:
        user_error(
            "no active manager-mode task found",
            hint="Pass --task-id, set BATON_TASK_ID, or run 'baton plan --manager-mode --save \"<task>\"' first.",
        )
        return  # pragma: no cover -- user_error never returns

    paths = ManagerArtifactPaths(context_root, task_id)
    plan = _load_plan(paths, context_root, task_id)
    if plan is None:
        user_error(
            f"no plan found for task {task_id!r}",
            hint="Run 'baton plan --manager-mode --save \"<task>\"' first.",
        )
        return  # pragma: no cover -- user_error never returns

    execution_state = _load_execution_state_dict(context_root, task_id)
    artifacts = _load_artifacts(paths)
    config = _load_config(context_root)
    builder = ManagerReportBuilder(config, paths)

    if getattr(args, "json_output", False):
        data = builder.build_report_data(plan, artifacts, execution_state)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    text = builder.build_report(plan, artifacts, execution_state)
    try:
        # Keep the sidecar in sync with what was just shown. Execution
        # hooks (M9) refresh it further as the run progresses; this write
        # is what makes `baton report` useful even before those hooks land.
        builder.save_report(plan, artifacts, execution_state)
    except OSError as exc:  # pragma: no cover -- best-effort persistence
        print(f"warning: could not persist manager-report.md: {exc}", file=sys.stderr)
    print(text)


# ---------------------------------------------------------------------------
# Helpers (mirror agent_baton.cli.commands.execution.handoff's local copies --
# kept local to avoid importing the heavy execute module just for a path
# lookup / task-id resolution)
# ---------------------------------------------------------------------------


def _resolve_context_root() -> Path:
    """Resolve the ``.claude/team-context`` directory.

    Mirrors :func:`agent_baton.cli.commands.execution.execute._resolve_context_root`.
    """
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
    """Apply the same task-id resolution chain as ``baton execute``."""
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


def _load_plan(paths: ManagerArtifactPaths, context_root: Path, task_id: str) -> MachinePlan | None:
    """Prefer the live execution-state's plan (may carry amendments); fall
    back to the ``plan.json`` sidecar written by ``baton plan --save``
    (brief-only state, execution not yet started)."""
    state = StatePersistence(context_root, task_id=task_id).load()
    if state is not None:
        return state.plan

    plan_json = paths.root / "plan.json"
    if plan_json.is_file():
        try:
            return MachinePlan.from_dict(json.loads(plan_json.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError, ValueError, KeyError):
            return None
    return None


def _load_execution_state_dict(context_root: Path, task_id: str) -> dict[str, Any] | None:
    state = StatePersistence(context_root, task_id=task_id).load()
    if state is None:
        return None
    return state.to_dict()


def _load_artifacts(paths: ManagerArtifactPaths) -> ManagerArtifacts:
    """Best-effort reconstruction of the sidecar artifacts this CLI needs.

    ``charter`` has no JSON sidecar (only the rendered
    ``project-charter.md`` -- see ``ManagerArtifactPaths``), so it is left
    ``None`` here; report sections that would use it fall back to
    plan-level data instead (the graceful-degradation path the hard
    constraints require).
    """
    artifacts = ManagerArtifacts()
    artifacts.scope_map = _read_json_model(paths.scope_map, ScopeMap)
    artifacts.blueprint = _read_json_model(paths.team_blueprint, TeamBlueprint)
    artifacts.knowledge_plan = _read_json_model(paths.knowledge_plan, KnowledgePlan)
    return artifacts


def _read_json_model(path: Path, model_cls: type) -> Any:
    if not path.is_file():
        return None
    try:
        return model_cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, ValueError, KeyError):
        return None


def _load_config(context_root: Path) -> ManagerConfig:
    # ``<root>/.claude/team-context`` -> project root is two levels up.
    project_root = context_root.parent.parent
    try:
        return ManagerConfig.load(project_root)
    except ManagerConfigError:
        return ManagerConfig()
