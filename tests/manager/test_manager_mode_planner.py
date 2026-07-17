"""Tests for :mod:`agent_baton.core.manager.planner` (Wave 3 / Task 11 --
full ``ManagerModePlanner`` composition).

See docs/internal/manager-mode-pmo-plan.md Wave 3 / Task 11 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §16
Milestone 8 (composition prerequisites -- the fixture-repo E2E itself is
``tests/e2e/test_manager_mode_planning.py``).

Test inputs are hand-constructed ``MachinePlan`` objects -- the 7-stage
``IntelligentPlanner`` pipeline is never invoked here (that's the E2E's
job). Every plan below uses the default ``ManagerConfig()`` (adversarial
review "always" for both phase completion and project completion), which
guarantees phase + final review steps are injected -- the composition
order proof depends on this.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.engine.planning.scope_contract import ScopeContractError
from agent_baton.core.manager.artifacts import ManagerArtifacts
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.manager.planner import ManagerModePlanner
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep


def _two_phase_plan(task_id: str = "task-planner-composition") -> MachinePlan:
    """Medium-complexity, two-phase plan -- backend-engineer then test-engineer.

    Mirrors ``tests/manager/test_team_blueprint.py``'s ``_two_workstream_plan``
    so ``ScopeMapBuilder`` produces one workstream per phase, positionally
    aligned.
    """
    return MachinePlan(
        task_id=task_id,
        task_summary="Add a reporting endpoint with tests and docs",
        task_type="feature",
        complexity="medium",
        detected_stack="python",
        risk_level="MEDIUM",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Implement the reporting endpoint.",
                        deliverables=["app/reporting/service.py"],
                        allowed_paths=["app/reporting/**"],
                        step_type="developing",
                    ),
                ],
            ),
            PlanPhase(
                phase_id=2,
                name="Test",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="test-engineer",
                        task_description="Add tests for the reporting endpoint.",
                        deliverables=["tests/reporting/test_service.py"],
                        allowed_paths=["tests/reporting/**"],
                        depends_on=["1.1"],
                        step_type="testing",
                    ),
                ],
            ),
        ],
    )


def _empty_registry() -> KnowledgeRegistry:
    return KnowledgeRegistry()


def _registry_with_review_rubric(tmp_path: Path) -> KnowledgeRegistry:
    """A registry with ``review-rubric`` plus the two default
    ``required_for_code_steps`` packs (``coding-conventions``,
    ``testing-strategy``) -- the latter two exist so
    ``test_review_bundle_integration`` can assert they are attached to
    implementation steps but never leak into review-step bundles (see
    that test's docstring)."""
    pack_dir = tmp_path / "knowledge" / "review-rubric"
    pack_dir.mkdir(parents=True)
    (pack_dir / "knowledge.yaml").write_text(
        "name: review-rubric\n"
        "description: Adversarial review rubric.\n"
        "target_agents: [code-reviewer]\n",
        encoding="utf-8",
    )
    (pack_dir / "rubric.md").write_text(
        "---\nname: rubric\ndescription: Review rubric\n---\n\n# Rubric\n",
        encoding="utf-8",
    )

    coding_dir = tmp_path / "knowledge" / "coding-conventions"
    coding_dir.mkdir(parents=True)
    (coding_dir / "knowledge.yaml").write_text(
        "name: coding-conventions\n"
        "description: Project coding conventions.\n"
        "target_agents: [backend-engineer]\n",
        encoding="utf-8",
    )
    (coding_dir / "conventions.md").write_text(
        "---\nname: conventions\ndescription: Coding conventions\n---\n\n"
        "# Conventions\n",
        encoding="utf-8",
    )

    testing_dir = tmp_path / "knowledge" / "testing-strategy"
    testing_dir.mkdir(parents=True)
    (testing_dir / "knowledge.yaml").write_text(
        "name: testing-strategy\n"
        "description: Project testing strategy.\n"
        "target_agents: [test-engineer]\n",
        encoding="utf-8",
    )
    (testing_dir / "strategy.md").write_text(
        "---\nname: strategy\ndescription: Testing strategy\n---\n\n"
        "# Strategy\n",
        encoding="utf-8",
    )

    registry = KnowledgeRegistry()
    registry.load_directory(tmp_path / "knowledge")
    return registry


def _planner(
    tmp_path: Path,
    *,
    config: ManagerConfig | None = None,
    registry: KnowledgeRegistry | None = None,
) -> ManagerModePlanner:
    return ManagerModePlanner(
        config or ManagerConfig(),
        project_root=tmp_path,
        team_context_dir=tmp_path / ".claude" / "team-context",
        knowledge_registry=registry or _empty_registry(),
    )


# ---------------------------------------------------------------------------
# test_build_produces_all_artifacts
# ---------------------------------------------------------------------------


def test_build_produces_all_artifacts(tmp_path: Path) -> None:
    plan = _two_phase_plan()
    planner = _planner(tmp_path)

    artifacts = planner.build(plan, plan.task_summary)

    assert isinstance(artifacts, ManagerArtifacts)
    assert artifacts.charter is not None
    assert artifacts.scope_map is not None and artifacts.scope_map.workstreams
    assert artifacts.blueprint is not None and artifacts.blueprint.roles
    assert artifacts.role_cards_md
    assert artifacts.knowledge_plan is not None
    assert artifacts.brief_md

    # Default config injects a review after every phase plus a final
    # project review -- contracts + bundles must exist for ALL of them,
    # not just the original implementation steps.
    expected_step_ids = {"1.1", "review-1", "2.1", "review-2", "review-2-final"}
    assert set(artifacts.scope_contracts) == expected_step_ids
    assert set(artifacts.scope_contracts_md) == expected_step_ids
    assert set(artifacts.context_bundles) == expected_step_ids

    # No fallback/synthetic role cards were needed.
    assert artifacts.warnings == []


def test_review_steps_use_review_role_card_not_phase_owner(tmp_path: Path) -> None:
    """Binding rule: a review step's scope contract/bundle must be built
    with the *review* role's card, not the phase's implementation owner's
    card -- even though the review step lives inside the same phase."""
    plan = _two_phase_plan()
    config = ManagerConfig()
    planner = _planner(tmp_path, config=config)

    artifacts = planner.build(plan, plan.task_summary)

    phase_review_agent = config.policies.review_agents.adversarial_review
    project_review_agent = config.policies.review_agents.project_review
    expected_agent = {
        "review-1": phase_review_agent,
        "review-2": phase_review_agent,
        "review-2-final": project_review_agent,
    }
    for review_step_id, review_agent in expected_agent.items():
        contract = artifacts.scope_contracts[review_step_id]
        assert contract.agent_name == review_agent
        bundle = artifacts.context_bundles[review_step_id]
        assert bundle.agent_name == review_agent
        assert any(p.name == "review-rubric" for p in bundle.knowledge_packs)

    # The implementation steps still resolve to their own workstream owner.
    assert artifacts.scope_contracts["1.1"].agent_name == "backend-engineer"
    assert artifacts.scope_contracts["2.1"].agent_name == "test-engineer"


# ---------------------------------------------------------------------------
# test_composition_order
# ---------------------------------------------------------------------------


def test_composition_order(tmp_path: Path) -> None:
    """Review steps present in scope_contracts keys proves PhasePolicyApplier
    ran BEFORE scope contracts/bundles were built (Task 4/Task 11 binding
    composition order)."""
    plan = _two_phase_plan()
    planner = _planner(tmp_path)

    # Before composition, the plan has no review steps at all.
    assert all(not s.step_id.startswith("review-") for s in plan.all_steps)

    artifacts = planner.build(plan, plan.task_summary)

    # After composition, the plan itself was mutated in place (the only
    # mutation PhasePolicyApplier performs) AND every injected review step
    # has a scope contract + context bundle.
    review_ids_in_plan = {s.step_id for s in plan.all_steps if s.step_id.startswith("review-")}
    assert review_ids_in_plan == {"review-1", "review-2", "review-2-final"}
    assert review_ids_in_plan <= set(artifacts.scope_contracts)
    assert review_ids_in_plan <= set(artifacts.context_bundles)


# ---------------------------------------------------------------------------
# test_review_bundle_integration
# ---------------------------------------------------------------------------


def test_review_bundle_integration(tmp_path: Path) -> None:
    """Deferred PRD M6 case: a review step's context bundle includes the
    latest phase handoff ref (when a prior phase exists) and the
    review-rubric pack when the registry has it.

    Also pins the absence half of review-bundle gating (Wave 3 review Fix
    2): ``knowledge_packs.required_for_code_steps`` (``coding-conventions``,
    ``testing-strategy``) must never leak into a review step's bundle even
    though the registry has both packs available -- they are attached only
    to implementation steps (``step_type in {"developing", "testing"}``),
    never to review steps (``step_type="reviewing"``, whose knowledge comes
    exclusively from the review role card's ``required_knowledge_packs``,
    i.e. ``review-rubric``). See ``KnowledgePlanBuilder``'s
    ``_IMPLEMENTATION_STEP_TYPES`` gate and
    ``TeamBlueprintBuilder._build_review_role_card``.
    """
    plan = _two_phase_plan()
    config = ManagerConfig()
    registry = _registry_with_review_rubric(tmp_path)
    planner = _planner(tmp_path, config=config, registry=registry)

    artifacts = planner.build(plan, plan.task_summary)

    required_for_code_steps = set(config.knowledge_packs.required_for_code_steps)

    # Absence half: review-step bundles never carry a
    # required_for_code_steps pack name, even though the registry has both
    # available (added to _registry_with_review_rubric above).
    for review_step_id in ("review-1", "review-2", "review-2-final"):
        bundle = artifacts.context_bundles[review_step_id]
        bundle_pack_names = {p.name for p in bundle.knowledge_packs}
        assert bundle_pack_names.isdisjoint(required_for_code_steps), (
            f"{review_step_id} bundle unexpectedly carries a "
            f"required_for_code_steps pack: {bundle_pack_names & required_for_code_steps}"
        )

    # Presence half: the implementation steps DO carry them.
    for impl_step_id in ("1.1", "2.1"):
        bundle = artifacts.context_bundles[impl_step_id]
        bundle_pack_names = {p.name for p in bundle.knowledge_packs}
        assert required_for_code_steps <= bundle_pack_names, (
            f"{impl_step_id} bundle missing required_for_code_steps packs: "
            f"{required_for_code_steps - bundle_pack_names}"
        )

    paths = ManagerArtifactPaths(tmp_path / ".claude" / "team-context", plan.task_id)

    # review-2 and review-2-final live in phase 2 -- phase 1's handoff is
    # the "latest phase handoff" available to them.
    for review_step_id in ("review-2", "review-2-final"):
        bundle = artifacts.context_bundles[review_step_id]
        assert str(paths.phase_handoff(1)) in bundle.prior_handoffs

    # review-1 (phase 1's own review) has no earlier phase.
    review_1_bundle = artifacts.context_bundles["review-1"]
    assert review_1_bundle.prior_handoffs == []

    # review-rubric is present with real registry metadata (non-placeholder).
    for review_step_id in ("review-1", "review-2", "review-2-final"):
        bundle = artifacts.context_bundles[review_step_id]
        rubric_refs = [p for p in bundle.knowledge_packs if p.name == "review-rubric"]
        assert rubric_refs, f"{review_step_id} missing review-rubric pack ref"
        assert rubric_refs[0].path  # registry-backed, not a bare placeholder


# ---------------------------------------------------------------------------
# test_dry_run_writes_nothing / test_save_writes_all_sidecars
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    plan = _two_phase_plan()
    team_context_dir = tmp_path / ".claude" / "team-context"
    planner = _planner(tmp_path)

    artifacts = planner.build(plan, plan.task_summary)

    assert artifacts.charter is not None  # composition ran fully in-memory
    assert not team_context_dir.exists()


def test_save_writes_all_sidecars(tmp_path: Path) -> None:
    plan = _two_phase_plan()
    team_context_dir = tmp_path / ".claude" / "team-context"
    planner = _planner(tmp_path)

    artifacts = planner.build_and_write(plan, plan.task_summary)

    paths = ManagerArtifactPaths(team_context_dir, plan.task_id)

    assert paths.charter.is_file()
    assert paths.scope_map.is_file()
    assert paths.team_blueprint.is_file()
    assert paths.knowledge_plan.is_file()
    assert paths.manager_brief.is_file()

    for role in artifacts.role_cards_md:
        assert paths.role_card(role).is_file()

    expected_step_ids = {"1.1", "review-1", "2.1", "review-2", "review-2-final"}
    for step_id in expected_step_ids:
        assert paths.scope_contract(step_id, ext="json").is_file()
        assert paths.scope_contract(step_id, ext="md").is_file()
        assert paths.context_bundle(step_id).is_file()

    # JSON sidecars round-trip and match the in-memory artifacts.
    scope_map_on_disk = json.loads(paths.scope_map.read_text(encoding="utf-8"))
    assert scope_map_on_disk["task_id"] == plan.task_id

    # Real files back the token estimates now (no "missing file" noise for
    # a step's own contract/role-card references).
    bundle = artifacts.context_bundles["1.1"]
    contract_ref = next(r for r in bundle.must_read if r.reason == "scope contract")
    assert contract_ref.token_estimate > 0
    assert not any("Missing file for token estimate" in w for w in bundle.truncation_warnings)


def _ambiguous_scope_plan(task_id: str = "task-ambiguous-scope") -> MachinePlan:
    """A single write-capable step with zero derivable path evidence."""
    return MachinePlan(
        task_id=task_id,
        task_summary="do the thing",
        task_type="feature",
        complexity="medium",
        risk_level="LOW",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Do the thing.",
                        deliverables=["the thing"],
                        step_type="developing",
                    ),
                ],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Phase 3 "Make scope contracts authoritative" -- manager-planner-level
