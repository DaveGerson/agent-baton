"""Regression tests for bd-124f — focused gate-command scoping.

Verifies that:
- ``focused`` scope (default) produces pytest commands scoped to the test
  files that cover the changed source paths, not the full suite.
- ``full`` scope produces legacy unscoped ``pytest`` / ``pytest --cov`` commands.
- ``smoke`` scope produces import-smoke (build) and collect-only (test) commands.
- Empty changed-paths falls back to import-smoke (build) and ``pytest --co``
  (test) under ``focused`` scope.
- ``_test_files_for_changes`` maps source paths to test file names correctly.
- ``_coverage_package_for_changes`` derives the correct ``--cov`` target.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.engine.planner import (
    GateScope,
    IntelligentPlanner,
    _coverage_package_for_changes,
    _test_files_for_changes,
)
from agent_baton.models.execution import PlanGate, PlanPhase, PlanStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_agent_dir(tmp_path: Path) -> Path:
    """Create a minimal agents directory so the registry doesn't fail."""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(exist_ok=True)
    for name in [
        "backend-engineer", "architect", "test-engineer", "code-reviewer",
    ]:
        content = (
            f"---\nname: {name}\ndescription: {name} specialist.\n"
            f"model: sonnet\npermissionMode: default\ntools: Read, Write\n---\n"
        )
        (agents_dir / f"{name}.md").write_text(content, encoding="utf-8")
    return agents_dir


def _make_planner(tmp_path: Path) -> IntelligentPlanner:
    from agent_baton.core.orchestration.registry import AgentRegistry
    from agent_baton.core.orchestration.router import AgentRouter

    ctx = tmp_path / "team-context"
    ctx.mkdir()
    agents_dir = _make_agent_dir(tmp_path)

    p = IntelligentPlanner(team_context_root=ctx)
    reg = AgentRegistry()
    reg.load_directory(agents_dir)
    p._registry = reg
    p._router = AgentRouter(reg)
    return p


def _make_test_file(root: Path, rel_path: str) -> Path:
    """Create a stub test file at root/rel_path and return its Path."""
    target = root / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# stub\n", encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Unit tests: _test_files_for_changes
# ---------------------------------------------------------------------------

class TestTestFilesForChanges:
    def test_maps_engine_module_to_test_file(self, tmp_path: Path) -> None:
        """A source file under core/engine maps to tests/test_<stem>.py."""
        _make_test_file(tmp_path, "tests/test_worktree_manager.py")
        result = _test_files_for_changes(
            ["agent_baton/core/engine/worktree_manager.py"],
            project_root=tmp_path,
        )
        assert "tests/test_worktree_manager.py" in result

    def test_maps_cli_command_to_test_file(self, tmp_path: Path) -> None:
        """A CLI command module maps to tests/test_<stem>.py."""
        _make_test_file(tmp_path, "tests/test_plan_cmd.py")
        result = _test_files_for_changes(
            ["agent_baton/cli/commands/plan_cmd.py"],
            project_root=tmp_path,
        )
        assert "tests/test_plan_cmd.py" in result

    def test_test_file_included_directly(self, tmp_path: Path) -> None:
        """A path that is itself a test file is returned directly."""
        _make_test_file(tmp_path, "tests/test_executor.py")
        result = _test_files_for_changes(
            ["tests/test_executor.py"],
            project_root=tmp_path,
        )
        assert "tests/test_executor.py" in result

    def test_skip_init_py(self, tmp_path: Path) -> None:
        """__init__.py is skipped — it is tested via its consumers."""
        _make_test_file(tmp_path, "tests/test___init__.py")
        result = _test_files_for_changes(
            ["agent_baton/core/engine/__init__.py"],
            project_root=tmp_path,
        )
        assert result == []

    def test_skip_validators(self, tmp_path: Path) -> None:
        """_validators.py is skipped — it is tested via consumers."""
        _make_test_file(tmp_path, "tests/test__validators.py")
        result = _test_files_for_changes(
            ["agent_baton/cli/commands/execution/_validators.py"],
            project_root=tmp_path,
        )
        assert result == []

    def test_skip_templates(self, tmp_path: Path) -> None:
        """Files under agent_baton/templates/ are skipped."""
        result = _test_files_for_changes(
            ["agent_baton/templates/CLAUDE.md"],
            project_root=tmp_path,
        )
        assert result == []

    def test_empty_paths_returns_empty(self, tmp_path: Path) -> None:
        """No changed paths → no test files."""
        result = _test_files_for_changes([], project_root=tmp_path)
        assert result == []

    def test_nonexistent_test_file_not_included(self, tmp_path: Path) -> None:
        """Source file with no corresponding test file → empty result."""
        result = _test_files_for_changes(
            ["agent_baton/core/engine/worktree_manager.py"],
            project_root=tmp_path,
        )
        assert result == []

    def test_wildcard_matches_suffixed_test_files(self, tmp_path: Path) -> None:
        """test_<stem>_extra.py is found by the wildcard pattern."""
        _make_test_file(tmp_path, "tests/test_planner_extra.py")
        result = _test_files_for_changes(
            ["agent_baton/core/engine/planner.py"],
            project_root=tmp_path,
        )
        assert "tests/test_planner_extra.py" in result

    def test_deduplication(self, tmp_path: Path) -> None:
        """The same test file is not repeated when two source files map to it."""
        _make_test_file(tmp_path, "tests/test_executor.py")
        result = _test_files_for_changes(
            [
                "agent_baton/core/engine/executor.py",
                "agent_baton/core/engine/executor.py",
            ],
            project_root=tmp_path,
        )
        assert result.count("tests/test_executor.py") == 1


