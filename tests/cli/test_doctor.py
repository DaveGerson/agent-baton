"""Tests for the top-level ``baton doctor`` command."""
from __future__ import annotations

import argparse
import json
import sqlite3
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


def _write_beads_workspace(root: Path) -> Path:
    beads_dir = root / ".beads"
    beads_dir.mkdir(parents=True)
    for name in ("config.yaml", "interactions.jsonl", "metadata.json"):
        (beads_dir / name).write_text("{}\n", encoding="utf-8")
    return beads_dir


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


@pytest.fixture(autouse=True)
def _clear_baton_task_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BATON_TASK_ID", raising=False)


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

    # bd is mandatory after ADR-13b WP-G: with PATH cleared it is genuinely
    # missing, so doctor reports it as a failing check and the CLI exits
    # non-zero (unlike the still-optional claude_cli, which stays a warning).
    assert rc == 1
    assert payload["schema_version"] == 1
    assert payload["ok"] is False
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
        "beads_workspace",
        "git",
        "git_worktree",
        "claude_cli",
        "team_context",
        "planner_validation",
        "terminology",
    } <= check_ids
    assert _check(payload, "bd")["status"] == "error"
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


class TestCheckBd:
    """bd-7is: ``bd`` is mandatory after ADR-13b WP-G, so doctor must fail
    (not warn) when it cannot be found, with an actionable remediation
    message, while honoring the same BATON_BD_BIN override the runtime
    (BdClient) uses.
    """

    def test_missing_bd_is_an_error_with_remediation(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from agent_baton.cli.commands import diagnostics_cmd

        monkeypatch.setenv("PATH", "")
        monkeypatch.delenv("BATON_BD_BIN", raising=False)

        check = diagnostics_cmd._check_bd()

        assert check.status == "error"
        assert check.details["path"] is None
        # Actionable: names an install path and the override knob.
        assert "install" in check.message.lower()
        assert "BATON_BD_BIN" in check.message

    def test_bd_found_on_path_is_ok(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from agent_baton.cli.commands import diagnostics_cmd

        monkeypatch.delenv("BATON_BD_BIN", raising=False)
        monkeypatch.setattr(
            diagnostics_cmd.shutil, "which", lambda name: f"/usr/bin/{name}"
        )

        check = diagnostics_cmd._check_bd()

        assert check.status == "ok"
        assert check.details["path"] == "/usr/bin/bd"

    def test_baton_bd_bin_override_is_honored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from agent_baton.cli.commands import diagnostics_cmd

        custom_bin = tmp_path / "custom-bd"
        custom_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        monkeypatch.setenv("PATH", "")
        monkeypatch.setenv("BATON_BD_BIN", str(custom_bin))

        check = diagnostics_cmd._check_bd()

        assert check.status == "ok"
        assert check.details["executable"] == str(custom_bin)

    def test_baton_bd_bin_missing_target_is_still_an_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from agent_baton.cli.commands import diagnostics_cmd

        monkeypatch.setenv("PATH", "")
        monkeypatch.setenv("BATON_BD_BIN", str(tmp_path / "does-not-exist"))

        check = diagnostics_cmd._check_bd()

        assert check.status == "error"
        assert "does-not-exist" in check.message


def test_doctor_handler_exits_nonzero_after_printing_error_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    payload = {
        "schema_version": 1,
        "ok": False,
        "project_root": "project",
        "summary": {"ok": 1, "warning": 0, "error": 1},
        "checks": [
            {
                "id": "planner_validation",
                "label": "Planner validation",
                "status": "error",
                "message": "Saved plan validation failed",
                "details": {},
            }
        ],
    }
    monkeypatch.setattr(
        diagnostics_cmd,
        "build_report",
        lambda project_root: payload,
    )

    with pytest.raises(SystemExit) as exc_info:
        diagnostics_cmd.handler(argparse.Namespace(json=True))

    assert exc_info.value.code == 1
    assert json.loads(capsys.readouterr().out) == payload


def test_beads_workspace_reports_ok_when_expected_files_exist(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    _write_beads_workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "beads_workspace")

    assert check["status"] == "ok"
    assert check["details"]["exists"] is True
    assert check["details"]["missing_files"] == []
    assert sorted(check["details"]["present_files"]) == [
        "config.yaml",
        "interactions.jsonl",
        "metadata.json",
    ]


def test_beads_workspace_reports_warning_when_workspace_is_missing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "beads_workspace")

    assert check["status"] == "warning"
    assert check["details"]["exists"] is False
    assert check["details"]["missing_files"] == [
        "config.yaml",
        "interactions.jsonl",
        "metadata.json",
    ]
    assert check["details"]["present_files"] == []


def test_git_worktree_reports_linked_worktree_metadata_when_git_is_monkeypatched(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    expected = {
        ("rev-parse", "--is-inside-work-tree"): {
            "returncode": 0,
            "stdout": "true\n",
            "stderr": "",
        },
        ("rev-parse", "--show-superproject-working-tree"): {
            "returncode": 0,
            "stdout": "\n",
            "stderr": "",
        },
        ("rev-parse", "--git-dir"): {
            "returncode": 0,
            "stdout": ".git\\worktrees\\roadmap-ux-doctor\n",
            "stderr": "",
        },
        ("rev-parse", "--git-common-dir"): {
            "returncode": 0,
            "stdout": ".git\n",
            "stderr": "",
        },
        ("branch", "--show-current"): {
            "returncode": 0,
            "stdout": "bd-rm-ux-p1\n",
            "stderr": "",
        },
        ("rev-parse", "--abbrev-ref", "HEAD"): {
            "returncode": 0,
            "stdout": "bd-rm-ux-p1\n",
            "stderr": "",
        },
    }

    def fake_git(args: list[str], cwd: Path) -> dict[str, object]:
        key = tuple(args)
        if key not in expected:
            raise AssertionError(f"unexpected git args: {args}")
        return expected[key]

    monkeypatch.setattr(diagnostics_cmd.shutil, "which", lambda _name: "git")
    monkeypatch.setattr(diagnostics_cmd, "_git", fake_git)

    check = diagnostics_cmd._check_git_worktree(tmp_path)

    assert check.status == "ok"
    assert check.details["branch"] == "bd-rm-ux-p1"
    assert check.details["git_dir"] == ".git\\worktrees\\roadmap-ux-doctor"
    assert check.details["git_common_dir"] == ".git"
    assert check.details["is_linked_worktree"] is True
    assert check.details["is_submodule"] is False
    assert check.details["detached_head"] is False


def test_git_helper_passes_no_optional_locks_before_subcommand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    calls: list[list[str]] = []

    class FakeCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd: list[str], **_kwargs: object) -> FakeCompletedProcess:
        calls.append(cmd)
        return FakeCompletedProcess()

    monkeypatch.setattr(diagnostics_cmd.subprocess, "run", fake_run)

    diagnostics_cmd._git(["status", "--porcelain"], tmp_path)

    assert calls == [
        [
            "git",
            "-C",
            str(tmp_path),
            "--no-optional-locks",
            "status",
            "--porcelain",
        ]
    ]


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

    # bd is mandatory (ADR-13b WP-G): missing it is a real failure (`error`),
    # reported cleanly rather than raising -- the other, still-optional
    # features degrade to warnings as before.
    assert payload["ok"] is False
    assert _check(payload, "pmo_ui_assets")["status"] == "warning"
    assert _check(payload, "bd")["status"] == "error"
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


def test_doctor_build_report_does_not_create_team_context_artifacts_in_fresh_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    team_context = tmp_path / ".claude" / "team-context"
    db_path = team_context / "baton.db"
    wal_path = team_context / "baton.db-wal"
    shm_path = team_context / "baton.db-shm"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "planner_validation")

    assert check["status"] == "warning"
    assert not (tmp_path / ".claude").exists()
    assert not team_context.exists()
    assert not db_path.exists()
    assert not wal_path.exists()
    assert not shm_path.exists()


