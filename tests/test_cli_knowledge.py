"""Tests for knowledge-delivery CLI flags on `baton plan`.

Covers:
- Flag parsing: --knowledge, --knowledge-pack, --intervention
- Values passed through to IntelligentPlanner.create_plan()
- Default intervention level
- KnowledgeRegistry constructed and passed to planner
"""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from agent_baton.cli.commands.execution import plan_cmd
from agent_baton.models.execution import MachinePlan, PlanPhase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_plan(**kwargs) -> MachinePlan:
    """Return a MachinePlan with defaults — enough for the handler to run."""
    defaults = dict(
        task_id="2026-01-01-test-task-abc12345",
        task_summary="Test task",
        risk_level="LOW",
        budget_tier="standard",
        git_strategy="commit-per-agent",
        phases=[],
        shared_context="",
        pattern_source=None,
        task_type="new-feature",
        explicit_knowledge_packs=[],
        explicit_knowledge_docs=[],
        intervention_level="low",
    )
    defaults.update(kwargs)
    return MachinePlan(**defaults)


def _parse(argv: list[str]) -> argparse.Namespace:
    """Run the plan_cmd register() and parse *argv*."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    plan_cmd.register(sub)
    return parser.parse_args(["plan"] + argv)


# ---------------------------------------------------------------------------
# Flag parsing tests
# ---------------------------------------------------------------------------

class TestFlagParsing:
    def test_knowledge_single(self):
        args = _parse(["do something", "--knowledge", "path/to/doc.md"])
        assert args.knowledge == ["path/to/doc.md"]

    def test_knowledge_repeatable(self):
        args = _parse([
            "do something",
            "--knowledge", "path/to/a.md",
            "--knowledge", "path/to/b.md",
        ])
        assert args.knowledge == ["path/to/a.md", "path/to/b.md"]

    def test_knowledge_default_empty(self):
        args = _parse(["do something"])
        assert args.knowledge == []

    def test_knowledge_pack_single(self):
        args = _parse(["do something", "--knowledge-pack", "compliance"])
        assert args.knowledge_pack == ["compliance"]

    def test_knowledge_pack_repeatable(self):
        args = _parse([
            "do something",
            "--knowledge-pack", "compliance",
            "--knowledge-pack", "agent-baton",
        ])
        assert args.knowledge_pack == ["compliance", "agent-baton"]

    def test_knowledge_pack_default_empty(self):
        args = _parse(["do something"])
        assert args.knowledge_pack == []

    def test_intervention_low(self):
        args = _parse(["do something", "--intervention", "low"])
        assert args.intervention == "low"

    def test_intervention_medium(self):
        args = _parse(["do something", "--intervention", "medium"])
        assert args.intervention == "medium"

    def test_intervention_high(self):
        args = _parse(["do something", "--intervention", "high"])
        assert args.intervention == "high"

    def test_intervention_default_is_low(self):
        args = _parse(["do something"])
        assert args.intervention == "low"

    def test_intervention_rejects_invalid_value(self):
        """argparse should reject values not in choices."""
        with pytest.raises(SystemExit):
            _parse(["do something", "--intervention", "extreme"])

    def test_all_flags_together(self):
        args = _parse([
            "do something",
            "--knowledge", "doc1.md",
            "--knowledge", "doc2.md",
            "--knowledge-pack", "pack-a",
            "--intervention", "high",
        ])
        assert args.knowledge == ["doc1.md", "doc2.md"]
        assert args.knowledge_pack == ["pack-a"]
        assert args.intervention == "high"


# ---------------------------------------------------------------------------
# Pass-through to IntelligentPlanner.create_plan()
# ---------------------------------------------------------------------------

class TestHandlerPassThrough:
    """Verify that handler() passes CLI flag values to create_plan()."""

    def _run_handler(self, argv: list[str]) -> MagicMock:
        """
        Parse *argv*, run handler(), and return the mock create_plan call args.

        Returns the MagicMock that was used as create_plan so tests can
        inspect how it was called.
        """
        args = _parse(argv)
        mock_plan = _make_minimal_plan()
        mock_create_plan = MagicMock(return_value=mock_plan)
        mock_planner = MagicMock()
        mock_planner.create_plan = mock_create_plan
        mock_planner.explain_plan = MagicMock(return_value="explanation")

        with (
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner",
                return_value=mock_planner,
            ),
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.KnowledgeRegistry",
                return_value=MagicMock(),
            ),
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.RetrospectiveEngine",
                return_value=MagicMock(),
            ),
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.DataClassifier",
                return_value=MagicMock(),
            ),
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.PolicyEngine",
                return_value=MagicMock(),
            ),
        ):
            plan_cmd.handler(args)

        return mock_create_plan

    def test_knowledge_docs_passed_through(self):
        mock_create_plan = self._run_handler([
            "do something",
            "--knowledge", "path/doc1.md",
            "--knowledge", "path/doc2.md",
        ])
        _, kwargs = mock_create_plan.call_args
        assert kwargs["explicit_knowledge_docs"] == ["path/doc1.md", "path/doc2.md"]

    def test_knowledge_packs_passed_through(self):
        mock_create_plan = self._run_handler([
            "do something",
            "--knowledge-pack", "compliance",
            "--knowledge-pack", "agent-baton",
        ])
        _, kwargs = mock_create_plan.call_args
        assert kwargs["explicit_knowledge_packs"] == ["compliance", "agent-baton"]

    def test_intervention_level_passed_through(self):
        mock_create_plan = self._run_handler([
            "do something",
            "--intervention", "high",
        ])
        _, kwargs = mock_create_plan.call_args
        assert kwargs["intervention_level"] == "high"

    def test_default_intervention_level_is_low(self):
        mock_create_plan = self._run_handler(["do something"])
        _, kwargs = mock_create_plan.call_args
        assert kwargs["intervention_level"] == "low"

    def test_empty_knowledge_lists_passed_as_empty(self):
        mock_create_plan = self._run_handler(["do something"])
        _, kwargs = mock_create_plan.call_args
        assert kwargs["explicit_knowledge_docs"] == []
        assert kwargs["explicit_knowledge_packs"] == []

    def test_all_three_flags_passed_together(self):
        mock_create_plan = self._run_handler([
            "build a feature",
            "--knowledge", "spec.md",
            "--knowledge-pack", "compliance",
            "--intervention", "medium",
        ])
        _, kwargs = mock_create_plan.call_args
        assert kwargs["explicit_knowledge_docs"] == ["spec.md"]
        assert kwargs["explicit_knowledge_packs"] == ["compliance"]
        assert kwargs["intervention_level"] == "medium"


# ---------------------------------------------------------------------------
# KnowledgeRegistry lifecycle
# ---------------------------------------------------------------------------

class TestKnowledgeRegistryLifecycle:
    """Verify that KnowledgeRegistry is instantiated and load_default_paths() called."""

    def test_knowledge_registry_constructed_and_loaded(self):
        args = _parse(["do something"])
        mock_plan = _make_minimal_plan()
        mock_registry_instance = MagicMock()
        mock_planner_instance = MagicMock()
        mock_planner_instance.create_plan = MagicMock(return_value=mock_plan)
        mock_planner_instance.explain_plan = MagicMock(return_value="")

        MockKnowledgeRegistry = MagicMock(return_value=mock_registry_instance)

        with (
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.KnowledgeRegistry",
                MockKnowledgeRegistry,
            ),
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner",
                return_value=mock_planner_instance,
            ),
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.RetrospectiveEngine",
                return_value=MagicMock(),
            ),
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.DataClassifier",
                return_value=MagicMock(),
            ),
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.PolicyEngine",
                return_value=MagicMock(),
            ),
        ):
            plan_cmd.handler(args)

        # Registry was instantiated
        MockKnowledgeRegistry.assert_called_once()
        # load_default_paths() was called on the instance
        mock_registry_instance.load_default_paths.assert_called_once()

    def test_knowledge_registry_passed_to_planner(self):
        args = _parse(["do something"])
        mock_plan = _make_minimal_plan()
        mock_registry_instance = MagicMock()
        mock_planner_instance = MagicMock()
        mock_planner_instance.create_plan = MagicMock(return_value=mock_plan)

        MockIntelligentPlanner = MagicMock(return_value=mock_planner_instance)

        with (
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.KnowledgeRegistry",
                return_value=mock_registry_instance,
            ),
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.IntelligentPlanner",
                MockIntelligentPlanner,
            ),
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.RetrospectiveEngine",
                return_value=MagicMock(),
            ),
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.DataClassifier",
                return_value=MagicMock(),
            ),
            patch(
                "agent_baton.cli.commands.execution.plan_cmd.PolicyEngine",
                return_value=MagicMock(),
            ),
        ):
            plan_cmd.handler(args)

        # IntelligentPlanner was called with knowledge_registry=<our mock instance>
        _, planner_kwargs = MockIntelligentPlanner.call_args
        assert planner_kwargs.get("knowledge_registry") is mock_registry_instance


# ---------------------------------------------------------------------------
# MachinePlan model — knowledge fields round-trip
# ---------------------------------------------------------------------------

class TestMachinePlanKnowledgeFields:
    """Verify the MachinePlan model serialises/deserialises knowledge fields."""

    def test_to_dict_includes_knowledge_fields(self):
        plan = _make_minimal_plan(
            explicit_knowledge_packs=["compliance"],
            explicit_knowledge_docs=["spec.md"],
            intervention_level="medium",
        )
        d = plan.to_dict()
        assert d["explicit_knowledge_packs"] == ["compliance"]
        assert d["explicit_knowledge_docs"] == ["spec.md"]
        assert d["intervention_level"] == "medium"

    def test_from_dict_restores_knowledge_fields(self):
        plan = _make_minimal_plan(
            explicit_knowledge_packs=["agent-baton"],
            explicit_knowledge_docs=["path/to/doc.md"],
            intervention_level="high",
        )
        restored = MachinePlan.from_dict(plan.to_dict())
        assert restored.explicit_knowledge_packs == ["agent-baton"]
        assert restored.explicit_knowledge_docs == ["path/to/doc.md"]
        assert restored.intervention_level == "high"

    def test_from_dict_defaults_intervention_to_low(self):
        plan = _make_minimal_plan()
        d = plan.to_dict()
        d.pop("intervention_level", None)
        restored = MachinePlan.from_dict(d)
        assert restored.intervention_level == "low"

    def test_from_dict_defaults_knowledge_lists_to_empty(self):
        plan = _make_minimal_plan()
        d = plan.to_dict()
        d.pop("explicit_knowledge_packs", None)
        d.pop("explicit_knowledge_docs", None)
        restored = MachinePlan.from_dict(d)
        assert restored.explicit_knowledge_packs == []
        assert restored.explicit_knowledge_docs == []


# ---------------------------------------------------------------------------
# IntelligentPlanner.create_plan() — knowledge params wired to MachinePlan
# ---------------------------------------------------------------------------

class TestPlannerKnowledgePassThrough:
    """Integration-level: create_plan() stores knowledge args on the returned plan."""

    @pytest.fixture
    def tmp_agents_dir(self, tmp_path: Path) -> Path:
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        for name in ("backend-engineer--python", "architect", "test-engineer"):
            content = (
                f"---\nname: {name}\ndescription: Test agent.\nmodel: sonnet\n"
                f"permissionMode: default\ntools: Read, Write\n---\n\n# {name}\n"
            )
            (agents_dir / f"{name}.md").write_text(content, encoding="utf-8")
        return agents_dir

    @pytest.fixture
    def planner(self, tmp_path: Path, tmp_agents_dir: Path):
        from agent_baton.core.engine.planner import IntelligentPlanner
        from agent_baton.core.orchestration.registry import AgentRegistry
        from agent_baton.core.orchestration.router import AgentRouter

        ctx = tmp_path / "team-context"
        ctx.mkdir()
        p = IntelligentPlanner(team_context_root=ctx)
        reg = AgentRegistry()
        reg.load_directory(tmp_agents_dir)
        p._registry = reg
        p._router = AgentRouter(reg)
        return p

    def test_explicit_knowledge_packs_stored_on_plan(self, planner):
        plan = planner.create_plan(
            "Build a feature",
            explicit_knowledge_packs=["compliance", "agent-baton"],
        )
        assert plan.explicit_knowledge_packs == ["compliance", "agent-baton"]

    def test_explicit_knowledge_docs_stored_on_plan(self, planner):
        plan = planner.create_plan(
            "Build a feature",
            explicit_knowledge_docs=["path/to/spec.md"],
        )
        assert plan.explicit_knowledge_docs == ["path/to/spec.md"]

    def test_intervention_level_stored_on_plan(self, planner):
        plan = planner.create_plan(
            "Build a feature",
            intervention_level="high",
        )
        assert plan.intervention_level == "high"

    def test_default_intervention_level_is_low(self, planner):
        plan = planner.create_plan("Build a feature")
        assert plan.intervention_level == "low"

    def test_none_knowledge_packs_becomes_empty_list(self, planner):
        plan = planner.create_plan(
            "Build a feature",
            explicit_knowledge_packs=None,
        )
        assert plan.explicit_knowledge_packs == []

    def test_none_knowledge_docs_becomes_empty_list(self, planner):
        plan = planner.create_plan(
            "Build a feature",
            explicit_knowledge_docs=None,
        )
        assert plan.explicit_knowledge_docs == []
