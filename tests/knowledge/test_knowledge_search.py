"""Tests for knowledge search and resolve simulation CLI commands."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from agent_baton.core.engine.knowledge_resolver import KnowledgeResolver
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.core.orchestration.registry import AgentRegistry


def _run_cli(argv: list[str]) -> int:
    from agent_baton.cli.main import main

    try:
        main(argv)
        return 0
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0


def _isolate_defaults(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(tmp_path)


def _write_pack(tmp_path: Path) -> tuple[Path, Path]:
    knowledge = tmp_path / ".claude" / "knowledge"
    pack = knowledge / "auth-pack"
    pack.mkdir(parents=True)
    (pack / "knowledge.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "auth-pack",
                "description": "Authentication token renewal and session rules",
                "tags": ["authentication", "tokens"],
                "target_agents": ["backend-engineer--python"],
                "default_delivery": "reference",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    doc = pack / "renewal.md"
    doc.write_text(
        "---\n"
        "name: token-renewal\n"
        "description: Authentication token renewal flow and refresh handling\n"
        "tags: [authentication, token, renewal]\n"
        "priority: high\n"
        "---\n"
        "Renew short-lived authentication tokens before expiry.\n",
        encoding="utf-8",
    )
    return pack, doc


def _write_agent(tmp_path: Path) -> None:
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "backend-engineer--python.md").write_text(
        "---\n"
        "name: backend-engineer--python\n"
        "description: Backend engineer\n"
        "knowledge_packs: [auth-pack]\n"
        "---\n"
        "# Agent\n",
        encoding="utf-8",
    )


def test_search_json_returns_registry_metadata(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    _pack, doc = _write_pack(tmp_path)

    rc = _run_cli([
        "knowledge",
        "search",
        "authentication token renewal",
        "--json",
    ])
    data = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert data["query"] == "authentication token renewal"
    assert data["results"]
    first = data["results"][0]
    assert first["pack"] == "auth-pack"
    assert first["doc"] == "token-renewal"
    assert first["score"] > 0
    assert first["path"] == str(doc)
    assert first["tags"] == ["authentication", "token", "renewal"]
    assert first["priority"] == "high"
    assert first["token_estimate"] > 0


def test_resolve_json_matches_actual_resolver_output(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    _write_pack(tmp_path)
    _write_agent(tmp_path)

    rc = _run_cli([
        "knowledge",
        "resolve",
        "--agent",
        "backend-engineer--python",
        "--task",
        "refresh authentication token renewal",
        "--json",
    ])
    cli_data = json.loads(capsys.readouterr().out)

    registry = KnowledgeRegistry()
    registry.load_default_paths()
    agent_registry = AgentRegistry()
    agent_registry.load_default_paths()
    expected = [
        attachment.to_dict()
        for attachment in KnowledgeResolver(
            registry, agent_registry=agent_registry
        ).resolve(
            agent_name="backend-engineer--python",
            task_description="refresh authentication token renewal",
        )
    ]

    assert rc == 0
    assert cli_data["attachments"] == expected
    assert cli_data["attachments"][0]["pack_name"] == "auth-pack"
    assert cli_data["attachments"][0]["document_name"] == "token-renewal"