def test_doctor_build_report_does_not_write_probe_files_in_team_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    team_context = tmp_path / ".claude" / "team-context"
    team_context.mkdir(parents=True)
    sentinel = team_context / "existing-note.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    before_children = sorted(path.name for path in team_context.iterdir())
    path_write_text = Path.write_text

    def guarded_write_text(self: Path, *args, **kwargs):
        if self.parent == team_context and self.name.startswith(".baton-doctor"):
            raise AssertionError("doctor attempted a temp-file writability probe")
        return path_write_text(self, *args, **kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(Path, "write_text", guarded_write_text)

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "team_context")
    after_children = sorted(path.name for path in team_context.iterdir())

    assert check["status"] == "ok"
    assert before_children == after_children
    assert not any(name.startswith(".baton-doctor") for name in after_children)


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


def test_doctor_reads_active_task_from_existing_sqlite_without_extra_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    team_context = tmp_path / ".claude" / "team-context"
    team_context.mkdir(parents=True)
    task_a_dir = team_context / "executions" / "task-a"
    task_b_dir = team_context / "executions" / "task-b"
    task_a_dir.mkdir(parents=True)
    task_b_dir.mkdir(parents=True)
    (task_a_dir / "plan.json").write_text(
        json.dumps(_valid_saved_plan("Task A plan")),
        encoding="utf-8",
    )
    (task_b_dir / "plan.json").write_text(
        json.dumps(_valid_saved_plan("Task B plan")),
        encoding="utf-8",
    )
    db_path = team_context / "baton.db"
    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
    conn.execute(
        "CREATE TABLE active_task (id INTEGER PRIMARY KEY, task_id TEXT)"
    )
    conn.execute(
        "INSERT INTO active_task (id, task_id) VALUES (1, 'task-b')"
    )
    conn.commit()
    conn.close()
    assert not (team_context / "baton.db-wal").exists()
    assert not (team_context / "baton.db-shm").exists()
    before_paths = sorted(
        str(path.relative_to(tmp_path))
        for path in tmp_path.rglob("*")
        if path.is_file()
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "planner_validation")
    after_paths = sorted(
        str(path.relative_to(tmp_path))
        for path in tmp_path.rglob("*")
        if path.is_file()
    )

    assert check["status"] == "ok"
    assert check["details"]["active_task_id"] == "task-b"
    assert check["details"]["active_task_source"] == "sqlite"
    assert check["details"]["plan_path"] == str(task_b_dir / "plan.json")
    assert not (team_context / "active-task-id.txt").exists()
    assert not (team_context / "baton.db-wal").exists()
    assert not (team_context / "baton.db-shm").exists()
    assert after_paths == before_paths


