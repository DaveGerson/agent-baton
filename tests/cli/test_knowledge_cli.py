"""Tests for ``baton knowledge list|show|scan|audit|propose`` (M5 CLI verbs).

See docs/internal/manager-mode-pmo-plan.md Wave 1 / Task 7 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §8.4, §12.4.

Registration harness note: most tests in this file register ``pack_cmds`` on
a *fresh, isolated* ``argparse`` subparsers tree (mirroring the existing
convention in ``tests/knowledge/test_codebase_brief.py`` of exercising a
single knowledge submodule's handler directly) rather than going through
``agent_baton.cli.main.main()``'s full multi-module ``discover_commands()``
registration.

bd-b6i (fixed): ``agent_baton/cli/commands/knowledge/ab_cmd.py`` used to
build its own ``p.add_subparsers(dest="knowledge_subcommand")`` directly
instead of using the cooperative ``get_or_create_parser`` helper documented
in ``agent_baton/cli/commands/knowledge/__init__.py``. Because ``ab_cmd`` is
discovered before every other ``knowledge`` submodule (alphabetical:
``ab_cmd`` < ``brief`` < ``effectiveness_cmd`` < ``harvest_cmd`` <
``lifecycle_cmd`` < ``pack_cmds`` < ``ranking_cmd``), it won the "who creates
the knowledge parser first" race in a real ``baton`` invocation, poisoning
the shared sub-action's ``dest`` to ``"knowledge_subcommand"``.
``__init__.py``'s ``dispatch()`` reads ``args.knowledge_cmd`` (never set in
that case), so *every* ``baton knowledge <verb>`` invocation -- including
``ab`` itself -- silently fell through to the "Usage: baton knowledge
SUBCOMMAND ..." message instead of dispatching, when run through the real
CLI entry point. ``ab_cmd.py`` now uses ``get_or_create_parser`` /
``register_handler`` like every other submodule; see
``test_knowledge_list_via_main`` and ``test_knowledge_ab_list_via_main``
below for the full-entry-point regression coverage.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest


def _write_pack(root: Path, name: str, manifest_yaml: str) -> Path:
    pack_dir = root / name
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "knowledge.yaml").write_text(manifest_yaml, encoding="utf-8")
    return pack_dir


def _run_cli(argv: list[str]) -> int:
    """Parse *argv* against an isolated parser carrying only ``pack_cmds``'
    ``baton knowledge`` registration, then dispatch -- see module docstring
    for why this doesn't go through the full ``agent_baton.cli.main.main()``.
    """
    from agent_baton.cli.commands.knowledge import pack_cmds

    parser = argparse.ArgumentParser(prog="baton")
    subparsers = parser.add_subparsers(dest="command")
    pack_cmds.register(subparsers)

    args = parser.parse_args(argv)
    try:
        args._dispatch(args)
        return 0
    except SystemExit as e:
        return int(e.code) if e.code is not None else 0


@pytest.fixture(autouse=True)
def _isolated_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Chdir into *tmp_path* and redirect ``Path.home()`` to a bare fake
    home so no real developer-machine knowledge pack leaks into a test."""
    monkeypatch.chdir(tmp_path)
    fake_home = tmp_path / "_fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    return tmp_path


