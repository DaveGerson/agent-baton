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


def test_doctor_accepts_docs_stems_and_reports_missing_stems(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    pack = tmp_path / ".claude" / "knowledge" / "harvested"
    pack.mkdir(parents=True)
    (pack / "knowledge.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "harvested",
                "description": "Harvested pack",
                "tags": ["taxonomy"],
                "default_delivery": "reference",
                "docs": ["taxonomy-quick-ref", "missing-stem"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _write_doc(
        pack / "taxonomy-quick-ref.md",
        name="taxonomy-quick-ref",
        description="Taxonomy quick reference",
        tags=["taxonomy"],
    )

    rc = _run_cli(["knowledge", "doctor", "--json"])
    data = json.loads(capsys.readouterr().out)
    missing = [
        issue
        for issue in data["issues"]
        if issue["code"] == "missing-declared-file"
    ]

    assert rc == 0
    assert len(missing) == 1
    assert "missing-stem" in missing[0]["message"]
    assert "taxonomy-quick-ref" not in missing[0]["message"]


def test_doctor_reports_invalid_doc_frontmatter_for_sequence_and_scalar(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    pack = tmp_path / ".claude" / "knowledge" / "invalid-frontmatter"
    pack.mkdir(parents=True)
    (pack / "knowledge.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "invalid-frontmatter",
                "description": "Invalid frontmatter fixtures",
                "default_delivery": "reference",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (pack / "sequence.md").write_text(
        "---\n- not\n- a\n- mapping\n---\nbody\n", encoding="utf-8"
    )
    (pack / "scalar.md").write_text(
        "---\njust-a-string\n---\nbody\n", encoding="utf-8"
    )

    rc = _run_cli(["knowledge", "doctor", "--json"])
    data = json.loads(capsys.readouterr().out)
    invalid = [
        issue
        for issue in data["issues"]
        if issue["code"] == "invalid-doc-frontmatter"
    ]

    assert rc == 0
    assert {issue["doc"] for issue in invalid} == {"sequence", "scalar"}
    assert all("Edit " in issue["message"] for issue in invalid)
    assert not any(
        issue["code"] == "empty-doc-description"
        for issue in data["issues"]
    )


def test_doctor_reports_non_utf8_manifest_as_invalid_manifest(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    pack = tmp_path / ".claude" / "knowledge" / "legacy-manifest"
    pack.mkdir(parents=True)
    (pack / "knowledge.yaml").write_bytes(
        b"name: legacy-\xe9\ndescription: legacy manifest\n"
    )
    _write_doc(
        pack / "guide.md",
        name="guide",
        description="Valid guide",
        tags=["legacy"],
    )

    rc = _run_cli(["knowledge", "doctor", "--json"])
    data = json.loads(capsys.readouterr().out)
    invalid_manifest = [
        issue
        for issue in data["issues"]
        if issue["code"] == "invalid-manifest"
    ]

    assert rc == 0
    assert len(invalid_manifest) == 1
    assert invalid_manifest[0]["pack"] == "legacy-manifest"
    assert "Edit " in invalid_manifest[0]["message"]


def test_doctor_reports_non_utf8_doc_as_unreadable_doc(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    pack = tmp_path / ".claude" / "knowledge" / "legacy-doc"
    pack.mkdir(parents=True)
    (pack / "knowledge.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "legacy-doc",
                "description": "Legacy encoded document",
                "default_delivery": "reference",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (pack / "legacy.md").write_bytes(
        b"---\nname: legacy\ndescription: Caf\xe9\n---\nbody\n"
    )

    rc = _run_cli(["knowledge", "doctor", "--json"])
    data = json.loads(capsys.readouterr().out)
    unreadable = [
        issue
        for issue in data["issues"]
        if issue["code"] == "unreadable-doc"
    ]

    assert rc == 0
    assert len(unreadable) == 1
    assert unreadable[0]["doc"] == "legacy"
    assert "Edit " in unreadable[0]["message"]
    assert not any(
        issue["code"] == "empty-doc-description"
        for issue in data["issues"]
    )


def test_declared_doc_with_dotted_stem_resolves_md_fallback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    pack = tmp_path / ".claude" / "knowledge" / "dotted-stem"
    pack.mkdir(parents=True)
    (pack / "knowledge.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "dotted-stem",
                "description": "Dotted stem fixtures",
                "documents": ["notes.v2"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _write_doc(
        pack / "notes.v2.md",
        name="notes.v2",
        description="Versioned notes",
        tags=["versioned"],
    )

    rc = _run_cli(["knowledge", "doctor", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert not any(
        issue["code"] == "missing-declared-file" for issue in data["issues"]
    )


def test_declared_json_doc_not_satisfied_by_md_shadow(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    pack = tmp_path / ".claude" / "knowledge" / "shadowed"
    pack.mkdir(parents=True)
    (pack / "knowledge.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "shadowed",
                "description": "Shadowed declaration fixtures",
                "documents": ["config.json"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    # A shadow "config.json.md" must NOT satisfy the "config.json" declaration
    # — only genuinely extensionless stems get the +.md fallback.
    (pack / "config.json.md").write_text("shadow", encoding="utf-8")

    rc = _run_cli(["knowledge", "doctor", "--json"])
    data = json.loads(capsys.readouterr().out)
    missing = [
        issue
        for issue in data["issues"]
        if issue["code"] == "missing-declared-file"
    ]

    assert rc == 0
    assert len(missing) == 1
    assert "config.json" in missing[0]["message"]


def test_doctor_isolates_unreadable_pack_and_continues(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    knowledge = tmp_path / ".claude" / "knowledge"

    good_pack = knowledge / "good-pack"
    good_pack.mkdir(parents=True)
    (good_pack / "knowledge.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "good-pack",
                "description": "Good pack",
                "default_delivery": "reference",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _write_doc(good_pack / "guide.md", name="guide", description="Valid guide")

    locked_pack = knowledge / "locked-pack"
    locked_pack.mkdir(parents=True)
    (locked_pack / "knowledge.yaml").write_text(
        yaml.safe_dump(
            {"name": "locked-pack", "description": "Locked pack"},
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    original_glob = Path.glob

    def flaky_glob(self: Path, pattern: str):
        if self == locked_pack and pattern == "*.md":
            raise PermissionError("simulated ACL denial")
        return original_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", flaky_glob)

    rc = _run_cli(["knowledge", "doctor", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert rc == 0
    unreadable = [
        issue for issue in data["issues"] if issue["code"] == "unreadable-pack"
    ]
    assert len(unreadable) == 1
    assert unreadable[0]["pack"] == "locked-pack"
    # The good pack was still validated despite the locked pack's failure.
    assert data["summary"]["packs"] == 2
    assert data["summary"]["documents"] == 1


def test_strict_duplicate_explicit_root_emits_single_issue(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    missing_root = tmp_path / "does-not-exist"

    rc = _run_cli([
        "knowledge",
        "doctor",
        "--json",
        "--knowledge-root",
        str(missing_root),
        "--knowledge-root",
        str(missing_root),
        "--strict",
    ])
    data = json.loads(capsys.readouterr().out)
    missing = [
        issue for issue in data["issues"] if issue["code"] == "missing-root"
    ]

    assert rc == 1
    assert len(missing) == 1


def test_strict_missing_explicit_root_emits_issue_and_fails(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _isolate_defaults(monkeypatch, tmp_path)
    missing_root = tmp_path / "does-not-exist"

    rc = _run_cli([
        "knowledge",
        "doctor",
        "--json",
        "--knowledge-root",
        str(missing_root),
        "--strict",
    ])
    data = json.loads(capsys.readouterr().out)
    missing = [
        issue for issue in data["issues"] if issue["code"] == "missing-root"
    ]

    assert rc == 1
    assert len(missing) == 1
    assert "does-not-exist" in missing[0]["message"]
