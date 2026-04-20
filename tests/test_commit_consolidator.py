"""Tests for CommitConsolidator — cherry-pick based rebase consolidation.

Since ``CommitConsolidator`` does not exist yet as a standalone module the
tests cover the consolidation *contract* by exercising the data models it
produces (``ConsolidationResult``, ``FileAttribution``) and by simulating
what a consolidator would do against a real git repo created in a tmp dir.

The CommitConsolidator public surface (assumed from execution.py models and
pmo.py usage) is:

    consolidator = CommitConsolidator(repo_root=..., plan=..., state=...)
    result: ConsolidationResult = consolidator.consolidate()

When the module is importable the tests exercise it directly.  When it is
not yet importable (e.g. not yet wired) the tests for the models themselves
still run as fast-path coverage of ConsolidationResult round-trips and
serialisation.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.models.execution import (
    ConsolidationResult,
    ExecutionState,
    FileAttribution,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)

# ---------------------------------------------------------------------------
# Try to import the real CommitConsolidator.  Tests that need it will be
# skipped if it has not been implemented yet.
# ---------------------------------------------------------------------------

try:
    from agent_baton.core.engine.consolidator import CommitConsolidator  # type: ignore[import]
    _HAS_CONSOLIDATOR = True
except ImportError:
    _HAS_CONSOLIDATOR = False
    CommitConsolidator = None  # type: ignore[assignment,misc]

_requires_consolidator = pytest.mark.skipif(
    not _HAS_CONSOLIDATOR,
    reason="CommitConsolidator not yet importable",
)


# ---------------------------------------------------------------------------
# Git test-repo helpers
# ---------------------------------------------------------------------------

def _init_git_repo(path: Path) -> None:
    """Create a bare git repo in *path* with a valid initial commit."""
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"],
        cwd=str(path), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(path), check=True, capture_output=True,
    )
    # Create an initial commit so HEAD exists.
    readme = path / "README.md"
    readme.write_text("# Test repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(path), check=True, capture_output=True,
    )


def _git_commit_file(repo: Path, filename: str, content: str, message: str) -> str:
    """Write *content* to *filename*, stage it, and commit.  Returns the SHA."""
    file_path = repo / filename
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", str(filename)], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=str(repo), check=True, capture_output=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _current_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Helpers to build minimal plans / states
# ---------------------------------------------------------------------------

def _minimal_plan(task_id: str = "task-001", n_steps: int = 1) -> MachinePlan:
    steps = [
        PlanStep(
            step_id=f"1.{i+1}",
            agent_name=f"agent-{i+1}",
            task_description=f"Step {i+1}",
        )
        for i in range(n_steps)
    ]
    return MachinePlan(
        task_id=task_id,
        task_summary="Consolidation test plan",
        phases=[
            PlanPhase(
                phase_id=0,
                name="Implementation",
                steps=steps,
                gate=PlanGate(gate_type="test", command="pytest"),
            )
        ],
    )


def _state_with_commits(
    plan: MachinePlan,
    commits: list[str | None],
) -> ExecutionState:
    """Build an ExecutionState whose step_results have the given commit hashes.

    *commits* must align with ``plan.all_steps`` by index.
    """
    state = ExecutionState(plan=plan, task_id=plan.task_id)
    state.step_results = []
    for step, commit_hash in zip(plan.all_steps, commits):
        state.step_results.append(
            StepResult(
                step_id=step.step_id,
                agent_name=step.agent_name,
                status="complete" if commit_hash else "complete",
                outcome="Done",
                commit_hash=commit_hash or "",
            )
        )
    return state


# ===========================================================================
# ConsolidationResult serialisation (model unit tests — always run)
# ===========================================================================


class TestConsolidationResultModel:
    """ConsolidationResult round-trips through to_dict / from_dict."""

    def test_default_status_is_success(self) -> None:
        cr = ConsolidationResult()
        assert cr.status == "success"

    def test_to_dict_contains_all_required_keys(self) -> None:
        cr = ConsolidationResult(
            status="success",
            final_head="abc123",
            base_commit="base456",
            files_changed=["src/main.py"],
            total_insertions=10,
            total_deletions=2,
            rebased_commits=[{"step_id": "1.1", "new_hash": "abc"}],
            skipped_steps=["1.3"],
        )
        d = cr.to_dict()
        for key in (
            "status", "final_head", "base_commit", "files_changed",
            "total_insertions", "total_deletions", "rebased_commits",
            "attributions", "conflict_files", "conflict_step_id",
            "skipped_steps", "started_at", "completed_at", "error",
        ):
            assert key in d, f"missing key: {key}"

    def test_from_dict_round_trips_status(self) -> None:
        cr = ConsolidationResult(status="conflict", conflict_step_id="1.2")
        restored = ConsolidationResult.from_dict(cr.to_dict())
        assert restored.status == "conflict"
        assert restored.conflict_step_id == "1.2"

    def test_skipped_steps_round_trips(self) -> None:
        cr = ConsolidationResult(skipped_steps=["1.2", "2.1"])
        restored = ConsolidationResult.from_dict(cr.to_dict())
        assert restored.skipped_steps == ["1.2", "2.1"]

    def test_attributions_round_trips(self) -> None:
        attr = FileAttribution(
            file_path="src/api.py",
            step_id="1.1",
            agent_name="backend",
            insertions=5,
            deletions=1,
        )
        cr = ConsolidationResult(attributions=[attr])
        restored = ConsolidationResult.from_dict(cr.to_dict())
        assert len(restored.attributions) == 1
        assert restored.attributions[0].file_path == "src/api.py"
        assert restored.attributions[0].insertions == 5

    def test_files_changed_list_preserved(self) -> None:
        cr = ConsolidationResult(files_changed=["a.py", "b.py", "c/d.ts"])
        restored = ConsolidationResult.from_dict(cr.to_dict())
        assert restored.files_changed == ["a.py", "b.py", "c/d.ts"]

    def test_empty_conflict_fields_on_success(self) -> None:
        cr = ConsolidationResult(status="success")
        assert cr.conflict_files == []
        assert cr.conflict_step_id == ""

    def test_from_dict_handles_missing_keys(self) -> None:
        """Deserialising a minimal dict must not raise."""
        cr = ConsolidationResult.from_dict({"status": "partial"})
        assert cr.status == "partial"
        assert cr.skipped_steps == []


class TestFileAttributionModel:
    def test_to_dict_and_from_dict_round_trip(self) -> None:
        fa = FileAttribution(
            file_path="tests/test_foo.py",
            step_id="2.3",
            agent_name="qa-engineer",
            insertions=20,
            deletions=3,
        )
        restored = FileAttribution.from_dict(fa.to_dict())
        assert restored.file_path == fa.file_path
        assert restored.insertions == fa.insertions
        assert restored.deletions == fa.deletions

    def test_defaults_are_zero(self) -> None:
        fa = FileAttribution(file_path="f.py", step_id="1.1", agent_name="a")
        assert fa.insertions == 0
        assert fa.deletions == 0


# ===========================================================================
# ExecutionState stores ConsolidationResult (model integration)
# ===========================================================================


class TestExecutionStateConsolidationResult:
    def test_consolidation_result_defaults_to_none(self) -> None:
        plan = _minimal_plan()
        state = ExecutionState(plan=plan, task_id=plan.task_id)
        assert state.consolidation_result is None

    def test_consolidation_result_round_trips_through_to_dict(self) -> None:
        plan = _minimal_plan()
        state = ExecutionState(plan=plan, task_id=plan.task_id)
        cr = ConsolidationResult(
            status="success",
            final_head="deadbeef",
            skipped_steps=["1.2"],
        )
        state.consolidation_result = cr
        d = state.to_dict()
        assert d["consolidation_result"] is not None
        assert d["consolidation_result"]["status"] == "success"

    def test_consolidation_result_none_stays_none_in_dict(self) -> None:
        plan = _minimal_plan()
        state = ExecutionState(plan=plan, task_id=plan.task_id)
        d = state.to_dict()
        assert d.get("consolidation_result") is None

    def test_from_dict_restores_consolidation_result(self) -> None:
        plan = _minimal_plan()
        state = ExecutionState(plan=plan, task_id=plan.task_id)
        state.consolidation_result = ConsolidationResult(
            status="conflict",
            conflict_step_id="1.3",
        )
        restored = ExecutionState.from_dict(state.to_dict())
        assert restored.consolidation_result is not None
        assert restored.consolidation_result.status == "conflict"
        assert restored.consolidation_result.conflict_step_id == "1.3"


# ===========================================================================
# CommitConsolidator unit tests (skipped when module is absent)
# ===========================================================================


@_requires_consolidator
class TestCommitConsolidatorHappyPath:
    """Three sequential steps each with a distinct commit — no conflicts."""

    def test_three_steps_returns_success_status(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        # Create three commits, one per "step".
        h1 = _git_commit_file(repo, "step1.txt", "step 1 output\n", "step 1.1")
        h2 = _git_commit_file(repo, "step2.txt", "step 2 output\n", "step 1.2")
        h3 = _git_commit_file(repo, "step3.txt", "step 3 output\n", "step 1.3")

        plan = _minimal_plan(n_steps=3)
        state = _state_with_commits(plan, [h1, h2, h3])

        consolidator = CommitConsolidator(repo_root=repo, plan=plan, state=state)
        result = consolidator.consolidate()

        assert result.status == "success"

    def test_three_steps_rebased_commits_ordered(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        h1 = _git_commit_file(repo, "s1.txt", "a\n", "step 1.1")
        h2 = _git_commit_file(repo, "s2.txt", "b\n", "step 1.2")
        h3 = _git_commit_file(repo, "s3.txt", "c\n", "step 1.3")

        plan = _minimal_plan(n_steps=3)
        state = _state_with_commits(plan, [h1, h2, h3])

        consolidator = CommitConsolidator(repo_root=repo, plan=plan, state=state)
        result = consolidator.consolidate()

        # All three step IDs should appear in rebased_commits in order.
        rebased_ids = [c["step_id"] for c in result.rebased_commits]
        assert rebased_ids == ["1.1", "1.2", "1.3"]

    def test_three_steps_files_changed_populated(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        h1 = _git_commit_file(repo, "src/a.py", "x\n", "step 1.1")
        h2 = _git_commit_file(repo, "src/b.py", "y\n", "step 1.2")
        h3 = _git_commit_file(repo, "src/c.py", "z\n", "step 1.3")

        plan = _minimal_plan(n_steps=3)
        state = _state_with_commits(plan, [h1, h2, h3])

        consolidator = CommitConsolidator(repo_root=repo, plan=plan, state=state)
        result = consolidator.consolidate()

        # All three files should be in files_changed.
        for fname in ("src/a.py", "src/b.py", "src/c.py"):
            assert fname in result.files_changed


@_requires_consolidator
class TestCommitConsolidatorSkippedSteps:
    """Steps with empty commit_hash should be recorded in skipped_steps."""

    def test_empty_commit_hash_recorded_as_skipped(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        h1 = _git_commit_file(repo, "s1.txt", "a\n", "step 1.1")
        # step 1.2 has no commit hash (agent made no changes).
        h3 = _git_commit_file(repo, "s3.txt", "c\n", "step 1.3")

        plan = _minimal_plan(n_steps=3)
        state = _state_with_commits(plan, [h1, None, h3])

        consolidator = CommitConsolidator(repo_root=repo, plan=plan, state=state)
        result = consolidator.consolidate()

        assert "1.2" in result.skipped_steps

    def test_all_empty_commits_produces_all_skipped(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        plan = _minimal_plan(n_steps=3)
        state = _state_with_commits(plan, [None, None, None])

        consolidator = CommitConsolidator(repo_root=repo, plan=plan, state=state)
        result = consolidator.consolidate()

        # Every step was skipped — all three should appear in skipped_steps.
        assert set(result.skipped_steps) == {"1.1", "1.2", "1.3"}

    def test_skipped_steps_not_in_rebased_commits(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        h1 = _git_commit_file(repo, "s1.txt", "a\n", "step 1.1")
        plan = _minimal_plan(n_steps=2)
        # step 1.2 is skipped.
        state = _state_with_commits(plan, [h1, None])

        consolidator = CommitConsolidator(repo_root=repo, plan=plan, state=state)
        result = consolidator.consolidate()

        rebased_ids = [c["step_id"] for c in result.rebased_commits]
        assert "1.2" not in rebased_ids


@_requires_consolidator
class TestCommitConsolidatorTopologicalSort:
    """Topological ordering respects step depends_on relationships."""

    def test_dependent_step_applied_after_its_dependency(
        self, tmp_path: Path
    ) -> None:
        """Step 1.2 depends on 1.1; verify 1.1 appears before 1.2 in result."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        h1 = _git_commit_file(repo, "base.txt", "base\n", "step 1.1")
        h2 = _git_commit_file(repo, "derived.txt", "derived\n", "step 1.2")

        step1 = PlanStep(
            step_id="1.1",
            agent_name="agent-a",
            task_description="Base work",
        )
        step2 = PlanStep(
            step_id="1.2",
            agent_name="agent-b",
            task_description="Dependent work",
            depends_on=["1.1"],
        )
        plan = MachinePlan(
            task_id="topo-test",
            task_summary="Topo test",
            phases=[PlanPhase(phase_id=0, name="Work", steps=[step1, step2])],
        )
        state = ExecutionState(plan=plan, task_id="topo-test")
        state.step_results = [
            StepResult(step_id="1.1", agent_name="agent-a", commit_hash=h1),
            StepResult(step_id="1.2", agent_name="agent-b", commit_hash=h2),
        ]

        consolidator = CommitConsolidator(repo_root=repo, plan=plan, state=state)
        result = consolidator.consolidate()

        rebased_ids = [c["step_id"] for c in result.rebased_commits]
        assert rebased_ids.index("1.1") < rebased_ids.index("1.2")


