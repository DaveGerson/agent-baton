"""Tests for the two manager-mode dispatcher kwargs (M4):
``scope_contract_section`` / ``context_bundle_section`` on
``PromptDispatcher.build_delegation_prompt``.

See docs/internal/manager-mode-pmo-plan.md Wave 2 / Task 8 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §11.4 /
§16 Milestone 4.

``test_dispatcher_unchanged_without_kwargs`` is the byte-identical
guarantee: it snapshots the prompt built with the pre-manager-mode
signature (no new kwargs passed at all) and proves it is identical to
one built after this change, as long as the new kwargs are omitted.
"""
from __future__ import annotations

from pathlib import Path

from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import PlanStep

_GOLDEN_DIR = Path(__file__).parent / "golden"
_GOLDEN_BASELINE_PROMPT = _GOLDEN_DIR / "manager_context_prompt_baseline.txt"


def _make_step() -> PlanStep:
    return PlanStep(
        step_id="2.1",
        agent_name="backend-engineer",
        task_description="Implement the service-layer change required for the reporting endpoint.",
        deliverables=["app/reporting/service.py"],
        allowed_paths=["app/reporting/**"],
        context_files=["app/reporting/README.md"],
    )


_COMMON_KWARGS = dict(
    shared_context="Stack: Python 3.11, pytest",
    handoff_from="Previous step wrote the router.",
    project_description="Agent Baton orchestration engine",
    task_summary="Add a reporting endpoint with tests and docs",
    task_type="new-feature",
)


def test_dispatcher_includes_scope_contract_section() -> None:
    dispatcher = PromptDispatcher()
    step = _make_step()

    scope_contract_section = (
        "## Scope Contract\n"
        "In scope:\n"
        "- app/reporting/**\n"
        "\n"
        "Out of scope:\n"
        "- authentication changes"
    )
    context_bundle_section = (
        "## Context Bundle\n"
        "Must read:\n"
        "- scope-contracts/2_1.md\n"
        "\n"
        "Reference only:\n"
        "- docs/architecture.md"
    )

    prompt = dispatcher.build_delegation_prompt(
        step,
        **_COMMON_KWARGS,
        scope_contract_section=scope_contract_section,
        context_bundle_section=context_bundle_section,
    )

    assert "## Scope Contract" in prompt
    assert "## Context Bundle" in prompt

    # Ordering: after the knowledge section (there is none here, so simply
    # after "## Intent") and before "## Your Task".
    intent_idx = prompt.index("## Intent")
    contract_idx = prompt.index("## Scope Contract")
    bundle_idx = prompt.index("## Context Bundle")
    your_task_idx = prompt.index("## Your Task")

    assert intent_idx < contract_idx < bundle_idx < your_task_idx


def test_dispatcher_omits_sections_when_blank() -> None:
    """Blank/whitespace-only section strings behave like ``None`` — the
    ``if section and section.strip():`` gate must not emit an empty
    heading-less block."""
    dispatcher = PromptDispatcher()
    step = _make_step()

    prompt = dispatcher.build_delegation_prompt(
        step,
        **_COMMON_KWARGS,
        scope_contract_section="   ",
        context_bundle_section="",
    )

    assert "## Scope Contract" not in prompt
    assert "## Context Bundle" not in prompt


def test_dispatcher_unchanged_without_kwargs() -> None:
    """Byte-identical guarantee: a prompt built without the two new
    manager-mode kwargs is identical to one built with the pre-M4
    ``build_delegation_prompt`` signature.

    The "pre-change" reference here is reconstructed by calling
    ``build_delegation_prompt`` with only the parameters that existed
    before this change (i.e. omitting ``scope_contract_section`` /
    ``context_bundle_section`` entirely, relying on their defaults).
    Since both new parameters default to ``None`` and are gated by
    ``if section and section.strip():``, omitting them must produce
    output identical, character for character, to explicitly passing
    ``None`` for both — which is what proves the change is additive and
    non-manager-mode plans are unaffected.
    """
    dispatcher = PromptDispatcher()
    step = _make_step()

    baseline = dispatcher.build_delegation_prompt(step, **_COMMON_KWARGS)
    with_explicit_none = dispatcher.build_delegation_prompt(
        step,
        **_COMMON_KWARGS,
        scope_contract_section=None,
        context_bundle_section=None,
    )

    assert baseline == with_explicit_none
    assert "## Scope Contract" not in baseline
    assert "## Context Bundle" not in baseline


