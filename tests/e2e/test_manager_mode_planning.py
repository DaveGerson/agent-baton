"""Manager-mode planning end-to-end test (PRD Milestone 8 / Wave 3 Task 12).

Runs the REAL 7-stage ``IntelligentPlanner.create_plan()`` +
``ManagerModePlanner.build_and_write()`` against
``tests/fixtures/medium_project_repo`` -- no stubs, no mocks of the
planning pipeline itself. Asserts every PRD M8 item (see
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §16
Milestone 8) plus a non-manager control case proving manager-mode
artifacts are strictly additive.

No-network guard: ``ANTHROPIC_API_KEY`` is deleted, ``BATON_MANAGER_ENRICH``
is forced to ``off``, ``BATON_PLAN_REVIEW`` is unset, and the ``anthropic``
module is replaced with a call-tracking fake so a live SDK client can never
actually be constructed -- asserted empty at the end of the test rather
than relying on exception semantics (``maybe_enrich_charter`` swallows
*any* exception, so a raising fake would prove nothing).

The task classifier is pinned to the deterministic ``KeywordClassifier``
(bypassing ``FallbackClassifier``'s default ``TalentAgentClassifier`` ->
keyword degradation path) so this test never shells out to a `claude` CLI
that might happen to be installed and authenticated on the machine running
it -- see docs/internal/manager-mode-pmo-plan.md Wave 3 / Task 12's
"no live Claude invocation occurs" requirement.
"""
from __future__ import annotations

import shutil
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.engine.classifier import KeywordClassifier
from agent_baton.core.engine.planner import IntelligentPlanner
from agent_baton.core.govern.classifier import DataClassifier
from agent_baton.core.govern.policy import PolicyEngine
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.manager.planner import ManagerModePlanner
from agent_baton.core.observe.retrospective import RetrospectiveEngine
from agent_baton.core.orchestration.context import ContextManager
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry

_FIXTURE_REPO = Path(__file__).resolve().parents[1] / "fixtures" / "medium_project_repo"
_TASK_SUMMARY = "Add a reporting endpoint with tests and docs"


