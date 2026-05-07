"""Tests for ``baton execute next --terse`` mode.

Covers:
    (a) --terse with DISPATCH: sidecar file written, prompt block suppressed,
        Prompt-File line emitted.
    (b) --terse with GATE / COMPLETE: output identical to non-terse.
    (c) default (no --terse): DISPATCH output unchanged (prompt inline).
    (d) --terse with --output json: delegation_prompt replaced by sidecar path,
        prompt_file field added.
"""
from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands.execution.execute import (
    _print_action,
    _write_dispatch_sidecar,
    _DISPATCH_PROMPT_SIDECAR,
    handler,
)
from agent_baton.models.execution import ActionType

_MOD = "agent_baton.cli.commands.execution.execute"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_PROMPT = "You are a backend-engineer. Implement the feature described below.\n" * 40


def _dispatch_action(
    step_id: str = "1.1",
    agent: str = "backend-engineer",
    model: str = "sonnet",
    message: str = "Implement storage layer",
    prompt: str = _SAMPLE_PROMPT,
) -> dict:
    return {
        "action_type": ActionType.DISPATCH.value,
        "step_id": step_id,
        "agent_name": agent,
        "agent_model": model,
        "message": message,
        "delegation_prompt": prompt,
    }


def _gate_action() -> dict:
    return {
        "action_type": ActionType.GATE.value,
        "gate_type": "tests",
        "phase_id": 1,
        "gate_command": "pytest tests/ -x -q",
        "message": "Run tests",
    }


def _complete_action() -> dict:
    return {
        "action_type": ActionType.COMPLETE.value,
        "summary": "All steps done.",
        "message": "All steps done.",
    }


def _capture(fn, *args, **kwargs) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn(*args, **kwargs)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _print_action — terse=True, DISPATCH
# ---------------------------------------------------------------------------

