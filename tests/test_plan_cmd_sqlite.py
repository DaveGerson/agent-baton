"""Tests for `baton plan --save` SQLite persistence.

Covers:
- Normal generate + --save path calls _persist_plan_to_db with the plan
- --import --save path calls _persist_plan_to_db with the plan
- DB failure is non-fatal: file save still completes and a warning is logged
- Without --save, _persist_plan_to_db is never called
- Multiple distinct plans land in the DB without collision (task_id is PK)
- _persist_plan_to_db happy path: plan row appears in baton.db
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from agent_baton.cli.commands.execution import plan_cmd
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _make_minimal_plan(task_id: str = "2026-01-01-test-sqlite-aa112233") -> MachinePlan:
    """Return a minimal MachinePlan suitable for persistence tests."""
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer--python",
        task_description="Implement the thing",
        model="sonnet",
        depends_on=[],
        deliverables=[],
        allowed_paths=[],
        blocked_paths=[],
        context_files=[],
    )
    phase = PlanPhase(
        phase_id=1,
        name="Implement",
        steps=[step],
        approval_required=False,
    )
    return MachinePlan(
        task_id=task_id,
        task_summary="SQLite persistence test",
        risk_level="LOW",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[phase],
        shared_context="",
        pattern_source=None,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _make_args(
    *,
    summary: str = "do the thing",
    save: bool = True,
    explain: bool = False,
    json_flag: bool = False,
    import_path: str | None = None,
    template: bool = False,
) -> argparse.Namespace:
    """Construct a Namespace that mirrors what argparse produces for plan_cmd."""
    return argparse.Namespace(
        summary=summary,
        save=save,
        explain=explain,
        json=json_flag,
        import_path=import_path,
        template=template,
        task_type=None,
        agents=None,
        project=None,
        knowledge=[],
        knowledge_pack=[],
        intervention="low",
        model=None,
        complexity=None,
    )


def _run_handler_save(args: argparse.Namespace, plan: MachinePlan, ctx_dir: Path) -> MagicMock:
    """Run handler() for the normal generate + --save path.

    Mocks out the planner, context manager (local imports), and all
    filesystem writes that touch outside tmp_path.  Returns the mock
    that replaced ``_persist_plan_to_db`` so callers can assert on it.
    """
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = plan
    mock_planner.explain_plan.return_value = "explanation"

    mock_persist = MagicMock()

    # ContextManager is imported inside the save block; patch at its source.
    with (
        patch(
            "agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner",
            return_value=mock_planner,
        ),
        patch(
            "agent_baton.cli.commands.execution.plan_cmd.KnowledgeRegistry",
            return_value=MagicMock(),
        ),
        patch(
            "agent_baton.cli.commands.execution.plan_cmd.RetrospectiveEngine",
            return_value=MagicMock(),
        ),
        patch(
            "agent_baton.cli.commands.execution.plan_cmd.DataClassifier",
            return_value=MagicMock(),
        ),
        patch(
            "agent_baton.cli.commands.execution.plan_cmd.PolicyEngine",
            return_value=MagicMock(),
        ),
        # Redirect ctx_dir resolution so file writes land in tmp_path.
        patch(
            "agent_baton.core.orchestration.context.ContextManager",
            return_value=MagicMock(),
        ),
        patch(
            "agent_baton.cli.commands.execution.plan_cmd._persist_plan_to_db",
            mock_persist,
        ),
        # Redirect ".claude/team-context".resolve() to ctx_dir.
        patch.object(
            Path, "resolve",
            lambda self: ctx_dir if str(self) == ".claude/team-context" else Path.resolve.__wrapped__(self),  # type: ignore[attr-defined]
        ),
    ):
        plan_cmd.handler(args)

    return mock_persist


# ---------------------------------------------------------------------------
# _persist_plan_to_db unit tests (direct, no handler involvement)
# ---------------------------------------------------------------------------

class TestPersistPlanToDb:
    """Direct tests of the _persist_plan_to_db helper."""

    def test_plan_written_to_baton_db(self, tmp_path: Path) -> None:
        """Happy path: plan row appears in baton.db after the call."""
        plan = _make_minimal_plan()
        plan_cmd._persist_plan_to_db(tmp_path, plan)

        store = SqliteStorage(tmp_path / "baton.db")
        loaded = store.load_plan(plan.task_id)
        assert loaded is not None
        assert loaded.task_id == plan.task_id
        assert loaded.task_summary == plan.task_summary

    def test_db_failure_does_not_raise(self, tmp_path: Path) -> None:
        """If save_plan raises, _persist_plan_to_db swallows it silently."""
        plan = _make_minimal_plan()
        mock_storage = MagicMock()
        mock_storage.save_plan.side_effect = RuntimeError("disk full")

        # get_project_storage is imported locally inside _persist_plan_to_db;
        # patch at the source module it is imported from.
        with patch(
            "agent_baton.core.storage.get_project_storage",
            return_value=mock_storage,
        ):
            # Must not raise
            plan_cmd._persist_plan_to_db(tmp_path, plan)

    def test_db_failure_emits_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """DB failure produces a WARNING log entry."""
        plan = _make_minimal_plan()
        mock_storage = MagicMock()
        mock_storage.save_plan.side_effect = OSError("permission denied")

        with patch(
            "agent_baton.core.storage.get_project_storage",
            return_value=mock_storage,
        ):
            with caplog.at_level(
                logging.WARNING,
                logger="agent_baton.cli.commands.execution.plan_cmd",
            ):
                plan_cmd._persist_plan_to_db(tmp_path, plan)

        assert any("non-fatal" in r.message for r in caplog.records)

    def test_idempotent_double_save(self, tmp_path: Path) -> None:
        """Calling _persist_plan_to_db twice for the same plan does not raise."""
        plan = _make_minimal_plan()
        plan_cmd._persist_plan_to_db(tmp_path, plan)
        plan_cmd._persist_plan_to_db(tmp_path, plan)  # upsert — no error

        store = SqliteStorage(tmp_path / "baton.db")
        assert store.load_plan(plan.task_id) is not None

    def test_multiple_distinct_plans(self, tmp_path: Path) -> None:
        """Two plans with different task_ids both land in baton.db."""
        plan_a = _make_minimal_plan(task_id="2026-01-01-plan-a-00000001")
        plan_b = _make_minimal_plan(task_id="2026-01-01-plan-b-00000002")

        plan_cmd._persist_plan_to_db(tmp_path, plan_a)
        plan_cmd._persist_plan_to_db(tmp_path, plan_b)

        store = SqliteStorage(tmp_path / "baton.db")
        assert store.load_plan(plan_a.task_id) is not None
        assert store.load_plan(plan_b.task_id) is not None

    def test_passes_sqlite_backend_explicitly(self, tmp_path: Path) -> None:
        """_persist_plan_to_db always requests backend='sqlite', never 'file'."""
        plan = _make_minimal_plan()
        mock_storage = MagicMock()

        with patch(
            "agent_baton.core.storage.get_project_storage",
            return_value=mock_storage,
        ) as mock_factory:
            plan_cmd._persist_plan_to_db(tmp_path, plan)

        mock_factory.assert_called_once_with(tmp_path, backend="sqlite")


# ---------------------------------------------------------------------------
# handler() integration tests — normal generate + --save path
# ---------------------------------------------------------------------------

class TestHandlerSavePath:
    """handler() with --save must call _persist_plan_to_db with the right args."""

    def test_persist_called_with_correct_plan(self, tmp_path: Path) -> None:
        """_persist_plan_to_db is called once with the generated plan."""
        plan = _make_minimal_plan()
        args = _make_args(save=True)
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)

        mock_persist = MagicMock()
        mock_planner = MagicMock()
        mock_planner.create_plan.return_value = plan
        mock_planner.explain_plan.return_value = ""

        with (
            patch("agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner", return_value=mock_planner),
            patch("agent_baton.cli.commands.execution.plan_cmd.KnowledgeRegistry", return_value=MagicMock()),
            patch("agent_baton.cli.commands.execution.plan_cmd.RetrospectiveEngine", return_value=MagicMock()),
            patch("agent_baton.cli.commands.execution.plan_cmd.DataClassifier", return_value=MagicMock()),
            patch("agent_baton.cli.commands.execution.plan_cmd.PolicyEngine", return_value=MagicMock()),
            patch("agent_baton.core.orchestration.context.ContextManager", return_value=MagicMock()),
            patch("agent_baton.cli.commands.execution.plan_cmd._persist_plan_to_db", mock_persist),
            patch.object(Path, "resolve", lambda self: ctx_dir if str(self) == ".claude/team-context" else object.__getattribute__(self, "resolve")()),
        ):
            plan_cmd.handler(args)

        mock_persist.assert_called_once()
        _, called_plan = mock_persist.call_args[0]
        assert called_plan.task_id == plan.task_id

    def test_persist_not_called_without_save(self, tmp_path: Path) -> None:
        """Without --save, _persist_plan_to_db is never called."""
        plan = _make_minimal_plan()
        args = _make_args(save=False, json_flag=True)

        mock_persist = MagicMock()
        mock_planner = MagicMock()
        mock_planner.create_plan.return_value = plan

        with (
            patch("agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner", return_value=mock_planner),
            patch("agent_baton.cli.commands.execution.plan_cmd.KnowledgeRegistry", return_value=MagicMock()),
            patch("agent_baton.cli.commands.execution.plan_cmd.RetrospectiveEngine", return_value=MagicMock()),
            patch("agent_baton.cli.commands.execution.plan_cmd.DataClassifier", return_value=MagicMock()),
            patch("agent_baton.cli.commands.execution.plan_cmd.PolicyEngine", return_value=MagicMock()),
            patch("agent_baton.cli.commands.execution.plan_cmd._persist_plan_to_db", mock_persist),
        ):
            plan_cmd.handler(args)

        mock_persist.assert_not_called()


# ---------------------------------------------------------------------------
# handler() integration tests — --import --save path
# ---------------------------------------------------------------------------

class TestHandlerImportSavePath:
    """handler() with --import --save must call _persist_plan_to_db."""

    def _run_import_save(
        self,
        plan: MachinePlan,
        plan_file: Path,
        ctx_dir: Path,
        mock_persist: MagicMock,
        extra_patches: list | None = None,
    ) -> None:
        args = _make_args(save=True, import_path=str(plan_file))
        ctx_patches = [
            # ContextManager is a local import inside the save block.
            patch("agent_baton.core.orchestration.context.ContextManager", return_value=MagicMock()),
            patch("agent_baton.cli.commands.execution.plan_cmd._persist_plan_to_db", mock_persist),
            patch.object(
                Path, "resolve",
                lambda self: ctx_dir if str(self) == ".claude/team-context" else Path(str(self)),
            ),
        ]
        if extra_patches:
            ctx_patches.extend(extra_patches)

        # Apply all patches via nested context managers
        import contextlib
        with contextlib.ExitStack() as stack:
            for p in ctx_patches:
                stack.enter_context(p)
            plan_cmd.handler(args)

    def test_persist_called_with_imported_plan(self, tmp_path: Path) -> None:
        """_persist_plan_to_db is called once with the imported plan."""
        plan = _make_minimal_plan(task_id="2026-01-01-imported-aa000001")
        plan_file = tmp_path / "my-plan.json"
        plan_file.write_text(
            json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)

        mock_persist = MagicMock()
        self._run_import_save(plan, plan_file, ctx_dir, mock_persist)

        mock_persist.assert_called_once()
        _, called_plan = mock_persist.call_args[0]
        assert called_plan.task_id == plan.task_id

    def test_import_no_save_does_not_persist(self, tmp_path: Path) -> None:
        """--import without --save must not call _persist_plan_to_db."""
        plan = _make_minimal_plan(task_id="2026-01-01-imported-cc000003")
        plan_file = tmp_path / "my-plan.json"
        plan_file.write_text(
            json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        args = _make_args(save=False, import_path=str(plan_file))
        mock_persist = MagicMock()

        with patch("agent_baton.cli.commands.execution.plan_cmd._persist_plan_to_db", mock_persist):
            plan_cmd.handler(args)

        mock_persist.assert_not_called()

    def test_imported_plan_db_failure_nonfatal(self, tmp_path: Path) -> None:
        """_persist_plan_to_db swallowing an error must not abort the import."""
        plan = _make_minimal_plan(task_id="2026-01-01-imported-bb000002")
        plan_file = tmp_path / "my-plan.json"
        plan_file.write_text(
            json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        ctx_dir = tmp_path / ".claude" / "team-context"
        ctx_dir.mkdir(parents=True)

        # Simulate a _persist_plan_to_db that raises internally —
        # the exception must be caught inside _persist_plan_to_db itself.
        # We verify the handler completes without propagating the error by
        # using the real _persist_plan_to_db but injecting a failing storage.
        args = _make_args(save=True, import_path=str(plan_file))

        mock_storage = MagicMock()
        mock_storage.save_plan.side_effect = RuntimeError("disk full")

        import contextlib
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("agent_baton.core.orchestration.context.ContextManager", return_value=MagicMock())
            )
            stack.enter_context(
                patch("agent_baton.core.storage.get_project_storage", return_value=mock_storage)
            )
            stack.enter_context(
                patch.object(
                    Path, "resolve",
                    lambda self: ctx_dir if str(self) == ".claude/team-context" else Path(str(self)),
                )
            )
            # Must not raise
            plan_cmd.handler(args)
