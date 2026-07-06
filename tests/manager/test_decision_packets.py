"""Tests for :mod:`agent_baton.core.manager.decisions` (M7 -- decision
packets).

See docs/internal/manager-mode-pmo-plan.md Wave 2 / Task 10 and
docs/specs/agent-baton-claude-code-middle-manager-prd-tdd.md §15.3/§16
Milestone 7.

``ManagerDecision.created_at`` is always caller-supplied in these tests
(never a clock read) -- see the module docstring's "Wave 0 self-review
note".
"""
from __future__ import annotations

import json
from pathlib import Path

from agent_baton.core.config.manager import ManagerConfig
from agent_baton.core.manager.decisions import (
    DecisionPacketBuilder,
    compute_decision_id,
    decision_to_markdown,
)
from agent_baton.core.manager.paths import ManagerArtifactPaths
from agent_baton.core.runtime.decisions import DecisionManager
from agent_baton.models.manager import ManagerDecision


def _paths(tmp_path: Path, task_id: str = "task-decisions") -> ManagerArtifactPaths:
    return ManagerArtifactPaths(tmp_path, task_id)


# ---------------------------------------------------------------------------
# compute_decision_id
# ---------------------------------------------------------------------------


def test_decision_id_deterministic_from_summary_and_created_at() -> None:
    id_a = compute_decision_id("same summary", "2026-07-02T00:00:00Z")
    id_b = compute_decision_id("same summary", "2026-07-02T00:00:00Z")
    assert id_a == id_b
    assert id_a.startswith("dec-")
    assert len(id_a) == len("dec-") + 8


def test_decision_id_changes_with_summary_or_created_at() -> None:
    base = compute_decision_id("summary one", "2026-07-02T00:00:00Z")
    different_summary = compute_decision_id("summary two", "2026-07-02T00:00:00Z")
    different_time = compute_decision_id("summary one", "2026-07-02T01:00:00Z")
    assert base != different_summary
    assert base != different_time


# ---------------------------------------------------------------------------
# decision_to_markdown
# ---------------------------------------------------------------------------


def test_decision_to_markdown_renders_spec_sections() -> None:
    decision = ManagerDecision(
        decision_type="scope_expansion",
        task_id="task-decisions",
        summary="Backend engineer needs to modify app/auth/session.py.",
        context="The reporting endpoint depends on session metadata.",
        options=[
            "Approve scope expansion for app/auth/session.py.",
            "Amend plan to add an auth/session workstream.",
            "Reject and ask agent to find an alternative within current scope.",
        ],
        recommended_option="Option 2. The change affects auth/session behavior.",
        created_at="2026-07-02T00:00:00Z",
    )

    text = decision_to_markdown(decision)

    assert text.startswith("# Manager Decision Required: Scope Expansion")
    assert "## Summary" in text
    assert "## Context" in text
    assert "## Options" in text
    assert "## Recommendation" in text
    assert "1. Approve scope expansion for app/auth/session.py." in text
    assert "2. Amend plan to add an auth/session workstream." in text
    assert "Option 2. The change affects auth/session behavior." in text


def test_decision_to_markdown_title_for_each_decision_type() -> None:
    expectations = {
        "scope_expansion": "Scope Expansion",
        "ambiguity": "Ambiguity",
        "knowledge_gap": "Knowledge Gap",
        "review_veto": "Review Veto",
        "approval": "Approval",
    }
    for decision_type, expected_title in expectations.items():
        decision = ManagerDecision(decision_type=decision_type, summary="x", created_at="t")
        text = decision_to_markdown(decision)
        assert text.startswith(f"# Manager Decision Required: {expected_title}")


# ---------------------------------------------------------------------------
# DecisionPacketBuilder.create -- the three required effects
# ---------------------------------------------------------------------------


def test_scope_expansion_creates_packet_when_queued(tmp_path: Path) -> None:
    """DecisionPacketBuilder + a live DecisionManager -> all three effects:
    decisions/<id>.md, a decision-log.jsonl line, and a filed DecisionRequest
    (simulating the queue_for_manager scope-expansion policy routing a
    signal to this builder)."""
    config = ManagerConfig()
    paths = _paths(tmp_path)
    decision_manager = DecisionManager(decisions_dir=tmp_path / "decisions-store")
    builder = DecisionPacketBuilder(config, paths, decision_manager=decision_manager)

    decision = ManagerDecision(
        decision_type="scope_expansion",
        task_id="task-decisions",
        summary="Backend engineer needs to modify app/auth/session.py, outside the reporting scope.",
        context="The reporting endpoint depends on session metadata not exposed by the reporting service.",
        options=[
            "Approve scope expansion for app/auth/session.py.",
            "Amend plan to add an auth/session workstream.",
            "Reject and ask agent to find an alternative within current scope.",
        ],
        recommended_option="Option 2. The change affects auth/session behavior and should have a dedicated owner and review.",
        created_at="2026-07-02T00:00:00Z",
    )

    packet_path = builder.create(decision)

    # Effect 1: decisions/<id>.md
    assert decision.decision_id  # populated in place
    assert packet_path == paths.decision(decision.decision_id)
    assert packet_path.is_file()
    text = packet_path.read_text(encoding="utf-8")
    assert text.startswith("# Manager Decision Required: Scope Expansion")

    # Effect 2: decision-log.jsonl
    assert paths.decision_log.is_file()
    log_lines = paths.decision_log.read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 1
    logged = json.loads(log_lines[0])
    assert logged["decision_id"] == decision.decision_id
    assert logged["decision_type"] == "scope_expansion"
    assert logged["task_id"] == "task-decisions"

    # Effect 3: DecisionManager sees a pending request baton execute decide can resolve.
    pending = decision_manager.pending()
    assert len(pending) == 1
    assert pending[0].request_id == decision.decision_id
    assert pending[0].task_id == "task-decisions"
    assert pending[0].decision_type == "scope_expansion"
    assert str(packet_path) in pending[0].context_files


