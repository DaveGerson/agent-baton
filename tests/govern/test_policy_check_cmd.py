"""Tests for ``baton policy-check`` CLI command (Phase F hook enforcement).

Drives the handler in-process by monkeypatching stdin/cwd/env.
"""
from __future__ import annotations

import argparse
import io
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(*, agent: str | None = None, cwd: str | None = None) -> argparse.Namespace:
    return argparse.Namespace(agent=agent, cwd=cwd)


def _run(
    payload: dict,
    *,
    agent: str | None = None,
    cwd: Path | None = None,
    env: dict | None = None,
    monkeypatch,
    capsys,
) -> tuple[str, str, int | None]:
    """Run policy-check handler, return (stdout, stderr, exit_code)."""
    from agent_baton.cli.commands.govern import policy_check as cmd

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)

    exit_code = None
    try:
        cmd.handler(_make_args(agent=agent, cwd=str(cwd) if cwd else None))
    except SystemExit as e:
        exit_code = int(e.code) if e.code is not None else 0

    out, err = capsys.readouterr()
    return out, err, exit_code


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBashPathExtraction:
    """Bash tool: path tokens are extracted and checked against path_block rules."""

    def test_blocked_bash_path_in_standard_dev(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """A Bash command touching .env triggers the standard_dev block."""
        # Write active-policy.json pointing to standard_dev.
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        (dot_claude / "active-policy.json").write_text(
            json.dumps({"preset": "standard_dev"}), encoding="utf-8"
        )

        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "cat /repo/.env"},
            "session_id": "s1",
        }
        out, err, ec = _run(payload, cwd=tmp_path, monkeypatch=monkeypatch, capsys=capsys)
        assert ec is None  # exit 0
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "standard_dev" in reason
        assert "block_env_files" in reason

    def test_bash_allowed_path_passes(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """A Bash command not touching any blocked path emits no deny output."""
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        (dot_claude / "active-policy.json").write_text(
            json.dumps({"preset": "standard_dev"}), encoding="utf-8"
        )

        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "pytest tests/"},
            "session_id": "s1",
        }
        out, err, ec = _run(payload, cwd=tmp_path, monkeypatch=monkeypatch, capsys=capsys)
        assert ec is None
        assert out.strip() == ""  # no deny


class TestWritePathBlocking:
    """Write tool: file_path checked against path_block rules."""

    def test_write_to_env_file_blocked(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Write to .env path is blocked under standard_dev."""
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        (dot_claude / "active-policy.json").write_text(
            json.dumps({"preset": "standard_dev"}), encoding="utf-8"
        )

        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/project/.env"},
            "session_id": "s2",
        }
        out, err, ec = _run(payload, cwd=tmp_path, monkeypatch=monkeypatch, capsys=capsys)
        assert ec is None
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert ".env" in reason or "block_env_files" in reason

    def test_write_to_allowed_path_passes(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Write to a normal source file is allowed under standard_dev."""
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        (dot_claude / "active-policy.json").write_text(
            json.dumps({"preset": "standard_dev"}), encoding="utf-8"
        )

        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/project/src/main.py"},
            "session_id": "s3",
        }
        out, err, ec = _run(payload, cwd=tmp_path, monkeypatch=monkeypatch, capsys=capsys)
        assert ec is None
        assert out.strip() == ""


class TestRegulatedPreset:
    """regulated preset: tool_restrict blocks Bash for all agents."""

    def test_regulated_denies_bash_via_tool_restrict(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        (dot_claude / "active-policy.json").write_text(
            json.dumps({"preset": "regulated"}), encoding="utf-8"
        )

        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "echo hello"},
            "session_id": "s4",
        }
        out, err, ec = _run(payload, cwd=tmp_path, monkeypatch=monkeypatch, capsys=capsys)
        assert ec is None
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "regulated" in reason
        assert "no_bash_on_data" in reason


class TestRequireAgentFiltered:
    """require_agent rules must NOT produce a deny decision."""

    def test_require_agent_rule_does_not_deny(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        # regulated has require_sme and require_auditor (require_agent rules).
        # A Write to an ordinary path should NOT be denied by those.
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        (dot_claude / "active-policy.json").write_text(
            json.dumps({"preset": "regulated"}), encoding="utf-8"
        )

        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/project/src/main.py"},
            "session_id": "s5",
        }
        out, err, ec = _run(payload, cwd=tmp_path, monkeypatch=monkeypatch, capsys=capsys)
        assert ec is None
        # If a deny JSON is emitted the only reason would be a path_block or
        # tool_restrict rule (not require_agent/require_gate).
        if out.strip():
            data = json.loads(out)
            reason = data["hookSpecificOutput"]["permissionDecisionReason"]
            assert "require_sme" not in reason
            assert "require_auditor" not in reason


class TestFailModes:
    def test_fail_open_missing_active_policy(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Missing .claude/active-policy.json falls back to standard_dev, no crash."""
        payload = {
            "tool_name": "Read",
            "tool_input": {"file_path": "/project/README.md"},
            "session_id": "s6",
        }
        out, err, ec = _run(payload, cwd=tmp_path, monkeypatch=monkeypatch, capsys=capsys)
        assert ec is None  # exit 0, no crash

    def test_fail_closed_bad_stdin_exits_2(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """BATON_POLICY_FAIL_CLOSED=1: bad stdin → exit 2."""
        from agent_baton.cli.commands.govern import policy_check as cmd

        monkeypatch.setattr("sys.stdin", io.StringIO("not json{{{"))
        monkeypatch.setenv("BATON_POLICY_FAIL_CLOSED", "1")

        with pytest.raises(SystemExit) as exc_info:
            cmd.handler(_make_args(cwd=str(tmp_path)))
        assert exc_info.value.code == 2

    def test_deny_reason_contains_preset_rule_pattern(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Deny reason includes preset key, rule name, and pattern."""
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        (dot_claude / "active-policy.json").write_text(
            json.dumps({"preset": "standard_dev"}), encoding="utf-8"
        )

        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/project/secrets/key.pem"},
            "session_id": "s7",
        }
        out, err, ec = _run(payload, cwd=tmp_path, monkeypatch=monkeypatch, capsys=capsys)
        data = json.loads(out)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "standard_dev" in reason
        assert "block_secrets_dir" in reason
        assert "secrets" in reason  # pattern contains "secrets"


class TestPlanJsonFallback:
    """When no active-policy.json exists, plan.json risk_level drives preset."""

    def test_high_risk_plan_falls_back_to_regulated(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        team_ctx = tmp_path / ".claude" / "team-context"
        team_ctx.mkdir(parents=True)
        (team_ctx / "plan.json").write_text(
            json.dumps({"risk_level": "HIGH"}), encoding="utf-8"
        )

        # Under regulated, Bash is tool-restricted → deny.
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "session_id": "s8",
        }
        out, err, ec = _run(payload, cwd=tmp_path, monkeypatch=monkeypatch, capsys=capsys)
        assert ec is None
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
