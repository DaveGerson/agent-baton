"""Tests for the bd-backed bead store (ADR-13b full replacement, phase 1).

Two layers:
- Pure-unit mapping tests (no `bd` binary needed) — Bead <-> bd issue dict.
- Integration tests against the real `bd` CLI, skipped when it isn't installed.
"""
from __future__ import annotations

import shutil

import pytest

from agent_baton.core.engine.bd_client import BD_BUILTIN_TYPES, BdClient, bd_enabled, bd_prefix
from agent_baton.core.engine.bd_mapping import (
    bd_issue_to_bead,
    bead_labels,
    bead_to_create_kwargs,
)
from agent_baton.core.engine.bead_backend import make_bead_store, selected_backend
from agent_baton.models.bead import Bead, BeadLink, ExecutableBead

_BD_AVAILABLE = shutil.which("bd") is not None


def _sample_bead(**overrides) -> Bead:
    base = dict(
        bead_id="bd-a1b2",
        task_id="T-42",
        step_id="2.1",
        agent_name="architect",
        bead_type="decision",
        content="Use JWT for auth persistence",
        confidence="high",
        scope="task",
        tags=["auth", "security"],
        affected_files=["auth.py"],
        status="open",
        created_at="2026-06-01T10:00:00Z",
        source="agent-signal",
    )
    base.update(overrides)
    return Bead(**base)


# ---------------------------------------------------------------------------
# Mapping (pure unit — no bd needed)
# ---------------------------------------------------------------------------


def test_bead_labels_include_facets():
    labels = bead_labels(_sample_bead())
    assert "auth" in labels and "security" in labels
    assert "bead-type:decision" in labels
    assert "scope:task" in labels
    assert "source:agent-signal" in labels
    assert "task:T-42" in labels


def test_bead_labels_dedup_and_status_facet():
    labels = bead_labels(_sample_bead(status="quarantine", tags=["auth", "auth"]))
    assert labels.count("auth") == 1
    assert "baton-status:quarantine" in labels


def test_create_kwargs_shape():
    kw = bead_to_create_kwargs(_sample_bead())
    assert kw["bead_id"] == "bd-a1b2"
    assert kw["issue_type"] == "decision"  # bd built-in
    assert kw["description"] == "Use JWT for auth persistence"
    assert kw["metadata"]["baton"]["agent_name"] == "architect"


def test_create_kwargs_unknown_type_falls_back_to_task():
    kw = bead_to_create_kwargs(_sample_bead(bead_type="warning"))
    assert kw["issue_type"] == "task"  # 'warning' is not a bd built-in
    assert "decision" in BD_BUILTIN_TYPES  # sanity on the constant


def test_roundtrip_via_metadata_blob():
    bead = _sample_bead()
    kw = bead_to_create_kwargs(bead)
    # Simulate what `bd show --json` returns: native fields + our metadata blob.
    issue = {
        "id": bead.bead_id,
        "title": kw["title"],
        "status": "open",
        "issue_type": kw["issue_type"],
        "labels": kw["labels"],
        "metadata": kw["metadata"],
    }
    restored = bd_issue_to_bead(issue)
    assert restored.bead_id == bead.bead_id
    assert restored.agent_name == "architect"
    assert restored.step_id == "2.1"
    assert restored.confidence == "high"
    assert restored.affected_files == ["auth.py"]
    assert restored.bead_type == "decision"


def test_roundtrip_closed_status_overlay():
    bead = _sample_bead()
    kw = bead_to_create_kwargs(bead)
    issue = {
        "id": bead.bead_id,
        "status": "closed",
        "closed_at": "2026-06-02T00:00:00Z",
        "metadata": kw["metadata"],
    }
    restored = bd_issue_to_bead(issue)
    assert restored.status == "closed"
    assert restored.closed_at == "2026-06-02T00:00:00Z"


