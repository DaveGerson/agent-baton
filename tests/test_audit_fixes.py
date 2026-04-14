"""Tests for changes introduced during the audit fix cycle.

Covers:
1. Silent failures → logging  (persistence, router, registry, policy)
2. Learning loop closure       (ImprovementLoop.run_cycle calls refresh / save_recommendations)
3. CLI hardening               (main() top-level error handler)
4. Sync trigger fix            (push trigger kwarg, auto_sync_current_project passes "auto")
5. Step-level domain events    (payload fields, "interrupted" does NOT emit)
"""
from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. Silent failures → logging
# ---------------------------------------------------------------------------


class TestPersistenceWarnsOnCorruptedState:
    """StatePersistence.load() must warn (not swallow silently) on bad JSON."""

    def test_corrupted_state_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from agent_baton.core.engine.persistence import StatePersistence

        sp = StatePersistence(tmp_path)
        # Write deliberately malformed JSON
        (tmp_path / "execution-state.json").write_text(
            "{ this is not valid json !!!", encoding="utf-8"
        )

        with caplog.at_level(logging.WARNING, logger="agent_baton.core.engine.persistence"):
            result = sp.load()

        assert result is None
        warning_texts = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "Corrupted" in warning_texts or "unreadable" in warning_texts

    def test_corrupted_state_returns_none_not_exception(self, tmp_path: Path) -> None:
        from agent_baton.core.engine.persistence import StatePersistence

        sp = StatePersistence(tmp_path)
        (tmp_path / "execution-state.json").write_text(
            '{"status": "running", "missing_required_key": true}',
            encoding="utf-8",
        )

        # Should return None rather than raising
        result = sp.load()
        assert result is None

    def test_missing_state_file_returns_none_silently(self, tmp_path: Path) -> None:
        """No warning expected when the file simply does not exist yet."""
        from agent_baton.core.engine.persistence import StatePersistence

        sp = StatePersistence(tmp_path)
        result = sp.load()
        assert result is None


