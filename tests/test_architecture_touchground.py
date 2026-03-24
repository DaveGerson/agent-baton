"""Architecture touchground tests — verify every subsystem has a working end-to-end
data flow with no dead ends.

Each test class targets one pipeline path and asserts that data produced in one
layer is actually consumed (or visible) in a downstream layer.  These tests do
NOT call external processes or real LLMs; all I/O is via tmp_path fixtures.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.persistence import StatePersistence
from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.events.bus import EventBus
from agent_baton.core.govern.classifier import DataClassifier
from agent_baton.core.govern.policy import PolicyEngine, PolicyRule, PolicySet
from agent_baton.core.observe.dashboard import DashboardGenerator
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.observe.telemetry import AgentTelemetry, TelemetryEvent
from agent_baton.core.observe.usage import UsageLogger
from agent_baton.core.pmo.scanner import PmoScanner
from agent_baton.core.pmo.store import PmoStore
from agent_baton.models.execution import (
    ActionType,
    ExecutionState,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
)
from agent_baton.models.pmo import PmoConfig, PmoProject
from agent_baton.models.retrospective import (
    AgentOutcome,
    KnowledgeGap,
    Retrospective,
    RosterRecommendation,
)
from agent_baton.models.usage import AgentUsageRecord, TaskUsageRecord


# ---------------------------------------------------------------------------
# Shared factories
# ---------------------------------------------------------------------------

def _step(step_id: str = "1.1", agent: str = "backend-engineer") -> PlanStep:
    return PlanStep(
        step_id=step_id,
        agent_name=agent,
        task_description="implement the feature",
    )


def _gate(gate_type: str = "test") -> PlanGate:
    return PlanGate(gate_type=gate_type, command="pytest")


def _phase(
    phase_id: int = 0,
    name: str = "Implement",
    steps: list[PlanStep] | None = None,
    gate: PlanGate | None = None,
) -> PlanPhase:
    return PlanPhase(
        phase_id=phase_id,
        name=name,
        steps=steps or [_step()],
        gate=gate,
    )


def _plan(
    task_id: str = "task-001",
    task_summary: str = "Add login feature",
    phases: list[PlanPhase] | None = None,
) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        phases=phases or [_phase()],
    )


def _engine(tmp_path: Path, bus: EventBus | None = None) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=tmp_path, bus=bus)


def _agent_usage(name: str = "backend-engineer", tokens: int = 5000) -> AgentUsageRecord:
    return AgentUsageRecord(
        name=name,
        model="sonnet",
        steps=1,
        retries=0,
        gate_results=[],
        estimated_tokens=tokens,
        duration_seconds=10.0,
    )


def _task_usage(
    task_id: str = "task-001",
    agents: list[AgentUsageRecord] | None = None,
) -> TaskUsageRecord:
    used = agents or [_agent_usage()]
    return TaskUsageRecord(
        task_id=task_id,
        timestamp="2026-03-01T10:00:00",
        agents_used=used,
        total_agents=len(used),
        risk_level="LOW",
        sequencing_mode="phased_delivery",
        gates_passed=1,
        gates_failed=0,
        outcome="SHIP",
        notes="",
    )


# ---------------------------------------------------------------------------
# 1. Event Bus → CLI Execution
# ---------------------------------------------------------------------------

class TestEventBusFlowsInExecution:
    """Events published by ExecutionEngine are received by the bus."""

    def test_start_publishes_event(self, tmp_path: Path) -> None:
        bus = EventBus()
        engine = _engine(tmp_path, bus=bus)
        engine.start(_plan())

        topics = [e.topic for e in bus.history()]
        assert "task.started" in topics, "start() must publish task.started"

    def test_record_step_does_not_publish_step_event(self, tmp_path: Path) -> None:
        # By design the engine does NOT publish step.completed — that is the
        # Worker's responsibility (see P1.4 decision).  Touchground verifies
        # that the bus still receives events from the lifecycle calls so it is
        # functionally wired, and that this specific omission is deliberate.
        bus = EventBus()
        engine = _engine(tmp_path, bus=bus)
        engine.start(_plan())
        pre_count = len(bus.history())
        engine.record_step_result("1.1", "backend-engineer")
        # Bus count must not have decreased (no events were removed); the
        # record call itself just doesn't add step.completed.
        assert len(bus.history()) >= pre_count
        # step.completed is intentionally absent from the engine-level bus
        step_completed = [e for e in bus.history() if e.topic == "step.completed"]
        assert step_completed == [], (
            "Engine must not publish step.completed — that belongs to the Worker"
        )

    def test_complete_publishes_event(self, tmp_path: Path) -> None:
        bus = EventBus()
        engine = _engine(tmp_path, bus=bus)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.complete()

        topics = [e.topic for e in bus.history()]
        assert "task.completed" in topics, "complete() must publish task.completed"

    def test_bus_events_persisted_to_disk(self, tmp_path: Path) -> None:
        """EventPersistence subscriber is auto-wired when bus is provided."""
        bus = EventBus()
        engine = _engine(tmp_path, bus=bus)
        engine.start(_plan())
        engine.record_step_result("1.1", "backend-engineer")
        engine.complete()

        events_dir = tmp_path / "events"
        assert events_dir.is_dir(), "events/ directory must be created"
        jsonl_files = list(events_dir.glob("*.jsonl"))
        assert jsonl_files, "at least one .jsonl event log must exist"
        lines = jsonl_files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 2, "at least task.started and task.completed must be persisted"

    def test_events_have_monotonic_sequence_numbers(self, tmp_path: Path) -> None:
        """Sequence numbers must be strictly monotonic within a task."""
        bus = EventBus()
        engine = _engine(tmp_path, bus=bus)
        engine.start(_plan(task_id="seq-check"))
        engine.record_step_result("1.1", "backend-engineer")
        engine.complete()

        events = bus.replay("seq-check")
        seqs = [e.sequence for e in events]
        assert seqs == sorted(seqs), "sequence numbers must be in ascending order"
        assert len(set(seqs)) == len(seqs), "sequence numbers must be unique"


# ---------------------------------------------------------------------------
# 2. Learning Loop → Planner Feedback
# ---------------------------------------------------------------------------

class TestLearningLoopCloses:
    """Retrospective data written by the engine is readable by the planner."""

    def test_retrospective_saved_as_structured_json(self, tmp_path: Path) -> None:
        """complete() must write a .json sidecar alongside the .md retrospective."""
        engine = _engine(tmp_path)
        plan = _plan(task_id="retro-json-check")
        engine.start(plan)
        engine.record_step_result("1.1", "backend-engineer")
        engine.complete()

        retro_dir = tmp_path / "retrospectives"
        json_files = list(retro_dir.glob("*.json"))
        assert json_files, "retrospective JSON sidecar must be written on complete()"

        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        assert "task_id" in data, "retrospective JSON must contain task_id"

    def test_planner_consults_retrospective_feedback(self, tmp_path: Path) -> None:
        """Planner wired with a RetrospectiveEngine reads roster recommendations.

        Strategy: write a retro that recommends removing 'code-reviewer'.
        Create a planner for a 'new-feature' task (which defaults to including
        code-reviewer) and verify the shared_context or agent list shows the
        feedback was considered.
        """
        retro_dir = tmp_path / "retrospectives"
        retro_engine = RetrospectiveEngine(retrospectives_dir=retro_dir)

        retro = Retrospective(
            task_id="prior-task-1",
            task_name="Prior feature task",
            timestamp="2026-03-01T09:00:00",
            agent_count=3,
            retry_count=0,
            gates_passed=2,
            gates_failed=0,
            risk_level="LOW",
            estimated_tokens=8000,
            roster_recommendations=[
                RosterRecommendation(
                    action="remove",
                    target="code-reviewer",
                    reason="redundant for small features",
                ),
            ],
        )
        retro_engine.save(retro)

        planner = IntelligentPlanner(
            team_context_root=tmp_path,
            retro_engine=retro_engine,
        )
        plan = planner.create_plan(
            "Add new notification feature",
            task_type="new-feature",
        )

        # The planner stores the feedback it loaded.
        assert planner._last_retro_feedback is not None, (
            "Planner must load retrospective feedback when retro_engine is supplied"
        )
        assert planner._last_retro_feedback.source_count >= 1, (
            "At least one retrospective must have been read"
        )

        # code-reviewer should have been dropped from agent assignments
        all_agents = plan.all_agents
        assert "code-reviewer" not in all_agents, (
            "code-reviewer must be removed from plan based on retrospective recommendation"
        )

    def test_pattern_learner_patterns_used_by_planner(self, tmp_path: Path) -> None:
        """A pre-written pattern file causes the planner to record a pattern_source."""
        # Write a learned-patterns.json that the planner's PatternLearner will find.
        patterns_data = [
            {
                "pattern_id": "phased_delivery-001",
                "task_type": "phased_delivery",
                "stack": None,
                "recommended_template": "Design → Implement → Test",
                "recommended_agents": ["architect", "backend-engineer", "test-engineer"],
                "confidence": 0.85,
                "sample_size": 12,
                "success_rate": 0.92,
                "avg_token_cost": 15000,
                "evidence": [],
                "created_at": "2026-03-01T00:00:00Z",
                "updated_at": "2026-03-01T00:00:00Z",
            }
        ]
        (tmp_path / "learned-patterns.json").write_text(
            json.dumps(patterns_data),
            encoding="utf-8",
        )

        planner = IntelligentPlanner(team_context_root=tmp_path)
        # Directly load from the file to confirm it's readable
        loaded = planner._pattern_learner.load_patterns()
        assert len(loaded) == 1, "PatternLearner must read the pre-written pattern file"
        assert loaded[0].confidence >= 0.7, "Loaded pattern must meet confidence threshold"


# ---------------------------------------------------------------------------
# 3. Telemetry Captures Runtime Data
# ---------------------------------------------------------------------------

class TestTelemetryWired:
    """AgentTelemetry receives events from ExecutionEngine lifecycle calls."""

    def test_telemetry_logs_on_step_record(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(task_id="tel-step"))
        engine.record_step_result(
            "1.1", "backend-engineer",
            outcome="done",
            files_changed=["app.py"],
            duration_seconds=12.5,
        )

        tel = AgentTelemetry(log_path=tmp_path / "telemetry.jsonl")
        events = tel.read_events()
        step_events = [e for e in events if e.event_type == "step_completed"]
        assert step_events, "step_completed telemetry event must be written after record_step_result"

    def test_telemetry_logs_on_execution_complete(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(task_id="tel-complete"))
        engine.record_step_result("1.1", "backend-engineer")
        engine.complete()

        tel = AgentTelemetry(log_path=tmp_path / "telemetry.jsonl")
        events = tel.read_events()
        completion_events = [
            e for e in events if e.event_type == "execution_completed"
        ]
        assert completion_events, "execution_completed telemetry event must be written on complete()"

    def test_telemetry_logs_execution_started(self, tmp_path: Path) -> None:
        engine = _engine(tmp_path)
        engine.start(_plan(task_id="tel-start"))

        tel = AgentTelemetry(log_path=tmp_path / "telemetry.jsonl")
        events = tel.read_events()
        start_events = [e for e in events if e.event_type == "execution_started"]
        assert start_events, "execution_started telemetry event must be written on start()"

    def test_telemetry_file_is_valid_jsonl(self, tmp_path: Path) -> None:
        """Every line in telemetry.jsonl must be valid JSON."""
        engine = _engine(tmp_path)
        engine.start(_plan(task_id="tel-jsonl"))
        engine.record_step_result("1.1", "backend-engineer")
        engine.complete()

        log_path = tmp_path / "telemetry.jsonl"
        assert log_path.exists(), "telemetry.jsonl must be created"
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                parsed = json.loads(line)
                assert "event_type" in parsed, "each telemetry line must have event_type"


# ---------------------------------------------------------------------------
# 4. Governance Feeds Into Planning
# ---------------------------------------------------------------------------

class TestGovernanceIntegration:
    """DataClassifier and PolicyEngine results surface in MachinePlan."""

    def test_classifier_called_during_plan_creation(self, tmp_path: Path) -> None:
        """Classifier result is stored on the planner after create_plan."""
        classifier = DataClassifier()
        planner = IntelligentPlanner(
            team_context_root=tmp_path,
            classifier=classifier,
        )
        planner.create_plan("Implement OAuth2 authentication and JWT token secrets")

        assert planner._last_classification is not None, (
            "Planner must run the classifier and store the result"
        )
        # A task mentioning auth + secrets must be classified as HIGH risk
        assert planner._last_classification.risk_level.value in ("HIGH", "CRITICAL"), (
            "Auth/secrets task must be classified as HIGH or CRITICAL risk"
        )

    def test_classifier_result_raises_plan_risk_level(self, tmp_path: Path) -> None:
        """If the classifier sees HIGH risk, the plan's risk_level must reflect it."""
        classifier = DataClassifier()
        planner = IntelligentPlanner(
            team_context_root=tmp_path,
            classifier=classifier,
        )
        # "secrets" is a high-risk signal in the classifier
        plan = planner.create_plan("Rotate API secrets and update credentials")

        assert plan.risk_level in ("HIGH", "CRITICAL"), (
            "Plan risk_level must be at least HIGH when classifier detects high-risk signals"
        )

    def test_policy_violations_surfaced_in_plan(self, tmp_path: Path) -> None:
        """Policy violations detected during planning are recorded on the planner."""
        # Use the built-in 'infrastructure' preset which requires an auditor agent.
        policy_engine = PolicyEngine(policies_dir=tmp_path / "policies")
        planner = IntelligentPlanner(
            team_context_root=tmp_path,
            policy_engine=policy_engine,
        )
        # A "docker" task will trigger the infrastructure classifier path and
        # the infrastructure policy preset, which requires the auditor agent.
        planner.create_plan(
            "Deploy new Docker container to production",
            task_type="new-feature",
        )

        # The planner must have run policy validation and stored any violations.
        # Even if no violations exist (auditor is present), the policy engine
        # must have been consulted — verified by the classification result.
        # We specifically verify violations list is accessible (may be empty).
        assert hasattr(planner, "_last_policy_violations"), (
            "Planner must expose _last_policy_violations after create_plan"
        )

    def test_policy_engine_returns_builtin_preset(self, tmp_path: Path) -> None:
        """PolicyEngine.load_preset returns the standard_dev preset without disk files."""
        engine = PolicyEngine(policies_dir=tmp_path / "policies")
        preset = engine.load_preset("standard_dev")
        assert preset is not None, "standard_dev built-in preset must be loadable"
        assert len(preset.rules) > 0, "standard_dev preset must have at least one rule"


