"""Manager-mode execution end-to-end tests (PRD Milestone 9 / Wave 4 Task 13).

Drives the real ``ExecutionEngine`` (no live Claude -- every step result is
recorded programmatically, mirroring ``tests/test_gate_retry.py`` and
``tests/test_bead_signal_wiring.py``'s harness patterns) over a hand-built
manager-mode plan whose PMO sidecars are produced by the real
``ManagerModePlanner`` (mirrors ``tests/manager/test_manager_mode_planner.py``
-- the 7-stage ``IntelligentPlanner`` pipeline is not needed since
``ManagerModePlanner`` is a pure post-processor over an already-built
``MachinePlan``).

Required cases (docs/internal/manager-mode-pmo-plan.md Task 13 / PRD §16
Milestone 9):

- dispatch prompt includes scope contract + context bundle (with pack refs)
- non-manager dispatch prompt is unaffected (direct engine-level check,
  complementing the dispatcher-level golden snapshot in
  tests/engine/test_manager_context_prompt.py)
- phase completion writes handoff with all required sections when configured
- injected adversarial-review step is the next DISPATCH after phase steps
- manager report updates after phase completion
- scope-expansion signal routes per policy (allow_with_note / queue_for_manager
  / block)
- complete() writes the final manager report
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.core.events.bus import EventBus
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.manager.planner import ManagerModePlanner
from agent_baton.core.orchestration.knowledge_registry import KnowledgeRegistry
from agent_baton.core.storage.sqlite_backend import SqliteStorage
from agent_baton.models.execution import ActionType, MachinePlan, PlanPhase, PlanStep

# ---------------------------------------------------------------------------
# Plan builders
# ---------------------------------------------------------------------------


def _single_phase_plan(task_id: str, *, manager_mode: bool) -> MachinePlan:
    """One phase, one regular step. ``ManagerConfig()`` defaults
    (adversarial_review="always" for both phase + project completion)
    inject a phase-level review step AND a final review step onto this
    single phase -- see agent_baton.core.manager.phase_policy.apply()."""
    return MachinePlan(
        task_id=task_id,
        task_summary="Add a reporting endpoint with tests and docs",
        task_type="feature",
        complexity="medium",
        detected_stack="python",
        risk_level="LOW",
        manager_mode=manager_mode,
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


def _routing_plan(task_id: str) -> MachinePlan:
    """One phase, one step, no review injection needed -- used by the
    scope-expansion routing tests, which only exercise record_step_result,
    never a full phase-completion pass."""
    return MachinePlan(
        task_id=task_id,
        task_summary="Routing test plan",
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
                        task_description="Implement the thing.",
                        step_type="developing",
                    ),
                ],
            ),
        ],
    )


def _build_manager_sidecars(plan: MachinePlan, project_root: Path, ctx_dir: Path) -> None:
    """Run the real ManagerModePlanner post-processor over *plan*, writing
    every PMO sidecar (including injecting review steps -- see
    PhasePolicyApplier) and mutating *plan* in place, mirroring
    tests/manager/test_manager_mode_planner.py."""
    planner = ManagerModePlanner(
        ManagerConfig(),
        project_root=project_root,
        team_context_dir=ctx_dir,
        knowledge_registry=KnowledgeRegistry(),
    )
    planner.build_and_write(plan, plan.task_summary)


# ---------------------------------------------------------------------------
# Engine construction (in-memory fake bead store -- avoids a real ``bd``
# subprocess call, which is unreliable across platforms/CI images; the
# fake supports every BeadStore method the executor calls: write/query
# with task_id/step_id/bead_type/status filtering, increment_retrieval_count,
# update_quality_score. Mirrors the fakes in tests/test_synthesize_beads.py
# and tests/test_executable_beads.py, extended with filter-aware query()).
# ---------------------------------------------------------------------------


class _FakeBeadStore:
    def __init__(self) -> None:
        self._beads: dict[str, "Bead"] = {}

    def write(self, bead) -> str:
        self._beads[bead.bead_id] = bead
        return bead.bead_id

    def read(self, bead_id: str):
        return self._beads.get(bead_id)

    def query(
        self,
        *,
        task_id: str | None = None,
        step_id: str | None = None,
        bead_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
        **_kw,
    ) -> list:
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


def _engine_with_fake_beads(
    ctx_dir: Path, task_id: str, monkeypatch: pytest.MonkeyPatch
) -> tuple[ExecutionEngine, _FakeBeadStore]:
    ctx_dir.mkdir(parents=True, exist_ok=True)
    db_path = ctx_dir / "baton.db"
    db_path.touch()
    fake_store = _FakeBeadStore()

    def _patched_make_bead_store(path, *, soul_router=None, repo_root=None):
        return fake_store

    storage = SqliteStorage(db_path)
    monkeypatch.setattr(
        "agent_baton.core.engine.bead_backend.make_bead_store",
        _patched_make_bead_store,
    )
    engine = ExecutionEngine(
        team_context_root=ctx_dir,
        bus=EventBus(),
        storage=storage,
        task_id=task_id,
    )
    return engine, fake_store


def _engine_with_no_bead_store(
    ctx_dir: Path, task_id: str, monkeypatch: pytest.MonkeyPatch
) -> ExecutionEngine:
    """Engine construction with ``self._bead_store`` forced to ``None``
    (I2 regression fixture) -- proves the M9 phase-artifact hook
    (handoff + manager-report refresh) is wired independently of bead-graph
    synthesis, which early-returns immediately when no bead store is
    available (see ``ExecutionEngine._synthesize_beads_post_phase``)."""
    ctx_dir.mkdir(parents=True, exist_ok=True)
    db_path = ctx_dir / "baton.db"
    db_path.touch()

    def _patched_make_bead_store(path, *, soul_router=None, repo_root=None):
        return None

    storage = SqliteStorage(db_path)
    monkeypatch.setattr(
        "agent_baton.core.engine.bead_backend.make_bead_store",
        _patched_make_bead_store,
    )
    return ExecutionEngine(
        team_context_root=ctx_dir,
        bus=EventBus(),
        storage=storage,
        task_id=task_id,
    )


def _paths(project_root: Path, task_id: str) -> ManagerArtifactPaths:
    return ManagerArtifactPaths(project_root / ".claude" / "team-context", task_id)


def _write_baton_yaml(project_root: Path, scope_expansion_policy: str) -> None:
    yaml_dir = project_root / ".claude"
    yaml_dir.mkdir(parents=True, exist_ok=True)
    (yaml_dir / "baton.yaml").write_text(
        "version: 1\n"
        "scoping:\n"
        f"  scope_expansion_policy: {scope_expansion_policy}\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Dispatch prompt tests
# ---------------------------------------------------------------------------


class TestDispatchPromptManagerSidecars:
    def test_dispatch_prompt_includes_scope_contract_and_context_bundle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-m9-dispatch"
        plan = _single_phase_plan(task_id, manager_mode=True)
        ctx_dir = tmp_path / ".claude" / "team-context"
        _build_manager_sidecars(plan, tmp_path, ctx_dir)

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        action = engine.start(plan)

        assert action.action_type == ActionType.DISPATCH
        assert action.step_id == "1.1"
        prompt = action.delegation_prompt
        assert "## Scope Contract" in prompt
        assert "## Context Bundle" in prompt
        # The scope-expansion signal-format hint lives in the manager-gated
        # section, not the shared _SIGNALS_BLOCK (binding constraint).
        assert "SCOPE_EXPANSION: <path> — <reason>" in prompt

    def test_dispatch_prompt_context_bundle_has_pack_refs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The role card for backend-engineer requires
        knowledge_packs.required_for_code_steps packs by default
        (coding-conventions, testing-strategy) -- even with an empty
        registry (no packs found on disk), the bundle still records pack
        *names* as required/missing references, which is what the prompt
        section renders (never full doc bodies)."""
        task_id = "task-m9-dispatch-packs"
        plan = _single_phase_plan(task_id, manager_mode=True)
        ctx_dir = tmp_path / ".claude" / "team-context"
        _build_manager_sidecars(plan, tmp_path, ctx_dir)

        paths = _paths(tmp_path, task_id)
        bundle_data = json.loads(paths.context_bundle("1.1").read_text(encoding="utf-8"))
        assert bundle_data["knowledge_packs"], "fixture assumption: step has pack refs"

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        action = engine.start(plan)
        prompt = action.delegation_prompt
        assert "Knowledge packs:" in prompt
        for pack in bundle_data["knowledge_packs"]:
            assert pack["name"] in prompt