class TestRegistryWarnsOnUnreadableFile:
    """AgentRegistry._parse_agent_file() must warn when a file cannot be read."""

    def test_unreadable_file_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from agent_baton.core.orchestration.registry import AgentRegistry

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        bad_file = agents_dir / "bad-agent.md"
        bad_file.write_text("---\nname: bad-agent\n---\nbody\n", encoding="utf-8")

        registry = AgentRegistry()

        # Patch read_text to simulate an OSError (e.g. permission denied)
        with patch.object(
            type(bad_file), "read_text", side_effect=OSError("permission denied")
        ):
            with caplog.at_level(logging.WARNING, logger="agent_baton.core.orchestration.registry"):
                result = registry._parse_agent_file(bad_file)

        assert result is None
        warning_texts = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "Failed to read" in warning_texts or "agent will not be available" in warning_texts

    def test_unreadable_file_skipped_when_loading_directory(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """load_directory() skips unreadable files and continues loading others."""
        from agent_baton.core.orchestration.registry import AgentRegistry

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        # Write a good agent and a bad agent
        good = agents_dir / "good-agent.md"
        good.write_text(
            "---\nname: good-agent\ndescription: ok\n---\nbody\n", encoding="utf-8"
        )
        bad = agents_dir / "bad-agent.md"
        bad.write_text(
            "---\nname: bad-agent\ndescription: broken\n---\nbody\n", encoding="utf-8"
        )

        registry = AgentRegistry()

        # Make read_text fail only for the bad file
        original_read = Path.read_text

        def selective_read(self_path, *args, **kwargs):
            if self_path.name == "bad-agent.md":
                raise OSError("permission denied")
            return original_read(self_path, *args, **kwargs)

        with patch.object(Path, "read_text", selective_read):
            with caplog.at_level(logging.WARNING, logger="agent_baton.core.orchestration.registry"):
                _count = registry.load_directory(agents_dir)

        # Good agent was loaded; bad was skipped
        assert registry.get("good-agent") is not None
        assert registry.get("bad-agent") is None


class TestRouterWarnsOnLearnedOverridesFailure:
    """AgentRouter.route() must warn when LearnedOverrides raises, then fall back."""

    def test_learned_overrides_failure_logs_warning_and_falls_back(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "backend-engineer--python.md").write_text(
            "---\nname: backend-engineer--python\ndescription: python be\n---\nbody\n",
            encoding="utf-8",
        )
        registry = AgentRegistry()
        registry.load_directory(agents_dir)
        router = AgentRouter(registry)

        stack = MagicMock()
        stack.language = "python"
        stack.framework = None

        # LearnedOverrides is imported inline in router.route(), so patch the
        # class in its home module — that's what the import statement resolves to.
        with patch(
            "agent_baton.core.learn.overrides.LearnedOverrides",
            side_effect=RuntimeError("overrides database corrupted"),
        ):
            with caplog.at_level(
                logging.WARNING, logger="agent_baton.core.orchestration.router"
            ):
                result = router.route("backend-engineer", stack=stack)

        # Should still return a valid agent name (fallback to FLAVOR_MAP)
        assert result in ("backend-engineer--python", "backend-engineer")
        warning_texts = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "learned" in warning_texts.lower() or "override" in warning_texts.lower()


class TestPolicyWarnsOnCorruptPresetFile:
    """PolicyEngine.load_preset() must warn when the on-disk JSON is corrupt."""

    def test_corrupt_preset_file_logs_warning_and_returns_none(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from agent_baton.core.govern.policy import PolicyEngine

        policies_dir = tmp_path / "policies"
        policies_dir.mkdir()
        # Write a corrupt JSON file for a custom preset
        (policies_dir / "my_preset.json").write_text(
            "{ not valid json at all", encoding="utf-8"
        )

        engine = PolicyEngine(policies_dir=policies_dir)

        with caplog.at_level(logging.WARNING, logger="agent_baton.core.govern.policy"):
            result = engine.load_preset("my_preset")

        assert result is None
        warning_texts = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "Failed to load" in warning_texts or "policy" in warning_texts.lower()

    def test_corrupt_preset_falls_back_to_builtin(
        self, tmp_path: Path
    ) -> None:
        """A corrupt on-disk preset for a built-in name returns None (no silent fallback)."""
        from agent_baton.core.govern.policy import PolicyEngine

        policies_dir = tmp_path / "policies"
        policies_dir.mkdir()
        # Overwrite the built-in standard_dev with garbage
        (policies_dir / "standard_dev.json").write_text(
            "GARBAGE", encoding="utf-8"
        )

        engine = PolicyEngine(policies_dir=policies_dir)
        result = engine.load_preset("standard_dev")
        # Corrupt file → returns None (caller should treat as missing)
        assert result is None

    def test_valid_builtin_preset_still_loads(self, tmp_path: Path) -> None:
        """Built-in presets load normally when there is no on-disk override."""
        from agent_baton.core.govern.policy import PolicyEngine

        engine = PolicyEngine(policies_dir=tmp_path / "empty_policies")
        result = engine.load_preset("standard_dev")
        assert result is not None
        assert result.name == "standard_dev"


# ---------------------------------------------------------------------------
# 2. Learning loop closure
# ---------------------------------------------------------------------------


def _make_loop(
    tmp_path: Path,
    recommendations=None,
    learner=None,
    tuner=None,
) -> "ImprovementLoop":  # noqa: F821
    """Build an ImprovementLoop with mocked collaborators."""
    from agent_baton.core.improve.experiments import ExperimentManager
    from agent_baton.core.improve.loop import ImprovementLoop
    from agent_baton.core.improve.proposals import ProposalManager
    from agent_baton.core.improve.rollback import RollbackManager
    from agent_baton.core.improve.scoring import AgentScorecard, PerformanceScorer
    from agent_baton.core.improve.triggers import TriggerEvaluator
    from agent_baton.core.improve.vcs import AgentVersionControl
    from agent_baton.core.learn.recommender import Recommender

    improvements_dir = tmp_path / "improvements"

    triggers = MagicMock(spec=TriggerEvaluator)
    triggers.should_analyze.return_value = True
    triggers.detect_anomalies.return_value = []

    recommender = MagicMock(spec=Recommender)
    recommender.analyze.return_value = recommendations or []
    if learner is not None:
        recommender._learner = learner
    if tuner is not None:
        recommender._tuner = tuner

    scorer = MagicMock(spec=PerformanceScorer)
    scorecard = AgentScorecard(agent_name="test", times_used=5, first_pass_rate=0.8)
    scorer.score_agent.return_value = scorecard

    proposals = ProposalManager(improvements_dir)
    experiments = ExperimentManager(improvements_dir)
    vcs = AgentVersionControl(tmp_path / "agents")
    rollbacks = RollbackManager(vcs=vcs, improvements_dir=improvements_dir)

    return ImprovementLoop(
        trigger_evaluator=triggers,
        recommender=recommender,
        proposal_manager=proposals,
        experiment_manager=experiments,
        rollback_manager=rollbacks,
        scorer=scorer,
        improvements_dir=improvements_dir,
    )


class TestLearningLoopClosure:
    def test_refresh_called_when_learner_exists(self, tmp_path: Path) -> None:
        learner = MagicMock()
        loop = _make_loop(tmp_path, learner=learner)
        loop.run_cycle(force=True)
        learner.refresh.assert_called_once()

    def test_save_recommendations_called_when_tuner_exists(self, tmp_path: Path) -> None:
        tuner = MagicMock()
        loop = _make_loop(tmp_path, tuner=tuner)
        loop.run_cycle(force=True)
        tuner.save_recommendations.assert_called_once()

    def test_both_called_when_both_exist(self, tmp_path: Path) -> None:
        learner = MagicMock()
        tuner = MagicMock()
        loop = _make_loop(tmp_path, learner=learner, tuner=tuner)
        loop.run_cycle(force=True)
        learner.refresh.assert_called_once()
        tuner.save_recommendations.assert_called_once()

    def test_cycle_completes_when_refresh_raises(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        learner = MagicMock()
        learner.refresh.side_effect = RuntimeError("disk full")
        loop = _make_loop(tmp_path, learner=learner)

        with caplog.at_level(logging.WARNING, logger="agent_baton.core.improve.loop"):
            report = loop.run_cycle(force=True)

        # Cycle must complete — not propagate the exception
        assert report is not None
        assert not report.skipped
        warning_texts = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "refresh" in warning_texts.lower() or "Pattern" in warning_texts

    def test_cycle_completes_when_save_recommendations_raises(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        tuner = MagicMock()
        tuner.save_recommendations.side_effect = OSError("no space left")
        loop = _make_loop(tmp_path, tuner=tuner)

        with caplog.at_level(logging.WARNING, logger="agent_baton.core.improve.loop"):
            report = loop.run_cycle(force=True)

        assert report is not None
        assert not report.skipped
        warning_texts = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "tuner" in warning_texts.lower() or "save_recommendations" in warning_texts.lower() or "Budget" in warning_texts

    def test_cycle_completes_when_neither_exists(self, tmp_path: Path) -> None:
        """When _learner / _tuner are None, run_cycle must still complete."""
        loop = _make_loop(tmp_path, learner=None, tuner=None)
        report = loop.run_cycle(force=True)
        assert report is not None
        assert not report.skipped


# ---------------------------------------------------------------------------
# 3. CLI hardening — main() top-level error handler
# ---------------------------------------------------------------------------


def _make_cmd_module(_subparsers_adder, cmd_name: str, handler_side_effect):
    """Build a fake command module that registers a real argparse subparser.

    main() calls mod.register(sub) to create the subparser and then extracts
    the subcommand name from sp.prog.  For our fake module to work correctly
    the register() call must actually add the subparser to argparse so that
    parse_args() accepts the subcommand.
    """
    mod = MagicMock()
    mod.handler.side_effect = handler_side_effect

    def _register(sub):
        sp = sub.add_parser(cmd_name)
        return sp

    mod.register.side_effect = _register
    return mod


class TestMainErrorHandler:
    """cli/main.py catches unexpected exceptions, prints a user message, exits 1."""

    def test_exception_prints_user_friendly_message(
        self, capsys
    ) -> None:
        from agent_baton.cli.main import main

        mod = _make_cmd_module(None, "test-err-cmd", ValueError("something went wrong internally"))

        with patch("agent_baton.cli.main.discover_commands", return_value={"test-err-cmd": mod}):
            with pytest.raises(SystemExit) as exc_info:
                main(["test-err-cmd"])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ValueError" in captured.err
        assert "something went wrong internally" in captured.err

    def test_exception_hints_debug_mode(
        self, capsys, monkeypatch
    ) -> None:
        from agent_baton.cli.main import main

        monkeypatch.delenv("BATON_DEBUG", raising=False)
        mod = _make_cmd_module(None, "test-hint-cmd", RuntimeError("oops"))

        with patch("agent_baton.cli.main.discover_commands", return_value={"test-hint-cmd": mod}):
            with pytest.raises(SystemExit):
                main(["test-hint-cmd"])

        captured = capsys.readouterr()
        assert "BATON_DEBUG" in captured.err

    def test_baton_debug_shows_traceback(
        self, capsys, monkeypatch
    ) -> None:
        from agent_baton.cli.main import main

        monkeypatch.setenv("BATON_DEBUG", "1")
        mod = _make_cmd_module(None, "test-trace-cmd", ValueError("trace me"))

        with patch("agent_baton.cli.main.discover_commands", return_value={"test-trace-cmd": mod}):
            with pytest.raises(SystemExit) as exc_info:
                main(["test-trace-cmd"])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        # Full traceback should appear on stderr when BATON_DEBUG is set
        assert "Traceback" in captured.err

    def test_baton_debug_absent_no_traceback(
        self, capsys, monkeypatch
    ) -> None:
        from agent_baton.cli.main import main

        monkeypatch.delenv("BATON_DEBUG", raising=False)
        mod = _make_cmd_module(None, "test-notrace-cmd", ValueError("no trace"))

        with patch("agent_baton.cli.main.discover_commands", return_value={"test-notrace-cmd": mod}):
            with pytest.raises(SystemExit):
                main(["test-notrace-cmd"])

        captured = capsys.readouterr()
        assert "Traceback" not in captured.err

    def test_system_exit_not_caught(self) -> None:
        """SystemExit from a handler must propagate, not be swallowed."""
        from agent_baton.cli.main import main

        mod = _make_cmd_module(None, "test-exit-cmd", SystemExit(42))

        with patch("agent_baton.cli.main.discover_commands", return_value={"test-exit-cmd": mod}):
            with pytest.raises(SystemExit) as exc_info:
                main(["test-exit-cmd"])

        # Must preserve original exit code, not replace with 1
        assert exc_info.value.code == 42

    def test_keyboard_interrupt_not_caught(self) -> None:
        """KeyboardInterrupt must propagate so terminals can handle Ctrl-C."""
        from agent_baton.cli.main import main

        mod = _make_cmd_module(None, "test-ctrlc-cmd", KeyboardInterrupt())

        with patch("agent_baton.cli.main.discover_commands", return_value={"test-ctrlc-cmd": mod}):
            with pytest.raises(KeyboardInterrupt):
                main(["test-ctrlc-cmd"])


# ---------------------------------------------------------------------------
# 4. Sync trigger fix
# ---------------------------------------------------------------------------


class TestSyncTrigger:
    """push() records the trigger kwarg in sync_history; auto_sync passes 'auto'."""

    @staticmethod
    def _make_project_db(tmp_path: Path, subdir: str = "proj") -> tuple[Path, "SqliteStorage"]:  # noqa: F821
        from agent_baton.core.storage.sqlite_backend import SqliteStorage

        db_dir = tmp_path / subdir
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = db_dir / "baton.db"
        store = SqliteStorage(db_path)
        return db_path, store

    @staticmethod
    def _minimal_state(task_id: str):
        from agent_baton.models.execution import (
            ExecutionState,
            MachinePlan,
            PlanPhase,
            PlanStep,
            StepResult,
        )

        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="task",
            model="sonnet",
            deliverables=[],
            allowed_paths=[],
            context_files=[],
        )
        phase = PlanPhase(phase_id=1, name="Impl", steps=[step])
        plan = MachinePlan(
            task_id=task_id,
            task_summary="test",
            risk_level="LOW",
            phases=[phase],
        )
        state = ExecutionState(
            task_id=task_id,
            plan=plan,
            status="complete",
            current_phase=1,
            current_step_index=0,
            started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T01:00:00Z",
            step_results=[
                StepResult(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    status="complete",
                    outcome="done",
                    files_changed=[],
                    commit_hash="abc",
                    estimated_tokens=100,
                    duration_seconds=5.0,
                )
            ],
        )
        return state

    def test_push_records_manual_trigger_by_default(self, tmp_path: Path) -> None:
        from agent_baton.core.storage.central import CentralStore
        from agent_baton.core.storage.sync import SyncEngine

        db_path, store = self._make_project_db(tmp_path)
        store.save_execution(self._minimal_state("task-trig-001"))
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        engine.push("proj-default-trigger", db_path)

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT trigger FROM sync_history WHERE project_id = ?",
            ("proj-default-trigger",),
        )
        central.close()

        assert len(rows) == 1
        assert rows[0]["trigger"] == "manual"

    def test_push_records_explicit_trigger(self, tmp_path: Path) -> None:
        from agent_baton.core.storage.central import CentralStore
        from agent_baton.core.storage.sync import SyncEngine

        db_path, store = self._make_project_db(tmp_path, "proj2")
        store.save_execution(self._minimal_state("task-trig-002"))
        store.close()

        central_path = tmp_path / "central.db"
        engine = SyncEngine(central_path)
        engine.push("proj-explicit", db_path, trigger="rebuild")

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT trigger FROM sync_history WHERE project_id = ?",
            ("proj-explicit",),
        )
        central.close()

        assert len(rows) == 1
        assert rows[0]["trigger"] == "rebuild"

    def test_auto_sync_records_auto_trigger(self, tmp_path: Path, monkeypatch) -> None:
        """auto_sync_current_project() must call push with trigger='auto'."""
        from agent_baton.core.storage.sync import SyncEngine, auto_sync_current_project

        # Set up a real project DB and register it in central.db
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        tc_dir = project_dir / ".claude" / "team-context"
        tc_dir.mkdir(parents=True)
        db_path = tc_dir / "baton.db"

        from agent_baton.core.storage.sqlite_backend import SqliteStorage

        store = SqliteStorage(db_path)
        store.save_execution(self._minimal_state("task-auto-001"))
        store.close()

        central_path = tmp_path / "central.db"

        # Monkeypatch the default central DB path
        monkeypatch.setattr(
            "agent_baton.core.storage.sync._CENTRAL_DB_DEFAULT", central_path
        )
        monkeypatch.chdir(project_dir)

        # Register the project in central.db
        engine = SyncEngine(central_path)
        conn = engine._conn_mgr.get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO projects (project_id, name, path, program) VALUES (?, ?, ?, ?)",
            ("my-proj", "my_project", str(project_dir), "baton"),
        )
        conn.commit()

        result = auto_sync_current_project()

        assert result is not None

        from agent_baton.core.storage.central import CentralStore

        central = CentralStore(central_path)
        rows = central.query(
            "SELECT trigger FROM sync_history WHERE project_id = ?", ("my-proj",)
        )
        central.close()

        assert len(rows) >= 1
        assert rows[-1]["trigger"] == "auto"


