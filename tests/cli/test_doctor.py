"""Tests for the top-level ``baton doctor`` command."""
from __future__ import annotations

import json
from pathlib import Path

import yaml


def _run_cli(argv: list[str]) -> int:
    from agent_baton.cli.main import main

    try:
        main(argv)
        return 0
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0


def _write_agent(path: Path, *, name: str) -> None:
    path.write_text(
        (
            "---\n"
            f"name: {name}\n"
            "description: Test agent\n"
            "model: sonnet\n"
            "---\n"
            "Test instructions.\n"
        ),
        encoding="utf-8",
    )


def _write_project_layout(root: Path) -> None:
    agents_dir = root / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    _write_agent(agents_dir / "architect.md", name="architect")

    knowledge_pack = root / ".claude" / "knowledge" / "project-knowledge"
    knowledge_pack.mkdir(parents=True)
    (knowledge_pack / "knowledge.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "project-knowledge",
                "description": "Project knowledge pack",
                "tags": ["project"],
                "default_delivery": "reference",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (knowledge_pack / "guide.md").write_text(
        (
            "---\n"
            "name: guide\n"
            "description: Project guide\n"
            "---\n"
            "Body.\n"
        ),
        encoding="utf-8",
    )

    assurance_pack = root / ".claude" / "packs" / "project-assurance"
    assurance_pack.mkdir(parents=True)
    (assurance_pack / "pack.json").write_text(
        '{"name": "project-assurance", "version": "0.1.0"}\n',
        encoding="utf-8",
    )

    team_context = root / ".claude" / "team-context"
    team_context.mkdir(parents=True)


def _check(payload: dict[str, object], check_id: str) -> dict[str, object]:
    checks = payload["checks"]
    assert isinstance(checks, list)
    for check in checks:
        assert isinstance(check, dict)
        if check.get("id") == check_id:
            return check
    raise AssertionError(f"missing doctor check: {check_id}")


def test_discovery_registers_top_level_doctor_and_knowledge_doctor_separately(
    capsys,
) -> None:
    from agent_baton.cli.main import discover_commands

    modules = discover_commands()

    assert "diagnostics_cmd" in modules
    assert modules["diagnostics_cmd"].__name__.endswith(".diagnostics_cmd")
    assert "doctor_cmd" in modules
    assert modules["doctor_cmd"].__name__.endswith(".knowledge.doctor_cmd")

    assert _run_cli(["doctor", "--help"]) == 0
    assert _run_cli(["knowledge", "doctor", "--help"]) == 0
    assert _run_cli(["--help"]) == 0
    help_text = capsys.readouterr().out
    assert "doctor" in help_text


def test_doctor_json_reports_required_checks_and_optional_warnings(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _write_project_layout(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("PATH", "")

    rc = _run_cli(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    check_ids = {check["id"] for check in payload["checks"]}

    assert rc == 0
    assert payload["schema_version"] == 1
    assert payload["ok"] is True
    assert {
        "python",
        "package_version",
        "bundled_agents",
        "project_agents",
        "knowledge_packs",
        "assurance_packs",
        "pmo_ui_assets",
        "package_resources",
        "bd",
        "git",
        "claude_cli",
        "team_context",
        "terminology",
    } <= check_ids
    assert _check(payload, "bd")["status"] == "warning"
    assert _check(payload, "claude_cli")["status"] == "warning"
    assert _check(payload, "project_agents")["details"]["count"] == 1
    assert _check(payload, "knowledge_packs")["details"]["project_count"] == 1
    assert _check(payload, "assurance_packs")["details"]["project_count"] == 1
    assert _check(payload, "team_context")["status"] == "ok"

    bundled = _check(payload, "bundled_agents")
    assert bundled["details"]["count"] > 0
    assert "talent-builder" in bundled["details"]["names"]

    resources = _check(payload, "package_resources")
    assert {
        "bundled_agents",
        "references",
        "templates",
        "pmo_static_assets",
    } <= set(resources["details"]["resources"])


def test_missing_optional_features_are_warnings_not_crashes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    team_context = tmp_path / ".claude" / "team-context"
    team_context.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(
        diagnostics_cmd,
        "_probe_writable_directory",
        lambda _path: (False, "permission denied"),
    )

    payload = diagnostics_cmd.build_report(tmp_path)

    assert payload["ok"] is True
    assert _check(payload, "pmo_ui_assets")["status"] == "warning"
    assert _check(payload, "bd")["status"] == "warning"
    assert _check(payload, "claude_cli")["status"] == "warning"
    assert _check(payload, "knowledge_packs")["status"] == "warning"
    assert _check(payload, "assurance_packs")["status"] == "warning"
    assert _check(payload, "team_context")["status"] == "warning"


def test_doctor_text_distinguishes_pack_types_and_uses_canonical_terms(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _write_project_layout(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))

    rc = _run_cli(["doctor"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "Baton doctor" in out
    assert "Knowledge packs" in out
    assert "Assurance packs" in out
    assert "talent-builder" in out
    assert "knowledge.yaml" in out
    assert "talent-manager" not in out


def test_doctor_help_mentions_json_and_pack_types(capsys) -> None:
    rc = _run_cli(["doctor", "--help"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "--json" in out
    assert "knowledge packs" in out
    assert "assurance packs" in out


def test_documented_terminology_is_canonical() -> None:
    repo = Path(__file__).resolve().parents[2]
    roster = (repo / "docs" / "agent-roster.md").read_text(encoding="utf-8")
    terminology = (repo / "docs" / "terminology.md").read_text(encoding="utf-8")
    governance = (
        repo / "docs" / "governance-knowledge-and-events.md"
    ).read_text(encoding="utf-8")
    cli_reference = (repo / "docs" / "cli-reference.md").read_text(
        encoding="utf-8"
    )

    assert "`talent-builder`" in roster
    assert "`talent-manager` is a compatibility alias" in roster
    assert "knowledge.yaml" in terminology
    assert "Knowledge pack" in terminology
    assert "Assurance pack" in terminology
    assert "knowledge.yaml" in governance
    assert "assurance packs" in cli_reference
