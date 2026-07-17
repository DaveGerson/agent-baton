"""Tests for agent_baton.core.engine.planning.talent_factory.

Covers the bounded dispatch lifecycle: exactly one attempt per gap,
validation-gated install, name-collision policy, atomic rollback-safety,
and the deterministic generic-agent fallback / explicit planning failure.
See docs/internal/talent-factory-contract.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.config.manager import TalentFactoryConfig
from agent_baton.core.engine.planning.capability_gap import (
    CapabilityGap,
    CapabilityGapEvidence,
    CapabilityGapKind,
    PermittedArtifactType,
    TalentLifecycleAction,
    TalentLifecycleDecision,
    decide_talent_lifecycle,
    detect_missing_role_gap,
)
from agent_baton.core.engine.planning.talent_factory import (
    DispatchOutcome,
    TalentBuilderRequest,
    TalentFactoryError,
    pick_generic_fallback_agent,
    run_talent_factory_for_gap,
)
from agent_baton.core.orchestration.registry import AgentRegistry

_VALID_BODY = """
## Mission

You are a senior widget specialist.

## Before Starting

1. Read this entire agent definition.

## Knowledge References

No knowledge packs required for this role yet.

## Principles

- Be rigorous.

## Anti-Patterns

- Do not fabricate results.

## Output Format

