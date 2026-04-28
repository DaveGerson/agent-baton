"""Tests for the CI provider gate runner (Wave 4.1).

Covers:

- :class:`agent_baton.core.gates.ci_gate.CIGateRunner` — GitHub Actions
  polling loop, including success / failure / timeout / gh-missing /
  log-excerpt-cap behaviours.
- :func:`agent_baton.core.gates.ci_gate.parse_ci_gate_config` — JSON +
  shorthand parsing.
- :class:`agent_baton.core.engine.executor.ExecutionEngine._run_ci_gate`
  — the executor-side dispatch helper that the CLI gate handler uses.

All shell calls are mocked.  The polling loop's ``sleep_func`` is
patched to a no-op and ``time_func`` is driven by a deterministic
counter so timeout behaviour is reproducible without real wall-clock
waits.
"""
from __future__ import annotations

import json
import subprocess
from typing import Any
from unittest import mock

import pytest

from agent_baton.core.gates.ci_gate import (
    CIGateResult,
    CIGateRunner,
    LOG_EXCERPT_MAX_CHARS,
    parse_ci_gate_config,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

class _Clock:
    """Monotonic counter that advances by ``step`` on each call.

    Drives :class:`CIGateRunner` through the polling loop deterministically.
    """

    def __init__(self, *, step: float = 1.0) -> None:
        self._t = 0.0
        self._step = step

    def __call__(self) -> float:
        v = self._t
        self._t += self._step
        return v


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Build a :class:`subprocess.CompletedProcess` stand-in."""
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr,
    )


# ── parse_ci_gate_config ─────────────────────────────────────────────────────

def test_parse_ci_gate_config_shorthand():
    """A bare workflow filename becomes the workflow with default everything."""
    cfg = parse_ci_gate_config("ci.yml")
    assert cfg.workflow == "ci.yml"
    assert cfg.provider == "github"
    assert cfg.timeout_s == 600
    assert cfg.branch == "auto"


def test_parse_ci_gate_config_json_full():
    """JSON form populates every field."""
    raw = json.dumps({
        "provider": "github",
        "workflow": "release.yml",
        "timeout_s": 1200,
        "branch": "release",
        "poll_interval_s": 5,
    })
    cfg = parse_ci_gate_config(raw)
    assert cfg.workflow == "release.yml"
    assert cfg.timeout_s == 1200
    assert cfg.branch == "release"
    assert cfg.poll_interval_s == 5


def test_parse_ci_gate_config_empty_returns_default():
    cfg = parse_ci_gate_config("")
    assert cfg.workflow == "ci.yml"
    assert cfg.provider == "github"


# ── CIGateRunner: GitHub success path ───────────────────────────────────────

def test_github_gate_passes_on_success_conclusion():
    """A run that completes with conclusion=success returns passed=True."""
    runner = CIGateRunner(
        poll_interval_s=0,
        sleep_func=lambda *_: None,
        time_func=_Clock(),
    )

    list_payload = json.dumps([{
        "databaseId": 12345,
        "headSha": "abc1234567",
        "status": "in_progress",
        "conclusion": None,
        "url": "https://github.com/o/r/actions/runs/12345",
    }])
    view_payload = json.dumps({
        "status": "completed",
        "conclusion": "success",
        "url": "https://github.com/o/r/actions/runs/12345",
    })

    def fake_run(cmd, **_kwargs):
        if cmd[:3] == ["gh", "run", "list"]:
            return _completed(stdout=list_payload, returncode=0)
        if cmd[:3] == ["gh", "run", "view"]:
            return _completed(stdout=view_payload, returncode=0)
        return _completed(returncode=1)

    with mock.patch("agent_baton.core.gates.ci_gate.shutil.which", return_value="/usr/bin/gh"), \
         mock.patch("agent_baton.core.gates.ci_gate.subprocess.run", side_effect=fake_run):
        result = runner.wait_for_workflow(
            provider="github",
            workflow="ci.yml",
            branch="feat/x",
            commit_sha="abc1234567",
            timeout_s=60,
        )

    assert result.passed is True
    assert result.run_id == "12345"
    assert result.conclusion == "success"
    assert result.url.endswith("/12345")
    assert result.log_excerpt == ""  # success: no log excerpt fetched


# ── CIGateRunner: GitHub failure path ───────────────────────────────────────

def test_github_gate_fails_on_failure_conclusion():
    """A run that completes with conclusion=failure returns passed=False."""
    runner = CIGateRunner(
        poll_interval_s=0,
        sleep_func=lambda *_: None,
        time_func=_Clock(),
    )

    list_payload = json.dumps([{
        "databaseId": 999,
        "headSha": "deadbeef",
        "status": "in_progress",
        "conclusion": None,
        "url": "https://github.com/o/r/actions/runs/999",
    }])
    view_payload = json.dumps({
        "status": "completed",
        "conclusion": "failure",
        "url": "https://github.com/o/r/actions/runs/999",
    })
    failed_log = "FAILED test_foo\nAssertionError: expected 1, got 2\n"

    def fake_run(cmd, **_kwargs):
        if cmd[:3] == ["gh", "run", "list"]:
            return _completed(stdout=list_payload, returncode=0)
        if cmd[:3] == ["gh", "run", "view"]:
            if "--log-failed" in cmd:
                return _completed(stdout=failed_log, returncode=0)
            return _completed(stdout=view_payload, returncode=0)
        return _completed(returncode=1)

    with mock.patch("agent_baton.core.gates.ci_gate.shutil.which", return_value="/usr/bin/gh"), \
         mock.patch("agent_baton.core.gates.ci_gate.subprocess.run", side_effect=fake_run):
        result = runner.wait_for_workflow(
            provider="github",
            workflow="ci.yml",
            branch="feat/x",
            commit_sha="deadbeef",
            timeout_s=60,
        )

    assert result.passed is False
    assert result.conclusion == "failure"
    assert "AssertionError" in result.log_excerpt


# ── CIGateRunner: polls until run appears ───────────────────────────────────

def test_github_gate_polls_until_run_appears():
    """The runner tolerates an empty list response and retries."""
    runner = CIGateRunner(
        poll_interval_s=0,
        sleep_func=lambda *_: None,
        time_func=_Clock(),
    )

    # First two list calls return empty (run not registered yet); third
    # returns the matching run; then view returns completed/success.
    list_responses = [
        _completed(stdout="[]", returncode=0),
        _completed(stdout="[]", returncode=0),
        _completed(stdout=json.dumps([{
            "databaseId": 7,
            "headSha": "abc",
            "status": "in_progress",
            "conclusion": None,
            "url": "https://example/7",
        }]), returncode=0),
    ]
    view_response = _completed(stdout=json.dumps({
        "status": "completed",
        "conclusion": "success",
        "url": "https://example/7",
    }), returncode=0)

    call_state = {"list": 0}

    def fake_run(cmd, **_kwargs):
        if cmd[:3] == ["gh", "run", "list"]:
            r = list_responses[call_state["list"]]
            call_state["list"] += 1
            return r
        return view_response

    with mock.patch("agent_baton.core.gates.ci_gate.shutil.which", return_value="/usr/bin/gh"), \
         mock.patch("agent_baton.core.gates.ci_gate.subprocess.run", side_effect=fake_run):
        result = runner.wait_for_workflow(
            provider="github",
            workflow="ci.yml",
            branch="feat/x",
            commit_sha="abc",
            timeout_s=60,
        )

    assert result.passed is True
    assert result.run_id == "7"
    # We polled list 3 times (2 empty, 1 matching).
    assert call_state["list"] == 3


# ── CIGateRunner: timeout path ──────────────────────────────────────────────

def test_github_gate_times_out_returns_fail():
    """When no run appears within timeout_s, return passed=False conclusion=timeout."""
    # Clock advances by 10s per tick so we hit the deadline quickly.
    runner = CIGateRunner(
        poll_interval_s=0,
        sleep_func=lambda *_: None,
        time_func=_Clock(step=10.0),
    )

    def fake_run(cmd, **_kwargs):
        # Always return empty — no run ever appears.
        return _completed(stdout="[]", returncode=0)

    with mock.patch("agent_baton.core.gates.ci_gate.shutil.which", return_value="/usr/bin/gh"), \
         mock.patch("agent_baton.core.gates.ci_gate.subprocess.run", side_effect=fake_run):
        result = runner.wait_for_workflow(
            provider="github",
            workflow="ci.yml",
            branch="feat/x",
            commit_sha="abc",
            timeout_s=30,
        )

    assert result.passed is False
    assert result.conclusion == "timeout"
    assert result.run_id == ""
    assert "Timed out" in result.log_excerpt


# ── CIGateRunner: gh missing ────────────────────────────────────────────────

def test_gh_unavailable_returns_friendly_error():
    """Missing gh CLI returns passed=False with a helpful pointer."""
    runner = CIGateRunner(poll_interval_s=0, sleep_func=lambda *_: None)

    with mock.patch("agent_baton.core.gates.ci_gate.shutil.which", return_value=None):
        result = runner.wait_for_workflow(
            provider="github",
            workflow="ci.yml",
            branch="main",
            commit_sha="abc",
            timeout_s=60,
        )

    assert result.passed is False
    assert result.conclusion == "gh_unavailable"
    assert "cli.github.com" in result.log_excerpt


# ── CIGateRunner: GitLab stub ────────────────────────────────────────────────

def test_gitlab_provider_raises_not_implemented():
    """GitLab is documented as future work and raises with a clear hint."""
    runner = CIGateRunner()
    with pytest.raises(NotImplementedError, match="GitLab"):
        runner.wait_for_workflow(
            provider="gitlab",
            workflow="ci.yml",
            branch="main",
            commit_sha="abc",
            timeout_s=60,
        )


# ── ExecutionEngine: dispatches to CI runner ────────────────────────────────

def test_executor_dispatches_ci_gate_type():
    """ExecutionEngine._run_ci_gate parses config and forwards to the runner."""
    from agent_baton.core.engine.executor import ExecutionEngine

    # Build a minimal engine that we never start; we only call the helper.
    # _run_ci_gate is a pure helper that does not touch state, so we can
    # construct without bus/storage by passing a dummy team_context_root.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        engine = ExecutionEngine.__new__(ExecutionEngine)  # bypass __init__

        # Pre-built runner that returns a known result.
        sentinel = CIGateResult(
            passed=True,
            run_id="42",
            conclusion="success",
            url="https://example/42",
            duration_s=1.0,
        )

        class _StubRunner:
            calls: list[dict[str, Any]] = []

            def wait_for_workflow(self, **kwargs):
                _StubRunner.calls.append(kwargs)
                return sentinel

        result = engine._run_ci_gate(
            '{"provider": "github", "workflow": "ci.yml", "timeout_s": 90, "branch": "feat/x"}',
            commit_sha="abc1234",
            branch="feat/x",
            runner=_StubRunner(),
        )

    assert result is sentinel
    assert _StubRunner.calls[0]["provider"] == "github"
    assert _StubRunner.calls[0]["workflow"] == "ci.yml"
    assert _StubRunner.calls[0]["branch"] == "feat/x"
    assert _StubRunner.calls[0]["commit_sha"] == "abc1234"
    assert _StubRunner.calls[0]["timeout_s"] == 90


# ── log_excerpt cap ─────────────────────────────────────────────────────────

def test_log_excerpt_capped_at_500_chars():
    """Failed-run log excerpt is capped at LOG_EXCERPT_MAX_CHARS (500)."""
    runner = CIGateRunner(
        poll_interval_s=0,
        sleep_func=lambda *_: None,
        time_func=_Clock(),
    )

    # Generate an oversized failed log.
    big_log = "X" * 5000

    list_payload = json.dumps([{
        "databaseId": 1,
        "headSha": "sha",
        "status": "in_progress",
        "conclusion": None,
        "url": "u",
    }])
    view_payload = json.dumps({
        "status": "completed",
        "conclusion": "failure",
        "url": "u",
    })

    def fake_run(cmd, **_kwargs):
        if cmd[:3] == ["gh", "run", "list"]:
            return _completed(stdout=list_payload, returncode=0)
        if cmd[:3] == ["gh", "run", "view"]:
            if "--log-failed" in cmd:
                return _completed(stdout=big_log, returncode=0)
            return _completed(stdout=view_payload, returncode=0)
        return _completed(returncode=1)

    with mock.patch("agent_baton.core.gates.ci_gate.shutil.which", return_value="/usr/bin/gh"), \
         mock.patch("agent_baton.core.gates.ci_gate.subprocess.run", side_effect=fake_run):
        result = runner.wait_for_workflow(
            provider="github",
            workflow="ci.yml",
            branch="x",
            commit_sha="sha",
            timeout_s=60,
        )

    assert result.passed is False
    assert result.conclusion == "failure"
    assert len(result.log_excerpt) == LOG_EXCERPT_MAX_CHARS
    # And the cap takes the *tail* of the log (last 500 chars).
    assert result.log_excerpt == "X" * LOG_EXCERPT_MAX_CHARS