# ---------------------------------------------------------------------------
# 5. Step-level domain events — payload fields and "interrupted" guard
# ---------------------------------------------------------------------------


def _step_event_engine(tmp_path: Path):
    """Return (engine, bus) pair with a simple one-step plan already started."""
    from agent_baton.core.engine.executor import ExecutionEngine
    from agent_baton.core.events.bus import EventBus
    from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

    bus = EventBus()
    engine = ExecutionEngine(team_context_root=tmp_path, bus=bus)
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer",
        task_description="do something",
    )
    phase = PlanPhase(phase_id=0, name="Impl", steps=[step])
    plan = MachinePlan(
        task_id="evt-task", task_summary="event test", phases=[phase]
    )
    engine.start(plan)
    return engine, bus


class TestStepDomainEventPayload:
    def test_step_completed_payload_contains_step_id(self, tmp_path: Path) -> None:
        engine, bus = _step_event_engine(tmp_path)
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete", outcome="done"
        )
        evts = [e for e in bus.replay("evt-task") if e.topic == "step.completed"]
        assert len(evts) == 1
        assert evts[0].payload["step_id"] == "1.1"

    def test_step_completed_payload_contains_agent(self, tmp_path: Path) -> None:
        engine, bus = _step_event_engine(tmp_path)
        engine.record_step_result(
            "1.1", "backend-engineer", status="complete", outcome="done"
        )
        evts = [e for e in bus.replay("evt-task") if e.topic == "step.completed"]
        assert evts[0].payload["agent_name"] == "backend-engineer"

    def test_step_completed_payload_contains_status_via_topic(
        self, tmp_path: Path
    ) -> None:
        """The event topic itself encodes the status ('step.completed')."""
        engine, bus = _step_event_engine(tmp_path)
        engine.record_step_result("1.1", "backend-engineer", status="complete")
        evts = [e for e in bus.replay("evt-task") if e.topic == "step.completed"]
        assert len(evts) == 1

    def test_step_failed_payload_contains_step_id_and_agent(
        self, tmp_path: Path
    ) -> None:
        engine, bus = _step_event_engine(tmp_path)
        engine.record_step_result(
            "1.1", "backend-engineer", status="failed", error="timeout"
        )
        evts = [e for e in bus.replay("evt-task") if e.topic == "step.failed"]
        assert len(evts) == 1
        assert evts[0].payload["step_id"] == "1.1"
        assert evts[0].payload["agent_name"] == "backend-engineer"

    def test_step_dispatched_payload_contains_step_id_and_agent(
        self, tmp_path: Path
    ) -> None:
        engine, bus = _step_event_engine(tmp_path)
        engine.mark_dispatched("1.1", "backend-engineer")
        evts = [e for e in bus.replay("evt-task") if e.topic == "step.dispatched"]
        assert len(evts) == 1
        assert evts[0].payload["step_id"] == "1.1"
        assert evts[0].payload["agent_name"] == "backend-engineer"

    def test_interrupted_status_does_not_emit_event(self, tmp_path: Path) -> None:
        """'interrupted' is not complete, failed, or dispatched — no event emitted."""
        engine, bus = _step_event_engine(tmp_path)
        engine.record_step_result("1.1", "backend-engineer", status="interrupted")

        step_topics = [
            e.topic
            for e in bus.replay("evt-task")
            if e.topic in ("step.completed", "step.failed", "step.dispatched")
        ]
        assert step_topics == [], (
            f"Expected no step domain event for 'interrupted' status, got: {step_topics}"
        )

    def test_only_one_step_event_per_record_call(self, tmp_path: Path) -> None:
        """Each record_step_result call emits exactly one step-level event."""
        engine, bus = _step_event_engine(tmp_path)
        engine.record_step_result("1.1", "backend-engineer", status="complete")

        step_events = [
            e
            for e in bus.replay("evt-task")
            if e.topic.startswith("step.")
        ]
        assert len(step_events) == 1


