"""Tests for HeadlessTalentBuilderDispatcher -- the production talent-factory
dispatcher (agent_baton.core.engine.planning.talent_factory).

These are the "process failure" regressions the talent-factory contract
requires (docs/internal/talent-factory-contract.md): the real dispatcher
wraps a subprocess (``claude --print`` via ``HeadlessClaude``), and every
way that subprocess can fail -- binary missing, non-zero exit, a raised
exception, an empty/whitespace-only response -- must resolve to a
``DispatchOutcome(success=False, ...)`` rather than propagating a raw
exception or silently producing a phantom artifact. Complements
``tests/test_talent_factory.py`` (which exercises ``run_talent_factory_for_gap``
against a *fake* dispatcher) by exercising the one dispatcher implementation
that actually shells out.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from agent_baton.core.engine.planning.capability_gap import (
    CapabilityGap,
    CapabilityGapEvidence,
    CapabilityGapKind,
    PermittedArtifactType,
)
from agent_baton.core.engine.planning.talent_factory import (
    HeadlessTalentBuilderDispatcher,
    TalentBuilderRequest,
)
from agent_baton.core.runtime.headless import HeadlessResult

_VALID_ARTIFACT = """---
name: quantum-specialist
description: |
  Handles quantum-domain analysis.
model: sonnet
permissionMode: default
tools: Read, Glob, Grep
created_by: talent-builder
status: draft
version: 0.1.0
---

# Quantum Specialist

## Mission

You are a quantum specialist.

## Before Starting

1. Read this entire agent definition.

## Knowledge References

None required yet.

## Principles

- Be rigorous.

## Anti-Patterns

- Do not fabricate results.

## Output Format