def test_knowledge_gap_creates_recommendation_packet(tmp_path: Path) -> None:
    """A knowledge_gap-typed decision produces the same three effects, with
    no DecisionManager wired -- the manager-decision route for a blocked
    knowledge gap (PRD: 'knowledge gap signal creates missing knowledge
    recommendation or decision packet')."""
    config = ManagerConfig()
    paths = _paths(tmp_path, task_id="task-decisions-2")
    builder = DecisionPacketBuilder(config, paths)  # no decision_manager

    decision = ManagerDecision(
        decision_type="knowledge_gap",
        task_id="task-decisions-2",
        summary="No knowledge pack covers the billing reconciliation domain.",
        context="Two agents reported the same gap across separate retrospectives.",
        options=[
            "Create a billing-reconciliation knowledge pack.",
            "Proceed without a pack and accept the risk.",
        ],
        recommended_option="Option 1. Recurrence across agents signals a durable gap.",
        created_at="2026-07-02T01:00:00Z",
    )

    packet_path = builder.create(decision)

    assert packet_path.is_file()
    text = packet_path.read_text(encoding="utf-8")
    assert text.startswith("# Manager Decision Required: Knowledge Gap")
    assert "billing reconciliation" in text

    log_lines = paths.decision_log.read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 1
    logged = json.loads(log_lines[0])
    assert logged["decision_type"] == "knowledge_gap"
    assert logged["decision_id"] == decision.decision_id


def test_create_populates_decision_id_when_missing(tmp_path: Path) -> None:
    config = ManagerConfig()
    paths = _paths(tmp_path)
    builder = DecisionPacketBuilder(config, paths)

    decision = ManagerDecision(
        decision_type="approval", task_id="task-decisions",
        summary="Approve deploy window.", created_at="2026-07-02T02:00:00Z",
    )
    assert decision.decision_id == ""

    builder.create(decision)

    assert decision.decision_id == compute_decision_id("Approve deploy window.", "2026-07-02T02:00:00Z")


def test_create_respects_preset_decision_id(tmp_path: Path) -> None:
    config = ManagerConfig()
    paths = _paths(tmp_path)
    builder = DecisionPacketBuilder(config, paths)

    decision = ManagerDecision(
        decision_type="review_veto", task_id="task-decisions",
        summary="Adversarial review vetoed phase 1.", created_at="2026-07-02T03:00:00Z",
        decision_id="dec-preset01",
    )

    path = builder.create(decision)

    assert path == paths.decision("dec-preset01")
    assert decision.decision_id == "dec-preset01"


def test_create_without_decision_manager_never_touches_decision_manager_module(tmp_path: Path) -> None:
    """decision_manager=None is a fully supported mode, not just a default
    placeholder -- the two file-based effects still happen, and no
    DecisionRequest is filed anywhere."""
    config = ManagerConfig()
    paths = _paths(tmp_path)
    builder = DecisionPacketBuilder(config, paths, decision_manager=None)

    decision = ManagerDecision(
        decision_type="ambiguity", task_id="task-decisions",
        summary="Task summary is underspecified.", created_at="2026-07-02T04:00:00Z",
    )

    path = builder.create(decision)

    assert path.is_file()
    assert paths.decision_log.is_file()
    assert builder.decision_manager is None


def test_created_at_is_never_read_from_clock(tmp_path: Path) -> None:
    """Two decisions built with an explicit (identical, non-current)
    created_at produce identical decision_ids -- proof the builder never
    substitutes datetime.now() for a caller-supplied timestamp."""
    config = ManagerConfig()
    paths_a = _paths(tmp_path, task_id="task-a")
    paths_b = _paths(tmp_path, task_id="task-b")

    decision_a = ManagerDecision(
        decision_type="ambiguity", summary="same summary", created_at="1999-01-01T00:00:00Z",
    )
    decision_b = ManagerDecision(
        decision_type="ambiguity", summary="same summary", created_at="1999-01-01T00:00:00Z",
    )

    DecisionPacketBuilder(config, paths_a).create(decision_a)
    DecisionPacketBuilder(config, paths_b).create(decision_b)

    assert decision_a.decision_id == decision_b.decision_id