# ---------------------------------------------------------------------------
# 6. Governance CLI wiring — _build_policy_engine is importable and functional
# ---------------------------------------------------------------------------


class TestBuildPolicyEngine:
    """_build_policy_engine() must return a PolicyEngine and never raise."""

    def test_returns_policy_engine_instance(self) -> None:
        from agent_baton.cli.commands.execution.execute import _build_policy_engine
        from agent_baton.core.govern.policy import PolicyEngine

        engine = _build_policy_engine()
        assert engine is not None
        assert isinstance(engine, PolicyEngine)

    def test_returns_none_gracefully_on_import_error(self) -> None:
        """When the import fails, _build_policy_engine returns None without raising."""
        import importlib
        import sys

        # Temporarily break the import by inserting a sentinel that raises.
        original = sys.modules.get("agent_baton.core.govern.policy")
        sys.modules["agent_baton.core.govern.policy"] = None  # type: ignore[assignment]
        try:
            # Re-import execute to pick up a fresh function reference under the
            # monkeypatched module state.
            from agent_baton.cli.commands.execution import execute as exec_mod
            importlib.reload(exec_mod)
            result = exec_mod._build_policy_engine()
            assert result is None
        finally:
            # Restore original module state.
            if original is not None:
                sys.modules["agent_baton.core.govern.policy"] = original
            else:
                sys.modules.pop("agent_baton.core.govern.policy", None)
            importlib.reload(exec_mod)

    def test_policy_engine_has_list_presets(self) -> None:
        """The returned PolicyEngine knows the five standard presets."""
        from agent_baton.cli.commands.execution.execute import _build_policy_engine

        engine = _build_policy_engine()
        presets = engine.list_presets()
        assert "standard_dev" in presets
        assert "regulated" in presets