class _AnthropicTracker:
    """Fake ``anthropic`` module: records every ``Anthropic(...)``
    construction instead of doing anything real. Installed in
    ``sys.modules`` so ANY code path that does ``import anthropic;
    anthropic.Anthropic(...)`` is caught, regardless of whether the
    caller's own exception handling would otherwise hide it."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def Anthropic(self, *args: Any, **kwargs: Any) -> Any:  # noqa: N802 - matches SDK's class name
        self.calls.append((args, kwargs))
        raise AssertionError("anthropic.Anthropic() must never be constructed in this E2E")


@pytest.fixture
def anthropic_tracker(monkeypatch: Any) -> _AnthropicTracker:
    tracker = _AnthropicTracker()
    fake_module = types.ModuleType("anthropic")
    fake_module.Anthropic = tracker.Anthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)
    return tracker


@pytest.fixture
def fixture_repo(tmp_path: Path, monkeypatch: Any) -> Path:
    """A fresh, writable copy of tests/fixtures/medium_project_repo, cwd'd
    into so every relative-path lookup (KnowledgeRegistry.load_default_paths,
    AgentRegistry.load_default_paths, ManagerConfig.find_config_file) reads
    from the fixture instead of the real agent-baton repo."""
    repo_copy = tmp_path / "medium_project_repo"
    shutil.copytree(_FIXTURE_REPO, repo_copy)
    monkeypatch.chdir(repo_copy)
    return repo_copy


@pytest.fixture(autouse=True)
def _no_network_env(monkeypatch: Any) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("BATON_MANAGER_ENRICH", "off")
    monkeypatch.delenv("BATON_PLAN_REVIEW", raising=False)


def _build_planner(fixture_repo: Path, registry: KnowledgeRegistry) -> IntelligentPlanner:
    return IntelligentPlanner(
        team_context_root=fixture_repo / ".claude" / "team-context",
        classifier=DataClassifier(),
        policy_engine=PolicyEngine(),
        retro_engine=RetrospectiveEngine(),
        knowledge_registry=registry,
        task_classifier=KeywordClassifier(),
        bead_store=None,
    )


def test_manager_mode_planning_produces_full_pmo_packet(
    fixture_repo: Path, anthropic_tracker: _AnthropicTracker
) -> None:
    registry = KnowledgeRegistry()
    registry.load_default_paths()

    planner = _build_planner(fixture_repo, registry)
    plan = planner.create_plan(
        _TASK_SUMMARY,
        complexity="medium",
        project_root=fixture_repo,
        gate_scope="focused",
    )
    plan.manager_mode = True

    manager_config = ManagerConfig.load(fixture_repo)
    assert manager_config.policies.phase_completion.adversarial_review == "always"
    assert manager_config.policies.phase_completion.handoff_required is True
    assert manager_config.policies.project_completion.adversarial_review == "always"

    ctx_dir = fixture_repo / ".claude" / "team-context"
    manager_planner = ManagerModePlanner(
        manager_config,
        project_root=fixture_repo,
        team_context_dir=ctx_dir,
        knowledge_registry=registry,
        cli_gate_scope_explicit=False,
    )
    artifacts = manager_planner.build_and_write(plan, plan.task_summary)

    paths = ManagerArtifactPaths(ctx_dir, plan.task_id)

    # --- PRD M8 required assertions ---------------------------------
    assert paths.charter.is_file(), "project-charter.md must exist"
    assert paths.scope_map.is_file(), "scope-map.json must exist"
    assert paths.team_blueprint.is_file(), "team-blueprint.json must exist"
    assert paths.role_cards_dir.is_dir()
    assert any(paths.role_cards_dir.iterdir()), "role-cards/ must be non-empty"
    assert paths.knowledge_plan.is_file(), "knowledge-plan.json must exist"
    assert paths.context_bundles_dir.is_dir()
    assert any(paths.context_bundles_dir.iterdir()), "context-bundles/ must be non-empty"
    assert paths.manager_brief.is_file(), "manager-brief.md must exist"

    # Configured adversarial review is represented in plan/team/policies:
    # review steps were actually injected into the plan...
    review_step_ids = [s.step_id for s in plan.all_steps if s.step_id.startswith("review-")]
    assert review_step_ids, "adversarial_review=always must inject review steps"
    assert any(sid.endswith("-final") for sid in review_step_ids), (
        "project_completion.adversarial_review=always must inject a final review step"
    )
    # ...every review step got a scope contract + context bundle too
    # (composition order: policy runs before contracts/bundles)...
    for review_step_id in review_step_ids:
        assert paths.scope_contract(review_step_id, ext="md").is_file()
        assert paths.scope_contract(review_step_id, ext="json").is_file()
        assert paths.context_bundle(review_step_id).is_file()
    # ...and the team blueprint carries a role for the configured review
    # agent(s).
    review_role_names = {card.role for card in artifacts.blueprint.roles}
    assert manager_config.policies.review_agents.adversarial_review in review_role_names
    assert manager_config.policies.review_agents.project_review in review_role_names

    # missing_packs includes repo-architecture + testing-strategy (the
    # fixture registry only has coding-conventions).
    missing_names = {p.name for p in artifacts.knowledge_plan.missing_packs}
    assert "repo-architecture" in missing_names
    assert "testing-strategy" in missing_names
    assert "coding-conventions" not in missing_names

    # manager-brief.md is readable with workstreams/team/policies.
    brief_text = paths.manager_brief.read_text(encoding="utf-8")
    assert "## Workstreams" in brief_text
    assert "## Team" in brief_text
    assert "## Configured Policies" in brief_text
    assert "adversarial review" in brief_text.lower()

    # --- No-network guard --------------------------------------------
    assert anthropic_tracker.calls == [], (
        "no live anthropic.Anthropic() client may be constructed during "
        "this hermetic planning run"
    )


def test_non_manager_plan_has_no_pmo_artifacts(fixture_repo: Path) -> None:
    """Control case: the same fixture, same task, WITHOUT manager mode --
    executions/<task_id>/ must contain only plan.* (no PMO filenames)."""
    registry = KnowledgeRegistry()
    registry.load_default_paths()

    planner = _build_planner(fixture_repo, registry)
    plan = planner.create_plan(
        _TASK_SUMMARY,
        complexity="medium",
        project_root=fixture_repo,
        gate_scope="focused",
    )
    assert plan.manager_mode is False  # never touched -- ManagerModePlanner not invoked

    ctx_dir = fixture_repo / ".claude" / "team-context"
    ctx = ContextManager(team_context_dir=ctx_dir, task_id=plan.task_id)
    ctx.write_plan(plan)

    task_dir = ctx_dir / "executions" / plan.task_id
    filenames = {p.name for p in task_dir.iterdir()}
    assert filenames == {"plan.json", "plan.md"}

    pmo_filenames = {
        "project-charter.md",
        "scope-map.json",
        "team-blueprint.json",
        "knowledge-plan.json",
        "manager-brief.md",
    }
    assert not (filenames & pmo_filenames)
    assert not (task_dir / "role-cards").exists()
    assert not (task_dir / "scope-contracts").exists()
    assert not (task_dir / "context-bundles").exists()
