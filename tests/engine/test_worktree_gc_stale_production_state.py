"""RW-3 (rework of F049) — the gc_stale in-flight guard must read what
production actually writes, and must keep its terminal-step safety gate.

Companion to ``tests/test_worktree_manager_fold_and_gc.py``.  That file builds
the ``step_worktrees`` row with a hand-written ``INSERT``, so it cannot see
whether the *production* writer ever populates the column the guard reads.
Everything here drives real production paths only:

  * ``ExecutionEngine.start()`` + ``mark_dispatched()`` (which stores
    ``WorktreeHandle.to_dict()`` in ``state.step_worktrees``), and/or
  * ``SqliteStorage.save_execution()`` (which mirrors that dict into the
    ``step_worktrees`` table),

against a real git repo and a real ``baton.db``.  No hand-built INSERTs, no
mocks of the code under test, no source-text assertions (the one exception is
the documentation-consistency class at the bottom, which is explicitly about
doc text).

Requirements pinned (numbering from the RW-3 brief):

RW-3.1  The value ``WorktreeHandle.to_dict()`` emits for the worktree location
        must be the value ``_replace_collection_rows`` writes into
        ``step_worktrees.worktree_path`` — today to_dict() emits ``"path"`` and
        the writer reads ``"worktree_path"``, so the column is always ``''``
        and the guard can never match.
RW-3.2  ``gc_stale()`` must retain a worktree whose ownership was persisted
        through the real writer by a live execution.
RW-3.3  Both engine call sites (engine init, ``complete()``) must pass
        ``terminal_step_ids`` so GC cannot reclaim a worktree whose step never
        reached a terminal status.
RW-3.4  The stale threshold must be reconciled: the CLI default must not
        contradict ``WorktreeManager._get_default_stale_hours()``, and the
        takeover error message / docs must not advertise the retired 72h.

Also folded in from the same review (unnumbered, "low cost"):
  * ``_LIVE_EXECUTION_STATUSES`` must cover the whole non-terminal
    blocked-on-input cluster (``AwaitingApprovalState`` in
    ``agent_baton/core/engine/states.py``: approval_pending,
    feedback_pending, gate_pending, gate_failed, paused, paused-takeover),
    not just running + paused-takeover.
  * ``_rebase_fold`` / ``_merge_fold`` must refuse to integrate when the
    canonical repo is no longer on ``handle.base_branch``, instead of
    silently landing the agent's work on whatever branch is checked out.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.engine.worktree_manager import (
    WorktreeFoldError,
    WorktreeHandle,
    WorktreeManager,
)
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real git repo on ``main`` with one commit, worktrees enabled."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"],
                   cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "initial"],
                   cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.setenv("BATON_WORKTREE_ENABLED", "1")
    # The guard must discover baton.db by walking up from the project root —
    # never leak an ambient override from the developer's shell.
    monkeypatch.delenv("BATON_DB_PATH", raising=False)
    monkeypatch.delenv("BATON_WORKTREE_STALE_HOURS", raising=False)
    monkeypatch.delenv("BATON_WORKTREE_GC_HOURS", raising=False)
    return tmp_path


@pytest.fixture
def team_context(tmp_git_repo: Path) -> Path:
    ctx = tmp_git_repo / ".claude" / "team-context"
    ctx.mkdir(parents=True, exist_ok=True)
    return ctx


@pytest.fixture
def storage(team_context: Path) -> SqliteStorage:
    return SqliteStorage(team_context / "baton.db")


@pytest.fixture
def engine(team_context: Path, storage: SqliteStorage) -> ExecutionEngine:
    return ExecutionEngine(team_context_root=team_context, storage=storage)


def _plan(task_id: str, step_ids: tuple[str, ...] = ("1.1",)) -> MachinePlan:
    return MachinePlan(
        task_id=task_id,
        task_summary="RW-3 gc_stale guard fixture plan",
        risk_level="LOW",
        phases=[PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                PlanStep(
                    step_id=sid,
                    agent_name="backend-engineer",
                    task_description=f"do work for {sid}",
                    model="sonnet",
                    step_type="implementation",
                )
                for sid in step_ids
            ],
        )],
    )


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def _commit_file(repo_path: Path, filename: str, content: str) -> str:
    (repo_path / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=repo_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", f"add {filename}"], cwd=repo_path,
                   check=True, capture_output=True)
    r = _git(["rev-parse", "HEAD"], cwd=repo_path)
    return r.stdout.strip()


def _dispatched_handle(engine: ExecutionEngine, step_id: str) -> WorktreeHandle:
    """Return the handle the engine actually recorded for *step_id*."""
    state = engine._load_execution()
    assert state is not None, "engine must have persisted execution state"
    raw = dict(getattr(state, "step_worktrees", {})).get(step_id)
    assert raw is not None, (
        f"mark_dispatched() must record a worktree handle for step {step_id}"
    )
    return WorktreeHandle.from_dict(raw)


def _backdate_manifest(handle: WorktreeHandle, hours: float) -> None:
    """Rewrite the on-disk manifest so the worktree looks *hours* old."""
    manifest = handle.path / ".baton-worktree.json"
    data = json.loads(manifest.read_text("utf-8"))
    data["created_at"] = (
        datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    ).isoformat(timespec="seconds")
    manifest.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _persisted_worktree_paths(db_path: Path) -> dict[str, str]:
    """Return {step_id: worktree_path} straight out of the production table."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return {
            r["step_id"]: r["worktree_path"]
            for r in conn.execute(
                "SELECT step_id, worktree_path FROM step_worktrees"
            )
        }


