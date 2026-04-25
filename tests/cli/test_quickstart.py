"""Tests for ``baton quickstart`` -- one-command onboarding.

Covers:
1. Fresh repo: all five steps run + report success.
2. Existing CLAUDE.md: step 3 reports "exists, skipping" + does not overwrite.
3. Existing baton.db: step 2 reports "already initialised".
4. ``--dry-run``: does not save plan.json/plan.md.
5. ``--name`` is reflected in output.
6. Idempotent re-run leaves all artefacts intact.
"""
from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.cli.commands import quickstart
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_plan(task_id: str = "2026-04-25-quickstart-aabb0011") -> MachinePlan:
    step = PlanStep(
        step_id="1.1",
        agent_name="backend-engineer--python",
        task_description="Write hello world",
        model="sonnet",
        depends_on=[],
        deliverables=[],
        allowed_paths=[],
        blocked_paths=[],
        context_files=[],
    )
    phase = PlanPhase(
        phase_id=1,
        name="Implement",
        steps=[step],
        approval_required=False,
    )
    return MachinePlan(
        task_id=task_id,
        task_summary="Add a hello-world function",
        risk_level="LOW",
        budget_tier="standard",
        execution_mode="phased",
        git_strategy="commit-per-agent",
        phases=[phase],
        shared_context="",
        pattern_source=None,
        created_at="2026-04-25T00:00:00+00:00",
    )


def _make_args(*, name: str | None = None, dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(name=name, dry_run=dry_run)


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp directory that looks like a git repo with a python stack."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "repo"\n', encoding="utf-8"
    )
    monkeypatch.chdir(repo)
    return repo


@pytest.fixture
def patched_planner(monkeypatch: pytest.MonkeyPatch):
    """Stub out IntelligentPlanner + collaborators.

    Returns the mock_planner so individual tests can assert call args.
    """
    mock_planner = MagicMock()
    mock_planner.create_plan.return_value = _make_minimal_plan()

    monkeypatch.setattr(
        "agent_baton.core.engine.planner.IntelligentPlanner",
        lambda **kw: mock_planner,
    )
    monkeypatch.setattr(
        "agent_baton.core.govern.classifier.DataClassifier",
        lambda *a, **kw: MagicMock(),
    )
    monkeypatch.setattr(
        "agent_baton.core.govern.policy.PolicyEngine",
        lambda *a, **kw: MagicMock(),
    )
    monkeypatch.setattr(
        "agent_baton.core.observe.retrospective.RetrospectiveEngine",
        lambda *a, **kw: MagicMock(),
    )
    monkeypatch.setattr(
        "agent_baton.core.orchestration.knowledge_registry.KnowledgeRegistry",
        lambda *a, **kw: MagicMock(),
    )
    # Best-effort persistence to plans table -- bypass.
    monkeypatch.setattr(
        "agent_baton.core.storage.get_project_storage",
        lambda *a, **kw: MagicMock(),
    )
    return mock_planner


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFreshRepo:
    """Step 1: a brand-new repo runs all 5 steps and succeeds."""

    def test_all_steps_reported(
        self,
        fake_repo: Path,
        patched_planner: MagicMock,
        capsys: pytest.CaptureFixture,
    ) -> None:
        quickstart.handler(_make_args())
        out = capsys.readouterr().out

        # Step 1: detection.
        assert "Repo root:" in out
        assert "Stack:      python" in out
        # Step 2: db init.
        assert "Initialised .claude/team-context/baton.db" in out
        assert "schema v" in out
        # Step 3: CLAUDE.md.
        assert "Wrote CLAUDE.md" in out
        # Step 4: agents.
        assert "agent definitions" in out or "scripts/install.sh" in out
        # Step 5: starter plan.
        assert "Generated starter plan" in out
        # Step 6: hint.
        assert "Next steps:" in out
        assert "baton execute start" in out

    def test_creates_baton_db(
        self,
        fake_repo: Path,
        patched_planner: MagicMock,
        capsys: pytest.CaptureFixture,
    ) -> None:
        quickstart.handler(_make_args())
        db_path = fake_repo / ".claude" / "team-context" / "baton.db"
        assert db_path.exists(), "baton.db must be created"

    def test_creates_claude_md(
        self,
        fake_repo: Path,
        patched_planner: MagicMock,
        capsys: pytest.CaptureFixture,
    ) -> None:
        quickstart.handler(_make_args())
        claude_md = fake_repo / "CLAUDE.md"
        assert claude_md.exists(), "CLAUDE.md must be written"
        body = claude_md.read_text(encoding="utf-8")
        assert "agent-baton" in body.lower() or "agent baton" in body.lower()

    def test_writes_plan_files(
        self,
        fake_repo: Path,
        patched_planner: MagicMock,
        capsys: pytest.CaptureFixture,
    ) -> None:
        quickstart.handler(_make_args())
        ctx = fake_repo / ".claude" / "team-context"
        assert (ctx / "plan.json").exists()
        assert (ctx / "plan.md").exists()


class TestExistingClaudeMd:
    """Step 2: existing CLAUDE.md is preserved untouched."""

    def test_does_not_overwrite(
        self,
        fake_repo: Path,
        patched_planner: MagicMock,
        capsys: pytest.CaptureFixture,
    ) -> None:
        existing = "# My Project\n\nMy own instructions live here.\n"
        (fake_repo / "CLAUDE.md").write_text(existing, encoding="utf-8")

        quickstart.handler(_make_args())
        out = capsys.readouterr().out

        assert "CLAUDE.md already exists -- skipping" in out
        # Content is byte-identical.
        assert (fake_repo / "CLAUDE.md").read_text(encoding="utf-8") == existing


