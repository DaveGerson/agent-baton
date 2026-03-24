"""ForgeSession — consultative plan creation using IntelligentPlanner.

Does NOT call Anthropic API directly. Delegates entirely to
IntelligentPlanner.create_plan() for plan generation.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.pmo.store import PmoStore
from agent_baton.models.execution import ExecutionState, MachinePlan
from agent_baton.models.pmo import PmoProject


class ForgeSession:
    """Create and save execution plans using baton's own planner."""

    def __init__(
        self,
        planner: object,  # IntelligentPlanner (typed loosely to avoid circular deps)
        store: PmoStore,
    ) -> None:
        self._planner = planner
        self._store = store

    def create_plan(
        self,
        description: str,
        program: str,
        project_id: str,
        *,
        task_type: str | None = None,
        priority: int = 0,
    ) -> MachinePlan:
        """Create an execution plan via IntelligentPlanner.

        Parameters
        ----------
        description:
            Natural-language task description (the PRD).
        program:
            Program code (e.g., "RW").
        project_id:
            ID of the registered project to scope the plan to.
        task_type:
            Optional task type override (e.g., "new-feature", "bug-fix").
        priority:
            0=normal, 1=high, 2=critical.

        Returns
        -------
        MachinePlan ready for review and approval.
        """
        project = self._store.get_project(project_id)
        project_root = Path(project.path) if project else None

        plan: MachinePlan = self._planner.create_plan(
            task_summary=description,
            task_type=task_type,
            project_root=project_root,
        )
        return plan

    def save_plan(
        self,
        plan: MachinePlan,
        project: PmoProject,
    ) -> Path:
        """Save an approved plan to the project's team-context.

        Writes both plan.json (for the engine) and plan.md (for humans).
        Does NOT create an ExecutionState — that happens when
        ``baton execute start`` is run.

        Returns the path to the written plan.json.
        """
        from agent_baton.core.orchestration.context import ContextManager
        context_root = Path(project.path) / ".claude" / "team-context"
        # Write into task-scoped directory
        ctx = ContextManager(
            team_context_dir=context_root,
            task_id=plan.task_id,
        )
        ctx.write_plan(plan)

        return ctx.plan_json_path

    def signal_to_plan(
        self,
        signal_id: str,
        project_id: str,
    ) -> MachinePlan | None:
        """Triage a signal into a plan via the Forge.

        Looks up the signal, generates a bug-fix plan, and links them.
        Returns None if signal not found.
        """
        config = self._store.load_config()
        signal = next(
            (s for s in config.signals if s.signal_id == signal_id), None
        )
        if signal is None:
            return None

        project = self._store.get_project(project_id)
        if project is None:
            return None

        description = (
            f"Bug fix: {signal.title}"
            + (f"\n\n{signal.description}" if signal.description else "")
        )

        plan = self.create_plan(
            description=description,
            program=project.program,
            project_id=project_id,
            task_type="bug-fix",
        )

        # Link the signal to the plan
        signal.forge_task_id = plan.task_id
        signal.status = "triaged"
        self._store.save_config(config)

        return plan
