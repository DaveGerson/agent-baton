"""Tests for ``baton knowledge doctor``."""
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


def _isolate_defaults(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(tmp_path)


def _write_doc(
    path: Path,
    *,
    name: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    body: str = "body",
) -> None:
    metadata: dict[str, object] = {}
    if name is not None:
        metadata["name"] = name
    if description is not None:
        metadata["description"] = description
    if tags is not None:
        metadata["tags"] = tags

    if metadata:
        text = "---\n" + yaml.safe_dump(metadata, sort_keys=False) + "---\n" + body
    else:
        text = body
    path.write_text(text, encoding="utf-8")


def test_doctor_json_reports_actionable_pack_warnings(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    knowledge = tmp_path / ".claude" / "knowledge"

    missing_manifest = knowledge / "missing-manifest"
    missing_manifest.mkdir(parents=True)
    _write_doc(
        missing_manifest / "guide.md",
        name="guide",
        description="Valid guide",
        tags=["auth"],
    )

    broken = knowledge / "broken"
    broken.mkdir()
    (broken / "knowledge.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "broken",
                "description": "Broken fixture",
                "default_delivery": "inline",
                "documents": [{"path": "missing.md"}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _write_doc(broken / "a.md", name="duplicate", description="", body="short")
    _write_doc(
        broken / "b.md",
        name="duplicate",
        description="Second duplicate",
        body="short",
    )
    _write_doc(broken / "raw.md", body="no frontmatter")
    _write_doc(
        broken / "large.md",
        name="large-inline",
        description="Large inline candidate",
        tags=["auth"],
        body="x" * 3000,
    )

    rc = _run_cli(["knowledge", "doctor", "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    codes = {issue["code"] for issue in data["issues"]}

    assert rc == 0
    assert data["ok"] is False
    assert "missing-manifest" in codes
    assert "empty-doc-description" in codes
    assert "duplicate-doc-name" in codes
    assert "missing-declared-file" in codes
    assert "empty-doc-metadata" in codes
    assert "large-inline-candidate" in codes
    assert all("Edit " in issue["message"] for issue in data["issues"])


def test_doctor_strict_exits_nonzero_for_warnings(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    pack = tmp_path / ".claude" / "knowledge" / "missing-manifest"
    pack.mkdir(parents=True)
    _write_doc(pack / "guide.md", name="guide", description="Valid guide")

    rc = _run_cli(["knowledge", "doctor", "--strict"])
    out = capsys.readouterr().out

    assert rc == 1
    assert "missing-manifest" in out
    assert "Edit " in out


def test_doctor_clean_pack_reports_ok(tmp_path: Path, monkeypatch, capsys) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    pack = tmp_path / ".claude" / "knowledge" / "clean"
    pack.mkdir(parents=True)
    (pack / "knowledge.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "clean",
                "description": "Clean pack",
                "tags": ["auth"],
                "default_delivery": "reference",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _write_doc(
        pack / "guide.md",
        name="auth-guide",
        description="Authentication guide",
        tags=["auth"],
    )

    rc = _run_cli(["knowledge", "doctor", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert data["ok"] is True
    assert data["issues"] == []
    assert data["summary"]["documents"] == 1