# scope-contract validation/diagnostics.
# ---------------------------------------------------------------------------


def test_ambiguous_write_scope_recorded_as_warning_by_default(tmp_path: Path) -> None:
    """Default (strict_scope=False, unchanged for existing callers): a
    write-capable step with no derivable allowed_paths does not fail
    composition, but is recorded on artifacts.warnings."""
    plan = _ambiguous_scope_plan()
    planner = _planner(tmp_path)

    artifacts = planner.build(plan, plan.task_summary)

    assert artifacts.scope_contracts["1.1"].allowed_paths == []
    assert any("write_scope_missing" in w for w in artifacts.warnings)


def test_strict_scope_raises_for_ambiguous_write_scope(tmp_path: Path) -> None:
    """strict_scope=True turns the same ambiguous scope into a planning
    error raised before any sidecar is written."""
    plan = _ambiguous_scope_plan()
    planner = ManagerModePlanner(
        ManagerConfig(),
        project_root=tmp_path,
        team_context_dir=tmp_path / ".claude" / "team-context",
        knowledge_registry=_empty_registry(),
        strict_scope=True,
    )

    with pytest.raises(ScopeContractError, match="write_scope_missing"):
        planner.build(plan, plan.task_summary)


def test_review_step_contract_never_inherits_workstream_write_paths(tmp_path: Path) -> None:
    """Binding rule: an injected review step (step_type='reviewing', no
    explicit allowed_paths of its own) must be represented with an empty
    allowed_paths -- never the phase's write-capable workstream paths
    that ScopeContractBuilder's naive `step.allowed_paths or
    workstream.allowed_paths` fallback would otherwise hand it."""
    plan = _two_phase_plan()
    planner = _planner(tmp_path)

    artifacts = planner.build(plan, plan.task_summary)

    for review_step_id in ("review-1", "review-2", "review-2-final"):
        contract = artifacts.scope_contracts[review_step_id]
        assert contract.allowed_paths == [], (
            f"{review_step_id} must not inherit write-capable workstream paths"
        )

    # The implementation steps keep their own explicit write scope.
    assert artifacts.scope_contracts["1.1"].allowed_paths == ["app/reporting/**"]
    assert artifacts.scope_contracts["2.1"].allowed_paths == ["tests/reporting/**"]


def test_dry_run_and_save_produce_equivalent_artifacts(tmp_path: Path) -> None:
    """Sanity check: build() and build_and_write() run the identical
    composition -- only the disk side effects (and resulting token
    accounting accuracy) differ."""
    plan_a = _two_phase_plan("task-dry")
    plan_b = _two_phase_plan("task-save")

    dry_artifacts = _planner(tmp_path).build(plan_a, plan_a.task_summary)
    saved_artifacts = _planner(tmp_path).build_and_write(plan_b, plan_b.task_summary)

    assert set(dry_artifacts.scope_contracts) == set(saved_artifacts.scope_contracts)
    assert set(dry_artifacts.context_bundles) == set(saved_artifacts.context_bundles)
    assert set(dry_artifacts.role_cards_md) == set(saved_artifacts.role_cards_md)