# ---------------------------------------------------------------------------
# Unit tests: _coverage_package_for_changes
# ---------------------------------------------------------------------------

class TestCoveragePackageForChanges:
    def test_single_engine_module(self) -> None:
        result = _coverage_package_for_changes(
            ["agent_baton/core/engine/planner.py"]
        )
        assert result == "agent_baton/core/engine"

    def test_two_modules_same_subpackage(self) -> None:
        result = _coverage_package_for_changes(
            [
                "agent_baton/core/engine/planner.py",
                "agent_baton/core/engine/executor.py",
            ]
        )
        assert result == "agent_baton/core/engine"

    def test_two_modules_different_subpackages(self) -> None:
        result = _coverage_package_for_changes(
            [
                "agent_baton/core/engine/planner.py",
                "agent_baton/cli/commands/plan_cmd.py",
            ]
        )
        assert result == "agent_baton"

    def test_empty_paths(self) -> None:
        assert _coverage_package_for_changes([]) == ""

    def test_non_agent_baton_paths(self) -> None:
        assert _coverage_package_for_changes(["some/other/module.py"]) == ""


# ---------------------------------------------------------------------------
# Integration tests: _default_gate scope behaviour
# ---------------------------------------------------------------------------

class TestDefaultGateScope:
    """Tests for IntelligentPlanner._default_gate with gate_scope variants."""

    def _planner(self, tmp_path: Path) -> IntelligentPlanner:
        return _make_planner(tmp_path)

    # --- full scope (legacy behaviour) ---

    def test_full_scope_build_gate_is_plain_pytest(self, tmp_path: Path) -> None:
        p = self._planner(tmp_path)
        gate = p._default_gate("Implement", gate_scope="full")
        assert gate is not None
        assert gate.gate_type == "build"
        assert gate.command == "pytest"

    def test_full_scope_test_gate_is_pytest_cov(self, tmp_path: Path) -> None:
        p = self._planner(tmp_path)
        gate = p._default_gate("Test", gate_scope="full")
        assert gate is not None
        assert gate.gate_type == "test"
        assert gate.command == "pytest --cov"

    # --- smoke scope ---

    def test_smoke_scope_build_gate_is_import_check(self, tmp_path: Path) -> None:
        p = self._planner(tmp_path)
        gate = p._default_gate("Implement", gate_scope="smoke")
        assert gate is not None
        assert gate.gate_type == "build"
        assert "import agent_baton" in gate.command

    def test_smoke_scope_test_gate_is_collect_only(self, tmp_path: Path) -> None:
        p = self._planner(tmp_path)
        gate = p._default_gate("Test", gate_scope="smoke")
        assert gate is not None
        assert gate.gate_type == "test"
        assert "--co" in gate.command

    # --- focused scope: no matching test files → fallback ---

    def test_focused_scope_build_no_test_files_falls_back_to_smoke(
        self, tmp_path: Path
    ) -> None:
        p = self._planner(tmp_path)
        gate = p._default_gate(
            "Implement",
            gate_scope="focused",
            changed_paths=["agent_baton/core/engine/worktree_manager.py"],
            project_root=tmp_path,  # tmp_path has no tests/ dir
        )
        assert gate is not None
        assert gate.gate_type == "build"
        assert "import agent_baton" in gate.command

    def test_focused_scope_test_no_test_files_falls_back_to_collect_only(
        self, tmp_path: Path
    ) -> None:
        p = self._planner(tmp_path)
        gate = p._default_gate(
            "Test",
            gate_scope="focused",
            changed_paths=["agent_baton/core/engine/worktree_manager.py"],
            project_root=tmp_path,
        )
        assert gate is not None
        assert gate.gate_type == "test"
        assert "--co" in gate.command

    # --- focused scope: matching test file exists ---

    def test_focused_scope_build_with_test_file(self, tmp_path: Path) -> None:
        _make_test_file(tmp_path, "tests/test_worktree_manager.py")
        p = self._planner(tmp_path)
        gate = p._default_gate(
            "Implement",
            gate_scope="focused",
            changed_paths=["agent_baton/core/engine/worktree_manager.py"],
            project_root=tmp_path,
        )
        assert gate is not None
        assert gate.gate_type == "build"
        assert "tests/test_worktree_manager.py" in gate.command
        # Must NOT be plain pytest (i.e. scoped, not full)
        assert gate.command != "pytest"

    def test_focused_scope_test_with_test_file_includes_cov_flag(
        self, tmp_path: Path
    ) -> None:
        _make_test_file(tmp_path, "tests/test_worktree_manager.py")
        p = self._planner(tmp_path)
        gate = p._default_gate(
            "Test",
            gate_scope="focused",
            changed_paths=["agent_baton/core/engine/worktree_manager.py"],
            project_root=tmp_path,
        )
        assert gate is not None
        assert gate.gate_type == "test"
        assert "--cov" in gate.command
        assert "tests/test_worktree_manager.py" in gate.command
        # Must NOT be bare pytest --cov
        assert gate.command != "pytest --cov"

    def test_focused_scope_test_cov_scoped_to_engine_package(
        self, tmp_path: Path
    ) -> None:
        """Coverage flag points at the correct subpackage, not the whole repo."""
        _make_test_file(tmp_path, "tests/test_worktree_manager.py")
        p = self._planner(tmp_path)
        gate = p._default_gate(
            "Test",
            gate_scope="focused",
            changed_paths=["agent_baton/core/engine/worktree_manager.py"],
            project_root=tmp_path,
        )
        assert gate is not None
        assert "--cov=agent_baton/core/engine" in gate.command

    # --- no-gate phases are unaffected ---

    def test_design_phase_returns_no_gate(self, tmp_path: Path) -> None:
        p = self._planner(tmp_path)
        for scope in ("focused", "full", "smoke"):
            assert p._default_gate("Design", gate_scope=scope) is None  # type: ignore[arg-type]

    def test_review_phase_returns_no_gate(self, tmp_path: Path) -> None:
        p = self._planner(tmp_path)
        assert p._default_gate("Review", gate_scope="focused") is None

    # --- empty changed_paths with focused scope ---

    def test_focused_scope_empty_changed_paths_build_is_smoke(
        self, tmp_path: Path
    ) -> None:
        p = self._planner(tmp_path)
        gate = p._default_gate(
            "Implement",
            gate_scope="focused",
            changed_paths=[],
            project_root=tmp_path,
        )
        assert gate is not None
        assert "import agent_baton" in gate.command

    def test_focused_scope_empty_changed_paths_test_is_collect_only(
        self, tmp_path: Path
    ) -> None:
        p = self._planner(tmp_path)
        gate = p._default_gate(
            "Test",
            gate_scope="focused",
            changed_paths=[],
            project_root=tmp_path,
        )
        assert gate is not None
        assert "--co" in gate.command


