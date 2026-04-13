"""Tests for agent_baton.core.learn.resolvers — type-specific resolution strategies."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_baton.core.learn.overrides import LearnedOverrides
from agent_baton.core.learn.resolvers import (
    resolve_agent_degradation,
    resolve_gate_mismatch,
    resolve_knowledge_gap,
    resolve_roster_bloat,
    resolve_routing_mismatch,
)
from agent_baton.models.learning import LearningEvidence, LearningIssue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_issue(
    issue_type: str = "routing_mismatch",
    target: str = "python:backend-engineer",
    evidence: list[LearningEvidence] | None = None,
    **kwargs,
) -> LearningIssue:
    return LearningIssue(
        issue_id="test-issue-id",
        issue_type=issue_type,
        severity="medium",
        status="open",
        title="Test issue",
        target=target,
        evidence=evidence or [],
        **kwargs,
    )


def _make_evidence(data: dict | None = None) -> LearningEvidence:
    return LearningEvidence(
        timestamp="2026-04-13T12:00:00Z",
        source_task_id="task-1",
        detail="Test observation",
        data=data or {},
    )


@pytest.fixture
def overrides_path(tmp_path: Path) -> Path:
    return tmp_path / "learned-overrides.json"


@pytest.fixture
def overrides(overrides_path: Path) -> LearnedOverrides:
    return LearnedOverrides(overrides_path)


# ---------------------------------------------------------------------------
# resolve_routing_mismatch
# ---------------------------------------------------------------------------


class TestResolveRoutingMismatch:
    def test_writes_flavor_override(self, overrides: LearnedOverrides):
        ev = _make_evidence({"suggested_flavor": "python"})
        issue = _make_issue(
            issue_type="routing_mismatch",
            target="python/react:backend-engineer",
            evidence=[ev],
        )
        result = resolve_routing_mismatch(issue, overrides)
        flavor_overrides = overrides.get_flavor_overrides()
        assert "python/react" in flavor_overrides
        assert flavor_overrides["python/react"]["backend-engineer"] == "python"

    def test_returns_human_readable_description(self, overrides: LearnedOverrides):
        ev = _make_evidence({"suggested_flavor": "python"})
        issue = _make_issue(
            target="python/react:backend-engineer",
            evidence=[ev],
        )
        result = resolve_routing_mismatch(issue, overrides)
        assert "python/react" in result
        assert "backend-engineer" in result
        assert "python" in result

    def test_falls_back_to_detected_stack_for_flavor(self, overrides: LearnedOverrides):
        ev = _make_evidence({"detected_stack": "python/react"})
        issue = _make_issue(
            target="python/react:backend-engineer",
            evidence=[ev],
        )
        resolve_routing_mismatch(issue, overrides)
        flavor_overrides = overrides.get_flavor_overrides()
        assert flavor_overrides["python/react"]["backend-engineer"] == "python"

    def test_suggested_flavor_takes_precedence_over_detected_stack(self, overrides: LearnedOverrides):
        ev = _make_evidence({"suggested_flavor": "fastapi", "detected_stack": "python/django"})
        issue = _make_issue(
            target="python/django:backend-engineer",
            evidence=[ev],
        )
        resolve_routing_mismatch(issue, overrides)
        flavor_overrides = overrides.get_flavor_overrides()
        assert flavor_overrides["python/django"]["backend-engineer"] == "fastapi"

    def test_strips_reason_suffix_from_target(self, overrides: LearnedOverrides):
        """Target like 'python:backend-engineer=detected_language_mismatch' must be parsed."""
        ev = _make_evidence({"suggested_flavor": "python"})
        issue = _make_issue(
            target="python:backend-engineer=detected_language_mismatch",
            evidence=[ev],
        )
        resolve_routing_mismatch(issue, overrides)
        flavor_overrides = overrides.get_flavor_overrides()
        assert flavor_overrides["python"]["backend-engineer"] == "python"

    def test_unparseable_target_no_evidence_returns_error_message(self, overrides: LearnedOverrides):
        issue = _make_issue(target="unstructured-target-no-colon", evidence=[])
        result = resolve_routing_mismatch(issue, overrides)
        assert "Cannot" in result or "cannot" in result or "manual" in result

    def test_missing_agent_base_returns_error_message(self, overrides: LearnedOverrides):
        """Target with colon but no agent base and no evidence should be graceful."""
        issue = _make_issue(target="python:agent-x", evidence=[])
        result = resolve_routing_mismatch(issue, overrides)
        # No flavor → cannot write override
        assert "manual" in result.lower() or "could not" in result.lower()


# ---------------------------------------------------------------------------
# resolve_agent_degradation
# ---------------------------------------------------------------------------


class TestResolveAgentDegradation:
    def test_adds_agent_to_drop_list(self, overrides: LearnedOverrides):
        issue = _make_issue(
            issue_type="agent_degradation",
            target="visualization-expert",
        )
        resolve_agent_degradation(issue, overrides)
        drops = overrides.get_agent_drops()
        assert "visualization-expert" in drops

    def test_returns_human_readable_description(self, overrides: LearnedOverrides):
        issue = _make_issue(
            issue_type="agent_degradation",
            target="data-scientist",
        )
        result = resolve_agent_degradation(issue, overrides)
        assert "data-scientist" in result

    def test_idempotent_double_call(self, overrides: LearnedOverrides):
        issue = _make_issue(issue_type="agent_degradation", target="bad-agent")
        resolve_agent_degradation(issue, overrides)
        resolve_agent_degradation(issue, overrides)
        drops = overrides.get_agent_drops()
        assert drops.count("bad-agent") == 1


# ---------------------------------------------------------------------------
# resolve_gate_mismatch
# ---------------------------------------------------------------------------


class TestResolveGateMismatch:
    def test_writes_gate_override_from_suggested_command(self, overrides: LearnedOverrides):
        ev = _make_evidence({"suggested_command": "vitest run"})
        issue = _make_issue(
            issue_type="gate_mismatch",
            target="typescript:test",
            evidence=[ev],
        )
        resolve_gate_mismatch(issue, overrides)
        gate_overrides = overrides.get_gate_overrides()
        assert gate_overrides["typescript"]["test"] == "vitest run"

    def test_falls_back_to_detected_command(self, overrides: LearnedOverrides):
        ev = _make_evidence({"detected_command": "npm test"})
        issue = _make_issue(
            issue_type="gate_mismatch",
            target="javascript:build",
            evidence=[ev],
        )
        resolve_gate_mismatch(issue, overrides)
        gate_overrides = overrides.get_gate_overrides()
        assert gate_overrides["javascript"]["build"] == "npm test"

    def test_suggested_command_takes_precedence_over_detected(self, overrides: LearnedOverrides):
        ev = _make_evidence({"suggested_command": "vitest run", "detected_command": "jest"})
        issue = _make_issue(
            issue_type="gate_mismatch",
            target="typescript:test",
            evidence=[ev],
        )
        resolve_gate_mismatch(issue, overrides)
        gate_overrides = overrides.get_gate_overrides()
        assert gate_overrides["typescript"]["test"] == "vitest run"

    def test_returns_human_readable_description(self, overrides: LearnedOverrides):
        ev = _make_evidence({"suggested_command": "vitest run"})
        issue = _make_issue(target="typescript:test", evidence=[ev])
        result = resolve_gate_mismatch(issue, overrides)
        assert "typescript" in result
        assert "vitest run" in result

    def test_unparseable_target_returns_error_message(self, overrides: LearnedOverrides):
        issue = _make_issue(target="no-colon-here", evidence=[])
        result = resolve_gate_mismatch(issue, overrides)
        assert "manual" in result.lower() or "could not" in result.lower()

    def test_no_command_in_evidence_returns_error_message(self, overrides: LearnedOverrides):
        ev = _make_evidence({})  # no suggested_command
        issue = _make_issue(target="typescript:test", evidence=[ev])
        result = resolve_gate_mismatch(issue, overrides)
        assert "manual" in result.lower() or "could not" in result.lower()


# ---------------------------------------------------------------------------
# resolve_knowledge_gap
# ---------------------------------------------------------------------------


class TestResolveKnowledgeGap:
    def test_creates_stub_file(self, tmp_path: Path, overrides: LearnedOverrides):
        issue = _make_issue(
            issue_type="knowledge_gap",
            target="ml-pipelines",
        )
        knowledge_root = tmp_path / "knowledge"
        resolve_knowledge_gap(issue, overrides, knowledge_root=knowledge_root)
        stub = knowledge_root / "ml-pipelines.md"
        assert stub.exists()

    def test_stub_contains_issue_info(self, tmp_path: Path, overrides: LearnedOverrides):
        issue = LearningIssue(
            issue_id="test-issue-uuid",
            issue_type="knowledge_gap",
            severity="low",
            status="open",
            title="Missing ML context",
            target="ml-pipelines",
        )
        knowledge_root = tmp_path / "knowledge"
        resolve_knowledge_gap(issue, overrides, knowledge_root=knowledge_root)
        content = (knowledge_root / "ml-pipelines.md").read_text(encoding="utf-8")
        assert "ml-pipelines" in content
        assert "Missing ML context" in content

    def test_stub_contains_evidence_details(self, tmp_path: Path, overrides: LearnedOverrides):
        ev = _make_evidence()
        ev.detail = "Agent lacked context about feature stores"
        issue = _make_issue(
            issue_type="knowledge_gap",
            target="feature-stores",
            evidence=[ev],
        )
        knowledge_root = tmp_path / "knowledge"
        resolve_knowledge_gap(issue, overrides, knowledge_root=knowledge_root)
        content = (knowledge_root / "feature-stores.md").read_text(encoding="utf-8")
        assert "feature stores" in content

    def test_does_not_overwrite_existing_stub(self, tmp_path: Path, overrides: LearnedOverrides):
        knowledge_root = tmp_path / "knowledge"
        knowledge_root.mkdir(parents=True)
        stub = knowledge_root / "ml-pipelines.md"
        stub.write_text("# Existing content", encoding="utf-8")
        issue = _make_issue(issue_type="knowledge_gap", target="ml-pipelines")
        result = resolve_knowledge_gap(issue, overrides, knowledge_root=knowledge_root)
        assert "already exists" in result
        assert stub.read_text(encoding="utf-8") == "# Existing content"

    def test_returns_path_in_description(self, tmp_path: Path, overrides: LearnedOverrides):
        issue = _make_issue(issue_type="knowledge_gap", target="my-domain")
        knowledge_root = tmp_path / "knowledge"
        result = resolve_knowledge_gap(issue, overrides, knowledge_root=knowledge_root)
        assert "my-domain" in result

    def test_sanitizes_unsafe_target_name(self, tmp_path: Path, overrides: LearnedOverrides):
        issue = _make_issue(issue_type="knowledge_gap", target="../../etc/passwd")
        knowledge_root = tmp_path / "knowledge"
        resolve_knowledge_gap(issue, overrides, knowledge_root=knowledge_root)
        # Confirm no file is created outside knowledge_root
        assert not (tmp_path / "etc" / "passwd").exists()
        # Confirm sanitized file was created inside knowledge_root
        stubs = list(knowledge_root.iterdir())
        assert len(stubs) == 1

    def test_empty_target_uses_fallback_name(self, tmp_path: Path, overrides: LearnedOverrides):
        issue = _make_issue(issue_type="knowledge_gap", target="")
        knowledge_root = tmp_path / "knowledge"
        result = resolve_knowledge_gap(issue, overrides, knowledge_root=knowledge_root)
        assert "gap-stub" in result or knowledge_root.exists()


# ---------------------------------------------------------------------------
# resolve_roster_bloat
# ---------------------------------------------------------------------------


class TestResolveRosterBloat:
    def test_increments_threshold_when_no_suggestion(self, overrides: LearnedOverrides):
        issue = _make_issue(issue_type="roster_bloat", target="keyword-fallback:unknown", evidence=[])
        resolve_roster_bloat(issue, overrides)
        adjustments = overrides.load()["classifier_adjustments"]
        # Default is 2, should be incremented to 3
        assert adjustments["min_keyword_overlap"] == 3

    def test_uses_suggested_value_from_evidence(self, overrides: LearnedOverrides):
        ev = _make_evidence({"suggested_min_keyword_overlap": 5})
        issue = _make_issue(issue_type="roster_bloat", evidence=[ev])
        resolve_roster_bloat(issue, overrides)
        adjustments = overrides.load()["classifier_adjustments"]
        assert adjustments["min_keyword_overlap"] == 5

    def test_returns_human_readable_description(self, overrides: LearnedOverrides):
        issue = _make_issue(issue_type="roster_bloat", evidence=[])
        result = resolve_roster_bloat(issue, overrides)
        assert "min_keyword_overlap" in result

    def test_increments_version(self, overrides: LearnedOverrides):
        issue = _make_issue(issue_type="roster_bloat", evidence=[])
        resolve_roster_bloat(issue, overrides)
        assert overrides.load()["version"] == 2

    def test_increments_from_existing_threshold(self, overrides: LearnedOverrides):
        """If an override already exists at 4, incrementing should produce 5."""
        data = overrides.load()
        data["classifier_adjustments"]["min_keyword_overlap"] = 4
        overrides.save(data)
        issue = _make_issue(issue_type="roster_bloat", evidence=[])
        resolve_roster_bloat(issue, overrides)
        adjustments = overrides.load()["classifier_adjustments"]
        assert adjustments["min_keyword_overlap"] == 5
