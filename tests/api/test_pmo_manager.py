"""Integration tests for the manager-mode PMO API
(``agent_baton/api/routes/pmo_manager.py``, Phase 7 "Turn PMO into the
director console").

Two harnesses are used:

- ``TestManagerReadEndpoints`` builds a manager-mode plan's full sidecar
  set via the real ``ForgeSession.save_plan`` (the same code path
  exercised by ``POST /pmo/forge/approve`` -- see ``tests/test_pmo_forge.py``
  ``TestSavePlanManagerMode``) and then drives every GET route against it.
- ``TestManagerDecisionResolution`` reuses the diff-derived scope-expansion
  harness from ``tests/engine/test_scope_diff_enforcement.py`` (a real git
  worktree + a real ``ExecutionEngine.record_step_result`` call) so the
  decision this test resolves is genuine, not hand-faked -- and then drives
  the mutation endpoint through the API layer.

Both harnesses are hermetic: a fake bead store (no external ``bd`` binary)
and no headless/live ``claude`` invocation anywhere.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.deps import get_pmo_scanner, get_pmo_store  # noqa: E402
from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.config.manager import ManagerConfig  # noqa: E402
from agent_baton.core.manager.paths import ManagerArtifactPaths  # noqa: E402
from agent_baton.core.pmo.forge import ForgeSession  # noqa: E402
from agent_baton.core.runtime.headless import HeadlessClaude, HeadlessConfig  # noqa: E402
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep  # noqa: E402
from agent_baton.models.pmo import PmoCard, PmoProject  # noqa: E402

# Reuse existing manager-mode test harnesses rather than re-implementing
# git-worktree/engine plumbing (matches this codebase's own precedent --
# tests/engine/test_scope_diff_enforcement.py already imports from
# tests/e2e/test_manager_mode_execution_dry_run.py the same way).
from tests.e2e.test_manager_mode_execution_dry_run import (  # noqa: E402
    _engine_with_fake_beads,
    _routing_plan,
)
from tests.engine.test_scope_diff_enforcement import (  # noqa: E402
    _FakeWorktreeMgr,
    _init_git_repo,
    _worktree_handle_dict,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _manager_plan(task_id: str = "mgr-view-task") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Add a reporting endpoint with tests",
        task_type="feature",
        complexity="medium",
        risk_level="MEDIUM",
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
                        allowed_paths=["app/reporting/**"],
                        step_type="developing",
                    ),
                ],
            ),
        ],
    )


def _forge() -> ForgeSession:
    disabled_headless = HeadlessClaude(HeadlessConfig(claude_path="/nonexistent/claude"))
    return ForgeSession(planner=MagicMock(), store=MagicMock(), headless=disabled_headless)


def _card_for(plan: MachinePlan, project: PmoProject, column: str = "queued") -> PmoCard:
    return PmoCard(
        card_id=plan.task_id,
        project_id=project.project_id,
        program=project.program,
        title=plan.task_summary,
        column=column,
    )


@pytest.fixture()
def app():
    return create_app()


def _client_for_card(app, card: PmoCard, project: PmoProject, plan_dict: dict | None = None):
    mock_scanner = MagicMock()
    mock_scanner.find_card.return_value = (card, plan_dict)
    mock_store = MagicMock()
    mock_store.get_project.return_value = project

    app.dependency_overrides[get_pmo_scanner] = lambda: mock_scanner
    app.dependency_overrides[get_pmo_store] = lambda: mock_store
    return TestClient(app)


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


class TestManagerReadEndpoints:
    @pytest.fixture()
    def setup(self, tmp_path: Path, app):
        project_root = tmp_path / "proj"
        project_root.mkdir()
        project = PmoProject(project_id="proj1", name="Proj", path=str(project_root), program="TEST")

        plan = _manager_plan()
        _forge().save_plan(plan, project)  # publishes the full sidecar set + revision 1

        card = _card_for(plan, project)
        client = _client_for_card(app, card, project, plan_dict=plan.to_dict())
        try:
            yield SimpleNamespace(client=client, plan=plan, project=project, project_root=project_root)
        finally:
            app.dependency_overrides.clear()

    def test_charter_returns_markdown_and_revision(self, setup):
        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/charter")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == setup.plan.task_id
        assert data["revision"] == 1
        assert setup.plan.task_summary in data["markdown"] or "Charter" in data["markdown"]

    def test_scope_map_includes_workstreams(self, setup):
        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/scope-map")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["scope_map"]["workstreams"]) >= 1

    def test_workstreams_links_phase_to_workstream(self, setup):
        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/workstreams")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["links"]) == 1
        assert data["links"][0]["phase_id"] == 1
        assert data["links"][0]["phase_name"] == "Implement"
        assert "workstream" in data["links"][0]

    def test_team_blueprint_has_roles(self, setup):
        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/team-blueprint")
        assert resp.status_code == 200
        data = resp.json()
        assert data["team_blueprint"]["roles"]

    def test_role_cards_list_and_detail(self, setup):
        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/role-cards")
        assert resp.status_code == 200
        cards = resp.json()["role_cards"]
        assert cards
        role = cards[0]["role"]

        detail = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/role-cards/{role}")
        assert detail.status_code == 200
        assert detail.json()["markdown"] == cards[0]["markdown"]

    def test_role_card_missing_role_is_404(self, setup):
        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/role-cards/no-such-role")
        assert resp.status_code == 404

    def test_knowledge_plan(self, setup):
        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/knowledge-plan")
        assert resp.status_code == 200
        assert resp.json()["knowledge_plan"]["task_id"] == setup.plan.task_id

    def test_scope_contracts_list_and_detail(self, setup):
        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/scope-contracts")
        assert resp.status_code == 200
        contracts = resp.json()["contracts"]
        assert any(c["step_id"] == "1.1" for c in contracts)

        detail = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/scope-contracts/1.1")
        assert detail.status_code == 200
        body = detail.json()
        assert body["step_id"] == "1.1"
        assert body["contract"]["step_id"] == "1.1"
        assert body["markdown"]

    def test_scope_contract_missing_step_is_404(self, setup):
        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/scope-contracts/9.9")
        assert resp.status_code == 404

    def test_context_bundles_list_and_detail(self, setup):
        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/context-bundles")
        assert resp.status_code == 200
        bundles = resp.json()["bundles"]
        assert any(b["step_id"] == "1.1" for b in bundles)

        detail = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/context-bundles/1.1")
        assert detail.status_code == 200
        assert detail.json()["bundle"]["step_id"] == "1.1"

    def test_report_returns_brief(self, setup):
        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["manager_brief"]
        assert data["manager_report"] == ""  # no execution/retrospective happened

    def test_version_reports_revision_one(self, setup):
        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/version")
        assert resp.status_code == 200
        data = resp.json()
        assert data["published"] is True
        assert data["revision"] == 1
        assert data["trigger"] == "forge_approve"

    def test_validation_reports_consistent(self, setup):
        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/validation")
        assert resp.status_code == 200
        data = resp.json()
        assert data["published"] is True
        assert data["valid"] is True
        assert data["fingerprint_match"] is True
        assert data["errors"] == []

    def test_validation_detects_stale_sidecars(self, setup):
        """Directly mutate plan.json (bypassing amend_plan/rebuild_and_publish)
        to simulate a drifted plan -- the fingerprint must no longer match."""
        ctx = setup.project_root / ".claude" / "team-context"
        plan_json_path = ctx / "executions" / setup.plan.task_id / "plan.json"
        data = json.loads(plan_json_path.read_text(encoding="utf-8"))
        data["phases"][0]["steps"].append(
            {
                "step_id": "1.2",
                "agent_name": "backend-engineer",
                "task_description": "An out-of-band step never published.",
            }
        )
        plan_json_path.write_text(json.dumps(data), encoding="utf-8")

        resp = setup.client.get(f"/api/v1/pmo/manager/{setup.plan.task_id}/validation")
        assert resp.status_code == 200
        body = resp.json()
        assert body["published"] is True
        assert body["valid"] is False
        assert body["fingerprint_match"] is False
        assert body["errors"]


class TestManagerReadEndpointsErrors:
    def test_unknown_card_is_404(self, app):
        mock_scanner = MagicMock()
        mock_scanner.find_card.side_effect = KeyError("no such card")
        app.dependency_overrides[get_pmo_scanner] = lambda: mock_scanner
        try:
            client = TestClient(app)
            resp = client.get("/api/v1/pmo/manager/no-such-card/charter")
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_non_manager_mode_plan_is_409(self, tmp_path: Path, app):
        project_root = tmp_path / "proj"
        project_root.mkdir()
        project = PmoProject(project_id="proj1", name="Proj", path=str(project_root), program="TEST")

        plan = MachinePlan(
            task_id="plain-task",
            task_summary="Plain plan",
            phases=[
                PlanPhase(
                    phase_id=0,
                    name="Work",
                    steps=[PlanStep(step_id="1.1", agent_name="backend-engineer", task_description="Do work")],
                )
            ],
        )
        assert plan.manager_mode is False
        _forge().save_plan(plan, project)

        card = _card_for(plan, project)
        client = _client_for_card(app, card, project, plan_dict=plan.to_dict())
        try:
            resp = client.get(f"/api/v1/pmo/manager/{plan.task_id}/charter")
            assert resp.status_code == 409
        finally:
            app.dependency_overrides.clear()

    def test_manager_mode_plan_with_no_published_artifacts_is_404(self, tmp_path: Path, app):
        """A manager_mode=True plan whose plan.json was written directly
        (never went through ManagerModePlanner) has no sidecars yet."""
        from agent_baton.core.orchestration.context import ContextManager

        project_root = tmp_path / "proj"
        project_root.mkdir()
        project = PmoProject(project_id="proj1", name="Proj", path=str(project_root), program="TEST")
        plan = _manager_plan(task_id="unpublished-task")

        ctx = ContextManager(
            team_context_dir=project_root / ".claude" / "team-context", task_id=plan.task_id,
        )
        ctx.write_plan(plan)  # plan.json only -- no ManagerModePlanner ever ran

        card = _card_for(plan, project)
        client = _client_for_card(app, card, project, plan_dict=plan.to_dict())
        try:
            resp = client.get(f"/api/v1/pmo/manager/{plan.task_id}/charter")
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Decision resolution
# ---------------------------------------------------------------------------


def _trigger_diff_violation(tmp_path: Path, task_id: str, monkeypatch: pytest.MonkeyPatch):
    """Real diff-derived scope-expansion decision (see
    tests/engine/test_scope_diff_enforcement.py's identically-named
    helper): a step commits a change outside its allowed_paths=["app"]
    contract, which the engine independently detects and durably records
    as a scope_expansion ManagerDecision. Returns (engine, decision_id)."""
    worktree_dir = tmp_path / "wt"
    worktree_dir.mkdir()
    repo, base_sha = _init_git_repo(worktree_dir)
    (worktree_dir / "infra").mkdir()
    (worktree_dir / "infra" / "deploy.yml").write_text("deploy: true\n")
    import subprocess
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "sneak in infra change"], cwd=repo, check=True)

    plan = _routing_plan(task_id)
    plan.phases[0].steps[0].allowed_paths = ["app"]
    ctx_dir = tmp_path / ".claude" / "team-context"

    engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
    engine._worktree_mgr = _FakeWorktreeMgr()
    engine.start(plan)

    state = engine._load_state()
    state.step_worktrees["1.1"] = _worktree_handle_dict(
        path=repo, base_sha=base_sha, task_id=task_id, step_id="1.1"
    )
    engine._save_execution(state)

    engine.record_step_result(
        step_id="1.1", agent_name="backend-engineer", status="complete", outcome="All good.",
    )

    paths = ManagerArtifactPaths(ctx_dir, task_id)
    log_entries = [
        json.loads(line)
        for line in paths.decision_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    decision_id = next(e["decision_id"] for e in log_entries if e["decision_type"] == "scope_expansion")
    return engine, decision_id, ctx_dir


class TestManagerDecisionResolution:
    def _client(self, app, tmp_path: Path, task_id: str, project_path: Path):
        project = PmoProject(project_id="proj1", name="Proj", path=str(project_path), program="TEST")
        card = PmoCard(card_id=task_id, project_id="proj1", program="TEST", title="t", column="running")
        return _client_for_card(app, card, project, plan_dict=None)

    def test_list_and_get_decision(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app):
        task_id = "mgr-resolve-list"
        _, decision_id, _ctx_dir = _trigger_diff_violation(tmp_path, task_id, monkeypatch)
        client = self._client(app, tmp_path, task_id, tmp_path)
        try:
            listing = client.get(f"/api/v1/pmo/manager/{task_id}/decisions")
            assert listing.status_code == 200
            body = listing.json()
            assert body["count"] == 1
            assert body["decisions"][0]["decision_id"] == decision_id
            assert body["decisions"][0]["decision_type"] == "scope_expansion"
            assert body["decisions"][0]["resolved_at"] is None

            detail = client.get(f"/api/v1/pmo/manager/{task_id}/decisions/{decision_id}")
            assert detail.status_code == 200
            assert detail.json()["decision_id"] == decision_id
            assert "infra/deploy.yml" in detail.json()["markdown"]
        finally:
            app.dependency_overrides.clear()

    def test_get_unknown_decision_is_404(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app):
        task_id = "mgr-resolve-unknown"
        _trigger_diff_violation(tmp_path, task_id, monkeypatch)
        client = self._client(app, tmp_path, task_id, tmp_path)
        try:
            resp = client.get(f"/api/v1/pmo/manager/{task_id}/decisions/dec-doesnotexist")
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_approve_widens_scope_and_republishes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app,
    ) -> None:
        task_id = "mgr-resolve-approve"
        engine, decision_id, ctx_dir = _trigger_diff_violation(tmp_path, task_id, monkeypatch)
        client = self._client(app, tmp_path, task_id, tmp_path)
        try:
            resp = client.post(
                f"/api/v1/pmo/manager/{task_id}/decisions/{decision_id}/resolve",
                json={"resolution": "approve"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["applied"] is True
            assert body["resolution"] == "approve"
            assert body["step_id"] == "1.1"
            assert "infra/deploy.yml" in body["new_allowed_paths"]
            assert "app" in body["new_allowed_paths"]

            state = engine._load_state()
            assert state.plan.phases[0].steps[0].allowed_paths == body["new_allowed_paths"]
            step_result = state.get_step_result("1.1")
            assert step_result is None, "widened step's failed result was cleared for re-dispatch"

            # Republished as a manager-mode artifact set (revision >= 1).
            paths = ManagerArtifactPaths(ctx_dir, task_id)
            assert paths.charter.exists()
            assert paths.revision_manifest.exists()
        finally:
            app.dependency_overrides.clear()

    def test_reject_leaves_step_failed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app) -> None:
        task_id = "mgr-resolve-reject"
        engine, decision_id, _ctx_dir = _trigger_diff_violation(tmp_path, task_id, monkeypatch)
        client = self._client(app, tmp_path, task_id, tmp_path)
        try:
            resp = client.post(
                f"/api/v1/pmo/manager/{task_id}/decisions/{decision_id}/resolve",
                json={"resolution": "reject"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["applied"] is True
            assert body["resolution"] == "reject"
            assert body["new_allowed_paths"] == []

            state = engine._load_state()
            step_result = state.get_step_result("1.1")
            assert step_result.status == "failed"
            assert state.plan.phases[0].steps[0].allowed_paths == ["app"]
        finally:
            app.dependency_overrides.clear()

    def test_resolve_already_resolved_is_409(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app,
    ) -> None:
        task_id = "mgr-resolve-twice"
        _, decision_id, _ctx_dir = _trigger_diff_violation(tmp_path, task_id, monkeypatch)
        client = self._client(app, tmp_path, task_id, tmp_path)
        try:
            first = client.post(
                f"/api/v1/pmo/manager/{task_id}/decisions/{decision_id}/resolve",
                json={"resolution": "reject"},
            )
            assert first.status_code == 200

            second = client.post(
                f"/api/v1/pmo/manager/{task_id}/decisions/{decision_id}/resolve",
                json={"resolution": "reject"},
            )
            assert second.status_code == 409
        finally:
            app.dependency_overrides.clear()

    def test_resolve_unknown_decision_is_404(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app,
    ) -> None:
        task_id = "mgr-resolve-404"
        _trigger_diff_violation(tmp_path, task_id, monkeypatch)
        client = self._client(app, tmp_path, task_id, tmp_path)
        try:
            resp = client.post(
                f"/api/v1/pmo/manager/{task_id}/decisions/dec-doesnotexist/resolve",
                json={"resolution": "approve"},
            )
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_resolve_wrong_decision_type_is_400(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app,
    ) -> None:
        task_id = "mgr-resolve-wrong-type"
        engine, _decision_id, ctx_dir = _trigger_diff_violation(tmp_path, task_id, monkeypatch)

        from agent_baton.core.manager.decisions import DecisionPacketBuilder
        from agent_baton.models.manager import ManagerDecision

        paths = ManagerArtifactPaths(ctx_dir, task_id)
        approval_decision = ManagerDecision(
            decision_type="approval",
            task_id=task_id,
            summary="Please approve phase completion.",
            created_at="2026-07-17T00:00:00Z",
        )
        DecisionPacketBuilder(ManagerConfig(), paths).create(approval_decision)

        client = self._client(app, tmp_path, task_id, tmp_path)
        try:
            resp = client.post(
                f"/api/v1/pmo/manager/{task_id}/decisions/{approval_decision.decision_id}/resolve",
                json={"resolution": "approve"},
            )
            assert resp.status_code == 400
        finally:
            app.dependency_overrides.clear()

    def test_resolve_invalid_resolution_is_422(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app,
    ) -> None:
        """Pydantic Literal["approve","reject"] rejects anything else at
        the request-validation layer, before the route body ever runs."""
        task_id = "mgr-resolve-bad-enum"
        _, decision_id, _ctx_dir = _trigger_diff_violation(tmp_path, task_id, monkeypatch)
        client = self._client(app, tmp_path, task_id, tmp_path)
        try:
            resp = client.post(
                f"/api/v1/pmo/manager/{task_id}/decisions/{decision_id}/resolve",
                json={"resolution": "maybe"},
            )
            assert resp.status_code == 422
        finally:
            app.dependency_overrides.clear()