def test_knowledge_list_shows_status(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    knowledge_root = tmp_path / ".claude" / "knowledge"
    _write_pack(
        knowledge_root,
        "coding-conventions",
        "name: coding-conventions\nstatus: active\nconfidence: high\n",
    )

    rc = _run_cli(["knowledge", "list"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "coding-conventions" in out
    assert "active" in out
    assert "high" in out


def test_knowledge_list_empty_registry_prints_friendly_message(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    rc = _run_cli(["knowledge", "list"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "No knowledge packs" in out


def test_knowledge_show_pack(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    knowledge_root = tmp_path / ".claude" / "knowledge"
    _write_pack(
        knowledge_root,
        "coding-conventions",
        "name: coding-conventions\nstatus: active\nconfidence: high\nsource_files: [pyproject.toml]\n",
    )

    rc = _run_cli(["knowledge", "show", "coding-conventions"])
    out = capsys.readouterr().out

    assert rc == 0
    assert "coding-conventions" in out
    assert "active" in out
    assert "pyproject.toml" in out


def test_knowledge_show_unknown_pack_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    rc = _run_cli(["knowledge", "show", "does-not-exist"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "does-not-exist" in err


def test_knowledge_scan_writes_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    knowledge_root = tmp_path / ".claude" / "knowledge"
    _write_pack(knowledge_root, "coding-conventions", "name: coding-conventions\n")
    (tmp_path / "README.md").write_text("# Hi\n", encoding="utf-8")

    rc = _run_cli(["knowledge", "scan"])
    assert rc == 0

    scan_path = tmp_path / ".claude" / "team-context" / "knowledge-scan.json"
    assert scan_path.exists()
    payload = json.loads(scan_path.read_text(encoding="utf-8"))
    assert any(p["name"] == "coding-conventions" for p in payload["packs"])
    assert "README.md" in payload["discovered_files"]


def test_knowledge_audit_reports_invalid_status(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    knowledge_root = tmp_path / ".claude" / "knowledge"
    _write_pack(knowledge_root, "weird-pack", "name: weird-pack\nstatus: bogus\n")

    rc = _run_cli(["knowledge", "audit"])
    out = capsys.readouterr().out

    assert rc != 0
    assert "bogus" in out


def test_knowledge_audit_clean_registry_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    rc = _run_cli(["knowledge", "audit"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no issues" in out.lower()


def test_knowledge_propose_no_gaps_is_a_noop(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    rc = _run_cli(["knowledge", "propose"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "nothing to propose" in out.lower()


def test_knowledge_propose_writes_draft(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    retros_dir = tmp_path / ".claude" / "team-context" / "retrospectives"
    retros_dir.mkdir(parents=True)
    gap = {
        "description": "No documented convention for API error responses",
        "agent_name": "backend-engineer",
        "task_summary": "Add endpoint",
    }
    (retros_dir / "retro-1.json").write_text(
        json.dumps({"knowledge_gaps": [gap]}), encoding="utf-8"
    )
    gap2 = dict(gap, agent_name="test-engineer", task_summary="Add tests")
    (retros_dir / "retro-2.json").write_text(
        json.dumps({"knowledge_gaps": [gap2]}), encoding="utf-8"
    )

    rc = _run_cli(["knowledge", "propose"])
    out = capsys.readouterr().out
    assert rc == 0

    proposals_dir = tmp_path / ".claude" / "team-context" / "knowledge-proposals"
    written = list(proposals_dir.glob("*.md"))
    assert len(written) == 1
    assert written[0].name in out or str(written[0]) in out


# ---------------------------------------------------------------------------
# bd-b6i regression -- full ``agent_baton.cli.main.main()`` entry point.
#
# Prior to the fix, ``ab_cmd.py`` built its own ``knowledge`` parser directly
# (instead of the cooperative ``get_or_create_parser`` helper) and, being
# alphabetically first among ``knowledge`` submodules, won the "who creates
# the shared parser first" race in a real ``baton`` invocation -- poisoning
# the shared sub-action's ``dest`` to ``"knowledge_subcommand"``. The shared
# ``dispatch()`` in ``__init__.py`` reads ``args.knowledge_cmd`` (never set
# in that case), so *every* ``baton knowledge <verb>`` -- including ``ab``
# itself -- silently printed usage and exited 0 through the real
# ``main()``. These tests exercise ``main()`` directly (not the isolated
# single-module harness above) to catch this class of registration-order
# bug.
# ---------------------------------------------------------------------------


def test_knowledge_list_via_main(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from agent_baton.cli.main import main

    knowledge_root = tmp_path / ".claude" / "knowledge"
    _write_pack(
        knowledge_root,
        "coding-conventions",
        "name: coding-conventions\nstatus: active\nconfidence: high\n",
    )

    main(["knowledge", "list"])
    out = capsys.readouterr().out

    assert "coding-conventions" in out
    assert "Usage: baton knowledge" not in out


def test_knowledge_ab_list_via_main(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    from agent_baton.cli.main import main

    main(["knowledge", "ab", "list"])
    out = capsys.readouterr().out

    assert "No knowledge A/B experiments found." in out
    assert "Usage: baton knowledge" not in out
