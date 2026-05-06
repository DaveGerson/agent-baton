"""Tests for the release-notes builder (R3.3)."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from agent_baton.core.release.notes import (
    ReleaseNotes,
    ReleaseNotesBuilder,
    Statistics,
)


# ---------------------------------------------------------------------------
# Helpers: build a stub git repo on disk so we can drive ``git log`` queries
# without depending on the surrounding worktree's history.
# ---------------------------------------------------------------------------
def _git(repo: Path, *args: str, env_extra: dict | None = None) -> str:
    env = os.environ.copy()
    # Deterministic identity + suppress global hooks/config that may
    # interfere with the tiny in-memory repo.
    env.update(
        {
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@example.com",
        }
    )
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return proc.stdout


def _commit(repo: Path, message: str, *, file: str = None, content: str = "x") -> str:
    """Create a file change and commit. Returns the new SHA."""
    target = file or f"f-{abs(hash(message)) & 0xFFFF}.txt"
    full = repo / target
    full.parent.mkdir(parents=True, exist_ok=True)
    # Append so commits really differ.
    if full.exists():
        full.write_text(full.read_text(encoding="utf-8") + content + "\n", encoding="utf-8")
    else:
        full.write_text(content + "\n", encoding="utf-8")
    _git(repo, "add", "--", target)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


@pytest.fixture
def stub_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=master")
    # Disable any global gpg-signing requirement that might be set on the host.
    _git(repo, "config", "commit.gpgsign", "false")
    _git(repo, "config", "tag.gpgsign", "false")
    # Seed commit so HEAD exists; this is the "before" point.
    (repo / "README").write_text("init\n", encoding="utf-8")
    _git(repo, "add", "README")
    _git(repo, "commit", "-m", "chore: init")
    return repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_build_from_commit_range_collects_commits(stub_repo: Path) -> None:
    base = _git(stub_repo, "rev-parse", "HEAD").strip()
    _commit(stub_repo, "feat: add widget", file="widget.py", content="w")
    _commit(stub_repo, "fix: correct typo", file="widget.py", content="t")
    head = _git(stub_repo, "rev-parse", "HEAD").strip()

    builder = ReleaseNotesBuilder(repo_root=stub_repo)
    notes = builder.build(from_ref=base, to_ref=head)

    assert isinstance(notes, ReleaseNotes)
    assert notes.stats.commit_count == 2
    assert any("add widget" in h for h in notes.highlights)
    assert any("correct typo" in f for f in notes.fixes)


def test_highlights_capture_feat_and_dedupe(stub_repo: Path) -> None:
    base = _git(stub_repo, "rev-parse", "HEAD").strip()
    _commit(stub_repo, "feat(api): new endpoint", file="api.py")
    _commit(stub_repo, "feat: stand-alone feature", file="lib.py")
    _commit(stub_repo, "feat(dx): improve developer flow", file="dx.py")
    _commit(stub_repo, "perf: speed up query", file="perf.py")
    _commit(stub_repo, "feat: stand-alone feature", file="lib.py", content="dup")
    head = _git(stub_repo, "rev-parse", "HEAD").strip()

    builder = ReleaseNotesBuilder(repo_root=stub_repo)
    notes = builder.build(from_ref=base, to_ref=head)

    # feat() and feat(api) end up under highlights; feat(dx) goes under perf.
    assert "new endpoint" in " ".join(notes.highlights)
    assert "stand-alone feature" in " ".join(notes.highlights)
    # Dedup: only one stand-alone-feature entry despite two commits.
    assert sum("stand-alone feature" in h for h in notes.highlights) == 1
    # feat(dx): is funneled to perf/DX section, not highlights.
    assert not any("developer flow" in h for h in notes.highlights)
    perf_blob = " ".join(notes.perf)
    assert "developer flow" in perf_blob
    assert "speed up query" in perf_blob


def test_breaking_change_detection_subject_and_footer(stub_repo: Path) -> None:
    base = _git(stub_repo, "rev-parse", "HEAD").strip()
    # Subject-bang form
    _commit(stub_repo, "feat!: drop python 3.9", file="setup.py")
    # Footer form
    body_msg = (
        "feat(api): rename token field\n\n"
        "BREAKING CHANGE: callers must use ``api_token`` instead of ``token``.\n"
    )
    target = stub_repo / "api.py"
    target.write_text("hi\n", encoding="utf-8")
    _git(stub_repo, "add", "api.py")
    _git(stub_repo, "commit", "-m", body_msg)
    head = _git(stub_repo, "rev-parse", "HEAD").strip()

    builder = ReleaseNotesBuilder(repo_root=stub_repo)
    notes = builder.build(from_ref=base, to_ref=head)

    assert len(notes.breaking) == 2
    joined = " ".join(notes.breaking)
    assert "drop python 3.9" in joined
    assert "api_token" in joined


def test_markdown_sections_present(stub_repo: Path) -> None:
    base = _git(stub_repo, "rev-parse", "HEAD").strip()
    _commit(stub_repo, "feat: marquee feature", file="m.py")
    _commit(stub_repo, "fix: small repair", file="m.py", content="r")
    _commit(stub_repo, "perf: faster path", file="m.py", content="p")
    head = _git(stub_repo, "rev-parse", "HEAD").strip()

    builder = ReleaseNotesBuilder(repo_root=stub_repo)
    md = builder.build(from_ref=base, to_ref=head).to_markdown()

    assert "## Highlights" in md
    assert "## Fixes" in md
    assert "## Performance / DX" in md
    assert "## Statistics" in md
    assert "marquee feature" in md
    assert "small repair" in md
    assert "faster path" in md


def test_html_output_escapes_special_chars(stub_repo: Path) -> None:
    base = _git(stub_repo, "rev-parse", "HEAD").strip()
    # Commit subject containing characters that MUST be HTML-escaped.
    _commit(stub_repo, "feat: handle <script> & entities in titles", file="x.py")
    head = _git(stub_repo, "rev-parse", "HEAD").strip()

    builder = ReleaseNotesBuilder(repo_root=stub_repo)
    html = builder.build(from_ref=base, to_ref=head).to_html()

    # The raw form must NOT appear -- it would be valid HTML and a
    # cross-site-scripting hazard if we ever embedded the notes.
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "&amp;" in html
    # Sanity: we still produced a basic document skeleton.
    assert html.startswith("<!doctype html>")
    assert "</html>" in html


def test_json_output_round_trips(stub_repo: Path) -> None:
    base = _git(stub_repo, "rev-parse", "HEAD").strip()
    _commit(stub_repo, "feat: thing", file="t.py")
    head = _git(stub_repo, "rev-parse", "HEAD").strip()

    builder = ReleaseNotesBuilder(repo_root=stub_repo)
    notes = builder.build(from_ref=base, to_ref=head)
    payload = json.loads(notes.to_json())

    assert payload["range_from"] == base
    assert payload["range_to"] == head
    assert payload["stats"]["commit_count"] == 1
    assert payload["highlights"] == ["thing"]


def test_release_id_falls_back_when_entity_missing(stub_repo: Path) -> None:
    """If R3.1 isn't merged, --release should not crash."""
    base = _git(stub_repo, "rev-parse", "HEAD").strip()
    _commit(stub_repo, "feat: anything", file="a.py")
    head = _git(stub_repo, "rev-parse", "HEAD").strip()

    builder = ReleaseNotesBuilder(repo_root=stub_repo)
    notes = builder.build(release_id="rel-does-not-exist", from_ref=base, to_ref=head)
    assert notes.release_id == "rel-does-not-exist"
    assert notes.release_name is None
    assert notes.stats.commit_count == 1


