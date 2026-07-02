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


def _valid_saved_plan(task_summary: str) -> dict[str, object]:
    return {
        "task_summary": task_summary,
        "task_type": "documentation",
        "complexity": "medium",
        "risk_level": "LOW",
        "phases": [
            {
                "name": "Review",
                "steps": [
                    {
                        "task_description": "Inspect current versions",
                        "agent_name": "auditor",
                        "team": [],
                    }
                ],
            }
        ],
    }


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
        "planner_validation",
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
    assert _check(payload, "planner_validation")["status"] == "warning"


def test_knowledge_packs_warning_when_pack_dir_lacks_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    pack_dir = tmp_path / ".claude" / "knowledge" / "broken-pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "guide.md").write_text("body\n", encoding="utf-8")
    (tmp_path / ".claude" / "team-context").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(
        diagnostics_cmd,
        "_load_knowledge_registry_details",
        lambda _root: {
            "registry_loaded_count": 0,
            "registry_well_formed_count": 0,
            "registry_degraded_count": 1,
            "registry_degraded_names": ["broken-pack"],
        },
    )

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "knowledge_packs")

    assert check["status"] == "warning"
    assert check["details"]["project_count"] == 1
    assert check["details"]["project_with_manifest"] == 0
    assert check["details"]["registry_degraded_count"] == 1


def test_assurance_packs_warning_when_validation_finds_invalid_pack(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    pack_dir = tmp_path / ".claude" / "packs" / "project-assurance"
    pack_dir.mkdir(parents=True)
    (pack_dir / "pack.json").write_text(
        '{"name": "project-assurance", "version": "0.1.0"}\n',
        encoding="utf-8",
    )
    (tmp_path / ".claude" / "team-context").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(
        diagnostics_cmd,
        "_validate_assurance_pack_dirs",
        lambda *_roots: {
            "invalid_count": 1,
            "invalid_packs": [
                {
                    "pack": "project-assurance",
                    "path": str(pack_dir),
                    "errors": ["missing rubric"],
                }
            ],
        },
    )

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "assurance_packs")

    assert check["status"] == "warning"
    assert check["details"]["project_count"] == 1
    assert check["details"]["invalid_count"] == 1


def test_project_agents_warning_when_validation_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    _write_agent(agents_dir / "architect.md", name="architect")
    (tmp_path / ".claude" / "team-context").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(
        diagnostics_cmd,
        "_validate_agent_dir",
        lambda _path: {
            "validated_count": 0,
            "validation_warnings": 0,
            "validation_errors": 0,
            "validation_error": "validator import failed",
        },
    )

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "project_agents")

    assert check["status"] == "warning"
    assert check["details"]["count"] == 1
    assert check["details"]["validation_error"] == "validator import failed"


def test_doctor_json_includes_planner_validation_warning_without_saved_plan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / ".claude" / "team-context").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "planner_validation")

    assert check["status"] == "warning"
    assert check["details"]["plan_path"] is None
    assert check["details"]["machine_plan_importable"] is True


def test_doctor_discovers_task_scoped_saved_plan_for_planner_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    task_dir = (
        tmp_path
        / ".claude"
        / "team-context"
        / "executions"
        / "task-002"
    )
    task_dir.mkdir(parents=True)
    (task_dir / "plan.json").write_text(
        json.dumps(_valid_saved_plan("Review dependency versions")),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "planner_validation")

    assert check["message"] != "No saved plan is available to validate"
    assert check["details"]["plan_path"] == str(task_dir / "plan.json")
    assert check["details"]["plan_candidates"] == [
        str(tmp_path / ".claude" / "team-context" / "plan.json"),
        str(tmp_path / "plan.json"),
        str(task_dir / "plan.json"),
    ]


def test_doctor_prefers_active_task_scoped_plan_over_sorted_or_legacy_fallbacks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    team_context = tmp_path / ".claude" / "team-context"
    task_a_dir = team_context / "executions" / "task-a"
    task_b_dir = team_context / "executions" / "task-b"
    task_a_dir.mkdir(parents=True)
    task_b_dir.mkdir(parents=True)
    (team_context / "plan.json").write_text(
        json.dumps(_valid_saved_plan("Legacy team-context plan")),
        encoding="utf-8",
    )
    (task_a_dir / "plan.json").write_text(
        json.dumps(_valid_saved_plan("Task A plan")),
        encoding="utf-8",
    )
    (task_b_dir / "plan.json").write_text(
        json.dumps(_valid_saved_plan("Task B plan")),
        encoding="utf-8",
    )
    (team_context / "active-task-id.txt").write_text("task-b\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "planner_validation")

    assert check["status"] == "ok"
    assert check["details"]["active_task_id"] == "task-b"
    assert check["details"]["active_task_source"] == "file"
    assert check["details"]["plan_path"] == str(task_b_dir / "plan.json")
    assert check["details"]["plan_candidates"] == [
        str(team_context / "plan.json"),
        str(tmp_path / "plan.json"),
        str(task_a_dir / "plan.json"),
        str(task_b_dir / "plan.json"),
    ]


def test_doctor_reports_missing_active_task_plan_without_validating_fallback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    team_context = tmp_path / ".claude" / "team-context"
    fallback_plan = team_context / "plan.json"
    fallback_plan.parent.mkdir(parents=True)
    fallback_plan.write_text(
        json.dumps(_valid_saved_plan("Legacy fallback plan")),
        encoding="utf-8",
    )
    missing_active_plan = (
        team_context / "executions" / "task-b" / "plan.json"
    )
    (team_context / "active-task-id.txt").write_text("task-b\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "planner_validation")

    assert check["status"] == "warning"
    assert check["details"]["active_task_id"] == "task-b"
    assert check["details"]["active_task_source"] == "file"
    assert check["details"]["plan_path"] == str(missing_active_plan)
    assert str(missing_active_plan) in check["message"]
    assert check["details"]["plan_candidates"] == [
        str(team_context / "plan.json"),
        str(tmp_path / "plan.json"),
    ]


def test_doctor_reports_structured_error_for_malformed_saved_plan_json_shape(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    team_context = tmp_path / ".claude" / "team-context"
    team_context.mkdir(parents=True)
    (team_context / "plan.json").write_text('{"phases": ["bad"]}\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "planner_validation")

    assert check["status"] == "error"
    assert check["details"]["plan_path"] == str(team_context / "plan.json")
    assert check["details"]["validator_importable"] is True
    assert "validation_error" in check["details"]


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
