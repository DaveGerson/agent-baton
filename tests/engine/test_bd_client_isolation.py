"""Isolation guarantees for :class:`~agent_baton.core.engine.bd_client.BdClient`.

``BdClient`` is the single seam through which every ``bd`` invocation passes.
It makes two promises that the rest of the engine (and every user project)
depends on:

1. **Host-repo isolation** — ``init()`` must not mutate any *tracked* file in
   the project it bootstraps.  ``bd init`` runs an onboarding flow that edits
   the tracked ``.gitignore``; ``--setup-exclude`` moves ``.beads/`` into
   ``.git/info/exclude`` but does *not* stop the ``.gitignore`` edit.  The
   appended block includes a bare ``*.db`` rule, which is broad enough to
   git-ignore ``.claude/team-context/baton.db`` and any other ``*.db`` the
   user tracks.

2. **Workspace isolation** — every ``bd`` command must resolve to the bead
   database owned by the client's ``repo_root``.  ``bd`` auto-discovers
   ``.beads/`` by walking *upward* from cwd (all the way to ``/``), so a
   client rooted at a directory with no ``.beads/`` silently reads and writes
   some ancestor's bead store.  That is cross-project bead leakage in
   production and the root cause of shared-state bleed between tests.

Tests that need the real ``bd`` binary are skipped when it is not installed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from agent_baton.core.engine.bd_client import BdClient, BdError
from agent_baton.core.engine.bead_backend import make_bead_store

_BD_AVAILABLE = shutil.which("bd") is not None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )


def _make_repo(repo: Path, gitignore_body: str) -> bytes:
    """Create a git repo with a committed ``.gitignore``; return its bytes."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    gitignore = repo / ".gitignore"
    gitignore.write_bytes(gitignore_body.encode("utf-8"))
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-q", "-m", "seed")
    return gitignore.read_bytes()


def _titles(client: BdClient) -> list[str]:
    """Titles visible to *client*, treating a hard bd error as 'nothing visible'.

    A correctly scoped client pointed at a directory with no bead database may
    legitimately either return nothing or raise; what it must never do is
    surface some *other* workspace's beads.
    """
    try:
        return [str(issue.get("title", "")) for issue in client.list()]
    except BdError:
        return []


# ---------------------------------------------------------------------------
# 1. Host-repo isolation: bd init must not touch tracked files
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _BD_AVAILABLE, reason="bd binary not installed")
def test_init_leaves_tracked_gitignore_byte_identical(tmp_path):
    """`BdClient.init()` must not modify the project's tracked `.gitignore`."""
    repo = tmp_path / "proj"
    original = _make_repo(repo, "node_modules/\n# sentinel-do-not-touch\n")

    BdClient(repo).init()

    assert (repo / ".gitignore").read_bytes() == original, (
        "bd init rewrote the project's tracked .gitignore; BdClient.init() must "
        "leave it byte-identical.\nNow:\n"
        + (repo / ".gitignore").read_text(encoding="utf-8")
    )


@pytest.mark.skipif(not _BD_AVAILABLE, reason="bd binary not installed")
def test_init_leaves_no_tracked_file_dirty(tmp_path):
    """After init the working tree must have no modified tracked files."""
    repo = tmp_path / "proj"
    _make_repo(repo, "node_modules/\n")

    BdClient(repo).init()

    porcelain = _git(repo, "status", "--porcelain", "--", ".gitignore").stdout
    assert porcelain.strip() == "", (
        f"bd init left .gitignore dirty in the user's working tree: {porcelain!r}"
    )

    dirty = _git(repo, "diff", "--name-only", "HEAD").stdout.strip()
    assert dirty == "", f"bd init modified tracked file(s): {dirty!r}"


@pytest.mark.skipif(not _BD_AVAILABLE, reason="bd binary not installed")
def test_init_does_not_git_ignore_baton_state(tmp_path):
    """The bd onboarding `*.db` rule must not swallow baton's own state files."""
    repo = tmp_path / "proj"
    _make_repo(repo, "node_modules/\n")
    db = repo / ".claude" / "team-context" / "baton.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"")
    fixture = repo / "fixtures" / "seed.db"
    fixture.parent.mkdir(parents=True, exist_ok=True)
    fixture.write_bytes(b"")

    BdClient(repo).init()

    for path in (".claude/team-context/baton.db", "fixtures/seed.db"):
        proc = _git(repo, "check-ignore", "-v", "--", path)
        assert proc.returncode != 0, (
            f"{path} became git-ignored after bd init "
            f"(rule: {proc.stdout.strip()!r}); user *.db files must stay visible"
        )


def test_init_restores_gitignore_when_bd_rewrites_it(tmp_path, monkeypatch):
    """Unit companion (no bd binary): init() must repair a rewritten .gitignore.

    Simulates the observed bd 1.2.x behaviour — the init subprocess creates
    ``.beads/`` and appends its own block to the tracked ``.gitignore`` — and
    asserts ``init()`` hands the file back unchanged.
    """
    repo = tmp_path / "proj"
    repo.mkdir()
    gitignore = repo / ".gitignore"
    original = b"node_modules/\n# sentinel-do-not-touch\n"
    gitignore.write_bytes(original)

    client = BdClient(repo)

    def _fake_run(args, *, json_output):
        assert args[0] == "init"
        (repo / ".beads").mkdir(exist_ok=True)
        with gitignore.open("a", encoding="utf-8") as fh:
            fh.write("\n# Beads / Dolt files (added by bd init)\n.dolt/\n*.db\n")
        return ""

    monkeypatch.setattr(client, "_run", _fake_run)

    client.init()

    assert gitignore.read_bytes() == original, (
        "BdClient.init() did not restore the tracked .gitignore that bd rewrote"
    )


