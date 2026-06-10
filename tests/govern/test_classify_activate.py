"""Tests for ``baton classify --activate`` (Phase F hook enforcement).

Drives the handler in-process via the classify module.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_args(
    description: str,
    *,
    files: list[str] | None = None,
    activate: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(description=description, files=files, activate=activate)


def _run(
    description: str,
    *,
    files: list[str] | None = None,
    activate: bool = False,
    cwd: Path | None = None,
    monkeypatch,
    capsys,
) -> tuple[str, str]:
    from agent_baton.cli.commands.govern import classify as cmd

    if cwd:
        monkeypatch.chdir(cwd)

    cmd.handler(_make_args(description, files=files, activate=activate))
    out, err = capsys.readouterr()
    return out, err


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestActivateWritesFile:
    def test_writes_active_policy_json(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        out, _ = _run(
            "Standard development work",
            activate=True,
            cwd=tmp_path,
            monkeypatch=monkeypatch,
            capsys=capsys,
        )

        policy_file = tmp_path / ".claude" / "active-policy.json"
        assert policy_file.exists(), "active-policy.json was not created"
        data = json.loads(policy_file.read_text())
        assert "preset" in data
        assert "preset_display_name" in data
        assert "risk_level" in data
        assert "activated_at" in data
        assert data["activated_by"] == "baton classify --activate"

    def test_correct_preset_key_for_standard_task(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        _run(
            "Refactor the configuration loading module",
            activate=True,
            cwd=tmp_path,
            monkeypatch=monkeypatch,
            capsys=capsys,
        )
        data = json.loads((tmp_path / ".claude" / "active-policy.json").read_text())
        # Low-risk task → standard_dev preset key.
        assert data["preset"] == "standard_dev"


class TestActivateOverwritesStale:
    def test_overwrites_existing_active_policy(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir()
        stale = {"preset": "regulated", "activated_by": "old"}
        (dot_claude / "active-policy.json").write_text(json.dumps(stale))

        _run(
            "Write unit tests for the logging module",
            activate=True,
            cwd=tmp_path,
            monkeypatch=monkeypatch,
            capsys=capsys,
        )

        data = json.loads((dot_claude / "active-policy.json").read_text())
        assert data["activated_by"] == "baton classify --activate"


class TestRoundtripWithPolicyCheck:
    def test_activate_then_policy_check_reads_correct_preset(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """classify --activate writes a file that policy-check can read."""
        import io
        from agent_baton.cli.commands.govern import classify as classify_cmd
        from agent_baton.cli.commands.govern import policy_check as check_cmd

        monkeypatch.chdir(tmp_path)

        # Activate a standard_dev preset.
        classify_cmd.handler(_make_args("Refactor config loading", activate=True))
        capsys.readouterr()

        # A Write to .env should now be denied.
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(json.dumps({
                "tool_name": "Write",
                "tool_input": {"file_path": "/project/.env"},
                "session_id": "roundtrip",
            })),
        )
        check_cmd.handler(argparse.Namespace(agent=None, cwd=str(tmp_path)))
        out, _ = capsys.readouterr()
        data = json.loads(out)
        assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "standard_dev" in data["hookSpecificOutput"]["permissionDecisionReason"]


class TestNoActivateNoFile:
    def test_without_activate_no_file_written(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        _run(
            "Standard development work",
            activate=False,
            cwd=tmp_path,
            monkeypatch=monkeypatch,
            capsys=capsys,
        )
        assert not (tmp_path / ".claude" / "active-policy.json").exists()