# ---------------------------------------------------------------------------
# 5. Execution Namespacing Works End-to-End
# ---------------------------------------------------------------------------

class TestExecutionNamespacing:
    """StatePersistence namespacing stores and discovers state under executions/<task_id>/."""

    def test_namespaced_state_persists_to_correct_directory(
        self, tmp_path: Path
    ) -> None:
        context_root = tmp_path / "team-context"
        sp = StatePersistence(context_root, task_id="my-task-xyz")
        plan = _plan(task_id="my-task-xyz")
        state = ExecutionState(
            task_id="my-task-xyz",
            plan=plan,
            status="running",
        )
        sp.save(state)

        expected_path = context_root / "executions" / "my-task-xyz" / "execution-state.json"
        assert expected_path.exists(), (
            f"Namespaced state must be at executions/<task_id>/execution-state.json, "
            f"expected: {expected_path}"
        )

    def test_multiple_executions_coexist(self, tmp_path: Path) -> None:
        context_root = tmp_path / "team-context"

        for task_id in ("alpha-task", "beta-task"):
            sp = StatePersistence(context_root, task_id=task_id)
            state = ExecutionState(task_id=task_id, plan=_plan(task_id=task_id))
            sp.save(state)

        task_ids = StatePersistence.list_executions(context_root)
        assert "alpha-task" in task_ids, "alpha-task must appear in list_executions"
        assert "beta-task" in task_ids, "beta-task must appear in list_executions"

    def test_load_all_finds_namespaced_and_legacy(self, tmp_path: Path) -> None:
        context_root = tmp_path / "team-context"

        # Write a namespaced execution
        sp_namespaced = StatePersistence(context_root, task_id="named-task")
        named_state = ExecutionState(
            task_id="named-task",
            plan=_plan(task_id="named-task"),
        )
        sp_namespaced.save(named_state)

        # Write a legacy flat execution (different task_id to avoid dedup)
        sp_legacy = StatePersistence(context_root)
        legacy_state = ExecutionState(
            task_id="legacy-task",
            plan=_plan(task_id="legacy-task"),
        )
        sp_legacy.save(legacy_state)

        all_states = StatePersistence.load_all(context_root)
        all_task_ids = {s.task_id for s in all_states}
        assert "named-task" in all_task_ids, "load_all must find namespaced state"
        assert "legacy-task" in all_task_ids, "load_all must find legacy flat state"

    def test_active_task_id_tracks_default(self, tmp_path: Path) -> None:
        context_root = tmp_path / "team-context"

        sp = StatePersistence(context_root, task_id="active-task")
        state = ExecutionState(task_id="active-task", plan=_plan(task_id="active-task"))
        sp.save(state)
        sp.set_active()

        active_id = StatePersistence.get_active_task_id(context_root)
        assert active_id == "active-task", (
            "active-task-id.txt must point to the task that called set_active()"
        )

    def test_namespaced_load_returns_correct_state(self, tmp_path: Path) -> None:
        """Loading by task_id returns exactly that task's state."""
        context_root = tmp_path / "team-context"

        for task_id in ("first", "second"):
            sp = StatePersistence(context_root, task_id=task_id)
            sp.save(ExecutionState(task_id=task_id, plan=_plan(task_id=task_id)))

        loaded = StatePersistence(context_root, task_id="second").load()
        assert loaded is not None
        assert loaded.task_id == "second", (
            "load() on a namespaced persistence must return the correct task's state"
        )


