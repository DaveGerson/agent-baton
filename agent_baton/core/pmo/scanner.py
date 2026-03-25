"""PmoScanner — scan registered projects and build Kanban board state.

The scanner iterates over all projects registered in the PMO store,
reads their execution state (from SQLite or legacy JSON files), and
converts each active execution into a ``PmoCard`` with the appropriate
Kanban column (``queued``, ``in_progress``, ``awaiting_human``,
``deployed``).

The scanner also detects saved plans that have no corresponding execution
state (i.e. plans created but not yet started) and maps them to
``queued`` cards.

This module powers the ``baton pmo status`` CLI command and the PMO
dashboard UI.
"""
from __future__ import annotations

import logging
from pathlib import Path

from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.pmo.store import PmoStore
from agent_baton.core.storage import detect_backend, get_project_storage
from agent_baton.models.pmo import (
    PmoCard,
    PmoProject,
    ProgramHealth,
    status_to_column,
)

_log = logging.getLogger(__name__)


class PmoScanner:
    """Scan registered projects and produce Kanban board cards.

    The scanner supports both SQLite-backed and file-based projects.  For
    each project it auto-detects the storage backend and reads all
    execution states, converting each into a ``PmoCard`` with Kanban
    column assignment.

    Attributes:
        _store: The PMO store providing the list of registered projects
            and the archive of completed cards.
    """

    def __init__(self, store: PmoStore) -> None:
        self._store = store

    def _state_to_card(
        self, state: "ExecutionState", project: PmoProject
    ) -> PmoCard:
        """Convert an ``ExecutionState`` into a ``PmoCard``.

        Maps the execution status to a Kanban column via
        ``status_to_column()``, counts completed steps, failed steps,
        and passed gates, and extracts the current phase name.

        Args:
            state: The execution state to convert.
            project: The project this execution belongs to (provides
                ``project_id`` and ``program``).

        Returns:
            A ``PmoCard`` representing this execution on the Kanban board.
        """
        plan = state.plan
        completed = len([
            r for r in state.step_results if r.status == "complete"
        ])
        failed = [
            r for r in state.step_results if r.status == "failed"
        ]
        gates_passed = len([
            g for g in state.gate_results if g.passed
        ])

        current_phase_name = ""
        if state.current_phase < len(plan.phases):
            current_phase_name = plan.phases[state.current_phase].name

        return PmoCard(
            card_id=plan.task_id,
            project_id=project.project_id,
            program=project.program,
            title=plan.task_summary,
            column=status_to_column(state.status),
            risk_level=plan.risk_level,
            agents=list(plan.all_agents),
            steps_completed=completed,
            steps_total=plan.total_steps,
            gates_passed=gates_passed,
            current_phase=current_phase_name,
            error=failed[-1].error if failed else "",
            created_at=plan.created_at,
            updated_at=state.completed_at or state.started_at,
        )

    def scan_project(self, project: PmoProject) -> list[PmoCard]:
        """Scan a single project for execution states and return cards.

        Auto-detects whether the project uses SQLite or file-based storage
        via ``detect_backend()``.  For SQLite projects, reads from
        ``baton.db``; for file-based projects, reads from
        ``execution-state.json`` files (both namespaced and legacy flat).

        Also scans for saved plans without execution state (i.e. plans
        created via ``baton plan --save`` but not yet started) and maps
        them to ``queued`` cards.

        Args:
            project: The registered project to scan.

        Returns:
            List of ``PmoCard`` instances representing all executions
            and queued plans found in the project.
        """
        context_root = Path(project.path) / ".claude" / "team-context"
        cards: list[PmoCard] = []

        backend = detect_backend(context_root)
        if backend == "sqlite":
            _log.debug(
                "scan_project[%s]: using SQLite backend at %s",
                project.project_id,
                context_root / "baton.db",
            )
            try:
                storage = get_project_storage(context_root, backend="sqlite")
                task_ids = storage.list_executions()
                for tid in task_ids:
                    state = storage.load_execution(tid)
                    if state is not None:
                        cards.append(self._state_to_card(state, project))
            except Exception:
                _log.debug(
                    "scan_project[%s]: SQLite load failed, falling back to files",
                    project.project_id,
                    exc_info=True,
                )
                states = StatePersistence.load_all(context_root)
                for state in states:
                    cards.append(self._state_to_card(state, project))
        else:
            _log.debug(
                "scan_project[%s]: using file backend at %s",
                project.project_id,
                context_root,
            )
            # Load all execution states (namespaced + legacy flat file)
            states = StatePersistence.load_all(context_root)
            for state in states:
                cards.append(self._state_to_card(state, project))

        # Check for saved plans without execution state → queued
        # Scan both legacy root plan.json and task-scoped plan files
        import json
        from agent_baton.models.execution import MachinePlan

        seen_task_ids = {c.card_id for c in cards}

        plan_paths: list[Path] = []
        # Legacy root plan.json
        root_plan = context_root / "plan.json"
        if root_plan.exists():
            plan_paths.append(root_plan)
        # Task-scoped plan files (executions/<task-id>/plan.json)
        exec_dir = context_root / "executions"
        if exec_dir.is_dir():
            for task_dir in exec_dir.iterdir():
                if task_dir.is_dir():
                    scoped_plan = task_dir / "plan.json"
                    if scoped_plan.exists():
                        plan_paths.append(scoped_plan)

        for plan_path in plan_paths:
            try:
                data = json.loads(plan_path.read_text(encoding="utf-8"))
                plan = MachinePlan.from_dict(data)
                if plan.task_id not in seen_task_ids:
                    card = PmoCard(
                        card_id=plan.task_id,
                        project_id=project.project_id,
                        program=project.program,
                        title=plan.task_summary,
                        column="queued",
                        risk_level=plan.risk_level,
                        agents=list(plan.all_agents),
                        steps_total=plan.total_steps,
                        created_at=plan.created_at,
                    )
                    cards.append(card)
                    seen_task_ids.add(plan.task_id)
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        return cards

    def scan_all(self) -> list[PmoCard]:
        """Scan all registered projects and return all cards.

        Iterates over every project in the PMO config, calls
        ``scan_project`` for each, and appends up to 50 archived
        (deployed) cards from the archive.  Duplicate card IDs between
        active and archived cards are de-duplicated.

        Returns:
            Combined list of active and archived ``PmoCard`` instances.
        """
        config = self._store.load_config()
        cards: list[PmoCard] = []
        for project in config.projects:
            cards.extend(self.scan_project(project))

        # Also include archived (deployed) cards
        archived = self._store.read_archive(limit=50)
        # Avoid duplicates — archived cards have column="deployed"
        active_ids = {c.card_id for c in cards}
        for ac in archived:
            if ac.card_id not in active_ids:
                cards.append(ac)

        return cards

    def program_health(self) -> dict[str, ProgramHealth]:
        """Compute aggregate health metrics per program.

        Scans all projects and classifies each card into one of four
        buckets: ``completed`` (deployed), ``blocked`` (awaiting human),
        ``failed`` (has error), or ``active`` (everything else).
        Computes ``completion_pct`` as the ratio of completed to total
        plans.

        Returns:
            Dict mapping program name to ``ProgramHealth`` with fields
            ``total_plans``, ``completed``, ``blocked``, ``failed``,
            ``active``, and ``completion_pct``.
        """
        config = self._store.load_config()
        programs = config.programs or list({
            p.program for p in config.projects
        })

        health: dict[str, ProgramHealth] = {}
        for prog in programs:
            health[prog] = ProgramHealth(program=prog)

        cards = self.scan_all()
        for card in cards:
            h = health.get(card.program)
            if h is None:
                h = ProgramHealth(program=card.program)
                health[card.program] = h

            h.total_plans += 1
            if card.column == "deployed":
                h.completed += 1
            elif card.column == "awaiting_human":
                h.blocked += 1
            elif card.error:
                h.failed += 1
            else:
                h.active += 1

        for h in health.values():
            if h.total_plans > 0:
                h.completion_pct = round(
                    (h.completed / h.total_plans) * 100, 1
                )

        return health
