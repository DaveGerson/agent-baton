"""Regression coverage for bd-c134.

``ExecutionEngine._load_handoff_outcome`` previously joined the recorded
``outcome_spillover_path`` to the per-task execution dir without any
traversal validation, so a spillover_path containing ``..`` segments (or
a symlink that escaped the dir) could read arbitrary files from the host.

The fix resolves the candidate path strictly, rejects anything that does
not live inside ``<root>/executions/<task_id>/``, and falls back to the
inline ``result.outcome`` instead.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import StepResult


def _engine_with_task(tmp_path: Path, task_id: str = "t-traversal") -> ExecutionEngine:
    """Return an engine wired to *tmp_path* with task/exec dirs primed."""
    engine = ExecutionEngine(team_context_root=tmp_path)
    engine._task_id = task_id  # type: ignore[attr-defined]
    engine._root = tmp_path  # type: ignore[attr-defined]
    (tmp_path / "executions" / task_id).mkdir(parents=True, exist_ok=True)
    return engine


def _result(spillover: str, inline: str = "INLINE-FALLBACK") -> StepResult:
    return StepResult(
        step_id="1.1",
        agent_name="x",
        status="completed",
        outcome=inline,
        outcome_spillover_path=spillover,
    )


class TestSpilloverPathTraversalGuard:
    def test_dotdot_traversal_falls_back_to_inline(
        self, tmp_path: Path
    ) -> None:
        secret = tmp_path / "secret.txt"
        secret.write_text("LEAKED", encoding="utf-8")
        engine = _engine_with_task(tmp_path)

        # ../../secret.txt resolves outside <tmp_path>/executions/t-traversal/.
        result = _result(spillover="../../secret.txt")

        out = engine._load_handoff_outcome(result)
        assert "LEAKED" not in out
        assert out == "INLINE-FALLBACK"

    def test_absolute_path_outside_root_falls_back(
        self, tmp_path: Path
    ) -> None:
        secret = tmp_path / "outside.txt"
        secret.write_text("CLASSIFIED", encoding="utf-8")
        engine = _engine_with_task(tmp_path)

        # An absolute path joined to a base resolves to the absolute path
        # itself in Python — guard must still reject because it escapes.
        result = _result(spillover=str(secret))
        out = engine._load_handoff_outcome(result)

        assert "CLASSIFIED" not in out
        assert out == "INLINE-FALLBACK"

    def test_symlink_escape_falls_back(self, tmp_path: Path) -> None:
        secret = tmp_path / "real-secret.txt"
        secret.write_text("TOP-SECRET", encoding="utf-8")
        engine = _engine_with_task(tmp_path)
        link = tmp_path / "executions" / "t-traversal" / "evil-link"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unsupported")

        result = _result(spillover="evil-link")
        out = engine._load_handoff_outcome(result)
        assert "TOP-SECRET" not in out
        assert out == "INLINE-FALLBACK"

    def test_legitimate_inside_path_still_returned(self, tmp_path: Path) -> None:
        engine = _engine_with_task(tmp_path)
        legit = tmp_path / "executions" / "t-traversal" / "out.txt"
        legit.write_text("HELLO", encoding="utf-8")

        result = _result(spillover="out.txt", inline="should-not-be-used")
        out = engine._load_handoff_outcome(result)
        assert out == "HELLO"

    def test_missing_file_falls_back_silently(self, tmp_path: Path) -> None:
        engine = _engine_with_task(tmp_path)
        result = _result(spillover="never-existed.txt")
        out = engine._load_handoff_outcome(result)
        assert out == "INLINE-FALLBACK"

    def test_directory_target_falls_back(self, tmp_path: Path) -> None:
        engine = _engine_with_task(tmp_path)
        sub = tmp_path / "executions" / "t-traversal" / "subdir"
        sub.mkdir()
        result = _result(spillover="subdir")
        out = engine._load_handoff_outcome(result)
        assert out == "INLINE-FALLBACK"
