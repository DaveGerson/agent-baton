"""Talent-factory dispatch: rollback-safety, collision policy, and defense
in depth against a malformed/malicious generated artifact.

Complements ``tests/test_talent_factory.py`` (reject / version_suffix
collision policies, the default rollback-on-invalid-artifact path,
dispatch-failure fallback) with the cases that file does not cover:

- ``name_collision_policy: manual_review`` -- queued to a quarantine path,
  never overwrites, never registers.
- Registry-reload failure after a successful install rolls the just-written
  file back out (``install_failed_fallback``) rather than leaving an
  unreachable file on disk.
- A generated artifact whose frontmatter ``name`` is a path-escape attempt
  can never reach the install step -- validation's kebab-case name check
  is a hard stop, and no file is ever written outside the intended
  ``.claude/agents/`` tree.
- Malformed frontmatter / an unsafe (unrecognized) tool request, exercised
  through the full ``run_talent_factory_for_gap`` flow (not just the
  validator in isolation) so the "no artifact remains after a failed
  validation" invariant is checked against the real filesystem, not just
  ``ValidationResult.valid``.

See docs/internal/talent-factory-contract.md §5 (validation),
§6 (name collisions).
"""
from __future__ import annotations

from pathlib import Path

from agent_baton.core.config.manager import TalentFactoryConfig
from agent_baton.core.engine.planning.capability_gap import (
    CapabilityGap,
    CapabilityGapEvidence,
    CapabilityGapKind,
    TalentLifecycleAction,
    TalentLifecycleDecision,
    decide_talent_lifecycle,
    detect_missing_role_gap,
)
from agent_baton.core.engine.planning.talent_factory import (
    DispatchOutcome,
    TalentBuilderRequest,
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


def _agent_text(
    *,
    name: str = "widget-specialist",
    model: str = "sonnet",
    tools: str = "Read, Glob, Grep",
    permission_mode: str | None = "default",
) -> str:
    permission_line = f"permissionMode: {permission_mode}\n" if permission_mode else ""
    return (
        "---\n"
        f"name: {name}\n"
        "description: |\n"
        "  Handles widget-domain analysis.\n"
        f"model: {model}\n"
        f"{permission_line}"
        "color: teal\n"
        f"tools: {tools}\n"
        "created_by: talent-builder\n"
        "status: draft\n"
        "version: 0.1.0\n"
        "---\n"
        f"\n# Widget Specialist\n{_VALID_BODY}"
    )


class FakeDispatcher:
    """Writes a pre-configured artifact under the scoped scratch dir,
    exactly as a real dispatcher would."""

    def __init__(self, *, text: str, written_filename: str) -> None:
        self._text = text
        self._written_filename = written_filename
        self.calls: list[TalentBuilderRequest] = []

    def dispatch(self, request: TalentBuilderRequest) -> DispatchOutcome:
        self.calls.append(request)
        request.output_dir.mkdir(parents=True, exist_ok=True)
        path = request.output_dir / f"{self._written_filename}.md"
        path.write_text(self._text, encoding="utf-8")
        return DispatchOutcome(success=True, candidate_paths=[path])


class _AlwaysFailsRegisterRegistry:
    """Duck-typed registry stand-in: install succeeds on disk, but the
    in-process registry can never re-parse the file back (simulates a
    corrupt write, a race, or a parser regression discovered post-write).
    """

    def __init__(self, names: "set[str]") -> None:
        self.names = set(names)
        self.register_calls: list[Path] = []

    def register_generated_agent(self, path: Path):
        self.register_calls.append(path)
        return None


def _registry(tmp_path: Path, *, extra_agents: "list[str]" = ()) -> AgentRegistry:
    agents_dir = tmp_path / "seed-agents"
    agents_dir.mkdir()
    (agents_dir / "architect.md").write_text(
        "---\nname: architect\ndescription: plans things\n---\n# Architect\n", encoding="utf-8",
    )
    (agents_dir / "backend-engineer.md").write_text(
        "---\nname: backend-engineer\ndescription: builds things\n---\n# Backend Engineer\n",
        encoding="utf-8",
    )
    for extra in extra_agents:
        (agents_dir / f"{extra}.md").write_text(
            f"---\nname: {extra}\ndescription: extra\n---\n# {extra}\n", encoding="utf-8",
        )
    reg = AgentRegistry()
    reg.load_directory(agents_dir)
    return reg


def _dispatch_decision(requested: str, registry: AgentRegistry, **decide_kwargs):
    known = {n.split("--", 1)[0] for n in registry.names}
    gap = detect_missing_role_gap(requested, known_agents=known)
    assert gap is not None
    decision = decide_talent_lifecycle(gap, **decide_kwargs)
    return gap, decision


class TestManualReviewCollisionPolicy:
    def test_manual_review_quarantines_and_never_overwrites_or_registers(
        self, tmp_path: Path
    ) -> None:
        registry = _registry(tmp_path, extra_agents=["quantum-specialist"])
        gap = CapabilityGap(
            requested_capability="quantum-specialist",
            kind=CapabilityGapKind.MISSING_ROLE,
            evidence=(CapabilityGapEvidence(source="x", detail="explicit request"),),
        )
        # Force dispatch directly -- detect_missing_role_gap would short
        # circuit on an already-known name, but we need to exercise the
        # collision path against a name that legitimately collides.
        decision = TalentLifecycleDecision(
            action=TalentLifecycleAction.DISPATCH_TALENT_BUILDER,
            reason="test-forced dispatch",
            gap=gap,
        )
        dispatcher = FakeDispatcher(
            text=_agent_text(name="quantum-specialist"), written_filename="quantum-specialist"
        )
        config = TalentFactoryConfig(name_collision_policy="manual_review")

        outcome = run_talent_factory_for_gap(
            gap, decision, config=config, registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )

        assert outcome.status == "collision_fallback"
        assert outcome.resolved_agent_name in {"architect", "backend-engineer"}

        quarantined = tmp_path / ".claude" / "talent-builder-quarantine" / "quantum-specialist.md"
        assert quarantined.is_file()
        # Never lands in (or overwrites anything in) the live agents tree.
        assert not (tmp_path / ".claude" / "agents" / "quantum-specialist.md").exists()
        existing = registry.get("quantum-specialist")
        assert existing is not None and existing.description == "extra", (
            "the pre-existing hand-authored agent must be untouched"
        )


class TestInstallFailedRollback:
    def test_reparse_failure_after_install_rolls_the_file_back_out(self, tmp_path: Path) -> None:
        real_registry = _registry(tmp_path)
        fake_registry = _AlwaysFailsRegisterRegistry(set(real_registry.names))
        gap, decision = _dispatch_decision("quantum-specialist", real_registry)
        assert decision.action == TalentLifecycleAction.DISPATCH_TALENT_BUILDER

        dispatcher = FakeDispatcher(
            text=_agent_text(name="quantum-specialist"), written_filename="quantum-specialist"
        )
        config = TalentFactoryConfig(registry_reload="immediate")

        outcome = run_talent_factory_for_gap(
            gap, decision, config=config, registry=fake_registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )

        assert outcome.status == "install_failed_fallback"
        assert outcome.resolved_agent_name  # a generic fallback was still resolved
        assert len(fake_registry.register_calls) == 1

        # The atomic install did happen (that's how we know re-parse, not
        # write, failed) -- but the rollback must remove it. No artifact
        # may remain on disk after an install failure.
        installed_path = tmp_path / ".claude" / "agents" / "quantum-specialist.md"
        assert not installed_path.exists(), "install failure must roll the written file back out"


class TestPathEscapeDefense:
    def test_path_escaping_frontmatter_name_never_reaches_install(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        gap, decision = _dispatch_decision("widget-specialist", registry)
        assert decision.action == TalentLifecycleAction.DISPATCH_TALENT_BUILDER

        # The candidate file itself is written safely inside the scratch
        # dir (no traversal in the *write*) -- the attack is a
        # path-shaped value inside the frontmatter `name:` field, which is
        # what an install step would naively use to build a target path.
        malicious_text = _agent_text(name="../../../etc/evil-agent")
        dispatcher = FakeDispatcher(text=malicious_text, written_filename="widget-specialist")

        outcome = run_talent_factory_for_gap(
            gap, decision, config=TalentFactoryConfig(), registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )

        assert outcome.status == "validation_failed_fallback"
        assert outcome.validation is not None and not outcome.validation.valid
        assert any(
            "kebab-case" in e or "does not match filename" in e
            for e in outcome.validation.errors
        )

        # Nothing was ever installed -- the live agents tree is never even
        # created for a validation failure, let alone written outside it.
        assert not (tmp_path / ".claude" / "agents").exists()
        assert not (tmp_path.parent / "etc" / "evil-agent.md").exists()
        assert not (tmp_path / "etc" / "evil-agent.md").exists()

        # Scratch state is always cleaned up, success or failure.
        scratch_root = tmp_path / "scratch"
        if scratch_root.is_dir():
            assert list(scratch_root.iterdir()) == []


class TestMalformedFrontmatterEndToEnd:
    def test_missing_required_field_falls_back_and_installs_nothing(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        gap, decision = _dispatch_decision("widget-specialist", registry)

        bad_text = _agent_text(name="widget-specialist", permission_mode=None)
        dispatcher = FakeDispatcher(text=bad_text, written_filename="widget-specialist")

        outcome = run_talent_factory_for_gap(
            gap, decision, config=TalentFactoryConfig(), registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )

        assert outcome.status == "validation_failed_fallback"
        assert any("permissionMode" in e for e in outcome.validation.errors)
        assert outcome.resolved_agent_name in {"architect", "backend-engineer"}
        assert not (tmp_path / ".claude" / "agents").exists()
        assert registry.get("widget-specialist") is None


class TestUnsafeToolRequestEndToEnd:
    def test_unrecognized_tool_falls_back_and_installs_nothing(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        gap, decision = _dispatch_decision("widget-specialist", registry)

        unsafe_text = _agent_text(name="widget-specialist", tools="Read, Bash, DeleteAllFiles")
        dispatcher = FakeDispatcher(text=unsafe_text, written_filename="widget-specialist")

        outcome = run_talent_factory_for_gap(
            gap, decision, config=TalentFactoryConfig(), registry=registry,
            project_root=tmp_path, scratch_root=tmp_path / "scratch", dispatcher=dispatcher,
        )

        assert outcome.status == "validation_failed_fallback"
        assert any("unknown tool" in e.lower() for e in outcome.validation.errors)
        assert not (tmp_path / ".claude" / "agents").exists()
        assert registry.get("widget-specialist") is None