Return a summary of findings.
"""


def _valid_agent_text(name: str = "widget-specialist") -> str:
    return (
        "---\n"
        f"name: {name}\n"
        "description: |\n"
        "  Handles widget-domain analysis.\n"
        "model: sonnet\n"
        "permissionMode: default\n"
        "color: teal\n"
        "tools: Read, Glob, Grep\n"
        "created_by: talent-builder\n"
        "status: draft\n"
        "version: 0.1.0\n"
        "---\n"
        f"\n# Widget Specialist\n{_VALID_BODY}"
    )


class FakeDispatcher:
    """Records calls; returns a pre-configured DispatchOutcome."""

    def __init__(self, *, text: str | None = None, error: str = "", filename: str | None = None) -> None:
        self._text = text
        self._error = error
        self._filename = filename
        self.calls: list[TalentBuilderRequest] = []

    def dispatch(self, request: TalentBuilderRequest) -> DispatchOutcome:
        self.calls.append(request)
        if self._text is None:
            return DispatchOutcome(success=False, error=self._error or "dispatch failed")
        request.output_dir.mkdir(parents=True, exist_ok=True)
        name = self._filename or request.gap.requested_capability
        path = request.output_dir / f"{name}.md"
        path.write_text(self._text, encoding="utf-8")
        return DispatchOutcome(success=True, candidate_paths=[path])


def _registry(tmp_path: Path, *, extra_agents: "list[str]" = ()) -> AgentRegistry:
    agents_dir = tmp_path / "seed-agents"
    agents_dir.mkdir()
    (agents_dir / "architect.md").write_text(
        "---\nname: architect\ndescription: plans things\n---\n# Architect\n", encoding="utf-8",
    )
    (agents_dir / "backend-engineer.md").write_text(
        "---\nname: backend-engineer\ndescription: builds things\n---\n# Backend Engineer\n", encoding="utf-8",
    )
    for extra in extra_agents:
        (agents_dir / f"{extra}.md").write_text(
            f"---\nname: {extra}\ndescription: extra\n---\n# {extra}\n", encoding="utf-8",
        )
    reg = AgentRegistry()
    reg.load_directory(agents_dir)
    return reg


def _missing_role_decision(requested: str, registry: AgentRegistry, **decide_kwargs) -> tuple[CapabilityGap, TalentLifecycleDecision]:
    known = {n.split("--", 1)[0] for n in registry.names}
    gap = detect_missing_role_gap(requested, known_agents=known)
    assert gap is not None
    decision = decide_talent_lifecycle(gap, **decide_kwargs)
    return gap, decision


class TestPickGenericFallbackAgent:
    def test_prefers_first_candidate_present(self) -> None:
        names = {"backend-engineer", "architect", "system-maintainer"}
        assert pick_generic_fallback_agent(names) == "architect"

    def test_falls_through_priority_list(self) -> None:
        names = {"backend-engineer", "system-maintainer"}
        assert pick_generic_fallback_agent(names) == "backend-engineer"

    def test_falls_back_to_sorted_first_when_no_priority_match(self) -> None:
        names = {"zzz-agent", "aaa-agent"}
        assert pick_generic_fallback_agent(names) == "aaa-agent"

    def test_raises_when_registry_is_empty(self) -> None:
        with pytest.raises(TalentFactoryError):
            pick_generic_fallback_agent(set())


class TestPassthroughDecisions:
    def test_request_clarification_never_dispatches(self, tmp_path: Path) -> None:
        gap = CapabilityGap(
            requested_capability="do the thing",
            kind=CapabilityGapKind.WEAK_TASK_DESCRIPTION,
            evidence=(CapabilityGapEvidence(source="x", detail="too short"),),
        )
        decision = decide_talent_lifecycle(gap)
        assert decision.action == TalentLifecycleAction.REQUEST_CLARIFICATION

        registry = _registry(tmp_path)
        dispatcher = FakeDispatcher(text=_valid_agent_text())
        outcome = run_talent_factory_for_gap(
            gap, decision, config=TalentFactoryConfig(), registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )
        assert outcome.status == "clarification_requested"
        assert dispatcher.calls == []

    def test_queue_for_manager_never_dispatches(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        gap, decision = _missing_role_decision(
            "quantum-specialist", registry, attempts_used=1, retry_budget=1,
        )
        assert decision.action == TalentLifecycleAction.QUEUE_FOR_MANAGER

        dispatcher = FakeDispatcher(text=_valid_agent_text())
        outcome = run_talent_factory_for_gap(
            gap, decision, config=TalentFactoryConfig(), registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )
        assert outcome.status == "queued_for_manager"
        assert dispatcher.calls == []


class TestFallbackGenericAgentDecisions:
    def test_skip_init_falls_back_without_dispatch(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        gap, decision = _missing_role_decision(
            "quantum-specialist", registry, skip_init=True,
        )
        assert decision.action == TalentLifecycleAction.FALLBACK_GENERIC_AGENT

        dispatcher = FakeDispatcher(text=_valid_agent_text())
        outcome = run_talent_factory_for_gap(
            gap, decision, config=TalentFactoryConfig(), registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )
        assert outcome.status == "fallback"
        assert outcome.resolved_agent_name == "architect"
        assert dispatcher.calls == [], "skip_init must never dispatch talent-builder"
        assert not (tmp_path / ".claude" / "agents").exists()

    def test_allow_talent_builder_false_falls_back_without_dispatch(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        gap, decision = _missing_role_decision(
            "quantum-specialist", registry, allow_talent_builder=False,
        )
        assert decision.action == TalentLifecycleAction.FALLBACK_GENERIC_AGENT

        dispatcher = FakeDispatcher(text=_valid_agent_text())
        outcome = run_talent_factory_for_gap(
            gap, decision, config=TalentFactoryConfig(), registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )
        assert outcome.status == "fallback"
        assert dispatcher.calls == []


class TestSuccessfulDispatch:
    def test_generation_success_installs_and_registers(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        gap, decision = _missing_role_decision("quantum-specialist", registry)
        assert decision.action == TalentLifecycleAction.DISPATCH_TALENT_BUILDER

        dispatcher = FakeDispatcher(text=_valid_agent_text(name="quantum-specialist"))
        outcome = run_talent_factory_for_gap(
            gap, decision, config=TalentFactoryConfig(), registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )

        assert outcome.status == "generated"
        assert outcome.resolved_agent_name == "quantum-specialist"
        assert len(dispatcher.calls) == 1, "exactly one bounded dispatch attempt"

        installed = tmp_path / ".claude" / "agents" / "quantum-specialist.md"
        assert installed.is_file()
        assert registry.get("quantum-specialist") is not None

    def test_scratch_directory_is_cleaned_up(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        gap, decision = _missing_role_decision("quantum-specialist", registry)
        scratch_root = tmp_path / "scratch"
        dispatcher = FakeDispatcher(text=_valid_agent_text(name="quantum-specialist"))

        run_talent_factory_for_gap(
            gap, decision, config=TalentFactoryConfig(), registry=registry,
            project_root=tmp_path, scratch_root=scratch_root, dispatcher=dispatcher,
        )

        assert list(scratch_root.iterdir()) == [], "scratch dir must not leak generated artifacts"


class TestDispatchFailureFallback:
    def test_dispatch_failure_falls_back_and_does_not_install(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        gap, decision = _missing_role_decision("quantum-specialist", registry)
        dispatcher = FakeDispatcher(error="claude CLI not available")

        outcome = run_talent_factory_for_gap(
            gap, decision, config=TalentFactoryConfig(), registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )

        assert outcome.status == "generation_failed_fallback"
        assert outcome.resolved_agent_name == "architect"
        assert len(dispatcher.calls) == 1
        assert not (tmp_path / ".claude" / "agents" / "quantum-specialist.md").exists()


class TestValidationFailure:
    def test_invalid_artifact_rolls_back_by_default(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        gap, decision = _missing_role_decision("quantum-specialist", registry)
        bad_text = _valid_agent_text(name="quantum-specialist").replace("model: sonnet\n", "")
        dispatcher = FakeDispatcher(text=bad_text, filename="quantum-specialist")

        outcome = run_talent_factory_for_gap(
            gap, decision, config=TalentFactoryConfig(), registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )

        assert outcome.status == "validation_failed_fallback"
        assert outcome.resolved_agent_name == "architect"
        assert not (tmp_path / ".claude" / "agents" / "quantum-specialist.md").exists()
        assert not (tmp_path / ".claude" / "agents" / "_quarantine").exists()

    def test_invalid_artifact_is_quarantined_when_configured(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        gap, decision = _missing_role_decision("quantum-specialist", registry)
        bad_text = _valid_agent_text(name="quantum-specialist").replace("model: sonnet\n", "")
        dispatcher = FakeDispatcher(text=bad_text, filename="quantum-specialist")
        config = TalentFactoryConfig(on_validation_failure="quarantine")

        outcome = run_talent_factory_for_gap(
            gap, decision, config=config, registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )

        assert outcome.status == "validation_failed_fallback"
        quarantined = tmp_path / ".claude" / "agents" / "_quarantine" / "quantum-specialist.md"
        assert quarantined.is_file()
        # Never registered/live even though it's kept on disk for review.
        assert not (tmp_path / ".claude" / "agents" / "quantum-specialist.md").exists()


class TestNameCollisionPolicy:
    def test_reject_policy_refuses_to_overwrite(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path, extra_agents=["quantum-specialist"])
        gap = CapabilityGap(
            requested_capability="quantum-specialist",
            kind=CapabilityGapKind.MISSING_ROLE,
            evidence=(CapabilityGapEvidence(source="x", detail="explicit request"),),
        )
        # Force a DISPATCH decision directly (bypassing the "already known"
        # short-circuit in detect_missing_role_gap, since we deliberately
        # seeded a same-named agent to exercise the collision path).
        decision = TalentLifecycleDecision(
            action=TalentLifecycleAction.DISPATCH_TALENT_BUILDER,
            reason="test-forced dispatch",
            gap=gap,
        )
        dispatcher = FakeDispatcher(text=_valid_agent_text(name="quantum-specialist"))
        config = TalentFactoryConfig(name_collision_policy="reject")

        outcome = run_talent_factory_for_gap(
            gap, decision, config=config, registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )

        assert outcome.status == "collision_fallback"
        assert outcome.resolved_agent_name == "architect"

    def test_version_suffix_policy_installs_under_new_name(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path, extra_agents=["quantum-specialist"])
        gap = CapabilityGap(
            requested_capability="quantum-specialist",
            kind=CapabilityGapKind.MISSING_ROLE,
            evidence=(CapabilityGapEvidence(source="x", detail="explicit request"),),
        )
        decision = TalentLifecycleDecision(
            action=TalentLifecycleAction.DISPATCH_TALENT_BUILDER,
            reason="test-forced dispatch",
            gap=gap,
        )
        dispatcher = FakeDispatcher(text=_valid_agent_text(name="quantum-specialist"))
        config = TalentFactoryConfig(name_collision_policy="version_suffix")

        outcome = run_talent_factory_for_gap(
            gap, decision, config=config, registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )

        assert outcome.status == "generated"
        assert outcome.resolved_agent_name == "quantum-specialist--v2"
        assert (tmp_path / ".claude" / "agents" / "quantum-specialist--v2.md").is_file()
        assert not (tmp_path / ".claude" / "agents" / "quantum-specialist.md").exists()


class TestArtifactTypeNotWired:
    def test_knowledge_pack_only_gap_falls_back(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        gap = CapabilityGap(
            requested_capability="widget-domain",
            kind=CapabilityGapKind.MISSING_KNOWLEDGE,
            evidence=(CapabilityGapEvidence(source="x", detail="no pack"),),
        )
        decision = decide_talent_lifecycle(gap)
        assert decision.action == TalentLifecycleAction.DISPATCH_TALENT_BUILDER
        assert gap.permitted_artifacts == (PermittedArtifactType.KNOWLEDGE_PACK,)

        dispatcher = FakeDispatcher(text=_valid_agent_text())
        outcome = run_talent_factory_for_gap(
            gap, decision, config=TalentFactoryConfig(), registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )

        assert outcome.status == "fallback"
        assert dispatcher.calls == [], "no dispatcher is wired for knowledge_pack artifacts yet"
