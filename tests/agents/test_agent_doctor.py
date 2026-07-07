"""Tests for ``baton agents doctor``."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


def _run_cli(argv: list[str]) -> int:
    from agent_baton.cli.main import main

    try:
        main(argv)
        return 0
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0


def _isolate_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate global (~/.claude) discovery so only bundled + this project's
    agents are in play. Bundled agents (installed with the package) are
    always in scope -- ``AgentRegistry`` loads them via importlib.resources,
    independent of HOME/cwd -- so tests assert on a specific fixture agent's
    issues rather than on the report being entirely clean.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(tmp_path)


def _write_agent(path: Path, frontmatter: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "---\n" + yaml.safe_dump(frontmatter, sort_keys=False) + "---\n" + body
    path.write_text(text, encoding="utf-8")


def _issues_for(payload: dict, agent: str) -> list[dict]:
    return [issue for issue in payload["issues"] if issue["agent"] == agent]


_CLEAN_BODY = """\
# Test Clean Agent

## Mission

You are a focused specialist that reads and reports.

## Before Starting

1. Read this entire agent definition.

## Principles

- Stay inside the role boundary.

## Output Format

Return a summary of findings.
"""


def test_clean_contract_compliant_agent_reports_no_issues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    _write_agent(
        tmp_path / ".claude" / "agents" / "test-clean-agent.md",
        {
            "name": "test-clean-agent",
            "description": (
                "Specialist for validating clean-agent fixtures in the doctor\n"
                "test suite. Use when testing the happy path only."
            ),
            "model": "sonnet",
            "permissionMode": "default",
            "tools": "Read, Glob, Grep",
        },
        _CLEAN_BODY,
    )

    rc = _run_cli(["agents", "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert _issues_for(payload, "test-clean-agent") == []


def test_missing_required_field_is_error_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    # No 'tools' or 'permissionMode' field -- violates the Phase 1
    # generated-agent contract even though AgentRegistry happily loads it.
    # `created_by: talent-builder` marks this as a *generated* agent, which
    # is what makes the omission an error rather than a warning (see
    # test_hand_authored_agent_missing_tools_is_warning_not_error below).
    _write_agent(
        tmp_path / ".claude" / "agents" / "test-missing-tools-agent.md",
        {
            "name": "test-missing-tools-agent",
            "description": (
                "Specialist missing required frontmatter fields, used to "
                "verify the doctor's required-field contract check."
            ),
            "model": "sonnet",
            "created_by": "talent-builder",
        },
        _CLEAN_BODY,
    )

    rc = _run_cli(["agents", "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    issues = _issues_for(payload, "test-missing-tools-agent")
    codes = {issue["code"]: issue for issue in issues}

    assert rc == 1
    assert payload["ok"] is False
    assert "missing-required-field" in codes
    fields_flagged = {
        issue["field"] for issue in issues if issue["code"] == "missing-required-field"
    }
    assert fields_flagged == {"tools", "permissionMode"}
    assert all(issue["severity"] == "error" for issue in issues if issue["code"] == "missing-required-field")


def test_hand_authored_agent_missing_tools_is_warning_not_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    """Hand-authored agents (no `created_by`) omitting model/permissionMode/
    tools get a warning, not an error -- omitting `tools` means "inherit all
    tools", a meaningful and valid choice (e.g. orchestrator needs to spawn
    subagents). Lint compliance must never force a restrictive `tools:` list
    onto a hand-authored agent just to silence this doctor (finding F2).
    """
    _isolate_defaults(monkeypatch, tmp_path)
    _write_agent(
        tmp_path / ".claude" / "agents" / "test-hand-authored-agent.md",
        {
            "name": "test-hand-authored-agent",
            "description": (
                "Hand-authored specialist that deliberately omits model, "
                "permissionMode, and tools, used to verify the doctor "
                "downgrades this to a warning for non-generated agents."
            ),
        },
        _CLEAN_BODY,
    )

    rc = _run_cli(["agents", "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    issues = _issues_for(payload, "test-hand-authored-agent")
    codes = {issue["code"] for issue in issues}

    assert "missing-required-field" not in codes
    assert "missing-recommended-field" in codes
    fields_flagged = {
        issue["field"] for issue in issues if issue["code"] == "missing-recommended-field"
    }
    assert fields_flagged == {"model", "permissionMode", "tools"}
    assert all(
        issue["severity"] == "warning"
        for issue in issues if issue["code"] == "missing-recommended-field"
    )
    # Non-blocking in default (non-strict) mode.
    assert rc == 0
    assert payload["ok"] is True


def test_missing_knowledge_pack_reported_with_agent_and_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    _write_agent(
        tmp_path / ".claude" / "agents" / "test-knowledge-pack-agent.md",
        {
            "name": "test-knowledge-pack-agent",
            "description": (
                "Specialist that declares a knowledge pack which does not "
                "exist, used to verify the doctor's pack-reference check."
            ),
            "model": "sonnet",
            "permissionMode": "default",
            "tools": "Read, Glob, Grep",
            "knowledge_packs": ["does-not-exist-pack"],
        },
        _CLEAN_BODY,
    )

    rc = _run_cli(["agents", "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    issues = [
        issue for issue in _issues_for(payload, "test-knowledge-pack-agent")
        if issue["code"] == "missing-knowledge-pack"
    ]

    assert rc == 1
    assert len(issues) == 1
    assert issues[0]["agent"] == "test-knowledge-pack-agent"
    assert issues[0]["field"] == "knowledge_packs"
    assert issues[0]["severity"] == "error"
    assert "does-not-exist-pack" in issues[0]["message"]


def test_existing_knowledge_pack_reports_no_pack_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    pack_dir = tmp_path / ".claude" / "knowledge" / "real-pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "knowledge.yaml").write_text(
        yaml.safe_dump(
            {"name": "real-pack", "description": "A real pack", "tags": ["test"]},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _write_agent(
        tmp_path / ".claude" / "agents" / "test-real-pack-agent.md",
        {
            "name": "test-real-pack-agent",
            "description": (
                "Specialist that declares a knowledge pack which does exist, "
                "used to verify the doctor does not false-positive."
            ),
            "model": "sonnet",
            "permissionMode": "default",
            "tools": "Read, Glob, Grep",
            "knowledge_packs": ["real-pack"],
        },
        _CLEAN_BODY,
    )

    rc = _run_cli(["agents", "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    issues = _issues_for(payload, "test-real-pack-agent")

    assert rc == 0
    assert not any(issue["code"] == "missing-knowledge-pack" for issue in issues)


_BROAD_TOOLS_BODY = """\
# Test Broad Tools Agent