def test_external_issue_without_baton_blob():
    """An issue authored directly in bd is still visible as a bead."""
    issue = {
        "id": "bd-ext1",
        "title": "External issue",
        "description": "made by bd directly",
        "status": "open",
        "issue_type": "bug",
        "labels": ["task:T-9", "bead-type:warning", "infra"],
    }
    bead = bd_issue_to_bead(issue)
    assert bead.bead_id == "bd-ext1"
    assert bead.task_id == "T-9"
    assert bead.bead_type == "warning"  # from label
    assert bead.tags == ["infra"]       # synthetic facets stripped
    assert bead.source == "bd-external"


def test_quarantine_status_roundtrips_via_label():
    bead = _sample_bead(status="quarantine")
    kw = bead_to_create_kwargs(bead)
    issue = {
        "id": bead.bead_id,
        "status": "blocked",  # bd-side status for quarantine
        "labels": kw["labels"],
        "metadata": kw["metadata"],
    }
    restored = bd_issue_to_bead(issue)
    assert restored.status == "quarantine"


# ---------------------------------------------------------------------------
# ADR-13b WP-1 §3 — ExecutableBead subtype reconstruction via bd_mapping
# ---------------------------------------------------------------------------


def _make_executable_issue(
    *,
    bead_id: str = "bd-exec1",
    interpreter: str = "bash",
    script_sha: str = "abc123",
    script_ref: str = "refs/notes/baton-bead-scripts/abc123",
    script_body: str = "#!/bin/bash\necho hello",
    status: str = "open",
) -> dict:
    """Build a minimal bd issue dict carrying an ExecutableBead baton blob."""
    bead = ExecutableBead(
        bead_id=bead_id,
        task_id="T-exe",
        step_id="1.1",
        agent_name="engineer",
        bead_type="executable",
        content="A hello-world script",
        interpreter=interpreter,
        script_sha=script_sha,
        script_ref=script_ref,
        script_body=script_body,
        status=status,
    )
    from agent_baton.core.engine.bd_mapping import bead_to_create_kwargs

    kw = bead_to_create_kwargs(bead)
    return {
        "id": bead_id,
        "title": kw["title"],
        "status": status,
        "issue_type": kw["issue_type"],
        "labels": kw["labels"],
        "metadata": kw["metadata"],
    }


def test_executable_bead_reconstructed_as_subtype():
    """bd_issue_to_bead must return an ExecutableBead for bead_type='executable'."""
    issue = _make_executable_issue()
    restored = bd_issue_to_bead(issue)
    assert isinstance(restored, ExecutableBead), (
        f"Expected ExecutableBead, got {type(restored).__name__}"
    )
    assert restored.bead_type == "executable"
    assert restored.interpreter == "bash"
    assert restored.script_sha == "abc123"
    assert restored.script_ref == "refs/notes/baton-bead-scripts/abc123"


def test_executable_bead_script_body_survives_roundtrip():
    """script_body must round-trip through the baton metadata blob."""
    issue = _make_executable_issue(script_body="#!/bin/bash\necho roundtrip")
    restored = bd_issue_to_bead(issue)
    assert isinstance(restored, ExecutableBead)
    assert restored.script_body == "#!/bin/bash\necho roundtrip"


def test_executable_bead_script_body_in_to_dict():
    """ExecutableBead.to_dict() must include script_body."""
    bead = ExecutableBead(
        bead_id="bd-x",
        task_id="T-1",
        step_id="1.1",
        agent_name="eng",
        bead_type="executable",
        content="test",
        script_body="echo hello",
        script_sha="deadbeef",
        script_ref="refs/notes/baton-bead-scripts/deadbeef",
    )
    d = bead.to_dict()
    assert d["script_body"] == "echo hello"


def test_executable_bead_from_dict_with_script_body():
    """ExecutableBead.from_dict() must restore script_body from the blob."""
    blob = {
        "bead_id": "bd-y",
        "task_id": "T-2",
        "step_id": "1.2",
        "agent_name": "eng",
        "bead_type": "executable",
        "content": "test",
        "script_body": "echo restored",
        "script_sha": "cafebabe",
        "script_ref": "refs/notes/baton-bead-scripts/cafebabe",
    }
    bead = ExecutableBead.from_dict(blob)
    assert bead.script_body == "echo restored"
    assert bead.script_sha == "cafebabe"


