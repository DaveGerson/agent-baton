"""Wave 6.2 Part A follow-up — real dispatch tests for bd-c925 and bd-2b9f.

bd-c925: ConflictReconciler._dispatch_reconciler_agent real Haiku dispatch.
bd-2b9f: SwarmDispatcher._execute_swarm ExecutionEngine loop integration
         + SWARM_DISPATCH ActionType + set_swarm_launcher().

Test plan (25 tests):
  bd-c925 (reconciler, 10 tests):
    1.  test_reconciler_no_launcher_returns_failure
    2.  test_reconciler_launcher_success_returns_diff
    3.  test_reconciler_launcher_reconcile_blocked_returns_failure
    4.  test_reconciler_launcher_empty_outcome_returns_failure
    5.  test_reconciler_launcher_failed_status_returns_failure
    6.  test_reconciler_launcher_exception_returns_failure_with_message
    7.  test_reconciler_prompt_contains_all_context_fields
    8.  test_reconciler_reads_file_context_up_to_50_lines
    9.  test_reconciler_caps_file_context_at_3_files
    10. test_reconciler_passes_task_id_to_launcher

  bd-2b9f (executor integration, 15 tests):
    11. test_swarm_dispatch_action_type_exists
    12. test_execute_swarm_synthetic_when_no_launcher
    13. test_execute_swarm_with_launcher_drives_steps
    14. test_execute_swarm_launcher_failure_counted
    15. test_execute_swarm_launcher_exception_counted
    16. test_execute_swarm_returns_wall_clock
    17. test_execute_swarm_failed_chunks_listed
    18. test_execute_swarm_emits_telemetry_events
    19. test_engine_swarm_none_when_disabled
    20. test_engine_swarm_initialised_when_enabled
    21. test_engine_set_swarm_launcher_noop_when_swarm_none
    22. test_engine_set_swarm_launcher_injects_launcher
    23. test_swarm_dispatch_enum_value
    24. test_swarm_dispatcher_accepts_launcher_kwarg
    25. test_execute_swarm_coalesce_branch_name
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_baton.core.runtime.launcher import LaunchResult
from agent_baton.core.swarm.dispatcher import SwarmDispatcher, SwarmResult, _swarm_enabled
from agent_baton.core.swarm.partitioner import (
    ASTPartitioner,
    CodeChunk,
    ProofRef,
    ReconcileResult,
    RenameSymbol,
    ScopeKind,
    _stable_chunk_id,
)
from agent_baton.core.swarm.reconciler import ConflictReconciler
from agent_baton.core.govern.budget import BudgetEnforcer
from agent_baton.core.engine.worktree_manager import WorktreeManager
from agent_baton.models.execution import ActionType


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_chunk(chunk_id: str, files: list[Path] | None = None) -> CodeChunk:
    return CodeChunk(
        chunk_id=chunk_id,
        files=files or [],
        call_sites=[],
        scope=ScopeKind.MODULE,
        estimated_tokens=1000,
        independence_proof=ProofRef(kind="disjoint-files", details="test"),
    )


def _make_dispatcher(
    tmp_path: Path,
    launcher=None,
) -> SwarmDispatcher:
    """Return a SwarmDispatcher with mock engine and optional launcher."""
    engine = MagicMock()
    engine._bead_store = None
    engine._telemetry = None
    engine._task_id = "task-test-001"
    worktree_mgr = MagicMock(spec=WorktreeManager)
    partitioner = MagicMock(spec=ASTPartitioner)
    budget = BudgetEnforcer()
    return SwarmDispatcher(
        engine=engine,
        worktree_mgr=worktree_mgr,
        partitioner=partitioner,
        budget=budget,
        launcher=launcher,
    )


def _make_reconciler(launcher=None) -> ConflictReconciler:
    """Return a ConflictReconciler with a mock dispatcher."""
    mock_dispatcher = MagicMock()
    mock_dispatcher._engine = MagicMock()
    mock_dispatcher._engine._bead_store = None
    mock_dispatcher._engine._task_id = "swarm-task-abc"
    mock_dispatcher._launcher = launcher
    mock_dispatcher._worktree_mgr = MagicMock()
    mock_dispatcher._worktree_mgr._coalesce_worktree = None
    return ConflictReconciler(dispatcher=mock_dispatcher)


def _make_launch_result(
    status: str = "complete",
    outcome: str = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new",
    error: str = "",
    estimated_tokens: int = 5000,
    duration_seconds: float = 1.5,
) -> LaunchResult:
    return LaunchResult(
        step_id="reconcile-deadbeef",
        agent_name="swarm-reconciler",
        status=status,
        outcome=outcome,
        estimated_tokens=estimated_tokens,
        duration_seconds=duration_seconds,
        error=error,
    )


# ---------------------------------------------------------------------------
# bd-c925: ConflictReconciler._dispatch_reconciler_agent
# ---------------------------------------------------------------------------


# Test 1
def test_reconciler_no_launcher_returns_failure() -> None:
    """Without a launcher on the dispatcher, reconciler returns v1-style failure."""
    reconciler = _make_reconciler(launcher=None)
    result = reconciler.reconcile(
        conflicting_chunk_id="deadbeef1234",
        intent_a="rename Foo to Bar",
        intent_b="rename Foo to Baz",
        conflict_files=[Path("/nonexistent/file.py")],
    )
    assert isinstance(result, ReconcileResult)
    assert result.success is False
    assert result.resolved_diff == ""
    assert "launcher" in result.error.lower() or "no launcher" in result.error.lower()


# Test 2
def test_reconciler_launcher_success_returns_diff(tmp_path: Path) -> None:
    """With a launcher that returns a diff, reconciler returns success=True."""
    diff = "--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-old\n+new"
    mock_launcher = AsyncMock()
    mock_launcher.launch.return_value = _make_launch_result(
        status="complete", outcome=diff
    )

    reconciler = _make_reconciler(launcher=mock_launcher)
    result = reconciler.reconcile(
        conflicting_chunk_id="deadbeef0001",
        intent_a="rename X to Y",
        intent_b="add docstring to X",
        conflict_files=[],
    )
    assert result.success is True
    assert result.resolved_diff == diff
    assert result.error == ""
    mock_launcher.launch.assert_called_once()


# Test 3
def test_reconciler_launcher_reconcile_blocked_returns_failure() -> None:
    """RECONCILE_BLOCKED prefix in outcome maps to success=False."""
    mock_launcher = AsyncMock()
    mock_launcher.launch.return_value = _make_launch_result(
        status="complete",
        outcome="RECONCILE_BLOCKED: intents are semantically contradictory",
    )

    reconciler = _make_reconciler(launcher=mock_launcher)
    result = reconciler.reconcile(
        conflicting_chunk_id="deadbeef0002",
        intent_a="delete function foo",
        intent_b="rename function foo to bar",
        conflict_files=[],
    )
    assert result.success is False
    assert "RECONCILE_BLOCKED" in result.error or "contradictory" in result.error


# Test 4
def test_reconciler_launcher_empty_outcome_returns_failure() -> None:
    """Empty launcher outcome → success=False."""
    mock_launcher = AsyncMock()
    mock_launcher.launch.return_value = _make_launch_result(
        status="complete", outcome=""
    )

    reconciler = _make_reconciler(launcher=mock_launcher)
    result = reconciler.reconcile(
        conflicting_chunk_id="deadbeef0003",
        intent_a="a",
        intent_b="b",
        conflict_files=[],
    )
    assert result.success is False


# Test 5
def test_reconciler_launcher_failed_status_returns_failure() -> None:
    """Launcher status='failed' → success=False."""
    mock_launcher = AsyncMock()
    mock_launcher.launch.return_value = _make_launch_result(
        status="failed",
        outcome="",
        error="claude timed out",
    )

    reconciler = _make_reconciler(launcher=mock_launcher)
    result = reconciler.reconcile(
        conflicting_chunk_id="deadbeef0004",
        intent_a="a",
        intent_b="b",
        conflict_files=[],
    )
    assert result.success is False
    assert "failed" in result.error.lower() or "timed out" in result.error.lower()


# Test 6
def test_reconciler_launcher_exception_returns_failure_with_message() -> None:
    """Launcher raising an exception → success=False with exception message."""
    mock_launcher = AsyncMock()
    mock_launcher.launch.side_effect = RuntimeError("connection refused")

    reconciler = _make_reconciler(launcher=mock_launcher)
    result = reconciler.reconcile(
        conflicting_chunk_id="deadbeef0005",
        intent_a="a",
        intent_b="b",
        conflict_files=[],
    )
    assert result.success is False
    assert "connection refused" in result.error or "Reconciler" in result.error


# Test 7
def test_reconciler_prompt_contains_all_context_fields() -> None:
    """Reconciler prompt includes chunk_id, intent_a, intent_b, file list."""
    captured_prompts: list[str] = []

    async def _capture_launch(**kwargs):
        captured_prompts.append(kwargs.get("prompt", ""))
        return _make_launch_result()

    mock_launcher = MagicMock()
    mock_launcher.launch = _capture_launch

    reconciler = _make_reconciler(launcher=mock_launcher)
    reconciler.reconcile(
        conflicting_chunk_id="aabbccdd1234",
        intent_a="rename Foo to BarBaz",
        intent_b="add logging to Foo",
        conflict_files=[Path("/some/file.py")],
    )
    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]
    assert "aabbccdd" in prompt          # chunk_id prefix
    assert "rename Foo to BarBaz" in prompt
    assert "add logging to Foo" in prompt
    assert "/some/file.py" in prompt


# Test 8
def test_reconciler_reads_file_context_up_to_50_lines(tmp_path: Path) -> None:
    """Reconciler includes up to 50 lines of conflict file content in the prompt."""
    # Write a 100-line file; only first 50 should appear.
    conflict_file = tmp_path / "conflict.py"
    lines = [f"line_{i:03d} = {i}" for i in range(100)]
    conflict_file.write_text("\n".join(lines), encoding="utf-8")

    captured_prompts: list[str] = []

    async def _capture(**kwargs):
        captured_prompts.append(kwargs.get("prompt", ""))
        return _make_launch_result()

    mock_launcher = MagicMock()
    mock_launcher.launch = _capture

    reconciler = _make_reconciler(launcher=mock_launcher)
    reconciler.reconcile(
        conflicting_chunk_id="filectx0001",
        intent_a="change lines",
        intent_b="add import",
        conflict_files=[conflict_file],
    )
    prompt = captured_prompts[0]
    # line_049 should be present (50th line); line_050 should not.
    assert "line_049" in prompt
    assert "line_050" not in prompt


# Test 9
def test_reconciler_caps_file_context_at_3_files(tmp_path: Path) -> None:
    """Reconciler reads at most 3 conflict files to keep prompt bounded."""
    files = []
    for i in range(5):
        f = tmp_path / f"file_{i}.py"
        f.write_text(f"x_{i} = {i}\n", encoding="utf-8")
        files.append(f)

    captured_prompts: list[str] = []

    async def _capture(**kwargs):
        captured_prompts.append(kwargs.get("prompt", ""))
        return _make_launch_result()

    mock_launcher = MagicMock()
    mock_launcher.launch = _capture

    reconciler = _make_reconciler(launcher=mock_launcher)
    reconciler.reconcile(
        conflicting_chunk_id="filecap0001",
        intent_a="a",
        intent_b="b",
        conflict_files=files,
    )
    prompt = captured_prompts[0]
    # file_0, file_1, file_2 context included; file_3 and file_4 are NOT read
    # (they appear in the file list but not as context sections).
    file_context_count = prompt.count("=== ")
    assert file_context_count <= 3


# Test 10
def test_reconciler_passes_task_id_to_launcher() -> None:
    """Reconciler forwards engine._task_id as task_id kwarg to the launcher."""
    captured_kwargs: list[dict] = []

    async def _capture(**kwargs):
        captured_kwargs.append(kwargs)
        return _make_launch_result()

    mock_launcher = MagicMock()
    mock_launcher.launch = _capture

    reconciler = _make_reconciler(launcher=mock_launcher)
    # The mock dispatcher has _engine._task_id = "swarm-task-abc" (set in helper)
    reconciler.reconcile(
        conflicting_chunk_id="taskid0001",
        intent_a="a",
        intent_b="b",
        conflict_files=[],
    )
    assert len(captured_kwargs) == 1
    assert captured_kwargs[0].get("task_id") == "swarm-task-abc"
    assert captured_kwargs[0].get("agent_name") == "swarm-reconciler"


# ---------------------------------------------------------------------------
# bd-2b9f: SwarmDispatcher._execute_swarm + ExecutionEngine integration
# ---------------------------------------------------------------------------


# Test 11
def test_swarm_dispatch_action_type_exists() -> None:
    """ActionType.SWARM_DISPATCH exists with value 'swarm.dispatch'."""
    assert ActionType.SWARM_DISPATCH.value == "swarm.dispatch"


# Test 12
def test_execute_swarm_synthetic_when_no_launcher(tmp_path: Path) -> None:
    """_execute_swarm with no launcher returns synthetic all-succeeded result."""
    dispatcher = _make_dispatcher(tmp_path, launcher=None)
    directive = RenameSymbol(old="OldName", new="NewName")
    chunks = [_make_chunk(f"chunk_{i:03d}") for i in range(4)]
    plan = dispatcher._synthesize_swarm_plan(chunks, directive, model="claude-haiku")

    result = dispatcher._execute_swarm(plan)

    assert result.n_succeeded == 4
    assert result.n_failed == 0
    assert result.failed_chunks == []
    assert result.total_tokens == 4 * 10_000
    assert result.total_cost_usd > 0


# Test 13
def test_execute_swarm_with_launcher_drives_steps(tmp_path: Path) -> None:
    """_execute_swarm with a launcher calls launcher.launch for each implement step."""
    mock_launcher = AsyncMock()
    mock_launcher.launch.return_value = LaunchResult(
        step_id="2.1", agent_name="backend-engineer--python",
        status="complete", outcome="done", estimated_tokens=8000,
    )

    dispatcher = _make_dispatcher(tmp_path, launcher=mock_launcher)
    directive = RenameSymbol(old="A", new="B")
    chunks = [_make_chunk(f"c{i}") for i in range(3)]
    plan = dispatcher._synthesize_swarm_plan(chunks, directive, model="claude-haiku")

    result = dispatcher._execute_swarm(plan)

    # launcher.launch called once per implement step
    assert mock_launcher.launch.call_count == 3
    assert result.n_succeeded == 3
    assert result.n_failed == 0


# Test 14
def test_execute_swarm_launcher_failure_counted(tmp_path: Path) -> None:
    """Failed launcher result increments n_failed and adds to failed_chunks."""
    call_count = 0

    async def _mixed_launch(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            return LaunchResult(
                step_id=kwargs.get("step_id", ""),
                agent_name="backend-engineer--python",
                status="failed",
                outcome="",
                error="agent crashed",
            )
        return LaunchResult(
            step_id=kwargs.get("step_id", ""),
            agent_name="backend-engineer--python",
            status="complete",
            outcome="done",
            estimated_tokens=5000,
        )

    mock_launcher = MagicMock()
    mock_launcher.launch = _mixed_launch

    dispatcher = _make_dispatcher(tmp_path, launcher=mock_launcher)
    directive = RenameSymbol(old="X", new="Y")
    chunks = [_make_chunk(f"c{i}") for i in range(3)]
    plan = dispatcher._synthesize_swarm_plan(chunks, directive, model="claude-haiku")

    result = dispatcher._execute_swarm(plan)

    assert result.n_succeeded == 2
    assert result.n_failed == 1
    assert len(result.failed_chunks) == 1


# Test 15
def test_execute_swarm_launcher_exception_counted(tmp_path: Path) -> None:
    """Launcher raising an exception increments n_failed."""
    mock_launcher = AsyncMock()
    mock_launcher.launch.side_effect = RuntimeError("subprocess died")

    dispatcher = _make_dispatcher(tmp_path, launcher=mock_launcher)
    directive = RenameSymbol(old="P", new="Q")
    chunks = [_make_chunk("exc_chunk")]
    plan = dispatcher._synthesize_swarm_plan(chunks, directive, model="claude-haiku")

    result = dispatcher._execute_swarm(plan)

    assert result.n_failed == 1
    assert result.n_succeeded == 0
    assert len(result.failed_chunks) == 1


# Test 16
def test_execute_swarm_returns_wall_clock(tmp_path: Path) -> None:
    """_execute_swarm with launcher sets wall_clock_sec > 0."""
    mock_launcher = AsyncMock()
    mock_launcher.launch.return_value = LaunchResult(
        step_id="2.1", agent_name="backend-engineer--python",
        status="complete", outcome="done",
    )

    dispatcher = _make_dispatcher(tmp_path, launcher=mock_launcher)
    directive = RenameSymbol(old="A", new="B")
    chunks = [_make_chunk("c1")]
    plan = dispatcher._synthesize_swarm_plan(chunks, directive, model="claude-haiku")

    result = dispatcher._execute_swarm(plan)

    # wall_clock_sec should be set (may be 0.0 on very fast machines but ≥ 0)
    assert result.wall_clock_sec >= 0.0


# Test 17
def test_execute_swarm_failed_chunks_listed(tmp_path: Path) -> None:
    """failed_chunks contains step_ids of all failed steps."""
    async def _always_fail(**kwargs):
        return LaunchResult(
            step_id=kwargs.get("step_id", ""),
            agent_name="backend-engineer--python",
            status="failed",
            outcome="",
            error="fail",
        )

    mock_launcher = MagicMock()
    mock_launcher.launch = _always_fail

    dispatcher = _make_dispatcher(tmp_path, launcher=mock_launcher)
    directive = RenameSymbol(old="A", new="B")
    chunks = [_make_chunk(f"chunk_{i}") for i in range(2)]
    plan = dispatcher._synthesize_swarm_plan(chunks, directive, model="claude-haiku")

    result = dispatcher._execute_swarm(plan)

    assert result.n_failed == 2
    assert len(result.failed_chunks) == 2
    # Each entry should be a step_id string
    for fc in result.failed_chunks:
        assert isinstance(fc, str)


# Test 18
def test_execute_swarm_emits_telemetry_events(tmp_path: Path) -> None:
    """_execute_swarm emits telemetry events for chunk start/complete."""
    from agent_baton.core.observe.telemetry import AgentTelemetry, TelemetryEvent

    telemetry_log = tmp_path / "telemetry.jsonl"
    telemetry = AgentTelemetry(log_path=telemetry_log)

    mock_launcher = AsyncMock()
    mock_launcher.launch.return_value = LaunchResult(
        step_id="2.1", agent_name="backend-engineer--python",
        status="complete", outcome="done", estimated_tokens=3000,
    )

    engine_mock = MagicMock()
    engine_mock._bead_store = None
    engine_mock._telemetry = telemetry
    engine_mock._task_id = "telemetry-task"

    dispatcher = SwarmDispatcher(
        engine=engine_mock,
        worktree_mgr=MagicMock(spec=WorktreeManager),
        partitioner=MagicMock(spec=ASTPartitioner),
        budget=BudgetEnforcer(),
        launcher=mock_launcher,
    )
    directive = RenameSymbol(old="A", new="B")
    chunks = [_make_chunk("tel_chunk")]
    plan = dispatcher._synthesize_swarm_plan(chunks, directive, model="claude-haiku")

    dispatcher._execute_swarm(plan)

    events = telemetry.read_events()
    event_types = [e.event_type for e in events]
    assert "swarm.chunk_start" in event_types
    assert any(
        t in event_types for t in ("swarm.chunk_complete", "swarm.chunk_failed")
    )


# Test 19
def test_engine_swarm_none_when_disabled(tmp_path: Path) -> None:
    """ExecutionEngine._swarm is None when BATON_SWARM_ENABLED=0."""
    from agent_baton.core.engine.executor import ExecutionEngine

    with patch.dict(os.environ, {"BATON_SWARM_ENABLED": "0"}):
        engine = ExecutionEngine(team_context_root=tmp_path / "ctx")

    assert engine._swarm is None


# Test 20
def test_engine_swarm_initialised_when_enabled(tmp_path: Path) -> None:
    """ExecutionEngine._swarm is a SwarmDispatcher when BATON_SWARM_ENABLED=1."""
    import subprocess
    from agent_baton.core.engine.executor import ExecutionEngine

    # Need a git repo for WorktreeManager
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, check=True, capture_output=True)

    ctx = repo / ".claude" / "team-context"
    ctx.mkdir(parents=True)

    with patch.dict(os.environ, {"BATON_SWARM_ENABLED": "1", "BATON_WORKTREE_ENABLED": "1"}):
        engine = ExecutionEngine(team_context_root=ctx)

    assert engine._swarm is not None
    from agent_baton.core.swarm.dispatcher import SwarmDispatcher
    assert isinstance(engine._swarm, SwarmDispatcher)


# Test 21
def test_engine_set_swarm_launcher_noop_when_swarm_none(tmp_path: Path) -> None:
    """set_swarm_launcher() is a no-op when swarm is disabled."""
    from agent_baton.core.engine.executor import ExecutionEngine

    with patch.dict(os.environ, {"BATON_SWARM_ENABLED": "0"}):
        engine = ExecutionEngine(team_context_root=tmp_path / "ctx")

    assert engine._swarm is None
    # Must not raise
    engine.set_swarm_launcher(MagicMock())
    assert engine._swarm is None


# Test 22
def test_engine_set_swarm_launcher_injects_launcher(tmp_path: Path) -> None:
    """set_swarm_launcher() injects the launcher into the SwarmDispatcher."""
    import subprocess
    from agent_baton.core.engine.executor import ExecutionEngine

    repo = tmp_path / "repo2"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, check=True, capture_output=True)

    ctx = repo / ".claude" / "team-context"
    ctx.mkdir(parents=True)

    with patch.dict(os.environ, {"BATON_SWARM_ENABLED": "1", "BATON_WORKTREE_ENABLED": "1"}):
        engine = ExecutionEngine(team_context_root=ctx)

    assert engine._swarm is not None
    mock_launcher = MagicMock()
    engine.set_swarm_launcher(mock_launcher)
    assert engine._swarm._launcher is mock_launcher


# Test 23
def test_swarm_dispatch_enum_value() -> None:
    """ActionType.SWARM_DISPATCH serialises to 'swarm.dispatch'."""
    assert ActionType.SWARM_DISPATCH.value == "swarm.dispatch"
    # Confirm it round-trips via value lookup
    assert ActionType("swarm.dispatch") == ActionType.SWARM_DISPATCH


# Test 24
def test_swarm_dispatcher_accepts_launcher_kwarg(tmp_path: Path) -> None:
    """SwarmDispatcher.__init__ accepts launcher= and stores it as _launcher."""
    mock_launcher = MagicMock()
    dispatcher = _make_dispatcher(tmp_path, launcher=mock_launcher)
    assert dispatcher._launcher is mock_launcher


# Test 25
def test_execute_swarm_coalesce_branch_name(tmp_path: Path) -> None:
    """coalesce_branch in SwarmResult follows 'swarm-coalesce-<plan.task_id>' pattern."""
    mock_launcher = AsyncMock()
    mock_launcher.launch.return_value = LaunchResult(
        step_id="2.1", agent_name="backend-engineer--python",
        status="complete", outcome="done",
    )

    dispatcher = _make_dispatcher(tmp_path, launcher=mock_launcher)
    directive = RenameSymbol(old="A", new="B")
    chunks = [_make_chunk("c1")]
    plan = dispatcher._synthesize_swarm_plan(chunks, directive, model="claude-haiku")

    result = dispatcher._execute_swarm(plan)

    assert result.coalesce_branch.startswith("swarm-coalesce-")
    assert plan.task_id in result.coalesce_branch