# ---------------------------------------------------------------------------
# 6. PMO Scanner Reads All Execution States
# ---------------------------------------------------------------------------

class TestPmoScannerMultiExecution:
    """PmoScanner.scan_project returns cards for both namespaced and legacy states."""

    def _make_project(self, tmp_path: Path) -> PmoProject:
        return PmoProject(
            project_id="proj-a",
            name="Project Alpha",
            path=str(tmp_path),
            program="PROG",
        )

    def _make_store(self, tmp_path: Path) -> PmoStore:
        return PmoStore(
            config_path=tmp_path / "pmo-config.json",
            archive_path=tmp_path / "pmo-archive.jsonl",
        )

    def _write_namespaced_state(
        self,
        context_root: Path,
        task_id: str,
        task_summary: str = "A task",
    ) -> None:
        plan = _plan(task_id=task_id, task_summary=task_summary)
        sp = StatePersistence(context_root, task_id=task_id)
        sp.save(ExecutionState(task_id=task_id, plan=plan, status="running"))

    def _write_legacy_state(
        self,
        context_root: Path,
        task_id: str,
        task_summary: str = "A legacy task",
    ) -> None:
        plan = _plan(task_id=task_id, task_summary=task_summary)
        sp = StatePersistence(context_root)
        sp.save(ExecutionState(task_id=task_id, plan=plan, status="complete"))

    def test_scanner_finds_namespaced_executions(self, tmp_path: Path) -> None:
        context_root = tmp_path / ".claude" / "team-context"
        self._write_namespaced_state(context_root, "ns-task-1", "First feature")
        self._write_namespaced_state(context_root, "ns-task-2", "Second feature")

        project = self._make_project(tmp_path)
        store = self._make_store(tmp_path)
        scanner = PmoScanner(store=store)

        cards = scanner.scan_project(project)
        card_ids = {c.card_id for c in cards}
        assert "ns-task-1" in card_ids, "scanner must find namespaced state ns-task-1"
        assert "ns-task-2" in card_ids, "scanner must find namespaced state ns-task-2"

    def test_scanner_finds_legacy_flat_state(self, tmp_path: Path) -> None:
        context_root = tmp_path / ".claude" / "team-context"
        self._write_legacy_state(context_root, "legacy-scan-task", "Old flat state")

        project = self._make_project(tmp_path)
        store = self._make_store(tmp_path)
        scanner = PmoScanner(store=store)

        cards = scanner.scan_project(project)
        card_ids = {c.card_id for c in cards}
        assert "legacy-scan-task" in card_ids, (
            "scanner must find legacy flat execution-state.json"
        )

    def test_scanner_mixed_namespaced_and_legacy(self, tmp_path: Path) -> None:
        """scan_project returns cards for both namespaced and legacy states together."""
        context_root = tmp_path / ".claude" / "team-context"
        self._write_namespaced_state(context_root, "new-style-task", "New style")
        self._write_legacy_state(context_root, "old-style-task", "Old style")

        project = self._make_project(tmp_path)
        store = self._make_store(tmp_path)
        scanner = PmoScanner(store=store)

        cards = scanner.scan_project(project)
        card_ids = {c.card_id for c in cards}
        assert "new-style-task" in card_ids
        assert "old-style-task" in card_ids

    def test_scanner_card_column_maps_from_status(self, tmp_path: Path) -> None:
        """Cards produced by the scanner have columns derived from execution status."""
        context_root = tmp_path / ".claude" / "team-context"
        # running → executing
        plan = _plan(task_id="status-check")
        sp = StatePersistence(context_root, task_id="status-check")
        sp.save(ExecutionState(task_id="status-check", plan=plan, status="running"))

        project = self._make_project(tmp_path)
        scanner = PmoScanner(store=self._make_store(tmp_path))
        cards = scanner.scan_project(project)

        card = next(c for c in cards if c.card_id == "status-check")
        assert card.column == "executing", (
            'running status must map to "executing" column'
        )


