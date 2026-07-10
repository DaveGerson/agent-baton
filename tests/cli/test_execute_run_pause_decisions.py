"""Tests for the headless-pause / durable-decision contract in ``_run_loop``.

``baton execute run`` (and, by delegation, ``baton run``) is the headless
runner ``POST /pmo/execute/{card_id}`` spawns as a subprocess.  Before this
change, an APPROVAL action with no TTY exited non-zero without recording
anything durable beyond the execution's own (already-existing)
``approval_pending`` status, and FEEDBACK/INTERACT actions had no handling
at all -- ``_run_loop`` would spin calling ``next_action()`` until
``max_steps`` was exhausted and then abort with exit code 1, indistinguishable
from a genuine failure.

These tests pin the new contract: APPROVAL/FEEDBACK/INTERACT under a
non-TTY stdin record a durable :class:`DecisionRequest` (discoverable via
the ``/decisions`` REST API and the PMO decision inbox) before pausing, and
-- if that same decision has already been resolved by the time
``_run_loop`` re-evaluates the action -- apply it directly and continue
instead of pausing again.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution.execute import _run_loop
from agent_baton.core.runtime.decisions import DecisionManager, deterministic_decision_id
from agent_baton.models.execution import ActionType


def _non_tty():
    return patch("sys.stdin.isatty", return_value=False)


class TestApprovalHeadlessPause:
    def test_first_encounter_records_durable_decision_and_exits_nonzero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture,
    ) -> None:
        mock_engine = MagicMock()
        action_dict = {
            "action_type": ActionType.APPROVAL.value,
            "phase_id": 2,
            "message": "Phase 2 requires approval",
            "approval_context": "some context",
        }

        with _non_tty(), pytest.raises(SystemExit) as exc_info:
            _run_loop(
                engine=mock_engine, launcher=None, action_dict=action_dict,
                max_steps=5, dry_run=False, model_override="sonnet",
                task_id="approve-task", context_root=tmp_path,
            )

        assert exc_info.value.code != 0
        mock_engine.record_approval_result.assert_not_called()

        dm = DecisionManager(decisions_dir=tmp_path / "decisions")
        pending = dm.pending()
        assert len(pending) == 1
        assert pending[0].decision_type == "phase_approval"
        assert pending[0].task_id == "approve-task"
        assert pending[0].request_id == deterministic_decision_id(
            "approve-task", "approval", 2,
        )

        out = capsys.readouterr().out + capsys.readouterr().err
        # (also verify via the original pinned assertions in
        # test_execute_run_resume.py — this file adds decision-record
        # coverage, not a replacement for that contract.)

    def test_repeated_pause_does_not_duplicate_decision(self, tmp_path: Path) -> None:
        mock_engine = MagicMock()
        action_dict = {
            "action_type": ActionType.APPROVAL.value,
            "phase_id": 2,
            "message": "Phase 2 requires approval",
        }

        with _non_tty():
            for _ in range(2):
                with pytest.raises(SystemExit):
                    _run_loop(
                        engine=mock_engine, launcher=None, action_dict=dict(action_dict),
                        max_steps=5, dry_run=False, model_override="sonnet",
                        task_id="approve-task-dup", context_root=tmp_path,
                    )

        dm = DecisionManager(decisions_dir=tmp_path / "decisions")
        pending = [r for r in dm.pending() if r.task_id == "approve-task-dup"]
        assert len(pending) == 1, "a re-invocation must not create a duplicate decision"

    def test_already_resolved_decision_is_applied_and_execution_continues(
        self, tmp_path: Path,
    ) -> None:
        """If a decision was already resolved (e.g. via the REST API) before
        ``_run_loop`` re-evaluates the APPROVAL action, it must apply the
        resolution directly and continue instead of pausing again."""
        request_id = deterministic_decision_id("approve-task-2", "approval", 2)
        dm = DecisionManager(decisions_dir=tmp_path / "decisions")
        from agent_baton.models.decision import DecisionRequest
        dm.request(DecisionRequest(
            request_id=request_id, task_id="approve-task-2",
            decision_type="phase_approval", summary="approve please",
            options=["approve", "reject"],
        ))
        dm.resolve(request_id, chosen_option="approve", rationale="looks good")

        mock_engine = MagicMock()
        complete_action = MagicMock()
        complete_action.to_dict.return_value = {
            "action_type": ActionType.COMPLETE.value, "summary": "done",
        }
        mock_engine.next_action.return_value = complete_action
        mock_engine.complete.return_value = "All done"

        action_dict = {
            "action_type": ActionType.APPROVAL.value,
            "phase_id": 2,
            "message": "Phase 2 requires approval",
        }

        with _non_tty():
            # Must NOT raise SystemExit -- the resolved decision lets the
            # loop proceed straight to COMPLETE.
            _run_loop(
                engine=mock_engine, launcher=None, action_dict=action_dict,
                max_steps=5, dry_run=False, model_override="sonnet",
                task_id="approve-task-2", context_root=tmp_path,
            )

        mock_engine.record_approval_result.assert_called_once_with(
            phase_id=2, result="approve", feedback="looks good",
        )


class TestFeedbackHeadlessPause:
    def test_records_durable_decision_and_exits_nonzero(
        self, tmp_path: Path,
    ) -> None:
        mock_engine = MagicMock()
        action_dict = {
            "action_type": ActionType.FEEDBACK.value,
            "phase_id": 3,
            "message": "Pick a layout",
            "feedback_questions": [
                {"question_id": "q1", "question": "Which layout?", "options": ["Grid", "List"]},
            ],
        }

        with _non_tty(), pytest.raises(SystemExit) as exc_info:
            _run_loop(
                engine=mock_engine, launcher=None, action_dict=action_dict,
                max_steps=5, dry_run=False, model_override="sonnet",
                task_id="feedback-task", context_root=tmp_path,
            )

        assert exc_info.value.code != 0
        mock_engine.record_feedback_result.assert_not_called()

        dm = DecisionManager(decisions_dir=tmp_path / "decisions")
        pending = dm.pending()
        assert len(pending) == 1
        assert pending[0].decision_type == "feedback_response"
        assert pending[0].options == ["0", "1"]

    def test_already_resolved_decision_is_applied(self, tmp_path: Path) -> None:
        request_id = deterministic_decision_id("feedback-task-2", "feedback", 3, "q1")
        dm = DecisionManager(decisions_dir=tmp_path / "decisions")
        from agent_baton.models.decision import DecisionRequest
        dm.request(DecisionRequest(
            request_id=request_id, task_id="feedback-task-2",
            decision_type="feedback_response", summary="pick",
            options=["0", "1"],
        ))
        dm.resolve(request_id, chosen_option="1")

        mock_engine = MagicMock()
        complete_action = MagicMock()
        complete_action.to_dict.return_value = {"action_type": ActionType.COMPLETE.value, "summary": "done"}
        mock_engine.next_action.return_value = complete_action

        action_dict = {
            "action_type": ActionType.FEEDBACK.value,
            "phase_id": 3,
            "feedback_questions": [
                {"question_id": "q1", "question": "Which layout?", "options": ["Grid", "List"]},
            ],
        }

        with _non_tty():
            _run_loop(
                engine=mock_engine, launcher=None, action_dict=action_dict,
                max_steps=5, dry_run=False, model_override="sonnet",
                task_id="feedback-task-2", context_root=tmp_path,
            )

        mock_engine.record_feedback_result.assert_called_once_with(
            phase_id=3, question_id="q1", chosen_index=1,
        )


class TestInteractHeadlessPause:
    def test_records_durable_decision_and_exits_nonzero(self, tmp_path: Path) -> None:
        mock_engine = MagicMock()
        action_dict = {
            "action_type": ActionType.INTERACT.value,
            "interact_step_id": "1.1",
            "message": "Agent is asking a question",
        }

        with _non_tty(), pytest.raises(SystemExit) as exc_info:
            _run_loop(
                engine=mock_engine, launcher=None, action_dict=action_dict,
                max_steps=5, dry_run=False, model_override="sonnet",
                task_id="interact-task", context_root=tmp_path,
            )

        assert exc_info.value.code != 0
        mock_engine.complete_interaction.assert_not_called()
        mock_engine.provide_interact_input.assert_not_called()

        dm = DecisionManager(decisions_dir=tmp_path / "decisions")
        pending = dm.pending()
        assert len(pending) == 1
        assert pending[0].decision_type == "interact_response"
        assert pending[0].options == ["done"]

    def test_already_resolved_done_completes_interaction(self, tmp_path: Path) -> None:
        request_id = deterministic_decision_id("interact-task-2", "interact", "1.1")
        dm = DecisionManager(decisions_dir=tmp_path / "decisions")
        from agent_baton.models.decision import DecisionRequest
        dm.request(DecisionRequest(
            request_id=request_id, task_id="interact-task-2",
            decision_type="interact_response", summary="respond",
            options=["done"],
        ))
        dm.resolve(request_id, chosen_option="done")

        mock_engine = MagicMock()
        complete_action = MagicMock()
        complete_action.to_dict.return_value = {"action_type": ActionType.COMPLETE.value, "summary": "done"}
        mock_engine.next_action.return_value = complete_action

        action_dict = {
            "action_type": ActionType.INTERACT.value,
            "interact_step_id": "1.1",
        }

        with _non_tty():
            _run_loop(
                engine=mock_engine, launcher=None, action_dict=action_dict,
                max_steps=5, dry_run=False, model_override="sonnet",
                task_id="interact-task-2", context_root=tmp_path,
            )

        mock_engine.complete_interaction.assert_called_once_with(step_id="1.1")
