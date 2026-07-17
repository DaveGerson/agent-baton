"""Tests for :mod:`agent_baton.core.manager.rebuild` (Phase 6, 6.3 --
"Improve planning specificity and prevent context rot").

Covers the cross-artifact reference validator in isolation (hand-built
``ManagerArtifacts``, no planner/registry involved) and the transactional
stage-then-publish flow end-to-end (a real ``ManagerModePlanner.build()``
composition against an empty, hermetic ``KnowledgeRegistry`` -- never the
live ``claude`` binary, never a real network/LLM call).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.manager.artifacts import ManagerArtifacts
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.manager.rebuild import (
    load_revision_manifest,
    rebuild_and_publish,
    validate_manager_artifacts,
)
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.manager import (
    ContextBundle,
    RoleCard,
    ScopeContract,
    ScopeMap,
    TeamBlueprint,
    Workstream,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _plan(task_id: str = "task-rebuild") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Add a reporting endpoint",
        task_type="feature",
        complexity="medium",
        detected_stack="python",
        risk_level="LOW",
        manager_mode=True,
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
        ],
    )


def _no_review_config() -> ManagerConfig:
    """A config with adversarial review turned off, so the composed plan's
    step list is exactly what the test built (no injected review steps to
    reason about)."""
    return ManagerConfig(
        policies={
            "phase_completion": {"adversarial_review": "off"},
            "project_completion": {"adversarial_review": "off"},
        }
    )


def _rebuild(plan, tmp_path, *, trigger="test", config=None):
    return rebuild_and_publish(
        plan,
        plan.task_summary,
        config=config or _no_review_config(),
        project_root=tmp_path,
        team_context_dir=tmp_path / ".claude" / "team-context",
        trigger=trigger,
        knowledge_registry=KnowledgeRegistry(),
    )


# ---------------------------------------------------------------------------
# validate_manager_artifacts
# ---------------------------------------------------------------------------


def _valid_artifacts() -> ManagerArtifacts:
    """A minimal, internally-consistent artifact set for step "1.1"."""
    ws = Workstream(id="ws-1", name="Reporting", owner_role="backend-engineer")
    role = RoleCard(role="backend-engineer", agent_name="backend-engineer")
    return ManagerArtifacts(
        scope_map=ScopeMap(task_id="t", workstreams=[ws]),
        blueprint=TeamBlueprint(
            task_id="t",
            roles=[role],
            workstream_assignments={"ws-1": "backend-engineer"},
        ),
        role_cards_md={"backend-engineer": "# Role Card"},
        scope_contracts={
            "1.1": ScopeContract(step_id="1.1", agent_name="backend-engineer", workstream_id="ws-1"),
        },
        context_bundles={
            "1.1": ContextBundle(task_id="t", step_id="1.1", agent_name="backend-engineer"),
        },
    )


def test_validate_accepts_consistent_artifacts() -> None:
    plan = _plan()
    assert validate_manager_artifacts(plan, _valid_artifacts()) == []


def test_validate_flags_missing_scope_contract() -> None:
    plan = _plan()
    artifacts = _valid_artifacts()
    artifacts.scope_contracts = {}

    errors = validate_manager_artifacts(plan, artifacts)

    assert any("missing a scope contract" in e for e in errors)


def test_validate_flags_missing_context_bundle() -> None:
    plan = _plan()
    artifacts = _valid_artifacts()
    artifacts.context_bundles = {}

    errors = validate_manager_artifacts(plan, artifacts)

    assert any("missing a context bundle" in e for e in errors)


def test_validate_flags_orphan_contract_for_unknown_step() -> None:
    plan = _plan()
    artifacts = _valid_artifacts()
    artifacts.scope_contracts["9.9"] = ScopeContract(step_id="9.9", agent_name="ghost")
    artifacts.context_bundles["9.9"] = ContextBundle(task_id="t", step_id="9.9", agent_name="ghost")

    errors = validate_manager_artifacts(plan, artifacts)

    assert any("unknown steps" in e for e in errors)


def test_validate_flags_contract_referencing_unknown_workstream() -> None:
    plan = _plan()
    artifacts = _valid_artifacts()
    artifacts.scope_contracts["1.1"] = ScopeContract(
        step_id="1.1", agent_name="backend-engineer", workstream_id="ws-does-not-exist",
    )

    errors = validate_manager_artifacts(plan, artifacts)

    assert any("unknown workstream" in e for e in errors)


def test_validate_flags_blueprint_assignment_missing_role_card() -> None:
    plan = _plan()
    artifacts = _valid_artifacts()
    artifacts.blueprint.workstream_assignments["ws-1"] = "some-other-role"

    errors = validate_manager_artifacts(plan, artifacts)

    assert any("no rendered role card" in e for e in errors)


def test_validate_flags_bundle_step_id_mismatch() -> None:
    plan = _plan()
    artifacts = _valid_artifacts()
    artifacts.context_bundles["1.1"] = ContextBundle(
        task_id="t", step_id="wrong-id", agent_name="backend-engineer",
    )

    errors = validate_manager_artifacts(plan, artifacts)

    assert any("mismatched" in e for e in errors)


def test_validate_flags_knowledge_plan_unknown_step() -> None:
    from agent_baton.models.manager import KnowledgePlan

    plan = _plan()
    artifacts = _valid_artifacts()
    artifacts.knowledge_plan = KnowledgePlan(task_id="t", per_step_packs={"9.9": ["pack-a"]})

    errors = validate_manager_artifacts(plan, artifacts)

    assert any("per_step_packs references unknown steps" in e for e in errors)


# ---------------------------------------------------------------------------
# rebuild_and_publish -- success path
# ---------------------------------------------------------------------------


def test_rebuild_publishes_every_sidecar(tmp_path: Path) -> None:
    plan = _plan()

    result = _rebuild(plan, tmp_path)

    assert result.ok is True
    assert result.errors == []
    assert result.revision == 1

    paths = ManagerArtifactPaths(tmp_path / ".claude" / "team-context", plan.task_id)
    assert paths.charter.is_file()
    assert paths.scope_map.is_file()
    assert paths.team_blueprint.is_file()
    assert paths.knowledge_plan.is_file()
    assert paths.scope_contract("1.1", ext="json").is_file()
    assert paths.scope_contract("1.1", ext="md").is_file()
    assert paths.context_bundle("1.1").is_file()
    assert paths.role_card("backend-engineer").is_file()
    assert paths.manager_brief.is_file()
    assert paths.revision_manifest.is_file()

    manifest = load_revision_manifest(paths)
    assert manifest is not None
    assert manifest["revision"] == 1
    assert manifest["trigger"] == "test"
    assert manifest["task_id"] == plan.task_id


def test_rebuild_never_touches_decision_log_or_scope_evidence(tmp_path: Path) -> None:
    """Immutable decision history (Phase 3) must survive a rebuild
    byte-for-byte -- render_all()/write_all() never target these paths,
    but this test pins that invariant directly."""
    plan = _plan()
    paths = ManagerArtifactPaths(tmp_path / ".claude" / "team-context", plan.task_id)
    paths.decision_log.parent.mkdir(parents=True, exist_ok=True)
    paths.decision_log.write_text('{"decision_id": "d1"}\n', encoding="utf-8")
    before = paths.decision_log.read_text(encoding="utf-8")

    result = _rebuild(plan, tmp_path)

    assert result.ok is True
    assert paths.decision_log.read_text(encoding="utf-8") == before


def test_rebuild_revision_increments_across_calls(tmp_path: Path) -> None:
    plan = _plan()

    first = _rebuild(plan, tmp_path, trigger="amend-1")
    second = _rebuild(plan, tmp_path, trigger="amend-2")

    assert first.revision == 1
    assert second.revision == 2

    paths = ManagerArtifactPaths(tmp_path / ".claude" / "team-context", plan.task_id)
    manifest = load_revision_manifest(paths)
    assert manifest["revision"] == 2
    assert manifest["prior_revision"] == 1
    assert manifest["trigger"] == "amend-2"


def test_rebuild_new_step_gets_a_contract_and_bundle(tmp_path: Path) -> None:
    """An amendment that adds a step must show up with its own scope
    contract + context bundle after rebuild -- the actual bug this
    feature exists to fix (amend_plan previously never touched sidecars
    at all)."""
    plan = _plan()
    _rebuild(plan, tmp_path, trigger="initial")

    plan.phases[0].steps.append(
        PlanStep(
            step_id="1.2",
            agent_name="backend-engineer",
            task_description="Add a regression test for the new endpoint.",
            deliverables=["tests/reporting/test_service.py"],
            allowed_paths=["tests/reporting/**"],
            step_type="testing",
        )
    )

    result = _rebuild(plan, tmp_path, trigger="amendment")

    assert result.ok is True
    paths = ManagerArtifactPaths(tmp_path / ".claude" / "team-context", plan.task_id)
    assert paths.scope_contract("1.2", ext="json").is_file()
    assert paths.context_bundle("1.2").is_file()
    assert "1.1" in result.artifacts.scope_contracts
    assert "1.2" in result.artifacts.scope_contracts


# ---------------------------------------------------------------------------
# rebuild_and_publish -- failure path (rollback safety)
# ---------------------------------------------------------------------------


def test_failed_rebuild_leaves_prior_publish_untouched(tmp_path: Path, monkeypatch) -> None:
    plan = _plan()
    first = _rebuild(plan, tmp_path, trigger="initial")
    assert first.ok is True

    paths = ManagerArtifactPaths(tmp_path / ".claude" / "team-context", plan.task_id)
    before_charter = paths.charter.read_text(encoding="utf-8")
    before_bundle = paths.context_bundle("1.1").read_text(encoding="utf-8")
    before_manifest = paths.revision_manifest.read_text(encoding="utf-8")

    import agent_baton.core.manager.rebuild as rebuild_mod

    def _always_fails(_plan, _artifacts):
        return ["forced validation failure for test"]

    monkeypatch.setattr(rebuild_mod, "validate_manager_artifacts", _always_fails)

    second = _rebuild(plan, tmp_path, trigger="broken-amendment")

    assert second.ok is False
    assert second.errors == ["forced validation failure for test"]
    # Nothing on disk moved: same bytes, same revision.
    assert paths.charter.read_text(encoding="utf-8") == before_charter
    assert paths.context_bundle("1.1").read_text(encoding="utf-8") == before_bundle
    assert paths.revision_manifest.read_text(encoding="utf-8") == before_manifest
    manifest = load_revision_manifest(paths)
    assert manifest["revision"] == 1

    # No leftover temp files from the aborted staging pass.
    leftover_tmp = list(paths.root.rglob(".*.rebuild-tmp-*"))
    assert leftover_tmp == []


def test_rebuild_build_exception_returns_failure_without_writing(tmp_path: Path, monkeypatch) -> None:
    plan = _plan()

    import agent_baton.core.manager.planner as planner_mod

    def _boom(self, _plan, _task_summary):
        raise RuntimeError("boom")

    monkeypatch.setattr(planner_mod.ManagerModePlanner, "build", _boom)

    result = _rebuild(plan, tmp_path, trigger="initial")

    assert result.ok is False
    assert any("boom" in e for e in result.errors)
    paths = ManagerArtifactPaths(tmp_path / ".claude" / "team-context", plan.task_id)
    assert not paths.root.exists()