def test_doctor_reads_active_task_from_open_wal_sidecar_without_mutating_project(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    team_context = tmp_path / ".claude" / "team-context"
    team_context.mkdir(parents=True)
    task_dir = team_context / "executions" / "task-wal"
    task_dir.mkdir(parents=True)
    (task_dir / "plan.json").write_text(
        json.dumps(_valid_saved_plan("Task WAL plan")),
        encoding="utf-8",
    )
    db_path = team_context / "baton.db"
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        conn.execute(
            "CREATE TABLE active_task (id INTEGER PRIMARY KEY, task_id TEXT)"
        )
        conn.execute(
            "INSERT INTO active_task (id, task_id) VALUES (1, 'task-wal')"
        )
        conn.commit()
        assert (team_context / "baton.db-wal").exists()
        assert (team_context / "baton.db-shm").exists()
        before_paths = sorted(
            str(path.relative_to(tmp_path))
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))
        monkeypatch.setenv("PATH", "")

        payload = diagnostics_cmd.build_report(tmp_path)
        check = _check(payload, "planner_validation")
        after_paths = sorted(
            str(path.relative_to(tmp_path))
            for path in tmp_path.rglob("*")
            if path.is_file()
        )
    finally:
        conn.close()

    assert check["status"] == "ok"
    assert check["details"]["active_task_id"] == "task-wal"
    assert check["details"]["active_task_source"] == "sqlite"
    assert check["details"]["plan_path"] == str(task_dir / "plan.json")
    assert after_paths == before_paths


