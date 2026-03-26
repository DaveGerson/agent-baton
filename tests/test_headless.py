"""Tests for agent_baton.core.runtime.headless.

Strategy:
- shutil.which is monkeypatched so no real claude binary is needed.
- asyncio.create_subprocess_exec is replaced with FakeProcess factories
  to avoid spawning real subprocesses.
- Tests use asyncio.run(_run()) to match the project's async test convention.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_baton.core.runtime.headless import (
    HeadlessConfig,
    HeadlessClaude,
    HeadlessResult,
    _DEFAULT_ENV_PASSTHROUGH,
)
from agent_baton.models.execution import MachinePlan


# ---------------------------------------------------------------------------
# Fake process helper
# ---------------------------------------------------------------------------

class FakeProcess:
    """Minimal asyncio.subprocess.Process stand-in."""

    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._killed = False

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:  # noqa: A002
        return self._stdout, self._stderr

    def kill(self) -> None:
        self._killed = True

    async def wait(self) -> None:
        pass


def _make_fake_exec(process: FakeProcess):
    """Return an async factory that yields the given FakeProcess."""
    async def fake_exec(*args: Any, **kwargs: Any) -> FakeProcess:
        return process
    return fake_exec


# ---------------------------------------------------------------------------
# Patch helpers
# ---------------------------------------------------------------------------

def _patch_which(monkeypatch: pytest.MonkeyPatch, found: bool = True) -> None:
    monkeypatch.setattr("shutil.which", lambda x: "/usr/bin/claude" if found else None)


def _patch_subprocess(monkeypatch: pytest.MonkeyPatch, process: FakeProcess) -> None:
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _make_fake_exec(process))


# ---------------------------------------------------------------------------
# A minimal valid plan dict for roundtrip tests
# ---------------------------------------------------------------------------

_VALID_PLAN_DICT = {
    "task_id": "add-login-page",
    "task_summary": "Add a login page",
    "risk_level": "LOW",
    "budget_tier": "lean",
    "execution_mode": "phased",
    "git_strategy": "commit-per-agent",
    "phases": [
        {
            "phase_id": 1,
            "name": "Implementation",
            "steps": [
                {
                    "step_id": "1.1",
                    "agent_name": "backend-engineer",
                    "task_description": "Implement login endpoint",
                    "model": "sonnet",
                }
            ],
            "gate": {
                "gate_type": "test",
                "command": "pytest",
                "description": "Run tests",
            },
        }
    ],
}


# ===========================================================================
# HeadlessConfig
# ===========================================================================

class TestHeadlessConfig:
    def test_default_claude_path(self) -> None:
        cfg = HeadlessConfig()
        assert cfg.claude_path == "claude"

    def test_default_model(self) -> None:
        cfg = HeadlessConfig()
        assert cfg.model == "sonnet"

    def test_default_timeout(self) -> None:
        cfg = HeadlessConfig()
        assert cfg.timeout_seconds == 120.0

    def test_default_max_retries(self) -> None:
        cfg = HeadlessConfig()
        assert cfg.max_retries == 2

    def test_default_base_retry_delay(self) -> None:
        cfg = HeadlessConfig()
        assert cfg.base_retry_delay == 5.0

    def test_default_working_directory_is_none(self) -> None:
        cfg = HeadlessConfig()
        assert cfg.working_directory is None

    def test_default_env_passthrough_contains_api_key(self) -> None:
        cfg = HeadlessConfig()
        assert "ANTHROPIC_API_KEY" in cfg.env_passthrough

    def test_default_env_passthrough_matches_module_default(self) -> None:
        cfg = HeadlessConfig()
        assert set(cfg.env_passthrough) == set(_DEFAULT_ENV_PASSTHROUGH)

    def test_to_dict_roundtrip(self) -> None:
        cfg = HeadlessConfig(
            claude_path="/usr/local/bin/claude",
            model="opus",
            timeout_seconds=60.0,
            max_retries=3,
            base_retry_delay=2.0,
            working_directory=Path("/tmp/work"),
            env_passthrough=["ANTHROPIC_API_KEY", "AWS_PROFILE"],
        )
        data = cfg.to_dict()
        restored = HeadlessConfig.from_dict(data)
        assert restored.claude_path == cfg.claude_path
        assert restored.model == cfg.model
        assert restored.timeout_seconds == cfg.timeout_seconds
        assert restored.max_retries == cfg.max_retries
        assert restored.base_retry_delay == cfg.base_retry_delay
        assert restored.working_directory == cfg.working_directory
        assert restored.env_passthrough == cfg.env_passthrough

    def test_to_dict_working_directory_none(self) -> None:
        cfg = HeadlessConfig()
        data = cfg.to_dict()
        assert data["working_directory"] is None

    def test_from_dict_uses_defaults_for_missing_keys(self) -> None:
        restored = HeadlessConfig.from_dict({})
        assert restored.claude_path == "claude"
        assert restored.model == "sonnet"
        assert restored.timeout_seconds == 120.0

    def test_to_dict_working_directory_serialized_as_string(self) -> None:
        cfg = HeadlessConfig(working_directory=Path("/some/path"))
        data = cfg.to_dict()
        assert data["working_directory"] == "/some/path"
        assert isinstance(data["working_directory"], str)


# ===========================================================================
# HeadlessClaude initialization
# ===========================================================================

class TestHeadlessClaudeInit:
    def test_is_available_false_when_binary_not_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda x: None)
        hc = HeadlessClaude()
        assert hc.is_available is False

    def test_is_available_true_when_binary_found(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_which(monkeypatch, found=True)
        hc = HeadlessClaude()
        assert hc.is_available is True

    def test_custom_config_path_resolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda x: "/custom/claude" if x == "myclaude" else None)
        cfg = HeadlessConfig(claude_path="myclaude")
        hc = HeadlessClaude(config=cfg)
        assert hc.is_available is True

    def test_uses_default_config_when_none_passed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_which(monkeypatch, found=True)
        hc = HeadlessClaude(config=None)
        assert hc._config is not None
        assert hc._config.claude_path == "claude"


# ===========================================================================
# _build_plan_prompt
# ===========================================================================

class TestBuildPlanPrompt:
    def test_contains_description(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt("add login page")
        assert "add login page" in prompt

    def test_contains_priority_normal(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt("task", priority=0)
        assert "NORMAL" in prompt

    def test_contains_priority_critical(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt("task", priority=2)
        assert "CRITICAL" in prompt

    def test_contains_priority_high(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt("task", priority=1)
        assert "HIGH" in prompt

    def test_contains_project_id_when_provided(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt("task", project_id="proj-123")
        assert "proj-123" in prompt

    def test_omits_project_id_when_empty(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt("task", project_id="")
        assert "Project ID" not in prompt

    def test_contains_project_path_when_provided(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt("task", project_path="/home/user/proj")
        assert "/home/user/proj" in prompt

    def test_contains_task_type_when_provided(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt("task", task_type="bugfix")
        assert "bugfix" in prompt

    def test_omits_task_type_when_none(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt("task", task_type=None)
        assert "Task type" not in prompt

    def test_contains_available_agents(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt(
            "task", agents_available=["backend-engineer", "test-engineer"]
        )
        assert "backend-engineer" in prompt
        assert "test-engineer" in prompt

    def test_auto_detect_agents_when_none(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt("task", agents_available=None)
        assert "auto-detect" in prompt

    def test_contains_refinement_context_when_provided(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt("task", refinement_context="focus on auth")
        assert "focus on auth" in prompt
        assert "Refinement Context" in prompt

    def test_omits_refinement_section_when_empty(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt("task", refinement_context="")
        assert "Refinement Context" not in prompt

    def test_prompt_contains_json_output_schema(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt("task")
        assert "task_id" in prompt
        assert "phases" in prompt
        assert "risk_level" in prompt

    def test_all_fields_filled(self) -> None:
        prompt = HeadlessClaude._build_plan_prompt(
            description="migrate auth to OAuth",
            project_id="auth-service",
            project_path="/srv/auth",
            task_type="migration",
            priority=1,
            agents_available=["backend-engineer", "test-engineer"],
            refinement_context="keep backward compat",
        )
        assert "migrate auth to OAuth" in prompt
        assert "auth-service" in prompt
        assert "/srv/auth" in prompt
        assert "migration" in prompt
        assert "HIGH" in prompt
        assert "backend-engineer" in prompt
        assert "keep backward compat" in prompt


# ===========================================================================
# _parse_plan_output
# ===========================================================================

class TestParsePlanOutput:
    def test_parses_valid_json_into_machine_plan(self) -> None:
        output = json.dumps(_VALID_PLAN_DICT)
        plan = HeadlessClaude._parse_plan_output(output)
        assert plan is not None
        assert isinstance(plan, MachinePlan)
        assert plan.task_id == "add-login-page"

    def test_parses_json_with_leading_trailing_whitespace(self) -> None:
        output = "   " + json.dumps(_VALID_PLAN_DICT) + "\n\n"
        plan = HeadlessClaude._parse_plan_output(output)
        assert plan is not None
        assert plan.task_id == "add-login-page"

    def test_parses_markdown_fenced_json(self) -> None:
        output = "```json\n" + json.dumps(_VALID_PLAN_DICT) + "\n```"
        plan = HeadlessClaude._parse_plan_output(output)
        assert plan is not None
        assert plan.task_id == "add-login-page"

    def test_parses_plain_markdown_fence(self) -> None:
        # ``` without language specifier
        output = "```\n" + json.dumps(_VALID_PLAN_DICT) + "\n```"
        plan = HeadlessClaude._parse_plan_output(output)
        assert plan is not None
        assert plan.task_summary == "Add a login page"

    def test_parses_json_embedded_in_prose(self) -> None:
        # Valid JSON object buried in surrounding text (no fences)
        prefix = "Here is the plan:\n"
        suffix = "\nDone."
        output = prefix + json.dumps(_VALID_PLAN_DICT) + suffix
        plan = HeadlessClaude._parse_plan_output(output)
        assert plan is not None
        assert plan.task_id == "add-login-page"

    def test_returns_none_on_invalid_json(self) -> None:
        plan = HeadlessClaude._parse_plan_output("this is not json at all")
        assert plan is None

    def test_returns_none_on_empty_string(self) -> None:
        plan = HeadlessClaude._parse_plan_output("")
        assert plan is None

    def test_returns_none_on_json_missing_required_fields(self) -> None:
        # Valid JSON but missing task_id / task_summary
        output = json.dumps({"foo": "bar"})
        plan = HeadlessClaude._parse_plan_output(output)
        assert plan is None

    def test_plan_phases_parsed(self) -> None:
        output = json.dumps(_VALID_PLAN_DICT)
        plan = HeadlessClaude._parse_plan_output(output)
        assert plan is not None
        assert len(plan.phases) == 1
        assert plan.phases[0].name == "Implementation"

    def test_plan_step_parsed(self) -> None:
        output = json.dumps(_VALID_PLAN_DICT)
        plan = HeadlessClaude._parse_plan_output(output)
        assert plan is not None
        step = plan.phases[0].steps[0]
        assert step.step_id == "1.1"
        assert step.agent_name == "backend-engineer"


# ===========================================================================
# run() when not available
# ===========================================================================

class TestRunWhenNotAvailable:
    def test_returns_failure_result_when_binary_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda x: None)
        hc = HeadlessClaude()

        async def _run():
            result = await hc.run("some prompt")
            return result

        result = asyncio.run(_run())
        assert result.success is False

    def test_error_message_mentions_cli_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda x: None)
        hc = HeadlessClaude()

        async def _run():
            return await hc.run("prompt")

        result = asyncio.run(_run())
        assert result.error != ""
        assert "claude" in result.error.lower() or "not available" in result.error.lower()

    def test_output_is_empty_when_binary_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda x: None)
        hc = HeadlessClaude()

        async def _run():
            return await hc.run("prompt")

        result = asyncio.run(_run())
        assert result.output == ""

    def test_generate_plan_returns_none_when_binary_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("shutil.which", lambda x: None)
        hc = HeadlessClaude()

        async def _run():
            return await hc.generate_plan("add login page")

        plan = asyncio.run(_run())
        assert plan is None


# ===========================================================================
# run() subprocess path — successful execution
# ===========================================================================

class TestRunSubprocess:
    def test_successful_json_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_which(monkeypatch, found=True)
        response = {"result": "plan text here", "is_error": False}
        process = FakeProcess(
            stdout=json.dumps(response).encode(),
            stderr=b"",
            returncode=0,
        )
        _patch_subprocess(monkeypatch, process)

        hc = HeadlessClaude()

        async def _run():
            return await hc.run("generate a plan")

        result = asyncio.run(_run())
        assert result.success is True
        assert result.output == "plan text here"

    def test_nonzero_exit_returns_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_which(monkeypatch, found=True)
        process = FakeProcess(
            stdout=b"",
            stderr=b"some error",
            returncode=1,
        )
        _patch_subprocess(monkeypatch, process)

        hc = HeadlessClaude()

        async def _run():
            return await hc.run("prompt")

        result = asyncio.run(_run())
        assert result.success is False

    def test_json_is_error_true_returns_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_which(monkeypatch, found=True)
        response = {"result": "API error", "is_error": True}
        process = FakeProcess(
            stdout=json.dumps(response).encode(),
            stderr=b"",
            returncode=0,
        )
        _patch_subprocess(monkeypatch, process)

        hc = HeadlessClaude()

        async def _run():
            return await hc.run("prompt")

        result = asyncio.run(_run())
        assert result.success is False

    def test_raw_json_attached_on_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_which(monkeypatch, found=True)
        response = {"result": "some output", "is_error": False}
        process = FakeProcess(
            stdout=json.dumps(response).encode(),
            returncode=0,
        )
        _patch_subprocess(monkeypatch, process)

        hc = HeadlessClaude()

        async def _run():
            return await hc.run("prompt")

        result = asyncio.run(_run())
        assert result.raw_json is not None
        assert result.raw_json["result"] == "some output"

    def test_api_key_redacted_from_stderr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_which(monkeypatch, found=True)
        stderr_with_key = b"error: sk-ant-AABBCCDDEE12345 token expired"
        process = FakeProcess(
            stdout=b"",
            stderr=stderr_with_key,
            returncode=1,
        )
        _patch_subprocess(monkeypatch, process)

        hc = HeadlessClaude()

        async def _run():
            return await hc.run("prompt")

        result = asyncio.run(_run())
        assert "sk-ant-AABBCCDDEE12345" not in result.error
        assert "REDACTED" in result.error
