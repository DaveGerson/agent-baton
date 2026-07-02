"""Tests for :mod:`agent_baton.core.manager.context_bundles` (M4 --
``ScopeContractBuilder`` + ``ContextBundleBuilder``).

See docs/internal/manager-mode-pmo-plan.md Wave 2 / Task 8 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §16
Milestone 4.

Test inputs are hand-constructed ``PlanStep``/``Workstream``/``RoleCard``/
``KnowledgePlan`` objects -- the planner pipeline and knowledge registry
are never invoked.
"""
from __future__ import annotations

import json

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.manager.context_bundles import (
    ContextBundleBuilder,
    ScopeContractBuilder,
    contract_to_markdown,
    is_nontrivial_step,
)
from agent_baton.models.execution import MachinePlan, PlanPhase, PlanStep
from agent_baton.models.knowledge import KnowledgeAttachment
from agent_baton.models.manager import (
    ContextBundle,
    KnowledgePackReference,
    KnowledgePlan,
    RoleCard,
    ScopeContract,
    ScopeMap,
    Workstream,
)


def _step(**overrides) -> PlanStep:
    defaults = dict(
        step_id="2.1",
        agent_name="backend-engineer",
        task_description="Implement the service-layer change required for the reporting endpoint.",
        deliverables=["app/reporting/service.py"],
        allowed_paths=["app/reporting/**"],
        context_files=[],
        step_type="developing",
    )
    defaults.update(overrides)
    return PlanStep(**defaults)


def _workstream(**overrides) -> Workstream:
    defaults = dict(
        id="ws-1",
        name="Reporting",
        objective="Ship the reporting endpoint",
        allowed_paths=["app/reporting/**"],
        owner_role="backend-engineer",
        deliverables=["reporting endpoint"],
    )
    defaults.update(overrides)
    return Workstream(**defaults)


def _role_card(**overrides) -> RoleCard:
    defaults = dict(
        role="backend-engineer",
        agent_name="backend-engineer",
        mission="Own the reporting endpoint implementation.",
        owns=["reporting endpoint"],
        does_not_own=["frontend UI", "final adversarial review"],
        required_knowledge_packs=["coding-conventions", "testing-strategy"],
        default_context_budget=12000,
        escalation_triggers=["API contract ambiguity blocks implementation"],
    )
    defaults.update(overrides)
    return RoleCard(**defaults)


def _knowledge_plan(**overrides) -> KnowledgePlan:
    defaults = dict(
        task_id="task-bundles",
        selected_packs=[
            KnowledgePackReference(
                name="coding-conventions", path="", reason="config: required_for_code_steps",
                token_estimate=100,
            ),
            KnowledgePackReference(
                name="testing-strategy", path="", reason="config: required_for_code_steps",
                token_estimate=100,
            ),
        ],
        per_step_packs={"2.1": ["coding-conventions", "testing-strategy"]},
    )
    defaults.update(overrides)
    return KnowledgePlan(**defaults)


# ---------------------------------------------------------------------------
# is_nontrivial_step
# ---------------------------------------------------------------------------


def test_every_nontrivial_step_gets_contract() -> None:
    """Steps with an agent_name, a non-gate step_type, and no command are
    nontrivial; steps without an agent, gate-typed, or command-only are not.
    """
    assert is_nontrivial_step(_step()) is True
    assert is_nontrivial_step(_step(agent_name="")) is False
    assert is_nontrivial_step(_step(step_type="gate")) is False
    assert is_nontrivial_step(_step(command="pytest -q")) is False


def test_nontrivial_steps_identified_across_a_plan() -> None:
    plan = MachinePlan(
        task_id="task-bundles",
        task_summary="Add a reporting endpoint",
        phases=[
            PlanPhase(
                phase_id=1,
                name="Implement",
                steps=[
                    _step(step_id="1.1"),
                    _step(step_id="1.2", command="pytest -q"),
                ],
            ),
        ],
    )
    nontrivial_ids = [
        s.step_id for phase in plan.phases for s in phase.steps if is_nontrivial_step(s)
    ]
    assert nontrivial_ids == ["1.1"]


# ---------------------------------------------------------------------------
# ScopeContractBuilder
# ---------------------------------------------------------------------------