def test_doctor_records_degraded_sqlite_active_task_probe_details(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    team_context = tmp_path / ".claude" / "team-context"
    team_context.mkdir(parents=True)
    db_path = team_context / "baton.db"
    db_path.write_text("not a sqlite database\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "planner_validation")
    probe = check["details"]["active_task_sqlite_probe"]

    assert check["status"] == "warning"
    assert check["details"]["active_task_id"] is None
    assert check["details"]["active_task_source"] is None
    assert probe["status"] == "degraded"
    assert probe["db_path"] == str(db_path)
    assert probe["error"]
    assert probe["error_type"]


def test_doctor_prefers_baton_task_id_env_over_sqlite_active_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    team_context = tmp_path / ".claude" / "team-context"
    env_task_dir = team_context / "executions" / "task-env"
    sqlite_task_dir = team_context / "executions" / "task-sqlite"
    env_task_dir.mkdir(parents=True)
    sqlite_task_dir.mkdir(parents=True)
    (env_task_dir / "plan.json").write_text(
        json.dumps(_valid_saved_plan("Task env plan")),
        encoding="utf-8",
    )
    (sqlite_task_dir / "plan.json").write_text(
        json.dumps(_valid_saved_plan("Task sqlite plan")),
        encoding="utf-8",
    )
    db_path = team_context / "baton.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE active_task (id INTEGER PRIMARY KEY, task_id TEXT)"
    )
    conn.execute(
        "INSERT INTO active_task (id, task_id) VALUES (1, 'task-sqlite')"
    )
    conn.commit()
    conn.close()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("BATON_TASK_ID", " task-env ")

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "planner_validation")

    assert check["status"] == "ok"
    assert check["details"]["active_task_id"] == "task-env"
    assert check["details"]["active_task_source"] == "env"
    assert check["details"]["plan_path"] == str(env_task_dir / "plan.json")


def test_doctor_reads_active_task_from_baton_db_path_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """BATON_DB_PATH must be honoured by the sqlite active-task probe (D1)."""
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    team_context = tmp_path / ".claude" / "team-context"
    task_dir = team_context / "executions" / "task-override"
    task_dir.mkdir(parents=True)
    (task_dir / "plan.json").write_text(
        json.dumps(_valid_saved_plan("Task override plan")),
        encoding="utf-8",
    )

    # The overridden DB lives entirely outside team-context/baton.db so the
    # default path would never find it.
    override_db_dir = tmp_path / "elsewhere"
    override_db_dir.mkdir(parents=True)
    override_db_path = override_db_dir / "custom-baton.db"
    conn = sqlite3.connect(override_db_path)
    conn.execute(
        "CREATE TABLE active_task (id INTEGER PRIMARY KEY, task_id TEXT)"
    )
    conn.execute(
        "INSERT INTO active_task (id, task_id) VALUES (1, 'task-override')"
    )
    conn.commit()
    conn.close()

    # Confirm there is no baton.db at the default location doctor would
    # otherwise fall back to.
    assert not (team_context / "baton.db").exists()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("BATON_DB_PATH", str(override_db_path))

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "planner_validation")

    assert check["status"] == "ok"
    assert check["details"]["active_task_id"] == "task-override"
    assert check["details"]["active_task_source"] == "sqlite"
    assert check["details"]["plan_path"] == str(task_dir / "plan.json")


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


def test_doctor_flags_fallback_plan_selection_with_no_active_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """No active task resolves anywhere -> the fallback guess is surfaced (D2)."""
    from agent_baton.cli.commands import diagnostics_cmd

    home = tmp_path / "home"
    home.mkdir()
    team_context = tmp_path / ".claude" / "team-context"
    task_a_dir = team_context / "executions" / "task-a"
    task_b_dir = team_context / "executions" / "task-b"
    task_a_dir.mkdir(parents=True)
    task_b_dir.mkdir(parents=True)
    (task_a_dir / "plan.json").write_text(
        json.dumps(_valid_saved_plan("Task A plan")),
        encoding="utf-8",
    )
    (task_b_dir / "plan.json").write_text(
        json.dumps(_valid_saved_plan("Task B plan")),
        encoding="utf-8",
    )
    # No BATON_TASK_ID, no active_task sqlite row/db, no active-task-id.txt
    # marker -> nothing identifies a current task.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("BATON_TASK_ID", raising=False)
    monkeypatch.delenv("BATON_DB_PATH", raising=False)

    payload = diagnostics_cmd.build_report(tmp_path)
    check = _check(payload, "planner_validation")

    assert check["details"]["active_task_id"] is None
    assert check["details"]["plan_selection"] == "fallback-first-found"
    assert check["details"]["plan_path"] == str(task_a_dir / "plan.json")
    assert "caveat" in check["message"].lower()
    assert "no active task resolved" in check["message"].lower()


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
    assert check["details"]["plan_selection"] == "active-task"
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
    # talent-manager was documented as an alias but never resolvable in the
    # AgentRegistry; the claim was removed rather than implemented.
    assert "talent-manager" not in roster
    assert "knowledge.yaml" in terminology
    assert "Knowledge pack" in terminology
    assert "Assurance pack" in terminology
    assert "knowledge.yaml" in governance
    assert "assurance packs" in cli_reference