def test_specs_delivered_picked_up_from_added_files(stub_repo: Path) -> None:
    base = _git(stub_repo, "rev-parse", "HEAD").strip()
    spec_path = stub_repo / "docs" / "superpowers" / "specs" / "2026-04-25-cool-design.md"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("# Cool Design Spec\n\nbody\n", encoding="utf-8")
    _git(stub_repo, "add", "--", str(spec_path.relative_to(stub_repo)))
    _git(stub_repo, "commit", "-m", "docs: cool design spec")
    head = _git(stub_repo, "rev-parse", "HEAD").strip()

    builder = ReleaseNotesBuilder(repo_root=stub_repo)
    notes = builder.build(from_ref=base, to_ref=head)
    assert notes.specs, "expected at least one spec entry"
    paths = [p for p, _ in notes.specs]
    assert any(p.endswith("2026-04-25-cool-design.md") for p in paths)
    assert any("Cool Design Spec" in headline for _, headline in notes.specs)


def test_performance_under_3s(stub_repo: Path) -> None:
    """Sanity: a typical-size range generates notes well under 3 seconds."""
    base = _git(stub_repo, "rev-parse", "HEAD").strip()
    for i in range(40):
        _commit(stub_repo, f"feat: feature {i}", file=f"f{i}.py")
    head = _git(stub_repo, "rev-parse", "HEAD").strip()

    import time

    builder = ReleaseNotesBuilder(repo_root=stub_repo)
    t0 = time.perf_counter()
    notes = builder.build(from_ref=base, to_ref=head)
    elapsed = time.perf_counter() - t0
    assert elapsed < 3.0, f"notes generation took {elapsed:.2f}s"
    assert notes.stats.commit_count == 40