def test_contract_fields_complete() -> None:
    step = _step()
    workstream = _workstream()
    role_card = _role_card()

    contract = ScopeContractBuilder(ManagerConfig()).build(step, workstream, role_card)

    assert isinstance(contract, ScopeContract)
    assert contract.step_id == "2.1"
    assert contract.agent_name == "backend-engineer"
    assert contract.workstream_id == "ws-1"
    assert contract.mission
    assert contract.in_scope
    assert contract.out_of_scope
    assert contract.allowed_paths
    assert contract.expected_artifacts
    assert contract.definition_of_done
    assert contract.escalation_triggers
    # Standard four escalation triggers (spec §11.4) always present.
    assert "scope expansion needed" in contract.escalation_triggers
    assert "knowledge gap blocks work" in contract.escalation_triggers
    assert "assigned paths are insufficient" in contract.escalation_triggers
    assert "design assumption appears invalid" in contract.escalation_triggers
    # Role-card triggers folded in too.
    assert "API contract ambiguity blocks implementation" in contract.escalation_triggers
    # Definition of done includes deliverables plus fixed closing items.
    assert "handoff summary written" in contract.definition_of_done
    assert "no unrelated refactors" in contract.definition_of_done


def test_contract_trusts_role_card_agent_name_not_step_agent_name() -> None:
    """Binding composition rule: never derive ownership from
    ``step.agent_name`` when a role card is supplied -- after specialist
    diversification these can diverge."""
    step = _step(agent_name="claude")
    workstream = _workstream(owner_role="claude")
    role_card = _role_card(role="backend-engineer", agent_name="backend-engineer")

    contract = ScopeContractBuilder(ManagerConfig()).build(step, workstream, role_card)

    assert contract.agent_name == "backend-engineer"


def test_contract_out_of_scope_enriched_by_scope_map() -> None:
    step = _step()
    workstream = _workstream()
    role_card = _role_card()
    scope_map = ScopeMap(
        task_id="task-bundles",
        workstreams=[workstream, _workstream(id="ws-2", name="Auth")],
        out_of_scope=["Repo areas outside the scope map"],
    )

    contract = ScopeContractBuilder(ManagerConfig()).build(
        step, workstream, role_card, scope_map=scope_map
    )

    assert "Repo areas outside the scope map" in contract.out_of_scope
    assert "Auth workstream" in contract.out_of_scope


def test_contract_allowed_paths_falls_back_to_workstream() -> None:
    step = _step(allowed_paths=[])
    workstream = _workstream(allowed_paths=["app/reporting/**", "tests/reporting/**"])
    role_card = _role_card()

    contract = ScopeContractBuilder(ManagerConfig()).build(step, workstream, role_card)

    assert contract.allowed_paths == ["app/reporting/**", "tests/reporting/**"]


def test_contract_round_trip() -> None:
    contract = ScopeContractBuilder(ManagerConfig()).build(
        _step(), _workstream(), _role_card()
    )
    serialized = json.dumps(contract.to_dict())
    reloaded = ScopeContract.from_dict(json.loads(serialized))
    assert reloaded == contract


def test_contract_to_markdown_renders_sections_in_order() -> None:
    contract = ScopeContractBuilder(ManagerConfig()).build(
        _step(), _workstream(), _role_card()
    )
    rendered = contract_to_markdown(contract)

    assert rendered.startswith("# Scope Contract: Step 2.1")
    headers = [
        "## Mission",
        "## In Scope",
        "## Out of Scope",
        "## Allowed Paths",
        "## Definition of Done",
        "## Escalate If",
    ]
    positions = [rendered.index(h) for h in headers]
    assert positions == sorted(positions)
    assert "- app/reporting/**" in rendered


# ---------------------------------------------------------------------------
# ContextBundleBuilder
# ---------------------------------------------------------------------------


def test_bundle_includes_role_card_and_required_packs(tmp_path) -> None:
    contract_path = tmp_path / "scope-contracts" / "2_1.md"
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text("# Scope Contract: Step 2.1\n", encoding="utf-8")

    role_card_path = tmp_path / "role-cards" / "backend-engineer.md"
    role_card_path.parent.mkdir(parents=True)
    role_card_path.write_text("# Role Card: backend-engineer\n", encoding="utf-8")

    step = _step()
    role_card = _role_card()
    knowledge_plan = _knowledge_plan()

    bundle = ContextBundleBuilder(ManagerConfig()).build(
        step,
        contract_path,
        role_card,
        knowledge_plan,
        role_card_path=role_card_path,
        task_id="task-bundles",
    )

    assert isinstance(bundle, ContextBundle)
    assert bundle.task_id == "task-bundles"
    assert bundle.step_id == "2.1"
    assert bundle.agent_name == "backend-engineer"
    assert bundle.scope_contract_path == str(contract_path)

    must_read_paths = [ref.path for ref in bundle.must_read]
    assert str(contract_path) in must_read_paths
    assert str(role_card_path) in must_read_paths

    pack_names = {pack.name for pack in bundle.knowledge_packs}
    assert {"coding-conventions", "testing-strategy"} <= pack_names