class TestExecutionEngineReceivesPolicyEngine:
    """ExecutionEngine constructed in the CLI start path must have a policy_engine."""

    def test_policy_engine_wired_into_engine_start(self, tmp_path: Path) -> None:
        """When a PolicyEngine is supplied, block violations inject APPROVAL actions."""
        from agent_baton.core.engine.executor import ExecutionEngine
        from agent_baton.core.govern.policy import PolicyEngine, PolicyRule, PolicySet
        from agent_baton.models.execution import (
            ActionType, MachinePlan, PlanPhase, PlanStep,
        )
        from unittest.mock import MagicMock

        # Build a policy set with one path_block rule.
        block_rule = PolicyRule(
            name="block_env",
            description="Block .env writes",
            scope="all",
            rule_type="path_block",
            pattern="**/.env",
            severity="block",
        )
        policy_set = PolicySet(name="standard_dev", rules=[block_rule])
        mock_pe = MagicMock(spec=PolicyEngine)
        mock_pe.load_preset.return_value = policy_set
        real_pe = PolicyEngine()
        mock_pe.evaluate.side_effect = real_pe.evaluate

        step = PlanStep(
            step_id="1.1",
            agent_name="backend-engineer",
            task_description="Write .env",
            model="sonnet",
            deliverables=[],
            allowed_paths=[".env"],
            context_files=[],
        )
        phase = PlanPhase(phase_id=1, name="Impl", steps=[step])
        plan = MachinePlan(
            task_id="test-policy-wiring",
            task_summary="Test task",
            risk_level="LOW",
            phases=[phase],
        )

        engine = ExecutionEngine(
            team_context_root=tmp_path,
            policy_engine=mock_pe,
        )
        action = engine.start(plan)

        # A block violation must produce APPROVAL, not DISPATCH.
        assert action.action_type == ActionType.APPROVAL
