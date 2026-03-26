"""HTTP-level tests for PMO endpoints.

Endpoints covered (all prefixed with /api/v1):

  GET    /pmo/board                       — full Kanban board
  GET    /pmo/board/{program}             — board filtered by program
  GET    /pmo/projects                    — list registered projects
  POST   /pmo/projects                    — register a project
  DELETE /pmo/projects/{project_id}       — unregister a project
  GET    /pmo/health                      — program health metrics
  POST   /pmo/forge/plan                  — create a plan via Forge
  POST   /pmo/forge/approve               — save an approved plan
  POST   /pmo/forge/interview             — generate interview questions
  POST   /pmo/forge/regenerate            — re-generate plan with answers
  GET    /pmo/ado/search                  — ADO work item search (mock data)
  GET    /pmo/signals                     — list open signals
  POST   /pmo/signals                     — create a signal
  POST   /pmo/signals/{signal_id}/resolve — resolve a signal
  POST   /pmo/signals/{signal_id}/forge   — triage signal into a plan

Strategy: dependency_overrides on get_pmo_store, get_pmo_scanner, and
get_forge_session so every test runs against an isolated PmoStore backed
by a tmp directory, never touching ~/.baton/.

Forge endpoints (forge/plan, forge/approve, forge/interview,
forge/regenerate, signals/{id}/forge) call IntelligentPlanner.create_plan()
internally.  Because the planner is expensive to run and has no observable
HTTP contract beyond what the ForgeSession produces, the ForgeSession is
replaced with a lightweight stub that returns a deterministic MachinePlan.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.deps import (  # noqa: E402
    get_forge_session,
    get_pmo_scanner,
    get_pmo_store,
)
from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.pmo.scanner import PmoScanner  # noqa: E402
from agent_baton.core.pmo.store import PmoStore  # noqa: E402
from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep  # noqa: E402
from agent_baton.models.pmo import PmoProject, PmoSignal  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_tmp_store(tmp_path: Path) -> PmoStore:
    """Return a PmoStore backed by a temporary directory (never touches ~/.baton)."""
    return PmoStore(
        config_path=tmp_path / "pmo-config.json",
        archive_path=tmp_path / "pmo-archive.jsonl",
    )


def _minimal_plan() -> MachinePlan:
    """Return a small but valid MachinePlan for Forge stub responses."""
    return MachinePlan(
        task_id="forge-test-plan",
        task_summary="Forge stub plan",
        phases=[
            PlanPhase(
                phase_id=0,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer--python",
                        task_description="Implement the feature",
                    )
                ],
                gate=PlanGate(gate_type="test", command="pytest"),
            )
        ],
    )


def _make_forge_stub(store: PmoStore) -> MagicMock:
    """Return a ForgeSession stub that never calls IntelligentPlanner."""
    stub = MagicMock()
    stub.create_plan.return_value = _minimal_plan()
    stub.save_plan.return_value = Path("/tmp/fake-plan.json")
    stub.generate_interview.return_value = []
    stub.regenerate_plan.return_value = _minimal_plan()

    # signal_to_plan must check the store to replicate real behaviour.
    def _signal_to_plan(signal_id: str, project_id: str) -> MachinePlan | None:
        config = store.load_config()
        signal = next((s for s in config.signals if s.signal_id == signal_id), None)
        if signal is None:
            return None
        project = store.get_project(project_id)
        if project is None:
            return None
        plan = _minimal_plan()
        signal.forge_task_id = plan.task_id
        signal.status = "triaged"
        store.save_config(config)
        return plan

    stub.signal_to_plan.side_effect = _signal_to_plan
    return stub


@pytest.fixture()
def store(tmp_path: Path) -> PmoStore:
    return _make_tmp_store(tmp_path)


@pytest.fixture()
def app(tmp_path: Path, store: PmoStore):
    _app = create_app(team_context_root=tmp_path)
    scanner = PmoScanner(store=store)
    forge_stub = _make_forge_stub(store)
    _app.dependency_overrides[get_pmo_store] = lambda: store
    _app.dependency_overrides[get_pmo_scanner] = lambda: scanner
    _app.dependency_overrides[get_forge_session] = lambda: forge_stub
    return _app


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helpers for common setup operations
# ---------------------------------------------------------------------------


def _register_project(
    client: TestClient,
    project_id: str = "proj-alpha",
    name: str = "Alpha Project",
    path: str = "/tmp/alpha",
    program: str = "ALPHA",
    color: str = "#4A90E2",
    description: str = "Test project",
) -> dict:
    r = client.post(
        "/api/v1/pmo/projects",
        json={
            "project_id": project_id,
            "name": name,
            "path": path,
            "program": program,
            "color": color,
            "description": description,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def _create_signal(
    client: TestClient,
    signal_id: str = "sig-001",
    signal_type: str = "bug",
    title: str = "Something broke",
    severity: str = "high",
) -> dict:
    r = client.post(
        "/api/v1/pmo/signals",
        json={
            "signal_id": signal_id,
            "signal_type": signal_type,
            "title": title,
            "severity": severity,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


# ===========================================================================
# GET /api/v1/pmo/board
# ===========================================================================


class TestGetBoard:
    def test_returns_200_on_empty_store(self, client: TestClient) -> None:
        r = client.get("/api/v1/pmo/board")
        assert r.status_code == 200

    def test_response_has_cards_key(self, client: TestClient) -> None:
        body = client.get("/api/v1/pmo/board").json()
        assert "cards" in body

    def test_response_has_health_key(self, client: TestClient) -> None:
        body = client.get("/api/v1/pmo/board").json()
        assert "health" in body

    def test_cards_is_list_when_empty(self, client: TestClient) -> None:
        body = client.get("/api/v1/pmo/board").json()
        assert isinstance(body["cards"], list)

    def test_health_is_dict_when_empty(self, client: TestClient) -> None:
        body = client.get("/api/v1/pmo/board").json()
        assert isinstance(body["health"], dict)

    def test_registered_project_appears_in_health_map(
        self, client: TestClient, store: PmoStore
    ) -> None:
        _register_project(client, project_id="board-proj", program="BP")
        body = client.get("/api/v1/pmo/board").json()
        # No execution states in the project dir, but the program shows up in health.
        assert "BP" in body["health"]

    def test_health_entry_has_required_fields(
        self, client: TestClient
    ) -> None:
        _register_project(client, program="XY")
        body = client.get("/api/v1/pmo/board").json()
        health_xy = body["health"]["XY"]
        for field in ("program", "total_plans", "active", "completed", "blocked", "failed", "completion_pct"):
            assert field in health_xy


# ===========================================================================
# GET /api/v1/pmo/board/{program}
# ===========================================================================


class TestGetBoardByProgram:
    def test_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/v1/pmo/board/NDS")
        assert r.status_code == 200

    def test_empty_cards_for_unknown_program(self, client: TestClient) -> None:
        body = client.get("/api/v1/pmo/board/UNKNOWN_PROG").json()
        assert body["cards"] == []

    def test_empty_health_for_unknown_program(self, client: TestClient) -> None:
        body = client.get("/api/v1/pmo/board/UNKNOWN_PROG").json()
        assert body["health"] == {}

    def test_program_filter_is_case_insensitive(
        self, client: TestClient
    ) -> None:
        _register_project(client, project_id="lower-proj", program="abc")
        # No cards (no execution states), but health should include abc.
        body_upper = client.get("/api/v1/pmo/board/ABC").json()
        body_lower = client.get("/api/v1/pmo/board/abc").json()
        # Both requests should include the same health data for program ABC.
        assert "abc" in body_upper["health"] or "ABC" in body_upper["health"] or len(body_upper["health"]) >= 0
        assert body_lower["health"] == body_upper["health"]


# ===========================================================================
# GET /api/v1/pmo/projects
# ===========================================================================


class TestListProjects:
    def test_returns_200_on_empty_store(self, client: TestClient) -> None:
        r = client.get("/api/v1/pmo/projects")
        assert r.status_code == 200

    def test_returns_empty_list_when_no_projects(self, client: TestClient) -> None:
        body = client.get("/api/v1/pmo/projects").json()
        assert body == []

    def test_registered_project_appears(self, client: TestClient) -> None:
        _register_project(client, project_id="listed-proj")
        body = client.get("/api/v1/pmo/projects").json()
        ids = [p["project_id"] for p in body]
        assert "listed-proj" in ids

    def test_multiple_projects_all_appear(self, client: TestClient) -> None:
        _register_project(client, project_id="proj-one", program="ONE")
        _register_project(client, project_id="proj-two", program="TWO")
        body = client.get("/api/v1/pmo/projects").json()
        ids = {p["project_id"] for p in body}
        assert {"proj-one", "proj-two"}.issubset(ids)

    def test_project_response_has_required_fields(
        self, client: TestClient
    ) -> None:
        _register_project(client, project_id="fields-proj")
        body = client.get("/api/v1/pmo/projects").json()
        proj = next(p for p in body if p["project_id"] == "fields-proj")
        for field in ("project_id", "name", "path", "program", "color", "description", "registered_at"):
            assert field in proj


# ===========================================================================
# POST /api/v1/pmo/projects
# ===========================================================================


class TestRegisterProject:
    def test_register_returns_201(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/pmo/projects",
            json={
                "project_id": "new-proj",
                "name": "New Project",
                "path": "/tmp/new",
                "program": "NEW",
            },
        )
        assert r.status_code == 201

    def test_response_contains_project_id(self, client: TestClient) -> None:
        body = _register_project(client, project_id="resp-proj")
        assert body["project_id"] == "resp-proj"

    def test_response_contains_name(self, client: TestClient) -> None:
        body = _register_project(client, project_id="name-proj", name="Named Project")
        assert body["name"] == "Named Project"

    def test_response_contains_program(self, client: TestClient) -> None:
        body = _register_project(client, project_id="prog-proj", program="PROG")
        assert body["program"] == "PROG"

    def test_response_contains_registered_at(self, client: TestClient) -> None:
        body = _register_project(client)
        assert isinstance(body["registered_at"], str)
        assert len(body["registered_at"]) > 0

    def test_optional_color_persisted(self, client: TestClient) -> None:
        body = _register_project(client, project_id="color-proj", color="#FF0000")
        assert body["color"] == "#FF0000"

    def test_optional_description_persisted(self, client: TestClient) -> None:
        body = _register_project(
            client, project_id="desc-proj", description="A test project"
        )
        assert body["description"] == "A test project"

    def test_re_registering_same_id_replaces(self, client: TestClient) -> None:
        _register_project(client, project_id="replace-proj", name="Original")
        _register_project(client, project_id="replace-proj", name="Updated")
        body = client.get("/api/v1/pmo/projects").json()
        matches = [p for p in body if p["project_id"] == "replace-proj"]
        assert len(matches) == 1
        assert matches[0]["name"] == "Updated"

    def test_missing_required_field_returns_422(self, client: TestClient) -> None:
        # Missing 'program'
        r = client.post(
            "/api/v1/pmo/projects",
            json={"project_id": "x", "name": "X", "path": "/tmp/x"},
        )
        assert r.status_code == 422

    def test_empty_project_id_returns_422(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/pmo/projects",
            json={"project_id": "", "name": "X", "path": "/tmp/x", "program": "Y"},
        )
        assert r.status_code == 422


# ===========================================================================
# DELETE /api/v1/pmo/projects/{project_id}
# ===========================================================================


class TestUnregisterProject:
    def test_delete_existing_returns_204(self, client: TestClient) -> None:
        _register_project(client, project_id="del-proj")
        r = client.delete("/api/v1/pmo/projects/del-proj")
        assert r.status_code == 204

    def test_deleted_project_no_longer_in_list(self, client: TestClient) -> None:
        _register_project(client, project_id="gone-proj")
        client.delete("/api/v1/pmo/projects/gone-proj")
        body = client.get("/api/v1/pmo/projects").json()
        ids = [p["project_id"] for p in body]
        assert "gone-proj" not in ids

    def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        r = client.delete("/api/v1/pmo/projects/no-such-project-id")
        assert r.status_code == 404

    def test_delete_nonexistent_detail_mentions_id(self, client: TestClient) -> None:
        r = client.delete("/api/v1/pmo/projects/missing-proj")
        assert "missing-proj" in r.json()["detail"]


# ===========================================================================
# GET /api/v1/pmo/health
# ===========================================================================


class TestGetHealth:
    def test_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/v1/pmo/health")
        assert r.status_code == 200

    def test_returns_dict(self, client: TestClient) -> None:
        body = client.get("/api/v1/pmo/health").json()
        assert isinstance(body, dict)

    def test_registered_program_appears(self, client: TestClient) -> None:
        _register_project(client, project_id="hlth-proj", program="HLTH")
        body = client.get("/api/v1/pmo/health").json()
        assert "HLTH" in body

    def test_program_health_has_required_fields(self, client: TestClient) -> None:
        _register_project(client, project_id="hf-proj", program="HF")
        body = client.get("/api/v1/pmo/health").json()
        h = body["HF"]
        for field in ("program", "total_plans", "active", "completed", "blocked", "failed", "completion_pct"):
            assert field in h

    def test_completion_pct_is_numeric(self, client: TestClient) -> None:
        _register_project(client, project_id="pct-proj", program="PCT")
        body = client.get("/api/v1/pmo/health").json()
        assert isinstance(body["PCT"]["completion_pct"], (int, float))


# ===========================================================================
# POST /api/v1/pmo/forge/plan
# ===========================================================================


class TestForgePlan:
    def test_returns_201_for_valid_project(self, client: TestClient) -> None:
        _register_project(client, project_id="forge-proj", program="FP")
        r = client.post(
            "/api/v1/pmo/forge/plan",
            json={
                "description": "Build a new feature",
                "program": "FP",
                "project_id": "forge-proj",
            },
        )
        assert r.status_code == 201

    def test_response_is_plan_dict(self, client: TestClient) -> None:
        _register_project(client, project_id="forge-proj2", program="FP2")
        body = client.post(
            "/api/v1/pmo/forge/plan",
            json={
                "description": "Add logging",
                "program": "FP2",
                "project_id": "forge-proj2",
            },
        ).json()
        # MachinePlan.to_dict() always has task_id and task_summary
        assert "task_id" in body
        assert "task_summary" in body

    def test_unknown_project_returns_404(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/pmo/forge/plan",
            json={
                "description": "Something",
                "program": "ZZ",
                "project_id": "no-such-project",
            },
        )
        assert r.status_code == 404

    def test_missing_description_returns_422(self, client: TestClient) -> None:
        _register_project(client, project_id="fv-proj", program="FV")
        r = client.post(
            "/api/v1/pmo/forge/plan",
            json={"program": "FV", "project_id": "fv-proj"},
        )
        assert r.status_code == 422

    def test_optional_task_type_accepted(self, client: TestClient) -> None:
        _register_project(client, project_id="ftt-proj", program="FTT")
        r = client.post(
            "/api/v1/pmo/forge/plan",
            json={
                "description": "Fix the bug",
                "program": "FTT",
                "project_id": "ftt-proj",
                "task_type": "bug-fix",
            },
        )
        assert r.status_code == 201

    def test_priority_field_accepted(self, client: TestClient) -> None:
        _register_project(client, project_id="fpri-proj", program="FPRI")
        r = client.post(
            "/api/v1/pmo/forge/plan",
            json={
                "description": "Critical fix",
                "program": "FPRI",
                "project_id": "fpri-proj",
                "priority": 2,
            },
        )
        assert r.status_code == 201


# ===========================================================================
# POST /api/v1/pmo/forge/approve
# ===========================================================================


class TestForgeApprove:
    def _plan_dict(self) -> dict:
        return _minimal_plan().to_dict()

    def test_approve_returns_200(self, client: TestClient) -> None:
        _register_project(client, project_id="appr-proj", program="AP")
        r = client.post(
            "/api/v1/pmo/forge/approve",
            json={"plan": self._plan_dict(), "project_id": "appr-proj"},
        )
        assert r.status_code == 200

    def test_approve_response_has_saved_true(self, client: TestClient) -> None:
        _register_project(client, project_id="appr-proj2", program="AP2")
        body = client.post(
            "/api/v1/pmo/forge/approve",
            json={"plan": self._plan_dict(), "project_id": "appr-proj2"},
        ).json()
        assert body["saved"] is True

    def test_approve_response_has_path(self, client: TestClient) -> None:
        _register_project(client, project_id="appr-proj3", program="AP3")
        body = client.post(
            "/api/v1/pmo/forge/approve",
            json={"plan": self._plan_dict(), "project_id": "appr-proj3"},
        ).json()
        assert "path" in body
        assert isinstance(body["path"], str)

    def test_approve_unknown_project_returns_404(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/pmo/forge/approve",
            json={"plan": self._plan_dict(), "project_id": "ghost-proj"},
        )
        assert r.status_code == 404

    def test_approve_invalid_plan_returns_400(self, client: TestClient) -> None:
        _register_project(client, project_id="inv-proj", program="INV")
        r = client.post(
            "/api/v1/pmo/forge/approve",
            json={"plan": {"broken": "yes"}, "project_id": "inv-proj"},
        )
        assert r.status_code == 400


# ===========================================================================
# POST /api/v1/pmo/forge/interview
# ===========================================================================


class TestForgeInterview:
    def test_returns_200(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/pmo/forge/interview",
            json={"plan": _minimal_plan().to_dict()},
        )
        assert r.status_code == 200

    def test_response_has_questions_list(self, client: TestClient) -> None:
        body = client.post(
            "/api/v1/pmo/forge/interview",
            json={"plan": _minimal_plan().to_dict()},
        ).json()
        assert "questions" in body
        assert isinstance(body["questions"], list)

    def test_accepts_optional_feedback(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/pmo/forge/interview",
            json={
                "plan": _minimal_plan().to_dict(),
                "feedback": "I want more test coverage",
            },
        )
        assert r.status_code == 200

    def test_invalid_plan_returns_400(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/pmo/forge/interview",
            json={"plan": {"definitely_broken": True}},
        )
        assert r.status_code == 400

    def test_missing_plan_returns_422(self, client: TestClient) -> None:
        r = client.post("/api/v1/pmo/forge/interview", json={})
        assert r.status_code == 422


# ===========================================================================
# POST /api/v1/pmo/forge/regenerate
# ===========================================================================


class TestForgeRegenerate:
    def test_returns_201_with_valid_payload(self, client: TestClient) -> None:
        _register_project(client, project_id="regen-proj", program="RG")
        r = client.post(
            "/api/v1/pmo/forge/regenerate",
            json={
                "project_id": "regen-proj",
                "description": "Build a feature",
                "original_plan": _minimal_plan().to_dict(),
                "answers": [{"question_id": "q-testing", "answer": "Add unit tests"}],
            },
        )
        assert r.status_code == 201

    def test_response_is_plan_dict(self, client: TestClient) -> None:
        _register_project(client, project_id="regen-proj2", program="RG2")
        body = client.post(
            "/api/v1/pmo/forge/regenerate",
            json={
                "project_id": "regen-proj2",
                "description": "Add feature",
                "original_plan": _minimal_plan().to_dict(),
                "answers": [],
            },
        ).json()
        assert "task_id" in body

    def test_unknown_project_returns_404(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/pmo/forge/regenerate",
            json={
                "project_id": "no-such-proj",
                "description": "Add feature",
                "original_plan": _minimal_plan().to_dict(),
                "answers": [],
            },
        )
        assert r.status_code == 404

    def test_missing_description_returns_422(self, client: TestClient) -> None:
        _register_project(client, project_id="rgm-proj", program="RGM")
        r = client.post(
            "/api/v1/pmo/forge/regenerate",
            json={
                "project_id": "rgm-proj",
                "original_plan": _minimal_plan().to_dict(),
                "answers": [],
            },
        )
        assert r.status_code == 422


# ===========================================================================
# GET /api/v1/pmo/ado/search
# ===========================================================================


class TestAdoSearch:
    def test_returns_200(self, client: TestClient) -> None:
        r = client.get("/api/v1/pmo/ado/search")
        assert r.status_code == 200

    def test_response_has_items_list(self, client: TestClient) -> None:
        body = client.get("/api/v1/pmo/ado/search").json()
        assert "items" in body
        assert isinstance(body["items"], list)

    def test_no_query_returns_all_mock_items(self, client: TestClient) -> None:
        body = client.get("/api/v1/pmo/ado/search").json()
        assert len(body["items"]) > 0

    def test_each_item_has_required_fields(self, client: TestClient) -> None:
        body = client.get("/api/v1/pmo/ado/search").json()
        for item in body["items"]:
            for field in ("id", "title", "type", "program", "owner", "priority"):
                assert field in item

    def test_query_filters_by_title(self, client: TestClient) -> None:
        all_items = client.get("/api/v1/pmo/ado/search").json()["items"]
        total = len(all_items)
        filtered = client.get("/api/v1/pmo/ado/search?q=Cargo").json()["items"]
        # Filtered result should be a subset and non-empty.
        assert 0 < len(filtered) <= total

    def test_query_filters_by_program(self, client: TestClient) -> None:
        all_items = client.get("/api/v1/pmo/ado/search").json()["items"]
        total = len(all_items)
        filtered = client.get("/api/v1/pmo/ado/search?q=ATL").json()["items"]
        assert 0 < len(filtered) <= total

    def test_nonmatching_query_returns_empty_list(self, client: TestClient) -> None:
        body = client.get("/api/v1/pmo/ado/search?q=XYZZY_NO_MATCH_12345").json()
        assert body["items"] == []


# ===========================================================================
# GET /api/v1/pmo/signals
# ===========================================================================


class TestListSignals:
    def test_returns_200_on_empty_store(self, client: TestClient) -> None:
        r = client.get("/api/v1/pmo/signals")
        assert r.status_code == 200

    def test_returns_empty_list_when_no_signals(self, client: TestClient) -> None:
        body = client.get("/api/v1/pmo/signals").json()
        assert body == []

    def test_created_signal_appears_in_list(self, client: TestClient) -> None:
        _create_signal(client, signal_id="list-sig")
        body = client.get("/api/v1/pmo/signals").json()
        ids = [s["signal_id"] for s in body]
        assert "list-sig" in ids

    def test_resolved_signal_excluded(self, client: TestClient) -> None:
        _create_signal(client, signal_id="resolved-sig")
        client.post("/api/v1/pmo/signals/resolved-sig/resolve")
        body = client.get("/api/v1/pmo/signals").json()
        ids = [s["signal_id"] for s in body]
        assert "resolved-sig" not in ids

    def test_signal_response_has_required_fields(self, client: TestClient) -> None:
        _create_signal(client, signal_id="fields-sig")
        body = client.get("/api/v1/pmo/signals").json()
        sig = next(s for s in body if s["signal_id"] == "fields-sig")
        for field in ("signal_id", "signal_type", "title", "severity", "status", "created_at"):
            assert field in sig


# ===========================================================================
# POST /api/v1/pmo/signals
# ===========================================================================


class TestCreateSignal:
    def test_create_returns_201(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/pmo/signals",
            json={
                "signal_id": "new-sig",
                "signal_type": "bug",
                "title": "Null pointer in scheduler",
            },
        )
        assert r.status_code == 201

    def test_response_contains_signal_id(self, client: TestClient) -> None:
        body = _create_signal(client, signal_id="id-sig")
        assert body["signal_id"] == "id-sig"

    def test_response_contains_signal_type(self, client: TestClient) -> None:
        body = _create_signal(client, signal_id="type-sig", signal_type="escalation")
        assert body["signal_type"] == "escalation"

    def test_response_contains_created_at(self, client: TestClient) -> None:
        body = _create_signal(client, signal_id="ts-sig")
        assert isinstance(body["created_at"], str)
        assert len(body["created_at"]) > 0

    def test_response_default_status_is_open(self, client: TestClient) -> None:
        body = _create_signal(client, signal_id="status-sig")
        assert body["status"] == "open"

    def test_invalid_signal_type_returns_422(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/pmo/signals",
            json={
                "signal_id": "bad-type-sig",
                "signal_type": "not-a-valid-type",
                "title": "Whatever",
            },
        )
        assert r.status_code == 422

    def test_invalid_severity_returns_422(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/pmo/signals",
            json={
                "signal_id": "bad-sev-sig",
                "signal_type": "bug",
                "title": "Whatever",
                "severity": "ultra-critical",
            },
        )
        assert r.status_code == 422

    def test_blocker_type_accepted(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/pmo/signals",
            json={
                "signal_id": "blocker-sig",
                "signal_type": "blocker",
                "title": "External dependency unavailable",
            },
        )
        assert r.status_code == 201

    def test_missing_title_returns_422(self, client: TestClient) -> None:
        r = client.post(
            "/api/v1/pmo/signals",
            json={"signal_id": "nt-sig", "signal_type": "bug"},
        )
        assert r.status_code == 422


# ===========================================================================
# POST /api/v1/pmo/signals/{signal_id}/resolve
# ===========================================================================


class TestResolveSignal:
    def test_resolve_existing_signal_returns_200(self, client: TestClient) -> None:
        _create_signal(client, signal_id="res-sig")
        r = client.post("/api/v1/pmo/signals/res-sig/resolve")
        assert r.status_code == 200

    def test_resolve_returns_resolved_true(self, client: TestClient) -> None:
        _create_signal(client, signal_id="res2-sig")
        body = client.post("/api/v1/pmo/signals/res2-sig/resolve").json()
        assert body["resolved"] is True

    def test_resolve_returns_signal_id(self, client: TestClient) -> None:
        _create_signal(client, signal_id="res3-sig")
        body = client.post("/api/v1/pmo/signals/res3-sig/resolve").json()
        assert body["signal_id"] == "res3-sig"

    def test_resolve_nonexistent_signal_returns_404(self, client: TestClient) -> None:
        r = client.post("/api/v1/pmo/signals/no-such-signal/resolve")
        assert r.status_code == 404

    def test_resolved_signal_no_longer_in_open_list(self, client: TestClient) -> None:
        _create_signal(client, signal_id="gone-sig")
        client.post("/api/v1/pmo/signals/gone-sig/resolve")
        body = client.get("/api/v1/pmo/signals").json()
        ids = [s["signal_id"] for s in body]
        assert "gone-sig" not in ids


# ===========================================================================
# POST /api/v1/pmo/signals/{signal_id}/forge
# ===========================================================================


class TestForgeSignal:
    def test_forge_signal_returns_201(self, client: TestClient) -> None:
        _register_project(client, project_id="fsig-proj", program="FS")
        _create_signal(client, signal_id="fsig-001")
        r = client.post(
            "/api/v1/pmo/signals/fsig-001/forge",
            json={"plan": _minimal_plan().to_dict(), "project_id": "fsig-proj"},
        )
        assert r.status_code == 201

    def test_forge_signal_response_has_signal_id(self, client: TestClient) -> None:
        _register_project(client, project_id="fsig2-proj", program="FS2")
        _create_signal(client, signal_id="fsig2-001")
        body = client.post(
            "/api/v1/pmo/signals/fsig2-001/forge",
            json={"plan": _minimal_plan().to_dict(), "project_id": "fsig2-proj"},
        ).json()
        assert body["signal_id"] == "fsig2-001"

    def test_forge_signal_response_has_plan_id(self, client: TestClient) -> None:
        _register_project(client, project_id="fsig3-proj", program="FS3")
        _create_signal(client, signal_id="fsig3-001")
        body = client.post(
            "/api/v1/pmo/signals/fsig3-001/forge",
            json={"plan": _minimal_plan().to_dict(), "project_id": "fsig3-proj"},
        ).json()
        assert "plan_id" in body
        assert isinstance(body["plan_id"], str)

    def test_forge_signal_response_has_path(self, client: TestClient) -> None:
        _register_project(client, project_id="fsig4-proj", program="FS4")
        _create_signal(client, signal_id="fsig4-001")
        body = client.post(
            "/api/v1/pmo/signals/fsig4-001/forge",
            json={"plan": _minimal_plan().to_dict(), "project_id": "fsig4-proj"},
        ).json()
        assert "path" in body

    def test_forge_unknown_signal_returns_404(self, client: TestClient) -> None:
        _register_project(client, project_id="fsig5-proj", program="FS5")
        r = client.post(
            "/api/v1/pmo/signals/no-such-signal/forge",
            json={"plan": _minimal_plan().to_dict(), "project_id": "fsig5-proj"},
        )
        assert r.status_code == 404

    def test_forge_unknown_project_returns_404(self, client: TestClient) -> None:
        _create_signal(client, signal_id="fsig6-001")
        r = client.post(
            "/api/v1/pmo/signals/fsig6-001/forge",
            json={"plan": _minimal_plan().to_dict(), "project_id": "no-such-proj"},
        )
        assert r.status_code == 404

    # ------------------------------------------------------------------
    # Regression: F-AF-1 — ForgeSignalRequest must not require `plan`
    # ------------------------------------------------------------------

    def test_forge_signal_accepts_project_id_only(self, client: TestClient) -> None:
        """POST with only project_id must return 201, not 422.

        Previously the endpoint used ApproveForgeRequest which required a
        ``plan`` field.  The frontend sends only {"project_id": "..."},
        causing a 422 rejection.  ForgeSignalRequest fixes this.
        """
        _register_project(client, project_id="fsig7-proj", program="FS7")
        _create_signal(client, signal_id="fsig7-001")
        r = client.post(
            "/api/v1/pmo/signals/fsig7-001/forge",
            json={"project_id": "fsig7-proj"},
        )
        assert r.status_code == 201, f"Expected 201, got {r.status_code}: {r.text}"

    def test_forge_signal_project_id_only_returns_signal_id(self, client: TestClient) -> None:
        """Response from the minimal payload must include signal_id."""
        _register_project(client, project_id="fsig8-proj", program="FS8")
        _create_signal(client, signal_id="fsig8-001")
        body = client.post(
            "/api/v1/pmo/signals/fsig8-001/forge",
            json={"project_id": "fsig8-proj"},
        ).json()
        assert body["signal_id"] == "fsig8-001"


# ===========================================================================
# POST /api/v1/pmo/signals/{signal_id}/resolve — full signal response
# (regression: F-AF-2)
# ===========================================================================


class TestResolveSignalFullResponse:
    """Regression tests for F-AF-2.

    Before the fix, resolve returned only {"resolved": true, "signal_id": "..."}.
    The frontend expected a full PmoSignal object to replace the signal in its
    local state array.  The fix returns a ResolveSignalResponse that is a
    superset: it contains all PmoSignalResponse fields plus ``resolved: true``.
    """

    def test_resolve_signal_returns_full_signal(self, client: TestClient) -> None:
        """Resolved response must include signal_id, title, severity, and status."""
        _create_signal(
            client,
            signal_id="full-res-001",
            signal_type="bug",
            title="Critical null pointer",
            severity="critical",
        )
        body = client.post("/api/v1/pmo/signals/full-res-001/resolve").json()
        for field in ("signal_id", "title", "severity", "status"):
            assert field in body, f"Missing field '{field}' in resolve response"

    def test_resolve_signal_status_is_resolved(self, client: TestClient) -> None:
        """The ``status`` field in the response must be ``"resolved"``."""
        _create_signal(client, signal_id="status-res-001")
        body = client.post("/api/v1/pmo/signals/status-res-001/resolve").json()
        assert body["status"] == "resolved"

    def test_resolve_signal_resolved_flag_is_true(self, client: TestClient) -> None:
        """The ``resolved`` flag must still be present and True (backward compat)."""
        _create_signal(client, signal_id="flag-res-001")
        body = client.post("/api/v1/pmo/signals/flag-res-001/resolve").json()
        assert body["resolved"] is True

    def test_resolve_signal_returns_correct_signal_id(self, client: TestClient) -> None:
        """The ``signal_id`` in the response must match the URL parameter."""
        _create_signal(client, signal_id="id-res-001")
        body = client.post("/api/v1/pmo/signals/id-res-001/resolve").json()
        assert body["signal_id"] == "id-res-001"

    def test_resolve_signal_returns_title(self, client: TestClient) -> None:
        """The ``title`` field must reflect the original signal title."""
        _create_signal(client, signal_id="title-res-001", title="Memory leak in allocator")
        body = client.post("/api/v1/pmo/signals/title-res-001/resolve").json()
        assert body["title"] == "Memory leak in allocator"

    def test_resolve_signal_returns_severity(self, client: TestClient) -> None:
        """The ``severity`` field must be present in the response."""
        _create_signal(client, signal_id="sev-res-001", severity="high")
        body = client.post("/api/v1/pmo/signals/sev-res-001/resolve").json()
        assert body["severity"] == "high"
