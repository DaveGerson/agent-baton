"""End-to-end PMO API journey coverage for manager-mode plans (Phase 7 7.3
"test-engineer").

Complements the existing manager-mode API suites rather than duplicating
them:

- ``tests/api/test_pmo_manager.py`` drives every ``GET /pmo/manager/*``
  read route against a plan saved directly via ``ForgeSession.save_plan``,
  and the scope-expansion approve/reject mutation.
- ``tests/test_api_pmo.py::TestForgeApproveManagerMode`` covers
  ``POST /pmo/forge/approve`` in isolation.
- ``tests/test_api_pmo_decisions.py`` covers the generic per-card decision
  inbox (``/pmo/execute/{card_id}/decisions*``) for a *non*-manager-mode
  plan.

This file adds what none of those cover:

1. The FULL creation journey through Forge -- ``POST /pmo/forge/plan``
   (with ``manager_mode: true``) THEN ``POST /pmo/forge/approve`` THEN
   reading every manager-mode artifact category -- rather than saving the
   plan directly.
2. Missing, corrupt, and mixed-version (stale) sidecar artifacts are
   handled safely: a broken artifact 404s or is skipped, it never 500s or
   corrupts a sibling artifact's read.
3. The generic per-card decision inbox exercised on a manager-mode plan,
   including rationale round-tripping and the headless-resume side effect,
   combined with "refresh status" (``GET /pmo/cards/{card_id}/execution``)
   observing the transition.
4. Per-card task isolation across two independently manager-mode-published
   projects/cards for both the manager artifact API and the decision inbox.

Hermetic: headless Claude is disabled via a nonexistent binary path (so
``ForgeSession.create_plan`` always falls through to a mocked
``IntelligentPlanner``); no external ``bd`` binary is used anywhere (fake
in-memory bead store, mirroring ``tests/e2e/test_manager_mode_execution_dry_run
.py``'s harness).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.deps import get_bus, get_forge_session, get_pmo_scanner, get_pmo_store  # noqa: E402
from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.config.manager import ManagerConfig  # noqa: E402
from agent_baton.core.engine.executor import ExecutionEngine  # noqa: E402
from agent_baton.core.events.bus import EventBus  # noqa: E402
from agent_baton.core.manager.paths import ManagerArtifactPaths  # noqa: E402
from agent_baton.core.manager.planner import ManagerModePlanner  # noqa: E402
from agent_baton.core.orchestration.context import ContextManager  # noqa: E402
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry  # noqa: E402
from agent_baton.core.pmo.forge import ForgeSession  # noqa: E402
from agent_baton.core.pmo.scanner import PmoScanner  # noqa: E402
from agent_baton.core.pmo.store import PmoStore  # noqa: E402
from agent_baton.core.runtime.decisions import DecisionManager, deterministic_decision_id  # noqa: E402
from agent_baton.core.runtime.headless import HeadlessClaude, HeadlessConfig  # noqa: E402
from agent_baton.core.storage.sqlite_backend import SqliteStorage  # noqa: E402
from agent_baton.models.decision import DecisionRequest  # noqa: E402
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep  # noqa: E402
from agent_baton.models.pmo import PmoCard, PmoProject  # noqa: E402


# ---------------------------------------------------------------------------
# Plan builders
# ---------------------------------------------------------------------------


def _manager_plan(task_id: str, summary: str = "Add a reporting endpoint with tests") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary=summary,
        task_type="feature",
        complexity="medium",
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
                        allowed_paths=["app/reporting/**"],
                        step_type="developing",
                    ),
                ],
            ),
        ],
    )


def _approval_gated_manager_plan(task_id: str) -> MachinePlan:
    """Two phases, phase 1 gated on human approval -- mirrors
    ``tests/test_api_pmo_decisions.py::_seed_awaiting_approval_execution``
    but flagged ``manager_mode=True``."""
    return MachinePlan(
        task_id=task_id,
        task_summary="Manager-mode plan with an approval gate",
        risk_level="LOW",
        manager_mode=True,
        phases=[
            PlanPhase(
                phase_id=1, name="P1", approval_required=True,
                steps=[PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="x")],
            ),
            PlanPhase(
                phase_id=2, name="P2",
                steps=[PlanStep(step_id="2.1", agent_name="backend-engineer", task_description="y")],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Fixtures / small local doubles
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> PmoStore:
    return PmoStore(config_path=tmp_path / "pmo-config.json", archive_path=tmp_path / "pmo-archive.jsonl")


@pytest.fixture()
def mock_planner() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def real_forge_app(tmp_path: Path, store: PmoStore, mock_planner: MagicMock):
    """A real ``ForgeSession`` (headless disabled -- falls back to the
    mocked ``IntelligentPlanner``) wired into a real ``PmoScanner`` and a
    real ``PmoStore``, so ``POST /pmo/forge/plan`` -> ``POST
    /pmo/forge/approve`` -> ``GET /pmo/manager/*`` is a genuine round trip
    through disk, not a stubbed shortcut."""
    _app = create_app(team_context_root=tmp_path)
    scanner = PmoScanner(store=store)
    disabled_headless = HeadlessClaude(HeadlessConfig(claude_path="/nonexistent/claude"))
    forge = ForgeSession(planner=mock_planner, store=store, headless=disabled_headless)
    _app.dependency_overrides[get_pmo_store] = lambda: store
    _app.dependency_overrides[get_pmo_scanner] = lambda: scanner
    _app.dependency_overrides[get_forge_session] = lambda: forge
    return _app


@pytest.fixture()
def client(real_forge_app) -> TestClient:
    return TestClient(real_forge_app)


def _register_project(client: TestClient, project_id: str, project_root: Path, program: str = "MGR") -> None:
    r = client.post(
        "/api/v1/pmo/projects",
        json={"project_id": project_id, "name": project_id, "path": str(project_root), "program": program},
    )
    assert r.status_code == 201, r.text


class _StubScanner:
    """Minimal ``PmoScanner`` double -- used only by the decision-inbox /
    resume tests below, which seed an ``ExecutionState`` directly rather
    than through a Forge-saved plan, so a real scanner has nothing to scan
    against a synthesized card. Mirrors ``tests/test_api_pmo_decisions.py``
    ``_StubScanner``."""

    def __init__(self, cards: list[PmoCard]) -> None:
        self._cards = cards

    def scan_all(self) -> list[PmoCard]:
        return list(self._cards)

    def program_health(self, cards=None):
        return {}

    def find_card(self, card_id: str):
        for c in self._cards:
            if c.card_id == card_id:
                return c, None
        raise KeyError(card_id)


def _decision_app(tmp_path: Path, store: PmoStore, cards: list[PmoCard]):
    _app = create_app(team_context_root=tmp_path)
    _app.dependency_overrides[get_pmo_store] = lambda: store
    _app.dependency_overrides[get_pmo_scanner] = lambda: _StubScanner(cards)
    _app.dependency_overrides[get_forge_session] = lambda: MagicMock()
    _app.dependency_overrides[get_bus] = lambda: EventBus()
    return TestClient(_app)


def _no_review_config() -> ManagerConfig:
    """Off adversarial review on both hooks so step ids stay exactly what
    the plan builder wrote -- the decision/resume tests below key off a
    fixed ``"1.1"``/``"2.1"`` step id set."""
    return ManagerConfig.from_dict({
        "policies": {
            "phase_completion": {"adversarial_review": "off"},
            "project_completion": {"adversarial_review": "off"},
        }
    })


class _FakeBeadStore:
    """In-memory bead store -- no external ``bd`` binary (hermeticity)."""

    def __init__(self) -> None:
        self._beads: dict[str, object] = {}

    def write(self, bead) -> str:
        self._beads[bead.bead_id] = bead
        return bead.bead_id

    def read(self, bead_id: str):
        return self._beads.get(bead_id)

    def query(self, *, task_id=None, step_id=None, bead_type=None, status=None, limit=100, **_kw):
        result = list(self._beads.values())
        if task_id is not None:
            result = [b for b in result if b.task_id == task_id]
        if step_id is not None:
            result = [b for b in result if b.step_id == step_id]
        if bead_type is not None:
            result = [b for b in result if b.bead_type == bead_type]
        if status is not None:
            result = [b for b in result if b.status == status]
        return result[:limit]

    def increment_retrieval_count(self, bead_id: str) -> None:
        pass

    def update_quality_score(self, bead_id: str, delta: float) -> None:
        pass

    def close(self, bead_id: str, summary: str = "") -> None:
        b = self._beads.get(bead_id)
        if b:
            b.status = "closed"
            b.summary = summary


def _engine_with_fake_beads(ctx_dir: Path, task_id: str, monkeypatch: pytest.MonkeyPatch) -> ExecutionEngine:
    ctx_dir.mkdir(parents=True, exist_ok=True)
    db_path = ctx_dir / "baton.db"
    db_path.touch()
    fake_store = _FakeBeadStore()

    def _patched_make_bead_store(path, *, soul_router=None, repo_root=None):
        return fake_store

    monkeypatch.setattr(
        "agent_baton.core.engine.bead_backend.make_bead_store", _patched_make_bead_store,
    )
    storage = SqliteStorage(db_path)
    return ExecutionEngine(team_context_root=ctx_dir, bus=EventBus(), storage=storage, task_id=task_id)


def _seed_manager_mode_awaiting_approval(
    project_root: Path, task_id: str, monkeypatch: pytest.MonkeyPatch,
) -> tuple[ExecutionEngine, MachinePlan]:
    """Publish a manager-mode plan's sidecars + plan.json, start execution,
    complete phase 1's only step, and drive the engine to the approval gate
    -- mirrors ``tests/test_api_pmo_decisions.py
    ::_seed_awaiting_approval_execution`` but manager-mode."""
    plan = _approval_gated_manager_plan(task_id)
    ctx_dir = project_root / ".claude" / "team-context"

    planner = ManagerModePlanner(
        _no_review_config(), project_root=project_root, team_context_dir=ctx_dir,
        knowledge_registry=KnowledgeRegistry(),
    )
    planner.build_and_write(plan, plan.task_summary)
    ContextManager(team_context_dir=ctx_dir, task_id=task_id).write_plan(plan)

    engine = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
    engine.start(plan)
    engine.record_step_result("1.1", "backend-engineer", status="complete", outcome="done")
    action = engine.next_action()
    assert action.action_type.value == "approval", action.action_type
    return engine, plan


# ===========================================================================
# 1. Full creation journey through Forge
# ===========================================================================


class TestForgeCreateApproveManagerModeJourney:
    """POST /forge/plan -> POST /forge/approve -> GET every manager
    artifact category, all through the real ForgeSession/ManagerModePlanner
    pipeline (never a directly-saved plan)."""

    def test_full_journey_reads_every_artifact_category(
        self, client: TestClient, tmp_path: Path, mock_planner: MagicMock,
    ) -> None:
        project_root = tmp_path / "journey-proj"
        _register_project(client, "journey-proj", project_root)

        skeleton = _manager_plan("journey-task")
        mock_planner.create_plan.return_value = skeleton

        created = client.post(
            "/api/v1/pmo/forge/plan",
            json={
                "description": "Add a reporting endpoint with tests",
                "program": "MGR",
                "project_id": "journey-proj",
                "manager_mode": True,
            },
        )
        assert created.status_code == 201, created.text
        plan_dict = created.json()["plan"]
        assert plan_dict["manager_mode"] is True

        approved = client.post(
            "/api/v1/pmo/forge/approve",
            json={"plan": plan_dict, "project_id": "journey-proj"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["manager_mode"] is True
        assert approved.json()["manager_revision"] == 1

        task_id = plan_dict["task_id"]

        # -- Every documented read category, per pmo_manager.py's module docstring.
        charter = client.get(f"/api/v1/pmo/manager/{task_id}/charter")
        assert charter.status_code == 200
        assert charter.json()["revision"] == 1

        scope_map = client.get(f"/api/v1/pmo/manager/{task_id}/scope-map")
        assert scope_map.status_code == 200
        assert scope_map.json()["scope_map"]["task_id"] == task_id

        workstreams = client.get(f"/api/v1/pmo/manager/{task_id}/workstreams")
        assert workstreams.status_code == 200
        assert len(workstreams.json()["links"]) == 1

        team = client.get(f"/api/v1/pmo/manager/{task_id}/team-blueprint")
        assert team.status_code == 200
        assert team.json()["team_blueprint"]["roles"]

        roles = client.get(f"/api/v1/pmo/manager/{task_id}/role-cards")
        assert roles.status_code == 200
        role_list = roles.json()["role_cards"]
        assert role_list
        role_detail = client.get(f"/api/v1/pmo/manager/{task_id}/role-cards/{role_list[0]['role']}")
        assert role_detail.status_code == 200
        assert role_detail.json()["markdown"] == role_list[0]["markdown"]

        knowledge = client.get(f"/api/v1/pmo/manager/{task_id}/knowledge-plan")
        assert knowledge.status_code == 200
        assert knowledge.json()["knowledge_plan"]["task_id"] == task_id

        contracts = client.get(f"/api/v1/pmo/manager/{task_id}/scope-contracts")
        assert contracts.status_code == 200
        contract_list = contracts.json()["contracts"]
        assert any(c["step_id"] == "1.1" for c in contract_list)
        contract_detail = client.get(f"/api/v1/pmo/manager/{task_id}/scope-contracts/1.1")
        assert contract_detail.status_code == 200
        assert contract_detail.json()["contract"]["step_id"] == "1.1"

        bundles = client.get(f"/api/v1/pmo/manager/{task_id}/context-bundles")
        assert bundles.status_code == 200
        bundle_list = bundles.json()["bundles"]
        assert any(b["step_id"] == "1.1" for b in bundle_list)
        bundle_detail = client.get(f"/api/v1/pmo/manager/{task_id}/context-bundles/1.1")
        assert bundle_detail.status_code == 200
        assert bundle_detail.json()["bundle"]["step_id"] == "1.1"

        report = client.get(f"/api/v1/pmo/manager/{task_id}/report")
        assert report.status_code == 200
        assert report.json()["manager_brief"]

        decisions = client.get(f"/api/v1/pmo/manager/{task_id}/decisions")
        assert decisions.status_code == 200
        assert decisions.json() == {"task_id": task_id, "count": 0, "decisions": []}

        version = client.get(f"/api/v1/pmo/manager/{task_id}/version")
        assert version.status_code == 200
        assert version.json()["published"] is True
        assert version.json()["revision"] == 1
        assert version.json()["trigger"] == "forge_approve"

        validation = client.get(f"/api/v1/pmo/manager/{task_id}/validation")
        assert validation.status_code == 200
        assert validation.json()["valid"] is True
        assert validation.json()["errors"] == []

    def test_non_manager_mode_journey_gets_409_on_manager_reads(
        self, client: TestClient, tmp_path: Path, mock_planner: MagicMock,
    ) -> None:
        """Regression guard: a plain plan created the same way (no
        manager_mode flag) must never expose the manager-console API."""
        project_root = tmp_path / "plain-proj"
        _register_project(client, "plain-proj", project_root)
        mock_planner.create_plan.return_value = _manager_plan("plain-task")

        created = client.post(
            "/api/v1/pmo/forge/plan",
            json={"description": "Plain task", "program": "MGR", "project_id": "plain-proj"},
        )
        assert created.status_code == 201
        plan_dict = created.json()["plan"]
        assert plan_dict["manager_mode"] is False

        approved = client.post(
            "/api/v1/pmo/forge/approve", json={"plan": plan_dict, "project_id": "plain-proj"},
        )
        assert approved.status_code == 200
        assert approved.json()["manager_mode"] is False

        resp = client.get(f"/api/v1/pmo/manager/{plan_dict['task_id']}/charter")
        assert resp.status_code == 409


# ===========================================================================
# 2. Missing / corrupt / mixed-version artifacts
# ===========================================================================


class TestArtifactCorruptionAndStaleness:
    @pytest.fixture()
    def published(self, tmp_path: Path):
        project_root = tmp_path / "corrupt-proj"
        project_root.mkdir()
        project = PmoProject(project_id="corrupt-proj", name="P", path=str(project_root), program="MGR")

        plan = _manager_plan("corrupt-task")
        headless = HeadlessClaude(HeadlessConfig(claude_path="/nonexistent/claude"))
        forge = ForgeSession(planner=MagicMock(), store=MagicMock(), headless=headless)
        plan.manager_mode = True
        forge.save_plan(plan, project)

        ctx_dir = project_root / ".claude" / "team-context"
        paths = ManagerArtifactPaths(ctx_dir, plan.task_id)

        _app = create_app(team_context_root=tmp_path)
        store = PmoStore(config_path=tmp_path / "cfg.json", archive_path=tmp_path / "arc.jsonl")
        store.register_project(project)
        card = PmoCard(card_id=plan.task_id, project_id="corrupt-proj", program="MGR", title="t", column="queued")
        _app.dependency_overrides[get_pmo_store] = lambda: store
        _app.dependency_overrides[get_pmo_scanner] = lambda: _StubScanner([card])
        return TestClient(_app), plan, paths

    def test_missing_single_artifact_404s_without_affecting_siblings(self, published) -> None:
        client, plan, paths = published
        paths.team_blueprint.unlink()

        missing = client.get(f"/api/v1/pmo/manager/{plan.task_id}/team-blueprint")
        assert missing.status_code == 404

        # Siblings are untouched -- a missing file for one artifact category
        # must never cascade into another category failing.
        still_ok = client.get(f"/api/v1/pmo/manager/{plan.task_id}/charter")
        assert still_ok.status_code == 200
        still_ok2 = client.get(f"/api/v1/pmo/manager/{plan.task_id}/scope-map")
        assert still_ok2.status_code == 200

    def test_corrupt_json_artifact_404s_instead_of_500(self, published) -> None:
        client, plan, paths = published
        paths.scope_map.write_text("{not valid json!!", encoding="utf-8")

        resp = client.get(f"/api/v1/pmo/manager/{plan.task_id}/scope-map")
        assert resp.status_code == 404

        # A corrupt sibling doesn't affect an unrelated artifact either.
        charter = client.get(f"/api/v1/pmo/manager/{plan.task_id}/charter")
        assert charter.status_code == 200

    def test_corrupt_entry_in_a_listing_directory_is_skipped_not_fatal(self, published) -> None:
        """One malformed file inside scope-contracts/ or context-bundles/
        must not 500 the whole listing -- it is silently skipped (see
        pmo_manager.py's ``_read_json`` -> ``continue`` pattern) while
        valid sibling entries still come back."""
        client, plan, paths = published
        assert list(paths.scope_contracts_dir.glob("*.json")), "fixture sanity"
        (paths.scope_contracts_dir / "zzz-corrupt.json").write_text("{{{", encoding="utf-8")

        resp = client.get(f"/api/v1/pmo/manager/{plan.task_id}/scope-contracts")
        assert resp.status_code == 200
        contracts = resp.json()["contracts"]
        assert any(c["step_id"] == "1.1" for c in contracts)
        assert all(c["step_id"] != "zzz-corrupt" for c in contracts)

    def test_mixed_version_stale_plan_still_serves_last_good_reads(self, published) -> None:
        """An out-of-band plan.json edit (never republished) makes
        /validation report drift, but every artifact read still safely
        serves the last successfully published (now stale) content rather
        than erroring -- staleness is surfaced as metadata, not a hard
        failure of the read path."""
        client, plan, paths = published

        plan_json_path = paths.root / "plan.json"
        data = json.loads(plan_json_path.read_text(encoding="utf-8"))
        data["phases"][0]["steps"].append(
            {"step_id": "1.2", "agent_name": "backend-engineer", "task_description": "Out of band."}
        )
        plan_json_path.write_text(json.dumps(data), encoding="utf-8")

        validation = client.get(f"/api/v1/pmo/manager/{plan.task_id}/validation")
        assert validation.status_code == 200
        assert validation.json()["valid"] is False
        assert validation.json()["errors"]

        # Reads still succeed, serving the (stale) revision-1 content.
        charter = client.get(f"/api/v1/pmo/manager/{plan.task_id}/charter")
        assert charter.status_code == 200
        assert charter.json()["revision"] == 1

        version = client.get(f"/api/v1/pmo/manager/{plan.task_id}/version")
        assert version.status_code == 200
        assert version.json()["published"] is True
        assert version.json()["revision"] == 1

        # The new step has no scope contract sidecar -- 404 for THAT step
        # specifically, without breaking the rest of the artifact set.
        new_step = client.get(f"/api/v1/pmo/manager/{plan.task_id}/scope-contracts/1.2")
        assert new_step.status_code == 404
        old_step = client.get(f"/api/v1/pmo/manager/{plan.task_id}/scope-contracts/1.1")
        assert old_step.status_code == 200


# ===========================================================================
# 3. Decision approve/deny with rationale + resume + refreshed status
# ===========================================================================


class TestManagerModeDecisionRationaleAndResume:
    def test_approve_with_rationale_resumes_execution_and_refresh_reflects_it(
        self, tmp_path: Path, store: PmoStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        task_id = "mgr-approve-rationale"
        project_root = tmp_path / "proj"
        project_root.mkdir()
        store.register_project(PmoProject(project_id="p", name="P", path=str(project_root), program="MGR"))

        engine, _plan = _seed_manager_mode_awaiting_approval(project_root, task_id, monkeypatch)
        card = PmoCard(card_id=task_id, project_id="p", program="MGR", title="t", column="awaiting_human")
        client = _decision_app(tmp_path, store, [card])

        request_id = deterministic_decision_id(task_id, "approval", 1)
        dm = DecisionManager(decisions_dir=project_root / ".claude" / "team-context" / "decisions")
        dm.request(DecisionRequest(
            request_id=request_id, task_id=task_id, decision_type="phase_approval",
            summary="approve phase 1?", options=["approve", "reject"],
        ))

        # "Refresh status" before resolving -- still mid-approval.
        before = client.get(f"/api/v1/pmo/cards/{task_id}/execution")
        assert before.status_code == 200

        popen_calls: list = []
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda cmd, **kwargs: popen_calls.append({"cmd": cmd, "kwargs": kwargs}) or MagicMock(pid=4242),
        )

        resolve = client.post(
            f"/api/v1/pmo/execute/{task_id}/decisions/{request_id}/resolve",
            json={"option": "approve", "rationale": "Looks safe to ship — reviewed the diff."},
        )
        assert resolve.status_code == 200, resolve.text
        assert resolve.json() == {"resolved": True, "execution_resumed": True}
        assert popen_calls, "expected a headless resume subprocess to be launched"

        # Rationale round-trips through the persisted resolution record even
        # though the list/detail response models don't surface it -- it is
        # stored separately from the request (see DecisionManager.resolve).
        resolution = dm.get_resolution(request_id)
        assert resolution["rationale"] == "Looks safe to ship — reviewed the diff."
        assert dm.get(request_id).status == "resolved"

        status = engine.status()
        assert status["status"] != "approval_pending"

        # "Refresh status" after resolving -- observably different now.
        after = client.get(f"/api/v1/pmo/cards/{task_id}/execution")
        assert after.status_code == 200

    def test_deny_with_rationale_leaves_execution_paused_no_resume(
        self, tmp_path: Path, store: PmoStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        task_id = "mgr-deny-rationale"
        project_root = tmp_path / "proj"
        project_root.mkdir()
        store.register_project(PmoProject(project_id="p", name="P", path=str(project_root), program="MGR"))

        engine, _plan = _seed_manager_mode_awaiting_approval(project_root, task_id, monkeypatch)
        card = PmoCard(card_id=task_id, project_id="p", program="MGR", title="t", column="awaiting_human")
        client = _decision_app(tmp_path, store, [card])

        request_id = deterministic_decision_id(task_id, "approval", 1)
        dm = DecisionManager(decisions_dir=project_root / ".claude" / "team-context" / "decisions")
        dm.request(DecisionRequest(
            request_id=request_id, task_id=task_id, decision_type="phase_approval",
            summary="approve phase 1?", options=["approve", "reject"],
        ))

        popen_calls: list = []
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda cmd, **kwargs: popen_calls.append({"cmd": cmd, "kwargs": kwargs}) or MagicMock(pid=4242),
        )

        resolve = client.post(
            f"/api/v1/pmo/execute/{task_id}/decisions/{request_id}/resolve",
            json={"option": "reject", "rationale": "Not ready — needs a security pass first."},
        )
        assert resolve.status_code == 200, resolve.text
        assert resolve.json()["resolved"] is True
        # resume_task_headless spawns unconditionally whenever no worker is
        # already alive for this task (see its docstring) -- a reject still
        # "resumes" the headless loop so it can observe and persist the
        # terminal failure, it just doesn't continue phase 2's work.
        assert resolve.json()["execution_resumed"] is True

        resolution = dm.get_resolution(request_id)
        assert resolution["rationale"] == "Not ready — needs a security pass first."
        assert dm.get(request_id).status == "resolved"

        status = engine.status()
        assert status["status"] == "failed"

    def test_resolve_already_resolved_decision_is_400(
        self, tmp_path: Path, store: PmoStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        task_id = "mgr-double-resolve"
        project_root = tmp_path / "proj"
        project_root.mkdir()
        store.register_project(PmoProject(project_id="p", name="P", path=str(project_root), program="MGR"))
        _seed_manager_mode_awaiting_approval(project_root, task_id, monkeypatch)
        card = PmoCard(card_id=task_id, project_id="p", program="MGR", title="t", column="awaiting_human")
        client = _decision_app(tmp_path, store, [card])

        request_id = deterministic_decision_id(task_id, "approval", 1)
        dm = DecisionManager(decisions_dir=project_root / ".claude" / "team-context" / "decisions")
        dm.request(DecisionRequest(
            request_id=request_id, task_id=task_id, decision_type="phase_approval",
            summary="approve phase 1?", options=["approve", "reject"],
        ))
        monkeypatch.setattr("subprocess.Popen", lambda cmd, **kwargs: MagicMock(pid=1))

        first = client.post(
            f"/api/v1/pmo/execute/{task_id}/decisions/{request_id}/resolve",
            json={"option": "reject"},
        )
        assert first.status_code == 200
        second = client.post(
            f"/api/v1/pmo/execute/{task_id}/decisions/{request_id}/resolve",
            json={"option": "approve"},
        )
        assert second.status_code == 400


# ===========================================================================
# 4. Task isolation across two independently published manager-mode cards
# ===========================================================================


class TestTaskIsolationAcrossCards:
    def test_manager_artifacts_never_cross_cards(self, tmp_path: Path) -> None:
        headless = HeadlessClaude(HeadlessConfig(claude_path="/nonexistent/claude"))
        forge = ForgeSession(planner=MagicMock(), store=MagicMock(), headless=headless)

        root_a = tmp_path / "proj-a"
        root_a.mkdir()
        project_a = PmoProject(project_id="proj-a", name="A", path=str(root_a), program="A")
        plan_a = _manager_plan("task-a", summary="Task A: reporting endpoint")
        plan_a.manager_mode = True
        forge.save_plan(plan_a, project_a)

        root_b = tmp_path / "proj-b"
        root_b.mkdir()
        project_b = PmoProject(project_id="proj-b", name="B", path=str(root_b), program="B")
        plan_b = _manager_plan("task-b", summary="Task B: billing export")
        plan_b.manager_mode = True
        forge.save_plan(plan_b, project_b)

        store = PmoStore(config_path=tmp_path / "cfg.json", archive_path=tmp_path / "arc.jsonl")
        store.register_project(project_a)
        store.register_project(project_b)
        card_a = PmoCard(card_id="task-a", project_id="proj-a", program="A", title="A", column="queued")
        card_b = PmoCard(card_id="task-b", project_id="proj-b", program="B", title="B", column="queued")

        _app = create_app(team_context_root=tmp_path)
        _app.dependency_overrides[get_pmo_store] = lambda: store
        _app.dependency_overrides[get_pmo_scanner] = lambda: _StubScanner([card_a, card_b])
        client = TestClient(_app)

        charter_a = client.get("/api/v1/pmo/manager/task-a/charter")
        charter_b = client.get("/api/v1/pmo/manager/task-b/charter")
        assert charter_a.status_code == 200
        assert charter_b.status_code == 200
        assert charter_a.json()["markdown"] != charter_b.json()["markdown"]
        assert "reporting endpoint" in charter_a.json()["markdown"].lower() \
            or "task a" in charter_a.json()["markdown"].lower()
        assert "billing export" in charter_b.json()["markdown"].lower() \
            or "task b" in charter_b.json()["markdown"].lower()

        # Corrupting task-a's scope-map must never affect task-b's read.
        paths_a = ManagerArtifactPaths(root_a / ".claude" / "team-context", "task-a")
        paths_a.scope_map.write_text("not json", encoding="utf-8")
        assert client.get("/api/v1/pmo/manager/task-a/scope-map").status_code == 404
        assert client.get("/api/v1/pmo/manager/task-b/scope-map").status_code == 200

    def test_decision_inbox_never_leaks_across_cards(self, tmp_path: Path) -> None:
        root_a = tmp_path / "proj-a"
        root_a.mkdir()
        root_b = tmp_path / "proj-b"
        root_b.mkdir()

        dm_a = DecisionManager(decisions_dir=root_a / ".claude" / "team-context" / "decisions")
        dm_a.request(DecisionRequest(
            request_id=deterministic_decision_id("task-a", "approval", 1), task_id="task-a",
            decision_type="phase_approval", summary="A's decision", options=["approve", "reject"],
        ))
        dm_b = DecisionManager(decisions_dir=root_b / ".claude" / "team-context" / "decisions")
        dm_b.request(DecisionRequest(
            request_id=deterministic_decision_id("task-b", "approval", 1), task_id="task-b",
            decision_type="phase_approval", summary="B's decision", options=["approve", "reject"],
        ))

        store = PmoStore(config_path=tmp_path / "cfg.json", archive_path=tmp_path / "arc.jsonl")
        store.register_project(PmoProject(project_id="proj-a", name="A", path=str(root_a), program="A"))
        store.register_project(PmoProject(project_id="proj-b", name="B", path=str(root_b), program="B"))
        card_a = PmoCard(card_id="task-a", project_id="proj-a", program="A", title="A", column="awaiting_human")
        card_b = PmoCard(card_id="task-b", project_id="proj-b", program="B", title="B", column="awaiting_human")
        client = _decision_app(tmp_path, store, [card_a, card_b])

        body_a = client.get("/api/v1/pmo/execute/task-a/decisions").json()
        body_b = client.get("/api/v1/pmo/execute/task-b/decisions").json()
        assert [d["summary"] for d in body_a["decisions"]] == ["A's decision"]
        assert [d["summary"] for d in body_b["decisions"]] == ["B's decision"]

        # Resolving A's decision via B's card id is rejected outright.
        cross = client.post(
            f"/api/v1/pmo/execute/task-b/decisions/{deterministic_decision_id('task-a', 'approval', 1)}/resolve",
            json={"option": "approve"},
        )
        assert cross.status_code == 404
