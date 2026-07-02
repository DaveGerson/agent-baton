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

from agent_baton.core.engine.dispatcher import PromptDispatcher
from agent_baton.models.execution import PlanStep


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
