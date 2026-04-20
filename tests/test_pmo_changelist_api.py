"""HTTP-level tests for PMO changelist / merge / create-pr endpoints.

Endpoints covered (all prefixed with /api/v1):

  GET  /pmo/cards/{card_id}/changelist   — return ConsolidationResult for a card
  POST /pmo/cards/{card_id}/merge        — fast-forward merge consolidated branch
  POST /pmo/cards/{card_id}/create-pr   — open a GitHub PR via gh CLI

Strategy:
- PmoScanner is replaced with _StubScanner that returns controlled card lists.
- PmoStore is backed by a tmp directory.
- Storage backend is mocked to return controlled ExecutionState objects.
- subprocess / shutil.which are patched for merge and create-pr to avoid
  real git and gh calls.
- No network or filesystem git operations are performed.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from agent_baton.api.deps import (  # noqa: E402
    get_bus,
    get_forge_session,
    get_pmo_scanner,
    get_pmo_store,
)
from agent_baton.api.server import create_app  # noqa: E402
from agent_baton.core.events.bus import EventBus  # noqa: E402
from agent_baton.core.pmo.store import PmoStore  # noqa: E402
from agent_baton.models.execution import (  # noqa: E402
    ConsolidationResult,
    ExecutionState,
    MachinePlan,
    PlanGate,
    PlanPhase,
    PlanStep,
    StepResult,
)
from agent_baton.models.pmo import PmoCard, PmoProject  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tmp_store(tmp_path: Path) -> PmoStore:
    return PmoStore(
        config_path=tmp_path / "pmo-config.json",
        archive_path=tmp_path / "pmo-archive.jsonl",
    )


def _minimal_plan(task_id: str = "cl-task-001") -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="Changelist test plan",
        phases=[
            PlanPhase(
                phase_id=0,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer--python",
                        task_description="Implement feature",
                    )
                ],
                gate=PlanGate(gate_type="test", command="pytest"),
            )
        ],
    )


def _make_card(
    task_id: str,
    project_id: str = "proj-cl",
    column: str = "executing",
) -> PmoCard:
    return PmoCard(
        card_id=task_id,
        project_id=project_id,
        program="CL",
        title=f"Card {task_id}",
        column=column,
        risk_level="LOW",
        priority=0,
        agents=["backend-engineer--python"],
    )


class _StubScanner:
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


def _make_app(
    tmp_path: Path,
    store: PmoStore,
    cards: list[PmoCard],
    bus: EventBus | None = None,
) -> TestClient:
    app = create_app(team_context_root=tmp_path)
    scanner = _StubScanner(cards)
    forge_stub = MagicMock()
    _bus = bus or EventBus()

    app.dependency_overrides[get_pmo_store] = lambda: store
    app.dependency_overrides[get_pmo_scanner] = lambda: scanner
    app.dependency_overrides[get_forge_session] = lambda: forge_stub
    app.dependency_overrides[get_bus] = lambda: _bus
    return TestClient(app)


def _register_project(
    store: PmoStore,
    tmp_path: Path,
    project_id: str = "proj-cl",
) -> Path:
    project_root = tmp_path / project_id
    project_root.mkdir(parents=True, exist_ok=True)
    store.register_project(
        PmoProject(
            project_id=project_id,
            name="Changelist Project",
            path=str(project_root),
            program="CL",
        )
    )
    return project_root


def _write_execution_state(
    project_root: Path,
    state: ExecutionState,
) -> None:
    """Write execution-state.json in the standard location."""
    exec_dir = (
        project_root
        / ".claude"
        / "team-context"
        / "executions"
        / state.task_id
    )
    exec_dir.mkdir(parents=True, exist_ok=True)
    (exec_dir / "execution-state.json").write_text(
        json.dumps(state.to_dict()), encoding="utf-8"
    )


def _make_consolidation_result(status: str = "success") -> ConsolidationResult:
    return ConsolidationResult(
        status=status,
        final_head="deadbeef1234" if status == "success" else "",
        base_commit="base0000",
        files_changed=["src/main.py", "tests/test_main.py"],
        total_insertions=42,
        total_deletions=7,
        rebased_commits=[
            {
                "step_id": "1.1",
                "agent_name": "backend-engineer--python",
                "original_hash": "orig111",
                "new_hash": "new111",
            }
        ],
        skipped_steps=[],
        conflict_files=[] if status == "success" else ["shared.py"],
        conflict_step_id="" if status == "success" else "1.1",
        started_at="2026-04-19T10:00:00Z",
        completed_at="2026-04-19T10:01:00Z",
    )


# ===========================================================================
# GET /api/v1/pmo/cards/{card_id}/changelist
# ===========================================================================


class TestGetChangelist:
    def test_unknown_card_returns_404(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        client = _make_app(tmp_path, store, [])
        r = client.get("/api/v1/pmo/cards/no-such-card/changelist")
        assert r.status_code == 404

    def test_404_detail_mentions_card_id(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        client = _make_app(tmp_path, store, [])
        r = client.get("/api/v1/pmo/cards/missing-cl-card/changelist")
        assert "missing-cl-card" in r.json()["detail"]

    def test_card_without_consolidation_result_returns_404(
        self, tmp_path: Path
    ) -> None:
        """A card with an execution state but no consolidation_result → 404."""
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("no-cr-task")
        state = ExecutionState(plan=plan, task_id="no-cr-task")
        # No consolidation_result set.
        _write_execution_state(project_root, state)

        card = _make_card("no-cr-task")
        client = _make_app(tmp_path, store, [card])

        r = client.get("/api/v1/pmo/cards/no-cr-task/changelist")
        assert r.status_code == 404

    def test_returns_200_when_consolidation_result_present(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("cr-task-001")
        state = ExecutionState(plan=plan, task_id="cr-task-001")
        state.consolidation_result = _make_consolidation_result("success")
        _write_execution_state(project_root, state)

        card = _make_card("cr-task-001")
        client = _make_app(tmp_path, store, [card])

        r = client.get("/api/v1/pmo/cards/cr-task-001/changelist")
        assert r.status_code == 200

    def test_response_shape_has_status_field(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("cr-shape-001")
        state = ExecutionState(plan=plan, task_id="cr-shape-001")
        state.consolidation_result = _make_consolidation_result("success")
        _write_execution_state(project_root, state)

        card = _make_card("cr-shape-001")
        client = _make_app(tmp_path, store, [card])

        body = client.get("/api/v1/pmo/cards/cr-shape-001/changelist").json()
        assert "status" in body

    def test_response_status_matches_consolidation_result(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("cr-status-001")
        state = ExecutionState(plan=plan, task_id="cr-status-001")
        state.consolidation_result = _make_consolidation_result("conflict")
        _write_execution_state(project_root, state)

        card = _make_card("cr-status-001")
        client = _make_app(tmp_path, store, [card])

        body = client.get("/api/v1/pmo/cards/cr-status-001/changelist").json()
        assert body["status"] == "conflict"

    def test_response_has_files_changed_list(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("cr-files-001")
        state = ExecutionState(plan=plan, task_id="cr-files-001")
        state.consolidation_result = _make_consolidation_result("success")
        _write_execution_state(project_root, state)

        card = _make_card("cr-files-001")
        client = _make_app(tmp_path, store, [card])

        body = client.get("/api/v1/pmo/cards/cr-files-001/changelist").json()
        assert "files_changed" in body
        assert isinstance(body["files_changed"], list)

    def test_response_contains_all_required_fields(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("cr-allfields-001")
        state = ExecutionState(plan=plan, task_id="cr-allfields-001")
        state.consolidation_result = _make_consolidation_result("success")
        _write_execution_state(project_root, state)

        card = _make_card("cr-allfields-001")
        client = _make_app(tmp_path, store, [card])

        body = client.get("/api/v1/pmo/cards/cr-allfields-001/changelist").json()
        for field in (
            "status", "final_head", "base_commit", "files_changed",
            "total_insertions", "total_deletions", "rebased_commits",
            "attributions", "skipped_steps", "conflict_files",
            "started_at", "completed_at",
        ):
            assert field in body, f"missing field: {field}"

    def test_skipped_steps_are_reflected_in_response(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("cr-skip-001")
        state = ExecutionState(plan=plan, task_id="cr-skip-001")
        cr = _make_consolidation_result("success")
        cr.skipped_steps = ["1.2", "1.3"]
        state.consolidation_result = cr
        _write_execution_state(project_root, state)

        card = _make_card("cr-skip-001")
        client = _make_app(tmp_path, store, [card])

        body = client.get("/api/v1/pmo/cards/cr-skip-001/changelist").json()
        assert "1.2" in body["skipped_steps"]
        assert "1.3" in body["skipped_steps"]

    def test_conflict_fields_populated_when_status_is_conflict(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("cr-conflict-001")
        state = ExecutionState(plan=plan, task_id="cr-conflict-001")
        state.consolidation_result = _make_consolidation_result("conflict")
        _write_execution_state(project_root, state)

        card = _make_card("cr-conflict-001")
        client = _make_app(tmp_path, store, [card])

        body = client.get("/api/v1/pmo/cards/cr-conflict-001/changelist").json()
        assert body["status"] == "conflict"
        assert "conflict_files" in body
        assert isinstance(body["conflict_files"], list)


# ===========================================================================
# POST /api/v1/pmo/cards/{card_id}/merge
# ===========================================================================


class TestMergeCard:
    """Tests for the merge endpoint.  Git calls are mocked."""

    def _state_with_success_cr(self, task_id: str) -> ExecutionState:
        plan = _minimal_plan(task_id)
        state = ExecutionState(plan=plan, task_id=task_id)
        state.consolidation_result = _make_consolidation_result("success")
        state.step_results = [
            StepResult(
                step_id="1.1",
                agent_name="backend-engineer--python",
                status="complete",
                outcome="Done",
                commit_hash="abc123",
            )
        ]
        return state

    def test_unknown_card_returns_404(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        client = _make_app(tmp_path, store, [])
        r = client.post(
            "/api/v1/pmo/cards/no-card/merge",
            json={"force": False},
        )
        assert r.status_code == 404

    def test_card_without_consolidation_result_returns_404(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("merge-nocr-task")
        state = ExecutionState(plan=plan, task_id="merge-nocr-task")
        _write_execution_state(project_root, state)

        card = _make_card("merge-nocr-task")
        client = _make_app(tmp_path, store, [card])

        r = client.post(
            "/api/v1/pmo/cards/merge-nocr-task/merge",
            json={"force": False},
        )
        assert r.status_code == 404

    def test_merge_with_non_success_status_and_no_force_returns_409(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("merge-conflict-task")
        state = ExecutionState(plan=plan, task_id="merge-conflict-task")
        state.consolidation_result = _make_consolidation_result("conflict")
        _write_execution_state(project_root, state)

        card = _make_card("merge-conflict-task")
        client = _make_app(tmp_path, store, [card])

        r = client.post(
            "/api/v1/pmo/cards/merge-conflict-task/merge",
            json={"force": False},
        )
        assert r.status_code == 409

    def test_merge_with_force_bypasses_status_guard(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        plan = _minimal_plan("merge-force-task")
        state = ExecutionState(plan=plan, task_id="merge-force-task")
        state.consolidation_result = _make_consolidation_result("conflict")
        _write_execution_state(project_root, state)

        card = _make_card("merge-force-task")
        client = _make_app(tmp_path, store, [card])

        # Mock git rev-parse to return a fake HEAD sha.
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "deadbeef\n"
        fake_proc.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/git"), \
             patch("subprocess.run", return_value=fake_proc):
            r = client.post(
                "/api/v1/pmo/cards/merge-force-task/merge",
                json={"force": True},
            )

        assert r.status_code == 200

    def test_successful_merge_returns_merge_commit(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        task_id = "merge-ok-task"
        state = self._state_with_success_cr(task_id)
        _write_execution_state(project_root, state)

        card = _make_card(task_id)
        client = _make_app(tmp_path, store, [card])

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "cafebabe\n"
        fake_proc.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/git"), \
             patch("subprocess.run", return_value=fake_proc):
            r = client.post(
                f"/api/v1/pmo/cards/{task_id}/merge",
                json={"force": False},
            )

        assert r.status_code == 200
        body = r.json()
        assert "merge_commit" in body

    def test_merge_response_has_cleaned_worktrees_list(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        task_id = "merge-wt-task"
        state = self._state_with_success_cr(task_id)
        _write_execution_state(project_root, state)

        card = _make_card(task_id)
        client = _make_app(tmp_path, store, [card])

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "abc123\n"
        fake_proc.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/git"), \
             patch("subprocess.run", return_value=fake_proc):
            body = client.post(
                f"/api/v1/pmo/cards/{task_id}/merge",
                json={"force": False},
            ).json()

        assert "cleaned_worktrees" in body
        assert isinstance(body["cleaned_worktrees"], list)

    def test_merge_git_not_found_returns_500(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)

        task_id = "merge-nogit-task"
        state = self._state_with_success_cr(task_id)
        _write_execution_state(project_root, state)

        card = _make_card(task_id)
        client = _make_app(tmp_path, store, [card])

        with patch("shutil.which", return_value=None):
            r = client.post(
                f"/api/v1/pmo/cards/{task_id}/merge",
                json={"force": False},
            )

        assert r.status_code == 500


# ===========================================================================
# POST /api/v1/pmo/cards/{card_id}/create-pr
# ===========================================================================


class TestCreateCardPr:
    """Tests for the create-pr endpoint.  gh CLI calls are mocked."""

    def _state_with_step_results(self, task_id: str) -> ExecutionState:
        plan = _minimal_plan(task_id)
        state = ExecutionState(plan=plan, task_id=task_id)
        state.step_results = [
            StepResult(
                step_id="1.1",
                agent_name="backend-engineer--python",
                status="complete",
                outcome="Implemented feature X",
                commit_hash="abc123",
            )
        ]
        return state

    def test_unknown_card_returns_404(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        client = _make_app(tmp_path, store, [])
        r = client.post(
            "/api/v1/pmo/cards/no-such-card/create-pr",
            json={"title": "My PR", "base_branch": "main"},
        )
        assert r.status_code == 404

    def test_missing_title_returns_422(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        state = self._state_with_step_results("pr-notitle")
        _write_execution_state(project_root, state)

        card = _make_card("pr-notitle")
        client = _make_app(tmp_path, store, [card])

        r = client.post(
            "/api/v1/pmo/cards/pr-notitle/create-pr",
            json={"base_branch": "main"},
        )
        assert r.status_code == 422

    def test_gh_not_found_returns_500(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        state = self._state_with_step_results("pr-nogh")
        _write_execution_state(project_root, state)

        card = _make_card("pr-nogh")
        client = _make_app(tmp_path, store, [card])

        with patch("shutil.which", return_value=None):
            r = client.post(
                "/api/v1/pmo/cards/pr-nogh/create-pr",
                json={"title": "Feature PR", "base_branch": "main"},
            )

        assert r.status_code == 500
        assert "gh" in r.json()["detail"].lower()

    def test_gh_failure_returns_500(self, tmp_path: Path) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        state = self._state_with_step_results("pr-ghfail")
        _write_execution_state(project_root, state)

        card = _make_card("pr-ghfail")
        client = _make_app(tmp_path, store, [card])

        fake_proc = MagicMock()
        fake_proc.returncode = 1
        fake_proc.stdout = ""
        fake_proc.stderr = "authentication required"

        with patch("shutil.which", return_value="/usr/bin/gh"), \
             patch("subprocess.run", return_value=fake_proc):
            r = client.post(
                "/api/v1/pmo/cards/pr-ghfail/create-pr",
                json={"title": "Feature PR", "base_branch": "main"},
            )

        assert r.status_code == 500

    def test_successful_create_pr_returns_pr_url(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        task_id = "pr-success-001"
        state = self._state_with_step_results(task_id)
        _write_execution_state(project_root, state)

        card = _make_card(task_id)
        client = _make_app(tmp_path, store, [card])

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "https://github.com/org/repo/pull/42\n"
        fake_proc.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/gh"), \
             patch("subprocess.run", return_value=fake_proc):
            r = client.post(
                f"/api/v1/pmo/cards/{task_id}/create-pr",
                json={"title": "Feature: add auth", "base_branch": "main"},
            )

        assert r.status_code == 201
        body = r.json()
        assert "pr_url" in body
        assert "pull/42" in body["pr_url"]

    def test_successful_create_pr_returns_pr_number(
        self, tmp_path: Path
    ) -> None:
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        task_id = "pr-number-001"
        state = self._state_with_step_results(task_id)
        _write_execution_state(project_root, state)

        card = _make_card(task_id)
        client = _make_app(tmp_path, store, [card])

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "https://github.com/org/repo/pull/99\n"
        fake_proc.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/gh"), \
             patch("subprocess.run", return_value=fake_proc):
            body = client.post(
                f"/api/v1/pmo/cards/{task_id}/create-pr",
                json={"title": "PR title", "base_branch": "main"},
            ).json()

        assert body["pr_number"] == 99

    def test_gh_command_includes_title_and_base_branch(
        self, tmp_path: Path
    ) -> None:
        """Verify gh is invoked with the correct --title and --base flags."""
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        task_id = "pr-cmd-001"
        state = self._state_with_step_results(task_id)
        _write_execution_state(project_root, state)

        card = _make_card(task_id)
        client = _make_app(tmp_path, store, [card])

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "https://github.com/org/repo/pull/7\n"
        fake_proc.stderr = ""

        captured_cmd: list = []

        def _capture_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return fake_proc

        with patch("shutil.which", return_value="/usr/bin/gh"), \
             patch("subprocess.run", side_effect=_capture_run):
            client.post(
                f"/api/v1/pmo/cards/{task_id}/create-pr",
                json={"title": "My title", "base_branch": "develop"},
            )

        assert "--title" in captured_cmd
        assert "My title" in captured_cmd
        assert "--base" in captured_cmd
        assert "develop" in captured_cmd

    def test_unparsable_pr_url_returns_500(self, tmp_path: Path) -> None:
        """When gh outputs a URL without /pull/N the endpoint returns 500."""
        store = _make_tmp_store(tmp_path)
        project_root = _register_project(store, tmp_path)
        task_id = "pr-badurl-001"
        state = self._state_with_step_results(task_id)
        _write_execution_state(project_root, state)

        card = _make_card(task_id)
        client = _make_app(tmp_path, store, [card])

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = "Some unexpected output without a PR link\n"
        fake_proc.stderr = ""

        with patch("shutil.which", return_value="/usr/bin/gh"), \
             patch("subprocess.run", return_value=fake_proc):
            r = client.post(
                f"/api/v1/pmo/cards/{task_id}/create-pr",
                json={"title": "PR", "base_branch": "main"},
            )

        assert r.status_code == 500
