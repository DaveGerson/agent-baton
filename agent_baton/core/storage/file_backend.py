"""FileStorage — backward-compatible wrapper around existing file-based persistence.

Delegates to StatePersistence, UsageLogger, AgentTelemetry, EventPersistence,
TraceRecorder, RetrospectiveEngine, PatternLearner, BudgetTuner, and
ContextManager so legacy projects continue to work unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.events.persistence import EventPersistence
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.observe.telemetry import AgentTelemetry
from agent_baton.core.observe.trace import TraceRecorder
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.models.events import Event
from agent_baton.models.execution import (
    ApprovalResult,
    ExecutionState,
    GateResult,
    MachinePlan,
    PlanAmendment,
    StepResult,
)
from agent_baton.models.plan import MissionLogEntry
from agent_baton.models.retrospective import Retrospective
from agent_baton.models.trace import TaskTrace
from agent_baton.models.usage import TaskUsageRecord


class FileStorage:
    """Backward-compatible file-based storage backend.

    Wraps existing persistence classes so projects that haven't migrated
    to SQLite continue to work exactly as before.
    """

    def __init__(self, context_root: Path) -> None:
        self._root = context_root

    @property
    def context_root(self) -> Path:
        return self._root

    def close(self) -> None:
        pass  # no-op for file-based storage

    # ── Execution State ────────────────────────────────────────────────────

    def save_execution(self, state: ExecutionState) -> None:
        sp = StatePersistence(self._root, task_id=state.task_id)
        sp.save(state)

    def load_execution(self, task_id: str) -> ExecutionState | None:
        sp = StatePersistence(self._root, task_id=task_id)
        state = sp.load()
        if state is not None:
            return state
        # Fall back to legacy flat file
        sp_legacy = StatePersistence(self._root)
        legacy = sp_legacy.load()
        if legacy and legacy.task_id == task_id:
            return legacy
        return None

    def list_executions(self) -> list[str]:
        return StatePersistence.list_executions(self._root)

    def delete_execution(self, task_id: str) -> None:
        sp = StatePersistence(self._root, task_id=task_id)
        sp.clear()

    # ── Active Task ────────────────────────────────────────────────────────

    def set_active_task(self, task_id: str) -> None:
        sp = StatePersistence(self._root, task_id=task_id)
        sp.set_active()

    def get_active_task(self) -> str | None:
        return StatePersistence.get_active_task_id(self._root)

    # ── Plans ──────────────────────────────────────────────────────────────

    def save_plan(self, plan: MachinePlan) -> None:
        from agent_baton.core.orchestration.context import ContextManager
        ctx = ContextManager(
            team_context_dir=self._root,
            task_id=plan.task_id,
        )
        ctx.write_plan(plan)

    def load_plan(self, task_id: str) -> MachinePlan | None:
        # Try task-scoped directory first
        plan_path = self._root / "executions" / task_id / "plan.json"
        if not plan_path.exists():
            plan_path = self._root / "plan.json"
        if not plan_path.exists():
            return None
        try:
            data = json.loads(plan_path.read_text(encoding="utf-8"))
            return MachinePlan.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    # ── Step/Gate/Approval Results ─────────────────────────────────────────

    def save_step_result(self, task_id: str, result: StepResult) -> None:
        state = self.load_execution(task_id)
        if state:
            state.step_results.append(result)
            self.save_execution(state)

    def save_gate_result(self, task_id: str, result: GateResult) -> None:
        state = self.load_execution(task_id)
        if state:
            state.gate_results.append(result)
            self.save_execution(state)

    def save_approval_result(self, task_id: str, result: ApprovalResult) -> None:
        state = self.load_execution(task_id)
        if state:
            state.approval_results.append(result)
            self.save_execution(state)

    def save_amendment(self, task_id: str, amendment: PlanAmendment) -> None:
        state = self.load_execution(task_id)
        if state:
            state.amendments.append(amendment)
            self.save_execution(state)

    # ── Events ─────────────────────────────────────────────────────────────

    def append_event(self, event: Event) -> None:
        ep = EventPersistence(events_dir=self._root / "events")
        ep.append(event)

    def read_events(self, task_id: str, from_seq: int = 0) -> list[Event]:
        ep = EventPersistence(events_dir=self._root / "events")
        events = ep.replay(task_id=task_id)
        return [e for e in events if e.sequence >= from_seq]

    # ── Usage ──────────────────────────────────────────────────────────────

    def log_usage(self, record: TaskUsageRecord) -> None:
        logger = UsageLogger(log_path=self._root / "usage-log.jsonl")
        logger.log(record)

    def read_usage(self, limit: int | None = None) -> list[TaskUsageRecord]:
        logger = UsageLogger(log_path=self._root / "usage-log.jsonl")
        records = logger.read_all()
        if limit:
            return records[-limit:]
        return records

    # ── Telemetry ──────────────────────────────────────────────────────────

    def log_telemetry(self, event: dict) -> None:
        t = AgentTelemetry(log_path=self._root / "telemetry.jsonl")
        t.log_event(**event)

    def read_telemetry(self, limit: int | None = None) -> list[dict]:
        t = AgentTelemetry(log_path=self._root / "telemetry.jsonl")
        events = t.read_events()
        if limit:
            return events[-limit:]
        return events

    # ── Retrospectives ─────────────────────────────────────────────────────

    def save_retrospective(self, retro: Retrospective) -> None:
        engine = RetrospectiveEngine(
            retrospectives_dir=self._root / "retrospectives"
        )
        engine.save(retro)

    def load_retrospective(self, task_id: str) -> Retrospective | None:
        retro_path = self._root / "retrospectives" / f"{task_id}.json"
        if not retro_path.exists():
            return None
        try:
            data = json.loads(retro_path.read_text(encoding="utf-8"))
            return Retrospective.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def list_retrospective_ids(self, limit: int = 100) -> list[str]:
        engine = RetrospectiveEngine(
            retrospectives_dir=self._root / "retrospectives"
        )
        paths = engine.list_json_files()[-limit:]
        return [p.stem for p in paths]

    # ── Traces ─────────────────────────────────────────────────────────────

    def save_trace(self, trace: TaskTrace) -> None:
        recorder = TraceRecorder(team_context_root=self._root)
        recorder.complete_trace(trace, outcome="SHIP")

    def load_trace(self, task_id: str) -> TaskTrace | None:
        recorder = TraceRecorder(team_context_root=self._root)
        return recorder.load_trace(task_id)

    # ── Patterns & Budget ──────────────────────────────────────────────────

    def save_patterns(self, patterns: list) -> None:
        path = self._root / "learned-patterns.json"
        self._root.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([p.to_dict() for p in patterns], indent=2),
            encoding="utf-8",
        )

    def load_patterns(self) -> list:
        from agent_baton.models.pattern import LearnedPattern
        path = self._root / "learned-patterns.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [LearnedPattern.from_dict(p) for p in data]
        except (json.JSONDecodeError, KeyError):
            return []

    def save_budget_recommendations(self, recs: list) -> None:
        path = self._root / "budget-recommendations.json"
        self._root.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([r.to_dict() for r in recs], indent=2),
            encoding="utf-8",
        )

    def load_budget_recommendations(self) -> list:
        from agent_baton.models.budget import BudgetRecommendation
        path = self._root / "budget-recommendations.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return [BudgetRecommendation.from_dict(r) for r in data]
        except (json.JSONDecodeError, KeyError):
            return []

    # ── Mission Log ────────────────────────────────────────────────────────

    def append_mission_log(self, task_id: str, entry: MissionLogEntry) -> None:
        from agent_baton.core.orchestration.context import ContextManager
        ctx = ContextManager(team_context_dir=self._root, task_id=task_id)
        ctx.append_to_mission_log(entry)

    def read_mission_log(self, task_id: str) -> str | None:
        from agent_baton.core.orchestration.context import ContextManager
        ctx = ContextManager(team_context_dir=self._root, task_id=task_id)
        return ctx.read_mission_log()

    # ── Shared Context & Profile ───────────────────────────────────────────

    def save_context(self, task_id: str, content: str, **sections: str) -> None:
        from agent_baton.core.orchestration.context import ContextManager
        ctx = ContextManager(team_context_dir=self._root, task_id=task_id)
        ctx.write_context(task=sections.get("task", ""), **sections)

    def read_context(self, task_id: str) -> str | None:
        from agent_baton.core.orchestration.context import ContextManager
        ctx = ContextManager(team_context_dir=self._root, task_id=task_id)
        return ctx.read_context()

    def save_profile(self, content: str) -> None:
        from agent_baton.core.orchestration.context import ContextManager
        ctx = ContextManager(team_context_dir=self._root)
        ctx.write_profile(content)

    def read_profile(self) -> str | None:
        from agent_baton.core.orchestration.context import ContextManager
        ctx = ContextManager(team_context_dir=self._root)
        return ctx.read_profile()
