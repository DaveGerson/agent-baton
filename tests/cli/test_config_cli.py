"""Tests for ``baton config`` -- ``--profile manager`` (Wave 3 / Task 11).

See docs/internal/manager-mode-pmo-plan.md Wave 3 / Task 11.

``agent_baton/cli/commands/config_cmd.py`` predates manager-mode PMO work
entirely (it already implements ``baton config show|validate|init`` over
:class:`~agent_baton.core.config.project_config.ProjectConfig`). Task 11
extends it with a ``--profile {project,manager}`` flag rather than
replacing it -- ``project`` (the default) preserves the pre-existing
behavior unchanged; ``manager`` operates on the SAME ``.claude/baton.yaml``
file's manager-mode section via
:class:`~agent_baton.core.config.manager.ManagerConfig`.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent_baton.cli.commands import config_cmd
from agent_baton.core.config.manager import ManagerConfig


@pytest.fixture(autouse=True)
def _fake_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: Any) -> Path:
    """Redirect ``Path.home()`` so ``ManagerConfig.load()``'s
    ``~/.baton/config.yaml`` check never reads a real developer machine's
    config (mirrors ``tests/manager/conftest.py``'s ``fake_home``, which
    does not apply here since this file lives under ``tests/cli/``)."""
    fake_home_dir = tmp_path_factory.mktemp("fake_home_config_cli")
    monkeypatch.setattr(Path, "home", lambda: fake_home_dir)
    return fake_home_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="baton")
    subparsers = parser.add_subparsers()
    config_cmd.register(subparsers)
    return parser


def _run(argv: list[str]) -> argparse.Namespace:
    parser = _build_parser()
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# init --profile manager
# ---------------------------------------------------------------------------


def test_config_init_writes_template(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    args = _run(["config", "init", "--profile", "manager"])

    config_cmd.handler(args)

    target = tmp_path / ".claude" / "baton.yaml"
    assert target.is_file()

    # Valid per ManagerConfig.from_yaml -- the required contract.
    cfg = ManagerConfig.from_yaml(target)
    assert cfg.manager_mode.enabled_by_default is False
    assert cfg.team.max_agents_by_complexity["medium"] == 5
    assert cfg.scoping.scope_expansion_policy == "queue_for_manager"
    assert cfg.policies.phase_completion.adversarial_review == "always"
    assert cfg.policies.review_agents.project_review == "auditor"


def test_config_init_manager_refuses_overwrite_without_force(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / ".claude" / "baton.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("version: 1\n", encoding="utf-8")

    args = _run(["config", "init", "--profile", "manager"])
    with pytest.raises(SystemExit) as exc_info:
        config_cmd.handler(args)

    assert exc_info.value.code != 0
    assert "already exists" in capsys.readouterr().err
    assert target.read_text(encoding="utf-8") == "version: 1\n"  # untouched


def test_config_init_manager_force_overwrites(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / ".claude" / "baton.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("version: 1\n", encoding="utf-8")

    args = _run(["config", "init", "--profile", "manager", "--force"])
    config_cmd.handler(args)

    assert "manager_mode:" in target.read_text(encoding="utf-8")


def test_config_init_manager_respects_explicit_path(tmp_path: Path) -> None:
    target = tmp_path / "custom" / "manager-config.yaml"
    args = _run(
        ["config", "init", "--profile", "manager", "--path", str(target)]
    )

    config_cmd.handler(args)

    assert target.is_file()
    ManagerConfig.from_yaml(target)  # does not raise


def test_config_init_project_profile_default_unchanged(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Regression guard: omitting --profile (or passing --profile project)
    keeps writing the pre-existing ProjectConfig starter at the cwd root,
    not .claude/baton.yaml."""
    monkeypatch.chdir(tmp_path)
    args = _run(["config", "init"])

    config_cmd.handler(args)

    assert (tmp_path / "baton.yaml").is_file()
    assert not (tmp_path / ".claude" / "baton.yaml").exists()
    assert "default_agents:" in (tmp_path / "baton.yaml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# validate --profile manager
# ---------------------------------------------------------------------------


def test_config_validate_reports_bad_enum(tmp_path: Path, capsys: Any) -> None:
    bad_config = tmp_path / "baton.yaml"
    bad_config.write_text(
        "policies:\n  phase_completion:\n    adversarial_review: sometimes\n",
        encoding="utf-8",
    )

    args = _run(["config", "validate", "--profile", "manager", str(bad_config)])
    with pytest.raises(SystemExit) as exc_info:
        config_cmd.handler(args)

    assert exc_info.value.code != 0
    stderr = capsys.readouterr().err
    assert "adversarial_review" in stderr
    assert "sometimes" in stderr


def test_config_validate_manager_ok_on_valid_file(tmp_path: Path, capsys: Any) -> None:
    good_config = tmp_path / "baton.yaml"
    good_config.write_text("manager_mode:\n  enabled_by_default: true\n", encoding="utf-8")

    args = _run(["config", "validate", "--profile", "manager", str(good_config)])
    config_cmd.handler(args)  # must not raise/exit non-zero

    assert "OK" in capsys.readouterr().out


def test_config_validate_manager_missing_path_errors(tmp_path: Path, capsys: Any) -> None:
    args = _run(
        ["config", "validate", "--profile", "manager", str(tmp_path / "nope.yaml")]
    )
    with pytest.raises(SystemExit) as exc_info:
        config_cmd.handler(args)

    assert exc_info.value.code != 0
    assert "does not exist" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# show --profile manager
# ---------------------------------------------------------------------------


def test_config_show_renders_effective_config(tmp_path: Path, capsys: Any) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "baton.yaml").write_text(
        "team:\n  max_agents_by_complexity:\n    medium: 7\n",
        encoding="utf-8",
    )

    args = _run(["config", "show", "--profile", "manager", "--start-dir", str(tmp_path)])
    config_cmd.handler(args)

    out = capsys.readouterr().out
    assert str(claude_dir / "baton.yaml") in out or "Loaded" in out
    rendered = yaml.safe_load(out.split("\n", 1)[1] if out.startswith("Loaded") else out)
    assert rendered["team"]["max_agents_by_complexity"]["medium"] == 7
    assert rendered["policies"]["phase_completion"]["adversarial_review"] == "always"


def test_config_show_manager_no_config_found(tmp_path: Path, capsys: Any) -> None:
    args = _run(["config", "show", "--profile", "manager", "--start-dir", str(tmp_path)])
    config_cmd.handler(args)

    out = capsys.readouterr().out
    assert "No baton.yaml found" in out


# ---------------------------------------------------------------------------
# project profile smoke (unchanged behavior)
# ---------------------------------------------------------------------------


def test_config_show_project_profile_still_works(tmp_path: Path) -> None:
    args = _run(["config", "show", "--start-dir", str(tmp_path)])
    config_cmd.handler(args)  # must not raise