Return a summary.
"""


def _gap(name: str = "quantum-specialist") -> CapabilityGap:
    return CapabilityGap(
        requested_capability=name,
        kind=CapabilityGapKind.MISSING_ROLE,
        evidence=(CapabilityGapEvidence(source="roster_stage", detail="no match"),),
    )


def _request(tmp_path: Path, *, name: str = "quantum-specialist") -> TalentBuilderRequest:
    return TalentBuilderRequest(
        gap=_gap(name),
        output_dir=tmp_path / "scratch",
        project_root=tmp_path,
        permitted_artifacts=(PermittedArtifactType.AGENT,),
    )


def _patched_headless_claude(fake_hc: MagicMock):
    """Patch the HeadlessClaude class the dispatcher local-imports at call
    time, so ``HeadlessClaude(config)`` returns *fake_hc*."""
    return patch("agent_baton.core.runtime.headless.HeadlessClaude", return_value=fake_hc)


class TestClaudeCliUnavailable:
    def test_binary_not_on_path_returns_failure_without_invoking_subprocess(
        self, tmp_path: Path
    ) -> None:
        fake_hc = MagicMock()
        fake_hc.is_available = False

        with _patched_headless_claude(fake_hc):
            outcome = HeadlessTalentBuilderDispatcher().dispatch(_request(tmp_path))

        assert outcome.success is False
        assert "not available" in outcome.error
        assert outcome.candidate_paths == []
        fake_hc.run_sync.assert_not_called()


class TestProcessFailureHandling:
    """A live 'claude' subprocess can fail in several ways -- none of them
    may propagate as a raw, uncaught exception out of dispatch()."""

    def test_run_sync_raising_is_caught_and_reported_as_failure(self, tmp_path: Path) -> None:
        fake_hc = MagicMock()
        fake_hc.is_available = True
        fake_hc.run_sync.side_effect = RuntimeError("subprocess exploded")

        with _patched_headless_claude(fake_hc):
            outcome = HeadlessTalentBuilderDispatcher().dispatch(_request(tmp_path))

        assert outcome.success is False
        assert "subprocess exploded" in outcome.error
        assert outcome.candidate_paths == []

    def test_non_zero_exit_returns_failure_with_reported_error(self, tmp_path: Path) -> None:
        fake_hc = MagicMock()
        fake_hc.is_available = True
        fake_hc.run_sync.return_value = HeadlessResult(
            success=False, error="claude exited 1: rate limited"
        )

        with _patched_headless_claude(fake_hc):
            outcome = HeadlessTalentBuilderDispatcher().dispatch(_request(tmp_path))

        assert outcome.success is False
        assert outcome.error == "claude exited 1: rate limited"
        assert outcome.candidate_paths == []
        # A failed subprocess must never leave a candidate artifact behind.
        assert not (tmp_path / "scratch").exists() or list((tmp_path / "scratch").iterdir()) == []

    def test_empty_output_is_treated_as_failure(self, tmp_path: Path) -> None:
        fake_hc = MagicMock()
        fake_hc.is_available = True
        fake_hc.run_sync.return_value = HeadlessResult(success=True, output="   \n  ")

        with _patched_headless_claude(fake_hc):
            outcome = HeadlessTalentBuilderDispatcher().dispatch(_request(tmp_path))

        assert outcome.success is False
        assert "empty artifact" in outcome.error


class TestSuccessfulDispatch:
    def test_writes_candidate_file_under_the_scoped_output_dir(self, tmp_path: Path) -> None:
        fake_hc = MagicMock()
        fake_hc.is_available = True
        fake_hc.run_sync.return_value = HeadlessResult(success=True, output=_VALID_ARTIFACT)

        with _patched_headless_claude(fake_hc):
            outcome = HeadlessTalentBuilderDispatcher().dispatch(_request(tmp_path))

        assert outcome.success is True
        assert len(outcome.candidate_paths) == 1
        candidate = outcome.candidate_paths[0]
        assert candidate.parent == tmp_path / "scratch"
        assert candidate.is_file()
        assert "name: quantum-specialist" in candidate.read_text(encoding="utf-8")

    def test_code_fenced_output_is_unwrapped_before_writing(self, tmp_path: Path) -> None:
        fenced = f"```markdown\n{_VALID_ARTIFACT}```\n"
        fake_hc = MagicMock()
        fake_hc.is_available = True
        fake_hc.run_sync.return_value = HeadlessResult(success=True, output=fenced)

        with _patched_headless_claude(fake_hc):
            outcome = HeadlessTalentBuilderDispatcher().dispatch(_request(tmp_path))

        assert outcome.success is True
        content = outcome.candidate_paths[0].read_text(encoding="utf-8")
        assert not content.startswith("```")
        assert "```" not in content.splitlines()[-1]

    def test_prompt_carries_gap_evidence_and_forbids_self_generation(self, tmp_path: Path) -> None:
        captured_prompts: list[str] = []

        def _fake_run_sync(prompt: str, **_kwargs: object) -> HeadlessResult:
            captured_prompts.append(prompt)
            return HeadlessResult(success=True, output=_VALID_ARTIFACT)

        fake_hc = MagicMock()
        fake_hc.is_available = True
        fake_hc.run_sync.side_effect = _fake_run_sync

        with _patched_headless_claude(fake_hc):
            HeadlessTalentBuilderDispatcher().dispatch(_request(tmp_path))

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        assert "quantum-specialist" in prompt
        assert "missing_role" in prompt
        assert "no match" in prompt  # the gap's evidence detail
        assert "talent-builder" in prompt  # explicit "never name it talent-builder"

    def test_dispatch_never_invoked_more_than_once_per_call(self, tmp_path: Path) -> None:
        fake_hc = MagicMock()
        fake_hc.is_available = True
        fake_hc.run_sync.return_value = HeadlessResult(success=True, output=_VALID_ARTIFACT)

        with _patched_headless_claude(fake_hc):
            HeadlessTalentBuilderDispatcher().dispatch(_request(tmp_path))

        assert fake_hc.run_sync.call_count == 1