def test_non_executable_bead_still_reconstructed_as_base_bead():
    """Beads with bead_type != 'executable' must remain plain Bead instances."""
    from agent_baton.core.engine.bd_mapping import bead_to_create_kwargs

    plain_bead = Bead(
        bead_id="bd-plain",
        task_id="T-3",
        step_id="1.1",
        agent_name="arch",
        bead_type="decision",
        content="Use SQLite",
    )
    kw = bead_to_create_kwargs(plain_bead)
    issue = {
        "id": "bd-plain",
        "status": "open",
        "metadata": kw["metadata"],
    }
    restored = bd_issue_to_bead(issue)
    # Must be exactly Bead, NOT ExecutableBead.
    assert type(restored) is Bead


# ---------------------------------------------------------------------------
# Backend selector
# ---------------------------------------------------------------------------


def test_default_backend_is_auto(monkeypatch):
    # ADR-13b step F: the default flipped to ``auto`` (WP-H update).
    # ``auto`` resolves to ``bd`` when bd is installed and enabled, otherwise
    # falls back to SQLite — tested by test_make_bead_store_auto_resolves below.
    monkeypatch.delenv("BATON_BD_BACKEND", raising=False)
    assert selected_backend() == "auto"


def test_backend_env_override(monkeypatch):
    monkeypatch.setenv("BATON_BD_BACKEND", "bd")
    assert selected_backend() == "bd"
    monkeypatch.setenv("BATON_BD_BACKEND", "sqlite")
    assert selected_backend() == "sqlite"
    monkeypatch.setenv("BATON_BD_BACKEND", "bogus")
    assert selected_backend() == "auto"  # unknown falls back to the new default


def test_bd_enabled_default_on(monkeypatch):
    monkeypatch.delenv("BATON_BD_ENABLED", raising=False)
    assert bd_enabled() is True
    monkeypatch.setenv("BATON_BD_ENABLED", "0")
    assert bd_enabled() is False


def test_make_bead_store_sqlite_when_pinned(tmp_path, monkeypatch):
    # ADR-13b step F: default is now ``auto``.  Explicitly pin to sqlite to
    # get a BeadStore regardless of whether bd is installed.
    monkeypatch.setenv("BATON_BD_BACKEND", "sqlite")
    store = make_bead_store(tmp_path / "baton.db")
    from agent_baton.core.engine.bead_store import BeadStore

    assert isinstance(store, BeadStore)


def test_make_bead_store_auto_uses_bd_when_available(tmp_path, monkeypatch):
    # ADR-13b step F: ``auto`` resolves to BdBeadStore when bd is installed.
    from agent_baton.core.engine.bd_bead_store import BdBeadStore
    from agent_baton.core.engine.bead_store import BeadStore

    monkeypatch.delenv("BATON_BD_BACKEND", raising=False)
    store = make_bead_store(tmp_path / "baton.db")
    if _BD_AVAILABLE:
        assert isinstance(store, BdBeadStore), (
            "auto+bd-present must return BdBeadStore"
        )
    else:
        assert isinstance(store, BeadStore), (
            "auto+bd-absent must fall back to BeadStore"
        )