def _set_execution_status(storage: SqliteStorage, task_id: str, status: str) -> None:
    """Flip execution status through the production writer (no hand INSERT)."""
    state = storage.load_execution(task_id)
    assert state is not None
    state.status = status
    storage.save_execution(state)


class _GcSpy:
    """Call-through spy over ``WorktreeManager.gc_stale``.

    The engine runs GC on a daemon thread, so tests need (a) a way to wait for
    it and (b) visibility of the kwargs the production call site passed.  The
    real implementation still runs — this is a spy, not a stub.
    """

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[dict] = []
        self.done = threading.Event()
        real = WorktreeManager.gc_stale

        def _spy(mgr_self, max_age_hours=None, **kwargs):  # type: ignore[no-untyped-def]
            self.calls.append({"max_age_hours": max_age_hours, **kwargs})
            try:
                return real(mgr_self, max_age_hours, **kwargs)
            finally:
                self.done.set()

        monkeypatch.setattr(WorktreeManager, "gc_stale", _spy)

    def wait(self, timeout: float = 30.0) -> None:
        assert self.done.wait(timeout), (
            "expected the engine to invoke WorktreeManager.gc_stale()"
        )


# ---------------------------------------------------------------------------
# RW-3.1 — the writer must persist the key the handle actually emits
# ---------------------------------------------------------------------------


