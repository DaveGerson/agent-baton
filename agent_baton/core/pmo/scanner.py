"""PmoScanner — scan registered projects and build the Kanban board state.

For each registered project, reads execution-state.json via StatePersistence
and maps ExecutionState.status to a PmoCard column.
"""
from __future__ import annotations

from pathlib import Path

from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.pmo.store import PmoStore
from agent_baton.models.pmo import (
    PmoCard,
    PmoProject,
    ProgramHealth,
    status_to_column,
)


class PmoScanner:
    """Scan registered projects and produce Kanban board cards."""

    def __init__(self, store: PmoStore) -> None:
        self._store = store

    def _state_to_card(
        self, state: "ExecutionState", project: PmoProject
    ) -> PmoCard:
        """Convert an ExecutionState to a PmoCard."""
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

        Supports both namespaced executions (multiple plans per project)
        and legacy flat execution-state.json files.
        """
        context_root = Path(project.path) / ".claude" / "team-context"
        cards: list[PmoCard] = []

        # Load all execution states (namespaced + legacy flat file)
        states = StatePersistence.load_all(context_root)
        for state in states:
            cards.append(self._state_to_card(state, project))

        # Also check for a saved plan (plan.json) without execution state → queued
        seen_task_ids = {c.card_id for c in cards}
        plan_path = context_root / "plan.json"
        if plan_path.exists():
            import json
            try:
                data = json.loads(plan_path.read_text(encoding="utf-8"))
                from agent_baton.models.execution import MachinePlan
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
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        return cards

    def scan_all(self) -> list[PmoCard]:
        """Scan all registered projects and return all cards."""
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
        """Compute aggregate health metrics per program."""
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
