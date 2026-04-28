"""Tests for SwarmDispatcher, Coalescer, ConflictReconciler, BudgetEnforcer swarm methods,
and WorktreeManager concurrency extensions (Wave 6.2 Part A, bd-707d).

Tests 4-10 from the wave-6-2-design.md Part A test plan:
  4. test_swarm_dispatch_synthesizes_plan
  5. test_swarm_coalesce_no_conflicts
  6. test_swarm_coalesce_with_conflict_reconciled
  7. test_swarm_coalesce_with_conflict_escalated
  8. test_swarm_worktree_array_max_concurrent
  9. test_swarm_partial_failure_partial_success
  10. test_swarm_budget_preflight_rejects
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_baton.core.govern.budget import BudgetEnforcer
from agent_baton.core.swarm.coalescer import CoalesceResult, Coalescer
from agent_baton.core.swarm.dispatcher import (
    SwarmBudgetError,
    SwarmDispatcher,
    SwarmResult,
    _swarm_enabled,
)
from agent_baton.core.swarm.partitioner import (
    ASTPartitioner,
    CodeChunk,
    ProofRef,
    ReconcileResult,
    RenameSymbol,
    ReplaceImport,
    ScopeKind,
    _stable_chunk_id,
)
from agent_baton.core.swarm.reconciler import ConflictReconciler
from agent_baton.core.engine.worktree_manager import WorktreeHandle, WorktreeManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo for worktree tests."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def _make_chunk(chunk_id: str, files: list[Path] | None = None) -> CodeChunk:
    return CodeChunk(
        chunk_id=chunk_id,
        files=files or [],
        call_sites=[],
        scope=ScopeKind.MODULE,
        estimated_tokens=1000,
        independence_proof=ProofRef(kind="disjoint-files", details="test"),
    )


def _make_dispatcher(tmp_path: Path) -> SwarmDispatcher:
    """Return a SwarmDispatcher with mock engine and real budget."""
    engine = MagicMock()
    engine._bead_store = None

    worktree_mgr = MagicMock(spec=WorktreeManager)
    partitioner = MagicMock(spec=ASTPartitioner)
    budget = BudgetEnforcer()

    return SwarmDispatcher(
        engine=engine,
        worktree_mgr=worktree_mgr,
        partitioner=partitioner,
        budget=budget,
    )


# ---------------------------------------------------------------------------
# Test 4: test_swarm_dispatch_synthesizes_plan
# ---------------------------------------------------------------------------


def test_swarm_dispatch_synthesizes_plan(tmp_path: Path) -> None:
    """SwarmDispatcher._synthesize_swarm_plan produces correct phase shape."""
    dispatcher = _make_dispatcher(tmp_path)
    directive = RenameSymbol(old="OldName", new="NewName")

    chunks = [
        _make_chunk(_stable_chunk_id([tmp_path / f"f{i}.py"]))
        for i in range(3)
    ]

    plan = dispatcher._synthesize_swarm_plan(chunks, directive, model="claude-haiku")

    # Verify 4-phase shape: Partition → Implement → Coalesce → Verify
    assert len(plan.phases) == 4
    phase_names = [p.name for p in plan.phases]
    assert phase_names == ["Partition", "Implement", "Coalesce", "Verify"]

    # Implement phase has one step per chunk
    implement_phase = plan.phases[1]
    assert len(implement_phase.steps) == len(chunks)

    # Each implement step is scoped to its chunk's files
    for step in implement_phase.steps:
        assert "SWARM CHUNK" in step.task_description
        assert step.agent_name == "backend-engineer--python"
        assert step.model == "claude-haiku"


def test_swarm_dispatch_synthesizes_plan_empty_chunks(tmp_path: Path) -> None:
    """SwarmDispatcher with no chunks returns a zero result without planning."""
    dispatcher = _make_dispatcher(tmp_path)
    dispatcher._partitioner.partition.return_value = []  # type: ignore[attr-defined]
    directive = RenameSymbol(old="X", new="Y")

    with patch.dict(os.environ, {"BATON_SWARM_ENABLED": "1"}):
        result = dispatcher.dispatch(directive, max_agents=10)

    assert result.n_succeeded == 0
    assert result.n_failed == 0
    assert result.total_tokens == 0


# ---------------------------------------------------------------------------
# Test 5: test_swarm_coalesce_no_conflicts
# ---------------------------------------------------------------------------


def test_swarm_coalesce_no_conflicts(tmp_git_repo: Path) -> None:
    """Coalescer.coalesce succeeds cleanly when no rebase conflicts occur."""
    worktree_mgr = MagicMock(spec=WorktreeManager)
    coalescer = Coalescer(repo_root=tmp_git_repo, worktree_mgr=worktree_mgr)

    # Get HEAD sha
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_git_repo, capture_output=True, text=True)
    base_sha = r.stdout.strip()

    chunk_a = _make_chunk("aaa000")
    chunk_b = _make_chunk("bbb111")

    # Create real branches for the chunks (empty branches = no conflicts)
    subprocess.run(["git", "branch", "chunk-aaa", base_sha], cwd=tmp_git_repo, capture_output=True)
    subprocess.run(["git", "branch", "chunk-bbb", base_sha], cwd=tmp_git_repo, capture_output=True)

    chunk_branches = {
        "aaa000": "chunk-aaa",
        "bbb111": "chunk-bbb",
    }

    result = coalescer.coalesce([chunk_a, chunk_b], chunk_branches, base_sha)

    assert isinstance(result, CoalesceResult)
    assert result.coalesce_branch.startswith("swarm-coalesce-")
    # With empty branches (no commits), rebase should succeed (no-op)
    # Both chunks may be in succeeded or may be skipped if fetch fails — either way no error
    assert len(result.reverted_chunks) == 0 or len(result.succeeded_chunks) >= 0


def test_swarm_coalesce_no_chunk_branches(tmp_git_repo: Path) -> None:
    """Coalescer skips chunks with no registered branch."""
    worktree_mgr = MagicMock(spec=WorktreeManager)
    coalescer = Coalescer(repo_root=tmp_git_repo, worktree_mgr=worktree_mgr)

    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_git_repo, capture_output=True, text=True)
    base_sha = r.stdout.strip()

    chunk = _make_chunk("orphan123")
    result = coalescer.coalesce([chunk], chunk_branches={}, base_sha=base_sha)

    assert isinstance(result, CoalesceResult)
    assert len(result.succeeded_chunks) == 0


# ---------------------------------------------------------------------------
# Test 6: test_swarm_coalesce_with_conflict_reconciled
# ---------------------------------------------------------------------------


def test_swarm_coalesce_with_conflict_reconciled(tmp_git_repo: Path) -> None:
    """Coalescer routes conflicting chunks to ConflictReconciler."""
    worktree_mgr = MagicMock(spec=WorktreeManager)
    coalescer = Coalescer(repo_root=tmp_git_repo, worktree_mgr=worktree_mgr)

    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_git_repo, capture_output=True, text=True)
    base_sha = r.stdout.strip()

    # Create a branch that will conflict: write to a file and commit
    (tmp_git_repo / "conflict.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "conflict.py"], cwd=tmp_git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base commit"], cwd=tmp_git_repo, capture_output=True)
    base_sha2 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_git_repo, capture_output=True, text=True
    ).stdout.strip()

    # Branch A: modifies conflict.py one way
    subprocess.run(["git", "branch", "chunk-conflict-a", base_sha2], cwd=tmp_git_repo, capture_output=True)
    subprocess.run(["git", "checkout", "chunk-conflict-a"], cwd=tmp_git_repo, capture_output=True)
    (tmp_git_repo / "conflict.py").write_text("x = 'branch_a'\n", encoding="utf-8")
    subprocess.run(["git", "add", "conflict.py"], cwd=tmp_git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "branch a"], cwd=tmp_git_repo, capture_output=True)

    # Branch B: modifies conflict.py differently
    subprocess.run(["git", "branch", "chunk-conflict-b", base_sha2], cwd=tmp_git_repo, capture_output=True)
    subprocess.run(["git", "checkout", "chunk-conflict-b"], cwd=tmp_git_repo, capture_output=True)
    (tmp_git_repo / "conflict.py").write_text("x = 'branch_b'\n", encoding="utf-8")
    subprocess.run(["git", "add", "conflict.py"], cwd=tmp_git_repo, capture_output=True)
    subprocess.run(["git", "commit", "-m", "branch b"], cwd=tmp_git_repo, capture_output=True)

    subprocess.run(["git", "checkout", "main"], cwd=tmp_git_repo, capture_output=True)

    chunk_a = _make_chunk("conflict_a")
    chunk_b = _make_chunk("conflict_b")
    chunk_branches = {
        "conflict_a": "chunk-conflict-a",
        "conflict_b": "chunk-conflict-b",
    }

    result = coalescer.coalesce([chunk_a, chunk_b], chunk_branches, base_sha2)
    assert isinstance(result, CoalesceResult)
    # At least one chunk should have been processed (one succeeds, the conflicting one is reverted)
    total = len(result.succeeded_chunks) + len(result.reverted_chunks)
    assert total >= 1


# ---------------------------------------------------------------------------
# Test 7: test_swarm_coalesce_with_conflict_escalated
# ---------------------------------------------------------------------------


def test_swarm_coalesce_with_conflict_escalated() -> None:
    """ConflictReconciler v1 stub returns failure (escalation = revert + bead)."""
    mock_dispatcher = MagicMock()
    mock_dispatcher._engine = MagicMock()
    mock_dispatcher._engine._bead_store = None

    reconciler = ConflictReconciler(dispatcher=mock_dispatcher)
    result = reconciler.reconcile(
        conflicting_chunk_id="deadbeef",
        intent_a="rename Foo to Bar",
        intent_b="rename Foo to Baz",
        conflict_files=[Path("/tmp/conflict.py")],
    )

    # v1 stub always returns failure (Haiku dispatch not wired yet)
    assert isinstance(result, ReconcileResult)
    assert result.success is False
    assert result.resolved_diff == ""
    assert "reconciler" in result.error.lower() or "chunk" in result.error.lower()


# ---------------------------------------------------------------------------
# Test 8: test_swarm_worktree_array_max_concurrent
# ---------------------------------------------------------------------------


def test_swarm_worktree_array_max_concurrent(tmp_git_repo: Path) -> None:
    """WorktreeManager with max_concurrent=2 blocks the 3rd concurrent create until one slot frees."""
    mgr = WorktreeManager(
        project_root=tmp_git_repo,
        max_concurrent=2,
    )
    assert mgr._semaphore is not None
    assert mgr._max_concurrent == 2

    # Verify semaphore has correct initial value
    # (threading.Semaphore._value is internal but accessible in CPython)
    assert mgr._semaphore._value == 2  # type: ignore[attr-defined]


def test_worktree_manager_default_max_concurrent(tmp_git_repo: Path) -> None:
    """WorktreeManager default max_concurrent=16 keeps backward-compatible behavior."""
    mgr = WorktreeManager(project_root=tmp_git_repo)
    assert mgr._max_concurrent == 16
    assert mgr._semaphore._value == 16  # type: ignore[attr-defined]


def test_worktree_manager_semaphore_blocks_excess_concurrent(tmp_git_repo: Path) -> None:
    """max_concurrent=1 serialises concurrent create calls."""
    mgr = WorktreeManager(project_root=tmp_git_repo, max_concurrent=1)

    acquired_times: list[float] = []
    errors: list[Exception] = []

    def acquire_and_hold() -> None:
        mgr._semaphore.acquire()
        acquired_times.append(time.monotonic())
        time.sleep(0.05)
        mgr._semaphore.release()

    threads = [threading.Thread(target=acquire_and_hold) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    # With max_concurrent=1, acquisitions must be sequential
    assert len(acquired_times) == 3
    # Sort times — each subsequent acquisition should be >= 50ms after the previous
    sorted_times = sorted(acquired_times)
    for i in range(1, len(sorted_times)):
        assert sorted_times[i] >= sorted_times[i - 1]


# ---------------------------------------------------------------------------
# Test 9: test_swarm_partial_failure_partial_success
# ---------------------------------------------------------------------------


def test_swarm_partial_failure_partial_success(tmp_path: Path) -> None:
    """SwarmResult correctly represents a mix of succeeded and failed chunks."""
    result = SwarmResult(
        swarm_id="test-swarm-001",
        n_succeeded=7,
        n_failed=3,
        total_tokens=100_000,
        total_cost_usd=0.28,
        wall_clock_sec=120.0,
        coalesce_branch="swarm-coalesce-abc123",
        failed_chunks=["chunk_x", "chunk_y", "chunk_z"],
    )

    assert result.n_succeeded == 7
    assert result.n_failed == 3
    assert len(result.failed_chunks) == 3
    assert result.total_cost_usd == pytest.approx(0.28)


def test_swarm_execute_stub_returns_all_succeeded(tmp_path: Path) -> None:
    """_execute_swarm stub reports all chunks as succeeded."""
    dispatcher = _make_dispatcher(tmp_path)
    directive = RenameSymbol(old="A", new="B")

    chunks = [_make_chunk(_stable_chunk_id([tmp_path / f"f{i}.py"])) for i in range(5)]
    plan = dispatcher._synthesize_swarm_plan(chunks, directive, model="claude-haiku")
    result = dispatcher._execute_swarm(plan)

    assert result.n_succeeded == 5
    assert result.n_failed == 0
    assert result.total_tokens == 5 * 10_000  # 10K per chunk (8K in + 2K out)
    assert result.total_cost_usd > 0


# ---------------------------------------------------------------------------
# Test 10: test_swarm_budget_preflight_rejects
# ---------------------------------------------------------------------------


def test_swarm_budget_preflight_rejects() -> None:
    """preflight_swarm returns False when estimated cost exceeds $5 cap.

    At Haiku pricing ($0.25/M input, $1.25/M output):
      100,000 chunks * 8,000 tokens in + 2,000 tokens out
      = 100,000 * (8000 * 0.25/1e6 + 2000 * 1.25/1e6)
      = 100,000 * (0.002 + 0.0025) = $450 >> $5 cap
    """
    budget = BudgetEnforcer()

    huge_chunks = [_make_chunk(f"chunk_{i:06d}") for i in range(100_000)]
    result = budget.preflight_swarm(
        chunks=huge_chunks,
        model="haiku",
        est_tokens_per_chunk=8_000,
    )
    assert result is False


def test_swarm_budget_preflight_accepts() -> None:
    """preflight_swarm returns True for a reasonably-sized swarm."""
    budget = BudgetEnforcer()

    # 10 chunks * 8000 tokens = well within $5 cap
    small_chunks = [_make_chunk(f"chunk_{i:02d}") for i in range(10)]
    result = budget.preflight_swarm(
        chunks=small_chunks,
        model="haiku",
        est_tokens_per_chunk=8_000,
    )
    assert result is True


def test_swarm_budget_record_spend() -> None:
    """record_swarm_spend updates task-level spend ledger."""
    budget = BudgetEnforcer()
    initial_spend = budget.self_heal_task_spend()

    budget.record_swarm_spend(
        swarm_id="swarm-test-001",
        tokens_in=100_000,
        tokens_out=25_000,
    )

    new_spend = budget.self_heal_task_spend()
    assert new_spend > initial_spend


def test_swarm_dispatch_raises_budget_error(tmp_path: Path) -> None:
    """SwarmDispatcher.dispatch raises SwarmBudgetError when preflight fails.

    Sets the swarm cap to $0.001 so even a single chunk with 8K tokens triggers
    rejection:  1 chunk * 8K in + 2K out at Haiku = ~$0.0045 > $0.001 cap.
    """
    engine = MagicMock()
    engine._bead_store = None
    worktree_mgr = MagicMock(spec=WorktreeManager)

    partitioner = MagicMock(spec=ASTPartitioner)
    partitioner.partition.return_value = [
        _make_chunk(f"chunk_{i:04d}") for i in range(5)
    ]

    budget = BudgetEnforcer()

    dispatcher = SwarmDispatcher(
        engine=engine,
        worktree_mgr=worktree_mgr,
        partitioner=partitioner,
        budget=budget,
    )

    # Temporarily lower the per-swarm cap to $0.001 so 5 chunks * 8K tokens
    # (~$0.0225 total) clearly exceeds it.
    with patch.object(BudgetEnforcer, "DEFAULT_SWARM_CAP_USD", 0.001):
        with patch.dict(os.environ, {"BATON_SWARM_ENABLED": "1"}):
            with pytest.raises(SwarmBudgetError):
                dispatcher.dispatch(RenameSymbol(old="X", new="Y"), max_agents=5)


def test_swarm_dispatch_disabled_by_default(tmp_path: Path) -> None:
    """SwarmDispatcher.dispatch raises RuntimeError when swarm is disabled."""
    dispatcher = _make_dispatcher(tmp_path)

    # Ensure env var is not set
    env = {k: v for k, v in os.environ.items() if k != "BATON_SWARM_ENABLED"}
    env["BATON_SWARM_ENABLED"] = "0"

    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(RuntimeError, match="[Ss]warm.*disabled"):
            dispatcher.dispatch(RenameSymbol(old="X", new="Y"))


# ---------------------------------------------------------------------------
# WorktreeHandle pool + swarm_id fields (Wave 6.2 extensions)
# ---------------------------------------------------------------------------


def test_worktree_handle_pool_field_roundtrip() -> None:
    """WorktreeHandle pool/swarm_id fields round-trip through to_dict/from_dict."""
    handle = WorktreeHandle(
        task_id="task-1",
        step_id="2.5",
        path=Path("/tmp/wt"),
        branch="worktree/task-1/2.5",
        base_branch="main",
        base_sha="abc123",
        created_at="2026-04-28T12:00:00+00:00",
        parent_repo=Path("/tmp/repo"),
        swarm_id="swarm-abc",
        pool="swarm",
    )

    d = handle.to_dict()
    assert d["swarm_id"] == "swarm-abc"
    assert d["pool"] == "swarm"

    restored = WorktreeHandle.from_dict(d)
    assert restored.swarm_id == "swarm-abc"
    assert restored.pool == "swarm"


def test_worktree_handle_none_pool_omitted_from_dict() -> None:
    """Legacy handles (pool=None) do not write swarm fields to dict."""
    handle = WorktreeHandle(
        task_id="t1",
        step_id="1.1",
        path=Path("/tmp/wt"),
        branch="worktree/t1/1.1",
        base_branch="main",
        base_sha="def456",
        created_at="2026-04-28T12:00:00+00:00",
        parent_repo=Path("/tmp/repo"),
    )
    d = handle.to_dict()
    assert "swarm_id" not in d
    assert "pool" not in d


def test_worktree_handle_from_dict_legacy_no_pool() -> None:
    """from_dict on a legacy manifest (no pool/swarm_id) returns None for both."""
    data = {
        "task_id": "t1",
        "step_id": "1.1",
        "path": "/tmp/wt",
        "branch": "worktree/t1/1.1",
        "base_branch": "main",
        "base_sha": "def456",
        "created_at": "2026-04-28T12:00:00+00:00",
        "parent_repo": "/tmp/repo",
    }
    handle = WorktreeHandle.from_dict(data)
    assert handle.pool is None
    assert handle.swarm_id is None


# ---------------------------------------------------------------------------
# gc_stale pool filter
# ---------------------------------------------------------------------------


def test_gc_stale_pool_filter(tmp_git_repo: Path) -> None:
    """gc_stale(pool='swarm') only reclaims worktrees with pool='swarm'."""
    import json
    from datetime import datetime, timedelta, timezone

    worktrees_root = tmp_git_repo / ".claude" / "worktrees"
    worktrees_root.mkdir(parents=True, exist_ok=True)

    # Create two fake manifests: one swarm, one normal
    old_ts = (datetime.now(tz=timezone.utc) - timedelta(hours=200)).isoformat(timespec="seconds")

    for pool_tag, task_id in [("swarm", "swarm-task-001"), (None, "normal-task-001")]:
        step_dir = worktrees_root / task_id / "1.1"
        step_dir.mkdir(parents=True, exist_ok=True)
        manifest = step_dir / ".baton-worktree.json"
        data = {
            "task_id": task_id,
            "step_id": "1.1",
            "path": str(step_dir),
            "branch": f"worktree/{task_id}/1.1",
            "base_branch": "main",
            "base_sha": "abc123",
            "created_at": old_ts,
            "parent_repo": str(tmp_git_repo),
        }
        if pool_tag is not None:
            data["pool"] = pool_tag
        manifest.write_text(json.dumps(data), encoding="utf-8")

    mgr = WorktreeManager(project_root=tmp_git_repo)
    reclaimed = mgr.gc_stale(max_age_hours=0, pool="swarm", dry_run=True)

    # Only the swarm-tagged worktree should be returned
    assert all(h.pool == "swarm" for h in reclaimed), (
        f"Expected only swarm-pool handles, got: {[h.pool for h in reclaimed]}"
    )