class TestWorktreeOwnershipReachesTheColumnTheGuardReads:
    """The guard joins on ``step_worktrees.worktree_path``; production must fill it."""

    def test_engine_dispatch_populates_worktree_path_column(
        self, engine: ExecutionEngine, storage: SqliteStorage
    ) -> None:
        engine.start(_plan("task-col-engine"))
        engine.mark_dispatched("1.1", "backend-engineer")
        handle = _dispatched_handle(engine, "1.1")

        persisted = _persisted_worktree_paths(Path(storage.db_path))

        assert persisted.get("1.1"), (
            "step_worktrees.worktree_path is empty after a real dispatch — the "
            "in-flight guard joins on this column, so it can never match. "
            f"row={persisted!r} handle.path={handle.path}"
        )
        assert Path(persisted["1.1"]).resolve() == handle.path.resolve(), (
            "the persisted worktree_path must be the worktree the engine created; "
            f"got {persisted['1.1']!r}, expected {handle.path}"
        )

    def test_save_execution_populates_worktree_path_column(
        self, tmp_git_repo: Path, storage: SqliteStorage
    ) -> None:
        """Same defect at the storage seam, without the engine in the picture."""
        from agent_baton.models.execution import ExecutionState

        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id="task-col-storage", step_id="1.1",
                            base_branch="main")

        state = ExecutionState(
            task_id="task-col-storage",
            plan=_plan("task-col-storage"),
        )
        # Exactly what executor.mark_dispatched does.
        state.step_worktrees = {"1.1": handle.to_dict()}
        storage.save_execution(state)

        persisted = _persisted_worktree_paths(Path(storage.db_path))
        assert persisted.get("1.1"), (
            "save_execution() dropped the worktree location: WorktreeHandle."
            "to_dict() and the step_worktrees writer disagree about the key "
            f"name. row={persisted!r} to_dict()={handle.to_dict()!r}"
        )
        assert Path(persisted["1.1"]).resolve() == handle.path.resolve()

    def test_state_roundtrip_still_rehydrates_the_handle(
        self, engine: ExecutionEngine
    ) -> None:
        """Guard: fixing the key must not break state → WorktreeHandle reload."""
        engine.start(_plan("task-roundtrip"))
        engine.mark_dispatched("1.1", "backend-engineer")

        state = engine._load_execution()
        assert state is not None
        rehydrated = WorktreeHandle.from_dict(state.step_worktrees["1.1"])

        assert rehydrated.path.is_dir()
        assert rehydrated.branch == "worktree/task-roundtrip/1.1"

    def test_on_disk_manifest_still_rehydrates_the_handle(
        self, tmp_git_repo: Path
    ) -> None:
        """Guard: ``.baton-worktree.json`` is parsed by gc_stale — keep it readable."""
        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id="task-manifest", step_id="1.1",
                            base_branch="main")

        manifest = handle.path / ".baton-worktree.json"
        rehydrated = WorktreeHandle.from_dict(json.loads(manifest.read_text("utf-8")))

        assert rehydrated.path.resolve() == handle.path.resolve()
        assert rehydrated.step_id == "1.1"


# ---------------------------------------------------------------------------
# RW-3.2 — the guard must protect state persisted by the real writer
# ---------------------------------------------------------------------------


class TestGcStaleGuardAgainstProductionPersistedState:
    """No hand-written rows: ownership is recorded exactly as production records it."""

    def test_running_execution_persisted_by_engine_protects_its_worktree(
        self, engine: ExecutionEngine, tmp_git_repo: Path
    ) -> None:
        engine.start(_plan("task-live-real"))
        engine.mark_dispatched("1.1", "backend-engineer")
        handle = _dispatched_handle(engine, "1.1")
        _commit_file(handle.path, "wip.txt", "unmerged agent work\n")
        _backdate_manifest(handle, hours=10)

        mgr = WorktreeManager(project_root=tmp_git_repo)
        reclaimed = mgr.gc_stale(max_age_hours=1)

        assert [h.step_id for h in reclaimed] == [], (
            "gc_stale destroyed the worktree of a live execution whose ownership "
            "was persisted through the production writer — the in-flight guard "
            "reads a column production never fills"
        )
        assert handle.path.is_dir(), (
            "the worktree of a running execution must survive gc_stale"
        )

    def test_guard_reports_the_owning_task_for_production_state(
        self, engine: ExecutionEngine, tmp_git_repo: Path
    ) -> None:
        engine.start(_plan("task-live-owner"))
        engine.mark_dispatched("1.1", "backend-engineer")
        handle = _dispatched_handle(engine, "1.1")

        mgr = WorktreeManager(project_root=tmp_git_repo)
        in_flight, owner = mgr._is_in_flight(handle.path)

        assert in_flight is True
        assert owner == "task-live-owner", (
            "the guard must name the live execution that owns the worktree, so "
            f"the skip is auditable; got {owner!r}"
        )

    def test_finished_execution_worktree_is_still_reclaimed(
        self, engine: ExecutionEngine, tmp_git_repo: Path, storage: SqliteStorage
    ) -> None:
        """Control: the guard must not become a blanket refusal to ever GC."""
        engine.start(_plan("task-done-real"))
        engine.mark_dispatched("1.1", "backend-engineer")
        handle = _dispatched_handle(engine, "1.1")
        _backdate_manifest(handle, hours=10)
        _set_execution_status(storage, "task-done-real", "complete")

        mgr = WorktreeManager(project_root=tmp_git_repo)
        reclaimed = mgr.gc_stale(max_age_hours=1)

        assert [h.step_id for h in reclaimed] == ["1.1"], (
            "a stale worktree of a finished execution must still be reclaimed"
        )
        assert not handle.path.is_dir()


