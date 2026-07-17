"""Integration tests for the team runtime-contract CLI verbs (Phase 4 4.2):
``baton team list|claim|update|send|read``.

Exercises the CLI handler functions directly (argparse.Namespace in,
stdout/SystemExit out) against a real ``ExecutionEngine`` + SQLite-backed
``TeamRegistry`` -- only the bead store is faked (the ``bd`` binary is not
installed in this sandbox; see ``tests/test_team_tools.py``'s
``_FakeBeadStore`` for the established hermetic pattern this mirrors).

The fake bead store is keyed by db_path and shared across separate
``ExecutionEngine``/CLI-handler invocations within a test, which is what
lets "restart survives" be exercised meaningfully: each CLI call in these
tests constructs a brand-new ``ExecutionEngine`` (exactly as the real CLI
process does on every invocation), and state must be visible across that
boundary purely through the persisted backend -- no shared Python object
identity is relied upon except the keyed-by-db-path fake store standing in
for the real (equally persistent) ``bd``-backed store.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

import agent_baton.cli.commands.team_cmd as team_cmd
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.bead import Bead
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep, TeamMember
from agent_baton.utils.time import utcnow_zulu as _utcnow


# ---------------------------------------------------------------------------
# Hermetic bead store -- keyed by db_path so separate ExecutionEngine
# constructions (simulating separate CLI process invocations / a restart)
# share the same persisted data, matching real BdBeadStore's durability
# without requiring the external `bd` binary.
# ---------------------------------------------------------------------------


class _FakeBeadStore:
    def __init__(self) -> None:
        self._beads: dict[str, Bead] = {}

    def write(self, bead: Bead) -> str:
        self._beads[bead.bead_id] = bead
        return bead.bead_id

    def read(self, bead_id: str) -> Bead | None:
        return self._beads.get(bead_id)

    def close(self, bead_id: str, summary: str) -> None:
        bead = self._beads.get(bead_id)
        if bead is None:
            return
        bead.status = "closed"
        bead.closed_at = _utcnow()

    def query(
        self,
        *,
        task_id: str | None = None,
        agent_name: str | None = None,
        bead_type: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        limit: int = 100,
    ) -> list[Bead]:
        out: list[Bead] = []
        for bead in self._beads.values():
            if task_id is not None and bead.task_id != task_id:
                continue
            if agent_name is not None and bead.agent_name != agent_name:
                continue
            if bead_type is not None and bead.bead_type != bead_type:
                continue
            if status is not None and bead.status != status:
                continue
            if tags and not set(tags).issubset(set(bead.tags or [])):
                continue
            out.append(bead)
        out.sort(key=lambda b: b.created_at, reverse=True)
        return out[:limit]


_FAKE_STORES: dict[str, _FakeBeadStore] = {}


def _fake_make_bead_store(db_path: Path, **_kwargs) -> _FakeBeadStore:
    return _FAKE_STORES.setdefault(str(db_path), _FakeBeadStore())


@pytest.fixture(autouse=True)
def _patch_bead_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    _FAKE_STORES.clear()
    monkeypatch.setattr(
        "agent_baton.core.engine.bead_backend.make_bead_store",
        _fake_make_bead_store,
    )


@pytest.fixture
def context_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / ".claude" / "team-context"
    root.mkdir(parents=True)
    # Bypass the git/cwd walk entirely (this is the resolution path a
    # worktree-isolated team member's subprocess uses -- see
    # _resolve_runtime_context_root's docstring).
    monkeypatch.setenv("BATON_TEAM_CONTEXT_ROOT", str(root))
    monkeypatch.delenv("BATON_DB_PATH", raising=False)
    monkeypatch.delenv("BATON_TASK_ID", raising=False)
    monkeypatch.delenv("BATON_TEAM_MEMBER_ID", raising=False)
    return root


def _team_plan() -> MachinePlan:
    return MachinePlan(
        task_id="task-cli",
        task_summary="cli team",
        phases=[PlanPhase(
            phase_id=1, name="impl",
            steps=[PlanStep(
                step_id="1.1", agent_name="team",
                task_description="team a",
                team=[
                    TeamMember(member_id="1.1.a", agent_name="architect", role="lead"),
                    TeamMember(member_id="1.1.b", agent_name="be", role="implementer"),
                ],
            )],
        )],
    )


@pytest.fixture
def bootstrapped_task(context_root: Path) -> str:
    """Start a plan + register the team, exactly like a real ``baton execute
    start`` would, so the CLI verbs have a team/task to operate against."""
    from agent_baton.core.engine.executor import ExecutionEngine

    storage = SqliteStorage(context_root / "baton.db")
    engine = ExecutionEngine(team_context_root=context_root, storage=storage)
    engine.start(_team_plan())
    engine.next_actions()  # registers team-1.1 in TeamRegistry
    return "task-cli"


def _ns(**kwargs) -> argparse.Namespace:
    defaults = dict(
        task_id="task-cli", team_id="team-1.1", member_id=None,
        json_output=True, resource="tasks", status=None, limit=100,
        list_all=False, task_bead_id=None, allow_reassign=False,
        title=None, detail="", outcome="", idempotency_key=None,
        parent_task_bead_id=None, from_team=None, from_member=None,
        to_team=None, to_member=None, subject=None, body=None, no_ack=False,
    )
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# Happy path: create, claim, complete, list, send, read -- and restart
# durability across independent ExecutionEngine constructions.
# ---------------------------------------------------------------------------


class TestHappyPathAndRestartDurability:
    def test_update_create_then_list_survives_new_engine(
        self, bootstrapped_task: str, capsys: pytest.CaptureFixture,
    ) -> None:
        team_cmd._handle_team_update(_ns(
            member_id="1.1.a", title="write adapter", detail="d",
        ))
        created = json.loads(capsys.readouterr().out)
        assert created["status"] == "open"
        task_bead_id = created["task_bead_id"]

        # Simulate a restart: brand-new handler call, brand-new
        # ExecutionEngine constructed inside _handle_team_list.
        team_cmd._handle_team_list(_ns(member_id="1.1.a"))
        listed = json.loads(capsys.readouterr().out)
        assert [t["task_bead_id"] for t in listed] == [task_bead_id]
        assert listed[0]["status"] == "open"

    def test_claim_then_complete_survives_restart(
        self, bootstrapped_task: str, capsys: pytest.CaptureFixture,
    ) -> None:
        team_cmd._handle_team_update(_ns(member_id="1.1.a", title="t"))
        task_bead_id = json.loads(capsys.readouterr().out)["task_bead_id"]

        team_cmd._handle_team_claim(_ns(
            member_id="1.1.b", task_bead_id=task_bead_id,
        ))
        claimed = json.loads(capsys.readouterr().out)
        assert claimed == {"task_bead_id": task_bead_id, "claimed_by": "1.1.b"}

        # New engine (restart) sees the claim.
        team_cmd._handle_team_list(_ns(member_id="1.1.b"))
        listed = json.loads(capsys.readouterr().out)
        assert listed[0]["claimed_by"] == "1.1.b"

        team_cmd._handle_team_update(_ns(
            member_id="1.1.b", task_bead_id=task_bead_id,
            status="complete", outcome="done",
        ))
        completed = json.loads(capsys.readouterr().out)
        assert completed == {"task_bead_id": task_bead_id, "status": "done"}

        team_cmd._handle_team_list(_ns(member_id="1.1.a", status="done"))
        done_list = json.loads(capsys.readouterr().out)
        assert done_list[0]["task_bead_id"] == task_bead_id

    def test_send_then_read_acks_and_survives_restart(
        self, bootstrapped_task: str, capsys: pytest.CaptureFixture,
    ) -> None:
        team_cmd._handle_team_send(_ns(
            from_team="team-1.1", member_id="1.1.a",
            to_team="team-1.1", to_member="1.1.b",
            subject="hello", body="world",
        ))
        sent = json.loads(capsys.readouterr().out)
        assert sent["message_bead_id"]

        # New engine reads the unread message and acks it by default.
        team_cmd._handle_team_read(_ns(member_id="1.1.b"))
        first = json.loads(capsys.readouterr().out)
        assert len(first) == 1
        assert first[0]["subject"] == "hello"

        # Another new engine confirms the ack persisted -- not re-delivered.
        team_cmd._handle_team_read(_ns(member_id="1.1.b"))
        second = json.loads(capsys.readouterr().out)
        assert second == []

    def test_member_id_resolved_from_env_when_flag_omitted(
        self, bootstrapped_task: str, capsys: pytest.CaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BATON_TEAM_MEMBER_ID", "1.1.a")
        team_cmd._handle_team_update(_ns(member_id=None, title="via env"))
        created = json.loads(capsys.readouterr().out)
        assert created["status"] == "open"

    def test_list_all_bypasses_member_filter_and_env(
        self, bootstrapped_task: str, capsys: pytest.CaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        team_cmd._handle_team_update(_ns(member_id="1.1.a", title="t1"))
        t1 = json.loads(capsys.readouterr().out)["task_bead_id"]
        team_cmd._handle_team_update(_ns(member_id="1.1.b", title="t2"))
        capsys.readouterr()
        # 1.1.b claims t1 -- ordinarily invisible to 1.1.a's filtered view.
        team_cmd._handle_team_claim(_ns(member_id="1.1.b", task_bead_id=t1))
        capsys.readouterr()

        # Even with BATON_TEAM_MEMBER_ID set, --all forces the unfiltered
        # lead/observer-wide view: both tasks are visible.
        monkeypatch.setenv("BATON_TEAM_MEMBER_ID", "1.1.a")
        team_cmd._handle_team_list(_ns(member_id=None, list_all=True))
        listed = json.loads(capsys.readouterr().out)
        assert len(listed) == 2
        assert t1 in {row["task_bead_id"] for row in listed}

    def test_human_readable_table_output_without_json_flag(
        self, bootstrapped_task: str, capsys: pytest.CaptureFixture,
    ) -> None:
        team_cmd._handle_team_update(_ns(
            member_id="1.1.a", title="t", json_output=False,
        ))
        out = capsys.readouterr().out.strip()
        assert "task_bead_id=" in out
        assert "status=open" in out
        # Not JSON.
        with pytest.raises(json.JSONDecodeError):
            json.loads(out)


# ---------------------------------------------------------------------------
# Failure taxonomy -- docs/internal/team-runtime-contract.md §7.3.
# ---------------------------------------------------------------------------


class TestFailureTaxonomyExitCodes:
    def test_unknown_team_id_exits_usage(
        self, bootstrapped_task: str,
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            team_cmd._handle_team_list(_ns(
                member_id="1.1.a", team_id="team-does-not-exist",
            ))
        assert exc_info.value.code == team_cmd.EXIT_USAGE

    def test_missing_member_id_exits_usage(
        self, bootstrapped_task: str,
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            team_cmd._handle_team_claim(_ns(
                member_id=None, task_bead_id="bd-x",
            ))
        assert exc_info.value.code == team_cmd.EXIT_USAGE

    def test_concurrency_conflict_exits_4(
        self, bootstrapped_task: str, capsys: pytest.CaptureFixture,
    ) -> None:
        team_cmd._handle_team_update(_ns(member_id="1.1.a", title="t"))
        task_bead_id = json.loads(capsys.readouterr().out)["task_bead_id"]
        team_cmd._handle_team_claim(_ns(member_id="1.1.a", task_bead_id=task_bead_id))
        capsys.readouterr()

        with pytest.raises(SystemExit) as exc_info:
            team_cmd._handle_team_claim(_ns(
                member_id="1.1.b", task_bead_id=task_bead_id,
            ))
        assert exc_info.value.code == team_cmd.EXIT_CONCURRENCY_CONFLICT

    def test_unknown_team_id_containing_unavailable_is_usage_not_backend(
        self, bootstrapped_task: str,
    ) -> None:
        """Regression (phase 4 review): the exit-5 mapping must branch on the
        typed TeamBackendUnavailableError, not sniff "unavailable" in the
        message — a user-supplied team_id containing that word is a plain
        typo (exit 2, "fix and retry"), not "environment broken, stop
        retrying" (exit 5)."""
        with pytest.raises(SystemExit) as exc_info:
            team_cmd._handle_team_list(_ns(
                member_id="1.1.a", team_id="team-unavailable",
            ))
        assert exc_info.value.code == team_cmd.EXIT_USAGE

    def test_backend_unavailable_exits_5(
        self, context_root: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # No bootstrapped_task fixture here: without an active execution,
        # _require_task_id would fail first with EXIT_USAGE instead of the
        # backend-unavailable path we want to exercise, so start a plan but
        # force make_bead_store to fail like a missing `bd` binary would.
        from agent_baton.core.engine.executor import ExecutionEngine
        storage = SqliteStorage(context_root / "baton.db")
        engine = ExecutionEngine(team_context_root=context_root, storage=storage)
        engine.start(_team_plan())
        engine.next_actions()

        def _raise(*_a, **_k):
            raise RuntimeError("bd not on PATH")

        monkeypatch.setattr(
            "agent_baton.core.engine.bead_backend.make_bead_store", _raise,
        )
        with pytest.raises(SystemExit) as exc_info:
            team_cmd._handle_team_update(_ns(member_id="1.1.a", title="t"))
        assert exc_info.value.code == team_cmd.EXIT_BACKEND_UNAVAILABLE
