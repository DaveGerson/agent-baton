"""Tests for :mod:`agent_baton.core.manager.scope` (M2 -- scope map).

See docs/internal/manager-mode-pmo-plan.md Wave 1 / Task 5 and PRD §4.1 /
§10.2 / §16 Milestone 2.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.engine.planning.scope_contract import ScopeContractError
from agent_baton.core.manager.charter import ProjectCharterBuilder
from agent_baton.core.manager.scope import ScopeMapBuilder
from agent_baton.models.execution import MachinePlan, PlanGate, PlanPhase, PlanStep


def _make_plan(
    *,
    task_id: str = "task-scope-1",
    task_summary: str = "Add a reporting endpoint with tests and docs",
    complexity: str = "medium",
    risk_level: str = "MEDIUM",
    phases: list[PlanPhase] | None = None,
) -> MachinePlan:
    if phases is None:
        phases = [
            PlanPhase(
                phase_id=1,
                name="Implementation",
                steps=[
                    PlanStep(
                        step_id="1.1",
                        agent_name="backend-engineer",
                        task_description="Build the reporting endpoint.",
                        deliverables=["reporting endpoint"],
                        allowed_paths=["app/reporting/service.py"],
                        context_files=["app/reporting/service.py"],
                    ),
                    PlanStep(
                        step_id="1.2",
                        agent_name="backend-engineer",
                        task_description="Wire the endpoint into the router.",
                        deliverables=["router wiring"],
                        allowed_paths=["app/reporting/routes.py"],
                        depends_on=["1.1"],
                    ),
                ],
                gate=PlanGate(gate_type="test", command="pytest tests/reporting -q"),
            ),
            PlanPhase(
                phase_id=2,
                name="Testing and docs",
                steps=[
                    PlanStep(
                        step_id="2.1",
                        agent_name="test-engineer",
                        task_description="Write tests for the reporting endpoint.",
                        deliverables=["test suite"],
                        allowed_paths=["tests/reporting"],
                        depends_on=["1.1"],
                    ),
                    PlanStep(
                        step_id="2.2",
                        agent_name="test-engineer",
                        task_description="Document the reporting endpoint.",
                        deliverables=["README section"],
                        allowed_paths=["docs/reporting.md"],
                        depends_on=["2.1"],
                    ),
                ],
                gate=PlanGate(gate_type="test", command="pytest -q"),
            ),
        ]
    return MachinePlan(
        task_id=task_id,
        task_summary=task_summary,
        complexity=complexity,
        risk_level=risk_level,
        task_type="feature",
        detected_stack="python",
        phases=phases,
    )


def _build_scope_map(plan: MachinePlan, config: ManagerConfig | None = None):
    config = config or ManagerConfig()
    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, Path("/nonexistent"))
    return ScopeMapBuilder(config).build(charter, plan), charter


def test_scope_map_json_round_trip() -> None:
    plan = _make_plan()
    scope_map, _charter = _build_scope_map(plan)

    reloaded = type(scope_map).from_dict(json.loads(json.dumps(scope_map.to_dict())))

    assert reloaded == scope_map


def test_one_workstream_per_phase_ids_are_stable() -> None:
    plan = _make_plan()
    scope_map, _charter = _build_scope_map(plan)

    assert [ws.id for ws in scope_map.workstreams] == ["ws-1", "ws-2"]
    assert [ws.name for ws in scope_map.workstreams] == ["Implementation", "Testing and docs"]


def test_single_phase_light_plan_yields_one_workstream() -> None:
    phases = [
        PlanPhase(
            phase_id=1,
            name="Quick fix",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Fix the bug.",
                    deliverables=["bug fix"],
                    allowed_paths=["app/bugfix.py"],
                ),
            ],
        ),
    ]
    plan = _make_plan(complexity="light", phases=phases)
    scope_map, _charter = _build_scope_map(plan)

    assert len(scope_map.workstreams) == 1


def test_owner_role_is_modal_agent_name() -> None:
    phases = [
        PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Step 1.",
                    deliverables=["a"],
                    allowed_paths=["app/a.py"],
                ),
                PlanStep(
                    step_id="1.2",
                    agent_name="backend-engineer",
                    task_description="Step 2.",
                    deliverables=["b"],
                    allowed_paths=["app/b.py"],
                ),
                PlanStep(
                    step_id="1.3",
                    agent_name="test-engineer",
                    task_description="Step 3.",
                    deliverables=["c"],
                    allowed_paths=["tests/c.py"],
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)
    scope_map, _charter = _build_scope_map(plan)

    assert scope_map.workstreams[0].owner_role == "backend-engineer"


def test_cross_phase_dependency_produces_workstream_edge() -> None:
    plan = _make_plan()
    scope_map, _charter = _build_scope_map(plan)

    # Phase 2 step "2.1" depends_on "1.1" (phase 1) -> ws-2 depends on ws-1.
    ws_by_id = {ws.id: ws for ws in scope_map.workstreams}
    assert "ws-1" in ws_by_id["ws-2"].dependencies


def test_no_explicit_cross_phase_dep_falls_back_to_previous_phase() -> None:
    phases = [
        PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Step 1.",
                    deliverables=["a"],
                    allowed_paths=["app/a.py"],
                ),
            ],
        ),
        PlanPhase(
            phase_id=2,
            name="Testing",
            steps=[
                PlanStep(
                    step_id="2.1",
                    agent_name="test-engineer",
                    task_description="Step 2 -- no depends_on.",
                    deliverables=["b"],
                    allowed_paths=["tests/b.py"],
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)
    scope_map, _charter = _build_scope_map(plan)

    ws_by_id = {ws.id: ws for ws in scope_map.workstreams}
    assert ws_by_id["ws-2"].dependencies == ["ws-1"]
    assert ws_by_id["ws-1"].dependencies == []


def test_allowed_paths_fall_back_to_charter_likely_repo_areas() -> None:
    phases = [
        PlanPhase(
            phase_id=1,
            name="Improvements",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Improve things.",
                    deliverables=["improvements"],
                    # no allowed_paths / context_files
                ),
            ],
        ),
    ]
    plan = _make_plan(task_summary="improve things", phases=phases)
    scope_map, charter = _build_scope_map(plan)

    assert charter.likely_repo_areas == []
    assert scope_map.workstreams[0].allowed_paths == []
    assert scope_map.workstreams[0].likely_paths == []


def test_scope_expansion_policy_from_config() -> None:
    plan = _make_plan()
    config = ManagerConfig.from_dict({"scoping": {"scope_expansion_policy": "block"}})
    scope_map, _charter = _build_scope_map(plan, config)

    assert scope_map.scope_expansion_policy == "block"


def test_scope_map_out_of_scope_matches_charter() -> None:
    plan = _make_plan()
    scope_map, charter = _build_scope_map(plan)

    assert scope_map.out_of_scope == charter.out_of_scope


# ---------------------------------------------------------------------------
# Phase 3 "Make scope contracts authoritative" -- deterministic derivation,
# path normalization, and strict-mode / diagnostics behavior.
# ---------------------------------------------------------------------------


def test_explicit_allowed_paths_are_normalized() -> None:
    """Backslash-authored / duplicate-slash paths normalize to a single
    forward-slash form -- the path normalization contract applies to
    every explicit ``allowed_paths`` entry, not just derived ones."""
    phases = [
        PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Build the reporting endpoint.",
                    deliverables=["improvements"],
                    allowed_paths=["app\\reporting\\service.py", "app//reporting//service.py"],
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)
    scope_map, _charter = _build_scope_map(plan)

    assert scope_map.workstreams[0].allowed_paths == ["app/reporting/service.py"]


def test_deliverable_path_evidence_derives_allowed_paths() -> None:
    """No explicit allowed_paths, but a deliverable string is itself
    path-shaped -- decomposition evidence encoded in prose still produces
    a non-empty, normalized write-scope contract."""
    phases = [
        PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Build the reporting endpoint.",
                    deliverables=["app/reporting/service.py"],
                ),
            ],
        ),
    ]
    plan = _make_plan(task_summary="build the reporting endpoint", phases=phases)
    scope_map, _charter = _build_scope_map(plan)

    assert scope_map.workstreams[0].allowed_paths == ["app/reporting/service.py"]


def test_agent_role_evidence_requires_confirmed_project_root() -> None:
    """No explicit paths, no path-shaped deliverables/context files, and
    no charter repo areas -- the agent-role fallback tier only fires when
    a real project_root confirms the role's conventional directory
    actually exists (never invents an unconfirmed path)."""
    phases = [
        PlanPhase(
            phase_id=1,
            name="Testing",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="test-engineer",
                    task_description="Add coverage.",
                    deliverables=["coverage"],
                ),
            ],
        ),
    ]
    plan = _make_plan(task_summary="add coverage", phases=phases)
    config = ManagerConfig()
    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, Path("/nonexistent"))

    # Without project_root: role tier never fires -- stays empty.
    scope_map_no_root = ScopeMapBuilder(config).build(charter, plan)
    assert scope_map_no_root.workstreams[0].allowed_paths == []

    # With a project_root that really has a "tests" directory: role tier
    # fires and selects it.
    real_root = Path(__file__).resolve().parents[2]
    assert (real_root / "tests").is_dir()  # sanity: this repo really has one
    scope_map_with_root = ScopeMapBuilder(config).build(charter, plan, project_root=real_root)
    assert scope_map_with_root.workstreams[0].allowed_paths == ["tests"]


def test_read_only_workstream_stays_empty_without_diagnostic() -> None:
    """A phase whose only step is intentionally read-only (step_type
    'reviewing') must be represented with an empty allowed_paths -- never
    backfilled from the charter/topology fallback chain -- and must not
    raise or diagnose even in strict mode."""
    phases = [
        PlanPhase(
            phase_id=1,
            name="Review",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="code-reviewer",
                    task_description="Review the change.",
                    deliverables=["review verdict"],
                    step_type="reviewing",
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)
    config = ManagerConfig()
    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, Path("/nonexistent"))

    diagnostics: list[str] = []
    scope_map = ScopeMapBuilder(config).build(
        charter, plan, strict=True, diagnostics=diagnostics
    )

    assert scope_map.workstreams[0].allowed_paths == []
    assert diagnostics == []


def test_ambiguous_write_scope_is_diagnosed_but_not_fatal_by_default() -> None:
    """A write-capable step (default step_type='developing') with zero
    derivable evidence yields an empty allowed_paths (unchanged advisory
    behavior for existing non-strict callers) but is now recorded as an
    explicit diagnostic when the caller asks for one."""
    plan = _make_ambiguous_plan()
    config = ManagerConfig()
    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, Path("/nonexistent"))

    diagnostics: list[str] = []
    scope_map = ScopeMapBuilder(config).build(charter, plan, diagnostics=diagnostics)

    assert scope_map.workstreams[0].allowed_paths == []
    assert any("write_scope_missing" in d for d in diagnostics)


def test_ambiguous_write_scope_raises_in_strict_mode() -> None:
    """The same ambiguous plan, but with strict=True: ambiguous write
    scope for a write-capable step is now a planning error raised before
    the scope map is handed back to the caller."""
    plan = _make_ambiguous_plan()
    config = ManagerConfig()
    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, Path("/nonexistent"))

    with pytest.raises(ScopeContractError, match="write_scope_missing"):
        ScopeMapBuilder(config).build(charter, plan, strict=True)


def test_contradictory_scope_raises_even_without_strict() -> None:
    """A step whose allowed_paths collides with its own blocked_paths is
    a genuine contract contradiction -- it always raises, regardless of
    strict mode (never a valid, dispatchable contract)."""
    phases = [
        PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Build the reporting endpoint.",
                    deliverables=["reporting endpoint"],
                    allowed_paths=["app/reporting/service.py"],
                    blocked_paths=["app/reporting"],
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)
    config = ManagerConfig()
    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, Path("/nonexistent"))

    with pytest.raises(ScopeContractError, match="write_scope_contradictory"):
        ScopeMapBuilder(config).build(charter, plan, strict=False)


# ---------------------------------------------------------------------------
# Threat model (Phase 3 "Make scope contracts authoritative", 3.3)
# ---------------------------------------------------------------------------


def test_contradiction_across_sibling_steps_in_same_workstream_raises() -> None:
    """Blocked-over-allowed precedence must hold even when the collision
    only exists AFTER aggregating two different steps in the same phase
    -- one step's allowed_paths colliding with a SIBLING step's
    blocked_paths, not its own. ``diagnose_step_scope`` is invoked per
    step against the WORKSTREAM's resolved allowed_paths (see
    ``ScopeMapBuilder._record_diagnostics``), so this must be caught even
    though step 1.1 alone (allowed vs its own, empty, blocked_paths)
    looks clean in isolation."""
    phases = [
        PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Build the reporting endpoint.",
                    deliverables=["reporting endpoint"],
                    allowed_paths=["app/reporting/service.py"],
                ),
                PlanStep(
                    step_id="1.2",
                    agent_name="backend-engineer",
                    task_description="Sibling step declares the shared area off-limits.",
                    deliverables=["router wiring"],
                    blocked_paths=["app/reporting/service.py"],
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)
    config = ManagerConfig()
    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, Path("/nonexistent"))

    with pytest.raises(ScopeContractError, match="write_scope_contradictory"):
        ScopeMapBuilder(config).build(charter, plan, strict=False)


def test_explicit_generated_path_in_allowed_paths_is_honored_verbatim() -> None:
    """The generated-file policy (``is_generated_path`` / ``GENERATED_PATH_
    MARKERS``) only ever excludes build/tooling output from *inferred*
    evidence tiers (deliverables/context-files text-mining); an operator's
    EXPLICIT ``allowed_paths`` entry is never second-guessed, including a
    generated directory like ``dist/`` -- see ``derive_allowed_paths``'s
    "explicit" tier docstring."""
    phases = [
        PlanPhase(
            phase_id=1,
            name="Release build",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="devops-engineer",
                    task_description="Publish the release bundle.",
                    deliverables=["release bundle"],
                    allowed_paths=["dist/bundle.js"],
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)
    scope_map, _charter = _build_scope_map(plan)
    assert scope_map.workstreams[0].allowed_paths == ["dist/bundle.js"]


def test_inferred_evidence_never_derives_a_generated_path() -> None:
    """The flip side: when a step has NO explicit allowed_paths and must
    fall through to inferred evidence, a generated-looking deliverable
    string must never be silently promoted to write scope."""
    phases = [
        PlanPhase(
            phase_id=1,
            name="Build",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="devops-engineer",
                    task_description="Produce the build output.",
                    deliverables=["dist/bundle.js"],
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)
    scope_map, _charter = _build_scope_map(plan)
    assert scope_map.workstreams[0].allowed_paths == []


def test_blocked_subpath_nested_under_allowed_path_is_flagged_not_silently_carved_out() -> None:
    """Threat-model note (not a bypass -- documents current, fail-closed
    behavior): ``paths_overlap`` is bidirectional (either side may be the
    more specific one -- see its docstring), so
    ``allowed_paths=["app"]`` + ``blocked_paths=["app/node_modules"]``
    (an operator's attempt at "anything in app/ except node_modules/")
    is currently flagged the exact same way as a genuine contradiction
    (``allowed_paths=["app/x"]`` fully inside ``blocked_paths=["app"]``)
    -- it raises rather than silently accepting a partial carve-out. This
    is the SAFE direction (an ambiguous contract is rejected outright,
    never silently narrowed in a way that could surprise an operator who
    expected the exclusion to apply); it means this codebase does not
    currently support "allow a directory except one subdirectory" as a
    single scope contract -- callers must enumerate siblings explicitly
    instead. Pinned here so a future relaxation of this rule is a
    deliberate decision, not an accidental regression."""
    phases = [
        PlanPhase(
            phase_id=1,
            name="Implementation",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Build the service.",
                    deliverables=["service"],
                    allowed_paths=["app"],
                    blocked_paths=["app/node_modules"],
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)
    config = ManagerConfig()
    charter = ProjectCharterBuilder(config).build(plan, plan.task_summary, Path("/nonexistent"))

    with pytest.raises(ScopeContractError, match="write_scope_contradictory"):
        ScopeMapBuilder(config).build(charter, plan, strict=False)


def test_empty_scope_map_for_all_read_only_phase_is_not_backfilled() -> None:
    """Threat: 'empty scope'. A workstream made entirely of read-only
    (reviewing/consulting) steps must be left with an EMPTY
    allowed_paths -- never silently backfilled with the workstream's
    likely_paths, a sibling workstream's scope, or the whole repo. An
    empty allowed_paths here is the valid representation of "this
    workstream does not write", which downstream
    ``ClaudeCodeLauncher.configure_step_scope`` (write_capable=False for
    these step types) also relies on."""
    phases = [
        PlanPhase(
            phase_id=1,
            name="Design review",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="security-reviewer",
                    task_description="Review the proposed design for security issues.",
                    deliverables=["security review notes"],
                    step_type="reviewing",
                ),
            ],
        ),
    ]
    plan = _make_plan(phases=phases)
    scope_map, _charter = _build_scope_map(plan)
    assert scope_map.workstreams[0].allowed_paths == []


def _make_ambiguous_plan(task_id: str = "task-scope-ambiguous") -> MachinePlan:
    """A single write-capable step with no path-shaped evidence anywhere
    -- no allowed_paths, no path-shaped deliverables/context files, and a
    task summary that matches no real directory. Mirrors ``test_scope_map
    .py``'s pre-existing ``test_allowed_paths_fall_back_to_charter_likely
    _repo_areas`` fixture (kept in sync deliberately -- both exercise the
    same "zero evidence" scenario, one for the lenient default, one for
    the opt-in strict/diagnostic path)."""
    phases = [
        PlanPhase(
            phase_id=1,
            name="Improvements",
            steps=[
                PlanStep(
                    step_id="1.1",
                    agent_name="backend-engineer",
                    task_description="Improve things.",
                    deliverables=["improvements"],
                ),
            ],
        ),
    ]
    return _make_plan(task_id=task_id, task_summary="improve things", phases=phases)