class TestGcStaleLiveStatusCoverage:
    """Every non-terminal execution status must protect its worktree.

    ``states.AwaitingApprovalState`` defines the blocked-on-input cluster:
    approval_pending, feedback_pending, gate_pending, gate_failed, paused,
    paused-takeover.  All of them own their worktree and none has reached a
    terminal state, so GC must leave them alone.
    """

    @pytest.mark.parametrize(
        "status",
        [
            "running",
            "paused-takeover",
            "gate_pending",
            "gate_failed",
            "approval_pending",
            "feedback_pending",
            "paused",
        ],
    )
    def test_non_terminal_status_protects_worktree(
        self,
        engine: ExecutionEngine,
        tmp_git_repo: Path,
        storage: SqliteStorage,
        status: str,
    ) -> None:
        task_id = f"task-status-{status.replace('_', '-')}"
        engine.start(_plan(task_id))
        engine.mark_dispatched("1.1", "backend-engineer")
        handle = _dispatched_handle(engine, "1.1")
        _backdate_manifest(handle, hours=10)
        _set_execution_status(storage, task_id, status)

        mgr = WorktreeManager(project_root=tmp_git_repo)
        reclaimed = mgr.gc_stale(max_age_hours=1)

        assert [h.step_id for h in reclaimed] == [], (
            f"gc_stale reclaimed the worktree of an execution in non-terminal "
            f"status {status!r}; work in that state is still owned and unmerged"
        )
        assert handle.path.is_dir()

    @pytest.mark.parametrize("status", ["complete", "failed", "cancelled"])
    def test_terminal_status_does_not_protect_worktree(
        self,
        engine: ExecutionEngine,
        tmp_git_repo: Path,
        storage: SqliteStorage,
        status: str,
    ) -> None:
        """Control: widening the live set must not disable GC altogether."""
        task_id = f"task-term-{status}"
        engine.start(_plan(task_id))
        engine.mark_dispatched("1.1", "backend-engineer")
        handle = _dispatched_handle(engine, "1.1")
        _backdate_manifest(handle, hours=10)
        _set_execution_status(storage, task_id, status)

        mgr = WorktreeManager(project_root=tmp_git_repo)
        reclaimed = mgr.gc_stale(max_age_hours=1)

        assert [h.step_id for h in reclaimed] == ["1.1"], (
            f"a stale worktree of a terminal ({status}) execution must be reclaimed"
        )


# ---------------------------------------------------------------------------
# RW-3.3 — both engine GC call sites must pass the terminal-step safety gate
# ---------------------------------------------------------------------------