def test_init_does_not_create_gitignore_when_absent(tmp_path, monkeypatch):
    """A project with no `.gitignore` must still have none after init."""
    repo = tmp_path / "proj"
    repo.mkdir()
    gitignore = repo / ".gitignore"

    client = BdClient(repo)

    def _fake_run(args, *, json_output):
        (repo / ".beads").mkdir(exist_ok=True)
        gitignore.write_text("# Beads / Dolt files (added by bd init)\n*.db\n",
                             encoding="utf-8")
        return ""

    monkeypatch.setattr(client, "_run", _fake_run)

    client.init()

    assert not gitignore.exists(), (
        "BdClient.init() left behind a .gitignore that bd created; baton must "
        "not introduce one into a project that had none"
    )


# ---------------------------------------------------------------------------
# 2. Workspace isolation: bd must not escape upward out of repo_root
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _BD_AVAILABLE, reason="bd binary not installed")
def test_client_does_not_read_ancestor_bead_workspace(tmp_path):
    """A client rooted below another bd workspace must not see its beads."""
    outer = tmp_path / "outer"
    outer.mkdir()
    outer_client = BdClient(outer)
    outer_client.init()
    outer_client.create("ANCESTOR-SENTINEL-BEAD", bead_id="bd-anc1")
    assert "ANCESTOR-SENTINEL-BEAD" in _titles(outer_client), (
        "precondition failed: the ancestor workspace does not hold the sentinel"
    )

    nested = outer / "workspaces" / "project"
    nested.mkdir(parents=True)
    nested_client = BdClient(nested)
    assert nested_client.db_exists() is False

    assert "ANCESTOR-SENTINEL-BEAD" not in _titles(nested_client), (
        "bd walked up out of repo_root and read the ancestor directory's bead "
        "database; every bd invocation must be scoped to repo_root/.beads"
    )
    assert nested_client.show("bd-anc1") is None, (
        "show() resolved a bead belonging to an ancestor workspace"
    )


@pytest.mark.skipif(not _BD_AVAILABLE, reason="bd binary not installed")
def test_bead_store_query_does_not_leak_ancestor_beads(tmp_path):
    """`BdBeadStore.query()` on a fresh project must not see ancestor beads.

    Reads never call `_ensure_init()`, so an un-bootstrapped project is exactly
    where the upward workspace walk leaks another project's memory in.
    """
    outer = tmp_path / "outer"
    outer.mkdir()
    outer_client = BdClient(outer)
    outer_client.init()
    outer_client.create("ANCESTOR-SENTINEL-BEAD", bead_id="bd-anc2")

    nested = outer / "workspaces" / "project"
    nested.mkdir(parents=True)
    store = make_bead_store(
        nested / ".claude" / "team-context" / "baton.db", repo_root=nested
    )

    contents = [b.content for b in store.query(limit=200)]
    assert not any("ANCESTOR-SENTINEL-BEAD" in c for c in contents), (
        "BdBeadStore.query() returned beads owned by an ancestor workspace: "
        f"{contents[:5]}"
    )
    assert store.read("bd-anc2") is None, (
        "BdBeadStore.read() resolved a bead from an ancestor workspace"
    )


@pytest.mark.skipif(not _BD_AVAILABLE, reason="bd binary not installed")
@pytest.mark.xfail(
    reason=(
        "Capability temporarily unsupported, not abandoned.  This test passed by "
        "way of _ensure_git_boundary running `git init` in any directory lacking "
        "a .git, which is unsafe two ways: the repo_root derivation walks "
        "ancestors and can hand it $HOME or /, and creating a nested repo inside "
        "the user's project makes `git add -A` there fail with 'does not have a "
        "commit checked out'.  _git_boundary_refusal now declines those cases, "
        "which costs this nested-project capability.  bd offers no substitute: "
        "--db, BEADS_DIR and a pre-created .beads/ were each measured and none "
        "stops `bd init` walking to an ancestor workspace.  The WS-F053 rework "
        "must restore this through bd-native scoping and delete this marker; the "
        "assertions below are the acceptance criteria and must not be relaxed."
    ),
    strict=False,
)
def test_nested_project_bootstraps_its_own_workspace(tmp_path):
    """A nested project must be able to init, and its writes must stay local."""
    outer = tmp_path / "outer"
    outer.mkdir()
    outer_client = BdClient(outer)
    outer_client.init()

    nested = outer / "workspaces" / "project"
    nested.mkdir(parents=True)
    nested_client = BdClient(nested)
    try:
        nested_client.init()
    except BdError as exc:
        pytest.fail(
            "BdClient.init() aborted in a nested project because bd discovered "
            "the ancestor workspace instead of bootstrapping repo_root; init "
            f"must be scoped to repo_root/.beads.  bd said: {exc}"
        )

    assert (nested / ".beads").is_dir(), (
        "init() reported success but created no .beads/ under repo_root"
    )

    nested_client.create("NESTED-ONLY-BEAD", bead_id="bd-nst1")
    assert "NESTED-ONLY-BEAD" not in _titles(outer_client), (
        "a nested project's bead was written into the ancestor workspace"
    )