def test_dispatcher_prompt_matches_frozen_golden_snapshot() -> None:
    """F2 (Wave 2 review): real byte-identity snapshot.

    ``test_dispatcher_unchanged_without_kwargs`` above only proves
    omit-vs-explicit-``None`` produce the same string -- a tautology that
    would pass even if the *baseline* prompt shape itself silently
    regressed (e.g. a stray blank line, a reordered section). This test
    instead pins the exact no-kwargs prompt for ``_make_step()`` +
    ``_COMMON_KWARGS`` against a frozen fixture
    (``tests/engine/golden/manager_context_prompt_baseline.txt``,
    generated from the current, already-merged-and-confined dispatcher
    change) and asserts byte-for-byte equality. Any future change to
    ``build_delegation_prompt``'s output shape for a non-manager-mode
    dispatch must consciously regenerate this fixture, not just satisfy
    the omit-vs-None tautology above.
    """
    dispatcher = PromptDispatcher()
    step = _make_step()

    prompt = dispatcher.build_delegation_prompt(step, **_COMMON_KWARGS)

    golden = _GOLDEN_BASELINE_PROMPT.read_text(encoding="utf-8")
    assert prompt == golden


def test_dispatcher_scope_contract_without_bundle() -> None:
    """The two sections are independent -- one may be present without the
    other (e.g. a bundle failed to build but the contract is available)."""
    dispatcher = PromptDispatcher()
    step = _make_step()

    prompt = dispatcher.build_delegation_prompt(
        step,
        **_COMMON_KWARGS,
        scope_contract_section="## Scope Contract\n- in scope item",
    )

    assert "## Scope Contract" in prompt
    assert "## Context Bundle" not in prompt


# ===========================================================================
# Phase 6, 6.4 -- checkpoint + scope amendment: the resumed dispatch prompt
# must reflect the amended sidecars, not the ones current when the
# checkpoint fired.
#
# ``_dispatch_action`` (executor.py) reads the scope-contract/context-bundle
# sidecars fresh from disk (via ``ManagerArtifactPaths``) at dispatch time --
# never from anything cached on ``ExecutionState``. This end-to-end scenario
# pins that contract across a real CHECKPOINT boundary: a step is amended
# (scope widened, sidecars rebuilt to a new revision) *after* the engine has
# already checkpointed and *before* the amended step is ever dispatched, and
# a brand-new ``ExecutionEngine`` instance (simulating a fresh session that
# picked up the checkpoint's resume command) must dispatch it with the
# amended content.
# ===========================================================================