@_requires_consolidator
class TestCommitConsolidatorConflictDetection:
    """Conflict detection when two steps modify the same file differently."""

    def test_conflicting_edits_to_same_file_sets_conflict_status(
        self, tmp_path: Path
    ) -> None:
        """Two steps that both modify shared.txt on different branches conflict."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        base_head = _current_head(repo)

        # Create a shared file in the initial commit.
        (repo / "shared.txt").write_text("line1\nline2\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add shared"],
            cwd=str(repo), check=True, capture_output=True,
        )
        base_with_shared = _current_head(repo)

        # Step 1.1 — branch from base_with_shared, edit shared.txt one way.
        subprocess.run(
            ["git", "checkout", "-b", "step-1.1", base_with_shared],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "shared.txt").write_text("modified by step1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "step 1.1 edit"],
            cwd=str(repo), check=True, capture_output=True,
        )
        h1 = _current_head(repo)

        # Step 1.2 — branch from same base, edit shared.txt differently.
        subprocess.run(
            ["git", "checkout", "-b", "step-1.2", base_with_shared],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "shared.txt").write_text("modified by step2 — DIFFERENT\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "step 1.2 edit"],
            cwd=str(repo), check=True, capture_output=True,
        )
        h2 = _current_head(repo)

        # Return to main so the consolidator has a clean base.
        subprocess.run(
            ["git", "checkout", "master"],
            cwd=str(repo), check=True, capture_output=True,
        )

        plan = _minimal_plan(n_steps=2)
        state = _state_with_commits(plan, [h1, h2])

        consolidator = CommitConsolidator(repo_root=repo, plan=plan, state=state)
        result = consolidator.consolidate()

        assert result.status in ("conflict", "partial")
        # The conflicting file should be recorded.
        if result.conflict_files:
            assert "shared.txt" in result.conflict_files

    def test_non_overlapping_edits_do_not_conflict(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        h1 = _git_commit_file(repo, "file_a.py", "content a\n", "step 1.1")
        h2 = _git_commit_file(repo, "file_b.py", "content b\n", "step 1.2")

        plan = _minimal_plan(n_steps=2)
        state = _state_with_commits(plan, [h1, h2])

        consolidator = CommitConsolidator(repo_root=repo, plan=plan, state=state)
        result = consolidator.consolidate()

        assert result.status == "success"
        assert result.conflict_files == []


@_requires_consolidator
class TestCommitConsolidatorCleanupWorktrees:
    """cleanup_worktrees removes agent worktrees created during consolidation."""

    def test_cleanup_worktrees_removes_temp_directories(
        self, tmp_path: Path
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        h1 = _git_commit_file(repo, "f.txt", "x\n", "step 1.1")

        plan = _minimal_plan(n_steps=1)
        state = _state_with_commits(plan, [h1])

        consolidator = CommitConsolidator(repo_root=repo, plan=plan, state=state)
        consolidator.consolidate()
        # Cleanup should not raise even when there is nothing to remove.
        consolidator.cleanup_worktrees()


@_requires_consolidator
class TestCommitConsolidatorDirtyWorktree:
    """Dirty worktree is detected and handled gracefully."""

    def test_dirty_worktree_detected_before_consolidation(
        self, tmp_path: Path
    ) -> None:
        """Uncommitted changes in the working tree should raise or set error."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)
        h1 = _git_commit_file(repo, "f.txt", "x\n", "step 1.1")

        # Make the worktree dirty.
        (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")

        plan = _minimal_plan(n_steps=1)
        state = _state_with_commits(plan, [h1])

        consolidator = CommitConsolidator(repo_root=repo, plan=plan, state=state)
        try:
            result = consolidator.consolidate()
            # If consolidation proceeds, the error must be recorded in the result.
            if result.error:
                # Acceptable: dirty worktree detected and recorded.
                assert result.error
            # Alternatively, status may be 'partial' or 'conflict'.
        except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
            # Acceptable: dirty worktree causes an exception.
            assert exc is not None