def test_bundle_respects_max_knowledge_docs(tmp_path) -> None:
    contract_path = tmp_path / "contract.md"
    contract_path.write_text("contract", encoding="utf-8")

    step = _step()
    role_card = _role_card(
        required_knowledge_packs=["pack-a", "pack-b", "pack-c", "pack-d"]
    )
    knowledge_plan = _knowledge_plan(
        selected_packs=[
            KnowledgePackReference(name=n, token_estimate=10)
            for n in ("pack-a", "pack-b", "pack-c", "pack-d", "pack-e")
        ],
        per_step_packs={"2.1": ["pack-e"]},
    )
    config = ManagerConfig(context={"max_knowledge_docs_per_step": 2})

    bundle = ContextBundleBuilder(config).build(
        step, contract_path, role_card, knowledge_plan
    )

    assert len(bundle.knowledge_packs) == 2
    # Required packs win the cap over the step-attached "pack-e".
    assert {p.name for p in bundle.knowledge_packs} == {"pack-a", "pack-b"}


def test_overflow_drops_reference_docs_before_required(tmp_path) -> None:
    contract_path = tmp_path / "contract.md"
    contract_path.write_text("x" * 40, encoding="utf-8")  # ~10 tokens

    doc_a = tmp_path / "doc_a.md"
    doc_a.write_text("y" * 400, encoding="utf-8")  # ~100 tokens, lowest priority
    doc_b = tmp_path / "doc_b.md"
    doc_b.write_text("z" * 400, encoding="utf-8")  # ~100 tokens, higher priority

    handoff_old = tmp_path / "phase-1-handoff.md"
    handoff_old.write_text("h" * 40, encoding="utf-8")  # ~10 tokens
    handoff_latest = tmp_path / "phase-2-handoff.md"
    handoff_latest.write_text("h" * 40, encoding="utf-8")  # ~10 tokens

    step = _step(
        knowledge=[
            KnowledgeAttachment(
                source="gap-suggested",
                pack_name=None,
                document_name="doc_a",
                path=str(doc_a),
                delivery="reference",
            ),
            KnowledgeAttachment(
                source="explicit",
                pack_name=None,
                document_name="doc_b",
                path=str(doc_b),
                delivery="reference",
            ),
        ]
    )
    role_card = _role_card(
        required_knowledge_packs=["coding-conventions"], default_context_budget=40
    )
    knowledge_plan = _knowledge_plan(
        selected_packs=[
            KnowledgePackReference(name="coding-conventions", token_estimate=20),
        ],
        per_step_packs={},
    )
    config = ManagerConfig()

    bundle = ContextBundleBuilder(config).build(
        step,
        contract_path,
        role_card,
        knowledge_plan,
        prior_handoff_paths=[str(handoff_old), str(handoff_latest)],
    )

    # required pack, contract, and the latest handoff all survive.
    assert any(p.name == "coding-conventions" for p in bundle.knowledge_packs)
    assert bundle.scope_contract_path == str(contract_path)
    assert str(handoff_latest) in bundle.prior_handoffs
    assert str(handoff_old) not in bundle.prior_handoffs
    # The lowest-priority reference doc (gap-suggested) is dropped before
    # the higher-priority explicit one.
    reference_paths = [ref.path for ref in bundle.reference_only]
    assert str(doc_a) not in reference_paths
    assert bundle.truncation_warnings