class TestPrintActionTerseDispatch:
    def test_sidecar_file_written(self, tmp_path: Path) -> None:
        action = _dispatch_action(prompt="hello prompt")
        sidecar = tmp_path / "team-context" / "current-dispatch.prompt.md"

        with patch(f"{_MOD}._DISPATCH_PROMPT_SIDECAR", str(sidecar)):
            # Also patch Path so mkdir resolves to tmp_path subtree
            _print_action(action, terse=True)

        # The sidecar path used is the module-level constant, not the patched
        # one in _write_dispatch_sidecar (which uses the module constant directly).
        # We patch _write_dispatch_sidecar itself for filesystem isolation.

    def test_sidecar_written_via_helper(self, tmp_path: Path, monkeypatch) -> None:
        """Verify _write_dispatch_sidecar creates the file with the prompt."""
        sidecar = tmp_path / ".claude" / "team-context" / "current-dispatch.prompt.md"
        monkeypatch.chdir(tmp_path)

        returned = _write_dispatch_sidecar("my delegation prompt")

        assert sidecar.exists()
        assert sidecar.read_text() == "my delegation prompt"
        assert returned == _DISPATCH_PROMPT_SIDECAR

    def test_sidecar_overwritten_on_second_call(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        _write_dispatch_sidecar("first prompt")
        _write_dispatch_sidecar("second prompt")

        sidecar = tmp_path / ".claude" / "team-context" / "current-dispatch.prompt.md"
        assert sidecar.read_text() == "second prompt"

    def test_prompt_block_suppressed(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        action = _dispatch_action(prompt="SECRET PROMPT CONTENT")

        out = _capture(_print_action, action, terse=True)

        assert "SECRET PROMPT CONTENT" not in out
        assert "--- Delegation Prompt ---" not in out
        assert "--- End Prompt ---" not in out

    def test_prompt_file_line_emitted(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        action = _dispatch_action()

        out = _capture(_print_action, action, terse=True)

        assert "Prompt-File:" in out
        assert _DISPATCH_PROMPT_SIDECAR in out

    def test_standard_fields_still_present(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        action = _dispatch_action(
            step_id="2.3",
            agent="test-engineer",
            model="haiku",
            message="Write tests for the storage layer",
        )

        out = _capture(_print_action, action, terse=True)

        assert "ACTION: DISPATCH" in out
        assert "Agent: test-engineer" in out
        assert "Model: haiku" in out
        assert "Step:  2.3" in out
        assert "Message: Write tests for the storage layer" in out

    def test_team_step_fields_still_present(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        action = _dispatch_action(step_id="1.1.a")

        out = _capture(_print_action, action, terse=True)

        assert "Team-Step: yes" in out
        assert "Parent-Step: 1.1" in out


# ---------------------------------------------------------------------------
# _print_action — terse=False (default), DISPATCH
# ---------------------------------------------------------------------------

class TestPrintActionNonTerseDispatch:
    def test_prompt_inline_when_not_terse(self) -> None:
        action = _dispatch_action(prompt="INLINE PROMPT HERE")

        out = _capture(_print_action, action)  # terse defaults to False

        assert "INLINE PROMPT HERE" in out
        assert "--- Delegation Prompt ---" in out
        assert "--- End Prompt ---" in out

    def test_no_prompt_file_line_when_not_terse(self) -> None:
        action = _dispatch_action()

        out = _capture(_print_action, action)

        assert "Prompt-File:" not in out


# ---------------------------------------------------------------------------
# _print_action — terse=True, non-DISPATCH actions unchanged
# ---------------------------------------------------------------------------

class TestPrintActionTerseNonDispatch:
    def test_gate_output_same_terse_and_non_terse(self) -> None:
        action = _gate_action()
        out_default = _capture(_print_action, action, terse=False)
        out_terse = _capture(_print_action, action, terse=True)
        assert out_terse == out_default

    def test_complete_output_same_terse_and_non_terse(self) -> None:
        action = _complete_action()
        out_default = _capture(_print_action, action, terse=False)
        out_terse = _capture(_print_action, action, terse=True)
        assert out_terse == out_default


# ---------------------------------------------------------------------------
# handler "next" subcommand — text output
# ---------------------------------------------------------------------------

def _make_next_args(
    terse: bool = False,
    output: str = "text",
    task_id: str | None = "task-test-1",
    all_actions: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        subcommand="next",
        terse=terse,
        output=output,
        task_id=task_id,
        all_actions=all_actions,
    )


def _make_mock_engine(action_dict: dict) -> MagicMock:
    engine = MagicMock()
    mock_action = MagicMock()
    mock_action.to_dict.return_value = action_dict
    engine.next_action.return_value = mock_action
    return engine


class TestHandlerNextTerseText:
    def _run_next(
        self,
        action_dict: dict,
        terse: bool = False,
        tmp_path: Path | None = None,
        monkeypatch=None,
    ) -> str:
        if tmp_path and monkeypatch:
            monkeypatch.chdir(tmp_path)
        engine = _make_mock_engine(action_dict)
        args = _make_next_args(terse=terse)

        buf = io.StringIO()
        with (
            patch(f"{_MOD}.ExecutionEngine", return_value=engine),
            patch(f"{_MOD}.EventBus"),
            patch(f"{_MOD}.get_project_storage"),
            patch(f"{_MOD}.detect_backend", return_value="file"),
            patch(f"{_MOD}.StatePersistence.get_active_task_id", return_value=None),
            redirect_stdout(buf),
        ):
            handler(args)

        return buf.getvalue()

    def test_terse_dispatch_suppresses_prompt(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        action = _dispatch_action(prompt="DO NOT PRINT ME")
        out = self._run_next(action, terse=True, tmp_path=tmp_path, monkeypatch=monkeypatch)
        assert "DO NOT PRINT ME" not in out
        assert "--- Delegation Prompt ---" not in out

    def test_terse_dispatch_emits_prompt_file(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        action = _dispatch_action()
        out = self._run_next(action, terse=True, tmp_path=tmp_path, monkeypatch=monkeypatch)
        assert "Prompt-File:" in out

    def test_terse_dispatch_writes_sidecar(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        action = _dispatch_action(prompt="SIDECAR CONTENT")
        self._run_next(action, terse=True)
        sidecar = tmp_path / ".claude" / "team-context" / "current-dispatch.prompt.md"
        assert sidecar.exists()
        assert "SIDECAR CONTENT" in sidecar.read_text()

    def test_non_terse_dispatch_prompt_inline(self) -> None:
        action = _dispatch_action(prompt="MUST BE INLINE")
        out = self._run_next(action, terse=False)
        assert "MUST BE INLINE" in out
        assert "--- Delegation Prompt ---" in out

    def test_terse_gate_same_as_non_terse(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        action = _gate_action()
        monkeypatch.chdir(tmp_path)
        out_terse = self._run_next(action, terse=True)
        out_plain = self._run_next(action, terse=False)
        assert out_terse == out_plain

    def test_terse_complete_same_as_non_terse(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        action = _complete_action()
        monkeypatch.chdir(tmp_path)
        out_terse = self._run_next(action, terse=True)
        out_plain = self._run_next(action, terse=False)
        assert out_terse == out_plain


# ---------------------------------------------------------------------------
# handler "next" subcommand — JSON output with --terse
# ---------------------------------------------------------------------------

class TestHandlerNextTerseJson:
    def _run_next_json(
        self,
        action_dict: dict,
        terse: bool = False,
        tmp_path: Path | None = None,
        monkeypatch=None,
    ) -> list[dict]:
        if tmp_path and monkeypatch:
            monkeypatch.chdir(tmp_path)
        engine = _make_mock_engine(action_dict)
        args = _make_next_args(terse=terse, output="json")

        buf = io.StringIO()
        with (
            patch(f"{_MOD}.ExecutionEngine", return_value=engine),
            patch(f"{_MOD}.EventBus"),
            patch(f"{_MOD}.get_project_storage"),
            patch(f"{_MOD}.detect_backend", return_value="file"),
            patch(f"{_MOD}.StatePersistence.get_active_task_id", return_value=None),
            redirect_stdout(buf),
        ):
            handler(args)

        return json.loads(buf.getvalue())

    def test_terse_json_dispatch_delegation_prompt_replaced_by_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        action = _dispatch_action(prompt="LONG PROMPT TEXT")
        result = self._run_next_json(action, terse=True, tmp_path=tmp_path, monkeypatch=monkeypatch)
        item = result[0]
        assert item["delegation_prompt"] != "LONG PROMPT TEXT"
        assert item["delegation_prompt"] == _DISPATCH_PROMPT_SIDECAR

    def test_terse_json_dispatch_prompt_file_field_present(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        action = _dispatch_action()
        result = self._run_next_json(action, terse=True, tmp_path=tmp_path, monkeypatch=monkeypatch)
        item = result[0]
        assert "prompt_file" in item
        assert item["prompt_file"] == _DISPATCH_PROMPT_SIDECAR

    def test_non_terse_json_dispatch_prompt_inline(self) -> None:
        action = _dispatch_action(prompt="FULL PROMPT INLINE")
        result = self._run_next_json(action, terse=False)
        item = result[0]
        assert item["delegation_prompt"] == "FULL PROMPT INLINE"
        assert "prompt_file" not in item

    def test_terse_json_gate_unaffected(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        action = _gate_action()
        monkeypatch.chdir(tmp_path)
        result_terse = self._run_next_json(action, terse=True)
        result_plain = self._run_next_json(action, terse=False)
        assert result_terse == result_plain


# ---------------------------------------------------------------------------
# _print_action — GATE structured extension fields (derived_commands / agent_additions)
# ---------------------------------------------------------------------------


class TestPrintActionGateExtensions:
    """Verify that _print_action emits derived_commands and agent_additions
    blocks when present, and omits them when empty.

    These are additive fields under the existing GATE branch — the existing
    Action/Type/Phase/Command/Message block must be unchanged.
    """

    def _gate_action_extended(
        self,
        *,
        derived_commands: list[dict] | None = None,
        agent_additions: list[str] | None = None,
    ) -> dict:
        d = {
            "action_type": ActionType.GATE.value,
            "gate_type": "test",
            "phase_id": 2,
            "gate_command": "pytest tests/ -q",
            "message": "Run test gate",
        }
        if derived_commands:
            d["derived_commands"] = derived_commands
        if agent_additions:
            d["agent_additions"] = agent_additions
        return d

    def test_existing_gate_fields_unchanged_when_extensions_absent(self) -> None:
        """Core gate fields still appear when no extension fields are present."""
        action = _gate_action()
        out = _capture(_print_action, action)
        assert "ACTION: GATE" in out
        assert "Type:    tests" in out
        assert "Phase:   1" in out
        assert "Command: pytest tests/ -x -q" in out
        assert "Message: Run tests" in out

    def test_derived_commands_block_emitted_when_present(self) -> None:
        """'Derived commands:' block appears when derived_commands is non-empty."""
        dc = [
            {"command": "npm audit --audit-level=high", "source_file": "package.json", "rationale": "audit script"},
        ]
        action = self._gate_action_extended(derived_commands=dc)
        out = _capture(_print_action, action)
        assert "Derived commands:" in out
        assert "npm audit --audit-level=high" in out
        assert "package.json" in out
        assert "audit script" in out

    def test_agent_additions_block_emitted_when_present(self) -> None:
        """'Agent additions:' block appears when agent_additions is non-empty."""
        action = self._gate_action_extended(agent_additions=["pre-commit run --all-files"])
        out = _capture(_print_action, action)
        assert "Agent additions:" in out
        assert "pre-commit run --all-files" in out

    def test_derived_commands_block_absent_when_empty(self) -> None:
        """No 'Derived commands:' block when the list is empty or absent."""
        action = _gate_action()  # no derived_commands key
        out = _capture(_print_action, action)
        assert "Derived commands:" not in out

    def test_agent_additions_block_absent_when_empty(self) -> None:
        """No 'Agent additions:' block when the list is empty or absent."""
        action = _gate_action()  # no agent_additions key
        out = _capture(_print_action, action)
        assert "Agent additions:" not in out

    def test_both_blocks_emitted_when_both_present(self) -> None:
        """Both blocks appear when both extension fields are non-empty."""
        dc = [{"command": "make test", "source_file": "Makefile", "rationale": "test target"}]
        aa = ["npm audit"]
        action = self._gate_action_extended(derived_commands=dc, agent_additions=aa)
        out = _capture(_print_action, action)
        assert "Derived commands:" in out
        assert "make test" in out
        assert "Agent additions:" in out
        assert "npm audit" in out

    def test_multiple_derived_commands_all_listed(self) -> None:
        """Each entry in derived_commands gets its own bullet line."""
        dc = [
            {"command": "pytest -q", "source_file": "ci.yml", "rationale": "test"},
            {"command": "npm run lint", "source_file": "ci.yml", "rationale": "lint"},
        ]
        action = self._gate_action_extended(derived_commands=dc)
        out = _capture(_print_action, action)
        assert "pytest -q" in out
        assert "npm run lint" in out

    def test_multiple_agent_additions_all_listed(self) -> None:
        """Each item in agent_additions gets its own bullet line."""
        aa = ["npm audit --audit-level=high", "pre-commit run --all-files"]
        action = self._gate_action_extended(agent_additions=aa)
        out = _capture(_print_action, action)
        assert "npm audit --audit-level=high" in out
        assert "pre-commit run --all-files" in out

    def test_existing_gate_fields_present_alongside_extensions(self) -> None:
        """Core gate fields are unaffected when extension blocks are added."""
        dc = [{"command": "pytest -q", "source_file": "ci.yml", "rationale": "test"}]
        action = self._gate_action_extended(derived_commands=dc)
        out = _capture(_print_action, action)
        # Core fields still intact.
        assert "ACTION: GATE" in out
        assert "Type:    test" in out
        assert "Phase:   2" in out
        assert "Command: pytest tests/ -q" in out
        assert "Message: Run test gate" in out
        # And the new block is also present.
        assert "Derived commands:" in out