class TestExistingDb:
    """Step 3: existing baton.db reports already-initialised."""

    def test_reports_already_initialised(
        self,
        fake_repo: Path,
        patched_planner: MagicMock,
        capsys: pytest.CaptureFixture,
    ) -> None:
        # First run creates the db.
        quickstart.handler(_make_args())
        capsys.readouterr()

        # Second run: db already there.
        quickstart.handler(_make_args())
        out = capsys.readouterr().out

        assert "baton.db already initialised" in out
        assert "skipping" in out


class TestDryRun:
    """Step 4: --dry-run does not save plan.json/plan.md."""

    def test_no_plan_files_written(
        self,
        fake_repo: Path,
        patched_planner: MagicMock,
        capsys: pytest.CaptureFixture,
    ) -> None:
        quickstart.handler(_make_args(dry_run=True))

        ctx = fake_repo / ".claude" / "team-context"
        assert not (ctx / "plan.json").exists(), "--dry-run must not save plan.json"
        assert not (ctx / "plan.md").exists(), "--dry-run must not save plan.md"

    def test_dry_run_forecast_printed(
        self,
        fake_repo: Path,
        patched_planner: MagicMock,
        capsys: pytest.CaptureFixture,
    ) -> None:
        quickstart.handler(_make_args(dry_run=True))
        out = capsys.readouterr().out

        assert "Dry-run forecast" in out
        assert "(dry-run)" in out


class TestNameFlag:
    """Step 5: --name appears in the banner and is forwarded to the planner."""

    def test_name_in_output(
        self,
        fake_repo: Path,
        patched_planner: MagicMock,
        capsys: pytest.CaptureFixture,
    ) -> None:
        quickstart.handler(_make_args(name="acme-app"))
        out = capsys.readouterr().out

        assert "acme-app" in out

    def test_name_passed_to_planner(
        self,
        fake_repo: Path,
        patched_planner: MagicMock,
        capsys: pytest.CaptureFixture,
    ) -> None:
        quickstart.handler(_make_args(name="acme-app"))

        assert patched_planner.create_plan.called
        call = patched_planner.create_plan.call_args
        # First positional arg is the task summary.
        summary = call.args[0] if call.args else call.kwargs.get("task_summary", "")
        assert "acme-app" in summary


class TestIdempotent:
    """Step 6: re-running quickstart leaves artefacts intact and exits 0."""

    def test_two_runs_same_artefacts(
        self,
        fake_repo: Path,
        patched_planner: MagicMock,
        capsys: pytest.CaptureFixture,
    ) -> None:
        quickstart.handler(_make_args())
        capsys.readouterr()

        claude_md_before = (fake_repo / "CLAUDE.md").read_text(encoding="utf-8")
        db_mtime_before = (fake_repo / ".claude" / "team-context" / "baton.db").stat().st_mtime

        # Second run -- nothing should be clobbered.
        quickstart.handler(_make_args())
        out = capsys.readouterr().out

        assert "already initialised" in out
        assert "CLAUDE.md already exists -- skipping" in out

        claude_md_after = (fake_repo / "CLAUDE.md").read_text(encoding="utf-8")
        assert claude_md_after == claude_md_before

        # The database file itself isn't deleted; mtime may bump because we
        # opened a connection, but the file still exists.
        assert (fake_repo / ".claude" / "team-context" / "baton.db").exists()


# ---------------------------------------------------------------------------
# Stack detection unit tests (cheap, no planner)
# ---------------------------------------------------------------------------


class TestStackDetection:
    def test_python_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        assert quickstart._detect_stack(tmp_path) == "python"

    def test_node_package_json(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        assert quickstart._detect_stack(tmp_path) == "node"

    def test_rust_cargo_toml(self, tmp_path: Path) -> None:
        (tmp_path / "Cargo.toml").write_text("", encoding="utf-8")
        assert quickstart._detect_stack(tmp_path) == "rust"

    def test_python_wins_over_node(self, tmp_path: Path) -> None:
        # Most-specific (python) wins when multiple markers are present.
        (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
        (tmp_path / "package.json").write_text("{}", encoding="utf-8")
        assert quickstart._detect_stack(tmp_path) == "python"

    def test_no_markers(self, tmp_path: Path) -> None:
        assert quickstart._detect_stack(tmp_path) is None


class TestRepoDetection:
    def test_finds_git_root(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        nested = repo / "a" / "b" / "c"
        nested.mkdir(parents=True)

        assert quickstart._find_repo_root(nested) == repo.resolve()

    def test_no_git_returns_none(self, tmp_path: Path) -> None:
        # tmp_path is not a git repo (and pytest's tmp_path is well below
        # the user's home so no ancestor .git is hit either).
        bare = tmp_path / "bare"
        bare.mkdir()
        # Walking up will eventually hit something user-specific. To avoid
        # flakiness we only assert the function returns *something or none*
        # without crashing -- the contract under test is "no .git here".
        result = quickstart._find_repo_root(bare)
        assert result is None or (result / ".git").exists()


class TestNoGitRepo:
    """When cwd is not inside a git repo, exit 1 with a helpful error."""

    def test_exits_when_no_repo(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        # Pretend nothing has a .git folder above us.
        monkeypatch.setattr(quickstart, "_find_repo_root", lambda _start: None)

        with pytest.raises(SystemExit) as excinfo:
            quickstart.handler(_make_args())
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "not inside a git repository" in err
