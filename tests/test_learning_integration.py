"""Integration tests for the learning automation system.

Verifies that the three main consumers (AgentRouter, IntelligentPlanner gate
commands, and IntelligentPlanner agent drops) correctly read from a
learned-overrides.json file written by LearnedOverrides.

Strategy: the router and planner use ``LearnedOverrides()`` with no arguments,
which resolves the default path relative to cwd.  Tests use ``monkeypatch.chdir``
to redirect cwd to a tmp directory so writes go to an isolated location.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_baton.core.learn.overrides import LearnedOverrides, _DEFAULT_PATH
from agent_baton.core.orchestration.registry import AgentRegistry
from agent_baton.core.orchestration.router import AgentRouter, StackProfile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry_with(*agent_names: str) -> AgentRegistry:
    """Build a minimal in-memory registry containing the given agent names."""
    registry = AgentRegistry()
    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        for name in agent_names:
            (d / f"{name}.md").write_text(
                f"---\nname: {name}\ndescription: {name} description\n---\nbody\n",
                encoding="utf-8",
            )
        registry.load_directory(d)
    return registry


def _write_overrides(root: Path, data: dict) -> Path:
    """Write a learned-overrides.json under root/.claude/team-context/ and return its path."""
    ctx = root / ".claude" / "team-context"
    ctx.mkdir(parents=True, exist_ok=True)
    p = ctx / "learned-overrides.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# AgentRouter reads flavor_map from learned-overrides.json
# ---------------------------------------------------------------------------


class TestRouterReadsFlavourOverrides:
    def test_learned_flavor_takes_precedence_over_hardcoded_map(
        self, tmp_path: Path, monkeypatch
    ):
        """When learned-overrides.json says backend-engineer -> fastapi for python,
        AgentRouter.route() should return backend-engineer--fastapi if it exists."""
        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()  # uses cwd-relative default
        ovr.add_flavor_override("python", "backend-engineer", "fastapi")

        registry = _make_registry_with(
            "backend-engineer",
            "backend-engineer--python",
            "backend-engineer--fastapi",
        )
        router = AgentRouter(registry)
        stack = StackProfile(language="python", framework=None)
        result = router.route("backend-engineer", stack=stack)

        assert result == "backend-engineer--fastapi"

    def test_learned_flavor_only_used_if_agent_exists_in_registry(
        self, tmp_path: Path, monkeypatch
    ):
        """If the learned flavor points to a non-existent agent, fall back to default."""
        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()
        ovr.add_flavor_override("python", "backend-engineer", "nonexistent-flavor")

        registry = _make_registry_with("backend-engineer", "backend-engineer--python")
        router = AgentRouter(registry)
        stack = StackProfile(language="python", framework=None)
        result = router.route("backend-engineer", stack=stack)

        # Falls back to hardcoded map → backend-engineer--python
        assert result == "backend-engineer--python"

    def test_router_unaffected_when_overrides_file_missing(
        self, tmp_path: Path, monkeypatch
    ):
        """Router must not crash if the overrides file doesn't exist."""
        monkeypatch.chdir(tmp_path)
        # No overrides file created — default path doesn't exist

        registry = _make_registry_with("backend-engineer", "backend-engineer--python")
        router = AgentRouter(registry)
        stack = StackProfile(language="python", framework=None)
        result = router.route("backend-engineer", stack=stack)

        assert result == "backend-engineer--python"

    def test_router_unaffected_when_overrides_file_corrupt(
        self, tmp_path: Path, monkeypatch
    ):
        """Router must not crash if the overrides file contains invalid JSON."""
        monkeypatch.chdir(tmp_path)
        ctx = tmp_path / ".claude" / "team-context"
        ctx.mkdir(parents=True)
        (ctx / "learned-overrides.json").write_text("{{ CORRUPT }", encoding="utf-8")

        registry = _make_registry_with("backend-engineer", "backend-engineer--python")
        router = AgentRouter(registry)
        stack = StackProfile(language="python", framework=None)
        result = router.route("backend-engineer", stack=stack)

        # LearnedOverrides.load() returns empty defaults on corrupt file →
        # no learned override → falls back to hardcoded map
        assert result == "backend-engineer--python"

    def test_learned_stack_key_matches_composite_key(
        self, tmp_path: Path, monkeypatch
    ):
        """Stack 'python' + framework 'react' should use key 'python/react'."""
        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()
        ovr.add_flavor_override("python/react", "backend-engineer", "python")

        registry = _make_registry_with("backend-engineer", "backend-engineer--python")
        router = AgentRouter(registry)
        stack = StackProfile(language="python", framework="react")
        result = router.route("backend-engineer", stack=stack)

        assert result == "backend-engineer--python"