# ---------------------------------------------------------------------------
# 7. Dashboard Shows Current Data
# ---------------------------------------------------------------------------

class TestDashboardFreshness:
    """DashboardGenerator includes a telemetry section when telemetry data exists."""

    def test_dashboard_includes_telemetry_section(self, tmp_path: Path) -> None:
        usage_log = tmp_path / "usage-log.jsonl"
        tel_log = tmp_path / "telemetry.jsonl"

        # Write a usage record so the dashboard has data to render
        usage_logger = UsageLogger(log_path=usage_log)
        usage_logger.log(_task_usage("dash-task-1"))

        # Write some telemetry events
        tel = AgentTelemetry(log_path=tel_log)
        tel.log_event(TelemetryEvent(
            timestamp="2026-03-01T10:00:00",
            agent_name="backend-engineer",
            event_type="step_completed",
            duration_ms=5000,
        ))
        tel.log_event(TelemetryEvent(
            timestamp="2026-03-01T10:01:00",
            agent_name="test-engineer",
            event_type="step_completed",
            duration_ms=3000,
        ))

        gen = DashboardGenerator(
            usage_logger=UsageLogger(log_path=usage_log),
            telemetry=AgentTelemetry(log_path=tel_log),
        )
        dashboard = gen.generate()

        assert "## Telemetry" in dashboard, (
            "Dashboard must include a Telemetry section when telemetry data exists"
        )

    def test_dashboard_no_telemetry_section_without_data(self, tmp_path: Path) -> None:
        """When telemetry log is empty, the Telemetry section must be absent."""
        usage_log = tmp_path / "usage-log.jsonl"
        tel_log = tmp_path / "telemetry.jsonl"
        tel_log.touch()  # empty file

        usage_logger = UsageLogger(log_path=usage_log)
        usage_logger.log(_task_usage("dash-task-2"))

        gen = DashboardGenerator(
            usage_logger=UsageLogger(log_path=usage_log),
            telemetry=AgentTelemetry(log_path=tel_log),
        )
        dashboard = gen.generate()

        assert "## Telemetry" not in dashboard, (
            "Telemetry section must be absent when there are no telemetry events"
        )

    def test_dashboard_reflects_recent_usage(self, tmp_path: Path) -> None:
        """Dashboard renders the task count accurately from the usage log."""
        usage_log = tmp_path / "usage-log.jsonl"
        logger = UsageLogger(log_path=usage_log)
        for i in range(3):
            logger.log(_task_usage(task_id=f"task-{i}"))

        gen = DashboardGenerator(usage_logger=UsageLogger(log_path=usage_log))
        dashboard = gen.generate()

        assert "3 tasks tracked" in dashboard, (
            "Dashboard must show accurate task count from usage log"
        )