## Mission

You are an implementer that writes files without ever explaining why.

## Before Starting

1. Read this entire agent definition.

## Principles

- Stay inside the role boundary.

## Output Format

Return a summary of files changed.
"""


def test_safety_warning_visible_but_non_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    _write_agent(
        tmp_path / ".claude" / "agents" / "test-broad-tools-agent.md",
        {
            "name": "test-broad-tools-agent",
            "description": (
                "Specialist with broad tool access and no stated "
                "justification, used to verify the doctor's safety check."
            ),
            "model": "sonnet",
            "permissionMode": "auto-edit",
            "tools": "Read, Write, Edit, Glob, Grep, Bash",
        },
        _BROAD_TOOLS_BODY,
    )

    rc = _run_cli(["agents", "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    issues = [
        issue for issue in _issues_for(payload, "test-broad-tools-agent")
        if issue["code"] == "broad-tools-no-justification"
    ]

    assert len(issues) == 1
    assert issues[0]["severity"] == "warning"
    # Non-blocking in default (non-strict) mode: overall run still exits 0
    # since this fixture introduces no errors.
    assert rc == 0
    assert payload["ok"] is True


def test_reviewer_agent_with_mutating_tools_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    _write_agent(
        tmp_path / ".claude" / "agents" / "test-mutating-reviewer.md",
        {
            "name": "test-mutating-reviewer",
            "description": (
                "Reviewer agent that should be read-only but has Edit "
                "access, used to verify the reviewer-safety check."
            ),
            "model": "opus",
            "permissionMode": "default",
            "tools": "Read, Glob, Grep, Edit",
        },
        _CLEAN_BODY,
    )

    rc = _run_cli(["agents", "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    issues = [
        issue for issue in _issues_for(payload, "test-mutating-reviewer")
        if issue["code"] == "reviewer-with-mutating-tools"
    ]

    assert rc == 0
    assert len(issues) == 1
    assert issues[0]["severity"] == "warning"


def test_strict_promotes_warnings_to_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    _write_agent(
        tmp_path / ".claude" / "agents" / "test-broad-tools-agent.md",
        {
            "name": "test-broad-tools-agent",
            "description": (
                "Specialist with broad tool access and no stated "
                "justification, used to verify --strict behavior."
            ),
            "model": "sonnet",
            "permissionMode": "auto-edit",
            "tools": "Read, Write, Edit, Glob, Grep, Bash",
        },
        _BROAD_TOOLS_BODY,
    )

    rc_default = _run_cli(["agents", "doctor", "--json"])
    capsys.readouterr()
    rc_strict = _run_cli(["agents", "doctor", "--json", "--strict"])
    payload_strict = json.loads(capsys.readouterr().out)

    assert rc_default == 0
    assert rc_strict == 1
    assert payload_strict["summary"]["warnings"] > 0
    assert payload_strict["summary"]["errors"] == 0


def test_before_starting_missing_local_reference_is_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    body = """\
# Test Dangling Reference Agent