class TestNonManagerDispatchPromptUnchanged:
    def test_non_manager_prompt_has_no_manager_sections(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-m9-non-manager"
        plan = _single_phase_plan(task_id, manager_mode=False)
        ctx_dir = tmp_path / ".claude" / "team-context"

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        action = engine.start(plan)

        prompt = action.delegation_prompt
        assert "## Scope Contract" not in prompt
        assert "## Context Bundle" not in prompt
        assert "SCOPE_EXPANSION: <path>" not in prompt

    def test_manager_mode_off_ignores_sidecars_present_on_disk(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Direct engine-level check complementing the dispatcher-level
        golden snapshot (tests/engine/test_manager_context_prompt.py):
        even when scope-contract/context-bundle sidecars exist on disk for
        this exact step_id (e.g. left over from an earlier manager-mode
        plan/run reusing the same task_id), a manager_mode=False plan must
        never load or inject them -- the ``if state.plan.manager_mode``
        gate in _dispatch_action is the only thing standing between "sidecar
        exists" and "prompt changes", so this proves that gate, not just
        sidecar absence."""
        task_id = "task-m9-stale-sidecars"
        manager_plan = _single_phase_plan(task_id, manager_mode=True)
        ctx_dir = tmp_path / ".claude" / "team-context"
        _build_manager_sidecars(manager_plan, tmp_path, ctx_dir)
        paths = _paths(tmp_path, task_id)
        assert paths.scope_contract("1.1", "md").is_file()
        assert paths.context_bundle("1.1").is_file()

        # Fresh engine/plan, SAME task_id and step_id, manager_mode=False.
        non_manager_plan = _single_phase_plan(task_id, manager_mode=False)
        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        action = engine.start(non_manager_plan)

        prompt = action.delegation_prompt
        assert "## Scope Contract" not in prompt
        assert "## Context Bundle" not in prompt


# ---------------------------------------------------------------------------
# Phase completion: handoff + report + review-step dispatch
# ---------------------------------------------------------------------------


def _drive_through_phase_one(
    engine: ExecutionEngine, plan: MachinePlan
) -> list:
    """Start the engine and record every step in phase 1 to completion
    (regular step, phase review, final review -- ManagerConfig() defaults
    inject both onto a single-phase plan). Returns the ordered list of
    DISPATCH actions observed, so callers can assert dispatch order."""
    dispatched: list = []
    action = engine.start(plan)
    while action.action_type == ActionType.DISPATCH:
        dispatched.append(action)
        engine.record_step_result(
            step_id=action.step_id,
            agent_name=action.agent_name,
            status="complete",
            outcome=f"Completed {action.step_id}.",
            estimated_tokens=500,
            duration_seconds=1.0,
        )
        action = engine.next_action()
    return dispatched, action


class TestPhaseCompletionHooks:
    def test_review_step_is_next_dispatch_after_phase_steps(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-m9-review-order"
        plan = _single_phase_plan(task_id, manager_mode=True)
        ctx_dir = tmp_path / ".claude" / "team-context"
        _build_manager_sidecars(plan, tmp_path, ctx_dir)

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        dispatched, final_action = _drive_through_phase_one(engine, plan)

        dispatched_step_ids = [a.step_id for a in dispatched]
        assert dispatched_step_ids[0] == "1.1"
        assert "review-1" in dispatched_step_ids
        assert dispatched_step_ids.index("review-1") == 1, (
            "the phase-level review step must be the next DISPATCH "
            "immediately after the phase's regular step(s)"
        )
        # Single-phase plan + project_completion.adversarial_review="always"
        # (ManagerConfig() default) also injects a final review step.
        assert "review-1-final" in dispatched_step_ids
        assert final_action.action_type == ActionType.COMPLETE

    def test_phase_completion_writes_handoff_with_required_sections(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-m9-handoff"
        plan = _single_phase_plan(task_id, manager_mode=True)
        ctx_dir = tmp_path / ".claude" / "team-context"
        _build_manager_sidecars(plan, tmp_path, ctx_dir)

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        _drive_through_phase_one(engine, plan)

        paths = _paths(tmp_path, task_id)
        handoff_path = paths.phase_handoff(1)
        assert handoff_path.is_file(), "handoffs/phase-1-handoff.md must exist"
        text = handoff_path.read_text(encoding="utf-8")
        for section in (
            "## Completed Work",
            "## Files Changed",
            "## Decisions Made",
            "## Unresolved Questions",
            "## Knowledge Gaps",
            "## Scope Changes",
            "## Next Phase Recommendations",
        ):
            assert section in text, f"missing required handoff section: {section}"
        assert "1.1" in text

    def test_handoff_written_when_bead_store_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """I2 regression: ``_synthesize_beads_post_phase`` early-returns
        immediately (before ever reaching the M9 handoff/report block)
        when ``self._bead_store is None`` -- a purely bead-graph-synthesis
        concern that must never suppress the PMO handoff. The M9 phase
        hook must be invoked unconditionally at the phase boundary, not
        nested inside bead-graph synthesis."""
        task_id = "task-m9-handoff-no-beads"
        plan = _single_phase_plan(task_id, manager_mode=True)
        ctx_dir = tmp_path / ".claude" / "team-context"
        _build_manager_sidecars(plan, tmp_path, ctx_dir)

        engine = _engine_with_no_bead_store(ctx_dir, task_id, monkeypatch)
        assert engine._bead_store is None
        _drive_through_phase_one(engine, plan)

        paths = _paths(tmp_path, task_id)
        assert paths.phase_handoff(1).is_file(), (
            "handoffs/phase-1-handoff.md must exist even when the bead "
            "store is unavailable"
        )
        assert paths.manager_report.is_file(), (
            "manager-report.md must be refreshed even when the bead "
            "store is unavailable"
        )

    def test_no_handoff_written_when_manager_mode_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-m9-no-handoff"
        plan = _single_phase_plan(task_id, manager_mode=False)
        ctx_dir = tmp_path / ".claude" / "team-context"

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        action = engine.start(plan)
        engine.record_step_result(
            step_id=action.step_id,
            agent_name=action.agent_name,
            status="complete",
            outcome="Done.",
        )
        engine.next_action()

        paths = _paths(tmp_path, task_id)
        assert not paths.phase_handoff(1).exists()
        assert not paths.manager_report.exists()

    def test_manager_report_updates_after_phase_completion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-m9-report-refresh"
        plan = _single_phase_plan(task_id, manager_mode=True)
        ctx_dir = tmp_path / ".claude" / "team-context"
        _build_manager_sidecars(plan, tmp_path, ctx_dir)
        paths = _paths(tmp_path, task_id)

        # Before execution: no manager-report.md yet (only written by the
        # execution hooks, never by planning).
        assert not paths.manager_report.exists()

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        _drive_through_phase_one(engine, plan)

        assert paths.manager_report.is_file()
        text = paths.manager_report.read_text(encoding="utf-8")
        assert f"# Manager Report: {task_id}" in text
        assert "## Phase / Workstream Progress" in text
        # All three phase-1 steps (1.1, review-1, review-1-final) completed.
        assert "complete" in text.lower()


# ---------------------------------------------------------------------------
# Scope-expansion signal routing
# ---------------------------------------------------------------------------


class TestScopeExpansionRouting:
    def test_block_policy_fails_the_step_visibly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-m9-scope-block"
        plan = _routing_plan(task_id)
        ctx_dir = tmp_path / ".claude" / "team-context"
        _write_baton_yaml(tmp_path, "block")

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        engine.start(plan)
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome=(
                "Implemented the base service.\n"
                "SCOPE_EXPANSION: app/auth/session.py — session metadata needed\n"
            ),
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "failed"
        assert "app/auth/session.py" in result.error
        assert "block" in result.error.lower()
        # C1 regression: manager-mode scope-expansion signals must route
        # EXCLUSIVELY through manager_scope_signal -- the pre-existing
        # free-text adaptive-replanner queue (bead_signal.py) must never
        # also pick up the same outcome text (double-routing).
        assert state.pending_scope_expansions == []

    def test_allow_with_note_writes_warning_bead(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-m9-scope-note"
        plan = _routing_plan(task_id)
        ctx_dir = tmp_path / ".claude" / "team-context"
        _write_baton_yaml(tmp_path, "allow_with_note")

        engine, bd_store = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        engine.start(plan)
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome=(
                "Implemented the base service.\n"
                "SCOPE_EXPANSION: app/auth/session.py — session metadata needed\n"
            ),
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "complete", "allow_with_note must not fail the step"

        warnings = bd_store.query(task_id=task_id, bead_type="warning")
        scope_warnings = [b for b in warnings if "scope-expansion" in (b.tags or [])]
        assert scope_warnings, "expected a warning bead noting the scope expansion"
        assert "app/auth/session.py" in scope_warnings[0].content
        # C1 regression: allow_with_note must record + proceed, NOT also
        # queue the signal for the adaptive replanner to auto-amend later.
        assert state.pending_scope_expansions == []

    def test_queue_for_manager_creates_decision_packet(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-m9-scope-queue"
        plan = _routing_plan(task_id)
        ctx_dir = tmp_path / ".claude" / "team-context"
        _write_baton_yaml(tmp_path, "queue_for_manager")

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        engine.start(plan)
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome=(
                "Implemented the base service.\n"
                "SCOPE_EXPANSION: app/auth/session.py — session metadata needed\n"
            ),
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "complete", "queue_for_manager must not fail the step"

        paths = _paths(tmp_path, task_id)
        assert paths.decisions_dir.is_dir()
        decision_files = list(paths.decisions_dir.glob("*.md"))
        assert decision_files, "expected a decision packet .md file"
        packet_text = decision_files[0].read_text(encoding="utf-8")
        assert "Scope Expansion" in packet_text
        assert "app/auth/session.py" in packet_text

        log_lines = paths.decision_log.read_text(encoding="utf-8").splitlines()
        entries = [json.loads(line) for line in log_lines if line.strip()]
        assert any(e.get("decision_type") == "scope_expansion" for e in entries)
        # C1 regression: the decision packet must be the ONLY routing
        # outcome -- the work must not ALSO be auto-planned into the next
        # phase boundary while the manager decision packet sits open.
        assert state.pending_scope_expansions == []

    def test_no_routing_when_manager_mode_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The stricter <path> — <reason> signal must be inert for
        non-manager-mode plans -- no decision packets, no warning beads,
        no step failure, regardless of any baton.yaml policy present."""
        task_id = "task-m9-scope-off"
        plan = _routing_plan(task_id)
        plan.manager_mode = False
        ctx_dir = tmp_path / ".claude" / "team-context"
        _write_baton_yaml(tmp_path, "block")

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        engine.start(plan)
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome="SCOPE_EXPANSION: app/auth/session.py — session metadata needed\n",
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "complete"

        paths = _paths(tmp_path, task_id)
        assert not paths.decisions_dir.exists()
        # C1 inverse: manager-mode routing is inert, but the PRE-EXISTING
        # free-text adaptive-replanner queueing (bead_signal.py) is
        # UNCHANGED for non-manager plans -- the same outcome text also
        # matches the loose SCOPE_EXPANSION: <description> pattern and
        # must still be queued for the next phase boundary.
        assert len(state.pending_scope_expansions) == 1
        assert state.pending_scope_expansions[0]["step_id"] == "1.1"

    def test_free_text_scope_expansion_still_queues_when_manager_mode_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C1 inverse (dedicated case): a purely free-text
        ``SCOPE_EXPANSION: <description>`` signal -- one that does NOT
        match the stricter manager-mode ``<path> — <reason>`` shape at
        all -- must still queue into ``state.pending_scope_expansions``
        for the pre-existing adaptive-replanner to pick up at the next
        phase boundary when ``manager_mode=False``. Proves the C1 fix
        only gates manager-mode plans, never touching the non-manager
        replanner behavior."""
        task_id = "task-m9-freetext-non-manager"
        plan = _routing_plan(task_id)
        plan.manager_mode = False
        ctx_dir = tmp_path / ".claude" / "team-context"
        _write_baton_yaml(tmp_path, "block")  # policy must be irrelevant here

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        engine.start(plan)
        engine.record_step_result(
            step_id="1.1",
            agent_name="backend-engineer",
            status="complete",
            outcome="SCOPE_EXPANSION: needed to touch the auth module too\n",
        )

        state = engine._load_state()
        result = state.get_step_result("1.1")
        assert result.status == "complete"
        assert len(state.pending_scope_expansions) == 1
        assert state.pending_scope_expansions[0]["step_id"] == "1.1"
        assert "auth module" in state.pending_scope_expansions[0]["description"]

        paths = _paths(tmp_path, task_id)
        assert not paths.decisions_dir.exists()


# ---------------------------------------------------------------------------
# complete() final report
# ---------------------------------------------------------------------------


class TestCompleteWritesFinalReport:
    def test_complete_writes_final_manager_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-m9-complete"
        plan = _single_phase_plan(task_id, manager_mode=True)
        ctx_dir = tmp_path / ".claude" / "team-context"
        _build_manager_sidecars(plan, tmp_path, ctx_dir)

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        _dispatched, final_action = _drive_through_phase_one(engine, plan)
        assert final_action.action_type == ActionType.COMPLETE

        paths = _paths(tmp_path, task_id)
        before_mtime = paths.manager_report.stat().st_mtime_ns
        before_text = paths.manager_report.read_text(encoding="utf-8")
        assert "complete" not in before_text.lower().split("## status")[1].split("##")[0]

        summary = engine.complete()

        assert paths.manager_report.is_file()
        after_mtime = paths.manager_report.stat().st_mtime_ns
        after_text = paths.manager_report.read_text(encoding="utf-8")
        assert after_mtime >= before_mtime
        assert "complete" in after_text.lower().split("## status")[1].split("##")[0]
        assert "Manager report:" in summary
        assert str(paths.manager_report) in summary

    def test_complete_is_noop_for_non_manager_plan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task_id = "task-m9-complete-off"
        plan = _single_phase_plan(task_id, manager_mode=False)
        ctx_dir = tmp_path / ".claude" / "team-context"

        engine, _ = _engine_with_fake_beads(ctx_dir, task_id, monkeypatch)
        action = engine.start(plan)
        engine.record_step_result(
            step_id=action.step_id,
            agent_name=action.agent_name,
            status="complete",
            outcome="Done.",
        )
        engine.next_action()

        summary = engine.complete()

        paths = _paths(tmp_path, task_id)
        assert not paths.manager_report.exists()
        assert "Manager report:" not in summary