# ---------------------------------------------------------------------------
# IntelligentPlanner reads gate_commands from learned-overrides.json
# ---------------------------------------------------------------------------


class TestPlannerReadGateOverrides:
    """Verify that IntelligentPlanner._default_gate() merges gate overrides."""

    def test_gate_override_applied_for_matching_language(
        self, tmp_path: Path, monkeypatch
    ):
        """When typescript:test override is 'vitest run', the plan gate should use it."""
        from agent_baton.core.engine.planner import IntelligentPlanner

        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()
        ovr.add_gate_override("typescript", "test", "vitest run")

        stack = StackProfile(language="typescript", framework=None)
        planner = IntelligentPlanner()
        gate = planner._default_gate("Test", stack)

        assert gate is not None
        assert gate.command == "vitest run"

    def test_gate_override_not_applied_for_other_language(
        self, tmp_path: Path, monkeypatch
    ):
        """A typescript override should not affect a python stack."""
        from agent_baton.core.engine.planner import IntelligentPlanner

        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()
        ovr.add_gate_override("typescript", "test", "vitest run")

        python_stack = StackProfile(language="python", framework=None)
        planner = IntelligentPlanner()
        gate = planner._default_gate("Test", python_stack)

        assert gate is not None
        assert "vitest" not in gate.command

    def test_gate_override_for_build_phase(self, tmp_path: Path, monkeypatch):
        """Overrides should work for the build gate type as well."""
        from agent_baton.core.engine.planner import IntelligentPlanner

        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()
        ovr.add_gate_override("typescript", "build", "npx tsc --noEmit")

        ts_stack = StackProfile(language="typescript", framework=None)
        planner = IntelligentPlanner()
        gate = planner._default_gate("Implement", ts_stack)

        assert gate is not None
        assert gate.command == "npx tsc --noEmit"

    def test_planner_does_not_crash_on_missing_overrides_file(
        self, tmp_path: Path, monkeypatch
    ):
        """_default_gate must be resilient even if overrides file doesn't exist."""
        from agent_baton.core.engine.planner import IntelligentPlanner

        monkeypatch.chdir(tmp_path)
        # No overrides file created

        python_stack = StackProfile(language="python", framework=None)
        planner = IntelligentPlanner()
        gate = planner._default_gate("Test", python_stack)

        assert gate is not None


# ---------------------------------------------------------------------------
# IntelligentPlanner reads agent_drops from learned-overrides.json
# ---------------------------------------------------------------------------


class TestPlannerReadAgentDrops:
    """Verify that _apply_retro_feedback merges learned agent drops."""

    def _feedback_stub(self, drop: list[str] | None = None, prefer: list[str] | None = None):
        stub = MagicMock()
        stub.agents_to_drop.return_value = drop or []
        stub.agents_to_prefer.return_value = prefer or []
        return stub

    def test_learned_drop_excludes_agent_from_plan(self, tmp_path: Path, monkeypatch):
        from agent_baton.core.engine.planner import IntelligentPlanner

        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()
        ovr.add_agent_drop("visualization-expert")

        planner = IntelligentPlanner()
        agents = ["backend-engineer", "visualization-expert", "test-engineer"]
        result = planner._apply_retro_feedback(agents, self._feedback_stub())

        assert "visualization-expert" not in result

    def test_learned_drop_preserves_other_agents(self, tmp_path: Path, monkeypatch):
        from agent_baton.core.engine.planner import IntelligentPlanner

        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()
        ovr.add_agent_drop("visualization-expert")

        planner = IntelligentPlanner()
        agents = ["backend-engineer", "visualization-expert", "test-engineer"]
        result = planner._apply_retro_feedback(agents, self._feedback_stub())

        assert "backend-engineer" in result
        assert "test-engineer" in result

    def test_learned_drop_does_not_empty_agent_list(self, tmp_path: Path, monkeypatch):
        """If dropping all agents would leave an empty list, keep the original."""
        from agent_baton.core.engine.planner import IntelligentPlanner

        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()
        ovr.add_agent_drop("backend-engineer")

        planner = IntelligentPlanner()
        agents = ["backend-engineer"]  # Only one agent — drop would empty the list
        result = planner._apply_retro_feedback(agents, self._feedback_stub())

        assert len(result) >= 1

    def test_learned_drop_combined_with_retro_drops(self, tmp_path: Path, monkeypatch):
        """Learned drops and retro drops should both be applied."""
        from agent_baton.core.engine.planner import IntelligentPlanner

        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()
        ovr.add_agent_drop("data-scientist")

        planner = IntelligentPlanner()
        feedback = self._feedback_stub(drop=["visualization-expert"])
        agents = ["backend-engineer", "data-scientist", "visualization-expert", "test-engineer"]
        result = planner._apply_retro_feedback(agents, feedback)

        assert "data-scientist" not in result
        assert "visualization-expert" not in result
        assert "backend-engineer" in result

    def test_flavored_agent_excluded_by_base_name_drop(self, tmp_path: Path, monkeypatch):
        """Dropping 'backend-engineer' should also exclude 'backend-engineer--python'."""
        from agent_baton.core.engine.planner import IntelligentPlanner

        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()
        ovr.add_agent_drop("backend-engineer")

        planner = IntelligentPlanner()
        agents = ["backend-engineer--python", "test-engineer"]
        result = planner._apply_retro_feedback(agents, self._feedback_stub())

        assert "backend-engineer--python" not in result

    def test_planner_does_not_crash_on_corrupt_overrides_file(
        self, tmp_path: Path, monkeypatch
    ):
        """_apply_retro_feedback must be resilient to a corrupt overrides file."""
        from agent_baton.core.engine.planner import IntelligentPlanner

        monkeypatch.chdir(tmp_path)
        ctx = tmp_path / ".claude" / "team-context"
        ctx.mkdir(parents=True)
        (ctx / "learned-overrides.json").write_text("{ NOT VALID }", encoding="utf-8")

        planner = IntelligentPlanner()
        agents = ["backend-engineer", "test-engineer"]
        result = planner._apply_retro_feedback(agents, self._feedback_stub())

        assert len(result) > 0