# ---------------------------------------------------------------------------
# End-to-end: create_plan gate_scope threading
# ---------------------------------------------------------------------------

class TestCreatePlanGateScope:
    """Verify that gate_scope is honoured end-to-end through create_plan."""

    def test_create_plan_full_scope_produces_plain_pytest(
        self, tmp_path: Path
    ) -> None:
        p = _make_planner(tmp_path)
        plan = p.create_plan(
            "Add a worktree manager utility",
            gate_scope="full",
            project_root=tmp_path,
        )
        build_gates = [
            phase.gate
            for phase in plan.phases
            if phase.gate and phase.gate.gate_type == "build"
        ]
        test_gates = [
            phase.gate
            for phase in plan.phases
            if phase.gate and phase.gate.gate_type == "test"
        ]
        for gate in build_gates:
            assert gate.command == "pytest", (
                f"Expected plain 'pytest' for full scope, got {gate.command!r}"
            )
        for gate in test_gates:
            assert gate.command == "pytest --cov", (
                f"Expected 'pytest --cov' for full scope, got {gate.command!r}"
            )

    def test_create_plan_default_scope_is_focused(self, tmp_path: Path) -> None:
        """Default gate_scope is 'focused' — build gate must not be plain pytest."""
        p = _make_planner(tmp_path)
        plan = p.create_plan(
            "Implement new executor feature",
            project_root=tmp_path,
            # gate_scope omitted — should default to "focused"
        )
        build_gates = [
            phase.gate
            for phase in plan.phases
            if phase.gate and phase.gate.gate_type == "build"
        ]
        for gate in build_gates:
            # With no test files present in tmp_path, focused falls back to
            # import smoke — either way it must NOT be plain "pytest"
            assert gate.command != "pytest", (
                f"Plain 'pytest' leaked into focused plan: {gate.command!r}"
            )

    def test_create_plan_smoke_scope(self, tmp_path: Path) -> None:
        p = _make_planner(tmp_path)
        plan = p.create_plan(
            "Quick patch on executor",
            gate_scope="smoke",
            project_root=tmp_path,
        )
        for phase in plan.phases:
            if phase.gate and phase.gate.gate_type == "build":
                assert "import agent_baton" in phase.gate.command
            if phase.gate and phase.gate.gate_type == "test":
                assert "--co" in phase.gate.command