# ---------------------------------------------------------------------------
# Integration against the real bd CLI (skipped when not installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _BD_AVAILABLE, reason="bd binary not installed")
class TestBdIntegration:
    def _store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BATON_BD_BACKEND", "bd")
        monkeypatch.setenv("BATON_BD_PREFIX", "bd")
        return make_bead_store(tmp_path / ".claude" / "team-context" / "baton.db",
                               repo_root=tmp_path)

    def test_client_version(self, tmp_path):
        client = BdClient(tmp_path)
        assert client.available()
        assert "bd version" in client.version().lower() or client.version()

    def test_init_is_non_invasive(self, tmp_path):
        """Regression: `bd init` must NOT onboard/auto-commit the host repo.

        Earlier, BdClient.init ran a bare `bd init` whose onboarding appended a
        BEADS block to CLAUDE.md, wrote AGENTS.md/.agents/.codex, installed git
        hooks via core.hooksPath (which then auto-committed), and edited the
        tracked .gitignore. init() now passes --skip-agents/--skip-hooks/
        --setup-exclude so none of that happens.
        """
        import subprocess

        repo = tmp_path
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"],
                       check=True, capture_output=True)
        # Seed a CLAUDE.md so we can assert bd never touches it.
        claude_md = repo / "CLAUDE.md"
        claude_md.write_text("# project rules\n", encoding="utf-8")
        original = claude_md.read_text(encoding="utf-8")

        BdClient(repo).init()

        # No onboarding artifacts.
        assert not (repo / "AGENTS.md").exists()
        assert not (repo / ".agents").exists()
        assert not (repo / ".codex").exists()
        # CLAUDE.md untouched.
        assert claude_md.read_text(encoding="utf-8") == original
        # No hooksPath hijack.
        hp = subprocess.run(
            ["git", "-C", str(repo), "config", "--local", "--get", "core.hooksPath"],
            capture_output=True, text=True,
        )
        assert hp.returncode != 0 or not hp.stdout.strip()
        # .beads kept local via .git/info/exclude (not the tracked .gitignore).
        exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        assert ".beads" in exclude

    def test_write_read_roundtrip(self, tmp_path, monkeypatch):
        store = self._store(tmp_path, monkeypatch)
        bead = _sample_bead()
        bead_id = store.write(bead)
        assert bead_id == "bd-a1b2"
        got = store.read("bd-a1b2")
        assert got is not None
        assert got.bead_id == "bd-a1b2"
        assert got.agent_name == "architect"
        assert got.bead_type == "decision"
        assert got.confidence == "high"
        assert got.affected_files == ["auth.py"]

    def test_query_by_task_and_type(self, tmp_path, monkeypatch):
        store = self._store(tmp_path, monkeypatch)
        store.write(_sample_bead(bead_id="bd-aaaa", bead_type="decision"))
        store.write(_sample_bead(bead_id="bd-bbbb", bead_type="warning",
                                 content="careful here"))
        decisions = store.query(task_id="T-42", bead_type="decision")
        assert {b.bead_id for b in decisions} == {"bd-aaaa"}
        all_task = store.query(task_id="T-42")
        assert {b.bead_id for b in all_task} == {"bd-aaaa", "bd-bbbb"}

    def test_quality_score_and_retrieval_count_persist(self, tmp_path, monkeypatch):
        """ADR-13b review blocker #2: BEAD_FEEDBACK analytics must persist on bd."""
        store = self._store(tmp_path, monkeypatch)
        store.write(_sample_bead(bead_id="bd-qual"))
        store.update_quality_score("bd-qual", 0.5)
        store.update_quality_score("bd-qual", 0.25)
        store.increment_retrieval_count("bd-qual")
        store.increment_retrieval_count("bd-qual")
        got = store.read("bd-qual")
        assert got is not None
        assert abs(got.quality_score - 0.75) < 1e-9
        assert got.retrieval_count == 2

    def test_close_marks_closed(self, tmp_path, monkeypatch):
        store = self._store(tmp_path, monkeypatch)
        store.write(_sample_bead(bead_id="bd-cccc"))
        store.close("bd-cccc", summary="done — see commit abc123")
        got = store.read("bd-cccc")
        assert got is not None
        assert got.status == "closed"

    def test_ready_excludes_blocked(self, tmp_path, monkeypatch):
        store = self._store(tmp_path, monkeypatch)
        store.write(_sample_bead(bead_id="bd-dep0", content="prerequisite"))
        blocked = _sample_bead(bead_id="bd-dep1", content="needs dep0",
                               links=[BeadLink(target_bead_id="bd-dep0", link_type="blocks")])
        store.write(blocked)
        ready_ids = {b.bead_id for b in store.ready("T-42")}
        # The prerequisite is ready; the blocked bead is not.
        assert "bd-dep0" in ready_ids
        assert "bd-dep1" not in ready_ids