class TestCheckpointThenScopeAmendmentDispatch:
    def _plan(self, task_id: str) -> "MachinePlan":
        from agent_baton.models.execution import MachinePlan, PlanPhase

        return MachinePlan(
            task_id=task_id,
            task_summary="Add a reporting endpoint with tests",
            task_type="feature",
            complexity="medium",
            risk_level="LOW",
            manager_mode=True,
            phases=[
                PlanPhase(
                    phase_id=1,
                    name="Design",
                    steps=[
                        PlanStep(
                            step_id="1.1",
                            agent_name="architect",
                            task_description="Design the reporting endpoint.",
                            deliverables=["docs/reporting-design.md"],
                            allowed_paths=["docs/**"],
                            step_type="planning",
                        ),
                    ],
                ),
                PlanPhase(
                    phase_id=2,
                    name="Implement",
                    steps=[
                        PlanStep(
                            step_id="2.1",
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

    @staticmethod
    def _no_review_config():
        from agent_baton.core.config.manager import ManagerConfig

        # Adversarial-review injection is an orthogonal PhasePolicyApplier
        # concern (Phase 6, 6.3's rebuild pipeline) -- disabling it keeps
        # this test's phase/step shape exactly what it defines, so a
        # checkpoint-boundary assertion tied to a specific phase index
        # can't be thrown off by an injected review step.
        return ManagerConfig(
            policies={
                "phase_completion": {"adversarial_review": "off"},
                "project_completion": {"adversarial_review": "off"},
            }
        )

    def test_resumed_dispatch_reads_amended_sidecars_not_stale(
        self, tmp_path, monkeypatch,
    ) -> None:
        from agent_baton.core.manager.paths import ManagerArtifactPaths
        from agent_baton.core.manager.rebuild import rebuild_and_publish
        from agent_baton.models.execution import ActionType

        monkeypatch.setenv("BATON_CHECKPOINT_ENABLED", "1")
        monkeypatch.setenv("BATON_CHECKPOINT_PHASE_INTERVAL", "1")

        context_root = tmp_path / ".claude" / "team-context"
        task_id = "task-checkpoint-then-amend"
        plan = self._plan(task_id)

        engine = ExecutionEngine(team_context_root=context_root, task_id=task_id)
        engine.start(plan)

        # ── Initial publish (revision 1): 2.1's scope contract is scoped to
        # app/reporting/** only.
        publish1 = rebuild_and_publish(
            plan, plan.task_summary,
            config=self._no_review_config(),
            project_root=tmp_path,
            team_context_dir=context_root,
            trigger="initial",
        )
        assert publish1.ok is True, publish1.errors
        assert publish1.revision == 1

        mgr_paths = ManagerArtifactPaths(context_root, task_id)
        original_contract = mgr_paths.scope_contract("2.1", ext="md").read_text(encoding="utf-8")
        assert "app/reporting/exports" not in original_contract

        # ── Complete phase 1's only step -- with
        # BATON_CHECKPOINT_PHASE_INTERVAL=1 this phase-boundary advance must
        # trip a checkpoint, BEFORE 2.1 is ever dispatched.
        engine.record_step_result("1.1", "architect")
        checkpoint_action = engine.next_action()
        assert checkpoint_action.action_type == ActionType.CHECKPOINT
        assert checkpoint_action.checkpoint_handoff["phase_id"] == 2

        # ── While checkpointed, the manager amends 2.1's scope (widens
        # allowed_paths/deliverables) and republishes -- exactly the durable
        # sidecar-then-plan-mutation ordering
        # ``ExecutionEngine.resolve_scope_expansion``/``amend_plan`` use
        # elsewhere for an approved scope amendment.
        loaded = engine._load_state()
        target_step = loaded.plan.phases[1].steps[0]
        assert target_step.step_id == "2.1"
        target_step.allowed_paths = list(target_step.allowed_paths) + [
            "app/reporting/exports/**"
        ]
        target_step.deliverables = list(target_step.deliverables) + [
            "app/reporting/exports/service.py"
        ]

        publish2 = rebuild_and_publish(
            loaded.plan, loaded.plan.task_summary,
            config=self._no_review_config(),
            project_root=tmp_path,
            team_context_dir=context_root,
            trigger="post_checkpoint_amend",
        )
        assert publish2.ok is True, publish2.errors
        assert publish2.revision == 2
        engine._save_execution(loaded)

        amended_contract = mgr_paths.scope_contract("2.1", ext="md").read_text(encoding="utf-8")
        assert "app/reporting/exports/**" in amended_contract

        # ── Fresh session: a brand-new ExecutionEngine instance against the
        # same team_context_root (no shared in-memory state) picks the
        # execution back up exactly as the checkpoint's own
        # ``baton execute resume`` command would.
        fresh_engine = ExecutionEngine(team_context_root=context_root, task_id=task_id)
        resumed_action = fresh_engine.next_action()

        # The checkpoint boundary is already recorded -- dedup means resume
        # does NOT re-checkpoint, it proceeds straight to dispatch.
        assert resumed_action.action_type == ActionType.DISPATCH
        assert resumed_action.step_id == "2.1"
        prompt = resumed_action.delegation_prompt
        assert "## Scope Contract" in prompt
        # The amended (post-checkpoint) scope must be what the resumed
        # dispatch prompt carries -- not the revision-1 sidecar that was
        # current when the checkpoint fired.
        assert "app/reporting/exports/**" in prompt
        assert "app/reporting/exports/service.py" in prompt

    def test_checkpoint_dedup_holds_through_the_amendment(
        self, tmp_path, monkeypatch,
    ) -> None:
        """A resumed session that amends scope and dispatches must not
        re-trip the checkpoint it already crossed."""
        from agent_baton.core.manager.rebuild import rebuild_and_publish
        from agent_baton.models.execution import ActionType

        monkeypatch.setenv("BATON_CHECKPOINT_ENABLED", "1")
        monkeypatch.setenv("BATON_CHECKPOINT_PHASE_INTERVAL", "1")

        context_root = tmp_path / ".claude" / "team-context"
        task_id = "task-checkpoint-dedup-amend"
        plan = self._plan(task_id)

        engine = ExecutionEngine(team_context_root=context_root, task_id=task_id)
        engine.start(plan)
        rebuild_and_publish(
            plan, plan.task_summary,
            config=self._no_review_config(),
            project_root=tmp_path,
            team_context_dir=context_root,
            trigger="initial",
        )
        engine.record_step_result("1.1", "architect")
        assert engine.next_action().action_type == ActionType.CHECKPOINT

        loaded = engine._load_state()
        loaded.plan.phases[1].steps[0].allowed_paths.append("app/reporting/exports/**")
        rebuild_and_publish(
            loaded.plan, loaded.plan.task_summary,
            config=self._no_review_config(),
            project_root=tmp_path,
            team_context_dir=context_root,
            trigger="post_checkpoint_amend",
        )
        engine._save_execution(loaded)

        fresh_engine = ExecutionEngine(team_context_root=context_root, task_id=task_id)
        resumed_action = fresh_engine.next_action()

        assert resumed_action.action_type == ActionType.DISPATCH
        state = fresh_engine._load_state()
        assert state.checkpoint_count == 1
        assert len(state.checkpoints) == 1