def test_overflow_drops_exactly_one_reference_doc(tmp_path) -> None:
    """F3 (Wave 2 review): budget sized so exactly ONE reference doc must
    drop. ``doc_a`` (gap-suggested, lower priority) is dropped; ``doc_b``
    (explicit, higher priority) survives; the first truncation warning
    names ``doc_a``.

    Explicit ``token_estimate`` values (rather than file-size-derived
    estimates) keep the arithmetic exact: contract(10) + doc_a(100) +
    doc_b(50) + required pack(20) = 180 total against a budget of 100 --
    dropping doc_a alone (-100 -> 80) satisfies the budget, so doc_b must
    never be touched.
    """
    contract_path = tmp_path / "contract.md"
    contract_path.write_text("x" * 40, encoding="utf-8")  # 40 // 4 == 10 tokens

    step = _step(
        knowledge=[
            KnowledgeAttachment(
                source="gap-suggested",
                pack_name=None,
                document_name="doc_a",
                path="doc_a.md",
                delivery="reference",
                token_estimate=100,
            ),
            KnowledgeAttachment(
                source="explicit",
                pack_name=None,
                document_name="doc_b",
                path="doc_b.md",
                delivery="reference",
                token_estimate=50,
            ),
        ]
    )
    role_card = _role_card(
        required_knowledge_packs=["coding-conventions"], default_context_budget=100
    )
    knowledge_plan = _knowledge_plan(
        selected_packs=[
            KnowledgePackReference(name="coding-conventions", token_estimate=20),
        ],
        per_step_packs={},
    )
    config = ManagerConfig()

    bundle = ContextBundleBuilder(config).build(
        step, contract_path, role_card, knowledge_plan
    )

    reference_paths = [ref.path for ref in bundle.reference_only]
    assert "doc_a.md" not in reference_paths
    assert "doc_b.md" in reference_paths
    assert any(p.name == "coding-conventions" for p in bundle.knowledge_packs)
    assert bundle.truncation_warnings
    assert bundle.truncation_warnings[0] == (
        "Dropped reference doc to fit token budget: doc_a.md"
    )


def test_overflow_warns_on_residual_overrun_when_nothing_droppable(tmp_path) -> None:
    """F4.2 (Wave 2 review): when the overflow loop exits still over
    budget because nothing droppable remains (no reference docs, no extra
    handoffs), a truncation warning states the residual overrun instead of
    silently dispatching an over-budget bundle."""
    contract_path = tmp_path / "contract.md"
    contract_path.write_text("x" * 400, encoding="utf-8")  # ~100 tokens, never dropped

    step = _step()
    role_card = _role_card(required_knowledge_packs=[], default_context_budget=10)
    knowledge_plan = _knowledge_plan(selected_packs=[], per_step_packs={})
    config = ManagerConfig()

    bundle = ContextBundleBuilder(config).build(
        step, contract_path, role_card, knowledge_plan
    )

    assert bundle.estimated_tokens > bundle.token_budget
    assert bundle.truncation_warnings
    assert any(
        "token budget exceeded" in w.lower() for w in bundle.truncation_warnings
    )


def test_knowledge_pack_cap_warns_when_required_pack_dropped(tmp_path) -> None:
    """F4.3 (Wave 2 review): when the ``[:max_docs]`` cap cuts a pack that
    is a member of ``config.knowledge_packs.required_for_code_steps``, a
    truncation warning names it. ``testing-strategy`` is in the default
    ``required_for_code_steps`` list; capping to 2 docs here keeps
    ``repo-architecture`` and ``coding-conventions`` and cuts it."""
    contract_path = tmp_path / "contract.md"
    contract_path.write_text("contract", encoding="utf-8")

    step = _step()
    role_card = _role_card(
        required_knowledge_packs=["repo-architecture", "coding-conventions", "testing-strategy"]
    )
    knowledge_plan = _knowledge_plan(
        selected_packs=[
            KnowledgePackReference(name="repo-architecture", token_estimate=10),
            KnowledgePackReference(name="coding-conventions", token_estimate=10),
            KnowledgePackReference(name="testing-strategy", token_estimate=10),
        ],
        per_step_packs={},
    )
    config = ManagerConfig(context={"max_knowledge_docs_per_step": 2})

    bundle = ContextBundleBuilder(config).build(
        step, contract_path, role_card, knowledge_plan
    )

    pack_names = {p.name for p in bundle.knowledge_packs}
    assert pack_names == {"repo-architecture", "coding-conventions"}
    assert any("testing-strategy" in w for w in bundle.truncation_warnings)


def test_bundle_round_trip(tmp_path) -> None:
    contract_path = tmp_path / "contract.md"
    contract_path.write_text("contract", encoding="utf-8")
    bundle = ContextBundleBuilder(ManagerConfig()).build(
        _step(), contract_path, _role_card(), _knowledge_plan(), task_id="task-bundles"
    )
    serialized = json.dumps(bundle.to_dict())
    reloaded = ContextBundle.from_dict(json.loads(serialized))
    assert reloaded == bundle