class TestEngineGcPassesTerminalStepGate:
    """gc_stale's documented safety gate is opt-in; the engine must opt in."""

    def test_complete_retains_worktree_of_non_terminal_step(
        self, engine: ExecutionEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A step that never reported a result must keep its worktree."""
        engine.start(_plan("task-complete-nonterm"))
        engine.mark_dispatched("1.1", "backend-engineer")
        handle = _dispatched_handle(engine, "1.1")
        _commit_file(handle.path, "wip.txt", "in-flight agent work\n")
        _backdate_manifest(handle, hours=10)

        spy = _GcSpy(monkeypatch)
        engine.complete()
        spy.wait()

        assert handle.path.is_dir(), (
            "complete() GC destroyed the worktree of step 1.1, whose result is "
            "still 'dispatched' — gc_stale's terminal_step_ids gate was not "
            "passed, so the safety check never ran"
        )

    def test_complete_reclaims_worktree_of_terminal_step(
        self, engine: ExecutionEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Control: a failed (terminal) step's stale worktree is still reclaimed."""
        engine.start(_plan("task-complete-term"))
        engine.mark_dispatched("1.1", "backend-engineer")
        handle = _dispatched_handle(engine, "1.1")
        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer",
            status="failed", error="boom",
        )
        _backdate_manifest(handle, hours=10)

        spy = _GcSpy(monkeypatch)
        engine.complete()
        spy.wait()

        assert not handle.path.is_dir(), (
            "a stale worktree of a terminal (failed) step must still be reclaimed "
            "by complete()'s GC — the safety gate must not block everything"
        )

    def test_complete_gc_call_passes_terminal_step_ids(
        self, engine: ExecutionEngine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        engine.start(_plan("task-kwargs-complete", step_ids=("1.1", "1.2")))
        engine.mark_dispatched("1.1", "backend-engineer")
        engine.record_step_result(
            step_id="1.1", agent_name="backend-engineer", status="complete",
        )
        engine.mark_dispatched("1.2", "backend-engineer")

        spy = _GcSpy(monkeypatch)
        engine.complete()
        spy.wait()

        assert spy.calls, "complete() must run gc_stale"
        call = spy.calls[-1]
        assert call.get("terminal_step_ids") is not None, (
            "complete() called gc_stale without terminal_step_ids, which "
            f"disables the safety gate entirely; kwargs={call!r}"
        )
        terminal = set(call["terminal_step_ids"])
        assert "1.1" in terminal, (
            f"the completed step must be reclaimable; got {terminal!r}"
        )
        assert "1.2" not in terminal, (
            "step 1.2 is still dispatched — it must not be offered to GC as "
            f"terminal; got {terminal!r}"
        )

    def test_engine_init_gc_call_passes_terminal_step_ids(
        self, team_context: Path, storage: SqliteStorage,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        spy = _GcSpy(monkeypatch)
        ExecutionEngine(team_context_root=team_context, storage=storage)
        spy.wait()

        assert spy.calls, "engine init must run its background gc_stale"
        call = spy.calls[-1]
        assert call.get("terminal_step_ids") is not None, (
            "the engine-init background GC called gc_stale without "
            "terminal_step_ids, so it can reclaim a worktree whose step is "
            f"still in flight; kwargs={call!r}"
        )

    def test_engine_init_gc_retains_worktree_of_non_terminal_step(
        self, team_context: Path, storage: SqliteStorage, tmp_git_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Init GC must not reclaim a crashed run's still-dispatched worktree."""
        first = ExecutionEngine(team_context_root=team_context, storage=storage)
        first.start(_plan("task-init-nonterm"))
        first.mark_dispatched("1.1", "backend-engineer")
        handle = _dispatched_handle(first, "1.1")
        _commit_file(handle.path, "wip.txt", "work from a crashed run\n")
        _backdate_manifest(handle, hours=10)
        # The run is no longer live, so only the terminal-step gate can save it.
        _set_execution_status(storage, "task-init-nonterm", "complete")

        spy = _GcSpy(monkeypatch)
        ExecutionEngine(team_context_root=team_context, storage=storage)
        spy.wait()

        assert handle.path.is_dir(), (
            "engine-init GC destroyed the worktree of step 1.1, whose result is "
            "still 'dispatched' (unmerged work) — terminal_step_ids was never "
            "passed to gc_stale"
        )


# ---------------------------------------------------------------------------
# RW-3.4 — one stale threshold, consistently reported
# ---------------------------------------------------------------------------


def _execute_parser() -> argparse.ArgumentParser:
    from agent_baton.cli.commands.execution.execute import register

    root = argparse.ArgumentParser(prog="baton")
    sub = root.add_subparsers(dest="command")
    register(sub)
    return root


class TestStaleThresholdReconciliation:
    """``baton execute worktree-gc`` must agree with the manager's own default."""

    def test_cli_default_uses_the_manager_default_threshold(
        self, tmp_git_repo: Path, team_context: Path,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    ) -> None:
        from agent_baton.cli.commands.execution.execute import _handle_worktree_gc

        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id="task-cli-default", step_id="1.1",
                            base_branch="main")
        _backdate_manifest(handle, hours=5)  # older than 4h, younger than 72h

        monkeypatch.chdir(tmp_git_repo)
        args = _execute_parser().parse_args(["execute", "worktree-gc", "--dry-run"])
        _handle_worktree_gc(args)

        out = capsys.readouterr().out
        assert "step=1.1" in out, (
            "a 5h-old worktree was not eligible for `baton execute worktree-gc` "
            "with no --max-age-hours: the CLI default still contradicts "
            f"WorktreeManager._get_default_stale_hours()="
            f"{WorktreeManager._get_default_stale_hours()}. Output:\n{out}"
        )
        assert handle.path.is_dir(), "--dry-run must not delete anything"

    def test_cli_default_honours_the_stale_hours_env_var(
        self, tmp_git_repo: Path, team_context: Path,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The CLI must delegate, not hardcode: env override has to win."""
        from agent_baton.cli.commands.execution.execute import _handle_worktree_gc

        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id="task-cli-env", step_id="1.1",
                            base_branch="main")
        _backdate_manifest(handle, hours=5)

        monkeypatch.setenv("BATON_WORKTREE_STALE_HOURS", "10")
        monkeypatch.chdir(tmp_git_repo)
        args = _execute_parser().parse_args(["execute", "worktree-gc", "--dry-run"])
        _handle_worktree_gc(args)

        out = capsys.readouterr().out
        assert "step=1.1" not in out, (
            "BATON_WORKTREE_STALE_HOURS=10 must protect a 5h-old worktree even "
            f"from the CLI's own default. Output:\n{out}"
        )

    def test_explicit_cli_max_age_still_wins(
        self, tmp_git_repo: Path, team_context: Path,
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Control: --max-age-hours must keep overriding everything."""
        from agent_baton.cli.commands.execution.execute import _handle_worktree_gc

        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id="task-cli-explicit", step_id="1.1",
                            base_branch="main")
        _backdate_manifest(handle, hours=5)

        monkeypatch.setenv("BATON_WORKTREE_STALE_HOURS", "10")
        monkeypatch.chdir(tmp_git_repo)
        args = _execute_parser().parse_args(
            ["execute", "worktree-gc", "--dry-run", "--max-age-hours", "1"]
        )
        _handle_worktree_gc(args)

        out = capsys.readouterr().out
        assert "step=1.1" in out, (
            f"explicit --max-age-hours 1 must reclaim a 5h-old worktree; got:\n{out}"
        )

    @pytest.mark.parametrize("env_hours", [None, "6"])
    def test_takeover_error_quotes_the_effective_threshold(
        self, tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch,
        env_hours: str | None,
    ) -> None:
        """The takeover hint must not advertise the retired 72h default."""
        from agent_baton.core.engine.takeover import (
            TakeoverSession,
            TakeoverWorktreeMissingError,
        )

        if env_hours is None:
            monkeypatch.delenv("BATON_WORKTREE_STALE_HOURS", raising=False)
        else:
            monkeypatch.setenv("BATON_WORKTREE_STALE_HOURS", env_hours)

        effective = WorktreeManager._get_default_stale_hours()
        session = TakeoverSession(
            WorktreeManager(project_root=tmp_git_repo), "task-no-wt"
        )

        with pytest.raises(TakeoverWorktreeMissingError) as excinfo:
            session.resolve_handle("1.1")

        message = str(excinfo.value)
        assert "72" not in message, (
            "the takeover hint still advertises a 72h GC window that no code "
            f"path uses; message={message!r}"
        )
        assert str(effective) in message, (
            "the takeover hint must quote the threshold actually in force "
            f"({effective}h); message={message!r}"
        )


class TestStaleThresholdDocumentation:
    """Documentation-consistency (doc text is the subject, not a behaviour proxy).

    RW-3.4 names two prose sites that still promise 72h.  There is no runtime
    behaviour to observe for prose, so these assertions read the prose.
    """

    def test_worktree_manager_module_docstring_matches_runtime_default(self) -> None:
        from agent_baton.core.engine import worktree_manager as wm

        doc = wm.__doc__ or ""
        assert "72" not in doc, (
            "worktree_manager's module docstring still documents a 72h stale "
            f"window; the code uses {WorktreeManager._get_default_stale_hours()}h"
        )

    def test_pillar_doc_matches_runtime_default(self) -> None:
        doc_path = _REPO_ROOT / "docs" / "pillars" / "right-agent-right-time.md"
        text = doc_path.read_text("utf-8")

        assert "72" not in text, (
            f"{doc_path} still documents a 72-hour worktree GC window; the code "
            f"reclaims after {WorktreeManager._get_default_stale_hours()}h"
        )


# ---------------------------------------------------------------------------
# Folded in from the same review: fold must not land work on the wrong branch
# ---------------------------------------------------------------------------


class TestFoldRefusesWhenCanonicalRepoLeftTheBaseBranch:
    """``git merge`` in the canonical repo integrates into whatever is checked
    out.  If that is no longer ``handle.base_branch``, folding would silently
    graft the agent's work onto an unrelated branch, and the recorded
    ``base_branch`` head would never advance."""

    @pytest.mark.parametrize("strategy", ["rebase", "merge"])
    def test_fold_raises_when_canonical_head_moved(
        self, tmp_git_repo: Path, strategy: str
    ) -> None:
        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id=f"task-branch-drift-{strategy}", step_id="1.1",
                            base_branch="main")
        agent_sha = _commit_file(handle.path, "agent.txt", "agent output\n")

        # A human (or another orchestrator) checks out a different branch.
        assert _git(["checkout", "-b", "sidebranch"], cwd=tmp_git_repo).returncode == 0

        with pytest.raises(WorktreeFoldError):
            mgr.fold_back(handle, commit_hash=agent_sha, strategy=strategy)

    @pytest.mark.parametrize("strategy", ["rebase", "merge"])
    def test_agent_work_does_not_land_on_the_unrelated_branch(
        self, tmp_git_repo: Path, strategy: str
    ) -> None:
        mgr = WorktreeManager(project_root=tmp_git_repo)
        handle = mgr.create(task_id=f"task-branch-drift2-{strategy}", step_id="1.1",
                            base_branch="main")
        agent_sha = _commit_file(handle.path, "agent.txt", "agent output\n")
        _git(["checkout", "-b", "sidebranch"], cwd=tmp_git_repo)

        try:
            mgr.fold_back(handle, commit_hash=agent_sha, strategy=strategy)
        except WorktreeFoldError:
            pass

        shown = _git(["show", "sidebranch:agent.txt"], cwd=tmp_git_repo)
        assert shown.returncode != 0, (
            "fold_back grafted the agent's commit onto 'sidebranch', which is "
            "not the handle's base_branch ('main')"
        )
        assert handle.path.is_dir(), (
            "the worktree must be retained when the fold is refused"
        )
