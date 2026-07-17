"""Tests for the independent, diff-derived scope-expansion enforcement path
added to ``ExecutionEngine.record_step_result`` (Phase 3 "Make scope
contracts authoritative", step 3.2).

Reuses the manager-mode execution harness from
``tests.e2e.test_manager_mode_execution_dry_run`` (engine construction with a
fake bead store, ``_routing_plan``, ``ManagerArtifactPaths`` helper) rather
than duplicating it.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agent_baton.core.engine.worktree_manager import WorktreeHandle
from tests.e2e.test_manager_mode_execution_dry_run import (
    _engine_with_fake_beads,
    _paths,
    _routing_plan,
)


class _FakeWorktreeMgr:
    """Minimal WorktreeManager stand-in.

    The diff-verification block only needs ``_bead_store`` (truthiness
    check) and ``_file_bead_warning``; the pre-existing Wave 1.3 fold-back
    block downstream of it (unmodified by this step) additionally needs
    ``fold_back`` / ``cleanup`` / ``_verify_safe_to_discard``. Tests assert
    on ``fold_back_calls`` to confirm an out-of-scope diff is never folded.
    """

    def __init__(self) -> None:
        self._bead_store = None
        self._trace = None
        self.cleanup_calls: list[tuple[object, bool, bool]] = []
        self.fold_back_calls: list[tuple[object, str]] = []
        self.discard_check_calls: list[object] = []
        self.warnings: list[str] = []

    def _file_bead_warning(self, *, task_id: str, step_id: str, content: str) -> None:
        self.warnings.append(content)

    def cleanup(self, handle, on_failure: bool = False, force: bool = False) -> None:
        self.cleanup_calls.append((handle, on_failure, force))

    def fold_back(self, handle, commit_hash: str) -> str:
        self.fold_back_calls.append((handle, commit_hash))
        return commit_hash

    def _verify_safe_to_discard(self, handle) -> None:
        self.discard_check_calls.append(handle)


def _init_git_repo(tmp_path: Path) -> tuple[str, str]:
    repo = str(tmp_path)
    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    run("init", "-q")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text("x = 1\n")
    run("add", "-A")
    run("commit", "-q", "-m", "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    return repo, base_sha


def _worktree_handle_dict(*, path: str, base_sha: str, task_id: str, step_id: str) -> dict:
    return WorktreeHandle(
        task_id=task_id,
        step_id=step_id,
        path=Path(path),
        branch=f"worktree/{task_id}/{step_id}",
        base_branch="main",
        base_sha=base_sha,
        created_at="2026-07-10T00:00:00Z",
        parent_repo=Path(path),
    ).to_dict()


class TestOutOfScopeDiffBlocksAcceptance:
    def test_out_of_contract_committed_change_fails_the_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-diff-block"
        worktree_dir = tmp_path / "wt"
        worktree_dir.mkdir()
        repo, base_sha = _init_git_repo(worktree_dir)

        # Agent commits inside its worktree, touching a path OUTSIDE its
        # allowed_paths ("app") -- and never emits a SCOPE_EXPANSION marker.
        (worktree_dir / "infra").mkdir()
        (worktree_dir / "infra" / "deploy.yml").write_text("deploy: true\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "sneak in infra change"], cwd=repo, check=True)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        plan = _routing_plan(task_id)
        plan.phases[0].steps[0].allowed_paths = ["app"]
        ctx_dir = tmp_path / ".claude" / "team-context"

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        fake_wt = _FakeWorktreeMgr()
        engine._worktree_mgr = fake_wt
        engine.start(plan)

        state = engine._load_state()
        state.step_worktrees["1.1"] = _worktree_handle_dict(
            path=repo, base_sha=base_sha, task_id=task_id, step_id="1.1"
        )
        engine._save_execution(state)

        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",  # the agent (and any caller) claims success
            outcome="Implemented the base service. No scope issues to report.",
            commit_hash=head,  # even a caller-reported commit_hash is not trusted
            files_changed=["app/a.py"],  # a caller-reported (wrong!) diff is not trusted
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "failed", "an undeclared out-of-scope diff must never be accepted"
        assert "OUT_OF_SCOPE_DIFF" in result.error
        assert "infra/deploy.yml" in result.error

        # Never folded back.
        assert fake_wt.fold_back_calls == []
        assert fake_wt.cleanup_calls, "worktree must be retained via the 'failed' cleanup path"
        assert fake_wt.cleanup_calls[0][1] is True  # on_failure=True

        # The worktree registry entry is still present (retained, not popped).
        state = engine._load_state()
        assert "1.1" in state.step_worktrees

    def test_durable_evidence_backed_decision_is_filed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-diff-decision"
        worktree_dir = tmp_path / "wt"
        worktree_dir.mkdir()
        repo, base_sha = _init_git_repo(worktree_dir)
        (worktree_dir / "infra").mkdir()
        (worktree_dir / "infra" / "deploy.yml").write_text("deploy: true\n")
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
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome="All good.",
        )

        paths = _paths(tmp_path, task_id)
        decision_files = list(paths.decisions_dir.glob("*.md"))
        assert decision_files, "expected a durable decision packet"
        packet_text = decision_files[0].read_text(encoding="utf-8")
        assert "infra/deploy.yml" in packet_text
        assert "independently" in packet_text.lower()

        log_entries = [
            json.loads(line)
            for line in paths.decision_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        assert any(e.get("decision_type") == "scope_expansion" for e in log_entries)

        evidence_files = list(paths.scope_evidence_dir.glob("*.json"))
        assert evidence_files, "expected persisted diff evidence"
        evidence = json.loads(evidence_files[0].read_text(encoding="utf-8"))
        assert evidence["step_id"] == "1.1"
        assert "infra/deploy.yml" in evidence["real_changed_files"]
        assert evidence["violations"][0]["path"] == "infra/deploy.yml"

    def test_clean_diff_within_scope_is_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-diff-clean"
        worktree_dir = tmp_path / "wt"
        worktree_dir.mkdir()
        repo, base_sha = _init_git_repo(worktree_dir)
        (worktree_dir / "app" / "b.py").write_text("y = 2\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "add b.py inside scope"], cwd=repo, check=True)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        plan = _routing_plan(task_id)
        plan.phases[0].steps[0].allowed_paths = ["app"]
        ctx_dir = tmp_path / ".claude" / "team-context"

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        fake_wt = _FakeWorktreeMgr()
        engine._worktree_mgr = fake_wt
        engine.start(plan)

        state = engine._load_state()
        state.step_worktrees["1.1"] = _worktree_handle_dict(
            path=repo, base_sha=base_sha, task_id=task_id, step_id="1.1"
        )
        engine._save_execution(state)

        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome="Implemented b.py.",
            commit_hash=head,
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "complete"

    def test_no_scope_contract_means_no_enforcement(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A step with no allowed_paths/blocked_paths at all has no contract
        to violate -- the diff-verification block is a no-op for it."""
        task_id = "task-diff-no-contract"
        worktree_dir = tmp_path / "wt"
        worktree_dir.mkdir()
        repo, base_sha = _init_git_repo(worktree_dir)
        (worktree_dir / "anything").mkdir()
        (worktree_dir / "anything" / "x.py").write_text("z = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "no contract"], cwd=repo, check=True)

        plan = _routing_plan(task_id)
        assert plan.phases[0].steps[0].allowed_paths == []
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
            step_id="1.1", agent_name="backend-engineer", status="complete", outcome="Done.",
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "complete"


# ---------------------------------------------------------------------------
# resolve_scope_expansion
# ---------------------------------------------------------------------------


def _trigger_diff_violation(tmp_path: Path, task_id: str, monkeypatch: pytest.MonkeyPatch):
    """Shared setup: run a plan step whose actual diff violates its
    allowed_paths=["app"] contract, producing a durable decision. Returns
    (engine, decision_id)."""
    worktree_dir = tmp_path / "wt"
    worktree_dir.mkdir()
    repo, base_sha = _init_git_repo(worktree_dir)
    (worktree_dir / "infra").mkdir()
    (worktree_dir / "infra" / "deploy.yml").write_text("deploy: true\n")
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

    paths = _paths(tmp_path, task_id)
    log_entries = [
        json.loads(line)
        for line in paths.decision_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    decision_id = next(e["decision_id"] for e in log_entries if e["decision_type"] == "scope_expansion")
    return engine, decision_id


class TestResolveScopeExpansion:
    def test_invalid_resolution_is_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        task_id = "task-resolve-invalid"
        engine, decision_id = _trigger_diff_violation(tmp_path, task_id, monkeypatch)
        result = engine.resolve_scope_expansion(decision_id, "maybe")
        assert result["applied"] is False
        assert "invalid resolution" in result["error"]

    def test_unknown_decision_id_is_reported(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        task_id = "task-resolve-unknown"
        engine, _decision_id = _trigger_diff_violation(tmp_path, task_id, monkeypatch)
        result = engine.resolve_scope_expansion("dec-doesnotexist", "approve")
        assert result["applied"] is False
        assert "no decision found" in result["error"]

    def test_reject_leaves_step_failed_and_worktree_retained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-resolve-reject"
        engine, decision_id = _trigger_diff_violation(tmp_path, task_id, monkeypatch)

        result = engine.resolve_scope_expansion(decision_id, "reject")
        assert result["applied"] is True
        assert result["resolution"] == "reject"

        state = engine._load_state()
        step_result = state.get_step_result("1.1")
        assert step_result.status == "failed", "denied expansion must leave the step failed"
        assert "1.1" in state.step_worktrees, "denied expansion must leave the worktree retained"
        assert state.plan.phases[0].steps[0].allowed_paths == ["app"], "denied expansion must not amend the plan"

    def test_reject_twice_is_idempotent_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-resolve-reject-twice"
        engine, decision_id = _trigger_diff_violation(tmp_path, task_id, monkeypatch)
        engine.resolve_scope_expansion(decision_id, "reject")
        second = engine.resolve_scope_expansion(decision_id, "reject")
        assert second["applied"] is False
        assert "already resolved" in second["error"]

    def test_approve_amends_plan_and_clears_failed_step_for_retry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-resolve-approve"
        engine, decision_id = _trigger_diff_violation(tmp_path, task_id, monkeypatch)

        result = engine.resolve_scope_expansion(decision_id, "approve")
        assert result["applied"] is True
        assert result["resolution"] == "approve"
        assert "infra/deploy.yml" in result["new_allowed_paths"]

        state = engine._load_state()
        step = state.plan.phases[0].steps[0]
        assert "app" in step.allowed_paths
        assert "infra/deploy.yml" in step.allowed_paths

        # The failed StepResult is cleared so the step is dispatchable again.
        assert state.get_step_result("1.1") is None
        # The stale worktree pointer is cleared so re-dispatch creates a fresh one.
        assert "1.1" not in state.step_worktrees

        actions = engine.next_actions()
        assert any(a.step_id == "1.1" for a in actions), "step must be re-dispatchable after approval"

    def test_approve_with_explicit_additional_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-resolve-approve-explicit"
        engine, decision_id = _trigger_diff_violation(tmp_path, task_id, monkeypatch)

        result = engine.resolve_scope_expansion(
            decision_id, "approve", additional_paths=["infra"]
        )
        assert result["applied"] is True
        assert result["new_allowed_paths"] == ["app", "infra"]

    def test_approve_writes_scope_contract_sidecar_when_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-resolve-approve-sidecar"
        engine, decision_id = _trigger_diff_violation(tmp_path, task_id, monkeypatch)
        paths = _paths(tmp_path, task_id)
        contract_path = paths.scope_contract("1.1", ext="json")
        contract_path.parent.mkdir(parents=True, exist_ok=True)
        contract_path.write_text(json.dumps({"step_id": "1.1", "allowed_paths": ["app"]}), encoding="utf-8")

        engine.resolve_scope_expansion(decision_id, "approve")

        updated = json.loads(contract_path.read_text(encoding="utf-8"))
        assert "infra/deploy.yml" in updated["allowed_paths"]

    def test_approve_also_publishes_a_full_manager_artifact_revision(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Phase 6, 6.3: approving a scope-expansion decision widens the
        specific step's contract (Phase 3, unchanged, asserted above) AND
        triggers a best-effort full rebuild-and-publish of every manager
        sidecar so the rest of the artifact set (scope map, blueprint,
        knowledge plan, every other bundle) doesn't drift stale relative
        to the widened plan."""
        task_id = "task-resolve-approve-revision"
        engine, decision_id = _trigger_diff_violation(tmp_path, task_id, monkeypatch)
        paths = _paths(tmp_path, task_id)
        assert not paths.revision_manifest.exists()

        result = engine.resolve_scope_expansion(decision_id, "approve")

        assert result["applied"] is True
        assert paths.revision_manifest.is_file()
        manifest = json.loads(paths.revision_manifest.read_text(encoding="utf-8"))
        assert manifest["revision"] == 1
        assert manifest["trigger"] == "scope_expansion_resolved"
        # The step's widened allowed_paths flow through to the freshly
        # rebuilt scope contract too (not just the earlier narrow patch).
        contract = json.loads(
            paths.scope_contract("1.1", ext="json").read_text(encoding="utf-8")
        )
        assert "infra/deploy.yml" in contract["allowed_paths"]
