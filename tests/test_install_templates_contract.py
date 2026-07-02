"""Contract tests for installing generated-agent starter templates."""
from __future__ import annotations

import argparse
from pathlib import Path

from agent_baton.cli.commands.distribute.install import _cmd_install


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_FILES = ("base-agent.md", "flavored-agent.md", "reviewer-agent.md")
INSTALLED_TEMPLATE_DIR = Path(".claude") / "templates" / "agents"


def _installed_template_paths() -> tuple[str, ...]:
    return tuple(
        f".claude/templates/agents/{filename}" for filename in TEMPLATE_FILES
    )


def test_baton_install_copies_agent_starter_templates_to_project_scope(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(
        scope="project",
        source=str(ROOT),
        force=True,
        upgrade=False,
        verify=False,
    )

    _cmd_install(args)

    for filename in TEMPLATE_FILES:
        installed = tmp_path / INSTALLED_TEMPLATE_DIR / filename
        source = ROOT / "templates" / "agents" / filename
        assert installed.read_text(encoding="utf-8") == source.read_text(
            encoding="utf-8"
        )


def test_powershell_installer_file_list_includes_agent_starter_templates() -> None:
    text = (ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert '$AgentTemplatesSrc  = Join-Path $RootDir "templates" "agents"' in text
    assert '$TemplateAgentTarget = Join-Path $Base "templates\\agents"' in text
    assert 'Get-ChildItem "$AgentTemplatesSrc\\*.md"' in text
    assert "Copy-Item $_.FullName -Destination $TemplateAgentTarget -Force" in text


def test_shell_installer_file_list_includes_agent_starter_templates() -> None:
    text = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert 'AGENT_TEMPLATES_SRC="$ROOT_DIR/templates/agents"' in text
    assert 'TEMPLATE_AGENT_TARGET="$BASE/templates/agents"' in text
    assert 'for f in "$AGENT_TEMPLATES_SRC"/*.md; do' in text
    assert 'cp "$f" "$TEMPLATE_AGENT_TARGET/"' in text


def test_documented_starter_template_paths_match_installed_paths() -> None:
    expected_paths = _installed_template_paths()
    docs = (
        ROOT / "references" / "agent-authoring.md",
        ROOT / "agents" / "talent-builder.md",
    )

    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for expected_path in expected_paths:
            assert expected_path in text, f"{doc} missing {expected_path}"