## Mission

You are a specialist that reads a reference doc that doesn't exist.

## Before Starting

1. Read `references/does-not-exist.md` before doing anything else.

## Output Format

Return a summary.
"""
    _write_agent(
        tmp_path / ".claude" / "agents" / "test-dangling-reference-agent.md",
        {
            "name": "test-dangling-reference-agent",
            "description": (
                "Specialist whose Before Starting section references a "
                "file that does not exist, used to verify path checking."
            ),
            "model": "sonnet",
            "permissionMode": "default",
            "tools": "Read, Glob, Grep",
        },
        body,
    )

    rc = _run_cli(["agents", "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    issues = [
        issue for issue in _issues_for(payload, "test-dangling-reference-agent")
        if issue["code"] == "missing-before-starting-reference"
    ]

    assert rc == 0
    assert len(issues) == 1
    assert "references/does-not-exist.md" in issues[0]["message"]


def test_json_output_is_stable_and_parseable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    _write_agent(
        tmp_path / ".claude" / "agents" / "test-clean-agent.md",
        {
            "name": "test-clean-agent",
            "description": (
                "Specialist for validating clean-agent fixtures in the doctor "
                "test suite. Use when testing JSON stability only."
            ),
            "model": "sonnet",
            "permissionMode": "default",
            "tools": "Read, Glob, Grep",
        },
        _CLEAN_BODY,
    )

    rc = _run_cli(["agents", "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert set(payload.keys()) == {"ok", "summary", "issues"}
    assert set(payload["summary"].keys()) == {"agents", "errors", "warnings"}
    assert isinstance(payload["issues"], list)
    for issue in payload["issues"]:
        assert set(issue.keys()) == {
            "severity", "code", "message", "agent", "field", "path",
        }
        assert issue["severity"] in {"error", "warning"}


def test_text_output_reports_summary_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)

    rc = _run_cli(["agents", "doctor"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "Agent doctor" in out
    assert "agents=" in out
    assert "errors=" in out
    assert "warnings=" in out


def test_bare_agents_command_still_lists_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    """``baton agents`` (no subcommand) must keep its pre-existing listing
    behavior -- the doctor subcommand is additive, not a replacement.
    """
    _isolate_defaults(monkeypatch, tmp_path)

    rc = _run_cli(["agents"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "agents loaded." in out


def test_doctor_help_is_discoverable(capsys) -> None:
    rc = _run_cli(["agents", "doctor", "--help"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "--json" in out
    assert "--strict" in out