# ---------------------------------------------------------------------------
# Full loop: LearnedOverrides written then consumed by router
# ---------------------------------------------------------------------------


class TestFullLoopOverrideWriteThenConsume:
    def test_write_override_then_router_reads_it(self, tmp_path: Path, monkeypatch):
        """End-to-end: write a flavor override and verify the router picks it up."""
        monkeypatch.chdir(tmp_path)

        # Step 1: Write the override
        ovr = LearnedOverrides()
        ovr.add_flavor_override("python", "backend-engineer", "python")

        # Step 2: Router reads it (uses same cwd-relative default path)
        registry = _make_registry_with("backend-engineer", "backend-engineer--python")
        router = AgentRouter(registry)
        stack = StackProfile(language="python", framework=None)
        result = router.route("backend-engineer", stack=stack)

        assert result == "backend-engineer--python"

    def test_multiple_overrides_all_consumed(self, tmp_path: Path, monkeypatch):
        """Multiple overrides written in one session should all be readable."""
        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()
        ovr.add_flavor_override("python", "backend-engineer", "python")
        ovr.add_flavor_override("typescript", "frontend-engineer", "react")
        ovr.add_gate_override("typescript", "test", "vitest run")
        ovr.add_agent_drop("visualization-expert")

        loaded_ovr = LearnedOverrides()
        flavor_ovrs = loaded_ovr.get_flavor_overrides()
        gate_ovrs = loaded_ovr.get_gate_overrides()
        drops = loaded_ovr.get_agent_drops()

        assert flavor_ovrs["python"]["backend-engineer"] == "python"
        assert flavor_ovrs["typescript"]["frontend-engineer"] == "react"
        assert gate_ovrs["typescript"]["test"] == "vitest run"
        assert "visualization-expert" in drops

    def test_version_increments_across_multiple_writes(self, tmp_path: Path, monkeypatch):
        """Each mutation increments the version counter by 1."""
        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()
        ovr.add_flavor_override("python", "backend-engineer", "python")   # 1→2
        ovr.add_gate_override("typescript", "test", "vitest run")          # 2→3
        ovr.add_agent_drop("visualization-expert")                          # 3→4

        data = LearnedOverrides().load()
        assert data["version"] == 4

    def test_last_updated_is_set(self, tmp_path: Path, monkeypatch):
        """last_updated should be a non-empty ISO timestamp after any write."""
        monkeypatch.chdir(tmp_path)
        ovr = LearnedOverrides()
        ovr.add_agent_drop("agent-x")

        data = LearnedOverrides().load()
        assert data["last_updated"] != ""
        # Basic ISO format check: contains 'T' separator
        assert "T" in data["last_updated"]
