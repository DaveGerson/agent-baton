"""Wave 6.2 Part A — Swarm end-to-end integration test (bd-707d).

Integration test: test_swarm_with_real_libcst_on_fixture_repo

Uses a real git repo + real Python files.  Exercises the full
ASTPartitioner → BudgetEnforcer.preflight_swarm → SwarmDispatcher._synthesize_swarm_plan
pipeline without dispatching actual Claude agents.

Skipped automatically when libcst is not installed.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Skip entire module when libcst is not available
pytest.importorskip("libcst", reason="libcst required for swarm integration tests")

from agent_baton.core.govern.budget import BudgetEnforcer
from agent_baton.core.swarm.dispatcher import SwarmDispatcher, SwarmResult
from agent_baton.core.swarm.partitioner import (
    ASTPartitioner,
    RenameSymbol,
    ReplaceImport,
)
from agent_baton.core.engine.worktree_manager import WorktreeManager


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Create a realistic Python project fixture for swarm integration tests."""
    # Git init
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True, capture_output=True)

    # Create a package with a symbol used in multiple places
    pkg = tmp_path / "mypackage"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")

    (pkg / "models.py").write_text(
        "class DataProcessor:\n"
        "    def process(self, data):\n"
        "        return data\n",
        encoding="utf-8",
    )
    (pkg / "service_a.py").write_text(
        "from mypackage.models import DataProcessor\n\n"
        "def run_a():\n"
        "    dp = DataProcessor()\n"
        "    return dp.process('a')\n",
        encoding="utf-8",
    )
    (pkg / "service_b.py").write_text(
        "from mypackage.models import DataProcessor\n\n"
        "def run_b():\n"
        "    dp = DataProcessor()\n"
        "    return dp.process('b')\n",
        encoding="utf-8",
    )
    (pkg / "service_c.py").write_text(
        "from mypackage.models import DataProcessor\n\n"
        "def run_c():\n"
        "    dp = DataProcessor()\n"
        "    return dp.process('c')\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


# ---------------------------------------------------------------------------
# test_swarm_with_real_libcst_on_fixture_repo
# ---------------------------------------------------------------------------


def test_swarm_with_real_libcst_on_fixture_repo(fixture_repo: Path) -> None:
    """Full pipeline: partition real Python files → budget preflight → plan synthesis."""
    partitioner = ASTPartitioner(fixture_repo)
    directive = RenameSymbol(old="DataProcessor", new="DataTransformer")

    # Step 1: partition
    chunks = partitioner.partition(directive, max_chunks=100)
    assert len(chunks) >= 1, "Expected at least one chunk from fixture repo"

    # All chunks must be disjoint
    seen_files: set[Path] = set()
    for chunk in chunks:
        for f in chunk.files:
            assert f not in seen_files, f"File {f} appears in multiple chunks"
            seen_files.add(f)

    # Step 2: budget preflight
    budget = BudgetEnforcer()
    ok = budget.preflight_swarm(chunks, model="haiku", est_tokens_per_chunk=8_000)
    assert ok is True, "Small fixture repo should pass budget preflight"

    # Step 3: plan synthesis
    engine = MagicMock()
    engine._bead_store = None
    worktree_mgr = MagicMock(spec=WorktreeManager)
    dispatcher = SwarmDispatcher(
        engine=engine,
        worktree_mgr=worktree_mgr,
        partitioner=partitioner,
        budget=budget,
    )

    plan = dispatcher._synthesize_swarm_plan(chunks, directive, model="claude-haiku")

    # Verify plan structure
    assert plan.task_id.startswith("swarm-")
    assert len(plan.phases) == 4
    implement_steps = plan.phases[1].steps
    assert len(implement_steps) == len(chunks)

    # Each implement step allows only its chunk's files
    for i, (step, chunk) in enumerate(zip(implement_steps, chunks)):
        assert len(step.allowed_paths) == len(chunk.files)
        for allowed, chunk_file in zip(step.allowed_paths, chunk.files):
            assert allowed == str(chunk_file)


def test_swarm_replace_import_on_fixture_repo(fixture_repo: Path) -> None:
    """ReplaceImport directive partitions files using requests correctly."""
    # Add some files that use requests
    for i in range(3):
        (fixture_repo / f"client_{i}.py").write_text(
            f"import requests\n\ndef fetch_{i}(url):\n    return requests.get(url)\n",
            encoding="utf-8",
        )

    partitioner = ASTPartitioner(fixture_repo)
    directive = ReplaceImport(old="requests", new="httpx")

    chunks = partitioner.partition(directive, max_chunks=50)
    # Should find the 3 new client files
    assert len(chunks) >= 1

    budget = BudgetEnforcer()
    ok = budget.preflight_swarm(chunks, model="haiku", est_tokens_per_chunk=8_000)
    assert ok is True


def test_swarm_max_chunks_cap_on_fixture_repo(fixture_repo: Path) -> None:
    """max_chunks cap is respected even with real libcst partitioning."""
    # Create 15 independent files
    for i in range(15):
        (fixture_repo / f"standalone_{i:02d}.py").write_text(
            f"from mypackage.models import DataProcessor\n\nx_{i} = DataProcessor()\n",
            encoding="utf-8",
        )

    partitioner = ASTPartitioner(fixture_repo)
    directive = RenameSymbol(old="DataProcessor", new="DataTransformer")

    chunks = partitioner.partition(directive, max_chunks=5)
    assert len(chunks) <= 5


def test_swarm_end_to_end_dispatch_with_enabled_flag(fixture_repo: Path) -> None:
    """Full dispatch() call with BATON_SWARM_ENABLED=1 succeeds."""
    partitioner = ASTPartitioner(fixture_repo)
    budget = BudgetEnforcer()

    engine = MagicMock()
    engine._bead_store = None
    worktree_mgr = MagicMock(spec=WorktreeManager)

    dispatcher = SwarmDispatcher(
        engine=engine,
        worktree_mgr=worktree_mgr,
        partitioner=partitioner,
        budget=budget,
    )

    directive = RenameSymbol(old="DataProcessor", new="DataTransformer")

    with patch.dict(os.environ, {"BATON_SWARM_ENABLED": "1"}):
        result = dispatcher.dispatch(directive, max_agents=10, model="claude-haiku")

    assert isinstance(result, SwarmResult)
    assert result.swarm_id
    assert result.n_succeeded >= 0
    assert result.n_failed == 0
    assert result.total_cost_usd >= 0.0
